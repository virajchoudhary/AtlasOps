import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from kubernetes import client as kubernetes_client
from kubernetes.client.exceptions import ApiException
from urllib3.response import HTTPResponse

from agents import action_execution as actions


@pytest.mark.parametrize("method,status,content_type", [
    ("GET", 200, None),
    ("PATCH", 200, "application/json-patch+json"),
    ("PATCH", 201, "application/json-patch+json"),
    ("DELETE", 200, "application/json"),
    ("DELETE", 202, "application/json"),
])
def test_atomic_request_uses_real_sdk_without_network(
    monkeypatch, method, status, content_type,
):
    resource = {"apiVersion": "v1", "kind": "Status", "status": "Success"}
    response = HTTPResponse(
        body=json.dumps(resource).encode(), status=status,
        headers={"Content-Type": "application/json"},
    )
    transport = Mock(return_value=response)
    configuration = kubernetes_client.Configuration(
        host="https://fixture-kubernetes.invalid",
    )
    configuration.api_key["authorization"] = "Bearer non-secret-unit-key"
    if method == "GET":
        body = None
    elif method == "PATCH":
        body = [{"op": "test", "path": "/metadata/uid", "value": "owned-uid"}]
    else:
        body = {"preconditions": {"uid": "owned-uid"}}
    path = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/atlasops-boutique"
    with kubernetes_client.ApiClient(configuration) as client:
        monkeypatch.setattr(client.rest_client.pool_manager, "request", transport)
        assert actions._request(client, path, method, body=body) == resource

    assert transport.call_args.args == (method, configuration.host + path)
    sent = transport.call_args.kwargs
    assert (json.loads(sent["body"]) if sent.get("body") else None) == body
    assert sent["timeout"].total == 30
    assert sent["headers"]["Accept"] == "application/json"
    assert sent["headers"]["authorization"] == "Bearer non-secret-unit-key"
    if content_type is None:
        assert sent["headers"].get("Content-Type") in {None, "application/json"}
    else:
        assert sent["headers"].get("Content-Type") == content_type


def test_atomic_request_real_sdk_conflict_is_terminal_without_retry(monkeypatch):
    response = HTTPResponse(
        body=b'{"apiVersion":"v1","kind":"Status","status":"Failure"}', status=409,
        headers={"Content-Type": "application/json"},
    )
    transport = Mock(return_value=response)
    configuration = kubernetes_client.Configuration(
        host="https://fixture-kubernetes.invalid",
    )
    configuration.retries = 0
    with kubernetes_client.ApiClient(configuration) as client:
        monkeypatch.setattr(client.rest_client.pool_manager, "request", transport)
        with pytest.raises(ApiException) as error:
            actions._request(
                client, "/api/v1/namespaces/default/pods/owned", "PATCH",
                body=[{"op": "test", "path": "/metadata/uid", "value": "owned-uid"}],
            )
    assert error.value.status == 409
    assert transport.call_count == 1


def test_atomic_request_preserves_legacy_sdk_response_contract():
    resource = {"apiVersion": "v1", "kind": "Status", "status": "Success"}
    calls = []

    def legacy_call_api(
        path, method, *, header_params, body, auth_settings, response_type,
        _return_http_data_only, _request_timeout,
    ):
        calls.append((path, method, header_params, body, auth_settings))
        assert response_type == "object"
        assert _return_http_data_only is True
        assert _request_timeout == 30
        return resource

    client = SimpleNamespace(call_api=legacy_call_api)
    assert actions._request(client, "/api/v1/pods") == resource
    assert calls == [(
        "/api/v1/pods", "GET", {"Accept": "application/json"}, None, ["BearerToken"],
    )]


@pytest.fixture
def runtime(monkeypatch):
    resource = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {
            "namespace": "default", "name": "frontend", "uid": "frontend-uid",
            "resourceVersion": "42", "generation": 7,
            "annotations": {"deployment.kubernetes.io/revision": "3"},
        },
        "spec": {"replicas": 1, "template": {"metadata": {"labels": {"app": "frontend"}}, "spec": {"containers": [{"name": "server", "image": "safe:v3"}]}}},
        "status": {"availableReplicas": 0, "observedGeneration": 7},
    }
    client = SimpleNamespace(
        configuration=SimpleNamespace(host="https://fixture-kubernetes.invalid", retries=None),
        call_api=Mock(return_value=resource), close=Mock(),
    )
    monkeypatch.setenv("KUBECONFIG_CONTEXT", "fixture-kind")
    monkeypatch.setattr(actions, "_new_chaos_api_client", Mock(return_value=(Mock(), client)))
    return resource, client


def test_scale_binds_immutable_resource_and_patch_before_atomic_execution(runtime):
    resource, client = runtime
    arguments = {"deployment": "frontend", "namespace": "default", "replicas": 2}
    action = actions.prepare_action("kubectl_scale", arguments)
    snapshot = action.approval_action
    arguments["replicas"] = 8
    resource["metadata"]["resourceVersion"] = "43"
    assert action.approval_action == snapshot
    assert action.target.resource_version == "42"
    result = actions.execute_prepared_action(action)
    assert result["success"] is True
    path, method = client.call_api.call_args.args
    assert path == "/apis/apps/v1/namespaces/default/deployments/frontend"
    assert method == "PATCH"
    assert client.call_api.call_args.kwargs["body"] == [
        {"op": "test", "path": "/metadata/uid", "value": "frontend-uid"},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "42"},
        {"op": "replace", "path": "/spec/replicas", "value": 2},
    ]
    assert client.call_api.call_args.kwargs["header_params"]["Content-Type"] == "application/json-patch+json"
    assert client.configuration.retries == 0


@pytest.mark.parametrize("status", [404, 409, 422])
def test_stale_target_conflict_never_retries_or_rebinds(runtime, status):
    _resource, client = runtime
    action = actions.prepare_action("kubectl_scale", {"deployment": "frontend", "replicas": 2})

    class Conflict(Exception):
        pass

    error = Conflict("credential=must-not-leak")
    error.status = status
    client.call_api.side_effect = error
    result = actions.execute_prepared_action(action)
    assert result["success"] is False
    assert result["error_class"] == "target_conflict"
    assert "must-not-leak" not in str(result)
    assert client.call_api.call_count == 2


def test_changed_server_blocks_before_any_write(runtime):
    _resource, client = runtime
    action = actions.prepare_action("kubectl_scale", {"deployment": "frontend", "replicas": 2})
    client.configuration.host = "https://different-cluster.invalid"
    result = actions.execute_prepared_action(action)
    assert result["error_class"] == "target_mismatch"
    assert client.call_api.call_count == 1


def test_argo_managed_deployment_cannot_be_patched_directly(runtime):
    resource, client = runtime
    resource["metadata"]["annotations"]["argocd.argoproj.io/tracking-id"] = "owned-app:apps/Deployment:default/frontend"
    with pytest.raises(actions.ActionPreparationError, match="Argo-managed"):
        actions.prepare_action("kubectl_scale", {"deployment": "frontend", "replicas": 2})
    assert [call.args[1] for call in client.call_api.call_args_list] == ["GET"]


def test_deployment_rollback_uses_only_a_prior_owned_replicaset_template(runtime):
    resource, client = runtime
    previous = deepcopy(resource["spec"]["template"])
    previous["spec"]["containers"][0]["image"] = "safe:v2"
    previous["metadata"]["labels"]["pod-template-hash"] = "old-pod-hash"
    client.call_api.side_effect = [
        resource,
        {"items": [
            {"metadata": {"uid": "foreign", "annotations": {"deployment.kubernetes.io/revision": "2"}, "ownerReferences": [{"uid": "foreign-deployment", "controller": True}]}, "spec": {"template": resource["spec"]["template"]}},
            {"metadata": {"uid": "owned-rs", "annotations": {"deployment.kubernetes.io/revision": "2"}, "ownerReferences": [{"uid": "frontend-uid", "controller": True}]}, "spec": {"template": previous}},
        ]},
    ]
    action = actions.prepare_action("kubectl_rollout", {"action": "undo", "resource": "deployment/frontend"})
    patch = action.approval_action["patch"]
    assert patch[-1]["value"]["spec"]["containers"][0]["image"] == "safe:v2"
    assert "pod-template-hash" not in patch[-1]["value"]["metadata"]["labels"]


def test_argo_rollback_is_an_atomic_application_patch(runtime, monkeypatch):
    workload, client = runtime
    monkeypatch.setenv("ATLASOPS_ARGOCD_APP", "atlasops-boutique")
    source = {"repoURL": "https://github.com/virajchoudhary/AtlasOps.git", "path": "infra/local/boutique", "targetRevision": "b" * 40}
    application = {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
        "metadata": {"name": "atlasops-boutique", "namespace": "argocd", "uid": "app-uid", "resourceVersion": "19"},
        "spec": {
            "project": "atlasops-boutique", "source": source,
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "default"},
        },
        "status": {"history": [{"id": 3, "revision": "a" * 40, "source": source}]},
    }
    client.call_api.side_effect = [application, workload, {}]
    alert = {
        "commonLabels": {
            "alertname": "AtlasOpsOnlineBoutiqueDeploymentUnavailable",
            "namespace": "default", "deployment": "frontend",
        },
        "alerts": [{"labels": {
            "alertname": "AtlasOpsOnlineBoutiqueDeploymentUnavailable",
            "namespace": "default", "deployment": "frontend",
        }}],
    }
    action = actions.prepare_action(
        "argocd_rollback", {"app": "atlasops-boutique", "revision": "3"}, alert=alert,
    )
    result = actions.execute_prepared_action(action)
    assert result["success"] is True
    assert client.call_api.call_args.args == ("/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/atlasops-boutique", "PATCH")
    body = client.call_api.call_args.kwargs["body"]
    assert body[0]["value"] == "app-uid"
    assert body[1]["value"] == "19"
    assert body[-2]["value"]["targetRevision"] == "a" * 40
    assert body[-1]["value"]["sync"]["revision"] == "a" * 40


@pytest.mark.parametrize("arguments", [
    {"deployment": "frontend", "namespace": "kube-system", "replicas": 2},
    {"deployment": "atlasops-coordinator", "replicas": 2},
    {"deployment": "frontend", "replicas": 0},
])
def test_operational_scale_rejects_targets_outside_the_workload_contract(runtime, arguments):
    with pytest.raises(actions.ActionPreparationError):
        actions.prepare_action("kubectl_scale", arguments)


def test_unsupported_operational_mutation_does_not_call_a_shell(runtime):
    with pytest.raises(actions.ActionPreparationError, match="No atomic"):
        actions.prepare_action("alertmanager_silence", {})
