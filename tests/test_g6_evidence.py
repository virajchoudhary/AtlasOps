import pytest
from bench import runner

from bench import g6_evidence as evidence


def _episode(**overrides):
    value = {
        "agent_claimed_resolved": True,
        "env_resolved": True,
        "incident": {"trajectory": []},
        "root_cause_evaluation": {"available": True, "correct": True},
        "scenario_id": "single_fault/sf-001",
        "status": "ok",
        "tier": "single_fault",
        "time_to_resolve_s": 10,
        "tool_metrics": {
            "attempts": 2,
            "executed_failures": 0,
            "executed_successes": 2,
            "invalid_arguments": 0,
            "pre_action_evidence": True,
        },
        "total_turns": 4,
        "verification": {"verification_status": "passed", "verification_timestamp": 1},
    }
    value.update(overrides)
    return value


def test_reset_failure_is_preserved_as_harness_invalid():
    episode = _episode(
        environment_invalid_before_trial=True,
        reset_failure=True,
        status="error",
    )
    classification = evidence.classify_episode(episode)

    assert classification["categories"][0] == "HARNESS_INVALID"
    assert "reset_failure" in classification["reasons"]
    assert classification["infrastructure_valid"] is False


def test_taxonomy_separates_harness_model_and_verification():
    harness = evidence.classify_episode({
        "error": "manifest_apply_failed",
        "status": "skip",
    })
    assert harness["categories"] == ["HARNESS_INVALID"]
    assert harness["reasons"] == ["fault_injection_failed"]

    model = evidence.classify_episode({
        "agent_claimed_resolved": True,
        "env_resolved": False,
        "reward_contract": {"penalties": {"false_resolution": 0.25}},
        "root_cause_evaluation": {"available": True, "correct": False},
        "status": "ok",
        "verification": {"verification_status": "failed"},
    })
    assert set(model["categories"]) == {"VERIFICATION_FAILURE", "MODEL_FAILURE"}
    assert {"false_resolution", "wrong_diagnosis"}.issubset(set(model["reasons"]))


def test_metrics_have_explicit_denominators_and_ttr_distribution():
    results = [
        _episode(),
        _episode(
            agent_claimed_resolved=True,
            env_resolved=False,
            reward_contract={"penalties": {"hallucinated_evidence": 0.2}},
            root_cause_evaluation={"available": True, "correct": False},
            time_to_resolve_s=30,
            tool_metrics={
                "attempts": 2,
                "executed_failures": 1,
                "executed_successes": 1,
                "invalid_arguments": 1,
                "pre_action_evidence": False,
            },
        ),
        {"error": "manifest_apply_failed", "scenario_id": "x", "status": "skip"},
    ]
    metrics = evidence.compute_g6_metrics(results)

    assert metrics["completion"] == {
        "completed": 2,
        "denominator": "all_attempted_episodes",
        "rate": pytest.approx(2 / 3),
    }
    assert metrics["env_resolution"]["numerator"] == 1
    assert metrics["env_resolution"]["denominator"] == "all_attempted_episodes"
    assert metrics["env_resolution"]["rate"] == pytest.approx(1 / 3)
    assert metrics["false_resolution"]["rate"] == pytest.approx(1 / 3)
    assert metrics["root_cause_accuracy"]["correct"] == 1
    assert metrics["root_cause_accuracy"]["rate"] == pytest.approx(1 / 3)
    assert metrics["evidence_fabrication"]["rate"] == pytest.approx(1 / 3)
    assert metrics["unnecessary_mutation"]["rate"] == pytest.approx(1 / 3)
    assert metrics["tool_calls"]["attempts"] == 4
    assert metrics["tool_calls"]["invalid_or_unsupported"] == 1
    assert metrics["tool_calls"]["validity_rate"] == pytest.approx(0.75)
    assert metrics["ttr_seconds"] == {
        "count": 2,
        "max": 30.0,
        "mean": 20.0,
        "min": 10.0,
        "p50": 20.0,
        "p95": 29.0,
    }


def test_raw_record_is_hashed_and_marks_identity_hidden():
    episode = _episode()
    manifest = {
        "catalog_sha256": "catalog",
        "frozen_split_sha256": "split",
        "model": {"name": "model", "provider": "provider"},
        "observed_runtime": {"git_commit": "sha"},
        "predeclared_protocol": {"split_role": "validation"},
        "run_id": "run-1",
    }
    record = evidence.build_raw_record(
        episode,
        run_manifest=manifest,
        episode_index=7,
        written_at="2030-01-01T00:00:00Z",
    )
    unsigned = {key: value for key, value in record.items() if key != "record_sha256"}

    assert record["record_sha256"]
    assert record["record_sha256"] == evidence.sha256_object(unsigned)
    assert record["infrastructure_valid"] is True
    assert record["timestamps"]["record_written_at"] == "2030-01-01T00:00:00Z"
    assert record["episode_index"] == 7
    assert record["scenario_identity"]["hidden_orchestration_metadata"] is True


def test_resume_rejects_changed_scenarios_or_configuration():
    stored = {
        "catalog_sha256": "catalog",
        "config_sha256": "one",
        "frozen_split_sha256": "split",
        "role_and_verifier_contracts": {"tool": "contract"},
        "scenario_ids": ["a"],
    }
    common = {
        "catalog_sha256": "catalog",
        "contracts": {"tool": "contract"},
        "frozen_split_sha256": "split",
    }

    runner.validate_resume_manifest(
        stored,
        config_hash="one",
        scenario_ids=["a"],
        **common,
    )
    with pytest.raises(RuntimeError, match="scenario sequence"):
        runner.validate_resume_manifest(stored, config_hash="one", scenario_ids=["b"], **common)
    with pytest.raises(RuntimeError, match="configuration"):
        runner.validate_resume_manifest(stored, config_hash="two", scenario_ids=["a"], **common)
    with pytest.raises(RuntimeError, match="catalogue"):
        runner.validate_resume_manifest(
            stored,
            catalog_sha256="other",
            config_hash="one",
            contracts={"tool": "contract"},
            frozen_split_sha256="split",
            scenario_ids=["a"],
        )


def test_resume_rejects_missing_or_mismatched_raw_records(tmp_path):
    with pytest.raises(RuntimeError, match="raw-record"):
        runner.validate_resume_raw_records(tmp_path, 1)

    raw_path = tmp_path / "raw_records.jsonl"
    raw_path.write_text('{"a":1}\n', encoding="utf-8")
    runner.validate_resume_raw_records(tmp_path, 1)
    raw_path.write_text('{"a":1}\n{broken\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="truncated"):
        runner.validate_resume_raw_records(tmp_path, 1)
