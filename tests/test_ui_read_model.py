"""Truth and path-safety contracts for the read-only product projection."""

import pytest
from fastapi.testclient import TestClient

from app import app
from ui_read_model import attempt_detail, attempts, catalog, gates


def test_governance_snapshot_is_complete_and_preserves_negative_results():
    rows = gates()
    assert [row["gate"] for row in rows] == [f"G{i}" for i in range(16)]
    assert rows[4]["status"] == "NOT_PASSED"
    assert rows[9]["status"] == "REOPENED"
    assert rows[13]["status"] == "REOPENED"
    assert "EMPIRICAL EVIDENCE MISSING" in rows[6]["status"]


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


@pytest.mark.parametrize("name", [
    "../EXP-STAGE4-SF002-010.json",
    "EXP-STAGE4-SF002-010.cleanup.json",
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
    assert any(item["classification"] == "Mock evidence" for item in data["evidence"])
    assert any(item["classification"] == "Predetermined historical output"
               for item in data["evidence"])
    assert {item["area"] for item in data["evidence"]} == {
        "Environment", "Model evaluations", "Training provenance",
        "Recommender", "Ablation", "Submission",
    }
    assert any(item["classification"] == "NOT_CERTIFIED inventory"
               for item in data["evidence"])


def test_ui_endpoints_are_read_only_and_reject_unknown_attempts():
    client = TestClient(app)
    assert client.get("/ui/catalog").status_code == 200
    detail = client.get("/ui/attempts/EXP-STAGE4-SF002-010.json")
    assert detail.status_code == 200
    assert detail.json()["state"] == "Not passed"
    assert client.get("/ui/attempts/EXP-STAGE4-SF002-010.cleanup.json").status_code == 404
