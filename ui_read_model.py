"""Small read-only projection for the operator console.

This module does not infer live health from repository evidence. Its records are
historical or catalog data; live observations come from the existing API routes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

from config.scenario_catalog import SCENARIO_CATALOG
from recommender.runbook_catalog import get_all_runbooks

ROOT = Path(__file__).resolve().parent
STATUS_FILE = ROOT / "docs/project/MASTER_PIPELINE_STATUS.md"
STAGE4_DIR = ROOT / "artifacts/evidence/stage4"
ATTEMPT_NAME = re.compile(
    r"EXP-STAGE4-SF002-(?:00[2-9]|01[0-4])(?:\.interruption)?\.json\Z"
)
STAGE_ROW = re.compile(r"^\| \*\*Stage (\d+)\*\* \|")
MUTATING_TOOLS = {"kubectl_rollout", "argocd_rollback", "kubectl_scale", "kubectl_restart"}

# The demo reads only these current result summaries; it never loads a model or
# renders archived mock outcome tables.
EVIDENCE = (
    ("artifacts/evidence/stage7/free-t4-v17/RESULT.json",
     "SFT v17 training and preservation record", "Current artifact provenance", "SFT"),
    ("artifacts/evidence/stage7/free-t4-v17/reload-v17.json",
     "SFT v17 independent reload", "Independent reload verification", "SFT"),
    (("artifacts/evidence/stage8/base-sft-validation-v1/"
      "base-sft-validation-20261003-v1/summary.json"),
     "Matched Base-vs-SFT Validation summary", "Current diagnostic result", "Validation"),
    ("docs/project/CONTROLLED_G9_ADMISSION_V1.md",
     "G4 final chronology reference", "Current G4 disposition summary", "G4"),
    ("docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md",
     "Controlled G9 final negative report", "Frozen negative result", "GRPO"),
    ("artifacts/evidence/stage9/final-aligned-diagnostic-v1/diagnostic/diagnostic.json",
     "Final aligned G9 diagnostic", "Observed negative diagnostic", "GRPO"),
    ("artifacts/evidence/stage9/final-aligned-diagnostic-v1/LOCAL_VERIFICATION.json",
     "Final aligned diagnostic local verification", "Local verification", "GRPO"),
    ("artifacts/evidence/stage9/final-aligned-diagnostic-v1/INDEPENDENT_REVIEW.json",
     "Final aligned diagnostic independent review", "Independent review", "GRPO"),
    ("artifacts/evidence/stage4/EXP-STAGE4-SF002-015.integrity-index-v1.json",
     "G4 attempt 015 integrity index", "Inconclusive / unscored reference", "G4"),
)

HISTORICAL_ARCHIVE = (
    {
        "title": "Legacy Stage 6/8/9 evaluator outputs",
        "path": "artifacts/evidence/mock_archive/",
        "classification": "HISTORICAL / NON-EMPIRICAL",
        "details": "Preserved for history; contents are not loaded or rendered by this demo.",
    },
    {
        "title": "Predetermined Stage 13 ablation profiles",
        "path": "artifacts/evidence/stage13/ablation_benchmark_results.json",
        "classification": "HISTORICAL / NON-EMPIRICAL",
        "details": "Not a measured ablation, current evaluation, or gate result.",
    },
    {
        "title": "Optional recommender research",
        "path": "artifacts/evidence/stage10/ and artifacts/evidence/stage11/",
        "classification": "HISTORICAL / SCENARIO-DERIVED / OUT OF SCOPE",
        "details": "Not historical operator feedback or incident-resolution evidence.",
    },
)

SFT_RESULT_PATH = "artifacts/evidence/stage7/free-t4-v17/RESULT.json"
SFT_RELOAD_PATH = "artifacts/evidence/stage7/free-t4-v17/reload-v17.json"
BASE_SFT_SUMMARY_PATH = (
    "artifacts/evidence/stage8/base-sft-validation-v1/"
    "base-sft-validation-20261003-v1/summary.json"
)
G4_CHRONOLOGY_PATH = "docs/project/CONTROLLED_G9_ADMISSION_V1.md"
G9_FINAL_REPORT_PATH = "docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md"
G9_ALIGNED_DIAGNOSTIC_PATH = (
    "artifacts/evidence/stage9/final-aligned-diagnostic-v1/diagnostic/diagnostic.json"
)
G4_015_INDEX_PATH = "artifacts/evidence/stage4/EXP-STAGE4-SF002-015.integrity-index-v1.json"


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(relative: str) -> dict[str, Any] | None:
    path = ROOT / relative
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_text(relative: str) -> str | None:
    try:
        return (ROOT / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _source_ref(relative: str, title: str, classification: str, area: str) -> dict[str, Any]:
    path = ROOT / relative
    available = path.is_file()
    try:
        digest = _sha(path) if available else None
    except OSError:
        available, digest = False, None
    return {
        "title": title,
        "path": relative,
        "classification": classification,
        "area": area,
        "available": available,
        "sha256": digest,
    }


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        try:
            return value if math.isfinite(float(value)) else None
        except OverflowError:
            return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text_value(value: Any, limit: int = 240) -> str | None:
    return _text(value, limit) or None if isinstance(value, str) else None


def gates() -> list[dict[str, str]]:
    """Read stable gate codes plus the unabridged source qualifier."""
    rows: list[dict[str, str]] = []
    for line in STATUS_FILE.read_text(encoding="utf-8").splitlines():
        match = STAGE_ROW.match(line)
        if not match:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        status_source = cells[4].replace("**", "")
        status, separator, status_note = status_source.partition(" (")
        if separator:
            status_note = status_note[:-1].strip() if status_note.endswith(")") else status_note.strip()
        else:
            status_note = ""
        rows.append({
            "stage": f"Stage {match.group(1)}",
            "name": cells[1],
            "gate": cells[2].replace("**", ""),
            "deliverable": cells[3].replace("**", ""),
            "status": status.strip(),
            "status_note": status_note,
        })
    if len(rows) != 16 or [r["gate"] for r in rows] != [f"G{i}" for i in range(16)]:
        raise ValueError("checked-in G0-G15 table is unavailable")
    return rows


def _g4_dispositions(report_text: str | None) -> dict[str, str]:
    expected = {
        "015": ("terminal inconclusive / unscored", "Terminal INCONCLUSIVE / UNSCORED"),
        "016": ("pre-fault abort / non-result", "PRE-FAULT ABORT / NON-RESULT"),
        "017": ("completed negative g4 outcome", "COMPLETED NEGATIVE"),
    }
    observed = {
        match.group(1): match.group(2).strip().casefold()
        for match in re.finditer(
            r"^\s*-\s*(015|016|017):\s*([^.\r\n]+)\.\s*$",
            report_text or "",
            flags=re.IGNORECASE | re.MULTILINE,
        )
    }
    return {
        number: label if observed.get(number) == phrase else "UNAVAILABLE"
        for number, (phrase, label) in expected.items()
    }


def final_g4_attempts() -> list[dict[str, Any]]:
    """Expose the final G4 chronology without implying missing raw records exist."""
    report_ref = _source_ref(
        G4_CHRONOLOGY_PATH, "G4 chronology reference",
        "Current final-status report", "G4",
    )
    index = _read_json(G4_015_INDEX_PATH) or {}
    index_ref = _source_ref(
        G4_015_INDEX_PATH, "Attempt 015 integrity index",
        "Inconclusive / unscored reference", "G4",
    )
    report_states = _g4_dispositions(_read_text(G4_CHRONOLOGY_PATH))
    index_valid = (
        index.get("schema_version") == "atlasops-prospective-integrity-index-v1"
        and index.get("experiment_id") == "EXP-STAGE4-SF002-015"
        and index.get("disposition") == "INCONCLUSIVE_INFERENCE_TRANSPORT_TIMEOUT"
        and index.get("scoring_allowed") is False
        and index.get("resolution") is None
        and index.get("reward") is None
        and index.get("time_to_resolve_s") is None
    )
    rows = []
    for number in ("015", "016", "017"):
        state = report_states[number]
        if number == "015" and not index_valid:
            state = "UNAVAILABLE"
        if number == "015" and index_ref["available"]:
            ref = index_ref
        else:
            ref = report_ref
        if state == "UNAVAILABLE":
            source_note = "No matching canonical disposition was verified."
        elif number == "015" and index_ref["available"]:
            source_note = "Integrity index is present; the referenced raw attempt is external."
        elif number == "017":
            source_note = "External raw operational evidence is intentionally not republished."
        else:
            source_note = "Attempt-level raw evidence is unavailable in this checkout."
        rows.append({
            "attempt": number,
            "state": state,
            "classification": "Final G4 chronology",
            "raw_record_available": False,
            "source": ref,
            "source_note": source_note,
            "resolution": None,
            "reward": None,
            "time_to_resolve_s": None,
        })
    return rows


def _word_count(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    return {"two": 2, "four": 4}.get(value.casefold())


def _g9_pilot_summary(report_text: str | None) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", report_text or "")
    steps = re.search(
        r"genuine replacement pilot executed (?P<steps>two|\d+) optimizer steps "
        r"and (?P<completions>four|\d+) policy completions\.",
        normalized,
        flags=re.IGNORECASE,
    )
    blocked = re.search(
        r"All (?P<blocked>four|\d+) actions were malformed/blocked and received "
        r"objective reward (?P<reward>-?\d+)\.",
        normalized,
        flags=re.IGNORECASE,
    )
    zero_advantages = re.search(
        r"Both groups had zero reward-driven advantages\.", normalized, flags=re.IGNORECASE
    )
    no_checkpoint = re.search(
        r"no acceptable SFT\+GRPO checkpoint was saved\.", normalized, flags=re.IGNORECASE
    )
    frozen = re.search(
        r"Controlled G9 is frozen as experimentally unsuccessful; no further GRPO "
        r"training retries are authorized\.",
        normalized,
        flags=re.IGNORECASE,
    )
    parsed_steps = _word_count(steps.group("steps")) if steps else None
    parsed_completions = _word_count(steps.group("completions")) if steps else None
    parsed_blocked = _word_count(blocked.group("blocked")) if blocked else None
    canonical = all((
        parsed_steps is not None,
        parsed_completions is not None,
        parsed_blocked is not None,
        blocked is not None,
        zero_advantages is not None,
        no_checkpoint is not None,
        frozen is not None,
    ))
    return {
        "available": canonical,
        "status": "FINAL NEGATIVE / FROZEN" if canonical else "UNAVAILABLE",
        "optimizer_steps": parsed_steps if canonical else None,
        "completions": parsed_completions if canonical else None,
        "malformed_or_blocked": parsed_blocked if canonical else None,
        "reward_each": int(blocked.group("reward")) if canonical and blocked else None,
        "zero_advantage_groups": 2 if canonical else None,
        "reward_driven_advantage_groups": 0 if canonical else None,
        "acceptable_checkpoint": False if canonical else None,
    }


def _valid_hash_map(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and bool(value)
        and all(
            isinstance(key, str)
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
            for key, digest in value.items()
        )
    )


def current_results() -> dict[str, Any]:
    """Project canonical summaries; absent or malformed values stay unavailable."""
    sft = _mapping(_read_json(SFT_RESULT_PATH))
    reload_record = _mapping(_read_json(SFT_RELOAD_PATH))
    validation = _mapping(_read_json(BASE_SFT_SUMMARY_PATH))
    sft_training = _mapping(sft.get("training"))
    sft_corpus = _mapping(sft.get("corpus"))
    sft_adapter = _mapping(sft.get("adapter"))
    sft_reload = _mapping(sft.get("independent_reload"))
    validation_arms = _mapping(validation.get("per_arm"))
    base_arm = _mapping(validation_arms.get("base"))
    sft_arm = _mapping(validation_arms.get("sft"))
    g9_report_text = _read_text(G9_FINAL_REPORT_PATH)
    g9_report_ref = _source_ref(
        G9_FINAL_REPORT_PATH, "Controlled G9 final negative report",
        "Frozen negative result", "GRPO",
    )
    pilot = _g9_pilot_summary(g9_report_text)
    if not g9_report_ref["available"]:
        pilot = _g9_pilot_summary(None)

    g9_diagnostic = _mapping(_read_json(G9_ALIGNED_DIAGNOSTIC_PATH))
    before = g9_diagnostic.get("tensor_hashes_before")
    after = g9_diagnostic.get("tensor_hashes_after")
    sft_reload_count = _integer(sft_reload.get("finite_lora_tensors_checked"))
    reload_file_count = _integer(reload_record.get("adapter_tensor_count"))
    sft_available = (
        sft.get("schema_version") == "atlasops-sft-pilot-completion-v1"
        and sft.get("status") == "REAL_SFT_ADAPTER_PRESERVED_AND_INDEPENDENT_RELOAD_VERIFIED"
        and sft_reload.get("status") == "PASS"
        and reload_record.get("schema_version") == "atlasops-sft-free-t4-reload-v1"
        and reload_record.get("status") == "PASS"
        and reload_record.get("run_id") == sft.get("run_id")
        and sft_reload_count is not None
        and reload_file_count == sft_reload_count
    )
    validation_available = (
        validation.get("schema_version") == "atlasops-base-sft-validation-summary-v1"
        and validation.get("lifecycle") == "completed"
        and bool(base_arm)
        and bool(sft_arm)
    )
    base_f1 = _number(base_arm.get("avg_diagnostic_f1")) if validation_available else None
    sft_f1 = _number(sft_arm.get("avg_diagnostic_f1")) if validation_available else None
    delta = round(sft_f1 - base_f1, 5) if base_f1 is not None and sft_f1 is not None else None
    aligned_sample_count = _integer(g9_diagnostic.get("sample_count"))
    aligned_admissible_count = _integer(g9_diagnostic.get("admissible_count"))
    aligned_optimizer_steps = _integer(g9_diagnostic.get("optimizer_steps"))
    aligned_hashes_valid = _valid_hash_map(before) and _valid_hash_map(after)
    aligned_available = (
        g9_diagnostic.get("interface") == "controlled-qwen-single-tool-call-v2"
        and aligned_sample_count is not None
        and aligned_admissible_count is not None
        and aligned_optimizer_steps is not None
        and aligned_hashes_valid
    )
    archive_match = re.search(
        r"Full negative archive remains unchanged:\s*SHA-256 `([0-9a-f]{64})`",
        g9_report_text or "",
        flags=re.IGNORECASE,
    )
    adapter_digest = _text_value(sft_adapter.get("weight_sha256"), 64)
    if not adapter_digest or re.fullmatch(r"[0-9a-f]{64}", adapter_digest) is None:
        adapter_digest = None
    safety_result = validation.get("safety_result")
    if not isinstance(safety_result, (bool, str, type(None))):
        safety_result = None
    return {
        "sft_v17": {
            "available": sft_available,
            "status": "PRESERVED; INDEPENDENT RELOAD VERIFIED" if sft_available else "UNAVAILABLE",
            "run_id": _text_value(sft.get("run_id"), 120) if sft_available else None,
            "model": _text_value(sft.get("model"), 120) if sft_available else None,
            "model_revision": _text_value(sft.get("model_revision"), 80) if sft_available else None,
            "training_steps": _integer(sft_training.get("global_steps")) if sft_available else None,
            "corpus_rows": _integer(sft_corpus.get("rows")) if sft_available else None,
            "corpus_split": _text_value(sft_corpus.get("split"), 40) if sft_available else None,
            "synthetic_corpus": (
                sft_corpus.get("synthetic")
                if sft_available and isinstance(sft_corpus.get("synthetic"), bool)
                else None
            ),
            "adapter_sha256": adapter_digest if sft_available else None,
            "reload_status": "PASS" if sft_available else None,
            "reload_tensor_count": sft_reload_count if sft_available else None,
            "incident_improvement_claim": False,
            "evidence": [
                _source_ref(SFT_RESULT_PATH, "SFT v17 completion record",
                            "Current artifact provenance", "SFT"),
                _source_ref(SFT_RELOAD_PATH, "SFT v17 independent reload",
                            "Independent reload verification", "SFT"),
            ],
        },
        "base_vs_sft": {
            "available": validation_available,
            "run_id": _text_value(validation.get("run_id"), 120) if validation_available else None,
            "scope": "Matched Validation-only diagnostic",
            "base_f1": base_f1,
            "sft_f1": sft_f1,
            "paired_delta": delta,
            "base_schema_valid": (
                _integer(base_arm.get("format_compliant_count")) if validation_available else None
            ),
            "base_schema_total": _integer(base_arm.get("scheduled_count")) if validation_available else None,
            "sft_schema_valid": (
                _integer(sft_arm.get("format_compliant_count")) if validation_available else None
            ),
            "sft_schema_total": _integer(sft_arm.get("scheduled_count")) if validation_available else None,
            "resolution_evaluated": (
                validation.get("environment_resolution_evaluated")
                if validation_available
                and isinstance(validation.get("environment_resolution_evaluated"), bool)
                else None
            ),
            "resolution_rate": (
                _number(validation.get("resolution_rate")) if validation_available else None
            ),
            "safety_result": safety_result if validation_available else None,
            "avg_reward": _number(validation.get("avg_reward")) if validation_available else None,
            "avg_time_to_resolve_s": (
                _number(validation.get("avg_time_to_resolve_s")) if validation_available else None
            ),
            "evidence": [
                _source_ref(BASE_SFT_SUMMARY_PATH, "Matched Base-vs-SFT Validation summary",
                            "Current diagnostic result", "Validation"),
            ],
        },
        "g9_pilot": {
            **pilot,
            "archive_sha256": (
                archive_match.group(1)
                if pilot["available"] and archive_match
                else None
            ),
            "evidence": [g9_report_ref],
        },
        "g9_aligned_diagnostic": {
            "available": aligned_available,
            "sample_count": aligned_sample_count if aligned_available else None,
            "admissible_count": aligned_admissible_count if aligned_available else None,
            "optimizer_steps": aligned_optimizer_steps if aligned_available else None,
            "tensor_hash_count": len(before) if aligned_available else None,
            "tensor_hashes_unchanged": before == after if aligned_available else None,
            "model_mutated": (
                g9_diagnostic.get("model_mutated")
                if aligned_available and isinstance(g9_diagnostic.get("model_mutated"), bool)
                else None
            ),
            "population_probability": None,
            "evidence": [
                _source_ref(G9_ALIGNED_DIAGNOSTIC_PATH, "Final aligned G9 diagnostic",
                            "Observed negative diagnostic", "GRPO"),
                _source_ref(
                    "artifacts/evidence/stage9/final-aligned-diagnostic-v1/LOCAL_VERIFICATION.json",
                    "Final aligned diagnostic local verification", "Local verification", "GRPO",
                ),
                _source_ref(
                    "artifacts/evidence/stage9/final-aligned-diagnostic-v1/INDEPENDENT_REVIEW.json",
                    "Final aligned diagnostic independent review", "Independent review", "GRPO",
                ),
            ],
        },
        "g4_attempts": final_g4_attempts(),
    }


def historical_archive() -> list[dict[str, str]]:
    """Return archive pointers only; historical outcome files are not opened."""
    return [dict(item) for item in HISTORICAL_ARCHIVE]


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
    evidence = [
        _source_ref(relative, title, classification, area)
        for relative, title, classification, area in EVIDENCE
    ]
    return {
        "source": "Checked-in repository snapshot; not a live health check",
        "source_sha": os.getenv("ATLASOPS_SOURCE_SHA", "").strip() or None,
        "gates": gates(),
        "attempts": attempts(),
        "current_results": current_results(),
        "historical_archive": historical_archive(),
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
