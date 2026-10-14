"""Synthetic GRPO reward/optimizer acceptance checks; never empirical evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.accept_grpo_controlled import accept_export
from training.grpo_controlled import (
    CLASSIFICATION,
    PROFILE,
    SCENARIO,
    CapacityEnvironment,
    EpisodeLedger,
)


def _scale_action(replicas: int) -> str:
    return json.dumps(
        {
            "tool": "kubectl_scale",
            "arguments": {"deployment": "paymentservice", "replicas": replicas},
            "agent_claimed_resolved": False,
        },
        separators=(",", ":"),
    )


def _record_group(
    environment: CapacityEnvironment,
    ledger: EpisodeLedger,
    completions: list[str],
    advantages: list[float],
) -> None:
    snapshot = environment.begin(SCENARIO)["state"]
    rewards = []
    for sample, completion in enumerate(completions):
        environment.before_action(snapshot, sample)
        rewards.append(environment.execute(completion, snapshot, sample)["reward"])
    environment.finish({}, "completed")
    ledger.append(
        "generation_tokens",
        {
            "group": environment.group,
            "objective_rewards": rewards,
            "advantages": advantages,
        },
    )


def _write_export(
    tmp_path: Path,
    groups: list[tuple[list[str], list[float]]],
    monkeypatch,
    *,
    changed_groups: set[int] | None = None,
) -> dict[str, object]:
    from training import grpo_reload_isolation, sft_provenance

    monkeypatch.setattr(
        sft_provenance,
        "checkpoint_inventory",
        lambda *_args: {"synthetic_test_fixture": True},
    )
    monkeypatch.setattr(
        grpo_reload_isolation,
        "validate_isolation_evidence",
        lambda *_args, **_kwargs: True,
    )
    run = tmp_path / "run"
    run.mkdir()
    optimizer_changed_groups = {1, 2} if changed_groups is None else changed_groups

    ledger = EpisodeLedger(run / "episodes.jsonl")
    environment = CapacityEnvironment(ledger)
    try:
        for completions, advantages in groups:
            _record_group(
                environment,
                ledger,
                completions,
                advantages,
            )
        gradients = [
            {
                "step": index,
                "group": index + 1,
                "finite": True,
                "norm": 1.0,
            }
            for index in range(2)
        ]
        optimizer_steps = [
            {
                "step": index + 1,
                "group": index + 1,
                "changed_trainable_tensors": int(index + 1 in optimizer_changed_groups),
            }
            for index in range(2)
        ]
        for gradient in gradients:
            ledger.append("gradient_observed", gradient)
        for step in optimizer_steps:
            ledger.append("optimizer_step_observed", step)
        ledger.append(
            "training_finished",
            {
                "global_step": 2,
                "changed_tensors": 1,
                "gradients": gradients,
                "optimizer_steps": optimizer_steps,
            },
        )
    finally:
        ledger.close()

    manifest_path = run / "controlled_grpo_manifest.json"
    evidence_files = {
        path.relative_to(run).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(run.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "profile": PROFILE,
        "classification": CLASSIFICATION,
        "status": "TRAINED_RELOAD_PENDING",
        "changed_tensors": 1,
        "checkpoint": {"synthetic_test_fixture": True},
        "receipt": {"source_sha": "synthetic-test-source"},
        "evidence_files": evidence_files,
    }
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
    )
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    reload_path = tmp_path / "reload.json"
    reload_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "profile": PROFILE,
                "classification": CLASSIFICATION,
                "manifest_sha256": manifest_sha256,
                "network_isolation": {"synthetic_test_fixture": True},
                "inference_performed": False,
                "held_out_accessed": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return accept_export(
        run,
        reload_path,
        manifest_sha256,
        hashlib.sha256(reload_path.read_bytes()).hexdigest(),
    )


def _group(completions: list[str], advantages: list[float]) -> tuple[list[str], list[float]]:
    return completions, advantages


def test_export_rejects_advantages_from_a_different_equal_reward_group(
    tmp_path, monkeypatch
):
    varied_rewards = [_scale_action(3), "malformed"]
    equal_rewards = [_scale_action(1), _scale_action(1)]

    with pytest.raises(ValueError, match="reward|advantage"):
        _write_export(
            tmp_path,
            [
                _group(varied_rewards, [0.0, 0.0]),
                _group(equal_rewards, [1.0, -1.0]),
            ],
            monkeypatch,
        )


@pytest.mark.parametrize(
    "advantages",
    [[-1.0, 1.0], [1.0, 1.0], [1.0]],
    ids=["reversed-reward-order", "no-relative-signal", "incomplete-group"],
)
def test_export_rejects_advantages_not_ordered_by_objective_rewards(
    tmp_path, monkeypatch, advantages
):
    with pytest.raises(ValueError, match="advantage"):
        _write_export(
            tmp_path,
            [
                _group([_scale_action(3), "malformed"], advantages),
                _group([_scale_action(1), _scale_action(1)], [0.0, 0.0]),
            ],
            monkeypatch,
        )


def test_export_does_not_count_equal_reward_optimizer_step_as_reward_learning(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="reward-driven|reward and parameter update"):
        _write_export(
            tmp_path,
            [
                _group([_scale_action(3), "malformed"], [1.0, -1.0]),
                _group([_scale_action(1), _scale_action(1)], [0.0, 0.0]),
            ],
            monkeypatch,
            changed_groups={2},
        )


def test_export_accepts_same_group_reward_advantages_gradient_and_update(
    tmp_path, monkeypatch
):
    report = _write_export(
        tmp_path,
        [
            _group([_scale_action(3), "malformed"], [1.0, -1.0]),
            _group([_scale_action(1), _scale_action(1)], [0.0, 0.0]),
        ],
        monkeypatch,
    )

    assert report["status"] == "CONTROLLED_CHECKPOINT_TRAINED_RELOADED_AND_EXPORTED"


def test_equal_reward_groups_cannot_support_a_reward_learning_claim(
    tmp_path, monkeypatch
):
    equal_rewards = [_scale_action(1), _scale_action(1)]

    with pytest.raises(ValueError, match="reward|advantage"):
        _write_export(
            tmp_path,
            [
                _group(equal_rewards, [0.0, 0.0]),
                _group(equal_rewards, [0.0, 0.0]),
            ],
            monkeypatch,
        )
