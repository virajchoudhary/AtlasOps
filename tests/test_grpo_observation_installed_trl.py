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


def _tiny_tokenizer(runtime, lifecycle: _Lifecycle):
    prompt = _observed_prompt(lifecycle)
    vocab = {
        "[UNK]": 0,
        "[PAD]": 1,
        "[BOS]": 2,
        "[EOS]": 3,
        prompt: 4,
        COMPLETION: 5,
        "stale catalogue prompt": 6,
    }
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


def _build_trainer(runtime, tmp_path):
    torch = runtime.torch
    torch.manual_seed(2026)
    lifecycle = _Lifecycle()
    tokenizer = _tiny_tokenizer(runtime, lifecycle)
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
    completion_ids = tokenizer.encode(COMPLETION, add_special_tokens=False)
    assert len(completion_ids) == 1

    def fixed_generate(_model, input_ids, **kwargs):
        generated_inputs.append(input_ids.detach().cpu().clone())
        action_and_eos = torch.tensor(
            [completion_ids + [tokenizer.eos_token_id]],
            dtype=input_ids.dtype,
            device=input_ids.device,
        ).expand(input_ids.shape[0], -1)
        return torch.cat((input_ids, action_and_eos), dim=1)

    def capture_forward(_model, *args, **kwargs):
        input_ids = kwargs.get("input_ids", args[0] if args else None)
        if input_ids is not None:
            forward_devices.append(input_ids.device.type)
        return real_forward(*args, **kwargs)

    model.generate = MethodType(fixed_generate, model)
    model.forward = MethodType(capture_forward, model)
    return trainer, model, tokenizer, lifecycle, generated_inputs, forward_devices


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


def test_installed_controlled_hook_runs_real_cpu_optimizer_with_fixture_tokens(
    _runtime, tmp_path
):
    """Actual TRL optimization on a random tiny fixture, NOT pretrained evidence."""
    from tests.test_grpo_controlled import action
    from training.grpo_controlled import (
        SCENARIO,
        CapacityEnvironment,
        ControlledGRPOMixin,
        EpisodeLedger,
    )

    runtime = _runtime
    runtime.torch.manual_seed(2026)
    ledger = EpisodeLedger(tmp_path / "episodes.jsonl")
    environment = CapacityEnvironment(ledger)
    preview = environment.begin(SCENARIO)["state"]
    environment.finish({}, "fixture_preview")
    preview["instruction"] = ACTION_INSTRUCTION
    prompt = json.dumps(preview, sort_keys=True, separators=(",", ":"))
    valid = action()
    vocab = {"[UNK]": 0, "[PAD]": 1, "[BOS]": 2, "[EOS]": 3,
             prompt: 4, valid: 5, "malformed": 6}
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
    base_checkpoint = tmp_path / "base"
    base_model.save_pretrained(base_checkpoint)
    from peft import LoraConfig, get_peft_model

    model = get_peft_model(base_model, LoraConfig(
        r=2, lora_alpha=4, target_modules=["c_attn"], task_type="CAUSAL_LM",
    ))
    before = {
        name: parameter.detach().clone()
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    }
    assert before and all("lora_" in name for name in before)

    def fixture_generate(_model, input_ids, **kwargs):
        tokens = runtime.torch.tensor(
            [[5, 3], [6, 3]], device=input_ids.device, dtype=input_ids.dtype
        )
        assert input_ids.shape[0] == 2
        return runtime.torch.cat((input_ids, tokens), dim=1)

    model.generate = MethodType(fixture_generate, model)

    class ControlledFixtureTrainer(ControlledGRPOMixin, runtime.GRPOTrainer):
        def __init__(self, *args, **kwargs):
            self.reward_mechanics_groups = []
            super().__init__(*args, **kwargs)

        def _generate_and_score_completions(self, inputs):
            result = super()._generate_and_score_completions(inputs)
            group = self.observation_evidence[-1]
            rewards = [record["reward"] for record in group["records"]]
            advantages = (
                result["advantages"].detach().cpu().reshape(-1).tolist()
            )
            from training.grpo_reward import has_reward_driven_advantages

            self.reward_mechanics_groups.append(
                has_reward_driven_advantages(rewards, advantages)
            )
            return result

    from transformers import TrainerCallback

    gradient_records = []

    class GradientProbe(TrainerCallback):
        def on_pre_optimizer_step(self, args, state, control, **kwargs):
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
            gradient_records.append({"finite": finite, "norm": norm})

    args = runtime.GRPOConfig(
        output_dir=str(tmp_path / "trainer"), use_cpu=True, bf16=False, fp16=False,
        seed=2026,
        max_steps=1, per_device_train_batch_size=2, gradient_accumulation_steps=1,
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
        model.train()
        trainer.train()
        assert trainer.state.global_step == 1
        assert trainer.optimizer is not None
        assert len(gradient_records) == 1
        assert gradient_records[0]["finite"]
        assert gradient_records[0]["norm"] > 0
        assert any(
            len({r["reward"] for r in group["records"]}) > 1
            for group in trainer.observation_evidence
        )
        assert trainer.reward_mechanics_groups == [True]
        parameters_after = dict(model.named_parameters())
        assert any(
            not runtime.torch.equal(before[name], parameters_after[name].detach())
            for name in before
        )
        assert environment.steps == 2

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
