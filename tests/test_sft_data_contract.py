"""SFT executable-tool-call data contract tests.

The runtime parses NATIVE ``tool_calls`` structures (arguments as a JSON
string) and feeds back ``role: "tool"`` observations. The SFT corpus
serializer must teach exactly that representation — never flatten calls to
prose — without fabricating actions or outcomes. Synthetic fixtures only.
"""

from __future__ import annotations

import json

from training.generate_trajectories import SFT_EXAMPLE_FORMAT, trajectory_to_sft_examples


def _synthetic_incident() -> dict:
    return {
        "incident_id": "inc-synthetic",
        "triage": {
            "trajectory": [
                {
                    "role": "triage",
                    "turn": 0,
                    "tool": "alertmanager_list_alerts",
                    "args": {"active_only": True},
                    "output": {"success": True, "count": 1},
                }
            ],
            "final": {"severity": "P1"},
        },
        "remediation": {
            "input": {"incident_id": "inc-synthetic", "approval_mode": "auto"},
            "trajectory": [
                {
                    "role": "remediation",
                    "turn": 0,
                    "tool": "promql_query",
                    "args": {"query": "up"},
                    "output": {"success": True, "result": []},
                },
                {
                    "role": "remediation",
                    "turn": 1,
                    "tool": "synthetic_mutating_tool",
                    "args": '{"target_name": "synthetic-resource"}',
                    "output": {"success": True},
                },
                {
                    "role": "remediation",
                    "turn": 2,
                    "content": "Investigation showed a healthy signal; action executed.",
                },
            ],
            "final": {"outcome": "resolved", "executed_actions": [{"tool": "synthetic_mutating_tool"}]},
        },
        # A role with prose only must produce zero tool turns.
        "comms": {
            "trajectory": [
                {"role": "comms", "turn": 0, "content": "Incident closed."}
            ],
            "final": {"slack_posted": True},
        },
    }


JUDGE = {"overall": 0.8}
REWARD = {"total": 0.75}


def test_tool_step_serializes_to_native_tool_calls_with_string_arguments():
    examples = trajectory_to_sft_examples("unit/t-1", "single_fault", _synthetic_incident(), JUDGE, REWARD)
    triage = next(e for e in examples if e["role"] == "triage")
    msgs = triage["messages"]
    assistant = next(m for m in msgs if m.get("tool_calls"))
    call = assistant["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "alertmanager_list_alerts"
    # Arguments MUST be a JSON string on the wire, parseable to the recorded args.
    assert isinstance(call["function"]["arguments"], str)
    assert json.loads(call["function"]["arguments"]) == {"active_only": True}
    # The recorded environment observation follows as a tool message.
    tool_msg = msgs[msgs.index(assistant) + 1]
    assert tool_msg["role"] == "tool"
    assert json.loads(tool_msg["content"]) == {"success": True, "count": 1}
    assert tool_msg["tool_call_id"] == call["id"]


def test_investigate_then_act_ordering_is_preserved():
    examples = trajectory_to_sft_examples("unit/t-2", "multi_fault", _synthetic_incident(), JUDGE, REWARD)
    rem = next(e for e in examples if e["role"] == "remediation")
    seq = []
    for m in rem["messages"]:
        if m.get("tool_calls"):
            seq.append(("call", m["tool_calls"][0]["function"]["name"]))
        elif m.get("role") == "tool":
            seq.append(("obs", None))
        elif m.get("role") == "assistant" and not m.get("tool_calls"):
            seq.append(("text", None))
    # read-only call -> its observation -> mutating call -> its observation -> conclusion text -> final
    assert seq == [
        ("call", "promql_query"), ("obs", None),
        ("call", "synthetic_mutating_tool"), ("obs", None),
        ("text", None), ("text", None),
    ]
    assert rem["n_tool_turns"] == 2


def test_prose_only_role_never_fabricates_tool_calls():
    examples = trajectory_to_sft_examples("unit/t-3", "cascade", _synthetic_incident(), JUDGE, REWARD)
    comms = next(e for e in examples if e["role"] == "comms")
    assert all(not m.get("tool_calls") for m in comms["messages"])
    assert comms["n_tool_turns"] == 0


def test_provenance_and_format_marker_are_retained():
    examples = trajectory_to_sft_examples("unit/t-4", "named_replays", _synthetic_incident(), JUDGE, REWARD)
    assert examples
    for e in examples:
        assert e["format"] == SFT_EXAMPLE_FORMAT
        assert e["scenario_id"] == "unit/t-4"
        assert e["tier"] == "named_replays"
        assert e["reward"] == REWARD["total"]
        assert e["judge"] == JUDGE


def test_roles_without_trajectory_are_skipped():
    incident = _synthetic_incident()
    incident["diagnosis"] = {}
    examples = trajectory_to_sft_examples("unit/t-5", "single_fault", incident, JUDGE, REWARD)
    assert all(e["role"] != "diagnosis" for e in examples)


def test_recorded_final_conclusion_is_plain_assistant_text_not_a_call():
    examples = trajectory_to_sft_examples("unit/t-6", "single_fault", _synthetic_incident(), JUDGE, REWARD)
    rem = next(e for e in examples if e["role"] == "remediation")
    last = rem["messages"][-1]
    assert last["role"] == "assistant"
    assert not last.get("tool_calls")
    assert json.loads(last["content"])["outcome"] == "resolved"
