"""Local, non-empirical dataset provenance and output-isolation contracts."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from recommender import dataset
from recommender.runbook_catalog import RUNBOOK_CATALOG


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
    assert manifest["generator_source_sha256"] == hashlib.sha256(
        Path(dataset.__file__).read_bytes()
    ).hexdigest()
    assert manifest["canonical_lf_sha256"] == hashlib.sha256(
        output.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()


def test_symptoms_do_not_leak_expected_root_cause(tmp_path, monkeypatch):
    monkeypatch.setattr(dataset, "EVIDENCE_DIR", tmp_path / "evidence")
    variant_a = {
        sid: replace(meta, expected_root_cause=f"SENTINEL-A-{sid}")
        for sid, meta in dataset.SCENARIO_CATALOG.items()
    }
    variant_b = {
        sid: replace(meta, expected_root_cause=f"SENTINEL-B-{sid}")
        for sid, meta in dataset.SCENARIO_CATALOG.items()
    }

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

    assert records_a.keys() == records_b.keys() == dataset.SCENARIO_CATALOG.keys()
    for sid in records_a:
        symptoms_a = records_a[sid]["symptoms_text"]
        symptoms_b = records_b[sid]["symptoms_text"]
        assert symptoms_a == symptoms_b, sid
        assert "SENTINEL-A-" not in symptoms_a
        assert "SENTINEL-B-" not in symptoms_b


def test_every_scenario_resolves_to_a_valid_runbook():
    for sid, meta in dataset.SCENARIO_CATALOG.items():
        rb_id = dataset.resolve_runbook_for_scenario(sid, meta)
        assert rb_id in RUNBOOK_CATALOG, f"{sid} resolved to unknown runbook {rb_id}"
