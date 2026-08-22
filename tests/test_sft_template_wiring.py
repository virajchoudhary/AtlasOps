"""Core wiring-contract tests for the SFT rendering boundary.

Runs in the standard dev environment: no transformers/torch required.
Heavy tokenizer/loss-mask proofs live in test_sft_mask_proof.py.
"""

from __future__ import annotations

import copy
import json

import pytest

from training.generate_trajectories import trajectory_to_sft_examples
from training.sft_rendering import (
    TEMPLATE_PATH,
    load_role_prompt,
    normalize_tool_arguments,
    prepare_example_for_training,
    render_messages,
    role_tool_schemas,
    sha256_of,
    validate_message_sequence,
)

JUDGE = {"overall": 0.8}
REWARD = {"total": 0.75}


def _example(role: str = "remediation") -> dict:
    incident = {
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
    return next(
        e
        for e in trajectory_to_sft_examples("synthetic/widget", "single_fault", incident, JUDGE, REWARD)
        if e["role"] == role
    )


class TestNormalizeToolArguments:
    def test_dict_passthrough_identity(self):
        obj = {"widget_id": "w1"}
        assert normalize_tool_arguments(obj) is obj

    def test_valid_object_string_parses(self):
        assert normalize_tool_arguments('{"widget_id": "w1"}') == {"widget_id": "w1"}

    def test_empty_object_string_allowed(self):
        assert normalize_tool_arguments("{}") == {}

    def test_malformed_string_rejected(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            normalize_tool_arguments('{"widget_id": "w')

    def test_array_rejected(self):
        with pytest.raises(ValueError, match="JSON object"):
            normalize_tool_arguments('["a"]')

    def test_scalar_rejected(self):
        with pytest.raises(ValueError, match="JSON object"):
            normalize_tool_arguments("42")

    def test_null_rejected(self):
        with pytest.raises(ValueError, match="JSON object"):
            normalize_tool_arguments("null")


class TestCanonicalPromptInjection:
    def test_all_roles_load_nonempty_prompts(self):
        for role in ("triage", "diagnosis", "remediation", "comms"):
            text = load_role_prompt(role)
            assert text.strip()

    def test_remediation_prompt_is_canonical_runtime_text(self):
        assert load_role_prompt("remediation").startswith("# Remediation Agent System Prompt")

    def test_unknown_role_rejected(self):
        with pytest.raises(ValueError, match="unknown SFT role"):
            load_role_prompt("root")


class TestToolSchemaParity:
    def test_schemas_come_from_runtime_source_of_truth(self):
        from agents.coordinator import _tool_schema
        from agents.tool_policy import ROLE_ALLOWED_TOOLS

        for role in ("triage", "diagnosis", "remediation", "comms"):
            expected = [_tool_schema(n) for n in sorted(ROLE_ALLOWED_TOOLS.get(role, frozenset()))]
            assert role_tool_schemas(role) == expected

    def test_remediation_includes_chaos_allowlist_schema(self):
        schemas = {s["function"]["name"]: s for s in role_tool_schemas("remediation")}
        chaos = schemas["chaos_stop_experiment"]["function"]["parameters"]
        assert chaos["properties"]["namespace"]["enum"] == ["chaos-mesh"]

    def test_no_parallel_registry_for_unknown_role(self):
        with pytest.raises(ValueError, match="unknown SFT role"):
            role_tool_schemas("admin")


class TestSequenceValidation:
    def _messages(self):
        return [
            {"role": "system", "content": "s"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "inspect_widget", "arguments": {}}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        ]

    def test_wellformed_passes(self):
        validate_message_sequence(self._messages())

    def test_dangling_tool_response_rejected(self):
        msgs = self._messages()
        msgs[2]["tool_call_id"] = "ghost"
        with pytest.raises(ValueError, match="dangling"):
            validate_message_sequence(msgs)

    def test_missing_tool_response_rejected(self):
        msgs = self._messages()[:2] + [{"role": "assistant", "content": "done"}]
        with pytest.raises(ValueError, match="missing tool response"):
            validate_message_sequence(msgs)

    def test_duplicate_call_ids_rejected(self):
        msgs = self._messages()
        dup = copy.deepcopy(msgs[1])
        dup["tool_calls"][0]["id"] = "c1"
        msgs.insert(2, dup)
        with pytest.raises(ValueError, match="duplicate"):
            validate_message_sequence(msgs)

    def test_call_without_name_rejected(self):
        msgs = self._messages()
        msgs[1]["tool_calls"][0]["function"]["name"] = ""
        with pytest.raises(ValueError, match="without function name"):
            validate_message_sequence(msgs)


class TestPrepareExampleForTraining:
    def test_system_prompt_replaced_with_canonical_runtime_prompt(self):
        prepared = prepare_example_for_training(_example())
        assert prepared["messages"][0]["content"] == load_role_prompt("remediation")
        assert prepared["messages"][0]["content"].startswith("# Remediation Agent System Prompt")

    def test_source_example_is_never_mutated(self):
        original = _example()
        snapshot = copy.deepcopy(original)
        prepared = prepare_example_for_training(original)
        assert original == snapshot
        wire_args = [
            tc["function"]["arguments"]
            for m in original["messages"]
            for tc in m.get("tool_calls") or []
        ]
        # Source keeps wire-format strings; only the prepared copy has objects.
        assert any(isinstance(a, str) for a in wire_args)
        prepared_args = [
            tc["function"]["arguments"]
            for m in prepared["messages"]
            for tc in m.get("tool_calls") or []
        ]
        assert all(isinstance(a, dict) for a in prepared_args)

    def test_provenance_is_derived_and_stable(self):
        p1 = prepare_example_for_training(_example())
        p2 = prepare_example_for_training(_example())
        prov = p1["provenance"]
        assert prov["role"] == "remediation"
        assert prov["scenario_id"] == "synthetic/widget"
        assert prov["n_tool_turns"] == 2
        assert prov["template"] == TEMPLATE_PATH.name
        assert prov["template_sha256"] == sha256_of(TEMPLATE_PATH.read_text(encoding="utf-8"))
        assert prov["system_prompt_sha256"] == sha256_of(load_role_prompt("remediation"))
        assert prov["tool_schema_sha256"] == p2["provenance"]["tool_schema_sha256"]
        assert set(prov) >= {
            "role", "scenario_id", "n_tool_turns", "system_prompt_sha256",
            "tool_schema_sha256", "template", "template_sha256",
        }

    def test_unsupported_format_rejected(self):
        bad = _example()
        bad["format"] = "chatml-flat-v0"
        with pytest.raises(ValueError, match="unsupported example format"):
            prepare_example_for_training(bad)

    def test_malformed_wire_args_fail_before_rendering(self):
        bad = _example()
        for m in bad["messages"]:
            for tc in m.get("tool_calls") or []:
                tc["function"]["arguments"] = '{"widget_id": '
        with pytest.raises(ValueError, match="not valid JSON"):
            prepare_example_for_training(bad)


class TestRenderOrdering:
    def test_rendered_structure_matches_production_format(self):
        prepared = prepare_example_for_training(_example())
        out = render_messages(prepared["messages"], prepared["tools"])
        i_tools = out.index("<tools>")
        i_user = out.index("<|im_start|>user\n{\"alert\"")
        i_call1 = out.index('{"name": "inspect_widget", "arguments": {"widget_id": "widget-alpha"}}')
        i_resp1 = out.index('<tool_response>\n{"restarts": 4, "status": "degraded"}')
        i_call2 = out.index('{"name": "restart_widget", "arguments": {"widget_id": "widget-alpha"}}')
        i_resp2 = out.index('<tool_response>\n{"success": true}')
        i_final = out.index('"outcome": "resolved"')
        assert i_tools < i_user < i_call1 < i_resp1 < i_call2 < i_resp2 < i_final

    def test_semantic_args_not_double_encoded_in_tool_call_regions(self):
        prepared = prepare_example_for_training(_example())
        out = render_messages(prepared["messages"], prepared["tools"])
        call_region = out[out.index("<|im_start|>assistant"):]
        assert '"arguments": {"widget_id"' in call_region
        assert '\\"widget_id\\"' not in call_region

    def test_generation_markers_wrap_only_assistant_regions(self):
        prepared = prepare_example_for_training(_example())
        text, spans = render_messages(prepared["messages"], prepared["tools"], track_generation=True)
        # Every emitted tool_call and the conclusion live inside generation spans.
        joined = "\n".join(spans)
        assert '{"name": "inspect_widget"' in joined
        assert '{"name": "restart_widget"' in joined
        assert '"outcome": "resolved"' in joined
        assert "<|im_end|>" in joined
        # Observations, tool definitions, user/system context stay OUTSIDE spans.
        assert "<tool_response>" not in joined
        assert "# Tools" not in joined
        assert "WidgetAlphaDegraded" not in joined
        assert '"status": "degraded"' not in joined
        # Spans are exact substrings of the full rendering (no text mutation).
        for span in spans:
            assert span in text
