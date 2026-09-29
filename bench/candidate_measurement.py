"""Non-empirical, per-episode G13 candidate measurements.

This module consumes an adapter-normalized episode while retaining its raw
observations. Its candidate contract is not a frozen protocol and its output
cannot certify or establish empirical results.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
import math
import re
from typing import Any

_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CLOCK_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_VERIFICATION_STATUSES = {"passed", "failed", "inconclusive", "error"}
_FAILURE_EVENT_NAMES = {
    "episode_failed",
    "episode_interrupted",
    "episode_unscorable",
    "infrastructure_failure",
    "model_failure",
    "step_failure",
}
_INFRASTRUCTURE_EVENT_NAMES = {"episode_interrupted", "infrastructure_failure"}
_MODEL_FAILURE_EVENT_NAMES = {"model_failure", "step_failure"}


def measure_candidate_episode(
    raw_episode: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a non-claimable candidate measurement for one common raw episode.

    The episode schema requires ``schema_version``, ``scenario_id``,
    ``source_identity``, ``source_sha256``, ``raw_refs``, and an ordered
    ``events`` list. Canonical event names are ``fault_authorization``,
    ``fault_observation``, ``alert_delivery``, ``pre_action_verification``,
    ``diagnosis_output``, ``action_result``, ``post_action_verification``,
    ``cleanup_started``, ``model_failure``, ``infrastructure_failure``, and
    ``episode_interrupted``. Events may contain additional source fields.

    The contract requires ``schema_version``, ``contract_version``,
    ``required_verifier_check_ids``, ``expected_diagnosis_by_scenario``,
    ``diagnosis_label_mapping``, and ``clock_source`` (the event field holding
    timezone-aware RFC 3339 timestamps).
    """
    episode = _validate_episode(raw_episode)
    candidate_contract = _validate_contract(contract)
    events = episode["events"]
    scenario_id = episode["scenario_id"]
    clock_source = candidate_contract["clock_source"]
    timestamps = _parse_event_timestamps(events, clock_source)
    indexed_events = [(index, event["event"], event) for index, event in enumerate(events)]

    verifications = _read_verifications(
        indexed_events,
        scenario_id,
        set(candidate_contract["required_verifier_check_ids"]),
    )
    admission = _assess_admission(indexed_events, verifications)
    _validate_post_admission_order(admission, indexed_events, verifications)
    actions = _read_actions(indexed_events)
    failure_events = [
        {
            "event_index": index,
            "event": name,
            "record": deepcopy(dict(event)),
        }
        for index, name, event in indexed_events
        if name in _FAILURE_EVENT_NAMES
    ]
    has_model_failure = any(
        failure["event"] in _MODEL_FAILURE_EVENT_NAMES for failure in failure_events
    )
    pending_events = [
        failure
        for failure in failure_events
        if failure["event"] in _INFRASTRUCTURE_EVENT_NAMES
        or failure["event"] == "episode_unscorable"
        or (failure["event"] == "episode_failed" and not has_model_failure)
    ]
    pending_adjudication = bool(pending_events)
    a1_resolution = _measure_a1_resolution(
        admission,
        actions,
        verifications,
        failure_events,
        pending_adjudication,
        clock_source,
    )
    score = _score_episode(
        admission,
        actions,
        verifications,
        set(candidate_contract["required_verifier_check_ids"]),
        failure_events,
        pending_adjudication,
    )
    diagnosis = _score_diagnosis(
        admission,
        scenario_id,
        indexed_events,
        candidate_contract,
    )
    ttr = _time_to_recovery(
        admission,
        indexed_events,
        verifications,
        actions,
        timestamps,
        clock_source,
        pending_adjudication,
    )

    return {
        "schema_version": _SCHEMA_VERSION,
        "measurement_version": "g13-candidate-episode-v1",
        "evaluation_mode": "NON_EMPIRICAL",
        "non_empirical": True,
        "empirical": False,
        "empirical_claim_allowed": False,
        "certification_status": "NOT_CERTIFIED",
        "contract": candidate_contract,
        "scenario_id": scenario_id,
        "source_identity": deepcopy(dict(episode["source_identity"])),
        "source_sha256": episode["source_sha256"],
        "raw_refs": deepcopy(episode["raw_refs"]),
        "raw_event_refs": [
            {
                "event_index": index,
                "event": name,
                "raw_ref": deepcopy(event.get("raw_ref")),
            }
            for index, name, event in indexed_events
            if "raw_ref" in event
        ],
        "raw_episode": episode,
        "eligibility": admission,
        "a1_resolution_candidate": a1_resolution,
        "outcome_classification": _classify_outcome(
            admission,
            failure_events,
            a1_resolution,
        ),
        "objective_score": score,
        "diagnosis": diagnosis,
        "time_to_recovery": ttr,
        "failures": failure_events,
        "adjudication": {
            "required": pending_adjudication,
            "status": (
                "pending_independent_adjudication" if pending_adjudication else "not_indicated"
            ),
            "reason": (
                "terminal, interruption or infrastructure failure is retained; "
                "no automatic exclusion or INFRA_INVALID authority is applied"
                if pending_adjudication
                else None
            ),
        },
    }


def _validate_episode(raw_episode: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_episode, Mapping):
        raise TypeError("raw_episode must be a mapping")
    episode = deepcopy(dict(raw_episode))
    if type(episode.get("schema_version")) is not int or episode["schema_version"] != 1:
        raise ValueError("raw_episode schema_version must be integer 1")
    scenario_id = _required_text(episode.get("scenario_id"), "raw_episode scenario_id")
    if not isinstance(episode.get("source_identity"), Mapping):
        raise ValueError("raw_episode source_identity must be a mapping")
    source_sha256 = episode.get("source_sha256")
    if not isinstance(source_sha256, str) or not _SHA256_RE.fullmatch(source_sha256):
        raise ValueError("raw_episode source_sha256 must be a 64-character digest")
    if not isinstance(episode.get("raw_refs"), list):
        raise ValueError("raw_episode raw_refs must be a list")
    events = episode.get("events")
    if not isinstance(events, list):
        raise ValueError("raw_episode events must be a list")
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ValueError(f"raw_episode event {index} must be a mapping")
        event_name = event.get("event")
        if not isinstance(event_name, str) or not event_name.strip():
            raise ValueError(f"raw_episode event {index} requires a non-empty event name")
        if "scenario_id" in event and event["scenario_id"] != scenario_id:
            raise ValueError(f"raw_episode event {index} scenario_id conflicts with the episode")
    return episode


def _validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(contract, Mapping):
        raise TypeError("contract must be a mapping")
    candidate = deepcopy(dict(contract))
    if type(candidate.get("schema_version")) is not int or candidate["schema_version"] != 1:
        raise ValueError("contract schema_version must be integer 1")
    candidate["contract_version"] = _required_text(
        candidate.get("contract_version"), "contract_version"
    )
    required_ids = candidate.get("required_verifier_check_ids")
    if (
        not isinstance(required_ids, list)
        or not required_ids
        or any(not isinstance(check_id, str) or not check_id.strip() for check_id in required_ids)
        or len(set(required_ids)) != len(required_ids)
    ):
        raise ValueError("required_verifier_check_ids must be unique non-empty strings")
    candidate["required_verifier_check_ids"] = [
        _required_text(check_id, "required_verifier_check_id") for check_id in required_ids
    ]

    expected = candidate.get("expected_diagnosis_by_scenario")
    if not isinstance(expected, Mapping) or not expected:
        raise ValueError("expected_diagnosis_by_scenario must be a non-empty mapping")
    for scenario_id, label in expected.items():
        _required_text(scenario_id, "expected diagnosis scenario ID")
        _required_text(label, f"expected diagnosis label for {scenario_id!r}")

    label_mapping = candidate.get("diagnosis_label_mapping")
    if not isinstance(label_mapping, Mapping) or not label_mapping:
        raise ValueError("diagnosis_label_mapping must be a non-empty mapping")
    for raw_label, category in label_mapping.items():
        _required_text(raw_label, "diagnosis mapping source label")
        _required_text(category, f"diagnosis mapping category for {raw_label!r}")

    clock_source = candidate.get("clock_source")
    if not isinstance(clock_source, str) or not _CLOCK_FIELD_RE.fullmatch(clock_source):
        raise ValueError("clock_source must name one event timestamp field")
    candidate["expected_diagnosis_by_scenario"] = dict(expected)
    candidate["diagnosis_label_mapping"] = dict(label_mapping)
    return candidate


def _required_text(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or not value.isprintable()
    ):
        raise ValueError(f"{label} must be a non-empty printable string")
    return value


def _parse_event_timestamps(
    events: list[Mapping[str, Any]], clock_source: str
) -> dict[int, datetime]:
    parsed: dict[int, datetime] = {}
    previous: datetime | None = None
    for index, event in enumerate(events):
        if clock_source not in event or event[clock_source] is None:
            continue
        value = event[clock_source]
        if not isinstance(value, str):
            raise ValueError(
                f"event {index} {clock_source} must be a timezone-aware RFC 3339 string"
            )
        if not _RFC3339_RE.fullmatch(value):
            raise ValueError(f"event {index} {clock_source} is not a valid RFC 3339 timestamp")
        try:
            timestamp = datetime.fromisoformat(
                value[:-1] + "+00:00" if value.endswith("Z") else value
            )
        except ValueError as exc:
            raise ValueError(
                f"event {index} {clock_source} is not a valid RFC 3339 timestamp"
            ) from exc
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(f"event {index} {clock_source} must include a timezone")
        timestamp = timestamp.astimezone(UTC)
        try:
            numeric_timestamp = timestamp.timestamp()
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError(f"event {index} {clock_source} is outside supported range") from exc
        if not math.isfinite(numeric_timestamp):
            raise ValueError(f"event {index} {clock_source} is not finite")
        if previous is not None and timestamp < previous:
            raise ValueError("raw episode event timestamps are out of order")
        parsed[index] = timestamp
        previous = timestamp
    return parsed


def _read_verifications(
    indexed_events: list[tuple[int, str, Mapping[str, Any]]],
    scenario_id: str,
    required_ids: set[str],
) -> dict[str, Any]:
    pre_action_events = [
        (index, event) for index, name, event in indexed_events if name == "pre_action_verification"
    ]
    if len(pre_action_events) > 1:
        raise ValueError("raw episode has duplicate pre-action verifications")
    pre_action = (
        _parse_verification(
            pre_action_events[0][0],
            pre_action_events[0][1],
            scenario_id,
            required_ids,
        )
        if pre_action_events
        else None
    )
    post_action = [
        _parse_verification(index, event, scenario_id, required_ids)
        for index, name, event in indexed_events
        if name == "post_action_verification"
    ]
    cleanup_indices = [index for index, name, _ in indexed_events if name == "cleanup_started"]
    return {
        "pre_action": pre_action,
        "post_action": post_action,
        "cleanup_index": min(cleanup_indices) if cleanup_indices else None,
    }


def _parse_verification(
    index: int,
    event: Mapping[str, Any],
    scenario_id: str,
    required_ids: set[str],
) -> dict[str, Any]:
    verification = event.get("verification")
    if verification is None:
        return {
            "event_index": index,
            "record": event,
            "status": None,
            "env_resolved": None,
            "checks": None,
        }
    if not isinstance(verification, Mapping):
        raise ValueError(f"verification event {index} verification must be a mapping")
    observed_scenario_id = verification.get("scenario_id")
    if observed_scenario_id is not None and observed_scenario_id != scenario_id:
        raise ValueError(f"verification event {index} scenario_id conflicts with the episode")
    status = verification.get("verification_status")
    if status is not None and (not isinstance(status, str) or status not in _VERIFICATION_STATUSES):
        raise ValueError(f"verification event {index} has an invalid verification_status")
    env_resolved = verification.get("env_resolved")
    if env_resolved is not None and type(env_resolved) is not bool:
        raise ValueError(f"verification event {index} env_resolved must be boolean")
    if status in {"passed", "failed"} and type(env_resolved) is not bool:
        raise ValueError(f"conclusive verification event {index} requires boolean env_resolved")
    if status == "passed" and env_resolved is False:
        raise ValueError(f"verification event {index} passed status conflicts with env_resolved")
    if status == "failed" and env_resolved is True:
        raise ValueError(f"verification event {index} failed status conflicts with env_resolved")
    if status in {"inconclusive", "error"} and env_resolved is True:
        raise ValueError(f"non-conclusive verification event {index} cannot claim env_resolved")

    raw_checks = verification.get("checks")
    checks: dict[str, bool | None] | None
    if raw_checks is None:
        checks = None
    elif not isinstance(raw_checks, list):
        raise ValueError(f"verification event {index} checks must be a list")
    else:
        checks = {}
        for check_index, check in enumerate(raw_checks):
            if not isinstance(check, Mapping):
                raise ValueError(
                    f"verification event {index} check {check_index} must be a mapping"
                )
            check_id = _required_text(
                check.get("check_id"),
                f"verification event {index} check {check_index} check_id",
            )
            if check_id in checks:
                raise ValueError(f"verification event {index} has duplicate check_id {check_id!r}")
            passed = check.get("passed")
            if passed is not None and type(passed) is not bool:
                raise ValueError(
                    f"verification event {index} check {check_id!r} passed must be boolean"
                )
            required = check.get("required")
            if required is not None and type(required) is not bool:
                raise ValueError(
                    f"verification event {index} check {check_id!r} required must be boolean"
                )
            if check_id in required_ids and check.get("required") is False:
                raise ValueError(
                    f"verification event {index} marks required check {check_id!r} optional"
                )
            checks[check_id] = passed
    if (
        status == "passed"
        and checks is not None
        and any(checks.get(check_id) is False for check_id in required_ids)
    ):
        raise ValueError(
            f"verification event {index} passed status conflicts with a failed required check"
        )
    return {
        "event_index": index,
        "record": event,
        "status": status,
        "env_resolved": env_resolved,
        "checks": checks,
    }


def _assess_admission(
    indexed_events: list[tuple[int, str, Mapping[str, Any]]],
    verifications: Mapping[str, Any],
) -> dict[str, Any]:
    authorized = _consistent_boolean(indexed_events, "fault_authorization", "authorized")
    observed = _consistent_boolean(indexed_events, "fault_observation", "observed")
    delivery_values = [
        _event_boolean(event, "delivered", index, name)
        for index, name, event in indexed_events
        if name == "alert_delivery"
    ]
    delivered = (
        True
        if True in delivery_values
        else False
        if delivery_values and all(value is False for value in delivery_values)
        else None
    )
    pre_action = verifications["pre_action"]
    pre_action_failed = (
        pre_action is not None
        and pre_action["status"] == "failed"
        and pre_action["env_resolved"] is False
    )
    pre_action_recovered = (
        pre_action is not None
        and pre_action["status"] == "passed"
        and pre_action["env_resolved"] is True
    )

    failed_requirements: list[str] = []
    unknown_requirements: list[str] = []
    if authorized is False:
        failed_requirements.append("fault authorization was not observed as authorized")
    elif authorized is None:
        unknown_requirements.append("fault authorization is missing or undetermined")
    if observed is False:
        failed_requirements.append("fault was not observed")
    elif observed is None:
        unknown_requirements.append("fault observation is missing or undetermined")
    if delivered is False:
        failed_requirements.append("alert delivery was not observed")
    elif delivered is None:
        unknown_requirements.append("alert delivery is missing or undetermined")
    if pre_action_recovered:
        failed_requirements.append("pre-action verifier did not observe an unresolved fault")
    elif not pre_action_failed:
        unknown_requirements.append(
            "pre-action verifier is missing or inconclusive about the unresolved fault"
        )

    if failed_requirements:
        status = "ineligible"
        eligible: bool | None = False
        reasons = failed_requirements
    elif unknown_requirements:
        status = "undetermined"
        eligible = None
        reasons = unknown_requirements
    else:
        status = "eligible"
        eligible = True
        reasons = []
    return {"status": status, "eligible": eligible, "reasons": reasons}


def _validate_post_admission_order(
    admission: Mapping[str, Any],
    indexed_events: list[tuple[int, str, Mapping[str, Any]]],
    verifications: Mapping[str, Any],
) -> None:
    if admission["status"] != "eligible":
        return
    pre_action = verifications["pre_action"]
    assert pre_action is not None
    pre_action_index = pre_action["event_index"]
    ordered_event_names = {
        "action_result",
        "diagnosis_output",
        "episode_failed",
        "episode_unscorable",
        "model_failure",
        "policy_output",
        "step_failure",
    }
    if any(
        name in ordered_event_names and index < pre_action_index
        for index, name, _ in indexed_events
    ):
        raise ValueError("policy or terminal event precedes failed pre-action verification")
    if not any(
        index < pre_action_index and name == "alert_delivery" and event.get("delivered") is True
        for index, name, event in indexed_events
    ):
        raise ValueError("delivered alert must precede failed pre-action verification")


def _consistent_boolean(
    indexed_events: list[tuple[int, str, Mapping[str, Any]]],
    event_name: str,
    field: str,
) -> bool | None:
    values = [
        _event_boolean(event, field, index, name)
        for index, name, event in indexed_events
        if name == event_name
    ]
    known = {value for value in values if value is not None}
    if len(known) > 1:
        raise ValueError(f"raw episode has contradictory {event_name}.{field} observations")
    return next(iter(known)) if known else None


def _event_boolean(
    event: Mapping[str, Any], field: str, index: int, event_name: str
) -> bool | None:
    value = event.get(field)
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"event {index} {event_name}.{field} must be boolean")
    return value


def _read_actions(
    indexed_events: list[tuple[int, str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    actions = []
    for index, name, event in indexed_events:
        if name != "action_result":
            continue
        executed = _event_boolean(event, "executed", index, name)
        if executed is None:
            raise ValueError(f"action_result event {index} requires boolean executed")
        claimed = _event_boolean(event, "agent_claimed_resolved", index, name)
        actions.append(
            {
                "event_index": index,
                "record": event,
                "executed": executed,
                "agent_claimed_resolved": claimed,
            }
        )
    return actions


def _score_episode(
    admission: Mapping[str, Any],
    actions: list[Mapping[str, Any]],
    verifications: Mapping[str, Any],
    required_ids: set[str],
    failure_events: list[Mapping[str, Any]],
    pending_adjudication: bool,
) -> dict[str, Any]:
    components: dict[str, float | None] = {
        "verified_recovery": None,
        "required_check_coverage": None,
        "false_resolution_claim": None,
    }
    if admission["status"] != "eligible":
        return {"value": None, "reason": "episode_admission_not_eligible", "components": components}
    unscorable_events = [item for item in failure_events if item["event"] == "episode_unscorable"]
    if unscorable_events:
        failure = unscorable_events[-1]["record"].get("failure")
        reason = (
            f"episode_unscorable: {failure}"
            if isinstance(failure, str) and failure
            else "episode_unscorable: raw terminal observation"
        )
        return {"value": None, "reason": reason, "components": components}
    if pending_adjudication:
        return {
            "value": None,
            "reason": "pending_independent_adjudication",
            "components": components,
        }
    if not actions:
        return {
            "value": None,
            "reason": "action_and_claim_observation_missing",
            "components": components,
        }

    # The pre-action verifier establishes eligibility; only a post-attempt
    # observation can supply this episode's recovery and check-coverage score.
    final_action_attempt_index = actions[-1]["event_index"]
    observations = [
        observation
        for observation in verifications["post_action"]
        if observation["event_index"] > final_action_attempt_index
        and (
            verifications["cleanup_index"] is None
            or observation["event_index"] < verifications["cleanup_index"]
        )
    ]
    if not observations:
        return {
            "value": None,
            "reason": (
                "post_action_verifier_observation_missing_before_cleanup"
                if verifications["cleanup_index"] is not None
                else "post_action_verifier_observation_missing"
            ),
            "components": components,
        }
    selected = observations[-1]

    status = selected["status"]
    if status not in {"passed", "failed"}:
        reason = (
            "objective_verifier_status_missing"
            if status is None
            else f"objective_verifier_{status}"
        )
        return {
            "value": None,
            "reason": reason,
            "components": components,
            "verifier_event_index": selected["event_index"],
        }
    verified_recovery = status == "passed" and selected["env_resolved"] is True
    components["verified_recovery"] = float(verified_recovery)

    checks = selected["checks"]
    missing_check_ids = sorted(
        check_id for check_id in required_ids if checks is None or checks.get(check_id) is None
    )
    if missing_check_ids:
        return {
            "value": None,
            "reason": f"required_verifier_check_observation_missing: {missing_check_ids}",
            "components": components,
            "verifier_event_index": selected["event_index"],
        }
    assert checks is not None
    coverage = sum(checks[check_id] is True for check_id in required_ids) / len(required_ids)
    components["required_check_coverage"] = coverage

    claim_values = [action["agent_claimed_resolved"] for action in actions]
    if any(value is None for value in claim_values):
        return {
            "value": None,
            "reason": "agent_resolution_claim_observation_missing",
            "components": components,
            "verifier_event_index": selected["event_index"],
        }
    claimed_resolved = any(value is True for value in claim_values)
    false_resolution_claim = claimed_resolved and not verified_recovery
    components["false_resolution_claim"] = float(false_resolution_claim)
    value = (
        0.75 * components["verified_recovery"]
        + 0.25 * components["required_check_coverage"]
        - 0.25 * components["false_resolution_claim"]
    )
    if not math.isfinite(value):
        raise ValueError("candidate episode score is not finite")
    return {
        "value": value,
        "reason": None,
        "formula": (
            "0.75*verified_recovery + 0.25*required_check_coverage - 0.25*false_resolution_claim"
        ),
        "components": components,
        "verifier_event_index": selected["event_index"],
    }


def _measure_a1_resolution(
    admission: Mapping[str, Any],
    actions: list[Mapping[str, Any]],
    verifications: Mapping[str, Any],
    failure_events: list[Mapping[str, Any]],
    pending_adjudication: bool,
    clock_source: str,
) -> dict[str, Any]:
    def result(
        value: int | None,
        reason: str,
        endpoint: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "value": value,
            "reason": reason,
            "first_verifier_endpoint": (deepcopy(dict(endpoint)) if endpoint is not None else None),
        }

    if admission["status"] != "eligible":
        return result(None, "episode_admission_not_eligible")
    if any(item["event"] == "episode_unscorable" for item in failure_events):
        return result(None, "episode_unscorable_pending_independent_adjudication")
    if pending_adjudication:
        return result(None, "pending_independent_adjudication")

    executed_action_indices = [
        action["event_index"] for action in actions if action["executed"] is True
    ]
    post_action = [
        observation
        for observation in verifications["post_action"]
        if any(
            action_index < observation["event_index"] for action_index in executed_action_indices
        )
    ]
    cleanup_index = verifications["cleanup_index"]
    before_cleanup = (
        [observation for observation in post_action if observation["event_index"] < cleanup_index]
        if cleanup_index is not None
        else []
    )
    first_success = next(
        (
            observation
            for observation in before_cleanup
            if observation["status"] == "passed" and observation["env_resolved"] is True
        ),
        None,
    )
    if first_success is not None:
        return result(
            1,
            "first_conclusive_post_action_success_before_cleanup",
            _verifier_endpoint(first_success, clock_source),
        )

    first_failure = next(
        (
            observation
            for observation in before_cleanup
            if observation["status"] == "failed" and observation["env_resolved"] is False
        ),
        None,
    )
    endpoint = (
        _verifier_endpoint(first_failure, clock_source) if first_failure is not None else None
    )
    if any(item["event"] in _MODEL_FAILURE_EVENT_NAMES for item in failure_events):
        return result(0, "eligible_model_failure", endpoint)
    if actions and not executed_action_indices:
        return result(0, "eligible_no_action_execution", endpoint)
    if not actions:
        return result(None, "action_execution_observation_missing")
    if not post_action:
        return result(None, "post_action_verifier_missing_or_inconclusive")
    if cleanup_index is None:
        return result(None, "cleanup_boundary_observation_missing", endpoint)
    if not before_cleanup:
        return result(None, "post_action_verifier_missing_or_inconclusive")
    final_observation = before_cleanup[-1]
    if final_observation["status"] == "failed" and final_observation["env_resolved"] is False:
        return result(0, "conclusive_verifier_no_recovery_before_cleanup", endpoint)
    return result(None, "post_action_verifier_missing_or_inconclusive", endpoint)


def _verifier_endpoint(
    observation: Mapping[str, Any],
    clock_source: str,
) -> dict[str, Any]:
    record = observation["record"]
    return {
        "event_index": observation["event_index"],
        "verification_status": observation["status"],
        "env_resolved": observation["env_resolved"],
        "recorded_at": deepcopy(record.get(clock_source)),
        "raw_ref": deepcopy(record.get("raw_ref")),
    }


def _score_diagnosis(
    admission: Mapping[str, Any],
    scenario_id: str,
    indexed_events: list[tuple[int, str, Mapping[str, Any]]],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    if admission["status"] != "eligible":
        return {
            "correct": None,
            "score": None,
            "expected_category": None,
            "mapped_category": None,
            "raw_label": None,
            "reason": "episode_admission_not_eligible",
        }
    expected = contract["expected_diagnosis_by_scenario"].get(scenario_id)
    if expected is None:
        return {
            "correct": None,
            "score": None,
            "expected_category": None,
            "mapped_category": None,
            "raw_label": None,
            "reason": "expected_diagnosis_label_missing_from_contract",
        }
    outputs = [event for _, name, event in indexed_events if name == "diagnosis_output"]
    if len(outputs) > 1:
        return {
            "correct": None,
            "score": None,
            "expected_category": expected,
            "mapped_category": None,
            "raw_label": None,
            "candidate_raw_labels": [deepcopy(event.get("label")) for event in outputs],
            "reason": "multiple_diagnosis_outputs_ambiguous",
        }
    raw_label = outputs[-1].get("label") if outputs else None
    if not isinstance(raw_label, str) or not raw_label.strip():
        return {
            "correct": False,
            "score": 0.0,
            "expected_category": expected,
            "mapped_category": None,
            "raw_label": deepcopy(raw_label),
            "reason": "missing_or_invalid_diagnosis_output",
        }
    mapped_category = contract["diagnosis_label_mapping"].get(raw_label)
    if mapped_category is None:
        return {
            "correct": False,
            "score": 0.0,
            "expected_category": expected,
            "mapped_category": None,
            "raw_label": raw_label,
            "reason": "diagnosis_label_not_in_contract_mapping",
        }
    correct = mapped_category == expected
    return {
        "correct": correct,
        "score": float(correct),
        "expected_category": expected,
        "mapped_category": mapped_category,
        "raw_label": raw_label,
        "reason": None if correct else "diagnosis_category_mismatch",
    }


def _time_to_recovery(
    admission: Mapping[str, Any],
    indexed_events: list[tuple[int, str, Mapping[str, Any]]],
    verifications: Mapping[str, Any],
    actions: list[Mapping[str, Any]],
    timestamps: Mapping[int, datetime],
    clock_source: str,
    pending_adjudication: bool,
) -> dict[str, Any]:
    def result(seconds: float | None, status: str, reason: str | None) -> dict[str, Any]:
        return {
            "seconds": seconds,
            "status": status,
            "reason": reason,
            "clock_source": clock_source,
        }

    if admission["status"] != "eligible":
        return result(None, "unavailable", "episode admission is not established")
    if pending_adjudication:
        return result(None, "unavailable", "pending_independent_adjudication")
    alert_event = next(
        (
            (index, event)
            for index, name, event in indexed_events
            if name == "alert_delivery" and event.get("delivered") is True
        ),
        None,
    )
    if alert_event is None:
        return result(None, "unavailable", "delivered alert observation is missing")
    alert_index, _ = alert_event
    alert_time = timestamps.get(alert_index)
    if alert_time is None:
        return result(None, "unavailable", f"delivered alert lacks {clock_source}")

    executed_action_indices = [
        action["event_index"] for action in actions if action["executed"] is True
    ]
    if not executed_action_indices:
        return result(None, "censored", "no action was executed")

    cleanup_indices = [index for index, name, _ in indexed_events if name == "cleanup_started"]
    cleanup_index = min(cleanup_indices) if cleanup_indices else None
    post_action = sorted(verifications["post_action"], key=lambda item: item["event_index"])
    for observation in post_action:
        index = observation["event_index"]
        if not any(action_index < index for action_index in executed_action_indices):
            continue
        if cleanup_index is not None and index >= cleanup_index:
            continue
        if observation["status"] != "passed" or observation["env_resolved"] is not True:
            continue
        if index <= alert_index:
            continue
        if cleanup_index is None:
            return result(None, "unavailable", "cleanup boundary observation is missing")
        recovery_time = timestamps.get(index)
        if recovery_time is None:
            return result(None, "unavailable", f"first conclusive success lacks {clock_source}")
        seconds = (recovery_time - alert_time).total_seconds()
        if seconds < 0 or not math.isfinite(seconds):
            raise ValueError("time-to-recovery timestamps are invalid")
        return result(seconds, "observed", None)
    return result(
        None,
        "censored",
        "no conclusive post-action verifier success was observed before cleanup",
    )


def _classify_outcome(
    admission: Mapping[str, Any],
    failure_events: list[Mapping[str, Any]],
    a1_resolution: Mapping[str, Any],
) -> str:
    has_model_failure = any(item["event"] in _MODEL_FAILURE_EVENT_NAMES for item in failure_events)
    if any(
        item["event"] in _INFRASTRUCTURE_EVENT_NAMES or item["event"] == "episode_unscorable"
        for item in failure_events
    ) or (
        any(item["event"] == "episode_failed" for item in failure_events) and not has_model_failure
    ):
        return (
            "eligible_pending_independent_adjudication"
            if admission["status"] == "eligible"
            else "pending_independent_adjudication"
        )
    if admission["status"] != "eligible":
        return "not_assessed"
    if a1_resolution["value"] == 1:
        return "eligible_recovered"
    if a1_resolution["value"] == 0:
        return "eligible_negative"
    if any(item["event"] == "episode_unscorable" for item in failure_events):
        return "eligible_outcome_unscorable"
    if a1_resolution["reason"] == "action_execution_observation_missing":
        return "eligible_outcome_undetermined"
    if a1_resolution["reason"] == "post_action_verifier_missing_or_inconclusive":
        return "eligible_outcome_unscorable"
    return "eligible_outcome_unscorable"
