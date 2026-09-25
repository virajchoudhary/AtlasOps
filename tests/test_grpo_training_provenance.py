"""GRPO run records must precede training and validate saved checkpoint bytes."""

from __future__ import annotations

import json
import sys

import pytest

from config.splits import TRAIN_SPLIT
from training.grpo_provenance import (
    MANIFEST_NAME,
    checkpoint_inventory,
    create_run_manifest,
    persist_status,
    validate_sft_parent,
)
from training.sft_provenance import (
    canonical_json_sha256,
    write_manifest_atomic,
)
from training.sft_provenance import (
    create_run_manifest as create_sft_manifest,
)
from training.sft_provenance import (
    mark_completed as complete_sft,
)
from training.sft_provenance import (
    mark_running as run_sft,
)


def _sft_parent_record(tmp_path):
    corpus = tmp_path / "train.jsonl"
    corpus.write_text(
        "".join(json.dumps({"scenario_id": scenario_id, "role": "triage"}) + "\n"
                for scenario_id in TRAIN_SPLIT),
        encoding="utf-8",
    )
    checkpoint = tmp_path / "sft"
    checkpoint.mkdir()
    manifest = create_sft_manifest(
        corpus_path=corpus,
        output_dir=checkpoint,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        base_model_revision="resolved-model-revision",
        tokenizer="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision="resolved-tokenizer-revision",
        role="all",
        hyperparameters={},
    )
    manifest = run_sft(
        manifest,
        resolved_model_revision="resolved-model-revision",
        resolved_tokenizer_revision="resolved-tokenizer-revision",
    )
    manifest["source"] = {"git_sha": "a" * 40, "git_dirty": False}
    (checkpoint / "adapter_config.json").write_text("{}", encoding="utf-8")
    (checkpoint / "adapter_model.safetensors").write_bytes(b"sft-adapter-fixture")
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = complete_sft(
        manifest,
        output_dir=checkpoint,
        manifest_path=manifest_path,
        trainer_state={"global_step": 1},
        training_history=[],
    )
    write_manifest_atomic(manifest_path, manifest)
    return checkpoint, validate_sft_parent(
        checkpoint,
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision="resolved-model-revision",
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision="resolved-tokenizer-revision",
    )


def _parent():
    return {
        "checkpoint_path": "test-sft-checkpoint",
        "manifest_sha256": "c" * 64,
        "checkpoint_tree_sha256": "d" * 64,
        "training_source_sha": "a" * 40,
        "train_corpus_sha256": "e" * 64,
        "train_split_sha256": canonical_json_sha256(list(TRAIN_SPLIT)),
    }


def _planned_manifest():
    return create_run_manifest(
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision="resolved-model-revision",
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision="resolved-tokenizer-revision",
        seed=42,
        generation_config={"max_completion_length": 256},
        hyperparameters={"max_steps": 1},
        sft_parent=_parent(),
        prompt_rows=[{"scenario_id": TRAIN_SPLIT[0], "prompt": "public fixture"}],
    )


def test_completed_manifest_hashes_all_checkpoint_files(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "planned")
    assert manifest["splits"]["train_sha256"] == canonical_json_sha256(list(TRAIN_SPLIT))
    assert manifest["training"]["mode"] == "online_rl_real_environment"
    assert manifest["sft_parent"]["checkpoint_tree_sha256"] == "d" * 64
    assert manifest["splits"]["selected_scenario_ids"] == [TRAIN_SPLIT[0]]
    assert manifest["splits"]["selected_prompt_rows_sha256"]

    manifest = persist_status(manifest_path, manifest, "running")
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "adapter_model.safetensors").write_bytes(b"real-bytes-for-test-only")
    (tmp_path / "rollout_trajectories.jsonl").write_text(
        '{"env_resolved":false}\n', encoding="utf-8"
    )
    completed = persist_status(manifest_path, manifest, "completed")
    assert completed["status"] == "completed"
    assert {row["path"] for row in completed["checkpoint"]["files"]} == {
        "adapter_config.json",
        "adapter_model.safetensors",
        "rollout_trajectories.jsonl",
    }
    assert completed["checkpoint"] == checkpoint_inventory(tmp_path)


def test_missing_adapter_cannot_be_marked_completed(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    with pytest.raises(RuntimeError, match="adapter_config.json"):
        persist_status(manifest_path, manifest, "completed")
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "running"


def test_adapter_config_without_weights_cannot_be_marked_completed(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="adapter model weights"):
        persist_status(manifest_path, manifest, "completed")


def test_training_failure_persists_failed_state_before_optional_ml_imports(
    monkeypatch, tmp_path
):
    from training import grpo

    output_dir = tmp_path / "run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grpo.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            "resolved-model-revision",
            "--tokenizer-revision",
            "resolved-tokenizer-revision",
            "--sft-checkpoint",
            str(tmp_path / "sft"),
            "--output",
            str(output_dir),
        ],
    )
    monkeypatch.setattr(grpo, "validate_sft_parent", lambda *args, **kwargs: _parent())

    def fail_training(args, output_dir):
        assert json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))[
            "status"
        ] == "running"
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(grpo, "run_training", fail_training)
    with pytest.raises(RuntimeError, match="fixture failure"):
        grpo.main()
    saved = json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["failure"]["error_type"] == "RuntimeError"
    assert saved["checkpoint"] is None


def test_grpo_parent_requires_byte_valid_sft_checkpoint(tmp_path):
    checkpoint, parent = _sft_parent_record(tmp_path)
    assert parent["checkpoint_tree_sha256"]
    assert parent["train_split_sha256"] == canonical_json_sha256(list(TRAIN_SPLIT))
    with pytest.raises(ValueError, match="must match"):
        validate_sft_parent(
            checkpoint,
            model_id="another-model",
            model_revision="resolved-model-revision",
            tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
            tokenizer_revision="resolved-tokenizer-revision",
        )
    (checkpoint / "adapter_model.safetensors").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_sft_parent(
            checkpoint,
            model_id="Qwen/Qwen2.5-7B-Instruct",
            model_revision="resolved-model-revision",
            tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
            tokenizer_revision="resolved-tokenizer-revision",
        )


def test_grpo_loader_trains_from_sft_adapter(monkeypatch, tmp_path):
    from training import grpo

    checkpoint, _ = _sft_parent_record(tmp_path)
    calls = []

    class Tokenizer:
        pad_token = None
        eos_token = "eos"

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return cls()

    class BaseModel:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return object()

    class AdapterModel:
        @staticmethod
        def from_pretrained(base, path, *, is_trainable):
            calls.append((path, is_trainable))
            return AdapterModel()

        def print_trainable_parameters(self):
            pass

    monkeypatch.setattr(grpo, "AutoTokenizer", Tokenizer)
    monkeypatch.setattr(grpo, "AutoModelForCausalLM", BaseModel)
    monkeypatch.setattr(grpo, "PeftModel", AdapterModel)
    monkeypatch.setattr(grpo, "prepare_model_for_kbit_training", lambda model: model)
    monkeypatch.setattr(grpo, "_flash_attn_available", lambda: False)
    grpo.load_model_and_tokenizer(
        "Qwen/Qwen2.5-7B-Instruct",
        model_revision="resolved-model-revision",
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision="resolved-tokenizer-revision",
        sft_checkpoint=checkpoint,
    )
    assert calls == [(str(checkpoint), True)]
