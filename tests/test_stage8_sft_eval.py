"""Tests for Stage 8: Evaluate SFT Before RL (Gate G8).

Validates:
1. SFT model evaluation across benchmark splits (Val, Test, Leaderboard).
2. Diagnostic F1 improvement over zero-shot baseline.
3. 100% structured tool call and format compliance.
4. Measurable resolution rate delta between zero-shot and SFT.
5. Strict benchmark split isolation invariants.
6. Centralized comparison table updates with SFT evaluation metrics.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from bench.sft_eval import evaluate_sft_mock_episode, evaluate_sft_split
from config.splits import TEST_SPLIT, TRAIN_SPLIT, VAL_SPLIT, get_split


class TestStage8SFTEvaluation:
    def test_evaluate_sft_mock_episode_structure_and_compliance(self):
        scenario_id = "single_fault/pod_memory_limit"
        episode = evaluate_sft_mock_episode(scenario_id, model_name="qwen2.5:7b-instruct-sft")

        assert episode["status"] == "ok"
        assert episode["format_compliant"] is True
        assert episode["tool_arguments_valid"] is True
        assert "diagnostic_f1" in episode
        assert episode["diagnostic_f1"] > 0.0
        assert "reward_contract" in episode
        assert 0.0 <= episode["reward_contract"]["total"] <= 1.0
        assert episode["reward_contract"]["total"] > 0.25

    @pytest.mark.asyncio
    async def test_evaluate_sft_val_split(self, tmp_path):
        summary = await evaluate_sft_split("val", model_name="qwen2.5:7b-instruct-sft", mock=True, output_dir=tmp_path)

        assert summary["total_scenarios"] == 6
        assert summary["format_compliance_rate"] == 1.0
        assert summary["tool_arguments_valid_rate"] == 1.0
        assert summary["resolution_rate"] > 0.0
        assert summary["avg_reward_contract"] > 0.50

        # Check output artifacts
        episodes_file = tmp_path / "sft_val_episodes.jsonl"
        summary_file = tmp_path / "sft_val_summary.json"
        assert episodes_file.exists()
        assert summary_file.exists()

    @pytest.mark.asyncio
    async def test_evaluate_sft_test_split(self, tmp_path):
        summary = await evaluate_sft_split("test", model_name="qwen2.5:7b-instruct-sft", mock=True, output_dir=tmp_path)

        assert summary["total_scenarios"] == 6
        assert summary["format_compliance_rate"] == 1.0
        assert summary["tool_arguments_valid_rate"] == 1.0
        assert summary["resolution_rate"] > 0.0
        assert summary["avg_reward_contract"] > 0.50

    def test_split_isolation_invariant(self):
        val_scenarios = set(get_split("val"))
        test_scenarios = set(get_split("test"))
        train_scenarios = set(get_split("train"))

        assert val_scenarios.isdisjoint(test_scenarios)
        assert val_scenarios.isdisjoint(train_scenarios)
        assert test_scenarios.isdisjoint(train_scenarios)

    @pytest.mark.asyncio
    async def test_sft_outperforms_zero_shot_baseline(self, tmp_path):
        from bench.zero_shot_baseline import evaluate_zero_shot_split

        zero_shot_summary = await evaluate_zero_shot_split("val", model_name="qwen2.5:7b-instruct", mock=True, output_dir=tmp_path / "zs")
        sft_summary = await evaluate_sft_split("val", model_name="qwen2.5:7b-instruct-sft", mock=True, output_dir=tmp_path / "sft")

        # Scientific Delta Verification:
        # SFT resolution rate > Zero-shot (0.0%)
        assert sft_summary["resolution_rate"] > zero_shot_summary["resolution_rate"]
        # SFT contract reward > Zero-shot contract reward
        assert sft_summary["avg_reward_contract"] > zero_shot_summary["avg_reward_contract"]

    @pytest.mark.asyncio
    async def test_comparison_table_renders_sft_entry(self):
        table_path = Path("bench/results/comparison_table.md")
        if not table_path.exists():
            await evaluate_sft_split("val", model_name="qwen2.5:7b-instruct-sft", mock=True)
        assert table_path.exists()
        content = table_path.read_text(encoding="utf-8")
        assert "sft" in content.lower()
        assert "qwen2.5:7b-instruct-sft" in content
