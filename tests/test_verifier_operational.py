from __future__ import annotations

import time
from typing import Any

import pytest

from agents.tools import alertmanager
from agents.verifier import (
    OPERATIONAL_SAMPLE_MAX_AGE_SECONDS,
    EnvironmentVerifier,
)

ALERT_NAME = "AtlasOpsOnlineBoutiqueDeploymentUnavailable"


@pytest.fixture(autouse=True)
def _use_default_workload_namespace(monkeypatch) -> None:
    monkeypatch.delenv("ATLASOPS_WORKLOAD_NAMESPACE", raising=False)


def _operational_alert(
    common_labels: dict[str, str],
    individual_labels: dict[str, str] | None = None,
) -> dict:
    common = {"alertname": ALERT_NAME, **common_labels}
    individual = {"alertname": ALERT_NAME, **(individual_labels or common_labels)}
    return {"commonLabels": common, "alerts": [{"labels": individual}]}


def _deployment(
    *,
    name: str = "frontend",
    namespace: str = "default",
    api_version: str = "apps/v1",
    kind: str = "Deployment",
    uid: str = "frontend-uid",
    resource_version: str = "42",
    generation: int = 7,
    observed_generation: int = 7,
    desired: int = 2,
    ready: int = 2,
    available: int = 2,
) -> dict:
    return {
        "apiVersion": api_version,
        "kind": kind,
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": uid,
            "resourceVersion": resource_version,
            "generation": generation,
        },
        "spec": {"replicas": desired},
        "status": {
            "observedGeneration": observed_generation,
            "replicas": desired,
            "readyReplicas": ready,
            "availableReplicas": available,
        },
    }


class FakeBackends:
    def __init__(
        self,
        deployment: dict | None = None,
        active_alerts: list[dict] | None = None,
        metric_results: dict[Any, dict] | None = None,
    ) -> None:
        self.deployment = deployment or _deployment()
        self.active_alerts = active_alerts or []
        self.metric_results = metric_results or {}
        self.kubectl_calls: list[tuple[str, str, str]] = []
        self.promql_queries: list[str] = []
        self.promql_times: list[float | None] = []
        self.response_timestamps: list[float] = []
        self.alert_calls: list[bool] = []

    def kubectl(self, resource: str, namespace: str = "-A", output: str = "json") -> dict:
        self.kubectl_calls.append((resource, namespace, output))
        if resource.lower() in ("deployment", "deployments") or resource.lower().startswith(
            "deployment/"
        ):
            return {
                "success": True,
                "parsed": self.deployment,
            }
        if any(kind in resource.lower() for kind in (
            "podchaos",
            "networkchaos",
            "stresschaos",
            "dnschaos",
            "iochaos",
            "timechaos",
        )):
            return {"success": True, "parsed": {"items": []}}
        return {"success": False, "error": f"Unexpected Kubernetes query: {resource}"}

    def prometheus(self, query: str, time_unix=None) -> dict:
        self.promql_queries.append(query)
        self.promql_times.append(time_unix)
        is_age_query = query.startswith(
            "max by (namespace, deployment) (time() - timestamp("
        )
        if "kube_deployment_spec_replicas" in query:
            metric = "kube_deployment_spec_replicas"
            default_value = (
                1.0
                if is_age_query
                else self.deployment.get("spec", {}).get("replicas", 2)
            )
        else:
            metric = "kube_deployment_status_replicas_available"
            default_value = (
                1.0
                if is_age_query
                else self.deployment.get("status", {}).get("availableReplicas", 2)
            )
        response_key = (metric, "age" if is_age_query else "value")
        if response_key in self.metric_results:
            response = self.metric_results[response_key]
            return response(query, time_unix) if callable(response) else response
        if not is_age_query and metric in self.metric_results:
            response = self.metric_results[metric]
            return response(query, time_unix) if callable(response) else response
        evaluation_timestamp = (
            float(time_unix) if time_unix is not None else time.time()
        )
        self.response_timestamps.append(evaluation_timestamp)
        return {
            "success": True,
            "resultType": "vector",
            "result": [
                {
                    "metric": {
                        "namespace": self.deployment["metadata"]["namespace"],
                        "deployment": self.deployment["metadata"]["name"],
                    },
                    "value": [str(evaluation_timestamp), str(default_value)],
                }
            ],
        }

    def alertmanager(self, active_only: bool = True) -> dict:
        self.alert_calls.append(active_only)
        return {"success": True, "alerts": self.active_alerts}

    def bound_deployment(self, target) -> dict:
        return self.kubectl(
            f"deployment/{target.deployment}", namespace=target.namespace, output="json",
        )

    def verifier(self) -> EnvironmentVerifier:
        return EnvironmentVerifier(
            kubectl_getter=self.kubectl,
            alert_lister=self.alertmanager,
            promql_querier=self.prometheus,
            bound_deployment_getter=self.bound_deployment,
        )


def _valid_payload(deployment: str = "frontend", namespace: str = "default") -> dict:
    labels = {"deployment": deployment, "namespace": namespace}
    return _operational_alert(labels)


def _prometheus_vector(
    value: Any,
    evaluation_timestamp: float,
    *,
    deployment: str = "frontend",
    namespace: str = "default",
) -> dict:
    return {
        "success": True,
        "resultType": "vector",
        "result": [
            {
                "metric": {"namespace": namespace, "deployment": deployment},
                "value": [str(evaluation_timestamp), str(value)],
            }
        ],
    }


def _prometheus_at_query_time(value: Any):
    def response(query: str, time_unix: float | None) -> dict:
        assert time_unix is not None
        return _prometheus_vector(value, time_unix)

    return response


def _operational_context(
    deployment: str = "frontend",
    uid: str = "frontend-uid",
    namespace: str = "default",
) -> dict:
    arguments = {
        "deployment": deployment,
        "replicas": 3,
        "namespace": namespace,
    }
    target = {
        "server": "https://kubernetes.example.invalid",
        "context": "kind-atlasops-local",
        "api_version": "apps/v1",
        "kind": "Deployment",
        "namespace": namespace,
        "name": deployment,
        "uid": uid,
        "resource_version": "41",
    }
    prepared_action = {
        "tool": "kubectl_scale",
        "arguments": dict(arguments),
        "target": dict(target),
        "patch": [{"op": "replace", "path": "/spec/replicas", "value": 3}],
    }
    action = {
        "step": 1,
        "tool": "kubectl_scale",
        "args": dict(arguments),
        "output": {
            "success": True,
            "target": dict(target),
            "atomic_preconditions": True,
        },
        "success": True,
        "prepared_action": prepared_action,
    }
    return {
        "operational_mode": True,
        "action": action,
    }


def _operational_final_context() -> dict:
    action = _operational_context()["action"]
    return {
        "operational_mode": True,
        "remediation": {"final": {"executed_actions": [action]}},
    }


def _non_deployment_context(tool: str, namespace: str = "default") -> dict:
    context = _operational_context(namespace=namespace)
    action = context["action"]
    verification_target = action["prepared_action"]["target"]
    if tool == "chaos_stop_experiment":
        arguments = {
            "kind": "PodChaos",
            "name": "frontend-fault",
            "namespace": "chaos-mesh",
        }
        mutation_target = {
            **verification_target,
            "api_version": "chaos-mesh.org/v1alpha1",
            "kind": "PodChaos",
            "namespace": "chaos-mesh",
            "name": "frontend-fault",
            "uid": "frontend-fault-uid",
            "resource_version": "47",
        }
    elif tool == "argocd_rollback":
        arguments = {"app": "atlasops-boutique", "revision": "17"}
        mutation_target = {
            **verification_target,
            "api_version": "argoproj.io/v1alpha1",
            "kind": "Application",
            "namespace": "argocd",
            "name": "atlasops-boutique",
            "uid": "atlasops-app-uid",
            "resource_version": "48",
        }
    else:
        raise AssertionError(f"Unsupported test action: {tool}")

    action["tool"] = tool
    action["args"] = dict(arguments)
    action["output"]["target"] = dict(mutation_target)
    action["prepared_action"] = {
        "tool": tool,
        "arguments": dict(arguments),
        "target": mutation_target,
        "verification_target": dict(verification_target),
        "patch": {"prepared": True},
    }
    return context


def test_empty_scenario_verifies_exact_operational_target_with_fresh_metrics():
    backends = FakeBackends(
        active_alerts=[
            {
                "alertname": ALERT_NAME,
                "namespace": "default",
                "deployment": "cartservice",
                "status": "active",
            }
        ]
    )

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload("frontend"),
    )

    assert result.scenario_id == ""
    assert result.env_resolved is True
    assert result.verification_status == "passed"
    assert backends.kubectl_calls == [("deployment/frontend", "default", "json")]
    assert backends.alert_calls == [True]
    assert len(backends.promql_queries) == 4
    assert backends.promql_times[0] == backends.promql_times[1]
    assert backends.promql_times[2] == backends.promql_times[3]
    assert result.verification_timestamp >= max(backends.response_timestamps)
    desired_sample = next(
        check
        for check in result.checks
        if check.name == "operational_metric_kube_deployment_spec_replicas_fresh"
    ).observed
    assert desired_sample["source_sample_timestamp"] <= desired_sample["observed_at"]
    assert (
        desired_sample["source_sample_age_at_observation_seconds"]
        <= OPERATIONAL_SAMPLE_MAX_AGE_SECONDS
    )
    for query in backends.promql_queries:
        assert 'namespace="default"' in query
        assert 'deployment="frontend"' in query
        assert "timestamp(" in query
        assert "time() -" in query
        assert ">= 0" in query
        assert "<= 60" in query
        assert "vector(0)" not in query


def test_operational_target_accepts_consistent_service_and_deployment_aliases():
    backends = FakeBackends()
    alert = _operational_alert(
        {"service": "frontend", "namespace": "default"},
        {"deployment": "frontend", "namespace": "default"},
    )

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=alert,
    )

    assert result.env_resolved is True
    assert result.verification_status == "passed"


def test_operational_mode_binds_prepared_action_target_and_current_deployment_uid():
    backends = FakeBackends(deployment=_deployment())
    mutation_returned_at = time.time()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
        incident_context=_operational_context(),
    )

    assert result.env_resolved is True
    assert result.verification_status == "passed"
    assert backends.kubectl_calls == [("deployment/frontend", "default", "json")]
    assert result.verification_timestamp >= mutation_returned_at


def test_operational_mode_refuses_healthy_data_on_a_rebound_kubernetes_server(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from agents.tools import chaos

    backends = FakeBackends()
    client = SimpleNamespace(
        configuration=SimpleNamespace(host="https://different-cluster.invalid"),
        call_api=Mock(), close=Mock(),
    )
    factory = Mock(return_value=(Mock(), client))
    monkeypatch.setattr(chaos, "_new_chaos_api_client", factory)
    verifier = EnvironmentVerifier(
        kubectl_getter=backends.kubectl,
        alert_lister=backends.alertmanager,
        promql_querier=backends.prometheus,
    )
    result = verifier.verify(
        scenario_id="", agent_claimed_resolved=True,
        alert=_valid_payload(), incident_context=_operational_context(),
    )
    assert result.env_resolved is False
    factory.assert_called_once_with("kind-atlasops-local")
    client.call_api.assert_not_called()
    assert backends.kubectl_calls == []


def test_bound_verifier_selects_the_approved_context_and_reads_its_deployment(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from agents.tools import chaos

    monkeypatch.setenv("KUBECONFIG_CONTEXT", "unrelated-current-context")
    backends = FakeBackends()
    client = SimpleNamespace(
        configuration=SimpleNamespace(host="https://kubernetes.example.invalid", retries=1),
        call_api=Mock(return_value=_deployment()), close=Mock(),
    )
    factory = Mock(return_value=(Mock(), client))
    monkeypatch.setattr(chaos, "_new_chaos_api_client", factory)
    verifier = EnvironmentVerifier(
        kubectl_getter=backends.kubectl,
        alert_lister=backends.alertmanager,
        promql_querier=backends.prometheus,
    )
    result = verifier.verify(
        scenario_id="", agent_claimed_resolved=True,
        alert=_valid_payload(), incident_context=_operational_context(),
    )
    assert result.env_resolved is True
    factory.assert_called_once_with("kind-atlasops-local")
    assert client.call_api.call_args.args == (
        "/apis/apps/v1/namespaces/default/deployments/frontend", "GET",
    )
    assert client.call_api.call_args.kwargs["response_type"] == "object"
    assert client.configuration.retries == 0
    assert backends.kubectl_calls == []
    client.close.assert_called_once()


def test_bound_verifier_decodes_real_sdk_response_without_network(monkeypatch):
    import json
    from unittest.mock import Mock

    from kubernetes import client as kubernetes_client
    from kubernetes.client.rest import RESTResponse
    from urllib3.response import HTTPResponse

    from agents.tools import chaos

    monkeypatch.setenv("KUBECONFIG_CONTEXT", "unrelated-current-context")
    backends = FakeBackends()
    configuration = kubernetes_client.Configuration(
        host="https://kubernetes.example.invalid",
    )
    configuration.api_key["authorization"] = "Bearer non-secret-unit-key"
    response = RESTResponse(HTTPResponse(
        body=json.dumps(backends.deployment).encode(), status=200,
        headers={"Content-Type": "application/json"},
    ))
    transport = Mock(return_value=response)
    with kubernetes_client.ApiClient(configuration) as client:
        monkeypatch.setattr(client, "request", transport)
        factory = Mock(return_value=(Mock(), client))
        monkeypatch.setattr(chaos, "_new_chaos_api_client", factory)
        verifier = EnvironmentVerifier(
            kubectl_getter=backends.kubectl,
            alert_lister=backends.alertmanager,
            promql_querier=backends.prometheus,
        )
        result = verifier.verify(
            scenario_id="", agent_claimed_resolved=True,
            alert=_valid_payload(), incident_context=_operational_context(),
        )
        assert result.env_resolved is True
        assert client.configuration.retries == 0

    factory.assert_called_once_with("kind-atlasops-local")
    assert transport.call_args.args == (
        "GET", configuration.host + "/apis/apps/v1/namespaces/default/deployments/frontend",
    )
    sent = transport.call_args.kwargs
    assert sent["_request_timeout"] == 30
    assert sent["headers"]["Accept"] == "application/json"
    assert sent["headers"]["authorization"] == "Bearer non-secret-unit-key"
    assert backends.kubectl_calls == []


@pytest.mark.parametrize("tool", ["chaos_stop_experiment", "argocd_rollback"])
def test_operational_mode_uses_prepared_verification_target_for_non_deployment_actions(
    tool,
    monkeypatch,
):
    namespace = "atlasops-boutique"
    monkeypatch.setenv("ATLASOPS_WORKLOAD_NAMESPACE", namespace)
    backends = FakeBackends(deployment=_deployment(namespace=namespace))

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(namespace=namespace),
        incident_context=_non_deployment_context(tool, namespace=namespace),
    )

    assert result.env_resolved is True
    assert result.verification_status == "passed"
    assert backends.kubectl_calls == [
        ("deployment/frontend", namespace, "json")
    ]
    assert all(f'namespace="{namespace}"' in query for query in backends.promql_queries)


def test_operational_mode_rejects_non_deployment_action_without_verification_target():
    context = _non_deployment_context("chaos_stop_experiment")
    del context["action"]["prepared_action"]["verification_target"]
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
        incident_context=context,
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert backends.kubectl_calls == []


@pytest.mark.parametrize("field", ["server", "context"])
def test_operational_mode_rejects_verification_target_from_another_cluster(field):
    context = _non_deployment_context("argocd_rollback")
    context["action"]["prepared_action"]["verification_target"][field] = "other-cluster"
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
        incident_context=context,
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert backends.kubectl_calls == []


def test_operational_mode_rejects_verification_target_that_differs_from_alert():
    context = _non_deployment_context("chaos_stop_experiment")
    context["action"]["prepared_action"]["verification_target"]["name"] = "cartservice"
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
        incident_context=context,
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert backends.kubectl_calls == []


def test_operational_mode_does_not_fall_back_from_explicit_malformed_verification_target():
    context = _operational_context()
    context["action"]["prepared_action"]["verification_target"] = None
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
        incident_context=context,
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert backends.kubectl_calls == []


def test_operational_final_verification_reads_prepared_action_from_remediation_record():
    backends = FakeBackends(deployment=_deployment())

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
        incident_context=_operational_final_context(),
    )

    assert result.env_resolved is True
    assert result.verification_status == "passed"


@pytest.mark.parametrize(
    "context",
    [
        {"operational_mode": True},
        _operational_context(deployment="cartservice"),
    ],
)
def test_operational_mode_rejects_missing_or_conflicting_action_binding(context):
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
        incident_context=context,
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert backends.kubectl_calls == []
    assert backends.promql_queries == []


def test_operational_mode_rejects_action_record_that_differs_from_prepared_arguments():
    context = _operational_context()
    context["action"]["args"]["deployment"] = "cartservice"
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
        incident_context=context,
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert backends.kubectl_calls == []


def test_operational_mode_rejects_unknown_policy_without_dynamic_fallback():
    unknown_alert = _operational_alert(
        {"deployment": "frontend", "namespace": "default"}
    )
    unknown_alert["commonLabels"]["alertname"] = "UnsupportedDeploymentAlarm"
    unknown_alert["alerts"][0]["labels"]["alertname"] = "UnsupportedDeploymentAlarm"
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=unknown_alert,
        incident_context={"operational_mode": True},
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "unknown_operational_policy"
    assert backends.kubectl_calls == []
    assert backends.promql_queries == []
    assert backends.alert_calls == []


@pytest.mark.parametrize(
    "alert",
    [
        {
            "commonLabels": {
                "alertname": ALERT_NAME,
                "deployment": "frontend",
                "namespace": "default",
            },
            "alerts": [],
        },
        {
            "commonLabels": {
                "alertname": ALERT_NAME,
                "deployment": "frontend",
                "namespace": "default",
            },
            "alerts": None,
        },
        {
            "commonLabels": {
                "alertname": ALERT_NAME,
                "deployment": "frontend",
                "namespace": "default",
            },
            "alerts": [{"labels": None}],
        },
    ],
)
def test_operational_target_rejects_empty_or_malformed_alert_payloads(alert):
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=alert,
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert backends.kubectl_calls == []
    assert backends.promql_queries == []


def test_operational_mode_rejects_a_replaced_deployment_uid():
    backends = FakeBackends(
        deployment=_deployment(uid="replacement-uid"),
    )

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
        incident_context=_operational_context(),
    )

    assert result.env_resolved is False
    assert result.verification_status == "inconclusive"
    deployment_check = next(
        check for check in result.checks if check.name == "operational_deployment_current"
    )
    assert deployment_check.passed is False


@pytest.mark.parametrize(
    "common,individual",
    [
        (
            {"deployment": "frontend", "namespace": "default"},
            {"deployment": "cartservice", "namespace": "default"},
        ),
        (
            {"deployment": "not-a-service", "namespace": "default"},
            {"deployment": "not-a-service", "namespace": "default"},
        ),
        (
            {"namespace": "default"},
            {"namespace": "default"},
        ),
        (
            {"deployment": "frontend"},
            {"deployment": "frontend"},
        ),
        (
            {"deployment": "frontend", "namespace": "kube-system"},
            {"deployment": "frontend", "namespace": "kube-system"},
        ),
        (
            {
                "deployment": "frontend",
                "service": "cartservice",
                "namespace": "default",
            },
            {
                "deployment": "frontend",
                "service": "cartservice",
                "namespace": "default",
            },
        ),
    ],
)
def test_operational_target_rejects_missing_unknown_or_conflicting_labels(common, individual):
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_operational_alert(common, individual),
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert result.is_false_resolution is True
    assert backends.kubectl_calls == []
    assert backends.promql_queries == []
    assert backends.alert_calls == []


def test_operational_target_rejects_multiple_individual_deployments():
    backends = FakeBackends()
    alert = {
        "commonLabels": {
            "alertname": ALERT_NAME,
            "namespace": "default",
        },
        "alerts": [
            {"labels": {"deployment": "frontend", "namespace": "default"}},
            {"labels": {"deployment": "cartservice", "namespace": "default"}},
        ],
    }

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=alert,
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert backends.kubectl_calls == []
    assert backends.promql_queries == []


def test_operational_target_rejects_malformed_common_labels():
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert={
            "commonLabels": [],
            "alerts": [
                {
                    "labels": {
                        "alertname": ALERT_NAME,
                        "deployment": "frontend",
                        "namespace": "default",
                    }
                }
            ],
        },
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert backends.kubectl_calls == []


def test_operational_target_alert_still_firing_cannot_be_cleared_by_silence():
    backends = FakeBackends(
        active_alerts=[
            {
                "alertname": ALERT_NAME,
                "namespace": "default",
                "deployment": "frontend",
                "status": "suppressed",
            }
        ]
    )

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    assert result.verification_status == "failed"
    alert_check = next(check for check in result.checks if check.name == "operational_alert_cleared")
    assert alert_check.passed is False
    assert "still present" in alert_check.details


@pytest.mark.parametrize(
    "active_alert",
    [
        {"alertname": ALERT_NAME, "namespace": "default", "deployment": "not-a-service"},
        {"alertname": ALERT_NAME, "namespace": "kube-system", "deployment": "frontend"},
        {"alertname": ALERT_NAME, "namespace": "default"},
    ],
)
def test_operational_alert_clearance_is_inconclusive_for_unknown_scope(active_alert):
    backends = FakeBackends(active_alerts=[active_alert])

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    assert result.verification_status == "inconclusive"
    alert_check = next(
        check for check in result.checks if check.name == "operational_alert_cleared"
    )
    assert alert_check.passed is False
    assert "could not be scoped" in alert_check.details


@pytest.mark.parametrize("failure", ["response", "exception", "malformed"])
def test_operational_alertmanager_api_failures_are_inconclusive(failure):
    backends = FakeBackends()

    def alert_lister(active_only: bool = True) -> dict:
        if failure == "exception":
            raise OSError("Alertmanager unavailable")
        if failure == "response":
            return {"success": False, "error": "Alertmanager unavailable"}
        return {"success": True, "alerts": None}

    verifier = EnvironmentVerifier(
        kubectl_getter=backends.kubectl,
        alert_lister=alert_lister,
        promql_querier=backends.prometheus,
    )
    result = verifier.verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    assert result.verification_status == "inconclusive"
    alert_check = next(
        check for check in result.checks if check.name == "operational_alert_cleared"
    )
    assert alert_check.passed is False


def test_operational_kubernetes_api_failure_is_inconclusive():
    backends = FakeBackends()
    verifier = EnvironmentVerifier(
        kubectl_getter=lambda *args, **kwargs: {
            "success": False,
            "error": "Kubernetes API unavailable",
        },
        alert_lister=backends.alertmanager,
        promql_querier=backends.prometheus,
    )

    result = verifier.verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    assert result.verification_status == "inconclusive"


@pytest.mark.parametrize(
    "response",
    [
        {"success": True, "resultType": "vector", "result": []},
        {
            "success": True,
            "resultType": "vector",
            "result": [
                {
                    "metric": {"namespace": "default", "deployment": "frontend"},
                    "value": [str(time.time()), "NaN"],
                }
            ],
        },
        {
            "success": True,
            "resultType": "vector",
            "result": [
                {
                    "metric": {"namespace": "default", "deployment": "checkoutservice"},
                    "value": [str(time.time()), "2"],
                }
            ],
        },
        {
            "success": True,
            "resultType": "scalar",
            "result": [
                {
                    "metric": {"namespace": "default", "deployment": "frontend"},
                    "value": [str(time.time()), "2"],
                }
            ],
        },
    ],
)
@pytest.mark.parametrize(
    "metric",
    [
        "kube_deployment_spec_replicas",
        "kube_deployment_status_replicas_available",
    ],
)
def test_operational_verification_rejects_missing_malformed_or_mismatched_metric_samples(
    metric, response
):
    backends = FakeBackends(metric_results={metric: response})

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=False,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    assert result.verification_status == "inconclusive"
    assert result.is_false_negative is False
    assert any("timestamp(" in query for query in backends.promql_queries)


@pytest.mark.parametrize(
    "source_age",
    [-1, OPERATIONAL_SAMPLE_MAX_AGE_SECONDS + 1],
)
def test_operational_verification_rejects_future_or_stale_source_metric_samples(
    source_age,
):
    backends = FakeBackends(
        metric_results={
            ("kube_deployment_spec_replicas", "age"): _prometheus_at_query_time(
                source_age
            )
        }
    )

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=False,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    assert result.verification_status == "inconclusive"
    desired_check = next(
        check
        for check in result.checks
        if check.name == "operational_metric_kube_deployment_spec_replicas_fresh"
    )
    assert desired_check.passed is False


@pytest.mark.parametrize("evaluation_offset", [-61, 1])
def test_operational_verification_rejects_stale_or_future_query_results(
    evaluation_offset,
):
    metric_results = {}
    for metric in (
        "kube_deployment_spec_replicas",
        "kube_deployment_status_replicas_available",
    ):
        metric_results[(metric, "value")] = (
            lambda query, time_unix, offset=evaluation_offset: _prometheus_vector(
                2,
                time_unix + offset,
            )
        )
        metric_results[(metric, "age")] = (
            lambda query, time_unix, offset=evaluation_offset: _prometheus_vector(
                1,
                time_unix + offset,
            )
        )
    backends = FakeBackends(metric_results=metric_results)

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=False,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    assert result.verification_status == "inconclusive"


def test_operational_verification_treats_prometheus_api_failure_as_inconclusive():
    backends = FakeBackends(
        metric_results={
            ("kube_deployment_spec_replicas", "value"): {
                "success": False,
                "error": "Prometheus unavailable",
            }
        }
    )

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=False,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    assert result.verification_status == "inconclusive"
    assert result.error == "operational_telemetry_inconclusive"


@pytest.mark.parametrize(
    "mutation",
    [
        "object_api_version",
        "object_kind",
        "metadata_name",
        "metadata_namespace",
        "metadata_uid",
        "resource_version",
    ],
)
def test_operational_verification_rejects_contradictory_or_incomplete_deployment_identity(
    mutation,
):
    backends = FakeBackends(deployment=_deployment())

    def kubectl(resource: str, namespace: str = "-A", output: str = "json") -> dict:
        response = backends.kubectl(resource, namespace, output)
        if resource.lower() in ("deployment", "deployments") or resource.lower().startswith(
            "deployment/"
        ):
            parsed = response["parsed"]
            if mutation == "object_api_version":
                parsed["apiVersion"] = "v1"
            elif mutation == "object_kind":
                parsed["kind"] = "ReplicaSet"
            elif mutation == "metadata_name":
                parsed["metadata"]["name"] = "cartservice"
            elif mutation == "metadata_namespace":
                parsed["metadata"]["namespace"] = "kube-system"
            elif mutation == "metadata_uid":
                parsed["metadata"].pop("uid")
            elif mutation == "resource_version":
                parsed["metadata"].pop("resourceVersion")
        return response

    verifier = EnvironmentVerifier(
        kubectl_getter=kubectl,
        alert_lister=backends.alertmanager,
        promql_querier=backends.prometheus,
    )

    result = verifier.verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    check = next(
        check for check in result.checks if check.name == "operational_deployment_current"
    )
    assert check.passed is False
    assert result.verification_status in {"failed", "inconclusive"}


@pytest.mark.parametrize(
    "deployment",
    [
        _deployment(generation=8, observed_generation=7),
        _deployment(
            generation=8,
            desired=0,
            ready=0,
            available=0,
        ),
        _deployment(generation=8, ready=1, available=1),
        {
            **_deployment(generation=8),
            "status": {
                "observedGeneration": "8",
                "replicas": 2,
                "readyReplicas": 2,
                "availableReplicas": 2,
            },
        },
    ],
)
def test_operational_verification_requires_current_nonzero_ready_deployment(deployment):
    backends = FakeBackends(deployment=deployment)

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    deployment_check = next(
        check for check in result.checks if check.name == "operational_deployment_current"
    )
    assert deployment_check.passed is False


def test_explicit_frozen_scenario_id_keeps_the_frozen_spec_channel(monkeypatch):
    monkeypatch.setenv("ATLASOPS_WORKLOAD_NAMESPACE", "atlasops-boutique")
    cartservice = _deployment(
        name="cartservice",
        uid="cartservice-uid",
        desired=1,
        ready=1,
        available=1,
    )
    backends = FakeBackends(deployment=cartservice)

    result = backends.verifier().verify(
        scenario_id="single_fault/sf-001",
        agent_claimed_resolved=True,
        alert=_valid_payload("frontend"),
    )

    assert result.scenario_id == "single_fault/sf-001"
    assert result.env_resolved is True
    assert backends.promql_queries == []


def test_operational_policy_uses_the_configured_workload_namespace(monkeypatch):
    monkeypatch.setenv("ATLASOPS_WORKLOAD_NAMESPACE", "atlasops-boutique")
    backends = FakeBackends(
        deployment=_deployment(namespace="atlasops-boutique"),
    )

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(namespace="atlasops-boutique"),
        incident_context=_operational_context(namespace="atlasops-boutique"),
    )

    assert result.env_resolved is True
    assert backends.kubectl_calls == [
        ("deployment/frontend", "atlasops-boutique", "json")
    ]
    assert all('namespace="atlasops-boutique"' in query for query in backends.promql_queries)


@pytest.mark.parametrize(
    "namespace",
    ["", "  ", "AtlasOps", "bad/name", "x" * 64],
)
def test_operational_policy_fails_closed_for_malformed_configured_namespace(
    monkeypatch,
    namespace,
):
    monkeypatch.setenv("ATLASOPS_WORKLOAD_NAMESPACE", namespace)
    backends = FakeBackends()

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=True,
        alert=_valid_payload(),
    )

    assert result.env_resolved is False
    assert result.verification_status == "error"
    assert result.error == "operational_target_unresolved"
    assert backends.kubectl_calls == []
    assert backends.promql_queries == []
    assert backends.alert_calls == []


def test_healthy_environment_is_authoritative_when_agent_claims_unresolved():
    backends = FakeBackends(deployment=_deployment())

    result = backends.verifier().verify(
        scenario_id="",
        agent_claimed_resolved=False,
        alert=_valid_payload(),
        incident_context=_operational_final_context(),
    )

    assert result.env_resolved is True
    assert result.verification_status == "passed"
    assert result.is_false_negative is True
    assert result.is_false_resolution is False


def test_alertmanager_projects_deployment_label_for_scoped_clearance(monkeypatch):
    requested = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> list[dict]:
            return [
                {
                    "labels": {
                        "alertname": ALERT_NAME,
                        "deployment": "frontend",
                        "namespace": "default",
                        "severity": "warning",
                    },
                    "status": {"state": "suppressed"},
                    "startsAt": "2026-10-29T00:00:00Z",
                }
            ]

    def get_alerts(*args, **kwargs):
        requested.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(alertmanager.requests, "get", get_alerts)

    result = alertmanager.alertmanager_list_alerts(active_only=True)

    assert result["success"] is True
    assert result["alerts"][0]["deployment"] == "frontend"
    assert result["alerts"][0]["status"] == "suppressed"
    assert requested["params"] == {
        "active": "true",
        "silenced": "true",
        "inhibited": "true",
        "unprocessed": "true",
    }
