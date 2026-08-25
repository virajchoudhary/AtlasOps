"""Stage 9 preparation contracts for direct GRPO policy/environment coupling."""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from pathlib import Path

import pytest

from agents import coordinator
from config.runtime import (
    CurriculumManager,
    StepRewardTracker,
    validate_scenario_splits,
)
from training.grpo import compute_reward


def _configure_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-only-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))


def _policy_completion() -> str:
    return json.dumps({
        "name": "chaos_stop_experiment",
        "arguments": {
            "kind": "StressChaos",
            "name": "sf-002-paymentservice-cpu",
            "namespace": "chaos-mesh",
        },
    })


def test_policy_completion_executes_exact_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The TRL completion itself is parsed and executed; no runtime model substitutes it."""
    _configure_audit(tmp_path, monkeypatch)
    calls = []

    def fake_tool(**args):
        calls.append(args)
        return {"success": True, "stdout": "deleted"}

    def reject_runtime_model(*_args, **_kwargs):
        raise AssertionError("policy rollout must not call the runtime chat model")

    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "chaos_stop_experiment", fake_tool)
    monkeypatch.setattr(coordinator, "post_with_retry", reject_runtime_model)
    monkeypatch.setattr(
        coordinator.circuit_breaker,
        "check_before_tool_call",
        lambda **_kwargs: None,
    )

    result = asyncio.run(coordinator.call_agent(
        "remediation",
        {"incident_id": "inc-g9-test"},
        policy_completion=_policy_completion(),
    ))
    final = result["final"]

    assert calls == [{
        "kind": "StressChaos",
        "name": "sf-002-paymentservice-cpu",
        "namespace": "chaos-mesh",
    }]
    assert final["mode"] == "policy_rollout"
    assert final["policy_completion_valid"] is True
    assert final["policy_action_identity_match"] is True
    assert final["executed_actions"][0]["tool"] == "chaos_stop_experiment"
    assert final["executed_actions"][0]["success"] is True
    assert result["step_reward_summary"]["dense_reward_total"] > 0


def test_handle_incident_routes_policy_completion_after_safety_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval still happens upstream; the approved policy action is passed intact."""
    _configure_audit(tmp_path, monkeypatch)
    completion = _policy_completion()
    seen_policy_completions = []
    verification = SimpleNamespace(
        env_resolved=True,
        verification_status="passed",
        failed_checks=[],
        to_dict=lambda: {"env_resolved": True},
    )

    async def fake_call_agent(role, user_input, max_turns=10, **kwargs):
        del user_input, max_turns
        if role == "remediation":
            seen_policy_completions.append(kwargs.get("policy_completion"))
            return {
                "role": role,
                "trajectory": [],
                "final": {"mode": "policy_rollout", "outcome": "resolved"},
                "step_reward_summary": {},
            }
        if role == "triage":
            return {
                "role": role,
                "trajectory": [],
                "final": {"severity": "P2"},
                "step_reward_summary": {},
            }
        return {"role": role, "trajectory": [], "final": {}, "step_reward_summary": {}}

    async def fake_settle(**kwargs):
        return {"settled": True, "scenario_id": kwargs["scenario_id"]}

    monkeypatch.setattr(coordinator, "call_agent", fake_call_agent)
    monkeypatch.setattr(coordinator, "settle_environment", fake_settle)
    monkeypatch.setattr(
        "agents.verifier.verify_environment",
        lambda **_kwargs: verification,
    )
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path)

    incident = asyncio.run(coordinator.handle_incident(
        {"commonLabels": {"alertname": "Test"}, "alerts": []},
        incident_id="inc-g9-routing",
        scenario_id="single_fault/sf-002",
        remediation_policy_completion=completion,
    ))

    assert seen_policy_completions == [completion]
    assert incident["env_resolved"] is True
    assert incident["verification"]["env_resolved"] is True


@pytest.mark.parametrize("completion", ["", "I recommend chaos_stop_experiment(...)", json.dumps([
    {"name": "kubectl_get", "arguments": {}},
    {"name": "promql_query", "arguments": {}},
])])
def test_invalid_policy_completion_cannot_substitute_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completion: str,
) -> None:
    _configure_audit(tmp_path, monkeypatch)
    executed = []

    def reject_tool(**_args):
        executed.append(_args)
        raise AssertionError("invalid policy completion must not execute")

    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "chaos_stop_experiment", reject_tool)

    result = asyncio.run(coordinator.call_agent(
        "remediation",
        {"incident_id": "inc-g9-invalid"},
        policy_completion=completion,
    ))

    assert executed == []
    assert result["final"]["outcome"] == "unresolved"
    assert result["final"]["executed_actions"] == []
    assert result["final"]["policy_completion_valid"] is False
    assert result["trajectory"][0]["validation_state"] == "invalid_policy_completion"


def test_reward_resolution_requires_verifier_and_attributed_policy_action() -> None:
    action = {
        "tool": "chaos_stop_experiment",
        "args": {"kind": "StressChaos", "name": "fault", "namespace": "chaos-mesh"},
        "output": {"success": True},
        "success": True,
    }
    attributed_episode = {
        "tier": "single_fault",
        "scenario_id": "single_fault/sf-002",
        "outcome": "resolved",
        "agent_claimed_resolved": True,
        "env_resolved": True,
        "total_turns": 6,
        "time_to_resolve_s": 100,
        "remediation": {
            "final": {
                "mode": "policy_rollout",
                "policy_completion_valid": True,
                "policy_action_identity_match": True,
                "policy_action_admitted": True,
                "executed_actions": [action],
            }
        },
    }
    unattributed = {
        **attributed_episode,
        "remediation": {"final": {"mode": "manual", "executed_actions": []}},
    }

    assert compute_reward(attributed_episode) == pytest.approx(0.93)
    assert compute_reward(unattributed) == 0.0


def test_false_resolution_penalty_uses_explicit_environment_truth() -> None:
    episode = {
        "tier": "single_fault",
        "outcome": "resolved",
        "agent_claimed_resolved": True,
        "env_resolved": False,
        "total_turns": 6,
        "remediation": {"final": {"executed_actions": []}},
    }

    assert compute_reward(episode) == pytest.approx(-0.25)


def test_absent_judge_does_not_create_subjective_penalty() -> None:
    from config.runtime import evaluate_reward_contract

    episode = {
        "tier": "named_replays",
        "agent_claimed_resolved": False,
        "env_resolved": False,
        "total_turns": 8,
        "time_to_resolve_s": 300,
    }
    penalties = evaluate_reward_contract(episode)["penalties"]

    assert penalties["hallucinated_evidence"] == 0.0
    assert penalties["unsafe_shortcut"] == 0.0


def test_training_and_final_test_split_guard_rejects_overlap() -> None:
    with pytest.raises(ValueError, match="training_scenario_leak"):
        validate_scenario_splits(
            {"single_fault": ["single_fault/sf-999-final"]},
            {"single_fault": ["single_fault/sf-999-final"]},
        )


def test_curriculum_sampling_is_seeded_and_restorable() -> None:
    pool = [(f"single_fault/sf-{number:03d}", "single_fault") for number in range(1, 9)]
    first = CurriculumManager(seed=7)
    second = CurriculumManager(seed=7)
    expected = [first.next_scenario(pool)[0] for _ in range(12)]
    actual = [second.next_scenario(pool)[0] for _ in range(12)]
    assert actual == expected

    resumed = CurriculumManager()
    resumed.restore_state(first.export_state())
    after_original = [first.next_scenario(pool)[0] for _ in range(5)]
    after_resume = [resumed.next_scenario(pool)[0] for _ in range(5)]
    assert after_resume == after_original


def test_chaos_stop_is_scored_as_mutating_progress() -> None:
    tracker = StepRewardTracker()
    reward = tracker.record(
        "chaos_stop_experiment",
        {"kind": "StressChaos", "name": "fault", "namespace": "chaos-mesh"},
        {"success": True},
    )

    assert tracker.summary()["success_count"] == 1
    assert reward > 0
