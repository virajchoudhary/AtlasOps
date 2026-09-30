"""Synthetic, non-claimable adapters for the three required G13 arms."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from bench.candidate_adapters import adapt_three_arm_raw
from bench.candidate_measurement import measure_candidate_episode
from bench.episode_membership import ordered_scenario_ids_sha256


SCENARIO_ID = "synthetic/sf-001"
SOURCE_IDENTITY = {"run_id": "synthetic-run", "model": "synthetic-model"}
REQUIRED_CHECK_IDS = ["deployment_ready", "latency_below_threshold"]


def _prediction(**updates) -> dict:
    prediction = {
        "severity": "P1",
        "root_cause": "packet loss",
        "affected_services": ["checkout"],
        "confidence": 0.5,
    }
    prediction.update(updates)
    return prediction


def _diagnosis_row(
    variant: str,
    *,
    status: str = "ok",
    prediction: dict | None = None,
) -> dict:
    row = {
        "scenario_id": SCENARIO_ID,
        "evaluation_mode": "empirical",
        "status": status,
        "prediction": prediction,
    }
    if status == "ok":
        parsed = _prediction() if prediction is None else prediction
        row["prediction"] = parsed
        row["raw_model_response"] = json.dumps(parsed, separators=(",", ":"))
    if variant == "Zero-Shot Baseline":
        row.update(SOURCE_IDENTITY)
    return row


def _jsonl(records: list[dict]) -> bytes:
    return "".join(
        json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n" for record in records
    ).encode("utf-8")


def _adapt(
    records: list[dict],
    variant: str,
    expected_ids: list[str] | None = None,
    source_identity: dict | None = None,
):
    raw = _jsonl(records)
    return adapt_three_arm_raw(
        raw,
        variant=variant,
        partition="val",
        expected_scenario_ids=([SCENARIO_ID] if expected_ids is None else expected_ids),
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        source_identity=SOURCE_IDENTITY if source_identity is None else source_identity,
    )


def _eligible_measurement_episode(episode: dict) -> dict:
    # Synthetic admission context exercises diagnosis scoring only; it is not
    # asserted to originate in either native adapter format.
    admission_events = [
        {"event": "fault_authorization", "authorized": True},
        {"event": "fault_observation", "observed": True},
        {
            "event": "alert_delivery",
            "delivered": True,
            "recorded_at": "2026-09-29T12:00:00Z",
        },
    ]
    result = deepcopy(episode)
    if not any(event["event"] == "pre_action_verification" for event in result["events"]):
        admission_events.append(
            {
                "event": "pre_action_verification",
                "verification": {
                    "verification_status": "failed",
                    "env_resolved": False,
                    "checks": [
                        {"check_id": check_id, "passed": False}
                        for check_id in REQUIRED_CHECK_IDS
                    ],
                },
            }
        )
    result["events"] = admission_events + result["events"]
    return result


def _measurement_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "candidate-v1-synthetic-test",
        "required_verifier_check_ids": REQUIRED_CHECK_IDS.copy(),
        "expected_diagnosis_by_scenario": {SCENARIO_ID: "network_partition"},
        "diagnosis_label_mapping": {"packet loss": "network_partition"},
        "clock_source": "recorded_at",
    }


@pytest.mark.parametrize(
    ("status", "expected_event"),
    [("error", "model_failure"), ("ok", "diagnosis_output")],
)
def test_diagnosis_rows_preserve_failures_without_inventing_incident_evidence(
    status, expected_event
):
    record = _diagnosis_row("SFT Model", status=status)
    record.update(
        {
            "empirical_claim_allowed": status == "ok",
            "scoring_reference": {"expected_root_cause": "private-truth-not-for-policy"},
            "env_resolved": None,
            "time_to_resolve_s": None,
        }
    )

    adapted = _adapt([record], "SFT Model")

    assert adapted["evaluation_mode"] == "NON_EMPIRICAL_OBSERVATION"
    assert adapted["empirical_claim_allowed"] is False
    assert adapted["certification_status"] == "NOT_CERTIFIED"
    assert adapted["source_sha256"] == hashlib.sha256(_jsonl([record])).hexdigest()
    assert adapted["raw_records"] == [record]
    assert adapted["missing_scenario_ids"] == []
    assert adapted["source_identity"] == {"run_id": None, "model": None}
    assert adapted["source_identity_status"] == "UNBOUND"
    assert adapted["source_identity_declaration"] == SOURCE_IDENTITY
    episode = adapted["episodes"][0]
    assert episode["scenario_id"] == SCENARIO_ID
    assert episode["source_identity"] == {"run_id": None, "model": None}
    assert episode["source_identity_status"] == "UNBOUND"
    assert episode["source_identity_declaration"] == SOURCE_IDENTITY
    assert episode["events"][0]["event"] == expected_event
    assert not any(
        event["event"]
        in {
            "fault_authorization",
            "fault_observation",
            "alert_delivery",
            "pre_action_verification",
            "post_action_verification",
        }
        for event in episode["events"]
    )
    assert "private-truth-not-for-policy" not in json.dumps(episode)
    if status == "ok":
        assert episode["events"][0]["label"] == "packet loss"


def test_g9_adapter_marks_diagnosis_unavailable_and_scorer_keeps_it_null_when_eligible():
    events = _g9_events()

    adapted = _adapt(events, "SFT + GRPO")

    episode = adapted["episodes"][0]
    raw_refs = deepcopy(episode["raw_refs"])
    assert episode["diagnosis_observation"] == {
        "status": "unavailable",
        "reason": "g9_diagnosis_not_observed",
        "source_format": "g9_event_stream",
        "source_sha256": adapted["source_sha256"],
        "raw_refs": raw_refs,
    }
    assert not any(event["event"] == "diagnosis_output" for event in episode["events"])
    assert adapted["raw_records"] == events

    measured = measure_candidate_episode(
        _eligible_measurement_episode(episode),
        _measurement_contract(),
    )

    assert measured["eligibility"]["status"] == "eligible"
    assert measured["diagnosis"] == {
        "correct": None,
        "score": None,
        "expected_category": "network_partition",
        "mapped_category": None,
        "raw_label": None,
        "reason": "g9_diagnosis_not_observed",
    }
    assert measured["raw_episode"]["raw_refs"] == raw_refs
    assert measured["raw_episode"]["source_sha256"] == adapted["source_sha256"]


@pytest.mark.parametrize("variant", ["Zero-Shot Baseline", "SFT Model"])
def test_missing_native_model_prediction_remains_diagnosis_negative_when_eligible(variant):
    row = _diagnosis_row(variant, status="error")

    adapted = _adapt([row], variant)
    episode = _eligible_measurement_episode(adapted["episodes"][0])

    measured = measure_candidate_episode(episode, _measurement_contract())

    assert measured["eligibility"]["status"] == "eligible"
    assert measured["diagnosis"]["correct"] is False
    assert measured["diagnosis"]["score"] == 0.0
    assert measured["diagnosis"]["raw_label"] is None
    assert measured["diagnosis"]["reason"] == "missing_or_invalid_diagnosis_output"
    assert episode.get("diagnosis_observation") is None


@pytest.mark.parametrize("variant", ["Zero-Shot Baseline", "SFT Model"])
def test_native_rows_bind_identity_only_when_raw_rows_contain_it(variant):
    row = _diagnosis_row(variant)
    row.update(SOURCE_IDENTITY)

    adapted = _adapt([row], variant)

    assert adapted["source_identity"] == SOURCE_IDENTITY
    assert adapted["source_identity_status"] == "RAW_BOUND"
    assert adapted["source_identity_declaration"] == SOURCE_IDENTITY
    episode = adapted["episodes"][0]
    assert episode["source_identity"] == SOURCE_IDENTITY
    assert episode["source_identity_status"] == "RAW_BOUND"
    assert episode["source_identity_declaration"] == SOURCE_IDENTITY
    if variant == "Zero-Shot Baseline":
        assert adapted["source_identity_model_basis"] == "requested_model_name_only"
        assert episode["source_identity_model_basis"] == "requested_model_name_only"
        assert adapted["source_identity_limitation"] == (
            "requested model name does not attest the served model digest"
        )
        assert episode["source_identity_limitation"] == (
            "requested model name does not attest the served model digest"
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("run_id", "other-run"), ("model", "other-model")],
)
def test_native_row_raw_identity_conflicts_with_declaration_fail_closed(field, value):
    row = _diagnosis_row("SFT Model")
    row.update(SOURCE_IDENTITY)
    row[field] = value

    with pytest.raises(ValueError, match="identity mismatch"):
        _adapt([row], "SFT Model")


@pytest.mark.parametrize("variant", ["Zero-Shot Baseline", "SFT Model"])
@pytest.mark.parametrize(
    ("field", "change"),
    [
        ("severity", "missing"),
        ("severity", "wrong_type"),
        ("root_cause", "missing"),
        ("root_cause", "wrong_type"),
        ("affected_services", "missing"),
        ("affected_services", "wrong_type"),
        ("confidence", "missing"),
        ("confidence", "wrong_type"),
        ("confidence", "non_finite"),
    ],
)
def test_success_rows_reject_invalid_native_prediction_schema(variant, field, change):
    invalid = _prediction()
    if change == "missing":
        invalid.pop(field)
    elif change == "wrong_type":
        invalid[field] = {
            "severity": 1,
            "root_cause": ["packet loss"],
            "affected_services": "checkout",
            "confidence": True,
        }[field]
    else:
        invalid["confidence"] = float("nan")
    row = _diagnosis_row(variant)
    if change == "non_finite":
        row["raw_model_response"] = (
            '{"severity":"P1","root_cause":"packet loss",'
            '"affected_services":["checkout"],"confidence":NaN}'
        )
    else:
        row["prediction"] = invalid
        row["raw_model_response"] = json.dumps(invalid, separators=(",", ":"))

    with pytest.raises(ValueError, match="invalid successful prediction"):
        _adapt([row], variant)


@pytest.mark.parametrize("variant", ["Zero-Shot Baseline", "SFT Model"])
def test_success_rows_reject_raw_response_that_disagrees_with_parsed_prediction(variant):
    row = _diagnosis_row(variant)
    row["raw_model_response"] = json.dumps(
        _prediction(root_cause="database saturation"),
        separators=(",", ":"),
    )

    with pytest.raises(ValueError, match="differs from raw_model_response"):
        _adapt([row], variant)


@pytest.mark.parametrize("variant", ["Zero-Shot Baseline", "SFT Model"])
@pytest.mark.parametrize(
    "raw_response",
    [
        (
            '{"severity":"P0","severity":"P1","root_cause":"packet loss",'
            '"affected_services":["checkout"],"confidence":0.5}'
        ),
        (
            '{"severity":"P1","root_cause":"packet loss",'
            '"affected_services":["checkout"],"confidence":0.5,'
            '"extra":{"source":"first","source":"second"}}'
        ),
    ],
    ids=["top-level-duplicate", "nested-duplicate"],
)
def test_success_rows_reject_ambiguous_raw_json_objects(variant, raw_response):
    row = _diagnosis_row(variant)
    row["raw_model_response"] = raw_response

    with pytest.raises(ValueError, match="invalid successful prediction") as exc:
        _adapt([row], variant)

    assert raw_response not in str(exc.value)


def test_g6_and_g8_keep_their_native_empty_affected_services_rules():
    row = _diagnosis_row("Zero-Shot Baseline", prediction=_prediction(affected_services=[]))

    adapted = _adapt([row], "Zero-Shot Baseline")

    assert adapted["episodes"][0]["events"][0]["event"] == "diagnosis_output"

    sft_row = _diagnosis_row("SFT Model", prediction=_prediction(affected_services=[]))
    with pytest.raises(ValueError, match="invalid successful prediction"):
        _adapt([sft_row], "SFT Model")


@pytest.mark.parametrize("field", ["run_id", "model"])
def test_g6_requires_each_raw_row_to_bind_requested_identity(field):
    row = _diagnosis_row("Zero-Shot Baseline", status="error")
    del row[field]

    with pytest.raises(ValueError, match="requires non-empty"):
        _adapt([row], "Zero-Shot Baseline")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", None),
        ("run_id", 7),
        ("run_id", " "),
        ("model", None),
        ("model", {"name": "synthetic-model"}),
        ("model", " "),
    ],
)
def test_g6_requested_identity_fields_must_be_nonempty_text(field, value):
    row = _diagnosis_row("Zero-Shot Baseline", status="error")
    row[field] = value

    with pytest.raises(ValueError, match="requires non-empty"):
        _adapt([row], "Zero-Shot Baseline")


@pytest.mark.parametrize("field", ["run_id", "model"])
def test_g6_rejects_requested_identity_mismatch_on_any_row(field):
    rows = [
        _diagnosis_row("Zero-Shot Baseline", status="error"),
        _diagnosis_row("Zero-Shot Baseline", status="error"),
    ]
    rows[1][field] = f"other-{field}"

    with pytest.raises(ValueError, match="identity mismatch"):
        _adapt(rows, "Zero-Shot Baseline")


def test_g8_without_a_raw_run_id_stays_fully_unbound_even_if_model_is_present():
    row = _diagnosis_row("SFT Model")
    row["model"] = SOURCE_IDENTITY["model"]

    adapted = _adapt([row], "SFT Model")

    assert adapted["source_identity"] == {"run_id": None, "model": None}
    assert adapted["source_identity_status"] == "UNBOUND"
    assert adapted["episodes"][0]["source_identity"] == {"run_id": None, "model": None}
    assert adapted["source_identity_limitation"] == (
        "raw rows lack run_id; identity remains UNBOUND"
    )
    assert adapted["episodes"][0]["source_identity_limitation"] == (
        "raw rows lack run_id; identity remains UNBOUND"
    )


def test_g8_does_not_bind_run_identity_reported_by_only_some_rows():
    first = _diagnosis_row("SFT Model")
    first.update(SOURCE_IDENTITY)
    second = _diagnosis_row("SFT Model")
    second["scenario_id"] = "synthetic/sf-002"

    adapted = _adapt(
        [first, second],
        "SFT Model",
        [SCENARIO_ID, "synthetic/sf-002"],
    )

    assert adapted["source_identity"] == {"run_id": None, "model": None}
    assert adapted["source_identity_status"] == "UNBOUND"
    assert adapted["source_identity_limitation"] == (
        "raw rows lack run_id, model; identity remains UNBOUND"
    )


def test_g6_raw_bound_identity_is_labeled_as_requested_name_not_served_digest():
    row = _diagnosis_row("Zero-Shot Baseline")
    row["observed_model_identity"] = {
        "name": "synthetic-model",
        "digest": "sha256:" + "a" * 64,
    }

    adapted = _adapt([row], "Zero-Shot Baseline")

    assert adapted["source_identity"] == SOURCE_IDENTITY
    assert adapted["source_identity_model_basis"] == "requested_model_name_only"
    assert adapted["source_identity_limitation"] == (
        "requested model name does not attest the served model digest"
    )
    assert adapted["empirical_claim_allowed"] is False
    assert adapted["episodes"][0]["source_identity"] == SOURCE_IDENTITY
    assert adapted["episodes"][0]["source_identity_model_basis"] == "requested_model_name_only"
    assert "observed_model_identity" not in adapted["episodes"][0]


def _g9_events(expected_ids: list[str] | None = None) -> list[dict]:
    scheduled_ids = [SCENARIO_ID] if expected_ids is None else expected_ids
    verification = {
        "scenario_id": SCENARIO_ID,
        "verification_status": "failed",
        "env_resolved": False,
        "checks": [
            {"name": "deployment_ready", "passed": True, "required": True},
            {"name": "latency_below_threshold", "passed": False, "required": True},
        ],
    }
    return [
        {
            "event": "run_started",
            "evaluation_mode": "EMPIRICAL",
            "split": "val",
            "split_sha256": ordered_scenario_ids_sha256(scheduled_ids),
            "provenance": {"base_model": {"id": "synthetic-model"}},
            "evaluator_source": {"git_sha": "b" * 40, "git_dirty": False},
        },
        {
            "event": "episode_started",
            "evaluation_mode": "EMPIRICAL",
            "scenario_id": SCENARIO_ID,
        },
        {
            "event": "pre_action_verification",
            "scenario_id": SCENARIO_ID,
            "verification": verification,
        },
        {
            "event": "step_result",
            "scenario_id": SCENARIO_ID,
            "record": {
                "parsed_action": {"tool": "synthetic_noop", "arguments": {}},
                "executed_action": None,
                "environment_result": {
                    "status": "blocked",
                    "executed_actions": [],
                },
                "agent_claimed_resolved": True,
                "verification": verification,
                "step_finished_at": "2026-09-29T12:00:02Z",
            },
        },
        {
            "event": "episode_failed",
            "scenario_id": SCENARIO_ID,
            "result": {
                "scenario_id": SCENARIO_ID,
                "status": "failed",
                "scorable": False,
                "failure": "blocked_policy_action",
            },
        },
        {
            "event": "run_completed",
            "summary": {
                "run_id": "synthetic-run",
                "model": "synthetic-model",
                "split": "val",
                "split_sha256": ordered_scenario_ids_sha256(scheduled_ids),
                "scenario_count": len(scheduled_ids),
                "completed_episodes": 1,
                "scorable_episodes": 0,
                "unscorable_episodes": 1,
                "failed_episodes": 1,
                "empirical_claim_allowed": False,
            },
        },
    ]


def test_g9_adapter_keeps_action_and_verifier_but_never_invents_alert_clock():
    events = _g9_events()

    adapted = _adapt(events, "SFT + GRPO")

    assert adapted["run_outcome"] == "completed"
    assert adapted["raw_records"] == events
    assert adapted["missing_scenario_ids"] == []
    episode = adapted["episodes"][0]
    assert [event["event"] for event in episode["events"]] == [
        "pre_action_verification",
        "action_result",
        "post_action_verification",
        "episode_failed",
    ]
    assert adapted["source_identity"] == SOURCE_IDENTITY
    assert adapted["source_identity_status"] == "RAW_BOUND"
    assert adapted["source_identity_declaration"] == SOURCE_IDENTITY
    assert episode["events"][1]["executed"] is False
    assert episode["events"][1]["agent_claimed_resolved"] is True
    assert episode["source_identity"] == SOURCE_IDENTITY
    assert episode["source_identity_status"] == "RAW_BOUND"
    assert episode["source_identity_declaration"] == SOURCE_IDENTITY
    assert episode["events"][2]["verification"]["checks"] == [
        {"check_id": "deployment_ready", "passed": True, "required": True},
        {"check_id": "latency_below_threshold", "passed": False, "required": True},
    ]
    assert not any(event["event"] == "alert_delivery" for event in episode["events"])
    assert not any(
        "recorded_at" in event
        for event in episode["events"]
        if event["event"] == "post_action_verification"
    )


def test_g9_event_timestamps_remain_source_metadata_without_admission_events():
    events = _g9_events()
    pre_action = next(event for event in events if event["event"] == "pre_action_verification")
    step_result = next(event for event in events if event["event"] == "step_result")
    pre_action["recorded_at"] = "2026-09-29T12:00:01Z"
    step_result["recorded_at"] = "2026-09-29T12:00:02Z"
    events[1]["state"] = {"alert": {"severity": "P1"}}
    events[1]["live"] = True
    step_result["remediation_approval"] = {"approved": True}
    pre_action_line = events.index(pre_action) + 1
    step_result_line = events.index(step_result) + 1

    adapted = _adapt(events, "SFT + GRPO")

    episode = adapted["episodes"][0]
    pre_action_event = next(
        event for event in episode["events"] if event["event"] == "pre_action_verification"
    )
    action_result = next(event for event in episode["events"] if event["event"] == "action_result")
    post_action = next(
        event for event in episode["events"] if event["event"] == "post_action_verification"
    )
    assert pre_action_event["source_event_recorded_at"] == "2026-09-29T12:00:01Z"
    assert pre_action_event["raw_ref"] == {
        "source_sha256": adapted["source_sha256"],
        "line": pre_action_line,
    }
    for mapped_event in (action_result, post_action):
        assert mapped_event["source_event_recorded_at"] == "2026-09-29T12:00:02Z"
        assert mapped_event["raw_ref"] == {
            "source_sha256": adapted["source_sha256"],
            "line": step_result_line,
        }
    assert all(
        "recorded_at" not in event and "clock_source" not in event
        for event in episode["events"]
    )
    assert not any(
        event["event"]
        in {
            "fault_authorization",
            "fault_observation",
            "alert_delivery",
            "cleanup_started",
            "cleanup_completed",
        }
        for event in episode["events"]
    )
    assert adapted["evaluation_mode"] == "NON_EMPIRICAL_OBSERVATION"
    assert adapted["non_empirical"] is True
    assert adapted["empirical_claim_allowed"] is False
    assert adapted["certification_status"] == "NOT_CERTIFIED"


def test_g9_missing_source_event_timestamps_stay_absent():
    adapted = _adapt(_g9_events(), "SFT + GRPO")

    assert all(
        "source_event_recorded_at" not in event
        and "recorded_at" not in event
        and "clock_source" not in event
        for event in adapted["episodes"][0]["events"]
    )


@pytest.mark.parametrize(
    "recorded_at",
    [None, 17, "", "not-a-timestamp", "2026-09-29T12:00:02"],
)
@pytest.mark.parametrize("event_name", ["pre_action_verification", "step_result"])
def test_g9_malformed_source_event_timestamp_fails_closed(recorded_at, event_name):
    events = _g9_events()
    next(event for event in events if event["event"] == event_name)["recorded_at"] = recorded_at

    with pytest.raises(ValueError, match="recorded_at"):
        _adapt(events, "SFT + GRPO")


def test_g9_missing_executed_action_stays_unknown_and_retains_its_raw_reference():
    events = _g9_events()
    step_event = next(event for event in events if event["event"] == "step_result")
    del step_event["record"]["executed_action"]
    step_line = events.index(step_event) + 1

    adapted = _adapt(events, "SFT + GRPO")

    episode = adapted["episodes"][0]
    assert "action_result" not in [event["event"] for event in episode["events"]]
    assert {
        "source_sha256": adapted["source_sha256"],
        "line": step_line,
    } in episode["raw_refs"]
    assert adapted["raw_records"] == events


def test_g9_null_executed_action_without_emitter_list_stays_unknown():
    events = _g9_events()
    step_event = next(event for event in events if event["event"] == "step_result")
    del step_event["record"]["environment_result"]

    adapted = _adapt(events, "SFT + GRPO")

    assert "action_result" not in [event["event"] for event in adapted["episodes"][0]["events"]]


def test_g9_valid_emitted_action_is_retained_as_executed():
    events = _g9_events()
    step_event = next(event for event in events if event["event"] == "step_result")
    action = {
        "tool": "synthetic_noop",
        "arguments": {},
        "result": {"stdout": "synthetic result"},
    }
    step_event["record"]["executed_action"] = action
    step_event["record"]["environment_result"]["status"] = "ok"
    step_event["record"]["environment_result"]["executed_actions"] = [action]

    adapted = _adapt(events, "SFT + GRPO")

    action_result = next(
        event for event in adapted["episodes"][0]["events"] if event["event"] == "action_result"
    )
    assert action_result["executed"] is True


@pytest.mark.parametrize(
    "executed_action",
    [
        [],
        "synthetic_noop",
        {},
        {"tool": "synthetic_noop", "arguments": []},
    ],
)
def test_g9_malformed_non_null_executed_action_fails_closed(executed_action):
    events = _g9_events()
    step_event = next(event for event in events if event["event"] == "step_result")
    step_event["record"]["executed_action"] = executed_action

    with pytest.raises(ValueError, match="executed_action"):
        _adapt(events, "SFT + GRPO")


def test_g9_run_completed_with_missing_member_is_explicitly_partial():
    scheduled_ids = [SCENARIO_ID, "synthetic/sf-002"]
    events = _g9_events(scheduled_ids)

    adapted = _adapt(events, "SFT + GRPO", scheduled_ids)

    assert adapted["run_outcome"] == "partial"
    assert adapted["scheduled_scenario_ids"] == scheduled_ids
    assert adapted["observed_scenario_ids"] == [SCENARIO_ID]
    assert adapted["missing_scenario_ids"] == ["synthetic/sf-002"]
    assert adapted["empirical_claim_allowed"] is False
    assert adapted["non_empirical"] is True
    assert adapted["source_identity"] == SOURCE_IDENTITY
    assert adapted["source_identity_status"] == "RAW_BOUND"


def test_g9_run_completed_rejects_summary_count_claiming_missing_member():
    scheduled_ids = [SCENARIO_ID, "synthetic/sf-002"]
    events = _g9_events(scheduled_ids)
    events[-1]["summary"]["completed_episodes"] = len(scheduled_ids)

    with pytest.raises(ValueError, match="completed_episodes"):
        _adapt(events, "SFT + GRPO", scheduled_ids)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario_count", 2),
        ("completed_episodes", 0),
        ("scorable_episodes", 1),
        ("unscorable_episodes", 0),
        ("failed_episodes", 0),
        ("split", "train"),
        ("split_sha256", "0" * 64),
    ],
)
def test_g9_run_completed_rejects_contradictory_summary_metadata(field, value):
    events = _g9_events()
    events[-1]["summary"][field] = value

    with pytest.raises(ValueError, match="G9 run_completed summary"):
        _adapt(events, "SFT + GRPO")


def test_g9_run_completed_rejects_positive_claim_with_missing_member():
    scheduled_ids = [SCENARIO_ID, "synthetic/sf-002"]
    events = _g9_events(scheduled_ids)
    terminal = events[-2]
    terminal["event"] = "episode_completed"
    terminal["result"].update({"status": "ok", "scorable": True, "failure": None})
    summary = events[-1]["summary"]
    summary.update(
        {
            "scorable_episodes": 1,
            "unscorable_episodes": 0,
            "failed_episodes": 0,
            "empirical_claim_allowed": True,
        }
    )

    with pytest.raises(ValueError, match="missing scheduled episodes"):
        _adapt(events, "SFT + GRPO", scheduled_ids)


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("run_started", "run_id"),
        ("run_started", "model"),
        ("summary", "run_id"),
        ("summary", "model"),
    ],
)
def test_g9_source_identity_mismatches_fail_closed(location, field):
    events = _g9_events()
    if location == "run_started" and field == "run_id":
        events[0]["run_id"] = "other-run"
    elif location == "run_started":
        events[0]["provenance"]["base_model"]["id"] = "other-model"
    else:
        events[-1]["summary"][field] = "other-run" if field == "run_id" else "other-model"

    with pytest.raises(ValueError, match="identity mismatch"):
        _adapt(events, "SFT + GRPO")


@pytest.mark.parametrize("field", ["run_id", "model"])
def test_g9_completed_summary_requires_run_identity_fields(field):
    events = _g9_events()
    del events[-1]["summary"][field]

    with pytest.raises(ValueError, match="run_completed summary requires"):
        _adapt(events, "SFT + GRPO")


def test_g9_completed_summary_binds_identity_without_copying_its_declaration():
    unknown_identity = {"run_id": None, "model": None}

    adapted = _adapt(
        _g9_events(),
        "SFT + GRPO",
        source_identity=unknown_identity,
    )

    assert adapted["source_identity"] == SOURCE_IDENTITY
    assert adapted["source_identity_status"] == "RAW_BOUND"
    assert adapted["source_identity_declaration"] == unknown_identity


@pytest.mark.parametrize("terminal", ["run_interrupted", None])
def test_g9_incomplete_run_does_not_promote_arbitrary_identity_declaration(terminal):
    events = _g9_events()[:-2]
    if terminal is not None:
        events.append(
            {
                "event": terminal,
                "scenario_id": SCENARIO_ID,
                "completed_episodes": 0,
            }
        )
    declaration = {
        "run_id": "caller-asserted-run",
        "model": "synthetic-model",
    }

    adapted = _adapt(
        events,
        "SFT + GRPO",
        source_identity=declaration,
    )

    assert adapted["run_outcome"] == ("interrupted" if terminal is not None else "partial")
    assert adapted["source_identity"] == {"run_id": None, "model": None}
    assert adapted["source_identity_status"] == "UNBOUND"
    assert adapted["source_identity_declaration"] == declaration
    assert adapted["episodes"][0]["source_identity"] == {
        "run_id": None,
        "model": None,
    }
    assert adapted["episodes"][0]["source_identity_status"] == "UNBOUND"
    assert adapted["episodes"][0]["source_identity_declaration"] == declaration


def test_g9_raw_started_and_completed_model_contradiction_fails_without_declaration():
    events = _g9_events()
    events[0]["provenance"]["base_model"]["id"] = "start-model"

    with pytest.raises(ValueError, match="identity mismatch"):
        _adapt(
            events,
            "SFT + GRPO",
            source_identity={"run_id": None, "model": None},
        )


def test_g9_episode_result_identity_conflict_fails_without_declaration():
    events = _g9_events()
    events[-2]["result"]["provenance"] = {"base_model": {"id": "episode-model"}}

    with pytest.raises(ValueError, match="identity mismatch"):
        _adapt(
            events,
            "SFT + GRPO",
            source_identity={"run_id": None, "model": None},
        )


def test_adapter_rejects_wrong_order_or_mock_source_without_dropping_rows():
    rows = [
        {"scenario_id": "synthetic/sf-002", "evaluation_mode": "empirical"},
        {"scenario_id": SCENARIO_ID, "evaluation_mode": "empirical"},
    ]
    with pytest.raises(ValueError, match="membership"):
        _adapt(rows, "SFT Model", [SCENARIO_ID, "synthetic/sf-002"])

    mock = [{"scenario_id": SCENARIO_ID, "evaluation_mode": "mock"}]
    with pytest.raises(ValueError, match="non-empirical marker"):
        _adapt(mock, "Zero-Shot Baseline")

    events = _g9_events()
    events[0]["split_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="split_sha256"):
        _adapt(events, "SFT + GRPO")


def test_adapter_reports_missing_scheduled_row_without_inventing_an_episode():
    row = _diagnosis_row("Zero-Shot Baseline", status="error")

    adapted = _adapt(
        [row],
        "Zero-Shot Baseline",
        [SCENARIO_ID, "synthetic/sf-002"],
    )

    assert adapted["run_outcome"] == "partial"
    assert adapted["observed_scenario_ids"] == [SCENARIO_ID]
    assert adapted["missing_scenario_ids"] == ["synthetic/sf-002"]
    assert len(adapted["episodes"]) == 1
    assert adapted["episodes"][0]["events"][0]["event"] == "model_failure"


def test_adapter_rejects_duplicate_json_fields_and_wrong_source_hash():
    raw = b'{"scenario_id":"synthetic/sf-001","scenario_id":"synthetic/sf-001"}\n'
    arguments = {
        "variant": "SFT Model",
        "partition": "val",
        "expected_scenario_ids": [SCENARIO_ID],
        "source_identity": SOURCE_IDENTITY,
    }
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        adapt_three_arm_raw(raw, expected_sha256="0" * 64, **arguments)
    with pytest.raises(ValueError, match="duplicate field"):
        adapt_three_arm_raw(
            raw,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            **arguments,
        )
