"""Contract tests for the empirical and explicitly synthetic G9 evaluators."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import bench.grpo_eval as grpo_eval_module
from bench.grpo_eval import (
    CHECKPOINT_MANIFEST,
    evaluate_grpo_episode,
    evaluate_grpo_split,
    validate_grpo_checkpoint,
)
from config.splits import get_split
from training.grpo import build_direct_action_prompts
from training.grpo_environment import DirectPolicyEnvironment, parse_policy_action
from training.grpo_provenance import validate_sft_parent
from training.sft_provenance import (
    create_run_manifest as create_sft_manifest,
)
from training.sft_provenance import (
    mark_completed as complete_sft,
)
from training.sft_provenance import (
    mark_running as run_sft,
)
from training.sft_provenance import (
    write_manifest_atomic,
)


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _checkpoint_with_manifest(root: Path) -> Path:
    corpus = root / "train.jsonl"
    corpus.write_text(
        "".join(json.dumps({"scenario_id": scenario_id, "role": "triage"}) + "\n"
                for scenario_id in get_split("train")),
        encoding="utf-8",
    )
    sft_checkpoint = root / "sft"
    sft_checkpoint.mkdir()
    sft_manifest = create_sft_manifest(
        corpus_path=corpus,
        output_dir=sft_checkpoint,
        base_model="org/base",
        base_model_revision="base-revision",
        tokenizer="org/base",
        tokenizer_revision="tokenizer-revision",
        role="all",
        hyperparameters={},
    )
    sft_manifest = run_sft(
        sft_manifest,
        resolved_model_revision="base-revision",
        resolved_tokenizer_revision="tokenizer-revision",
    )
    sft_manifest["source"] = {"git_sha": "c" * 40, "git_dirty": False}
    (sft_checkpoint / "adapter_config.json").write_text("{}", encoding="utf-8")
    (sft_checkpoint / "adapter_model.safetensors").write_bytes(b"sft-adapter-test")
    sft_manifest_path = sft_checkpoint / "sft_run_manifest.json"
    sft_manifest = complete_sft(
        sft_manifest,
        output_dir=sft_checkpoint,
        manifest_path=sft_manifest_path,
        trainer_state={"global_step": 1},
        training_history=[],
    )
    write_manifest_atomic(sft_manifest_path, sft_manifest)
    sft_parent = validate_sft_parent(
        sft_checkpoint,
        model_id="org/base",
        model_revision="base-revision",
        tokenizer_id="org/base",
        tokenizer_revision="tokenizer-revision",
    )

    checkpoint = root / "checkpoint"
    checkpoint.mkdir()
    adapter_config = checkpoint / "adapter_config.json"
    adapter_config.write_text('{"peft_type":"LORA"}', encoding="utf-8")
    adapter_weights = checkpoint / "adapter_model.safetensors"
    adapter_weights.write_bytes(b"test-adapter-weights")
    inventory = [
        {
            "path": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in (adapter_config, adapter_weights)
    ]
    prompt_rows = build_direct_action_prompts(["single_fault"])
    selected_ids = [row["scenario_id"] for row in prompt_rows]
    manifest = {
        "schema_version": 1,
        "status": "completed",
        "training": {
            "algorithm": "GRPO",
            "mode": "online_rl_real_environment",
            "seed": 17,
            "generation_config": {"max_completion_length": 256},
            "hyperparameters": {"tiers": ["single_fault"]},
        },
        "base_model": {"id": "org/base", "resolved_revision": "base-revision"},
        "tokenizer": {"id": "org/base", "resolved_revision": "tokenizer-revision"},
        "source": {"code_sha": "a" * 40, "source_state": "clean"},
        "sft_parent": sft_parent,
        "splits": {
            "train_sha256": _canonical_sha256(list(get_split("train"))),
            "selected_scenario_ids": selected_ids,
            "selected_scenario_ids_sha256": _canonical_sha256(selected_ids),
            "selected_prompt_rows_sha256": _canonical_sha256(prompt_rows),
        },
        "checkpoint": {
            "files": inventory,
            "tree_sha256": _canonical_sha256(inventory),
        },
    }
    (checkpoint / CHECKPOINT_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return checkpoint


class _FixedPolicy:
    def __init__(self, completion: str):
        self.completion = completion
        self.seen_states: list[dict] = []

    async def generate(self, state, *, seed: int, generation_config):
        self.seen_states.append(dict(state))
        return self.completion


class _RecordingEnvironment:
    def __init__(self, *, resolved: bool, events: list[dict] | None = None, mutate_action: bool = False):
        self.resolved = resolved
        self.events = events
        self.mutate_action = mutate_action
        self.submitted: list[str] = []

    async def step(self, completion_text: str, *, scenario_id: str, state: dict):
        if self.events is not None:
            assert self.events[-1]["event"] == "policy_output"
        self.submitted.append(completion_text)
        action = parse_policy_action(completion_text)
        args = dict(action["arguments"])
        if self.mutate_action:
            args["replicas"] = 99
        verification = {
            "scenario_id": scenario_id,
            "env_resolved": self.resolved,
            "verification_status": "passed" if self.resolved else "failed",
            "checks": [
                {"name": "observed-health", "required": True, "passed": self.resolved}
            ],
        }
        return {
            "status": "ok",
            "policy_completion": completion_text,
            "policy_action": action,
            "executed_actions": [
                {"tool": action["tool"], "arguments": args, "result": {"stdout": "tool output"}}
            ],
            "verification": verification,
            "env_resolved": self.resolved,
            "resolved": self.resolved,
            "agent_claimed_resolved": action["agent_claimed_resolved"],
            "terminal_block": None,
        }


@pytest.mark.asyncio
async def test_empirical_split_refuses_missing_checkpoint(tmp_path):
    with pytest.raises(ValueError, match="requires an actual"):
        await evaluate_grpo_split(
            "val",
            state_provider=lambda _scenario_id: {"alert": {"status": "firing"}},
            environment=_RecordingEnvironment(resolved=False),
            output_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_empirical_episode_rejects_uncheckpointed_policy():
    raw = '{"tool":"kubectl_scale","arguments":{},"agent_claimed_resolved":false}'
    with pytest.raises(ValueError, match="LocalGRPOPolicy"):
        await evaluate_grpo_episode(
            "test/scenario",
            {"alert": {"status": "firing"}},
            _FixedPolicy(raw),
            _RecordingEnvironment(resolved=False),
            seed=1,
        )


def test_checkpoint_provenance_rejects_tampered_files(tmp_path):
    checkpoint = _checkpoint_with_manifest(tmp_path)
    validated = validate_grpo_checkpoint(checkpoint)
    assert validated.checkpoint_sha256
    assert validated.manifest_sha256
    (checkpoint / "adapter_config.json").write_text('{"peft_type":"CHANGED"}', encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_grpo_checkpoint(checkpoint)


def test_checkpoint_provenance_rejects_incomplete_identity(tmp_path):
    checkpoint = _checkpoint_with_manifest(tmp_path)
    manifest_path = checkpoint / CHECKPOINT_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["tokenizer"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((TypeError, ValueError), match="tokenizer"):
        validate_grpo_checkpoint(checkpoint)


def test_checkpoint_provenance_rejects_wrong_train_split(tmp_path):
    checkpoint = _checkpoint_with_manifest(tmp_path)
    manifest_path = checkpoint / CHECKPOINT_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["splits"]["train_sha256"] = "b" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen split"):
        validate_grpo_checkpoint(checkpoint)


def test_checkpoint_provenance_rejects_misreported_selected_prompts(tmp_path):
    checkpoint = _checkpoint_with_manifest(tmp_path)
    manifest_path = checkpoint / CHECKPOINT_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["splits"]["selected_scenario_ids"] = ["single_fault/sf-001"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="selected prompts"):
        validate_grpo_checkpoint(checkpoint)


def test_checkpoint_provenance_rejects_tampered_sft_parent(tmp_path):
    checkpoint = _checkpoint_with_manifest(tmp_path)
    (tmp_path / "sft" / "adapter_model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_grpo_checkpoint(checkpoint)


def test_checkpoint_provenance_rejects_dirty_grpo_source(tmp_path):
    checkpoint = _checkpoint_with_manifest(tmp_path)
    manifest_path = checkpoint / CHECKPOINT_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"] = {
        "code_sha": "a" * 40,
        "source_state": "dirty",
        "dirty_diff_sha256": "b" * 64,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="clean immutable"):
        validate_grpo_checkpoint(checkpoint)


@pytest.mark.asyncio
async def test_non_empirical_contract_preserves_exact_output_and_verifier_truth():
    raw = json.dumps(
        {
            "tool": "kubectl_scale",
            "arguments": {"deployment": "checkoutservice", "replicas": 1},
            "agent_claimed_resolved": True,
        }
    )
    events: list[dict] = []
    policy = _FixedPolicy(raw)
    environment = _RecordingEnvironment(resolved=False, events=events)
    episode = await evaluate_grpo_episode(
        "test/scenario",
        {"alert": {"labels": {"severity": "warning"}}},
        policy,
        environment,
        seed=3,
        max_steps=1,
        evaluation_mode="NON_EMPIRICAL",
        event_sink=events.append,
    )

    step = episode["trajectory"][0]
    assert episode["evaluation_mode"] == "NON_EMPIRICAL"
    assert episode["empirical"] is False
    assert environment.submitted == [raw]
    assert step["raw_policy_output"] == raw
    assert step["parsed_action"] == step["environment_result"]["policy_action"]
    assert step["executed_action"]["tool"] == step["parsed_action"]["tool"]
    assert step["executed_action"]["arguments"] == step["parsed_action"]["arguments"]
    assert step["tool_result"] == {"stdout": "tool output"}
    assert episode["agent_claimed_resolved"] is True
    assert episode["env_resolved"] is False
    assert episode["resolved"] is False
    assert episode["status"] == "ok"
    assert episode["failure"] is None
    assert episode["termination_reason"] == "max_steps_without_verified_resolution"
    assert step["reward_decomposition"]["r_verified_resolution"] == 0.0
    assert step["reward_decomposition"]["penalty_false_resolution"] == -0.25
    assert events[0]["event"] == "episode_started"
    assert events[1]["event"] == "policy_output"
    assert events[2]["event"] == "step_result"


@pytest.mark.asyncio
async def test_environment_cannot_change_action_and_still_claim_acceptance():
    raw = json.dumps(
        {
            "tool": "kubectl_scale",
            "arguments": {"deployment": "checkoutservice", "replicas": 1},
            "agent_claimed_resolved": False,
        }
    )
    episode = await evaluate_grpo_episode(
        "test/scenario",
        {"alert": {"status": "firing"}},
        _FixedPolicy(raw),
        _RecordingEnvironment(resolved=True, mutate_action=True),
        seed=1,
        evaluation_mode="NON_EMPIRICAL",
    )
    assert episode["status"] == "failed"
    assert episode["resolved"] is False
    assert episode["failure"].startswith("environment_contract_error")


@pytest.mark.asyncio
async def test_verifier_can_resolve_episode_when_policy_does_not_claim_success():
    raw = json.dumps(
        {
            "tool": "kubectl_scale",
            "arguments": {"deployment": "checkoutservice", "replicas": 1},
            "agent_claimed_resolved": False,
        }
    )
    episode = await evaluate_grpo_episode(
        "test/scenario",
        {"alert": {"status": "firing"}},
        _FixedPolicy(raw),
        _RecordingEnvironment(resolved=True),
        seed=1,
        max_steps=1,
        evaluation_mode="NON_EMPIRICAL",
    )
    assert episode["agent_claimed_resolved"] is False
    assert episode["verification"]["env_resolved"] is True
    assert episode["resolved"] is True


@pytest.mark.asyncio
async def test_truth_fields_are_rejected_before_policy_generation_or_action():
    raw = '{"tool":"kubectl_scale","arguments":{},"agent_claimed_resolved":false}'
    policy = _FixedPolicy(raw)
    environment = _RecordingEnvironment(resolved=False)
    with pytest.raises(ValueError, match="Benchmark truth field"):
        await evaluate_grpo_episode(
            "test/scenario",
            {"alert": {}, "expected_root_cause": "hidden diagnosis"},
            policy,
            environment,
            seed=1,
            evaluation_mode="NON_EMPIRICAL",
        )
    assert policy.seen_states == []
    assert environment.submitted == []


@pytest.mark.asyncio
async def test_mock_split_is_labeled_non_empirical(tmp_path):
    summary = await evaluate_grpo_split("val", mock=True, output_dir=tmp_path)
    assert summary["evaluation_mode"] == "NON_EMPIRICAL"
    assert summary["empirical"] is False
    rows = [
        json.loads(line)
        for line in (tmp_path / "grpo_val_episodes.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows
    assert all(row["evaluation_mode"] == "NON_EMPIRICAL" for row in rows)


@pytest.mark.asyncio
async def test_empirical_split_rejects_injected_environment(tmp_path):
    checkpoint = _checkpoint_with_manifest(tmp_path)
    output_dir = tmp_path / "output"
    with pytest.raises(ValueError, match="built-in tool and environment verifier"):
        await evaluate_grpo_split(
            "val",
            checkpoint=checkpoint,
            state_provider=lambda _scenario_id: {"alert": {"status": "firing"}},
            environment=_RecordingEnvironment(resolved=True),
            output_dir=output_dir,
            max_steps=1,
        )
    assert not output_dir.exists()


@pytest.mark.asyncio
async def test_empirical_episode_rejects_resolved_pre_action_state(monkeypatch):
    import training.grpo_environment as environment_module

    calls = []

    class FakeLocalPolicy:
        async def generate(self, state, *, seed, generation_config):
            calls.append("generated")
            return '{"tool":"kubectl_scale","arguments":{},"agent_claimed_resolved":false}'

    def verifier(**kwargs):
        calls.append("preflight")
        return type(
            "Verification",
            (),
            {"to_dict": lambda self: {
                "verification_status": "passed",
                "env_resolved": True,
            }},
        )()

    monkeypatch.setattr(grpo_eval_module, "LocalGRPOPolicy", FakeLocalPolicy)
    monkeypatch.setattr(grpo_eval_module, "verify_environment", verifier)
    monkeypatch.setattr(environment_module, "verify_environment", verifier)
    with pytest.raises(ValueError, match="unresolved fault"):
        await evaluate_grpo_episode(
            "single_fault/sf-002",
            {"alert": {"status": "firing"}},
            FakeLocalPolicy(),
            DirectPolicyEnvironment(),
            seed=1,
        )
    assert calls == ["preflight"]
