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
    _UI_CSS,
    _apply_chaos,
    _comparison_html,
    _header_html,
    _list_stage4_attempts,
    _load_ablation_matrix,
    _load_comparison_table,
    _load_current_results,
    _load_g4_final_chronology,
    _load_project_status,
    _load_stage4_attempt,
    _load_static_runbooks,
    _reset_chaos,
    build_app,
)
from demo.launcher import launch_demo
from demo.launcher import main as launcher_main


class TestStage14DemoSafety:
    def test_build_app_constructs_product_views_without_execution_controls(self):
        app = build_app()
        assert app is not None
        assert app.title == "AtlasOps | Read-only evidence demo"
        assert app.analytics_enabled is False
        assert len(app.blocks) > 0
        labels = {component.get("props", {}).get("label") for component in app.config["components"]}
        assert {"Overview", "Incidents", "Agents", "Models", "Evaluations",
                "Runbooks", "Evidence", "Settings"} <= labels
        assert "Scenario Control" not in labels
        assert "Rank runbooks" not in str(app.config)
        assert "Symptoms" not in str(app.config)

    def test_modern_ui_shell_is_read_only_and_accessible(self):
        header = _header_html()
        comparison = _comparison_html()
        assert "atlas-hero" in header
        assert "READ-ONLY RESEARCH SNAPSHOT" in header
        assert "NOT CERTIFIED" in header
        assert "atlas-chart" in comparison
        assert "0.16875" in comparison
        assert "0.15935" in comparison
        assert "@media (prefers-reduced-motion: reduce)" in _UI_CSS
        assert "@keyframes atlasRise" in _UI_CSS
        assert "@keyframes atlasFlow" in _UI_CSS
        assert "kubectl apply" not in header
        assert "share=True" not in header

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
        assert "G14" in status and "PARTIAL" in status
        chronology = _load_g4_final_chronology()
        assert "015" in chronology and "INCONCLUSIVE / UNSCORED" in chronology
        assert "016" in chronology and "PRE-FAULT ABORT / NON-RESULT" in chronology
        assert "017" in chronology and "COMPLETED NEGATIVE" in chronology
        assert "referenced raw attempt is external" in chronology
        assert "intentionally not republished" in chronology
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
        protected, _ = _load_stage4_attempt("EXP-STAGE4-SF002-017.json")
        assert "Select a preserved" in protected
        for attempt_name in _list_stage4_attempts():
            attempt_summary, source = _load_stage4_attempt(attempt_name)
            assert "Preserved attempt" in attempt_summary
            assert "SHA-256" in source

    def test_current_results_are_foregrounded_and_archive_is_not_loaded(self):
        result = _load_current_results()
        assert "0.16875" in result
        assert "0.15935" in result
        assert "-0.00940" in result
        assert "2 optimizer steps" in result
        assert "4/4" in result
        assert "0/8" in result
        assert "392 LoRA tensor hashes" in result
        assert "time-to-resolve" in result
        assert "unavailable" in result.lower()

    def test_load_ablation_matrix_and_comparison_table(self):
        ablation_text = _load_ablation_matrix()
        assert "HISTORICAL ARCHIVE / NON-EMPIRICAL" in ablation_text
        assert "not a measured ablation" in ablation_text
        assert "0.918" not in ablation_text

        table_text = _load_comparison_table()
        assert "HISTORICAL ARCHIVE / NON-EMPIRICAL" in table_text
        assert "not loaded or displayed as current results" in table_text

    def test_runbook_view_is_static_and_not_a_recommender_query(self):
        catalog = _load_static_runbooks()
        assert "Static catalog only" in catalog
        assert "RB-POD-OOM" in catalog
        assert "does not rank, fit, or query a recommender" in catalog

    def test_demo_launcher_cli_configuration(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["launcher.py", "--port", "8080", "--host", "127.0.0.1"])
        import demo.launcher as dl
        monkeypatch.setattr(dl, "launch_demo", lambda **kwargs: kwargs)
        # Verify main executes and parses arguments
        assert launcher_main() is None

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5"])
    def test_demo_launcher_rejects_non_loopback_hosts(self, host):
        with pytest.raises(ValueError, match="loopback"):
            launch_demo(host=host)

    def test_demo_launcher_rejects_public_share(self):
        with pytest.raises(ValueError, match="sharing is disabled"):
            launch_demo(share=True)
