"""Fresh-process offline reload for the controlled-only adapter, not evaluation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from training.grpo_reload_isolation import (
    KAGGLE_GPU_PROFILE, NAMESPACE_PROFILE, initialize_gpu_runtime, prepare_isolation,
    require_network_isolation,  # noqa: F401 - retained public alias
)


def reload_checkpoint(args):
    from training.grpo_controlled import (
        CLASSIFICATION, MODEL, PROFILE, REVISION, validate_v17_parent,
    )
    from training.sft_free_t4_gate import _verify_model_inventory
    from training.sft_provenance import (
        checkpoint_inventory, has_redirecting_path_component, write_manifest_atomic,
    )

    manifest_path = args.run / "controlled_grpo_manifest.json"
    if has_redirecting_path_component(args.run):
        raise ValueError("Redirected controlled run")
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != args.manifest_sha256:
        raise ValueError("Controlled run manifest changed before reload")
    manifest = json.loads(raw)
    if (
        manifest.get("profile") != PROFILE
        or manifest.get("classification") != CLASSIFICATION
        or manifest.get("status") != "TRAINED_RELOAD_PENDING"
        or manifest.get("changed_tensors", 0) <= 0
        or args.output.exists() or not args.output.is_absolute()
        or has_redirecting_path_component(args.output)
        or args.output.resolve().is_relative_to(args.run.resolve())
    ):
        raise ValueError("Reload needs a trained controlled run and fresh external output")
    receipt = manifest["receipt"]
    adapter = args.run / "adapter"
    if checkpoint_inventory(adapter, manifest_path) != manifest["checkpoint"]:
        raise ValueError("Controlled checkpoint inventory changed")
    validate_v17_parent(Path(receipt["parent_path"]))
    cache = _verify_model_inventory(receipt)
    report = {
        "profile": PROFILE, "status": "started", "manifest_sha256": args.manifest_sha256,
        "classification": CLASSIFICATION, "certification_status": "NOT_CERTIFIED",
        "inference_performed": False, "held_out_accessed": False,
        "network_isolation": None,
    }
    write_manifest_atomic(args.output, report)
    guard = None
    try:
        source_sha = __import__("subprocess").check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        if source_sha != receipt["source_sha"]:
            raise ValueError("Reload source differs from the training receipt")
        isolation, guard = prepare_isolation(
            args.reload_profile, receipt_path=args.offline_receipt,
            receipt_sha256=args.offline_receipt_sha256, source_sha=source_sha,
        )
        report["network_isolation"] = isolation
        write_manifest_atomic(args.output, report)
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        if guard and args.reload_profile == KAGGLE_GPU_PROFILE:
            isolation["runtime_discovery"] = initialize_gpu_runtime(isolation["context"]["runtime"], guard)
            isolation["syscall_guard_installed"] = True
            write_manifest_atomic(args.output, report)
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        if guard and args.reload_profile != KAGGLE_GPU_PROFILE:
            guard.require_clean()
            guard.install_syscall_guard()
            report["network_isolation"]["syscall_guard_installed"] = True

        common = {"revision": REVISION, "cache_dir": cache, "local_files_only": True,
                  "trust_remote_code": False}
        tokenizer = AutoTokenizer.from_pretrained(str(adapter), local_files_only=True)
        base = AutoModelForCausalLM.from_pretrained(
            MODEL, **common, device_map={"": 0}, torch_dtype=torch.float16,
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True,
            ),
        )
        model = PeftModel.from_pretrained(
            base, str(adapter), is_trainable=False, local_files_only=True,
        )
        model.eval()
        tensors = [p for n, p in model.named_parameters() if "lora_" in n]
        if not tensors or not all(torch.isfinite(p).all().item() for p in tensors):
            raise ValueError("Reloaded controlled adapter has missing/nonfinite tensors")
        if checkpoint_inventory(adapter, manifest_path) != manifest["checkpoint"]:
            raise ValueError("Controlled checkpoint changed during reload")
        validate_v17_parent(Path(receipt["parent_path"]))
        _verify_model_inventory(receipt)
        if guard:
            guard.require_clean()
        report.update(status="PASS", finite_lora_tensors=len(tensors),
                      model_class=type(model).__name__, tokenizer_class=type(tokenizer).__name__)
    except BaseException as exc:
        report.update(status="NOT_VERIFIED", failure_type=type(exc).__name__)
        if hasattr(exc, "isolation_evidence"):
            report["network_isolation"] = exc.isolation_evidence
        raise
    finally:
        if guard and report["network_isolation"]:
            report["network_isolation"]["forbidden_attempts"] = guard.attempts
            if guard.attempts:
                report["status"] = "NOT_VERIFIED"
        write_manifest_atomic(args.output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reload-profile", default=NAMESPACE_PROFILE,
                        choices=[NAMESPACE_PROFILE, "kaggle-verified-offline-v1", KAGGLE_GPU_PROFILE])
    parser.add_argument("--offline-receipt", type=Path)
    parser.add_argument("--offline-receipt-sha256")
    args = parser.parse_args()
    from training.sft_provenance import has_redirecting_path_component, write_manifest_atomic

    if (
        args.output.exists() or not args.output.is_absolute()
        or has_redirecting_path_component(args.output)
        or args.output.resolve().is_relative_to(args.run.resolve())
    ):
        raise ValueError("Reload report needs a fresh external path")
    try:
        reload_checkpoint(args)
    except BaseException as exc:
        if not args.output.exists():
            write_manifest_atomic(args.output, {
                "profile": "controlled-g9-capacity-v1", "status": "NOT_VERIFIED",
                "classification": "CONTROLLED_SYNTHETIC_TRAINING",
                "certification_status": "NOT_CERTIFIED",
                "manifest_sha256": args.manifest_sha256,
                "reload_profile": args.reload_profile, "failure_type": type(exc).__name__,
                "inference_performed": False, "held_out_accessed": False,
            })
        raise


if __name__ == "__main__":
    main()
