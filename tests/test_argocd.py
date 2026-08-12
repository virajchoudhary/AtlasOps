"""Security regression tests for the Argo CD tool wrapper."""

import importlib
from unittest.mock import Mock, patch

import pytest


def test_module_imports_without_configuration_or_http(monkeypatch):
    for name in ("ARGOCD_URL", "ARGOCD_USER", "ARGOCD_PASS", "ARGOCD_VERIFY_TLS"):
        monkeypatch.delenv(name, raising=False)

    import agents.tools.argocd as argocd

    with (
        patch.object(argocd.requests, "post") as auth_request,
        patch.object(argocd.requests, "request") as api_request,
    ):
        importlib.reload(argocd)

    auth_request.assert_not_called()
    api_request.assert_not_called()


def test_missing_url_fails_before_http(monkeypatch):
    for name in ("ARGOCD_URL", "ARGOCD_USER", "ARGOCD_PASS", "ARGOCD_VERIFY_TLS"):
        monkeypatch.delenv(name, raising=False)

    import agents.tools.argocd as argocd

    argocd = importlib.reload(argocd)
    with (
        patch.object(argocd.requests, "post") as auth_request,
        patch.object(argocd.requests, "request") as api_request,
    ):
        result = argocd.argocd_list_apps()

    assert result == {
        "success": False,
        "error": "argocd_configuration_error: missing required environment variable ARGOCD_URL",
    }
    auth_request.assert_not_called()
    api_request.assert_not_called()


def test_missing_user_fails_before_http(monkeypatch):
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.invalid")
    monkeypatch.delenv("ARGOCD_USER", raising=False)
    monkeypatch.delenv("ARGOCD_PASS", raising=False)

    import agents.tools.argocd as argocd

    argocd = importlib.reload(argocd)
    with (
        patch.object(argocd.requests, "post") as auth_request,
        patch.object(argocd.requests, "request") as api_request,
    ):
        result = argocd.argocd_app_get("example-app")

    assert result == {
        "success": False,
        "error": "argocd_configuration_error: missing required environment variable ARGOCD_USER",
    }
    auth_request.assert_not_called()
    api_request.assert_not_called()


def test_missing_password_fails_before_http(monkeypatch):
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.invalid")
    monkeypatch.setenv("ARGOCD_USER", "test-user")
    monkeypatch.delenv("ARGOCD_PASS", raising=False)

    import agents.tools.argocd as argocd

    argocd = importlib.reload(argocd)
    with (
        patch.object(argocd.requests, "post") as auth_request,
        patch.object(argocd.requests, "request") as api_request,
    ):
        result = argocd.argocd_app_history("example-app")

    assert result == {
        "success": False,
        "error": "argocd_configuration_error: missing required environment variable ARGOCD_PASS",
    }
    auth_request.assert_not_called()
    api_request.assert_not_called()


@pytest.mark.parametrize(
    "configured_url",
    ["argocd.example.invalid", "ftp://argocd.example.invalid", "https://"],
)
def test_invalid_url_fails_before_http(monkeypatch, configured_url):
    monkeypatch.setenv("ARGOCD_URL", configured_url)
    monkeypatch.setenv("ARGOCD_USER", "test-user")
    monkeypatch.setenv("ARGOCD_PASS", "test-placeholder-secret")

    import agents.tools.argocd as argocd

    argocd = importlib.reload(argocd)
    with (
        patch.object(argocd.requests, "post") as auth_request,
        patch.object(argocd.requests, "request") as api_request,
    ):
        result = argocd.argocd_app_get("example-app")

    assert result == {
        "success": False,
        "error": "argocd_configuration_error: ARGOCD_URL must use http or https with a host",
    }
    auth_request.assert_not_called()
    api_request.assert_not_called()


def test_https_is_preserved_and_tls_verification_defaults_true(monkeypatch):
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.invalid")
    monkeypatch.setenv("ARGOCD_USER", "test-user")
    monkeypatch.setenv("ARGOCD_PASS", "test-placeholder-secret")
    monkeypatch.delenv("ARGOCD_VERIFY_TLS", raising=False)

    import agents.tools.argocd as argocd

    argocd = importlib.reload(argocd)
    auth_response = Mock()
    auth_response.json.return_value = {"token": "test-placeholder-token"}
    api_response = Mock(status_code=200)
    api_response.json.return_value = {"metadata": {"name": "example-app"}}

    with (
        patch.object(argocd.requests, "post", return_value=auth_response) as auth_request,
        patch.object(argocd.requests, "request", return_value=api_response) as api_request,
    ):
        result = argocd.argocd_app_get("example-app")

    assert result["success"] is True
    assert auth_request.call_args.args[0] == "https://argocd.example.invalid/api/v1/session"
    assert auth_request.call_args.kwargs["verify"] is True
    assert api_request.call_args.args[1] == "https://argocd.example.invalid/api/v1/applications/example-app"
    assert api_request.call_args.kwargs["verify"] is True


def test_explicit_tls_verification_override_is_honored(monkeypatch):
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.invalid")
    monkeypatch.setenv("ARGOCD_USER", "test-user")
    monkeypatch.setenv("ARGOCD_PASS", "test-placeholder-secret")
    monkeypatch.setenv("ARGOCD_VERIFY_TLS", "false")

    import agents.tools.argocd as argocd

    argocd = importlib.reload(argocd)
    auth_response = Mock()
    auth_response.json.return_value = {"token": "test-placeholder-token"}
    api_response = Mock(status_code=200)
    api_response.json.return_value = {"metadata": {"name": "example-app"}}

    with (
        patch.object(argocd.requests, "post", return_value=auth_response) as auth_request,
        patch.object(argocd.requests, "request", return_value=api_response) as api_request,
    ):
        result = argocd.argocd_app_get("example-app")

    assert result["success"] is True
    assert auth_request.call_args.kwargs["verify"] is False
    assert api_request.call_args.kwargs["verify"] is False


def test_authentication_failure_does_not_expose_password(monkeypatch):
    test_password = "test-placeholder-secret"
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.invalid")
    monkeypatch.setenv("ARGOCD_USER", "test-user")
    monkeypatch.setenv("ARGOCD_PASS", test_password)

    import agents.tools.argocd as argocd

    argocd = importlib.reload(argocd)
    failure = argocd.requests.RequestException(f"mock failure included {test_password}")
    with (
        patch.object(argocd.requests, "post", side_effect=failure),
        patch.object(argocd.requests, "request") as api_request,
    ):
        result = argocd.argocd_list_apps()

    assert result == {
        "success": False,
        "error": "argocd_authentication_error: authentication request failed",
    }
    assert test_password not in str(result)
    api_request.assert_not_called()


def test_cached_token_is_isolated_by_runtime_configuration(monkeypatch):
    monkeypatch.setenv("ARGOCD_URL", "https://argocd-a.example.invalid")
    monkeypatch.setenv("ARGOCD_USER", "test-user-a")
    monkeypatch.setenv("ARGOCD_PASS", "test-placeholder-secret-a")

    import agents.tools.argocd as argocd

    argocd = importlib.reload(argocd)
    auth_a = Mock()
    auth_a.json.return_value = {"token": "test-placeholder-token-a"}
    auth_b = Mock()
    auth_b.json.return_value = {"token": "test-placeholder-token-b"}
    api_response = Mock(status_code=200)
    api_response.json.return_value = {"metadata": {"name": "example-app"}}

    with (
        patch.object(argocd.requests, "post", side_effect=[auth_a, auth_b]) as auth_request,
        patch.object(argocd.requests, "request", return_value=api_response) as api_request,
    ):
        first = argocd.argocd_app_get("example-app")
        monkeypatch.setenv("ARGOCD_URL", "https://argocd-b.example.invalid")
        monkeypatch.setenv("ARGOCD_USER", "test-user-b")
        monkeypatch.setenv("ARGOCD_PASS", "test-placeholder-secret-b")
        second = argocd.argocd_app_get("example-app")

    assert first["success"] is True
    assert second["success"] is True
    assert auth_request.call_count == 2
    assert api_request.call_args_list[0].args[1].startswith("https://argocd-a.example.invalid/")
    assert api_request.call_args_list[0].kwargs["headers"] == {
        "Authorization": "Bearer test-placeholder-token-a"
    }
    assert api_request.call_args_list[1].args[1].startswith("https://argocd-b.example.invalid/")
    assert api_request.call_args_list[1].kwargs["headers"] == {
        "Authorization": "Bearer test-placeholder-token-b"
    }


def test_invalid_tls_setting_fails_before_http(monkeypatch):
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.invalid")
    monkeypatch.setenv("ARGOCD_USER", "test-user")
    monkeypatch.setenv("ARGOCD_PASS", "test-placeholder-secret")
    monkeypatch.setenv("ARGOCD_VERIFY_TLS", "sometimes")

    import agents.tools.argocd as argocd

    argocd = importlib.reload(argocd)
    with (
        patch.object(argocd.requests, "post") as auth_request,
        patch.object(argocd.requests, "request") as api_request,
    ):
        result = argocd.argocd_app_get("example-app")

    assert result == {
        "success": False,
        "error": "argocd_configuration_error: ARGOCD_VERIFY_TLS must be true or false",
    }
    auth_request.assert_not_called()
    api_request.assert_not_called()


def test_unauthorized_response_refreshes_token_once(monkeypatch):
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.invalid")
    monkeypatch.setenv("ARGOCD_USER", "test-user")
    monkeypatch.setenv("ARGOCD_PASS", "test-placeholder-secret")

    import agents.tools.argocd as argocd

    argocd = importlib.reload(argocd)
    first_auth = Mock()
    first_auth.json.return_value = {"token": "test-placeholder-token-old"}
    second_auth = Mock()
    second_auth.json.return_value = {"token": "test-placeholder-token-new"}
    unauthorized = Mock(status_code=401)
    successful = Mock(status_code=200)
    successful.json.return_value = {"metadata": {"name": "example-app"}}

    with (
        patch.object(argocd.requests, "post", side_effect=[first_auth, second_auth]) as auth_request,
        patch.object(
            argocd.requests,
            "request",
            side_effect=[unauthorized, successful],
        ) as api_request,
    ):
        result = argocd.argocd_app_get("example-app")

    assert result["success"] is True
    assert auth_request.call_count == 2
    assert api_request.call_count == 2
    assert api_request.call_args_list[0].kwargs["headers"] == {
        "Authorization": "Bearer test-placeholder-token-old"
    }
    assert api_request.call_args_list[1].kwargs["headers"] == {
        "Authorization": "Bearer test-placeholder-token-new"
    }
