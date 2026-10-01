"""Bounded, append-only, non-empirical GRPO lifecycle journal."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import stat
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from training.sft_provenance import (
    _read_bounded_snapshot,
    has_redirecting_path_component,
)

SCHEMA = "atlasops.grpo_observation_journal"
SCHEMA_VERSION = 1
MAX_OBSERVATION_JOURNAL_BYTES = 1024 * 1024
# Structural bound for serialized event and record counts, not a training budget.
_MAX_SAMPLES_PER_GROUP = 4096
_EVENTS = frozenset(
    {
        "group_started",
        "group_observed",
        "sample_started",
        "sample_execute_started",
        "group_finished",
    }
)
_ROW_FIELDS = {
    "schema",
    "schema_version",
    "sequence",
    "group_id",
    "event",
    "result_classification",
    "certification_status",
    "data",
}
_REQUIRED_DATA_FIELDS = {
    "group_started": {"scenario_id"},
    "group_observed": {
        "scenario_id",
        "observation_digest",
        "state_prompt_sha256",
        "observed_at",
    },
    "sample_started": {"scenario_id", "sample_index", "completion_sha256"},
    "sample_execute_started": {"scenario_id", "sample_index", "completion_sha256"},
    "group_finished": {
        "scenario_id",
        "status",
        "error_type",
        "finish_callback_status",
        "finish_error_type",
        "records",
    },
}
_RECORD_FIELDS = {
    "sample_index",
    "result_status",
    "verifier_status",
    "failure",
    "reward",
    "scorable",
    "approval_decision",
    "terminal_block",
}
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_GROUP_ID = re.compile(r"[0-9a-f]{32}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_SCENARIO_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_FAILURE_CODES = frozenset(
    {
        "invalid_before_action_observation",
        "before_action_state_drift",
        "before_action_timestamp_invalid",
        "invalid_environment_result",
        "action_lineage_mismatch",
        "invalid_blocked_result",
    }
)
_BLOCK_CATEGORIES = frozenset(
    {
        "already_resolved",
        "approval_required",
        "invalid_action",
        "missing_evidence",
        "policy_block",
        "tool_unavailable",
    }
)


def _require_text(value: Any, field: str, *, maximum: int = 128) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in value)
    ):
        raise ValueError(f"Invalid observation journal {field}")
    return value


def _validate_timestamp(value: Any) -> str:
    text = _require_text(value, "observed_at", maximum=40)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid observation journal observed_at") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Invalid observation journal observed_at")
    return text


def _validate_record(value: Any, expected_index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - _RECORD_FIELDS:
        raise ValueError("Invalid observation journal record fields")
    record = dict(value)
    index = record.get("sample_index")
    if type(index) is not int or index != expected_index:
        raise ValueError("Invalid observation journal sample index")
    if type(record.get("scorable")) is not bool:
        raise ValueError("Invalid observation journal scorable flag")
    if record.get("result_status") not in {None, "ok", "blocked", "unscorable"}:
        raise ValueError("Invalid observation journal result status")
    if record.get("verifier_status") not in {
        None,
        "passed",
        "failed",
        "error",
        "inconclusive",
        "not_resolved",
        "unverified",
    }:
        raise ValueError("Invalid observation journal verifier status")
    failure = record.get("failure")
    if failure is not None:
        failure = _require_text(failure, "failure", maximum=128)
        allowed = (
            failure in _FAILURE_CODES
            or _IDENTIFIER.fullmatch(failure) is not None
            or re.fullmatch(
                r"(?:before_action_exception|before_action_state_invalid|"
                r"execute_exception):[A-Za-z][A-Za-z0-9_]{0,63}",
                failure,
            )
            is not None
            or (
                failure.startswith("environment_blocked:")
                and failure.partition(":")[2] in _BLOCK_CATEGORIES
            )
        )
        if not allowed:
            raise ValueError("Invalid observation journal failure code")
    reward = record.get("reward")
    if reward is not None and (
        isinstance(reward, bool)
        or not isinstance(reward, (int, float))
        or (isinstance(reward, float) and not math.isfinite(reward))
    ):
        raise ValueError("Invalid observation journal reward")
    decision = record.get("approval_decision")
    if decision not in {
        None,
        "approved",
        "rejected",
        "timeout",
        "missing",
        "identity_missing",
        "unavailable",
        "invalid",
    }:
        raise ValueError("Invalid observation journal approval decision")
    terminal = record.get("terminal_block")
    if terminal is not None:
        if (
            not isinstance(terminal, Mapping)
            or set(terminal) != {"category"}
            or terminal["category"] not in _BLOCK_CATEGORIES
        ):
            raise ValueError("Invalid observation journal terminal block")
        record["terminal_block"] = {"category": terminal["category"]}
    return record


def _validate_data(event: str, value: Any) -> dict[str, Any]:
    if event not in _EVENTS or not isinstance(value, Mapping):
        raise ValueError("Invalid observation journal event data")
    data = dict(value)
    if set(data) != _REQUIRED_DATA_FIELDS[event]:
        raise ValueError("Invalid observation journal event fields")
    scenario = data["scenario_id"]
    if not isinstance(scenario, str) or not _SCENARIO_ID.fullmatch(scenario):
        raise ValueError("Invalid observation journal scenario_id")
    if event == "group_observed":
        for field in ("observation_digest", "state_prompt_sha256"):
            if not isinstance(data[field], str) or not _HASH.fullmatch(data[field]):
                raise ValueError(f"Invalid observation journal {field}")
        _validate_timestamp(data["observed_at"])
    elif event in {"sample_started", "sample_execute_started"}:
        if (
            type(data["sample_index"]) is not int
            or data["sample_index"] < 0
            or data["sample_index"] >= _MAX_SAMPLES_PER_GROUP
            or not isinstance(data["completion_sha256"], str)
            or not _HASH.fullmatch(data["completion_sha256"])
        ):
            raise ValueError("Invalid observation journal sample marker")
    elif event == "group_finished":
        if data["status"] not in {"completed", "failed", "interrupted"}:
            raise ValueError("Invalid observation journal final status")
        if data["finish_callback_status"] not in {"returned", "raised"}:
            raise ValueError("Invalid observation journal finish status")
        for field in ("error_type", "finish_error_type"):
            if data[field] is not None and not _IDENTIFIER.fullmatch(
                _require_text(data[field], field, maximum=64)
            ):
                raise ValueError(f"Invalid observation journal {field}")
        records = data["records"]
        if not isinstance(records, list) or len(records) > _MAX_SAMPLES_PER_GROUP:
            raise ValueError("Invalid observation journal records")
        data["records"] = [
            _validate_record(record, index) for index, record in enumerate(records)
        ]
        if data["status"] == "completed" and (
            data["finish_callback_status"] != "returned"
            or data["error_type"] is not None
            or data["finish_error_type"] is not None
            or any(
                not record["scorable"]
                or record.get("result_status") != "ok"
                or record.get("verifier_status") not in {"passed", "failed"}
                or record.get("reward") is None
                or record.get("failure") is not None
                for record in data["records"]
            )
        ):
            raise ValueError("Invalid observation journal completed result")
    return data


def _apply_event(
    state: dict[str, Any], event: str, group_id: str, data: dict[str, Any]
) -> None:
    active = state["active"]
    if event == "group_started":
        if active is not None or group_id in state["used"]:
            raise ValueError("Invalid observation journal group order")
        state["used"].add(group_id)
        state["active"] = {
            "group_id": group_id,
            "scenario_id": data["scenario_id"],
            "phase": "started",
            "samples": [],
        }
        return
    if active is None or active["group_id"] != group_id:
        raise ValueError("Invalid observation journal group order")
    if data["scenario_id"] != active["scenario_id"]:
        raise ValueError("Invalid observation journal scenario order")
    phase = active["phase"]
    if event == "group_observed":
        if phase != "started":
            raise ValueError("Invalid observation journal event order")
        active["phase"] = "observed"
    elif event == "sample_started":
        if phase not in {"observed", "executed"}:
            raise ValueError("Invalid observation journal event order")
        if data["sample_index"] != len(active["samples"]):
            raise ValueError("Invalid observation journal sample order")
        active["samples"].append(
            {
                "completion_sha256": data["completion_sha256"],
                "executed": False,
            }
        )
        active["phase"] = "sample_started"
    elif event == "sample_execute_started":
        if phase != "sample_started" or not active["samples"]:
            raise ValueError("Invalid observation journal event order")
        sample = active["samples"][-1]
        if (
            data["sample_index"] != len(active["samples"]) - 1
            or data["completion_sha256"] != sample["completion_sha256"]
        ):
            raise ValueError("Invalid observation journal sample binding")
        sample["executed"] = True
        active["phase"] = "executed"
    elif event == "group_finished":
        samples = active["samples"]
        if len(data["records"]) != len(samples):
            raise ValueError("Invalid observation journal final records")
        if data["status"] == "completed" and (
            not samples or not all(sample["executed"] for sample in samples)
        ):
            raise ValueError("Invalid observation journal completed group")
        state["groups"].append(
            {
                "group_id": group_id,
                "scenario_id": active["scenario_id"],
                "status": data["status"],
                "sample_count": len(samples),
            }
        )
        state["active"] = None


def _initial_state() -> dict[str, Any]:
    return {"active": None, "used": set(), "groups": []}


def _safe_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


class ObservationJournal:
    """Create one exclusive journal and durably append allowlisted event rows."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = _safe_path(path)
        self._fd: int | None = None
        self._enter_attempted = False
        self._closed = False
        self._poisoned = False
        self._sequence = 1
        self._bytes_written = 0
        self._state = _initial_state()
        self._lock = threading.Lock()

    def __enter__(self) -> ObservationJournal:
        with self._lock:
            if self._enter_attempted:
                raise RuntimeError("Observation journal is one-shot")
            self._enter_attempted = True
            if has_redirecting_path_component(self._path):
                raise ValueError("Observation journal path contains a redirect")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_APPEND", 0)
            flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            self._fd = os.open(self._path, flags, 0o600)
            try:
                self._verify_path()
            except BaseException:
                os.close(self._fd)
                self._fd = None
                self._poisoned = True
                raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        with self._lock:
            self._closed = True
            if self._fd is not None:
                fd, self._fd = self._fd, None
                os.close(fd)
        return False

    def _verify_path(self) -> None:
        if self._fd is None or has_redirecting_path_component(self._path):
            raise OSError("Observation journal path changed or redirected")
        try:
            path_info = self._path.lstat()
            fd_info = os.fstat(self._fd)
        except OSError as exc:
            raise OSError("Observation journal path is unavailable") from exc
        if (
            not stat.S_ISREG(path_info.st_mode)
            or not stat.S_ISREG(fd_info.st_mode)
            or not os.path.samestat(path_info, fd_info)
            or getattr(fd_info, "st_nlink", 1) != 1
            or fd_info.st_size != self._bytes_written
        ):
            raise OSError("Observation journal path changed")

    def append(self, event: str, group_id: str, data: Mapping[str, Any]) -> None:
        if self._closed or self._fd is None:
            raise RuntimeError("Observation journal is not open")
        if not self._lock.acquire(blocking=False):
            self._poisoned = True
            raise RuntimeError("Concurrent observation journal append rejected")
        try:
            if self._closed or self._fd is None:
                raise RuntimeError("Observation journal is not open")
            if self._poisoned:
                raise RuntimeError("Observation journal is poisoned")
            if not isinstance(group_id, str) or not _GROUP_ID.fullmatch(group_id):
                raise ValueError("Invalid observation journal group_id")
            safe_data = _validate_data(event, data)
            candidate_state = copy.deepcopy(self._state)
            _apply_event(candidate_state, event, group_id, safe_data)
            row = {
                "schema": SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "sequence": self._sequence,
                "group_id": group_id,
                "event": event,
                "result_classification": "NON_EMPIRICAL",
                "certification_status": "NOT_CERTIFIED",
                "data": safe_data,
            }
            encoded = (
                json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    allow_nan=False,
                ).encode("ascii")
                + b"\n"
            )
            if self._bytes_written + len(encoded) > MAX_OBSERVATION_JOURNAL_BYTES:
                raise ValueError("Observation journal byte limit exceeded")
            self._verify_path()
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(self._fd, remaining)
                if written <= 0:
                    raise OSError("Observation journal append made no progress")
                remaining = remaining[written:]
            os.fsync(self._fd)
            self._bytes_written += len(encoded)
            self._verify_path()
            self._state = candidate_state
            self._sequence += 1
        except BaseException:
            self._poisoned = True
            raise
        finally:
            self._lock.release()


def _report(
    status: str,
    *,
    events: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    rows = events or []
    result: dict[str, Any] = {
        "status": status,
        "resume_allowed": False,
        "result_classification": "NON_EMPIRICAL",
        "certification_status": "NOT_CERTIFIED",
        "event_count": len(rows),
        "group_count": len(state["groups"]) if state is not None else 0,
        "groups": list(state["groups"]) if state is not None else [],
        "events": rows,
    }
    if reason is not None:
        result["reason"] = reason
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate journal key")
        result[key] = value
    return result


def read_observation_journal(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read one bounded snapshot without repairing, truncating, or resuming it."""
    try:
        raw, read_status = _read_bounded_snapshot(
            _safe_path(path), MAX_OBSERVATION_JOURNAL_BYTES
        )
    except (OSError, TypeError, ValueError):
        return _report("UNAVAILABLE", reason="unavailable")
    if raw is None:
        if read_status == "too_large":
            return _report("INVALID", reason="too_large")
        if read_status in {"redirected", "not_regular", "changed"}:
            return _report("INVALID", reason="unsafe_path")
        return _report("UNAVAILABLE", reason="unavailable")
    state = _initial_state()
    events: list[dict[str, Any]] = []
    if not raw:
        return _report("INCOMPLETE", events=events, state=state, reason="empty")
    complete = raw.endswith(b"\n")
    lines = raw.split(b"\n")
    if complete:
        lines.pop()
    else:
        lines.pop()
    for line in lines:
        if not line:
            return _report("INVALID", events=events, state=state, reason="empty_line")
        try:
            row = json.loads(
                line.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError("Non-finite JSON number")
                ),
            )
            if (
                not isinstance(row, dict)
                or set(row) != _ROW_FIELDS
                or row.get("schema") != SCHEMA
                or type(row.get("schema_version")) is not int
                or row.get("schema_version") != SCHEMA_VERSION
                or type(row.get("sequence")) is not int
                or row["sequence"] != len(events) + 1
                or not isinstance(row.get("group_id"), str)
                or not _GROUP_ID.fullmatch(row["group_id"])
                or row.get("event") not in _EVENTS
                or row.get("result_classification") != "NON_EMPIRICAL"
                or row.get("certification_status") != "NOT_CERTIFIED"
            ):
                raise ValueError("Invalid journal row")
            row["data"] = _validate_data(row["event"], row["data"])
            _apply_event(state, row["event"], row["group_id"], row["data"])
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError):
            return _report("INVALID", events=events, state=state, reason="invalid_row")
        events.append(row)
    if not complete or state["active"] is not None or not state["groups"]:
        return _report("INCOMPLETE", events=events, state=state, reason="not_finalized")
    return _report("RECORDED", events=events, state=state)
