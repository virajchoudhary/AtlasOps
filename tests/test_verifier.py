"""Unit tests for the Objective Environment Verifier (agents/verifier.py).

Guarantees 100% mocked offline execution with zero network or cloud dependencies.
"""

from __future__ import annotations

import json
import pytest

from agents.verifier import (
    CheckResult,
    EnvironmentVerificationResult,
    EnvironmentVerifier,
    MetricPredicate,
    ScenarioVerificationSpec,
    WorkloadPredicate,
    verify_environment,
)


def _mock_kubectl_success(items: list[dict]):
    def _kubectl_get(resource: str, namespace: str = "-A", output: str = "json"):
        return {
            "success": True,
            "stdout": json.dumps({"items": items}),
            "parsed": {"items": items},
            "returncode": 0,
        }
    return _kubectl_get


def _mock_kubectl_failure(error_msg: str = "cluster unreachable"):
    def _kubectl_get(resource: str, namespace: str = "-A", output: str = "json"):
        return {
            "success": False,
            "error": error_msg,
            "stderr": error_msg,
            "returncode": 1,
        }
    return _kubectl_get


def _mock_alertmanager_success(alerts: list[dict]):
    def _alert_list(active_only: bool = True):
        return {
            "success": True,
            "count": len(alerts),
            "alerts": alerts,
        }
    return _alert_list


def _mock_alertmanager_failure(error_msg: str = "alertmanager connection refused"):
    def _alert_list(active_only: bool = True):
        return {
            "success": False,
            "error": error_msg,
        }
    return _alert_list


def _mock_promql_success(result_value: float = 0.0):
    def _promql_query(query: str, time_unix=None):
        return {
            "success": True,
            "result": [{"metric": {"__name__": "metric"}, "value": [1786542619, str(result_value)]}],
            "resultType": "vector",
        }
    return _promql_query


def _mock_promql_failure(error_msg: str = "prometheus timeout"):
    def _promql_query(query: str, time_unix=None):
        return {
            "success": False,
            "error": error_msg,
        }
    return _promql_query


def test_resolved_environment_all_checks_pass():
    """Case 1: Ground-truth healthy cluster with all predicates satisfied."""
    deployment_item = {
        "metadata": {"name": "frontend", "namespace": "default"},
        "spec": {"replicas": 1},
        "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
    }
    verifier = EnvironmentVerifier(
        kubectl_getter=_mock_kubectl_success([deployment_item]),
        alert_lister=_mock_alertmanager_success([]),
        promql_querier=_mock_promql_success(0.001),
    )

    result = verifier.verify(
        scenario_id="single_fault/sf-001",
        agent_claimed_resolved=True,
    )

    assert result.env_resolved is True
    assert result.agent_claimed_resolved is True
    assert result.verification_status == "passed"
    assert result.is_false_resolution is False
    assert result.is_false_negative is False
    assert len(result.failed_checks) == 0
    assert len(result.checks) >= 3


def test_unresolved_environment_workload_failing():
    """Case 2: Workload has zero ready replicas."""
    deployment_item = {
        "metadata": {"name": "frontend", "namespace": "default"},
        "spec": {"replicas": 1},
        "status": {"replicas": 1, "readyReplicas": 0, "availableReplicas": 0},
    }
    verifier = EnvironmentVerifier(
        kubectl_getter=_mock_kubectl_success([deployment_item]),
        alert_lister=_mock_alertmanager_success([]),
        promql_querier=_mock_promql_success(0.0),
    )

    result = verifier.verify(
        scenario_id="single_fault/sf-001",
        agent_claimed_resolved=False,
    )

    assert result.env_resolved is False
    assert result.agent_claimed_resolved is False
    assert result.verification_status == "failed"
    assert result.is_false_resolution is False
    assert "workload_frontend_ready" in result.failed_checks


def test_false_resolution_detection():
    """Case 3: Agent falsely claims resolved, but environment is broken."""
    deployment_item = {
        "metadata": {"name": "cartservice", "namespace": "default"},
        "spec": {"replicas": 1},
        "status": {"replicas": 1, "readyReplicas": 0},
    }
    verifier = EnvironmentVerifier(
        kubectl_getter=_mock_kubectl_success([deployment_item]),
        alert_lister=_mock_alertmanager_success([]),
        promql_querier=_mock_promql_success(0.0),
    )

    result = verifier.verify(
        scenario_id="single_fault/sf-004",
        agent_claimed_resolved=True,  # Agent hallucinated or falsely claimed victory
    )

    assert result.env_resolved is False
    assert result.agent_claimed_resolved is True
    assert result.is_false_resolution is True
    assert result.is_false_negative is False
    assert result.verification_status == "failed"


def test_false_negative_detection():
    """Case 4: Agent claimed unresolved/escalated, but environment is actually healthy."""
    deployment_item = {
        "metadata": {"name": "checkoutservice", "namespace": "default"},
        "spec": {"replicas": 1},
        "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
    }
    verifier = EnvironmentVerifier(
        kubectl_getter=_mock_kubectl_success([deployment_item]),
        alert_lister=_mock_alertmanager_success([]),
        promql_querier=_mock_promql_success(0.0),
    )

    result = verifier.verify(
        scenario_id="single_fault/sf-008",
        agent_claimed_resolved=False,
    )

    assert result.env_resolved is True
    assert result.agent_claimed_resolved is False
    assert result.is_false_negative is True
    assert result.is_false_resolution is False
    assert result.verification_status == "passed"


def test_alert_still_firing_causes_failure():
    """Case 5: Workload is ready, but targeted alert is still actively firing."""
    deployment_item = {
        "metadata": {"name": "frontend", "namespace": "default"},
        "spec": {"replicas": 1},
        "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
    }
    firing_alerts = [
        {"alertname": "FrontendCrashLooping", "status": "active", "namespace": "default"}
    ]
    verifier = EnvironmentVerifier(
        kubectl_getter=_mock_kubectl_success([deployment_item]),
        alert_lister=_mock_alertmanager_success(firing_alerts),
        promql_querier=_mock_promql_success(0.0),
    )

    result = verifier.verify(
        scenario_id="single_fault/sf-001",
        agent_claimed_resolved=True,
    )

    assert result.env_resolved is False
    assert result.is_false_resolution is True
    assert "alerts_cleared" in result.failed_checks


def test_metric_threshold_breach_causes_failure():
    """Case 6: Error rate metric exceeds permitted threshold."""
    deployment_item = {
        "metadata": {"name": "frontend", "namespace": "default"},
        "spec": {"replicas": 1},
        "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
    }
    verifier = EnvironmentVerifier(
        kubectl_getter=_mock_kubectl_success([deployment_item]),
        alert_lister=_mock_alertmanager_success([]),
        promql_querier=_mock_promql_success(0.15),  # 15% error rate > 5% threshold
    )

    result = verifier.verify(
        scenario_id="single_fault/sf-001",
        agent_claimed_resolved=True,
    )

    assert result.env_resolved is False
    assert result.is_false_resolution is True
    assert any(fc.startswith("metric_") for fc in result.failed_checks)


def test_composite_cascade_multi_workload_verification():
    """Case 7: Cascade scenario verifying multiple distinct services."""
    deployments = [
        {"metadata": {"name": "redis-cart", "namespace": "default"}, "status": {"readyReplicas": 1, "replicas": 1}},
        {"metadata": {"name": "cartservice", "namespace": "default"}, "status": {"readyReplicas": 1, "replicas": 1}},
        {"metadata": {"name": "frontend", "namespace": "default"}, "status": {"readyReplicas": 1, "replicas": 1}},
    ]
    verifier = EnvironmentVerifier(
        kubectl_getter=_mock_kubectl_success(deployments),
        alert_lister=_mock_alertmanager_success([]),
        promql_querier=_mock_promql_success(0.0),
    )

    result = verifier.verify(
        scenario_id="cascade/cs-001",
        agent_claimed_resolved=True,
    )

    assert result.env_resolved is True
    assert result.verification_status == "passed"
    workload_checks = [c for c in result.checks if c.name.startswith("workload_")]
    assert len(workload_checks) == 3


def test_missing_backend_inconclusive_handling():
    """Case 8: All telemetry backends fail closed (inconclusive status)."""
    verifier = EnvironmentVerifier(
        kubectl_getter=_mock_kubectl_failure("cluster unreachable"),
        alert_lister=_mock_alertmanager_failure("alertmanager down"),
        promql_querier=_mock_promql_failure("prometheus down"),
    )

    result = verifier.verify(
        scenario_id="single_fault/sf-001",
        agent_claimed_resolved=True,
    )

    assert result.env_resolved is False
    assert result.verification_status == "inconclusive"
    assert result.error == "environment_telemetry_unreachable"


def test_dynamic_adversarial_scenario_synthesis():
    """Case 9: Dynamically generated adversarial scenario synthesizing predicates from alert payload."""
    alert_payload = {
        "commonLabels": {
            "alertname": "AdversarialPaymentDrop",
            "service": "paymentservice",
            "namespace": "default",
        },
        "alerts": [
            {"labels": {"alertname": "AdversarialPaymentDrop", "service": "paymentservice", "namespace": "default"}}
        ],
    }
    deployment_item = {
        "metadata": {"name": "paymentservice", "namespace": "default"},
        "status": {"readyReplicas": 1, "replicas": 1},
    }
    verifier = EnvironmentVerifier(
        kubectl_getter=_mock_kubectl_success([deployment_item]),
        alert_lister=_mock_alertmanager_success([]),
        promql_querier=_mock_promql_success(0.0),
    )

    result = verifier.verify(
        scenario_id="adversarial/adv-synthetic-042",
        agent_claimed_resolved=True,
        alert=alert_payload,
    )

    assert result.env_resolved is True
    assert result.verification_status == "passed"
    assert any("paymentservice" in c.target for c in result.checks)


def test_deterministic_serialization_to_dict():
    """Case 10: Serialization contract produces typed JSON-compatible dictionary."""
    res = EnvironmentVerificationResult(
        scenario_id="single_fault/sf-001",
        agent_claimed_resolved=True,
        env_resolved=True,
        verification_status="passed",
        checks=[CheckResult(name="workload_frontend_ready", target="default/frontend", passed=True, details="ok")],
        failed_checks=[],
        observed_metrics={"http_5xx_rate": 0.001},
        evidence=["Workload default/frontend ready"],
    )

    d = res.to_dict()
    assert d["scenario_id"] == "single_fault/sf-001"
    assert d["agent_claimed_resolved"] is True
    assert d["env_resolved"] is True
    assert d["verification_status"] == "passed"
    assert d["is_false_resolution"] is False
    assert len(d["checks"]) == 1
    assert d["observed_metrics"]["http_5xx_rate"] == 0.001

    # Verify JSON serializability
    encoded = json.dumps(d)
    assert isinstance(encoded, str)
    decoded = json.loads(encoded)
    assert decoded["scenario_id"] == "single_fault/sf-001"
