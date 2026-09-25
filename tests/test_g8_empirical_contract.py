"""Scientific-validity contracts for the Stage 8 SFT evaluator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bench import sft_eval
from bench.sft_eval import evaluate_sft_split
from config.scenario_catalog import SCENARIO_CATALOG
from config.splits import TRAIN_SPLIT, VAL_SPLIT


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


def _prediction() -> str:
    return json.dumps(
        {
            "severity": "P1",
            "affected_services": ["unknown"],
            "root_cause": "service resource saturation",
            "confidence": 0.5,
        }
    )


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
    assert all(
        "resolution_rate" not in tier_metrics
        for tier_metrics in summary["per_tier"].values()
    )


@pytest.mark.asyncio
async def test_empirical_failure_never_falls_back_to_mock(tmp_path):
    checkpoint = _checkpoint(tmp_path)

    async def failing_inference(messages, model_name, checkpoint_path, generation_config):
        raise RuntimeError("model load failed")

    summary = await evaluate_sft_split(
        "val",
        mode="empirical",
        checkpoint=checkpoint,
        output_dir=tmp_path / "output",
        inference_fn=failing_inference,
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
    assert all("model load failed" in row["error"] for row in rows)
