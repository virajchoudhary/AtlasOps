"""AtlasOps Zero-Shot Baseline Benchmark Harness (Gate G6).

Evaluates unfinetuned base foundation models (e.g. Qwen2.5-7B-Instruct / 3B / 1.5B)
across standardized benchmark splits (Val, Test, Leaderboard, Train), recording
genuine zero-shot metrics (diagnostic F1, triage accuracy, tool validity, objective
environment resolution rate, time-to-resolve, and contract reward).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bench.runner import compute_summary, run_scenario, write_comparison_table
from config.scenario_catalog import SCENARIO_CATALOG, ScenarioMetadata
from config.splits import get_split

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("zero_shot_baseline")

RESULTS_DIR = Path("bench/results")
EVIDENCE_DIR = Path("artifacts/evidence/stage6")


def tokenize_text(text: str) -> list[str]:
    """Tokenize lowercased text into alphanumeric word tokens for F1 scoring."""
    return re.findall(r"\w+", (text or "").lower())


def compute_diagnostic_f1(predicted: str, ground_truth: str) -> dict[str, float]:
    """Compute token-level precision, recall, and F1 between predicted and ground-truth root cause."""
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
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


async def evaluate_zero_shot_split(
    split_name: str,
    model_name: str = "qwen2.5:7b-instruct",
    mock: bool = True,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run zero-shot baseline evaluation over a named benchmark split."""
    scenario_ids = get_split(split_name)
    tag = f"zero_shot_{split_name}_{Path(model_name).name.replace(':', '_')}"
    run_id = f"{tag}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    out_dir = output_dir or (RESULTS_DIR / "zero_shot_baseline" / split_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Starting zero-shot baseline on split=%s (%d scenarios) with model=%s (mock=%s)",
             split_name, len(scenario_ids), model_name, mock)

    episodes: list[dict[str, Any]] = []
    episodes_file = out_dir / "results_per_episode.jsonl"

    with episodes_file.open("w", encoding="utf-8") as f:
        for idx, sid in enumerate(scenario_ids, 1):
            log.info("[%d/%d] Running %s", idx, len(scenario_ids), sid)
            ep = await run_scenario(sid, mock=mock)
            
            # Enrich episode with ground-truth diagnostic evaluation
            meta = SCENARIO_CATALOG.get(sid)
            gt_root_cause = meta.expected_root_cause if meta else ""
            predicted_root_cause = str(
                ep.get("diagnosis", {}).get("final", {}).get("root_cause")
                or ep.get("outcome")
                or ""
            )
            diag_metrics = compute_diagnostic_f1(predicted_root_cause, gt_root_cause)
            ep["ground_truth_root_cause"] = gt_root_cause
            ep["predicted_root_cause"] = predicted_root_cause
            ep["diagnostic_metrics"] = diag_metrics

            episodes.append(ep)
            f.write(json.dumps(ep) + "\n")
            f.flush()

    summary = compute_summary(episodes, tag=tag, model=model_name)
    
    # Add zero-shot specific aggregate metrics
    avg_diag_f1 = (
        sum(e.get("diagnostic_metrics", {}).get("f1", 0.0) for e in episodes) / max(len(episodes), 1)
    )
    summary["avg_diagnostic_f1"] = round(avg_diag_f1, 3)
    summary["split_name"] = split_name
    summary["mock_eval"] = mock

    # Write summary files
    summary_path = out_dir / "results_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    
    evidence_path = EVIDENCE_DIR / f"zero_shot_{split_name}_summary.json"
    evidence_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    write_comparison_table(summary)
    log.info("Zero-shot baseline for split=%s finished! Resolution rate: %.1f%%, Avg Contract Reward: %.3f",
             split_name, summary["resolution_rate"] * 100, summary.get("avg_reward_contract", 0))

    return summary


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run AtlasOps Zero-Shot Baseline Benchmark")
    parser.add_argument("--split", default="val", choices=["val", "test", "leaderboard", "train"],
                        help="Benchmark split to evaluate")
    parser.add_argument("--model", default="qwen2.5:7b-instruct", help="Model name or path")
    parser.add_argument("--mock", action="store_true", default=True,
                        help="Run offline deterministic mock baseline")
    parser.add_argument("--live", action="store_true", help="Run against live cluster")
    parser.add_argument("--output", default="", help="Custom output directory")
    args = parser.parse_args()

    mock = not args.live if args.live else args.mock
    out = Path(args.output) if args.output else None
    await evaluate_zero_shot_split(split_name=args.split, model_name=args.model, mock=mock, output_dir=out)


if __name__ == "__main__":
    asyncio.run(main())
