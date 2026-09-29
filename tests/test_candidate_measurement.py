"""Synthetic contract tests for the non-empirical G13 episode scorer."""

from __future__ import annotations

from copy import deepcopy

import pytest

from bench.candidate_measurement import measure_candidate_episode


SCENARIO_ID = "adv-test-only-001"
REQUIRED_CHECK_IDS = ["deployment_ready", "latency_below_threshold"]


def _verification(
    status: str,
    env_resolved: bool,
    checks: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "verification_status": status,
        "env_resolved": env_resolved,
        "checks": checks,
    }


def _contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract_version": "candidate-v1-test-only",
        "required_verifier_check_ids": REQUIRED_CHECK_IDS.copy(),
        "expected_diagnosis_by_scenario": {SCENARIO_ID: "network_partition"},
        "diagnosis_label_mapping": {
            "network partition": "network_partition",
            "packet loss": "network_partition",
            "database saturation": "database_saturation",
        },
        "clock_source": "recorded_at",
    }


def _episode(events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "scenario_id": SCENARIO_ID,
        "source_identity": {"run_id": "synthetic-run", "model": "synthetic-model"},
        "source_sha256": "a" * 64,
        "raw_refs": [{"artifact": "synthetic.jsonl", "line": 1}],
        "events": events,
    }


def _admission_events() -> list[dict[str, object]]:
    return [
        {"event": "fault_authorization", "authorized": True},
        {"event": "fault_observation", "observed": True},
        {
            "event": "alert_delivery",
            "delivered": True,
            "recorded_at": "2026-09-29T12:00:00Z",
            "raw_ref": {"artifact": "synthetic.jsonl", "line": 2},
        },
        {
            "event": "pre_action_verification",
            "verification": _verification(
                "failed",
                False,
                [
                    {"check_id": REQUIRED_CHECK_IDS[0], "passed": False},
                    {"check_id": REQUIRED_CHECK_IDS[1], "passed": False},
                ],
            ),
        },
    ]


def test_scores_unresolved_one_of_two_checks_and_false_claim_as_minus_0125():
    events = [
        *_admission_events(),
        {
            "event": "action_result",
            "executed": False,
            "agent_claimed_resolved": True,
        },
        {
            "event": "post_action_verification",
            "verification": _verification(
                "failed",
                False,
                [
                    {"check_id": REQUIRED_CHECK_IDS[0], "passed": True},
                    {"check_id": REQUIRED_CHECK_IDS[1], "passed": False},
                ],
            ),
        },
    ]
    raw_episode = _episode(events)
    before = deepcopy(raw_episode)

    measured = measure_candidate_episode(raw_episode, _contract())

    assert measured["eligibility"]["status"] == "eligible"
    assert measured["objective_score"]["value"] == -0.125
    assert measured["objective_score"]["components"] == {
        "verified_recovery": 0.0,
        "required_check_coverage": 0.5,
        "false_resolution_claim": 1.0,
    }
    assert measured["empirical_claim_allowed"] is False
    assert measured["evaluation_mode"] == "NON_EMPIRICAL"
    assert measured["certification_status"] == "NOT_CERTIFIED"
    assert measured["raw_episode"] == before
    assert raw_episode == before


def test_computes_ttr_from_delivered_alert_to_post_action_success_before_cleanup():
    events = [
        {
            "event": "episode_started",
            "recorded_at": "2026-09-29T11:59:50Z",
        },
        *_admission_events(),
    ]
    events.extend(
        [
            {
                "event": "diagnosis_output",
                "label": "packet loss",
                "recorded_at": "2026-09-29T12:00:00.500Z",
            },
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:01Z",
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "passed",
                    True,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": True},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": True},
                    ],
                ),
                "recorded_at": "2026-09-29T12:00:03Z",
            },
            {
                "event": "cleanup_started",
                "recorded_at": "2026-09-29T12:00:04Z",
            },
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["diagnosis"] == {
        "correct": True,
        "score": 1.0,
        "expected_category": "network_partition",
        "mapped_category": "network_partition",
        "raw_label": "packet loss",
        "reason": None,
    }
    assert measured["objective_score"]["value"] == 1.0
    assert measured["time_to_recovery"] == {
        "seconds": 3.0,
        "status": "observed",
        "reason": None,
        "clock_source": "recorded_at",
    }


def test_cleanup_or_later_verifier_cannot_override_pre_cleanup_failure():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:01Z",
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "failed",
                    False,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": False},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": False},
                    ],
                ),
                "recorded_at": "2026-09-29T12:00:02Z",
            },
            {
                "event": "cleanup_started",
                "recorded_at": "2026-09-29T12:00:03Z",
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "passed",
                    True,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": True},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": True},
                    ],
                ),
                "recorded_at": "2026-09-29T12:00:04Z",
            },
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["objective_score"]["value"] == 0.0
    assert measured["objective_score"]["components"]["verified_recovery"] == 0.0
    assert measured["objective_score"]["verifier_event_index"] == 5
    assert measured["a1_resolution_candidate"]["value"] == 0
    assert measured["a1_resolution_candidate"]["reason"] == (
        "conclusive_verifier_no_recovery_before_cleanup"
    )
    assert measured["time_to_recovery"]["seconds"] is None
    assert measured["time_to_recovery"]["status"] == "censored"


def test_missing_or_inconclusive_admission_is_undetermined_not_excluded():
    events = _admission_events()
    events.pop(3)

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["eligibility"] == {
        "status": "undetermined",
        "eligible": None,
        "reasons": ["pre-action verifier is missing or inconclusive about the unresolved fault"],
    }
    assert measured["objective_score"]["value"] is None
    assert measured["objective_score"]["reason"] == "episode_admission_not_eligible"
    assert measured["a1_resolution_candidate"]["value"] is None
    assert measured["outcome_classification"] == "not_assessed"


def test_explicitly_unauthorized_fault_is_ineligible():
    events = _admission_events()
    events[0]["authorized"] = False

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["eligibility"]["status"] == "ineligible"
    assert measured["eligibility"]["eligible"] is False
    assert measured["eligibility"]["reasons"] == [
        "fault authorization was not observed as authorized"
    ]


def test_post_start_model_failure_and_no_action_remain_eligible_negative():
    events = _admission_events()
    events.extend(
        [
            {"event": "step_failure", "failure": "policy_generation_error: TimeoutError"},
            {
                "event": "action_result",
                "executed": False,
                "agent_claimed_resolved": False,
            },
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["eligibility"]["status"] == "eligible"
    assert measured["outcome_classification"] == "eligible_negative"
    assert measured["failures"][0]["record"]["failure"] == ("policy_generation_error: TimeoutError")
    assert measured["a1_resolution_candidate"]["value"] == 0
    assert measured["a1_resolution_candidate"]["reason"] == "eligible_model_failure"
    assert measured["objective_score"]["value"] is None
    assert measured["objective_score"]["reason"] == ("post_action_verifier_observation_missing")


def test_p1_rejection_is_preserved_as_eligible_negative_without_execution():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "approval_decision",
                "approval": {"decision": "rejected"},
                "recorded_at": "2026-09-29T12:00:00.500Z",
            },
            {
                "event": "action_result",
                "executed": False,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:01Z",
            },
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["outcome_classification"] == "eligible_negative"
    assert measured["a1_resolution_candidate"]["value"] == 0
    assert measured["a1_resolution_candidate"]["reason"] == "eligible_no_action_execution"
    assert measured["raw_episode"]["events"][4]["approval"]["decision"] == "rejected"


def test_infrastructure_interruption_requires_adjudication_without_auto_exclusion():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": False,
                "agent_claimed_resolved": False,
            },
            {
                "event": "infrastructure_failure",
                "failure": "synthetic telemetry disconnect",
                "classification": "INFRA_INVALID",
            },
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["eligibility"]["status"] == "eligible"
    assert measured["outcome_classification"] == ("eligible_pending_independent_adjudication")
    assert measured["adjudication"]["status"] == "pending_independent_adjudication"
    assert measured["adjudication"]["required"] is True
    assert measured["objective_score"]["value"] is None
    assert measured["objective_score"]["reason"] == "pending_independent_adjudication"
    assert measured["a1_resolution_candidate"]["value"] is None
    assert measured["raw_episode"]["events"][-1]["classification"] == "INFRA_INVALID"


def test_infrastructure_interruption_suppresses_conclusive_score_and_ttr():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:01Z",
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "passed",
                    True,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": True},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": True},
                    ],
                ),
                "recorded_at": "2026-09-29T12:00:03Z",
            },
            {
                "event": "cleanup_started",
                "recorded_at": "2026-09-29T12:00:04Z",
            },
            {
                "event": "infrastructure_failure",
                "failure": "synthetic post-verification disconnect",
            },
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["eligibility"]["status"] == "eligible"
    assert measured["outcome_classification"] == ("eligible_pending_independent_adjudication")
    assert measured["objective_score"]["value"] is None
    assert measured["objective_score"]["reason"] == "pending_independent_adjudication"
    assert measured["a1_resolution_candidate"]["value"] is None
    assert measured["time_to_recovery"]["seconds"] is None
    assert measured["time_to_recovery"]["reason"] == ("pending_independent_adjudication")


def test_episode_unscorable_is_retained_as_an_explicit_null_score_reason():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "passed",
                    True,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": True},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": True},
                    ],
                ),
            },
            {"event": "cleanup_started"},
            {"event": "episode_unscorable", "failure": "missing final state"},
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["objective_score"]["value"] is None
    assert measured["objective_score"]["reason"] == ("episode_unscorable: missing final state")
    assert measured["a1_resolution_candidate"]["value"] is None
    assert measured["a1_resolution_candidate"]["reason"] == (
        "episode_unscorable_pending_independent_adjudication"
    )
    assert measured["outcome_classification"] == ("eligible_pending_independent_adjudication")
    assert measured["adjudication"]["required"] is True
    assert measured["failures"][-1]["event"] == "episode_unscorable"


def test_generic_episode_failure_is_retained_pending_adjudication():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": False,
                "agent_claimed_resolved": False,
            },
            {"event": "episode_failed", "failure": "RuntimeError"},
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["eligibility"]["status"] == "eligible"
    assert measured["outcome_classification"] == ("eligible_pending_independent_adjudication")
    assert measured["adjudication"]["required"] is True
    assert measured["failures"][-1]["event"] == "episode_failed"


def test_model_failure_with_conclusive_failed_verifier_remains_negative():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "failed",
                    False,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": False},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": False},
                    ],
                ),
            },
            {"event": "cleanup_started"},
            {"event": "step_failure", "failure": "policy_generation_error"},
            {"event": "episode_failed", "failure": "RuntimeError"},
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["eligibility"]["status"] == "eligible"
    assert measured["objective_score"]["value"] == 0.0
    assert measured["a1_resolution_candidate"]["value"] == 0
    assert measured["outcome_classification"] == "eligible_negative"
    assert measured["adjudication"]["status"] == "not_indicated"


def test_first_success_remains_a1_recovery_after_later_action_and_failed_check():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:01Z",
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "passed",
                    True,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": True},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": True},
                    ],
                ),
                "recorded_at": "2026-09-29T12:00:02Z",
            },
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:03Z",
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "failed",
                    False,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": False},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": False},
                    ],
                ),
                "recorded_at": "2026-09-29T12:00:04Z",
            },
            {
                "event": "cleanup_started",
                "recorded_at": "2026-09-29T12:00:05Z",
            },
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["a1_resolution_candidate"]["value"] == 1
    assert measured["a1_resolution_candidate"]["reason"] == (
        "first_conclusive_post_action_success_before_cleanup"
    )
    assert measured["a1_resolution_candidate"]["first_verifier_endpoint"] == {
        "event_index": 5,
        "verification_status": "passed",
        "env_resolved": True,
        "recorded_at": "2026-09-29T12:00:02Z",
        "raw_ref": None,
    }
    assert measured["objective_score"]["value"] == 0.0
    assert measured["time_to_recovery"]["seconds"] == 2.0
    assert measured["outcome_classification"] == "eligible_recovered"


def test_a1_is_null_for_inconclusive_verifier_after_executed_action():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "inconclusive",
                    False,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": False},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": False},
                    ],
                ),
            },
            {"event": "cleanup_started"},
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["a1_resolution_candidate"]["value"] is None
    assert measured["a1_resolution_candidate"]["reason"] == (
        "post_action_verifier_missing_or_inconclusive"
    )
    assert measured["outcome_classification"] == "eligible_outcome_unscorable"


def test_later_unexecuted_action_does_not_invent_a1_negative():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:01Z",
            },
            {
                "event": "action_result",
                "executed": False,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:02Z",
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "inconclusive",
                    False,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": False},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": False},
                    ],
                ),
                "recorded_at": "2026-09-29T12:00:03Z",
            },
            {"event": "cleanup_started", "recorded_at": "2026-09-29T12:00:04Z"},
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["a1_resolution_candidate"]["value"] is None
    assert measured["a1_resolution_candidate"]["reason"] == (
        "post_action_verifier_missing_or_inconclusive"
    )


def test_a1_is_null_when_post_action_verifier_is_missing():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
            },
            {"event": "cleanup_started"},
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["a1_resolution_candidate"]["value"] is None
    assert measured["a1_resolution_candidate"]["reason"] == (
        "post_action_verifier_missing_or_inconclusive"
    )


def test_inconclusive_post_action_verifier_keeps_score_null_with_reason():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:01Z",
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "inconclusive",
                    False,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": False},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": False},
                    ],
                ),
                "recorded_at": "2026-09-29T12:00:02Z",
            },
            {"event": "cleanup_started", "recorded_at": "2026-09-29T12:00:03Z"},
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["objective_score"]["value"] is None
    assert measured["objective_score"]["reason"] == "objective_verifier_inconclusive"
    assert measured["a1_resolution_candidate"]["value"] is None
    assert measured["a1_resolution_candidate"]["reason"] == (
        "post_action_verifier_missing_or_inconclusive"
    )
    assert measured["time_to_recovery"]["seconds"] is None
    assert measured["time_to_recovery"]["status"] == "censored"


def test_missing_and_invalid_diagnosis_outputs_count_as_misses():
    for output in (
        {"event": "diagnosis_output", "label": None},
        {"event": "diagnosis_output", "label": {"root_cause": "network"}},
        {"event": "diagnosis_output", "label": "unmapped phrase"},
    ):
        events = [*_admission_events(), output]

        measured = measure_candidate_episode(_episode(events), _contract())

        assert measured["diagnosis"]["correct"] is False
        assert measured["diagnosis"]["score"] == 0.0


def test_missing_diagnosis_after_eligible_model_failure_remains_a_negative():
    events = [
        *_admission_events(),
        {"event": "model_failure", "reason": "synthetic generation error"},
    ]

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["eligibility"]["status"] == "eligible"
    assert measured["diagnosis"]["correct"] is False
    assert measured["diagnosis"]["score"] == 0.0
    assert measured["diagnosis"]["reason"] == "missing_or_invalid_diagnosis_output"


def test_g9_unavailable_diagnosis_marker_conflicting_with_output_is_rejected():
    raw_episode = _episode(
        [
            *_admission_events(),
            {"event": "diagnosis_output", "label": "packet loss"},
        ]
    )
    raw_episode["diagnosis_observation"] = {
        "status": "unavailable",
        "reason": "g9_diagnosis_not_observed",
        "source_format": "g9_event_stream",
        "source_sha256": raw_episode["source_sha256"],
        "raw_refs": deepcopy(raw_episode["raw_refs"]),
    }

    with pytest.raises(ValueError, match="diagnosis observation conflicts with diagnosis_output"):
        measure_candidate_episode(raw_episode, _contract())


def test_multiple_diagnosis_outputs_are_ambiguous_and_all_raw_labels_are_retained():
    events = [
        *_admission_events(),
        {"event": "diagnosis_output", "label": "database saturation"},
        {"event": "diagnosis_output", "label": "packet loss"},
    ]

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["diagnosis"]["correct"] is None
    assert measured["diagnosis"]["score"] is None
    assert measured["diagnosis"]["reason"] == "multiple_diagnosis_outputs_ambiguous"
    assert measured["diagnosis"]["candidate_raw_labels"] == [
        "database saturation",
        "packet loss",
    ]
    assert [
        event["label"]
        for event in measured["raw_episode"]["events"]
        if event["event"] == "diagnosis_output"
    ] == ["database saturation", "packet loss"]


def test_ttr_is_censored_when_no_post_action_success_occurs():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:01Z",
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "failed",
                    False,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": False},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": False},
                    ],
                ),
                "recorded_at": "2026-09-29T12:00:02Z",
            },
            {
                "event": "cleanup_started",
                "recorded_at": "2026-09-29T12:00:03Z",
            },
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["time_to_recovery"]["seconds"] is None
    assert measured["time_to_recovery"]["status"] == "censored"
    assert "no conclusive" in measured["time_to_recovery"]["reason"]


def test_ttr_is_unavailable_when_success_lacks_a_cleanup_boundary():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": True,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:01Z",
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "passed",
                    True,
                    [
                        {"check_id": REQUIRED_CHECK_IDS[0], "passed": True},
                        {"check_id": REQUIRED_CHECK_IDS[1], "passed": True},
                    ],
                ),
                "recorded_at": "2026-09-29T12:00:03Z",
            },
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["time_to_recovery"]["seconds"] is None
    assert measured["time_to_recovery"]["status"] == "unavailable"
    assert measured["time_to_recovery"]["reason"] == ("cleanup boundary observation is missing")


def test_missing_required_check_observation_keeps_score_null():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": False,
                "agent_claimed_resolved": False,
            },
            {
                "event": "post_action_verification",
                "verification": _verification(
                    "failed",
                    False,
                    [{"check_id": REQUIRED_CHECK_IDS[0], "passed": False}],
                ),
            },
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["objective_score"]["value"] is None
    assert measured["objective_score"]["reason"] == (
        "required_verifier_check_observation_missing: ['latency_below_threshold']"
    )
    assert measured["objective_score"]["components"]["verified_recovery"] == 0.0


def test_rejects_contradictory_verifier_and_out_of_order_timestamps():
    bad_verification = _verification(
        "failed",
        True,
        [
            {"check_id": REQUIRED_CHECK_IDS[0], "passed": False},
            {"check_id": REQUIRED_CHECK_IDS[1], "passed": False},
        ],
    )
    events = _admission_events()
    events[3]["verification"] = bad_verification

    with pytest.raises(ValueError, match="conflicts with env_resolved"):
        measure_candidate_episode(_episode(events), _contract())

    events = _admission_events()
    events.extend(
        [
            {
                "event": "diagnosis_output",
                "label": "packet loss",
                "recorded_at": "2026-09-29T12:00:02Z",
            },
            {
                "event": "action_result",
                "executed": False,
                "agent_claimed_resolved": False,
                "recorded_at": "2026-09-29T12:00:01Z",
            },
        ]
    )
    with pytest.raises(ValueError, match="out of order"):
        measure_candidate_episode(_episode(events), _contract())


def test_rejects_passed_verifier_with_failed_required_check():
    events = [
        *_admission_events(),
        {
            "event": "action_result",
            "executed": False,
            "agent_claimed_resolved": False,
        },
        {
            "event": "post_action_verification",
            "verification": _verification(
                "passed",
                True,
                [
                    {"check_id": REQUIRED_CHECK_IDS[0], "passed": False},
                    {"check_id": REQUIRED_CHECK_IDS[1], "passed": True},
                ],
            ),
        },
    ]

    with pytest.raises(ValueError, match="passed status conflicts"):
        measure_candidate_episode(_episode(events), _contract())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", True, "schema_version must be integer 1"),
        (
            "required_verifier_check_ids",
            ["duplicate", "duplicate"],
            "unique non-empty strings",
        ),
        ("clock_source", "recorded_at.timestamp", "name one event timestamp field"),
    ],
)
def test_rejects_malformed_candidate_contract(field, value, message):
    contract = _contract()
    contract[field] = value

    with pytest.raises(ValueError, match=message):
        measure_candidate_episode(_episode(_admission_events()), contract)


def test_rejects_duplicate_or_contradictory_admission_observations():
    events = _admission_events()
    events.append({"event": "fault_authorization", "authorized": False})

    with pytest.raises(ValueError, match="contradictory fault_authorization"):
        measure_candidate_episode(_episode(events), _contract())

    events = _admission_events()
    events.append(deepcopy(events[3]))

    with pytest.raises(ValueError, match="duplicate pre-action verifications"):
        measure_candidate_episode(_episode(events), _contract())


def test_rejects_action_or_alert_delivery_after_pre_action_verification():
    events = _admission_events()
    events.insert(3, {"event": "policy_output"})

    with pytest.raises(ValueError, match="precedes failed pre-action verification"):
        measure_candidate_episode(_episode(events), _contract())

    events = _admission_events()
    events[2], events[3] = events[3], events[2]

    with pytest.raises(ValueError, match="delivered alert must precede"):
        measure_candidate_episode(_episode(events), _contract())


def test_self_declared_infra_invalid_label_has_no_exclusion_authority():
    events = _admission_events()
    events.extend(
        [
            {
                "event": "action_result",
                "executed": False,
                "agent_claimed_resolved": False,
            },
            {"event": "episode_completed", "classification": "INFRA_INVALID"},
        ]
    )

    measured = measure_candidate_episode(_episode(events), _contract())

    assert measured["eligibility"]["status"] == "eligible"
    assert measured["outcome_classification"] == "eligible_negative"
    assert measured["adjudication"]["status"] == "not_indicated"
    assert measured["raw_episode"]["events"][-1]["classification"] == "INFRA_INVALID"


def test_rejects_naive_or_non_string_clock_values():
    for clock_value in (
        "2026-09-29T12:00:00",
        "2026-09-29 12:00:00Z",
        float("nan"),
    ):
        events = _admission_events()
        events[2]["recorded_at"] = clock_value
        with pytest.raises(ValueError, match="timezone|RFC 3339"):
            measure_candidate_episode(_episode(events), _contract())
