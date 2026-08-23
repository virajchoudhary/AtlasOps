"""Turn-observability contract tests: every model turn survives to evidence.

Mocks only — no network, no Ollama, no Kubernetes. Pins behavioral
equivalence (call counts / outcomes unchanged) while proving persistence.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _audit_environment(monkeypatch):
    """Self-contained audit config: never depend on import-order side effects
    (e.g. the Stage 4 runner seeding env) and never touch the real audit log."""
    audit_root = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scratch"
        / "coordinator-turn-audit"
        / uuid.uuid4().hex
    )
    audit_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "turn-observability-synthetic-secret-0123456789")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(audit_root / "audit_log.jsonl"))


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
        assert [record["turn"] for record in records] == [0, 1]

        retry_record, final_record = records
        assert retry_record["assistant_text"] == "I will inspect the widget service and then decide."
        assert retry_record["assistant_text_truncated"] is False
        assert retry_record["native_tool_calls"] == []
        assert retry_record["parsed_tool_calls"] == 0
        assert retry_record["finish_reason"] == "stop"
        assert retry_record["validation_state"] == "no_tool_calls"
        assert retry_record["executed_tool_calls"] == []
        assert retry_record["conclusion_present"] is False
        assert retry_record["retry"] == {
            "triggered": True,
            "reason": "remediation_no_tool_call_retry",
        }

        assert final_record["assistant_text"] == CONCLUSION
        assert final_record["native_tool_calls"] == []
        assert final_record["finish_reason"] == "stop"
        assert final_record["validation_state"] == "final_conclusion"
        assert final_record["executed_tool_calls"] == []
        assert final_record["conclusion_present"] is True

        indices = [
            index for index, entry in enumerate(result["trajectory"])
            if entry.get("kind") == "model_turn"
        ]
        conclusion_indices = [
            index for index, entry in enumerate(result["trajectory"][indices[1] + 1:])
            if entry.get("role") == "remediation" and entry.get("turn") == 1
            and entry.get("content") == CONCLUSION
        ]
        assert indices[0] < indices[1]
        assert conclusion_indices, "turn-1 conclusion evidence missing"

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

        records = [e for e in result["trajectory"] if e.get("kind") == "model_turn"]
        assert [record["turn"] for record in records] == [0, 1, 2]

        malformed_record, retry_record, final_record = records
        assert malformed_record["native_tool_calls"] == [{
            "id": "call_broken",
            "name": "chaos_stop_experiment",
            "arguments": '{"kind": "StressCh',
        }]
        assert malformed_record["validation_state"] == "invalid_arguments"
        assert malformed_record["executed_tool_calls"] == []

        assert invalid[0]["output"]["success"] is False
        assert invalid[0]["raw_arguments"] == '{"kind": "StressCh'

        assert retry_record["turn"] == 1
        assert retry_record["assistant_text"] == "The experiment could not be stopped; retrying."
        assert retry_record["validation_state"] == "no_tool_calls"
        assert retry_record["retry"]["reason"] == "remediation_no_tool_call_retry"
        assert retry_record["executed_tool_calls"] == []

        assert final_record["turn"] == 2
        assert final_record["assistant_text"] == CONCLUSION
        assert final_record["validation_state"] == "final_conclusion"
        assert final_record["executed_tool_calls"] == []

        trajectory_indices = {id(entry): index for index, entry in enumerate(result["trajectory"])}
        model_indices = [trajectory_indices[id(record)] for record in records]
        invalid_index = trajectory_indices[id(invalid[0])]
        assert model_indices[0] < invalid_index < model_indices[1] < model_indices[2]

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

        records = [e for e in result["trajectory"] if e.get("kind") == "model_turn"]
        ordinary_records = records[:-1]
        forced_record = records[-1]

        assert [record["turn"] for record in ordinary_records] == list(range(10))
        assert [record["finish_reason"] for record in ordinary_records] == ["tool_calls"] * 10
        assert all(record["retry"]["triggered"] is False for record in ordinary_records)
        assert all(
            isinstance(record["executed_tool_calls"], list)
            and all(isinstance(name, str) for name in record["executed_tool_calls"])
            for record in records
        )
        assert [record["executed_tool_calls"] for record in ordinary_records[:6]] == [
            ["promql_query"],
            ["promql_query"],
            ["promql_query"],
            ["promql_query"],
            ["promql_query"],
            ["promql_query"],
        ]
        assert all(record["executed_tool_calls"] == [] for record in ordinary_records[6:])

        exhausted_record = ordinary_records[-1]
        assert exhausted_record["termination_reason"] == "max_turns_exhausted"
        assert exhausted_record["native_tool_calls"][0]["name"] == "promql_query"

        assert forced_record is not exhausted_record
        assert forced_record["turn"] == 10
        assert forced_record["turn_kind"] == "forced_conclusion"
        assert forced_record["validation_state"] == "final_conclusion"
        assert forced_record["assistant_text"] == CONCLUSION
        assert forced_record["executed_tool_calls"] == []


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
