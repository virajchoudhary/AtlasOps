from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents.tools import chaos


@pytest.fixture
def runtime(monkeypatch):
    resource = {
        "apiVersion": "chaos-mesh.org/v1alpha1",
        "kind": "StressChaos",
        "metadata": {
            "name": "owned-fault",
            "namespace": "chaos-mesh",
            "uid": "original-uid",
            "resourceVersion": "observed-42",
        },
        "status": {
            "experiment": {"containerRecords": [{"phase": "Injected"}]},
            "conditions": [
                {"type": "Paused", "status": "False"},
                {"type": "AllRecovered", "status": "False"},
            ],
        },
    }
    api = Mock()
    api.get_namespaced_custom_object.return_value = resource
    api.delete_namespaced_custom_object.return_value = {"status": "Success"}
    client = SimpleNamespace(close=Mock())
    factory = Mock(return_value=(api, client))
    shell = Mock(return_value={"success": True, "stdout": "deleted by name"})
    monkeypatch.setattr(chaos, "_new_chaos_api_client", factory)
    monkeypatch.setattr(chaos, "_run", shell)
    monkeypatch.delenv("KUBECONFIG_CONTEXT", raising=False)
    return resource, api, client, factory, shell


def test_default_stop_deletes_only_the_observed_uid_and_version(runtime):
    resource, api, client, factory, shell = runtime
    result = chaos.chaos_stop_experiment("StressChaos", "owned-fault")
    assert result["success"] is True
    assert api.delete_namespaced_custom_object.call_count == 1
    assert api.delete_namespaced_custom_object.call_args.kwargs["body"]["preconditions"] == {
        "uid": resource["metadata"]["uid"],
        "resourceVersion": resource["metadata"]["resourceVersion"],
    }
    assert result["uid"] == "original-uid"
    assert result["resource_version"] == "observed-42"
    factory.assert_called_once_with(None)
    client.close.assert_called_once()
    shell.assert_not_called()


@pytest.mark.parametrize("missing", ["uid", "resourceVersion"])
def test_default_stop_rejects_an_unpinned_resource(runtime, missing):
    resource, api, _client, _factory, shell = runtime
    resource["metadata"].pop(missing)
    result = chaos.chaos_stop_experiment("StressChaos", "owned-fault")
    assert result["success"] is False
    api.delete_namespaced_custom_object.assert_not_called()
    shell.assert_not_called()


def test_default_stop_refuses_unknown_activity(runtime):
    resource, api, _client, _factory, shell = runtime
    resource["status"] = {}
    result = chaos.chaos_stop_experiment("StressChaos", "owned-fault")
    assert result["success"] is False
    api.delete_namespaced_custom_object.assert_not_called()
    shell.assert_not_called()


def test_default_stop_does_not_retry_an_identity_conflict(runtime):
    _resource, api, _client, _factory, shell = runtime

    class Conflict(Exception):
        status = 409
        reason = "Conflict"

    api.delete_namespaced_custom_object.side_effect = Conflict()
    result = chaos.chaos_stop_experiment("StressChaos", "owned-fault")
    assert result["success"] is False
    assert "409" in result["error"]
    api.get_namespaced_custom_object.assert_called_once()
    api.delete_namespaced_custom_object.assert_called_once()
    shell.assert_not_called()


@pytest.mark.parametrize(
    ("key", "value"),
    [("name", "replacement-name"), ("namespace", "different-namespace")],
)
def test_default_stop_refuses_mismatched_api_identity(runtime, key, value):
    resource, api, _client, _factory, shell = runtime
    resource["metadata"][key] = value
    result = chaos.chaos_stop_experiment("StressChaos", "owned-fault")
    assert result["success"] is False
    api.delete_namespaced_custom_object.assert_not_called()
    shell.assert_not_called()


def test_default_stop_keeps_an_explicit_host_context(runtime, monkeypatch):
    _resource, _api, _client, factory, shell = runtime
    monkeypatch.setenv("KUBECONFIG_CONTEXT", "owned-host-context")
    result = chaos.chaos_stop_experiment("StressChaos", "owned-fault")
    assert result["success"] is True
    factory.assert_called_once_with("owned-host-context")
    shell.assert_not_called()


def test_default_stop_reports_read_failure_without_fallback(runtime):
    resource, api, _client, _factory, shell = runtime
    original = deepcopy(resource)
    api.get_namespaced_custom_object.side_effect = RuntimeError("offline")
    result = chaos.chaos_stop_experiment("StressChaos", "owned-fault")
    assert result["success"] is False
    assert resource == original
    api.delete_namespaced_custom_object.assert_not_called()
    shell.assert_not_called()
