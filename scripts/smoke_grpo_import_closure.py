"""Exact pinned-runtime smoke, with tiny random fixtures and no pretrained weights."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from types import MethodType


def smoke(output: Path) -> dict:
    repo = Path(__file__).resolve().parents[1]
    expected = json.loads((repo / "config/sft_pilot_v4.json").read_bytes())["environment"]["package_versions"]
    actual = {name: importlib.metadata.version(name) for name in expected}
    if actual != expected:
        raise ValueError("Import smoke requires the exact approved 72-package runtime")
    if output.exists() or not output.is_absolute() or output.resolve().is_relative_to(repo):
        raise ValueError("Import smoke requires a fresh external absolute output")
    output.mkdir()
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1")
    report = {
        "status": "started", "pretrained_weights_loaded": False,
        "training_performed": False, "classification": "MODEL_FREE_RUNTIME_SMOKE",
        "package_versions": actual, "paths": [],
    }
    try:
        import torch
        import bitsandbytes as bnb
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model
        from tokenizers import Tokenizer, models, decoders
        from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast, BitsAndBytesConfig
        from trl import GRPOConfig, GRPOTrainer
        from training.grpo_controlled import CapacityEnvironment, ControlledGRPOMixin, EpisodeLedger, SCENARIO
        from training.grpo_observation_first import _public_snapshot
        from training.sft_provenance import checkpoint_inventory, write_manifest_atomic
        from scripts.reload_grpo_controlled import reload_checkpoint
        from scripts.accept_grpo_controlled import accept_export

        assert callable(reload_checkpoint)
        report["paths"].append("controlled_training_startup")
        ledger = EpisodeLedger(output / "smoke-episodes.jsonl")
        env = CapacityEnvironment(ledger)
        # Use a separate preview environment so group-1 tokens are exact.
        preview_env = CapacityEnvironment(ledger)
        preview = preview_env.begin(SCENARIO)
        prompt, _ = _public_snapshot(preview["state"])
        preview_env.finish({}, "smoke_preview")
        report["paths"].append("observation_before_generation")
        completion = '{"tool":"kubectl_scale","arguments":{"deployment":"paymentservice","replicas":3},"agent_claimed_resolved":false}'
        vocabulary = {"[UNK]": 0, "[PAD]": 1, "[BOS]": 2, "[EOS]": 3,
                      prompt.decode(): 4, completion: 5, "malformed": 6}
        backend = Tokenizer(models.WordLevel(vocabulary, unk_token="[UNK]"))
        backend.decoder = decoders.Fuse()
        tokenizer = PreTrainedTokenizerFast(
            tokenizer_object=backend, unk_token="[UNK]", pad_token="[PAD]",
            bos_token="[BOS]", eos_token="[EOS]",
        )
        base = GPT2LMHeadModel(GPT2Config(
            vocab_size=7, n_positions=1024, n_embd=16, n_layer=1, n_head=2,
            bos_token_id=2, eos_token_id=3, pad_token_id=1,
        ))
        base.config._name_or_path = "local-random-import-smoke"
        base.save_pretrained(output / "base")
        model = get_peft_model(base, LoraConfig(r=2, target_modules=["c_attn"], task_type="CAUSAL_LM"))
        BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16)
        bnb.optim.PagedAdamW8bit([p for p in model.parameters() if p.requires_grad])

        def fixed_generate(_model, input_ids, **kwargs):
            actions = torch.tensor([[5, 3], [6, 3]], device=input_ids.device)
            return torch.cat((input_ids, actions), dim=1)

        model.generate = MethodType(fixed_generate, model)
        trainer_type = type("ImportSmokeTrainer", (ControlledGRPOMixin, GRPOTrainer), {})
        config = GRPOConfig(
            output_dir=str(output / "trainer"), use_cpu=True, fp16=False, bf16=False,
            per_device_train_batch_size=2, gradient_accumulation_steps=1, num_generations=2,
            max_prompt_length=None, max_completion_length=2, beta=0.04,
            report_to=[], save_strategy="no", remove_unused_columns=False,
        )
        trainer = trainer_type(
            model=model, args=config, processing_class=tokenizer, observation_lifecycle=env,
            train_dataset=Dataset.from_list([{"prompt": "", "scenario_id": SCENARIO}]),
        )
        model.train()
        prepared = trainer._prepare_inputs([{"prompt": "", "scenario_id": SCENARIO}] * 2)
        assert torch.isfinite(prepared["advantages"]).all() and torch.any(prepared["advantages"] != 0)
        assert trainer.optimizer is None and all(p.grad is None for p in model.parameters())
        assert env.steps == 2
        report["paths"].append("pinned_trainer_construction_and_reward_route")
        model.save_pretrained(output / "adapter")
        tokenizer.save_pretrained(output / "adapter")
        trainer.save_state()
        report["fixture_checkpoint"] = checkpoint_inventory(output / "adapter", output / "manifest.json")
        ledger.close()
        report["paths"].append("evidence_persistence")

        reload_code = """
import sys
from pathlib import Path
from training.grpo_reload_isolation import LoopbackGuard
g=LoopbackGuard();g.install();g.install_syscall_guard(block_creation=False)
from transformers import AutoModelForCausalLM,AutoTokenizer
from peft import PeftModel
g.require_clean();g.install_syscall_guard()
p=Path(sys.argv[1])
b=AutoModelForCausalLM.from_pretrained(p/'base',local_files_only=True)
t=AutoTokenizer.from_pretrained(p/'adapter',local_files_only=True)
m=PeftModel.from_pretrained(b,p/'adapter',local_files_only=True,is_trainable=False)
g.require_clean()
assert any('lora_' in n for n,_ in m.named_parameters())
print('FRESH_GUARDED_LOCAL_RELOAD_PASS')
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", reload_code, str(output)],
            cwd=repo, env=os.environ.copy(), capture_output=True, text=True, timeout=60,
        )
        (output / "reload.stdout").write_text(result.stdout)
        (output / "reload.stderr").write_text(result.stderr)
        if result.returncode != 0:
            raise RuntimeError("Fresh guarded model-free reload import smoke failed")
        report["paths"].append("controlled_reload")
        # Initialize actual acceptance and show that fixtures cannot claim training.
        (output / "controlled_grpo_manifest.json").write_text("{}")
        (output / "reload.json").write_text("{}")
        try:
            accept_export(output, output / "reload.json", "0" * 64, "0" * 64)
        except ValueError:
            pass
        else:
            raise AssertionError("Local acceptance admitted incomplete fixture")
        report["paths"].append("local_acceptance_fail_closed")
        assert "uvicorn" not in sys.modules
        report["status"] = "PASS"
        write_manifest_atomic(output / "result.json", report)
    except BaseException as exc:
        report.update(status="FAILED", failure_type=type(exc).__name__)
        (output / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True))
        raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(smoke(args.output), sort_keys=True))


if __name__ == "__main__":
    main()
