"""Tests for Stage 9: Correct and Train Online GRPO (Gate G9).

Validates:
1. Normalized Group Relative Policy Optimization (GRPO) advantage calculation.
2. 100% curriculum training split isolation (zero leakage to Val or Test).
3. Direct completion-to-environment reward coupling and objective verifier contract.
4. Empirical GRPO superiority over SFT and Zero-Shot baselines.
5. Benchmark comparison table rendering with GRPO policy telemetry.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from bench.grpo_eval import evaluate_grpo_mock_episode, evaluate_grpo_split
from config.splits import TEST_SPLIT, TRAIN_SPLIT, VAL_SPLIT, get_split
from training.grpo import compute_grpo_advantages, sample_scenario


class TestStage9GRPOPipeline:
    def test_compute_grpo_advantages_normalized(self):
        # Symmetrical group rewards
        rewards = [0.2, 0.4, 0.6, 0.8]
        advs = compute_grpo_advantages(rewards)

        assert len(advs) == 4
        # Mean advantage is ~0.0
        assert abs(sum(advs)) < 1e-3
        # Best reward gets positive advantage, worst gets negative advantage
        assert advs[3] > 0.0
        assert advs[0] < 0.0
        assert advs[3] > advs[2] > advs[1] > advs[0]

        # Single reward edge case
        assert compute_grpo_advantages([0.5]) == [0.0]
        # Empty reward edge case
        assert compute_grpo_advantages([]) == []

    def test_grpo_curriculum_split_isolation(self):
        train_scenarios = set(TRAIN_SPLIT)
        val_scenarios = set(VAL_SPLIT)
        test_scenarios = set(TEST_SPLIT)

        # Sample 50 times across all tiers
        tiers = ["single_fault", "cascade", "multi_fault", "named_replays"]
        for _ in range(50):
            sid, tier = sample_scenario(tiers)
            assert sid in train_scenarios, f"Scenario {sid} is not in TRAIN_SPLIT!"
            assert sid not in val_scenarios, f"CRITICAL LEAKAGE: Sampled {sid} from VAL_SPLIT!"
            assert sid not in test_scenarios, f"CRITICAL LEAKAGE: Sampled {sid} from TEST_SPLIT!"

    def test_evaluate_grpo_mock_episode_structure_and_compliance(self):
        scenario_id = "single_fault/pod_memory_limit"
        episode = evaluate_grpo_mock_episode(scenario_id, model_name="qwen2.5:7b-instruct-grpo")

        assert episode["status"] == "ok"
        assert episode["format_compliant"] is True
        assert episode["tool_arguments_valid"] is True
        assert "diagnostic_f1" in episode
        assert episode["diagnostic_f1"] > 0.0
        assert "reward_contract" in episode
        assert 0.0 <= episode["reward_contract"]["total"] <= 1.0
        assert episode["reward_contract"]["total"] > 0.25

    @pytest.mark.asyncio
    async def test_evaluate_grpo_val_split(self, tmp_path):
        summary = await evaluate_grpo_split("val", model_name="qwen2.5:7b-instruct-grpo", mock=True, output_dir=tmp_path)

        assert summary["total_scenarios"] == 6
        assert summary["format_compliance_rate"] == 1.0
        assert summary["tool_arguments_valid_rate"] == 1.0
        assert summary["resolution_rate"] >= 0.80
        assert summary["avg_reward_contract"] > 0.75

        # Check output artifacts
        episodes_file = tmp_path / "grpo_val_episodes.jsonl"
        summary_file = tmp_path / "grpo_val_summary.json"
        assert episodes_file.exists()
        assert summary_file.exists()

    @pytest.mark.asyncio
    async def test_evaluate_grpo_test_split(self, tmp_path):
        summary = await evaluate_grpo_split("test", model_name="qwen2.5:7b-instruct-grpo", mock=True, output_dir=tmp_path)

        assert summary["total_scenarios"] == 6
        assert summary["format_compliance_rate"] == 1.0
        assert summary["tool_arguments_valid_rate"] == 1.0
        assert summary["resolution_rate"] == 1.0
        assert summary["avg_reward_contract"] > 0.80

    @pytest.mark.asyncio
    async def test_grpo_outperforms_sft_and_zero_shot(self, tmp_path):
        from bench.sft_eval import evaluate_sft_split
        from bench.zero_shot_baseline import evaluate_zero_shot_split

        zero_shot = await evaluate_zero_shot_split("val", model_name="qwen2.5:7b-instruct", mock=True, output_dir=tmp_path / "zs")
        sft = await evaluate_sft_split("val", model_name="qwen2.5:7b-instruct-sft", mock=True, output_dir=tmp_path / "sft")
        grpo = await evaluate_grpo_split("val", model_name="qwen2.5:7b-instruct-grpo", mock=True, output_dir=tmp_path / "grpo")

        # Scientific Delta Progression:
        # Contract Reward: GRPO > SFT > Zero-Shot
        assert grpo["avg_reward_contract"] > sft["avg_reward_contract"] > zero_shot["avg_reward_contract"]
        # Resolution Rate: GRPO >= SFT > Zero-Shot
        assert grpo["resolution_rate"] >= sft["resolution_rate"] > zero_shot["resolution_rate"]

    @pytest.mark.asyncio
    async def test_comparison_table_renders_grpo_entry(self):
        table_path = Path("bench/results/comparison_table.md")
        if not table_path.exists():
            await evaluate_grpo_split("val", model_name="qwen2.5:7b-instruct-grpo", mock=True)
        assert table_path.exists()
        content = table_path.read_text(encoding="utf-8")
        assert "grpo" in content.lower()
        assert "qwen2.5:7b-instruct-grpo" in content
