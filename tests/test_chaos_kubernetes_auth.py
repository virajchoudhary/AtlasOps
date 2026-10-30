from unittest.mock import Mock

import pytest
from kubernetes import client, config

from agents.tools import chaos


@pytest.fixture
def auth_clients(monkeypatch):
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT", raising=False)
    host_client = Mock(name="host_client")
    pod_client = Mock(name="pod_client")
    host_loader = Mock(return_value=host_client)
    pod_loader = Mock()
    pod_factory = Mock(return_value=pod_client)
    object_api = Mock(name="custom_objects_api")
    object_factory = Mock(return_value=object_api)
    monkeypatch.setattr(config, "new_client_from_config", host_loader)
    monkeypatch.setattr(config, "load_incluster_config", pod_loader)
    monkeypatch.setattr(client, "ApiClient", pod_factory)
    monkeypatch.setattr(client, "CustomObjectsApi", object_factory)
    return host_loader, pod_loader, pod_factory, object_factory, host_client, pod_client


def test_pod_uses_an_owned_incluster_configuration(monkeypatch, auth_clients):
    host_loader, pod_loader, pod_factory, object_factory, _, pod_client = auth_clients
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")

    api, transport = chaos._new_chaos_api_client(None)

    host_loader.assert_not_called()
    pod_loader.assert_called_once()
    configuration = pod_loader.call_args.kwargs["client_configuration"]
    assert isinstance(configuration, client.Configuration)
    assert configuration.retries == 0
    pod_factory.assert_called_once_with(configuration=configuration)
    object_factory.assert_called_once_with(api_client=pod_client)
    assert api is object_factory.return_value
    assert transport is pod_client


def test_explicit_host_context_never_falls_back_to_pod(monkeypatch, auth_clients):
    host_loader, pod_loader, pod_factory, object_factory, host_client, _ = auth_clients
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")

    _, transport = chaos._new_chaos_api_client("authorized-context")

    host_loader.assert_called_once()
    assert host_loader.call_args.kwargs["context"] == "authorized-context"
    configuration = host_loader.call_args.kwargs["client_configuration"]
    assert isinstance(configuration, client.Configuration)
    assert configuration.retries == 0
    pod_loader.assert_not_called()
    pod_factory.assert_not_called()
    object_factory.assert_called_once_with(api_client=host_client)
    assert transport is host_client


def test_host_keeps_existing_kubeconfig_behavior(auth_clients):
    host_loader, pod_loader, pod_factory, _, host_client, _ = auth_clients

    _, transport = chaos._new_chaos_api_client(None)

    host_loader.assert_called_once()
    assert host_loader.call_args.kwargs["context"] is None
    assert host_loader.call_args.kwargs["client_configuration"].retries == 0
    pod_loader.assert_not_called()
    pod_factory.assert_not_called()
    assert transport is host_client


def test_failed_pod_authentication_does_not_try_host_credentials(
    monkeypatch, auth_clients
):
    host_loader, pod_loader, pod_factory, _, _, _ = auth_clients
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    pod_loader.side_effect = config.ConfigException("unit-test missing Pod token")

    with pytest.raises(config.ConfigException):
        chaos._new_chaos_api_client(None)

    host_loader.assert_not_called()
    pod_factory.assert_not_called()


def test_failed_explicit_context_does_not_try_incluster_authentication(
    monkeypatch, auth_clients
):
    host_loader, pod_loader, pod_factory, _, _, _ = auth_clients
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    host_loader.side_effect = config.ConfigException("unit-test invalid context")

    with pytest.raises(config.ConfigException):
        chaos._new_chaos_api_client("authorized-context")

    pod_loader.assert_not_called()
    pod_factory.assert_not_called()


def test_pod_client_is_closed_after_use(monkeypatch, auth_clients):
    _, _, _, _, _, pod_client = auth_clients
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")

    with chaos._chaos_api_client(None):
        pass

    pod_client.close.assert_called_once_with()


@pytest.mark.parametrize("context", [None, "fixture-kind"])
def test_real_sdk_pool_disables_retries_before_first_request(monkeypatch, context):
    from urllib3.exceptions import ConnectTimeoutError, MaxRetryError

    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")

    def configure(configuration):
        configuration.host = "https://fixture-kubernetes.invalid"

    def pod_loader(*, client_configuration):
        configure(client_configuration)

    def host_loader(*, context, client_configuration=None):
        assert context == "fixture-kind"
        configuration = client_configuration or client.Configuration()
        configure(configuration)
        return client.ApiClient(configuration=configuration)

    monkeypatch.setattr(config, "load_incluster_config", pod_loader)
    monkeypatch.setattr(config, "new_client_from_config", host_loader)
    _api, transport = chaos._new_chaos_api_client(context)
    try:
        assert transport.configuration.retries == 0
        retries = transport.rest_client.pool_manager.connection_pool_kw["retries"]
        assert retries.total == 0
        for method in ("PATCH", "DELETE"):
            with pytest.raises(MaxRetryError):
                retries.increment(
                    method=method, url="/fixture-resource",
                    error=ConnectTimeoutError("fixture connection timeout"),
                )
    finally:
        transport.close()
