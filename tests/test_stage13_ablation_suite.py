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
    adversarial_membership_sha256: str | None = None,
    adversarial_ids: list[str] | None = None,
    seed: int | None = None,
    protocol_sha256: str | None = None,
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
    if adversarial_membership_sha256 is not None:
        payload["adversarial_membership_sha256"] = adversarial_membership_sha256
    if adversarial_ids is not None:
        payload["adversarial_ids"] = adversarial_ids
    if seed is not None:
        payload["seed"] = seed
    if protocol_sha256 is not None:
        payload["protocol_sha256"] = protocol_sha256
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_test_membership_record(
    tmp_path: Path,
    *,
    adversarial_ids: list[str] | None = None,
    seed: int = 7,
    protocol_sha256: str = "c" * 64,
) -> tuple[Path, str, dict]:
    record = {
        "schema_version": 1,
        "split": "adversarial",
        "seed": seed,
        "protocol_sha256": protocol_sha256,
        "adversarial_ids": adversarial_ids or [
            "adv-test-only-001",
            "adv-test-only-002",
        ],
    }
    path = tmp_path / "test-only-adversarial-membership.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest, record


def _write_full_suite_inputs(
    tmp_path: Path,
    *,
    record: dict | None = None,
    include_membership_reference: bool = True,
) -> tuple[Path, Path, str, list[Path]]:
    if record is None:
        membership_path, membership_sha256, record = _write_test_membership_record(
            tmp_path
        )
    else:
        membership_path = tmp_path / "test-only-adversarial-membership.json"
        membership_path.write_text(json.dumps(record), encoding="utf-8")
        membership_sha256 = hashlib.sha256(membership_path.read_bytes()).hexdigest()

    variants = {}
    adversarial_artifacts = []
    for variant_index, variant in enumerate(REQUIRED_VARIANTS):
        partitions = {}
        for partition in REQUIRED_PARTITIONS:
            artifact_paths = []
            for run_index, reward in enumerate((0.5, 0.7), start=1):
                artifact = _artifact(
                    tmp_path / "artifacts" / f"{variant_index}-{partition}-{run_index}.json",
                    partition=partition,
                    variant=variant,
                    reward=reward,
                    adversarial_membership_sha256=(
                        membership_sha256 if partition == "adversarial" else None
                    ),
                    adversarial_ids=(
                        record["adversarial_ids"]
                        if partition == "adversarial"
                        and isinstance(record.get("adversarial_ids"), list)
                        else None
                    ),
                    seed=(
                        record["seed"]
                        if partition == "adversarial"
                        and isinstance(record.get("seed"), int)
                        else None
                    ),
                    protocol_sha256=(
                        record["protocol_sha256"]
                        if partition == "adversarial"
                        and isinstance(record.get("protocol_sha256"), str)
                        else None
                    ),
                )
                artifact_paths.append(str(artifact.relative_to(tmp_path)))
                if partition == "adversarial":
                    adversarial_artifacts.append(artifact)
            partitions[partition] = artifact_paths
        variants[variant] = partitions

    manifest_payload = {"variants": variants}
    if include_membership_reference:
        manifest_payload["adversarial_membership"] = str(
            membership_path.relative_to(tmp_path)
        )
    manifest = tmp_path / "ablation-inputs.json"
    manifest.write_text(json.dumps(manifest_payload), encoding="utf-8")
    return manifest, membership_path, membership_sha256, adversarial_artifacts


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


def test_direct_adversarial_aggregation_cannot_supply_its_own_trust_anchor(tmp_path):
    membership = {
        "adversarial_ids": ["adv-test-only-001"],
        "seed": 7,
        "protocol_sha256": "c" * 64,
    }
    digest = "d" * 64
    artifact = _artifact(
        tmp_path / "self-declared.json",
        partition="adversarial",
        adversarial_membership_sha256=digest,
        adversarial_ids=membership["adversarial_ids"],
        seed=7,
        protocol_sha256=membership["protocol_sha256"],
    )
    with pytest.raises(ValueError, match="full suite"):
        aggregate_variant_partition("SFT Model", "adversarial", [artifact])
    with pytest.raises(TypeError, match="unexpected keyword"):
        aggregate_variant_partition(
            "SFT Model",
            "adversarial",
            [artifact],
            adversarial_membership=membership,
            adversarial_membership_sha256=digest,
        )


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
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    result = run_full_ablation_suite(
        manifest_path=manifest,
        output_dir=tmp_path / "output",
        expected_adversarial_membership_sha256=membership_sha256,
    )
    assert result["evaluation_mode"] == "empirical_aggregation"
    assert result["non_empirical"] is False
    assert result["empirical_claim_allowed"] is False
    assert result["certification_status"] == "NOT_CERTIFIED"
    assert result["results"]["SFT Model"]["val"]["run_count"] == 2


def test_full_suite_requires_external_membership_hash_anchor(tmp_path):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    output_dir = tmp_path / "output"
    with pytest.raises(ValueError, match="supplied externally"):
        run_full_ablation_suite(manifest_path=manifest, output_dir=output_dir)
    with pytest.raises(ValueError, match="does not match external"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=output_dir,
            expected_adversarial_membership_sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="64-character digest"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=output_dir,
            expected_adversarial_membership_sha256="not-a-digest",
        )
    assert not output_dir.exists()
    assert len(membership_sha256) == 64


def test_full_suite_requires_membership_record_reference(tmp_path):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(
        tmp_path, include_membership_reference=False
    )
    with pytest.raises(ValueError, match="reference an adversarial membership"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=membership_sha256,
        )


def test_full_suite_rejects_missing_membership_record(tmp_path):
    manifest, membership_path, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    membership_path.unlink()
    with pytest.raises(FileNotFoundError, match="membership record missing"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=membership_sha256,
        )


def test_full_suite_rejects_oversized_membership_record(tmp_path):
    manifest, membership_path, _, _ = _write_full_suite_inputs(tmp_path)
    record_bytes = b" " * 262_145
    membership_path.write_bytes(record_bytes)
    expected_sha256 = hashlib.sha256(record_bytes).hexdigest()
    with pytest.raises(ValueError, match="exceeds 262144 bytes"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=expected_sha256,
        )


def test_full_suite_rejects_duplicate_membership_fields(tmp_path):
    manifest, membership_path, _, _ = _write_full_suite_inputs(tmp_path)
    record = json.loads(membership_path.read_text(encoding="utf-8"))
    membership_path.write_text(
        json.dumps(record)[:-1] + ', "adversarial_ids": ["adv-test-only-003"]}',
        encoding="utf-8",
    )
    expected_sha256 = hashlib.sha256(membership_path.read_bytes()).hexdigest()
    output_dir = tmp_path / "output"
    with pytest.raises(ValueError, match="duplicate field 'adversarial_ids'"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=output_dir,
            expected_adversarial_membership_sha256=expected_sha256,
        )
    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"adversarial_ids": ["adv-test-only-001", "adv-test-only-001"]},
            "unique",
        ),
        ({"adversarial_ids": ["../escape"]}, "safe"),
        ({"adversarial_ids": []}, "non-empty"),
        ({"seed": -1}, "non-negative"),
        ({"protocol_sha256": "not-a-digest"}, "protocol_sha256"),
    ],
)
def test_full_suite_rejects_invalid_membership_record(tmp_path, updates, message):
    manifest, membership_path, _, _ = _write_full_suite_inputs(tmp_path)
    record = json.loads(membership_path.read_text(encoding="utf-8"))
    record.update(updates)
    membership_path.write_text(json.dumps(record), encoding="utf-8")
    expected_sha256 = hashlib.sha256(membership_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match=message):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=expected_sha256,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("adversarial_membership_sha256", "d" * 64, "membership SHA-256"),
        ("adversarial_ids", ["adv-unapproved"], "membership IDs"),
        (
            "adversarial_ids",
            ["adv-test-only-002", "adv-test-only-001"],
            "membership IDs",
        ),
        ("seed", 8, "seed mismatch"),
        ("protocol_sha256", "d" * 64, "protocol SHA-256"),
    ],
)
def test_full_suite_rejects_adversarial_artifact_provenance_mismatch(
    tmp_path, field, value, message
):
    manifest, _, membership_sha256, adversarial_artifacts = _write_full_suite_inputs(
        tmp_path
    )
    artifact_path = adversarial_artifacts[0]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact[field] = value
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=membership_sha256,
        )


def test_full_suite_requires_exact_adversarial_split_field(tmp_path):
    manifest, _, membership_sha256, adversarial_artifacts = _write_full_suite_inputs(
        tmp_path
    )
    artifact_path = adversarial_artifacts[0]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact.pop("split")
    artifact["split_name"] = "adversarial"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="exact split field"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=membership_sha256,
        )


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
