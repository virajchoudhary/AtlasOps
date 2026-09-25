"""Tests for Stage 14: Deploy Final Demo Safely (Gate G14).

Validates:
1. Gradio Ops Console tab construction and UI component registration.
2. Read-only scenario selection and cleanup guidance.
3. Interactive Hybrid Runbook Recommender UI querying.
4. Multi-model ablation matrix and benchmark loading.
5. Standalone demo launcher argument parsing and configuration.
"""

from __future__ import annotations

import pytest

from dashboard import (
    _apply_chaos,
    _list_stage4_attempts,
    _load_ablation_matrix,
    _load_comparison_table,
    _load_project_status,
    _load_stage4_attempt,
    _query_hybrid_recommender,
    _reset_chaos,
    build_app,
)
from demo.launcher import main as launcher_main


class TestStage14DemoSafety:
    def test_build_app_constructs_status_and_evidence_tabs(self):
        app = build_app()
        assert app is not None
        assert app.title == "AtlasOps Ops Console & Demo Interface"
        assert app.analytics_enabled is False
        assert len(app.blocks) > 0
        labels = {component.get("props", {}).get("label") for component in app.config["components"]}
        assert "🧭 Project Status" in labels
        assert "📋 Preserved G4 Evidence" in labels

    def test_scenario_selection_never_claims_or_runs_a_fault(self, monkeypatch):
        monkeypatch.setenv("DEMO_SAFE_MODE", "0")
        monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: pytest.fail("kubectl invoked"))
        res = _apply_chaos("single_fault/sf-001")
        assert "[READ-ONLY DEMO]" in res
        assert "No fault was injected" in res
        assert "no incident workflow ran" in res
        assert "Unknown scenario" in _apply_chaos("../../elsewhere")

        reset_res = _reset_chaos()
        assert "No cluster cleanup was performed" in reset_res

    def test_stage_status_and_preserved_negative_evidence(self):
        status = _load_project_status()
        assert "G4" in status and "NOT_PASSED" in status
        assert "G13" in status and "REOPENED" in status
        summary, provenance = _load_stage4_attempt("EXP-STAGE4-SF002-010.json")
        assert "NOT PASSED" in summary
        assert "timeout" in summary
        assert "Evidence inconsistency" in summary
        assert "Safety finding" in summary
        assert "Target drift" in summary
        assert "kubectl_rollout" in summary
        assert "SHA-256" in provenance

        interrupted, _ = _load_stage4_attempt("EXP-STAGE4-SF002-014.interruption.json")
        assert "INCONCLUSIVE" in interrupted
        assert "no completed verifier verdict" in interrupted
        invalid, _ = _load_stage4_attempt("../EXP-STAGE4-SF002-010.json")
        assert "Select a preserved" in invalid
        for attempt_name in _list_stage4_attempts():
            attempt_summary, source = _load_stage4_attempt(attempt_name)
            assert "Preserved attempt" in attempt_summary
            assert "SHA-256" in source

    def test_dashboard_recommender_query_interactive(self):
        res = _query_hybrid_recommender(
            alertname="KubeMemoryOvercommit",
            service="frontend",
            symptoms="OOMKilled memory limit exceeded",
            top_k=3,
        )
        assert "Top 3 Recommended Runbooks" in res
        assert "RB-POD-OOM" in res
        assert "Suggested Tools" in res
        assert "Ranking Score" in res
        assert "scenario-derived" in res

    def test_load_ablation_matrix_and_comparison_table(self):
        ablation_text = _load_ablation_matrix()
        assert "NON-EMPIRICAL" in ablation_text
        assert "AtlasOps Final Multi-Generation Ablation & Stress Matrix" in ablation_text or "results" in ablation_text
        assert "Zero-Shot Baseline" in ablation_text

        table_text = _load_comparison_table()
        assert len(table_text) > 0
        assert "UNVERIFIED" in table_text or "No verified" in table_text

    def test_demo_launcher_cli_configuration(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["launcher.py", "--port", "8080", "--host", "127.0.0.1"])
        import demo.launcher as dl
        monkeypatch.setattr(dl, "launch_demo", lambda **kwargs: kwargs)
        # Verify main executes and parses arguments
        launcher_main()
