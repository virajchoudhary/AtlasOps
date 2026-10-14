"""Reproducible QLoRA SFT entrypoint for AtlasOps.

The runner writes durable provenance before optional ML dependencies or model
weights are loaded, updates it through the training lifecycle, and hashes the
resulting adapter/checkpoint files on successful completion.
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.splits import TRAIN_SEED
from training.sft_provenance import (
    SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256,
    SCENARIO_DERIVED_SYNTHETIC_TOTAL_SCENARIOS,
    canonical_bytes_sha256,
    canonical_json_sha256,
    create_run_manifest,
    file_sha256,
    has_redirecting_path_component,
    mark_completed,
    mark_failed,
    mark_running,
    resolve_tokenizer_revision,
    snapshot_training_corpus,
    validate_hf_reference,
    validate_resolved_hf_commit,
    write_manifest_atomic,
)
from training.sft_rendering import (
    TEMPLATE_PATH,
    load_role_prompt,
    normalize_tool_arguments,
    prepare_example_for_training,
    validate_tool_call_role_acl,
)

TARGET_MODULES = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]

# Known synthetic assistant targets after tool-argument normalization.
_SYNTHETIC_ASSISTANT_TARGETS_SHA256 = (
    "c6e7449f83fe55e20716db3e16743f47aa2273bea0b9261d69a0a398d3d1b311"
)
_SYNTHETIC_ROLE_TARGETS_SHA256 = {
    "triage": "4e22bb67807887f11e186ded1ebe9bf0de31d5379a8ab20f848bdbc71ac17900",
    "diagnosis": "3679c1c00d353869de585314c6555fa773fdc1384624e914720a1e115c6bcd63",
    "remediation": "e2c0ddd6678a7e41946c6d431c3210dc618a3f8ff09f04cf1569dd2a7d7c99a6",
    "comms": "74e69f96d81adb5c5d97973b50f02d251c5019b841c76d378ccbcd376cd28b3e",
}
_FORBIDDEN_DIAGNOSIS_LABEL_KEYS = frozenset(
    {
        "benchmark", "benchmark_labels", "benchmark_truth", "case_id", "corpus_version",
        "data_origin", "diagnosis_outcome", "expected_alert", "expected_checks",
        "expected_predicates", "expected_remediation", "expected_root_cause",
        "ground_truth", "judge", "judge_score", "known_good_runbook", "outcome",
        "reward", "reward_contract", "scenario_id", "split", "synthetic_outcome",
        "tier",
    }
)


def _assistant_targets_sha256(rows: tuple[dict[str, Any], ...]) -> str:
    targets = []
    for row in rows:
        assistant_turns = []
        for message in row["messages"]:
            if message.get("role") != "assistant":
                continue
            calls = []
            for call in message.get("tool_calls") or []:
                function = call["function"]
                calls.append(
                    {
                        "name": function["name"],
                        "arguments": normalize_tool_arguments(function["arguments"]),
                    }
                )
            assistant_turns.append(
                {"content": message.get("content") or "", "tool_calls": calls}
            )
        targets.append({"role": row["role"], "assistant_turns": assistant_turns})
    return canonical_json_sha256(sorted(targets, key=canonical_json_sha256))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        required=True,
        help="Hugging Face repository id; local paths are unsupported",
    )
    parser.add_argument(
        "--model-revision",
        required=True,
        help="Full 40-character immutable Hugging Face commit SHA",
    )
    parser.add_argument(
        "--tokenizer",
        help=(
            "Hugging Face repository id; defaults to --model "
            "(local paths are unsupported)"
        ),
    )
    parser.add_argument(
        "--tokenizer-revision",
        help=(
            "Full 40-character immutable Hugging Face commit SHA; defaults to "
            "--model-revision "
            "only when tokenizer and model use the same repository"
        ),
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
    parser.add_argument(
        "--pilot-profile", choices=["frozen-v4", "free-t4-v1"], default="frozen-v4",
        help="Select the separately approved pilot; the original frozen plan remains unchanged",
    )
    parser.add_argument(
        "--preflight-only", action="store_true",
        help="Verify the pinned pilot plan without creating output or importing model loaders",
    )
    parser.add_argument(
        "--execution-approval",
        help="Future independently reviewed hash-pinned execution record; preparation has none",
    )
    parser.add_argument(
        "--execution-approval-sha256",
        help="Independent operator digest for the free-T4 execution record",
    )
    return parser.parse_args()


def _hyperparameters(args: argparse.Namespace) -> dict[str, Any]:
    free_t4 = getattr(args, "pilot_profile", "frozen-v4") == "free-t4-v1"
    values = {
        "quantization": {
            "load_in_4bit": True,
            "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": "float16" if free_t4 else "bfloat16",
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
        "bf16": not free_t4,
        "assistant_only_loss": True,
        "chat_template_path": str(TEMPLATE_PATH),
        "chat_template_sha256": file_sha256(TEMPLATE_PATH),
    }
    if free_t4:
        values["fp16"] = True
    return values


def _finite_confidence(value: float) -> bool:
    try:
        return math.isfinite(value) and 0 <= value <= 1
    except OverflowError:
        return False


def _contains_forbidden_diagnosis_labels(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = (
                "_".join(key.strip().casefold().replace("-", " ").split())
                if isinstance(key, str)
                else ""
            )
            if (
                normalized in _FORBIDDEN_DIAGNOSIS_LABEL_KEYS
                or normalized.startswith("expected_")
                or normalized.endswith("_outcome")
                or _contains_forbidden_diagnosis_labels(item)
            ):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_diagnosis_labels(item) for item in value)
    return False


def validate_diagnosis_target_contracts(
    examples: Iterable[dict[str, Any]],
) -> None:
    """Reject Diagnosis targets and Remediation handoffs that contradict the prompt.

    The validator checks only. Frozen corpus rows are never normalized or
    rewritten; a mismatch requires a new reviewed corpus version.
    """
    prompt = load_role_prompt("diagnosis")
    match = re.search(r"```json\s*(.*?)\s*```", prompt, re.DOTALL)
    if match is None:
        raise ValueError(
            "Canonical Diagnosis prompt must contain a JSON output example"
        )
    prompt_example = json.loads(match.group(1))
    if not isinstance(prompt_example, dict):
        raise TypeError("Canonical Diagnosis prompt example must be a JSON object")

    category_example = prompt_example.get("category")
    categories = (
        set(category_example.split("|"))
        if isinstance(category_example, str) and "|" in category_example
        else None
    )
    prompt_fields = set(prompt_example)
    for example in examples:
        role = example.get("role")
        if role not in {"diagnosis", "remediation"}:
            continue
        messages = example.get("messages")
        if role == "diagnosis":
            label = "Diagnosis SFT target"
            if (
                not isinstance(messages, list)
                or not messages
                or not isinstance(messages[-1], dict)
                or messages[-1].get("role") != "assistant"
                or messages[-1].get("tool_calls")
            ):
                raise ValueError(
                    "Diagnosis SFT target must end with a final assistant conclusion"
                )
            content = messages[-1].get("content")
            try:
                target = json.loads(content) if isinstance(content, str) else None
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Diagnosis SFT target must be one JSON object matching the "
                    "canonical runtime prompt"
                ) from exc
        else:
            label = "Remediation SFT diagnosis handoff"
            if (
                not isinstance(messages, list)
                or len(messages) < 2
                or not isinstance(messages[1], dict)
                or messages[1].get("role") != "user"
            ):
                raise ValueError(
                    "Remediation SFT diagnosis handoff must be in the first user context"
                )
            content = messages[1].get("content")
            try:
                context = json.loads(content) if isinstance(content, str) else None
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Remediation SFT diagnosis handoff context must be a JSON object"
                ) from exc
            if not isinstance(context, dict):
                raise TypeError(
                    "Remediation SFT diagnosis handoff context must be a JSON object"
                )
            target = context.get("diagnosis")
            if not isinstance(target, dict):
                raise TypeError(
                    "Remediation SFT diagnosis handoff must be a canonical Diagnosis object"
                )
        if not isinstance(target, dict):
            raise TypeError(
                f"{label} must be one JSON object matching the canonical runtime prompt"
            )

        errors: list[str] = []
        for field in sorted(prompt_fields - set(target)):
            errors.append(f"missing {field}")
        for field in sorted(set(target) - prompt_fields):
            errors.append(f"unexpected {field}")
        for field, expected in prompt_example.items():
            if field not in target:
                continue
            actual = target[field]
            if field == "confidence":
                if (
                    isinstance(actual, bool)
                    or not isinstance(actual, int | float)
                    or not _finite_confidence(actual)
                ):
                    errors.append("confidence must be finite and in [0, 1]")
            elif field == "category":
                if not isinstance(actual, str) or (
                    categories is not None and actual not in categories
                ):
                    errors.append("category is outside the canonical prompt choices")
            elif isinstance(expected, str):
                if not isinstance(actual, str) or not actual.strip():
                    errors.append(
                        f"{field} must be non-empty text "
                        f"(got {type(actual).__name__})"
                    )
                elif field == "next_agent" and actual != expected:
                    errors.append("next_agent differs from the canonical prompt handoff")
            elif isinstance(expected, list):
                if not isinstance(actual, list):
                    errors.append(f"{field} must be an array")
                elif expected and isinstance(expected[0], dict) and any(
                    not isinstance(item, dict) for item in actual
                ):
                    errors.append(f"{field} entries must be objects")
            elif not isinstance(actual, type(expected)):
                errors.append(
                    f"{field} must be {type(expected).__name__}, "
                    f"got {type(actual).__name__}"
                )
        if _contains_forbidden_diagnosis_labels(target):
            errors.append("outcome or training-label metadata is forbidden")

        if errors:
            scenario_id = example.get("scenario_id", "<unknown>")
            raise ValueError(
                f"{label} is incompatible with the canonical runtime prompt "
                f"for {scenario_id!r}: {'; '.join(errors)}"
            )


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output)
    corpus_path = Path(args.data)
    tokenizer_id = args.tokenizer or args.model
    if (
        args.tokenizer
        and args.tokenizer != args.model
        and not args.tokenizer_revision
    ):
        raise ValueError(
            "An explicit --tokenizer-revision is required when --tokenizer selects "
            "a different Hugging Face repository"
        )
    tokenizer_revision = args.tokenizer_revision or args.model_revision
    validate_hf_reference(
        args.model,
        args.model_revision,
        label="base model",
    )
    validate_hf_reference(
        tokenizer_id,
        tokenizer_revision,
        label="tokenizer",
    )
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
    if has_redirecting_path_component(output_dir):
        raise ValueError(
            "SFT output path must not contain symlink, reparse, or hard-link redirects"
        )
    if output_dir.exists():
        raise FileExistsError(output_dir)

    corpus_snapshot = snapshot_training_corpus(corpus_path)
    training_source_rows = tuple(
        row
        for row in corpus_snapshot.rows
        if args.role == "all" or row.get("role") == args.role
    )
    if not training_source_rows:
        raise ValueError(f"No training examples remain for role={args.role}")
    known_role_targets = any(
        len(role_rows) == SCENARIO_DERIVED_SYNTHETIC_TOTAL_SCENARIOS
        and _assistant_targets_sha256(role_rows) == digest
        for role, digest in _SYNTHETIC_ROLE_TARGETS_SHA256.items()
        if (
            role_rows := tuple(
                row for row in training_source_rows if row.get("role") == role
            )
        )
    )
    if (
        canonical_bytes_sha256(corpus_snapshot.raw_bytes)
        == SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256
        or _assistant_targets_sha256(corpus_snapshot.rows)
        == _SYNTHETIC_ASSISTANT_TARGETS_SHA256
        or known_role_targets
    ):
        raise ValueError(
            "SFT training admission rejected the canonical Train corpus for every role"
        )
    validate_tool_call_role_acl(training_source_rows)
    validate_diagnosis_target_contracts(training_source_rows)

    if args.pilot_profile == "free-t4-v1":
        from training.sft_free_t4_gate import require_execution_authority, validate_preparation
    else:
        from training.sft_pilot_gate import require_execution_authority, validate_preparation

    admission = validate_preparation(
        corpus_snapshot,
        model=args.model,
        model_revision=args.model_revision,
        tokenizer=tokenizer_id,
        tokenizer_revision=tokenizer_revision,
        role=args.role,
        hyperparameters=_hyperparameters(args),
    )
    if args.preflight_only:
        import json

        print(json.dumps(admission, sort_keys=True))
        return
    approval_options = {}
    if args.pilot_profile == "free-t4-v1":
        approval_options["approval_sha256"] = args.execution_approval_sha256
    elif args.execution_approval_sha256:
        raise ValueError("External approval digests are only supported by the free-T4 profile")
    execution = require_execution_authority(
        admission, Path(args.execution_approval) if args.execution_approval else None,
        output_dir=output_dir,
        **approval_options,
    )

    manifest = create_run_manifest(
        corpus_path=corpus_path,
        corpus_snapshot=corpus_snapshot,
        output_dir=output_dir,
        base_model=args.model,
        base_model_revision=args.model_revision,
        tokenizer=tokenizer_id,
        tokenizer_revision=tokenizer_revision,
        role=args.role,
        hyperparameters=_hyperparameters(args),
        seed=args.seed,
    )
    manifest["pilot_admission"] = admission
    manifest["execution_approval"] = execution
    if execution:
        manifest["run_id"] = execution["run_id"]
    loader_cache_options = {}
    if execution:
        loader_cache_options["cache_dir"] = execution["model_cache_dir"]
    output_dir.mkdir(parents=True, exist_ok=False)
    write_manifest_atomic(manifest_path, manifest)

    try:
        from datasets import Dataset

        training_rows = [
            prepare_example_for_training(row) for row in training_source_rows
        ]
        dataset = Dataset.from_list(training_rows)
        if len(dataset) == 0:
            raise ValueError(f"No training examples remain for role={args.role}")

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

        if args.pilot_profile == "free-t4-v1":
            import torch

            torch.cuda.reset_peak_memory_stats()

        sft_parameters = inspect.signature(SFTConfig).parameters
        if "assistant_only_loss" not in sft_parameters:
            raise RuntimeError(
                "Installed TRL does not support assistant_only_loss; refusing unsafe training"
            )
        length_parameter = (
            "max_length" if "max_length" in sft_parameters else "max_seq_length"
        )

        set_seed(args.seed)
        tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_id,
            revision=tokenizer_revision,
            trust_remote_code=False,
            local_files_only=True,
            **loader_cache_options,
        )
        tokenizer_init_kwargs = getattr(tokenizer, "init_kwargs", None)
        resolved_tokenizer_revision, resolved_tokenizer_revision_basis = (
            resolve_tokenizer_revision(
                tokenizer_revision,
                tokenizer_init_kwargs.get("_commit_hash")
                if isinstance(tokenizer_init_kwargs, dict)
                else None,
            )
        )
        manifest["tokenizer"]["resolved_revision"] = resolved_tokenizer_revision
        manifest["tokenizer"]["resolved_revision_basis"] = (
            resolved_tokenizer_revision_basis
        )
        write_manifest_atomic(manifest_path, manifest)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        tokenizer.chat_template = TEMPLATE_PATH.read_text(encoding="utf-8")

        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=_hyperparameters(args)["quantization"]["bnb_4bit_compute_dtype"],
            bnb_4bit_use_double_quant=True,
        )
        model_precision_options = {}
        if args.pilot_profile == "free-t4-v1":
            model_precision_options = {"torch_dtype": torch.float16, "attn_implementation": "sdpa"}
        model = AutoModelForCausalLM.from_pretrained(
            args.model,
            revision=args.model_revision,
            quantization_config=quantization,
            device_map="auto",
            trust_remote_code=False,
            local_files_only=True,
            **loader_cache_options,
            **model_precision_options,
        )
        if args.pilot_profile == "free-t4-v1":
            from training.sft_chunked_loss import install_supervised_logits
            from training.sft_t4_attention import install_t4_attention

            install_t4_attention(model)
            manifest["attention_implementation"] = {
                "name": "pinned-SDPA-explicit-GQA-repeat",
                "sha256": file_sha256(Path(__file__).with_name("sft_t4_attention.py")),
            }
            install_supervised_logits(model)
            manifest["loss_implementation"] = {
                "name": "supervised-logits-chunked-cross-entropy",
                "sha256": file_sha256(Path(__file__).with_name("sft_chunked_loss.py")),
                "purpose": "Bound temporary FP32 loss allocations without changing targets",
            }
        resolved_model_revision = validate_resolved_hf_commit(
            args.model_revision,
            getattr(
                getattr(model, "config", None),
                "_commit_hash",
                None,
            ),
            label="base model",
        )
        model = prepare_model_for_kbit_training(model)
        if args.pilot_profile == "free-t4-v1":
            for parameter in model.parameters():
                if not parameter.requires_grad and parameter.dtype == torch.float32:
                    parameter.data = parameter.data.to(torch.float16)
            manifest["attention_memory_policy"] = "Explicit FP16 SDPA; frozen nonquantized parameters FP16"
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
        if args.pilot_profile == "free-t4-v1":
            model.get_input_embeddings().to(dtype=torch.float16)
            model.get_output_embeddings().to(dtype=torch.float16)
            manifest["frozen_projection_dtype"] = "float16"
        model.print_trainable_parameters()

        manifest = mark_running(
            manifest,
            resolved_model_revision=resolved_model_revision,
            resolved_tokenizer_revision=resolved_tokenizer_revision,
            resolved_tokenizer_revision_basis=resolved_tokenizer_revision_basis,
        )
        write_manifest_atomic(manifest_path, manifest)

        train_config_values = {
            "output_dir": str(output_dir),
            "num_train_epochs": args.epochs,
            "learning_rate": args.lr,
            "per_device_train_batch_size": args.batch_size,
            "gradient_accumulation_steps": args.grad_accum,
            "bf16": _hyperparameters(args)["bf16"],
            "logging_steps": 10,
            "save_strategy": "epoch",
            "report_to": [],
            "optim": "paged_adamw_8bit",
            "seed": args.seed,
            "data_seed": args.seed,
            "assistant_only_loss": True,
            length_parameter: args.max_seq_len,
        }
        if args.pilot_profile == "free-t4-v1":
            train_config_values["fp16"] = True
            train_config_values["gradient_checkpointing"] = True
            train_config_values["gradient_checkpointing_kwargs"] = {"use_reentrant": False}
            manifest["activation_memory_policy"] = "Non-reentrant checkpointing without host-RAM offloading"
        train_args = SFTConfig(**train_config_values)
        trainer_class = SFTTrainer
        if args.pilot_profile == "free-t4-v1":
            from training.sft_supervised_trainer import SupervisedLogitsSFTTrainer

            trainer_class = SupervisedLogitsSFTTrainer
            manifest["trainer_implementation"] = {
                "name": "SFTTrainer-supervised-logit-metrics",
                "sha256": file_sha256(Path(__file__).with_name("sft_supervised_trainer.py")),
            }
        trainer = trainer_class(
            model=model,
            processing_class=tokenizer,
            train_dataset=dataset,
            args=train_args,
        )
        if args.pilot_profile == "free-t4-v1":
            from torch.nn.attention import SDPBackend, sdpa_kernel

            manifest["attention_kernel_policy"] = "FLASH_ATTENTION or EFFICIENT_ATTENTION; math fallback disabled"
            write_manifest_atomic(manifest_path, manifest)
            with sdpa_kernel([SDPBackend.FLASH_ATTENTION, SDPBackend.EFFICIENT_ATTENTION]):
                trainer.train()
        else:
            trainer.train()
        if args.pilot_profile == "free-t4-v1":
            manifest["gpu_memory"] = {
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
                "total_memory_bytes": torch.cuda.get_device_properties(0).total_memory,
            }
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
