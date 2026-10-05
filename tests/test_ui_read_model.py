"""Truth and path-safety contracts for the read-only product projection."""

import pytest
from fastapi.testclient import TestClient

import ui_read_model
from app import app
from ui_read_model import attempt_detail, attempts, catalog, gates


def test_governance_snapshot_is_complete_and_preserves_negative_results():
    rows = gates()
    assert [row["gate"] for row in rows] == [f"G{i}" for i in range(16)]
    assert rows[4]["status"] == "NOT_PASSED"
    assert rows[9]["status"] == "NOT_PASSED"
    assert rows[13]["status"] == "REOPENED"
    assert "EMPIRICAL EVIDENCE MISSING" in rows[6]["status"]
    assert rows[14]["status"] == "PARTIAL"
    assert rows[4]["status_note"]


def test_attempt_projection_keeps_failure_and_provenance_visible():
    records = attempts()
    assert any(row["name"] == "EXP-STAGE4-SF002-010.json" for row in records)
    detail = attempt_detail("EXP-STAGE4-SF002-010.json")
    assert detail["governance"] == "G4 NOT_PASSED"
    assert detail["state"] == "Not passed"
    assert detail["triage"]["frozen_targets"] == ["paymentservice"]
    assert detail["triage"]["services"] == ["adservice"]
    assert detail["approval"] == "timeout"
    assert detail["verification"]["env_resolved"] is False
    assert any(check["name"] == "chaos_mesh_cleared" and not check["passed"]
               for check in detail["verification"]["checks"])
    assert any("Safety finding" in warning for warning in detail["warnings"])
    assert len(detail["sha256"]) == 64
    assert "stdout" not in str(detail)

    interrupted = attempt_detail("EXP-STAGE4-SF002-014.interruption.json")
    assert interrupted["state"] == "Interrupted"
    assert interrupted["verification"]["env_resolved"] is not True
    assert interrupted["verification"]["checks"] == []


def test_current_result_projection_uses_current_evidence_and_keeps_nulls():
    data = catalog()["current_results"]
    sft = data["sft_v17"]
    assert sft["status"] == "PRESERVED; INDEPENDENT RELOAD VERIFIED"
    assert sft["training_steps"] == 9
    assert sft["corpus_rows"] == 68
    assert sft["adapter_sha256"] == (
        "f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff"
    )
    assert sft["reload_tensor_count"] == 392
    assert sft["incident_improvement_claim"] is False

    validation = data["base_vs_sft"]
    assert validation["base_f1"] == 0.16875
    assert validation["sft_f1"] == 0.15935
    assert validation["paired_delta"] == -0.0094
    assert (validation["base_schema_valid"], validation["base_schema_total"]) == (6, 6)
    assert (validation["sft_schema_valid"], validation["sft_schema_total"]) == (6, 6)
    assert validation["resolution_evaluated"] is False
    assert validation["resolution_rate"] is None
    assert validation["safety_result"] is None
    assert validation["avg_reward"] is None
    assert validation["avg_time_to_resolve_s"] is None

    pilot = data["g9_pilot"]
    assert pilot["status"] == "FINAL NEGATIVE / FROZEN"
    assert (pilot["optimizer_steps"], pilot["completions"]) == (2, 4)
    assert (pilot["malformed_or_blocked"], pilot["reward_each"]) == (4, -1)
    assert pilot["zero_advantage_groups"] == 2
    assert pilot["acceptable_checkpoint"] is False
    assert pilot["archive_sha256"] == (
        "39834cc3911bf6e32deec509a1c139d905e17aee802563abcb2ce3802f23cd33"
    )

    aligned = data["g9_aligned_diagnostic"]
    assert (aligned["admissible_count"], aligned["sample_count"]) == (0, 8)
    assert aligned["optimizer_steps"] == 0
    assert aligned["tensor_hash_count"] == 392
    assert aligned["tensor_hashes_unchanged"] is True
    assert aligned["population_probability"] is None


def test_final_g4_chronology_shows_missing_raw_records_without_filling_metrics():
    rows = catalog()["current_results"]["g4_attempts"]
    assert [row["attempt"] for row in rows] == ["015", "016", "017"]
    assert rows[0]["state"] == "Terminal INCONCLUSIVE / UNSCORED"
    assert rows[1]["state"] == "PRE-FAULT ABORT / NON-RESULT"
    assert rows[2]["state"] == "COMPLETED NEGATIVE"
    assert rows[0]["source"]["available"] is True
    assert rows[1]["source"]["path"] == "docs/project/CONTROLLED_G9_ADMISSION_V1.md"
    assert rows[2]["source_note"].endswith("intentionally not republished.")
    assert all(row["raw_record_available"] is False for row in rows)
    assert all(row["resolution"] is None for row in rows)
    assert all(row["reward"] is None for row in rows)
    assert all(row["time_to_resolve_s"] is None for row in rows)


def test_malformed_nested_evidence_projects_unavailable_without_crashing(monkeypatch):
    records = {
        ui_read_model.SFT_RESULT_PATH: {
            "schema_version": "atlasops-sft-pilot-completion-v1",
            "status": "REAL_SFT_ADAPTER_PRESERVED_AND_INDEPENDENT_RELOAD_VERIFIED",
            "run_id": "malformed",
            "training": "not-an-object",
            "corpus": [],
            "adapter": None,
            "independent_reload": ["not-an-object"],
        },
        ui_read_model.SFT_RELOAD_PATH: ["not-an-object"],
        ui_read_model.BASE_SFT_SUMMARY_PATH: {
            "schema_version": "atlasops-base-sft-validation-summary-v1",
            "lifecycle": "completed",
            "per_arm": ["not-an-object"],
        },
        ui_read_model.G9_ALIGNED_DIAGNOSTIC_PATH: {
            "interface": "controlled-qwen-single-tool-call-v2",
            "sample_count": "8",
            "admissible_count": float("nan"),
            "optimizer_steps": [],
            "tensor_hashes_before": ["not-a-hash-map"],
            "tensor_hashes_after": {},
        },
    }
    monkeypatch.setattr(ui_read_model, "_read_json", lambda path: records.get(path))
    monkeypatch.setattr(ui_read_model, "_read_text", lambda _path: "unrelated non-empty text")

    result = ui_read_model.current_results()
    assert result["sft_v17"]["status"] == "UNAVAILABLE"
    assert result["sft_v17"]["training_steps"] is None
    assert result["sft_v17"]["corpus_rows"] is None
    assert result["sft_v17"]["adapter_sha256"] is None
    assert result["base_vs_sft"]["available"] is False
    assert result["base_vs_sft"]["base_f1"] is None
    assert result["base_vs_sft"]["paired_delta"] is None
    assert result["g9_pilot"]["status"] == "UNAVAILABLE"
    assert result["g9_pilot"]["optimizer_steps"] is None
    assert result["g9_aligned_diagnostic"]["available"] is False
    assert result["g9_aligned_diagnostic"]["admissible_count"] is None
    assert result["g9_aligned_diagnostic"]["tensor_hash_count"] is None
    assert all(row["state"] == "UNAVAILABLE" for row in result["g4_attempts"])


def test_nonfinite_metrics_are_not_rendered_as_results(monkeypatch):
    summary = {
        "schema_version": "atlasops-base-sft-validation-summary-v1",
        "lifecycle": "completed",
        "run_id": "non-finite",
        "per_arm": {
            "base": {"avg_diagnostic_f1": float("nan"), "format_compliant_count": 6,
                     "scheduled_count": 6},
            "sft": {"avg_diagnostic_f1": float("inf"), "format_compliant_count": 6,
                    "scheduled_count": 6},
        },
    }
    monkeypatch.setattr(
        ui_read_model, "_read_json",
        lambda path: summary if path == ui_read_model.BASE_SFT_SUMMARY_PATH else None,
    )
    monkeypatch.setattr(ui_read_model, "_read_text", lambda _path: None)

    result = ui_read_model.current_results()["base_vs_sft"]
    assert result["available"] is True
    assert result["base_f1"] is None
    assert result["sft_f1"] is None
    assert result["paired_delta"] is None
    assert ui_read_model._number(float("nan")) is None
    assert ui_read_model._number(float("inf")) is None
    assert ui_read_model._number(10 ** 1000) is None


@pytest.mark.parametrize("name", [
    "../EXP-STAGE4-SF002-010.json",
    "EXP-STAGE4-SF002-010.cleanup.json",
    "EXP-STAGE4-SF002-015.attempt.json",
    "EXP-STAGE4-SF002-016.json",
    "EXP-STAGE4-SF002-017.json",
    "EXP-STAGE4-SF002-999.json",
])
def test_attempt_path_is_allowlisted(name):
    with pytest.raises(FileNotFoundError):
        attempt_detail(name)


def test_catalog_labels_historical_and_synthetic_evidence():
    data = catalog()
    assert len(data["runbooks"]) == 12
    assert data["source_sha"] is None or len(data["source_sha"]) > 0
    assert all(item["sha256"] for item in data["evidence"])
    assert any(item["path"] == "artifacts/evidence/stage7/free-t4-v17/RESULT.json"
               for item in data["evidence"])
    assert all("mock_archive" not in item["path"] for item in data["evidence"])
    assert {item["area"] for item in data["evidence"]} == {
        "SFT", "Validation", "GRPO", "G4",
    }
    assert all("NON-EMPIRICAL" in item["classification"]
               for item in data["historical_archive"][:2])
    assert all("contents are not loaded" in item["details"]
               for item in data["historical_archive"][:1])


def test_ui_endpoints_are_read_only_and_reject_unknown_attempts():
    client = TestClient(app)
    assert client.get("/ui/catalog").status_code == 200
    status_catalog = client.get("/ui/catalog").json()
    assert status_catalog["gates"][4]["status"] == "NOT_PASSED"
    assert "status_note" in status_catalog["gates"][4]
    detail = client.get("/ui/attempts/EXP-STAGE4-SF002-010.json")
    assert detail.status_code == 200
    assert detail.json()["state"] == "Not passed"
    current = client.get("/ui/catalog").json()["current_results"]
    assert current["base_vs_sft"]["paired_delta"] == -0.0094
    assert current["g9_aligned_diagnostic"]["tensor_hash_count"] == 392
    assert client.get("/ui/attempts/EXP-STAGE4-SF002-017.json").status_code == 404
    assert client.get("/ui/attempts/EXP-STAGE4-SF002-010.cleanup.json").status_code == 404
