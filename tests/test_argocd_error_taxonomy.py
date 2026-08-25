"""Deterministic error-taxonomy tests for the Argo CD tool wrappers."""

import importlib
import json
from unittest.mock import Mock, patch

import pytest
import requests


def _reload_with_env(monkeypatch):
    monkeypatch.setenv("ARGOCD_URL", "https://argocd.example.invalid")
    monkeypatch.setenv("ARGOCD_USER", "user")
    monkeypatch.setenv("ARGOCD_PASS", "pass")
    import agents.tools.argocd as argocd

    return importlib.reload(argocd)


def _auth_ok(monkeypatch, argocd):
    token_resp = Mock()
    token_resp.status_code = 200
    token_resp.json.return_value = {"token": "test-token"}
    # Replace (not decorate) requests.post so no real HTTP auth call is made;
    # monkeypatch restores the original after each test.
    monkeypatch.setattr(argocd.requests, "post", Mock(return_value=token_resp))


def _api_response(status_code, body=None, text=""):
    resp = Mock()
    resp.status_code = status_code

    def raise_for_status():
        if status_code >= 400:
            err = requests.HTTPError(f"{status_code} error")
            err.response = resp
            raise err

    resp.raise_for_status = raise_for_status
    if body is not None:
        resp.json.return_value = body
    else:
        resp.json.side_effect = ValueError("no json")
    resp.text = text
    return resp


def test_404_is_classified_as_not_found(monkeypatch):
    argocd = _reload_with_env(monkeypatch)
    _auth_ok(monkeypatch, argocd)
    not_found = _api_response(404, {"message": "app paymentservice not found"})
    with patch.object(argocd.requests, "request", return_value=not_found):
        result = argocd.argocd_rollback("paymentservice", "3")
    assert result["success"] is False
    assert result["error_class"] == "not_found"
    assert result["status_code"] == 404
    assert result["error"] == "argocd_not_found (HTTP 404)"


def test_400_is_classified_as_invalid_request(monkeypatch):
    argocd = _reload_with_env(monkeypatch)
    _auth_ok(monkeypatch, argocd)
    bad = _api_response(400, {"message": "invalid rollback id"})
    with patch.object(argocd.requests, "request", return_value=bad):
        result = argocd.argocd_rollback("paymentservice", "999")
    assert result["success"] is False
    assert result["error_class"] == "invalid_request"
    assert result["status_code"] == 400
    # Response bodies are intentionally not echoed (secret-safety).
    assert "invalid rollback id" not in result["error"]


def test_non_numeric_rollback_revision_fails_before_api(monkeypatch):
    argocd = _reload_with_env(monkeypatch)
    _auth_ok(monkeypatch, argocd)
    with patch.object(argocd.requests, "request") as request:
        result = argocd.argocd_rollback("paymentservice", "latest")
    assert result == {
        "success": False,
        "error": "argocd_invalid_revision: revision must be a non-negative integer, got 'latest'",
        "error_class": "invalid_revision",
    }
    request.assert_not_called()


def test_timeout_is_classified(monkeypatch):
    argocd = _reload_with_env(monkeypatch)
    _auth_ok(monkeypatch, argocd)
    with patch.object(argocd.requests, "request", side_effect=requests.exceptions.Timeout()):
        result = argocd.argocd_list_apps()
    assert result == {
        "success": False,
        "error": "argocd_error: request timeout",
        "error_class": "timeout",
    }


def test_connection_error_is_classified(monkeypatch):
    argocd = _reload_with_env(monkeypatch)
    _auth_ok(monkeypatch, argocd)
    with patch.object(argocd.requests, "request", side_effect=requests.exceptions.ConnectionError()):
        result = argocd.argocd_app_get("paymentservice")
    assert result["error_class"] == "connection_failed"
    assert result["success"] is False


def test_authentication_transport_timeout_is_timeout_not_auth_failure(monkeypatch):
    argocd = _reload_with_env(monkeypatch)
    with patch.object(
        argocd.requests, "post", side_effect=requests.exceptions.Timeout()
    ):
        result = argocd.argocd_list_apps()
    assert result == {
        "success": False,
        "error": "argocd_error: request timeout",
        "error_class": "timeout",
    }


@pytest.mark.parametrize(
    ("status", "expected_class"),
    [(401, "authentication_failed"), (403, "authorization_failed"), (500, "http_500_error")],
)
def test_http_status_classes(monkeypatch, status, expected_class):
    argocd = _reload_with_env(monkeypatch)
    _auth_ok(monkeypatch, argocd)
    # 401 retried once internally then still failing must classify, not crash.
    resp = _api_response(status, {})
    with patch.object(argocd.requests, "request", return_value=resp):
        result = argocd.argocd_list_apps()
    assert result["success"] is False
    assert result["error_class"] == expected_class


def test_unparseable_body_still_yields_class_and_never_leaks_secrets(monkeypatch):
    argocd = _reload_with_env(monkeypatch)
    token_resp = Mock()
    token_resp.status_code = 200
    token_resp.json.return_value = {"token": "SUPERSECRET"}
    monkeypatch.setattr(argocd.requests, "post", Mock(return_value=token_resp))
    resp = _api_response(404, None, text="not found SUPERSECRET")
    with patch.object(argocd.requests, "request", return_value=resp):
        result = argocd.argocd_rollback("x", "1")
    payload = json.dumps(result)
    assert result["error_class"] == "not_found"
    assert "SUPERSECRET" not in payload
