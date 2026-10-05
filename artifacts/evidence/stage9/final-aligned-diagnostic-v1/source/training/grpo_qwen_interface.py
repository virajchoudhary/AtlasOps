"""Versioned controlled action interface using v17's project-owned Qwen wire."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from training.grpo_environment import (
    _reject_nonfinite_json_constant, _unique_json_object, _validate_finite_json_numbers,
)
from training.sft_rendering import TEMPLATE_PATH, load_role_prompt

INTERFACE = "controlled-qwen-single-tool-call-v2"
ALLOWED_TOOLS = ("kubectl_get", "kubectl_scale")
DIAGNOSTIC_SAMPLES = 8
INSTRUCTION = (
    "This is a controlled capacity simulator. Use only the supplied observed state "
    "and tools. Return exactly one tool call in the specified <tool_call> format. "
    "Do not include prose, a conclusion, additional calls or a recovery claim."
)


def render_observed_prompt(tokenizer: Any, state: Mapping[str, Any]) -> str:
    """Render the observed state, not labels, with the exact v17 template."""
    from agents.coordinator import _tool_schema

    if set(state) != {"alert", "incident_id", "observations", "triage"}:
        raise ValueError("Qwen controlled prompt requires the complete public state only")
    tools = [_tool_schema(name) for name in ALLOWED_TOOLS]
    return tokenizer.apply_chat_template(
        [{"role": "system", "content": load_role_prompt("remediation") + "\n\n" + INSTRUCTION},
         {"role": "user", "content": json.dumps(state, sort_keys=True, separators=(",", ":"))}],
        tools=tools, chat_template=TEMPLATE_PATH.read_text(encoding="utf-8"),
        tokenize=False, add_generation_prompt=True,
    )


def parse_single_tool_call(text: str) -> dict[str, Any]:
    """Accept only one complete canonical tool envelope, never repair output."""
    from agents.coordinator import _TOOL_PARAMETER_SCHEMAS
    from training.sft_candidate import _validate_schema

    if not isinstance(text, str):
        raise TypeError("Model action must be raw text")
    wire = text.strip()
    if not wire.startswith("<tool_call>") or not wire.endswith("</tool_call>"):
        raise ValueError("Exactly one canonical Qwen tool call is required")
    if wire.count("<tool_call>") != 1 or wire.count("</tool_call>") != 1:
        raise ValueError("Multiple or ambiguous tool calls are forbidden")
    payload = json.loads(
        wire[len("<tool_call>"):-len("</tool_call>")].strip(),
        object_pairs_hook=_unique_json_object, parse_constant=_reject_nonfinite_json_constant,
    )
    _validate_finite_json_numbers(payload)
    if not isinstance(payload, dict) or set(payload) != {"name", "arguments"}:
        raise ValueError("Only name and arguments belong to the canonical action")
    tool, arguments = payload["name"], payload["arguments"]
    if not isinstance(tool, str) or tool not in ALLOWED_TOOLS or not isinstance(arguments, dict):
        raise ValueError("Unknown tool or non-object arguments")
    _validate_schema(arguments, _TOOL_PARAMETER_SCHEMAS[tool])
    # This interface has no model resolution-claim field. No claim is invented.
    return {"tool": tool, "arguments": arguments}
