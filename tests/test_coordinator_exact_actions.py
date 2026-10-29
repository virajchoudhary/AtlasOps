"""Model-free evidence for P1 approval after exact tool selection."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import pytest

from agents.approval import ApprovalGate


@pytest.fixture
def runtime(monkeypatch):
    import agents.coordinator as coordinator

    monkeypatch.setattr(coordinator, "require_audit_log", MagicMock())
    monkeypatch.setattr(coordinator, "audit_log", MagicMock())
    monkeypatch.setattr(coordinator, "thought_emit", MagicMock())
    monkeypatch.setattr(coordinator, "circuit_breaker", MagicMock())
    gate = ApprovalGate(timeout_seconds=0.1)
    monkeypatch.setattr(coordinator, "approval_gate", gate)
    mutation = MagicMock(return_value={"success": True})
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "kubectl_scale", mutation)
    response = MagicMock()
    response.json.return_value = {
        "model": coordinator.MODEL_NAME,
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": "selected-action",
                    "type": "function",
                    "function": {
                        "name": "kubectl_scale",
                        "arguments": json.dumps({
                            "deployment": "frontend", "replicas": 2
                        }),
                    },
                }],
            },
        }],
    }
    monkeypatch.setattr(
        coordinator, "post_with_retry", AsyncMock(return_value=response)
    )
    return coordinator, gate, mutation


def test_unapproved_p1_tool_selection_never_mutates(runtime):
    coordinator, gate, mutation = runtime
    result = asyncio.run(coordinator.call_agent(
        "remediation",
        {"incident_id": "inc-exact", "triage": {"severity": "P1"}},
        max_turns=1,
    ))
    mutation.assert_not_called()
    assert result["final"]["status"] == "approval_timeout"
    assert result["final"]["executed_actions"] == []
    assert gate.pending() == []


def test_operational_uncertain_mutation_stops_without_a_second_model_turn(runtime, monkeypatch):
    from agents.action_execution import PreparedAction, ResourceIdentity

    coordinator, gate, mutation = runtime
    gate.timeout_seconds = 30
    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    action = PreparedAction(
        "kubectl_scale", '{"deployment":"frontend","replicas":2}',
        ResourceIdentity(
            "https://fixture-kubernetes.invalid", "fixture-kind", "apps/v1",
            "Deployment", "default", "frontend", "frontend-uid", "42",
        ),
        '[{"op":"test","path":"/metadata/uid","value":"frontend-uid"},'
        '{"op":"test","path":"/metadata/resourceVersion","value":"42"},'
        '{"op":"replace","path":"/spec/replicas","value":2}]',
    )
    monkeypatch.setattr(coordinator, "prepare_action", MagicMock(return_value=action))
    execute = MagicMock(return_value={
        "success": False, "error_class": "mutation_uncertain", "status_code": 503,
    })
    monkeypatch.setattr(coordinator, "execute_prepared_action", execute)
    note = MagicMock()

    async def run():
        async def approve(selected):
            request = gate.request_action(
                incident_id="inc-uncertain", severity="P1", action=selected
            )
            assert gate.callback(
                request.token, "approved", approved_by="operator",
                action_digest=request.action_digest,
            )["ok"]
            return await gate.wait_for_action_decision(
                request.incident_id, request_token=request.token
            )

        progress_token = coordinator.current_execution.set(SimpleNamespace(note=note))
        approver_token = coordinator._ACTIVE_ACTION_APPROVER.set(approve)
        observer_token = coordinator._ACTIVE_MUTATION_OBSERVER.set(AsyncMock(return_value={
            "env_resolved": True, "verification_status": "passed",
        }))
        try:
            with pytest.raises(RuntimeError, match="outcome is uncertain"):
                await coordinator.call_agent(
                    "remediation",
                    {"incident_id": "inc-uncertain", "triage": {"severity": "P1"}},
                    max_turns=2,
                )
        finally:
            coordinator.current_execution.reset(progress_token)
            coordinator._ACTIVE_ACTION_APPROVER.reset(approver_token)
            coordinator._ACTIVE_MUTATION_OBSERVER.reset(observer_token)

    asyncio.run(run())
    execute.assert_called_once_with(action)
    mutation.assert_not_called()
    assert coordinator.post_with_retry.await_count == 1
    assert note.call_args.args == ("applying", "mutation_uncertain")


def test_operational_runtime_cannot_bypass_atomic_adapters_with_rl_policy(runtime, monkeypatch):
    coordinator, _gate, mutation = runtime
    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    monkeypatch.setenv("KUBECONFIG_CONTEXT", "fixture-kind")
    qualifier = MagicMock(side_effect=RuntimeError("Unsafe backend was admitted"))
    monkeypatch.setattr(coordinator, "_require_rl_policy_live_context", qualifier)
    with pytest.raises(ValueError, match="Operational runtime requires atomic agent actions"):
        asyncio.run(coordinator.handle_incident(
            {"commonLabels": {"alertname": "SafetyTest"}},
            incident_id="inc-no-policy-bypass", remediation_policy=object(),
        ))
    mutation.assert_not_called()
    coordinator.post_with_retry.assert_not_awaited()
    qualifier.assert_not_called()


@pytest.mark.parametrize("channel", ["argument", "alert"])
def test_operational_runtime_cannot_select_a_frozen_evaluation_channel(runtime, monkeypatch, channel):
    coordinator, _gate, mutation = runtime
    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    admission = MagicMock(side_effect=AssertionError("Invalid policy reached admission"))
    monkeypatch.setattr(coordinator, "get_execution_journal", admission)
    alert = {
        "commonLabels": {"alertname": "AtlasOpsOnlineBoutiqueDeploymentUnavailable"},
    }
    kwargs = {}
    if channel == "argument":
        kwargs["scenario_id"] = "single_fault/sf-002"
    else:
        alert["scenario_id"] = "single_fault/sf-002"
    with pytest.raises(ValueError, match="frozen evaluation"):
        asyncio.run(coordinator.handle_incident(
            alert, incident_id="inc-no-evaluation-bypass", **kwargs,
        ))
    admission.assert_not_called()
    mutation.assert_not_called()
    coordinator.post_with_retry.assert_not_awaited()


def test_p1_receipt_contains_the_selected_command_before_execution(runtime):
    coordinator, gate, mutation = runtime

    async def decide():
        task = asyncio.create_task(coordinator.call_agent(
            "remediation",
            {"incident_id": "inc-exact", "triage": {"severity": "P1"}},
            max_turns=1,
        ))
        try:
            for _ in range(100):
                pending = gate.pending()
                if pending or task.done():
                    break
                await asyncio.sleep(0.001)
            assert pending, "P1 did not create an exact-action approval request"
            request = pending[0]
            assert request["action"] == {
                "tool": "kubectl_scale",
                "arguments": {"deployment": "frontend", "replicas": 2},
            }
            mutation.assert_not_called()
            assert gate.callback(
                request["token"], "rejected", approved_by="operator"
            )["ok"]
            result = await task
            assert result["final"]["status"] == "approval_rejected"
            assert result["final"]["executed_actions"] == []
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    asyncio.run(decide())
    mutation.assert_not_called()
