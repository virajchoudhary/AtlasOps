"""Prospective, non-empirical replay and comparison for three G13 arms."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import re
from typing import Any

from bench import (
    candidate_adapters,
    candidate_lineage,
    candidate_measurement,
    episode_membership,
)
from bench.candidate_lineage import validate_candidate_lineage

_ARM_ORDER = (
    "Zero-Shot Baseline",
    "SFT Model",
    "SFT + GRPO",
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_POPULATION_METRICS = (
    "reward_mean",
    "resolution_rate",
    "diagnosis_accuracy",
)
_SUMMARY_METRICS = (
    *_POPULATION_METRICS,
    "time_to_recovery_mean_seconds",
)
_NULL_METRICS = {
    "reward_mean": ("objective_score", "value", "reason"),
    "resolution_rate": ("a1_resolution_candidate", "value", "reason"),
    "diagnosis_accuracy": ("diagnosis", "score", "reason"),
    "time_to_recovery_mean_seconds": ("time_to_recovery", "seconds", "reason"),
}


def replay_candidate_comparison(
    run_descriptors: Sequence[Mapping[str, Any]],
    candidate_schema: Mapping[str, Any],
    measurement_contract: Mapping[str, Any],
    *,
    source_pins: Mapping[str, Mapping[str, Any]],
    comparison_scorer_sha256: str,
    measurement_contract_sha256: str,
    partition: str,
    native_sources: Mapping[str, bytes] | None = None,
    common_episode_rows: bytes | None = None,
    expected_common_episode_rows_sha256: str | None = None,
    declared_summaries: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Replay supplied, hash-bound candidate episodes without empirical claims.

    Exactly one raw input form is accepted:

    * ``native_sources`` maps each ordered arm to its source JSONL bytes.
      The G6/G8/G9 adapter verifies each byte stream against its pinned digest.
      G6/G8 run/model labels remain caller-declared; only a completed G9 run
      can raw-bind identity through its matching terminal summary.
    * ``common_episode_rows`` contains strict JSONL objects of the form
      ``{"arm": <arm label>, "episode": <full raw episode>}``. Its complete
      byte stream requires the separate ``expected_common_episode_rows_sha256``
      pin. Per-arm ``source_identity`` and ``source_sha256`` values inside
      episodes must also match the caller's ``source_pins``.

    Every run descriptor must carry ``raw_source`` (the same identity and
    digest as its external source pin), ``partition``, and
    ``measurement_contract_sha256``. The expected scorer digest must match a
    canonical manifest of the replay, measurement, adapter, lineage, and
    membership module sources and all three lineage records.
    Measurement contracts are hashed as UTF-8 canonical JSON with sorted keys,
    compact separators, and non-finite numbers rejected.

    This helper performs no repository or partition fetch. It checks the
    caller-supplied partition label and explicit metadata, but cannot establish
    source authorization for unlabeled input bytes. It retains missing and
    failed episodes and leaves summaries null if their scheduled denominator
    or required evidence is incomplete.
    """
    arms, scenario_ids = _read_candidate_membership(candidate_schema)
    if arms != list(_ARM_ORDER):
        raise ValueError("candidate_schema arms must use the exact required three-arm order")
    _validate_partition(partition)
    _reject_final_test_scope(candidate_schema, "candidate_schema")
    _reject_final_test_scope(run_descriptors, "run_descriptors")
    _reject_final_test_scope(measurement_contract, "measurement_contract")

    if (native_sources is None) == (common_episode_rows is None):
        raise ValueError("provide exactly one of native_sources or common_episode_rows")
    if common_episode_rows is None and expected_common_episode_rows_sha256 is not None:
        raise ValueError(
            "expected_common_episode_rows_sha256 is only valid with common_episode_rows"
        )

    lineage = validate_candidate_lineage(run_descriptors, candidate_schema)
    if lineage["lineage_status"] != "CONSISTENT":
        raise ValueError("candidate lineage is invalid: " + "; ".join(lineage["errors"]))

    expected_scorer_sha256 = _require_sha256(
        comparison_scorer_sha256,
        "comparison_scorer_sha256",
    )
    scorer_source_manifest = _runtime_scorer_source_manifest()
    actual_scorer_sha256 = _canonical_sha256(
        scorer_source_manifest,
        "scorer_runtime_source_manifest",
    )
    if expected_scorer_sha256 != actual_scorer_sha256:
        raise ValueError("comparison_scorer_sha256 does not match the runtime scorer source")

    candidate_scorer = candidate_schema.get("comparison_scorer")
    if (
        not isinstance(candidate_scorer, Mapping)
        or _require_sha256(
            candidate_scorer.get("sha256"),
            "candidate_schema.comparison_scorer.sha256",
        )
        != expected_scorer_sha256
    ):
        raise ValueError("candidate schema scorer digest differs from the caller pin")

    actual_contract_sha256 = _canonical_sha256(
        measurement_contract,
        "measurement_contract",
    )
    expected_contract_sha256 = _require_sha256(
        measurement_contract_sha256,
        "measurement_contract_sha256",
    )
    if actual_contract_sha256 != expected_contract_sha256:
        raise ValueError("measurement_contract_sha256 does not match canonical contract")
    if candidate_schema.get("measurement_contract_sha256") != expected_contract_sha256:
        raise ValueError("candidate schema measurement-contract digest differs from caller pin")

    normalized_pins = _validate_run_bindings(
        run_descriptors,
        source_pins,
        arms,
        partition,
        expected_contract_sha256,
    )
    normalized_declared_summaries = _validate_declared_summaries(
        declared_summaries,
        arms,
    )

    if native_sources is not None:
        episodes_by_arm, missing_by_arm, source_info_by_arm = _adapt_native_sources(
            native_sources,
            normalized_pins,
            arms,
            scenario_ids,
            partition,
        )
        common_rows_sha256 = None
    else:
        assert common_episode_rows is not None
        episodes_by_arm, missing_by_arm, source_info_by_arm, common_rows_sha256 = (
            _parse_common_episode_rows(
                common_episode_rows,
                expected_common_episode_rows_sha256,
                normalized_pins,
                arms,
                scenario_ids,
            )
        )

    measured_by_arm: dict[str, list[dict[str, Any]]] = {}
    for arm in arms:
        measured_by_arm[arm] = [
            candidate_measurement.measure_candidate_episode(episode, measurement_contract)
            for episode in episodes_by_arm[arm]
        ]
        for episode in measured_by_arm[arm]:
            identity_provenance = deepcopy(source_info_by_arm[arm]["identity_binding"])
            if source_info_by_arm[arm]["format"] == "common_episode_rows":
                raw_episode = episode["raw_episode"]
                identity_provenance["row_reported_source_identity"] = deepcopy(
                    raw_episode["source_identity"]
                )
                identity_provenance["row_reported_source_identity_status"] = raw_episode[
                    "source_identity_status"
                ]
            episode["source_identity_provenance"] = identity_provenance

    arm_results: dict[str, Any] = {}
    for arm in arms:
        measured = measured_by_arm[arm]
        missing = missing_by_arm[arm]
        arm_results[arm] = {
            "source": source_info_by_arm[arm],
            "scheduled_scenario_ids": list(scenario_ids),
            "observed_scenario_ids": [episode["scenario_id"] for episode in measured],
            "missing_scheduled_ids": list(missing),
            "episodes": measured,
            **_count_episode_results(measured, missing),
            "summary": _summarize_episodes(measured, missing, len(scenario_ids)),
        }
        arm_results[arm]["declared_summary_comparison"] = _compare_declared_summary(
            normalized_declared_summaries.get(arm, {}),
            arm_results[arm]["summary"],
        )

    return {
        "schema_version": 1,
        "replay_version": "g13-candidate-replay-v1",
        "protocol_status": "PROPOSED_NOT_FROZEN",
        "evaluation_mode": "NON_EMPIRICAL",
        "non_empirical": True,
        "empirical": False,
        "empirical_claim_allowed": False,
        "certification_status": "NOT_CERTIFIED",
        "partition_scope": {
            "repository_fetch_performed": False,
            "partition_fetch_performed": False,
            "caller_partition_label": partition,
            "source_authorization": "NOT_ESTABLISHED_FROM_SUPPLIED_BYTES",
            "explicit_test_scope_markers": "REJECTED",
        },
        "partition": partition,
        "arm_order": list(arms),
        "scenario_ids": list(scenario_ids),
        "lineage": lineage,
        "provenance_pins": {
            "comparison_scorer_sha256": expected_scorer_sha256,
            "comparison_scorer_source_manifest": scorer_source_manifest,
            "measurement_contract_sha256": expected_contract_sha256,
            "source_pin_status": "bound_to_run_descriptors_and_external_pins",
            "source_identity_note": (
                "Only adapter-reported RAW_BOUND identity is presented as raw-bound; "
                "common-row identity statuses remain row-reported and replay-unbound."
            ),
            "common_episode_rows_sha256": common_rows_sha256,
        },
        "arms": arm_results,
    }


def _read_candidate_membership(
    candidate_schema: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    if not isinstance(candidate_schema, Mapping):
        raise TypeError("candidate_schema must be a mapping")
    arms = candidate_schema.get("arms")
    scenario_ids = candidate_schema.get("scenario_ids")
    if not isinstance(arms, list) or not all(isinstance(item, str) for item in arms):
        raise ValueError("candidate_schema.arms must be an ordered string list")
    if (
        not isinstance(scenario_ids, list)
        or not scenario_ids
        or any(not isinstance(item, str) or not item.strip() for item in scenario_ids)
        or len(set(scenario_ids)) != len(scenario_ids)
    ):
        raise ValueError("candidate_schema.scenario_ids must be unique, ordered, and non-empty")
    return list(arms), list(scenario_ids)


def _validate_partition(partition: str) -> None:
    if not isinstance(partition, str) or not partition.strip():
        raise ValueError("partition must be supplied as non-empty text")
    normalized = _normalize_scope_label(partition)
    if _is_final_test_label(normalized):
        raise ValueError("candidate replay must not access the final Test partition")
    if normalized not in {"val", "validation", "synthetic"}:
        raise ValueError("partition must be val, validation, or an explicit synthetic partition")


def _reject_final_test_scope(value: Any, label: str) -> None:
    marker_keys = {
        "final_test",
        "final_test_set",
        "final_test_split",
        "finaltest",
        "finaltestset",
        "held_out_test",
        "held_out_test_set",
        "heldout_test",
        "heldouttest",
        "heldouttestset",
        "hold_out_test",
        "holdout_test",
        "holdouttest",
        "holdouttestset",
        "test",
        "test_partition",
        "test_set",
        "testset",
        "test_split",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                normalized_key = _normalize_scope_label(key)
                if _is_scope_field(normalized_key) and _contains_final_test_label(item):
                    raise ValueError(f"{label}.{key} references the final Test partition")
                marker_key = normalized_key.removeprefix("is_")
                if marker_key in marker_keys and item is not None and item is not False:
                    raise ValueError(f"{label}.{key} marks the final Test partition")
            _reject_final_test_scope(item, f"{label}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_final_test_scope(item, f"{label}[{index}]")


def _normalize_scope_label(value: str) -> str:
    camel_case_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value.strip())
    return re.sub(r"[^a-zA-Z0-9]+", "_", camel_case_split).strip("_").casefold()


def _is_scope_field(normalized_key: str) -> bool:
    explicit = {
        "dataset",
        "dataset_name",
        "dataset_partition",
        "dataset_split",
        "partition",
        "partition_label",
        "partition_name",
        "split",
        "split_name",
    }
    suffixes = ("_partition", "_partition_label", "_partition_name", "_split", "_split_name")
    return normalized_key in explicit or normalized_key.endswith(suffixes)


def _is_final_test_label(value: str) -> bool:
    normalized = _normalize_scope_label(value)
    compact_aliases = {
        "finaltest",
        "finaltestset",
        "heldouttest",
        "heldouttestset",
        "holdouttest",
        "holdouttestset",
        "testset",
    }
    return "test" in normalized.split("_") or normalized.replace("_", "") in compact_aliases


def _contains_final_test_label(value: Any) -> bool:
    if isinstance(value, str):
        return _is_final_test_label(value)
    if isinstance(value, Mapping):
        return any(_contains_final_test_label(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_final_test_label(item) for item in value)
    return False


def _validate_run_bindings(
    run_descriptors: Sequence[Mapping[str, Any]],
    source_pins: Mapping[str, Mapping[str, Any]],
    arms: list[str],
    partition: str,
    measurement_contract_sha256: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(source_pins, Mapping) or set(source_pins) != set(arms):
        raise ValueError("source_pins must contain exactly the three required arms")
    if len(run_descriptors) != len(arms):
        raise ValueError("run_descriptors must contain exactly the three required arms")

    normalized: dict[str, dict[str, Any]] = {}
    for arm, run in zip(arms, run_descriptors, strict=True):
        if not isinstance(run, Mapping):
            raise ValueError(f"run descriptor for {arm!r} must be a mapping")
        if run.get("partition") != partition:
            raise ValueError(f"{arm} partition differs from the caller-supplied partition")
        if run.get("measurement_contract_sha256") != measurement_contract_sha256:
            raise ValueError(f"{arm} measurement-contract digest differs from caller pin")

        pin = source_pins[arm]
        if not isinstance(pin, Mapping) or set(pin) != {"source_identity", "source_sha256"}:
            raise ValueError(f"{arm} source pin must contain identity and source_sha256")
        identity = _validate_source_identity(
            pin.get("source_identity"),
            arm,
            allow_unknown=arm == _ARM_ORDER[2],
        )
        source_sha256 = _require_sha256(pin.get("source_sha256"), f"{arm} source_sha256")

        descriptor_pin = run.get("raw_source")
        if not isinstance(descriptor_pin, Mapping) or set(descriptor_pin) != {
            "source_identity",
            "source_sha256",
        }:
            raise ValueError(f"{arm} run descriptor must include raw_source pins")
        descriptor_identity = _validate_source_identity(
            descriptor_pin.get("source_identity"),
            f"{arm} run descriptor raw_source",
            allow_unknown=arm == _ARM_ORDER[2],
        )
        descriptor_sha256 = _require_sha256(
            descriptor_pin.get("source_sha256"),
            f"{arm} run descriptor raw_source.sha256",
        )
        if not _same_json_value(identity, descriptor_identity):
            raise ValueError(f"{arm} raw source identity differs from external pin")
        if source_sha256 != descriptor_sha256:
            raise ValueError(f"{arm} raw source digest differs from external pin")
        normalized[arm] = {
            "source_identity": identity,
            "source_sha256": source_sha256,
        }
    return normalized


def _validate_source_identity(
    value: Any,
    label: str,
    *,
    allow_unknown: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} source_identity must be a mapping")
    identity = deepcopy(dict(value))
    for key, item in identity.items():
        if not isinstance(key, str):
            raise ValueError(f"{label} source_identity keys must be strings")
        _ensure_json_value(item, f"{label} source_identity.{key}")
    for field in ("run_id", "model"):
        item = identity.get(field)
        if item is None and allow_unknown:
            continue
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} source_identity requires non-empty {field}")
    return identity


def _adapt_native_sources(
    native_sources: Mapping[str, bytes],
    source_pins: Mapping[str, Mapping[str, Any]],
    arms: list[str],
    scenario_ids: list[str],
    partition: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]], dict[str, Any]]:
    if not isinstance(native_sources, Mapping) or set(native_sources) != set(arms):
        raise ValueError("native_sources must contain exactly the three required arms")
    from bench.candidate_adapters import adapt_three_arm_raw

    episodes_by_arm: dict[str, list[dict[str, Any]]] = {}
    missing_by_arm: dict[str, list[str]] = {}
    source_info_by_arm: dict[str, Any] = {}
    for arm in arms:
        raw_bytes = native_sources[arm]
        pin = source_pins[arm]
        adapted = adapt_three_arm_raw(
            raw_bytes,
            variant=arm,
            partition=partition,
            expected_scenario_ids=scenario_ids,
            expected_sha256=pin["source_sha256"],
            source_identity=pin["source_identity"],
        )
        _reject_final_test_scope(adapted["raw_records"], f"{arm} native source")
        if adapted["source_sha256"] != pin["source_sha256"]:
            raise ValueError(f"{arm} native source digest differs from external pin")
        declaration = _validate_source_identity(
            adapted.get("source_identity_declaration"),
            f"{arm} native source identity declaration",
            allow_unknown=arm == _ARM_ORDER[2],
        )
        if not _same_json_value(declaration, pin["source_identity"]):
            raise ValueError(f"{arm} native source identity declaration differs from pin")
        identity_binding = _validate_reported_identity(
            adapted.get("source_identity"),
            adapted.get("source_identity_status"),
            declaration,
            f"{arm} native source",
        )
        if adapted["scheduled_scenario_ids"] != scenario_ids:
            raise ValueError(f"{arm} native source scheduled membership differs from schema")
        for episode in adapted["episodes"]:
            if (
                not _same_json_value(episode.get("source_identity_declaration"), declaration)
                or episode.get("source_identity_status") != identity_binding["status"]
                or not _same_json_value(
                    episode.get("source_identity"),
                    identity_binding["raw_bound_source_identity"],
                )
            ):
                raise ValueError(f"{arm} episode identity binding differs from native run")
        episodes_by_arm[arm] = adapted["episodes"]
        missing_by_arm[arm] = adapted["missing_scenario_ids"]
        source_info_by_arm[arm] = {
            "format": adapted["source_format"],
            "identity_binding": identity_binding,
            "source_sha256": adapted["source_sha256"],
            "source_sha256_status": "verified_against_raw_bytes",
            "run_outcome": adapted["run_outcome"],
        }
    return episodes_by_arm, missing_by_arm, source_info_by_arm


def _validate_reported_identity(
    raw_identity: Any,
    raw_status: Any,
    declared_identity: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    if raw_status not in {"RAW_BOUND", "UNBOUND"}:
        raise ValueError(f"{label} returned invalid source_identity_status")
    if not isinstance(raw_identity, Mapping) or set(raw_identity) != {"run_id", "model"}:
        raise ValueError(f"{label} identity must contain run_id and model")
    identity = deepcopy(dict(raw_identity))
    for field, value in identity.items():
        if raw_status == "RAW_BOUND":
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} raw-bound {field} must be non-empty text")
            declared = declared_identity.get(field)
            if declared is not None and declared != value:
                raise ValueError(f"{label} raw-bound {field} differs from caller declaration")
        elif value is not None:
            raise ValueError(f"{label} unbound {field} must be null")
    return {
        "caller_declared_source_identity": deepcopy(dict(declared_identity)),
        "raw_bound_source_identity": identity,
        "status": raw_status,
    }


def _parse_common_episode_rows(
    raw_bytes: bytes,
    expected_sha256: str | None,
    source_pins: Mapping[str, Mapping[str, Any]],
    arms: list[str],
    scenario_ids: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]], dict[str, Any], str]:
    if not isinstance(raw_bytes, bytes):
        raise TypeError("common_episode_rows must be bytes")
    if len(raw_bytes) > episode_membership.MAX_RAW_OUTPUT_BYTES:
        raise ValueError("common_episode_rows exceeds the raw-output size limit")
    expected_digest = _require_sha256(
        expected_sha256,
        "expected_common_episode_rows_sha256",
    )
    observed_digest = hashlib.sha256(raw_bytes).hexdigest()
    if observed_digest != expected_digest:
        raise ValueError("common_episode_rows SHA-256 mismatch")

    expected_pairs = [(arm, scenario_id) for arm in arms for scenario_id in scenario_ids]
    pair_positions = {pair: index for index, pair in enumerate(expected_pairs)}
    rows_by_arm: dict[str, list[dict[str, Any]]] = {arm: [] for arm in arms}
    row_identity_statuses_by_arm: dict[str, list[str]] = {arm: [] for arm in arms}
    observed_pairs: set[tuple[str, str]] = set()
    previous_position = -1
    for line_number, envelope in _parse_strict_jsonl_rows(raw_bytes):
        if not isinstance(envelope, Mapping) or set(envelope) != {"arm", "episode"}:
            raise ValueError(
                f"common_episode_rows line {line_number} must contain only arm and episode"
            )
        arm = envelope["arm"]
        episode = envelope["episode"]
        if not isinstance(arm, str) or arm not in source_pins:
            raise ValueError(f"common_episode_rows line {line_number} has an unknown arm")
        if not isinstance(episode, Mapping):
            raise ValueError(f"common_episode_rows line {line_number} episode must be an object")
        if episode.get("diagnosis_observation") is not None and arm != _ARM_ORDER[2]:
            raise ValueError(
                f"common_episode_rows line {line_number} diagnosis_observation marker "
                f"is only valid for {_ARM_ORDER[2]}"
            )
        scenario_id = episode.get("scenario_id")
        if not isinstance(scenario_id, str):
            raise ValueError(f"common_episode_rows line {line_number} scenario_id must be text")
        pair = (arm, scenario_id)
        if pair not in pair_positions:
            raise ValueError(
                f"common_episode_rows line {line_number} has an unexpected scenario ID"
            )
        if pair in observed_pairs:
            raise ValueError(
                f"common_episode_rows line {line_number} duplicates scheduled membership"
            )
        position = pair_positions[pair]
        if position <= previous_position:
            raise ValueError("common_episode_rows ordered membership differs from scheduled order")
        previous_position = position
        observed_pairs.add(pair)

        pin = source_pins[arm]
        declaration = _validate_source_identity(
            episode.get("source_identity_declaration"),
            f"common_episode_rows line {line_number}",
            allow_unknown=arm == _ARM_ORDER[2],
        )
        if not _same_json_value(declaration, pin["source_identity"]):
            raise ValueError(
                f"common_episode_rows line {line_number} identity declaration differs from pin"
            )
        row_identity = _validate_reported_identity(
            episode.get("source_identity"),
            episode.get("source_identity_status"),
            declaration,
            f"common_episode_rows line {line_number}",
        )
        source_sha256 = _require_sha256(
            episode.get("source_sha256"),
            f"common_episode_rows line {line_number} source_sha256",
        )
        if source_sha256 != pin["source_sha256"]:
            raise ValueError(
                f"common_episode_rows line {line_number} source digest differs from pin"
            )
        _reject_final_test_scope(episode, f"common_episode_rows line {line_number}")
        rows_by_arm[arm].append(deepcopy(dict(episode)))
        row_identity_statuses_by_arm[arm].append(row_identity["status"])

    missing_by_arm = {
        arm: [
            scenario_id for scenario_id in scenario_ids if (arm, scenario_id) not in observed_pairs
        ]
        for arm in arms
    }
    source_info_by_arm = {
        arm: {
            "format": "common_episode_rows",
            "identity_binding": {
                "caller_declared_source_identity": deepcopy(source_pins[arm]["source_identity"]),
                "raw_bound_source_identity": {"run_id": None, "model": None},
                "status": "UNBOUND",
                "row_reported_source_identity_statuses": row_identity_statuses_by_arm[arm],
            },
            "source_sha256": source_pins[arm]["source_sha256"],
            "source_sha256_status": "matched_to_external_pin_not_recomputed",
            "run_outcome": "partial" if missing_by_arm[arm] else "records_complete",
        }
        for arm in arms
    }
    return rows_by_arm, missing_by_arm, source_info_by_arm, observed_digest


def _parse_strict_jsonl_rows(raw_bytes: bytes) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    source = io.BytesIO(raw_bytes)
    line_number = 0
    while line := source.readline(episode_membership.MAX_RAW_OUTPUT_LINE_BYTES + 1):
        line_number += 1
        if len(line) > episode_membership.MAX_RAW_OUTPUT_LINE_BYTES:
            raise ValueError(f"common_episode_rows line {line_number} exceeds the line limit")
        if line.endswith(b"\n"):
            line = line[:-1]
        if line.endswith(b"\r"):
            line = line[:-1]
        if not line.strip():
            raise ValueError(f"common_episode_rows line {line_number} is blank")
        if line_number > episode_membership.MAX_RAW_OUTPUT_RECORDS:
            raise ValueError("common_episode_rows exceeds the raw-output record limit")
        try:
            decoded = line.decode("utf-8")
            record = json.loads(
                decoded,
                object_pairs_hook=_unique_json_object,
                parse_float=episode_membership._parse_finite_float,
                parse_constant=episode_membership._reject_non_finite_constant,
            )
            episode_membership._reject_non_empirical_markers(
                record,
                allow_nonclaimable=True,
            )
        except UnicodeDecodeError as exc:
            raise ValueError(f"common_episode_rows line {line_number} is not UTF-8") from exc
        except (json.JSONDecodeError, RecursionError, ValueError) as exc:
            raise ValueError(f"common_episode_rows line {line_number}: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"common_episode_rows line {line_number} must be a JSON object")
        rows.append((line_number, record))
    return rows


def _count_episode_results(
    episodes: list[Mapping[str, Any]],
    missing_scheduled_ids: list[str],
) -> dict[str, Any]:
    eligibility = Counter(episode["eligibility"]["status"] for episode in episodes)
    outcome = Counter(episode["outcome_classification"] for episode in episodes)
    ineligible_attempted_count = sum(
        episode["eligibility"]["status"] == "ineligible" and _has_attempt_evidence(episode)
        for episode in episodes
    )
    null_reasons: dict[str, dict[str, int]] = {}
    for metric, (section, value_key, reason_key) in _NULL_METRICS.items():
        reasons: Counter[str] = Counter()
        for episode in episodes:
            value = episode[section].get(value_key)
            reason = episode[section].get(reason_key)
            if value is None:
                reasons[reason or "unspecified"] += 1
        null_reasons[metric] = dict(sorted(reasons.items()))

    return {
        "eligibility_counts": {
            "eligible": eligibility.get("eligible", 0),
            "ineligible": eligibility.get("ineligible", 0),
            "undetermined": eligibility.get("undetermined", 0),
            "missing": len(missing_scheduled_ids),
        },
        "pending_adjudication_count": sum(
            episode["adjudication"]["required"] is True for episode in episodes
        ),
        "ineligible_attempted_count": ineligible_attempted_count,
        "negative_episode_count": outcome.get("eligible_negative", 0),
        "outcome_counts": dict(sorted(outcome.items())),
        "null_reason_counts": null_reasons,
    }


def _summarize_episodes(
    episodes: list[Mapping[str, Any]],
    missing_scheduled_ids: list[str],
    scheduled_count: int,
) -> dict[str, dict[str, Any]]:
    eligible = [episode for episode in episodes if episode["eligibility"]["status"] == "eligible"]
    ineligible_attempted = [
        episode
        for episode in episodes
        if episode["eligibility"]["status"] == "ineligible" and _has_attempt_evidence(episode)
    ]
    prestart_ineligible = [
        episode
        for episode in episodes
        if episode["eligibility"]["status"] == "ineligible" and not _has_attempt_evidence(episode)
    ]
    excluded_ineligible_count = len(prestart_ineligible)
    unknown_ids = [
        episode["scenario_id"]
        for episode in episodes
        if episode["eligibility"]["status"] == "undetermined"
    ]
    pending_ids = [
        episode["scenario_id"]
        for episode in episodes
        if episode["adjudication"]["required"] is True
    ]

    population_blockers: list[str] = []
    if missing_scheduled_ids:
        population_blockers.append("missing_scheduled_ids: " + ", ".join(missing_scheduled_ids))
    if unknown_ids:
        population_blockers.append("eligibility undetermined: " + ", ".join(unknown_ids))
    if pending_ids:
        population_blockers.append("pending independent adjudication: " + ", ".join(pending_ids))
    if ineligible_attempted:
        population_blockers.append(
            "activity observed in pre-start ineligible episodes: "
            + ", ".join(episode["scenario_id"] for episode in ineligible_attempted)
        )

    summaries: dict[str, dict[str, Any]] = {}
    for metric in _POPULATION_METRICS:
        values: list[float] = []
        reasons = list(population_blockers)
        for episode in eligible:
            value, reason = _episode_metric_value(episode, metric)
            if value is None:
                reasons.append(f"{episode['scenario_id']}: {reason or 'required evidence missing'}")
            else:
                values.append(value)
        if not eligible and not reasons:
            reasons.append("no eligible attempted episodes")
        if len(values) != len(eligible) and not reasons:
            reasons.append("eligible-attempt denominator is incomplete")
        value = None if reasons else sum(values) / len(eligible)
        summaries[metric] = _metric_summary(
            value,
            reasons,
            denominator=len(eligible),
            scheduled_count=scheduled_count,
            observed_count=len(episodes),
            eligible_attempt_count=len(eligible),
            excluded_pre_start_ineligible_count=excluded_ineligible_count,
            ineligible_attempted_count=len(ineligible_attempted),
        )

    ttr_reasons = list(population_blockers)
    resolved_values: list[float] = []
    resolved_without_ttr_count = 0
    right_censored_count = 0
    unavailable_unresolved_count = 0
    for episode in eligible:
        ttr = episode["time_to_recovery"]
        resolution = episode["a1_resolution_candidate"]["value"]
        if resolution is not None and (
            isinstance(resolution, bool) or resolution not in (0, 1, 0.0, 1.0)
        ):
            raise ValueError("A1 resolution candidate must be zero, one, or null")

        seconds = ttr["seconds"]
        if ttr["status"] == "observed":
            if (
                isinstance(seconds, bool)
                or not isinstance(seconds, (int, float))
                or not math.isfinite(float(seconds))
                or seconds < 0
            ):
                resolved_without_ttr_count += 1
                ttr_reasons.append(
                    f"{episode['scenario_id']}: observed resolution lacks a valid TTR value"
                )
            else:
                resolved_values.append(float(seconds))
        elif resolution == 1:
            resolved_without_ttr_count += 1
            ttr_reasons.append(
                f"{episode['scenario_id']}: resolved episode lacks observed TTR: "
                f"{ttr.get('reason') or ttr['status']}"
            )
        elif resolution is None:
            ttr_reasons.append(f"{episode['scenario_id']}: A1 resolution candidate is unknown")
        elif ttr["status"] == "censored":
            right_censored_count += 1
        else:
            unavailable_unresolved_count += 1

    if not resolved_values and resolved_without_ttr_count == 0 and not ttr_reasons:
        ttr_reasons.append("no observed resolved episodes")
    if resolved_without_ttr_count:
        ttr_reasons.append(f"{resolved_without_ttr_count} resolved episode(s) lack observed TTR")
    ttr_value = (
        None
        if ttr_reasons
        else sum(resolved_values) / len(resolved_values)
        if resolved_values
        else None
    )
    summaries["time_to_recovery_mean_seconds"] = _metric_summary(
        ttr_value,
        ttr_reasons,
        denominator=len(resolved_values) + resolved_without_ttr_count,
        scheduled_count=scheduled_count,
        observed_count=len(episodes),
        eligible_attempt_count=len(eligible),
        excluded_pre_start_ineligible_count=excluded_ineligible_count,
        ineligible_attempted_count=len(ineligible_attempted),
        resolved_episode_count=len(resolved_values) + resolved_without_ttr_count,
        right_censored_count=right_censored_count,
        unavailable_unresolved_count=unavailable_unresolved_count,
        resolved_without_observed_ttr_count=resolved_without_ttr_count,
    )
    return summaries


def _has_attempt_evidence(episode: Mapping[str, Any]) -> bool:
    attempt_events = {
        "action_result",
        "diagnosis_output",
        "model_failure",
        "policy_output",
        "step_failure",
    }
    raw_episode = episode["raw_episode"]
    events = raw_episode.get("events", [])
    return any(
        isinstance(event, Mapping) and event.get("event") in attempt_events for event in events
    )


def _metric_summary(
    value: float | None,
    reasons: list[str],
    *,
    denominator: int,
    scheduled_count: int,
    observed_count: int,
    eligible_attempt_count: int,
    excluded_pre_start_ineligible_count: int,
    ineligible_attempted_count: int = 0,
    **counts: int,
) -> dict[str, Any]:
    if value is None and not reasons:
        reasons = ["required evidence is incomplete"]
    return {
        "value": value,
        "status": "NULL" if value is None else "COMPUTED_CANDIDATE_ARITHMETIC",
        "denominator": denominator,
        "scheduled_count": scheduled_count,
        "observed_count": observed_count,
        "eligible_attempt_count": eligible_attempt_count,
        "excluded_pre_start_ineligible_count": excluded_pre_start_ineligible_count,
        "ineligible_attempted_count": ineligible_attempted_count,
        "reasons": _unique_in_order(reasons),
        **counts,
    }


def _episode_metric_value(
    episode: Mapping[str, Any],
    metric: str,
) -> tuple[float | None, str | None]:
    if metric == "reward_mean":
        value = episode["objective_score"]["value"]
        reason = episode["objective_score"].get("reason")
    elif metric == "resolution_rate":
        value = episode["a1_resolution_candidate"]["value"]
        reason = episode["a1_resolution_candidate"].get("reason")
    elif metric == "diagnosis_accuracy":
        value = episode["diagnosis"]["score"]
        reason = episode["diagnosis"].get("reason")
    else:
        raise ValueError(f"unsupported eligible-attempt metric {metric!r}")
    if value is None:
        return None, reason
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{metric} measurement is not numeric")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError(f"{metric} measurement is not finite")
    return numeric_value, None


def _compare_declared_summary(
    declared: Mapping[str, Any],
    recomputed: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(declared, Mapping):
        raise TypeError("declared summary for each arm must be a mapping")
    comparisons: dict[str, Any] = {}
    for metric, declared_value in declared.items():
        if not isinstance(metric, str):
            raise ValueError("declared summary metric names must be strings")
        if metric not in _SUMMARY_METRICS:
            comparisons[metric] = {
                "status": "NOT_COMPUTED",
                "declared": deepcopy(declared_value),
                "recomputed": None,
                "reason": "metric is outside the supported candidate arithmetic",
            }
            continue
        result = recomputed[metric]
        value = result["value"]
        if value is None:
            comparisons[metric] = {
                "status": "NOT_COMPARABLE",
                "declared": deepcopy(declared_value),
                "recomputed": None,
                "reason": "recomputation is null: " + "; ".join(result["reasons"]),
            }
            continue
        if (
            isinstance(declared_value, bool)
            or not isinstance(declared_value, (int, float))
            or not math.isfinite(float(declared_value))
        ):
            comparisons[metric] = {
                "status": "INVALID_DECLARED_VALUE",
                "declared": deepcopy(declared_value),
                "recomputed": value,
                "reason": "declared value must be a finite JSON number",
            }
            continue
        # Tolerance covers float serialization only; it is not a metric threshold.
        matches = math.isclose(float(declared_value), value, rel_tol=0.0, abs_tol=1e-12)
        comparisons[metric] = {
            "status": "MATCH" if matches else "MISMATCH",
            "declared": deepcopy(declared_value),
            "recomputed": value,
            "reason": None if matches else "declared value differs from recomputation",
        }
    return comparisons


def _validate_declared_summaries(
    value: Mapping[str, Mapping[str, Any]] | None,
    arms: list[str],
) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("declared_summaries must be a mapping by arm")
    if any(not isinstance(arm, str) or arm not in arms for arm in value):
        raise ValueError("declared_summaries contains an unknown arm")
    normalized: dict[str, dict[str, Any]] = {}
    for arm, metrics in value.items():
        if not isinstance(metrics, Mapping):
            raise ValueError(f"declared summaries for {arm!r} must be a mapping")
        copied_metrics = deepcopy(dict(metrics))
        for metric, declared_value in copied_metrics.items():
            if not isinstance(metric, str):
                raise ValueError(f"declared summary metric names for {arm!r} must be strings")
            _ensure_json_value(declared_value, f"declared_summaries.{arm}.{metric}")
        normalized[arm] = copied_metrics
    return normalized


def _canonical_sha256(value: Any, label: str) -> str:
    _ensure_json_value(value, label)
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure_json_value(value: Any, label: str) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if math.isfinite(value):
            return
        raise ValueError(f"{label} must not contain non-finite numbers")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} mapping keys must be strings")
            _ensure_json_value(item, f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _ensure_json_value(item, f"{label}[{index}]")
        return
    raise ValueError(f"{label} must contain only JSON-compatible values")


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a 64-character SHA-256 digest")
    return value.casefold()


def _runtime_scorer_source_manifest() -> list[dict[str, str]]:
    modules = {
        "bench/candidate_adapters.py": candidate_adapters,
        "bench/candidate_lineage.py": candidate_lineage,
        "bench/candidate_measurement.py": candidate_measurement,
        "bench/episode_membership.py": episode_membership,
    }
    manifest = [
        {
            "path": path,
            "sha256": _module_source_sha256(module, path),
        }
        for path, module in sorted(modules.items())
    ]
    manifest.append(
        {
            "path": "bench/candidate_replay.py",
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    )
    return sorted(manifest, key=lambda item: item["path"])


def _module_source_sha256(module: Any, label: str) -> str:
    source_path = getattr(module, "__file__", None)
    if not isinstance(source_path, str) or not source_path.endswith(".py"):
        raise RuntimeError(f"{label} runtime source is unavailable")
    return hashlib.sha256(Path(source_path).read_bytes()).hexdigest()


def _same_json_value(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        return left.keys() == right.keys() and all(
            _same_json_value(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"duplicate field {key!r}")
        record[key] = value
    return record


def _unique_in_order(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))
