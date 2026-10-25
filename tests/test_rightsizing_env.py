"""Model-free contracts for the right-sizing GRPO environment."""

import json

import pytest

from training import rightsizing_env as env

STATE = env.CapacityState(replicas=2, demand=10, capacity_per_replica=3)  # needs 4


def action(replicas=4, **changes):
    payload = {"tool": "kubectl_scale",
               "arguments": {"deployment": "paymentservice", "namespace": "default", "replicas": replicas},
               "agent_claimed_resolved": False}
    payload.update(changes)
    return json.dumps(payload)


def test_states_are_saturated_unique_and_within_the_tool_policy_bound():
    states = env.all_states()
    assert len(states) == len(set(states))
    assert all(state.replicas < state.needed <= env.MAX_REPLICAS for state in states)
    assert {state.capacity_per_replica for state in states} == {1, 2, 3, 4, 5}


def test_split_is_disjoint_complete_and_deterministic():
    train, held_out = env.split_states()
    assert len(held_out) == 64
    assert not set(train) & set(held_out)
    assert set(train) | set(held_out) == set(env.all_states())
    assert env.states_sha256(held_out) == env.states_sha256(env.split_states()[1])
    assert env.states_sha256(held_out) != env.states_sha256(env.split_states(seed=7)[1])


def test_only_the_smallest_covering_replica_count_resolves():
    exact = env.score(STATE, action(4))
    assert exact["resolved"] and exact["reward"] == 1.0
    for replicas in (3, 5, 20):
        result = env.score(STATE, action(replicas))
        assert result["admissible"] and not result["resolved"]
        assert result["reward"] == pytest.approx(0.25 * 2 / 3, abs=1e-6)
    assert env.score(STATE, action(0))["reward"] == pytest.approx(0.25 / 3, abs=1e-6)


@pytest.mark.parametrize("completion, category", [
    (action(21), "policy_block"),
    (action("4"), "invalid_action"),
    ("```json\n" + action(4) + "\n```", "invalid_action"),
    ("The answer is 4.", "invalid_action"),
    (json.dumps({**json.loads(action(4)), "note": "x"}), "invalid_action"),
    (action(4, tool="kubectl_delete"), "invalid_action"),  # no schema, as in the G9 environment
    (json.dumps({"tool": "kubectl_scale", "arguments": {"deployment": "frontend", "replicas": 4},
                 "agent_claimed_resolved": False}), "target_mismatch"),
])
def test_invalid_or_blocked_actions_score_minus_one(completion, category):
    result = env.score(STATE, completion)
    assert result == {"reward": -1.0, "admissible": False, "resolved": False, "category": category,
                      "replicas": None, "needed": 4}


def test_claims_never_change_the_reward():
    claimed = json.dumps({**json.loads(action(5)), "agent_claimed_resolved": True})
    assert env.score(STATE, claimed)["reward"] == env.score(STATE, action(5))["reward"]


def test_read_only_action_is_admissible_but_resolves_nothing():
    result = env.score(STATE, json.dumps({"tool": "kubectl_get", "arguments": {"resource": "deployments"},
                                          "agent_claimed_resolved": False}))
    assert result["admissible"] and not result["resolved"] and result["replicas"] == 2


def test_prompt_carries_the_observation_but_not_the_answer():
    messages = env.prompt_messages(STATE)
    observation = json.loads(messages[1]["content"])["observations"]
    assert observation["demand_units"] == 10 and observation["capacity_units_per_replica"] == 3
    assert '"needed"' not in messages[1]["content"]


def test_expert_demonstration_is_the_resolving_action():
    for state in env.split_states()[0][:50]:
        result = env.score(state, env.expert_completion(state))
        assert result["resolved"] and result["reward"] == 1.0
