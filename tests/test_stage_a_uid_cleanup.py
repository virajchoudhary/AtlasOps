"""Regression tests for UID-bound Stage A Chaos cleanup."""

from __future__ import annotations

import inspect
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from agents.tools import chaos


def _active_resource(*, uid: str = "fault-uid", resource_version: str = "42") -> dict:
    return {
        "apiVersion": "chaos-mesh.org/v1alpha1",
        "kind": "StressChaos",
        "metadata": {
            "name": "stage-a-fault",
            "namespace": "chaos-mesh",
            "uid": uid,
            "resourceVersion": resource_version,
        },
        "status": {
            "experiment": {
                "containerRecords": [
                    {"phase": "Injected", "id": "container-1"},
                    {"phase": "Not Injected", "id": "container-2"},
                ]
            },
            "conditions": [
                {"type": "Paused", "status": "False"},
                {"type": "AllRecovered", "status": "False"},
            ],
        },
    }


def _install_fake_kubernetes(monkeypatch, api):
    api_client = SimpleNamespace(closed=False)

    def close():
        api_client.closed = True

    api_client.close = close
    config_calls: list[dict] = []

    def new_client_from_config(**kwargs):
        config_calls.append(kwargs)
        return api_client

    def custom_objects_api(*, api_client):
        assert api_client is not None
        return api

    kubernetes = ModuleType("kubernetes")
    kubernetes.config = SimpleNamespace(
        new_client_from_config=new_client_from_config,
    )
    kubernetes.client = SimpleNamespace(CustomObjectsApi=custom_objects_api)
    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes)
    return config_calls, api_client


def _scope_identity(**overrides) -> dict:
    identity = {
        "kind": "StressChaos",
        "name": "stage-a-fault",
        "namespace": "chaos-mesh",
        "uid": "fault-uid",
        "resource_version": "stale-injection-version",
        "kube_context": "kind-atlasops-local",
    }
    identity.update(overrides)
    return identity


def test_delete_chaos_with_preconditions_sends_uid_and_resource_version(
    monkeypatch,
):
    api = Mock()
    api.delete_namespaced_custom_object.return_value = {"status": "Success"}
    config_calls, api_client = _install_fake_kubernetes(monkeypatch, api)

    result = chaos.delete_chaos_with_preconditions(
        "StressChaos",
        "stage-a-fault",
        "chaos-mesh",
        uid="fault-uid",
        resource_version="42",
        kube_context="kind-atlasops-local",
    )

    assert result["success"] is True
    assert result["uid"] == "fault-uid"
    assert result["resource_version"] == "42"
    assert config_calls == [{"context": "kind-atlasops-local"}]
    api.delete_namespaced_custom_object.assert_called_once_with(
        group="chaos-mesh.org",
        version="v1alpha1",
        namespace="chaos-mesh",
        plural="stresschaos",
        name="stage-a-fault",
        body={
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {
                "uid": "fault-uid",
                "resourceVersion": "42",
            },
        },
        _request_timeout=30,
    )
    assert api_client.closed is True


def test_delete_chaos_surfaces_api_precondition_conflict(monkeypatch):
    api = Mock()

    class FakeApiException(Exception):
        status = 409
        reason = "Conflict"

    api.delete_namespaced_custom_object.side_effect = FakeApiException("conflict")
    _install_fake_kubernetes(monkeypatch, api)

    result = chaos.delete_chaos_with_preconditions(
        "StressChaos",
        "stage-a-fault",
        "chaos-mesh",
        uid="fault-uid",
        resource_version="42",
    )

    assert result["success"] is False
    assert "409" in result["error"]
    api.delete_namespaced_custom_object.assert_called_once()


def test_delete_chaos_does_not_treat_not_found_as_proven_absence(monkeypatch):
    api = Mock()

    class FakeApiException(Exception):
        status = 404
        reason = "Not Found"

    api.delete_namespaced_custom_object.side_effect = FakeApiException("not found")
    _install_fake_kubernetes(monkeypatch, api)

    result = chaos.delete_chaos_with_preconditions(
        "StressChaos",
        "stage-a-fault",
        "chaos-mesh",
        uid="fault-uid",
        resource_version="42",
    )

    assert result["success"] is False
    assert result["status_code"] == 404


@pytest.mark.parametrize(
    ("uid", "resource_version"),
    [
        (None, "42"),
        ("", "42"),
        ("fault-uid", None),
        ("fault-uid", ""),
    ],
)
def test_delete_chaos_rejects_missing_preconditions_without_loading_config(
    monkeypatch,
    uid,
    resource_version,
):
    api = Mock()
    config_calls, _ = _install_fake_kubernetes(monkeypatch, api)

    result = chaos.delete_chaos_with_preconditions(
        "StressChaos",
        "stage-a-fault",
        "chaos-mesh",
        uid=uid,
        resource_version=resource_version,
    )

    assert result["success"] is False
    assert config_calls == []
    api.delete_namespaced_custom_object.assert_not_called()


@pytest.mark.parametrize(
    ("kind", "name", "namespace", "kube_context"),
    [
        ("Deployment", "stage-a-fault", "chaos-mesh", None),
        ("StressChaos", "bad/name", "chaos-mesh", None),
        ("StressChaos", "stage-a-fault", "default", None),
        ("StressChaos", "stage-a-fault", "chaos-mesh", ""),
    ],
)
def test_delete_chaos_rejects_invalid_target_or_context(
    monkeypatch,
    kind,
    name,
    namespace,
    kube_context,
):
    api = Mock()
    config_calls, _ = _install_fake_kubernetes(monkeypatch, api)

    result = chaos.delete_chaos_with_preconditions(
        kind,
        name,
        namespace,
        uid="fault-uid",
        resource_version="42",
        kube_context=kube_context,
    )

    assert result["success"] is False
    assert config_calls == []
    api.delete_namespaced_custom_object.assert_not_called()


def test_scoped_stop_uses_bound_uid_and_current_resource_version(monkeypatch):
    events: list[str] = []
    api = Mock()
    api.get_namespaced_custom_object.side_effect = lambda **_kwargs: (
        events.append("get") or _active_resource(resource_version="current-73")
    )

    def guarded_delete(**kwargs):
        events.append("delete")
        return {"status": "Success"}

    api.delete_namespaced_custom_object.side_effect = guarded_delete
    config_calls, api_client = _install_fake_kubernetes(monkeypatch, api)
    run = Mock(side_effect=AssertionError("scoped stop must not use kubectl"))
    monkeypatch.setattr(chaos, "_run", run)

    def before_delete():
        events.append("guard")

    with chaos.stage_a_chaos_scope(
        _scope_identity(),
        before_delete=before_delete,
    ):
        result = chaos.chaos_stop_experiment(
            "StressChaos",
            "stage-a-fault",
            namespace="chaos-mesh",
        )

    assert result["success"] is True
    assert events == ["get", "guard", "delete"]
    assert config_calls == [{"context": "kind-atlasops-local"}]
    api.get_namespaced_custom_object.assert_called_once_with(
        group="chaos-mesh.org",
        version="v1alpha1",
        namespace="chaos-mesh",
        plural="stresschaos",
        name="stage-a-fault",
        _request_timeout=30,
    )
    delete_body = api.delete_namespaced_custom_object.call_args.kwargs["body"]
    assert delete_body["preconditions"] == {
        "uid": "fault-uid",
        "resourceVersion": "current-73",
    }
    assert api_client.closed is True
    run.assert_not_called()


def test_scoped_stop_rejects_changed_injected_container_records(monkeypatch):
    api = Mock()
    api.get_namespaced_custom_object.return_value = _active_resource()
    _install_fake_kubernetes(monkeypatch, api)
    with chaos.stage_a_chaos_scope(_scope_identity(container_records=[{
        "phase": "Injected", "id": "different-container",
    }])):
        result = chaos.chaos_stop_experiment("StressChaos", "stage-a-fault", "chaos-mesh")
    assert result["success"] is False
    api.delete_namespaced_custom_object.assert_not_called()


@pytest.mark.parametrize(
    ("resource", "identity"),
    [
        (_active_resource(uid="replacement-uid"), _scope_identity()),
        (
            {
                **_active_resource(),
                "metadata": {
                    "name": "stage-a-fault",
                    "namespace": "chaos-mesh",
                    "uid": "fault-uid",
                },
            },
            _scope_identity(),
        ),
        (
            {
                **_active_resource(),
                "status": {
                    "experiment": {"containerRecords": [{"phase": "Not Injected"}]},
                    "conditions": [
                        {"type": "Paused", "status": "False"},
                        {"type": "AllRecovered", "status": "True"},
                    ],
                },
            },
            _scope_identity(),
        ),
    ],
)
def test_scoped_stop_refuses_replacement_missing_version_or_inactive_object(
    monkeypatch,
    resource,
    identity,
):
    api = Mock()
    api.get_namespaced_custom_object.return_value = resource
    config_calls, _ = _install_fake_kubernetes(monkeypatch, api)
    run = Mock(side_effect=AssertionError("scoped stop must not fall back"))
    monkeypatch.setattr(chaos, "_run", run)
    before_delete = Mock()

    with chaos.stage_a_chaos_scope(identity, before_delete=before_delete):
        result = chaos.chaos_stop_experiment(
            "StressChaos",
            "stage-a-fault",
            namespace="chaos-mesh",
        )

    assert result["success"] is False
    assert config_calls == [{"context": "kind-atlasops-local"}]
    api.delete_namespaced_custom_object.assert_not_called()
    before_delete.assert_not_called()
    run.assert_not_called()


def test_scoped_stop_mismatched_action_fails_before_api_or_kubectl(monkeypatch):
    api = Mock()
    config_calls, _ = _install_fake_kubernetes(monkeypatch, api)
    run = Mock(side_effect=AssertionError("scoped stop must not fall back"))
    monkeypatch.setattr(chaos, "_run", run)

    with chaos.stage_a_chaos_scope(_scope_identity()):
        result = chaos.chaos_stop_experiment(
            "StressChaos",
            "different-experiment",
            namespace="chaos-mesh",
        )

    assert result["success"] is False
    assert config_calls == []
    api.get_namespaced_custom_object.assert_not_called()
    api.delete_namespaced_custom_object.assert_not_called()
    run.assert_not_called()


def test_scoped_stop_api_read_failure_never_falls_back_to_kubectl(monkeypatch):
    api = Mock()

    class FakeApiException(Exception):
        status = 503
        reason = "Unavailable"

    api.get_namespaced_custom_object.side_effect = FakeApiException("offline")
    _install_fake_kubernetes(monkeypatch, api)
    run = Mock(side_effect=AssertionError("scoped stop must not fall back"))
    monkeypatch.setattr(chaos, "_run", run)

    with chaos.stage_a_chaos_scope(_scope_identity()):
        result = chaos.chaos_stop_experiment(
            "StressChaos",
            "stage-a-fault",
            namespace="chaos-mesh",
        )

    assert result["success"] is False
    assert "503" in result["error"]
    api.delete_namespaced_custom_object.assert_not_called()
    run.assert_not_called()


def test_stage_a_scope_requires_a_bound_uid():
    with pytest.raises(ValueError, match="uid"):
        with chaos.stage_a_chaos_scope(_scope_identity(uid="")):
            pass


@pytest.mark.parametrize("kube_context", [None, ""])
def test_stage_a_scope_requires_an_explicit_context(kube_context):
    with pytest.raises(ValueError, match="kube_context"):
        with chaos.stage_a_chaos_scope(
            _scope_identity(kube_context=kube_context),
        ):
            pass


def test_stage_a_chaos_inventory_retains_identity_and_complete_records(monkeypatch):
    resource = _active_resource()
    resource["metadata"]["resourceVersion"] = "listed-19"
    resource["status"]["experiment"]["containerRecords"][0]["pod"] = "payment-0"
    monkeypatch.setattr(
        chaos,
        "_run",
        lambda *_args, **_kwargs: {
            "success": True,
            "stdout": json.dumps({"items": [resource]}),
        },
    )

    result = chaos.chaos_list_experiments()

    assert result["success"] is True
    for experiment in (result["inventory"][0], result["active_experiments"][0]):
        assert experiment["uid"] == "fault-uid"
        assert experiment["resource_version"] == "listed-19"
        assert (
            experiment["status"]["container_records"]
            == resource["status"]["experiment"]["containerRecords"]
        )


def test_chaos_stop_tool_arguments_remain_kind_name_namespace_only():
    assert list(inspect.signature(chaos.chaos_stop_experiment).parameters) == [
        "kind",
        "name",
        "namespace",
    ]
