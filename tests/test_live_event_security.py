from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient


API_KEY = "synthetic-operator-key"
LIVE_EVENT_PATHS = ("/thoughts", "/stream", "/api/thoughts", "/api/stream")


@pytest.fixture
def integrated_client(monkeypatch):
    import app as module
    import agents.stream as events
    from agents.approval import ApprovalGate

    gate = ApprovalGate()
    monkeypatch.setattr(module, "approval_gate", gate)
    monkeypatch.setattr(module, "_API_KEY", API_KEY)
    monkeypatch.setattr(module.coordinator_module, "_RUNTIME_API_KEY", API_KEY)
    history = Mock(return_value=[{"text": "synthetic-private-event"}])

    async def finite_stream():
        yield "data: synthetic-private-event\n\n"

    subscription = Mock(side_effect=finite_stream)
    monkeypatch.setattr(module, "get_history", history)
    monkeypatch.setattr(events, "get_history", history)
    monkeypatch.setattr(module, "subscribe", subscription)
    monkeypatch.setattr(events, "subscribe", subscription)
    return TestClient(module.app), gate, history, subscription


@pytest.mark.parametrize("identity", [None, "", "   "])
def test_root_approval_requires_explicit_identity(integrated_client, identity):
    client, gate, _, _ = integrated_client
    request = gate.request("inc-synthetic-identity", "P1", "Inspect checkoutservice")
    payload = {"token": request.token, "decision": "approved"}
    if identity is not None:
        payload["approved_by"] = identity
    response = client.post(
        "/approve", json=payload, headers={"X-AtlasOps-Key": "synthetic-operator-key"},
    )
    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "approved_by is required"}
    assert request.decision == "pending"
    assert not request.event.is_set()


def test_root_approval_accepts_explicit_identity(integrated_client):
    client, gate, _, _ = integrated_client
    request = gate.request("inc-synthetic-approved", "P1", "Inspect checkoutservice")
    response = client.post(
        "/approve",
        json={
            "token": request.token,
            "decision": "approved",
            "approved_by": "synthetic-reviewer",
        },
        headers={"X-AtlasOps-Key": API_KEY},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert request.decision == "approved"
    assert request.approved_by == "synthetic-reviewer"
    assert request.event.is_set()


@pytest.mark.parametrize("path", LIVE_EVENT_PATHS)
def test_live_events_fail_closed_without_configured_key(integrated_client, monkeypatch, path):
    import app as module

    client, _, history, subscription = integrated_client
    monkeypatch.setattr(module, "_API_KEY", "")
    monkeypatch.setattr(module.coordinator_module, "_RUNTIME_API_KEY", "")
    response = client.get(path, headers={"X-AtlasOps-Key": API_KEY})
    assert response.status_code == 503
    history.assert_not_called()
    subscription.assert_not_called()


@pytest.mark.parametrize(
    ("headers", "params"),
    [
        ({}, {}),
        ({"X-AtlasOps-Key": "wrong-synthetic-key"}, {}),
        ({}, {"key": API_KEY}),
        ({}, {"api_key": API_KEY}),
    ],
    ids=("missing-header", "wrong-header", "key-query", "api-key-query"),
)
@pytest.mark.parametrize("path", LIVE_EVENT_PATHS)
def test_live_events_reject_unauthorized_requests_before_read(
    integrated_client, path, headers, params,
):
    client, _, history, subscription = integrated_client
    response = client.get(path, headers=headers, params=params)
    assert response.status_code == 401
    assert "synthetic-private-event" not in response.text
    history.assert_not_called()
    subscription.assert_not_called()


@pytest.mark.parametrize("path", LIVE_EVENT_PATHS)
def test_live_events_return_finite_synthetic_data_with_auth(integrated_client, path):
    client, _, history, subscription = integrated_client
    response = client.get(path, headers={"X-AtlasOps-Key": API_KEY})
    assert response.status_code == 200
    if path.endswith("/thoughts"):
        assert response.json() == {"thoughts": [{"text": "synthetic-private-event"}]}
        history.assert_called_once_with()
        subscription.assert_not_called()
    else:
        assert response.text == "data: synthetic-private-event\n\n"
        subscription.assert_called_once_with()
        history.assert_not_called()


def test_read_only_presentation_catalog_remains_public(integrated_client):
    client, _, _, _ = integrated_client
    response = client.get("/api/catalog")
    assert response.status_code == 200
