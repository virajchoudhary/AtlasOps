"""Scientific-validity contracts for the Stage 6 zero-shot evaluator."""

from __future__ import annotations

import json

import pytest

import bench.zero_shot_baseline as zero_shot
from config.scenario_catalog import SCENARIO_CATALOG
from config.splits import VAL_SPLIT


def _prediction(root_cause: str = "service saturation under load") -> str:
    return json.dumps(
        {
            "severity": "P1",
            "affected_services": ["unknown"],
            "root_cause": root_cause,
            "confidence": 0.4,
        }
    )


@pytest.mark.asyncio
async def test_evaluation_mode_must_be_explicit(tmp_path):
    with pytest.raises(ValueError, match="Evaluation mode is required"):
        await zero_shot.evaluate_zero_shot_split("val", output_dir=tmp_path)


@pytest.mark.asyncio
async def test_empirical_mode_requires_exact_model_revision(tmp_path):
    async def inference(messages, model_name, generation_config):
        return _prediction()

    with pytest.raises(ValueError, match="model_revision"):
        await zero_shot.evaluate_zero_shot_split(
            "val",
            mode="empirical",
            output_dir=tmp_path,
            inference_fn=inference,
        )


@pytest.mark.asyncio
async def test_empirical_default_backend_requires_configured_endpoint(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("VLLM_BASE", raising=False)
    with pytest.raises(RuntimeError, match="VLLM_BASE"):
        await zero_shot.evaluate_zero_shot_split(
            "val",
            model_revision="revision-1",
            mode="empirical",
            output_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_empirical_mode_never_exposes_expected_root_cause(tmp_path):
    calls = []

    async def inference(messages, model_name, generation_config):
        calls.append(
            {
                "messages": messages,
                "model_name": model_name,
                "generation_config": generation_config,
            }
        )
        return _prediction()

    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        model_name="example/base-model",
        model_revision="0123456789abcdef",
        mode="empirical",
        output_dir=tmp_path,
        inference_fn=inference,
        inference_backend="openai-compatible",
    )

    assert len(calls) == len(VAL_SPLIT)
    for scenario_id, call in zip(VAL_SPLIT, calls, strict=True):
        serialized = json.dumps(call["messages"])
        assert SCENARIO_CATALOG[scenario_id].expected_root_cause not in serialized
        assert "expected_root_cause" not in serialized
        assert "chaos_kinds" not in serialized
        assert "manifest_relpath" not in serialized

    assert summary["evaluation_mode"] == "empirical"
    assert summary["empirical_inference_executed"] is True
    assert summary["empirical_claim_allowed"] is False
    assert summary["non_empirical"] is True
    assert summary["environment_resolution_evaluated"] is False
    assert summary["resolution_rate"] is None
    assert summary["avg_reward_contract"] is None
    assert summary["avg_time_to_resolve_s"] is None
    assert summary["model_revision"] == "0123456789abcdef"
    assert summary["failed_scenarios"] == 0
    assert summary["source"]["git_sha"]
    assert summary["raw_predictions_sha256"]


@pytest.mark.asyncio
async def test_empirical_failure_is_preserved_without_mock_fallback(
    tmp_path,
    monkeypatch,
):
    calls = 0
    monkeypatch.setenv("ATLASOPS_MOCK_EVAL", "1")

    async def failing_inference(messages, model_name, generation_config):
        nonlocal calls
        calls += 1
        raise RuntimeError("endpoint unavailable")

    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        model_revision="revision-1",
        mode="empirical",
        output_dir=tmp_path,
        inference_fn=failing_inference,
        inference_backend="injected-test-double",
    )

    assert calls == len(VAL_SPLIT)
    assert summary["failed_scenarios"] == len(VAL_SPLIT)
    assert summary["empirical_inference_executed"] is False
    rows = [
        json.loads(line)
        for line in (tmp_path / "results_per_episode.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert all(row["status"] == "error" for row in rows)
    assert all(row["evaluation_mode"] == "empirical" for row in rows)
    assert all("mock" not in row for row in rows)
    assert all("endpoint unavailable" in row["error"] for row in rows)


@pytest.mark.asyncio
async def test_custom_output_does_not_write_canonical_evidence(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical-evidence"
    output = tmp_path / "run-output"
    monkeypatch.setattr(zero_shot, "EVIDENCE_DIR", canonical)

    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        mode="mock",
        output_dir=output,
    )

    assert summary["mock_eval"] is True
    assert summary["non_empirical"] is True
    assert summary["empirical_inference_executed"] is False
    assert not canonical.exists()
    assert (output / "zero_shot_val_summary.json").exists()


@pytest.mark.asyncio
async def test_implicit_mock_output_is_isolated_from_canonical_evidence(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical-evidence"
    monkeypatch.setattr(zero_shot, "EVIDENCE_DIR", canonical)
    monkeypatch.setattr(zero_shot, "RESULTS_DIR", tmp_path / "results")

    summary = await zero_shot.evaluate_zero_shot_split("val", mode="mock")

    assert summary["non_empirical"] is True
    assert not canonical.exists()
    assert "non_empirical" in summary["raw_predictions_path"]
    with pytest.raises(ValueError, match="explicit unique output_dir"):
        await zero_shot.evaluate_zero_shot_split(
            "val", mode="empirical", model_revision="revision"
        )
