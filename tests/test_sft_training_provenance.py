"""Tests for durable SFT run and checkpoint provenance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from config.splits import TEST_SPLIT, TRAIN_SEED, TRAIN_SPLIT
from training.sft_provenance import (
    canonical_file_sha256,
    create_run_manifest,
    inspect_training_corpus,
    mark_completed,
    mark_failed,
    mark_running,
    write_manifest_atomic,
)


def _manifest(tmp_path: Path) -> tuple[dict, Path, Path]:
    corpus = tmp_path / "train.jsonl"
    corpus.write_text(
        "".join(
            json.dumps({"scenario_id": scenario_id, "role": "triage"}) + "\r\n"
            for scenario_id in TRAIN_SPLIT
        ),
        encoding="utf-8",
    )
    output = tmp_path / "checkpoint"
    output.mkdir()
    manifest_path = output / "sft_run_manifest.json"
    manifest = create_run_manifest(
        corpus_path=corpus,
        output_dir=output,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        base_model_revision="model-commit",
        tokenizer="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision="tokenizer-commit",
        role="all",
        hyperparameters={"seed": TRAIN_SEED},
    )
    return manifest, output, manifest_path


def test_planned_manifest_captures_exact_inputs_and_source(tmp_path):
    manifest, output, _ = _manifest(tmp_path)

    assert manifest["status"] == "planned"
    assert manifest["base_model"]["requested_revision"] == "model-commit"
    assert manifest["tokenizer"]["requested_revision"] == "tokenizer-commit"
    assert manifest["dataset"]["split"] == "train"
    assert manifest["dataset"]["split_scenarios"] == list(TRAIN_SPLIT)
    assert manifest["dataset"]["observed_scenarios"] == sorted(TRAIN_SPLIT)
    assert manifest["dataset"]["total_examples"] == len(TRAIN_SPLIT)
    assert manifest["dataset"]["split_seed"] == TRAIN_SEED
    assert manifest["dataset"]["corpus_sha256_canonical_lf"]
    assert manifest["dataset"]["split_sha256"]
    assert manifest["source"]["git_sha"]
    assert manifest["output_dir"] == str(output.resolve())
    assert "packages" in manifest["environment"]


def test_corpus_hash_is_newline_stable(tmp_path):
    crlf = tmp_path / "crlf.jsonl"
    lf = tmp_path / "lf.jsonl"
    crlf.write_bytes(b'{"a":1}\r\n{"b":2}\r\n')
    lf.write_bytes(b'{"a":1}\n{"b":2}\n')
    assert canonical_file_sha256(crlf) == canonical_file_sha256(lf)


def test_exact_revisions_are_required(tmp_path):
    corpus = tmp_path / "train.jsonl"
    corpus.write_text(
        "".join(
            json.dumps({"scenario_id": scenario_id, "role": "triage"}) + "\n"
            for scenario_id in TRAIN_SPLIT
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="base model revision"):
        create_run_manifest(
            corpus_path=corpus,
            output_dir=tmp_path / "out",
            base_model="model",
            base_model_revision="",
            tokenizer="tokenizer",
            tokenizer_revision="revision",
            role="all",
            hyperparameters={},
        )


def test_sft_rejects_manifest_path_incompatible_with_evaluators(monkeypatch, tmp_path):
    from training import sft

    output = tmp_path / "checkpoint"
    monkeypatch.setattr(sys, "argv", [
        "sft.py",
        "--model", "Qwen/Qwen2.5-7B-Instruct",
        "--model-revision", "model-commit",
        "--data", str(tmp_path / "missing-corpus.jsonl"),
        "--output", str(output),
        "--provenance-output", str(tmp_path / "external-manifest.json"),
    ])
    with pytest.raises(ValueError, match="so G8 and G9"):
        sft.main()
    assert not output.exists()
    assert not (tmp_path / "external-manifest.json").exists()


def test_corpus_rejects_validation_or_test_scenarios(tmp_path):
    corpus = tmp_path / "leaky.jsonl"
    corpus.write_text(
        json.dumps({"scenario_id": TEST_SPLIT[0], "role": "triage"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-training scenario"):
        inspect_training_corpus(corpus)


def test_running_and_completed_manifest_hash_checkpoint_files(tmp_path):
    manifest, output, manifest_path = _manifest(tmp_path)
    running = mark_running(
        manifest,
        resolved_model_revision="resolved-model-commit",
        resolved_tokenizer_revision="resolved-tokenizer-commit",
    )
    assert running["status"] == "running"

    (output / "adapter_config.json").write_text('{"r":16}\n', encoding="utf-8")
    (output / "adapter_model.safetensors").write_bytes(b"adapter-bytes")
    completed = mark_completed(
        running,
        output_dir=output,
        manifest_path=manifest_path,
        trainer_state={"global_step": 4, "epoch": 1.0},
        training_history=[{"loss": 1.25, "step": 4}],
    )
    write_manifest_atomic(manifest_path, completed)

    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"
    assert persisted["base_model"]["resolved_revision"] == "resolved-model-commit"
    assert persisted["trainer_state"]["global_step"] == 4
    assert persisted["training_history"] == [{"loss": 1.25, "step": 4}]
    assert persisted["checkpoint"]["total_files"] == 2
    assert persisted["checkpoint"]["tree_sha256"]
    assert {
        item["path"] for item in persisted["checkpoint"]["files"]
    } == {"adapter_config.json", "adapter_model.safetensors"}


def test_completed_manifest_requires_adapter_files(tmp_path):
    manifest, output, manifest_path = _manifest(tmp_path)
    (output / "tokenizer.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="adapter_config.json"):
        mark_completed(
            manifest,
            output_dir=output,
            manifest_path=manifest_path,
            trainer_state={},
            training_history=[],
        )


def test_completed_manifest_rejects_symlinked_checkpoint_content(tmp_path):
    manifest, output, manifest_path = _manifest(tmp_path)
    (output / "adapter_config.json").write_text('{"r":16}\n', encoding="utf-8")
    (output / "adapter_model.safetensors").write_bytes(b"adapter-bytes")
    external = tmp_path / "external.txt"
    external.write_text("not part of the checkpoint", encoding="utf-8")
    try:
        (output / "external.txt").symlink_to(external)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(ValueError, match="symlink"):
        mark_completed(
            manifest,
            output_dir=output,
            manifest_path=manifest_path,
            trainer_state={},
            training_history=[],
        )


@pytest.mark.parametrize(
    ("exc", "interrupted", "expected_status"),
    [
        (RuntimeError("out of memory"), False, "failed"),
        (KeyboardInterrupt(), True, "interrupted"),
    ],
)
def test_failure_and_interruption_are_durable(
    tmp_path,
    exc,
    interrupted,
    expected_status,
):
    manifest, _, manifest_path = _manifest(tmp_path)
    failed = mark_failed(manifest, exc, interrupted=interrupted)
    write_manifest_atomic(manifest_path, failed)

    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["status"] == expected_status
    assert persisted["completed_at"]
    assert persisted["failure"]["type"] == type(exc).__name__
