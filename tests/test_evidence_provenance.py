"""Only protocol-attributed runs may be cited as Gate G4 evidence.

`artifacts/evidence/stage4/` holds runs from before and after the protocol-marker
mechanism was introduced (at run 009). The pre-mechanism runs are real and stay
in the directory — deleting failed runs is how a pass rate becomes a lie — but
they are not attributable to any declared protocol, so they cannot be cited for
or against one.

The rule these tests pin: a run may claim a Gate G4 pass only if it names a
protocol that was actually declared. Documenting that in the run record is not
enough; without a check, a future `gate_g4_pass: true` file with no marker, or
one naming a protocol nobody declared, would read as a pass to any reviewer
skimming the directory.

See docs/project/G4_LIVE_RUN_RECORD.md, "Reading the evidence directory".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config.g4_protocol as g4

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "evidence" / "stage4"


def _evidence_files() -> list[Path]:
    if not EVIDENCE_DIR.is_dir():
        return []
    return sorted(p for p in EVIDENCE_DIR.glob("*.json") if ".cleanup." not in p.name)


def _declared_markers() -> set[str]:
    return {
        value
        for name, value in vars(g4).items()
        if name.endswith("_PROTOCOL_MARKER") and isinstance(value, str)
    }


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_evidence_directory_is_present():
    """A missing evidence directory would make every check below vacuous."""
    assert _evidence_files(), f"no Gate G4 evidence found under {EVIDENCE_DIR}"


@pytest.mark.parametrize("path", _evidence_files(), ids=lambda p: p.stem)
def test_a_claimed_pass_names_a_declared_protocol(path: Path):
    record = _load(path)
    if record.get("gate_g4_pass") is not True:
        pytest.skip("not a claimed pass")

    marker = record.get("protocol_marker")
    assert marker, (
        f"{path.name} claims gate_g4_pass=True with no protocol_marker. "
        "An unattributed pass cannot be cited as gate evidence."
    )
    assert marker in _declared_markers(), (
        f"{path.name} claims a pass under protocol {marker!r}, which is not "
        "declared in config/g4_protocol.py."
    )


@pytest.mark.parametrize("path", _evidence_files(), ids=lambda p: p.stem)
def test_any_marker_present_is_one_that_was_declared(path: Path):
    """Applies to failures too — a failure attributed to a fictional protocol
    is as misleading as a pass, and is how a real regression gets explained away."""
    marker = _load(path).get("protocol_marker")
    if marker is None:
        pytest.skip("pre-mechanism run; absence is recorded provenance")
    assert marker in _declared_markers(), (
        f"{path.name} names undeclared protocol {marker!r}"
    )


def test_citable_passes_are_exactly_the_ones_the_record_names():
    """The run record cites 010 and 014. If that set changes, the prose is stale.

    Keyed on ``experiment_id`` rather than filename: the golden-incident manifest
    is a canonical copy of the latest pass, not an independent run, and counting
    it separately would overstate how many passes exist.
    """
    passes = {
        _load(p).get("experiment_id")
        for p in _evidence_files()
        if _load(p).get("gate_g4_pass") is True
    }
    assert passes == {"EXP-STAGE4-SF002-010", "EXP-STAGE4-SF002-014"}, (
        "Gate G4 passes changed; update the citation table in "
        "docs/project/G4_LIVE_RUN_RECORD.md to match."
    )


def test_golden_manifest_matches_the_run_it_points_at():
    """A stale manifest would advertise a pass that its source run no longer shows."""
    manifest_path = EVIDENCE_DIR / "golden_incident_sf002_manifest.json"
    if not manifest_path.exists():
        pytest.skip("no golden-incident manifest published")

    manifest = _load(manifest_path)
    source = EVIDENCE_DIR / f"{manifest['experiment_id']}.json"
    assert source.exists(), (
        f"manifest points at {manifest['experiment_id']}, which has no evidence file"
    )
    assert manifest == _load(source), (
        f"manifest has drifted from {source.name}; regenerate it rather than "
        "editing it by hand."
    )


def test_invalid_runs_do_not_claim_a_pass():
    """Runs that aborted before the agent ran carry no criteria and must be
    recorded as INVALID, never as a pass."""
    for path in _evidence_files():
        record = _load(path)
        criteria = record.get("causal_criteria") or {}
        if criteria:
            continue
        assert record.get("gate_g4_pass") is not True, (
            f"{path.name} claims a pass with no causal criteria evaluated"
        )
