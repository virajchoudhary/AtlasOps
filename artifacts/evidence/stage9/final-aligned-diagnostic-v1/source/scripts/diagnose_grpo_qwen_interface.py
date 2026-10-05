"""One frozen-sampling inference-only diagnostic; no optimizer or rewards."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from training.grpo_qwen_interface import (
    DIAGNOSTIC_SAMPLES, INTERFACE, parse_single_tool_call, render_observed_prompt,
)


def validate_output_isolation(output: Path, parent: Path, cache: Path) -> None:
    from training.sft_provenance import has_redirecting_path_component

    repo = Path(__file__).resolve().parents[1]
    if output.exists() or not output.is_absolute() or has_redirecting_path_component(output):
        raise ValueError("Diagnostic output must be fresh, absolute and local")
    for protected in (parent, cache, repo):
        target, source = output.resolve(), protected.resolve()
        if target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError("Diagnostic output overlaps a protected artifact/source tree")


def diagnose(parent: Path, inventory: Path, inventory_sha: str, output: Path) -> dict:
    from agents.coordinator import _check_tool_policy
    from training.grpo_controlled import (
        CapacityEnvironment, EpisodeLedger, HYPERPARAMETERS, MODEL, REVISION,
        SCENARIO, validate_v17_parent,
    )
    from training.sft_free_t4_gate import _verify_model_inventory
    from training.sft_provenance import write_manifest_atomic

    before_parent = validate_v17_parent(parent)
    receipt = {"model_files_manifest": str(inventory),
               "model_files_manifest_sha256": inventory_sha}
    cache = _verify_model_inventory(receipt)
    validate_output_isolation(output, parent, Path(cache))
    output.mkdir()
    ledger = EpisodeLedger(output / "observed-state.jsonl")
    env = CapacityEnvironment(ledger)
    state = env.begin(SCENARIO)["state"]
    env.finish({}, "INFERENCE_ONLY_OBSERVATION")
    ledger.close()
    report = {
        "status": "STARTED", "interface": INTERFACE, "sample_count": DIAGNOSTIC_SAMPLES,
        "split": "train", "scenario": SCENARIO, "optimizer_steps": 0,
        "reward_used": False, "model_mutated": None, "mutation_verified": False,
        "sampling": HYPERPARAMETERS,
        "base": MODEL, "revision": REVISION, "state": state, "admissible_count": 0,
        "parent": before_parent,
    }
    write_manifest_atomic(output / "diagnostic.json", report)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1")
    model, before = None, None
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        torch.manual_seed(HYPERPARAMETERS["seed"])
        tokenizer = AutoTokenizer.from_pretrained(
            MODEL, revision=REVISION, cache_dir=cache, local_files_only=True, trust_remote_code=False,
        )
        base = AutoModelForCausalLM.from_pretrained(
            MODEL, revision=REVISION, cache_dir=cache, local_files_only=True, trust_remote_code=False,
            device_map={"": 0}, torch_dtype=torch.float16,
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True,
            ),
        )
        model = PeftModel.from_pretrained(base, str(parent), is_trainable=False, local_files_only=True)
        model.eval()

        def tensor_hashes():
            return {
                n: hashlib.sha256(p.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes()).hexdigest()
                for n, p in model.named_parameters() if "lora_" in n
            }

        before = tensor_hashes()
        prompt = render_observed_prompt(tokenizer, state)
        (output / "rendered-prompt.txt").write_text(prompt, encoding="utf-8")
        encoded = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to(model.device)
        report["prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
        report["prompt_tokens"] = encoded["input_ids"][0].cpu().tolist()
        report["tensor_hashes_before"] = before
        write_manifest_atomic(output / "diagnostic.json", report)
        with (output / "samples.jsonl").open("x", encoding="utf-8") as stream:
            for index in range(DIAGNOSTIC_SAMPLES):
                with torch.inference_mode():
                    generated = model.generate(
                        **encoded, do_sample=True,
                        temperature=HYPERPARAMETERS["temperature"],
                        top_p=HYPERPARAMETERS["top_p"], top_k=HYPERPARAMETERS["top_k"],
                        max_new_tokens=HYPERPARAMETERS["max_completion_length"],
                        pad_token_id=tokenizer.eos_token_id,
                    )
                ids = generated[0, encoded["input_ids"].shape[1]:].cpu().tolist()
                raw = tokenizer.decode(ids, skip_special_tokens=True)
                raw_with_specials = tokenizer.decode(ids, skip_special_tokens=False)
                action, error, admitted = None, None, False
                try:
                    eos = tokenizer.eos_token_id
                    content_ids = ids[:-1] if ids and ids[-1] == eos else ids
                    if any(token in tokenizer.all_special_ids for token in content_ids):
                        raise ValueError("Interior special tokens are not one canonical action")
                    action = parse_single_tool_call(raw)
                    reason = _check_tool_policy("remediation", action["tool"], action["arguments"], state)
                    target_ok = (
                        action["arguments"].get("namespace", "default") == "default"
                        and (action["tool"] != "kubectl_scale"
                             or action["arguments"]["deployment"] == "paymentservice")
                    )
                    admitted = reason is None and target_ok
                    if not admitted:
                        error = "POLICY_OR_TARGET_REJECTED"
                except (ValueError, TypeError) as exc:
                    error = type(exc).__name__
                record = {"sample": index, "raw_completion": raw,
                          "raw_with_special_tokens": raw_with_specials, "completion_ids": ids,
                          "parsed_action": action, "admissible": admitted, "error": error}
                stream.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                report["admissible_count"] += int(admitted)
                print(json.dumps({"sample": index, "admissible": admitted}), flush=True)
        after = tensor_hashes()
        if after != before or any(p.grad is not None for p in model.parameters()):
            raise ValueError("Inference diagnostic mutated model or gradients")
        if validate_v17_parent(parent) != before_parent:
            raise ValueError("Parent inventory changed")
        _verify_model_inventory(receipt)
        report["tensor_hashes_after"] = after
        report.update(model_mutated=False, mutation_verified=True)
        report["status"] = (
            "NONZERO_ADMISSIBLE_ACTIONS_OBSERVED" if report["admissible_count"]
            else "ZERO_ADMISSIBLE_ACTIONS_FINAL_NEGATIVE"
        )
    except BaseException as exc:
        report.update(status="FAILED_INFERENCE_DIAGNOSTIC", failure_type=type(exc).__name__)
        raise
    finally:
        # Preserve checks independently of generation success; do not overwrite the failure.
        integrity = {}
        for name, check in (
            ("parent_unchanged", lambda: validate_v17_parent(parent) == before_parent),
            ("base_inventory_unchanged", lambda: _verify_model_inventory(receipt) == cache),
        ):
            try:
                integrity[name] = check()
            except Exception as exc:
                integrity[name] = {"verified": False, "failure_type": type(exc).__name__}
        if model is not None and before is not None:
            try:
                after = tensor_hashes()
                report["tensor_hashes_after"] = after
                unchanged = after == before and all(p.grad is None for p in model.parameters())
                report.update(model_mutated=not unchanged, mutation_verified=True)
            except Exception as exc:
                integrity["model_check"] = {"verified": False, "failure_type": type(exc).__name__}
        report["final_integrity_checks"] = integrity
        if (
            any(value is not True for value in integrity.values())
            or report["model_mutated"] is not False
            or not report["mutation_verified"]
        ):
            report["status"] = "FAILED_INFERENCE_DIAGNOSTIC"
        write_manifest_atomic(output / "diagnostic.json", report)
    if report["status"] == "FAILED_INFERENCE_DIAGNOSTIC":
        raise ValueError("Inference diagnostic integrity is unverified")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--model-inventory", type=Path, required=True)
    parser.add_argument("--model-inventory-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(diagnose(args.parent, args.model_inventory, args.model_inventory_sha256, args.output),
                     sort_keys=True))


if __name__ == "__main__":
    main()
