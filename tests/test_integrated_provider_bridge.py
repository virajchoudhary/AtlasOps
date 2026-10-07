"""NON_EMPIRICAL check of the real provider/coordinator composition."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

import agents.coordinator as coordinator
from tests.test_integrated_completion_provider import (
    _inconclusive_verification,
    _no_settle,
    safe_runtime as safe_runtime,
)
from tests.test_integrated_inference import rig as rig


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["base", "sft"])
async def test_paired_provider_accepts_all_four_coordinator_roles(
    arm, rig, safe_runtime, monkeypatch
):
    monkeypatch.setattr(coordinator, "settle_environment", _no_settle)
    monkeypatch.setattr(
        "agents.verifier.verify_environment",
        lambda **_kwargs: _inconclusive_verification(),
    )
    monkeypatch.setattr(coordinator, "post_with_retry", AsyncMock())
    calls = []
    active_role = None
    responses = {
        "triage": {"severity": "P3", "affected_services": ["frontend"], "title": "Fixture"},
        "diagnosis": {"root_cause": "unknown", "confidence": 0.1},
        "remediation": {"outcome": "unresolved", "proposed_actions": []},
        "comms": {"slack_posted": False, "summary": "Unresolved fixture"},
    }

    def decode(_token_ids, **_kwargs):
        return json.dumps(responses[active_role])

    monkeypatch.setattr(rig.tokenizer, "decode", decode)
    provider = rig.engine.provider(arm)

    async def complete(role, request):
        nonlocal active_role
        active_role = role
        calls.append(role)
        return await provider(role, request)

    result = await coordinator.handle_incident(
        {
            "commonLabels": {"alertname": "Fixture", "service": "frontend"},
            "alerts": [{"labels": {"service": "frontend"}}],
        },
        incident_id=f"inc-provider-bridge-{arm}",
        scenario_id="single_fault/provider-fixture",
        completion_provider=complete,
    )
    await rig.engine.close()
    assert set(calls) == {"triage", "diagnosis", "remediation", "comms"}
    assert result["evidence_class"] == "NON_EMPIRICAL"
    assert result["resolved"] is False
    coordinator.post_with_retry.assert_not_awaited()
    assert rig.model.calls and all(
        enabled is (arm == "sft") for enabled in rig.model.calls
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approved", "rejected", "timeout"])
@pytest.mark.parametrize("environment_recovered", [False, True])
async def test_direct_action_provider_composes_with_exact_p1_gate_and_verifier(
    rig, safe_runtime, monkeypatch, decision, environment_recovered,
):
    from agents.approval import ApprovalGate
    from bench.integrated_inference import DirectActionCompletionPolicy
    from training import grpo_environment

    gate = ApprovalGate(timeout_seconds=0.001 if decision == "timeout" else 10)
    monkeypatch.setattr(coordinator, "approval_gate", gate)
    monkeypatch.setenv("ATLASOPS_RL_POLICY_EXECUTE_ACTIONS", "1")
    monkeypatch.setenv("KUBECONFIG_CONTEXT", "kind-atlasops-synthetic")
    monkeypatch.setattr(coordinator, "settle_environment", _no_settle)
    monkeypatch.setattr(coordinator, "post_with_retry", AsyncMock(side_effect=AssertionError()))
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "slack_post_update", lambda **_: {"success": True})
    action = {
        "tool": "kubectl_scale",
        "arguments": {"deployment": "paymentservice", "namespace": "default", "replicas": 2},
        "agent_claimed_resolved": True,
    }
    pending_actions = []
    mutations = []
    request_action = gate.request_action

    def request(**kwargs):
        pending = request_action(**kwargs)
        pending_actions.append(pending.action)
        assert mutations == [] or decision == "approved"
        if decision != "timeout":
            asyncio.get_running_loop().call_soon(
                gate.callback, pending.token, decision, "test-operator", "Fixture decision",
            )
        return pending

    monkeypatch.setattr(gate, "request_action", request)

    class Verification:
        env_resolved = environment_recovered
        verification_status = "passed" if environment_recovered else "failed"

        def to_dict(self):
            return {"env_resolved": self.env_resolved,
                    "verification_status": self.verification_status}

    def mutate(**arguments):
        assert gate.pending() == []
        assert decision == "approved"
        mutations.append(arguments)
        return {"success": True}

    async def settle():
        return {}

    monkeypatch.setattr("agents.verifier.verify_environment", lambda **_: Verification())
    monkeypatch.setattr("training.sft_rendering.role_tool_schemas", lambda _: [
        {"type": "function", "function": {"name": "kubectl_scale", "parameters": {}}},
    ])
    original_environment = grpo_environment.DirectPolicyEnvironment

    def make_environment(**kwargs):
        kwargs.pop("settle", None)
        return original_environment(
            tool_registry={"kubectl_scale": mutate}, settle=settle,
            verifier=lambda **_: Verification(), **kwargs,
        )

    monkeypatch.setattr(grpo_environment, "DirectPolicyEnvironment", make_environment)
    active_role = None
    comms_inputs = []

    def decode(_ids, **_kwargs):
        system = rig.tokenizer.templates[-1][0][0]["content"]
        if system.startswith("You select one bounded incident remediation action"):
            assert "tools" not in rig.tokenizer.templates[-1][1]
            return json.dumps(action)
        return json.dumps({
            "triage": {"severity": "P2", "affected_services": ["paymentservice"], "title": "CPU"},
            "diagnosis": {"root_cause": "Observed CPU saturation", "confidence": 0.8},
            "comms": {"summary": "Fixture communication"},
        }[active_role])

    monkeypatch.setattr(rig.tokenizer, "decode", decode)

    async def provider(role, request):
        nonlocal active_role
        active_role = role
        if role == "comms":
            comms_inputs.append(json.loads(request["messages"][1]["content"]))
        return await rig.base(role, request)

    result = await coordinator.handle_incident(
        {"commonLabels": {"service": "paymentservice", "severity": "critical", "alertname": "CPU"}},
        incident_id=f"inc-direct-provider-{decision}-{environment_recovered}",
        scenario_id="single_fault/sf-002",
        remediation_policy=DirectActionCompletionPolicy(rig.base),
        completion_provider=provider,
        policy_seed=1337, policy_generation_config={"max_new_tokens": 512, "temperature": 0},
    )
    await rig.engine.close()
    approved = decision == "approved"
    assert result["approval"]["triage_severity"] == "P2"
    assert result["approval"]["severity"] == "P1"
    assert result["approval"]["decision"] == decision
    assert gate.pending() == []
    assert len(pending_actions) == (3 if approved and not environment_recovered else 1)
    assert all(proposal == {"tool": action["tool"], "arguments": action["arguments"]}
               for proposal in pending_actions)
    assert len(mutations) == (len(pending_actions) if approved else 0)
    assert result["env_resolved"] is environment_recovered
    assert result["resolved"] is (approved and environment_recovered)
    if approved:
        assert comms_inputs[0]["env_resolved"] is environment_recovered
        assert result["remediation"]["final"]["status"] == (
            "resolved" if environment_recovered else "unresolved"
        )
    else:
        assert comms_inputs == []
        assert result["comms"]["final"]["status"] == f"approval_{decision}"
    persisted = json.loads(
        (safe_runtime / f"{result['incident_id']}.json").read_text()
    )
    assert persisted == result
    for step in result["remediation"]["policy_steps"]:
        assert json.loads(step["raw_policy_output"]) == action
    coordinator.post_with_retry.assert_not_awaited()
