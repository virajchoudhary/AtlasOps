"""Regressions for unanswered automated review findings that were still present on main."""

import ast
import re
from pathlib import Path

import dashboard

ROOT = Path(__file__).resolve().parents[1]


def _read_model_areas() -> set[str]:
    tree = ast.parse((ROOT / "ui_read_model.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "EVIDENCE":
            return {entry.elts[3].value for entry in node.value.elts}
    raise AssertionError("EVIDENCE catalog not found")


def test_console_evidence_groups_match_read_model_areas():
    js = (ROOT / "static/console.js").read_text(encoding="utf-8")
    block = re.search(r"const areas = \[(.*?)\];", js, re.S).group(1)
    console_areas = set(re.findall(r'\[\s*"([^"]+)",', block))
    assert console_areas == _read_model_areas()


def test_console_notice_reports_g9_as_not_passed():
    js = (ROOT / "static/console.js").read_text(encoding="utf-8")
    assert "G9 and G13 are REOPENED" not in js
    assert "G4 and G9 are NOT_PASSED. G13 is REOPENED." in js


def test_dashboard_withholds_conclusion_when_validation_unavailable(monkeypatch):
    results = dashboard.current_results()
    unavailable = {key: None for key in results["base_vs_sft"]}
    unavailable["available"] = False
    results["base_vs_sft"] = unavailable
    monkeypatch.setattr(dashboard, "current_results", lambda: results)
    text = dashboard._load_current_results()
    assert "No diagnostic improvement was observed" not in text
    assert "Validation evidence is unavailable or incomplete" in text


def test_dashboard_states_conclusion_from_available_evidence():
    text = dashboard._load_current_results()
    assert "No diagnostic improvement was observed" in text
