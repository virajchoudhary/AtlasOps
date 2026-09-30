from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from bench import candidate_adapters, candidate_lineage, candidate_measurement, episode_membership
from bench.candidate_replay import replay_candidate_comparison
from bench.episode_membership import ordered_scenario_ids_sha256

ARM_ORDER = [
    "Zero-Shot Baseline",
    "SFT Model",
    "SFT + GRPO",
]
SCENARIO_ID = "synthetic/replay-001"
REQUIRED_CHECK_IDS = ["deployment_ready", "latency_below_threshold"]
_DEFAULT_G9_EVALUATOR_SOURCE = object()
_OMIT_G9_EVALUATOR_SOURCE = object()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _measurement_contract(scenario_ids: list[str] | None = None) -> dict:
    ids = [SCENARIO_ID] if scenario_ids is None else scenario_ids
    return {
        "schema_version": 1,
        "contract_version": "prospective-replay-test-v1",
        "required_verifier_check_ids": list(REQUIRED_CHECK_IDS),
        "expected_diagnosis_by_scenario": {scenario_id: "network_partition" for scenario_id in ids},
        "diagnosis_label_mapping": {
            "packet loss": "network_partition",
            "database saturation": "database_saturation",
        },
        "clock_source": "recorded_at",
    }


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return _sha256(payload.encode("utf-8"))


def _scorer_sha256() -> str:
    module_paths = {
        "bench/candidate_adapters.py": Path(candidate_adapters.__file__),
        "bench/candidate_lineage.py": Path(candidate_lineage.__file__),
        "bench/candidate_measurement.py": Path(candidate_measurement.__file__),
        "bench/episode_membership.py": Path(episode_membership.__file__),
        "bench/candidate_replay.py": (
            Path(__file__).resolve().parent.parent / "bench" / "candidate_replay.py"
        ),
    }
    manifest = [
        {"path": path, "sha256": _sha256(source_path.read_bytes())}
        for path, source_path in sorted(module_paths.items())
    ]
    return _canonical_sha256(manifest)


def _source_pins() -> dict[str, dict]:
    return {
        arm: {
            "source_identity": {
                "run_id": f"synthetic-run-{index}",
                "model": f"synthetic-model-{index}",
            },
            "source_sha256": str(index + 1) * 64,
        }
        for index, arm in enumerate(ARM_ORDER)
    }


def _candidate_inputs(
    source_pins: dict[str, dict] | None = None,
    scenario_ids: list[str] | None = None,
) -> tuple[dict, list[dict], dict, str, str]:
    scorer_sha256 = _scorer_sha256()
    ids = [SCENARIO_ID] if scenario_ids is None else scenario_ids
    contract = _measurement_contract(ids)
    contract_sha256 = _canonical_sha256(contract)
    pins = _source_pins() if source_pins is None else source_pins
    evaluation = {
        "permissions": {"read": True, "mutate": False},
        "inference": {"temperature": 0.0, "seed": 37},
        "tool_budget": {"calls": 4},
        "action_budget": {"actions": 3},
        "observation_budget": {"bytes": 8192},
        "time_budget": {"seconds": 240},
    }
    candidate_schema = {
        "arms": list(ARM_ORDER),
        "scenario_ids": list(ids),
        "sft_v2_adapter_tree_sha256": "2" * 64,
        "comparison_scorer": {
            "version": "prospective-replay-test-v1",
            "sha256": scorer_sha256,
        },
        "measurement_contract_sha256": contract_sha256,
        "evaluation_contract": evaluation,
    }
    base_model = {"revision": "a" * 40, "sha256": "b" * 64}
    tokenizer = {"revision": "c" * 40, "sha256": "d" * 64}
    source = {"commit_sha": "e" * 40, "tree_sha256": "f" * 64, "dirty": False}
    runs = []
    for index, arm in enumerate(ARM_ORDER):
        run = {
            "arm": arm,
            "base_model": dict(base_model),
            "tokenizer": dict(tokenizer),
            "source": dict(source),
            "evaluator": {
                "commit_sha": str(index + 1) * 40,
                "tree_sha256": str(index + 3) * 64,
                "dirty": False,
            },
            "comparison_scorer": {
                "version": candidate_schema["comparison_scorer"]["version"],
                "sha256": scorer_sha256,
            },
            "measurement_contract_sha256": contract_sha256,
            "partition": "validation",
            "raw_source": deepcopy(pins[arm]),
            "scenario_ids": list(ids),
            "split_sha256": _canonical_sha256(ids),
            "evaluation": evaluation,
            "serving_identity": None,
            "training_budget": None if index == 0 else {"steps": 10 + index},
            "adapter": (
                None
                if index == 0
                else {"tree_sha256": "2" * 64}
                if index == 1
                else {
                    "tree_sha256": "5" * 64,
                    "parent_sft_tree_sha256": "2" * 64,
                }
            ),
        }
        runs.append(run)
    return candidate_schema, runs, pins, scorer_sha256, contract_sha256


def _verification(
    status: str,
    env_resolved: bool,
    *,
    first_check: bool,
    second_check: bool,
) -> dict:
    return {
        "verification_status": status,
        "env_resolved": env_resolved,
        "checks": [
            {"check_id": REQUIRED_CHECK_IDS[0], "passed": first_check},
            {"check_id": REQUIRED_CHECK_IDS[1], "passed": second_check},
        ],
    }


def _worked_episode(
    arm: str,
    pin: dict,
    scenario_id: str = SCENARIO_ID,
) -> dict:
    return {
        "schema_version": 1,
        "scenario_id": scenario_id,
        "source_identity": {"run_id": None, "model": None},
        "source_identity_declaration": dict(pin["source_identity"]),
        "source_identity_status": "UNBOUND",
        "source_sha256": pin["source_sha256"],
        "raw_refs": [{"artifact": f"{arm}.jsonl", "line": 1}],
        "events": [
            {"event": "fault_authorization", "authorized": True},
            {"event": "fault_observation", "observed": True},
            {
                "event": "alert_delivery",
                "delivered": True,
                "recorded_at": "2026-09-29T12:00:00Z",
            },
            {
                "event": "pre_action_verification",
                "verification": _verification(
                    "failed",
                    False,
                    first_check=False,
                    second_check=False,
                ),
            },
            {
                "event": "diagnosis_output",
                "label": "packet loss",
                "recorded_at": "2026-09-29T12:00:00.250Z",
            },
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
                    first_check=True,
                    second_check=False,
                ),
            },
        ],
    }


def _common_rows(rows: list[tuple[str, dict]]) -> bytes:
    return "".join(
        json.dumps(
            {"arm": arm, "episode": episode},
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
        for arm, episode in rows
    ).encode("utf-8")


def _replay_common(
    rows: bytes,
    *,
    declared_summaries: dict | None = None,
    expected_sha256: str | None = None,
    scenario_ids: list[str] | None = None,
) -> dict:
    candidate_schema, runs, pins, scorer_sha256, contract_sha256 = _candidate_inputs(
        scenario_ids=scenario_ids
    )
    return replay_candidate_comparison(
        runs,
        candidate_schema,
        _measurement_contract(scenario_ids),
        source_pins=pins,
        comparison_scorer_sha256=scorer_sha256,
        measurement_contract_sha256=contract_sha256,
        common_episode_rows=rows,
        expected_common_episode_rows_sha256=(
            _sha256(rows) if expected_sha256 is None else expected_sha256
        ),
        partition="validation",
        declared_summaries=declared_summaries,
    )


def _recovered_episode(
    arm: str,
    pin: dict,
    scenario_id: str = SCENARIO_ID,
) -> dict:
    episode = deepcopy(_worked_episode(arm, pin, scenario_id))
    episode["events"][5] = {
        "event": "action_result",
        "executed": True,
        "agent_claimed_resolved": False,
        "recorded_at": "2026-09-29T12:00:01Z",
    }
    episode["events"][6] = {
        "event": "post_action_verification",
        "verification": _verification(
            "passed",
            True,
            first_check=True,
            second_check=True,
        ),
        "recorded_at": "2026-09-29T12:00:03Z",
    }
    episode["events"].append(
        {
            "event": "cleanup_started",
            "recorded_at": "2026-09-29T12:00:04Z",
        }
    )
    return episode


def _jsonl(records: list[dict]) -> bytes:
    return "".join(
        json.dumps(record, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
        for record in records
    ).encode("utf-8")


def _native_diagnosis_row(source_identity: dict | None = None) -> dict:
    prediction = {
        "severity": "P1",
        "root_cause": "packet loss",
        "affected_services": ["checkout"],
        "confidence": 0.5,
    }
    row = {
        "scenario_id": SCENARIO_ID,
        "evaluation_mode": "empirical",
        "status": "ok",
        "prediction": prediction,
        "raw_model_response": json.dumps(
            prediction,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
    }
    if source_identity is not None:
        row.update(source_identity)
    return row


def _g9_native_events(
    source_identity: dict,
    *,
    evaluator_source: object = _DEFAULT_G9_EVALUATOR_SOURCE,
) -> list[dict]:
    checks = [
        {"name": REQUIRED_CHECK_IDS[0], "passed": False, "required": True},
        {"name": REQUIRED_CHECK_IDS[1], "passed": False, "required": True},
    ]
    raw_evaluator_source = (
        {"git_sha": "3" * 40, "git_dirty": False}
        if evaluator_source is _DEFAULT_G9_EVALUATOR_SOURCE
        else deepcopy(evaluator_source)
    )
    provenance = {"base_model": {"id": source_identity["model"]}}
    run_started = {
        "event": "run_started",
        "evaluation_mode": "EMPIRICAL",
        "split": "validation",
        "split_sha256": ordered_scenario_ids_sha256([SCENARIO_ID]),
        "provenance": deepcopy(provenance),
    }
    if evaluator_source is not _OMIT_G9_EVALUATOR_SOURCE:
        run_started["evaluator_source"] = deepcopy(raw_evaluator_source)
    verification = {
        "scenario_id": SCENARIO_ID,
        "verification_status": "failed",
        "env_resolved": False,
        "checks": checks,
    }
    split_sha256 = ordered_scenario_ids_sha256([SCENARIO_ID])
    records = [
        run_started,
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
                "failure": "synthetic_model_failure",
            },
        },
        {
            "event": "run_completed",
            "summary": {
                "run_id": source_identity["run_id"],
                "model": source_identity["model"],
                "split": "validation",
                "split_sha256": split_sha256,
                "provenance": deepcopy(provenance),
                "scenario_count": 1,
                "completed_episodes": 1,
                "scorable_episodes": 0,
                "unscorable_episodes": 1,
                "failed_episodes": 1,
                "empirical_claim_allowed": False,
            },
        },
    ]
    if evaluator_source is not _OMIT_G9_EVALUATOR_SOURCE:
        records[-1]["summary"]["evaluator_source"] = deepcopy(raw_evaluator_source)
    return records


def _replay_native(sources: dict[str, bytes], pins: dict[str, dict]) -> dict:
    candidate_schema, runs, _, scorer_sha256, contract_sha256 = _candidate_inputs(pins)
    return replay_candidate_comparison(
        runs,
        candidate_schema,
        _measurement_contract(),
        source_pins=pins,
        comparison_scorer_sha256=scorer_sha256,
        measurement_contract_sha256=contract_sha256,
        native_sources=sources,
        partition="validation",
    )


def _native_g9_replay_inputs(
    evaluator_source: object = _DEFAULT_G9_EVALUATOR_SOURCE,
) -> tuple[dict[str, bytes], dict[str, dict], list[dict]]:
    declared = {
        arm: {
            "run_id": f"declared-run-{index}",
            "model": f"declared-model-{index}",
        }
        for index, arm in enumerate(ARM_ORDER)
    }
    g9_events = _g9_native_events(
        declared[ARM_ORDER[2]],
        evaluator_source=evaluator_source,
    )
    sources = {
        ARM_ORDER[0]: _jsonl([_native_diagnosis_row(declared[ARM_ORDER[0]])]),
        ARM_ORDER[1]: _jsonl([_native_diagnosis_row()]),
        ARM_ORDER[2]: _jsonl(g9_events),
    }
    pins = {
        arm: {
            "source_identity": declared[arm],
            "source_sha256": _sha256(sources[arm]),
        }
        for arm in ARM_ORDER
    }
    return sources, pins, g9_events


def test_replays_all_three_arms_and_reports_supported_minus_0125_decomposition():
    candidate_schema, _, pins, _, _ = _candidate_inputs()
    rows = _common_rows(
        [(arm, _worked_episode(arm, pins[arm])) for arm in candidate_schema["arms"]]
    )

    result = _replay_common(
        rows,
        declared_summaries={
            ARM_ORDER[0]: {"reward_mean": -0.125},
        },
    )

    assert result["arm_order"] == ARM_ORDER
    assert result["protocol_status"] == "PROPOSED_NOT_FROZEN"
    assert result["evaluation_mode"] == "NON_EMPIRICAL"
    assert result["non_empirical"] is True
    assert result["empirical"] is False
    assert result["empirical_claim_allowed"] is False
    assert result["certification_status"] == "NOT_CERTIFIED"
    assert result["partition_scope"] == {
        "repository_fetch_performed": False,
        "partition_fetch_performed": False,
        "caller_partition_label": "validation",
        "source_authorization": "NOT_ESTABLISHED_FROM_SUPPLIED_BYTES",
        "explicit_test_scope_markers": "REJECTED",
    }
    for arm in ARM_ORDER:
        episode = result["arms"][arm]["episodes"][0]
        assert episode["objective_score"]["value"] == -0.125
        assert episode["objective_score"]["components"] == {
            "verified_recovery": 0.0,
            "required_check_coverage": 0.5,
            "false_resolution_claim": 1.0,
        }
        assert result["arms"][arm]["summary"]["reward_mean"]["value"] == -0.125
        identity_binding = result["arms"][arm]["source"]["identity_binding"]
        assert identity_binding["status"] == "UNBOUND"
        assert identity_binding["row_reported_source_identity_statuses"] == ["UNBOUND"]
        assert episode["source_identity_provenance"]["status"] == "UNBOUND"
        assert (
            episode["source_identity_provenance"]["row_reported_source_identity_status"]
            == "UNBOUND"
        )
    assert (
        result["arms"][ARM_ORDER[0]]["declared_summary_comparison"]["reward_mean"]["status"]
        == "MATCH"
    )


def test_computes_resolution_diagnosis_and_ttr_only_with_complete_raw_evidence():
    _, _, pins, _, _ = _candidate_inputs()
    rows = _common_rows([(arm, _recovered_episode(arm, pins[arm])) for arm in ARM_ORDER])

    result = _replay_common(rows)

    for arm in ARM_ORDER:
        summary = result["arms"][arm]["summary"]
        assert summary["reward_mean"]["value"] == 1.0
        assert summary["resolution_rate"]["value"] == 1.0
        assert summary["diagnosis_accuracy"]["value"] == 1.0
        assert summary["time_to_recovery_mean_seconds"]["value"] == 3.0
        assert summary["reward_mean"]["denominator"] == 1


def test_mixed_resolved_and_right_censored_attempts_keep_resolved_ttr_mean():
    second_id = "synthetic/replay-002"
    _, _, pins, _, _ = _candidate_inputs()
    rows = _common_rows(
        [
            (ARM_ORDER[0], _recovered_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])),
            (ARM_ORDER[0], _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]], second_id)),
        ]
    )

    result = _replay_common(rows, scenario_ids=[SCENARIO_ID, second_id])
    summary = result["arms"][ARM_ORDER[0]]["summary"]

    assert summary["reward_mean"]["value"] == 0.4375
    assert summary["reward_mean"]["denominator"] == 2
    assert summary["resolution_rate"]["value"] == 0.5
    assert summary["diagnosis_accuracy"]["value"] == 1.0
    assert summary["time_to_recovery_mean_seconds"]["value"] == 3.0
    assert summary["time_to_recovery_mean_seconds"]["denominator"] == 1
    assert summary["time_to_recovery_mean_seconds"]["right_censored_count"] == 1


def test_known_prestart_ineligible_episode_is_reported_outside_attempt_denominator():
    second_id = "synthetic/replay-002"
    _, _, pins, _, _ = _candidate_inputs()
    ineligible = _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]], second_id)
    ineligible["events"][0]["authorized"] = False
    ineligible["events"] = ineligible["events"][:4]
    rows = _common_rows(
        [
            (ARM_ORDER[0], _recovered_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])),
            (ARM_ORDER[0], ineligible),
        ]
    )

    result = _replay_common(rows, scenario_ids=[SCENARIO_ID, second_id])
    arm_result = result["arms"][ARM_ORDER[0]]

    assert arm_result["eligibility_counts"] == {
        "eligible": 1,
        "ineligible": 1,
        "undetermined": 0,
        "missing": 0,
    }
    assert arm_result["summary"]["reward_mean"]["value"] == 1.0
    assert arm_result["summary"]["reward_mean"]["denominator"] == 1
    assert arm_result["summary"]["reward_mean"]["excluded_pre_start_ineligible_count"] == 1
    assert arm_result["summary"]["reward_mean"]["ineligible_attempted_count"] == 0
    assert arm_result["summary"]["diagnosis_accuracy"]["value"] == 1.0
    assert arm_result["summary"]["time_to_recovery_mean_seconds"]["value"] == 3.0


def test_activity_after_known_ineligibility_blocks_denominator_exclusion():
    second_id = "synthetic/replay-002"
    _, _, pins, _, _ = _candidate_inputs()
    ineligible_attempt = _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]], second_id)
    ineligible_attempt["events"][0]["authorized"] = False
    rows = _common_rows(
        [
            (ARM_ORDER[0], _recovered_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])),
            (ARM_ORDER[0], ineligible_attempt),
        ]
    )

    result = _replay_common(rows, scenario_ids=[SCENARIO_ID, second_id])
    summary = result["arms"][ARM_ORDER[0]]["summary"]["reward_mean"]

    assert summary["value"] is None
    assert summary["excluded_pre_start_ineligible_count"] == 0
    assert summary["ineligible_attempted_count"] == 1
    assert any("activity observed" in reason for reason in summary["reasons"])


def test_recovered_episode_without_clock_makes_ttr_summary_unverified():
    _, _, pins, _, _ = _candidate_inputs()
    recovered = _recovered_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])
    recovered["events"][2].pop("recorded_at")
    rows = _common_rows([(ARM_ORDER[0], recovered)])

    result = _replay_common(rows)
    ttr = result["arms"][ARM_ORDER[0]]["summary"]["time_to_recovery_mean_seconds"]

    assert ttr["value"] is None
    assert ttr["resolved_without_observed_ttr_count"] == 1
    assert any("resolved episode" in reason for reason in ttr["reasons"])


def test_resolution_summary_uses_explicit_measurement_not_reward_components():
    second_id = "synthetic/replay-002"
    _, _, pins, _, _ = _candidate_inputs()
    model_failure = _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])
    model_failure["events"] = model_failure["events"][:4] + [
        {"event": "model_failure", "reason": "synthetic model error"}
    ]
    late_failure = _recovered_episode(
        ARM_ORDER[0],
        pins[ARM_ORDER[0]],
        second_id,
    )
    late_failure["events"].insert(
        7,
        {
            "event": "post_action_verification",
            "verification": _verification(
                "failed",
                False,
                first_check=False,
                second_check=False,
            ),
            "recorded_at": "2026-09-29T12:00:05Z",
        },
    )
    late_failure["events"][-1]["recorded_at"] = "2026-09-29T12:00:06Z"
    rows = _common_rows(
        [
            (ARM_ORDER[0], model_failure),
            (ARM_ORDER[0], late_failure),
        ]
    )

    result = _replay_common(rows, scenario_ids=[SCENARIO_ID, second_id])
    arm_result = result["arms"][ARM_ORDER[0]]
    first, second = arm_result["episodes"]

    assert first["a1_resolution_candidate"]["value"] == 0
    assert first["a1_resolution_candidate"]["reason"] == "eligible_model_failure"
    assert first["objective_score"]["value"] is None
    assert second["a1_resolution_candidate"]["value"] == 1
    assert (
        second["a1_resolution_candidate"]["reason"]
        == "first_conclusive_post_action_success_before_cleanup"
    )
    assert second["objective_score"]["components"]["verified_recovery"] == 0.0
    assert arm_result["summary"]["resolution_rate"]["value"] == 0.5
    assert arm_result["summary"]["resolution_rate"]["denominator"] == 2
    assert arm_result["summary"]["reward_mean"]["value"] is None


def test_rejects_common_rows_hash_mismatch_before_returning_metrics():
    _, _, pins, _, _ = _candidate_inputs()
    rows = _common_rows([(arm, _worked_episode(arm, pins[arm])) for arm in ARM_ORDER])

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _replay_common(rows, expected_sha256="0" * 64)


def test_rejects_out_of_order_common_arm_membership():
    _, _, pins, _, _ = _candidate_inputs()
    rows = _common_rows([(arm, _worked_episode(arm, pins[arm])) for arm in reversed(ARM_ORDER)])

    with pytest.raises(ValueError, match="ordered membership"):
        _replay_common(rows)


def test_common_rows_require_explicit_hash_and_reject_duplicate_json_fields():
    candidate_schema, runs, pins, scorer_sha256, contract_sha256 = _candidate_inputs()
    episode = _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])
    encoded_episode = json.dumps(episode, separators=(",", ":"), ensure_ascii=False)
    duplicate_key_rows = (
        f'{{"arm":"{ARM_ORDER[0]}","arm":"{ARM_ORDER[0]}","episode":{encoded_episode}}}\n'
    ).encode("utf-8")
    arguments = {
        "source_pins": pins,
        "comparison_scorer_sha256": scorer_sha256,
        "measurement_contract_sha256": contract_sha256,
        "partition": "validation",
    }

    with pytest.raises(ValueError, match="64-character SHA-256"):
        replay_candidate_comparison(
            runs,
            candidate_schema,
            _measurement_contract(),
            common_episode_rows=duplicate_key_rows,
            expected_common_episode_rows_sha256=None,
            **arguments,
        )
    with pytest.raises(ValueError, match="duplicate field"):
        replay_candidate_comparison(
            runs,
            candidate_schema,
            _measurement_contract(),
            common_episode_rows=duplicate_key_rows,
            expected_common_episode_rows_sha256=_sha256(duplicate_key_rows),
            **arguments,
        )


def test_common_rows_reject_nonfinite_exponent_numbers():
    _, _, pins, _, _ = _candidate_inputs()
    episode = _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])
    episode["events"].append({"event": "unscored_annotation", "value": 1})
    rows = _common_rows([(ARM_ORDER[0], episode)])
    rows = rows.replace(b'"value":1', b'"value":1e400', 1)

    with pytest.raises(ValueError, match="non-finite JSON number"):
        _replay_common(rows)


def test_common_rows_reject_explicit_mock_or_nonempirical_source_markers():
    _, _, pins, _, _ = _candidate_inputs()
    episode = _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])
    episode["evaluation_mode"] = "mock"
    rows = _common_rows([(ARM_ORDER[0], episode)])

    with pytest.raises(ValueError, match="non-empirical marker"):
        _replay_common(rows)


@pytest.mark.parametrize("partition", ["test", "final-test", "heldout_test"])
def test_replay_rejects_final_test_partition(partition):
    candidate_schema, runs, pins, scorer_sha256, contract_sha256 = _candidate_inputs()
    rows = _common_rows([(arm, _worked_episode(arm, pins[arm])) for arm in ARM_ORDER])

    with pytest.raises(ValueError, match="final Test"):
        replay_candidate_comparison(
            runs,
            candidate_schema,
            _measurement_contract(),
            source_pins=pins,
            comparison_scorer_sha256=scorer_sha256,
            measurement_contract_sha256=contract_sha256,
            common_episode_rows=rows,
            expected_common_episode_rows_sha256=_sha256(rows),
            partition=partition,
        )


@pytest.mark.parametrize(
    ("scope_key", "scope_value"),
    [
        ("dataset_split", "test-set"),
        ("dataset_split", "held_out_test"),
        ("dataset_split", "test_set"),
        ("partition_name", "test"),
        ("partitionName", "heldout_test"),
        ("datasetSplit", "final-test"),
        ("held_out_test", True),
        ("test_set", ["synthetic/replay-001"]),
        ("testset", True),
        ("heldouttest", True),
        ("is_test", True),
        ("is_final_test", True),
        ("isTest", True),
        ("isFinalTest", True),
    ],
)
def test_replay_rejects_nested_final_test_scope_aliases(scope_key, scope_value):
    _, _, pins, _, _ = _candidate_inputs()
    episode = _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])
    episode["metadata"] = {scope_key: scope_value}
    rows = _common_rows([(ARM_ORDER[0], episode)])

    with pytest.raises(ValueError, match="final Test"):
        _replay_common(rows)


@pytest.mark.parametrize(
    "scope_key",
    [
        "dataset",
        "dataset_name",
        "dataset_partition",
        "dataset_split",
        "datasetName",
        "datasetPartition",
        "datasetSplit",
    ],
)
@pytest.mark.parametrize(
    "scope_location",
    ["run_descriptor", "measurement_contract", "raw_episode", "raw_event"],
)
def test_public_replay_rejects_final_test_dataset_scope_aliases(
    scope_key,
    scope_location,
):
    candidate_schema, runs, pins, scorer_sha256, contract_sha256 = _candidate_inputs()
    measurement_contract = _measurement_contract()
    row_episodes = [
        (arm, _worked_episode(arm, pins[arm]))
        for arm in ARM_ORDER
    ]

    if scope_location == "run_descriptor":
        runs[0]["metadata"] = {scope_key: "final-test"}
    elif scope_location == "measurement_contract":
        measurement_contract["metadata"] = {scope_key: "final-test"}
        contract_sha256 = _canonical_sha256(measurement_contract)
        candidate_schema["measurement_contract_sha256"] = contract_sha256
        for run in runs:
            run["measurement_contract_sha256"] = contract_sha256
    elif scope_location == "raw_episode":
        row_episodes[0][1][scope_key] = "final-test"
    else:
        row_episodes[0][1]["events"].append(
            {"event": "source_metadata", scope_key: "final-test"}
        )

    rows = _common_rows(row_episodes)
    with pytest.raises(ValueError, match="final Test"):
        replay_candidate_comparison(
            runs,
            candidate_schema,
            measurement_contract,
            source_pins=pins,
            comparison_scorer_sha256=scorer_sha256,
            measurement_contract_sha256=contract_sha256,
            common_episode_rows=rows,
            expected_common_episode_rows_sha256=_sha256(rows),
            partition="validation",
        )


def test_replay_allows_synthetic_dataset_and_unrelated_metadata():
    _, _, pins, _, _ = _candidate_inputs()
    rows = []
    for arm in ARM_ORDER:
        episode = _worked_episode(arm, pins[arm])
        episode["dataset"] = "synthetic/replay-001"
        episode["metadata"] = {"note": "final-test appears in unrelated prose"}
        rows.append((arm, episode))

    result = _replay_common(_common_rows(rows))

    assert result["scenario_ids"] == [SCENARIO_ID]


@pytest.mark.parametrize("arm", ARM_ORDER[:2])
def test_common_replay_rejects_g9_diagnosis_observation_on_g6_g8_before_scoring(
    arm,
    monkeypatch,
):
    _, _, pins, _, _ = _candidate_inputs()
    episode = _worked_episode(arm, pins[arm])
    episode["events"] = [
        event for event in episode["events"] if event["event"] != "diagnosis_output"
    ]
    episode["diagnosis_observation"] = {
        "status": "unavailable",
        "reason": "g9_diagnosis_not_observed",
        "source_format": "g9_event_stream",
        "source_sha256": episode["source_sha256"],
        "raw_refs": deepcopy(episode["raw_refs"]),
    }
    scored_episodes = []
    measure_candidate_episode = candidate_measurement.measure_candidate_episode

    def record_measurement(raw_episode, measurement_contract):
        scored_episodes.append(raw_episode)
        return measure_candidate_episode(raw_episode, measurement_contract)

    monkeypatch.setattr(
        candidate_measurement,
        "measure_candidate_episode",
        record_measurement,
    )

    with pytest.raises(ValueError, match=r"diagnosis_observation.*SFT \+ GRPO"):
        _replay_common(_common_rows([(arm, episode)]))

    assert scored_episodes == []


def test_replay_rejects_test_scope_in_supplied_contract_metadata():
    candidate_schema, runs, pins, scorer_sha256, contract_sha256 = _candidate_inputs()
    candidate_schema["evaluation_contract"]["split"] = "test"
    for run in runs:
        run["evaluation"]["split"] = "test"
    rows = _common_rows([(arm, _worked_episode(arm, pins[arm])) for arm in ARM_ORDER])

    with pytest.raises(ValueError, match="final Test partition"):
        replay_candidate_comparison(
            runs,
            candidate_schema,
            _measurement_contract(),
            source_pins=pins,
            comparison_scorer_sha256=scorer_sha256,
            measurement_contract_sha256=contract_sha256,
            common_episode_rows=rows,
            expected_common_episode_rows_sha256=_sha256(rows),
            partition="validation",
        )


def test_replay_rejects_test_scope_in_hash_pinned_event_rows():
    _, _, pins, _, _ = _candidate_inputs()
    episode = _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])
    episode["events"].append({"event": "source_metadata", "split": "test"})
    rows = _common_rows(
        [
            (ARM_ORDER[0], episode),
            (ARM_ORDER[1], _worked_episode(ARM_ORDER[1], pins[ARM_ORDER[1]])),
            (ARM_ORDER[2], _worked_episode(ARM_ORDER[2], pins[ARM_ORDER[2]])),
        ]
    )

    with pytest.raises(ValueError, match="final Test partition"):
        _replay_common(rows)


def test_replay_rejects_source_pin_mismatch_and_scorer_or_contract_digest_mismatch():
    candidate_schema, runs, pins, scorer_sha256, contract_sha256 = _candidate_inputs()
    rows = _common_rows([(arm, _worked_episode(arm, pins[arm])) for arm in ARM_ORDER])
    changed_pins = deepcopy(pins)
    changed_pins[ARM_ORDER[0]]["source_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="raw source digest differs"):
        replay_candidate_comparison(
            runs,
            candidate_schema,
            _measurement_contract(),
            source_pins=changed_pins,
            comparison_scorer_sha256=scorer_sha256,
            measurement_contract_sha256=contract_sha256,
            common_episode_rows=rows,
            expected_common_episode_rows_sha256=_sha256(rows),
            partition="validation",
        )
    with pytest.raises(ValueError, match="runtime scorer source"):
        replay_candidate_comparison(
            runs,
            candidate_schema,
            _measurement_contract(),
            source_pins=pins,
            comparison_scorer_sha256="0" * 64,
            measurement_contract_sha256=contract_sha256,
            common_episode_rows=rows,
            expected_common_episode_rows_sha256=_sha256(rows),
            partition="validation",
        )
    with pytest.raises(ValueError, match="does not match canonical contract"):
        replay_candidate_comparison(
            runs,
            candidate_schema,
            _measurement_contract(),
            source_pins=pins,
            comparison_scorer_sha256=scorer_sha256,
            measurement_contract_sha256="0" * 64,
            common_episode_rows=rows,
            expected_common_episode_rows_sha256=_sha256(rows),
            partition="validation",
        )


def test_missing_scheduled_episode_stays_explicit_and_nulls_the_arm_summary():
    _, _, pins, _, _ = _candidate_inputs()
    rows = _common_rows(
        [
            (ARM_ORDER[0], _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])),
            (ARM_ORDER[2], _worked_episode(ARM_ORDER[2], pins[ARM_ORDER[2]])),
        ]
    )

    result = _replay_common(rows)
    missing = result["arms"][ARM_ORDER[1]]

    assert missing["missing_scheduled_ids"] == [SCENARIO_ID]
    assert missing["eligibility_counts"]["missing"] == 1
    assert missing["episodes"] == []
    assert missing["summary"]["reward_mean"]["value"] is None
    assert any(
        "missing_scheduled_ids" in reason for reason in missing["summary"]["reward_mean"]["reasons"]
    )


def test_infrastructure_interruption_remains_pending_and_nulls_summaries():
    _, _, pins, _, _ = _candidate_inputs()
    interrupted = _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])
    interrupted["events"].append(
        {"event": "episode_interrupted", "reason": "synthetic interruption"}
    )
    rows = _common_rows(
        [
            (ARM_ORDER[0], interrupted),
            (ARM_ORDER[1], _worked_episode(ARM_ORDER[1], pins[ARM_ORDER[1]])),
            (ARM_ORDER[2], _worked_episode(ARM_ORDER[2], pins[ARM_ORDER[2]])),
        ]
    )

    result = _replay_common(rows)
    arm_result = result["arms"][ARM_ORDER[0]]

    assert arm_result["pending_adjudication_count"] == 1
    assert (
        arm_result["episodes"][0]["outcome_classification"]
        == "eligible_pending_independent_adjudication"
    )
    assert arm_result["summary"]["reward_mean"]["value"] is None
    assert any(
        "pending independent adjudication" in reason
        for reason in arm_result["summary"]["reward_mean"]["reasons"]
    )


def test_model_failure_is_retained_as_negative_without_inventing_reward():
    _, _, pins, _, _ = _candidate_inputs()
    failed = _worked_episode(ARM_ORDER[0], pins[ARM_ORDER[0]])
    failed["events"] = failed["events"][:4] + [
        {"event": "model_failure", "reason": "synthetic model error"}
    ]
    rows = _common_rows(
        [
            (ARM_ORDER[0], failed),
            (ARM_ORDER[1], _worked_episode(ARM_ORDER[1], pins[ARM_ORDER[1]])),
            (ARM_ORDER[2], _worked_episode(ARM_ORDER[2], pins[ARM_ORDER[2]])),
        ]
    )

    result = _replay_common(rows)
    arm_result = result["arms"][ARM_ORDER[0]]

    assert arm_result["negative_episode_count"] == 1
    assert arm_result["pending_adjudication_count"] == 0
    assert arm_result["episodes"][0]["objective_score"]["value"] is None
    assert arm_result["summary"]["reward_mean"]["value"] is None


def test_declared_discrepancies_are_reported_without_replacing_recomputation():
    _, _, pins, _, _ = _candidate_inputs()
    rows = _common_rows([(arm, _worked_episode(arm, pins[arm])) for arm in ARM_ORDER])

    result = _replay_common(
        rows,
        declared_summaries={
            ARM_ORDER[0]: {"reward_mean": 0.75, "macro_f1": 1.0},
        },
    )
    comparisons = result["arms"][ARM_ORDER[0]]["declared_summary_comparison"]

    assert comparisons["reward_mean"]["status"] == "MISMATCH"
    assert comparisons["reward_mean"]["recomputed"] == -0.125
    assert comparisons["macro_f1"]["status"] == "NOT_COMPUTED"
    assert result["arms"][ARM_ORDER[0]]["summary"]["reward_mean"]["value"] == -0.125


def test_native_diagnosis_only_rows_remain_non_empirical_and_g8_identity_stays_unbound():
    declared = {
        arm: {
            "run_id": f"declared-run-{index}",
            "model": f"declared-model-{index}",
        }
        for index, arm in enumerate(ARM_ORDER)
    }
    sources = {
        ARM_ORDER[0]: _jsonl([_native_diagnosis_row(declared[ARM_ORDER[0]])]),
        ARM_ORDER[1]: _jsonl([_native_diagnosis_row()]),
    }
    g9_events = _g9_native_events(declared[ARM_ORDER[2]])
    g9_events[2]["recorded_at"] = "2026-09-29T12:00:01Z"
    g9_events[3]["recorded_at"] = "2026-09-29T12:00:02Z"
    sources[ARM_ORDER[2]] = _jsonl(g9_events)
    pins = {
        arm: {
            "source_identity": declared[arm],
            "source_sha256": _sha256(sources[arm]),
        }
        for arm in ARM_ORDER
    }

    result = _replay_native(sources, pins)

    g6_source = result["arms"][ARM_ORDER[0]]["source"]["identity_binding"]
    assert g6_source["status"] == "RAW_BOUND"
    assert g6_source["raw_bound_source_identity"] == declared[ARM_ORDER[0]]
    g6_episode = result["arms"][ARM_ORDER[0]]["episodes"][0]
    assert g6_episode["raw_episode"]["source_identity_model_basis"] == (
        "requested_model_name_only"
    )
    g8_source = result["arms"][ARM_ORDER[1]]["source"]["identity_binding"]
    assert g8_source["status"] == "UNBOUND"
    assert g8_source["raw_bound_source_identity"] == {"run_id": None, "model": None}
    for arm in ARM_ORDER[:2]:
        episode = result["arms"][arm]["episodes"][0]
        assert episode["eligibility"]["status"] == "undetermined"
        assert episode["diagnosis"]["score"] is None
        assert result["arms"][arm]["summary"]["diagnosis_accuracy"]["value"] is None
        assert not any(
            event["event"] in {"fault_authorization", "fault_observation", "alert_delivery"}
            for event in episode["raw_episode"]["events"]
        )
    assert result["arms"][ARM_ORDER[1]]["episodes"][0]["raw_episode"][
        "source_identity_limitation"
    ] == "raw rows lack run_id, model; identity remains UNBOUND"
    assert result["arms"][ARM_ORDER[2]]["source"]["identity_binding"]["status"] == "RAW_BOUND"
    assert (
        result["arms"][ARM_ORDER[2]]["source"]["identity_binding"]["raw_bound_source_identity"]
        == declared[ARM_ORDER[2]]
    )
    assert result["arms"][ARM_ORDER[2]]["source"]["evaluator_identity_binding"] == {
        "status": "RAW_COMMIT_AND_DIRTY_MATCHED",
        "raw_reported": {"git_sha": "3" * 40, "git_dirty": False},
        "validated_descriptor": {
            "commit_sha": "3" * 40,
            "tree_sha256": "5" * 64,
            "dirty": False,
        },
        "tree_sha256_status": "DESCRIPTOR_ONLY_NOT_RAW_BOUND",
    }
    assert result["arms"][ARM_ORDER[2]]["source"]["terminal_record_ref"] == {
        "event": "run_completed",
        "source_sha256": _sha256(sources[ARM_ORDER[2]]),
        "line": 6,
    }
    g9_episode = result["arms"][ARM_ORDER[2]]["episodes"][0]
    assert result["evaluation_mode"] == "NON_EMPIRICAL"
    assert result["empirical_claim_allowed"] is False
    assert g9_episode["eligibility"]["status"] == "undetermined"
    assert g9_episode["objective_score"]["value"] is None
    assert g9_episode["time_to_recovery"]["seconds"] is None
    mapped_events = {
        event["event"]: event for event in g9_episode["raw_episode"]["events"]
    }
    assert mapped_events["pre_action_verification"]["source_event_recorded_at"] == (
        "2026-09-29T12:00:01Z"
    )
    for event_name in ("action_result", "post_action_verification"):
        assert mapped_events[event_name]["source_event_recorded_at"] == "2026-09-29T12:00:02Z"
    assert all(
        "recorded_at" not in event and "clock_source" not in event
        for event in g9_episode["raw_episode"]["events"]
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
        for event in g9_episode["raw_episode"]["events"]
    )


def test_native_g9_raw_evaluator_commit_must_match_validated_run_descriptor():
    sources, pins, _ = _native_g9_replay_inputs(
        {"git_sha": "4" * 40, "git_dirty": False}
    )

    with pytest.raises(ValueError, match="G9 raw evaluator git_sha"):
        _replay_native(sources, pins)


@pytest.mark.parametrize(
    "evaluator_source",
    [
        {"git_sha": "3" * 40, "git_dirty": True},
        {"git_sha": "3" * 40, "git_dirty": "false"},
        {"git_sha": "not-a-git-sha", "git_dirty": False},
        {"git_sha": "3" * 40, "git_dirty": False, "tree_sha256": "4" * 64},
    ],
    ids=["dirty", "malformed-dirty", "malformed-commit", "mismatched-tree"],
)
def test_native_g9_raw_evaluator_dirty_or_malformed_metadata_is_rejected(evaluator_source):
    sources, pins, _ = _native_g9_replay_inputs(evaluator_source)

    with pytest.raises(ValueError, match="G9 raw evaluator"):
        _replay_native(sources, pins)


def test_native_g9_start_and_terminal_evaluator_metadata_must_agree():
    sources, pins, g9_events = _native_g9_replay_inputs()
    g9_events[-1]["summary"]["evaluator_source"]["git_sha"] = "4" * 40
    sources[ARM_ORDER[2]] = _jsonl(g9_events)
    pins[ARM_ORDER[2]]["source_sha256"] = _sha256(sources[ARM_ORDER[2]])

    with pytest.raises(ValueError, match="G9 raw evaluator provenance is internally inconsistent"):
        _replay_native(sources, pins)


@pytest.mark.parametrize(
    "evaluator_source",
    [
        {},
        {"git_dirty": False},
        None,
        _OMIT_G9_EVALUATOR_SOURCE,
    ],
    ids=["missing-fields", "missing-commit", "null-source", "absent-source"],
)
def test_native_g9_missing_evaluator_fields_remain_unbound(evaluator_source):
    sources, pins, _ = _native_g9_replay_inputs(evaluator_source)

    result = _replay_native(sources, pins)
    binding = result["arms"][ARM_ORDER[2]]["source"]["evaluator_identity_binding"]

    assert binding["status"] == "UNBOUND"
    assert binding["raw_reported"]["git_sha"] is None
    assert binding["tree_sha256_status"] == "DESCRIPTOR_ONLY_NOT_RAW_BOUND"


@pytest.mark.parametrize("unscoped", [False, True], ids=["scoped", "unscoped"])
def test_incomplete_g9_keeps_run_and_model_identity_unbound(unscoped):
    declared_rows = {
        arm: {
            "run_id": f"declared-run-{index}",
            "model": f"declared-model-{index}",
        }
        for index, arm in enumerate(ARM_ORDER[:2])
    }
    sources = {
        ARM_ORDER[0]: _jsonl([_native_diagnosis_row(declared_rows[ARM_ORDER[0]])]),
        ARM_ORDER[1]: _jsonl([_native_diagnosis_row()]),
    }
    raw_g9_identity = {"run_id": "raw-run-not-terminal", "model": "raw-model"}
    interrupted_events = (
        _g9_native_events(raw_g9_identity)[:1]
        if unscoped
        else _g9_native_events(raw_g9_identity)[:-2]
    )
    interrupted_events.append(
        {
            "event": "run_interrupted",
            "completed_episodes": 0,
            **({} if unscoped else {"scenario_id": SCENARIO_ID}),
        }
    )
    sources[ARM_ORDER[2]] = _jsonl(interrupted_events)
    pins = {
        arm: {
            "source_identity": declared_rows[arm],
            "source_sha256": _sha256(sources[arm]),
        }
        for arm in ARM_ORDER[:2]
    }
    pins[ARM_ORDER[2]] = {
        "source_identity": {"run_id": None, "model": None},
        "source_sha256": _sha256(sources[ARM_ORDER[2]]),
    }

    result = _replay_native(sources, pins)
    g9_arm = result["arms"][ARM_ORDER[2]]
    g9_source = g9_arm["source"]
    identity_binding = g9_source["identity_binding"]

    assert (
        g9_source["evaluator_identity_binding"]["status"]
        == "RAW_COMMIT_AND_DIRTY_MATCHED"
    )
    assert identity_binding["status"] == "UNBOUND"
    assert identity_binding["caller_declared_source_identity"] == {
        "run_id": None,
        "model": None,
    }
    assert identity_binding["raw_bound_source_identity"] == {
        "run_id": None,
        "model": None,
    }
    assert g9_source["terminal_record_ref"] == {
        "event": "run_interrupted",
        "source_sha256": pins[ARM_ORDER[2]]["source_sha256"],
        "line": len(interrupted_events),
    }
    if unscoped:
        assert g9_arm["episodes"] == []
        assert g9_arm["observed_scenario_ids"] == []
        assert g9_arm["missing_scheduled_ids"] == [SCENARIO_ID]
    else:
        assert [episode["scenario_id"] for episode in g9_arm["episodes"]] == [
            SCENARIO_ID
        ]
    assert result["evaluation_mode"] == "NON_EMPIRICAL"
    assert result["non_empirical"] is True
    assert result["certification_status"] == "NOT_CERTIFIED"

    no_terminal_sources = dict(sources)
    no_terminal_sources[ARM_ORDER[2]] = _jsonl(_g9_native_events(raw_g9_identity)[:1])
    no_terminal_pins = deepcopy(pins)
    no_terminal_pins[ARM_ORDER[2]]["source_sha256"] = _sha256(
        no_terminal_sources[ARM_ORDER[2]]
    )
    no_terminal_result = _replay_native(no_terminal_sources, no_terminal_pins)
    no_terminal_arm = no_terminal_result["arms"][ARM_ORDER[2]]
    assert no_terminal_arm["source"]["run_outcome"] == "partial"
    assert no_terminal_arm["source"]["terminal_record_ref"] is None
    assert (
        no_terminal_arm["source"]["evaluator_identity_binding"]["status"]
        == "RAW_COMMIT_AND_DIRTY_MATCHED"
    )
    assert no_terminal_arm["episodes"] == []
