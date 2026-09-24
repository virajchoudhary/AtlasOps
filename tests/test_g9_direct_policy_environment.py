"""Direct policy-action-environment coupling tests for Gate G9."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from training import grpo
from training.grpo_environment import DirectPolicyEnvironment, parse_policy_action


def _completion(**overrides):
    action = {
        "tool": "chaos_stop_experiment",
        "arguments": {
            "kind": "StressChaos",
            "name": "experiment-1",
            "namespace": "chaos-mesh",
        },
        "agent_claimed_resolved": True,
    }
    action.update(overrides)
    return json.dumps(action)


def _state(*, approval=None):
    return {
        "alert": {"commonLabels": {"alertname": "HighCpuUsage"}},
        "triage": {"severity": "P1"},
        "approval": approval,
        "observations": {},
    }


def test_parser_rejects_multi_action_or_malformed_completion():
    with pytest.raises(ValueError, match="one JSON object"):
        parse_policy_action("use kubectl_scale")
    with pytest.raises(ValueError, match="multiple actions"):
        parse_policy_action('{"actions": [{"tool": "kubectl_scale"}]}')


@pytest.mark.asyncio
async def test_exact_policy_action_executes_once_then_verifies():
    calls = []

    def execute(**arguments):
        calls.append(("execute", arguments))
        return {"success": True}

    def observe():
        calls.append(("observe", None))
        return {
            "success": True,
            "observation_status": "observed",
            "active_experiments": [{
                "kind": "StressChaos",
                "name": "experiment-1",
                "namespace": "chaos-mesh",
            }],
        }

    def verify(**kwargs):
        calls.append(("verify", kwargs["incident_context"]["tool_result"]))
        return SimpleNamespace(
            env_resolved=True,
            to_dict=lambda: {"env_resolved": True, "status": "verified"},
        )

    environment = DirectPolicyEnvironment(
        tool_registry={"chaos_stop_experiment": execute, "chaos_list_experiments": observe},
        policy_check=lambda role, tool, arguments, state: None,
        verifier=verify,
        settle=lambda: calls.append(("settle", None)),
    )
    result = await environment.step(
        _completion(),
        scenario_id="single_fault/sf-002",
        state=_state(approval={"decision": "approved"}),
    )

    assert [entry[0] for entry in calls] == ["observe", "execute", "settle", "verify"]
    assert len(result["executed_actions"]) == 1
    assert result["env_resolved"] is True
    assert result["resolved"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("approval", [None, {"decision": "timeout"}, {"status": "rejected"}])
async def test_p1_without_explicit_approval_executes_nothing(approval):
    executed = []
    environment = DirectPolicyEnvironment(
        tool_registry={"chaos_stop_experiment": lambda **kwargs: executed.append(kwargs)},
        policy_check=lambda role, tool, arguments, state: None,
        verifier=lambda **kwargs: pytest.fail("verifier must not run"),
    )
    result = await environment.step(
        _completion(),
        scenario_id="single_fault/sf-002",
        state=_state(approval=approval),
    )

    assert executed == []
    assert result["terminal_block"]["category"] == "approval_required"
    assert result["env_resolved"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("severity", ["P0", "UNKNOWN"])
async def test_manual_or_unknown_severity_never_mutates_without_approval(severity):
    executed = []
    environment = DirectPolicyEnvironment(
        tool_registry={"chaos_stop_experiment": lambda **kwargs: executed.append(kwargs)},
        policy_check=lambda role, tool, arguments, state: None,
    )
    state = _state()
    state["triage"]["severity"] = severity
    result = await environment.step(
        _completion(), scenario_id="single_fault/sf-002", state=state
    )
    assert executed == []
    assert result["terminal_block"]["category"] == "approval_required"


@pytest.mark.asyncio
async def test_verified_resolution_blocks_additional_policy_mutation():
    executed = []
    environment = DirectPolicyEnvironment(
        tool_registry={"chaos_stop_experiment": lambda **kwargs: executed.append(kwargs)},
        policy_check=lambda role, tool, arguments, state: None,
    )
    state = _state(approval={"decision": "approved"})
    state["env_resolved"] = True
    result = await environment.step(
        _completion(), scenario_id="single_fault/sf-002", state=state
    )
    assert executed == []
    assert result["terminal_block"]["category"] == "already_resolved"


@pytest.mark.asyncio
async def test_agent_resolution_claim_cannot_override_verifier():
    environment = DirectPolicyEnvironment(
        tool_registry={
            "chaos_stop_experiment": lambda **kwargs: {"success": True},
            "chaos_list_experiments": lambda: {
                "success": True,
                "observation_status": "observed",
                "active_experiments": [{
                    "kind": "StressChaos", "name": "experiment-1",
                    "namespace": "chaos-mesh",
                }],
            },
        },
        policy_check=lambda role, tool, arguments, state: None,
        verifier=lambda **kwargs: {
            "env_resolved": False,
            "status": "failed",
        },
    )
    result = await environment.step(
        _completion(agent_claimed_resolved=True),
        scenario_id="single_fault/sf-002",
        state=_state(approval={"status": "approved"}),
    )
    assert result["agent_claimed_resolved"] is True
    assert result["env_resolved"] is False
    assert result["resolved"] is False


@pytest.mark.asyncio
async def test_rollback_requires_exact_observed_revision():
    executed = []
    environment = DirectPolicyEnvironment(
        tool_registry={"argocd_rollback": lambda **kwargs: executed.append(kwargs)},
        policy_check=lambda role, tool, arguments, state: None,
        verifier=lambda **kwargs: pytest.fail("verifier must not run"),
    )
    result = await environment.step(
        _completion(
            tool="argocd_rollback",
            arguments={"app_name": "checkoutservice", "revision": "7"},
        ),
        scenario_id="single_fault/sf-002",
        state=_state(approval={"decision": "approved"}),
    )
    assert executed == []
    assert result["terminal_block"]["category"] == "missing_evidence"


@pytest.mark.asyncio
async def test_live_evidence_must_match_before_mutation():
    executed = []
    environment = DirectPolicyEnvironment(
        tool_registry={
            "chaos_stop_experiment": lambda **kwargs: executed.append(kwargs),
            "chaos_list_experiments": lambda: {
                "success": False,
                "observation_status": "unavailable",
                "active_experiments": [{
                    "kind": "StressChaos", "name": "experiment-1",
                    "namespace": "chaos-mesh",
                }],
            },
        },
        policy_check=lambda role, tool, arguments, state: None,
    )
    result = await environment.step(
        _completion(),
        scenario_id="single_fault/sf-002",
        state=_state(approval={"decision": "approved"}),
    )
    assert executed == []
    assert result["terminal_block"]["category"] == "missing_evidence"

    environment = DirectPolicyEnvironment(
        tool_registry={
            "argocd_rollback": lambda **kwargs: executed.append(kwargs) or {"success": True},
            "argocd_app_history": lambda app: {
                "success": True,
                "history": [{"id": 7, "revision": "hash"}],
            },
        },
        policy_check=lambda role, tool, arguments, state: None,
        verifier=lambda **kwargs: {"env_resolved": False},
    )
    result = await environment.step(
        _completion(
            tool="argocd_rollback",
            arguments={"app": "checkoutservice", "revision": "7"},
        ),
        scenario_id="single_fault/sf-002",
        state=_state(approval={"decision": "approved"}),
    )
    assert len(executed) == 1
    assert result["pre_action_observation"]["history"] == [{"id": 7, "revision": "hash"}]


def test_training_prompts_use_train_split_without_benchmark_truth():
    prompts = grpo.build_direct_action_prompts(
        ["single_fault", "cascade", "multi_fault", "named_replays"]
    )
    serialized = json.dumps(prompts)
    assert len(prompts) == 16
    assert {item["scenario_id"] for item in prompts} == set(grpo.get_split("train"))
    assert all(
        item["prompt"] == grpo._direct_action_prompt(item["scenario_id"])
        for item in prompts
    )
    assert "triage_seed" not in serialized
    assert "expected_root_cause" not in serialized
    assert "chaos_kinds" not in serialized
    assert "exact action" in serialized


@pytest.mark.asyncio
async def test_reward_batch_rejects_prompt_scenario_mismatch_before_mutation(monkeypatch):
    applied = []
    monkeypatch.setattr(grpo, "apply_chaos", lambda scenario_id: applied.append(scenario_id))
    reward_function = grpo.OnlineRewardFunction(["single_fault"])
    try:
        with pytest.raises(ValueError, match="prompt/scenario mismatch"):
            await reward_function._score_batch(
                [_completion()],
                [grpo._direct_action_prompt("single_fault/sf-001")],
                ["single_fault/sf-002"],
            )
    finally:
        reward_function._loop.close()
    assert applied == []


def test_reward_and_rollout_ledger_use_verifier_truth(tmp_path):
    claimed_only = {
        "scenario_id": "single_fault/sf-002",
        "tier": "single_fault",
        "agent_claimed_resolved": True,
        "env_resolved": False,
        "resolved": False,
        "outcome": "unresolved",
        "total_turns": 1,
        "time_to_resolve_s": 1,
        "verification": {"env_resolved": False},
        "postmortem_path": None,
    }
    reward = grpo.compute_reward(claimed_only)
    assert claimed_only["env_resolved"] is False
    assert reward < 0.5

    ledger = tmp_path / "rollouts.jsonl"
    reward_function = grpo.OnlineRewardFunction(
        ["single_fault"],
        rollout_log_path=ledger,
    )
    try:
        claimed_only["reward_contract"] = {"total": reward}
        reward_function._persist_rollout(claimed_only)
    finally:
        reward_function._loop.close()
    persisted = json.loads(ledger.read_text(encoding="utf-8"))
    assert persisted["verification"]["env_resolved"] is False
    assert persisted["reward_contract"]["total"] == reward
    assert persisted["recorded_at"]


def test_grpo_cleanup_targets_only_the_selected_manifest(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[1] == "get":
            return SimpleNamespace(returncode=0, stdout='{"items":[]}')
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(grpo.subprocess, "run", fake_run)
    assert grpo.reset_chaos("single_fault/sf-002") is True
    assert commands[0][:3] == ["kubectl", "delete", "-f"]
    assert grpo.Path(commands[0][3]).parts[-2:] == ("single_fault", "sf-002.yaml")
    assert "--all" not in commands[0]
    assert commands[1][1] == "get"


@pytest.mark.asyncio
async def test_grpo_cleanup_failure_aborts_before_next_rollout(monkeypatch, tmp_path):
    applied = []
    monkeypatch.setattr(grpo, "zero_chaos_verified", lambda: True)
    monkeypatch.setattr(grpo, "apply_chaos", lambda scenario_id: applied.append(scenario_id) or False)
    monkeypatch.setattr(grpo, "reset_chaos", lambda scenario_id: False)
    ledger = tmp_path / "rollouts.jsonl"
    reward_function = grpo.OnlineRewardFunction(
        ["single_fault"], rollout_log_path=ledger
    )
    try:
        with pytest.raises(RuntimeError, match="cleanup was not verified"):
            await reward_function._score_batch(
                [_completion()],
                [grpo._direct_action_prompt("single_fault/sf-002")],
                ["single_fault/sf-002"],
            )
    finally:
        reward_function._loop.close()
    assert applied == ["single_fault/sf-002"]
    record = json.loads(ledger.read_text(encoding="utf-8"))
    assert record["failure"] == "scenario_cleanup_unverified"
    assert record["prior_failure"] == "chaos_apply_failed"


def test_completed_grpo_requires_at_least_one_verified_rollout(tmp_path):
    ledger = tmp_path / "rollouts.jsonl"
    ledger.write_text(
        json.dumps({"status": "failed", "failure": "real_alert_not_observed"}) + "\n",
        encoding="utf-8",
    )
    assert grpo._has_verified_rollout(ledger) is False
    with ledger.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "status": "ok",
            "verification": {"env_resolved": False},
        }) + "\n")
    assert grpo._has_verified_rollout(ledger) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("alert_severity", "triage_severity"),
    [("warning", "P2"), ("critical", "P1"), ("unknown", "P0"), ("", "P0")],
)
async def test_rollout_executes_completion_directly(
    monkeypatch, alert_severity, triage_severity
):
    observed = {}

    class FakeEnvironment:
        async def step(self, completion_text, *, scenario_id, state):
            observed.update(
                {
                    "completion": completion_text,
                    "scenario_id": scenario_id,
                    "state": state,
                }
            )
            return {
                "status": "ok",
                "env_resolved": False,
                "resolved": False,
                "agent_claimed_resolved": True,
                "outcome": "unresolved",
            }

    monkeypatch.setattr(grpo, "DirectPolicyEnvironment", FakeEnvironment)
    reward_function = grpo.OnlineRewardFunction(["single_fault"])
    completion = _completion()
    try:
        result = await reward_function._run_one_rollout(
            completion,
            "single_fault/sf-002",
            "single_fault",
            {
                "commonLabels": {"alertname": "HighCpuUsage", "severity": alert_severity},
                "alerts": [],
            },
        )
    finally:
        reward_function._loop.close()
    assert observed["completion"] == completion
    assert observed["scenario_id"] == "single_fault/sf-002"
    assert "triage_seed" not in json.dumps(observed["state"])
    assert observed["state"]["triage"]["severity"] == triage_severity
    assert result["resolved"] is False
    assert result["reward_contract"]["components"]["resolve"] == 0.0
