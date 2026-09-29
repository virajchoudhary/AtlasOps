"""SFT trainer handoff tests that never load weights or run training."""

from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.splits import TRAIN_SPLIT
from training import sft_provenance, sft_rendering
from training.generate_trajectories import SFT_EXAMPLE_FORMAT

MODEL_COMMIT = "a" * 40


class StopBeforeTraining(RuntimeError):
    """Sentinel raised by the stub trainer after capturing its dataset."""


@pytest.fixture(autouse=True)
def _lightweight_runtime_probe(monkeypatch):
    monkeypatch.setattr(
        sft_provenance,
        "runtime_environment",
        lambda: {"packages": {}},
    )


def _write_training_corpus(
    path: Path,
    *,
    malformed: bool = False,
    mixed_roles: bool = False,
) -> Path:
    tool_name = sft_rendering.role_tool_schemas("triage")[0]["function"]["name"]
    rows = []
    for index, scenario_id in enumerate(TRAIN_SPLIT):
        role = (
            ("triage", "diagnosis")[index % 2]
            if mixed_roles
            else "triage"
        )
        messages = [
            {"role": "system", "content": "generic system placeholder"},
            {"role": "user", "content": "Diagnose the test incident."},
        ]
        if index == 0 and not malformed:
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_fixture",
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": '{"namespace":"default","service":"paymentservice"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call_fixture",
                        "tool_name": tool_name,
                        "content": '{"status":"observed"}',
                    },
                ]
            )
        messages.append(
            {"role": "assistant", "content": "Investigate the service."}
        )
        row = {
            "format": SFT_EXAMPLE_FORMAT,
            "scenario_id": scenario_id,
            "role": role,
            "messages": messages,
        }
        if malformed and index == 0:
            row.pop("format")
        rows.append(row)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _install_training_stubs(monkeypatch, captured: dict) -> None:
    class Dataset(list):
        @classmethod
        def from_list(cls, rows):
            return cls(rows)

        def filter(self, predicate):
            return type(self)(row for row in self if predicate(row))

    datasets = types.ModuleType("datasets")
    datasets.Dataset = Dataset

    class LoraConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    peft = types.ModuleType("peft")
    peft.LoraConfig = LoraConfig
    peft.TaskType = SimpleNamespace(CAUSAL_LM="CAUSAL_LM")
    peft.prepare_model_for_kbit_training = lambda model: model
    peft.get_peft_model = lambda model, _config: model

    tokenizer = SimpleNamespace(
        pad_token="pad",
        eos_token="eos",
        init_kwargs={"_commit_hash": MODEL_COMMIT},
    )
    model = SimpleNamespace(
        config=SimpleNamespace(_commit_hash=MODEL_COMMIT),
        print_trainable_parameters=lambda: None,
    )

    class AutoTokenizer:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            captured["tokenizer_loads"] = captured.get("tokenizer_loads", 0) + 1
            return tokenizer

    class AutoModelForCausalLM:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            captured["model_loads"] = captured.get("model_loads", 0) + 1
            return model

    class BitsAndBytesConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    transformers = types.ModuleType("transformers")
    transformers.AutoModelForCausalLM = AutoModelForCausalLM
    transformers.AutoTokenizer = AutoTokenizer
    transformers.BitsAndBytesConfig = BitsAndBytesConfig
    transformers.set_seed = lambda _seed: None

    class SFTConfig:
        def __init__(
            self,
            *,
            assistant_only_loss: bool,
            max_length: int,
            **kwargs,
        ):
            self.assistant_only_loss = assistant_only_loss
            self.max_length = max_length
            self.kwargs = kwargs

    class SFTTrainer:
        def __init__(self, *, model, processing_class, train_dataset, args):
            captured["trainer_dataset"] = train_dataset
            captured["trainer_args"] = args
            captured["trainer_calls"] = captured.get("trainer_calls", 0) + 1
            raise StopBeforeTraining("captured SFTTrainer input; training intentionally stopped")

    trl = types.ModuleType("trl")
    trl.SFTConfig = SFTConfig
    trl.SFTTrainer = SFTTrainer

    for name, module in (
        ("datasets", datasets),
        ("peft", peft),
        ("transformers", transformers),
        ("trl", trl),
    ):
        monkeypatch.setitem(sys.modules, name, module)


def _set_sft_args(
    monkeypatch,
    corpus_path: Path,
    output_path: Path,
    *,
    role: str = "all",
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sft.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            MODEL_COMMIT,
            "--data",
            str(corpus_path),
            "--output",
            str(output_path),
            "--role",
            role,
        ],
    )


def test_sft_hands_prepared_snapshot_rows_to_trainer_without_training(
    monkeypatch,
    tmp_path,
):
    from training import sft

    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    corpus_bytes = corpus.read_bytes()
    source_rows = [json.loads(line) for line in corpus_bytes.decode("utf-8").splitlines()]
    captured = {}
    _install_training_stubs(monkeypatch, captured)
    _set_sft_args(monkeypatch, corpus, tmp_path / "checkpoint")

    prepare_calls = []
    prepare = sft_rendering.prepare_example_for_training

    def prepare_once(example):
        assert "provenance" not in example
        prepare_calls.append(example)
        return prepare(example)

    monkeypatch.setattr(sft, "prepare_example_for_training", prepare_once, raising=False)

    with pytest.raises(StopBeforeTraining, match="training intentionally stopped"):
        sft.main()

    trainer_rows = list(captured["trainer_dataset"])
    assert captured["trainer_calls"] == 1
    assert captured["trainer_args"].assistant_only_loss is True
    assert len(prepare_calls) == len(source_rows)
    assert len(trainer_rows) == len(source_rows)
    assert [row["scenario_id"] for row in trainer_rows] == [
        row["scenario_id"] for row in source_rows
    ]
    assert {row["scenario_id"] for row in trainer_rows} == set(TRAIN_SPLIT)

    raw_with_tool_call = next(
        row
        for row in source_rows
        if any(message.get("tool_calls") for message in row["messages"])
    )
    prepared_with_tool_call = next(
        row
        for row in trainer_rows
        if row["scenario_id"] == raw_with_tool_call["scenario_id"]
        and row["role"] == raw_with_tool_call["role"]
    )
    assert prepared_with_tool_call["messages"][0]["content"] == (
        sft_rendering.load_role_prompt(raw_with_tool_call["role"])
    )
    assert prepared_with_tool_call["tools"] == sft_rendering.role_tool_schemas(
        raw_with_tool_call["role"]
    )
    assert prepared_with_tool_call["provenance"]["role"] == raw_with_tool_call["role"]

    raw_call = next(
        call
        for message in raw_with_tool_call["messages"]
        for call in message.get("tool_calls", [])
    )
    prepared_call = next(
        call
        for message in prepared_with_tool_call["messages"]
        for call in message.get("tool_calls", [])
    )
    assert isinstance(raw_call["function"]["arguments"], str)
    assert isinstance(prepared_call["function"]["arguments"], dict)
    assert prepared_call["function"]["arguments"] == {
        "namespace": "default",
        "service": "paymentservice",
    }

    assert corpus.read_bytes() == corpus_bytes
    run_manifest = json.loads(
        (tmp_path / "checkpoint" / "sft_run_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert run_manifest["status"] == "failed"
    assert run_manifest["dataset"]["corpus_sha256_canonical_lf"] == hashlib.sha256(
        corpus_bytes.replace(b"\r\n", b"\n")
    ).hexdigest()
    assert run_manifest["dataset"]["data_origin"] == "UNVERIFIED"
    assert run_manifest["dataset"]["synthetic"] is None
    assert run_manifest["dataset"]["corpus_manifest"]["present"] is False


def test_sft_rejects_malformed_snapshot_row_before_loading_model_or_trainer(
    monkeypatch,
    tmp_path,
):
    from training import sft

    corpus = _write_training_corpus(
        tmp_path / "malformed.jsonl",
        malformed=True,
    )
    captured = {}
    _install_training_stubs(monkeypatch, captured)
    _set_sft_args(monkeypatch, corpus, tmp_path / "checkpoint")

    with pytest.raises(ValueError, match="unsupported example format"):
        sft.main()

    assert captured.get("model_loads", 0) == 0
    assert captured.get("trainer_calls", 0) == 0
    run_manifest = json.loads(
        (tmp_path / "checkpoint" / "sft_run_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert run_manifest["status"] == "failed"
    assert run_manifest["dataset"]["data_origin"] == "UNVERIFIED"
    assert run_manifest["dataset"]["synthetic"] is None


def test_sft_role_filter_prepares_only_selected_diagnosis_rows_for_trainer(
    monkeypatch,
    tmp_path,
):
    from training import sft

    corpus = _write_training_corpus(
        tmp_path / "mixed-roles.jsonl",
        mixed_roles=True,
    )
    corpus_bytes = corpus.read_bytes()
    source_rows = [
        json.loads(line) for line in corpus_bytes.decode("utf-8").splitlines()
    ]
    assert {row["role"] for row in source_rows} == {"triage", "diagnosis"}
    captured = {}
    _install_training_stubs(monkeypatch, captured)
    _set_sft_args(
        monkeypatch,
        corpus,
        tmp_path / "checkpoint",
        role="diagnosis",
    )

    prepare_calls = []
    prepare = sft_rendering.prepare_example_for_training

    def prepare_once(example):
        assert "provenance" not in example
        prepare_calls.append(example)
        return prepare(example)

    monkeypatch.setattr(sft, "prepare_example_for_training", prepare_once, raising=False)

    with pytest.raises(StopBeforeTraining, match="training intentionally stopped"):
        sft.main()

    trainer_rows = list(captured["trainer_dataset"])
    expected_diagnosis_rows = [
        row for row in source_rows if row["role"] == "diagnosis"
    ]
    assert len(prepare_calls) == len(expected_diagnosis_rows)
    assert [(row["scenario_id"], row["role"]) for row in prepare_calls] == [
        (row["scenario_id"], row["role"]) for row in expected_diagnosis_rows
    ]
    assert len(trainer_rows) == len(expected_diagnosis_rows)
    assert [row["scenario_id"] for row in trainer_rows] == [
        row["scenario_id"] for row in expected_diagnosis_rows
    ]
    assert {row["role"] for row in trainer_rows} == {"diagnosis"}

    diagnosis_row = trainer_rows[0]
    assert diagnosis_row["messages"][0]["content"] == (
        sft_rendering.load_role_prompt("diagnosis")
    )
    diagnosis_tools = sft_rendering.role_tool_schemas("diagnosis")
    assert diagnosis_tools
    assert diagnosis_row["tools"] == diagnosis_tools
    assert diagnosis_row["provenance"]["role"] == "diagnosis"

    assert corpus.read_bytes() == corpus_bytes
    run_manifest = json.loads(
        (tmp_path / "checkpoint" / "sft_run_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert run_manifest["dataset"]["corpus_sha256_canonical_lf"] == hashlib.sha256(
        corpus_bytes.replace(b"\r\n", b"\n")
    ).hexdigest()
    assert run_manifest["dataset"]["data_origin"] == "UNVERIFIED"
    assert run_manifest["dataset"]["synthetic"] is None
