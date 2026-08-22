"""Training-stack integration proofs for SFT tool-template wiring.

These tests run ONLY under the optional training extras with a locally cached
Qwen tokenizer (no model weights):

    pip install -e .[train] -c requirements/train-constraints.txt
    set ATLASOPS_SFT_INTEGRATION=1
    pytest tests/test_sft_mask_proof.py -q

They prove, at token level, that the project-owned generation-marker template
yields exactly the intended loss semantics: assistant tool calls and conclusions
are targets; system/user/tool-definitions/tool observations are context only.
Standard CI skips this module entirely.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("transformers", reason="transformers not installed (optional train extra)")

requires_train_env = pytest.mark.skipif(
    os.environ.get("ATLASOPS_SFT_INTEGRATION") != "1",
    reason="set ATLASOPS_SFT_INTEGRATION=1 with train extras + cached tokenizer",
)
TOKENIZER_REPO = os.environ.get("ATLASOPS_SFT_TOKENIZER_REPO", "Qwen/Qwen2.5-7B-Instruct")


def _example(role="remediation"):
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
        # A role whose recorded behaviour is prose-only exercises the no-tool path.
        "comms": {
            "trajectory": [{"role": "comms", "turn": 0, "content": "Widget alpha restored."}],
            "final": {"summary_for_dashboard": "Widget alpha restored."},
        },
    }
    return next(
        e
        for e in __import__(
            "training.generate_trajectories", fromlist=["trajectory_to_sft_examples"]
        ).trajectory_to_sft_examples("synthetic/widget", "single_fault", incident, {}, {})
        if e["role"] == role
    )


@pytest.fixture(scope="module")
def tokenizer():
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(TOKENIZER_REPO)


@pytest.fixture(scope="module")
def encoded(tokenizer):
    from training.sft_rendering import encode_example

    return encode_example(tokenizer, _example())


@requires_train_env
class TestTokenizedContract:
    def test_encode_shapes(self, encoded):
        ids = list(encoded["input_ids"])
        masks = [bool(m) for m in encoded["assistant_masks"]]
        assert len(ids) > 0 and len(masks) == len(ids)
        assert any(masks), "generation regions produced zero trainable tokens"
        assert not all(masks), "everything trainable would mean masking is broken"

    def test_tokenization_is_stable(self, tokenizer, encoded):
        from training.sft_rendering import encode_example

        again = encode_example(tokenizer, _example())
        assert list(encoded["input_ids"]) == list(again["input_ids"])

    def test_special_tokens_present(self, encoded, tokenizer):
        text = tokenizer.decode(list(encoded["input_ids"]))
        assert "<|im_start|>" in text and "<|im_end|>" in text


@requires_train_env
class TestLossSemantics:
    def _split(self, tokenizer, encoded):
        ids = list(encoded["input_ids"])
        masks = [bool(m) for m in encoded["assistant_masks"]]
        target_text = tokenizer.decode([i for i, m in zip(ids, masks) if m])
        context_text = tokenizer.decode([i for i, m in zip(ids, masks) if not m])
        return context_text, target_text

    def test_assistant_tool_calls_are_targets(self, tokenizer, encoded):
        _, target = self._split(tokenizer, encoded)
        assert '{"name": "inspect_widget"' in target
        assert '{"name": "restart_widget"' in target
        assert '"outcome": "resolved"' in target
        assert "<|im_end|>" in target

    def test_tool_observations_are_context_only(self, tokenizer, encoded):
        context, target = self._split(tokenizer, encoded)
        assert '<tool_response>\n{"restarts": 4, "status": "degraded"}' in context
        assert '<tool_response>\n{"success": true}' in context
        assert '"restarts": 4' not in target
        assert '"success": true' not in target

    def test_system_tools_and_user_are_context_only(self, tokenizer, encoded):
        context, target = self._split(tokenizer, encoded)
        assert "# Remediation Agent System Prompt" in context
        assert "# Tools" in context
        # Real runtime remediation schemas are supplied as context.
        assert '"promql_query"' in context
        assert '"alertmanager_silence"' in context
        # Synthetic fixture call payloads belong to targets, never to schemas.
        assert "WidgetAlphaDegraded" in context
        assert "# Remediation Agent System Prompt" not in target
        assert "# Tools" not in target
        assert '"promql_query"' not in target

    def test_emitted_calls_never_appear_as_context(self, tokenizer, encoded):
        context, _ = self._split(tokenizer, encoded)
        assert '{"name": "inspect_widget"' not in context
        assert '{"name": "restart_widget"' not in context


@requires_train_env
class TestNoToolAndEmptyArgExamples:
    def test_prose_only_example_trains_only_its_own_text(self, tokenizer):
        from training.sft_rendering import encode_example

        encoded = encode_example(tokenizer, _example(role="comms"))
        ids = list(encoded["input_ids"])
        masks = [bool(m) for m in encoded["assistant_masks"]]
        target = tokenizer.decode([i for i, m in zip(ids, masks) if m])
        context = tokenizer.decode([i for i, m in zip(ids, masks) if not m])
        assert "Widget alpha restored." in target
        assert "Widget alpha restored." not in context
        assert '"slack_post_update"' in context  # comms tool schemas still supplied as context

    def test_empty_argument_call_renders_semantic_empty_object(self, tokenizer):
        from training.sft_rendering import encode_example

        incident = {
            "incident_id": "inc-empty-args",
            "remediation": {
                "input": {"alert": "PingProbe"},
                "trajectory": [{
                    "role": "remediation",
                    "turn": 0,
                    "tool": "noop_ping_all",
                    "args": "{}",
                    "output": {"success": True},
                }],
                "final": {"outcome": "unresolved"},
            },
        }
        builder = __import__(
            "training.generate_trajectories", fromlist=["trajectory_to_sft_examples"]
        )
        example = next(
            e
            for e in builder.trajectory_to_sft_examples("synthetic/empty", "single_fault", incident, {}, {})
            if e["role"] == "remediation"
        )
        encoded = encode_example(tokenizer, example)
        target = tokenizer.decode([
            i for i, m in zip(encoded["input_ids"], encoded["assistant_masks"]) if m
        ])
        assert '"arguments": {}' in target


@requires_train_env
class TestTrlMechanismLinkage:
    def test_installed_trl_supports_assistant_only_loss_and_masks(self):
        import tempfile

        trl = pytest.importorskip("trl")
        version = tuple(int(p) for p in trl.__version__.split(".")[:2] if p.isdigit())
        assert version >= (0, 12), f"TRL {trl.__version__} predates assistant_only_loss"
        from trl import SFTConfig

        cfg = SFTConfig(output_dir=tempfile.mkdtemp(), assistant_only_loss=True, bf16=False)
        assert cfg.assistant_only_loss is True
        # Mechanism linkage: SFTTrainer must request generation-token masks from
        # apply_chat_template when the flag is enabled.
        import inspect

        import trl.trainer.sft_trainer as sft_mod

        src = inspect.getsource(sft_mod)
        assert "assistant_tokens_mask" in src or "assistant_masks" in src
