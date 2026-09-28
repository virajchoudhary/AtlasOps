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
_ADVERSARIAL_ID_RE = re.compile(
    r"^adv-[a-z0-9](?:[a-z0-9-]{0,43}[a-z0-9])?$"
)
_MAX_ADVERSARIAL_MEMBERSHIP_BYTES = 262_144
_MAX_ADVERSARIAL_MEMBERSHIP_IDS = 4_096
_T_975_DF_1_TO_30 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validated_adversarial_ids(value: Any, *, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > _MAX_ADVERSARIAL_MEMBERSHIP_IDS
    ):
        raise ValueError(
            f"{label} must be a non-empty list of at most "
            f"{_MAX_ADVERSARIAL_MEMBERSHIP_IDS} adversarial IDs"
        )
    if any(
        not isinstance(item, str) or _ADVERSARIAL_ID_RE.fullmatch(item) is None
        for item in value
    ):
        raise ValueError(f"{label} contains an unsafe adversarial ID")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must contain unique adversarial IDs")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"Adversarial membership record has duplicate field {key!r}")
        record[key] = value
    return record


def _load_adversarial_membership(
    manifest_path: Path,
    expected_sha256: str | None,
) -> tuple[dict[str, Any], Path, str]:
    if expected_sha256 is None:
        raise ValueError(
            "Expected adversarial membership SHA-256 must be supplied externally"
        )
    if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(
        expected_sha256
    ):
        raise ValueError(
            "Expected adversarial membership SHA-256 must be a 64-character digest"
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    membership_reference = manifest.get("adversarial_membership")
    if not isinstance(membership_reference, str) or not membership_reference:
        raise ValueError(
            "Ablation manifest must reference an adversarial membership record"
        )
    membership_path = Path(membership_reference)
    if not membership_path.is_absolute():
        membership_path = manifest_path.parent / membership_path
    membership_path = membership_path.resolve()
    if not membership_path.is_file():
        raise FileNotFoundError(
            f"Adversarial membership record missing: {membership_path}"
        )
    with membership_path.open("rb") as source:
        record_bytes = source.read(_MAX_ADVERSARIAL_MEMBERSHIP_BYTES + 1)
    if len(record_bytes) > _MAX_ADVERSARIAL_MEMBERSHIP_BYTES:
        raise ValueError(
            "Adversarial membership record exceeds "
            f"{_MAX_ADVERSARIAL_MEMBERSHIP_BYTES} bytes"
        )
    actual_sha256 = hashlib.sha256(record_bytes).hexdigest()
    if actual_sha256 != expected_sha256.lower():
        raise ValueError(
            "Adversarial membership record does not match external SHA-256 trust anchor"
        )
    try:
        record = json.loads(
            record_bytes.decode("utf-8"), object_pairs_hook=_unique_json_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Adversarial membership record is not valid UTF-8 JSON") from exc
    required_fields = {
        "schema_version",
        "split",
        "seed",
        "protocol_sha256",
        "adversarial_ids",
    }
    if not isinstance(record, dict) or set(record) != required_fields:
        raise ValueError(
            "Adversarial membership record must contain exactly the v1 fields"
        )
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ValueError("Adversarial membership record schema_version must be 1")
    if record["split"] != "adversarial":
        raise ValueError("Adversarial membership record split must be 'adversarial'")
    if type(record["seed"]) is not int or record["seed"] < 0:
        raise ValueError("Adversarial membership record seed must be non-negative")
    protocol_sha256 = record["protocol_sha256"]
    if not isinstance(protocol_sha256, str) or not _SHA256_RE.fullmatch(
        protocol_sha256
    ):
        raise ValueError("Adversarial membership protocol_sha256 must be a SHA-256 digest")
    adversarial_ids = _validated_adversarial_ids(
        record["adversarial_ids"],
        label="Adversarial membership record IDs",
    )
    return (
        {
            **record,
            "protocol_sha256": protocol_sha256.lower(),
            "adversarial_ids": adversarial_ids,
        },
        membership_path,
        actual_sha256,
    )


def _validate_adversarial_artifact(
    artifact: dict[str, Any],
    membership: dict[str, Any],
    membership_sha256: str,
) -> None:
    if artifact.get("split") != "adversarial":
        raise ValueError("Adversarial artifact requires an exact split field")
    artifact_membership_sha256 = artifact.get("adversarial_membership_sha256")
    if (
        not isinstance(artifact_membership_sha256, str)
        or not _SHA256_RE.fullmatch(artifact_membership_sha256)
        or artifact_membership_sha256.lower() != membership_sha256
    ):
        raise ValueError("Artifact adversarial membership SHA-256 mismatch")
    try:
        artifact_ids = _validated_adversarial_ids(
            artifact.get("adversarial_ids"),
            label="Artifact adversarial membership IDs",
        )
    except ValueError as exc:
        raise ValueError("Artifact adversarial membership IDs are invalid") from exc
    if artifact_ids != membership["adversarial_ids"]:
        raise ValueError("Artifact adversarial membership IDs mismatch")
    if type(artifact.get("seed")) is not int or artifact["seed"] != membership["seed"]:
        raise ValueError("Artifact adversarial seed mismatch")
    artifact_protocol_sha256 = artifact.get("protocol_sha256")
    if (
        not isinstance(artifact_protocol_sha256, str)
        or not _SHA256_RE.fullmatch(artifact_protocol_sha256)
        or artifact_protocol_sha256.lower() != membership["protocol_sha256"]
    ):
        raise ValueError("Artifact adversarial protocol SHA-256 mismatch")


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


def _aggregate_variant_partition_validated(
    variant: str,
    partition: str,
    artifact_paths: list[Path],
    *,
    adversarial_membership: dict[str, Any] | None = None,
    adversarial_membership_sha256: str | None = None,
) -> dict[str, Any]:
    """Aggregate one variant/partition from real evaluator artifacts."""
    if not artifact_paths:
        raise ValueError(f"No evaluator artifacts supplied for {variant}/{partition}")
    artifacts = [
        _load_empirical_artifact(path.resolve(), partition, variant)
        for path in artifact_paths
    ]
    if partition == "adversarial":
        if adversarial_membership is None or adversarial_membership_sha256 is None:
            raise ValueError(
                "Adversarial aggregation requires an externally anchored membership record"
            )
        for artifact in artifacts:
            _validate_adversarial_artifact(
                artifact,
                adversarial_membership,
                adversarial_membership_sha256,
            )
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


def aggregate_variant_partition(
    variant: str,
    partition: str,
    artifact_paths: list[Path],
) -> dict[str, Any]:
    """Aggregate ordinary partitions; adversarial requires full-suite anchoring."""
    if partition == "adversarial":
        raise ValueError(
            "Adversarial aggregation requires the full suite with an external membership SHA-256"
        )
    return _aggregate_variant_partition_validated(
        variant, partition, artifact_paths
    )


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
    expected_adversarial_membership_sha256: str | None = None,
) -> dict[str, Any]:
    """Aggregate a declared comparison matrix or emit a non-empirical plan."""
    destination = output_dir or Path("bench/results/ablation")
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
        resolved_manifest_path = manifest_path.resolve()
        matrix = load_ablation_manifest(resolved_manifest_path)
        membership, membership_path, membership_sha256 = _load_adversarial_membership(
            resolved_manifest_path,
            expected_adversarial_membership_sha256,
        )
        results = {
            variant: {
                partition: _aggregate_variant_partition_validated(
                    variant,
                    partition,
                    artifact_paths,
                    adversarial_membership=(
                        membership if partition == "adversarial" else None
                    ),
                    adversarial_membership_sha256=(
                        membership_sha256 if partition == "adversarial" else None
                    ),
                )
                for partition, artifact_paths in partitions.items()
            }
            for variant, partitions in matrix.items()
        }
        payload = {
            "suite_name": "AtlasOps Final Ablation & Stress Evaluation",
            "evaluation_mode": "empirical_aggregation",
            "non_empirical": False,
            "empirical_claim_allowed": False,
            "certification_status": "NOT_CERTIFIED",
            "created_at": datetime.now(UTC).isoformat(),
            "input_manifest": str(resolved_manifest_path),
            "input_manifest_sha256": _sha256(resolved_manifest_path),
            "adversarial_membership": {
                "path": str(membership_path),
                "sha256": membership_sha256,
            },
            "results": results,
        }

    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "ablation_benchmark_results.json"
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps artifact-driven ablation runner")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-adversarial-membership-sha256")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run and args.manifest is not None:
        parser.error("--dry-run cannot be combined with --manifest")
    run_full_ablation_suite(
        manifest_path=args.manifest,
        output_dir=args.output,
        dry_run=args.dry_run,
        expected_adversarial_membership_sha256=(
            args.expected_adversarial_membership_sha256
        ),
    )


if __name__ == "__main__":
    main()
