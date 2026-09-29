"""Tests for artifact-driven Stage 13 ablation aggregation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bench import episode_membership
from config.splits import get_split
from bench.ablation_suite import (
    REQUIRED_PARTITIONS,
    REQUIRED_VARIANTS,
    aggregate_variant_partition,
    run_full_ablation_suite,
)


def _membership_sha256(scenario_ids: list[str]) -> str:
    content = json.dumps(
        scenario_ids, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _synthetic_raw_episode_rows(
    variant: str, scenario_ids: list[str]
) -> list[dict]:
    """Build synthetic schema fixtures, not empirical evaluation evidence."""
    rows = []
    for scenario_id in scenario_ids:
        row = {"scenario_id": scenario_id, "evaluation_mode": "empirical"}
        if variant == "Zero-Shot Baseline":
            row["prediction"] = {"root_cause": "synthetic"}
        elif variant == "SFT Model":
            row["raw_model_response"] = "synthetic"
        elif variant == "SFT + Recommender":
            row["recommendation"] = "synthetic"
        elif variant == "Full Pipeline (GAI + RS + RL)":
            row["pipeline_result"] = "synthetic"
        rows.append(row)
    return rows


def _synthetic_g9_events(
    partition: str,
    scenario_ids: list[str],
    *,
    run_id: str,
    model: str,
    evaluator_source: dict,
    provenance: dict,
) -> list[dict]:
    """Build synthetic G9 event fixtures, not empirical evaluation evidence."""
    split_sha256 = _membership_sha256(scenario_ids)
    events = [
        {
            "event": "run_started",
            "evaluation_mode": "EMPIRICAL",
            "split": partition,
            "split_sha256": split_sha256,
            "evaluator_source": evaluator_source,
            "provenance": provenance,
        }
    ]
    for scenario_id in scenario_ids:
        events.extend(
            [
                {
                    "event": "episode_started",
                    "evaluation_mode": "EMPIRICAL",
                    "scenario_id": scenario_id,
                },
                {
                    "event": "policy_output",
                    "scenario_id": scenario_id,
                    "step": 0,
                },
                {
                    "event": "step_result",
                    "scenario_id": scenario_id,
                    "record": {"step": 0},
                },
                {
                    "event": "step_result",
                    "scenario_id": scenario_id,
                    "record": {"step": 1},
                },
                {
                    "event": "episode_completed",
                    "scenario_id": scenario_id,
                    "result": {
                        "scenario_id": scenario_id,
                        "status": "ok",
                        "scorable": True,
                    },
                },
            ]
        )
    events.append(
        {
            "event": "run_completed",
            "summary": {
                "split": partition,
                "split_sha256": split_sha256,
                "run_id": run_id,
                "model": model,
                "evaluator_source": evaluator_source,
                "provenance": provenance,
                "scenario_count": len(scenario_ids),
                "completed_episodes": len(scenario_ids),
                "empirical_claim_allowed": True,
            },
        }
    )
    return events


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
    raw_scenario_ids: list[str] | None = None,
) -> Path:
    if raw_scenario_ids is None and partition in {"val", "test", "leaderboard"}:
        raw_scenario_ids = list(get_split(partition))
    raw_path = path.with_suffix(".jsonl")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if raw_scenario_ids is None:
        raw_rows = [{"prediction": "test"}]
    elif variant == "Online GRPO RL":
        raw_rows = _synthetic_g9_events(
            partition,
            raw_scenario_ids,
            run_id=path.stem,
            model="checkpoint-a",
            evaluator_source={"git_sha": "b" * 40, "git_dirty": False},
            provenance={
                "base_model": {"id": "checkpoint-a"},
                "live_execution": {"execute_live_chaos": True},
            },
        )
    else:
        raw_rows = _synthetic_raw_episode_rows(variant, raw_scenario_ids)
    raw_path.write_text(
        "".join(json.dumps(row) + "\n" for row in raw_rows),
        encoding="utf-8",
    )
    raw_path_field = (
        "raw_trajectory_path" if variant == "Online GRPO RL" else "raw_predictions_path"
    )
    raw_hash_field = (
        "raw_trajectory_sha256"
        if variant == "Online GRPO RL"
        else "raw_predictions_sha256"
    )
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
        raw_path_field: str(raw_path),
        raw_hash_field: hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "evaluator_source": {"git_sha": "b" * 40, "git_dirty": False},
    }
    if variant == "Online GRPO RL":
        payload["provenance"] = {
            "base_model": {"id": "checkpoint-a"},
            "live_execution": {"execute_live_chaos": True},
        }
    if raw_scenario_ids is not None:
        payload["split_sha256"] = _membership_sha256(raw_scenario_ids)
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
                raw_scenario_ids = (
                    list(record["adversarial_ids"])
                    if partition == "adversarial"
                    else list(get_split(partition))
                )
                artifact = _artifact(
                    tmp_path / "artifacts" / f"{variant_index}-{partition}-{run_index}.json",
                    partition=partition,
                    variant=variant,
                    reward=reward,
                    raw_scenario_ids=raw_scenario_ids,
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
    assert result["episode_membership"]["scenario_ids"] == list(get_split("val"))
    assert result["metrics_source"] == "unverified_artifact_summaries"
    assert result["non_empirical"] is True
    assert result["empirical_claim_allowed"] is False


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "out_of_order"])
def test_public_aggregate_rejects_raw_episode_membership_mismatch(tmp_path, mutation):
    artifact = _artifact(tmp_path / "public-mismatch.json")
    rows = _synthetic_raw_episode_rows("SFT Model", list(get_split("val")))
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = dict(rows[0])
    else:
        rows[0], rows[1] = rows[1], rows[0]
    _rewrite_raw_output(artifact, "".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises(ValueError, match="raw episode membership"):
        aggregate_variant_partition("SFT Model", "val", [artifact])


def test_public_aggregate_requires_expected_split_hash(tmp_path):
    artifact = _artifact(tmp_path / "public-split-hash.json")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload.pop("split_sha256")
    artifact.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="split_sha256"):
        aggregate_variant_partition("SFT Model", "val", [artifact])


def test_public_aggregate_supports_g9_event_stream(tmp_path):
    artifact = _artifact(
        tmp_path / "public-g9.json",
        variant="Online GRPO RL",
        raw_scenario_ids=list(get_split("val")),
    )

    result = aggregate_variant_partition("Online GRPO RL", "val", [artifact])

    assert result["episode_membership"]["scenario_ids"] == list(get_split("val"))
    assert result["metrics_source"] == "unverified_artifact_summaries"


@pytest.mark.parametrize(
    ("variant", "partition", "message"),
    [
        ("Unknown Variant", "val", "unsupported variant"),
        ("SFT Model", "train", "unsupported partition"),
    ],
)
def test_public_aggregate_rejects_unsupported_variant_or_partition(
    tmp_path, variant, partition, message
):
    artifact = _artifact(tmp_path / "unsupported.json")

    with pytest.raises(ValueError, match=message):
        aggregate_variant_partition(variant, partition, [artifact])


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
    assert result["evaluation_mode"] == "declared_artifact_aggregation"
    assert result["non_empirical"] is True
    assert result["metrics_source"] == "unverified_artifact_summaries"
    assert result["empirical_claim_allowed"] is False
    assert result["certification_status"] == "NOT_CERTIFIED"
    assert result["results"]["SFT Model"]["val"]["run_count"] == 2
    for variant in REQUIRED_VARIANTS:
        membership = result["results"][variant]["val"]["episode_membership"]
        assert membership["scenario_ids"] == list(get_split("val"))
        assert membership["scenario_ids_sha256"] == _membership_sha256(
            list(get_split("val"))
        )
    leaderboard_ids = set(get_split("leaderboard"))
    assert leaderboard_ids.intersection(get_split("train"))
    assert leaderboard_ids.intersection(get_split("val"))
    assert (
        result["results"]["SFT + Recommender"]["leaderboard"]
        ["episode_membership"]["interpretation"]
        == "overlaps Train and Validation; not an independent held-out set"
    )
    assert result["results"]["Zero-Shot Baseline"]["adversarial"][
        "episode_membership"
    ]["scenario_ids"] == ["adv-test-only-001", "adv-test-only-002"]


def _rewrite_raw_output(artifact_path: Path, contents: str) -> None:
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    raw_path = Path(
        payload.get("raw_predictions_path") or payload["raw_trajectory_path"]
    )
    raw_path.write_text(contents, encoding="utf-8", newline="\n")
    raw_hash_field = (
        "raw_predictions_sha256"
        if "raw_predictions_path" in payload
        else "raw_trajectory_sha256"
    )
    payload[raw_hash_field] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "out_of_order"])
def test_full_suite_rejects_raw_episode_membership_mismatch_without_output(
    tmp_path, mutation
):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    artifact_path = tmp_path / "artifacts" / "0-val-1.json"
    expected_ids = list(get_split("val"))
    rows = _synthetic_raw_episode_rows("Zero-Shot Baseline", expected_ids)
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = dict(rows[0])
    else:
        rows[0], rows[1] = rows[1], rows[0]
    _rewrite_raw_output(
        artifact_path,
        "".join(json.dumps(row) + "\n" for row in rows),
    )
    output_dir = tmp_path / "output"

    with pytest.raises(ValueError, match="raw episode membership"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=output_dir,
            expected_adversarial_membership_sha256=membership_sha256,
        )

    assert not output_dir.exists()


def test_full_suite_compares_adversarial_raw_ids_to_anchored_order(tmp_path):
    manifest, _, membership_sha256, adversarial_artifacts = _write_full_suite_inputs(
        tmp_path
    )
    artifact_path = adversarial_artifacts[0]
    rows = _synthetic_raw_episode_rows(
        "Zero-Shot Baseline",
        ["adv-test-only-002", "adv-test-only-001"],
    )
    _rewrite_raw_output(
        artifact_path,
        "".join(json.dumps(row) + "\n" for row in rows),
    )
    output_dir = tmp_path / "output"

    with pytest.raises(ValueError, match="raw episode membership"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=output_dir,
            expected_adversarial_membership_sha256=membership_sha256,
        )

    assert not output_dir.exists()


@pytest.mark.parametrize("partition", REQUIRED_PARTITIONS)
@pytest.mark.parametrize("corruption", ["missing", "mismatch"])
def test_full_suite_requires_artifact_split_hash_for_each_partition(
    tmp_path, partition, corruption
):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    artifact_path = tmp_path / "artifacts" / f"0-{partition}-1.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if corruption == "missing":
        artifact.pop("split_sha256")
    else:
        artifact["split_sha256"] = "0" * 64
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    output_dir = tmp_path / "output"

    with pytest.raises(ValueError, match="split_sha256"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=output_dir,
            expected_adversarial_membership_sha256=membership_sha256,
        )

    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("variant_index", "marker_name", "marker_value"),
    [
        ("0", "evaluation_mode", "mock"),
        ("0", "non_empirical", True),
        ("0", "test_only_synthetic_fixture", True),
        ("3", "evaluation_mode", "NON_EMPIRICAL"),
        ("3", "non_empirical", True),
        ("3", "test_only_synthetic_fixture", True),
    ],
)
def test_full_suite_rejects_non_empirical_raw_markers(
    tmp_path, variant_index, marker_name, marker_value
):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    artifact_path = tmp_path / "artifacts" / f"{variant_index}-val-1.json"
    artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    raw_path = Path(
        artifact_payload.get("raw_predictions_path")
        or artifact_payload["raw_trajectory_path"]
    )
    records = [
        json.loads(line)
        for line in raw_path.read_text(encoding="utf-8").splitlines()
    ]
    records[0][marker_name] = marker_value
    _rewrite_raw_output(
        artifact_path,
        "".join(json.dumps(record) + "\n" for record in records),
    )
    output_dir = tmp_path / "output"

    with pytest.raises(ValueError, match="non-empirical marker"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=output_dir,
            expected_adversarial_membership_sha256=membership_sha256,
        )

    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("marker_name", "marker_value"),
    [
        ("test_only_synthetic_fixture", "true"),
        ("test_only_synthetic_fixture", False),
        ("non_empirical", 1),
        ("non_empirical", "false"),
        ("empirical_claim_allowed", False),
        ("empirical_claim_allowed", 1),
        ("mock_eval", "false"),
        ("evaluation_mode", "experimental"),
        ("evaluation_mode", True),
    ],
)
def test_full_suite_rejects_malformed_raw_evidence_markers(
    tmp_path, marker_name, marker_value
):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    artifact_path = tmp_path / "artifacts" / "0-val-1.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    raw_path = Path(payload["raw_predictions_path"])
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    rows[0][marker_name] = marker_value
    _rewrite_raw_output(
        artifact_path,
        "".join(json.dumps(row) + "\n" for row in rows),
    )

    with pytest.raises(ValueError, match="marker|evaluation_mode"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=membership_sha256,
        )
    assert not (tmp_path / "output").exists()


def test_full_suite_requires_empirical_mode_on_episode_rows(tmp_path):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    artifact_path = tmp_path / "artifacts" / "0-val-1.json"
    rows = _synthetic_raw_episode_rows("Zero-Shot Baseline", list(get_split("val")))
    rows[0].pop("evaluation_mode")
    _rewrite_raw_output(
        artifact_path,
        "".join(json.dumps(row) + "\n" for row in rows),
    )
    output_dir = tmp_path / "output"

    with pytest.raises(ValueError, match="evaluation_mode='empirical'"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=output_dir,
            expected_adversarial_membership_sha256=membership_sha256,
        )

    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("raw_content", "message"),
    [
        (
            '{"scenario_id":"single_fault/sf-006","scenario_id":"single_fault/sf-006"}\n',
            "duplicate field",
        ),
        ("{malformed json}\n", "malformed JSONL"),
        ('{"scenario_id":"single_fault/sf-006","value":NaN}\n', "non-finite"),
        ("[" * 1200 + "0" + "]" * 1200 + "\n", "nesting limit"),
    ],
    ids=["duplicate-key", "malformed-json", "non-finite", "deeply-nested"],
)
def test_full_suite_rejects_malformed_raw_episode_jsonl(
    tmp_path, raw_content, message
):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    artifact_path = tmp_path / "artifacts" / "0-val-1.json"
    _rewrite_raw_output(artifact_path, raw_content)
    with pytest.raises(ValueError, match=message):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=membership_sha256,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_run_started", "duplicate run_started"),
        ("interrupt", "interrupted"),
        ("missing_start", "without an active episode"),
        ("duplicate_start", "start"),
        ("out_of_order_start", "membership mismatch"),
        ("out_of_order_terminal", "without an active episode"),
        ("duplicate_terminal", "without an active episode"),
        ("missing_terminal", "run_completed occurred before the episode terminal"),
        ("mismatched_result", "result scenario_id"),
        ("cross_scenario", "cross-scenario"),
        ("missing_run_complete", "run_completed"),
    ],
)
def test_full_suite_rejects_invalid_g9_event_lifecycle(
    tmp_path, mutation, message
):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    artifact_path = tmp_path / "artifacts" / "3-val-1.json"
    artifact_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in Path(
            artifact_payload.get("raw_trajectory_path")
            or artifact_payload["raw_predictions_path"]
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    first_start = next(
        i for i, event in enumerate(events) if event["event"] == "episode_started"
    )
    first_terminal = next(
        i for i, event in enumerate(events) if event["event"] == "episode_completed"
    )
    if mutation == "duplicate_run_started":
        events.insert(1, dict(events[0]))
    elif mutation == "interrupt":
        events[first_terminal]["event"] = "episode_interrupted"
    elif mutation == "missing_start":
        events.pop(first_start)
    elif mutation == "duplicate_start":
        events.insert(first_start + 1, dict(events[first_start]))
    elif mutation == "out_of_order_start":
        events[first_start]["scenario_id"] = "single_fault/sf-007"
    elif mutation == "out_of_order_terminal":
        event = events.pop(first_terminal)
        events.insert(first_start, event)
    elif mutation == "duplicate_terminal":
        events.insert(first_terminal + 1, dict(events[first_terminal]))
    elif mutation == "missing_terminal":
        last_terminal = max(
            i for i, event in enumerate(events) if event["event"] == "episode_completed"
        )
        events.pop(last_terminal)
    elif mutation == "mismatched_result":
        events[first_terminal]["result"]["scenario_id"] = "single_fault/sf-007"
    elif mutation == "cross_scenario":
        policy_output = next(event for event in events if event["event"] == "policy_output")
        policy_output["scenario_id"] = "single_fault/sf-007"
    else:
        events.pop()
    _rewrite_raw_output(
        artifact_path,
        "".join(json.dumps(event) + "\n" for event in events),
    )

    with pytest.raises(ValueError, match=message):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=membership_sha256,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    ("location", "field", "replacement"),
    [
        ("run_started", "evaluator_source", {"git_sha": "c" * 40, "git_dirty": False}),
        ("run_started", "provenance", {"base_model": {"id": "other-model"}}),
        ("run_completed", "run_id", "another-run"),
        ("run_completed", "run_id", None),
        ("run_completed", "model", "other-model"),
        ("run_completed", "evaluator_source", {"git_sha": "c" * 40, "git_dirty": False}),
        ("run_completed", "provenance", {"base_model": {"id": "other-model"}}),
        ("artifact", "model", "other-model"),
    ],
)
def test_full_suite_rejects_g9_raw_artifact_identity_mismatch(
    tmp_path, location, field, replacement
):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    artifact_path = tmp_path / "artifacts" / "3-val-1.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if location == "artifact":
        artifact[field] = replacement
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    else:
        events = [
            json.loads(line)
            for line in Path(artifact["raw_trajectory_path"])
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        target = events[0] if location == "run_started" else events[-1]["summary"]
        target[field] = replacement
        _rewrite_raw_output(
            artifact_path,
            "".join(json.dumps(event) + "\n" for event in events),
        )

    with pytest.raises(ValueError, match="G9 raw artifact identity mismatch"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=membership_sha256,
        )
    assert not (tmp_path / "output").exists()


def test_full_suite_rejects_g9_nested_bool_number_identity_mismatch(tmp_path):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    artifact_path = tmp_path / "artifacts" / "3-val-1.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in Path(artifact["raw_trajectory_path"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    events[0]["provenance"]["live_execution"]["execute_live_chaos"] = 1
    events[-1]["summary"]["provenance"]["live_execution"]["execute_live_chaos"] = 1
    _rewrite_raw_output(
        artifact_path,
        "".join(json.dumps(event) + "\n" for event in events),
    )

    with pytest.raises(ValueError, match="G9 raw artifact identity mismatch"):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=membership_sha256,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    ("limit_name", "limit", "message"),
    [
        ("MAX_RAW_OUTPUT_BYTES", 16, "exceeds 16 bytes"),
        ("MAX_RAW_OUTPUT_LINE_BYTES", 16, "line 1 exceeds 16 bytes"),
        ("MAX_RAW_OUTPUT_RECORDS", 1, "exceeds 1 records"),
    ],
)
def test_full_suite_bounds_raw_jsonl_processing(
    tmp_path, monkeypatch, limit_name, limit, message
):
    manifest, _, membership_sha256, _ = _write_full_suite_inputs(tmp_path)
    monkeypatch.setattr(episode_membership, limit_name, limit)

    with pytest.raises(ValueError, match=message):
        run_full_ablation_suite(
            manifest_path=manifest,
            output_dir=tmp_path / "output",
            expected_adversarial_membership_sha256=membership_sha256,
        )
    assert not (tmp_path / "output").exists()


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
