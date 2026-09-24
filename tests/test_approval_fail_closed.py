"""SAFETY-01: real approval/coordinator/dispatch flow with mocked external edges."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.parametrize("severity,decision,alert_severity", [
    ("P1", "approved", None), ("P1", "rejected", None),
    ("P1", "timeout", None), ("P1", "missing", None),
    ("P2", None, None), ("P3", None, None),
    ("P2", "timeout", "critical"),
])
@pytest.mark.parametrize("environment_recovered", [False, True])
def test_approval_controls_mutating_dispatch_and_persisted_truth(
    monkeypatch, tmp_path, severity, decision, alert_severity, environment_recovered,
):
    import agents.coordinator as coord
    import agents.verifier as verifier
    from agents.approval import ApprovalGate
    from agents.circuit_breaker import CircuitBreaker
    from agents.tool_policy import CLUSTER_MUTATING_TOOLS
    from recommender.hybrid import HybridRecommender

    monkeypatch.setenv("ATLASOPS_LIVE_JUDGE", "0")
    monkeypatch.setenv("ATLASOPS_USE_HF_INFERENCE", "0")
    # Exercise closure notification, with every tool replaced below.
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.invalid/mock")
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(coord, "TRAJECTORIES_DIR", tmp_path)
    monkeypatch.setattr(coord, "require_audit_log", MagicMock())
    audit = MagicMock()
    monkeypatch.setattr(coord, "audit_log", audit)
    monkeypatch.setattr(coord, "thought_emit", MagicMock())
    breaker = CircuitBreaker()
    finish = MagicMock(wraps=breaker.finish_incident)
    monkeypatch.setattr(breaker, "finish_incident", finish)
    monkeypatch.setattr(coord, "circuit_breaker", breaker)
    monkeypatch.setattr("agents.tools.comms.discord_scenario_run_ping", MagicMock())
    # No recommender fitting, model inference, verifier infrastructure, or real tools.
    monkeypatch.setattr(HybridRecommender, "fit", MagicMock(side_effect=RuntimeError("mock-only")))
    monkeypatch.setattr(HybridRecommender, "load_checkpoint", MagicMock(side_effect=RuntimeError("mock-only")))
    tool_spies = {name: MagicMock(return_value={"success": True}) for name in coord.TOOL_REGISTRY}
    monkeypatch.setattr(coord, "TOOL_REGISTRY", tool_spies)
    monkeypatch.setattr(coord, "settle_environment", AsyncMock(return_value={}))
    verify = MagicMock(return_value=verifier.EnvironmentVerificationResult(
        scenario_id="", agent_claimed_resolved=False,
        env_resolved=environment_recovered, verification_status="mock",
    ))
    monkeypatch.setattr(verifier, "verify_environment", verify)

    gate = ApprovalGate(timeout_seconds=0.001)
    request = gate.request

    def request_with_decision(*args, **kwargs):
        req = request(*args, **kwargs)
        if decision in {"approved", "rejected"}:
            assert gate.callback(req.token, decision, approved_by="test-operator")["ok"]
        elif decision == "missing":
            gate._clear(req)
        return req

    monkeypatch.setattr(gate, "request", request_with_decision)
    monkeypatch.setattr(coord, "approval_gate", gate)

    def response(message):
        result = MagicMock()
        result.json.return_value = {"choices": [{"message": message, "finish_reason": "stop"}]}
        return result

    # If remediation is reached it really dispatches a mutator before claiming success.
    model = AsyncMock(side_effect=[
        response({"content": "", "tool_calls": [{
            "id": "mutation", "type": "function", "function": {
                "name": "kubectl_scale", "arguments": json.dumps({"deployment": "frontend", "replicas": 2}),
            },
        }]}),
        response({"content": json.dumps({"outcome": "resolved", "status": "resolved"})}),
    ])
    monkeypatch.setattr(coord, "post_with_retry", model)
    real_call_agent = coord.call_agent

    async def agent(role, payload):
        if role == "remediation":
            return await real_call_agent(role, payload)
        final = {"severity": severity} if role == "triage" else {"root_cause": "bad deploy"}
        # Hostile self-claims cannot authorize execution or overwrite verifier truth.
        final.update(status="resolved", outcome="resolved", summary="Incident resolved successfully.")
        return {"role": role, "trajectory": [], "final": final}

    calls = AsyncMock(side_effect=agent)
    monkeypatch.setattr(coord, "call_agent", calls)
    labels = {"alertname": "SafetyTest"}
    if alert_severity:
        labels["severity"] = alert_severity
    result = asyncio.run(coord.handle_incident(
        {"commonLabels": labels}, incident_id="inc-safety",
    ))
    persisted = json.loads((tmp_path / "inc-safety.json").read_text(encoding="utf-8"))
    assert persisted == result
    assert result["approval"].get("decision") == decision
    assert result["env_resolved"] is environment_recovered
    assert result["verification"]["env_resolved"] is environment_recovered
    assert gate.pending() == []
    allowed = decision == "approved" or (
        severity in {"P2", "P3"} and alert_severity != "critical"
    )
    if alert_severity == "critical":
        assert result["approval"]["severity"] == "P1"
        assert result["approval"]["triage_severity"] == "P2"
    assert persisted["resolved"] is (environment_recovered and allowed)

    # The benchmark consumer must preserve the operational decision, even if the
    # environment recovered independently. All benchmark infrastructure is mocked.
    from bench import runner
    monkeypatch.setattr(runner, "apply_chaos", MagicMock(return_value=True))
    monkeypatch.setattr(runner, "wait_for_alert", MagicMock(return_value={}))
    monkeypatch.setattr(runner, "handle_incident", AsyncMock(return_value=persisted))
    monkeypatch.setattr(runner, "judge_trajectory", AsyncMock(return_value={}))
    monkeypatch.setattr(runner, "reset_cluster", MagicMock())
    episode = asyncio.run(runner.run_scenario("single_fault/safety-test"))
    assert episode["env_resolved"] is environment_recovered
    assert episode["resolved"] is (environment_recovered and allowed)
    summary = runner.compute_summary([episode], "safety-test", "mock")
    assert summary["resolution_rate"] == float(environment_recovered and allowed)
    if allowed:
        tool_spies["kubectl_scale"].assert_called_once_with(deployment="frontend", replicas=2)
        assert [c.args[0] for c in calls.call_args_list] == ["triage", "diagnosis", "remediation", "comms"]
        assert result["agent_claimed_resolved"] is True
        assert finish.call_args.kwargs["resolved"] is environment_recovered
    else:
        for name in CLUSTER_MUTATING_TOOLS:
            tool_spies[name].assert_not_called()
        model.assert_not_awaited()
        assert [c.args[0] for c in calls.call_args_list] == ["triage", "diagnosis"]
        assert result["remediation"]["final"]["status"] == f"approval_{decision}"
        assert result["remediation"]["final"]["approval"]["status"] == decision
        assert result["remediation"]["final"]["executed_actions"] == []
        assert result["agent_claimed_resolved"] is False
        assert verify.call_args.kwargs["agent_claimed_resolved"] is False
        assert result["comms"]["final"]["status"] == f"approval_{decision}"
        closure = tool_spies["slack_post_update"].call_args.kwargs
        assert "Blocked" in closure["title"]
        assert "not executed" in closure["summary"]
        assert finish.call_args.kwargs["resolved"] is False
        decisions = [c.kwargs for c in audit.record.call_args_list if c.kwargs.get("action_type") == "approval_decision"]
        assert len(decisions) == 1
        assert decisions[0]["policy_check"] == "approval_denied"
        assert decisions[0]["result_summary"] == decision
