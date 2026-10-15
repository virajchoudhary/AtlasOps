from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path

import pytest

_DIAGNOSIS_PROMPT = (
    Path(__file__).resolve().parents[1] / "agents" / "prompts" / "diagnosis.md"
)
_HELDOUT_SENTINEL = "HELDOUT_ANSWER_MUST_NOT_REACH_THE_MODEL"
_QUERY = "rate(http_requests_total[5m])"


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _model_response(content="", *, tool_calls=None, finish_reason="stop"):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    **({"tool_calls": tool_calls} if tool_calls else {}),
                },
                "finish_reason": finish_reason,
            }
        ]
    }


@pytest.mark.parametrize("forced_path", ["invalid_conclusion", "turn_limit"])
def test_forced_diagnosis_conclusion_matches_canonical_schema(
    monkeypatch, forced_path
):
    from agents import coordinator
    from training.sft import validate_diagnosis_target_contracts

    prompt_source = _DIAGNOSIS_PROMPT.read_text(encoding="utf-8")
    match = re.search(r"```json\s*(.*?)\s*```", prompt_source, re.DOTALL)
    assert match is not None
    canonical_example = json.loads(match.group(1))

    incident_id = f"inc-forced-{forced_path}"
    evidence = (
        [
            {
                "tool": "promql_query",
                "query": _QUERY,
                "finding": (
                    "The query returned evidence_status=no_series; "
                    "no metric series were observed."
                ),
            }
        ]
        if forced_path == "turn_limit"
        else []
    )
    forced_target = {
        "incident_id": incident_id,
        "root_cause": "The available observations do not establish a cause.",
        "category": "unknown",
        "confidence": 0.0,
        "evidence": evidence,
        "blast_radius_update": (
            "The available observations do not establish the incident's blast radius."
        ),
        "next_agent": "remediation",
        "recommended_fix": [],
    }

    if forced_path == "turn_limit":
        initial_responses = [
            _model_response(
                "",
                tool_calls=[
                    {
                        "id": "call-no-series",
                        "type": "function",
                        "function": {
                            "name": "promql_query",
                            "arguments": json.dumps({"query": _QUERY}),
                        },
                    }
                ],
                finish_reason="tool_calls",
            )
        ]
    else:
        initial_responses = [_model_response("I cannot form the requested JSON.")]
    initial_responses.append(_model_response(json.dumps(forced_target)))
    request_payloads = []

    async def fake_post_with_retry(_client, _url, payload, **_kwargs):
        request_payloads.append(copy.deepcopy(payload))
        return _Response(initial_responses.pop(0))

    monkeypatch.setattr(coordinator, "require_audit_log", lambda: None)
    monkeypatch.setattr(coordinator, "post_with_retry", fake_post_with_retry)
    monkeypatch.setattr(coordinator, "thought_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(coordinator.audit_log, "record", lambda **_kwargs: None)
    monkeypatch.setattr(
        coordinator.circuit_breaker, "check_before_tool_call", lambda **_kwargs: None
    )
    monkeypatch.setitem(
        coordinator.TOOL_REGISTRY,
        "promql_query",
        lambda query: {
            "success": True,
            "evidence_status": "no_series",
            "result": [],
        },
    )

    result = asyncio.run(
        coordinator.call_agent(
            "diagnosis",
            {
                "incident_id": incident_id,
                "alert": {"description": "The checkout request rate is elevated."},
                "expected_root_cause": _HELDOUT_SENTINEL,
            },
            max_turns=1,
        )
    )

    assert len(request_payloads) == 2
    forced_prompt = request_payloads[-1]["messages"][-1]["content"]
    lowered_prompt = forced_prompt.casefold()
    assert all(field in forced_prompt for field in canonical_example)
    assert "actual tool observations" in lowered_prompt
    assert "every causal claim must cite a tool result" in lowered_prompt
    assert "blast_radius_update must describe only observed scope" in lowered_prompt
    assert "no_series" in lowered_prompt
    assert "not positive proof" in lowered_prompt
    assert "category unknown" in lowered_prompt
    assert "confidence 0" in lowered_prompt
    assert "next_agent to remediation" in lowered_prompt
    assert "do not include outcome or training-label metadata" in lowered_prompt
    assert _HELDOUT_SENTINEL not in json.dumps(request_payloads[-1]["messages"])
    if forced_path == "turn_limit":
        assert any(
            message.get("role") == "tool"
            and "no_series" in message.get("content", "")
            for message in request_payloads[-1]["messages"]
        )

    assert set(result["final"]) == set(canonical_example)
    assert result["final"] == forced_target
    assert "outcome" not in result["final"]
    assert not any(key.startswith("expected_") for key in result["final"])
    validate_diagnosis_target_contracts(
        [
            {
                "scenario_id": "synthetic-forced-conclusion",
                "role": "diagnosis",
                "messages": [
                    {"role": "assistant", "content": json.dumps(result["final"])}
                ],
            }
        ]
    )
