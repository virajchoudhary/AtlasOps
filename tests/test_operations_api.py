from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agents.approval import ApprovalGate
from agents.circuit_breaker import CircuitBreaker
from agents.execution_journal import get_execution_journal
from agents.operations_api import operations_lifespan, operations_router
from tests.test_approval_target_binding import ACTION


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    import agents.coordinator as coordinator

    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    monkeypatch.setenv("ATLASOPS_OPERATOR_ID", "test-operator")
    monkeypatch.setenv("ATLASOPS_RUNTIME_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-placeholder-operations-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(coordinator, "_RUNTIME_API_KEY", "test-placeholder-operator-key")
    gate, breaker = ApprovalGate(), CircuitBreaker()
    app = FastAPI(lifespan=operations_lifespan)
    app.include_router(operations_router(
        gate=lambda: gate, breaker=lambda: breaker, require_key=coordinator._require_runtime_api_key,
    ))
    return app, gate, breaker


def test_operations_require_an_operator_key_and_return_real_journal_state(runtime):
    app, gate, _breaker = runtime
    with TestClient(app) as client:
        assert client.get("/operations").status_code == 401
        journal = get_execution_journal()
        journal.begin("inc-operator", "b" * 32, alert={}, anchors=None)
        request = gate.request_action(incident_id="inc-operator", severity="P1", action=ACTION)
        response = client.get("/operations", headers={"X-AtlasOps-Key": "test-placeholder-operator-key"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["executions"][0]["state"] == "running"
        assert payload["pending"][0]["action_digest"] == request.action_digest
        assert payload["certification_status"] == "NOT_CERTIFIED"


def test_decision_binds_digest_and_uses_only_server_identity(runtime):
    app, gate, _breaker = runtime
    headers = {"X-AtlasOps-Key": "test-placeholder-operator-key"}
    with TestClient(app) as client:
        request = gate.request_action(incident_id="inc-operator", severity="P1", action=ACTION)
        body = {"token": request.token, "action_digest": request.action_digest, "decision": "approved"}
        assert client.post("/operations/decision", headers=headers, json={**body, "approved_by": "spoofed"}).status_code == 422
        assert client.post("/operations/decision", headers=headers, json={**body, "action_digest": "f" * 64}).status_code == 400
        assert client.post("/operations/decision", headers={**headers, "Origin": "https://hostile.invalid"}, json=body).status_code == 403
        assert request.decision == "pending"
        response = client.post("/operations/decision", headers={**headers, "Origin": "http://testserver"}, json=body)
        assert response.status_code == 200
        assert request.approved_by == "test-operator"
        assert client.post("/operations/decision", headers=headers, json=body).status_code == 400


def test_missing_server_principal_cannot_approve(runtime, monkeypatch):
    app, gate, _breaker = runtime
    monkeypatch.delenv("ATLASOPS_OPERATOR_ID")
    with TestClient(app) as client:
        request = gate.request_action(incident_id="inc-operator", severity="P1", action=ACTION)
        response = client.post("/operations/decision", headers={"X-AtlasOps-Key": "test-placeholder-operator-key"}, json={
            "token": request.token, "action_digest": request.action_digest, "decision": "approved",
        })
        assert response.status_code == 503
        assert request.decision == "pending"


def test_evidence_download_preserves_failed_verification_and_hashes(runtime):
    app, _gate, _breaker = runtime
    with TestClient(app) as client:
        journal = get_execution_journal()
        journal.begin("inc-export", "e" * 32, alert={}, anchors=None)
        journal.note("e" * 32, phase="finished", state="completed", event="finished", patch={
            "verification": {"verification_status": "failed"}, "env_resolved": False, "resolved": False,
        })
        response = client.get(f"/operations/{'e' * 32}/evidence", headers={"X-AtlasOps-Key": "test-placeholder-operator-key"})
        assert response.status_code == 200
        assert "attachment" in response.headers["Content-Disposition"]
        assert response.json()["record"]["resolved"] is False
        assert response.json()["integrity"]["ok"] is True
        assert response.json()["audit_integrity"]["ok"] is True


def test_pause_stops_future_tool_execution_without_resetting_counters(runtime):
    app, _gate, breaker = runtime
    breaker.start_incident()
    with TestClient(app) as client:
        result = client.post("/operations/pause", headers={"X-AtlasOps-Key": "test-placeholder-operator-key"})
        assert result.status_code == 200
        assert result.json()["tripped"] is True
        assert result.json()["active_incidents"] == 1


def test_operational_decision_wakes_coordinator_on_its_event_loop(runtime, monkeypatch):
    import asyncio
    import httpx

    app, gate, _breaker = runtime

    async def exercise():
        owner = asyncio.get_running_loop()
        callback = gate.callback

        def decide(*args, **kwargs):
            assert asyncio.get_running_loop() is owner
            return callback(*args, **kwargs)

        monkeypatch.setattr(gate, "callback", decide)
        request = gate.request_action(
            incident_id="inc-loop-owner", severity="P1", action=ACTION,
        )
        waiter = asyncio.create_task(gate.wait_for_action_decision(
            request.incident_id, request_token=request.token,
        ))
        await asyncio.sleep(0)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/operations/decision",
                    headers={"X-AtlasOps-Key": "test-placeholder-operator-key"},
                    json={
                        "token": request.token, "action_digest": request.action_digest,
                        "decision": "approved",
                    },
                )
                assert response.status_code == 200
            decision, permit = await asyncio.wait_for(waiter, timeout=1)
            assert decision["status"] == "approved"
            assert permit is not None
        finally:
            if not waiter.done():
                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter

    asyncio.run(exercise())
