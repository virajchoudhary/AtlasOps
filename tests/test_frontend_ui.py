"""Automated test suite for the modernized AtlasOps Frontend UI and API endpoints."""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app import app


class TestFrontendUIAndAPIs:
    """Test suite verifying the redesigned Mission Control web interface."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        self.client = TestClient(app)

    def test_static_index_html_structure_and_tabs(self):
        """Verify static/index.html exists and defines all 6 mission control tabs."""
        index_path = Path("static/index.html")
        assert index_path.exists(), "static/index.html must exist"
        content = index_path.read_text(encoding="utf-8")

        # Check document metadata and title
        assert "<title>AtlasOps" in content
        assert "neural-canvas" in content
        assert "grid-overlay" in content

        # Check all 6 tabs
        assert 'id="tab-ops"' in content
        assert 'id="tab-recommender"' in content
        assert 'id="tab-ablations"' in content
        assert 'id="tab-trajectories"' in content
        assert 'id="tab-replays"' in content
        assert 'id="tab-architecture"' in content

        # Check essential interactive elements
        assert 'id="scenario-select"' in content
        assert 'id="log-container"' in content
        assert 'id="postmortem-box"' in content
        assert 'id="rs-alert-select"' in content
        assert 'id="rs-service-select"' in content
        assert 'id="ablation-table"' in content

    def test_root_endpoint_serves_html(self):
        """Verify GET / returns HTTP 200 and serves HTML content."""
        response = self.client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "ATLAS" in response.text
        assert "Mission Control" in response.text

    def test_api_scenarios_endpoint(self):
        """Verify GET /api/scenarios returns the codified scenario catalog."""
        response = self.client.get("/api/scenarios")
        assert response.status_code == 200
        data = response.json()
        assert "scenarios" in data
        scenarios = data["scenarios"]
        assert len(scenarios) == 28
        scenario_ids = [s["scenario_id"] for s in scenarios]
        assert "single_fault/sf-001" in scenario_ids
        assert "cascade/cs-001" in scenario_ids

    def test_api_recommender_recommend_endpoint(self):
        """Verify POST /api/recommender/recommend executes hybrid recommendation."""
        payload = {
            "alert_name": "KubeMemoryOvercommit",
            "service": "frontend",
            "symptoms": "OOMKilled exit code 137 pod memory limit breached",
            "top_k": 3,
        }
        response = self.client.post("/api/recommender/recommend", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "recommendations" in data
        recs = data["recommendations"]
        assert len(recs) == 3
        rb_ids = [r["runbook_id"] for r in recs]
        assert "RB-POD-OOM" in rb_ids or "RB-POD-CRASH" in rb_ids
        assert recs[0]["score"] > 0.0
        assert len(recs[0]["suggested_tools"]) > 0

    def test_api_ablation_matrix_endpoint(self):
        """Verify GET /api/ablation-matrix returns multi-model benchmark results."""
        response = self.client.get("/api/ablation-matrix")
        assert response.status_code == 200
        data = response.json()
        assert "comparison_family" in data
        assert "partitions" in data
        assert "Full Pipeline (GAI + RS + RL)" in data["comparison_family"]
