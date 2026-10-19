"""Pinned-TRL mechanics checks with tiny random CPU fixtures only."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from types import MethodType, SimpleNamespace

import pytest

from bench.grpo_eval import ACTION_INSTRUCTION
from tests.test_grpo_observation_first import COMPLETION, _inputs, _Lifecycle
from training.grpo_observation_first import (
    ObservationFirstGRPOMixin,
    make_trl_0191_observation_first_trainer,
)

_REQUIRE_TRAIN_STACK = os.environ.get("ATLASOPS_REQUIRE_TRL_INTEGRATION") == "1"
_PINNED_DISTRIBUTIONS = {
    "trl": "0.19.1",
    "transformers": "4.57.6",
    "peft": "0.17.1",
    "accelerate": "1.14.0",
    "huggingface_hub": "0.36.2",
}


def _missing_optional_stack(message: str) -> None:
    if _REQUIRE_TRAIN_STACK:
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


@pytest.fixture(scope="module")
def _runtime():
    previous = {
        name: os.environ.get(name)
        for name in (
            "HF_HUB_OFFLINE",
            "TRANSFORMERS_OFFLINE",
            "HF_DATASETS_OFFLINE",
        )
    }
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
        }
    )
    try:
        mismatches = []
        for distribution, expected in _PINNED_DISTRIBUTIONS.items():
            try:
                actual = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                actual = None
            if actual != expected:
                mismatches.append(f"{distribution}={actual or 'missing'} (need {expected})")
        if os.environ.get("ATLASOPS_REQUIRE_PINNED_CPU_STACK") == "1":
            for distribution, expected in {
                "torch": "2.7.1", "datasets": "4.8.5", "tokenizers": "0.22.2"
            }.items():
                try:
                    actual = importlib.metadata.version(distribution)
                except importlib.metadata.PackageNotFoundError:
                    actual = None
                if actual is None or actual.split("+", 1)[0] != expected:
                    mismatches.append(f"{distribution}={actual or 'missing'} (need {expected})")
        if mismatches:
            _missing_optional_stack(
                "Pinned TRL integration dependencies are unavailable: "
                + ", ".join(mismatches)
            )

        try:
            import torch
            from datasets import Dataset
            from tokenizers import Tokenizer, decoders, models
            from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast
            from trl import GRPOConfig, GRPOTrainer
        except (ImportError, OSError, RuntimeError) as exc:
            _missing_optional_stack(
                f"Pinned TRL integration dependencies cannot be imported: {type(exc).__name__}"
            )

        yield SimpleNamespace(
            torch=torch,
            Dataset=Dataset,
            Tokenizer=Tokenizer,
            decoders=decoders,
            models=models,
            GPT2Config=GPT2Config,
            GPT2LMHeadModel=GPT2LMHeadModel,
            PreTrainedTokenizerFast=PreTrainedTokenizerFast,
            GRPOConfig=GRPOConfig,
            GRPOTrainer=GRPOTrainer,
        )
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _observed_prompt(lifecycle: _Lifecycle) -> str:
    state = json.loads(json.dumps(lifecycle.state, ensure_ascii=False))
    state["instruction"] = ACTION_INSTRUCTION
    return json.dumps(
        state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _tiny_tokenizer(runtime, lifecycle: _Lifecycle, *, extra_completions=()):
    prompt = _observed_prompt(lifecycle)
    vocab = {
        "[UNK]": 0,
        "[PAD]": 1,
        "[BOS]": 2,
        "[EOS]": 3,
        prompt: 4,
        COMPLETION: 5,
    }
    for completion in extra_completions:
        if completion not in vocab:
            vocab[completion] = len(vocab)
    vocab["stale catalogue prompt"] = len(vocab)
    backend = runtime.Tokenizer(runtime.models.WordLevel(vocab, unk_token="[UNK]"))
    backend.decoder = runtime.decoders.Fuse()
    return runtime.PreTrainedTokenizerFast(
        tokenizer_object=backend,
        unk_token="[UNK]",
        pad_token="[PAD]",
        bos_token="[BOS]",
        eos_token="[EOS]",
    )


def _lora_state_sha256(model, torch) -> str:
    digest = hashlib.sha256()
    parameters = sorted(
        (name, parameter)
        for name, parameter in model.named_parameters()
        if "lora_" in name
    )
    if not parameters:
        raise AssertionError("Expected trainable LoRA parameters")
    for name, parameter in parameters:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(
            parameter.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes()
        )
    return digest.hexdigest()


def _build_trainer(runtime, tmp_path, *, controlled=False):
    torch = runtime.torch
    torch.manual_seed(2026)
    if controlled:
        from training.grpo_controlled import _observation_lineage

        class ControlledFixtureLifecycle(_Lifecycle):
            def before_action(self, snapshot, index, *, g9_group_token):
                return super().before_action(snapshot, index)

            def execute(self, raw_completion, state, index, *, g9_group_token):
                result = super().execute(raw_completion, state, index)
                return {
                    **result,
                    "reward": 0.125,
                    "environmental_reward": 0.125,
                    "environmental_components": {
                        "r_verified_resolution": 0.0,
                        "r_required_check_coverage": 0.125,
                    },
                    "environmental_admissible": True,
                    "terminal_block": None,
                    "g9_group_token": g9_group_token,
                    **_observation_lineage(state),
                }

        lifecycle = ControlledFixtureLifecycle()
    else:
        lifecycle = _Lifecycle()
    completion_variant = COMPLETION.replace(",", ", ")
    tokenizer = _tiny_tokenizer(
        runtime,
        lifecycle,
        extra_completions=(completion_variant,) if controlled else (),
    )
    model = runtime.GPT2LMHeadModel(
        runtime.GPT2Config(
            vocab_size=len(tokenizer),
            n_positions=1024,
            n_ctx=1024,
            n_embd=16,
            n_layer=1,
            n_head=2,
            bos_token_id=tokenizer.bos_token_id,
            eos_token_id=tokenizer.eos_token_id,
            pad_token_id=tokenizer.pad_token_id,
        )
    )
    model.config._name_or_path = "atlasops-tiny-cpu-fixture"
    model.train()
    if controlled:
        from training.grpo_controlled import (
            ControlledGRPOMixin,
            _environmental_advantage_sha256,
            _environmental_advantage_values,
            _environmental_signal_sha256,
            _optimization_input_sha256,
        )

        class ControlledFixtureTrainer(ControlledGRPOMixin, runtime.GRPOTrainer):
            def _generate_and_score_completions(self, inputs):
                result = super()._generate_and_score_completions(inputs)
                advantages = _environmental_advantage_values(result)
                self._environmental_group_evidence.append(
                    {
                        "group": len(self.observation_evidence),
                        "environmental_advantages_sha256":
                            _environmental_advantage_sha256(advantages),
                        "environmental_signal_sha256":
                            _environmental_signal_sha256(result),
                        "optimization_input_sha256":
                            _optimization_input_sha256(result),
                    }
                )
                return result

        trainer_type = ControlledFixtureTrainer
    else:
        trainer_type = make_trl_0191_observation_first_trainer()
    assert issubclass(trainer_type, ObservationFirstGRPOMixin)
    assert issubclass(trainer_type, runtime.GRPOTrainer)

    args = runtime.GRPOConfig(
        output_dir=str(tmp_path),
        seed=2026,
        use_cpu=True,
        bf16=False,
        fp16=False,
        per_device_train_batch_size=2,
        gradient_accumulation_steps=1,
        num_generations=2,
        max_prompt_length=None,
        max_completion_length=2,
        num_iterations=2,
        beta=0.0,
        use_vllm=False,
        remove_unused_columns=False,
        report_to=[],
        disable_tqdm=True,
        save_strategy="no",
    )
    trainer = trainer_type(
        model=model,
        args=args,
        train_dataset=runtime.Dataset.from_list(_inputs()),
        processing_class=tokenizer,
        observation_lifecycle=lifecycle,
    )
    generated_inputs = []
    forward_devices = []
    real_forward = model.forward
    generated_completions = (
        [COMPLETION, completion_variant]
        if controlled
        else [COMPLETION, COMPLETION]
    )
    completion_ids = [
        tokenizer.encode(completion, add_special_tokens=False)
        for completion in generated_completions
    ]
    assert all(len(ids) == 1 for ids in completion_ids)

    def fixed_generate(_model, input_ids, **kwargs):
        generated_inputs.append(input_ids.detach().cpu().clone())
        action_and_eos = torch.tensor(
            [
                ids + [tokenizer.eos_token_id]
                for ids in completion_ids[: input_ids.shape[0]]
            ],
            dtype=input_ids.dtype,
            device=input_ids.device,
        )
        return torch.cat((input_ids, action_and_eos), dim=1)

    def capture_forward(_model, *args, **kwargs):
        input_ids = kwargs.get("input_ids", args[0] if args else None)
        if input_ids is not None:
            forward_devices.append(input_ids.device.type)
        return real_forward(*args, **kwargs)

    model.generate = MethodType(fixed_generate, model)
    model.forward = MethodType(capture_forward, model)
    return trainer, model, tokenizer, lifecycle, generated_inputs, forward_devices


def _force_trl_nonidentity_shuffle(runtime, monkeypatch):
    trainer_module = sys.modules[runtime.GRPOTrainer.__module__]
    shuffle_tensor_dict = trainer_module.shuffle_tensor_dict
    orders = []
    original_randperm = runtime.torch.randperm

    def force_shuffle(tensor_dict):
        batch_size = next(
            value.shape[0]
            for value in tensor_dict.values()
            if runtime.torch.is_tensor(value)
        )
        if batch_size != 2:
            return shuffle_tensor_dict(tensor_dict)

        def reverse_two_rows(size, *args, **kwargs):
            if size == 2:
                orders.append([1, 0])
                return runtime.torch.tensor(
                    [1, 0],
                    dtype=kwargs.get("dtype", runtime.torch.int64),
                    device=kwargs.get("device"),
                )
            return original_randperm(size, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(runtime.torch, "randperm", reverse_two_rows)
            return shuffle_tensor_dict(tensor_dict)

    monkeypatch.setattr(trainer_module, "shuffle_tensor_dict", force_shuffle)
    return orders


def test_installed_controlled_loss_accepts_trl_nonidentity_shuffle(
    _runtime, tmp_path, monkeypatch
):
    trainer, model, tokenizer, _lifecycle, _generated_inputs, _forward_devices = (
        _build_trainer(_runtime, tmp_path, controlled=True)
    )
    orders = _force_trl_nonidentity_shuffle(_runtime, monkeypatch)
    prepared = trainer._prepare_inputs(_inputs())

    original_completion_ids = [
        tokenizer.encode(completion, add_special_tokens=False)[0]
        for completion in (COMPLETION, COMPLETION.replace(",", ", "))
    ]
    assert orders == [[1, 0]]
    assert prepared["completion_ids"][:, 0].tolist() == original_completion_ids[::-1]

    loss = trainer.compute_loss(model, prepared)

    assert _runtime.torch.isfinite(loss).item()
    assert prepared["g9_sample_indices"].tolist() == [1, 0]
    assert len(trainer.environmental_gradient_evidence) == 1


@pytest.mark.parametrize("field", ["completion_ids", "advantages"])
def test_installed_controlled_loss_rejects_tampering_after_trl_shuffle(
    _runtime, tmp_path, monkeypatch, field
):
    trainer, model, tokenizer, _lifecycle, _generated_inputs, _forward_devices = (
        _build_trainer(_runtime, tmp_path, controlled=True)
    )
    orders = _force_trl_nonidentity_shuffle(_runtime, monkeypatch)
    prepared = trainer._prepare_inputs(_inputs())
    assert orders == [[1, 0]]
    prepared[field] = prepared[field].clone()
    if field == "completion_ids":
        prepared[field][0, 0] = tokenizer.pad_token_id
    else:
        prepared[field][0] += 0.25

    with pytest.raises(RuntimeError, match="differ from the environmental group"):
        trainer.compute_loss(model, prepared)

    assert trainer.environmental_gradient_evidence == ()


def test_controlled_probe_preserves_rng_update_and_checkpoint_profile():
    from functools import partial

    torch = pytest.importorskip("torch")
    from training.grpo_controlled import ControlledGRPOMixin

    def unused_checkpoint(*args, **kwargs):
        raise AssertionError("The fixture does not run checkpoint recomputation")

    class RewardLossBackend:
        def compute_loss(self, model, inputs, *args, **kwargs):
            outputs = model(inputs["features"]).squeeze(-1)
            return -(outputs * inputs["advantages"]).mean()

    class RewardProbeTrainer(ControlledGRPOMixin, RewardLossBackend):
        def __init__(self):
            self._environmental_gradient_evidence = []
            self._environmental_group_evidence = []
            self.state = SimpleNamespace(global_step=0)
            self.use_liger_loss = False
            self._metrics = {}
            self.beta = 0.04
            self.accelerator = SimpleNamespace(unwrap_model=lambda model: model)

    class CheckpointedPolicy(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.dropout = torch.nn.Dropout(0.5)
            self.projection = torch.nn.Linear(8, 1, bias=False)
            self.gradient_checkpointing = True
            self._gradient_checkpointing_func = partial(
                unused_checkpoint, use_reentrant=False, preserve_rng_state=False
            )
            self.projection.gradient_checkpointing = False
            self.projection._gradient_checkpointing_func = partial(
                unused_checkpoint, use_reentrant=False, determinism_check="none"
            )

        @property
        def is_gradient_checkpointing(self):
            return any(
                getattr(module, "gradient_checkpointing", False)
                for module in self.modules()
            )

        def gradient_checkpointing_enable(self, gradient_checkpointing_kwargs=None):
            kwargs = (
                {"use_reentrant": True}
                if gradient_checkpointing_kwargs is None
                else gradient_checkpointing_kwargs
            )
            checkpoint_function = partial(unused_checkpoint, **kwargs)
            for module in self.modules():
                if hasattr(module, "gradient_checkpointing"):
                    module.gradient_checkpointing = True
                    module._gradient_checkpointing_func = checkpoint_function

        def forward(self, inputs):
            return self.projection(self.dropout(inputs))

    def run_step(with_probe):
        torch.manual_seed(101)
        model = CheckpointedPolicy()
        original_settings = [
            (
                module,
                module.gradient_checkpointing,
                module._gradient_checkpointing_func,
            )
            for module in model.modules()
            if hasattr(module, "gradient_checkpointing")
        ]
        torch.manual_seed(2026)
        trainer = RewardProbeTrainer()
        optimizer = torch.optim.SGD(model.parameters(), lr=0.01)
        inputs = {
            "features": torch.ones((2, 8)),
            "prompt_ids": torch.tensor([[4, 5], [4, 5]]),
            "completion_ids": torch.tensor([[6, 3], [7, 3]]),
            "advantages": torch.tensor([1.0, -1.0]),
        }
        loss = (
            trainer.compute_loss(model, inputs)
            if with_probe
            else RewardLossBackend.compute_loss(trainer, model, inputs)
        )
        loss_value = loss.detach().clone()
        loss.backward()
        optimizer.step()
        updated_parameters = [
            parameter.detach().clone() for parameter in model.parameters()
        ]
        next_random_values = torch.rand(8)
        restored_checkpointing = all(
            module.gradient_checkpointing is enabled
            and module._gradient_checkpointing_func is checkpoint_function
            for module, enabled, checkpoint_function in original_settings
        )
        return (
            loss_value,
            updated_parameters,
            next_random_values,
            restored_checkpointing,
            tuple(trainer._environmental_gradient_evidence),
        )

    without_probe = run_step(with_probe=False)
    with_probe = run_step(with_probe=True)

    assert torch.equal(without_probe[0], with_probe[0])
    assert all(
        torch.equal(expected, actual)
        for expected, actual in zip(without_probe[1], with_probe[1], strict=True)
    )
    assert torch.equal(without_probe[2], with_probe[2])
    assert with_probe[3]
    assert with_probe[4][0]["component"] == "environmental_verification_policy"
    assert with_probe[4][0]["norm"] > 0


def test_installed_trl_uses_observed_tokens_and_scores_the_exact_generated_action(
    _runtime, tmp_path
):
    trainer, model, tokenizer, lifecycle, generated_inputs, forward_devices = (
        _build_trainer(_runtime, tmp_path)
    )
    parameters_before = [parameter.detach().clone() for parameter in model.parameters()]

    prepared = trainer._prepare_inputs(_inputs())

    expected_prompt = _observed_prompt(lifecycle)
    expected_prompt_ids = tokenizer(
        expected_prompt, add_special_tokens=False, return_tensors="pt"
    )["input_ids"]
    expected_generation_ids = expected_prompt_ids.expand(2, -1)
    stale_prompt_ids = tokenizer(
        "stale catalogue prompt", add_special_tokens=False, return_tensors="pt"
    )["input_ids"]
    assert len(generated_inputs) == 1
    assert _runtime.torch.equal(generated_inputs[0], expected_generation_ids)
    assert not _runtime.torch.equal(generated_inputs[0][0], stale_prompt_ids[0])
    assert tokenizer.batch_decode(generated_inputs[0], skip_special_tokens=True) == [
        expected_prompt,
        expected_prompt,
    ]
    assert forward_devices and set(forward_devices) == {"cpu"}
    assert "advantages" in prepared
    assert prepared["advantages"].shape[0] == 2

    executed = [event[2] for event in lifecycle.events if event[0] == "execute"]
    assert executed == [COMPLETION, COMPLETION]
    evidence = trainer.observation_evidence
    assert len(evidence) == 1
    assert evidence[0]["status"] == "completed"
    assert evidence[0]["result_classification"] == "NON_EMPIRICAL"
    assert evidence[0]["certification_status"] == "NOT_CERTIFIED"
    assert [record["reward"] for record in evidence[0]["records"]] == [0.125, 0.125]
    from training.grpo_reward import has_reward_driven_advantages

    equal_reward_advantages = prepared["advantages"].detach().cpu().reshape(-1).tolist()
    assert not _runtime.torch.any(prepared["advantages"] != 0).item()
    assert not has_reward_driven_advantages(
        [record["reward"] for record in evidence[0]["records"]],
        equal_reward_advantages,
    )
    assert trainer.optimizer is None
    assert model.training is True
    assert all(
        _runtime.torch.equal(before, after.detach())
        for before, after in zip(parameters_before, model.parameters(), strict=True)
    )
    assert all(parameter.grad is None for parameter in model.parameters())
    assert not any(
        path.name.startswith("checkpoint-")
        or path.name in {"model.safetensors", "pytorch_model.bin", "optimizer.pt"}
        for path in tmp_path.iterdir()
    )


def test_installed_trl_evaluation_refuses_before_observation_or_generation(
    _runtime, tmp_path
):
    trainer, model, _tokenizer, lifecycle, generated_inputs, _forward_devices = (
        _build_trainer(_runtime, tmp_path)
    )
    model.eval()

    with pytest.raises(RuntimeError, match="training mode"):
        trainer._prepare_inputs(_inputs())

    assert lifecycle.events == []
    assert trainer.observation_evidence == ()
    assert generated_inputs == []
    assert trainer._step == 0


@pytest.mark.parametrize("mode", ["rewarded", "equal-reward-kl-only"])
def test_installed_controlled_hook_measures_environmental_policy_gradient(
    _runtime, tmp_path, mode, monkeypatch
):
    from tests.test_grpo_controlled import action
    from training.grpo_controlled import (
        SCENARIO,
        CapacityEnvironment,
        ControlledGRPOMixin,
        EpisodeLedger,
    )

    runtime = _runtime
    runtime.torch.manual_seed(2026)
    max_steps = 1 if mode == "rewarded" else 2
    ledger = EpisodeLedger(tmp_path / "episodes.jsonl")
    environment = CapacityEnvironment(ledger)
    preview = environment.begin(SCENARIO)["state"]
    environment.finish({}, "fixture_preview")
    preview["instruction"] = ACTION_INSTRUCTION
    prompt = json.dumps(preview, sort_keys=True, separators=(",", ":"))
    valid = action()
    partial = action(replicas=2)
    equal_reward = action(replicas=1)
    vocab = {"[UNK]": 0, "[PAD]": 1, "[BOS]": 2, "[EOS]": 3,
             prompt: 4, valid: 5, "malformed": 6, equal_reward: 7, partial: 8}
    backend = runtime.Tokenizer(runtime.models.WordLevel(vocab, unk_token="[UNK]"))
    backend.decoder = runtime.decoders.Fuse()
    tokenizer = runtime.PreTrainedTokenizerFast(
        tokenizer_object=backend, unk_token="[UNK]", pad_token="[PAD]",
        bos_token="[BOS]", eos_token="[EOS]",
    )
    base_model = runtime.GPT2LMHeadModel(runtime.GPT2Config(
        vocab_size=len(tokenizer), n_positions=1024, n_embd=16, n_layer=1, n_head=2,
        bos_token_id=2, eos_token_id=3, pad_token_id=1,
    ))
    base_model.config._name_or_path = "controlled-random-cpu-fixture"
    base_model.config.use_cache = False
    base_model.enable_input_require_grads()
    base_model.gradient_checkpointing_enable()
    base_checkpoint = tmp_path / "base"
    base_model.save_pretrained(base_checkpoint)
    from peft import LoraConfig, get_peft_model

    model = get_peft_model(base_model, LoraConfig(
        r=2, lora_alpha=4, target_modules=["c_attn"], task_type="CAUSAL_LM",
    ))
    if mode == "equal-reward-kl-only":
        with runtime.torch.no_grad():
            for name, parameter in model.named_parameters():
                if "lora_A" in name or "lora_B" in name:
                    parameter.fill_(0.05)
    before = {
        name: parameter.detach().clone()
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    }
    assert before and all("lora_" in name for name in before)

    def fixture_generate(_model, input_ids, **kwargs):
        token_rows = (
            [[5, 3], [8, 3]]
            if mode == "rewarded"
            else [[7, 3], [7, 3]]
        )
        tokens = runtime.torch.tensor(
            token_rows, device=input_ids.device, dtype=input_ids.dtype
        )
        assert input_ids.shape[0] == 2
        return runtime.torch.cat((input_ids, tokens), dim=1)

    model.generate = MethodType(fixture_generate, model)

    def capture_loss_rows(inputs):
        sample_indices = inputs.get("g9_sample_indices")
        return {
            "sample_indices": (
                sample_indices.detach().cpu().tolist()
                if runtime.torch.is_tensor(sample_indices)
                else None
            ),
            "completion_ids": inputs["completion_ids"].detach().cpu().tolist(),
            "advantages": (
                inputs["advantages"].detach().cpu().reshape(-1).tolist()
            ),
        }

    class ControlledFixtureTrainer(ControlledGRPOMixin, runtime.GRPOTrainer):
        def __init__(self, *args, **kwargs):
            self.reward_mechanics_groups = []
            self.total_loss_input_rows = []
            self.environmental_gradient_input_rows = []
            self.advantage_swap_rejected = False
            super().__init__(*args, **kwargs)

        def _generate_and_score_completions(self, inputs):
            result = super()._generate_and_score_completions(inputs)
            group = self.observation_evidence[-1]
            components = [
                record["environmental_components"] for record in group["records"]
            ]
            environmental_rewards = [
                record["environmental_reward"] for record in group["records"]
            ]
            admissible = [
                record["environmental_admissible"] for record in group["records"]
            ]
            advantages = (
                result["advantages"].detach().cpu().reshape(-1).tolist()
            )
            from training.grpo_controlled import (
                _environmental_advantage_sha256,
                _environmental_signal_sha256,
                _optimization_input_sha256,
            )
            from training.grpo_reward import has_environmental_reward_signal

            environmental_signal = has_environmental_reward_signal(
                components, admissible, advantages
            )
            self._environmental_group_evidence.append(
                {
                    "group": len(self.observation_evidence),
                    "environmental_rewards": environmental_rewards,
                    "environmental_advantages": advantages,
                    "sample_indices": (
                        result["g9_sample_indices"].detach().cpu().tolist()
                        if runtime.torch.is_tensor(
                            result.get("g9_sample_indices")
                        )
                        else None
                    ),
                    "completion_ids":
                        result["completion_ids"].detach().cpu().tolist(),
                    "environmental_advantages_sha256":
                        _environmental_advantage_sha256(advantages),
                    "environmental_signal_sha256":
                        _environmental_signal_sha256(result),
                    "optimization_input_sha256":
                        _optimization_input_sha256(result),
                    "environmental_signal": environmental_signal,
                }
            )
            self.reward_mechanics_groups.append(environmental_signal)
            return result

        def _measure_environmental_policy_gradient(
            self, model, inputs, **kwargs
        ):
            self.environmental_gradient_input_rows.append(
                capture_loss_rows(inputs)
            )
            return super()._measure_environmental_policy_gradient(
                model, inputs, **kwargs
            )

        def compute_loss(
            self, model, inputs, return_outputs=False, num_items_in_batch=None
        ):
            if mode == "rewarded" and not self.advantage_swap_rejected:
                self.advantage_swap_rejected = True
                swapped_inputs = dict(inputs)
                swapped_inputs["advantages"] = inputs["advantages"].flip(0)
                with pytest.raises(
                    RuntimeError,
                    match="differ from the environmental group",
                ):
                    super().compute_loss(
                        model, swapped_inputs, return_outputs, num_items_in_batch
                    )
                assert self.environmental_gradient_evidence == ()
            self.total_loss_input_rows.append(capture_loss_rows(inputs))
            previous_gradients = {
                name: (
                    None if parameter.grad is None else parameter.grad.detach().clone()
                )
                for name, parameter in model.named_parameters()
                if parameter.requires_grad
            }
            loss = super().compute_loss(
                model, inputs, return_outputs, num_items_in_batch
            )
            for name, parameter in model.named_parameters():
                if not parameter.requires_grad:
                    continue
                previous = previous_gradients[name]
                if previous is None:
                    assert parameter.grad is None
                else:
                    assert runtime.torch.equal(previous, parameter.grad)
            return loss

    from transformers import TrainerCallback

    gradient_records = []
    optimizer_steps = []
    previous_parameters = {
        name: parameter.detach().clone() for name, parameter in before.items()
    }

    class GradientProbe(TrainerCallback):
        def on_pre_optimizer_step(self, args, state, control, **kwargs):
            signal = trainer._environmental_group_evidence[state.global_step]
            gradients = [
                parameter.grad
                for parameter in model.parameters()
                if parameter.requires_grad and parameter.grad is not None
            ]
            finite = bool(gradients) and all(
                runtime.torch.isfinite(gradient).all().item()
                for gradient in gradients
            )
            norm = sum(
                float(gradient.float().norm().item()) for gradient in gradients
            )
            gradient_records.append(
                {
                    "step": state.global_step,
                    "group": state.global_step + 1,
                    "finite": finite,
                    "norm": norm,
                    "environmental_advantages_sha256":
                        signal["environmental_advantages_sha256"],
                    "environmental_signal_sha256":
                        signal["environmental_signal_sha256"],
                    "optimization_input_sha256":
                        signal["optimization_input_sha256"],
                }
            )

        def on_step_end(self, args, state, control, **kwargs):
            group = state.global_step
            signal = trainer._environmental_group_evidence[group - 1]
            parameters_after_step = dict(model.named_parameters())
            changed = 0
            for name, previous in previous_parameters.items():
                current = parameters_after_step[name].detach()
                if not runtime.torch.equal(previous, current):
                    changed += 1
                    previous_parameters[name] = current.clone()
            optimizer_steps.append(
                {
                    "step": group,
                    "group": group,
                    "changed_trainable_tensors": changed,
                    "environmental_advantages_sha256":
                        signal["environmental_advantages_sha256"],
                    "environmental_signal_sha256":
                        signal["environmental_signal_sha256"],
                    "optimization_input_sha256":
                        signal["optimization_input_sha256"],
                }
            )

    args = runtime.GRPOConfig(
        output_dir=str(tmp_path / "trainer"), use_cpu=True, bf16=False, fp16=False,
        seed=2026,
        max_steps=max_steps, per_device_train_batch_size=2,
        gradient_accumulation_steps=1,
        num_generations=2, max_prompt_length=None, max_completion_length=2,
        beta=0.04, use_vllm=False, report_to=[], save_strategy="no",
        remove_unused_columns=False, disable_tqdm=True, learning_rate=0.001,
    )
    try:
        trainer = ControlledFixtureTrainer(
            model=model, args=args,
            train_dataset=runtime.Dataset.from_list([{"prompt": "", "scenario_id": SCENARIO}]),
            processing_class=tokenizer, observation_lifecycle=environment,
            callbacks=[GradientProbe()],
        )
        shuffle_orders = _force_trl_nonidentity_shuffle(runtime, monkeypatch)
        model.train()
        trainer.train()
        assert trainer.state.global_step == max_steps
        assert trainer.optimizer is not None
        assert len(gradient_records) == max_steps
        assert all(record["finite"] and record["norm"] > 0 for record in gradient_records)
        assert len(optimizer_steps) == max_steps
        assert all(record["changed_trainable_tensors"] > 0 for record in optimizer_steps)
        reward_gradients = list(trainer.environmental_gradient_evidence)
        assert len(reward_gradients) == max_steps
        assert shuffle_orders == [[1, 0]] * max_steps
        assert trainer.advantage_swap_rejected is (mode == "rewarded")
        assert len(trainer.total_loss_input_rows) == max_steps
        assert len(trainer.environmental_gradient_input_rows) == max_steps
        for index, evidence in enumerate(trainer._environmental_group_evidence):
            for observed_rows in (
                trainer.total_loss_input_rows[index],
                trainer.environmental_gradient_input_rows[index],
            ):
                order = observed_rows["sample_indices"]
                if order is None:
                    assert mode == "equal-reward-kl-only"
                    continue
                assert order == [1, 0]
                assert observed_rows["completion_ids"] == [
                    evidence["completion_ids"][sample] for sample in order
                ]
                assert observed_rows["advantages"] == [
                    evidence["environmental_advantages"][sample]
                    for sample in order
                ]
        assert all(
            record["step"] == index and record["group"] == index + 1
            and record["component"] == "environmental_verification_policy"
            and record["finite"]
            and record["environmental_advantages_sha256"]
            == trainer._environmental_group_evidence[index][
                "environmental_advantages_sha256"
            ]
            and record["environmental_signal_sha256"]
            == trainer._environmental_group_evidence[index][
                "environmental_signal_sha256"
            ]
            and record["optimization_input_sha256"]
            == trainer._environmental_group_evidence[index][
                "optimization_input_sha256"
            ]
            for index, record in enumerate(reward_gradients)
        )
        assert trainer.beta == 0.04
        assert trainer.args.beta == 0.04
        assert model.is_gradient_checkpointing is True
        assert trainer.reward_mechanics_groups == [mode == "rewarded"] * max_steps
        assert any(
            not runtime.torch.equal(before[name], dict(model.named_parameters())[name].detach())
            for name in before
        )
        assert environment.steps == max_steps * 2

        if mode == "rewarded":
            assert reward_gradients[0]["norm"] > 0
            assert [
                record["reward"] for record in trainer.observation_evidence[0]["records"]
            ] == [1.0, 0.125]
            assert [
                record["environmental_reward"]
                for record in trainer.observation_evidence[0]["records"]
            ] == [1.0, 0.125]
            assert all(
                record["environmental_admissible"]
                for record in trainer.observation_evidence[0]["records"]
            )
        else:
            assert all(record["norm"] == 0.0 for record in reward_gradients)
            assert all(
                [
                    record["environmental_reward"]
                    for record in group["records"]
                ] == [0.125, 0.125]
                for group in trainer.observation_evidence
            )
            from scripts.accept_grpo_controlled import _has_matched_optimizer_update

            signal_evidence = {
                record["group"]: {
                    name: record[name]
                    for name in (
                        "environmental_advantages_sha256",
                        "environmental_signal_sha256",
                        "optimization_input_sha256",
                    )
                }
                for record in trainer._environmental_group_evidence
            }
            rows = [
                {"event": "reward_gradient_observed", **record}
                for record in reward_gradients
            ] + [
                {"event": "gradient_observed", **record}
                for record in gradient_records
            ] + [
                {"event": "optimizer_step_observed", **record}
                for record in optimizer_steps
            ]
            assert not _has_matched_optimizer_update(
                rows,
                {
                    "reward_gradients": reward_gradients,
                    "gradients": gradient_records,
                    "optimizer_steps": optimizer_steps,
                },
                signal_evidence,
            )

        adapter = tmp_path / "adapter"
        model.save_pretrained(adapter, safe_serialization=True)
        expected_sha256 = _lora_state_sha256(model, runtime.torch)
        reload_code = r"""
import hashlib
import sys
import torch
from peft import PeftModel
from transformers import GPT2LMHeadModel

base = GPT2LMHeadModel.from_pretrained(sys.argv[1], local_files_only=True)
model = PeftModel.from_pretrained(
    base, sys.argv[2], local_files_only=True, is_trainable=False
)
digest = hashlib.sha256()
parameters = sorted(
    (name, parameter)
    for name, parameter in model.named_parameters()
    if "lora_" in name
)
for name, parameter in parameters:
    digest.update(name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(
        parameter.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes()
    )
print("ADAPTER_SHA256=" + digest.hexdigest())
"""
        reload_result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                reload_code,
                str(base_checkpoint),
                str(adapter),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            env={
                **os.environ,
                "ATLASOPS_AUTO_HF_INFERENCE": "0",
                "CUDA_VISIBLE_DEVICES": "",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            },
        )
        assert reload_result.returncode == 0, reload_result.stderr
        assert f"ADAPTER_SHA256={expected_sha256}" in reload_result.stdout
    finally:
        ledger.close()


def test_admitted_controlled_run_fails_closed_until_diagnostic_validation(
    tmp_path, monkeypatch
):
    from training import grpo_controlled

    monkeypatch.setattr(
        grpo_controlled, "REWARD_GRADIENT_DIAGNOSTIC_AVAILABLE", False
    )
    output = tmp_path / "controlled-output"
    with pytest.raises(
        RuntimeError, match="environmental-verification policy gradient"
    ):
        grpo_controlled._run_admitted_pilot({"output_path": str(output)})

    assert not output.exists()
    assert list(tmp_path.iterdir()) == []
