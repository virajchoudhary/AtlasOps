"""Regression checks for the current review-facing project evidence."""

from __future__ import annotations

import re
from pathlib import Path

from scripts.package_submission import (
    REQUIRED_REVIEW_ASSETS,
    collect_submission_assets,
    declared_gate_statuses,
)

ROOT = Path(__file__).resolve().parents[1]
CORE_REVIEW_DOCS = (
    "README.md",
    "JUDGES_START_HERE.md",
    "docs/AtlasOps_Technical_Report.md",
    "docs/slides.md",
)
STATUS_REVIEW_DOCS = (
    "docs/project/MASTER_PIPELINE_STATUS.md",
    "docs/EVIDENCE_INDEX.md",
    "docs/project/DEFERRED_RESEARCH_HANDOFF.md",
)
STALE_CURRENT_CLAIMS = (
    "g9 reopened",
    "grpo not run",
    "grpo has not been trained",
    "no sft checkpoint",
    "no genuine sft checkpoint",
    "attempt 010 is latest",
    "attempt 015 unreserved",
    "future attempt 015",
    "observation-order defect blocks training",
    "no measured base-vs-sft result",
    "no model comparison exists",
    "g9 needs a real checkpoint",
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_authoritative_gate_table_preserves_final_scientific_status():
    statuses = declared_gate_statuses()
    assert statuses["G4"] == "NOT_PASSED"
    assert statuses["G7"] == "PASS"
    assert statuses["G9"] == "NOT_PASSED"
    assert statuses["G10"] == "OUT_OF_SCOPE"
    assert statuses["G11"] == "OUT_OF_SCOPE"

    master = _read("docs/project/MASTER_PIPELINE_STATUS.md").lower()
    assert "015 terminal inconclusive/unscored" in master
    assert "016 pre-fault abort/non-result" in master
    assert "017 completed negative" in master
    assert "no 018" in master
    assert "no acceptable sft+grpo checkpoint" in master
    assert "the original g8 live incident-resolution criterion remains unmet" in master
    assert "preapproved free-t4 profile" in master

    stage7 = _read("docs/project/STAGE_7_SFT_DATA_AND_TRAINING.md").lower()
    assert "not a post-result waiver" in stage7
    assert "no incident improvement claim" in stage7


def test_current_summary_surfaces_report_the_real_diagnostic_and_negative_results():
    required_diagnostic = ("0.16875", "0.15935", "-0.00940")
    for path in CORE_REVIEW_DOCS:
        content = _read(path).lower()
        for value in required_diagnostic:
            assert value in content, f"{path} omits current Base-vs-SFT value {value}"
        assert "not_certified" in content
        assert "g4 remains not_passed" in content
        assert re.search(r"no diagnostic improvement (?:was )?observed", content)
        assert "no acceptable sft+grpo checkpoint" in content

    for path in CORE_REVIEW_DOCS:
        content = _read(path).lower()
        assert "392" in content
        assert "015" in content and "016" in content and "017" in content


def test_current_status_and_handoff_docs_do_not_reintroduce_stale_claims():
    current_text = "\n".join(_read(path) for path in (*CORE_REVIEW_DOCS, *STATUS_REVIEW_DOCS))
    normalized = " ".join(current_text.lower().split())
    for phrase in STALE_CURRENT_CLAIMS:
        assert phrase not in normalized, f"stale current-facing claim remains: {phrase}"


def test_required_reviewer_surfaces_and_canonical_evidence_are_packaged():
    assets = collect_submission_assets()
    missing = sorted(set(REQUIRED_REVIEW_ASSETS) - assets.keys())
    assert not missing, f"required current review assets are absent from the package: {missing}"
    assert not any(
        Path(path).suffix.lower() in {".pt", ".safetensors"}
        for path in assets
    )
