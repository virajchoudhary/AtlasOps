"""Tests for Stage 9: Correct and Train Online GRPO (Gate G9).

Validates:
1. Normalized Group Relative Policy Optimization (GRPO) advantage calculation.
2. 100% curriculum training split isolation (zero leakage to Val or Test).
3. Direct completion-to-environment reward coupling and objective verifier contract.
4. Synthetic compatibility outputs remain explicitly NON_EMPIRICAL.
5. Mock evaluation does not update the shared comparison table.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.grpo_eval import evaluate_grpo_mock_episode, evaluate_grpo_split
from config.splits import TEST_SPLIT, TRAIN_SPLIT, VAL_SPLIT
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
            sid, _tier = sample_scenario(tiers)
            assert sid in train_scenarios, f"Scenario {sid} is not in TRAIN_SPLIT!"
            assert sid not in val_scenarios, f"CRITICAL LEAKAGE: Sampled {sid} from VAL_SPLIT!"
            assert sid not in test_scenarios, f"CRITICAL LEAKAGE: Sampled {sid} from TEST_SPLIT!"

    def test_evaluate_grpo_mock_episode_structure_and_compliance(self):
        scenario_id = "single_fault/pod_memory_limit"
        episode = evaluate_grpo_mock_episode(scenario_id, model_name="qwen2.5:7b-instruct-grpo")

        assert episode["evaluation_mode"] == "NON_EMPIRICAL"
        assert episode["empirical"] is False
        assert episode["status"] == "ok"
        assert episode["format_compliant"] is True
        assert episode["tool_arguments_valid"] is True
        assert "diagnostic_f1" in episode
        assert "reward_contract" in episode

    @pytest.mark.asyncio
    async def test_evaluate_grpo_val_split(self, tmp_path):
        summary = await evaluate_grpo_split("val", model_name="qwen2.5:7b-instruct-grpo", mock=True, output_dir=tmp_path)

        assert summary["evaluation_mode"] == "NON_EMPIRICAL"
        assert summary["empirical"] is False
        assert summary["total_scenarios"] == 6
        assert summary["format_compliance_rate"] == 1.0
        assert summary["tool_arguments_valid_rate"] == 1.0

        # Check output artifacts
        episodes_file = tmp_path / "grpo_val_episodes.jsonl"
        summary_file = tmp_path / "grpo_val_summary.json"
        assert episodes_file.exists()
        assert summary_file.exists()

    @pytest.mark.asyncio
    async def test_evaluate_grpo_test_split(self, tmp_path):
        summary = await evaluate_grpo_split("test", model_name="qwen2.5:7b-instruct-grpo", mock=True, output_dir=tmp_path)

        assert summary["evaluation_mode"] == "NON_EMPIRICAL"
        assert summary["empirical"] is False
        assert summary["total_scenarios"] == 6
        assert summary["format_compliance_rate"] == 1.0
        assert summary["tool_arguments_valid_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_mock_grpo_summary_is_non_empirical(self, tmp_path):
        summary = await evaluate_grpo_split(
            "val",
            model_name="qwen2.5:7b-instruct-grpo",
            mock=True,
            output_dir=tmp_path / "grpo",
        )
        assert summary["evaluation_mode"] == "NON_EMPIRICAL"
        assert summary["empirical"] is False

    @pytest.mark.asyncio
    async def test_mock_does_not_update_comparison_table(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        output_dir = tmp_path / "non_empirical"
        summary = await evaluate_grpo_split(
            "val",
            model_name="qwen2.5:7b-instruct-grpo",
            mock=True,
            output_dir=output_dir,
        )
        assert summary["evaluation_mode"] == "NON_EMPIRICAL"
        assert output_dir.joinpath("grpo_val_episodes.jsonl").is_file()
        assert not Path("bench/results/comparison_table.md").exists()
