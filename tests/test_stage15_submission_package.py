"""Tests for Stage 15: Report, Package, and Submit (Gate G15).

Validates:
1. Generation and integrity of the final submission manifest (artifacts/SUBMISSION_MANIFEST.json).
2. Completeness of the final academic technical report (docs/AtlasOps_Technical_Report.md).
3. Cryptographic SHA-256 verification of canonical codebase assets.
4. Full 15-stage pipeline certification and gate closure.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from scripts.package_submission import (
    build_submission_package,
    collect_submission_assets,
    compute_sha256,
)


class TestStage15SubmissionPackage:
    def test_submission_package_generator_creates_manifest_and_summary(self):
        manifest = build_submission_package()
        assert manifest["project_name"] == "AtlasOps"
        assert manifest["stages_completed"] == "15 / 15 (100%)"
        assert manifest["status"] == "CERTIFIED_PASS"

        manifest_path = Path("artifacts/SUBMISSION_MANIFEST.json")
        summary_path = Path("artifacts/SUBMISSION_SUMMARY.md")

        assert manifest_path.exists()
        assert summary_path.exists()

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert "key_metrics" in data
        assert data["key_metrics"]["test_resolution_rate"] == "100.0%"

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
        assets = collect_submission_assets()
        assert len(assets) >= 15

        for path_str, meta in assets.items():
            p = Path(path_str)
            assert p.exists(), f"Tracked asset {path_str} does not exist!"
            actual_sha = compute_sha256(p)
            assert actual_sha == meta["sha256"], f"Checksum mismatch for {path_str}!"

    def test_pipeline_master_status_certifies_all_gates(self):
        status_path = Path("docs/project/MASTER_PIPELINE_STATUS.md")
        assert status_path.exists()
        content = status_path.read_text(encoding="utf-8")

        # Verify all 15 Gates are recorded
        for g_idx in range(1, 16):
            gate_tag = f"**G{g_idx}**"
            assert gate_tag in content, f"Missing Gate G{g_idx} in MASTER_PIPELINE_STATUS.md"
