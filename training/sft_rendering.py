"""Training-side rendering: wire-format SFT messages -> Qwen2.5 training inputs.

Boundary responsibilities (dataset itself stays OpenAI-wire-native per
openai-tool-messages-v1):

1. Inject the CANONICAL runtime system prompt for the agent role.
2. Inject the CANONICAL runtime tool schemas for the agent role.
3. Normalize ``tool_calls[].function.arguments`` from wire JSON strings to
   semantic objects — rendering-only; source data is never mutated.
4. Validate message sequencing (dangling/missing tool responses fail closed).
5. Render through the project-owned Qwen2.5 SFT template with generation
   markers so TRL ``assistant_only_loss=True`` masks everything that is not
   assistant output (system/user/tool definitions/tool observations).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import jinja2
import jinja2.ext
import jinja2.nodes
import jinja2.sandbox

TEMPLATE_PATH = Path(__file__).parent / "templates" / "qwen2_5_tool_sft.jinja"
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "agents" / "prompts"
SFT_ROLES = ("triage", "diagnosis", "remediation", "comms")
SFT_EXAMPLE_FORMAT = "openai-tool-messages-v1"


class GenerationMarkerExtension(jinja2.ext.Extension):
    """Local mirror of transformers' ``{% generation %}`` template extension.

    transformers registers an equivalent extension so chat templates can mark
    assistant-generated regions for TRL ``assistant_only_loss``. This mirror
    records the emitted span text so the rendering boundary can prove region
    semantics without importing the training stack.
    """

    tags = {"generation"}

    def parse(self, parser):  # noqa: D102 - jinja2 extension protocol
        lineno = next(parser.stream).lineno  # consume the 'generation' tag name
        body = parser.parse_statements(("name:endgeneration",), drop_needle=True)
        node = jinja2.nodes.CallBlock(
            self.call_method("_capture", []), [], [], body
        ).set_lineno(lineno)
        return node

    def _capture(self, caller):  # noqa: D102 - jinja2 extension protocol
        rendered = caller()
        spans = getattr(self.environment, "_generation_spans", None)
        if spans is not None:
            spans.append(rendered)
        return rendered


def sha256_of(value: Any) -> str:
    """Stable content hash for provenance records."""
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_role_prompt(role: str) -> str:
    """Load the canonical runtime system prompt for an agent role."""
    if role not in SFT_ROLES:
        raise ValueError(f"unknown SFT role '{role}'; expected one of {sorted(SFT_ROLES)}")
    path = PROMPTS_DIR / f"{role}.md"
    return path.read_text(encoding="utf-8")


def role_tool_schemas(role: str) -> list[dict[str, Any]]:
    """Model-visible tool schemas from the SAME source of truth as runtime.

    Derived programmatically from the runtime role ACL and schema registry —
    training never maintains a parallel tool registry.
    """
    if role not in SFT_ROLES:
        raise ValueError(f"unknown SFT role '{role}'; expected one of {sorted(SFT_ROLES)}")
    # Lazy import: keeps corpus-only usage light and guarantees parity with the
    # exact schemas the coordinator exposes at inference time.
    from agents.coordinator import _tool_schema
    from agents.tool_policy import ROLE_ALLOWED_TOOLS

    return [_tool_schema(name) for name in sorted(ROLE_ALLOWED_TOOLS.get(role, frozenset()))]


def normalize_tool_arguments(raw: Any) -> dict[str, Any]:
    """Wire-format arguments -> semantic object for template rendering.

    dict: accepted unchanged. str: must parse to a JSON object. Malformed JSON,
    arrays, scalars, and null are rejected with clear errors. No eval, no
    regex repair, no silent correction.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"tool arguments are not valid JSON: {raw[:120]!r}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"tool arguments must be a JSON object, got {type(parsed).__name__}")
        return parsed
    raise ValueError(f"tool arguments must be a JSON object or JSON string, got {type(raw).__name__}")


def validate_message_sequence(messages: list[dict[str, Any]]) -> None:
    """Fail closed on broken tool-call/response pairing before rendering.

    - every ``role:"tool"`` response must reference a preceding, still-unmatched
      assistant tool_call id;
    - every assistant tool_call id must receive exactly one response before the
      next assistant turn.
    """
    open_calls: set[str] = set()
    seen_calls: set[str] = set()
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant":
            for tc in message.get("tool_calls") or []:
                tc_id = tc.get("id")
                fn = tc.get("function") or {}
                if not fn.get("name"):
                    raise ValueError(f"message {index}: assistant tool_call without function name")
                if not tc_id:
                    raise ValueError(f"message {index}: assistant tool_call without id")
                if tc_id in seen_calls:
                    raise ValueError(f"message {index}: duplicate tool_call id {tc_id!r}")
                seen_calls.add(tc_id)
                open_calls.add(tc_id)
        elif role == "tool":
            tc_id = message.get("tool_call_id")
            if not tc_id:
                raise ValueError(f"message {index}: tool response without tool_call_id")
            if tc_id not in open_calls:
                raise ValueError(
                    f"message {index}: dangling tool response for id {tc_id!r} "
                    "(no preceding unmatched assistant tool_call)"
                )
            open_calls.discard(tc_id)
    if open_calls:
        raise ValueError(
            f"missing tool response for call id(s) {sorted(open_calls)!r} "
            "before end of sequence"
        )


def prepare_example_for_training(example: dict[str, Any]) -> dict[str, Any]:
    """Copy a serialized SFT example into its render-ready training form.

    - validates format marker and message sequence,
    - replaces the stored generic system placeholder with the canonical
      runtime role prompt,
    - normalizes stringified tool-call arguments to objects (rendering only),
    - attaches reproducibility provenance (prompt/schema/template hashes).

    The input example is never mutated.
    """
    if example.get("format") != SFT_EXAMPLE_FORMAT:
        raise ValueError(
            f"unsupported example format {example.get('format')!r}; expected {SFT_EXAMPLE_FORMAT!r}"
        )
    role = example["role"]
    prepared = copy.deepcopy(example)
    messages = prepared["messages"]
    validate_message_sequence(messages)

    system_prompt = load_role_prompt(role)
    replaced = False
    for message in messages:
        if message.get("role") == "system":
            message["content"] = system_prompt
            replaced = True
            break
    if not replaced:
        messages.insert(0, {"role": "system", "content": system_prompt})

    for message in messages:
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            fn["arguments"] = normalize_tool_arguments(fn.get("arguments"))

    tools = role_tool_schemas(role)
    prepared["tools"] = tools
    prepared["provenance"] = {
        "role": role,
        "scenario_id": example.get("scenario_id"),
        "n_tool_turns": sum(1 for m in messages if m.get("tool_calls")),
        "system_prompt_sha256": sha256_of(system_prompt),
        "tool_schema_sha256": sha256_of(tools),
        "template": TEMPLATE_PATH.name,
        "template_sha256": sha256_of(TEMPLATE_PATH.read_text(encoding="utf-8")),
    }
    return prepared


def render_messages(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    track_generation: bool = False,
) -> str | tuple[str, list[str]]:
    """Render messages through the project-owned template.

    Uses the same Jinja semantics as transformers' apply_chat_template
    (ImmutableSandboxedEnvironment, trim_blocks, lstrip_blocks, tojson filter)
    plus the generation-marker extension mirroring transformers' behaviour.
    With ``track_generation=True`` returns ``(text, generation_spans)`` where
    spans are the exact substrings emitted inside {% generation %} regions.
    """
    env = jinja2.sandbox.ImmutableSandboxedEnvironment(
        trim_blocks=True, lstrip_blocks=True, extensions=[GenerationMarkerExtension]
    )
    env.filters["tojson"] = lambda v: json.dumps(v, ensure_ascii=False)
    if track_generation:
        env._generation_spans = []
    template_src = TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered = env.from_string(template_src).render(
        messages=messages, tools=tools, add_generation_prompt=False
    )
    if track_generation:
        return rendered, list(env._generation_spans)
    return rendered


def encode_example(tokenizer: Any, example: dict[str, Any]) -> dict[str, Any]:
    """Tokenize a prepared example exactly as TRL's assistant_only_loss path does.

    Calls ``apply_chat_template(..., return_assistant_tokens_mask=True)``
    with the project-owned template override — the global/base tokenizer
    template is never mutated.
    """
    prepared = prepare_example_for_training(example)
    encoded = tokenizer.apply_chat_template(
        prepared["messages"],
        tools=prepared["tools"],
        chat_template=TEMPLATE_PATH.read_text(encoding="utf-8"),
        tokenize=True,
        return_dict=True,
        return_assistant_tokens_mask=True,
        add_generation_prompt=False,
    )
    encoded["provenance"] = prepared["provenance"]
    return encoded
