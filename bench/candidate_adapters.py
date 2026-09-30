"""Loss-preserving, non-claimable G6/G8/G9 raw episode adapters."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from typing import Any

from bench.episode_membership import (
    _parse_raw_jsonl,
    _validate_expected_ids,
    normalize_g9_event_observations,
    ordered_scenario_ids_sha256,
)

_ROW_VARIANTS = {"Zero-Shot Baseline", "SFT Model"}
_G9_VARIANT = "SFT + GRPO"
_G9_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


def adapt_three_arm_raw(
    raw_bytes: bytes,
    *,
    variant: str,
    partition: str,
    expected_scenario_ids: Sequence[str],
    expected_sha256: str,
    source_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Adapt hash-checked observations without inferring incident evidence.

    This accepts only the three required arms. ``expected_scenario_ids`` is
    supplied by a separate partition authority; missing scheduled members
    are reported, never turned into invented raw episodes.
    """
    if not isinstance(raw_bytes, bytes):
        raise TypeError("raw output must be bytes")
    if variant not in (*_ROW_VARIANTS, _G9_VARIANT):
        raise ValueError(f"unsupported three-arm variant {variant!r}")
    if not isinstance(partition, str) or not partition.strip():
        raise ValueError("partition must be a non-empty string")
    expected_ids = _validate_expected_ids(expected_scenario_ids)
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(c not in "0123456789abcdefABCDEF" for c in expected_sha256)
    ):
        raise ValueError("expected SHA-256 must be 64 hexadecimal characters")
    identity_declaration = _validate_source_identity_declaration(
        source_identity,
        allow_unknown=variant == _G9_VARIANT,
    )

    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if source_sha256 != expected_sha256.casefold():
        raise ValueError("raw output SHA-256 mismatch")
    if variant == _G9_VARIANT:
        normalized = normalize_g9_event_observations(raw_bytes, expected_sha256=expected_sha256)
        records = normalized["raw_events"]
        first = records[0]
        if first.get("split") != partition:
            raise ValueError("G9 run_started partition mismatch")
        if first.get("split_sha256") != ordered_scenario_ids_sha256(expected_ids):
            raise ValueError("G9 run_started split_sha256 mismatch")
        run_outcome = _validate_g9_run_observations(
            normalized,
            expected_ids=expected_ids,
            partition=partition,
            identity_declaration=identity_declaration,
        )
        identity_fields = _g9_source_identity_fields(
            records,
            identity_declaration=identity_declaration,
        )
        source_lines = {id(record): line for line, record in enumerate(records, start=1)}
        episodes = [
            _adapt_g9_episode(observed, source_lines, source_sha256, identity_fields)
            for observed in normalized["episodes"]
        ]
    else:
        records = _parse_raw_jsonl(raw_bytes, allow_nonclaimable=True)
        identity_fields = _row_source_identity_fields(
            records,
            variant=variant,
            identity_declaration=identity_declaration,
        )
        episodes = [
            _adapt_diagnosis_row(row, line, source_sha256, identity_fields, variant)
            for line, row in enumerate(records, start=1)
        ]
        for line, row in enumerate(records, start=1):
            if row.get("evaluation_mode") != "empirical":
                raise ValueError(f"raw episode row {line} must declare empirical evaluation mode")
        run_outcome = "records_complete"

    observed_ids = [episode["scenario_id"] for episode in episodes]
    missing_ids = _ordered_missing_ids(expected_ids, observed_ids)
    if missing_ids and variant in _ROW_VARIANTS:
        run_outcome = "partial"
    return {
        "schema_version": 1,
        "source_format": ("g9_event_stream" if variant == _G9_VARIANT else "episode_rows"),
        "variant": variant,
        "partition": partition,
        "source_sha256": source_sha256,
        **deepcopy(identity_fields),
        "evaluation_mode": "NON_EMPIRICAL_OBSERVATION",
        "non_empirical": True,
        "empirical_claim_allowed": False,
        "certification_status": "NOT_CERTIFIED",
        "run_outcome": run_outcome,
        "scheduled_scenario_ids": expected_ids,
        "observed_scenario_ids": observed_ids,
        "missing_scenario_ids": missing_ids,
        "raw_records": records,
        "episodes": episodes,
    }


def _ordered_missing_ids(expected: list[str], observed: list[str]) -> list[str]:
    if len(set(observed)) != len(observed):
        raise ValueError("raw episode membership contains duplicate scenario IDs")
    positions = {scenario_id: index for index, scenario_id in enumerate(expected)}
    if any(scenario_id not in positions for scenario_id in observed):
        raise ValueError("raw episode membership contains an unexpected scenario ID")
    observed_positions = [positions[scenario_id] for scenario_id in observed]
    if observed_positions != sorted(observed_positions):
        raise ValueError("raw episode membership is out of order")
    observed_set = set(observed)
    return [scenario_id for scenario_id in expected if scenario_id not in observed_set]


def _episode_base(
    scenario_id: str,
    source_sha256: str,
    identity_fields: Mapping[str, Any],
    lines: list[int],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scenario_id": scenario_id,
        **deepcopy(dict(identity_fields)),
        "source_sha256": source_sha256,
        "raw_refs": [{"source_sha256": source_sha256, "line": line} for line in lines],
        "events": [],
    }


def _validate_source_identity_declaration(
    source_identity: Mapping[str, Any],
    *,
    allow_unknown: bool,
) -> dict[str, Any]:
    if not isinstance(source_identity, Mapping):
        raise ValueError("source identity declaration must be a mapping")
    identity = deepcopy(dict(source_identity))
    for field in ("run_id", "model"):
        value = identity.get(field)
        if value is None and allow_unknown:
            identity[field] = None
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"source identity declaration requires non-empty {field}")
    return identity


def _source_identity_fields(
    raw_identity: Mapping[str, Any],
    identity_declaration: Mapping[str, Any],
) -> dict[str, Any]:
    identity = {field: raw_identity.get(field) for field in ("run_id", "model")}
    status = (
        "RAW_BOUND"
        if all(
            isinstance(identity[field], str) and identity[field].strip()
            for field in ("run_id", "model")
        )
        else "UNBOUND"
    )
    return {
        "source_identity": identity,
        "source_identity_declaration": deepcopy(dict(identity_declaration)),
        "source_identity_status": status,
    }


def _raw_identity_values(
    record: Mapping[str, Any],
    field: str,
    *,
    label: str,
) -> list[str]:
    values: list[Any] = []
    if field in record:
        values.append(record[field])
    if field == "model" and "provenance" in record:
        provenance = record["provenance"]
        if provenance is not None and not isinstance(provenance, Mapping):
            raise ValueError(f"{label}.provenance must be an object or null")
        if isinstance(provenance, Mapping) and "base_model" in provenance:
            base_model = provenance["base_model"]
            if base_model is not None and not isinstance(base_model, Mapping):
                raise ValueError(f"{label}.provenance.base_model must be an object or null")
            if isinstance(base_model, Mapping) and "id" in base_model:
                values.append(base_model["id"])

    identities: list[str] = []
    for value in values:
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label}.{field} must be non-empty text or null")
        identities.append(value)
    if len(set(identities)) > 1:
        raise ValueError(f"{label} has conflicting {field} identity values")
    return identities


def _g9_event_identity_values(
    record: Mapping[str, Any],
    field: str,
    *,
    label: str,
) -> list[str]:
    values = _raw_identity_values(record, field, label=label)
    result = record.get("result")
    if isinstance(result, Mapping):
        values.extend(
            _raw_identity_values(
                result,
                field,
                label=f"{label}.result",
            )
        )
    if len(set(values)) > 1:
        raise ValueError(f"G9 raw artifact identity mismatch: {label}.{field}")
    return values


def _row_source_identity_fields(
    records: list[dict[str, Any]],
    *,
    variant: str,
    identity_declaration: Mapping[str, Any],
) -> dict[str, Any]:
    raw_identity: dict[str, str | None] = {"run_id": None, "model": None}
    incomplete_fields: set[str] = set()
    for line, record in enumerate(records, start=1):
        if variant == "Zero-Shot Baseline":
            for field in ("run_id", "model"):
                value = record.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"raw episode row {line} requires non-empty requested {field}"
                    )
                if value != identity_declaration[field]:
                    raise ValueError(f"raw episode identity mismatch: row {line} {field}")
                current = raw_identity[field]
                if current is not None and current != value:
                    raise ValueError(
                        f"raw episode rows contain conflicting requested {field} identities"
                    )
                raw_identity[field] = value
            continue
        for field in ("run_id", "model"):
            values = _raw_identity_values(
                record,
                field,
                label=f"raw episode row {line}",
            )
            if not values:
                incomplete_fields.add(field)
            for value in values:
                declared = identity_declaration.get(field)
                if declared is not None and value != declared:
                    raise ValueError(f"raw episode identity mismatch: row {line} {field}")
                current = raw_identity[field]
                if current is not None and current != value:
                    raise ValueError(f"raw episode rows contain conflicting {field} identities")
                raw_identity[field] = value
    if variant == "Zero-Shot Baseline":
        fields = _source_identity_fields(raw_identity, identity_declaration)
        fields["source_identity_model_basis"] = "requested_model_name_only"
        fields["source_identity_limitation"] = (
            "requested model name does not attest the served model digest"
        )
        return fields
    if incomplete_fields or any(value is None for value in raw_identity.values()):
        missing_fields = [
            field
            for field in ("run_id", "model")
            if field in incomplete_fields or raw_identity[field] is None
        ]
        raw_identity = {"run_id": None, "model": None}
        fields = _source_identity_fields(raw_identity, identity_declaration)
        if variant == "SFT Model":
            fields["source_identity_limitation"] = (
                f"raw rows lack {', '.join(missing_fields)}; identity remains UNBOUND"
            )
        return fields
    return _source_identity_fields(raw_identity, identity_declaration)


def _g9_source_identity_fields(
    records: list[dict[str, Any]],
    *,
    identity_declaration: Mapping[str, Any],
) -> dict[str, Any]:
    raw_identity: dict[str, str | None] = {"run_id": None, "model": None}
    terminal = records[-1]
    if terminal.get("event") == "run_completed":
        summary = terminal["summary"]
        raw_identity = {
            "run_id": summary["run_id"],
            "model": summary["model"],
        }
    return _source_identity_fields(raw_identity, identity_declaration)


def _bind_g9_identity_record(
    record: Mapping[str, Any],
    identity_declaration: Mapping[str, Any],
    *,
    label: str,
    require_fields: bool = False,
) -> None:
    for field in ("run_id", "model"):
        identities = _raw_identity_values(record, field, label=f"G9 {label}")
        declared = identity_declaration.get(field)
        if require_fields:
            direct = record.get(field)
            if not isinstance(direct, str) or not direct.strip():
                raise ValueError(f"G9 {label} requires non-empty {field}")
            if declared is not None and direct != declared:
                raise ValueError(f"G9 raw artifact identity mismatch: {label}.{field}")
        if declared is not None and any(value != declared for value in identities):
            raise ValueError(f"G9 raw artifact identity mismatch: {label}.{field}")


def _validate_g9_run_observations(
    normalized: Mapping[str, Any],
    *,
    expected_ids: list[str],
    partition: str,
    identity_declaration: Mapping[str, Any],
) -> str:
    raw_events = normalized["raw_events"]
    if not isinstance(raw_events, list) or not raw_events:
        raise ValueError("G9 normalized stream requires a run_started event")
    _validate_g9_identity_consistency(raw_events, identity_declaration)
    _bind_g9_identity_record(
        raw_events[0],
        identity_declaration,
        label="run_started",
    )

    run_outcome = normalized["run_outcome"]
    terminal = raw_events[-1]
    terminal_name = terminal.get("event")
    if terminal_name in {"run_completed", "run_interrupted"}:
        _bind_g9_identity_record(
            terminal,
            identity_declaration,
            label=terminal_name,
        )
    if run_outcome != "completed":
        return run_outcome
    if terminal_name != "run_completed":
        raise ValueError("G9 completed run lacks its final run_completed event")

    summary = terminal.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError("G9 run_completed requires a summary object")
    _bind_g9_identity_record(
        summary,
        identity_declaration,
        label="run_completed summary",
        require_fields=True,
    )
    if summary.get("split") != partition or summary.get(
        "split_sha256"
    ) != ordered_scenario_ids_sha256(expected_ids):
        raise ValueError("G9 run_completed summary split does not match scheduled membership")

    terminal_episodes = []
    for episode in normalized["episodes"]:
        if episode.get("outcome") not in {"completed", "failed", "unscorable"}:
            raise ValueError("G9 run_completed contains a non-terminal episode")
        result = episode.get("result")
        if not isinstance(result, Mapping):
            raise ValueError("G9 terminal episode requires a result object")
        terminal_episodes.append(result)

    observed_ids = [episode["scenario_id"] for episode in normalized["episodes"]]
    missing_ids = _ordered_missing_ids(expected_ids, observed_ids)
    expected_counts = {
        "scenario_count": len(expected_ids),
        "completed_episodes": len(terminal_episodes),
        "scorable_episodes": sum(result.get("scorable") is True for result in terminal_episodes),
        "unscorable_episodes": sum(
            result.get("status") == "unscorable" or result.get("scorable") is False
            for result in terminal_episodes
        ),
        "failed_episodes": sum(result.get("status") == "failed" for result in terminal_episodes),
    }
    for field, expected in expected_counts.items():
        value = summary.get(field)
        if type(value) is not int or value != expected:
            raise ValueError(f"G9 run_completed summary {field} does not match observed episodes")

    claimed = summary.get("empirical_claim_allowed")
    expected_claim = bool(terminal_episodes) and all(
        result.get("status") == "ok" and result.get("scorable") is True
        for result in terminal_episodes
    )
    if type(claimed) is not bool or claimed is not expected_claim:
        raise ValueError("G9 run_completed summary empirical_claim_allowed conflicts with episodes")
    if missing_ids and claimed:
        raise ValueError("G9 run_completed summary claims a run with missing scheduled episodes")
    return "partial" if missing_ids else "completed"


def _validate_g9_identity_consistency(
    raw_events: list[dict[str, Any]],
    identity_declaration: Mapping[str, Any],
) -> None:
    for field in ("run_id", "model"):
        raw_values: list[str] = []
        for index, record in enumerate(raw_events, start=1):
            raw_values.extend(
                _g9_event_identity_values(
                    record,
                    field,
                    label=f"G9 event {index}",
                )
            )
            if record.get("event") == "run_completed":
                summary = record.get("summary")
                if isinstance(summary, Mapping):
                    raw_values.extend(
                        _raw_identity_values(
                            summary,
                            field,
                            label="G9 run_completed summary",
                        )
                    )
        if len(set(raw_values)) > 1:
            raise ValueError(f"G9 raw artifact identity mismatch: conflicting {field}")
        declared = identity_declaration.get(field)
        if declared is not None and any(value != declared for value in raw_values):
            raise ValueError(f"G9 raw artifact identity mismatch: {field}")


def _adapt_diagnosis_row(
    row: dict[str, Any],
    line: int,
    source_sha256: str,
    identity_fields: Mapping[str, Any],
    variant: str,
) -> dict[str, Any]:
    scenario_id = row.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError(f"raw episode row {line} requires scenario_id")
    episode = _episode_base(scenario_id, source_sha256, identity_fields, [line])
    if row.get("status") == "ok":
        prediction = _validate_successful_diagnosis_row(row, line, variant)
        episode["events"].append(
            {
                "event": "diagnosis_output",
                "scenario_id": scenario_id,
                "label": prediction["root_cause"],
                "raw_ref": episode["raw_refs"][0],
            }
        )
    else:
        reason = row.get("error_category") or row.get("outcome") or "missing_prediction"
        if not isinstance(reason, str) or not reason.strip():
            reason = "missing_prediction"
        episode["events"].append(
            {
                "event": "model_failure",
                "scenario_id": scenario_id,
                "reason": reason,
                "raw_ref": episode["raw_refs"][0],
            }
        )
    return episode


def _validate_successful_diagnosis_row(
    row: Mapping[str, Any],
    line: int,
    variant: str,
) -> dict[str, Any]:
    raw_response = row.get("raw_model_response")
    if not isinstance(raw_response, str):
        raise ValueError(f"raw episode row {line} has invalid successful prediction")
    try:
        prediction_json = json.dumps(
            row.get("prediction"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        parsed_prediction = _parse_candidate_diagnosis(prediction_json, variant)
        raw_prediction = _parse_candidate_diagnosis(raw_response, variant)
        parsed_canonical = json.dumps(
            parsed_prediction,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        raw_canonical = json.dumps(
            raw_prediction,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError(
            f"raw episode row {line} has invalid successful prediction"
        ) from None
    if parsed_canonical != raw_canonical:
        raise ValueError(
            f"raw episode row {line} prediction differs from raw_model_response"
        )
    return raw_prediction


def _unique_diagnostic_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate diagnostic JSON key")
        value[key] = item
    return value


def _parse_candidate_diagnosis(raw_text: str, variant: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1)
        text = re.sub(r"\s*```$", "", text, count=1)
    prediction = json.loads(text, object_pairs_hook=_unique_diagnostic_object)
    if not isinstance(prediction, dict):
        raise ValueError("diagnostic response must be an object")
    root_cause = prediction.get("root_cause")
    if not isinstance(root_cause, str) or not root_cause.strip():
        raise ValueError("diagnostic root cause is missing")
    if prediction.get("severity") not in {"P0", "P1", "P2", "P3"}:
        raise ValueError("diagnostic severity is invalid")
    affected = prediction.get("affected_services")
    if (
        not isinstance(affected, list)
        or (variant == "SFT Model" and not affected)
        or any(not isinstance(item, str) or not item.strip() for item in affected)
    ):
        raise ValueError("diagnostic services are invalid")
    confidence = prediction.get("confidence")
    if variant == "Zero-Shot Baseline":
        valid_type = type(confidence) in (int, float)
    else:
        valid_type = not isinstance(confidence, bool) and isinstance(
            confidence, (int, float)
        )
    if not valid_type:
        raise ValueError("diagnostic confidence is invalid")
    try:
        finite = math.isfinite(confidence)
    except OverflowError:
        finite = False
    if not finite or not 0 <= confidence <= 1:
        raise ValueError("diagnostic confidence is invalid")
    return prediction


def _adapt_g9_episode(
    observed: dict[str, Any],
    source_lines: dict[int, int],
    source_sha256: str,
    identity_fields: Mapping[str, Any],
) -> dict[str, Any]:
    raw_events = observed["events"]
    lines = [source_lines[id(event)] for event in raw_events]
    episode = _episode_base(observed["scenario_id"], source_sha256, identity_fields, lines)
    episode["diagnosis_observation"] = {
        "status": "unavailable",
        "reason": "g9_diagnosis_not_observed",
        "source_format": "g9_event_stream",
        "source_sha256": source_sha256,
        "raw_refs": deepcopy(episode["raw_refs"]),
    }
    for event, line in zip(raw_events, lines, strict=True):
        name = event["event"]
        raw_ref = {"source_sha256": source_sha256, "line": line}
        if name == "pre_action_verification":
            episode["events"].append(
                {
                    "event": name,
                    "verification": _map_verification(event.get("verification")),
                    **_g9_source_timestamp_metadata(event),
                    "raw_ref": raw_ref,
                }
            )
        elif name == "step_result":
            timestamp_metadata = _g9_source_timestamp_metadata(event)
            record = event.get("record")
            if not isinstance(record, Mapping):
                raise ValueError("G9 step_result requires an objective record")
            action = record.get("parsed_action")
            if action is not None and not isinstance(action, Mapping):
                raise ValueError("G9 parsed_action must be an object or null")
            claimed = record.get("agent_claimed_resolved")
            if claimed is not None and type(claimed) is not bool:
                raise ValueError("G9 agent_claimed_resolved must be boolean or null")
            executed = _g9_execution_observation(record, action)
            if executed is not None:
                episode["events"].append(
                    {
                        "event": "action_result",
                        "executed": executed,
                        "agent_claimed_resolved": claimed,
                        "parsed_action": deepcopy(action),
                        **timestamp_metadata,
                        "raw_ref": raw_ref,
                    }
                )
            if "verification" in record:
                episode["events"].append(
                    {
                        "event": "post_action_verification",
                        "verification": _map_verification(record["verification"]),
                        **timestamp_metadata,
                        "raw_ref": raw_ref,
                    }
                )
            if record.get("failure"):
                episode["events"].append(
                    {
                        "event": "step_failure",
                        "reason": record["failure"],
                        **timestamp_metadata,
                        "raw_ref": raw_ref,
                    }
                )
        elif name in {
            "episode_failed",
            "episode_unscorable",
            "episode_interrupted",
            "run_interrupted",
        }:
            episode["events"].append(
                {
                    "event": ("episode_interrupted" if name == "run_interrupted" else name),
                    "reason": (
                        event.get("result", {}).get("failure")
                        if isinstance(event.get("result"), Mapping)
                        else None
                    ),
                    **_g9_source_timestamp_metadata(event),
                    "raw_ref": raw_ref,
                }
            )
    return episode


def _g9_source_timestamp_metadata(event: Mapping[str, Any]) -> dict[str, str]:
    """Keep envelope persistence time as source metadata, never as an incident clock."""
    if "recorded_at" not in event:
        return {}
    value = event["recorded_at"]
    if not isinstance(value, str) or not _G9_RFC3339_RE.fullmatch(value):
        raise ValueError("G9 event recorded_at must be a timezone-aware RFC 3339 string")
    try:
        timestamp = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("G9 event recorded_at is not a valid RFC 3339 timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("G9 event recorded_at must include a timezone")
    return {"source_event_recorded_at": value}


def _g9_execution_observation(
    record: Mapping[str, Any],
    parsed_action: Mapping[str, Any] | None,
) -> bool | None:
    """Return execution only when the emitted action record supports it."""
    environment_actions: list[dict[str, Any]] | None = None
    if "environment_result" in record:
        environment_result = record["environment_result"]
        if environment_result is not None and not isinstance(environment_result, Mapping):
            raise ValueError("G9 environment_result must be an object or null")
        if isinstance(environment_result, Mapping) and "executed_actions" in environment_result:
            raw_actions = environment_result["executed_actions"]
            if not isinstance(raw_actions, list):
                raise ValueError("G9 environment_result.executed_actions must be a list")
            if len(raw_actions) > 1:
                raise ValueError(
                    "G9 environment_result.executed_actions must contain at most one action"
                )
            environment_actions = [
                _g9_action_record(action, "environment_result.executed_actions")
                for action in raw_actions
            ]

    if "executed_action" not in record:
        return None

    raw_action = record["executed_action"]
    if raw_action is None:
        if environment_actions is None:
            return None
        if not environment_actions:
            return False
        raise ValueError("G9 executed_action is null but environment_result reports execution")

    action = _g9_action_record(raw_action, "executed_action")
    if parsed_action is None:
        raise ValueError("G9 executed_action exists without a parsed_action")
    if action["tool"] != parsed_action.get("tool") or action["arguments"] != parsed_action.get(
        "arguments"
    ):
        raise ValueError("G9 executed_action conflicts with parsed_action")
    if environment_actions is not None and (
        not environment_actions or action != environment_actions[0]
    ):
        raise ValueError("G9 executed_action conflicts with environment_result.executed_actions")
    return True


def _g9_action_record(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"G9 {label} must be an object")
    tool = value.get("tool")
    arguments = value.get("arguments")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError(f"G9 {label}.tool must be non-empty text")
    if not isinstance(arguments, Mapping):
        raise ValueError(f"G9 {label}.arguments must be an object")
    return deepcopy(dict(value))


def _map_verification(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("G9 verification must be an object or null")
    mapped = deepcopy(dict(value))
    checks = mapped.get("checks")
    if checks is None:
        return mapped
    if not isinstance(checks, list):
        raise ValueError("G9 verification checks must be a list")
    mapped_checks = []
    for index, check in enumerate(checks):
        if not isinstance(check, Mapping):
            raise ValueError(f"G9 verification check {index} must be an object")
        check_id = check.get("check_id", check.get("name"))
        if not isinstance(check_id, str) or not check_id.strip():
            raise ValueError(f"G9 verification check {index} lacks a check ID")
        if "check_id" in check and "name" in check and check["check_id"] != check["name"]:
            raise ValueError("G9 verification check name conflicts with check_id")
        mapped_check = deepcopy(dict(check))
        mapped_check.pop("name", None)
        mapped_check["check_id"] = check_id
        mapped_checks.append(mapped_check)
    mapped["checks"] = mapped_checks
    return mapped
