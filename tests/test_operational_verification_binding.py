from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents import action_execution as actions
from agents.approval import ApprovalGate


@pytest.fixture
def resources(monkeypatch):
    workload = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "namespace": "atlasops-boutique", "name": "frontend",
            "uid": "workload-uid", "resourceVersion": "42",
        },
        "spec": {"replicas": 1},
    }
    source = {
        "repoURL": "https://github.com/virajchoudhary/AtlasOps.git",
        "path": "infra/local/boutique", "targetRevision": "b" * 40,
    }
    application = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "namespace": "argocd", "name": "atlasops-boutique",
            "uid": "application-uid", "resourceVersion": "19",
        },
        "spec": {
            "project": "atlasops-boutique", "source": source,
            "destination": {
                "server": "https://kubernetes.default.svc", "namespace": "atlasops-boutique",
            },
        },
        "status": {"history": [{"id": 3, "revision": "a" * 40, "source": source}]},
    }
    chaos = {
        "apiVersion": "chaos-mesh.org/v1alpha1",
        "kind": "PodChaos",
        "metadata": {
            "namespace": "chaos-mesh", "name": "fixture-pod-fault",
            "uid": "chaos-uid", "resourceVersion": "7",
        },
        "status": {
            "conditions": [
                {"type": "Paused", "status": "False"},
                {"type": "AllRecovered", "status": "False"},
            ],
            "experiment": {"containerRecords": [{"phase": "Injected"}]},
        },
    }
    documents = {
        "/apis/apps/v1/namespaces/atlasops-boutique/deployments/frontend": workload,
        "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/atlasops-boutique": application,
        "/apis/chaos-mesh.org/v1alpha1/namespaces/chaos-mesh/podchaos/fixture-pod-fault": chaos,
    }

    def request(path, method, **_kwargs):
        assert method == "GET"
        return deepcopy(documents[path])

    client = SimpleNamespace(
        configuration=SimpleNamespace(host="https://fixture-cluster.invalid", retries=0),
        call_api=Mock(side_effect=request), close=Mock(),
    )
    monkeypatch.setenv("KUBECONFIG_CONTEXT", "fixture-kind")
    monkeypatch.setenv("ATLASOPS_WORKLOAD_NAMESPACE", "atlasops-boutique")
    monkeypatch.setattr(actions, "_new_chaos_api_client", Mock(return_value=(Mock(), client)))
    alert = {
        "commonLabels": {
            "alertname": "AtlasOpsOnlineBoutiqueDeploymentUnavailable",
            "namespace": "atlasops-boutique", "deployment": "frontend",
        },
        "alerts": [{"labels": {
            "alertname": "AtlasOpsOnlineBoutiqueDeploymentUnavailable",
            "namespace": "atlasops-boutique", "deployment": "frontend",
        }}],
    }
    return client, workload, alert


@pytest.mark.parametrize("tool,arguments", [
    ("chaos_stop_experiment", {
        "kind": "PodChaos", "name": "fixture-pod-fault", "namespace": "chaos-mesh",
    }),
    ("argocd_rollback", {"app": "atlasops-boutique", "revision": "3"}),
])
def test_external_mutation_binds_alerted_workload_before_approval(resources, tool, arguments):
    client, workload, alert = resources
    action = actions.prepare_action(tool, arguments, alert=alert)
    target = action.approval_action["verification_target"]
    assert target == {
        "server": "https://fixture-cluster.invalid", "context": "fixture-kind",
        "api_version": "apps/v1", "kind": "Deployment",
        "namespace": "atlasops-boutique", "name": "frontend",
        "uid": "workload-uid", "resource_version": "42",
    }
    workload["metadata"]["uid"] = "replacement-uid"
    assert action.approval_action["verification_target"] == target
    assert all(call.args[1] == "GET" for call in client.call_api.call_args_list)
    assert action.target.uid != target["uid"]


@pytest.mark.parametrize("field,value", [
    ("uid", "replacement-uid"), ("namespace", "different-namespace"),
    ("name", "cartservice"), ("context", "different-context"),
])
def test_verification_identity_changes_exact_approval_digest(field, value):
    action = {
        "tool": "chaos_stop_experiment",
        "arguments": {"kind": "PodChaos", "name": "fixture-pod-fault"},
        "target": {"uid": "chaos-uid"},
        "patch": {"preconditions": {"uid": "chaos-uid"}},
        "verification_target": {
            "uid": "workload-uid", "namespace": "atlasops-boutique",
            "name": "frontend", "context": "fixture-kind",
        },
    }
    changed = deepcopy(action)
    changed["verification_target"][field] = value
    assert ApprovalGate.action_digest(action) != ApprovalGate.action_digest(changed)
    gate = ApprovalGate()
    receipt = gate.request_action(incident_id="inc-binding", severity="P1", action=action)
    assert receipt.to_dict()["action"]["verification_target"] == action["verification_target"]


def test_conflicting_alert_workloads_block_preparation_without_mutation(resources):
    client, _workload, alert = resources
    alert["alerts"][0]["labels"]["deployment"] = "cartservice"
    with pytest.raises(actions.ActionPreparationError, match="workload"):
        actions.prepare_action(
            "argocd_rollback", {"app": "atlasops-boutique", "revision": "3"}, alert=alert,
        )
    assert all(call.args[1] == "GET" for call in client.call_api.call_args_list)


def test_missing_alert_cannot_bind_an_application_recovery_target(resources):
    client, _workload, _alert = resources
    with pytest.raises(actions.ActionPreparationError, match="workload"):
        actions.prepare_action(
            "argocd_rollback", {"app": "atlasops-boutique", "revision": "3"},
        )
    assert all(call.args[1] == "GET" for call in client.call_api.call_args_list)


def test_individual_alert_labels_can_supply_the_complete_workload_identity(resources):
    _client, _workload, alert = resources
    alert["commonLabels"] = {}
    action = actions.prepare_action(
        "argocd_rollback", {"app": "atlasops-boutique", "revision": "3"}, alert=alert,
    )
    assert action.approval_action["verification_target"]["name"] == "frontend"


@pytest.mark.parametrize("field,value", [
    ("namespace", "unrelated-workloads"),
    ("server", "https://different-cluster.invalid"),
    ("repoURL", "https://fixture-private@github.com/virajchoudhary/AtlasOps.git"),
    ("project", "unrelated-project"),
    ("path", "infra/kubernetes"),
])
def test_application_ownership_cannot_escape_the_bound_workload(resources, field, value):
    client, _workload, alert = resources
    read = client.call_api.side_effect

    def changed_application(path, method, **kwargs):
        document = read(path, method, **kwargs)
        if document["kind"] == "Application":
            if field == "repoURL":
                document["spec"]["source"][field] = value
                document["status"]["history"][0]["source"][field] = value
            elif field == "path":
                document["spec"]["source"][field] = value
                document["status"]["history"][0]["source"][field] = value
            elif field == "project":
                document["spec"][field] = value
            else:
                document["spec"]["destination"][field] = value
        return document

    client.call_api.side_effect = changed_application
    with pytest.raises(actions.ActionPreparationError, match="owned Application"):
        actions.prepare_action(
            "argocd_rollback", {"app": "atlasops-boutique", "revision": "3"}, alert=alert,
        )
    assert all(call.args[1] == "GET" for call in client.call_api.call_args_list)


def test_unknown_operational_alert_is_blocked_before_any_mutation(resources):
    client, _workload, alert = resources
    alert["commonLabels"]["alertname"] = "UnknownRecoveryPolicy"
    alert["alerts"][0]["labels"]["alertname"] = "UnknownRecoveryPolicy"
    with pytest.raises(actions.ActionPreparationError, match="recovery policy"):
        actions.prepare_action(
            "argocd_rollback", {"app": "atlasops-boutique", "revision": "3"}, alert=alert,
        )
    client.call_api.assert_not_called()


def test_unknown_tool_arguments_cannot_be_silently_ignored(resources):
    client, _workload, alert = resources
    with pytest.raises(actions.ActionPreparationError, match="arguments"):
        actions.prepare_action(
            "argocd_rollback",
            {"app": "atlasops-boutique", "revision": "3", "unexecuted_option": True},
            alert=alert,
        )
    client.call_api.assert_not_called()
