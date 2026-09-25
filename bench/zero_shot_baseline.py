"""AtlasOps zero-shot base-model evaluation harness (Gate G6).

Mock mode exists for deterministic unit tests. Empirical mode performs real
OpenAI-compatible model inference over frozen evaluation inputs, preserves raw
predictions, and scores only after inference. It does not mutate or verify a
cluster, so environment-resolution metrics remain explicitly unevaluated.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx

from agents._http_retry import post_with_retry
from bench.runner import compute_summary, run_scenario
from config.scenario_catalog import SCENARIO_CATALOG, ScenarioMetadata
from config.splits import (
    LEADERBOARD_SEED,
    TEST_SEED,
    TRAIN_SEED,
    VAL_SEED,
    get_split,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("zero_shot_baseline")

RESULTS_DIR = Path("bench/results")
EVIDENCE_DIR = Path("artifacts/evidence/stage6")
REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_MODES = frozenset({"mock", "empirical"})
SPLIT_SEEDS = {
    "train": TRAIN_SEED,
    "val": VAL_SEED,
    "test": TEST_SEED,
    "leaderboard": LEADERBOARD_SEED,
}

InferenceCallable = Callable[
    [list[dict[str, str]], str, dict[str, Any]],
    Awaitable[str],
]


def tokenize_text(text: str) -> list[str]:
    """Tokenize lowercased text into alphanumeric word tokens for F1 scoring."""
    return re.findall(r"\w+", (text or "").lower())


def compute_diagnostic_f1(predicted: str, ground_truth: str) -> dict[str, float]:
    """Compute token-level precision, recall, and F1 after inference completes."""
    pred_tokens = tokenize_text(predicted)
    truth_tokens = tokenize_text(ground_truth)

    if not pred_tokens or not truth_tokens:
        f1 = 1.0 if pred_tokens == truth_tokens else 0.0
        return {"precision": f1, "recall": f1, "f1": f1}

    pred_set = set(pred_tokens)
    truth_set = set(truth_tokens)
    common = pred_set.intersection(truth_set)

    precision = len(common) / max(len(pred_set), 1)
    recall = len(common) / max(len(truth_set), 1)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def build_public_incident_input(meta: ScenarioMetadata) -> dict[str, Any]:
    """Project frozen metadata to fields observable in an incoming alert.

    Benchmark-only fields such as expected root cause, chaos kind, manifest,
    expected remediation, and verification predicates are deliberately absent.
    """
    return {
        "alert": {
            "labels": {
                "alertname": meta.expected_alert,
                "namespace": "default",
            },
            "status": "firing",
        }
    }


def build_zero_shot_messages(public_input: dict[str, Any]) -> list[dict[str, str]]:
    """Build the model request without benchmark labels or expected answers."""
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
                "You are the AtlasOps zero-shot incident diagnostician. Use only the "
                "provided alert observation. Do not claim that the environment is resolved. "
                "Return exactly one JSON object matching this schema: "
                f"{json.dumps(schema, sort_keys=True)}"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(public_input, sort_keys=True),
        },
    ]


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        raise RuntimeError("Unable to capture required Git source provenance") from exc
    return {"git_sha": sha, "git_dirty": dirty}


def _sanitized_endpoint() -> str:
    raw = os.getenv("VLLM_BASE", "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


def _parse_prediction(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1)
        text = re.sub(r"\s*```$", "", text, count=1)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise TypeError("Model prediction must be a JSON object")

    root_cause = payload.get("root_cause")
    if not isinstance(root_cause, str) or not root_cause.strip():
        raise ValueError("Model prediction requires a non-empty root_cause")
    affected = payload.get("affected_services", [])
    if not isinstance(affected, list) or not all(isinstance(item, str) for item in affected):
        raise ValueError("affected_services must be a list of strings")
    confidence = payload.get("confidence")
    if not isinstance(confidence, int | float) or not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("confidence must be a number between 0 and 1")
    return payload


async def openai_compatible_inference(
    messages: list[dict[str, str]],
    model_name: str,
    generation_config: dict[str, Any],
) -> str:
    """Call a configured OpenAI-compatible endpoint with no local fallback."""
    base_url = os.getenv("VLLM_BASE", "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("Empirical mode requires VLLM_BASE")

    api_key = os.getenv("LLM_API_KEY", "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": generation_config["temperature"],
        "top_p": generation_config["top_p"],
        "max_tokens": generation_config["max_tokens"],
        "seed": generation_config["seed"],
    }
    async with httpx.AsyncClient(headers=headers, timeout=generation_config["timeout_seconds"]) as client:
        response = await post_with_retry(
            client,
            f"{base_url}/chat/completions",
            payload,
            context="g6-zero-shot",
        )
        response.raise_for_status()
        body = response.json()

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Model response lacks choices[0].message.content") from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Model response content is empty")
    return content


def _resolve_mode(mode: str | None, mock: bool | None) -> Literal["mock", "empirical"]:
    if mode is None and mock is None:
        raise ValueError("Evaluation mode is required; choose 'mock' or 'empirical'")
    if mode is not None and mode not in EVALUATION_MODES:
        raise ValueError(f"Unknown evaluation mode: {mode!r}")
    compatibility_mode = None if mock is None else ("mock" if mock else "empirical")
    if mode is not None and compatibility_mode is not None and mode != compatibility_mode:
        raise ValueError("Conflicting mode and mock arguments")
    return (mode or compatibility_mode)  # type: ignore[return-value]


async def _evaluate_empirical_episode(
    scenario_id: str,
    model_name: str,
    generation_config: dict[str, Any],
    inference_fn: InferenceCallable,
    inference_backend: str,
    empirical_claim_allowed: bool,
) -> dict[str, Any]:
    meta = SCENARIO_CATALOG[scenario_id]
    public_input = build_public_incident_input(meta)
    messages = build_zero_shot_messages(public_input)
    started_at = datetime.now(UTC).isoformat()

    raw_text = ""
    try:
        raw_text = await inference_fn(messages, model_name, generation_config)
        prediction = _parse_prediction(raw_text)
        diagnostic_metrics = compute_diagnostic_f1(
            str(prediction["root_cause"]),
            meta.expected_root_cause,
        )
        status = "ok"
        error = None
    except Exception as exc:  # noqa: BLE001
        # Preserve every inference or parsing failure; never fall back to mock output.
        prediction = None
        diagnostic_metrics = {"precision": 0.0, "recall": 0.0, "f1": 0.0}
        status = "error"
        error = f"{type(exc).__name__}: {exc}"

    episode = {
        "scenario_id": scenario_id,
        "tier": meta.tier,
        "status": status,
        "evaluation_mode": "empirical",
        "empirical_inference_executed": status == "ok",
        "empirical_claim_allowed": empirical_claim_allowed and status == "ok",
        "inference_backend": inference_backend,
        "request_messages": messages,
        "public_input": public_input,
        "raw_model_response": raw_text,
        "prediction": prediction,
        "predicted_root_cause": (prediction or {}).get("root_cause", ""),
        "scoring_reference": {"expected_root_cause": meta.expected_root_cause},
        "diagnostic_metrics": diagnostic_metrics,
        "environment_resolution_evaluated": False,
        "env_resolved": None,
        "resolved": None,
        "agent_claimed_resolved": None,
        "outcome": "diagnosis_only" if status == "ok" else "inference_error",
        "total_turns": 1 if status == "ok" else 0,
        "time_to_resolve_s": None,
        "started_at": started_at,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    if error is not None:
        episode["error"] = error
    return episode


async def evaluate_zero_shot_split(
    split_name: str,
    model_name: str = "qwen2.5:7b-instruct",
    *,
    mode: str | None = None,
    mock: bool | None = None,
    model_revision: str | None = None,
    output_dir: Path | None = None,
    evidence_dir: Path | None = None,
    inference_fn: InferenceCallable | None = None,
    inference_backend: str | None = None,
    temperature: float = 0.0,
    top_p: float = 1.0,
    max_tokens: int = 512,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Evaluate a frozen split in explicitly selected mock or empirical mode."""
    selected_mode = _resolve_mode(mode, mock)
    scenario_ids = get_split(split_name)
    if selected_mode == "empirical" and not model_revision:
        raise ValueError("Empirical mode requires an exact model_revision")
    if selected_mode == "empirical" and output_dir is None:
        raise ValueError("Empirical mode requires an explicit unique output_dir")

    configured_backend = inference_fn is None
    if configured_backend:
        inference_fn = openai_compatible_inference
        selected_backend = "openai-compatible"
    else:
        selected_backend = inference_backend or "injected-test-double"
    if (
        selected_mode == "empirical"
        and configured_backend
        and not os.getenv("VLLM_BASE", "").strip()
    ):
        raise RuntimeError("Empirical mode requires VLLM_BASE")

    split_seed = SPLIT_SEEDS[split_name]
    generation_config = {
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "seed": split_seed,
        "timeout_seconds": timeout_seconds,
    }
    tag = f"zero_shot_{split_name}_{Path(model_name).name.replace(':', '_')}"
    started_at = datetime.now(UTC)
    run_id = f"{tag}-{started_at.strftime('%Y%m%d_%H%M%S_%f')}"
    out_dir = output_dir or (RESULTS_DIR / "non_empirical" / "zero_shot_baseline" / split_name / run_id)
    resolved_evidence_dir = evidence_dir or out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_evidence_dir.mkdir(parents=True, exist_ok=True)

    log.info(
        "Starting zero-shot baseline split=%s scenarios=%d model=%s mode=%s",
        split_name,
        len(scenario_ids),
        model_name,
        selected_mode,
    )

    episodes: list[dict[str, Any]] = []
    episodes_file = out_dir / "results_per_episode.jsonl"
    with episodes_file.open("x", encoding="utf-8", newline="\n") as stream:
        for index, scenario_id in enumerate(scenario_ids, 1):
            log.info("[%d/%d] Evaluating %s", index, len(scenario_ids), scenario_id)
            if selected_mode == "mock":
                episode = await run_scenario(scenario_id, mock=True)
                meta = SCENARIO_CATALOG[scenario_id]
                predicted = str(
                    episode.get("diagnosis", {}).get("final", {}).get("root_cause")
                    or episode.get("outcome")
                    or ""
                )
                episode.update(
                    {
                        "evaluation_mode": "mock",
                        "non_empirical": True,
                        "empirical_inference_executed": False,
                        "empirical_claim_allowed": False,
                        "ground_truth_root_cause": meta.expected_root_cause,
                        "predicted_root_cause": predicted,
                        "diagnostic_metrics": compute_diagnostic_f1(
                            predicted,
                            meta.expected_root_cause,
                        ),
                    }
                )
            else:
                episode = await _evaluate_empirical_episode(
                    scenario_id,
                    model_name,
                    generation_config,
                    inference_fn,
                    selected_backend,
                    configured_backend,
                )
            episodes.append(episode)
            stream.write(json.dumps(episode, sort_keys=True) + "\n")
            stream.flush()

    summary = compute_summary(episodes, tag=tag, model=model_name)
    valid = [episode for episode in episodes if episode.get("status") == "ok"]
    summary_updates = {
        "run_id": run_id,
        "variant": "Zero-Shot Baseline",
        "split_name": split_name,
        "evaluation_mode": selected_mode,
        "mock_eval": selected_mode == "mock",
        "non_empirical": selected_mode == "mock" or not configured_backend,
        "empirical_inference_executed": (
            selected_mode == "empirical"
            and len(valid) == len(episodes)
            and bool(episodes)
        ),
        "empirical_claim_allowed": (
            selected_mode == "empirical"
            and configured_backend
            and len(valid) == len(episodes)
            and bool(episodes)
        ),
        "environment_resolution_evaluated": False,
        "resolution_rate": summary["resolution_rate"] if selected_mode == "mock" else None,
        "failed_scenarios": len(episodes) - len(valid),
        "avg_diagnostic_f1": round(
            sum(item["diagnostic_metrics"]["f1"] for item in episodes)
            / max(len(episodes), 1),
            4,
        ),
        "model_revision": model_revision or "not_applicable",
        "inference_backend": selected_backend if selected_mode == "empirical" else "mock",
        "generation_config": generation_config,
        "truth_withheld_during_inference": True,
        "split_seed": split_seed,
        "split_sha256": _canonical_json_sha256(list(scenario_ids)),
        "dataset_sha256": _canonical_json_sha256(
            {
                scenario_id: SCENARIO_CATALOG[scenario_id].manifest_sha256
                for scenario_id in scenario_ids
            }
        ),
        "source": _source_provenance(),
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "endpoint": _sanitized_endpoint() if selected_mode == "empirical" else None,
        },
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "raw_predictions_path": str(episodes_file.resolve()),
        "raw_predictions_sha256": hashlib.sha256(episodes_file.read_bytes()).hexdigest(),
    }
    if selected_mode == "empirical":
        diagnostic_by_tier: dict[str, dict[str, float | int]] = {}
        for tier in sorted({episode["tier"] for episode in episodes}):
            tier_rows = [episode for episode in episodes if episode["tier"] == tier]
            diagnostic_by_tier[tier] = {
                "count": len(tier_rows),
                "avg_diagnostic_f1": round(
                    sum(row["diagnostic_metrics"]["f1"] for row in tier_rows)
                    / max(len(tier_rows), 1),
                    4,
                ),
            }
        summary_updates.update(
            {
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
    summary.update(summary_updates)

    summary_path = out_dir / "results_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    evidence_path = resolved_evidence_dir / f"zero_shot_{split_name}_summary.json"
    if evidence_path != summary_path:
        evidence_path.write_text(
            json.dumps(summary, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run AtlasOps zero-shot baseline")
    parser.add_argument(
        "--split",
        default="val",
        choices=["val", "test", "leaderboard", "train"],
    )
    parser.add_argument("--model", default="qwen2.5:7b-instruct")
    parser.add_argument("--model-revision")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--mock", action="store_true", help="Run NON_EMPIRICAL test mode")
    modes.add_argument(
        "--empirical",
        action="store_true",
        help="Call the configured base-model endpoint without cluster mutation",
    )
    parser.add_argument("--output", default="")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=512)
    args = parser.parse_args()

    await evaluate_zero_shot_split(
        split_name=args.split,
        model_name=args.model,
        mode="empirical" if args.empirical else "mock",
        model_revision=args.model_revision,
        output_dir=Path(args.output) if args.output else None,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
    )


if __name__ == "__main__":
    asyncio.run(main())
