"""Small read-only projection for the operator console.

This module does not infer live health from repository evidence. Its records are
historical or catalog data; live observations come from the existing API routes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from config.scenario_catalog import SCENARIO_CATALOG
from recommender.runbook_catalog import get_all_runbooks

ROOT = Path(__file__).resolve().parent
STATUS_FILE = ROOT / "docs/project/MASTER_PIPELINE_STATUS.md"
STAGE4_DIR = ROOT / "artifacts/evidence/stage4"
ATTEMPT_NAME = re.compile(r"EXP-STAGE4-SF002-\d{3}(?:\.interruption)?\.json\Z")
STAGE_ROW = re.compile(r"^\| \*\*Stage (\d+)\*\* \|")
MUTATING_TOOLS = {"kubectl_rollout", "argocd_rollback", "kubectl_scale", "kubectl_restart"}

# Deliberately curated. These are not all evidence files, nor live observations.
EVIDENCE = (
    ("artifacts/evidence/stage3/acceptance_report.json", "Environment acceptance",
     "Historical environment evidence", "Environment"),
    ("artifacts/evidence/mock_archive/stage6/zero_shot_test_summary.json",
     "Zero-shot test summary", "Mock evidence", "Model evaluations"),
    ("artifacts/evidence/mock_archive/stage8/sft_test_summary.json",
     "SFT test summary", "Mock evidence", "Model evaluations"),
    ("artifacts/evidence/mock_archive/stage9/grpo_test_summary.json",
     "GRPO test summary", "Mock evidence", "Model evaluations"),
    ("artifacts/evidence/stage7/sft_corpus_manifest.json", "SFT corpus manifest",
     "Training provenance", "Training provenance"),
    ("artifacts/evidence/stage10/rs_dataset_manifest.json", "Recommender dataset",
     "Scenario-derived evidence", "Recommender"),
    ("artifacts/evidence/stage11/rs_hybrid_eval_synthetic_v2.json",
     "Hybrid ranker evaluation", "Scenario-derived evidence", "Recommender"),
    ("artifacts/evidence/stage13/ablation_benchmark_results.json", "Ablation matrix",
     "Predetermined historical output", "Ablation"),
    ("artifacts/SUBMISSION_MANIFEST.json", "Submission asset inventory",
     "NOT_CERTIFIED inventory", "Submission"),
)


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gates() -> list[dict[str, str]]:
    """Read the checked-in governance table, preserving its exact status wording."""
    rows: list[dict[str, str]] = []
    for line in STATUS_FILE.read_text(encoding="utf-8").splitlines():
        match = STAGE_ROW.match(line)
        if not match:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        status = cells[4].replace("**", "").split(" (", 1)[0]
        rows.append({
            "stage": f"Stage {match.group(1)}",
            "name": cells[1],
            "gate": cells[2].replace("**", ""),
            "deliverable": cells[3].replace("**", ""),
            "status": status,
        })
    if len(rows) != 16 or [r["gate"] for r in rows] != [f"G{i}" for i in range(16)]:
        raise ValueError("checked-in G0-G15 table is unavailable")
    return rows


def attempts() -> list[dict[str, Any]]:
    if not STAGE4_DIR.is_dir():
        return []
    records = []
    for path in sorted(STAGE4_DIR.iterdir(), reverse=True):
        if not path.is_file() or not ATTEMPT_NAME.fullmatch(path.name):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        interrupted = path.name.endswith(".interruption.json")
        verdict = data.get("gate_g4_pass")
        records.append({
            "name": path.name,
            "id": _text(data.get("experiment_id") or path.stem, 100),
            "scenario": _text(data.get("scenario_id") or "single_fault/sf-002", 80),
            "timestamp": _text(data.get("completed_at") or data.get("interruption_timestamp")
                               or data.get("started_at"), 80),
            "state": ("Interrupted" if interrupted else "Not passed" if verdict is False
                      else "Historical raw pass, not certified" if verdict is True
                      else "Inconclusive"),
            "classification": "Historical evidence",
        })
    return records


def attempt_detail(name: str) -> dict[str, Any]:
    if not ATTEMPT_NAME.fullmatch(name):
        raise FileNotFoundError("attempt not found")
    path = STAGE4_DIR / name
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("expected object")
    except (OSError, ValueError, TypeError, UnicodeDecodeError) as exc:
        raise FileNotFoundError("attempt not found") from exc

    phases = data.get("phases") or {}
    execution = phases.get("coordinator_execution") or {}
    triage = execution.get("triage") or {}
    diagnosis = execution.get("diagnosis") or {}
    approval = execution.get("approval") or {}
    verification = phases.get("verification") or {}
    report = verification.get("verification_report") or {}
    scenario = SCENARIO_CATALOG.get(data.get("scenario_id"))
    targets = list(scenario.target_services) if scenario else []
    triage_targets = triage.get("affected_services") or []
    actions = [
        {
            "tool": _text(action.get("tool"), 60),
            "target": _text((action.get("args") or {}).get("resource")
                            or (action.get("args") or {}).get("app"), 100),
            "success": (action.get("output") or {}).get("success") is True,
        }
        for action in execution.get("executed_tool_actions") or []
        if isinstance(action, dict) and action.get("tool") in MUTATING_TOOLS
    ]
    warnings = []
    if targets and triage_targets and not set(targets).intersection(triage_targets):
        warnings.append("Target drift: triage did not name the frozen scenario target.")
    if approval.get("decision") == "timeout" and (data.get("causal_criteria") or {}).get(
        "8_approval_satisfied"
    ) is True:
        warnings.append("Historical inconsistency: approval criterion marked satisfied after timeout.")
    if approval.get("decision") == "timeout" and actions:
        warnings.append("Safety finding: mutating attempts were recorded after approval timeout.")
    if name.endswith(".interruption.json"):
        warnings.append("Interrupted attempt: no completed verifier verdict was recorded.")

    checks = [
        {
            "name": _text(check.get("name"), 100),
            "target": _text(check.get("target"), 100),
            "details": _text(check.get("details"), 300),
            "passed": check.get("passed") is True,
            "required": check.get("required") is True,
        }
        for check in report.get("checks") or []
        if isinstance(check, dict)
    ]
    return {
        "name": name,
        "id": _text(data.get("experiment_id") or path.stem, 100),
        "classification": "Historical evidence",
        "governance": "G4 NOT_PASSED",
        "state": ("Interrupted" if name.endswith(".interruption.json")
                  else "Not passed" if data.get("gate_g4_pass") is False
                  else "Historical raw pass, not certified" if data.get("gate_g4_pass") is True
                  else "Inconclusive"),
        "timestamp": _text(data.get("completed_at") or data.get("interruption_timestamp")
                           or data.get("started_at"), 80),
        "scenario": _text(data.get("scenario_id") or "single_fault/sf-002", 80),
        "model": _text(data.get("model"), 120) or "Unavailable",
        "source": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "triage": {
            "title": _text(triage.get("title")) or "No triage result recorded",
            "severity": _text(triage.get("severity"), 30) or "Unavailable",
            "services": [_text(s, 80) for s in triage_targets],
            "frozen_targets": targets,
        },
        "diagnosis": {
            "category": _text((diagnosis.get("root_cause") or {}).get("category"), 80),
            "specific": _text((diagnosis.get("root_cause") or {}).get("specific"), 400),
            "confidence": diagnosis.get("confidence"),
        },
        "recommendation": "Unavailable in this historical G4 attempt; G12 integration came later.",
        "approval": _text(approval.get("decision"), 40) or "Unavailable",
        "actions": actions,
        "verification": {
            "env_resolved": verification.get("env_resolved", data.get("env_resolved")),
            "status": _text(report.get("verification_status"), 60) or "Unavailable",
            "checks": checks,
        },
        "warnings": warnings,
    }


def catalog() -> dict[str, Any]:
    evidence = []
    for relative, title, classification, area in EVIDENCE:
        path = ROOT / relative
        if path.is_file():
            evidence.append({
                "title": title,
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "classification": classification,
                "area": area,
                "sha256": _sha(path),
            })
    return {
        "source": "Checked-in repository snapshot; not a live health check",
        "source_sha": os.getenv("ATLASOPS_SOURCE_SHA", "").strip() or None,
        "gates": gates(),
        "attempts": attempts(),
        "runbooks": [
            {
                "id": rb.runbook_id,
                "title": rb.title,
                "category": rb.category,
                "description": rb.description,
                "tools": rb.suggested_tools,
            }
            for rb in get_all_runbooks()
        ],
        "evidence": evidence,
    }
