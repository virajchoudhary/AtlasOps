"""Regression tests for deterministic evidence-grounding validation.

Motivating case (EXP-STAGE4-SF002-008): the diagnosis agent cited an
``argocd_list_apps`` observation that never appears in its own trajectory.
The validator must detect such fabricated citations deterministically while
preserving the raw model output untouched (preserve-and-score contract).
"""

import copy
import json

import pytest

from agents.grounding import build_grounding_reports, validate_evidence_grounding

_PROMQL_QUERY = "sum(rate(container_cpu_usage_seconds_total[5m]))"


def _promql_doc(tool: str, output: dict, *, cite_query: bool = True) -> dict:
    citation = {
        "tool": tool,
        "finding": "The metric query returned an observed sample.",
    }
    if cite_query:
        citation["query"] = _PROMQL_QUERY
    return {
        "role": "diagnosis",
        "trajectory": [
            {
                "role": "diagnosis",
                "tool": tool,
                "args": {"query": _PROMQL_QUERY},
                "output": output,
            }
        ],
        "final": {"root_cause": {"evidence": [citation]}},
    }


@pytest.mark.parametrize(
    ("tool", "output"),
    [
        (
            "promql_query",
            {
                "success": True,
                "resultType": "vector",
                "result": [{"metric": {"pod": "paymentservice-0"}, "value": [1, "0.5"]}],
                "has_data": True,
                "series_present": True,
                "no_series": False,
                "evidence_status": "series_present",
            },
        ),
        (
            "promql_query",
            {
                "success": True,
                "resultType": "scalar",
                "result": [1, "0.5"],
                "has_data": True,
                "series_present": True,
                "no_series": False,
                "evidence_status": "series_present",
            },
        ),
        (
            "promql_query_range",
            {
                "success": True,
                "result": [
                    {
                        "metric": {"pod": "paymentservice-0"},
                        "values": [[1, "0.4"], [2, "0.5"]],
                    }
                ],
                "has_data": True,
                "series_present": True,
                "no_series": False,
                "evidence_status": "series_present",
            },
        ),
    ],
)
def test_nonempty_promql_sample_citation_is_counted(tool, output):
    report = validate_evidence_grounding(_promql_doc(tool, output))

    assert report["grounded"] is True
    assert report["promql_citations_with_samples"] == 1
    assert report["promql_citation_statuses"] == [
        {"path": "final.root_cause.evidence[0]", "status": "series_present"}
    ]


@pytest.mark.parametrize(
    ("tool", "output", "status"),
    [
        (
            "promql_query",
            {
                "success": False,
                "error": "transport failed; bearer-secret-123",
                "error_class": "promql_transport_error",
                "evidence_status": "transport_error",
                "has_data": False,
                "series_present": False,
                "no_series": False,
            },
            "transport_error",
        ),
        (
            "promql_query",
            {
                "success": False,
                "error": "invalid query; bearer-secret-123",
                "error_class": "promql_query_error",
                "evidence_status": "query_error",
                "has_data": False,
                "series_present": False,
                "no_series": False,
            },
            "query_error",
        ),
        (
            "promql_query_range",
            {
                "success": False,
                "error": "invalid range query; bearer-secret-123",
                "error_class": "promql_query_range_error",
                "evidence_status": "query_error",
                "has_data": False,
                "series_present": False,
                "no_series": False,
            },
            "query_error",
        ),
        (
            "promql_query",
            {
                "success": False,
                "error_class": ["unexpected"],
                "evidence_status": "query_error",
            },
            "invalid_or_unavailable",
        ),
        (
            "promql_query",
            {
                "success": False,
                "error_class": "promql_query_error",
                "evidence_status": "query_error",
                "has_data": True,
                "series_present": True,
                "no_series": False,
            },
            "invalid_or_unavailable",
        ),
    ],
)
def test_failed_promql_citation_is_not_positive_and_error_is_sanitized(tool, output, status):
    report = validate_evidence_grounding(_promql_doc(tool, output))

    assert report["grounded"] is True
    assert report["promql_citations_with_samples"] == 0
    assert report["promql_citation_statuses"] == [
        {"path": "final.root_cause.evidence[0]", "status": status}
    ]
    assert "bearer-secret-123" not in json.dumps(report)


@pytest.mark.parametrize(
    ("tool", "wrong_error_class"),
    [
        ("promql_query", "promql_query_range_error"),
        ("promql_query_range", "promql_query_error"),
    ],
)
def test_mismatched_promql_error_class_is_invalid(tool, wrong_error_class):
    output = {
        "success": False,
        "error_class": wrong_error_class,
        "evidence_status": "query_error",
        "has_data": False,
        "series_present": False,
        "no_series": False,
    }
    report = validate_evidence_grounding(_promql_doc(tool, output))

    assert report["promql_citations_with_samples"] == 0
    assert report["promql_citation_statuses"][0]["status"] == "invalid_or_unavailable"


@pytest.mark.parametrize(
    ("tool", "result_type"),
    [("promql_query", "vector"), ("promql_query_range", None)],
)
def test_empty_successful_promql_response_is_not_positive_evidence(tool, result_type):
    output = {
        "success": True,
        "result": [],
        "has_data": False,
        "series_present": False,
        "no_series": True,
        "evidence_status": "no_series",
    }
    if result_type is not None:
        output["resultType"] = result_type
    report = validate_evidence_grounding(_promql_doc(tool, output))

    assert report["grounded"] is True
    assert report["promql_citations_with_samples"] == 0
    assert report["promql_citation_statuses"] == [
        {"path": "final.root_cause.evidence[0]", "status": "no_series"}
    ]


def test_inconsistent_promql_result_flags_fail_closed():
    output = {
        "success": True,
        "resultType": "vector",
        "result": [],
        "has_data": True,
        "series_present": True,
        "no_series": False,
        "evidence_status": "series_present",
    }
    report = validate_evidence_grounding(_promql_doc("promql_query", output))

    assert report["promql_citations_with_samples"] == 0
    assert report["promql_citation_statuses"][0]["status"] == "invalid_or_unavailable"


def test_malformed_promql_sample_fails_closed():
    output = {
        "success": True,
        "resultType": "vector",
        "result": [{"metric": {}, "value": [1, "not numeric"]}],
        "has_data": True,
        "series_present": True,
        "no_series": False,
        "evidence_status": "series_present",
    }
    report = validate_evidence_grounding(_promql_doc("promql_query", output))

    assert report["promql_citations_with_samples"] == 0
    assert report["promql_citation_statuses"][0]["status"] == "invalid_or_unavailable"


@pytest.mark.parametrize(
    ("tool", "sample"),
    [
        ("promql_query", [float("nan"), "0.5"]),
        ("promql_query", ["Inf", "0.5"]),
        ("promql_query", [1, float("inf")]),
        ("promql_query", [1, "NaN"]),
        ("promql_query", [1, "1e999"]),
        ("promql_query_range", ["-Inf", "0.5"]),
        ("promql_query_range", [1, "1e999"]),
    ],
)
def test_nonfinite_promql_samples_fail_closed(tool, sample):
    series = {"metric": {}}
    output = {
        "success": True,
        "has_data": True,
        "series_present": True,
        "no_series": False,
        "evidence_status": "series_present",
    }
    if tool == "promql_query":
        series["value"] = sample
        output["resultType"] = "vector"
        output["result"] = [series]
    else:
        series["values"] = [sample]
        output["result"] = [series]
    report = validate_evidence_grounding(_promql_doc(tool, output))

    assert report["promql_citations_with_samples"] == 0
    assert report["promql_citation_statuses"][0]["status"] == "invalid_or_unavailable"


def test_promql_sample_requires_an_exact_query_citation():
    output = {
        "success": True,
        "resultType": "vector",
        "result": [{"metric": {}, "value": [1, "0.5"]}],
        "has_data": True,
        "series_present": True,
        "no_series": False,
        "evidence_status": "series_present",
    }
    report = validate_evidence_grounding(_promql_doc("promql_query", output, cite_query=False))

    assert report["grounded"] is True
    assert report["promql_citations_with_samples"] == 0
    assert report["promql_citation_statuses"][0]["status"] == "query_not_cited"


def _doc_008_style() -> dict:
    """Minimal reproduction of the 008 diagnosis shape."""
    return {
        "role": "diagnosis",
        "trajectory": [
            {"role": "diagnosis", "tool": "promql_query", "output": {"success": True, "result": []}},
            {"role": "diagnosis", "tool": "kubectl_top_pods", "output": {"success": False}},
            {"role": "diagnosis", "tool": "jaeger_search", "output": {"success": True, "count": 0}},
        ],
        "final": {
            "confidence": 0.5,
            "root_cause": {
                "category": "deploy",
                "evidence": [
                    {
                        "finding": "No recent deployments found in the `paymentservice` namespace",
                        "tool": "argocd_list_apps",
                    }
                ],
            },
            "recommended_fix": [{"action": "rollback", "target": "paymentservice", "to_revision": "latest"}],
        },
    }


def test_fabricated_citation_is_detected_deterministically():
    report = validate_evidence_grounding(_doc_008_style())
    assert report["grounded"] is False
    assert report["citation_count"] == 1
    assert report["violations"] == [
        {
            "path": "final.root_cause.evidence[0]",
            "claimed_tool": "argocd_list_apps",
            "finding": "No recent deployments found in the `paymentservice` namespace",
            "reason": "cited observation has no actual execution record in this agent's trajectory",
        }
    ]
    # The executed tools observed in the trajectory are reported for contrast.
    assert "promql_query" in report["executed_tools"]
    assert "argocd_list_apps" not in report["executed_tools"]


def test_raw_model_output_is_never_mutated():
    doc = _doc_008_style()
    frozen = copy.deepcopy(doc)
    report = validate_evidence_grounding(doc)
    assert doc == frozen
    assert report["grounded"] is False


def test_genuine_citation_is_grounded():
    doc = _doc_008_style()
    doc["trajectory"].append(
        {"role": "diagnosis", "tool": "argocd_list_apps", "output": {"success": True, "apps": []}}
    )
    report = validate_evidence_grounding(doc)
    assert report["grounded"] is True
    assert report["violations"] == []


def test_identifier_mismatch_on_executed_tool_is_ungrounded():
    doc = {
        "role": "diagnosis",
        "trajectory": [
            {
                "tool": "promql_query",
                "args": {"query": "up"},
                "output": {"success": True},
            }
        ],
        "final": {
            "evidence": [
                {"finding": "invented", "tool": "promql_query", "query": "http_errors"}
            ]
        },
    }
    report = validate_evidence_grounding(doc)
    assert report["grounded"] is False
    assert report["citation_count"] == 1
    assert report["violations"][0]["reason"] == (
        "cited observation parameters do not match an actual execution"
    )


def test_evidence_item_without_tool_is_recorded_as_ungrounded():
    doc = {
        "role": "triage",
        "trajectory": [],
        "final": {"evidence": ["Workload default/paymentservice Ready replicas: 1/1"]},
    }
    report = validate_evidence_grounding(doc)
    assert report["grounded"] is False
    assert report["citation_count"] == 1
    assert report["violations"] == [
        {
            "path": "final.evidence[0]",
            "claimed_tool": None,
            "finding": "",
            "reason": "evidence item does not identify a tool observation",
        }
    ]


def test_nested_and_multiple_violations_are_all_reported():
    doc = {
        "role": "remediation",
        "trajectory": [{"tool": "kubectl_get", "execution_state": "executed"}],
        "final": {
            "evidence": [
                {"finding": "a", "tool": "promql_query"},
                {"finding": "b"},
            ],
            "nested": {"evidence": [{"finding": "c", "tool": "jaeger_search"}]},
            "list": [{"evidence": [{"finding": "d", "tool": "chaos_stop_experiment"}]}],
        },
    }
    report = validate_evidence_grounding(doc)
    assert report["grounded"] is False
    paths = [v["path"] for v in report["violations"]]
    assert paths == [
        "final.evidence[0]",
        "final.evidence[1]",
        "final.nested.evidence[0]",
        "final.list[0].evidence[0]",
    ]


def test_malformed_inputs_fail_safe_without_raising():
    assert validate_evidence_grounding(None)["grounded"] is True
    assert validate_evidence_grounding({})["grounded"] is True
    weird = {"trajectory": "not-a-list", "final": {"evidence": "not-a-list"}}
    report = validate_evidence_grounding(weird)
    assert report["grounded"] is True


def test_build_reports_never_raises_and_records_errors():
    class Exploding(dict):
        def get(self, *_a, **_k):
            raise RuntimeError("boom")

    reports = build_grounding_reports(
        {"ok": _doc_008_style(), "bad": Exploding({"trajectory": []})}
    )
    assert reports["ok"]["grounded"] is False
    assert reports["bad"]["grounded"] is None
    assert "grounding validation failed" in reports["bad"]["error"]


def test_reports_are_json_serializable_for_evidence_persistence():
    payload = build_grounding_reports({"diagnosis": _doc_008_style()})
    assert json.loads(json.dumps(payload))["diagnosis"]["grounded"] is False


def test_blocked_calls_are_not_available_as_cited_observations():
    doc = _doc_008_style()
    doc["trajectory"] = [
        {"role": "diagnosis", "tool": "argocd_list_apps", "args": {}, "output": {"success": False}, "invalid_arguments": True}
    ]
    report = validate_evidence_grounding(doc)
    assert report["grounded"] is False
    assert report["executed_tools"] == []


def test_unregistered_tool_placeholder_is_not_available_as_cited_observation():
    doc = _doc_008_style()
    doc["trajectory"] = [
        {
            "role": "diagnosis",
            "tool": "invented_unregistered_tool",
            "args": {},
            "output": {"error": "Unknown tool: invented_unregistered_tool"},
            "execution_state": "unknown_tool",
        }
    ]
    report = validate_evidence_grounding(doc)
    assert report["grounded"] is False
    assert report["executed_tools"] == []


def test_app_identifier_mismatch_on_executed_history_is_ungrounded():
    doc = {
        "role": "diagnosis",
        "trajectory": [
            {
                "tool": "argocd_app_history",
                "args": {"app": "actual-app"},
                "output": {"success": True},
            }
        ],
        "final": {
            "evidence": [
                {"finding": "invented", "tool": "argocd_app_history", "app": "wrong-app"}
            ]
        },
    }
    report = validate_evidence_grounding(doc)
    assert report["grounded"] is False
    assert report["violations"][0]["reason"] == (
        "cited observation parameters do not match an actual execution"
    )


def test_blocked_call_arguments_cannot_validate_a_citation():
    doc = {
        "trajectory": [
            {"tool": "promql_query", "args": {"query": "up"}, "output": {"success": True}},
            {
                "tool": "promql_query",
                "args": {"query": "invented"},
                "output": {"success": False},
                "invalid_arguments": True,
            },
        ],
        "final": {
            "evidence": [
                {"finding": "unsupported", "tool": "promql_query", "query": "invented"}
            ]
        },
    }
    report = validate_evidence_grounding(doc)
    assert report["grounded"] is False
    assert report["violations"][0]["reason"] == (
        "cited observation parameters do not match an actual execution"
    )
