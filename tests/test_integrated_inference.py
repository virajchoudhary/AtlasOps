from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from bench import base_sft_validation as validation
from bench import integrated_inference as integrated

MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
MODEL_REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
MANIFEST_PIN = "a" * 64


class Row(list):
    def tolist(self):
        return list(self)

    def __getitem__(self, index):
        value = super().__getitem__(index)
        return Row(value) if isinstance(index, slice) else value


class Tensor:
    def __init__(self, values):
        self.values = list(values)
        self.shape = (1, len(self.values))
        self.device = "cpu"

    def to(self, device):
        self.device = device
        return self

    def tolist(self):
        return [list(self.values)]

    def __getitem__(self, index):
        if index == 0:
            return Row(self.values)
        raise IndexError(index)


class Encoding(dict):
    def to(self, device):
        for value in self.values():
            value.to(device)
        return self


class Tokenizer:
    def __init__(self):
        self.templates = []
        self.decoded_ids = []

    def apply_chat_template(self, messages, **kwargs):
        self.templates.append((messages, kwargs))
        return json.dumps(messages, sort_keys=True)

    def __call__(self, prompt, *, return_tensors):
        assert return_tensors == "pt"
        return Encoding(input_ids=Tensor([11, 12]), attention_mask=Tensor([1, 1]))

    def decode(self, token_ids, *, skip_special_tokens):
        assert skip_special_tokens is True
        self.decoded_ids.append(list(token_ids))
        return f"raw:{token_ids[0]}"


class Model:
    def __init__(self):
        self.adapter_enabled = True
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.guard = threading.Lock()
        self.fail = False

    @contextlib.contextmanager
    def disable_adapter(self):
        previous = self.adapter_enabled
        self.adapter_enabled = False
        try:
            yield
        finally:
            self.adapter_enabled = previous

    def generate(self, **kwargs):
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.01)
            if self.fail:
                raise RuntimeError("fixture-secret-must-not-be-journaled")
            self.calls.append(self.adapter_enabled)
            generated = 202 if self.adapter_enabled else 101
            return Tensor([*kwargs["input_ids"].tolist()[0], generated])
        finally:
            with self.guard:
                self.active -= 1


def _request(*, tools=True, **config):
    result = {
        "model": "coordinator-model-name",
        "tool_choice": "auto",
        "messages": [
            {"role": "system", "content": "Run the bounded task."},
            {"role": "user", "content": "Inspect service health."},
        ],
    }
    if tools:
        result["tools"] = [
            {"type": "function", "function": {"name": "inspect", "parameters": {"type": "object"}}}
        ]
    result.update(config)
    return result


@pytest.fixture
def rig(tmp_path, monkeypatch):
    checkpoint = tmp_path / "checkpoint"
    base = tmp_path / "base"
    checkpoint.mkdir()
    base.mkdir()
    inventory = {
        "repository": MODEL_ID,
        "revision": MODEL_REVISION,
        "snapshot_dir": str(base.resolve()),
        "model_weights_loaded": False,
        "network_accessed": False,
        "files": {str(base / "model.safetensors"): "b" * 64},
        "weight_metadata": {"sha256": "c" * 64},
        "tokenizer_manifest_sha256": "d" * 64,
        "total_files": 1,
        "total_bytes": 10,
    }
    manifest = {
        "role_filter": "all",
        "dataset": {
            "role_counts": {
                "triage": 1,
                "diagnosis": 1,
                "remediation": 1,
                "comms": 1,
            }
        },
        "base_model": {"id": MODEL_ID, "resolved_revision": MODEL_REVISION},
        "tokenizer": {"id": MODEL_ID, "resolved_revision": MODEL_REVISION},
        "checkpoint": {"tree_sha256": "e" * 64, "files": []},
    }
    pin = {"value": MANIFEST_PIN}
    evaluator = SimpleNamespace(
        _load_checkpoint_manifest=lambda path: (manifest, pin["value"])
    )
    collector = SimpleNamespace(
        MODEL_REPOSITORY=MODEL_ID,
        MODEL_REVISION=MODEL_REVISION,
        collect_model_inventory=lambda *, snapshot_dir: dict(inventory),
    )
    monkeypatch.setattr(validation, "_runtime_modules", lambda: (evaluator, collector))
    tokenizer = Tokenizer()
    model = Model()

    class Cuda:
        def manual_seed_all(self, seed):
            assert seed == 1337

    fake_torch = SimpleNamespace(
        cuda=Cuda(),
        manual_seed=lambda seed: seed,
        no_grad=contextlib.nullcontext,
    )

    def fake_load(runner):
        runner.torch = fake_torch
        runner.tokenizer = tokenizer
        runner.model = model
        runner.runtime = {"fixture": True}

    monkeypatch.setattr(validation.LocalPairedInference, "load", fake_load)
    journal = tmp_path / "raw-inference.jsonl"
    engine = integrated.PairedCompletionEngine(
        checkpoint=checkpoint,
        checkpoint_manifest_sha256=MANIFEST_PIN,
        base_snapshot=base,
        base_snapshot_inventory_sha256=integrated.base_snapshot_inventory_sha256(inventory),
        journal_path=journal,
        fixture_backend=True,
    )
    return SimpleNamespace(
        engine=engine,
        base=engine.provider("base"),
        sft=engine.provider("sft"),
        checkpoint=checkpoint,
        base_snapshot=base,
        journal=journal,
        inventory=inventory,
        pin=pin,
        model=model,
        tokenizer=tokenizer,
    )


def _rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_all_roles_share_one_serialized_engine_and_preserve_raw_tool_templates(rig):
    async def run():
        history = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "inspect", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": '{"ok":true}'},
        ]
        calls = [
            rig.base(
                role,
                _request(
                    tools=(index % 2 == 0),
                    temperature=0.2,
                    **({"seed": 42} if index == 0 else {}),
                    **({"messages": history + _request()["messages"]} if index == 0 else {}),
                ),
            )
            if arm == "base"
            else rig.sft(role, _request(tools=(index % 2 == 0), temperature=0.2))
            for index, (arm, role) in enumerate(
                (("base", "triage"), ("sft", "diagnosis"), ("base", "remediation"), ("sft", "comms"))
            )
        ]
        results = await asyncio.gather(*calls)
        assert [result["choices"][0]["message"]["content"] for result in results] == [
            "raw:101", "raw:202", "raw:101", "raw:202"
        ]
        assert all("tool_calls" not in result["choices"][0]["message"] for result in results)
        assert rig.model.calls.count(False) == 2
        assert rig.model.calls.count(True) == 2
        assert rig.model.max_active == 1

    asyncio.run(run())
    assert rig.tokenizer.templates[0][1]["tools"] == _request()["tools"]
    assert "tools" not in rig.tokenizer.templates[1][1]
    assert rig.tokenizer.templates[0][0][0]["role"] == "assistant"
    rows = _rows(rig.journal)
    assert [row["record"] for row in rows] == [
        "attempt_started", "attempt_finished",
    ] * 4
    first_start, first_end = rows[:2]
    assert first_start["requested_generation_config"]["temperature"] == 0.2
    assert first_start["requested_generation_config"]["seed"] == 42
    assert first_start["effective_generation_config"]["temperature"] == 0.0
    assert first_start["effective_generation_config"]["seed"] == 1337
    assert first_start["requested_request"]["model"] == "coordinator-model-name"
    assert first_start["requested_tool_choice"] == "auto"
    assert first_start["effective_tool_choice"] == "raw assistant content only"
    assert first_end["raw_model_response"] == "raw:101"
    assert first_end["prompt_token_ids"] == [11, 12]
    assert first_end["generated_token_ids"] == [101]
    assert first_end["prompt_token_ids_sha256"]
    assert first_end["generated_token_ids_sha256"]
    assert first_end["raw_model_response_sha256"]
    assert all(row["evidence_class"] == "NON_EMPIRICAL" for row in rows)
    assert all(row["certification_status"] == "NOT_CERTIFIED" for row in rows)


def test_validation_rejects_unsafe_or_unmatched_requests_before_opening_journal(rig):
    async def run():
        with pytest.raises(ValueError, match="token budget"):
            await rig.sft("remediation", _request(max_tokens=256))
        with pytest.raises(ValueError, match="secret"):
            await rig.base(
                "diagnosis",
                {"messages": [{"role": "user", "content": '{"api_key":"not-allowed"}'}]},
            )
        with pytest.raises(ValueError, match="role"):
            await rig.base("operator", _request())
        with pytest.raises(ValueError, match="arm"):
            rig.engine.provider("combined")

    asyncio.run(run())
    assert not rig.journal.exists()
    assert rig.model.calls == []


def test_failure_is_durable_categorized_and_does_not_log_exception_values(rig):
    rig.model.fail = True

    async def run():
        with pytest.raises(integrated.InferenceProviderError) as error:
            await rig.base("remediation", _request())
        assert error.value.failure_category == "generation_failure"

    asyncio.run(run())
    rows = _rows(rig.journal)
    assert rows[0]["record"] == "attempt_started"
    assert rows[1]["record"] == "attempt_finished"
    assert rows[1]["inference_status"] == "failed"
    assert rows[1]["failure_category"] == "generation_failure"
    assert "fixture-secret" not in rig.journal.read_text(encoding="utf-8")
    assert rows[1]["raw_model_response"] is None


def test_final_inventory_check_and_exclusive_journal(rig):
    async def run():
        await rig.base("triage", _request(tools=False))
        result = await rig.engine.verify_final()
        assert result["base_snapshot_inventory_sha256"] == rig.engine.base_snapshot_inventory_sha256
        await rig.engine.close()
        second = integrated.PairedCompletionEngine(
            checkpoint=rig.checkpoint,
            checkpoint_manifest_sha256=MANIFEST_PIN,
            base_snapshot=rig.base_snapshot,
            base_snapshot_inventory_sha256=rig.engine.base_snapshot_inventory_sha256,
            journal_path=rig.journal,
            fixture_backend=True,
        )
        with pytest.raises(FileExistsError):
            await second.provider("base")("triage", _request())

    asyncio.run(run())
    assert _rows(rig.journal)[-1]["record"] == "final_verification"


def test_direct_action_policy_returns_raw_completion_and_strips_untrusted_state(
    rig, monkeypatch
):
    observed_public_state = {}
    coordinator = ModuleType("agents.coordinator")
    coordinator._strip_model_forbidden_context = lambda state: {
        key: value for key, value in state.items() if key != "scenario_id"
    }
    grpo_eval = ModuleType("bench.grpo_eval")

    def public_policy_state(state):
        observed_public_state.update(state)
        return dict(state)

    grpo_eval._public_policy_state = public_policy_state
    monkeypatch.setitem(__import__("sys").modules, "agents.coordinator", coordinator)
    monkeypatch.setitem(__import__("sys").modules, "bench.grpo_eval", grpo_eval)
    monkeypatch.setattr(
        "training.sft_rendering.load_role_prompt",
        lambda role: "Canonical remediation prompt.",
    )
    tool_schemas = [{"type": "function", "function": {"name": "restart", "parameters": {}}}]
    monkeypatch.setattr("training.sft_rendering.role_tool_schemas", lambda role: tool_schemas)
    policy = integrated.DirectActionCompletionPolicy(rig.sft)

    async def run():
        raw = await policy.generate(
            {
                "service": "payment",
                "symptom": "latency",
                "_runtime_control": {"approved": True},
                "approval": {"approved": True},
                "scenario_id": "not-model-visible",
            },
            seed=1337,
            generation_config={"temperature": 0.2, "max_new_tokens": 512},
        )
        assert raw == "raw:202"

    asyncio.run(run())
    rows = _rows(rig.journal)
    assert "tools" not in rows[0]["requested_request"]
    system = rows[0]["requested_request"]["messages"][0]["content"]
    assert "Canonical remediation prompt." not in system
    assert "Runtime action schemas" in system
    assert json.dumps(tool_schemas, sort_keys=True, ensure_ascii=False) in system
    user_state = json.loads(rows[0]["requested_request"]["messages"][1]["content"])
    assert user_state["instruction"] == integrated.ACTION_INSTRUCTION
    assert "_runtime_control" not in user_state
    assert "approval" not in user_state
    assert "scenario_id" not in user_state
    assert "_runtime_control" not in observed_public_state
    assert "approval" not in observed_public_state
    assert "tool, arguments, and agent_claimed_resolved" in (
        rows[0]["requested_request"]["messages"][0]["content"]
    )
    assert len(rows) == 2
