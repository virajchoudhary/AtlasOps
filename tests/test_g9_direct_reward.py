"""Direct-action reward uses objective verifier checks, not absent judge fields."""

import json

import pytest

from training.grpo_reward import aggregate_direct_action_steps, score_direct_action_step
from training.grpo import compute_direct_action_reward


def _verification(*, resolved: bool = False):
    return {
        "verification_status": "passed" if resolved else "failed",
        "env_resolved": resolved,
        "checks": [
            {"name": "workload", "required": True, "passed": True},
            {"name": "chaos_cleared", "required": True, "passed": resolved},
        ],
    }


def test_reachable_negative_retains_objective_partial_credit():
    score = score_direct_action_step(_verification(), agent_claimed_resolved=False)

    assert score["r_verified_resolution"] == 0.0
    assert score["r_required_check_coverage"] == 0.125
    assert score["penalty_false_resolution"] == 0.0
    assert score["total"] == 0.125


def test_false_resolution_claim_is_penalized_without_judge_fields():
    score = score_direct_action_step(_verification(), agent_claimed_resolved=True)

    assert score["r_required_check_coverage"] == 0.125
    assert score["penalty_false_resolution"] == -0.25
    assert score["total"] == -0.125


@pytest.mark.parametrize("status", ["inconclusive", "error"])
def test_nonconclusive_verification_has_no_reward(status):
    verification = _verification()
    verification["verification_status"] = status

    with pytest.raises(ValueError, match="conclusive"):
        score_direct_action_step(verification, agent_claimed_resolved=False)


@pytest.mark.parametrize(
    ("status", "resolved", "passed"),
    [("passed", True, False), ("failed", False, True)],
)
def test_verifier_status_must_agree_with_required_checks(status, resolved, passed):
    verification = _verification(resolved=resolved)
    verification["verification_status"] = status
    verification["checks"][1]["passed"] = passed

    with pytest.raises(ValueError, match="required checks"):
        score_direct_action_step(verification, agent_claimed_resolved=False)


def test_episode_reward_is_mean_of_observed_step_rewards():
    negative = score_direct_action_step(
        _verification(), agent_claimed_resolved=False
    )
    resolved = score_direct_action_step(
        _verification(resolved=True), agent_claimed_resolved=True
    )

    assert aggregate_direct_action_steps([negative, resolved]) == {
        "aggregation": "mean",
        "step_count": 2,
        "step_totals": [0.125, 1.0],
        "total": 0.5625,
    }


def test_episode_reward_requires_a_scorable_step():
    with pytest.raises(ValueError, match="at least one"):
        aggregate_direct_action_steps([])


def test_training_direct_action_uses_the_objective_step_score():
    result = {
        "status": "ok",
        "scorable": True,
        "settling": {"status": "settled", "stable": True, "stable_observations": 2},
        "verification": _verification(),
        "agent_claimed_resolved": False,
    }

    assert compute_direct_action_reward(result) == 0.125
    assert result["direct_reward_decomposition"]["total"] == 0.125


def test_training_rejects_an_unscorable_direct_action():
    result = {
        "status": "unscorable",
        "scorable": False,
        "verification": {
            **_verification(),
            "verification_status": "inconclusive",
        },
    }

    with pytest.raises(ValueError, match="unscorable"):
        compute_direct_action_reward(result)


@pytest.mark.asyncio
async def test_evaluator_and_training_share_one_step_score():
    from bench.grpo_eval import evaluate_grpo_episode
    from training.grpo_environment import parse_policy_action

    completion = '{"tool":"kubectl_get","arguments":{},"agent_claimed_resolved":false}'

    class FakePolicy:
        async def generate(self, _state, *, seed, generation_config):
            return completion

    class FakeEnvironment:
        async def step(self, raw, *, scenario_id, state):
            action = parse_policy_action(raw)
            return {
                "status": "ok",
                "policy_completion": raw,
                "policy_action": action,
                "executed_actions": [
                    {"tool": action["tool"], "arguments": action["arguments"], "result": {}}
                ],
                "verification": _verification(),
                "env_resolved": False,
                "resolved": False,
                "agent_claimed_resolved": False,
                "terminal_block": None,
            }

    episode = await evaluate_grpo_episode(
        "single_fault/sf-002",
        {"alert": {"status": "firing"}},
        FakePolicy(),
        FakeEnvironment(),
        seed=1,
        max_steps=1,
        evaluation_mode="NON_EMPIRICAL",
    )

    assert episode["trajectory"][0]["reward_decomposition"]["total"] == 0.125
    assert episode["reward"] == 0.125
    assert episode["empirical"] is False


@pytest.mark.asyncio
async def test_blocked_p1_cannot_become_a_claimable_empirical_episode(monkeypatch):
    import bench.grpo_eval as evaluator
    import training.grpo_environment as environment_module

    attempted_tools = []

    class FakeProvenance:
        def to_record(self):
            return {"source": "test-only"}

    class FakePolicy:
        execute_actions = True
        kube_context = "kind-atlasops-test"
        provenance = FakeProvenance()

        async def generate(self, _state, *, seed, generation_config):
            return json.dumps({
                "tool": "kubectl_scale",
                "arguments": {"deployment": "checkoutservice", "replicas": 0},
                "agent_claimed_resolved": False,
            })

    def fake_verifier(**_kwargs):
        record = {
            "verification_status": "failed",
            "env_resolved": False,
            "checks": [{"name": "fault_active", "required": True, "passed": False}],
        }
        return type("Verification", (), {"to_dict": lambda self: record})()

    monkeypatch.setattr(evaluator, "LocalGRPOPolicy", FakePolicy)
    monkeypatch.setattr(evaluator, "verify_environment", fake_verifier)
    monkeypatch.setattr(environment_module, "verify_environment", fake_verifier)
    monkeypatch.setitem(
        environment_module.TOOL_REGISTRY,
        "kubectl_scale",
        lambda **kwargs: attempted_tools.append(kwargs),
    )
    environment = environment_module.DirectPolicyEnvironment(
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
    )

    episode = await evaluator.evaluate_grpo_episode(
        "single_fault/sf-002",
        {"alert": {"status": "firing"}, "triage": {"severity": "P1"}},
        FakePolicy(),
        environment,
        seed=1,
        max_steps=1,
        evaluation_mode="EMPIRICAL",
        execute_actions=True,
        kube_context="kind-atlasops-test",
    )

    assert attempted_tools == []
    assert (
        episode["trajectory"][0]["environment_result"]["terminal_block"]["category"]
        == "approval_required"
    )
    assert episode["status"] == "unscorable"
    assert episode["scorable"] is False
    assert episode["reward"] is None


@pytest.mark.parametrize(
    ("status", "expected_reward"),
    [("ok", 0.125), ("unscorable", None)],
)
@pytest.mark.asyncio
async def test_training_batch_persists_direct_action_evidence(
    tmp_path, monkeypatch, status, expected_reward
):
    from bench import runner
    from config.runtime import CurriculumManager
    from training import grpo

    async def no_sleep(_seconds):
        return None

    class FakeEnvironment:
        def __init__(self, **_kwargs):
            pass

        async def step(self, _completion, *, scenario_id, state):
            return {
                "status": status,
                "scorable": status == "ok",
                "settling": {
                    "status": "settled",
                    "stable": True,
                    "stable_observations": 2,
                },
                "verification": _verification(),
                "env_resolved": False,
                "resolved": False,
                "agent_claimed_resolved": False,
                "executed_actions": [],
                "outcome": "unresolved",
            }

    monkeypatch.setattr(grpo, "zero_chaos_verified", lambda **_kwargs: True)
    monkeypatch.setattr(grpo, "apply_chaos", lambda _scenario, **_kwargs: True)
    monkeypatch.setattr(grpo, "reset_chaos", lambda _scenario, **_kwargs: True)
    monkeypatch.setattr(grpo, "DirectPolicyEnvironment", FakeEnvironment)
    monkeypatch.setattr(grpo.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(grpo, "_curriculum", CurriculumManager())
    monkeypatch.setattr(
        runner,
        "wait_for_alert",
        lambda: {"commonLabels": {"severity": "warning"}, "alerts": []},
    )

    ledger = tmp_path / "rollouts.jsonl"
    reward_fn = grpo.OnlineRewardFunction(
        ["single_fault"],
        rollout_log_path=ledger,
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
    )
    scenario_id = "single_fault/sf-002"
    try:
        if expected_reward is None:
            with pytest.raises(RuntimeError, match="cannot be scored"):
                await reward_fn._score_batch(
                    ['{"tool":"kubectl_get","arguments":{}}'],
                    [grpo._direct_action_prompt(scenario_id)],
                    [scenario_id],
                )
        else:
            rewards = await reward_fn._score_batch(
                ['{"tool":"kubectl_get","arguments":{}}'],
                [grpo._direct_action_prompt(scenario_id)],
                [scenario_id],
            )
    finally:
        reward_fn._loop.close()

    row = json.loads(ledger.read_text(encoding="utf-8").strip())
    if expected_reward is None:
        assert row["status"] == "unscorable"
        assert row["reward"] is None
        assert row["failure"] == "direct_action_reward_unscorable:ValueError"
        assert "direct_reward_decomposition" not in row
    else:
        assert rewards == [expected_reward]
        assert row["reward"] == expected_reward
        assert row["direct_reward_decomposition"]["total"] == expected_reward
