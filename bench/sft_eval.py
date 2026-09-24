"""Checkpoint-backed SFT evaluation for AtlasOps Gate G8.

Mock mode remains available for unit compatibility and is always labelled
non-empirical. Empirical mode validates a completed checkpoint manifest,
generates predictions with the adapter, and scores benchmark truth only after
inference. This diagnosis-only evaluator does not fabricate environment results.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from bench.runner import compute_summary
from bench.zero_shot_baseline import compute_diagnostic_f1
from config.scenario_catalog import SCENARIO_CATALOG, ScenarioMetadata
from config.splits import LEADERBOARD_SEED, TEST_SEED, TRAIN_SPLIT, VAL_SEED, get_split

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sft_eval")

RESULTS_DIR = Path("bench/results")
EVIDENCE_DIR = Path("artifacts/evidence/stage8")
MODES = frozenset({"mock", "empirical"})
REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_WEIGHT_NAMES = frozenset({"adapter_model.safetensors", "adapter_model.bin"})
SPLIT_SEEDS = {
    "val": VAL_SEED,
    "test": TEST_SEED,
    "leaderboard": LEADERBOARD_SEED,
}

InferenceCallable = Callable[
    [list[dict[str, str]], str, Path, dict[str, Any]],
    Awaitable[str],
]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _source_provenance() -> dict[str, Any]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Unable to capture evaluator source provenance") from exc
    return {"git_sha": sha, "git_dirty": dirty}


def _resolve_mode(mode: str | None, mock: bool | None) -> Literal["mock", "empirical"]:
    if mode is None and mock is None:
        raise ValueError("Evaluation mode is required; choose 'mock' or 'empirical'")
    if mode is not None and mode not in MODES:
        raise ValueError(f"Unknown evaluation mode: {mode!r}")
    compatibility_mode = None if mock is None else ("mock" if mock else "empirical")
    if mode and compatibility_mode and mode != compatibility_mode:
        raise ValueError("Conflicting mode and mock arguments")
    return (mode or compatibility_mode)  # type: ignore[return-value]


def _public_input(meta: ScenarioMetadata) -> dict[str, Any]:
    return {
        "alert": {
            "labels": {
                "alertname": meta.expected_alert,
                "namespace": "default",
            },
            "status": "firing",
        }
    }


def _messages(public_input: dict[str, Any]) -> list[dict[str, str]]:
    schema = {
        "severity": "P0|P1|P2|P3",
        "affected_services": ["service-name"],
        "root_cause": "concise evidence-based diagnosis",
        "confidence": 0.0,
    }
    return [
        {
            "role": "system",
            "content": (
                "Diagnose the AtlasOps alert using only the supplied observation. "
                "Do not claim environment resolution. Return one JSON object matching: "
                f"{json.dumps(schema, sort_keys=True)}"
            ),
        },
        {"role": "user", "content": json.dumps(public_input, sort_keys=True)},
    ]


def _parse_prediction(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1)
        text = re.sub(r"\s*```$", "", text, count=1)
    prediction = json.loads(text)
    if not isinstance(prediction, dict):
        raise TypeError("SFT prediction must be a JSON object")
    root_cause = prediction.get("root_cause")
    if not isinstance(root_cause, str) or not root_cause.strip():
        raise ValueError("SFT prediction requires a non-empty root_cause")
    return prediction


def _load_checkpoint_manifest(checkpoint: Path) -> tuple[dict[str, Any], str]:
    manifest_path = checkpoint / "sft_run_manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Checkpoint provenance manifest must not be a symlink")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Checkpoint provenance manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Checkpoint provenance schema_version must be 1")
    if manifest.get("status") != "completed":
        raise ValueError("Checkpoint provenance status must be completed")
    checkpoint_record = manifest.get("checkpoint")
    if not isinstance(checkpoint_record, dict) or not checkpoint_record.get("files"):
        raise ValueError("Checkpoint provenance lacks a file inventory")
    for identity in ("base_model", "tokenizer"):
        record = manifest.get(identity)
        if (
            not isinstance(record, dict)
            or not record.get("id")
            or not record.get("resolved_revision")
        ):
            raise ValueError(f"Checkpoint provenance lacks resolved {identity} revision")
    dataset = manifest.get("dataset")
    if not isinstance(dataset, dict) or dataset.get("split") != "train":
        raise ValueError("Checkpoint provenance must identify the frozen Train split")
    expected_train_hash = _canonical_json_sha256(list(TRAIN_SPLIT))
    if dataset.get("split_sha256") != expected_train_hash:
        raise ValueError("Checkpoint provenance Train split differs from the frozen split")
    if dataset.get("split_scenarios") != list(TRAIN_SPLIT):
        raise ValueError("Checkpoint provenance Train scenario identities do not match")
    source = manifest.get("source")
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("git_sha"), str)
        or len(source["git_sha"]) != 40
        or not isinstance(source.get("git_dirty"), bool)
    ):
        raise ValueError("Checkpoint provenance lacks source identity")
    if source["git_dirty"]:
        raise ValueError("Empirical G8 requires a clean immutable SFT training source")

    expected_by_path: dict[str, dict[str, Any]] = {}
    for record in checkpoint_record["files"]:
        if not isinstance(record, dict):
            raise TypeError("Checkpoint provenance contains an invalid file record")
        relative = record.get("path")
        expected_hash = record.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise TypeError("Checkpoint provenance contains an invalid file record")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"Checkpoint path escapes its directory: {relative}")
        normalized = relative_path.as_posix()
        if normalized in expected_by_path:
            raise ValueError(f"Duplicate checkpoint inventory path: {relative}")
        expected_by_path[normalized] = record

    current_files = []
    actual_paths: set[str] = set()
    for path in checkpoint.rglob("*"):
        if path == manifest_path:
            continue
        if path.is_symlink():
            raise ValueError(f"Checkpoint contains a symlink: {path}")
        if path.is_dir():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(checkpoint):
            raise ValueError(f"Checkpoint path escapes its directory: {path}")
        relative = path.relative_to(checkpoint).as_posix()
        declared = expected_by_path.get(relative)
        if declared is None:
            raise ValueError(f"Checkpoint file missing from provenance inventory: {relative}")
        actual_hash = _file_sha256(path)
        if actual_hash != declared["sha256"]:
            raise ValueError(f"Checkpoint hash mismatch: {relative}")
        declared_size = declared.get("size_bytes")
        if declared_size is not None and declared_size != path.stat().st_size:
            raise ValueError(f"Checkpoint size mismatch: {relative}")
        actual_paths.add(relative)
        current_files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": actual_hash,
            }
        )
    if actual_paths != set(expected_by_path):
        missing = sorted(set(expected_by_path) - actual_paths)
        raise FileNotFoundError(f"Checkpoint files missing from disk: {missing}")
    current_files.sort(key=lambda item: item["path"])
    tree_hash = _canonical_json_sha256(current_files)
    if tree_hash != checkpoint_record.get("tree_sha256"):
        raise ValueError("Checkpoint tree hash does not match provenance")
    if not (checkpoint / "adapter_config.json").is_file():
        raise ValueError("SFT checkpoint is missing adapter_config.json")
    if not any((checkpoint / name).is_file() for name in ADAPTER_WEIGHT_NAMES):
        raise ValueError("SFT checkpoint is missing adapter model weights")
    return manifest, _file_sha256(manifest_path)


class LocalSFTInference:
    """Lazy local base-model plus PEFT-adapter inference."""

    def __init__(self, manifest: dict[str, Any]):
        self.manifest = manifest
        self._model: Any = None
        self._tokenizer: Any = None

    def _load(self, checkpoint: Path) -> None:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        base = self.manifest["base_model"]
        tokenizer_record = self.manifest["tokenizer"]
        self._tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_record["id"],
            revision=tokenizer_record["resolved_revision"],
            trust_remote_code=True,
        )
        base_model = AutoModelForCausalLM.from_pretrained(
            base["id"],
            revision=base["resolved_revision"],
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
        )
        self._model = PeftModel.from_pretrained(base_model, str(checkpoint))
        self._model.eval()

    async def __call__(
        self,
        messages: list[dict[str, str]],
        model_name: str,
        checkpoint: Path,
        generation_config: dict[str, Any],
    ) -> str:
        return await asyncio.to_thread(
            self._generate,
            messages,
            checkpoint,
            generation_config,
        )

    def _generate(
        self,
        messages: list[dict[str, str]],
        checkpoint: Path,
        generation_config: dict[str, Any],
    ) -> str:
        import torch

        if self._model is None or self._tokenizer is None:
            self._load(checkpoint)
        torch.manual_seed(generation_config["seed"])
        prompt = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
        with torch.no_grad():
            output = self._model.generate(
                **inputs,
                max_new_tokens=generation_config["max_new_tokens"],
                do_sample=generation_config["temperature"] > 0,
                temperature=max(generation_config["temperature"], 1e-6),
                top_p=generation_config["top_p"],
            )
        generated = output[0][inputs["input_ids"].shape[1] :]
        return self._tokenizer.decode(generated, skip_special_tokens=True)


def evaluate_sft_mock_episode(scenario_id: str, model_name: str) -> dict[str, Any]:
    """Return deterministic NON_EMPIRICAL telemetry for unit compatibility."""
    meta = SCENARIO_CATALOG.get(scenario_id)
    tier = meta.tier if meta else scenario_id.split("/", 1)[0]
    expected_root = meta.expected_root_cause if meta else "pod failure"
    target_svc = meta.target_services[0] if meta and meta.target_services else "frontend"
    predicted = f"Root cause identified: {expected_root} causing errors on {target_svc}."
    diagnostic = compute_diagnostic_f1(predicted, expected_root)
    is_resolved = sum(ord(char) for char in scenario_id) % 3 != 0
    ttr = 32.0 if is_resolved else 55.0
    reward = {
        "total": round(
            (0.35 if is_resolved else 0.0)
            + max(0.0, min(0.20, (60.0 - ttr) / 60.0 * 0.20))
            + 0.20 * diagnostic["f1"]
            + 0.15
            + 0.10,
            3,
        ),
        "penalties": {
            "unsafe_shortcut": 0.0,
            "false_resolution": 0.0,
            "hallucinated_evidence": 0.0,
            "command_spam": 0.0,
        },
        "penalty_total": 0.0,
    }
    return {
        "scenario_id": scenario_id,
        "tier": tier,
        "status": "ok",
        "evaluation_mode": "mock",
        "non_empirical": True,
        "resolved": is_resolved,
        "time_to_resolve_s": ttr,
        "total_turns": 3 if is_resolved else 4,
        "judge": {"overall": 0.88 if is_resolved else 0.60},
        "reward_contract": reward,
        "diagnostic_f1": diagnostic["f1"],
        "diagnostic_precision": diagnostic["precision"],
        "diagnostic_recall": diagnostic["recall"],
        "format_compliant": True,
        "tool_arguments_valid": True,
    }


async def evaluate_sft_split(
    split_name: str,
    model_name: str = "qwen2.5:7b-instruct-sft",
    *,
    mode: str | None = None,
    mock: bool | None = None,
    checkpoint: Path | None = None,
    output_dir: Path | None = None,
    inference_fn: InferenceCallable | None = None,
    temperature: float = 0.0,
    top_p: float = 1.0,
    max_new_tokens: int = 512,
) -> dict[str, Any]:
    """Evaluate an SFT checkpoint over one frozen benchmark partition."""
    selected_mode = _resolve_mode(mode, mock)
    scenario_ids = get_split(split_name)
    if selected_mode == "empirical" and output_dir is None:
        raise ValueError("Empirical mode requires an explicit unique output_dir")
    manifest = None
    manifest_hash = None
    configured_backend = inference_fn is None
    if selected_mode == "empirical":
        if checkpoint is None:
            raise ValueError("Empirical mode requires an explicit checkpoint")
        checkpoint = checkpoint.resolve()
        manifest, manifest_hash = _load_checkpoint_manifest(checkpoint)
        if configured_backend:
            inference_fn = LocalSFTInference(manifest)

    generation_config = {
        "temperature": temperature,
        "top_p": top_p,
        "max_new_tokens": max_new_tokens,
        "seed": SPLIT_SEEDS[split_name],
    }
    results = []
    raw_file_name = f"sft_{split_name}_episodes.jsonl"
    out_dir = output_dir or (
        RESULTS_DIR / "non_empirical" / "sft" / split_name
        / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    episodes_file = out_dir / raw_file_name

    with episodes_file.open("x", encoding="utf-8", newline="\n") as stream:
        for scenario_id in scenario_ids:
            if selected_mode == "mock":
                episode = evaluate_sft_mock_episode(scenario_id, model_name)
            else:
                meta = SCENARIO_CATALOG[scenario_id]
                public_input = _public_input(meta)
                request_messages = _messages(public_input)
                raw_text = ""
                try:
                    raw_text = await inference_fn(
                        request_messages,
                        model_name,
                        checkpoint,
                        generation_config,
                    )
                    prediction = _parse_prediction(raw_text)
                    diagnostic = compute_diagnostic_f1(
                        str(prediction["root_cause"]),
                        meta.expected_root_cause,
                    )
                    status = "ok"
                    error = None
                except Exception as exc:  # noqa: BLE001
                    prediction = None
                    diagnostic = {"precision": 0.0, "recall": 0.0, "f1": 0.0}
                    status = "error"
                    error = f"{type(exc).__name__}: {exc}"
                episode = {
                    "scenario_id": scenario_id,
                    "tier": meta.tier,
                    "status": status,
                    "evaluation_mode": "empirical",
                    "non_empirical": not configured_backend,
                    "empirical_claim_allowed": configured_backend and status == "ok",
                    "request_messages": request_messages,
                    "raw_model_response": raw_text,
                    "prediction": prediction,
                    "scoring_reference": {"expected_root_cause": meta.expected_root_cause},
                    "diagnostic_f1": diagnostic["f1"],
                    "diagnostic_precision": diagnostic["precision"],
                    "diagnostic_recall": diagnostic["recall"],
                    "format_compliant": status == "ok",
                    "tool_arguments_valid": None,
                    "environment_resolution_evaluated": False,
                    "env_resolved": None,
                    "resolved": None,
                    "time_to_resolve_s": None,
                }
                if error is not None:
                    episode["error"] = error
            results.append(episode)
            stream.write(json.dumps(episode, sort_keys=True) + "\n")

    tag = f"sft-{split_name}-{model_name.replace(':', '-').replace('/', '-')}"
    summary = compute_summary(results, tag=tag, model=model_name)
    valid = [row for row in results if row.get("status") == "ok"]
    summary.update(
        {
            "split": split_name,
            "variant": "SFT Model",
            "evaluation_mode": selected_mode,
            "mock_eval": selected_mode == "mock",
            "non_empirical": selected_mode == "mock" or not configured_backend,
            "empirical_inference_executed": (
                selected_mode == "empirical"
                and len(valid) == len(results)
                and bool(results)
            ),
            "empirical_claim_allowed": (
                selected_mode == "empirical"
                and configured_backend
                and len(valid) == len(results)
                and bool(results)
            ),
            "failed_scenarios": len(results) - len(valid),
            "avg_diagnostic_f1": round(
                sum(row.get("diagnostic_f1", 0.0) for row in results)
                / max(len(results), 1),
                4,
            ),
            "format_compliance_rate": round(
                sum(1 for row in valid if row.get("format_compliant"))
                / max(len(valid), 1),
                4,
            ),
            "tool_arguments_valid_rate": (
                round(
                    sum(1 for row in valid if row.get("tool_arguments_valid"))
                    / max(len(valid), 1),
                    4,
                )
                if selected_mode == "mock"
                else None
            ),
            "checkpoint_path": str(checkpoint) if checkpoint else None,
            "checkpoint_manifest_sha256": manifest_hash,
            "checkpoint_tree_sha256": (
                manifest["checkpoint"]["tree_sha256"] if manifest else None
            ),
            "base_model": manifest["base_model"] if manifest else None,
            "training_source": manifest.get("source") if manifest else None,
            "evaluator_source": _source_provenance(),
            "generation_config": generation_config,
            "truth_withheld_during_inference": True,
            "raw_predictions_path": str(episodes_file.resolve()),
            "raw_predictions_sha256": _file_sha256(episodes_file),
        }
    )
    if selected_mode == "empirical":
        diagnostic_by_tier: dict[str, dict[str, float | int]] = {}
        for tier in sorted({row["tier"] for row in results}):
            tier_rows = [row for row in results if row["tier"] == tier]
            diagnostic_by_tier[tier] = {
                "count": len(tier_rows),
                "avg_diagnostic_f1": round(
                    sum(row["diagnostic_f1"] for row in tier_rows)
                    / max(len(tier_rows), 1),
                    4,
                ),
            }
        summary.update(
            {
                "environment_resolution_evaluated": False,
                "resolution_rate": None,
                "avg_reward": None,
                "avg_reward_contract": None,
                "avg_penalty": None,
                "avg_turns": None,
                "avg_time_to_resolve_s": None,
                "cascade_resolution_rate": None,
                "named_replay_resolution_rate": None,
                "unsafe_action_count": None,
                "false_resolution_count": None,
                "hallucinated_evidence_count": None,
                "per_tier": diagnostic_by_tier,
            }
        )

    summary_file = out_dir / f"sft_{split_name}_summary.json"
    summary_file.write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps SFT checkpoint evaluator")
    parser.add_argument("--split", default="val", choices=["val", "test", "leaderboard"])
    parser.add_argument("--model", default="qwen2.5:7b-instruct-sft")
    parser.add_argument("--checkpoint", type=Path)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true", help="Run NON_EMPIRICAL test mode")
    modes.add_argument("--empirical", action="store_true", help="Load and evaluate the checkpoint")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    asyncio.run(
        evaluate_sft_split(
            args.split,
            model_name=args.model,
            mode="empirical" if args.empirical else "mock",
            checkpoint=args.checkpoint,
            output_dir=args.output,
        )
    )


if __name__ == "__main__":
    main()
