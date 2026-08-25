"""Regression coverage for Stage 4 grounding-evidence persistence."""

from __future__ import annotations

import ast
import pathlib


_RUNNER_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "run_stage4_golden_incident.py"
)


def _constant(value: ast.AST) -> str | None:
    return value.value if isinstance(value, ast.Constant) else None


def test_stage4_persists_grounding_validation_in_execution_phase():
    tree = ast.parse(_RUNNER_PATH.read_text(encoding="utf-8"))
    phase_values = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        target = node.targets[0] if len(node.targets) == 1 else None
        if not isinstance(target, ast.Subscript):
            continue
        if _constant(target.slice) != "coordinator_execution":
            continue
        phase_values.append(node.value)

    assert len(phase_values) == 1
    keys = {
        key.value
        for key in phase_values[0].keys
        if isinstance(key, ast.Constant)
    }
    assert "grounding_validation" in keys
