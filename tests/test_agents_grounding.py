"""Regression tests for deterministic evidence-grounding validation.

Motivating case (EXP-STAGE4-SF002-008): the diagnosis agent cited an
``argocd_list_apps`` observation that never appears in its own trajectory.
The validator must detect such fabricated citations deterministically while
preserving the raw model output untouched (preserve-and-score contract).
"""

import copy
import json

from agents.grounding import build_grounding_reports, validate_evidence_grounding


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
