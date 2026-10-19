"""Regression checks for the current review-facing project evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from demo.read_api import create_app
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
CURRENT_G4_DOCS = (
    "README.md",
    "AGENTS.md",
    "JUDGES_START_HERE.md",
    "docs/project/MASTER_PIPELINE_STATUS.md",
    "docs/project/DEFERRED_RESEARCH_HANDOFF.md",
    "docs/EVIDENCE_INDEX.md",
    "docs/project/DEMO_RUNBOOK.md",
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
    current_g4 = " ".join(
        master.split("### gate g4:", 1)[1].split("### gate g5:", 1)[0].split()
    )
    assert "attempt 015 is terminal inconclusive/unscored" in current_g4
    assert "016 aborted before fault injection and is a non-result" in current_g4
    assert "017 is the latest verifiable completed negative" in current_g4
    assert "018 marker reports" in current_g4
    assert "the result and cleanup are unverified" in current_g4
    assert "no new g4 execution is authorized" in current_g4
    assert "no acceptable sft+grpo checkpoint" in master
    assert "the original g8 live incident-resolution criterion remains unmet" in master
    assert "preapproved free-t4 profile" in master

    evidence = _read("docs/EVIDENCE_INDEX.md").lower()
    assert "g4 attempt 018" in evidence
    assert "marker sha-256: `50f708112e3f2155922f8aade75e3e30169652eb244f97d11196ebce176bc208`" in evidence
    assert "no primary result, interruption record, or cleanup sidecar" in evidence
    assert "dated revised evaluation plan's negative-result and zero-chaos claims cannot be verified" in evidence
    plan = _read("docs/project/DEMO_REVISED_EVALUATION_PLAN.md").lower()
    assert "attempt 018 is completed negative" in plan

    stage7 = _read("docs/project/STAGE_7_SFT_DATA_AND_TRAINING.md").lower()
    assert "not a post-result waiver" in stage7
    assert "no incident improvement claim" in stage7


def test_completed_attempt_marker_without_result_does_not_project_a_g4_outcome(tmp_path):
    experiment = "EXP-STAGE4-SF002-018"
    marker = (
        tmp_path / "execution" / "artifacts" / "evidence" / "stage4" / ".attempts"
        / f"{experiment}.attempt.json"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps({"experiment_id": experiment, "state": "COMPLETED"}),
        encoding="utf-8",
    )

    client = TestClient(
        create_app(incident_capture_root=tmp_path, incident_id=experiment),
        base_url="http://localhost",
        client=("127.0.0.1", 1),
    )
    result = client.get("/api/incident-monitor").json()

    assert result["attempt_state"] == "COMPLETED"
    assert result["reserved"] is True
    assert result["status"] == "COMPLETED MARKER / OUTCOME UNAVAILABLE"
    assert result["gate_g4_pass"] is None
    assert result["env_resolved"] is None
    assert result["cleanup_verified_zero"] is None
    assert result["sources"] == [{
        "name": "Attempt marker (accounting only; not an outcome)",
        "sha256": hashlib.sha256(marker.read_bytes()).hexdigest(),
    }]


def test_current_g4_summaries_do_not_claim_an_attempt_018_outcome():
    for path in CURRENT_G4_DOCS:
        content = " ".join(_read(path).lower().split())
        assert "018" in content, f"{path} omits the surviving attempt-018 marker"
        assert "marker" in content, f"{path} does not identify the marker-only record"
        assert "unverified" in content, f"{path} does not qualify the missing outcome"
        assert "018 completed negative" not in content, (
            f"{path} treats the marker as a completed negative"
        )


def test_demo_runbook_presents_attempt_018_as_marker_only():
    runbook = " ".join(_read("docs/project/DEMO_RUNBOOK.md").lower().split())

    assert "--incident-capture golden" in runbook
    assert (
        "--incident-execution-root c:\\atlasops\\.codex-tmp\\website-execution-20261008-v4"
        in runbook
    )
    assert "`d:\\codex\\atlasops-demo-20261007` is the historical golden capture root" in runbook
    assert "--incident-capture golden-v3b" not in runbook
    assert "rehearsal / governed incident monitor" in runbook
    assert "completed marker / outcome unavailable" in runbook
    assert "attempt marker only" in runbook
    assert (
        "`gate_g4_pass`, `env_resolved`, and `cleanup_verified_zero` fields are `null`"
        in runbook
    )
    assert "no primary result, interruption record, or cleanup sidecar exists there" in runbook
    assert "the two-attempt protocol budget remains exhausted" in runbook

    unsupported_claims = (
        "as recorded_negative with policy block invalid_action",
        "cleanup then verified zero chaos independently",
        "show that recovery remained false",
        "post-verdict safety cleanup succeeded",
        "attempt 018 remains unreserved",
        "changes the completed live run",
    )
    for claim in unsupported_claims:
        assert claim not in runbook, f"unsupported attempt-018 claim remains: {claim}"


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


def test_current_demo_guides_reference_the_accepted_software_baseline():
    checklist = _read("docs/project/COMPLETION_CHECKLIST.md")
    baseline = re.search(
        r"Software baseline: merged PR \[\d+\]\((https://github\.com/[^)]+)\)",
        checklist,
    )
    assert baseline, "The checklist must identify the accepted software baseline"

    for path in (
        "docs/project/DEMO_RUNBOOK.md",
        "docs/AtlasOps_Technical_Report.md",
        "docs/project/G13_OFFLINE_SAFEGUARDS_HANDOFF.md",
    ):
        current = _read(path).split("## Historical", 1)[0]
        assert baseline.group(1) in current, (
            f"{path} must point readers to the checklist's accepted software baseline"
        )
        assert "completion-app-consolidated-20261015" not in current, (
            f"{path} directs readers to the superseded PR 204 checkout"
        )
