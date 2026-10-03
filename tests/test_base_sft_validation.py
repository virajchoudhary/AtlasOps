from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar

import pytest

from bench import base_sft_validation as comparison

MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
MODEL_REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
MANIFEST_PIN = "a" * 64


class FakeRow(list):
    def tolist(self):
        return list(self)

    def __getitem__(self, key):
        value = super().__getitem__(key)
        return FakeRow(value) if isinstance(key, slice) else value


class FakeTensor:
    def __init__(self, values, device="cpu"):
        self.values = list(values)
        self.device = device
        self.shape = (1, len(self.values))

    def to(self, device):
        self.device = device
        return self

    def tolist(self):
        return [list(self.values)]

    def __getitem__(self, index):
        if index != 0:
            raise IndexError(index)
        return FakeRow(self.values)


class FakeEncoding(dict):
    def to(self, device):
        for value in self.values():
            value.to(device)
        return self


class FakeTokenizer:
    def __init__(self, decoded_text=None):
        self.decoded_text = decoded_text or json.dumps(
            {
                "severity": "P1",
                "affected_services": ["api"],
                "root_cause": "disk full",
                "confidence": 0.75,
            }
        )
        self.prompts = []

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        assert tokenize is False
        assert add_generation_prompt is True
        return json.dumps(messages, sort_keys=True)

    def __call__(self, prompt, *, return_tensors):
        assert return_tensors == "pt"
        self.prompts.append(prompt)
        return FakeEncoding(
            input_ids=FakeTensor([11, 12, 13]),
            attention_mask=FakeTensor([1, 1, 1]),
        )

    def decode(self, token_ids, *, skip_special_tokens):
        assert skip_special_tokens is True
        return self.decoded_text


class FakeModel:
    def __init__(self, *, fail_base=False):
        self.adapter_enabled = True
        self.fail_base = fail_base
        self.calls = []
        self.hf_device_map = {"": 0}

    def eval(self):
        return self

    def parameters(self):
        return [SimpleNamespace(device="cuda:0")]

    def buffers(self):
        return []

    @contextlib.contextmanager
    def disable_adapter(self):
        previous = self.adapter_enabled
        self.adapter_enabled = False
        try:
            yield
        finally:
            self.adapter_enabled = previous

    def generate(self, **kwargs):
        self.calls.append((self.adapter_enabled, kwargs))
        if not self.adapter_enabled and self.fail_base:
            raise RuntimeError("base generation failed")
        generated = [101] if not self.adapter_enabled else [202]
        prompt_ids = kwargs["input_ids"].tolist()[0]
        return FakeTensor([*prompt_ids, *generated], device="cuda:0")


class FakeCuda:
    def __init__(self):
        self.device = 0
        self.seed_calls = []

    def is_available(self):
        return True

    def device_count(self):
        return 1

    def set_device(self, device):
        self.device = device

    def current_device(self):
        return self.device

    def manual_seed_all(self, seed):
        self.seed_calls.append(seed)

    def get_device_name(self, device):
        assert device == 0
        return "fixture-T4"


def _install_fake_loaders(monkeypatch, *, decoded_text=None, fail_base=False):
    tokenizer = FakeTokenizer(decoded_text=decoded_text)
    model = FakeModel(fail_base=fail_base)
    torch_module = ModuleType("torch")
    torch_module.__version__ = "2.7.1"
    torch_module.float16 = "float16"
    torch_module.version = SimpleNamespace(cuda="12.8")
    torch_module.cuda = FakeCuda()
    torch_module.manual_seed_calls = []
    torch_module.manual_seed = lambda seed: torch_module.manual_seed_calls.append(seed)
    torch_module.no_grad = contextlib.nullcontext

    calls = {"tokenizer": [], "model": [], "peft": []}

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            calls["tokenizer"].append((args, kwargs))
            return tokenizer

    class AutoModelForCausalLM:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            calls["model"].append((args, kwargs))
            return object()

    class BitsAndBytesConfig:
        def __init__(self, **kwargs):
            self.values = kwargs

    class PeftModel:
        @staticmethod
        def from_pretrained(base_model, *args, **kwargs):
            calls["peft"].append((args, kwargs))
            return model

    transformers_module = ModuleType("transformers")
    transformers_module.__version__ = "4.55.0"
    transformers_module.AutoTokenizer = AutoTokenizer
    transformers_module.AutoModelForCausalLM = AutoModelForCausalLM
    transformers_module.BitsAndBytesConfig = BitsAndBytesConfig
    peft_module = ModuleType("peft")
    peft_module.__version__ = "0.16.0"
    peft_module.PeftModel = PeftModel
    versions = {
        "torch": torch_module.__version__,
        "transformers": transformers_module.__version__,
        "peft": peft_module.__version__,
        "bitsandbytes": "0.45.0",
    }
    monkeypatch.setattr(
        comparison.importlib.metadata,
        "version",
        lambda name: versions[name],
    )
    monkeypatch.setitem(sys.modules, "torch", torch_module)
    monkeypatch.setitem(sys.modules, "transformers", transformers_module)
    monkeypatch.setitem(sys.modules, "peft", peft_module)
    return SimpleNamespace(
        tokenizer=tokenizer,
        model=model,
        torch=torch_module,
        calls=calls,
    )


def _fixture_context(monkeypatch, tmp_path, *, split_ids=("val/a", "val/b")):
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    original_dataset = {
        "data_origin": "scenario_derived_synthetic_review_candidate",
        "synthetic": True,
        "data_origin_source": "adjacent_corpus_manifest",
        "corpus_sha256_canonical_lf": "c" * 64,
        "split": "train",
        "split_sha256": "d" * 64,
        "total_examples": 68,
        "total_scenarios": 21,
        "corpus_manifest": {
            "present": True,
            "path": "/provenance/train-corpus-manifest.json",
            "sha256": "e" * 64,
        },
    }
    checkpoint_manifest_path = checkpoint / "sft_run_manifest.json"
    checkpoint_manifest_path.write_text(
        json.dumps({"dataset": original_dataset}),
        encoding="utf-8",
    )
    checkpoint_sha = hashlib.sha256(checkpoint_manifest_path.read_bytes()).hexdigest()
    base_snapshot = tmp_path / "base-snapshot"
    base_snapshot.mkdir()
    output = tmp_path / "output"

    manifest = {
        "base_model": {"id": MODEL_ID, "resolved_revision": MODEL_REVISION},
        "tokenizer": {"id": MODEL_ID, "resolved_revision": MODEL_REVISION},
        "checkpoint": {
            "tree_sha256": "f" * 64,
            "files": [{"path": "adapter.safetensors", "sha256": "9" * 64}],
        },
    }
    metadata = {
        scenario_id: SimpleNamespace(
            expected_alert=f"Alert-{scenario_id}",
            expected_root_cause="disk full",
            tier="single_fault",
        )
        for scenario_id in split_ids
    }

    class FakeEvaluator:
        SPLIT_SEEDS: ClassVar[dict[str, int]] = {"val": 1337}
        SCENARIO_CATALOG: ClassVar[dict[str, Any]] = metadata

        @staticmethod
        def _source_provenance():
            return {"git_sha": "1" * 40, "git_dirty": False}

        @staticmethod
        def get_split(name):
            assert name == "val"
            return tuple(split_ids)

        @staticmethod
        def _load_checkpoint_manifest(_checkpoint):
            return manifest, checkpoint_sha

        @staticmethod
        def _public_input(meta):
            return {"alert": meta.expected_alert}

        @staticmethod
        def _messages(public_input):
            return [{"role": "user", "content": json.dumps(public_input, sort_keys=True)}]

        @staticmethod
        def _parse_prediction(text):
            prediction = json.loads(text)
            if prediction.get("severity") not in {"P0", "P1", "P2", "P3"}:
                raise ValueError("invalid severity")
            if not prediction.get("root_cause"):
                raise ValueError("missing root cause")
            return prediction

        @staticmethod
        def _compute_diagnostic_f1(predicted, expected):
            score = 1.0 if predicted == expected else 0.0
            return {"precision": score, "recall": score, "f1": score}

    class FakeCollector:
        MODEL_REPOSITORY = MODEL_ID
        MODEL_REVISION = MODEL_REVISION

        def __init__(self):
            self.inventory_calls = []

        def collect_model_inventory(self, *, snapshot_dir):
            snapshot_dir = Path(snapshot_dir).resolve()
            self.inventory_calls.append(str(snapshot_dir))
            return {
                "repository": MODEL_ID,
                "revision": MODEL_REVISION,
                "snapshot_dir": str(snapshot_dir),
                "model_weights_loaded": False,
                "network_accessed": False,
                "files": {
                    str(snapshot_dir / "weights.safetensors"): "2" * 64,
                    str(snapshot_dir / "tokenizer.json"): "3" * 64,
                },
                "file_details": [],
            }

    collector = FakeCollector()
    monkeypatch.setattr(
        comparison,
        "_runtime_modules",
        lambda: (FakeEvaluator, collector),
    )
    return SimpleNamespace(
        checkpoint=checkpoint,
        checkpoint_sha=checkpoint_sha,
        base_snapshot=base_snapshot,
        output=output,
        manifest=manifest,
        collector=collector,
        evaluator=FakeEvaluator,
    )


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_exact_manifest_pin_and_matched_prompts_seed_config_and_loader(monkeypatch, tmp_path):
    fixture = _fixture_context(monkeypatch, tmp_path)
    fakes = _install_fake_loaders(monkeypatch)

    summary = comparison.run_base_sft_validation(
        checkpoint=fixture.checkpoint,
        checkpoint_manifest_sha256=fixture.checkpoint_sha,
        base_snapshot=fixture.base_snapshot,
        output_dir=fixture.output,
        run_id="paired-val-run",
    )

    raw_rows = _read_jsonl(fixture.output / "raw_episodes.jsonl")
    scored_rows = _read_jsonl(fixture.output / "episodes.jsonl")
    run_manifest = json.loads((fixture.output / "run_manifest.json").read_text())

    assert summary["lifecycle"] == "completed"
    assert len(raw_rows) == 4
    assert run_manifest["status"] == "completed"
    assert run_manifest["split"] == "val"
    assert run_manifest["scenario_ids"] == ["val/a", "val/b"]
    assert run_manifest["split_sha256"] == comparison._canonical_sha256(["val/a", "val/b"])
    assert (
        run_manifest["raw_predictions_sha256"]
        == hashlib.sha256((fixture.output / "episodes.jsonl").read_bytes()).hexdigest()
    )
    assert [(row["scenario_id"], row["arm"]) for row in raw_rows] == [
        ("val/a", "base"),
        ("val/a", "sft"),
        ("val/b", "base"),
        ("val/b", "sft"),
    ]
    for base, sft in zip(raw_rows[::2], raw_rows[1::2], strict=True):
        assert base["request_messages"] == sft["request_messages"]
        assert base["prompt_token_ids"] == sft["prompt_token_ids"] == [11, 12, 13]
        assert base["prompt_token_ids_sha256"] == sft["prompt_token_ids_sha256"]
        assert base["evaluation_config_sha256"] == sft["evaluation_config_sha256"]
        assert base["generation_config"] == sft["generation_config"]
        assert base["evaluation_generation_config"] == sft["evaluation_generation_config"]
    assert [call[0] for call in fakes.model.calls] == [False, True, False, True]
    assert fakes.torch.manual_seed_calls == [1337] * 4
    assert fakes.torch.cuda.seed_calls == [1337] * 4
    assert run_manifest["evaluation_config"]["seed"] == 1337
    assert run_manifest["evaluation_config"]["temperature"] == 0.0
    assert run_manifest["evaluation_config"]["top_p"] == 1.0
    assert run_manifest["evaluation_config"]["max_new_tokens"] == 512
    assert run_manifest["evaluation_config"]["attention_implementation"] == "sdpa"
    assert run_manifest["runtime"]["platform"]["python_version"]
    assert run_manifest["runtime"]["package_versions"]["bitsandbytes"] == "0.45.0"
    assert run_manifest["runtime"]["cuda_runtime_version"] == "12.8"
    model_args, model_kwargs = fakes.calls["model"][0]
    assert model_args == (str(fixture.base_snapshot),)
    assert model_kwargs["local_files_only"] is True
    assert model_kwargs["device_map"] == {"": 0}
    assert model_kwargs["torch_dtype"] == "float16"
    assert model_kwargs["attn_implementation"] == "sdpa"
    quantization = model_kwargs["quantization_config"].values
    assert quantization == {
        "load_in_4bit": True,
        "bnb_4bit_quant_type": "nf4",
        "bnb_4bit_use_double_quant": True,
        "bnb_4bit_compute_dtype": "float16",
    }
    assert (
        run_manifest["checkpoint_data_provenance_as_recorded"]["fields"]["data_origin"]
        == "scenario_derived_synthetic_review_candidate"
    )
    assert run_manifest["checkpoint_data_provenance_as_recorded"]["fields"]["synthetic"] is True
    assert len(scored_rows) == 4
    assert all(row["status"] == "ok" for row in scored_rows)
    assert all(row["evaluation_generation_config"]["seed"] == 1337 for row in scored_rows)
    assert all(row["generated_token_ids"] in ([101], [202]) for row in scored_rows)
    assert all(
        row["resolved"] is None
        and row["reward_contract"] is None
        and row["safety"] is None
        and row["time_to_resolve_s"] is None
        for row in scored_rows
    )
    assert summary["per_arm"]["base"]["format_compliance_denominator"].startswith("all scheduled")
    assert summary["per_arm"]["base"]["format_compliance_rate"] == 1.0
    assert summary["resolution_rate"] is None
    assert summary["avg_reward"] is None
    assert summary["avg_time_to_resolve_s"] is None
    assert fixture.collector.inventory_calls == [
        str(fixture.base_snapshot.resolve()),
        str(fixture.base_snapshot.resolve()),
    ]
    assert (
        run_manifest["artifacts"]["raw_episodes_sha256"]
        == hashlib.sha256((fixture.output / "raw_episodes.jsonl").read_bytes()).hexdigest()
    )
    assert (
        run_manifest["artifacts"]["episodes_sha256"]
        == hashlib.sha256((fixture.output / "episodes.jsonl").read_bytes()).hexdigest()
    )


def test_manifest_pin_mismatch_is_preserved_as_failed_scheduled_rows(monkeypatch, tmp_path):
    fixture = _fixture_context(monkeypatch, tmp_path)
    fakes = _install_fake_loaders(monkeypatch)

    summary = comparison.run_base_sft_validation(
        checkpoint=fixture.checkpoint,
        checkpoint_manifest_sha256="b" * 64,
        base_snapshot=fixture.base_snapshot,
        output_dir=fixture.output,
        run_id="bad-pin",
    )

    raw_rows = _read_jsonl(fixture.output / "raw_episodes.jsonl")
    run_manifest = json.loads((fixture.output / "run_manifest.json").read_text())
    assert summary["lifecycle"] == "failed"
    assert summary["fatal_error"]["error_message"].startswith("Checkpoint manifest does not match")
    assert raw_rows == []
    assert (fixture.output / "episodes.jsonl").read_bytes() == b""
    assert summary["not_executed_arm_row_count"] == 4
    assert fakes.calls["model"] == []
    assert fixture.collector.inventory_calls == []
    assert run_manifest["lifecycle"] == "failed"


def test_base_adapter_context_restores_enabled_state_when_generation_raises():
    runner = comparison.LocalPairedInference(Path("."), Path("."))
    runner.model = FakeModel(fail_base=True)
    runner.tokenizer = FakeTokenizer()
    runner.torch = SimpleNamespace(
        manual_seed=lambda _seed: None,
        cuda=SimpleNamespace(manual_seed_all=lambda _seed: None),
        no_grad=contextlib.nullcontext,
    )
    inputs = FakeEncoding(
        input_ids=FakeTensor([1, 2], "cuda:0"),
        attention_mask=FakeTensor([1, 1], "cuda:0"),
    )

    with pytest.raises(RuntimeError, match="base generation failed"):
        runner.generate("base", inputs)

    assert runner.model.adapter_enabled is True


def test_interrupt_persists_active_arm_and_stops_scheduling(monkeypatch, tmp_path):
    fixture = _fixture_context(monkeypatch, tmp_path)
    fakes = _install_fake_loaders(monkeypatch)

    def interrupt(**_kwargs):
        raise KeyboardInterrupt()

    fakes.model.generate = interrupt
    with pytest.raises(KeyboardInterrupt):
        comparison.run_base_sft_validation(
            checkpoint=fixture.checkpoint,
            checkpoint_manifest_sha256=fixture.checkpoint_sha,
            base_snapshot=fixture.base_snapshot,
            output_dir=fixture.output,
            run_id="interrupted",
        )

    raw_rows = _read_jsonl(fixture.output / "raw_episodes.jsonl")
    episode_rows = _read_jsonl(fixture.output / "episodes.jsonl")
    run_manifest = json.loads((fixture.output / "run_manifest.json").read_text())
    assert len(raw_rows) == len(episode_rows) == 1
    assert raw_rows[0]["scenario_id"] == "val/a"
    assert raw_rows[0]["arm"] == "base"
    assert episode_rows[0]["status"] == "interrupted"
    assert episode_rows[0]["diagnostic_f1"] is None
    assert fakes.model.adapter_enabled is True
    assert run_manifest["status"] == "failed"
    assert run_manifest["recorded_arm_row_count"] == 1
    assert run_manifest["not_executed_arm_row_count"] == 3


def test_invalid_response_keeps_raw_text_ids_and_null_f1(monkeypatch, tmp_path):
    fixture = _fixture_context(monkeypatch, tmp_path, split_ids=("val/only",))
    fakes = _install_fake_loaders(monkeypatch, decoded_text="not valid JSON")

    summary = comparison.run_base_sft_validation(
        checkpoint=fixture.checkpoint,
        checkpoint_manifest_sha256=fixture.checkpoint_sha,
        base_snapshot=fixture.base_snapshot,
        output_dir=fixture.output,
        run_id="invalid-output",
    )

    raw_rows = _read_jsonl(fixture.output / "raw_episodes.jsonl")
    scored_rows = _read_jsonl(fixture.output / "episodes.jsonl")
    assert summary["lifecycle"] == "completed"
    assert len(raw_rows) == len(scored_rows) == 2
    assert all(row["raw_model_response"] == "not valid JSON" for row in raw_rows)
    assert all(row["generated_token_ids"] in ([101], [202]) for row in raw_rows)
    assert all(row["status"] == "invalid" for row in scored_rows)
    assert all(row["format_compliant"] is False for row in scored_rows)
    assert all(row["diagnostic_f1"] is None for row in scored_rows)
    assert summary["per_arm"]["base"]["format_compliance_rate"] == 0.0
    assert summary["per_arm"]["base"]["format_compliance_denominator"] == (
        "all scheduled arm rows, including failures"
    )
    assert len(fakes.tokenizer.prompts) == 1


def test_dirty_source_refuses_before_output_creation(monkeypatch, tmp_path):
    fixture = _fixture_context(monkeypatch, tmp_path)
    fixture.evaluator._source_provenance = staticmethod(
        lambda: {"git_sha": "1" * 40, "git_dirty": True}
    )

    with pytest.raises(ValueError, match="clean evaluator source tree"):
        comparison.run_base_sft_validation(
            checkpoint=fixture.checkpoint,
            checkpoint_manifest_sha256=fixture.checkpoint_sha,
            base_snapshot=fixture.base_snapshot,
            output_dir=fixture.output,
            run_id="dirty-source",
        )

    assert not fixture.output.exists()


def test_existing_output_directory_refuses_before_source_probe(monkeypatch, tmp_path):
    fixture = _fixture_context(monkeypatch, tmp_path)
    fixture.output.mkdir()
    source_probe_called = False

    def source_probe():
        nonlocal source_probe_called
        source_probe_called = True
        return {"git_sha": "1" * 40, "git_dirty": False}

    fixture.evaluator._source_provenance = staticmethod(source_probe)
    with pytest.raises(FileExistsError, match="already exists"):
        comparison.run_base_sft_validation(
            checkpoint=fixture.checkpoint,
            checkpoint_manifest_sha256=fixture.checkpoint_sha,
            base_snapshot=fixture.base_snapshot,
            output_dir=fixture.output,
            run_id="collision",
        )
    assert source_probe_called is False


def test_unsupported_split_rejected_before_runtime_module_or_split_lookup(monkeypatch, tmp_path):
    def forbidden_runtime_modules():
        pytest.fail("unsupported split must be rejected before imports and data lookup")

    monkeypatch.setattr(comparison, "_runtime_modules", forbidden_runtime_modules)
    with pytest.raises(ValueError, match="Validation-only"):
        comparison.run_base_sft_validation(
            checkpoint=tmp_path / "checkpoint",
            checkpoint_manifest_sha256=MANIFEST_PIN,
            base_snapshot=tmp_path / "base",
            output_dir=tmp_path / "output",
            run_id="no-test-access",
            split_name="test",
        )
    assert not (tmp_path / "output").exists()
