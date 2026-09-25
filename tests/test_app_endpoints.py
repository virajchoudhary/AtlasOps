"""Lightweight integration smoke tests for app.py endpoints.

These tests mock external side effects so they can run in CI without a cluster.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def configured_test_api_key(monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_API_KEY", "atlasops-local-test-key")


def _client():
    import app as app_module
    from app import app

    headers: dict[str, str] = {}
    key = app_module._API_KEY or os.environ.get("ATLASOPS_API_KEY")
    if key:
        headers["X-AtlasOps-Key"] = key
        headers["X-API-Key"] = key
    return TestClient(app, headers=headers)


def test_health_endpoint_ok():
    client = _client()
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "model" in body
    assert "discord_webhook_configured" in body
    assert "slack_webhook_configured" in body


@pytest.mark.parametrize("scenario_id", ["single_fault/sf-001", "../../elsewhere"])
def test_web_injection_retired_even_when_flagged_on(monkeypatch, scenario_id):
    import app as app_module

    monkeypatch.setenv("ATLASOPS_ENABLE_WEB_CHAOS", "1")
    monkeypatch.setenv("ATLASOPS_WEB_CHAOS_CONTEXT", "kind-atlasops-local")
    with (
        patch("app.subprocess.run") as mock_run,
        patch("asyncio.create_task") as mock_task,
        patch.object(app_module.correlator, "ingest") as mock_ingest,
    ):
        response = _client().post("/inject", json={"scenario_id": scenario_id, "name": "Smoke"})
        assert response.status_code == 503
        assert "retired" in response.json()["detail"]
        mock_run.assert_not_called()
        mock_task.assert_not_called()
        mock_ingest.assert_not_called()


def test_mutating_and_approval_routes_fail_closed_without_key(monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_API_KEY", "")
    client = TestClient(app_module.app)
    for path in ("/inject", "/reset", "/approve", "/approval/callback", "/circuit-breaker/reset"):
        assert client.post(path, json={"scenario_id": "single_fault/sf-001"}).status_code == 503
    assert client.get("/approval/pending").status_code == 503


def test_pending_approval_tokens_require_operator_auth():
    import app as app_module

    client = TestClient(app_module.app)
    assert client.get("/approval/pending").status_code == 401
    assert client.post("/approve", json={"token": "any", "decision": "approved"}).status_code == 401


def test_unsigned_webhook_is_disabled_without_secret(monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_WEBHOOK_SECRET", "")
    client = TestClient(app_module.app)
    assert client.post("/webhook", json={}).status_code == 503
    monkeypatch.setattr(app_module, "_WEBHOOK_SECRET", "local-test-webhook-secret")
    assert client.post("/webhook", json={}).status_code == 401


def test_web_cleanup_retired_without_resetting_state(monkeypatch):
    import app as app_module

    monkeypatch.setenv("ATLASOPS_ENABLE_WEB_CHAOS", "1")
    monkeypatch.setenv("ATLASOPS_WEB_CHAOS_CONTEXT", "kind-atlasops-local")
    breaker_reset = MagicMock()
    correlator_reset = MagicMock()
    monkeypatch.setattr(app_module.circuit_breaker, "reset", breaker_reset)
    monkeypatch.setattr(app_module.correlator, "reset", correlator_reset)
    with patch("app.subprocess.run") as mock_run, patch("asyncio.create_task") as mock_task:
        reset = _client().post("/reset", json={"scenario_id": "single_fault/sf-001"})
        assert reset.status_code == 503
        assert "retired" in reset.json()["detail"]
        mock_run.assert_not_called()
        mock_task.assert_not_called()
        breaker_reset.assert_not_called()
        correlator_reset.assert_not_called()


def test_mutating_requests_require_valid_scenario_body():
    with patch("app.subprocess.run") as mock_run:
        assert _client().post("/inject", json={"scenario_id": ""}).status_code == 422
        assert _client().post("/reset", json={}).status_code == 422
        assert _client().post("/reset", json={"scenario_id": "../../elsewhere"}).status_code == 503
        mock_run.assert_not_called()


def test_slack_feed_returns_recent_posts(tmp_path, monkeypatch):
    log_file = tmp_path / "data" / "slack_posts.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    posts = [{"a": 1}, {"b": 2}]
    log_file.write_text("\n".join(json.dumps(p) for p in posts), encoding="utf-8")

    # Ensure endpoint reads from temporary file path.
    monkeypatch.chdir(tmp_path)
    client = _client()
    r = client.get("/slack/feed")
    assert r.status_code == 200
    assert len(r.json()["posts"]) == 2


@patch("app.subprocess.run")
def test_cluster_health_handles_kubectl_failure(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    client = _client()
    r = client.get("/cluster/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert isinstance(body["services"], dict)


def test_approval_callback_and_pending_flow():
    from agents.approval import approval_gate

    req = approval_gate.request("inc-test-approval", "P1", "rollback checkoutservice")
    client = _client()
    pending = client.get("/approval/pending")
    assert pending.status_code == 200
    assert any(p["incident_id"] == "inc-test-approval" for p in pending.json()["pending"])

    cb = client.post(
        "/approval/callback",
        json={"token": req.token, "decision": "approved", "approved_by": "test-user"},
    )
    assert cb.status_code == 200
    assert cb.json()["ok"] is True


def test_approval_callback_unknown_token_400():
    client = _client()
    r = client.post("/approval/callback", json={"token": "missing", "decision": "approved"})
    assert r.status_code == 400


def test_circuit_breaker_status_and_reset():
    client = _client()
    s = client.get("/circuit-breaker/status")
    assert s.status_code == 200
    assert "tripped" in s.json()

    r = client.post("/circuit-breaker/reset")
    assert r.status_code == 200
    assert r.json()["tripped"] is False


def test_incidents_active_endpoint_returns_list():
    client = _client()
    r = client.get("/incidents/active")
    assert r.status_code == 200
    body = r.json()
    assert "incidents" in body
    assert isinstance(body["incidents"], list)


def test_audit_log_and_verify_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-placeholder-audit-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    client = _client()
    l = client.get("/audit/log?limit=5")
    assert l.status_code == 200
    assert "entries" in l.json()
    v = client.get("/audit/verify")
    assert v.status_code == 200
    assert "ok" in v.json()

