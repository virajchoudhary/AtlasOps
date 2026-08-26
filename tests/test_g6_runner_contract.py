import asyncio
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from bench import runner


def _episode(**overrides):
    value = {
        "scenario_id": "single_fault/sf-001",
        "tier": "single_fault",
        "status": "ok",
        "resolved": True,
        "verification": {"verification_status": "passed"},
        "time_to_resolve_s": 100,
        "total_turns": 5,
        "judge": {"overall": 0.9},
        "reward_contract": {
            "penalty_total": 0,
            "penalties": {},
            "total": 0.9,
        },
        "tool_metrics": {
            "attempts": 2,
            "blocked_by_circuit_breaker": 0,
            "blocked_by_policy": 0,
            "cap_blocked": 0,
            "dedup_blocked": 0,
            "executed_failures": 0,
            "executed_successes": 2,
            "invalid_arguments": 0,
            "mutating_attempts": 1,
            "successful_investigations": 1,
            "successful_investigations_before_first_mutation": 1,
            "pre_action_evidence": True,
        },
        "root_cause_evaluation": {
            "available": True,
            "correct": True,
            "score": 1.0,
        },
    }
    value.update(overrides)
    return value


def test_root_cause_requires_target_and_fault_class():
    incident = {
        "diagnosis": {
            "final": {
                "root_cause": "OOMKill in cartservice after pod kill",
                "evidence": ["pod events"],
            }
        }
    }
    faults = [
        {"kind": "PodChaos", "action": "pod-kill", "targets": ["cartservice"]},
        {"kind": "DNSChaos", "action": "random", "targets": ["checkoutservice"]},
    ]
    result = runner.evaluate_root_cause(incident, faults)

    assert result["matched_faults"] == ["PodChaos:pod-kill"]
    assert result["correct"] is False
    assert result["score"] == pytest.approx(0.5)
    assert result["target_coverage"] == pytest.approx(0.5)


def test_summary_reports_coverage_failure_taxonomy_and_provenance():
    results = [
        _episode(),
        _episode(
            scenario_id="missing/manifest",
            tier="unknown",
            status="skip",
            error="manifest_apply_failed",
            resolved=False,
        ),
        _episode(
            scenario_id="agent/error",
            tier="unknown",
            status="error",
            error="boom",
            resolved=False,
        ),
    ]
    summary = runner.compute_summary(
        results,
        "unit-tag",
        "unit-model",
        {"config_sha256": "unit-config", "git_commit": "unit"},
    )

    assert summary["schema_version"] == runner.RESULT_SCHEMA_VERSION
    assert summary["config_sha256"] == "unit-config"
    assert summary["status_counts"] == {"ok": 1, "skip": 1, "error": 1}
    assert summary["completion_rate"] == pytest.approx(1 / 3, abs=5e-4)
    assert summary["resolution_rate"] == pytest.approx(1 / 3, abs=5e-4)
    assert summary["failure_taxonomy"]["reason_counts"]["manifest_apply_failed"] == 1
    assert summary["failure_taxonomy"]["reason_counts"]["agent_exception"] == 1
    assert summary["root_cause_metrics"] == {
        "available_episodes": 1,
        "correct_episodes": 1,
        "correct_rate_among_available": 1.0,
    }
    assert summary["per_tier"]["unknown"]["attempted_count"] == 2
    assert summary["per_tier"]["unknown"]["completed_count"] == 0


def test_output_directory_is_fail_closed_and_resume_preserves_raw_prefix(tmp_path):
    out_dir = tmp_path / "run"
    assert runner.prepare_output_directory(out_dir) == []

    episode = _episode()
    runner.append_episode(out_dir, episode)
    assert runner.prepare_output_directory(out_dir)[0]["scenario_id"] == episode["scenario_id"]

    completed = tmp_path / "completed"
    completed.mkdir()
    (completed / "results_summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="completed raw run"):
        runner.prepare_output_directory(completed)

    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "results_per_episode.jsonl").write_text('{"ok":true}\n{broken\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="truncated/invalid"):
        runner.prepare_output_directory(corrupt)

    unterminated = tmp_path / "unterminated"
    unterminated.mkdir()
    (unterminated / "results_per_episode.jsonl").write_text(
        '{"ok":true}\n{"ok":', encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="incomplete trailing record"):
        runner.prepare_output_directory(unterminated)


def test_main_refuses_final_test_without_explicit_gate(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["runner", "--model", "unit-model", "--split-role", "final_test"],
    )
    with pytest.raises(RuntimeError, match="membership is gated"):
        asyncio.run(runner.main())


def test_main_refuses_dynamic_mix_into_frozen_split(monkeypatch):
    argv = [
        "runner", "--model", "unit-model", "--split-role", "validation",
        "--adversarial", "1",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(RuntimeError, match="dynamic adversarial"):
        asyncio.run(runner.main())


def test_model_alert_sanitiser_removes_only_evaluation_identity():
    alert = {
        "scenario_id": "single_fault/sf-002",
        "commonLabels": {"alertname": "CPU", "service": "paymentservice"},
        "alerts": [{"labels": {"service": "paymentservice"}}],
    }
    cleaned = runner._model_visible_alert(alert)

    assert "scenario_id" not in cleaned
    assert cleaned["commonLabels"]["service"] == "paymentservice"
    assert alert["scenario_id"] == "single_fault/sf-002"


def test_quick_eval_uses_hidden_evaluation_channel(monkeypatch):
    from bench import quick_eval
    from agents import coordinator

    seen = {}

    async def fake_handle(alert, incident_id=None, scenario_id=None):
        seen["alert"] = alert
        seen["scenario_id"] = scenario_id
        return {}

    monkeypatch.setattr(coordinator, "handle_incident", fake_handle)
    asyncio.run(quick_eval.run_scenario("sf-001"))

    assert "scenario_id" not in seen["alert"]
    assert seen["scenario_id"] == "single_fault/sf-001"


def test_legacy_eval_and_leaderboard_do_not_embed_scenario_identity():
    for filename in ("eval.py", "leaderboard.py"):
        text = Path(filename).read_text(encoding="utf-8")
        assert 'alert["scenario_id"]' not in text
        assert "model_alert.pop(\"scenario_id\", None)" in text


def test_expected_alert_selector_rejects_unrelated_alerts():
    expected = [
        ("alertname=a", "service=one", "namespace=default", "severity=critical"),
        ("alertname=b", "service=two", "namespace=default", "severity=warning"),
    ]
    active = [
        {"labels": {"alertname": "b", "service": "two", "namespace": "default", "severity": "warning"}},
        {"labels": {"alertname": "a", "service": "one", "namespace": "default"}},
        {"labels": {"alertname": "unrelated", "service": "other"}},
    ]
    matched, missing, unexpected = runner.select_expected_alerts(
        active,
        expected,
        common_labels={"namespace": "default"},
    )

    assert len(matched) == 2
    assert not missing
    assert unexpected == [
        ("alertname=unrelated", "namespace=default", "service=other")
    ]


def test_run_scenario_preserves_harness_invalid_alert_observations(monkeypatch):
    monkeypatch.setattr(runner, "preflight_environment", Mock())
    monkeypatch.setattr(runner, "verify_injection", Mock())
    monkeypatch.setattr(runner, "apply_chaos", Mock(return_value=True))
    monkeypatch.setattr(runner, "reset_cluster", Mock(return_value=True))
    monkeypatch.setattr(
        runner,
        "wait_for_alert",
        Mock(side_effect=runner.AlertObservationContaminated("unrelated")),
    )

    episode = asyncio.run(runner.run_scenario("single_fault/sf-001"))
    assert episode["status"] == "error"
    assert episode["error"] == "alert_observation_contaminated"
    assert episode["environment_invalid_before_trial"] is True
