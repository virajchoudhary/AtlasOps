"""Automated contracts for the read-only product UI and existing API endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app


class TestFrontendUIAndAPIs:
    """Test suite verifying the operator console and API compatibility."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        self.client = TestClient(app)

    def test_static_index_html_structure_and_navigation(self):
        """The eight views use the incident-first shell, not the old simulation."""
        index_path = Path("static/index.html")
        assert index_path.exists(), "static/index.html must exist"
        content = index_path.read_text(encoding="utf-8")
        assert "<title>AtlasOps" in content
        assert all(f'data-page="{page}"' in content for page in (
            "overview", "incidents", "agents", "models", "evaluations",
            "runbooks", "evidence", "settings"
        ))
        assert 'id="global-status"' in content
        assert 'id="drawer"' in content
        assert 'id="quick-open"' in content
        assert 'id="palette-input"' in content
        assert 'id="sidebar-backdrop"' in content
        assert '/static/vendor/lucide.min.js' in content
        assert '/static/live-incident.js' in content
        assert Path("static/vendor/lucide.min.js").is_file()
        assert "ISC License" in Path("static/vendor/LUCIDE-LICENSE").read_text(encoding="utf-8")
        assert "runSimulation" not in content
        js = Path("static/console.js").read_text(encoding="utf-8")
        assert "G4 NOT_PASSED" in js
        assert "Historical evidence" in js
        assert "No active incidents" in js
        assert "No completed verdict" in js
        assert "/ui/attempts/" in js
        assert "Foundation & environment" in js
        assert "Training provenance" in js
        assert "data-gate" in js
        assert "processView(referenceSteps" in js
        assert "processView(recordedSteps(featured)" in js
        assert 'scenarios: "/api/scenarios"' in js
        assert 'featured: "/ui/attempts/EXP-STAGE4-SF002-010.json"' in js
        assert 'data-scenario' in js
        assert 'data-process-step' in js
        assert "Objective checks did not establish resolution." in js
        projection = Path("static/live-incident.js").read_text(encoding="utf-8")
        assert "approval_denied" in projection
        assert "Audit unavailable" in projection
        assert "No verdict exposed" in projection
        css = Path("static/console.css").read_text(encoding="utf-8")
        assert ".process-track" in css
        assert "prefers-reduced-motion: reduce" in css
        assert "/inject" not in js and "/reset" not in js
        assert "100.0%" not in js and "0.918" not in js

    def test_root_endpoint_serves_html(self):
        """Verify GET / returns HTTP 200 and serves HTML content."""
        response = self.client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "AtlasOps" in response.text
        assert self.client.get("/static/console.js").status_code == 200
        assert self.client.get("/static/console.css").status_code == 200
        assert self.client.get("/static/live-incident.js").status_code == 200

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
