"""Tests for Stage 15: Report, Package, and Submit (Gate G15).

Validates:
1. Generation and integrity of the final submission manifest (artifacts/SUBMISSION_MANIFEST.json).
2. Completeness of the final academic technical report (docs/AtlasOps_Technical_Report.md).
3. Cryptographic SHA-256 verification of canonical codebase assets.
4. Honest G0-G15 status inventory without inferred certification.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.package_submission import (
    build_submission_package,
    collect_submission_assets,
    compute_sha256,
)


class TestStage15SubmissionPackage:
    def test_submission_package_generator_creates_manifest_and_summary(self, tmp_path):
        manifest = build_submission_package(output_dir=tmp_path)
        assert manifest["project_name"] == "AtlasOps"
        assert manifest["status"] == "NOT_CERTIFIED"
        assert manifest["gate_statuses_declared"]["G4"] == "NOT_PASSED"
        assert manifest["gate_statuses_declared"]["G13"] == "REOPENED"

        manifest_path = tmp_path / "SUBMISSION_MANIFEST.json"
        summary_path = tmp_path / "SUBMISSION_SUMMARY.md"

        assert manifest_path.exists()
        assert summary_path.exists()

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["empirical_metrics"] is None
        assert "100.0%" not in summary_path.read_text(encoding="utf-8")

    def test_technical_report_structure_and_completeness(self):
        report_path = Path("docs/AtlasOps_Technical_Report.md")
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")

        required_sections = [
            "# AtlasOps: Autonomous Multi-Agent Incident Response",
            "## Abstract",
            "## 1. Introduction & Background",
            "## 2. System Architecture & Multi-Agent Flow",
            "## 3. Academic Workstreams & Methodology",
            "## 4. Empirical Evaluation & Multi-Model Ablations",
            "## 5. Demonstration & Operator Console",
            "## 6. Conclusion & Attribution",
        ]

        for sec in required_sections:
            assert sec in content, f"Missing required section: {sec}"

    def test_submission_manifest_integrity_and_metrics(self):
        assets = json.loads(
            Path("artifacts/SUBMISSION_MANIFEST.json").read_text(encoding="utf-8")
        )["assets"]
        assert len(assets) >= 15
        assert assets.keys() == collect_submission_assets().keys()
        assert {
            "ui_read_model.py",
            "static/index.html",
            "static/console.css",
            "static/console.js",
            "static/live-incident.js",
            "static/live-incident.test.js",
            "static/vendor/lucide.min.js",
            "static/vendor/LUCIDE-LICENSE",
            "docs/slides.md",
            "docs/media/console-overview-20260926.png",
            "docs/media/gradio-demo-20260926.png",
            "agents/judge.py",
            "agents/approval.py",
            "config/runtime.py",
            "docs/project/G4_PROTOCOL_V34_APPROVAL_CHANNEL.md",
            "tests/stage4_approval_process.py",
            "tests/test_stage4_approval_channel.py",
            "training/build_sft_dataset.py",
            "eval.py",
            "leaderboard.py",
        } <= assets.keys()

        for path_str, meta in assets.items():
            p = Path(path_str)
            assert p.exists(), f"Tracked asset {path_str} does not exist!"
            actual_sha = compute_sha256(p)
            assert actual_sha == meta["sha256"], f"Checksum mismatch for {path_str}!"
            assert p.stat().st_size == meta["size_bytes"]

    def test_pipeline_master_status_records_all_gates(self):
        status_path = Path("docs/project/MASTER_PIPELINE_STATUS.md")
        assert status_path.exists()
        content = status_path.read_text(encoding="utf-8")

        # Verify all 15 Gates are recorded
        for g_idx in range(1, 16):
            gate_tag = f"**G{g_idx}**"
            assert gate_tag in content, f"Missing Gate G{g_idx} in MASTER_PIPELINE_STATUS.md"

    def test_presentation_keeps_empirical_claims_open(self):
        slides = Path("docs/slides.md").read_text(encoding="utf-8")
        assert slides.count("\n---\n") == 8
        assert "Reviewed code baseline: `8560a8c7c46a8f91d74c574ebdf9c456e2835b4b`" in slides
        assert "Current reviewed main:" not in slides
        assert "G4 remains NOT_PASSED" in slides
        assert "NOT_CERTIFIED" in slides
        assert "SFT + Online GRPO Trained" not in slides
        assert "One real GKE cluster. No simulations." not in slides
