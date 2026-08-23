#!/usr/bin/env python
"""Documentation consistency guardrail.

Fails CI when documentation claims about the SRE tool surface or the scenario
catalogue drift away from runtime truth.

Truth sources (runtime introspection, offline):
- agents.tools.REGISTERED_TOOLS          -> registered wrapper count
- agents.tool_policy.AGENT_EXPOSED_TOOLS -> agent-exposed count
- config.runtime.FROZEN_SCENARIOS        -> frozen static scenarios (+ per-tier)
- config.runtime.DEFAULT_DYNAMIC_ADVERSARIAL_COUNT

Checked surfaces:
1. Numeric claims in current-truth docs must equal runtime truth.
2. Known-stale claims ("22 registered", "19 agent-exposed", ...) are rejected
   anywhere, except explicit historical-provenance lines in
   docs/project/MASTER_PIPELINE_STATUS.md.
3. Every frozen scenario must have its chaos manifest on disk, and the manifest
   directories must not drift in count.

Exit codes: 0 = consistent, 1 = drift detected, 2 = internal error.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Current-truth documents whose numeric claims must match runtime reality.
# docs/project/MASTER_PIPELINE_STATUS.md is deliberately NOT here: it is the
# immutable provenance record (historical rows may cite older counts).
CURRENT_TRUTH_FILES = [
    "README.md",
    "CLAUDE.md",
    "ARCHITECTURE.md",
    "BENCHMARKS.md",
    "JUDGES_START_HERE.md",
    "docs/project/IMPLEMENTATION_STATUS.md",
    "dashboard.py",
    "docs/slides.md",
    "static/index.html",
]

# (label, compiled extraction pattern, expected-value resolver name).
# Every match inside CURRENT_TRUTH_FILES must resolve to the runtime value.
CLAIM_PATTERNS = [
    # "N registered / M exposed" rows are true per-module breakdowns; the
    # negative lookahead keeps them out of the total-claim check.
    ("registered", re.compile(r"(\d+)\s+registered(?!\s*/\s*\d+\s+exposed)", re.IGNORECASE), "registered"),
    ("agent-exposed", re.compile(r"(\d+)\s+agent-exposed", re.IGNORECASE), "exposed"),
    ("role-authorized", re.compile(r"(\d+)\s+role-authorized", re.IGNORECASE), "exposed"),
    ("frozen scenarios", re.compile(r"(\d+)\s+frozen", re.IGNORECASE), "frozen_scenarios"),
    (
        "dynamic generated",
        re.compile(r"up to\s+(\d+)\s+generated", re.IGNORECASE),
        "dynamic_count",
    ),
]

# Patterns that must not appear anywhere in tracked text (stale truth).
STALE_PATTERNS = [
    (re.compile(r"\b22\s+registered", re.IGNORECASE), "stale '22 registered' claim"),
    (re.compile(r"\b22\s+SRE\s+tool\s+wrappers", re.IGNORECASE), "stale '22 SRE tool wrappers' claim"),
    (re.compile(r"\b22\s+Registered\s+Tool\s+Wrappers", re.IGNORECASE), "stale '22 Registered Tool Wrappers' heading"),
    (re.compile(r"\b19\s+agent-exposed", re.IGNORECASE), "stale '19 agent-exposed' claim"),
    (re.compile(r"22\s+registered\s+tools,\s*19", re.IGNORECASE), "stale '22 registered tools, 19 agent-exposed' pair"),
]

# Historical provenance lines allowed to cite pre-drift counts verbatim.
HISTORICAL_LINE_ALLOWLIST = re.compile(
    r"pull/(?:9)\)|\[PR #9\]"  # PR #9 provenance rows in the master status doc
)
HISTORICAL_FILES = {
    "docs/project/MASTER_PIPELINE_STATUS.md",
}


@dataclass(frozen=True)
class RuntimeTruth:
    registered: int
    exposed: int
    unexposed: tuple[str, ...]
    frozen_scenarios: int
    tier_counts: dict[str, int]
    dynamic_count: int


def runtime_truth() -> RuntimeTruth:
    """Introspect the live modules; this is the definition of 'reality'."""
    from agents.tools import REGISTERED_TOOLS
    from agents.tool_policy import AGENT_EXPOSED_TOOLS
    from config.runtime import (
        DEFAULT_DYNAMIC_ADVERSARIAL_COUNT,
        FROZEN_SCENARIO_TIER_COUNTS,
        FROZEN_SCENARIOS,
    )

    return RuntimeTruth(
        registered=len(REGISTERED_TOOLS),
        exposed=len(AGENT_EXPOSED_TOOLS),
        unexposed=tuple(sorted(REGISTERED_TOOLS - AGENT_EXPOSED_TOOLS)),
        frozen_scenarios=len(FROZEN_SCENARIOS),
        tier_counts=dict(FROZEN_SCENARIO_TIER_COUNTS),
        dynamic_count=DEFAULT_DYNAMIC_ADVERSARIAL_COUNT,
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _candidate(path_str: str) -> Path:
    """Absolute paths are used verbatim; relative paths resolve off REPO_ROOT."""
    p = Path(path_str)
    return p if p.is_absolute() else REPO_ROOT / p


def check_numeric_claims(truth: RuntimeTruth, files: list[str]) -> list[str]:
    """Every count-style claim in current-truth docs must equal runtime truth."""
    expected = {
        "registered": truth.registered,
        "exposed": truth.exposed,
        "frozen_scenarios": truth.frozen_scenarios,
        "dynamic_count": truth.dynamic_count,
    }
    problems: list[str] = []
    for rel in files:
        path = _candidate(rel)
        if not path.is_file():
            problems.append(f"{rel}: current-truth file missing")
            continue
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            for label, pattern, key in CLAIM_PATTERNS:
                for match in pattern.finditer(line):
                    claimed = int(match.group(1))
                    if claimed != expected[key]:
                        problems.append(
                            f"{rel}:{lineno}: {label} claim {claimed} != "
                            f"runtime truth {expected[key]}"
                        )
    return problems


def check_stale_patterns(files: list[str] | None = None) -> list[str]:
    """Reject known-stale claims anywhere except allowlisted historical lines."""
    if files is None:
        candidates = CURRENT_TRUTH_FILES + ["docs/project/MASTER_PIPELINE_STATUS.md"]
    else:
        candidates = files
    problems: list[str] = []
    for rel in candidates:
        path = _candidate(rel)
        if not path.is_file():
            continue
        historical_file = rel.replace("\\", "/") in HISTORICAL_FILES
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            for pattern, message in STALE_PATTERNS:
                if pattern.search(line):
                    if historical_file and HISTORICAL_LINE_ALLOWLIST.search(line):
                        continue
                    problems.append(f"{rel}:{lineno}: {message}")
    return problems


def check_manifest_inventory(truth: RuntimeTruth, manifests_dir: Path | None = None) -> list[str]:
    """Frozen catalogue must map 1:1 onto chaos manifests on disk."""
    from config.runtime import FROZEN_SCENARIOS_BY_TIER

    base = manifests_dir if manifests_dir is not None else REPO_ROOT / "bench" / "chaos_manifests"
    problems: list[str] = []
    for tier, scenarios in FROZEN_SCENARIOS_BY_TIER.items():
        on_disk = sorted(p.stem for p in (base / tier).glob("*.yaml"))
        declared = sorted(s.split("/", 1)[1] for s in scenarios)
        if on_disk != declared:
            missing = sorted(set(declared) - set(on_disk))
            extra = sorted(set(on_disk) - set(declared))
            problems.append(
                f"bench/chaos_manifests/{tier}: manifest drift "
                f"(missing={missing}, undeclared={extra})"
            )
    total = sum(len(v) for v in FROZEN_SCENARIOS_BY_TIER.values())
    if total != truth.frozen_scenarios:
        problems.append(
            f"frozen catalogue total {total} != FROZEN_SCENARIOS length {truth.frozen_scenarios}"
        )
    adversarial = base / "adversarial"
    if adversarial.is_dir():
        n_adv = len(list(adversarial.glob("*.yaml")))
        if n_adv > truth.dynamic_count:
            problems.append(
                f"bench/chaos_manifests/adversarial holds {n_adv} templates, "
                f"exceeding dynamic default {truth.dynamic_count}"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    del argv  # no CLI options by design; deterministic CI behaviour
    try:
        truth = runtime_truth()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"FAIL: could not introspect runtime truth: {exc}", file=sys.stderr)
        return 2

    print(
        "Runtime truth: "
        f"{truth.registered} registered tools, {truth.exposed} agent-exposed "
        f"(unexposed: {', '.join(truth.unexposed)}); "
        f"{truth.frozen_scenarios} frozen scenarios "
        f"{truth.tier_counts}; up to {truth.dynamic_count} dynamic"
    )

    problems: list[str] = []
    problems += check_numeric_claims(truth, CURRENT_TRUTH_FILES)
    problems += check_stale_patterns()
    problems += check_manifest_inventory(truth)

    if problems:
        print(f"DOC CONSISTENCY: FAIL ({len(problems)} problem(s))")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("DOC CONSISTENCY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
