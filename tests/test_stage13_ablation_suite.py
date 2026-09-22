"""Tests for artifact-driven Stage 13 ablation aggregation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bench.ablation_suite import (
    REQUIRED_PARTITIONS,
    REQUIRED_VARIANTS,
    aggregate_variant_partition,
    run_full_ablation_suite,
)


def _artifact(
    path: Path,
    *,
    partition: str = "val",
    empirical: bool = True,
    reward: float = 0.5,
) -> Path:
    payload = {
        "run_id": path.stem,
        "model": "checkpoint-a",
        "split": partition,
        "evaluation_mode": "empirical" if empirical else "mock",
        "non_empirical": not empirical,
        "empirical_claim_allowed": empirical,
        "resolution_rate": 0.5,
        "avg_time_to_resolve_s": 30.0,
        "avg_reward_contract": reward,
        "format_compliance_rate": 0.75,
        "raw_predictions_sha256": "a" * 64,
        "evaluator_source": {"git_sha": "b" * 40, "git_dirty": False},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_rejects_mock_artifacts(tmp_path):
    artifact = _artifact(tmp_path / "mock.json", empirical=False)
    with pytest.raises(ValueError, match="not empirical"):
        aggregate_variant_partition("SFT", "val", [artifact])


def test_rejects_missing_measured_metrics(tmp_path):
    artifact = _artifact(tmp_path / "missing.json")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["resolution_rate"] = None
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="lacks measured metrics"):
        aggregate_variant_partition("SFT", "val", [artifact])


def test_aggregates_repeated_runs_with_ci_and_artifact_hashes(tmp_path):
    first = _artifact(tmp_path / "run-1.json", reward=0.4)
    second = _artifact(tmp_path / "run-2.json", reward=0.6)
    result = aggregate_variant_partition("SFT", "val", [first, second])

    reward = result["metrics"]["avg_reward_contract"]
    assert reward["n"] == 2
    assert reward["mean"] == 0.5
    assert reward["ci95_half_width"] is not None
    assert result["artifacts"][0]["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()


def test_full_suite_uses_declared_artifacts_only(tmp_path):
    variants = {}
    for variant_index, variant in enumerate(REQUIRED_VARIANTS):
        partitions = {}
        for partition in REQUIRED_PARTITIONS:
            first = _artifact(
                tmp_path / "artifacts" / f"{variant_index}-{partition}-1.json",
                partition=partition,
            )
            second = _artifact(
                tmp_path / "artifacts" / f"{variant_index}-{partition}-2.json",
                partition=partition,
                reward=0.7,
            )
            partitions[partition] = [
                str(first.relative_to(tmp_path)),
                str(second.relative_to(tmp_path)),
            ]
        variants[variant] = partitions
    manifest = tmp_path / "ablation-inputs.json"
    manifest.write_text(
        json.dumps({"variants": variants}),
        encoding="utf-8",
    )
    result = run_full_ablation_suite(
        manifest_path=manifest,
        output_dir=tmp_path / "output",
    )
    assert result["evaluation_mode"] == "empirical_aggregation"
    assert result["non_empirical"] is False
    assert result["results"]["SFT Model"]["val"]["run_count"] == 2


def test_full_suite_rejects_incomplete_matrix(tmp_path):
    artifact = _artifact(tmp_path / "artifact.json")
    manifest = tmp_path / "ablation-inputs.json"
    manifest.write_text(
        json.dumps({"variants": {"SFT Model": {"val": [str(artifact)]}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing variants"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
        )


def test_dry_run_never_contains_metrics(tmp_path):
    result = run_full_ablation_suite(
        output_dir=tmp_path,
        dry_run=True,
    )
    assert result["evaluation_mode"] == "dry_run"
    assert result["non_empirical"] is True
    assert result["results"] is None
