"""Tests for Stage 12: Integrate GAI + RS + RL (Gate G12).

Validates:
1. Complete Multi-Agent Pipeline Dataflow: Alert -> Triage -> Diagnosis -> RS -> Remediation -> Verification -> Comms.
2. Coordinator correctly invokes HybridRecommender between Diagnosis and Remediation.
3. Remediation Agent receives ranked candidate runbooks with suggested tools and executable actions.
4. Full incident trajectory captures recommender telemetry.
5. Recommender failure fail-open resilience.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from agents.coordinator import handle_incident


class TestStage12IntegratedPipeline:
    @pytest.fixture(autouse=True)
    def setup_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-placeholder-audit-secret")
        monkeypatch.setenv("ATLASOPS_LIVE_JUDGE", "0")
        monkeypatch.setenv("TRAJECTORIES_DIR", str(tmp_path / "trajectories"))

    @pytest.mark.asyncio
    async def test_coordinator_handle_incident_invokes_recommender(self, monkeypatch):
        import agents.coordinator as coord

        alert = {
            "commonLabels": {"alertname": "KubeMemoryOvercommit"},
            "alerts": [{"labels": {"alertname": "KubeMemoryOvercommit", "service": "frontend"}}],
            "tier": "single_fault",
        }

        mock_triage_final = {
            "incident_id": "test-inc-1",
            "title": "High Memory Usage on Frontend",
            "severity": "P3",  # P3 = auto approval mode
            "affected_services": ["frontend"],
        }
        mock_diagnosis_final = {
            "incident_id": "test-inc-1",
            "root_cause": "OOMKilled container memory limit exceeded 137",
            "confidence": 0.95,
        }
        mock_remediation_final = {
            "incident_id": "test-inc-1",
            "status": "success",
            "executed_actions": [{"tool": "k8s_delete_pod", "args": {"pod": "frontend-123"}}],
            "outcome": "resolved",
        }
        mock_comms_final = {
            "incident_id": "test-inc-1",
            "summary": "Incident resolved via pod restart.",
        }

        async def fake_call_agent(role: str, user_input: dict, max_turns: int = 10):
            if role == "triage":
                return {"role": "triage", "trajectory": [], "final": mock_triage_final}
            elif role == "diagnosis":
                return {"role": "diagnosis", "trajectory": [], "final": mock_diagnosis_final}
            elif role == "remediation":
                # Verify that Remediation Agent received recommended runbooks from Stage 12 RS step!
                assert "recommended_runbooks" in user_input
                assert len(user_input["recommended_runbooks"]) > 0
                top_rb = user_input["recommended_runbooks"][0]
                assert top_rb["runbook_id"] == "RB-POD-OOM"
                assert "suggested_tools" in top_rb
                assert "actions" in top_rb
                return {"role": "remediation", "trajectory": [], "final": mock_remediation_final}
            elif role == "comms":
                return {"role": "comms", "trajectory": [], "final": mock_comms_final}
            return {"role": role, "trajectory": [], "final": {}}

        mock_verifier = MagicMock()
        mock_verifier.env_resolved = True
        mock_verifier.verification_status = "verified"
        mock_verifier.to_dict.return_value = {"status": "verified", "env_resolved": True}

        monkeypatch.setattr(coord, "call_agent", fake_call_agent)
        monkeypatch.setattr("agents.verifier.verify_environment", lambda **kwargs: mock_verifier)

        async def fake_settle(**kwargs):
            return {"status": "settled"}
        monkeypatch.setattr(coord, "settle_environment", fake_settle)

        result = await handle_incident(alert, scenario_id="single_fault/pod_memory_limit")

        assert result["incident_id"] is not None
        assert result["env_resolved"] is True
        assert "recommender" in result
        assert "recommended_runbooks" in result["recommender"]
        assert len(result["recommender"]["recommended_runbooks"]) == 3
        assert result["recommender"]["recommended_runbooks"][0]["runbook_id"] == "RB-POD-OOM"

    @pytest.mark.asyncio
    async def test_recommender_fails_open_gracefully(self, monkeypatch):
        import agents.coordinator as coord

        alert = {
            "commonLabels": {"alertname": "UnknownAlert"},
            "alerts": [],
        }

        mock_triage_final = {
            "incident_id": "test-inc-2",
            "title": "Unknown Failure",
            "severity": "P3",
            "affected_services": [],
        }

        async def fake_call_agent(role: str, user_input: dict, max_turns: int = 10):
            if role == "triage":
                return {"role": "triage", "trajectory": [], "final": mock_triage_final}
            if role == "remediation":
                assert "recommended_runbooks" in user_input
            return {"role": role, "trajectory": [], "final": {"status": "ok"}}

        mock_verifier = MagicMock()
        mock_verifier.env_resolved = False
        mock_verifier.verification_status = "unverified"
        mock_verifier.to_dict.return_value = {"status": "unverified", "env_resolved": False}

        monkeypatch.setattr(coord, "call_agent", fake_call_agent)
        monkeypatch.setattr("agents.verifier.verify_environment", lambda **kwargs: mock_verifier)

        async def fake_settle(**kwargs):
            return {"status": "settled"}
        monkeypatch.setattr(coord, "settle_environment", fake_settle)

        with patch("recommender.hybrid.HybridRecommender.recommend_runbooks", side_effect=RuntimeError("Recommender DB Offline")):
            result = await handle_incident(alert)
            assert result is not None
            assert "recommender" in result
