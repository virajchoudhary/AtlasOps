"""Read persisted incident records without loading the incident workflow."""

from __future__ import annotations

import json
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_TRAJECTORIES_DIR = "data/trajectories"
MAX_RECORD_BYTES = 1_048_576
MAX_HISTORY_LIMIT = 100
MAX_HISTORY_OFFSET = 10_000
MAX_DIRECTORY_ENTRIES = 20_000
MAX_HISTORY_RECORDS = 10_000
MAX_TEXT_CHARS = 2_000
MAX_LIST_ITEMS = 100
MAX_OBJECT_FIELDS = 100

_WINDOWS_RESERVED_INCIDENT_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {
        f"{prefix}{digit}"
        for prefix in ("COM", "LPT")
        for digit in range(1, 10)
    }
)
_EXECUTION_FILE = re.compile(
    r"(?P<incident_id>.+)\.execution-(?P<execution_id>[0-9a-f]{32})\.json"
)
_PARTIAL_FILE = re.compile(r".+\.partial-[0-9a-f]{32}\.json")
_SECRET_KEY = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|"
    r"password|passwd|secret|credential|private[_-]?key)"
)
_SECRET_TEXT = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"""(?ix)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"""
        r"""authorization|password|passwd|secret|credential|private[_-]?key)"""
        r"""["']?\s*[:=]\s*["']?[^,\s"'}`\]]+"""
    ),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)
_ABSOLUTE_PATH = (
    re.compile(r"""(?i)(?:\b[A-Z]:\\|\\\\[^\\/\s]+\\[^\\/\s]+\\)[^\s,;"'`]+"""),
    re.compile(r"""(?<![A-Za-z0-9_])/(?:[^/\s,;"'`]+/)*[^/\s,;"'`]+"""),
)
_OMIT = object()


class RecordNotFound(Exception):
    """The requested canonical or execution record is absent or unsafe."""


class HistoryUnavailable(Exception):
    """The configured store or a selected persisted record is unusable."""


@dataclass(frozen=True)
class _Identity:
    record_type: str
    incident_id: str
    execution_id: str | None = None


@dataclass(frozen=True)
class _Candidate:
    filename: str
    identities: tuple[_Identity, ...]


def _valid_incident_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 128
        and value[0].isascii()
        and value[0].isalnum()
        and not value.endswith(".")
        and all(
            char.isascii() and (char.isalnum() or char in "._-")
            for char in value
        )
        and value.split(".", 1)[0].upper() not in _WINDOWS_RESERVED_INCIDENT_NAMES
    )


def _valid_execution_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _configured_root(
    directory: str | os.PathLike[str] | None,
    *,
    missing_ok: bool,
) -> Path | None:
    configured = (
        directory
        if directory is not None
        else os.environ.get("TRAJECTORIES_DIR", DEFAULT_TRAJECTORIES_DIR)
    )
    try:
        root = Path(configured).resolve(strict=True)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise RecordNotFound from None
    except (OSError, RuntimeError):
        raise HistoryUnavailable from None

    try:
        if not root.is_dir():
            raise HistoryUnavailable
    except OSError:
        raise HistoryUnavailable from None
    return root


def _safe_file(root: Path, filename: str) -> Path | None:
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        return None

    try:
        resolved = (root / filename).resolve(strict=True)
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError):
        raise HistoryUnavailable from None

    if not resolved.is_relative_to(root):
        return None
    try:
        if not stat.S_ISREG(resolved.stat().st_mode):
            return None
    except FileNotFoundError:
        return None
    except OSError:
        raise HistoryUnavailable from None
    return resolved


def _candidate_for_filename(filename: str) -> _Candidate | None:
    if not filename.endswith(".json") or _PARTIAL_FILE.fullmatch(filename):
        return None

    identities: list[_Identity] = []
    execution_match = _EXECUTION_FILE.fullmatch(filename)
    if execution_match:
        incident_id = execution_match.group("incident_id")
        execution_id = execution_match.group("execution_id")
        if _valid_incident_id(incident_id) and _valid_execution_id(execution_id):
            identities.append(_Identity("execution", incident_id, execution_id))

    canonical_id = filename[:-5]
    if _valid_incident_id(canonical_id):
        identities.append(_Identity("canonical", canonical_id))

    if not identities:
        return None
    return _Candidate(filename, tuple(identities))


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON number: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_json(
    path: Path,
    *,
    allow_missing_execution_id: bool = False,
) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_RECORD_BYTES:
                raise HistoryUnavailable
            raw = stream.read(MAX_RECORD_BYTES + 1)
    except FileNotFoundError:
        raise RecordNotFound from None
    except OSError:
        raise HistoryUnavailable from None

    if len(raw) > MAX_RECORD_BYTES:
        raise HistoryUnavailable
    try:
        record = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise HistoryUnavailable from None

    if not isinstance(record, dict) or not _valid_incident_id(record.get("incident_id")):
        raise HistoryUnavailable
    if "execution_id" in record:
        if not _valid_execution_id(record["execution_id"]):
            raise HistoryUnavailable
    elif not allow_missing_execution_id:
        raise HistoryUnavailable
    return record


def _load_candidate(root: Path, candidate: _Candidate) -> tuple[_Identity, dict[str, Any]]:
    path = _safe_file(root, candidate.filename)
    if path is None:
        raise RecordNotFound
    allow_missing_execution_id = (
        len(candidate.identities) == 1
        and candidate.identities[0].record_type == "canonical"
    )
    record = _read_json(path, allow_missing_execution_id=allow_missing_execution_id)
    for identity in candidate.identities:
        if record["incident_id"] != identity.incident_id:
            continue
        if identity.execution_id is None or record.get("execution_id") == identity.execution_id:
            return identity, record
    raise HistoryUnavailable


def _safe_text(value: Any, *, maximum: int = MAX_TEXT_CHARS) -> Any:
    if not isinstance(value, str):
        return _OMIT
    safe = value
    for pattern in _SECRET_TEXT:
        safe = pattern.sub("[REDACTED]", safe)
    for pattern in _ABSOLUTE_PATH:
        safe = pattern.sub("[PATH]", safe)
    if len(safe) > maximum:
        suffix = "[truncated]"
        safe = safe[: maximum - len(suffix)] + suffix
    return safe


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return _OMIT
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if len(str(abs(value))) <= 100 else _OMIT
    if isinstance(value, float):
        return value if math.isfinite(value) else _OMIT
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, list):
        items = []
        for item in value[:MAX_LIST_ITEMS]:
            safe_item = _safe_value(item, depth=depth + 1)
            if safe_item is not _OMIT:
                items.append(safe_item)
        return items
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:MAX_OBJECT_FIELDS]:
            if not isinstance(key, str) or _SECRET_KEY.search(key):
                continue
            safe_item = _safe_value(item, depth=depth + 1)
            if safe_item is not _OMIT:
                result[key] = safe_item
        return result
    return _OMIT


def _project_fields(
    source: Any,
    field_names: tuple[str, ...],
    *,
    special: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        return None
    result: dict[str, Any] = {}
    for name in field_names:
        if name not in source:
            continue
        projector = (special or {}).get(name)
        value = projector(source[name]) if projector else _safe_value(source[name])
        if value is not _OMIT:
            result[name] = value
    return result


def _project_blast_radius(value: Any) -> Any:
    return _project_fields(
        value,
        ("services", "namespaces", "user_impact_pct", "revenue_path_affected"),
    ) or {}


def _project_evidence_sources(value: Any) -> Any:
    if not isinstance(value, list):
        return _OMIT
    sources = []
    for item in value[:MAX_LIST_ITEMS]:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        if not isinstance(tool, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", tool) is None:
            continue
        sources.append({"tool": tool})
    return sources


def _project_actions(value: Any) -> Any:
    if not isinstance(value, list):
        return _OMIT
    projected = []
    for action in value[:MAX_LIST_ITEMS]:
        if not isinstance(action, dict):
            continue
        item = _project_fields(
            action,
            ("step", "tool", "args", "result", "status"),
        )
        if item:
            projected.append(item)
    return projected


def _project_postmortem_path(value: Any) -> Any:
    if (
        not isinstance(value, str)
        or not value.startswith("docs/postmortems/")
        or "\\" in value
        or ":" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or re.fullmatch(r"docs/postmortems/[A-Za-z0-9._/-]{1,240}", value) is None
    ):
        return _OMIT
    return value


def _project_role(
    record: dict[str, Any],
    role: str,
    fields: tuple[str, ...],
) -> dict[str, Any] | None:
    section = record.get(role)
    if not isinstance(section, dict):
        return None
    final = section.get("final")
    if not isinstance(final, dict):
        return None

    special: dict[str, Any] = {}
    if role == "triage":
        special["blast_radius"] = _project_blast_radius
    if role == "diagnosis":
        special["evidence"] = _project_evidence_sources
    if role == "remediation":
        special["proposed_actions"] = _project_actions
        special["executed_actions"] = _project_actions
    if role == "comms":
        special["postmortem_path"] = _project_postmortem_path
    return _project_fields(final, fields, special=special)


def _project_checks(value: Any) -> Any:
    if not isinstance(value, list):
        return _OMIT
    projected = []
    for check in value[:MAX_LIST_ITEMS]:
        item = _project_fields(check, ("name", "target", "passed", "required"))
        if item:
            projected.append(item)
    return projected


def _project_observed_metrics(value: Any) -> Any:
    if not isinstance(value, dict):
        return _OMIT
    projected = {}
    for query, result in list(value.items())[:MAX_OBJECT_FIELDS]:
        safe_query = _safe_text(query, maximum=256)
        if safe_query is _OMIT or not isinstance(query, str) or _SECRET_KEY.search(query):
            continue
        if isinstance(result, bool):
            continue
        if (
            isinstance(result, int)
            and len(str(abs(result))) <= 100
            or isinstance(result, float)
            and math.isfinite(result)
        ):
            projected[safe_query] = result
    return projected


def _project_verification(value: Any) -> dict[str, Any] | None:
    return _project_fields(
        value,
        (
            "scenario_id",
            "agent_claimed_resolved",
            "env_resolved",
            "verification_status",
            "is_false_resolution",
            "is_false_negative",
            "failed_checks",
            "checks",
            "observed_metrics",
            "verification_timestamp",
        ),
        special={
            "checks": _project_checks,
            "observed_metrics": _project_observed_metrics,
        },
    )


def _project_record(record: dict[str, Any], record_type: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "incident_id": record["incident_id"],
        "execution_id": record.get("execution_id"),
        "record_type": record_type,
        "triage": _project_role(
            record,
            "triage",
            (
                "severity",
                "title",
                "blast_radius",
                "affected_services",
                "correlated_alerts",
                "handoff_notes",
            ),
        ),
        "diagnosis": _project_role(
            record,
            "diagnosis",
            (
                "root_cause",
                "category",
                "confidence",
                "blast_radius_update",
                "recommended_fix",
                "evidence",
            ),
        ),
        "remediation": _project_role(
            record,
            "remediation",
            (
                "mode",
                "status",
                "outcome",
                "proposed_actions",
                "executed_actions",
                "verified_by",
                "summary",
                "time_to_resolve_seconds",
                "runbook",
            ),
        ),
        "approval": _project_fields(
            record.get("approval"),
            (
                "mode",
                "severity",
                "triage_severity",
                "decision",
                "action_digest",
                "action_summary",
                "approved_by",
                "reason",
            ),
        ),
        "verification": _project_verification(record.get("verification")),
        "comms": _project_role(
            record,
            "comms",
            (
                "status",
                "slack_posted",
                "postmortem_path",
                "summary",
                "summary_for_dashboard",
                "lessons_learned",
            ),
        ),
    }
    for name in (
        "scenario_id",
        "episode_status",
        "execution_mode",
        "evidence_class",
        "certification_status",
    ):
        if name in record:
            value = _safe_value(record[name])
            if value is not _OMIT:
                result[name] = value
    for name in ("agent_claimed_resolved", "env_resolved", "resolved"):
        value = record.get(name, _OMIT)
        if value is not _OMIT and (value is None or isinstance(value, bool)):
            result[name] = value
    return result


def _project_summary(record: dict[str, Any], record_type: str) -> dict[str, Any]:
    triage = _project_role(record, "triage", ("severity", "title"))
    remediation = _project_role(record, "remediation", ("status", "outcome"))
    comms = _project_role(record, "comms", ("summary", "summary_for_dashboard"))
    verification = _project_verification(record.get("verification"))
    summary: dict[str, Any] = {
        "incident_id": record["incident_id"],
        "execution_id": record.get("execution_id"),
        "record_type": record_type,
        "severity": (triage or {}).get("severity"),
        "title": (triage or {}).get("title"),
        "status": (remediation or {}).get("outcome")
        or (remediation or {}).get("status"),
        "resolved": record.get("resolved")
        if isinstance(record.get("resolved"), bool)
        else None,
        "env_resolved": record.get("env_resolved")
        if isinstance(record.get("env_resolved"), bool)
        else None,
        "verification_status": (verification or {}).get("verification_status"),
        "summary": (comms or {}).get("summary")
        or (comms or {}).get("summary_for_dashboard"),
    }
    scenario_id = _safe_value(record.get("scenario_id", _OMIT))
    if scenario_id is not _OMIT:
        summary["scenario_id"] = scenario_id
    return summary


def list_incident_history(
    *,
    limit: int = 50,
    offset: int = 0,
    directory: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Return one bounded page of canonical and execution records."""
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_HISTORY_LIMIT
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= MAX_HISTORY_OFFSET
    ):
        raise ValueError("history pagination is out of bounds")

    root = _configured_root(directory, missing_ok=True)
    if root is None:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    candidates: list[_Candidate] = []
    scanned = 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                scanned += 1
                if scanned > MAX_DIRECTORY_ENTRIES:
                    raise HistoryUnavailable
                candidate = _candidate_for_filename(entry.name)
                if candidate is None or _safe_file(root, candidate.filename) is None:
                    continue
                candidates.append(candidate)
                if len(candidates) > MAX_HISTORY_RECORDS:
                    raise HistoryUnavailable
    except HistoryUnavailable:
        raise
    except OSError:
        raise HistoryUnavailable from None

    candidates.sort(key=lambda candidate: candidate.filename.casefold())
    total = len(candidates)
    items = []
    for candidate in candidates[offset : offset + limit]:
        try:
            identity, record = _load_candidate(root, candidate)
        except RecordNotFound:
            raise HistoryUnavailable from None
        items.append(_project_summary(record, identity.record_type))
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def read_incident_history(
    incident_id: str,
    *,
    execution_id: str | None = None,
    directory: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Read the canonical record or one exact execution snapshot."""
    if not _valid_incident_id(incident_id):
        raise RecordNotFound
    if execution_id is None:
        identity = _Identity("canonical", incident_id)
        filename = f"{incident_id}.json"
    else:
        if not _valid_execution_id(execution_id):
            raise RecordNotFound
        identity = _Identity("execution", incident_id, execution_id)
        filename = f"{incident_id}.execution-{execution_id}.json"

    root = _configured_root(directory, missing_ok=False)
    candidate = _Candidate(filename, (identity,))
    _, record = _load_candidate(root, candidate)
    return _project_record(record, identity.record_type)
