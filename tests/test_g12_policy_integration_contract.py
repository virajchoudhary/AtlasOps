"""Local control-flow proof for the Stage 12 checkpoint policy path."""

from __future__ import annotations

import json
import os

import pytest

from agents.coordinator import handle_incident
from agents.policy_remediation import run_policy_remediation
from config.g4_protocol import METRICS_SERVER_CONTEXT
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
        execute_live_chaos=True,
        kube_context="kind-atlasops-synthetic",
    )
    result = await run_policy_remediation(
        policy=policy,
        state={
            "incident_id": "inc-original",
            "alert": {"commonLabels": {"alertname": "HighCPUUsage"}},
            "triage": {"severity": "P2"},
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
    assert [step["environment_status"] for step in result["policy_steps"]] == [
        "ok", "ok"
    ]
    assert result["policy_steps"][0]["parsed_action"]["arguments"]["name"] == "observed-1"
    assert result["policy_steps"][1]["next_state"]["env_resolved"] is True


@pytest.mark.asyncio
async def test_blocked_policy_action_stops_without_mutation() -> None:
    executed = []
    environment = DirectPolicyEnvironment(
        tool_registry={"chaos_stop_experiment": lambda **kwargs: executed.append(kwargs)},
        policy_check=lambda role, tool, arguments, state: None,
        execute_live_chaos=True,
        kube_context="kind-atlasops-synthetic",
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
    assert result["policy_steps"][0]["environment_status"] == "blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("triage_severity", "alert_severity"),
    [("P2", "warning"), ("P1", "critical")],
)
@pytest.mark.parametrize(
    ("recommender_mode", "expected_recommender_status"),
    [
        ("disabled", "disabled"),
        ("unavailable", "unavailable"),
        ("executed", "executed"),
    ],
)
async def test_full_incident_path_uses_policy_action_and_verified_comms(
    monkeypatch,
    tmp_path,
    triage_severity,
    alert_severity,
    recommender_mode,
    expected_recommender_status,
) -> None:
    import asyncio

    import agents.verifier as verifier_module
    import training.grpo_environment as environment_module
    from agents import coordinator

    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-placeholder-audit-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("ATLASOPS_LIVE_JUDGE", "0")
    monkeypatch.setenv("ATLASOPS_RL_POLICY_EXECUTE_ACTIONS", "1")
    monkeypatch.setenv("KUBECONFIG_CONTEXT", METRICS_SERVER_CONTEXT)
    if recommender_mode == "disabled":
        monkeypatch.delenv("ATLASOPS_RECOMMENDER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ATLASOPS_RECOMMENDER_ENABLED", "1")
        if recommender_mode == "unavailable":
            monkeypatch.setenv(
                "ATLASOPS_RECOMMENDER_CHECKPOINT",
                str(tmp_path / "missing-recommender.json"),
            )
        else:
            monkeypatch.delenv("ATLASOPS_RECOMMENDER_CHECKPOINT", raising=False)
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
        assert os.environ["KUBECONFIG_CONTEXT"] == METRICS_SERVER_CONTEXT
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
                    "severity": triage_severity,
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

    approved_actions = []
    if triage_severity == "P1":
        original_request = coordinator.approval_gate.request_action

        def request_and_approve(**kwargs):
            request = original_request(**kwargs)
            approved_actions.append(request)
            asyncio.get_running_loop().call_soon(
                coordinator.approval_gate.callback,
                request.token,
                "approved",
                "test-operator",
                "unit-test approval",
            )
            return request

        monkeypatch.setattr(
            coordinator.approval_gate, "request_action", request_and_approve
        )

    monkeypatch.setattr(coordinator, "call_agent", fake_agent)
    monkeypatch.setattr(coordinator, "settle_environment", fake_settle)
    monkeypatch.setitem(
        coordinator.TOOL_REGISTRY,
        "slack_post_update",
        lambda **_kwargs: {"success": True},
    )
    monkeypatch.setattr(environment_module, "TOOL_REGISTRY", {"kubectl_scale": execute})
    monkeypatch.setattr(environment_module, "verify_environment", verify)
    monkeypatch.setattr(verifier_module, "verify_environment", verify)

    alert = {
        "commonLabels": {
            "alertname": "HighCPUUsage",
            "service": "paymentservice",
            "severity": alert_severity,
        },
        "expected_root_cause": "hidden benchmark answer",
    }
    result = await handle_incident(
        alert,
        incident_id="inc-original",
        scenario_id="single_fault/sf-002",
        remediation_policy=Policy(),
    )

    assert result["incident_id"] == "inc-original"
    assert result["recommender"]["status"] == expected_recommender_status
    assert result["recommender"]["recommended_runbooks"] == policy_states[0][
        "recommended_runbooks"
    ]
    persisted = json.loads(
        (tmp_path / "trajectories" / "inc-original.json").read_text(encoding="utf-8")
    )
    assert persisted["recommender"]["status"] == expected_recommender_status
    assert len(executed) == 2
    assert len(policy_states) == 2
    assert bool(policy_states[0]["recommended_runbooks"]) is (
        recommender_mode == "executed"
    )
    assert policy_states[1]["verification"]["env_resolved"] is False
    assert "expected_root_cause" not in json.dumps(policy_states)
    assert "single_fault/sf-002" not in json.dumps(policy_states)
    assert result["remediation"]["final"]["executed_actions"][0]["arguments"] == executed[0]
    assert result["remediation"]["final"]["evidence_class"] == "NON_EMPIRICAL"
    assert result["env_resolved"] is True
    assert comms_inputs[0]["env_resolved"] is True
    if triage_severity == "P1":
        assert result["approval"]["decision"] == "approved"
        assert result["approval"]["approved_by"] == "test-operator"
        assert len(approved_actions) == 2
        assert all(
            action.action["tool"] == "kubectl_scale"
            and action.action["arguments"] == {
                "deployment": "paymentservice",
                "namespace": "default",
                "replicas": 2,
            }
            for action in approved_actions
        )
        assert result["approval"]["action_digest"] == approved_actions[-1].action_digest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("severity", "alert_severity"),
    [("P2", "warning"), ("P3", "info")],
)
async def test_injected_rl_policy_without_live_opt_in_calls_no_agent_or_tool(
    monkeypatch, tmp_path, severity, alert_severity
):
    from agents import coordinator

    monkeypatch.delenv("ATLASOPS_RL_POLICY_EXECUTE_ACTIONS", raising=False)
    monkeypatch.delenv("KUBECONFIG_CONTEXT", raising=False)
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path / "trajectories")
    agent_calls = []
    policy_calls = []
    tool_calls = []

    class Policy:
        def generate(self, state, *, seed, generation_config):
            policy_calls.append(state)
            return json.dumps({
                "tool": "kubectl_scale",
                "arguments": {
                    "deployment": "paymentservice",
                    "replicas": 2,
                    "namespace": "default",
                },
                "agent_claimed_resolved": False,
            })

    async def fake_agent(role, *_args, **_kwargs):
        agent_calls.append(role)
        return {"role": role, "trajectory": [], "final": {}}

    monkeypatch.setattr(coordinator, "call_agent", fake_agent)
    monkeypatch.setitem(
        coordinator.TOOL_REGISTRY,
        "kubectl_scale",
        lambda **arguments: tool_calls.append(arguments) or {"success": True},
    )

    with pytest.raises(RuntimeError, match="live policy opt-in"):
        await handle_incident(
            {
                "commonLabels": {
                    "alertname": "HighCPUUsage",
                    "service": "paymentservice",
                    "severity": alert_severity,
                }
            },
            incident_id="inc-no-live-opt-in",
            scenario_id="single_fault/sf-002",
            remediation_policy=Policy(),
        )

    assert severity in {"P2", "P3"}
    assert agent_calls == []
    assert policy_calls == []
    assert tool_calls == []
    assert not (tmp_path / "trajectories").exists()
