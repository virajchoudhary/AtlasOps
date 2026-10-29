from __future__ import annotations

import pytest

from agents.verifier import EnvironmentVerifier, MetricPredicate, WorkloadPredicate


def _verifier_with_prometheus(result):
    return EnvironmentVerifier(
        promql_querier=lambda query, time_unix=None: {
            "success": True,
            "result": result,
            "resultType": "vector",
        }
    )


@pytest.mark.parametrize(
    "result",
    [
        [],
        [{}],
        [{"metric": {}, "value": []}],
        [{"metric": {}, "value": [1]}],
        [{"metric": {}, "value": [1, "not-a-number"]}],
    ],
)
def test_metric_does_not_treat_missing_or_malformed_data_as_zero(result):
    verifier = _verifier_with_prometheus(result)

    check, value = verifier._verify_metric(
        MetricPredicate(query="test_metric", operator="<", threshold=0.5)
    )

    assert check.passed is False
    assert value is None


@pytest.mark.parametrize("value", ["NaN", "+Inf", "-Inf"])
def test_metric_rejects_nonfinite_values(value):
    verifier = _verifier_with_prometheus(
        [{"metric": {}, "value": [1, value]}]
    )

    check, parsed_value = verifier._verify_metric(
        MetricPredicate(query="test_metric", operator="!=", threshold=0.0)
    )

    assert check.passed is False
    assert parsed_value is None


def test_workload_does_not_accept_a_prefix_named_deployment():
    verifier = EnvironmentVerifier(
        kubectl_getter=lambda *args, **kwargs: {
            "success": True,
            "parsed": {
                "items": [
                    {
                        "metadata": {"name": "frontend-abc123", "namespace": "default"},
                        "spec": {"replicas": 1},
                        "status": {"replicas": 1, "readyReplicas": 1},
                    }
                ]
            },
        }
    )

    check = verifier._verify_workload(WorkloadPredicate(name="frontend"))

    assert check.passed is False
    assert "not found" in check.details


def test_workload_does_not_accept_raw_running_text():
    verifier = EnvironmentVerifier(
        kubectl_getter=lambda *args, **kwargs: {
            "success": True,
            "stdout": "frontend 1/1 Running",
        }
    )

    check = verifier._verify_workload(WorkloadPredicate(name="frontend"))

    assert check.passed is False
    assert "parse" in check.details.lower()


@pytest.mark.parametrize(
    "response",
    [
        {"success": True},
        {"success": True, "parsed": "not a Kubernetes object"},
        {"success": True, "parsed": {"items": "not-a-list"}},
        {"success": True, "stdout": "not JSON"},
    ],
)
def test_legacy_deployment_check_does_not_treat_unparseable_data_as_absence(response):
    verifier = EnvironmentVerifier(
        kubectl_getter=lambda *args, **kwargs: response
    )

    check = verifier._verify_no_legacy_deployment("checkoutservice-legacy")

    assert check.passed is False
    assert "absence" in check.details.lower()


@pytest.mark.parametrize(
    "response",
    [
        {"success": True},
        {"success": True, "parsed": "not a Kubernetes object"},
        {"success": True, "parsed": {"items": "not-a-list"}},
        {"success": True, "stdout": "not JSON"},
    ],
)
def test_chaos_clearance_does_not_treat_unparseable_data_as_absence(response):
    verifier = EnvironmentVerifier(
        kubectl_getter=lambda *args, **kwargs: response
    )

    check = verifier._verify_chaos_clearance("single_fault/sf-001")

    assert check.passed is False
    assert "absence" in check.details.lower()


def test_valid_empty_kubernetes_lists_still_prove_resource_absence():
    verifier = EnvironmentVerifier(
        kubectl_getter=lambda *args, **kwargs: {
            "success": True,
            "parsed": {"apiVersion": "v1", "items": []},
        }
    )

    legacy_check = verifier._verify_no_legacy_deployment("checkoutservice-legacy")
    chaos_check = verifier._verify_chaos_clearance("single_fault/sf-001")

    assert legacy_check.passed is True
    assert chaos_check.passed is True
