from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents import action_execution as actions


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


@pytest.fixture
def argo_action(runtime, monkeypatch):
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
    client.call_api.side_effect = [application, workload]
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
    acknowledged = deepcopy(application)
    acknowledged["metadata"]["resourceVersion"] = "20"
    acknowledged["spec"]["source"] = action.approval_action["patch"][-2]["value"]
    acknowledged["operation"] = action.approval_action["patch"][-1]["value"]
    client.call_api.side_effect = None
    client.call_api.return_value = acknowledged
    return action, acknowledged, client


def test_argo_rollback_is_an_atomic_application_patch(argo_action):
    action, acknowledged, client = argo_action
    result = actions.execute_prepared_action(action)
    assert result["success"] is True
    assert client.call_api.call_args.args == ("/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/atlasops-boutique", "PATCH")
    body = client.call_api.call_args.kwargs["body"]
    assert body[0]["value"] == "app-uid"
    assert body[1]["value"] == "19"
    assert body[-2]["value"]["targetRevision"] == "a" * 40
    assert body[-1]["value"]["sync"]["revision"] == "a" * 40
    assert body[-1]["value"]["info"][0]["name"] == "atlasops-action-id"
    assert len(body[-1]["value"]["info"][0]["value"]) == 64
    assert result["mutation_receipt"]["target"]["uid"] == "app-uid"
    assert result["mutation_receipt"]["target"]["resource_version"] == "20"
    assert result["mutation_receipt"]["operation"] == acknowledged["operation"]
    assert result["action_digest"] == action.digest


@pytest.mark.parametrize("mismatch", ["uid", "resource_version", "revision", "operation"])
def test_argo_patch_acknowledgement_must_match_the_exact_prepared_target(argo_action, mismatch):
    action, acknowledged, client = argo_action
    if mismatch == "uid":
        acknowledged["metadata"]["uid"] = "other-application"
    elif mismatch == "resource_version":
        acknowledged["metadata"]["resourceVersion"] = action.target.resource_version
    elif mismatch == "revision":
        acknowledged["spec"]["source"] = {
            **acknowledged["spec"]["source"], "targetRevision": "e" * 40,
        }
    else:
        acknowledged["operation"] = {"sync": {"revision": "e" * 40}}
    calls_before = client.call_api.call_count
    result = actions.execute_prepared_action(action)
    assert result["success"] is False
    assert result["error_class"] == "mutation_uncertain"
    assert "mutation_receipt" not in result
    assert client.call_api.call_count == calls_before + 1


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
