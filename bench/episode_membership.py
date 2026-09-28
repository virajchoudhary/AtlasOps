"""Strict membership extraction from hash-checked Stage 13 JSONL outputs."""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections.abc import Sequence
from typing import Any

MAX_RAW_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_RAW_OUTPUT_LINE_BYTES = 2 * 1024 * 1024
MAX_RAW_OUTPUT_RECORDS = 100_000
_MAX_SCENARIO_ID_CHARS = 256

_G9_PROGRESS_EVENTS = {
    "pre_action_verification",
    "step_failure",
    "policy_output",
    "approval_requested",
    "approval_decision",
    "step_result",
}
_G9_TERMINAL_EVENTS = {
    "episode_completed",
    "episode_failed",
    "episode_interrupted",
    "episode_unscorable",
}
def ordered_scenario_ids_sha256(scenario_ids: Sequence[str]) -> str:
    payload = json.dumps(
        list(scenario_ids),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unique_raw_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"duplicate field {key!r}")
        record[key] = value
    return record


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number {value!r}")
    return parsed


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r}")


def _reject_non_empirical_markers(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = key.casefold()
            if normalized_key == "evaluation_mode" and (
                not isinstance(item, str) or item.strip().casefold() != "empirical"
            ):
                raise ValueError("non-empirical marker evaluation_mode")
            if normalized_key == "non_empirical" and item is not False:
                raise ValueError("non-empirical marker non_empirical")
            if normalized_key == "test_only_synthetic_fixture":
                raise ValueError("non-empirical marker test_only_synthetic_fixture")
            if normalized_key == "mock_eval" and item is not False:
                raise ValueError("non-empirical marker mock_eval")
            if normalized_key == "empirical_claim_allowed" and item is not True:
                raise ValueError("non-empirical marker empirical_claim_allowed")
            _reject_non_empirical_markers(item)
    elif isinstance(value, list):
        for item in value:
            _reject_non_empirical_markers(item)


def _parse_raw_jsonl(raw_bytes: bytes) -> list[dict[str, Any]]:
    if len(raw_bytes) > MAX_RAW_OUTPUT_BYTES:
        raise ValueError(
            f"raw JSONL exceeds {MAX_RAW_OUTPUT_BYTES} bytes"
        )
    records: list[dict[str, Any]] = []
    source = io.BytesIO(raw_bytes)
    line_number = 0
    while line := source.readline(MAX_RAW_OUTPUT_LINE_BYTES + 1):
        line_number += 1
        if len(line) > MAX_RAW_OUTPUT_LINE_BYTES:
            raise ValueError(
                f"raw JSONL line {line_number} exceeds "
                f"{MAX_RAW_OUTPUT_LINE_BYTES} bytes"
            )
        if line.endswith(b"\n"):
            line = line[:-1]
        if line.endswith(b"\r"):
            line = line[:-1]
        if not line.strip():
            raise ValueError(f"raw JSONL line {line_number} is empty")
        if line_number > MAX_RAW_OUTPUT_RECORDS:
            raise ValueError(
                f"raw JSONL exceeds {MAX_RAW_OUTPUT_RECORDS} records"
            )
        try:
            decoded = line.decode("utf-8")
            record = json.loads(
                decoded,
                object_pairs_hook=_unique_raw_json_object,
                parse_float=_parse_finite_float,
                parse_constant=_reject_non_finite_constant,
            )
            _reject_non_empirical_markers(record)
        except UnicodeDecodeError as exc:
            raise ValueError(f"raw JSONL line {line_number} is not UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"raw JSONL line {line_number} is malformed JSONL") from exc
        except ValueError as exc:
            raise ValueError(f"raw JSONL line {line_number}: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(
                f"raw JSONL line {line_number} must be a JSON object"
            )
        records.append(record)
    if not records:
        raise ValueError("raw JSONL contains no records")
    return records


def _scenario_id(record: dict[str, Any], *, label: str) -> str:
    scenario_id = record.get("scenario_id")
    if (
        not isinstance(scenario_id, str)
        or not scenario_id
        or len(scenario_id) > _MAX_SCENARIO_ID_CHARS
        or not scenario_id.isprintable()
    ):
        raise ValueError(f"{label} requires a safe top-level scenario_id")
    return scenario_id


def _validate_expected_ids(expected_ids: Sequence[str]) -> list[str]:
    if (
        not isinstance(expected_ids, Sequence)
        or isinstance(expected_ids, str | bytes)
        or not expected_ids
        or any(
            not isinstance(item, str)
            or not item
            or len(item) > _MAX_SCENARIO_ID_CHARS
            or not item.isprintable()
            for item in expected_ids
        )
    ):
        raise ValueError("expected ordered scenario IDs must be non-empty safe strings")
    ids = list(expected_ids)
    if len(set(ids)) != len(ids):
        raise ValueError("expected ordered scenario IDs must be unique")
    return ids


def _extract_episode_rows(
    records: list[dict[str, Any]], expected_ids: list[str], partition: str
) -> list[str]:
    for index, record in enumerate(records, start=1):
        mode = record.get("evaluation_mode")
        if not isinstance(mode, str) or mode.strip().casefold() != "empirical":
            raise ValueError(
                f"raw episode row {index} must declare evaluation_mode='empirical'"
            )
    scenario_ids = [
        _scenario_id(record, label=f"raw episode row {index}")
        for index, record in enumerate(records, start=1)
    ]
    if len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError("raw episode membership has duplicate scenario_id values")
    if scenario_ids != expected_ids:
        raise ValueError(
            f"raw episode membership mismatch for {partition}: expected "
            f"{len(expected_ids)} ordered IDs, observed {len(scenario_ids)}"
        )
    return scenario_ids


def _event_scenario_id(
    record: dict[str, Any], *, label: str, required: bool = True
) -> str | None:
    if "scenario_id" not in record and not required:
        return None
    return _scenario_id(record, label=label)


def _extract_g9_event_membership(
    records: list[dict[str, Any]],
    expected_ids: list[str],
    partition: str,
) -> list[str]:
    first = records[0]
    if first.get("event") != "run_started":
        raise ValueError("G9 event stream must begin with exactly one run_started")
    if first.get("evaluation_mode") != "EMPIRICAL":
        raise ValueError("G9 run_started must declare EMPIRICAL evaluation mode")
    if first.get("split") != partition:
        raise ValueError("G9 run_started split does not match the artifact partition")
    expected_split_sha256 = ordered_scenario_ids_sha256(expected_ids)
    if first.get("split_sha256") != expected_split_sha256:
        raise ValueError("G9 run_started split_sha256 does not match expected membership")
    if "scenario_id" in first:
        raise ValueError("G9 run_started cannot be scoped to one scenario_id")

    completed_ids: list[str] = []
    active_scenario_id: str | None = None
    saw_run_completed = False

    for index, record in enumerate(records[1:], start=2):
        event = record.get("event")
        if not isinstance(event, str):
            raise ValueError(f"G9 event record {index} requires an event name")
        if saw_run_completed:
            raise ValueError("G9 run_completed must be the final event")
        if event == "run_started":
            raise ValueError("G9 event stream contains a duplicate run_started")
        if event == "run_completed":
            if "scenario_id" in record:
                raise ValueError("G9 run_completed cannot be scoped to one scenario_id")
            if active_scenario_id is not None:
                raise ValueError("G9 run_completed occurred before the episode terminal")
            if completed_ids != expected_ids:
                raise ValueError(
                    "G9 run_completed is missing one or more expected episode terminals"
                )
            summary = record.get("summary")
            if not isinstance(summary, dict):
                raise ValueError("G9 run_completed requires a summary object")
            if (
                summary.get("split") != partition
                or summary.get("split_sha256") != expected_split_sha256
                or type(summary.get("scenario_count")) is not int
                or summary["scenario_count"] != len(expected_ids)
                or type(summary.get("completed_episodes")) is not int
                or summary["completed_episodes"] != len(expected_ids)
                or summary.get("empirical_claim_allowed") is not True
            ):
                raise ValueError("G9 run_completed summary does not match episode membership")
            if index != len(records):
                raise ValueError("G9 run_completed must be the final event")
            saw_run_completed = True
            continue
        if event in {"run_interrupted", "episode_interrupted"}:
            raise ValueError(f"G9 event stream is interrupted by {event}")
        if event == "episode_started":
            if active_scenario_id is not None:
                raise ValueError("G9 event stream contains a duplicate or nested episode start")
            if len(completed_ids) >= len(expected_ids):
                raise ValueError("G9 event stream contains an extra episode start")
            scenario_id = _event_scenario_id(
                record, label=f"G9 episode_started event {index}"
            )
            expected_scenario_id = expected_ids[len(completed_ids)]
            if scenario_id != expected_scenario_id:
                raise ValueError(
                    "raw episode membership mismatch in G9 episode start order"
                )
            if record.get("evaluation_mode") != "EMPIRICAL":
                raise ValueError("G9 episode_started must declare EMPIRICAL evaluation mode")
            active_scenario_id = scenario_id
            continue

        if event in _G9_TERMINAL_EVENTS:
            if active_scenario_id is None:
                raise ValueError(
                    f"G9 terminal event {event!r} occurred without an active episode"
                )
            event_scenario_id = _event_scenario_id(
                record,
                label=f"G9 terminal event {event!r}",
                required=False,
            )
            result = record.get("result")
            result_scenario_id = None
            if isinstance(result, dict) and "scenario_id" in result:
                result_scenario_id = _event_scenario_id(
                    result,
                    label=f"G9 {event} result",
                )
            if (
                event_scenario_id is not None
                and event_scenario_id != active_scenario_id
            ):
                raise ValueError("G9 terminal event contains a cross-scenario scenario_id")
            if result_scenario_id is not None and result_scenario_id != active_scenario_id:
                raise ValueError("G9 result scenario_id does not match its episode")
            if event == "episode_completed":
                if (
                    event_scenario_id is None
                    or not isinstance(result, dict)
                    or result_scenario_id is None
                    or result.get("status") != "ok"
                    or result.get("scorable") is not True
                ):
                    raise ValueError(
                        "G9 episode_completed result lacks a matching scorable episode"
                    )
                completed_ids.append(active_scenario_id)
                active_scenario_id = None
            else:
                raise ValueError(f"G9 episode terminal {event!r} is not completed")
            continue

        if event not in _G9_PROGRESS_EVENTS:
            raise ValueError(f"G9 event stream contains unsupported event {event!r}")
        if active_scenario_id is None:
            raise ValueError(
                f"G9 event {event!r} occurred without an active episode"
            )
        scenario_id = _event_scenario_id(
            record, label=f"G9 progress event {event!r}"
        )
        if scenario_id != active_scenario_id:
            raise ValueError("G9 progress event contains a cross-scenario scenario_id")

    if not saw_run_completed:
        raise ValueError("G9 event stream lacks the final run_completed event")
    return completed_ids


def derive_raw_scenario_ids(
    raw_bytes: bytes,
    *,
    expected_ids: Sequence[str],
    partition: str,
    raw_format: str,
) -> list[str]:
    """Derive and verify ordered membership in a supported raw JSONL format."""
    expected = _validate_expected_ids(expected_ids)
    records = _parse_raw_jsonl(raw_bytes)
    if raw_format == "episode_rows":
        return _extract_episode_rows(records, expected, partition)
    if raw_format == "g9_event_stream":
        scenario_ids = _extract_g9_event_membership(records, expected, partition)
        if scenario_ids != expected:
            raise ValueError(
                f"raw episode membership mismatch for {partition}: expected "
                f"{len(expected)} ordered IDs, observed {len(scenario_ids)}"
            )
        return scenario_ids
    raise ValueError(f"unsupported raw episode format {raw_format!r}")
