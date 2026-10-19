"""Synthetic GRPO reward/optimizer acceptance checks; never empirical evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from statistics import mean, stdev

import pytest

import scripts.accept_grpo_controlled as controlled_acceptance
from scripts.accept_grpo_controlled import accept_export
from training.grpo_controlled import (
    CLASSIFICATION,
    MODEL,
    PROFILE,
    REVISION,
    SCENARIO,
    CapacityEnvironment,
    EpisodeLedger,
    canonical_bytes,
)
from training.grpo_reward import has_reward_driven_advantages


def _scale_action(replicas: int, claim: bool = False) -> str:
    return json.dumps(
        {
            "tool": "kubectl_scale",
            "arguments": {"deployment": "paymentservice", "replicas": replicas},
            "agent_claimed_resolved": claim,
        },
        separators=(",", ":"),
    )


class _AcceptanceLedger(EpisodeLedger):
    def __init__(self, path, *, reward_overrides, row_mutator):
        super().__init__(path)
        self.reward_overrides = reward_overrides
        self.row_mutator = row_mutator

    def append(self, event, data):
        record = copy.deepcopy(dict(data))
        if event == "transition_verified":
            key = (record.get("group"), record.get("sample"))
            if key in self.reward_overrides:
                record["reward"] = self.reward_overrides[key]
        if self.row_mutator is not None:
            self.row_mutator(event, record)
        super().append(event, record)


def _record_group(
    environment: CapacityEnvironment,
    ledger: EpisodeLedger,
    completions: list[str],
    advantages: list[float],
    *,
    group_evidence: dict[str, object],
    objective_reward_overrides: list[float] | None = None,
) -> None:
    from training.grpo_observation_first import _public_snapshot

    snapshot = environment.begin(SCENARIO)["state"]
    prompt_bytes, observation_digest = _public_snapshot(snapshot)
    group_evidence["observation_digest"] = observation_digest
    group_evidence["state_prompt_sha256"] = hashlib.sha256(prompt_bytes).hexdigest()
    results = []
    for sample, completion in enumerate(completions):
        environment.before_action(
            snapshot,
            sample,
            g9_group_token=group_evidence["group_token"],
        )
        results.append(
            environment.execute(
                completion,
                snapshot,
                sample,
                g9_group_token=group_evidence["group_token"],
            )
        )
    environment.finish({}, "completed")
    objective_rewards = (
        objective_reward_overrides
        if objective_reward_overrides is not None
        else [result["reward"] for result in results]
    )
    environmental_components = [
        result["environmental_components"] for result in results
    ]
    environmental_rewards = [result["environmental_reward"] for result in results]
    environmental_admissible = [
        result["environmental_admissible"] for result in results
    ]
    objective_advantages = _trl_0191_advantages(objective_rewards)
    ledger.append(
        "generation_tokens",
        {
            "group": environment.group,
            "prompt_ids": group_evidence["prompt_ids"],
            "completion_ids": group_evidence["completion_ids"],
            "group_token": group_evidence["group_token"],
            "scenario_id": SCENARIO,
            "observation_digest": group_evidence["observation_digest"],
            "state_prompt_sha256": group_evidence["state_prompt_sha256"],
            "completion_sha256s": [
                hashlib.sha256(text.encode("utf-8")).hexdigest()
                for text in completions
            ],
            "objective_rewards": objective_rewards,
            "objective_advantages": objective_advantages,
            "environmental_components": environmental_components,
            "environmental_rewards": environmental_rewards,
            "environmental_admissible": environmental_admissible,
            "environmental_advantages": advantages,
            "environmental_advantages_sha256":
                group_evidence["environmental_advantages_sha256"],
            "environmental_signal_sha256":
                group_evidence["environmental_signal_sha256"],
            "optimization_input_sha256":
                group_evidence["optimization_input_sha256"],
            "advantages": advantages,
        },
    )


def _tokenizer_group_evidence(
    tokenizer,
    groups: list[tuple[list[str], list[float]]],
    *,
    completion_token_text_overrides: dict[int, list[str]] | None = None,
    prompt_text_overrides: dict[int, str] | None = None,
) -> list[dict[str, object]]:
    from tokenizers import AddedToken

    from training.grpo_observation_first import _public_snapshot

    class PreviewLedger:
        def append(self, _event, _data):
            pass

    preview = CapacityEnvironment(PreviewLedger())
    evidence = []
    for group_index, (completions, advantages) in enumerate(groups, start=1):
        snapshot = preview.begin(SCENARIO)["state"]
        prompt_bytes, observation_digest = _public_snapshot(snapshot)
        prompt_text = prompt_bytes.decode("utf-8")
        prompt_for_tokens = (prompt_text_overrides or {}).get(
            group_index, prompt_text
        )
        tokenizer.add_tokens([AddedToken(prompt_for_tokens, normalized=False)])
        prompt_ids = tokenizer(
            text=[prompt_for_tokens] * len(completions),
            padding=True,
            padding_side="left",
            add_special_tokens=False,
        )["input_ids"]

        token_texts = (completion_token_text_overrides or {}).get(
            group_index, completions
        )
        if len(token_texts) != len(completions):
            raise ValueError("Completion token-text fixture has the wrong group size")
        encoded_completions = tokenizer(
            text=token_texts,
            padding=False,
            add_special_tokens=False,
        )["input_ids"]
        completion_ids = [
            [*sample_ids, tokenizer.eos_token_id]
            for sample_ids in encoded_completions
        ]
        if any(not sample_ids or None in sample_ids for sample_ids in completion_ids):
            raise ValueError("Tiny tokenizer fixture did not encode every completion")

        environmental_advantages_sha256 = hashlib.sha256(
            canonical_bytes([float(value) for value in advantages])
        ).hexdigest()
        environmental_signal_sha256 = hashlib.sha256(
            canonical_bytes(
                {
                    "prompt_ids": prompt_ids,
                    "completion_ids": completion_ids,
                    "advantages": advantages,
                }
            )
        ).hexdigest()
        evidence.append(
            {
                "group_token": f"{group_index:032x}",
                "prompt_ids": prompt_ids,
                "completion_ids": completion_ids,
                "observation_digest": observation_digest,
                "state_prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
                "environmental_advantages_sha256":
                    environmental_advantages_sha256,
                "environmental_signal_sha256": environmental_signal_sha256,
                "optimization_input_sha256": hashlib.sha256(
                    canonical_bytes(
                        {
                            "fixture": "model-free",
                            "prompt_ids": prompt_ids,
                            "completion_ids": completion_ids,
                            "advantages": advantages,
                        }
                    )
                ).hexdigest(),
            }
        )

        for sample, completion in enumerate(completions):
            preview.before_action(
                snapshot,
                sample,
                g9_group_token=evidence[-1]["group_token"],
            )
            preview.execute(
                completion,
                snapshot,
                sample,
                g9_group_token=evidence[-1]["group_token"],
            )
        preview.finish({}, "completed")
    return evidence


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
    reward_gradient_component: str = "environmental_verification_policy",
    include_environmental_binding: bool = True,
    objective_reward_overrides: dict[int, list[float]] | None = None,
    completion_token_text_overrides: dict[int, list[str]] | None = None,
    prompt_text_overrides: dict[int, str] | None = None,
    omit_tokenizer: bool = False,
    row_mutator=None,
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
    reward_overrides = {
        (group, sample): value
        for group, values in (objective_reward_overrides or {}).items()
        for sample, value in enumerate(values)
    }
    tokenizer = None
    if omit_tokenizer:
        group_evidence = []
        for group_index, (completions, advantages) in enumerate(groups, start=1):
            prompt_ids = [[1, 2] for _ in completions]
            completion_ids = [[sample + 3, 2] for sample in range(len(completions))]
            environmental_advantages_sha256 = hashlib.sha256(
                canonical_bytes([float(value) for value in advantages])
            ).hexdigest()
            environmental_signal_sha256 = hashlib.sha256(
                canonical_bytes(
                    {
                        "prompt_ids": prompt_ids,
                        "completion_ids": completion_ids,
                        "advantages": advantages,
                    }
                )
            ).hexdigest()
            group_evidence.append(
                {
                    "group_token": f"{group_index:032x}",
                    "prompt_ids": prompt_ids,
                    "completion_ids": completion_ids,
                    "observation_digest": "",
                    "state_prompt_sha256": "",
                    "environmental_advantages_sha256":
                        environmental_advantages_sha256,
                    "environmental_signal_sha256": environmental_signal_sha256,
                    "optimization_input_sha256": hashlib.sha256(
                        canonical_bytes(
                            {
                                "fixture": "model-free",
                                "group": group_index,
                                "advantages": advantages,
                            }
                        )
                    ).hexdigest(),
                }
            )
    else:
        tokenizers = pytest.importorskip("tokenizers")
        transformers = pytest.importorskip("transformers")
        backend = tokenizers.Tokenizer(
            tokenizers.models.WordLevel(
                {"[UNK]": 0, "[PAD]": 1, "[EOS]": 2},
                unk_token="[UNK]",
            )
        )
        backend.pre_tokenizer = tokenizers.pre_tokenizers.WhitespaceSplit()
        backend.decoder = tokenizers.decoders.Fuse()
        tokenizer = transformers.PreTrainedTokenizerFast(
            tokenizer_object=backend,
            unk_token="[UNK]",
            pad_token="[PAD]",
            eos_token="[EOS]",
            clean_up_tokenization_spaces=False,
        )
        completion_texts = [
            completion
            for completions, _advantages in groups
            for completion in completions
        ]
        completion_texts.extend(
            completion
            for texts in (completion_token_text_overrides or {}).values()
            for completion in texts
        )
        tokenizer.add_tokens(
            [
                tokenizers.AddedToken(text, normalized=False)
                for text in dict.fromkeys(completion_texts)
            ]
        )
        group_evidence = _tokenizer_group_evidence(
            tokenizer,
            groups,
            completion_token_text_overrides=completion_token_text_overrides,
            prompt_text_overrides=prompt_text_overrides,
        )
        adapter = run / "adapter"
        adapter.mkdir()
        tokenizer.save_pretrained(adapter)

    ledger = _AcceptanceLedger(
        run / "episodes.jsonl",
        reward_overrides=reward_overrides,
        row_mutator=row_mutator,
    )
    environment = CapacityEnvironment(ledger)

    def _signal_fields(index: int) -> dict[str, str]:
        if not include_environmental_binding:
            return {}
        return {
            "environmental_advantages_sha256":
                group_evidence[index]["environmental_advantages_sha256"],
            "environmental_signal_sha256":
                group_evidence[index]["environmental_signal_sha256"],
            "optimization_input_sha256":
                group_evidence[index]["optimization_input_sha256"],
        }

    gradients = [
        {
            "step": index,
            "group": index + 1,
            "finite": True,
            "norm": 1.0,
            **_signal_fields(index),
        }
        for index in range(len(groups))
    ]
    reward_gradients = [
        {
            "step": index,
            "group": index + 1,
            "finite": True,
            "norm": float(any(value != 0 for value in advantages)),
            "component": reward_gradient_component,
            **_signal_fields(index),
        }
        for index, (_completions, advantages) in enumerate(groups)
    ]
    optimizer_steps = [
        {
            "step": index + 1,
            "group": index + 1,
            "changed_trainable_tensors": int(index + 1 in optimizer_changed_groups),
            **_signal_fields(index),
        }
        for index in range(len(groups))
    ]

    def _append_optimizer_evidence(index: int) -> None:
        if include_reward_gradient_evidence:
            ledger.append("reward_gradient_observed", reward_gradients[index])
        ledger.append("gradient_observed", gradients[index])
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
                group_evidence=group_evidence[index],
                objective_reward_overrides=(
                    objective_reward_overrides or {}
                ).get(index + 1),
            )
            if not optimizer_before_rewards:
                _append_optimizer_evidence(index)
        ledger.append(
            "training_finished",
            {
                "global_step": len(groups),
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
        "tokenizer_identity": {"id": MODEL, "revision": REVISION},
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


def _varied_environment_group() -> tuple[list[str], list[float]]:
    return _group(
        [_scale_action(3), _scale_action(2)],
        _trl_0191_advantages([1.0, 0.125]),
    )


def test_export_rejects_advantages_from_a_different_equal_reward_group(
    tmp_path, monkeypatch
):
    varied_rewards = [_scale_action(3), _scale_action(2)]
    equal_rewards = [_scale_action(1), _scale_action(1)]

    with pytest.raises(ValueError, match="reward|advantage"):
        _write_export(
            tmp_path,
            [
                _group(varied_rewards, [-1.0, 1.0]),
                _group(equal_rewards, [0.0, 0.0]),
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
                    [_scale_action(3), _scale_action(2)],
                    advantages,
                ),
                _varied_environment_group(),
            ],
            monkeypatch,
        )


def test_export_does_not_count_equal_reward_optimizer_step_as_reward_learning(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="environmental|admissible"):
        _write_export(
            tmp_path,
            [
                _varied_environment_group(),
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
    assert equal_reward_gradient["component"] == "environmental_verification_policy"
    assert equal_reward_gradient["finite"] is True
    assert equal_reward_gradient["norm"] == 0.0
    assert kl_only_update["changed_trainable_tensors"] > 0


def test_export_rejects_invalid_action_penalty_as_environmental_signal(
    tmp_path, monkeypatch
):
    penalty_only_group = [_scale_action(1), "malformed"]

    with pytest.raises(ValueError, match="environmental|admissible"):
        _write_export(
            tmp_path,
            [
                _group(
                    penalty_only_group,
                    _trl_0191_advantages([0.125, -1.0]),
                ),
                _varied_environment_group(),
            ],
            monkeypatch,
            changed_groups={1},
            include_reward_gradient_evidence=True,
            reward_gradient_component="reward_policy",
            include_environmental_binding=False,
        )


def test_export_rejects_claim_only_reward_variation_with_equal_verifier_components(
    tmp_path, monkeypatch
):
    claim_only_group = [_scale_action(1, False), _scale_action(1, True)]

    with pytest.raises(ValueError, match="reward|environmental"):
        _write_export(
            tmp_path,
            [
                _group(
                    claim_only_group,
                    _trl_0191_advantages([1.0, -1.0]),
                ),
                _varied_environment_group(),
            ],
            monkeypatch,
            changed_groups={1},
            include_reward_gradient_evidence=True,
            objective_reward_overrides={1: [1.0, -1.0]},
            reward_gradient_component="reward_policy",
            include_environmental_binding=False,
        )


def test_export_rejects_first_group_without_signal_even_if_later_group_varies(
    tmp_path, monkeypatch
):
    equal_environment_group = [_scale_action(1), _scale_action(1)]

    with pytest.raises(ValueError, match="environmental|admissible"):
        _write_export(
            tmp_path,
            [
                _group(equal_environment_group, [0.0, 0.0]),
                _varied_environment_group(),
            ],
            monkeypatch,
            include_reward_gradient_evidence=True,
            reward_gradient_component="reward_policy",
            include_environmental_binding=False,
        )


@pytest.mark.parametrize("tamper", ["decomposition", "prompt_hash", "sample_state"])
def test_export_rejects_tampered_verifier_or_prompt_lineage(
    tmp_path, monkeypatch, tamper
):
    def mutate(event, row):
        if tamper == "decomposition" and (
            event == "transition_verified"
            and row.get("group") == 1
            and row.get("sample") == 0
        ):
            row["objective_reward_decomposition"]["r_verified_resolution"] += 0.1
        elif tamper == "prompt_hash" and (
            event == "transition_verified"
            and row.get("group") == 1
            and row.get("sample") == 0
        ):
            row["state_prompt_sha256"] = "0" * 64
        elif tamper == "sample_state" and (
            event == "action_proposed"
            and row.get("group") == 1
            and row.get("sample") == 0
        ):
            row["state"]["observations"]["ready_replicas"] = 2

    with pytest.raises(ValueError, match="decomposition|lineage|state"):
        _write_export(
            tmp_path,
            [_varied_environment_group(), _varied_environment_group()],
            monkeypatch,
            include_reward_gradient_evidence=True,
            reward_gradient_component="reward_policy",
            include_environmental_binding=False,
            row_mutator=mutate,
        )


def test_export_fails_closed_without_separate_environmental_gradient_evidence(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="separate environmental-verifier policy gradient"):
        _write_export(
            tmp_path,
            [
                _varied_environment_group(),
                _varied_environment_group(),
            ],
            monkeypatch,
        )


def test_export_rejects_legacy_reward_gradient_component_name(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="separate environmental-verifier policy gradient"):
        _write_export(
            tmp_path,
            [_varied_environment_group(), _varied_environment_group()],
            monkeypatch,
            include_reward_gradient_evidence=True,
            reward_gradient_component="reward_policy",
        )


def test_export_rejects_optimizer_records_that_precede_their_reward_groups(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="chronology"):
        _write_export(
            tmp_path,
            [
                _varied_environment_group(),
                _varied_environment_group(),
            ],
            monkeypatch,
            optimizer_before_rewards=True,
            include_reward_gradient_evidence=True,
        )


def test_export_accepts_complete_environmental_signal_synthetic_fixture(
    tmp_path, monkeypatch
):
    report = _write_export(
        tmp_path,
        [_varied_environment_group(), _varied_environment_group()],
        monkeypatch,
        include_reward_gradient_evidence=True,
    )

    assert report["status"] == "CONTROLLED_CHECKPOINT_TRAINED_RELOADED_AND_EXPORTED"
    assert report["classification"] == CLASSIFICATION
    assert report["certification_status"] == "NOT_CERTIFIED"
    assert report["G4"] == "NOT_PASSED"


def test_export_rejects_missing_local_tokenizer(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="tokenizer"):
        _write_export(
            tmp_path,
            [_varied_environment_group(), _varied_environment_group()],
            monkeypatch,
            include_reward_gradient_evidence=True,
            omit_tokenizer=True,
        )


def test_export_rejects_coherently_rehashed_completion_text_token_mismatch(
    tmp_path, monkeypatch
):
    changed_text = _group(
        [_scale_action(4), _scale_action(2)],
        _trl_0191_advantages([1.0, 0.125]),
    )

    with pytest.raises(ValueError, match="completion.*token|token.*completion"):
        _write_export(
            tmp_path,
            [changed_text, _varied_environment_group()],
            monkeypatch,
            include_reward_gradient_evidence=True,
            completion_token_text_overrides={
                1: [_scale_action(3), _scale_action(2)],
            },
        )


def test_export_rejects_prompt_tokens_for_different_observed_state(
    tmp_path, monkeypatch
):
    with pytest.raises(ValueError, match="prompt.*token|token.*prompt"):
        _write_export(
            tmp_path,
            [_varied_environment_group(), _varied_environment_group()],
            monkeypatch,
            include_reward_gradient_evidence=True,
            prompt_text_overrides={1: '{"alert":{"status":"firing"}}'},
        )


def test_export_rejects_changed_completion_ids_without_updated_signal_hash(
    tmp_path, monkeypatch
):
    def change_token_id(event, row):
        if event == "generation_tokens" and row.get("group") == 1:
            row["completion_ids"][0][0] += 1

    with pytest.raises(
        ValueError,
        match="completion text differs from the training completion tokens",
    ):
        _write_export(
            tmp_path,
            [_varied_environment_group(), _varied_environment_group()],
            monkeypatch,
            include_reward_gradient_evidence=True,
            row_mutator=change_token_id,
        )


def test_environmental_probe_is_zero_for_equal_verifier_components():
    torch = pytest.importorskip("torch")
    from types import SimpleNamespace

    from training.grpo_controlled import ControlledGRPOMixin
    from training.grpo_reward import (
        has_environmental_reward_signal,
        normalize_group_advantages,
    )

    class LossBackend:
        def compute_loss(self, model, inputs, *args, **kwargs):
            logits = model(inputs["features"]).reshape(-1)
            return -(logits * inputs["advantages"]).sum()

    class ProbeTrainer(ControlledGRPOMixin, LossBackend):
        def __init__(self):
            self._environmental_gradient_evidence = []
            self._environmental_group_evidence = []
            self.state = SimpleNamespace(global_step=0)
            self.use_liger_loss = False
            self._metrics = {}
            self.beta = 0.04
            self.accelerator = SimpleNamespace(unwrap_model=lambda model: model)

    varied_components = [
        {"r_verified_resolution": 0.75, "r_required_check_coverage": 0.25},
        {"r_verified_resolution": 0.0, "r_required_check_coverage": 0.125},
    ]
    equal_components = [
        {"r_verified_resolution": 0.0, "r_required_check_coverage": 0.125},
        {"r_verified_resolution": 0.0, "r_required_check_coverage": 0.125},
    ]
    varied_advantages = normalize_group_advantages([1.0, 0.125])
    equal_advantages = normalize_group_advantages([0.125, 0.125])
    assert has_environmental_reward_signal(
        varied_components, [True, True], varied_advantages
    )
    assert not has_environmental_reward_signal(
        equal_components, [True, True], equal_advantages
    )

    def measure(advantages):
        trainer = ProbeTrainer()
        model = torch.nn.Linear(1, 1, bias=False)
        with torch.no_grad():
            model.weight.fill_(1.0)
        model.train()
        inputs = {
            "features": torch.tensor([[1.0], [2.0]]),
            "prompt_ids": torch.tensor([[4, 5], [4, 5]]),
            "completion_ids": torch.tensor([[6, 3], [7, 3]]),
            "advantages": torch.tensor(advantages),
        }
        trainer.compute_loss(model, inputs)
        return trainer.environmental_gradient_evidence[0]

    equal_gradient = measure(equal_advantages)
    varied_gradient = measure(varied_advantages)
    assert equal_gradient["component"] == "environmental_verification_policy"
    assert equal_gradient["norm"] == 0.0
    assert varied_gradient["component"] == "environmental_verification_policy"
    assert varied_gradient["norm"] > 0
    assert equal_gradient["environmental_advantages_sha256"]
    assert equal_gradient["environmental_signal_sha256"]
    assert equal_gradient["optimization_input_sha256"]


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

    with pytest.raises(ValueError, match="environmental|admissible"):
        _write_export(
            tmp_path,
            [
                _group(equal_rewards, [0.0, 0.0]),
                _group(equal_rewards, [0.0, 0.0]),
            ],
            monkeypatch,
        )
