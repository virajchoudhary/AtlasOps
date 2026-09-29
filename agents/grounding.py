"""Deterministic evidence-grounding validation for AtlasOps agents.

Scientific contract (preserve-and-score):

Agents may cite structured observations in their final output. Each citation
must correspond to an actual execution recorded in the same agent's trajectory;
when it supplies generic identifiers such as a query or resource name, those
identifiers must match that execution's arguments. Violations are *detected and
recorded* — they never mutate, retry, or suppress the raw model output.

PromQL citation provenance is kept separate from data availability. A failed
query or a successful empty result can be accurately cited as such, but only a
schema-valid, nonempty metric sample contributes to
``promql_citations_with_samples``. That count records sample presence, not
threshold attainment or causal proof.

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

import math
from typing import Any

from agents.tools import REGISTERED_TOOLS

_MAX_FINDING_LEN = 200


_BLOCKED_EXECUTION_MARKERS = (
    "invalid_arguments",
    "blocked_by_policy",
    "blocked_by_circuit_breaker",
    "dedup_blocked",
    "cap_blocked",
    "blocked_by_action_observation",
    "blocked_by_terminal_error",
    "blocked_by_verifier",
    "blocked_by_evidence",
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

_PROMQL_TOOLS = frozenset({"promql_query", "promql_query_range"})
_PROMQL_ERROR_STATUS_BY_CLASS = {
    "promql_query_error": "query_error",
    "promql_query_range_error": "query_error",
    "promql_transport_error": "transport_error",
}


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


def _valid_promql_sample(sample: Any) -> bool:
    if (
        not isinstance(sample, list)
        or len(sample) != 2
        or any(isinstance(value, bool) for value in sample)
    ):
        return False
    try:
        return math.isfinite(float(sample[0])) and math.isfinite(float(sample[1]))
    except (TypeError, ValueError, OverflowError):
        return False


def _has_promql_samples(tool: str, output: dict[str, Any]) -> bool:
    if (
        output.get("success") is not True
        or output.get("evidence_status") != "series_present"
        or output.get("has_data") is not True
        or output.get("series_present") is not True
        or output.get("no_series") is not False
    ):
        return False

    result = output.get("result")
    if not isinstance(result, list) or not result:
        return False

    if tool == "promql_query":
        result_type = output.get("resultType")
        if result_type == "scalar":
            return _valid_promql_sample(result)
        if result_type != "vector":
            return False
        return all(
            isinstance(series, dict)
            and isinstance(series.get("metric"), dict)
            and _valid_promql_sample(series.get("value"))
            for series in result
        )

    return all(
        isinstance(series, dict)
        and isinstance(series.get("metric"), dict)
        and isinstance(series.get("values"), list)
        and bool(series["values"])
        and all(_valid_promql_sample(sample) for sample in series["values"])
        for series in result
    )


def _promql_observation_status(tool: str, output: Any) -> str:
    """Return only a safe, schema-backed PromQL status; never expose raw errors."""
    if not isinstance(output, dict):
        return "invalid_or_unavailable"

    if output.get("success") is False:
        error_class = output.get("error_class")
        status = (
            _PROMQL_ERROR_STATUS_BY_CLASS.get(error_class)
            if isinstance(error_class, str)
            and error_class in {f"{tool}_error", "promql_transport_error"}
            else None
        )
        return (
            status
            if (
                status is not None
                and output.get("evidence_status") == status
                and output.get("has_data") is False
                and output.get("series_present") is False
                and output.get("no_series") is False
            )
            else "invalid_or_unavailable"
        )

    if output.get("success") is not True:
        return "invalid_or_unavailable"

    if (
        output.get("evidence_status") == "no_series"
        and output.get("result") == []
        and output.get("has_data") is False
        and output.get("series_present") is False
        and output.get("no_series") is True
    ):
        return "no_series"

    if _has_promql_samples(tool, output):
        return "series_present"

    return "invalid_or_unavailable"


def _promql_citation_status(
    tool: str,
    citation: dict[str, Any],
    executed_entries: list[dict[str, Any]],
) -> tuple[str, int]:
    finding = citation.get("finding")
    if not isinstance(finding, str) or not finding.strip():
        return "finding_missing", 0

    query = citation.get("query")
    if not isinstance(query, str) or not query.strip():
        return "query_not_cited", 0

    matches = [
        entry
        for entry in executed_entries
        if str(entry.get("tool")) == tool and _citation_matches_observation(citation, entry)
    ]
    if not matches:
        return "no_matching_execution", 0

    statuses = {_promql_observation_status(tool, entry.get("output")) for entry in matches}
    if statuses == {"series_present"}:
        return "series_present", 1
    if len(statuses) == 1:
        return next(iter(statuses)), 0
    return "mixed_observations", 0


def validate_evidence_grounding(agent_doc: Any) -> dict[str, Any]:
    """Validate citation provenance and report PromQL sample presence separately."""
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
    promql_citation_statuses = []
    promql_citations_with_samples = 0
    for path, tool, _finding, citation in citations:
        if tool not in _PROMQL_TOOLS:
            continue
        status, has_samples = _promql_citation_status(
            tool,
            citation,
            executed_entries,
        )
        promql_citation_statuses.append({"path": path, "status": status})
        promql_citations_with_samples += has_samples

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
        "promql_citations_with_samples": promql_citations_with_samples,
        "promql_citation_statuses": promql_citation_statuses,
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
