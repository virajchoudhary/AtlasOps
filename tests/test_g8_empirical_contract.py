"""Scientific-validity contracts for the Stage 8 SFT evaluator."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from bench import sft_eval
from bench.sft_eval import evaluate_sft_split
from config.scenario_catalog import SCENARIO_CATALOG
from config.splits import TRAIN_SPLIT, VAL_SPLIT
from training.sft_provenance import (
    MAX_VERIFIED_SFT_MANIFEST_BYTES,
    MAX_VERIFIED_SFT_CORPUS_BYTES,
    SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _checkpoint(tmp_path: Path, *, status: str = "completed") -> Path:
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir(parents=True)
    adapter_config = checkpoint / "adapter_config.json"
    adapter_config.write_text('{"peft_type":"LORA"}', encoding="utf-8")
    adapter_weights = checkpoint / "adapter_model.safetensors"
    adapter_weights.write_bytes(b"checkpoint-bytes")
    files = [
        {
            "path": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _sha(path),
        }
        for path in (adapter_config, adapter_weights)
    ]
    tree_hash = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "status": status,
        "base_model": {
            "id": "Qwen/Qwen2.5-7B-Instruct",
            "requested_revision": "base-revision",
            "resolved_revision": "base-revision",
        },
        "tokenizer": {
            "id": "Qwen/Qwen2.5-7B-Instruct",
            "requested_revision": "tokenizer-revision",
            "resolved_revision": "tokenizer-revision",
        },
        "dataset": {
            "split": "train",
            "split_seed": 2026,
            "split_scenarios": list(TRAIN_SPLIT),
            "split_sha256": hashlib.sha256(
                json.dumps(
                    list(TRAIN_SPLIT),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        },
        "source": {"git_sha": "a" * 40, "git_dirty": False},
        "schema_version": 1,
        "checkpoint": {"files": files, "tree_sha256": tree_hash},
    }
    (checkpoint / "sft_run_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return checkpoint


def _classify_checkpoint_dataset(checkpoint: Path) -> None:
    from training import build_sft_dataset

    corpus_dir = checkpoint.parent / "training-data"
    evidence_dir = checkpoint.parent / "frozen-evidence"
    previous = {
        "DATA_DIR": build_sft_dataset.DATA_DIR,
        "EVIDENCE_DIR": build_sft_dataset.EVIDENCE_DIR,
        "prepare_example_for_training": build_sft_dataset.prepare_example_for_training,
        "render_messages": build_sft_dataset.render_messages,
    }
    build_sft_dataset.DATA_DIR = corpus_dir
    build_sft_dataset.EVIDENCE_DIR = evidence_dir
    build_sft_dataset.prepare_example_for_training = (
        lambda example: {"messages": example["messages"], "tools": []}
    )
    build_sft_dataset.render_messages = (
        lambda messages, **kwargs: ("rendered", ["generated"])
    )
    try:
        corpus_path, corpus_manifest = build_sft_dataset.build_sft_corpus()
    finally:
        for name, value in previous.items():
            setattr(build_sft_dataset, name, value)

    corpus_manifest_path = corpus_dir / "sft_corpus_manifest.json"
    assert corpus_manifest["corpus_sha256_canonical_lf"] == (
        SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256
    )
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset"].update(
        {
            "corpus_path": str(corpus_path.resolve()),
            "corpus_sha256_canonical_lf": corpus_manifest[
                "corpus_sha256_canonical_lf"
            ],
            "total_examples": corpus_manifest["total_examples"],
            "total_scenarios": corpus_manifest["total_scenarios"],
            "data_origin": "scenario_derived_synthetic",
            "synthetic": True,
            "data_origin_source": "adjacent_corpus_manifest",
            "corpus_manifest": {
                "present": True,
                "path": str(corpus_manifest_path.resolve()),
                "sha256": _sha(corpus_manifest_path),
            },
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _approved_corpus_path(checkpoint: Path) -> Path:
    manifest = json.loads(
        (checkpoint / "sft_run_manifest.json").read_text(encoding="utf-8")
    )
    return Path(manifest["dataset"]["corpus_path"])


def _prediction() -> str:
    return json.dumps(
        {
            "severity": "P1",
            "affected_services": ["unknown"],
            "root_cause": "service resource saturation",
            "confidence": 0.5,
        }
    )


def test_checkpoint_manifest_downgrades_unsupported_origin_to_unverified(tmp_path):
    checkpoint = _checkpoint(tmp_path)
    _classify_checkpoint_dataset(checkpoint)
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset"]["data_origin"] = "human_expert_verified"
    manifest["dataset"]["synthetic"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    normalized, _ = sft_eval._load_checkpoint_manifest(
        checkpoint,
        approved_corpus_path=_approved_corpus_path(checkpoint),
    )

    assert normalized["dataset"]["data_origin"] == "UNVERIFIED"
    assert normalized["dataset"]["synthetic"] is None
    assert normalized["dataset"]["data_origin_source"] == "unverified"


@pytest.fixture(scope="module", autouse=True)
def _trajectories_dir(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv(
        "TRAJECTORIES_DIR",
        str(tmp_path_factory.mktemp("g8-trajectories")),
    )
    yield
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_mode_and_checkpoint_are_required(tmp_path):
    with pytest.raises(ValueError, match="Evaluation mode"):
        await evaluate_sft_split("val", output_dir=tmp_path)
    with pytest.raises(ValueError, match="checkpoint"):
        await evaluate_sft_split("val", mode="empirical", output_dir=tmp_path)


@pytest.mark.asyncio
async def test_implicit_mock_output_is_isolated_from_canonical_evidence(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical-evidence"
    monkeypatch.setattr(sft_eval, "EVIDENCE_DIR", canonical)
    monkeypatch.setattr(sft_eval, "RESULTS_DIR", tmp_path / "results")

    summary = await evaluate_sft_split("val", mode="mock")

    assert summary["non_empirical"] is True
    assert not canonical.exists()
    assert "non_empirical" in summary["raw_predictions_path"]
    with pytest.raises(ValueError, match="explicit unique output_dir"):
        await evaluate_sft_split("val", mode="empirical", checkpoint=tmp_path)


@pytest.mark.asyncio
async def test_incomplete_or_tampered_checkpoint_is_rejected(tmp_path):
    incomplete = _checkpoint(tmp_path / "incomplete", status="failed")
    with pytest.raises(ValueError, match="completed"):
        await evaluate_sft_split(
            "val",
            mode="empirical",
            checkpoint=incomplete,
            output_dir=tmp_path / "out-incomplete",
        )

    checkpoint = _checkpoint(tmp_path / "tampered")
    (checkpoint / "adapter_model.safetensors").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        await evaluate_sft_split(
            "val",
            mode="empirical",
            checkpoint=checkpoint,
            output_dir=tmp_path / "out-tampered",
        )


@pytest.mark.asyncio
async def test_dirty_sft_training_source_is_not_empirical_provenance(tmp_path):
    checkpoint = _checkpoint(tmp_path)
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["git_dirty"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="clean immutable"):
        await evaluate_sft_split(
            "val",
            mode="empirical",
            checkpoint=checkpoint,
            output_dir=tmp_path / "out-dirty",
        )


@pytest.mark.asyncio
async def test_checkpoint_inventory_rejects_path_escape(tmp_path):
    checkpoint = _checkpoint(tmp_path)
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["checkpoint"]["files"][0]["path"] = "../outside.bin"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        await evaluate_sft_split(
            "val",
            mode="empirical",
            checkpoint=checkpoint,
            output_dir=tmp_path / "output",
        )


@pytest.mark.asyncio
async def test_checkpoint_inventory_rejects_untracked_extra_file(tmp_path):
    checkpoint = _checkpoint(tmp_path)
    (checkpoint / "undeclared.bin").write_bytes(b"untracked checkpoint bytes")

    with pytest.raises(ValueError, match="missing from provenance inventory"):
        await evaluate_sft_split(
            "val",
            mode="empirical",
            checkpoint=checkpoint,
            output_dir=tmp_path / "output",
        )


@pytest.mark.asyncio
async def test_checkpoint_provenance_rejects_wrong_train_split(tmp_path):
    checkpoint = _checkpoint(tmp_path)
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset"]["split_sha256"] = "b" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="frozen split"):
        await evaluate_sft_split(
            "val",
            mode="empirical",
            checkpoint=checkpoint,
            output_dir=tmp_path / "output",
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_field", "incomplete"),
        ("invalid_manifest_hash", "SHA-256"),
    ],
)
def test_checkpoint_provenance_rejects_incomplete_or_tampered_origin(
    tmp_path,
    mutation,
    message,
):
    checkpoint = _checkpoint(tmp_path)
    _classify_checkpoint_dataset(checkpoint)
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "missing_field":
        del manifest["dataset"]["synthetic"]
    else:
        manifest["dataset"]["corpus_manifest"]["sha256"] = "not-a-hash"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        sft_eval._load_checkpoint_manifest(checkpoint)


@pytest.mark.parametrize(
    ("mutation", "verification_status"),
    [
        ("dataset_count", "corpus_metadata_mismatch"),
        ("sidecar_count", "metadata_mismatch"),
        ("sidecar_origin", "origin_mismatch"),
        ("corpus_hash", "corpus_hash_mismatch"),
        ("manifest_hash", "hash_mismatch"),
        ("nonexistent_manifest", "path_mismatch"),
        ("nonexistent_corpus", "approval_mismatch"),
        ("approved_missing_corpus", "corpus_unavailable"),
        ("missing_manifest", "manifest_unavailable"),
    ],
)
def test_checkpoint_downgrades_fabricated_adjacent_provenance(
    tmp_path,
    mutation,
    verification_status,
):
    checkpoint = _checkpoint(tmp_path)
    _classify_checkpoint_dataset(checkpoint)
    run_manifest_path = checkpoint / "sft_run_manifest.json"
    run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
    dataset = run_manifest["dataset"]
    sidecar_path = Path(dataset["corpus_manifest"]["path"])
    approved_corpus_path = Path(dataset["corpus_path"])

    if mutation == "dataset_count":
        dataset["total_examples"] = 1
    elif mutation == "sidecar_count":
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["total_examples"] = 1
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        dataset["corpus_manifest"]["sha256"] = _sha(sidecar_path)
    elif mutation == "sidecar_origin":
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["data_origin"] = "human_expert_verified"
        sidecar["synthetic"] = False
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        dataset["corpus_manifest"]["sha256"] = _sha(sidecar_path)
    elif mutation == "corpus_hash":
        dataset["corpus_sha256_canonical_lf"] = "f" * 64
    elif mutation == "manifest_hash":
        dataset["corpus_manifest"]["sha256"] = "f" * 64
    elif mutation == "nonexistent_manifest":
        dataset["corpus_manifest"]["path"] = str(
            sidecar_path.parent / "missing_sft_corpus_manifest.json"
        )
    elif mutation == "nonexistent_corpus":
        dataset["corpus_path"] = str(
            sidecar_path.parent / "missing_sft_corpus_train.jsonl"
        )
    elif mutation == "approved_missing_corpus":
        approved_corpus_path.unlink()
    else:
        sidecar_path.unlink()
    run_manifest_path.write_text(json.dumps(run_manifest), encoding="utf-8")

    normalized, _ = sft_eval._load_checkpoint_manifest(
        checkpoint,
        approved_corpus_path=approved_corpus_path,
    )

    assert normalized["dataset"]["data_origin"] == "UNVERIFIED"
    assert normalized["dataset"]["synthetic"] is None
    assert normalized["dataset"]["data_origin_source"] == "unverified"
    assert normalized["dataset"]["corpus_manifest"]["content_verified"] is False
    assert (
        normalized["dataset"]["corpus_manifest"]["verification_status"]
        == verification_status
    )


@pytest.mark.asyncio
async def test_empirical_path_uses_checkpoint_and_withholds_truth(tmp_path):
    checkpoint = _checkpoint(tmp_path)
    calls = []

    async def inference(messages, model_name, checkpoint_path, generation_config):
        calls.append((messages, checkpoint_path, generation_config))
        return _prediction()

    summary = await evaluate_sft_split(
        "val",
        model_name="sft-adapter",
        mode="empirical",
        checkpoint=checkpoint,
        output_dir=tmp_path / "output",
        inference_fn=inference,
    )

    assert len(calls) == len(VAL_SPLIT)
    for scenario_id, (messages, checkpoint_path, _) in zip(
        VAL_SPLIT,
        calls,
        strict=True,
    ):
        request = json.dumps(messages)
        assert SCENARIO_CATALOG[scenario_id].expected_root_cause not in request
        assert "expected_root_cause" not in request
        assert checkpoint_path == checkpoint.resolve()

    assert summary["evaluation_mode"] == "empirical"
    assert summary["empirical_inference_executed"] is True
    assert summary["empirical_claim_allowed"] is False
    assert summary["non_empirical"] is True
    assert summary["environment_resolution_evaluated"] is False
    assert summary["resolution_rate"] is None
    assert summary["avg_reward_contract"] is None
    assert summary["checkpoint_tree_sha256"]
    assert summary["raw_predictions_sha256"]
    assert summary["split_sha256"] == (
        "9f1bad373e66d7818019092c213f70edcc7e09dfbc538346ff6d0693ea78c6e4"
    )
    assert summary["training_data_provenance"]["data_origin"] == "UNVERIFIED"
    assert summary["training_data_provenance"]["synthetic"] is None
    assert summary["training_data_provenance"]["corpus_manifest"]["present"] is False
    assert all(
        "resolution_rate" not in tier_metrics
        for tier_metrics in summary["per_tier"].values()
    )


def test_synthetic_training_origin_does_not_block_checkpoint_backed_inference(
    tmp_path,
    monkeypatch,
):
    checkpoint = _checkpoint(tmp_path)
    _classify_checkpoint_dataset(checkpoint)

    async def local_backend(messages, model_name, checkpoint_path, generation_config):
        return _prediction()

    monkeypatch.setattr(
        sft_eval,
        "LocalSFTInference",
        lambda manifest: local_backend,
    )
    summary = asyncio.run(
        evaluate_sft_split(
            "val",
            mode="empirical",
            checkpoint=checkpoint,
            approved_corpus_path=_approved_corpus_path(checkpoint),
            output_dir=tmp_path / "output",
        )
    )

    assert summary["empirical_inference_executed"] is True
    assert summary["empirical_claim_allowed"] is True
    assert summary["non_empirical"] is False
    assert summary["training_data_provenance"]["data_origin"] == (
        "scenario_derived_synthetic"
    )
    assert summary["training_data_provenance"]["synthetic"] is True
    corpus_manifest = summary["training_data_provenance"]["corpus_manifest"]
    assert corpus_manifest["sha256"] == _sha(Path(corpus_manifest["path"]))
    assert corpus_manifest["content_verified"] is True
    assert corpus_manifest["verification_status"] == "verified"
    assert summary["environment_resolution_evaluated"] is False
    assert summary["resolution_rate"] is None


def test_unsupported_training_origin_is_never_reported_as_real(tmp_path):
    checkpoint = _checkpoint(tmp_path)
    _classify_checkpoint_dataset(checkpoint)
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset"]["data_origin"] = "human_expert_verified"
    manifest["dataset"]["synthetic"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    async def inference(messages, model_name, checkpoint_path, generation_config):
        return _prediction()

    summary = asyncio.run(
        evaluate_sft_split(
            "val",
            mode="empirical",
            checkpoint=checkpoint,
            approved_corpus_path=_approved_corpus_path(checkpoint),
            output_dir=tmp_path / "output",
            inference_fn=inference,
        )
    )

    assert summary["training_data_provenance"]["data_origin"] == "UNVERIFIED"
    assert summary["training_data_provenance"]["synthetic"] is None
    assert summary["training_data_provenance"]["data_origin_source"] == "unverified"


def test_checkpoint_downgrades_oversized_approved_corpus(tmp_path):
    checkpoint = _checkpoint(tmp_path)
    _classify_checkpoint_dataset(checkpoint)
    corpus_path = _approved_corpus_path(checkpoint)
    with corpus_path.open("wb") as stream:
        stream.truncate(MAX_VERIFIED_SFT_CORPUS_BYTES + 1)

    normalized, _ = sft_eval._load_checkpoint_manifest(
        checkpoint,
        approved_corpus_path=corpus_path,
    )

    assert normalized["dataset"]["data_origin"] == "UNVERIFIED"
    assert normalized["dataset"]["synthetic"] is None
    assert normalized["dataset"]["corpus_manifest"]["content_verified"] is False
    assert (
        normalized["dataset"]["corpus_manifest"]["verification_status"]
        == "corpus_too_large"
    )


def test_checkpoint_downgrades_oversized_approved_manifest(tmp_path):
    checkpoint = _checkpoint(tmp_path)
    _classify_checkpoint_dataset(checkpoint)
    manifest = json.loads(
        (checkpoint / "sft_run_manifest.json").read_text(encoding="utf-8")
    )
    sidecar_path = Path(manifest["dataset"]["corpus_manifest"]["path"])
    with sidecar_path.open("wb") as stream:
        stream.truncate(MAX_VERIFIED_SFT_MANIFEST_BYTES + 1)

    normalized, _ = sft_eval._load_checkpoint_manifest(
        checkpoint,
        approved_corpus_path=Path(manifest["dataset"]["corpus_path"]),
    )

    assert normalized["dataset"]["data_origin"] == "UNVERIFIED"
    assert normalized["dataset"]["synthetic"] is None
    assert normalized["dataset"]["corpus_manifest"]["content_verified"] is False
    assert (
        normalized["dataset"]["corpus_manifest"]["verification_status"]
        == "manifest_too_large"
    )


def test_unapproved_external_corpus_path_is_never_opened(tmp_path, monkeypatch):
    checkpoint = _checkpoint(tmp_path)
    _classify_checkpoint_dataset(checkpoint)
    sentinel_dir = tmp_path / "external-sentinel"
    sentinel_dir.mkdir()
    sentinel_corpus = sentinel_dir / "untrusted.jsonl"
    sentinel_manifest = sentinel_dir / "sft_corpus_manifest.json"
    sentinel_corpus.write_bytes(b"must not be opened")
    sentinel_manifest.write_bytes(b"must not be opened")
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dataset"]["corpus_path"] = str(sentinel_corpus.resolve())
    manifest["dataset"]["corpus_manifest"]["path"] = str(
        sentinel_manifest.resolve()
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    unapproved_paths = {
        os.path.normcase(os.path.abspath(sentinel_corpus)),
        os.path.normcase(os.path.abspath(sentinel_manifest)),
    }
    original_open = Path.open
    touched = []

    def guarded_open(path, *args, **kwargs):
        if os.path.normcase(os.path.abspath(path)) in unapproved_paths:
            touched.append(path)
            raise AssertionError("unapproved corpus path was opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    normalized, _ = sft_eval._load_checkpoint_manifest(checkpoint)

    assert not touched
    assert normalized["dataset"]["data_origin"] == "UNVERIFIED"
    assert normalized["dataset"]["synthetic"] is None
    assert normalized["dataset"]["corpus_manifest"]["content_verified"] is False
    assert (
        normalized["dataset"]["corpus_manifest"]["verification_status"]
        == "approval_required"
    )


def test_empirical_failure_never_falls_back_to_mock_or_persists_exception_text(tmp_path):
    checkpoint = _checkpoint(tmp_path)
    secret_marker = "INJECTED_SECRET_MARKER"

    async def failing_inference(messages, model_name, checkpoint_path, generation_config):
        raise RuntimeError(secret_marker)

    summary = asyncio.run(
        evaluate_sft_split(
            "val",
            mode="empirical",
            checkpoint=checkpoint,
            output_dir=tmp_path / "output",
            inference_fn=failing_inference,
        )
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "output" / "sft_val_episodes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert summary["failed_scenarios"] == len(VAL_SPLIT)
    assert summary["empirical_inference_executed"] is False
    assert all(row["evaluation_mode"] == "empirical" for row in rows)
    assert all(row["error_category"] == "runtime_error" for row in rows)
    assert all(row["error"] == "runtime_error: inference failed" for row in rows)
    serialized = (tmp_path / "output" / "sft_val_episodes.jsonl").read_text(
        encoding="utf-8"
    )
    assert secret_marker not in serialized
