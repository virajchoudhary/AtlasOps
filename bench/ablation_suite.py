"""Artifact-driven final ablation and stress evaluation (Gate G13).

This module never manufactures model metrics. It aggregates completed,
empirical evaluator artifacts and fails closed when provenance or required
measurements are absent. Dry-run mode emits only an execution plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REQUIRED_METRICS = (
    "resolution_rate",
    "avg_time_to_resolve_s",
    "avg_reward_contract",
    "format_compliance_rate",
)
REQUIRED_VARIANTS = (
    "Zero-Shot Baseline",
    "SFT Model",
    "SFT + Recommender",
    "Online GRPO RL",
    "Full Pipeline (GAI + RS + RL)",
)
REQUIRED_PARTITIONS = ("val", "test", "leaderboard", "adversarial")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_empirical_artifact(path: Path, expected_partition: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Evaluator artifact missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("evaluation_mode") != "empirical":
        raise ValueError(f"Artifact is not empirical: {path}")
    if payload.get("non_empirical") is not False:
        raise ValueError(f"Artifact is marked non-empirical: {path}")
    if payload.get("empirical_claim_allowed") is not True:
        raise ValueError(f"Artifact is not eligible for empirical claims: {path}")
    partition = payload.get("split") or payload.get("split_name") or payload.get("partition")
    if partition != expected_partition:
        raise ValueError(
            f"Artifact partition mismatch: expected {expected_partition!r}, got {partition!r}"
        )
    missing = [name for name in REQUIRED_METRICS if not isinstance(payload.get(name), int | float)]
    if missing:
        raise ValueError(f"Artifact lacks measured metrics {missing}: {path}")
    if not payload.get("raw_predictions_sha256") and not payload.get("raw_trajectory_sha256"):
        raise ValueError(f"Artifact lacks raw-output provenance: {path}")
    if not payload.get("evaluator_source") and not payload.get("source"):
        raise ValueError(f"Artifact lacks source provenance: {path}")
    return payload


def _aggregate(values: list[float]) -> dict[str, float | int | None]:
    mean = statistics.fmean(values)
    if len(values) < 2:
        ci95 = None
    else:
        ci95 = 1.96 * statistics.stdev(values) / math.sqrt(len(values))
    return {
        "n": len(values),
        "mean": round(mean, 6),
        "ci95_half_width": round(ci95, 6) if ci95 is not None else None,
    }


def aggregate_variant_partition(
    variant: str,
    partition: str,
    artifact_paths: list[Path],
) -> dict[str, Any]:
    """Aggregate one variant/partition from real evaluator artifacts."""
    if not artifact_paths:
        raise ValueError(f"No evaluator artifacts supplied for {variant}/{partition}")
    artifacts = [
        _load_empirical_artifact(path.resolve(), partition)
        for path in artifact_paths
    ]
    metrics = {
        metric: _aggregate([float(artifact[metric]) for artifact in artifacts])
        for metric in REQUIRED_METRICS
    }
    optional_runbook = [
        float(artifact["runbook_top3_hit_rate"])
        for artifact in artifacts
        if isinstance(artifact.get("runbook_top3_hit_rate"), int | float)
    ]
    if optional_runbook:
        metrics["runbook_top3_hit_rate"] = _aggregate(optional_runbook)
    return {
        "variant": variant,
        "partition": partition,
        "run_count": len(artifacts),
        "metrics": metrics,
        "artifacts": [
            {
                "path": str(path.resolve()),
                "sha256": _sha256(path.resolve()),
                "run_id": artifact.get("run_id"),
                "model": artifact.get("model"),
                "checkpoint_tree_sha256": artifact.get("checkpoint_tree_sha256"),
                "raw_output_sha256": (
                    artifact.get("raw_predictions_sha256")
                    or artifact.get("raw_trajectory_sha256")
                ),
                "source": artifact.get("evaluator_source") or artifact.get("source"),
            }
            for path, artifact in zip(artifact_paths, artifacts, strict=True)
        ],
    }


def load_ablation_manifest(path: Path) -> dict[str, dict[str, list[Path]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    variants = payload.get("variants")
    if not isinstance(variants, dict) or not variants:
        raise ValueError("Ablation manifest requires a non-empty variants object")
    missing_variants = sorted(set(REQUIRED_VARIANTS).difference(variants))
    if missing_variants:
        raise ValueError(f"Ablation manifest is missing variants: {missing_variants}")
    normalized = {}
    for variant in REQUIRED_VARIANTS:
        partitions = variants[variant]
        if not isinstance(partitions, dict):
            raise TypeError(f"Variant {variant!r} must map partitions to artifact lists")
        missing_partitions = sorted(set(REQUIRED_PARTITIONS).difference(partitions))
        if missing_partitions:
            raise ValueError(
                f"Ablation manifest is missing partitions for {variant}: {missing_partitions}"
            )
        normalized[variant] = {}
        for partition in REQUIRED_PARTITIONS:
            artifact_paths = partitions[partition]
            if not isinstance(artifact_paths, list):
                raise TypeError(f"{variant}/{partition} artifacts must be a list")
            normalized[variant][partition] = [
                (path.parent / item).resolve()
                for item in artifact_paths
                if isinstance(item, str)
            ]
    return normalized


def run_full_ablation_suite(
    *,
    manifest_path: Path | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Aggregate a declared comparison matrix or emit a non-empirical plan."""
    destination = output_dir or Path("bench/results/ablation")
    destination.mkdir(parents=True, exist_ok=True)
    if dry_run:
        payload = {
            "suite_name": "AtlasOps Final Ablation & Stress Evaluation",
            "evaluation_mode": "dry_run",
            "non_empirical": True,
            "results": None,
            "required_metrics": list(REQUIRED_METRICS),
            "required_variants": list(REQUIRED_VARIANTS),
            "required_partitions": list(REQUIRED_PARTITIONS),
            "message": "No evaluator artifacts were executed or scored.",
        }
    else:
        if manifest_path is None:
            raise ValueError("Empirical ablation requires --manifest")
        matrix = load_ablation_manifest(manifest_path.resolve())
        results = {
            variant: {
                partition: aggregate_variant_partition(variant, partition, artifact_paths)
                for partition, artifact_paths in partitions.items()
            }
            for variant, partitions in matrix.items()
        }
        payload = {
            "suite_name": "AtlasOps Final Ablation & Stress Evaluation",
            "evaluation_mode": "empirical_aggregation",
            "non_empirical": False,
            "created_at": datetime.now(UTC).isoformat(),
            "input_manifest": str(manifest_path.resolve()),
            "input_manifest_sha256": _sha256(manifest_path.resolve()),
            "results": results,
        }

    output = destination / "ablation_benchmark_results.json"
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps artifact-driven ablation runner")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run and args.manifest is not None:
        parser.error("--dry-run cannot be combined with --manifest")
    run_full_ablation_suite(
        manifest_path=args.manifest,
        output_dir=args.output,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
