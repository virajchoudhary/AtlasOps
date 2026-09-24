"""Stage 11 outputs retain the exact synthetic data and split identity."""

from __future__ import annotations

import json

import pytest

from config.splits import get_split
from recommender.dataset import IncidentInteraction
from recommender.train_hybrid import train_and_evaluate_hybrid


def _interaction(scenario_id: str, split: str) -> IncidentInteraction:
    return IncidentInteraction(
        interaction_id="interaction-1",
        incident_id="incident-1",
        scenario_id=scenario_id,
        split=split,
        tier="single_fault",
        alertname="HighCPUUsage",
        affected_services=["paymentservice"],
        symptoms_text="Observed CPU saturation",
        relevant_runbook_id="RB-CPU-THROTTLE",
        rating=1.0,
    )


def test_stage11_evidence_binds_synthetic_rows_and_checkpoint(tmp_path):
    row = _interaction(get_split("train")[0], "train")
    model_path = tmp_path / "hybrid.json"
    evidence_path = tmp_path / "evaluation.json"
    _, evidence = train_and_evaluate_hybrid(
        interactions=[row],
        output_model_path=model_path,
        output_evidence_path=evidence_path,
    )

    assert model_path.is_file()
    assert json.loads(evidence_path.read_text(encoding="utf-8")) == evidence
    provenance = evidence["dataset_provenance"]
    assert provenance["origin"] == "synthetic_scenario_derived"
    assert provenance["historical_interaction_feedback"] is False
    assert provenance["included_scenario_ids_by_split"]["train"] == [row.scenario_id]
    assert len(provenance["interaction_rows_sha256"]) == 64
    assert len(evidence["checkpoint_sha256"]) == 64


def test_stage11_rejects_split_mismatch_before_output(tmp_path):
    row = _interaction(get_split("test")[0], "train")
    model_path = tmp_path / "hybrid.json"
    with pytest.raises(ValueError, match="outside its frozen split"):
        train_and_evaluate_hybrid(
            interactions=[row],
            output_model_path=model_path,
            output_evidence_path=tmp_path / "evaluation.json",
        )
    assert not model_path.exists()
