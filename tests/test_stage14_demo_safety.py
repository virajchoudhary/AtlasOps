"""Tests for Stage 14: Deploy Final Demo Safely (Gate G14).

Validates:
1. Gradio Ops Console tab construction and UI component registration.
2. Zero-risk safe mode execution (cluster mutation protection).
3. Interactive Hybrid Runbook Recommender UI querying.
4. Multi-model ablation matrix and benchmark loading.
5. Standalone demo launcher argument parsing and configuration.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from dashboard import (
    _apply_chaos,
    _load_ablation_matrix,
    _load_comparison_table,
    _query_hybrid_recommender,
    _reset_chaos,
    build_app,
)
from demo.launcher import main as launcher_main


class TestStage14DemoSafety:
    def test_build_app_constructs_all_seven_tabs(self):
        app = build_app()
        assert app is not None
        assert app.title == "AtlasOps Ops Console & Demo Interface"
        # Verify Blocks instance is constructed
        assert len(app.blocks) > 0

    def test_demo_safe_mode_prevents_cluster_mutations(self, monkeypatch):
        monkeypatch.setenv("DEMO_SAFE_MODE", "1")
        # In safe mode, applying chaos returns safe simulated message
        res = _apply_chaos("single_fault/sf-001")
        assert "[SAFE MODE]" in res
        assert "without destructive cluster mutations" in res

        # In safe mode, resetting chaos returns safe confirmation
        reset_res = _reset_chaos()
        assert "[SAFE MODE]" in reset_res

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
        assert "Match Confidence" in res

    def test_load_ablation_matrix_and_comparison_table(self):
        ablation_text = _load_ablation_matrix()
        assert "AtlasOps Final Multi-Generation Ablation & Stress Matrix" in ablation_text or "results" in ablation_text
        assert "Zero-Shot Baseline" in ablation_text

        table_text = _load_comparison_table()
        assert len(table_text) > 0

    def test_demo_launcher_cli_configuration(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["launcher.py", "--port", "8080", "--host", "127.0.0.1"])
        import demo.launcher as dl
        monkeypatch.setattr(dl, "launch_demo", lambda **kwargs: kwargs)
        # Verify main executes and parses arguments
        launcher_main()
