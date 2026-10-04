"""Software-only controlled admission tests. No pretrained weights or training."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.approval import ApprovalGate
from training.grpo_controlled import (
    CLASSIFICATION, SCENARIO, CapacityEnvironment, ControlledGRPOMixin,
    EpisodeLedger, admit_execution,
)


def action(replicas=3, claim=False, **arguments):
    return json.dumps({
        "tool": "kubectl_scale",
        "arguments": {"deployment": "paymentservice", "replicas": replicas, **arguments},
        "agent_claimed_resolved": claim,
    })


@pytest.fixture
def environment(tmp_path):
    ledger = EpisodeLedger(tmp_path / "episodes.jsonl")
    env = CapacityEnvironment(ledger)
    yield env
    ledger.close()


def step(env, raw):
    snapshot = env.begin(SCENARIO)["state"]
    env.before_action(snapshot, 0)
    result = env.execute(raw, snapshot, 0)
    env.finish({}, "completed")
    return result


def test_objective_transition_and_claim_independence(environment):
    failed = step(environment, action(1, True))
    assert failed["reward"] == 0.125
    assert failed["verification"]["env_resolved"] is False
    resolved = step(environment, action(3, False))
    assert resolved["reward"] == 1
    assert resolved["verification"]["env_resolved"] is True
    assert resolved["next_state"]["observations"]["ready_replicas"] == 3
    assert len(resolved["executed_actions"]) == 1
    for claim in (False, True):
        assert step(environment, action(1, claim))["reward"] == 0.125


@pytest.mark.parametrize("raw,category", [
    ("<tool_call>{}</tool_call>", "invalid_action"),
    (action(True), "invalid_action"),
    (action(21), "policy_block"),
    (action(3, namespace="kube-system"), "target_mismatch"),
    (action(3, extra="unexposed"), "invalid_action"),
    ('{"tool":"kubectl_exec","arguments":{},"agent_claimed_resolved":false}', "invalid_action"),
    ('{"tool":"slack_post_update","arguments":'
     '{"channel":"x","severity":"P2","title":"x","summary":"x"},'
     '"agent_claimed_resolved":false}', "tool_unavailable"),
])
def test_invalid_or_blocked_actions_preserve_state(environment, raw, category):
    result = step(environment, raw)
    assert result["terminal_block"] == category
    assert result["executed_actions"] == []
    assert result["next_state"]["observations"]["ready_replicas"] == 1
    assert result["reward"] == -1


def test_group_branches_are_exact_clones_and_last_branch_continues(environment):
    snapshot = environment.begin(SCENARIO)["state"]
    environment.before_action(snapshot, 0)
    assert environment.execute(action(3), snapshot, 0)["reward"] == 1
    assert environment.before_action(snapshot, 1)["state"] == snapshot
    assert environment.execute(action(2), snapshot, 1)["reward"] == 0.125
    environment.finish({}, "completed")
    assert environment.begin(SCENARIO)["state"]["observations"]["ready_replicas"] == 2


@pytest.mark.parametrize("severity", ["P0", "P1", "UNKNOWN"])
def test_missing_approval_is_fail_closed(environment, severity):
    environment.severity = severity
    assert step(environment, action())["terminal_block"] == "approval_required"


def test_p1_exact_action_permit_and_replay_refusal(environment):
    environment.severity = "P1"
    state = environment.begin(SCENARIO)["state"]
    gate = ApprovalGate(timeout_seconds=10)
    parsed = json.loads(action())
    request = gate.request_action(
        incident_id=state["incident_id"], severity="P1", action=parsed
    )
    assert gate.callback(request.token, "approved", approved_by="test operator")["ok"]
    decision, permit = asyncio.run(
        gate.wait_for_action_decision(state["incident_id"], request_token=request.token)
    )
    assert decision["status"] == "approved"
    environment.before_action(state, 0)
    result = environment.execute(
        action(), state, 0, approval_gate=gate, approval_permit=permit
    )
    assert result["reward"] == 1
    environment.before_action(state, 1)
    result = environment.execute(
        action(), state, 1, approval_gate=gate, approval_permit=permit
    )
    assert result["terminal_block"] == "approval_required"


@pytest.mark.parametrize("decision", ["rejected", "timeout", "missing"])
def test_p1_failure_decisions_remain_distinct(environment, decision):
    environment.severity = "P1"
    state = environment.begin(SCENARIO)["state"]
    environment.before_action(state, 0)
    result = environment.execute(action(), state, 0, approval_decision=decision)
    assert result["approval_decision"] == decision
    assert result["executed_actions"] == []


def test_verifier_failure_is_null_evidence_not_zero(environment, monkeypatch, tmp_path):
    monkeypatch.setattr(environment, "verify", lambda: {"verification_status": "inconclusive"})
    with pytest.raises(ValueError, match="conclusive"):
        step(environment, action())
    rows = [json.loads(line) for line in (tmp_path / "episodes.jsonl").read_text().splitlines()]
    assert rows[-1]["event"] == "verifier_failure"
    assert rows[-1]["reward"] is None
    assert all(row["classification"] == CLASSIFICATION for row in rows)


def test_non_profile_scenario_refused_before_evidence(environment):
    with pytest.raises(ValueError, match="Train"):
        environment.begin("not-admitted")
    assert environment.group == 0


def test_receipt_refusal_before_model_load_or_output(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="SHA-256"):
        admit_execution(path, "0" * 64)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["receipt.json"]


def test_direct_runner_cannot_bypass_external_receipt(tmp_path):
    from training.grpo_controlled import run_pilot

    path = tmp_path / "receipt.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="SHA-256"):
        run_pilot(path, "0" * 64)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["receipt.json"]


def test_reload_requires_distinct_loopback_only_namespace(monkeypatch):
    from scripts.reload_grpo_controlled import require_network_isolation

    monkeypatch.setattr(Path, "iterdir", lambda path: iter([Path("lo")]))
    monkeypatch.setattr(
        "scripts.reload_grpo_controlled.os.readlink",
        lambda path: "net:self" if "self" in path else "net:host",
    )
    assert require_network_isolation()["namespace"] == "net:self"
    monkeypatch.setattr("scripts.reload_grpo_controlled.os.readlink", lambda path: "net:host")
    with pytest.raises(ValueError, match="isolated"):
        require_network_isolation()
    monkeypatch.setattr(Path, "iterdir", lambda path: iter([Path("lo"), Path("eth0")]))
    with pytest.raises(ValueError, match="isolated"):
        require_network_isolation()


class TrainerDouble:
    def __init__(self, *, model, args, reward_funcs, train_dataset):
        self.model = model
        self.num_generations = args.num_generations
        self.max_prompt_length = args.max_prompt_length
        self.reward_funcs = reward_funcs

    def _generate_and_score_completions(self, inputs):
        prompts = [row["prompt"] for row in inputs]
        # Assert observed capacity state reached generation, not catalogue text.
        assert all(json.loads(p)["observations"]["ready_replicas"] == 1 for p in prompts)
        kwargs = {
            key: [row[key] for row in inputs] for key in inputs[0] if key != "prompt"
        }
        return self.reward_funcs[0](
            prompts=prompts, completions=[action(), "malformed"], **kwargs
        )


class Trainer(ControlledGRPOMixin, TrainerDouble):
    pass


def test_observation_first_bound_reward_vector_with_invalid_sample(environment):
    trainer = Trainer(
        model=SimpleNamespace(training=True),
        args=SimpleNamespace(max_prompt_length=None, num_generations=2),
        train_dataset=[], observation_lifecycle=environment,
    )
    result = trainer._generate_and_score_completions(
        [{"prompt": "catalogue placeholder", "scenario_id": SCENARIO}] * 2
    )
    assert result == [1, -1]
    assert environment.steps == 2
    trainer.model.training = False
    with pytest.raises(RuntimeError, match="training mode"):
        trainer._generate_and_score_completions(
            [{"prompt": "", "scenario_id": SCENARIO}] * 2
        )
    assert environment.steps == 2


def test_prospective_governance_does_not_close_live_gates():
    text = Path("docs/project/CONTROLLED_G9_ADMISSION_V1.md").read_text()
    for phrase in (
        "no attempt", "018", "INCONCLUSIVE / unscored", "pre-fault abort / non-result",
        "completed negative G4 outcome", "criterion: unmet", "root_cause",
        "primary_target_preserved", "CONTROLLED_SYNTHETIC_TRAINING",
    ):
        assert phrase in text
