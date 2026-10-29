import json
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


@pytest.mark.asyncio
async def test_root_app_forwards_the_admitted_execution_id(monkeypatch):
    import app
    import agents.coordinator as coordinator

    handler = AsyncMock(return_value={"resolved": False})
    monkeypatch.setattr(coordinator, "handle_incident", handler)
    alert = {"commonLabels": {"alertname": "FixtureAvailability"}}
    anchors = {"primary_service": "frontend"}
    result = await app._dispatch_incident(
        alert, "inc-bridge", incident_anchors=anchors, execution_id="a" * 32,
    )
    handler.assert_awaited_once_with(
        alert, incident_id="inc-bridge", incident_anchors=anchors,
        execution_id="a" * 32,
    )
    assert result == {"resolved": False}


@pytest.mark.asyncio
async def test_authenticated_operational_webhook_reaches_the_root_bridge(
    monkeypatch, tmp_path,
):
    import app
    import agents.coordinator as coordinator
    from agents.correlator import IncidentCorrelator
    from agents.incident_dispatch import IncidentDispatcher

    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    monkeypatch.setenv("ATLASOPS_RUNTIME_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "fixture-audit-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(correlator, tmp_path / "admissions.sqlite3")
    monkeypatch.setattr(app, "correlator", correlator)
    monkeypatch.setattr(app, "incident_dispatcher", dispatcher)
    monkeypatch.setattr(app, "_WEBHOOK_SECRET", "fixture-webhook-secret")
    completed = asyncio.Event()
    received = {}

    async def handle(payload, *, incident_id, incident_anchors, execution_id):
        received.update(
            incident_id=incident_id, execution_id=execution_id,
            incident_anchors=incident_anchors, payload=payload,
        )
        completed.set()
        return {"resolved": False}

    monkeypatch.setattr(coordinator, "handle_incident", handle)
    payload = {
        "status": "firing",
        "alerts": [{
            "status": "firing", "fingerprint": "fixture-availability",
            "startsAt": "2026-10-29T00:00:00Z",
            "labels": {
                "alertname": "AtlasOpsOnlineBoutiqueDeploymentUnavailable",
                "deployment": "frontend", "namespace": "atlasops-boutique",
            },
        }],
        "commonLabels": {
            "alertname": "AtlasOpsOnlineBoutiqueDeploymentUnavailable",
            "deployment": "frontend", "namespace": "atlasops-boutique",
        },
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app.app), base_url="http://fixture",
    ) as client:
        response = await client.post(
            "/webhook", json=payload,
            headers={"Authorization": "Bearer fixture-webhook-secret"},
        )
        assert response.status_code == 200
        assert response.json()["dispatch_state"] == "started"
        await asyncio.wait_for(completed.wait(), timeout=5)
    assert received["incident_id"] == response.json()["incident_id"]
    assert len(received["execution_id"]) == 32
    assert received["incident_anchors"]["namespace"] == "atlasops-boutique"
    await asyncio.sleep(0)

def test_completed_records_redact_credentials_without_changing_negative_evidence(
    monkeypatch, tmp_path,
):
    import agents.coordinator as coordinator

    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path)
    record = {
        "incident_id": "inc-completed",
        "execution_id": "b" * 32,
        "resolved": False,
        "env_resolved": False,
        "verification": {"verification_status": "failed", "failed_checks": ["replicas"]},
        "generation_config": {"max_new_tokens": 256, "temperature": 0.0},
        "alert": {
            "api_key": "fixture-key-must-not-persist",
            "description": "Authorization: Bearer fixture-bearer-must-not-persist",
            "observation": "Preserve the completed negative result",
        },
        "diagnosis": {"trajectory": [{"output": {"password": "fixture-password"}}]},
    }
    original = deepcopy(record)
    coordinator._persist_incident_record(record)
    path = tmp_path / "inc-completed.json"
    first_bytes = path.read_bytes()
    saved = json.loads(first_bytes)
    assert "must-not-persist" not in first_bytes.decode()
    assert "fixture-password" not in first_bytes.decode()
    assert saved["alert"]["observation"] == record["alert"]["observation"]
    assert saved["verification"] == record["verification"]
    assert saved["generation_config"] == record["generation_config"]
    assert saved["resolved"] is False and saved["env_resolved"] is False
    assert record == original

    record["execution_id"] = "c" * 32
    coordinator._persist_incident_record(record)
    assert path.read_bytes() == first_bytes
    followup = tmp_path / f"inc-completed.execution-{'c' * 32}.json"
    assert json.loads(followup.read_text())["verification"] == record["verification"]


def test_audit_sanitizes_tool_results_before_truncation_and_hashing(tmp_path):
    from agents.audit import AuditLog

    audit = AuditLog("fixture-audit-secret", tmp_path / "audit.jsonl")
    summary = str({
        "success": False,
        "api_key": "fixture-key-must-not-persist",
        "error": "Authorization: Bearer fixture-bearer-must-not-persist",
        "observation": "The tool failed; keep this negative observation",
    })
    entry = audit.record(
        "inc-audit", "diagnosis", "tool_result",
        tool_name="kubectl_logs", result_summary=summary,
    )
    assert "must-not-persist" not in json.dumps(entry)
    assert "keep this negative observation" in entry["result_summary"]
    assert "must-not-persist" not in audit.log_path.read_text()
    assert audit.verify_integrity() == {"ok": True, "entries": 1}


@pytest.fixture
def native_runtime(monkeypatch, tmp_path):
    import agents.coordinator as coordinator

    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "fixture-audit-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("ATLASOPS_RUNTIME_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path / "trajectories")
    monkeypatch.setattr(coordinator, "MODEL_NAME", "fixture/expected-model")
    monkeypatch.setattr(coordinator, "API_KEY", "fixture-completion-key")
    monkeypatch.setattr(coordinator, "VLLM_BASE", "https://fixture.invalid/v1")
    monkeypatch.setattr(coordinator, "thought_emit", MagicMock())
    monkeypatch.setattr(coordinator, "circuit_breaker", MagicMock())
    return coordinator


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_approval", [False, True])
async def test_preparation_failure_persists_its_real_cause_and_complete_approval_notice(
    native_runtime, monkeypatch, prior_approval,
):
    from agents.approval import ApprovalGate
    from agents.verifier import EnvironmentVerificationResult
    from tests.test_approval_target_binding import ACTION

    coordinator = native_runtime
    gate = ApprovalGate()
    request_action = gate.request_action

    def approve_request(*args, **kwargs):
        request = request_action(*args, **kwargs)
        assert gate.callback(
            request.token, "approved", approved_by="test-operator",
            action_digest=request.action_digest,
        )["ok"]
        return request

    monkeypatch.setattr(gate, "request_action", approve_request)
    monkeypatch.setattr(coordinator, "approval_gate", gate)
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://fixture.invalid/never-contact")
    notification = MagicMock(return_value={"success": True})
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "slack_post_update", notification)
    settling = AsyncMock(side_effect=AssertionError("Blocked preparation must not settle"))
    monkeypatch.setattr(coordinator, "settle_environment", settling)
    monkeypatch.setattr("agents.verifier.verify_environment", MagicMock(return_value=(
        EnvironmentVerificationResult(
            scenario_id="", agent_claimed_resolved=False, env_resolved=False,
            verification_status="inconclusive",
        )
    )))

    async def agent(role, _payload):
        if role == "triage":
            final = {"severity": "P2"}
        elif role == "diagnosis":
            final = {"root_cause": {"category": "unknown"}}
        elif role == "remediation":
            if prior_approval:
                approver = coordinator._ACTIVE_ACTION_APPROVER.get()
                decision, permit = await approver(deepcopy(ACTION))
                assert decision["status"] == "approved" and permit is not None
            final = {
                "status": "action_preparation_failed", "outcome": "escalated",
                "executed_actions": [],
            }
        else:
            raise AssertionError("Blocked preparation must not call generated Comms")
        return {"role": role, "trajectory": [], "final": final}

    monkeypatch.setattr(coordinator, "call_agent", AsyncMock(side_effect=agent))
    result = await coordinator.handle_incident(
        {"commonLabels": {"alertname": "FixturePreparationFailure"}},
        incident_id="inc-preparation-cause",
    )
    assert result["comms"]["final"]["status"] == "action_preparation_failed"
    summary = result["comms"]["final"]["summary"]
    assert "could not be prepared" in summary
    assert "contradicted" not in summary
    assert result["resolved"] is False
    assert result["env_resolved"] is False
    saved = json.loads((coordinator.TRAJECTORIES_DIR / "inc-preparation-cause.json").read_text())
    assert saved["comms"] == result["comms"]
    assert notification.call_args.kwargs["summary"] == summary
    settling.assert_not_awaited()
    if prior_approval:
        notice = notification.call_args_list[0].kwargs
        assert "patch=" + json.dumps(ACTION["patch"], sort_keys=True) in notice["summary"]
        assert any("/approval/pending" in item for item in notice["action_items"])


def response_payload(model="fixture/expected-model"):
    return {
        "model": model,
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": '{"severity":"P2"}'},
        }],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["incident", "role"])
async def test_operational_runtime_rejects_injected_completion_before_any_execution(
    native_runtime, monkeypatch, entrypoint,
):
    coordinator = native_runtime
    provider = AsyncMock(return_value=response_payload())
    tool = MagicMock(side_effect=AssertionError("Injected completion reached a tool"))
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "kubectl_get", tool)
    begin = MagicMock(side_effect=AssertionError("Rejected provider admitted an execution"))
    monkeypatch.setattr(coordinator, "get_execution_journal", begin)
    with pytest.raises(ValueError, match="Operational.*completion"):
        if entrypoint == "incident":
            await coordinator.handle_incident(
                {"commonLabels": {"alertname": "FixtureAvailability"}},
                incident_id="inc-injected-operational",
                completion_provider=provider,
            )
        else:
            await coordinator.call_agent(
                "triage", {"incident_id": "inc-injected-operational"},
                completion_provider=provider,
            )
    provider.assert_not_awaited()
    tool.assert_not_called()
    begin.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,category",
    [
        (response_payload("fixture/different-model"), "model_identity_mismatch"),
        (response_payload(None), "model_identity_missing"),
        (response_payload(True), "model_identity_missing"),
        ({"model": "fixture/expected-model", "choices": []}, "malformed_response"),
    ],
)
async def test_native_operational_response_fails_before_tools(
    native_runtime, monkeypatch, payload, category,
):
    coordinator = native_runtime
    response = httpx.Response(
        200, json=payload,
        request=httpx.Request("POST", "https://fixture.invalid/v1/chat/completions"),
    )
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(coordinator, "post_with_retry", post)
    tool = MagicMock(side_effect=AssertionError("Invalid response reached a tool"))
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "kubectl_get", tool)
    with pytest.raises(coordinator.CompletionProviderError) as error:
        await coordinator.call_agent("triage", {"incident_id": "inc-model-identity"})
    assert error.value.failure_category == category
    assert error.value.trajectory[-1]["error_category"] == category
    assert error.value.trajectory[-1]["requested_model"] == coordinator.MODEL_NAME
    assert post.await_count == 1
    tool.assert_not_called()


@pytest.mark.asyncio
async def test_forced_native_conclusion_cannot_consume_the_wrong_model(
    native_runtime, monkeypatch,
):
    coordinator = native_runtime
    response = httpx.Response(
        200, json=response_payload("fixture/different-model"),
        request=httpx.Request("POST", "https://fixture.invalid/v1/chat/completions"),
    )
    monkeypatch.setattr(coordinator, "post_with_retry", AsyncMock(return_value=response))
    telemetry = {}
    result = await coordinator._force_json_conclusion(
        "triage", [], MagicMock(), telemetry=telemetry,
    )
    assert "severity" not in result
    assert result["failure_category"] == "model_identity_mismatch"
    assert telemetry["served_model"] == "fixture/different-model"


@pytest.mark.asyncio
async def test_authenticated_http_double_retains_requested_and_served_identity(
    native_runtime, monkeypatch,
):
    coordinator = native_runtime
    requests = []

    def serve(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer fixture-completion-key"
        assert request.url == "https://fixture.invalid/v1/chat/completions"
        assert json.loads(request.content)["model"] == "fixture/expected-model"
        return httpx.Response(200, json=response_payload())

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(serve)
    monkeypatch.setattr(
        coordinator.httpx, "AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    result = await coordinator.call_agent("triage", {"incident_id": "inc-auth-double"})
    assert result["final"]["severity"] == "P2"
    exchange = next(
        row for row in result["trajectory"] if row.get("kind") == "inference_exchange"
    )
    assert exchange["requested_model"] == exchange["served_model"] == coordinator.MODEL_NAME
    assert exchange["provider_injected"] is False
    assert exchange["status"] == "completed"
    assert len(requests) == 1
    assert "fixture-completion-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_native_transport_failure_has_safe_category_and_no_fallback(
    native_runtime, monkeypatch,
):
    coordinator = native_runtime
    post = AsyncMock(side_effect=httpx.ReadTimeout(
        "Authorization: Bearer fixture-must-not-persist",
    ))
    monkeypatch.setattr(coordinator, "post_with_retry", post)
    with pytest.raises(coordinator.CompletionProviderError) as error:
        await coordinator.call_agent("triage", {"incident_id": "inc-native-outage"})
    assert error.value.failure_category == "ReadTimeout"
    assert "must-not-persist" not in str(error.value)
    assert "must-not-persist" not in json.dumps(error.value.trajectory)
    assert "must-not-persist" not in json.dumps(coordinator.audit_log.tail(100))
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_wrong_model_forced_conclusion_stops_the_incident_and_preserves_failure(
    native_runtime, monkeypatch,
):
    coordinator = native_runtime
    initial = response_payload()
    initial["choices"][0]["message"]["content"] = "A fixture requires a forced conclusion."
    responses = [
        httpx.Response(
            200, json=payload,
            request=httpx.Request("POST", "https://fixture.invalid/v1/chat/completions"),
        )
        for payload in (initial, response_payload("fixture/different-model"))
    ]
    post = AsyncMock(side_effect=responses)
    monkeypatch.setattr(coordinator, "post_with_retry", post)
    with pytest.raises(coordinator.CompletionProviderError) as error:
        await coordinator.handle_incident(
            {"commonLabels": {"alertname": "FixtureAvailability"}},
            incident_id="inc-forced-identity",
        )
    assert error.value.failure_category == "model_identity_mismatch"
    assert post.await_count == 2
    path = next(coordinator.TRAJECTORIES_DIR.glob("*.partial-*.json"))
    partial = json.loads(path.read_text())
    assert partial["resolved"] is False
    assert partial["failure"]["category"] == "model_identity_mismatch"
    assert partial["diagnosis"] is None and partial["remediation"] is None
    assert partial["failed_role_trajectory"][-1]["served_model"] == "fixture/different-model"
