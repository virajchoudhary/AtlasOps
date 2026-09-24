"""Local, non-empirical dataset provenance and output-isolation contracts."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from config.scenario_catalog import ScenarioMetadata
from config.splits import TEST_SPLIT, TRAIN_SPLIT, VAL_SPLIT
from recommender import dataset
from recommender.runbook_catalog import RUNBOOK_CATALOG

UNSUPPORTED_CATALOG_IDS = {
    "multi_fault/mf-001",
    "multi_fault/mf-002",
    "multi_fault/mf-003",
    "multi_fault/mf-004",
    "multi_fault/mf-005",
    "named_replays/hist-github-2018",
    "single_fault/sf-008",
}


def test_custom_output_preserves_canonical_manifest(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    sentinel = canonical / "rs_dataset_manifest.json"
    sentinel.write_bytes(b"historical evidence\n")
    monkeypatch.setattr(dataset, "EVIDENCE_DIR", canonical)
    output = tmp_path / "custom" / "interactions.jsonl"

    _, manifest = dataset.build_incident_interactions(output_path=output)

    assert sentinel.read_bytes() == b"historical evidence\n"
    sidecar = output.with_suffix(".manifest.json")
    assert json.loads(sidecar.read_text(encoding="utf-8")) == manifest


def test_custom_output_cannot_alias_canonical_manifest(tmp_path, monkeypatch):
    evidence = tmp_path / "canonical"
    evidence.mkdir()
    sentinel = evidence / "rs_dataset_manifest.json"
    sentinel.write_bytes(b"historical evidence\n")
    monkeypatch.setattr(dataset, "EVIDENCE_DIR", evidence)

    with pytest.raises(ValueError, match="manifest cannot be used as a dataset output"):
        dataset.build_incident_interactions(output_path=sentinel)

    assert sentinel.read_bytes() == b"historical evidence\n"


def test_default_output_retains_canonical_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(dataset, "EVIDENCE_DIR", tmp_path / "evidence")
    _, manifest = dataset.build_incident_interactions()
    saved = dataset.EVIDENCE_DIR / "rs_dataset_manifest.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == manifest


def test_manifest_identifies_synthetic_source_and_exact_generator(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset, "EVIDENCE_DIR", tmp_path / "evidence")
    output, manifest = dataset.build_incident_interactions(
        output_path=tmp_path / "interactions.jsonl"
    )
    assert manifest["data_origin"] == "scenario_derived_synthetic_benchmark"
    assert manifest["historical_user_feedback"] is False
    assert manifest["runtime_query_fields"] == [
        "alertname",
        "affected_services",
        "symptoms_text",
    ]
    assert "tier is scenario/split metadata only" in manifest["runtime_feature_provenance"]
    assert "expected_root_cause is used only for the offline benchmark label" in manifest[
        "runtime_feature_provenance"
    ]
    assert manifest["generator_source_sha256"] == hashlib.sha256(
        Path(dataset.__file__).read_bytes()
    ).hexdigest()
    assert manifest["canonical_lf_sha256"] == hashlib.sha256(
        output.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()


def test_symptoms_do_not_leak_expected_root_cause(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset, "EVIDENCE_DIR", tmp_path / "evidence")
    sid = "single_fault/sf-003"
    variant_a = dict(dataset.SCENARIO_CATALOG)
    variant_a[sid] = replace(
        variant_a[sid], expected_root_cause="SENTINEL-A-memory exhaustion"
    )
    variant_b = dict(dataset.SCENARIO_CATALOG)
    variant_b[sid] = replace(
        variant_b[sid], expected_root_cause="SENTINEL-B-CPU saturation"
    )

    output_a, _ = dataset.build_incident_interactions(
        scenarios=variant_a, output_path=tmp_path / "a.jsonl"
    )
    output_b, _ = dataset.build_incident_interactions(
        scenarios=variant_b, output_path=tmp_path / "b.jsonl"
    )
    records_a = {
        record["scenario_id"]: record
        for record in (json.loads(line) for line in output_a.read_text(encoding="utf-8").splitlines())
    }
    records_b = {
        record["scenario_id"]: record
        for record in (json.loads(line) for line in output_b.read_text(encoding="utf-8").splitlines())
    }

    assert set(records_a) == set(records_b) == set(dataset.SCENARIO_CATALOG) - UNSUPPORTED_CATALOG_IDS
    runtime_fields = dataset.RUNTIME_QUERY_FIELDS
    features_a = {field: records_a[sid][field] for field in runtime_fields}
    features_b = {field: records_b[sid][field] for field in runtime_fields}
    assert features_a == features_b
    assert records_a[sid]["relevant_runbook_id"] == "RB-POD-OOM"
    assert records_b[sid]["relevant_runbook_id"] == "RB-CPU-THROTTLE"
    for output in (output_a, output_b):
        serialized = output.read_text(encoding="utf-8")
        assert "expected_root_cause" not in serialized
        assert "SENTINEL-A-" not in serialized
        assert "SENTINEL-B-" not in serialized
    assert records_a[sid]["tier"] == records_b[sid]["tier"] == "single_fault"
    assert "Tier:" not in records_a[sid]["symptoms_text"]
    assert "tier" not in dataset.RUNTIME_QUERY_FIELDS


def test_every_scenario_resolves_to_a_valid_runbook():
    unsupported = []
    for sid, meta in dataset.SCENARIO_CATALOG.items():
        try:
            rb_id = dataset.resolve_runbook_for_scenario(sid, meta)
        except ValueError:
            unsupported.append(sid)
        else:
            assert rb_id in RUNBOOK_CATALOG, f"{sid} resolved to unknown runbook {rb_id}"
    assert set(unsupported) == UNSUPPORTED_CATALOG_IDS


def test_unknown_chaos_shape_requires_explicit_mapping():
    meta = ScenarioMetadata(
        scenario_id="audit/unsupported-kind",
        tier="single_fault",
        manifest_relpath="audit.json",
        manifest_sha256="0" * 64,
        doc_count=1,
        chaos_kinds=("NovelChaos",),
        target_services=("frontend",),
        expected_alert="NovelAlert",
        expected_root_cause="Should not be exposed to the mapping contract.",
        verification_workloads=(),
    )
    with pytest.raises(ValueError, match="No catalog runbook can be derived"):
        dataset.resolve_runbook_for_scenario("audit/unsupported-kind", meta)


def test_generic_label_rules_use_cause_semantics_not_scenario_ids():
    cases = {
        "CPU saturation from sustained stress": "RB-CPU-THROTTLE",
        "Memory stress causing pressure": "RB-POD-OOM",
        "Network latency and jitter": "RB-NET-DELAY",
        "Packet corruption and checksum failures": "RB-NET-CORRUPT",
        "Filesystem error and full condition": "RB-DISK-FILL",
        "DNS name resolution failure": "RB-DNS-FAIL",
        "Pod kill causing container restarts": "RB-POD-CRASH",
    }
    for index, (cause, expected) in enumerate(cases.items(), start=1):
        meta = ScenarioMetadata(
            scenario_id=f"audit/generic-{index}",
            tier="single_fault",
            manifest_relpath="audit.json",
            manifest_sha256="0" * 64,
            doc_count=1,
            chaos_kinds=("UninformativeChaosKind",),
            target_services=("frontend",),
            expected_alert="GenericAlert",
            expected_root_cause=cause,
            verification_workloads=(),
        )
        assert dataset.resolve_runbook_for_scenario("different/scenario-name", meta) == expected


def test_multifault_requires_evidence_of_a_cascade_for_single_labeling():
    meta = replace(
        dataset.SCENARIO_CATALOG["multi_fault/mf-005"],
        scenario_id="audit/multi-fault",
        tier="multi_fault",
    )

    with pytest.raises(ValueError, match="No single relevant runbook is supported"):
        dataset.resolve_runbook_for_scenario(meta.scenario_id, meta)


def test_unsupported_causes_are_excluded_with_frozen_split_provenance(tmp_path):
    output = tmp_path / "interactions.jsonl"

    _, manifest = dataset.build_incident_interactions(output_path=output)

    assert manifest["source_scenario_count"] == 28
    expected_exclusions = sorted(UNSUPPORTED_CATALOG_IDS)
    assert manifest["total_interactions"] == 21
    assert manifest["excluded_scenario_count"] == len(expected_exclusions)
    assert sorted(manifest["excluded_scenarios"]) == expected_exclusions
    assert manifest["split_distribution"] == {"train": 12, "val": 5, "test": 4}
    assert manifest["frozen_split_scenario_ids"] == {
        "train": sorted(TRAIN_SPLIT),
        "val": sorted(VAL_SPLIT),
        "test": sorted(TEST_SPLIT),
    }
    for sid in expected_exclusions:
        split_name = (
            "train" if sid in TRAIN_SPLIT else "val" if sid in VAL_SPLIT else "test"
        )
        assert sid in manifest["source_split_scenario_ids"][split_name]
        assert sid not in manifest["included_split_scenario_ids"][split_name]
        assert manifest["source_manifest_sha256_by_scenario"][sid] == (
            dataset.SCENARIO_CATALOG[sid].manifest_sha256
        )


def test_scenarios_outside_frozen_splits_are_rejected_before_writing(tmp_path):
    meta = replace(
        dataset.SCENARIO_CATALOG["single_fault/sf-003"],
        scenario_id="audit/new-scenario",
    )
    output = tmp_path / "interactions.jsonl"

    with pytest.raises(ValueError, match="only accepts scenarios in the frozen"):
        dataset.build_incident_interactions(
            scenarios={meta.scenario_id: meta},
            output_path=output,
        )

    assert not output.exists()
