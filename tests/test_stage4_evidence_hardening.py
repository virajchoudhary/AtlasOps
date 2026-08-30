"""Deterministic regression tests for Stage 4 causal/evidence hardening."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from concurrent.futures import ThreadPoolExecutor
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scripts.run_stage4_golden_incident as runner
from config.g4_protocol import (
    APPROVED_G4_MODEL,
    APPROVED_G4_PROTOCOL_PROFILE,
    REQUIRED_METRICS_SERVER_ARGS,
    protocol_fingerprint,
)
from scripts.run_stage4_golden_incident import (
    ATTEMPT_STATE_COMPLETED,
    ATTEMPT_STATE_CONSUMED,
    ATTEMPT_STATE_RESERVED,
    DEGRADATION_QUERY,
    _attempt_marker_path,
    _paymentservice_baseline_check,
    collect_sf002_cpu_telemetry,
    G4_PLATFORM_HARDENING_MARKER,
    MAX_ATTEMPTS_PER_PROTOCOL_MARKER,
    complete_experiment_attempt,
    consume_experiment_attempt,
    evaluate_causal_g4_predicate,
    release_experiment_reservation,
    reserve_experiment_attempt,
    sf002_degradation_decision,
    stage4_evidence_metadata,
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


@pytest.fixture(autouse=True)
def isolated_protocol_runtime(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_query_ollama_model_identity",
        lambda selected_model: {
            "provider": "ollama-local",
            "name": selected_model,
            "digest": APPROVED_G4_PROTOCOL_PROFILE["model"]["digest"],
        },
    )
    monkeypatch.setattr(
        runner,
        "_probe_metrics_server_contract",
        lambda: APPROVED_G4_PROTOCOL_PROFILE["metrics_api"],
    )


def test_stage4_metadata_persists_protocol_marker():
    metadata = stage4_evidence_metadata()
    assert metadata["protocol_marker"] == G4_PLATFORM_HARDENING_MARKER


def test_reservation_records_protocol_marker_and_spent_limit_is_two():
    root = _attempt_root()
    reservation = reserve_experiment_attempt(
        "EXP-STAGE4-MARKER",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    assert reservation["protocol_profile"] == APPROVED_G4_PROTOCOL_PROFILE
    assert reservation["protocol_fingerprint"] == protocol_fingerprint(
        APPROVED_G4_PROTOCOL_PROFILE
    )
    assert MAX_ATTEMPTS_PER_PROTOCOL_MARKER == 2


def test_default_and_arbitrary_models_cannot_consume_approved_protocol_budget():
    root = _attempt_root()
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        reserve_experiment_attempt(
            "EXP-STAGE4-DEFAULT-MODEL",
            selected_model="qwen2.5:1.5b",
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not list(pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts").glob("*.json"))

    with pytest.raises(RuntimeError, match="approved protocol profile"):
        reserve_experiment_attempt(
            "EXP-STAGE4-ARBITRARY-MODEL",
            selected_model="qwen2.5:3b-instruct",
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not list(pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts").glob("*.json"))


def test_metrics_api_absence_cannot_reserve_under_required_present_profile(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_probe_metrics_server_contract",
        lambda: {"state": "missing"},
    )
    root = _attempt_root()
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        reserve_experiment_attempt(
            "EXP-STAGE4-METRICS-MISSING",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not list(pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts").glob("*.json"))


def test_metrics_api_drift_cannot_masquerade_under_same_profile(monkeypatch):
    drifted_payload = {
        "metadata": {"name": "metrics-server", "namespace": "kube-system"},
        "spec": {"template": {"spec": {
            "serviceAccountName": "metrics-server",
            "priorityClassName": "system-cluster-critical",
            "containers": [{
                "name": "metrics-server",
                "image": "registry.example.invalid/metrics-server:v0.7.2",
                "args": list(REQUIRED_METRICS_SERVER_ARGS),
                "ports": [{"containerPort": 10250, "name": "https", "protocol": "TCP"}],
                "resources": {"requests": {"cpu": "100m", "memory": "200Mi"}},
            }],
        }}},
    }
    monkeypatch.setattr(
        runner,
        "_probe_metrics_server_contract",
        lambda: __import__("config.g4_protocol", fromlist=["inspect_metrics_server_deployment"]).inspect_metrics_server_deployment(
            lambda _args: {"success": True, "stdout": json.dumps(drifted_payload)}
        ),
    )
    root = _attempt_root()
    with pytest.raises(RuntimeError, match="Metrics API Deployment provenance mismatch"):
        reserve_experiment_attempt(
            "EXP-STAGE4-METRICS-DRIFT",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not list(pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts").glob("*.json"))


def test_prompt_or_tool_contract_drift_fails_closed_before_attempt_budget(
    monkeypatch,
):
    root = _attempt_root()
    original_builder = runner.build_runtime_protocol_profile

    # Keep the live model/Metrics probes valid so this test isolates declared
    # contract drift from unrelated runtime drift.

    for field in ("diagnosis_prompt", "role_tool_contract"):
        def build_with_drift(**kwargs):
            profile = original_builder(**kwargs)
            profile[field] = {**profile[field], "sha256": "0" * 64}
            return profile

        monkeypatch.setattr(runner, "build_runtime_protocol_profile", build_with_drift)
        with pytest.raises(RuntimeError, match="approved protocol profile"):
            reserve_experiment_attempt(
                f"EXP-STAGE4-{field.upper()}-DRIFT",
                selected_model=APPROVED_G4_MODEL,
                main_sha="test-sha",
                attempt_root=root,
            )
    assert not list(pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts").glob("*.json"))


def test_historical_unmarked_attempts_are_not_retroactively_counted():
    root = _attempt_root()
    attempts_dir = pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts")
    attempts_dir.mkdir(parents=True)
    legacy = {
        "experiment_id": "EXP-STAGE4-HISTORICAL",
        "state": "COMPLETED",
        "protocol_marker": G4_PLATFORM_HARDENING_MARKER,
    }
    (attempts_dir / "EXP-STAGE4-HISTORICAL.attempt.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )

    reservation = reserve_experiment_attempt(
        "EXP-STAGE4-AFTER-HISTORY",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    assert reservation["protocol_fingerprint"] == protocol_fingerprint(
        APPROVED_G4_PROTOCOL_PROFILE
    )


def test_third_spent_attempt_for_same_protocol_marker_fails_closed():
    root = _attempt_root()
    for experiment_id in ("EXP-STAGE4-MARKER-A", "EXP-STAGE4-MARKER-B"):
        reservation = reserve_experiment_attempt(
            experiment_id,
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )
        consume_experiment_attempt(reservation, attempt_root=root)

    with pytest.raises(RuntimeError, match="protocol attempt limit reached"):
        reserve_experiment_attempt(
            "EXP-STAGE4-MARKER-C",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )


def test_corrupt_attempt_accounting_fails_closed():
    root = _attempt_root()
    attempts_dir = pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts")
    attempts_dir.mkdir(parents=True)
    (attempts_dir / "corrupt.attempt.json").write_text("{", encoding="utf-8")

    with pytest.raises(RuntimeError, match="attempt accounting record is invalid"):
        reserve_experiment_attempt(
            "EXP-STAGE4-CORRUPT-ACCOUNTING",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not list(attempts_dir.glob("EXP-STAGE4-CORRUPT-ACCOUNTING.*"))
    assert not (attempts_dir / runner.ATTEMPT_BUDGET_LOCK_FILENAME).exists()


def test_stale_reservation_lock_fails_closed_without_creating_attempt():
    root = _attempt_root()
    attempts_dir = pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts")
    lock_path = attempts_dir / runner.ATTEMPT_BUDGET_LOCK_FILENAME
    attempts_dir.mkdir(parents=True)
    lock_path.write_text("stale", encoding="utf-8")

    with pytest.raises(RuntimeError, match="reservation budget is locked"):
        reserve_experiment_attempt(
            "EXP-STAGE4-STALE-LOCK",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not any(attempts_dir.glob("EXP-STAGE4-STALE-LOCK.*"))
    assert lock_path.exists()
    lock_path.unlink()


def test_reserved_attempts_occupy_budget_slots_and_release_frees_a_slot():
    root = _attempt_root()
    spent = reserve_experiment_attempt(
        "EXP-STAGE4-SLOT-A",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    consume_experiment_attempt(spent, attempt_root=root)

    reserved = reserve_experiment_attempt(
        "EXP-STAGE4-SLOT-B",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    with pytest.raises(RuntimeError, match="protocol attempt limit reached"):
        reserve_experiment_attempt(
            "EXP-STAGE4-SLOT-C",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )

    assert release_experiment_reservation(reserved, attempt_root=root) is True
    replacement = reserve_experiment_attempt(
        "EXP-STAGE4-SLOT-D",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    assert replacement["state"] == ATTEMPT_STATE_RESERVED


def test_concurrent_reservations_cannot_exceed_the_last_budget_slot():
    root = _attempt_root()
    first = reserve_experiment_attempt(
        "EXP-STAGE4-RACE-SPENT",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    consume_experiment_attempt(first, attempt_root=root)

    def reserve_slot(experiment_id):
        try:
            return reserve_experiment_attempt(
                experiment_id,
                selected_model=APPROVED_G4_MODEL,
                main_sha="test-sha",
                attempt_root=root,
            )
        except RuntimeError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                reserve_slot,
                ("EXP-STAGE4-RACE-A", "EXP-STAGE4-RACE-B"),
            )
        )

    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, RuntimeError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert any(
        marker in str(failures[0])
        for marker in (
            "protocol attempt limit reached",
            "reservation budget is locked",
        )
    )
    attempts_dir = pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts")
    assert len(list(attempts_dir.glob("*.attempt.json"))) == 2
    assert not (attempts_dir / runner.ATTEMPT_BUDGET_LOCK_FILENAME).exists()


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
        {"max_cores": 0.24},
    )
    failed_ratio = sf002_degradation_decision(
        {"max_cores": 0.80},
        {"max_cores": 1.00},
    )
    missing = sf002_degradation_decision({"max_cores": None}, {"max_cores": 1.0})
    assert passed["passed"] is True
    # Amended envelope contract (2026-08-24): absolute requirement is >= 0.15
    # cores; a +0.14-core increase is below it even though the relative ratio
    # would be large.
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


def _deployment_item(status: dict) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "paymentservice", "namespace": "default", "labels": {"app": "paymentservice"}},
        "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "paymentservice"}}},
        "status": status,
    }


def _pod_item(phase: str = "Running", containers_ready: bool = True) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "paymentservice-754c98b599-rjvxx", "namespace": "default", "labels": {"app": "paymentservice"}},
        "status": {
            "phase": phase,
            "containerStatuses": [{"name": "paymentservice", "ready": containers_ready, "started": True}],
        },
    }


def test_baseline_health_requires_ready_desired_replicas():
    healthy = json.dumps({
        "items": [{"status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1}}]
    })
    not_ready = json.dumps({
        "items": [{"status": {"replicas": 1, "readyReplicas": 0, "availableReplicas": 0}}]
    })
    zero_desired = json.dumps({
        "items": [{"status": {"replicas": 0, "readyReplicas": 0, "availableReplicas": 0}}]
    })
    missing_replica_state = json.dumps({"items": [{"metadata": {"name": "paymentservice"}}]})
    ready_without_availability = json.dumps({
        "items": [{"status": {"replicas": 1, "readyReplicas": 1}}],
    })
    empty = json.dumps({"items": []})
    assert _paymentservice_baseline_healthy(healthy) is True
    assert _paymentservice_baseline_healthy(not_ready) is False
    assert _paymentservice_baseline_healthy(zero_desired) is False
    assert _paymentservice_baseline_healthy(missing_replica_state) is False
    # Availability is informational in the merged contract (mirrors the
    # Deployment readiness gate in agents/verifier.py): readiness is gated,
    # availability is not.
    assert _paymentservice_baseline_healthy(ready_without_availability) is True
    assert _paymentservice_baseline_healthy(empty) is False
    assert _paymentservice_baseline_healthy("not-json") is False


def test_stage4_preflight_supplies_deployment_schema_to_baseline_helper():
    deployment_payload = json.dumps({"items": [_deployment_item({"replicas": 1, "readyReplicas": 1, "availableReplicas": 1})]})
    with patch(
        "scripts.run_stage4_golden_incident.run_kubectl",
        return_value={"success": True, "stdout": deployment_payload, "returncode": 0},
    ) as kubectl:
        healthy, workloads = _paymentservice_baseline_check()
    kubectl.assert_called_once_with(
        ["get", "deployments", "-n", "default", "-l", "app=paymentservice", "-o", "json"]
    )
    assert healthy is True
    assert json.loads(workloads["stdout"])["items"][0]["kind"] == "Deployment"


def test_stage4_preflight_does_not_rely_on_pod_shaped_input():
    pod_payload = json.dumps({"items": [_pod_item()]})
    with patch(
        "scripts.run_stage4_golden_incident.run_kubectl",
        return_value={"success": True, "stdout": pod_payload, "returncode": 0},
    ):
        healthy, workloads = _paymentservice_baseline_check()
    assert healthy is False
    assert json.loads(workloads["stdout"])["items"][0]["kind"] == "Pod"
    # Directly: Pod JSON never satisfies the Deployment-schema helper even for
    # fully Running/ready pods (no replicas/readyReplicas fields -> fail closed).
    assert _paymentservice_baseline_healthy(pod_payload) is False


def test_stage4_preflight_fails_closed_when_deployment_query_fails():
    deployment_payload = json.dumps({"items": [_deployment_item({"replicas": 1, "readyReplicas": 1, "availableReplicas": 1})]})
    with patch(
        "scripts.run_stage4_golden_incident.run_kubectl",
        return_value={"success": False, "stderr": "connection refused", "returncode": 1, "stdout": deployment_payload},
    ):
        healthy, _workloads = _paymentservice_baseline_check()
    assert healthy is False


def test_attempt_lifecycle_blocks_duplicate_and_crashed_attempts():
    root = _attempt_root()
    reservation = reserve_experiment_attempt(
        "EXP-STAGE4-TEST",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    marker = _attempt_marker_path("EXP-STAGE4-TEST", root)
    assert pathlib.Path(marker).exists()
    with pytest.raises(RuntimeError, match="already exists"):
        reserve_experiment_attempt(
            "EXP-STAGE4-TEST",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )

    consume_experiment_attempt(reservation, attempt_root=root)
    consumed = json.loads(pathlib.Path(marker).read_text(encoding="utf-8"))
    assert consumed["state"] == ATTEMPT_STATE_CONSUMED
    with pytest.raises(RuntimeError, match="already exists"):
        reserve_experiment_attempt(
            "EXP-STAGE4-TEST",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )

    complete_experiment_attempt(reservation, attempt_root=root)
    completed = json.loads(pathlib.Path(marker).read_text(encoding="utf-8"))
    assert completed["state"] == ATTEMPT_STATE_COMPLETED
    with pytest.raises(RuntimeError, match="already exists"):
        reserve_experiment_attempt(
            "EXP-STAGE4-TEST",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )


def test_unused_reservation_is_released_after_pre_fault_abort():
    root = _attempt_root()
    reservation = reserve_experiment_attempt(
        "EXP-STAGE4-ABORT",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    assert release_experiment_reservation(reservation, attempt_root=root) is True
    assert pathlib.Path(_attempt_marker_path("EXP-STAGE4-ABORT", root)).exists() is False

    replacement = reserve_experiment_attempt(
        "EXP-STAGE4-AFTER-RELEASE",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    assert replacement["state"] == ATTEMPT_STATE_RESERVED


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


def test_evidence_records_the_settling_report_criterion_13_reads():
    """A verdict must be auditable from the artifact alone.

    Criterion 13 is `env_resolved and settling_completed`, where
    settling_completed comes from incident["settling"]["settled"]. Run 010 passed
    on a settling value that never reached the persisted evidence, so the record
    could not be used to check the verdict it reported.
    """
    import inspect

    import scripts.run_stage4_golden_incident as runner

    source = inspect.getsource(runner.main)
    assert 'evidence["phases"]["settling"]' in source
    # And it must come from the incident record, not be recomputed.
    assert 'incident_result.get("settling", {})' in source
