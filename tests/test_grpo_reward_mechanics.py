"""Synthetic GRPO reward/optimizer acceptance checks; never empirical evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import mean, stdev

import pytest

import scripts.accept_grpo_controlled as controlled_acceptance
from scripts.accept_grpo_controlled import accept_export
from training.grpo_controlled import (
    CLASSIFICATION,
    PROFILE,
    SCENARIO,
    CapacityEnvironment,
    EpisodeLedger,
)
from training.grpo_reward import has_reward_driven_advantages


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


def _trl_0191_advantages(rewards: list[float]) -> list[float]:
    center = mean(rewards)
    scale = stdev(rewards) + 1e-4
    return [(reward - center) / scale for reward in rewards]


def _write_export(
    tmp_path: Path,
    groups: list[tuple[list[str], list[float]]],
    monkeypatch,
    *,
    changed_groups: set[int] | None = None,
    optimizer_before_rewards: bool = False,
    include_reward_gradient_evidence: bool = False,
    reward_gradient_component: str = "reward_policy",
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
    gradients = [
        {
            "step": index,
            "group": index + 1,
            "finite": True,
            "norm": 1.0,
        }
        for index in range(2)
    ]
    reward_gradients = [
        {
            "step": index,
            "group": index + 1,
            "finite": True,
            "norm": float(any(value != 0 for value in advantages)),
            "component": reward_gradient_component,
        }
        for index, (_completions, advantages) in enumerate(groups)
    ]
    optimizer_steps = [
        {
            "step": index + 1,
            "group": index + 1,
            "changed_trainable_tensors": int(index + 1 in optimizer_changed_groups),
        }
        for index in range(2)
    ]

    def _append_optimizer_evidence(index: int) -> None:
        ledger.append("gradient_observed", gradients[index])
        if include_reward_gradient_evidence:
            ledger.append("reward_gradient_observed", reward_gradients[index])
        ledger.append("optimizer_step_observed", optimizer_steps[index])

    try:
        ledger.append("initial_adapter_tensors", {"synthetic_fixture": True})
        if optimizer_before_rewards:
            for index in range(2):
                _append_optimizer_evidence(index)
        for index, (completions, advantages) in enumerate(groups):
            _record_group(
                environment,
                ledger,
                completions,
                advantages,
            )
            if not optimizer_before_rewards:
                _append_optimizer_evidence(index)
        ledger.append(
            "training_finished",
            {
                "global_step": 2,
                "changed_tensors": 1,
                "gradients": gradients,
                "optimizer_steps": optimizer_steps,
                **(
                    {"reward_gradients": reward_gradients}
                    if include_reward_gradient_evidence
                    else {}
                ),
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
                _group(varied_rewards, _trl_0191_advantages([1.0, -1.0])),
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
                _group(
                    [_scale_action(3), "malformed"],
                    advantages,
                ),
                _group([_scale_action(1), _scale_action(1)], [0.0, 0.0]),
            ],
            monkeypatch,
        )


def test_export_does_not_count_equal_reward_optimizer_step_as_reward_learning(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="reward-only|reward-driven"):
        _write_export(
            tmp_path,
            [
                _group(
                    [_scale_action(3), "malformed"],
                    _trl_0191_advantages([1.0, -1.0]),
                ),
                _group([_scale_action(1), _scale_action(1)], [0.0, 0.0]),
            ],
            monkeypatch,
            changed_groups={2},
            include_reward_gradient_evidence=True,
        )
    rows = [
        json.loads(line)
        for line in (tmp_path / "run" / "episodes.jsonl").read_text().splitlines()
    ]
    equal_reward_gradient = next(
        row
        for row in rows
        if row.get("event") == "reward_gradient_observed" and row.get("group") == 2
    )
    kl_only_update = next(
        row
        for row in rows
        if row.get("event") == "optimizer_step_observed" and row.get("group") == 2
    )
    assert equal_reward_gradient["component"] == "reward_policy"
    assert equal_reward_gradient["finite"] is True
    assert equal_reward_gradient["norm"] == 0.0
    assert kl_only_update["changed_trainable_tensors"] > 0


def test_export_fails_closed_without_separate_reward_only_gradient_evidence(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="separate reward-only policy gradient"):
        _write_export(
            tmp_path,
            [
                _group(
                    [_scale_action(3), "malformed"],
                    _trl_0191_advantages([1.0, -1.0]),
                ),
                _group([_scale_action(1), _scale_action(1)], [0.0, 0.0]),
            ],
            monkeypatch,
        )


def test_export_rejects_legacy_reward_gradient_component_name(tmp_path, monkeypatch):
    varied_rewards = [_scale_action(3), "malformed"]
    advantages = _trl_0191_advantages([1.0, -1.0])

    with pytest.raises(ValueError, match="separate reward-only policy gradient"):
        _write_export(
            tmp_path,
            [_group(varied_rewards, advantages), _group(varied_rewards, advantages)],
            monkeypatch,
            include_reward_gradient_evidence=True,
            reward_gradient_component="reward_only_policy_loss",
        )


def test_export_rejects_optimizer_records_that_precede_their_reward_groups(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="chronology"):
        _write_export(
            tmp_path,
            [
                _group(
                    [_scale_action(3), "malformed"],
                    _trl_0191_advantages([1.0, -1.0]),
                ),
                _group([_scale_action(1), _scale_action(1)], [0.0, 0.0]),
            ],
            monkeypatch,
            optimizer_before_rewards=True,
            include_reward_gradient_evidence=True,
        )


@pytest.mark.parametrize(
    ("rewards", "incorrect_advantages"),
    [
        ([1.0, -1.0], [100.0, 99.0]),
        ([1.0, 0.0], [5.0, 4.0]),
    ],
    ids=["rank-preserving-offset", "wrong-scale-and-offset"],
)
def test_reward_driven_advantages_match_pinned_trl_group_normalization(
    rewards, incorrect_advantages
):
    expected = _trl_0191_advantages(rewards)
    near_expected = [expected[0] + 1e-7, expected[1] - 1e-7]

    assert has_reward_driven_advantages(rewards, expected)
    assert has_reward_driven_advantages(rewards, near_expected)
    with pytest.raises(ValueError, match="advantage"):
        has_reward_driven_advantages(rewards, incorrect_advantages)


def test_reward_driven_advantages_reject_wrong_sign_scale_and_group_offset():
    rewards = [1.0, -1.0]
    expected = _trl_0191_advantages(rewards)

    for incorrect in (
        [-value for value in expected],
        [2.0 * value for value in expected],
        [value + 2.0 for value in expected],
    ):
        with pytest.raises(ValueError, match="advantage"):
            has_reward_driven_advantages(rewards, incorrect)


def test_ledger_chronology_requires_reward_and_update_events_to_interleave():
    events = [("initial_adapter_tensors", {})]
    for group in range(1, 3):
        events.append(("state_observed", {"group": group}))
        for sample in range(2):
            events.extend(
                (event, {"group": group, "sample": sample})
                for event in ("branch_reset", "action_proposed", "transition_verified")
            )
        events.extend(
            (event, {"group": group, "step": step} if step is not None else {"group": group})
            for event, step in (
                ("group_finished", None),
                ("generation_tokens", None),
                ("reward_gradient_observed", group - 1),
                ("gradient_observed", group - 1),
                ("optimizer_step_observed", group),
            )
        )
    events.append(("training_finished", {}))
    valid_rows = [
        {"sequence": sequence, "event": event, **data}
        for sequence, (event, data) in enumerate(events, start=1)
    ]
    reward_gradient_index = next(
        index for index, row in enumerate(valid_rows)
        if row.get("event") == "reward_gradient_observed" and row.get("group") == 1
    )
    optimizer_index = next(
        index for index, row in enumerate(valid_rows)
        if row.get("event") == "optimizer_step_observed" and row.get("group") == 1
    )
    optimizer_first_rows = [
        *valid_rows[:1],
        *valid_rows[reward_gradient_index:optimizer_index + 1],
        *valid_rows[1:reward_gradient_index],
        *valid_rows[optimizer_index + 1:],
    ]
    optimizer_first_rows = [
        {**row, "sequence": sequence}
        for sequence, row in enumerate(optimizer_first_rows, start=1)
    ]
    sequence_gap_rows = [dict(row) for row in valid_rows]
    sequence_gap_rows[4]["sequence"] = 99

    controlled_acceptance._validate_ledger_chronology(valid_rows)
    with pytest.raises(ValueError, match="chronology"):
        controlled_acceptance._validate_ledger_chronology(optimizer_first_rows)
    with pytest.raises(ValueError, match="sequence"):
        controlled_acceptance._validate_ledger_chronology(sequence_gap_rows)


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
