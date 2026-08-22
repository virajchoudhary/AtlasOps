"""End-to-end render proof: AtlasOps SFT message contract through the REAL
Qwen2.5 production chat template.

No model weights, no training, no network at test time: the official template
text (Apache-2.0, Alibaba Cloud) is committed as a fixture and rendered with
Jinja2 using the same semantics transformers' apply_chat_template uses.

Proves:
1. Native tool_calls structures from trajectory_to_sft_examples survive
   rendering: tool schemas, <tool_call> JSON with OBJECT arguments,
   <tool_response> observations, multi-turn investigate->act, final answer.
2. Wire-format STRING arguments must be normalized to objects before template
   rendering -- the stock Qwen2.5 template pipe-arguments through ``tojson``,
   so a JSON-string argument would be double-encoded. This documents the exact
   trainer-side normalization required by any future SFT wiring.
3. The stock template contains no {% generation %} markers, so TRL
   assistant-only loss cannot be enabled without template augmentation
   (asserted structurally here to keep the finding pinned).
"""

from __future__ import annotations

import json
from pathlib import Path

import jinja2
import jinja2.sandbox
import pytest

from training.generate_trajectories import trajectory_to_sft_examples

FIXTURE = Path(__file__).parent / "fixtures" / "qwen2_5_chat_template.jinja"

# Neutral synthetic domain: no AtlasOps scenario identity anywhere.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "inspect_widget",
            "description": "Inspect the health status of a named widget.",
            "parameters": {
                "type": "object",
                "properties": {"widget_id": {"type": "string"}},
                "required": ["widget_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_widget",
            "description": "Restart a degraded widget.",
            "parameters": {
                "type": "object",
                "properties": {"widget_id": {"type": "string"}},
                "required": ["widget_id"],
                "additionalProperties": False,
            },
        },
    },
]


def _neutral_incident() -> dict:
    return {
        "incident_id": "inc-widget-synthetic",
        "remediation": {
            "input": {"widget_id": "widget-alpha", "alert": "WidgetAlphaDegraded"},
            "trajectory": [
                {
                    "role": "remediation",
                    "turn": 0,
                    "tool": "inspect_widget",
                    "args": {"widget_id": "widget-alpha"},
                    "output": {"status": "degraded", "restarts": 4},
                },
                {
                    "role": "remediation",
                    "turn": 1,
                    "tool": "restart_widget",
                    "args": '{"widget_id": "widget-alpha"}',
                    "output": {"success": True},
                },
            ],
            "final": {"outcome": "resolved", "executed_actions": ["restart_widget"]},
        },
    }


def _render(messages: list[dict], tools: list[dict] | None) -> str:
    template_src = FIXTURE.read_text(encoding="utf-8")
    env = jinja2.sandbox.ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    env.filters["tojson"] = lambda v: json.dumps(v, ensure_ascii=False)
    return env.from_string(template_src).render(
        messages=messages, tools=tools, add_generation_prompt=False
    )


@pytest.fixture(scope="module")
def rendered() -> str:
    example = next(
        e
        for e in trajectory_to_sft_examples(
            "synthetic/widget-alpha", "single_fault", _neutral_incident(), {}, {}
        )
        if e["role"] == "remediation"
    )
    # Trainer-side normalization under test (str -> object), mirroring what the
    # runtime model context actually contains inside <tool_call> blocks.
    normalized = json.loads(json.dumps(example))
    for m in normalized["messages"]:
        for tc in m.get("tool_calls") or []:
            args = tc["function"]["arguments"]
            if isinstance(args, str):
                tc["function"]["arguments"] = json.loads(args)
    return _render(normalized["messages"], TOOLS)


def test_tool_definitions_render_into_system_block(rendered):
    assert "# Tools" in rendered
    assert '"inspect_widget"' in rendered and '"restart_widget"' in rendered


def test_tool_call_survives_with_object_arguments(rendered):
    assert '<tool_call>' in rendered
    assert '{"name": "inspect_widget", "arguments": {"widget_id": "widget-alpha"}}' in rendered.replace("\n", "")
    assert '{"name": "restart_widget", "arguments": {"widget_id": "widget-alpha"}}' in rendered.replace("\n", "")


def test_observations_survive_as_tool_responses(rendered):
    assert "<tool_response>" in rendered
    assert '"status": "degraded"' in rendered
    assert '"success": true' in rendered


def test_investigate_then_act_ordering_preserved_in_rendered_text(rendered):
    # Match the emitted call payloads, not schema mentions in the Tools block.
    call1 = rendered.index('{"name": "inspect_widget", "arguments"')
    resp1 = rendered.index("<tool_response>")
    call2 = rendered.index('{"name": "restart_widget", "arguments"')
    final_idx = rendered.index('"outcome": "resolved"')
    assert call1 < resp1 < call2 < final_idx


def test_no_prose_flattening_of_tool_calls(rendered):
    assert 'json.dumps({"tool":' not in rendered
    assert '"Call restart_widget(' not in rendered


def test_wire_format_string_arguments_would_be_double_encoded():
    """Documents WHY trainer-side normalization is mandatory: rendering the raw
    wire-format string argument through the stock template double-encodes it."""
    messages = [
        {"role": "system", "content": "You are the remediation agent."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_x",
                "type": "function",
                "function": {"name": "restart_widget", "arguments": '{"widget_id": "w1"}'},
            }],
        },
    ]
    out = _render(messages, None)
    assert '\\"widget_id\\"' in out  # double-encoded evidence


def test_stock_template_has_no_generation_markers():
    src = FIXTURE.read_text(encoding="utf-8")
    assert "{% generation %}" not in src
