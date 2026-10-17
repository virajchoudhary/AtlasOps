"""Exercise communication conclusions after actual postmortem tool results."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.parametrize("max_turns", [1, 2])
@pytest.mark.parametrize("write_succeeds", [False, True])
def test_comms_conclusion_preserves_observed_write_result(
    monkeypatch, tmp_path, max_turns, write_succeeds,
):
    import agents.coordinator as coord
    from agents.tools import comms

    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-comms-conclusion-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(comms, "POSTMORTEM_DIR", tmp_path / "postmortems")
    monkeypatch.setitem(coord.TOOL_REGISTRY, "postmortem_draft", comms.postmortem_draft)
    monkeypatch.setattr(coord, "circuit_breaker", type(coord.circuit_breaker)())
    emitted = MagicMock()
    monkeypatch.setattr(coord, "thought_emit", emitted)
    destination = "draft.md" if write_succeeds else "../outside.md"
    provider = AsyncMock(side_effect=[
        {"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{
                "id": "draft", "type": "function",
                "function": {"name": "postmortem_draft", "arguments": json.dumps({
                    "incident": {"title": "Unresolved incident"},
                    "output_path": destination,
                })},
            }],
        }, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {
            "role": "assistant", "content": json.dumps({"slack_posted": False}),
        }, "finish_reason": "stop"}]},
    ])

    result = asyncio.run(coord.call_agent(
        "comms", {"incident_id": "inc-comms-truth"},
        max_turns=max_turns, completion_provider=provider,
    ))

    assert provider.await_count == 2
    outputs = [row["output"] for row in result["trajectory"] if row.get("tool") == "postmortem_draft"]
    assert len(outputs) == 1
    assert outputs[0]["success"] is write_succeeds
    assert (tmp_path / "postmortems" / "draft.md").is_file() is write_succeeds
    assert not (tmp_path / "outside.md").exists()
    conclusions = [call.args[2].lower() for call in emitted.call_args_list if call.args[1] == "conclusion"]
    assert conclusions
    assert all("incident closed" not in text and "updated" not in text for text in conclusions)
    if not write_succeeds:
        assert all("postmortem saved" not in text for text in conclusions)
        assert any("error" in call.args[2].lower() for call in emitted.call_args_list if call.args[1] == "tool_result")
