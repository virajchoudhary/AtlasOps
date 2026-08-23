"""Deterministic regression tests for Stage 4 causal/evidence hardening."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.run_stage4_golden_incident import (
    ATTEMPT_STATE_COMPLETED,
    ATTEMPT_STATE_CONSUMED,
    DEGRADATION_QUERY,
    _attempt_marker_path,
    collect_sf002_cpu_telemetry,
    complete_experiment_attempt,
    consume_experiment_attempt,
    evaluate_causal_g4_predicate,
    release_experiment_reservation,
    reserve_experiment_attempt,
    sf002_degradation_decision,
    _paymentservice_baseline_healthy,
)


def _attempt_root() -> str:
    root = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scratch"
        / "stage4-attempt-tests"
        / uuid.uuid4().hex
    )
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _valid_incident() -> dict:
    return {
        "incident_id": "inc-hardening",
        "triage": {"final": {"severity": "P1"}},
        "diagnosis": {"final": {"root_cause": "paymentservice CPU pressure"}},
        "approval": {"decision": "timeout"},
        "remediation": {
            "trajectory": [
                {
                    "tool": "chaos_stop_experiment",
                    "args": {
                        "kind": "StressChaos",
                        "name": "sf-002-paymentservice-cpu",
                        "namespace": "chaos-mesh",
                    },
                    "output": {"success": True},
                }
            ],
            "final": {"outcome": "resolved"},
        },
        "verification": {"env_resolved": True},
        "env_resolved": True,
        "comms": {"final": {"slack_posted": True}},
    }


def _model_response(content="", tool_calls=None, finish_reason="stop"):
    response = MagicMock()
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    response.json.return_value = {
        "choices": [{"message": message, "finish_reason": finish_reason}]
    }
    return response


def _native_call(name, args, id):
    return {
        "id": id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_sf002_degradation_requires_absolute_and_relative_increase():
    passed = sf002_degradation_decision(
        {"max_cores": 0.10},
        {"max_cores": 1.00},
    )
    failed_absolute = sf002_degradation_decision(
        {"max_cores": 0.10},
        {"max_cores": 0.25},
    )
    failed_ratio = sf002_degradation_decision(
        {"max_cores": 0.80},
        {"max_cores": 1.00},
    )
    missing = sf002_degradation_decision({"max_cores": None}, {"max_cores": 1.0})
    assert passed["passed"] is True
    assert failed_absolute["passed"] is False
    assert failed_ratio["passed"] is False
    assert missing["measured"] is False


def test_sf002_telemetry_uses_targeted_cadvisor_metric():
    response = {
        "success": True,
        "result": [{"value": [1, "0.75"]}, {"value": [2, "bad"]}],
    }
    with patch("agents.tools.prometheus.promql_query", return_value=response) as query:
        telemetry = collect_sf002_cpu_telemetry(time_unix=123.0)
    query.assert_called_once_with(DEGRADATION_QUERY, time_unix=123.0)
    assert telemetry["query_success"] is True
    assert telemetry["samples_cores"] == [0.75]
    assert telemetry["max_cores"] == 0.75


def test_baseline_health_requires_ready_desired_replicas():
    healthy = json.dumps({
        "items": [{"status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1}}]
    })
    not_ready = json.dumps({
        "items": [{"status": {"replicas": 1, "readyReplicas": 0, "availableReplicas": 0}}]
    })
    empty = json.dumps({"items": []})
    assert _paymentservice_baseline_healthy(healthy) is True
    assert _paymentservice_baseline_healthy(not_ready) is False
    assert _paymentservice_baseline_healthy(empty) is False
    assert _paymentservice_baseline_healthy("not-json") is False


def test_attempt_lifecycle_blocks_duplicate_and_crashed_attempts():
    root = _attempt_root()
    reservation = reserve_experiment_attempt(
        "EXP-STAGE4-TEST",
        selected_model="qwen2.5:3b-instruct",
        main_sha="test-sha",
        attempt_root=root,
    )
    marker = _attempt_marker_path("EXP-STAGE4-TEST", root)
    assert pathlib.Path(marker).exists()
    with pytest.raises(RuntimeError, match="already exists"):
        reserve_experiment_attempt(
            "EXP-STAGE4-TEST",
            selected_model="qwen2.5:3b-instruct",
            main_sha="test-sha",
            attempt_root=root,
        )

    consume_experiment_attempt(reservation, attempt_root=root)
    consumed = json.loads(pathlib.Path(marker).read_text(encoding="utf-8"))
    assert consumed["state"] == ATTEMPT_STATE_CONSUMED
    with pytest.raises(RuntimeError, match="already exists"):
        reserve_experiment_attempt(
            "EXP-STAGE4-TEST",
            selected_model="qwen2.5:3b-instruct",
            main_sha="test-sha",
            attempt_root=root,
        )

    complete_experiment_attempt(reservation, attempt_root=root)
    completed = json.loads(pathlib.Path(marker).read_text(encoding="utf-8"))
    assert completed["state"] == ATTEMPT_STATE_COMPLETED
    with pytest.raises(RuntimeError, match="already exists"):
        reserve_experiment_attempt(
            "EXP-STAGE4-TEST",
            selected_model="qwen2.5:3b-instruct",
            main_sha="test-sha",
            attempt_root=root,
        )


def test_unused_reservation_is_released_after_pre_fault_abort():
    root = _attempt_root()
    reservation = reserve_experiment_attempt(
        "EXP-STAGE4-ABORT",
        selected_model="qwen2.5:3b-instruct",
        main_sha="test-sha",
        attempt_root=root,
    )
    assert release_experiment_reservation(reservation, attempt_root=root) is True
    assert pathlib.Path(_attempt_marker_path("EXP-STAGE4-ABORT", root)).exists() is False


def test_causal_predicate_requires_degradation_settling_and_primary_evidence():
    incident = _valid_incident()
    failed = evaluate_causal_g4_predicate(
        baseline_healthy=True,
        injection_success=True,
        fault_observed=True,
        incident_result=incident,
        harness_repaired_pre_verification=False,
        degradation_proven=False,
        settling_completed=True,
        primary_evidence_persisted=True,
    )
    unsettled = evaluate_causal_g4_predicate(
        baseline_healthy=True,
        injection_success=True,
        fault_observed=True,
        incident_result=incident,
        harness_repaired_pre_verification=False,
        degradation_proven=True,
        settling_completed=False,
        primary_evidence_persisted=True,
    )
    unpersisted = evaluate_causal_g4_predicate(
        baseline_healthy=True,
        injection_success=True,
        fault_observed=True,
        incident_result=incident,
        harness_repaired_pre_verification=False,
        degradation_proven=True,
        settling_completed=True,
        primary_evidence_persisted=False,
    )
    assert failed["criteria"]["3_fault_observed_pre_trigger"] is False
    assert unsettled["criteria"]["13_objective_env_resolved"] is False
    assert unpersisted["criteria"]["15_evidence_persisted"] is False


def test_every_remediation_model_response_is_persisted_before_branching():
    prose = MagicMock()
    prose.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "I will investigate."}, "finish_reason": "stop"}]
    }
    action = MagicMock()
    action.json.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-hardening",
                    "type": "function",
                    "function": {
                        "name": "chaos_stop_experiment",
                        "arguments": json.dumps({
                            "kind": "StressChaos",
                            "name": "sf-002-paymentservice-cpu",
                            "namespace": "chaos-mesh",
                        }),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }]
    }
    conclusion = MagicMock()
    conclusion.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": '{"outcome":"unresolved"}'}, "finish_reason": "stop"}]
    }
    with patch("agents.coordinator.post_with_retry", side_effect=[prose, action, conclusion]):
        with patch("agents.coordinator.require_audit_log"):
            with patch(
                "agents.tools.chaos._run",
                return_value={"success": True, "stdout": "deleted", "returncode": 0},
            ):
                result = asyncio.run(
                    __import__("agents.coordinator", fromlist=["call_agent"]).call_agent(
                        "remediation",
                        {"incident_id": "inc-turns"},
                    )
                )
    records = [step for step in result["trajectory"] if step.get("kind") == "model_turn"]
    assert [record["turn"] for record in records] == [0, 1, 2]
    assert all(
        isinstance(record["executed_tool_calls"], list)
        and all(isinstance(name, str) for name in record["executed_tool_calls"])
        for record in records
    )
    assert records[0]["assistant_text"] == "I will investigate."
    assert records[0]["retry"]["reason"] == "remediation_no_tool_call_retry"
    assert records[1]["native_tool_calls"][0]["name"] == "chaos_stop_experiment"
    assert records[1]["validation_state"] == "validated"
    assert records[1]["executed_tool_calls"] == ["chaos_stop_experiment"]
    assert records[2]["conclusion_present"] is True


def test_forced_conclusion_model_response_is_persisted():
    initial_prose = MagicMock()
    initial_prose.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "I need help concluding."}, "finish_reason": "stop"}]
    }
    retry_prose = MagicMock()
    retry_prose.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "Still no structured call."}, "finish_reason": "stop"}]
    }
    forced = MagicMock()
    forced.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": '{"outcome":"unresolved"}'},
            "finish_reason": "stop",
        }]
    }
    with patch(
        "agents.coordinator.post_with_retry",
        side_effect=[initial_prose, retry_prose, forced],
    ):
        with patch("agents.coordinator.require_audit_log"):
            result = asyncio.run(__import__("agents.coordinator", fromlist=["call_agent"]).call_agent(
                "remediation",
                {"incident_id": "inc-forced"},
            ))
    records = [step for step in result["trajectory"] if step.get("kind") == "model_turn"]
    assert [record["turn"] for record in records] == [0, 1, 1]
    assert records[2]["turn_kind"] == "forced_conclusion"
    assert records[2]["finish_reason"] == "stop"
    assert records[2]["validation_state"] == "final_conclusion"


def test_max_turn_forced_conclusion_has_no_attributed_execution():
    responses = [
        _model_response(
            "",
            tool_calls=[_native_call("promql_query", {"query": f"q{i}"}, f"c{i}")],
            finish_reason="tool_calls",
        )
        for i in range(10)
    ]
    responses.append(_model_response('{"outcome":"unresolved"}'))
    with patch("agents.coordinator.post_with_retry", side_effect=responses) as post:
        with patch("agents.coordinator.require_audit_log"):
            with patch(
                "agents.tools.prometheus.promql_query",
                return_value={"success": True, "result": []},
            ):
                result = asyncio.run(__import__("agents.coordinator", fromlist=["call_agent"]).call_agent(
                    "remediation",
                    {"incident_id": "inc-max-turn-forced"},
                ))

    assert post.call_count == 11
    records = [entry for entry in result["trajectory"] if entry.get("kind") == "model_turn"]
    ordinary_records = records[:-1]
    forced_record = records[-1]
    assert [record["turn"] for record in ordinary_records] == list(range(10))
    assert forced_record["turn"] == 10
    assert forced_record["turn_kind"] == "forced_conclusion"
    assert forced_record["validation_state"] == "final_conclusion"
    assert forced_record["executed_tool_calls"] == []
    assert all(
        isinstance(record["executed_tool_calls"], list)
        and all(isinstance(name, str) for name in record["executed_tool_calls"])
        for record in records
    )


def test_settling_is_bounded_and_preserves_verifier_call_contract():
    responses = [
        SimpleNamespace(env_resolved=False, verification_status="failed", failed_checks=["chaos_mesh_cleared"]),
        SimpleNamespace(env_resolved=True, verification_status="passed", failed_checks=[]),
    ]

    def verify(**kwargs):
        assert kwargs["scenario_id"] == "single_fault/sf-002"
        assert kwargs["agent_claimed_resolved"] is True
        return responses.pop(0)

    with patch("agents.verifier.verify_environment", side_effect=verify):
        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            from agents.coordinator import settle_environment

            report = asyncio.run(settle_environment(
                scenario_id="single_fault/sf-002",
                agent_claimed_resolved=True,
                alert={"commonLabels": {}},
                incident_context={},
            ))
    assert report["settled"] is True
    assert len(report["observations"]) == 2
    assert report["timeout_seconds"] == 30
    sleep.assert_called_once_with(2)
