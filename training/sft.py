"""Reproducible QLoRA SFT entrypoint for AtlasOps.

The runner writes durable provenance before optional ML dependencies or model
weights are loaded, updates it through the training lifecycle, and hashes the
resulting adapter/checkpoint files on successful completion.
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.splits import TRAIN_SEED
from training.sft_provenance import (
    create_run_manifest,
    file_sha256,
    mark_completed,
    mark_failed,
    mark_running,
    write_manifest_atomic,
)
from training.sft_rendering import TEMPLATE_PATH

TARGET_MODULES = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Base model id/path")
    parser.add_argument("--model-revision", required=True, help="Exact model revision or commit")
    parser.add_argument("--tokenizer", help="Tokenizer id/path; defaults to --model")
    parser.add_argument(
        "--tokenizer-revision",
        help="Exact tokenizer revision; defaults to model revision",
    )
    parser.add_argument("--data", required=True, help="Path to JSONL SFT corpus")
    parser.add_argument("--output", required=True, help="Output directory for the LoRA adapter")
    parser.add_argument("--provenance-output", help="Run manifest path; defaults inside --output")
    parser.add_argument(
        "--role",
        default="all",
        choices=["triage", "diagnosis", "remediation", "comms", "all"],
    )
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-seq-len", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=TRAIN_SEED)
    return parser.parse_args()


def _hyperparameters(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "quantization": {
            "load_in_4bit": True,
            "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": "bfloat16",
            "bnb_4bit_use_double_quant": True,
        },
        "lora": {
            "r": 16,
            "alpha": 32,
            "dropout": 0.05,
            "target_modules": TARGET_MODULES,
            "bias": "none",
        },
        "epochs": args.epochs,
        "learning_rate": args.lr,
        "batch_size": args.batch_size,
        "gradient_accumulation_steps": args.grad_accum,
        "max_sequence_length": args.max_seq_len,
        "seed": args.seed,
        "optimizer": "paged_adamw_8bit",
        "bf16": True,
        "assistant_only_loss": True,
        "chat_template_path": str(TEMPLATE_PATH),
        "chat_template_sha256": file_sha256(TEMPLATE_PATH),
    }


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output)
    corpus_path = Path(args.data)
    tokenizer_id = args.tokenizer or args.model
    tokenizer_revision = args.tokenizer_revision or args.model_revision
    canonical_manifest_path = output_dir / "sft_run_manifest.json"
    manifest_path = (
        Path(args.provenance_output)
        if args.provenance_output
        else canonical_manifest_path
    )
    if manifest_path.resolve() != canonical_manifest_path.resolve():
        raise ValueError(
            "SFT provenance output must be <output>/sft_run_manifest.json "
            "so G8 and G9 can validate the completed adapter"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = create_run_manifest(
        corpus_path=corpus_path,
        output_dir=output_dir,
        base_model=args.model,
        base_model_revision=args.model_revision,
        tokenizer=tokenizer_id,
        tokenizer_revision=tokenizer_revision,
        role=args.role,
        hyperparameters=_hyperparameters(args),
        seed=args.seed,
    )
    write_manifest_atomic(manifest_path, manifest)

    try:
        from datasets import load_dataset
        from peft import (
            LoraConfig,
            TaskType,
            get_peft_model,
            prepare_model_for_kbit_training,
        )
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            set_seed,
        )
        from trl import SFTConfig, SFTTrainer

        set_seed(args.seed)
        dataset = load_dataset("json", data_files=str(corpus_path), split="train")
        if args.role != "all":
            dataset = dataset.filter(lambda row: row.get("role") == args.role)
        if len(dataset) == 0:
            raise ValueError(f"No training examples remain for role={args.role}")

        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_id,
            revision=tokenizer_revision,
            trust_remote_code=True,
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        tokenizer.chat_template = TEMPLATE_PATH.read_text(encoding="utf-8")

        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype="bfloat16",
            bnb_4bit_use_double_quant=True,
        )
        model = AutoModelForCausalLM.from_pretrained(
            args.model,
            revision=args.model_revision,
            quantization_config=quantization,
            device_map="auto",
            trust_remote_code=True,
        )
        model = prepare_model_for_kbit_training(model)
        model = get_peft_model(
            model,
            LoraConfig(
                task_type=TaskType.CAUSAL_LM,
                r=16,
                lora_alpha=32,
                lora_dropout=0.05,
                target_modules=TARGET_MODULES,
                bias="none",
            ),
        )
        model.print_trainable_parameters()

        resolved_model_revision = (
            getattr(getattr(model, "config", None), "_commit_hash", None)
            or args.model_revision
        )
        resolved_tokenizer_revision = (
            getattr(tokenizer, "init_kwargs", {}).get("_commit_hash")
            or tokenizer_revision
        )
        manifest = mark_running(
            manifest,
            resolved_model_revision=str(resolved_model_revision),
            resolved_tokenizer_revision=str(resolved_tokenizer_revision),
        )
        write_manifest_atomic(manifest_path, manifest)

        sft_parameters = inspect.signature(SFTConfig).parameters
        if "assistant_only_loss" not in sft_parameters:
            raise RuntimeError(
                "Installed TRL does not support assistant_only_loss; refusing unsafe training"
            )
        length_parameter = (
            "max_length" if "max_length" in sft_parameters else "max_seq_length"
        )
        train_config_values = {
            "output_dir": str(output_dir),
            "num_train_epochs": args.epochs,
            "learning_rate": args.lr,
            "per_device_train_batch_size": args.batch_size,
            "gradient_accumulation_steps": args.grad_accum,
            "bf16": True,
            "logging_steps": 10,
            "save_strategy": "epoch",
            "report_to": [],
            "optim": "paged_adamw_8bit",
            "seed": args.seed,
            "data_seed": args.seed,
            "assistant_only_loss": True,
            length_parameter: args.max_seq_len,
        }
        train_args = SFTConfig(**train_config_values)
        trainer = SFTTrainer(
            model=model,
            processing_class=tokenizer,
            train_dataset=dataset,
            args=train_args,
        )
        trainer.train()
        model.save_pretrained(str(output_dir))
        tokenizer.save_pretrained(str(output_dir))
        trainer.save_state()

        state = trainer.state
        trainer_state = {
            "global_step": state.global_step,
            "epoch": state.epoch,
            "best_metric": state.best_metric,
            "best_model_checkpoint": state.best_model_checkpoint,
        }
        manifest = mark_completed(
            manifest,
            output_dir=output_dir,
            manifest_path=manifest_path,
            trainer_state=trainer_state,
            training_history=list(state.log_history),
        )
        write_manifest_atomic(manifest_path, manifest)
        print(f"LoRA adapter saved to {output_dir}")
        print(f"Training provenance saved to {manifest_path}")
    except KeyboardInterrupt as exc:
        write_manifest_atomic(manifest_path, mark_failed(manifest, exc, interrupted=True))
        raise
    except BaseException as exc:
        write_manifest_atomic(manifest_path, mark_failed(manifest, exc))
        raise


if __name__ == "__main__":
    main()
