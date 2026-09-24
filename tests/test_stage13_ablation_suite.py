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
    variant: str = "SFT Model",
    empirical: bool = True,
    reward: float = 0.5,
    resolution_rate: float = 0.5,
    ttr: float | None = 30.0,
) -> Path:
    raw_path = path.with_suffix(".jsonl")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text('{"prediction":"test"}\n', encoding="utf-8")
    payload = {
        "run_id": path.stem,
        "variant": variant,
        "model": "checkpoint-a",
        "split": partition,
        "evaluation_mode": "empirical" if empirical else "mock",
        "non_empirical": not empirical,
        "empirical_claim_allowed": empirical,
        "resolution_rate": resolution_rate,
        "avg_time_to_resolve_s": ttr,
        "avg_reward_contract": reward,
        "format_compliance_rate": 0.75,
        "raw_predictions_path": str(raw_path),
        "raw_predictions_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "evaluator_source": {"git_sha": "b" * 40, "git_dirty": False},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_rejects_mock_artifacts(tmp_path):
    artifact = _artifact(tmp_path / "mock.json", empirical=False)
    with pytest.raises(ValueError, match="not empirical"):
        aggregate_variant_partition("SFT Model", "val", [artifact])


def test_rejects_missing_measured_metrics(tmp_path):
    artifact = _artifact(tmp_path / "missing.json")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["resolution_rate"] = None
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="lacks measured metrics"):
        aggregate_variant_partition("SFT Model", "val", [artifact])


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("resolution_rate", True),
        ("resolution_rate", 1.01),
        ("avg_time_to_resolve_s", -1.0),
        ("avg_reward_contract", float("nan")),
        ("format_compliance_rate", -0.01),
    ],
)
def test_rejects_invalid_measured_metrics(tmp_path, metric, value):
    artifact = _artifact(tmp_path / "invalid.json")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload[metric] = value
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="metric|lacks measured"):
        aggregate_variant_partition("SFT Model", "val", [artifact])


def test_negative_unresolved_run_keeps_reward_and_undefined_ttr(tmp_path):
    artifact = _artifact(
        tmp_path / "unresolved.json",
        reward=-0.25,
        resolution_rate=0.0,
        ttr=None,
    )
    result = aggregate_variant_partition("SFT Model", "val", [artifact])
    assert result["metrics"]["avg_reward_contract"]["mean"] == -0.25
    assert result["metrics"]["avg_time_to_resolve_s"] == {
        "n": 0, "mean": None, "ci95_half_width": None
    }
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["resolution_rate"] = 0.5
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="lacks measured metrics"):
        aggregate_variant_partition("SFT Model", "val", [artifact])


def test_rejects_invalid_raw_output_hash(tmp_path):
    artifact = _artifact(tmp_path / "invalid-hash.json")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["raw_predictions_sha256"] = "not-a-sha256"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="raw-output provenance"):
        aggregate_variant_partition("SFT Model", "val", [artifact])


def test_rejects_tampered_raw_output(tmp_path):
    artifact = _artifact(tmp_path / "tampered.json")
    artifact.with_suffix(".jsonl").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="raw-output bytes"):
        aggregate_variant_partition("SFT Model", "val", [artifact])


def test_rejects_variant_and_dirty_source(tmp_path):
    artifact = _artifact(tmp_path / "wrong-variant.json")
    with pytest.raises(ValueError, match="variant"):
        aggregate_variant_partition("Online GRPO RL", "val", [artifact])
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["evaluator_source"]["git_dirty"] = True
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="clean immutable source"):
        aggregate_variant_partition("SFT Model", "val", [artifact])


def test_aggregates_repeated_runs_with_ci_and_artifact_hashes(tmp_path):
    first = _artifact(tmp_path / "run-1.json", reward=0.4)
    second = _artifact(tmp_path / "run-2.json", reward=0.6)
    result = aggregate_variant_partition("SFT Model", "val", [first, second])

    reward = result["metrics"]["avg_reward_contract"]
    assert reward["n"] == 2
    assert reward["mean"] == 0.5
    assert reward["ci95_half_width"] == pytest.approx(1.2706, abs=1e-5)
    assert result["artifacts"][0]["sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()


def test_rejects_duplicate_run_artifacts(tmp_path):
    artifact = _artifact(tmp_path / "run-1.json")
    with pytest.raises(ValueError, match="must be distinct"):
        aggregate_variant_partition("SFT Model", "val", [artifact, artifact])


@pytest.mark.parametrize("value", [True, float("nan"), -0.01, 1.01])
def test_rejects_invalid_optional_runbook_rate(tmp_path, value):
    artifact = _artifact(tmp_path / "invalid-optional.json")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["runbook_top3_hit_rate"] = value
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="runbook_top3_hit_rate"):
        aggregate_variant_partition("SFT Model", "val", [artifact])


def test_rejects_malformed_manifest_artifact_paths(tmp_path):
    variants = {
        variant: {partition: ["missing.json"] for partition in REQUIRED_PARTITIONS}
        for variant in REQUIRED_VARIANTS
    }
    variants["SFT Model"]["val"] = [123, "missing.json"]
    manifest = tmp_path / "ablation-inputs.json"
    manifest.write_text(json.dumps({"variants": variants}), encoding="utf-8")
    with pytest.raises(TypeError, match="non-empty paths"):
        run_full_ablation_suite(manifest_path=manifest, output_dir=tmp_path / "output")


def test_full_suite_uses_declared_artifacts_only(tmp_path):
    variants = {}
    for variant_index, variant in enumerate(REQUIRED_VARIANTS):
        partitions = {}
        for partition in REQUIRED_PARTITIONS:
            first = _artifact(
                tmp_path / "artifacts" / f"{variant_index}-{partition}-1.json",
                partition=partition,
                variant=variant,
            )
            second = _artifact(
                tmp_path / "artifacts" / f"{variant_index}-{partition}-2.json",
                partition=partition,
                variant=variant,
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
