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
import re
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
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_T_975_DF_1_TO_30 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_empirical_artifact(
    path: Path, expected_partition: str, expected_variant: str | None = None
) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Evaluator artifact missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("evaluation_mode") != "empirical":
        raise ValueError(f"Artifact is not empirical: {path}")
    if payload.get("non_empirical") is not False:
        raise ValueError(f"Artifact is marked non-empirical: {path}")
    if payload.get("empirical_claim_allowed") is not True:
        raise ValueError(f"Artifact is not eligible for empirical claims: {path}")
    if expected_variant is not None and payload.get("variant") != expected_variant:
        raise ValueError(f"Artifact variant does not match {expected_variant!r}: {path}")
    partition = payload.get("split") or payload.get("split_name") or payload.get("partition")
    if partition != expected_partition:
        raise ValueError(
            f"Artifact partition mismatch: expected {expected_partition!r}, got {partition!r}"
        )
    missing = [
        name
        for name in REQUIRED_METRICS
        if (
            not isinstance(payload.get(name), int | float)
            or isinstance(payload.get(name), bool)
        )
        and not (
            name == "avg_time_to_resolve_s"
            and payload.get(name) is None
            and payload.get("resolution_rate") == 0
        )
    ]
    if missing:
        raise ValueError(f"Artifact lacks measured metrics {missing}: {path}")
    for metric in REQUIRED_METRICS:
        if metric == "avg_time_to_resolve_s" and payload[metric] is None:
            continue
        value = float(payload[metric])
        if not math.isfinite(value):
            raise ValueError(f"Artifact metric {metric} is not finite: {path}")
        if metric == "avg_reward_contract":
            if not -0.25 <= value <= 1.0:
                raise ValueError(f"Artifact metric {metric} is outside [-0.25, 1]: {path}")
        elif metric in {"resolution_rate", "format_compliance_rate"}:
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"Artifact metric {metric} is outside [0, 1]: {path}")
        elif value < 0.0:
            raise ValueError(f"Artifact metric {metric} must be non-negative: {path}")
    raw_output_hash = payload.get("raw_predictions_sha256") or payload.get("raw_trajectory_sha256")
    raw_output_path = payload.get("raw_predictions_path") or payload.get("raw_trajectory_path")
    if not isinstance(raw_output_hash, str) or not _SHA256_RE.fullmatch(raw_output_hash):
        raise ValueError(f"Artifact lacks raw-output provenance: {path}")
    if not isinstance(raw_output_path, str) or not raw_output_path:
        raise ValueError(f"Artifact lacks raw-output path: {path}")
    raw_path = Path(raw_output_path)
    if not raw_path.is_absolute():
        raw_path = path.parent / raw_path
    if not raw_path.is_file() or _sha256(raw_path) != raw_output_hash.lower():
        raise ValueError(f"Artifact raw-output bytes do not match provenance: {path}")
    source = payload.get("evaluator_source") or payload.get("source")
    if not isinstance(source, dict):
        raise TypeError(f"Artifact lacks source provenance: {path}")
    source_sha = source.get("git_sha") or source.get("code_sha")
    source_clean = source.get("git_dirty") is False if "git_dirty" in source else source.get("source_state") == "clean"
    if not isinstance(source_sha, str) or not _GIT_SHA_RE.fullmatch(source_sha) or not source_clean:
        raise ValueError(f"Artifact lacks clean immutable source provenance: {path}")
    return payload


def _t_975(df: int) -> float:
    if df <= len(_T_975_DF_1_TO_30):
        return _T_975_DF_1_TO_30[df - 1]
    z = 1.959963984540054
    return (
        z
        + (z**3 + z) / (4 * df)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3)
    )


def _aggregate(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "mean": None, "ci95_half_width": None}
    mean = statistics.fmean(values)
    if len(values) < 2:
        ci95 = None
    else:
        ci95 = _t_975(len(values) - 1) * statistics.stdev(values) / math.sqrt(len(values))
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
        _load_empirical_artifact(path.resolve(), partition, variant)
        for path in artifact_paths
    ]
    run_ids = [artifact.get("run_id") for artifact in artifacts]
    if any(not isinstance(run_id, str) or not run_id for run_id in run_ids):
        raise ValueError(f"Artifacts require distinct run_id provenance for {variant}/{partition}")
    if len(set(run_ids)) != len(run_ids) or len({path.resolve() for path in artifact_paths}) != len(artifact_paths):
        raise ValueError(f"Repeated run artifacts must be distinct for {variant}/{partition}")
    metrics = {
        metric: _aggregate([
            float(artifact[metric])
            for artifact in artifacts
            if artifact[metric] is not None
        ])
        for metric in REQUIRED_METRICS
    }
    optional_runbook = []
    for artifact in artifacts:
        if "runbook_top3_hit_rate" not in artifact:
            continue
        value = artifact["runbook_top3_hit_rate"]
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not 0.0 <= value <= 1.0
        ):
            raise ValueError("Optional runbook_top3_hit_rate must be a finite rate in [0, 1]")
        optional_runbook.append(float(value))
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
            if (
                not isinstance(artifact_paths, list)
                or not artifact_paths
                or any(not isinstance(item, str) or not item for item in artifact_paths)
            ):
                raise TypeError(f"{variant}/{partition} artifacts must be non-empty paths")
            normalized[variant][partition] = [
                (path.parent / item).resolve()
                for item in artifact_paths
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
