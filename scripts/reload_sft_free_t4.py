"""Fresh-process offline adapter reload, without evaluation or generation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--model-inventory", type=Path, required=True)
    parser.add_argument("--model-inventory-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--approval-sha256", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from bench.sft_eval import _load_checkpoint_manifest
    from training.sft_free_t4_gate import _verify_model_inventory
    from training.sft_provenance import (
        has_redirecting_path_component, source_provenance, write_manifest_atomic,
    )

    if (
        not args.output.is_absolute()
        or has_redirecting_path_component(args.output)
        or args.output.resolve().is_relative_to(args.checkpoint.resolve())
    ):
        raise ValueError("Reload evidence needs a fresh external absolute path")

    cache = _verify_model_inventory({
        "model_files_manifest": str(args.model_inventory.absolute()),
        "model_files_manifest_sha256": args.model_inventory_sha256,
    })
    manifest, before = _load_checkpoint_manifest(args.checkpoint)
    from training.sft_free_t4_gate import PROFILE, PLAN_SHA256, CORPUS_HASH

    admission = manifest.get("pilot_admission", {})
    approval = manifest.get("execution_approval", {})
    if (
        admission.get("profile") != PROFILE
        or admission.get("plan_sha256") != PLAN_SHA256
        or admission.get("corpus_sha256") != CORPUS_HASH
        or manifest.get("run_id") != args.run_id
        or approval.get("approval_record_sha256") != args.approval_sha256
    ):
        raise ValueError("Reload requires the exact approved free-T4 run identity")
    report = {
        "schema_version": "atlasops-sft-free-t4-reload-v1",
        "started_at_utc": datetime.now(UTC).isoformat(),
        "status": "started",
        "checkpoint_manifest_sha256_before": before,
        "run_id": args.run_id,
        "profile": PROFILE,
        "plan_sha256": PLAN_SHA256,
        "corpus_sha256": CORPUS_HASH,
        "approval_record_sha256": args.approval_sha256,
        "source": source_provenance(),
        "network_policy": "HF loaders offline; caller must additionally isolate process networking",
        "inference_performed": False,
        "held_out_outcomes_accessed": False,
        "empirical_claim": False,
    }
    write_manifest_atomic(args.output, report)
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        common = {"cache_dir": cache, "trust_remote_code": False, "local_files_only": True}
        tokenizer = AutoTokenizer.from_pretrained(
            manifest["tokenizer"]["id"],
            revision=manifest["tokenizer"]["resolved_revision"], **common,
        )
        base = AutoModelForCausalLM.from_pretrained(
            manifest["base_model"]["id"],
            revision=manifest["base_model"]["resolved_revision"],
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True,
            ),
            device_map={"": 0}, **common,
        )
        model = PeftModel.from_pretrained(
            base, str(args.checkpoint), local_files_only=True, is_trainable=False,
        )
        model.eval()
        _, after = _load_checkpoint_manifest(args.checkpoint)
        _verify_model_inventory({
            "model_files_manifest": str(args.model_inventory.absolute()),
            "model_files_manifest_sha256": args.model_inventory_sha256,
        })
        if before != after:
            raise ValueError("Checkpoint manifest changed during reload")
        tensors = [p for name, p in model.named_parameters() if "lora_" in name]
        if not tensors or not all(torch.isfinite(p).all().item() for p in tensors):
            raise ValueError("Reloaded adapter tensors are absent or nonfinite")
        report.update(
            status="PASS", checkpoint_manifest_sha256_after=after,
            adapter_tensor_count=len(tensors), tokenizer_class=type(tokenizer).__name__,
            model_class=type(model).__name__, gpu_name=torch.cuda.get_device_name(0),
            gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
        )
    except BaseException as exc:
        report.update(status="FAILED", failure_type=type(exc).__name__)
        raise
    finally:
        report["completed_at_utc"] = datetime.now(UTC).isoformat()
        write_manifest_atomic(args.output, report)
        print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
