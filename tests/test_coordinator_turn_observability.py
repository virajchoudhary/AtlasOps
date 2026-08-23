"""Turn-observability contract tests: every model turn survives to evidence.

Mocks only — no network, no Ollama, no Kubernetes. Pins behavioral
equivalence (call counts / outcomes unchanged) while proving persistence.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _audit_environment(monkeypatch, tmp_path):
    """Self-contained audit config: never depend on import-order side effects
    (e.g. the Stage 4 runner seeding env) and never touch the real audit log."""
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "turn-observability-synthetic-secret-0123456789")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit_log.jsonl"))


def _response(content="", tool_calls=None, finish_reason="stop"):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    r.json.return_value = {"choices": [{"message": message, "finish_reason": finish_reason}]}
    return r


def _call(name, args, id="call_x"):
    return {"id": id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args) if isinstance(args, dict) else args}}


CONCLUSION = json.dumps({
    "outcome": "unresolved", "proposed_actions": [], "executed_actions": [], "verified_by": "none"
})


class TestRetryTurnPersistence:
    def test_prose_retry_turn_is_persisted_before_continue(self):
        import asyncio
        from agents.coordinator import call_agent

        t0 = _response("I will inspect the widget service and then decide.", finish_reason="stop")
        t1 = _response(CONCLUSION)
        with patch("agents.coordinator.post_with_retry", side_effect=[t0, t1]) as mock_post:
            with patch("agents.coordinator.require_audit_log"):
                result = asyncio.run(call_agent("remediation", {"incident_id": "inc-obs-1"}))

        # Behavior equivalence: exactly two model calls, honest unresolved result.
        assert mock_post.call_count == 2
        final = result["final"]
        assert final["outcome"] == "unresolved"
        assert final["executed_actions"] == []

        records = [e for e in result["trajectory"] if e.get("kind") == "model_turn"]
        assert len(records) == 1
        rec = records[0]
        assert rec["turn"] == 0
        assert rec["retry"] == {"triggered": True, "reason": "remediation_no_tool_call_retry"}
        assert rec["assistant_text"] == "I will inspect the widget service and then decide."
        assert rec["assistant_text_truncated"] is False
        assert rec["native_tool_calls"] == []
        assert rec["parsed_tool_calls"] == 0
        assert rec["finish_reason"] == "stop"
        assert rec["executed_tool_calls"] == []
        assert rec["conclusion_present"] is False

        # Ordering: the discarded turn precedes the turn-1 conclusion entry.
        idx_rec = next(i for i, e in enumerate(result["trajectory"]) if e.get("kind") == "model_turn")
        later = [e for e in result["trajectory"][idx_rec + 1:] if e.get("turn") == 1]
        assert later, "turn-1 evidence missing"

    def test_empty_content_retry_is_persisted(self):
        import asyncio
        from agents.coordinator import call_agent

        t0 = _response("", finish_reason="stop")
        t1 = _response(CONCLUSION)
        with patch("agents.coordinator.post_with_retry", side_effect=[t0, t1]):
            with patch("agents.coordinator.require_audit_log"):
                result = asyncio.run(call_agent("remediation", {"incident_id": "inc-obs-2"}))

        rec = next(e for e in result["trajectory"] if e.get("kind") == "model_turn")
        assert rec["turn"] == 0
        assert rec["assistant_text"] == ""
        assert rec["assistant_text_truncated"] is False
        assert rec["retry"]["reason"] == "remediation_no_tool_call_retry"

    def test_long_text_truncation_is_explicit(self):
        import asyncio
        from agents.coordinator import call_agent

        t0 = _response("x" * 2500)
        t1 = _response(CONCLUSION)
        with patch("agents.coordinator.post_with_retry", side_effect=[t0, t1]):
            with patch("agents.coordinator.require_audit_log"):
                result = asyncio.run(call_agent("remediation", {"incident_id": "inc-obs-3"}))

        rec = next(e for e in result["trajectory"] if e.get("kind") == "model_turn")
        assert len(rec["assistant_text"]) == 2000
        assert rec["assistant_text_truncated"] is True

    def test_raw_native_tool_call_payloads_preserved_untouched(self):
        """A malformed native payload must appear RAW in evidence, never silently
        repaired (PR #21 semantics stay fail-closed), and the subsequent
        call-less turn still lands in the retry observability record."""
        import asyncio
        from agents.coordinator import call_agent

        broken_call = _call("chaos_stop_experiment", '{"kind": "StressCh', id="call_broken")
        t0 = _response("", tool_calls=[broken_call], finish_reason="tool_calls")
        t1 = _response("The experiment could not be stopped; retrying.")
        t2 = _response(CONCLUSION)
        with patch("agents.coordinator.post_with_retry", side_effect=[t0, t1, t2]):
            with patch("agents.coordinator.require_audit_log"):
                result = asyncio.run(call_agent("remediation", {"incident_id": "inc-obs-4"}))

        # Fail-closed execution record still present (invalid_arguments) with
        # the RAW malformed payload verbatim.
        invalid = [e for e in result["trajectory"] if e.get("invalid_arguments")]
        assert invalid and invalid[0]["output"]["success"] is False
        assert invalid[0]["raw_arguments"] == '{"kind": "StressCh'

        # The malformed native call and the following call-less remediation turn
        # are both persisted; the universal model-turn record precedes it.
        rec = [e for e in result["trajectory"] if e.get("kind") == "model_turn"][0]
        assert rec["turn"] == 1
        assert rec["retry"]["reason"] == "remediation_no_tool_call_retry"
        assert rec["assistant_text"] == "The experiment could not be stopped; retrying."

    def test_normal_paths_emit_complete_model_turn_records(self):
        """Invariant: every remediation response survives normal tool loops."""
        import asyncio
        from agents.coordinator import call_agent

        t0 = _response("", tool_calls=[_call("promql_query", {"query": "up"}, "c1")])
        t1 = _response("", tool_calls=[
            _call("chaos_stop_experiment",
                  {"kind": "StressChaos", "name": "any-exp", "namespace": "chaos-mesh"}, "c2")
        ], finish_reason="tool_calls")
        t2 = _response(json.dumps({"outcome": "resolved"}))
        with patch("agents.coordinator.post_with_retry", side_effect=[t0, t1, t2]), \
             patch.dict("agents.tools.TOOL_REGISTRY",
                        {"promql_query": lambda **kw: {"success": True, "result": []}}), \
             patch("agents.tools.chaos._run",
                   return_value={"success": True, "stdout": "deleted", "returncode": 0}):
            with patch("agents.coordinator.require_audit_log"):
                result = asyncio.run(call_agent("remediation", {"incident_id": "inc-obs-5"}))

        records = [e for e in result["trajectory"] if e.get("kind") == "model_turn"]
        assert [record["turn"] for record in records] == [0, 1, 2]
        assert records[0]["executed_tool_calls"] == ["promql_query"]
        assert records[1]["native_tool_calls"][0]["name"] == "chaos_stop_experiment"
        assert records[2]["conclusion_present"] is True
        final = result["final"]
        assert final["outcome"] == "resolved"
        # Investigate -> act: both the read-only probe and the mutation executed.
        assert [a["tool"] for a in final["executed_actions"]] == ["promql_query", "chaos_stop_experiment"]


class TestMaxTurnsPersistence:
    def test_final_response_recorded_on_exhaustion(self):
        import asyncio
        from agents.coordinator import call_agent

        responses = [
            _response("", tool_calls=[_call("promql_query", {"query": f"q{i}"}, f"c{i}")],
                      finish_reason="tool_calls")
            for i in range(10)
        ]
        responses.append(_response(CONCLUSION))  # forced-conclusion call
        with patch("agents.coordinator.post_with_retry", side_effect=responses) as mock_post:
            with patch("agents.coordinator.require_audit_log"):
                with patch("agents.tools.prometheus.promql_query",
                           return_value={"success": True, "result": []}):
                    result = asyncio.run(call_agent("remediation", {"incident_id": "inc-obs-6"}))

        # Behavior equivalence: same number of model calls as before the change.
        assert mock_post.call_count == 11

        rec = next(e for e in result["trajectory"] if e.get("kind") == "model_turn")
        assert rec["turn"] == 9
        assert rec["termination_reason"] == "max_turns_exhausted"
        assert rec["retry"]["triggered"] is False
        assert rec["finish_reason"] == "tool_calls"
        assert rec["native_tool_calls"][0]["name"] == "promql_query"


class TestAuditMirror:
    def test_retry_lifecycle_event_recorded(self):
        import asyncio
        from agents.coordinator import call_agent

        t0 = _response("prose only")
        t1 = _response(CONCLUSION)
        audit = MagicMock()
        with patch("agents.coordinator.post_with_retry", side_effect=[t0, t1]):
            with patch("agents.coordinator.require_audit_log"):
                with patch("agents.coordinator.audit_log", audit):
                    asyncio.run(call_agent("remediation", {"incident_id": "inc-obs-7"}))

        retry_events = [c for c in audit.record.call_args_list
                        if c.kwargs.get("action_type") == "remediation_retry"]
        assert len(retry_events) == 1
        summary = retry_events[0].kwargs["result_summary"]
        assert "turn 0" in summary and "remediation_no_tool_call_retry" in summary
