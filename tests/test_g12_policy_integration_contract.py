"""Local control-flow proof for the Stage 12 checkpoint policy path."""

from __future__ import annotations

import json

import pytest

from agents.coordinator import handle_incident
from agents.policy_remediation import run_policy_remediation
from training.grpo_environment import DirectPolicyEnvironment


class ScriptedPolicy:
    def __init__(self) -> None:
        self.states = []

    def generate(self, state, *, seed, generation_config):
        self.states.append(state)
        return json.dumps(
            {
                "tool": "chaos_stop_experiment",
                "arguments": {
                    "kind": "StressChaos",
                    "name": f"observed-{len(self.states)}",
                    "namespace": "chaos-mesh",
                },
                "agent_claimed_resolved": True,
            }
        )


@pytest.mark.asyncio
async def test_policy_receives_verifier_state_without_benchmark_truth() -> None:
    calls = []
    policy = ScriptedPolicy()

    def execute(**arguments):
        calls.append(("mutate", arguments["name"]))
        return {"success": True, "name": arguments["name"]}

    def observe():
        name = f"observed-{1 + len([entry for entry in calls if entry[0] == 'mutate'])}"
        calls.append(("observe", name))
        return {
            "success": True,
            "observation_status": "observed",
            "active_experiments": [{
                "kind": "StressChaos",
                "name": name,
                "namespace": "chaos-mesh",
            }],
        }

    def verify(**kwargs):
        assert kwargs["scenario_id"] == "single_fault/sf-002"
        calls.append(("verify", kwargs["incident_context"]["tool_result"]["name"]))
        return {"env_resolved": len([c for c in calls if c[0] == "verify"]) == 2}

    environment = DirectPolicyEnvironment(
        tool_registry={"chaos_stop_experiment": execute, "chaos_list_experiments": observe},
        policy_check=lambda role, tool, arguments, state: None,
        verifier=verify,
    )
    result = await run_policy_remediation(
        policy=policy,
        state={
            "incident_id": "inc-original",
            "alert": {"commonLabels": {"alertname": "HighCPUUsage"}},
            "triage": {"severity": "P1"},
            "approval": {"decision": "approved"},
            "recommended_runbooks": [{"runbook_id": "RB-CPU"}],
            "expected_root_cause": "hidden answer",
            "scenario_id": "single_fault/sf-002",
        },
        scenario_id="single_fault/sf-002",
        environment=environment,
        seed=7,
        generation_config={"do_sample": False},
    )

    assert calls == [
        ("observe", "observed-1"),
        ("mutate", "observed-1"),
        ("verify", "observed-1"),
        ("observe", "observed-2"),
        ("mutate", "observed-2"),
        ("verify", "observed-2"),
    ]
    assert len(policy.states) == 2
    assert "expected_root_cause" not in policy.states[0]
    assert "scenario_id" not in policy.states[0]
    assert policy.states[1]["verification"]["env_resolved"] is False
    assert result["final"]["incident_id"] == "inc-original"
    assert result["final"]["env_resolved"] is True
    assert len(result["final"]["executed_actions"]) == 2
    assert result["policy_steps"][0]["parsed_action"]["arguments"]["name"] == "observed-1"
    assert result["policy_steps"][1]["next_state"]["env_resolved"] is True


@pytest.mark.asyncio
async def test_blocked_policy_action_stops_without_mutation() -> None:
    executed = []
    environment = DirectPolicyEnvironment(
        tool_registry={"chaos_stop_experiment": lambda **kwargs: executed.append(kwargs)},
        policy_check=lambda role, tool, arguments, state: None,
    )
    result = await run_policy_remediation(
        policy=ScriptedPolicy(),
        state={"incident_id": "inc-1", "triage": {"severity": "P1"}},
        scenario_id="single_fault/sf-002",
        environment=environment,
        seed=7,
        generation_config={"do_sample": False},
    )
    assert executed == []
    assert result["final"]["status"] == "blocked"
    assert result["final"]["terminal_block"]["category"] == "approval_required"
    assert len(result["policy_steps"]) == 1


@pytest.mark.asyncio
async def test_full_incident_path_uses_policy_action_and_verified_comms(monkeypatch, tmp_path) -> None:
    import agents.verifier as verifier_module
    import training.grpo_environment as environment_module
    from agents import coordinator

    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-placeholder-audit-secret")
    monkeypatch.setenv("ATLASOPS_LIVE_JUDGE", "0")
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path / "trajectories")
    executed = []
    policy_states = []
    comms_inputs = []

    class Policy:
        def generate(self, state, *, seed, generation_config):
            policy_states.append(state)
            return json.dumps(
                {
                    "tool": "kubectl_scale",
                    "arguments": {
                        "deployment": "paymentservice",
                        "replicas": 2,
                        "namespace": "default",
                    },
                    "agent_claimed_resolved": True,
                }
            )

    def execute(**arguments):
        executed.append(arguments)
        return {"success": True, "applied": arguments}

    def verify(**kwargs):
        return type(
            "Verified",
            (),
            {
                "env_resolved": len(executed) == 2,
                "verification_status": "verified" if len(executed) == 2 else "unverified",
                "to_dict": lambda self: {
                    "env_resolved": len(executed) == 2,
                    "status": "verified" if len(executed) == 2 else "unverified",
                },
            },
        )()

    async def fake_agent(role, user_input, max_turns=10):
        if role == "triage":
            return {
                "role": role,
                "trajectory": [],
                "final": {
                    "severity": "P2",
                    "affected_services": ["paymentservice"],
                    "title": "Payment latency",
                },
            }
        if role == "diagnosis":
            return {
                "role": role,
                "trajectory": [],
                "final": {"root_cause": "Observed CPU saturation", "confidence": 0.8},
            }
        if role == "comms":
            comms_inputs.append(user_input)
            return {"role": role, "trajectory": [], "final": {"summary": "verified"}}
        pytest.fail(f"Unexpected LLM remediation call: {role}")

    async def fake_settle(**kwargs):
        return {"status": "observed"}

    monkeypatch.setattr(coordinator, "call_agent", fake_agent)
    monkeypatch.setattr(coordinator, "settle_environment", fake_settle)
    monkeypatch.setattr(environment_module, "TOOL_REGISTRY", {"kubectl_scale": execute})
    monkeypatch.setattr(environment_module, "verify_environment", verify)
    monkeypatch.setattr(verifier_module, "verify_environment", verify)

    alert = {
        "commonLabels": {"alertname": "HighCPUUsage", "service": "paymentservice"},
        "expected_root_cause": "hidden benchmark answer",
    }
    result = await handle_incident(
        alert,
        incident_id="inc-original",
        scenario_id="single_fault/sf-002",
        remediation_policy=Policy(),
    )

    assert result["incident_id"] == "inc-original"
    assert len(executed) == 2
    assert len(policy_states) == 2
    assert policy_states[0]["recommended_runbooks"]
    assert policy_states[1]["verification"]["env_resolved"] is False
    assert "expected_root_cause" not in json.dumps(policy_states)
    assert "single_fault/sf-002" not in json.dumps(policy_states)
    assert result["remediation"]["final"]["executed_actions"][0]["arguments"] == executed[0]
    assert result["remediation"]["final"]["evidence_class"] == "NON_EMPIRICAL"
    assert result["env_resolved"] is True
    assert comms_inputs[0]["env_resolved"] is True
