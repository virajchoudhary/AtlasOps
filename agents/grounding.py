"""Deterministic evidence-grounding validation for AtlasOps agents.

Scientific contract (preserve-and-score):

Agents may cite structured observations in their final output. Each citation
must correspond to an actual execution recorded in the same agent's trajectory;
when it supplies generic identifiers such as a query or resource name, those
identifiers must match that execution's arguments. Violations are *detected and
recorded* — they never mutate, retry, or suppress the raw model output.

Rationale: retry-with-validation-feedback would change the measured task
difficulty mid-run and let the model erase its own hallucination signal;
fail-closed would abort runs over exactly the model errors G4 exists to
measure. Preserve-and-score keeps the authoritative environment verifier as
the only success authority while making hallucination deterministically
quantifiable from immutable evidence.

The validator is fully general: every item in any list field named ``evidence``
must identify a tool observation, anywhere inside an agent's final output. It
is not tied to Argo CD, SF002, paymentservice, or Chaos.
"""

from __future__ import annotations

from typing import Any

from agents.tools import REGISTERED_TOOLS

_MAX_FINDING_LEN = 200


_BLOCKED_EXECUTION_MARKERS = (
    "invalid_arguments",
    "blocked_by_policy",
    "blocked_by_circuit_breaker",
    "dedup_blocked",
    "cap_blocked",
)

_CITATION_IDENTIFIER_KEYS = (
    "app",
    "query",
    "resource",
    "namespace",
    "name",
    "pod",
    "service",
    "trace_id",
)


def _executed_tools(trajectory: Any) -> set[str]:
    executed: set[str] = set()
    if not isinstance(trajectory, list):
        return executed
    for entry in trajectory:
        if not isinstance(entry, dict):
            continue
        if _is_completed_execution(entry):
            if entry.get("executed_tool_calls"):
                executed.update(
                    str(name)
                    for name in entry["executed_tool_calls"]
                    if str(name) in REGISTERED_TOOLS
                )
            else:
                executed.add(str(entry["tool"]))
    return executed


def _is_completed_execution(entry: dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("executed_tool_calls"):
        return any(str(name) in REGISTERED_TOOLS for name in entry["executed_tool_calls"])
    if entry.get("execution_state") == "unknown_tool":
        return False
    tool = str(entry.get("tool"))
    if entry.get("execution_state") == "executed":
        return tool in REGISTERED_TOOLS
    return tool in REGISTERED_TOOLS and "output" in entry and not any(
        entry.get(marker) for marker in _BLOCKED_EXECUTION_MARKERS
    )


def _iter_evidence_citations(node: Any, path: str):
    """Yield (path, tool, finding, citation) for every evidence item citing a tool.

    Recurses through the full structure so nested evidence lists are covered,
    while still validating any ``evidence`` list found at each level.
    """
    if isinstance(node, dict):
        evidence = node.get("evidence")
        if isinstance(evidence, list):
            for i, item in enumerate(evidence):
                tool = str(item.get("tool")) if isinstance(item, dict) and item.get("tool") else None
                finding = item.get("finding") if isinstance(item, dict) else ""
                yield f"{path}.evidence[{i}]", tool, finding, item
        for k, v in node.items():
            yield from _iter_evidence_citations(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_evidence_citations(v, f"{path}[{i}]")


def _citation_matches_observation(citation: dict[str, Any], entry: dict[str, Any]) -> bool:
    """Return whether every identifier supplied by the citation exists in one execution."""
    identifiers = [
        key
        for key in _CITATION_IDENTIFIER_KEYS
        if citation.get(key) is not None
    ]
    args = entry.get("args")
    if not isinstance(args, dict):
        return False
    return all(
        str(args.get(key)) == str(citation[key])
        for key in identifiers
    )


def validate_evidence_grounding(agent_doc: Any) -> dict[str, Any]:
    """Validate that cited evidence tools were actually executed by this agent."""
    agent_doc = agent_doc or {}
    trajectory = agent_doc.get("trajectory") or []
    executed = _executed_tools(trajectory)
    final = agent_doc.get("final") or {}

    citations = list(_iter_evidence_citations(final, "final"))
    executed_entries = [
        entry
        for entry in (trajectory if isinstance(trajectory, list) else [])
        if _is_completed_execution(entry) and isinstance(entry.get("args"), dict)
    ]
    violations = [
        {
            "path": path,
            "claimed_tool": tool,
            "finding": str(finding)[:_MAX_FINDING_LEN] if finding else "",
            "reason": (
                "evidence item does not identify a tool observation"
                if tool is None
                else (
                    "cited observation parameters do not match an actual execution"
                    if tool in executed
                    else "cited observation has no actual execution record in this agent's trajectory"
                )
            ),
        }
        for path, tool, finding, citation in citations
        if tool is None or (
            tool not in executed
            or (
                any(citation.get(key) is not None for key in _CITATION_IDENTIFIER_KEYS)
                and not any(
                    _citation_matches_observation(citation, entry)
                    for entry in executed_entries
                    if str(entry.get("tool")) == tool
                )
            )
        )
    ]
    return {
        "grounded": len(violations) == 0,
        "citation_count": len(citations),
        "cited_tools": sorted({tool for _, tool, _, _ in citations if tool is not None}),
        "executed_tools": sorted(executed),
        "violations": violations,
    }


def build_grounding_reports(agents: dict[str, Any]) -> dict[str, Any]:
    """Build grounding reports for a set of role -> agent-result mappings.

    Never raises: validation is diagnostic and must not disturb the run.
    """
    reports: dict[str, Any] = {}
    for role, doc in (agents or {}).items():
        try:
            reports[role] = validate_evidence_grounding(doc)
        except Exception as exc:
            reports[role] = {"grounded": None, "error": f"grounding validation failed: {exc}"}
    return reports
