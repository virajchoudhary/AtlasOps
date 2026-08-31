"""Tests for Stage 13: Final Ablation & Stress Evaluation (Gate G13).

Validates:
1. Ablation Suite execution across 5 model architectures x 4 benchmark partitions.
2. Monotonic performance progression from Zero-Shot -> SFT -> SFT+RS -> GRPO -> Full System.
3. Adversarial Chaos Stress partition evaluation and resilience.
4. Evidence JSON serialization and Markdown table generation.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from bench.ablation_suite import (
    evaluate_model_on_partition,
    run_full_ablation_suite,
)


class TestStage13AblationSuite:
    def test_ablation_suite_generates_json_and_markdown_artifacts(self, tmp_path):
        payload = run_full_ablation_suite(mock=True)

        assert payload["suite_name"] == "AtlasOps Final Ablation & Stress Evaluation"
        assert len(payload["comparison_family"]) == 5
        assert len(payload["partitions"]) == 4

        ev_path = Path("artifacts/evidence/stage13/ablation_benchmark_results.json")
        md_path = Path("bench/results/final_ablation_matrix.md")

        assert ev_path.exists()
        assert md_path.exists()

        data = json.loads(ev_path.read_text(encoding="utf-8"))
        assert "results" in data
        assert "Full Pipeline (GAI + RS + RL)" in data["results"]

    def test_monotonic_resolution_and_reward_improvement(self):
        val_zero = evaluate_model_on_partition("Zero-Shot Baseline", "val")
        val_sft = evaluate_model_on_partition("SFT Model", "val")
        val_sft_rs = evaluate_model_on_partition("SFT + Recommender", "val")
        val_grpo = evaluate_model_on_partition("Online GRPO RL", "val")
        val_full = evaluate_model_on_partition("Full Pipeline (GAI + RS + RL)", "val")

        # Resolution Progression: Zero (0%) < SFT (66.7%) < SFT+RS (83.3%) <= GRPO (100%) == Full (100%)
        assert val_zero.resolution_rate < val_sft.resolution_rate <= val_sft_rs.resolution_rate <= val_grpo.resolution_rate == val_full.resolution_rate

        # TTR Progression: Full System is fastest (18.5s < 22.0s < 31.0s < 39.7s < 45.0s)
        assert val_full.avg_time_to_resolve_seconds < val_grpo.avg_time_to_resolve_seconds < val_sft_rs.avg_time_to_resolve_seconds < val_sft.avg_time_to_resolve_seconds < val_zero.avg_time_to_resolve_seconds

        # Contract Reward Progression: Full System achieves highest composite reward (0.912 > 0.865 > 0.765 > 0.690 > 0.345)
        assert val_full.avg_contract_reward > val_grpo.avg_contract_reward > val_sft_rs.avg_contract_reward > val_sft.avg_contract_reward > val_zero.avg_contract_reward

    def test_adversarial_stress_robustness(self):
        adv_zero = evaluate_model_on_partition("Zero-Shot Baseline", "adversarial")
        adv_sft = evaluate_model_on_partition("SFT Model", "adversarial")
        adv_grpo = evaluate_model_on_partition("Online GRPO RL", "adversarial")
        adv_full = evaluate_model_on_partition("Full Pipeline (GAI + RS + RL)", "adversarial")

        # Full System maintains 100% resolution even under adversarial chaos stress
        assert adv_full.resolution_rate == 100.0
        assert adv_full.avg_contract_reward > 0.85
        assert adv_full.runbook_top3_hit_rate == 100.0

        # Baseline failures on adversarial stress
        assert adv_zero.resolution_rate == 0.0
        assert adv_sft.resolution_rate == 60.0
        assert adv_grpo.resolution_rate == 80.0

    def test_partition_scenario_counts_match_spec(self):
        val_m = evaluate_model_on_partition("Full Pipeline (GAI + RS + RL)", "val")
        test_m = evaluate_model_on_partition("Full Pipeline (GAI + RS + RL)", "test")
        lb_m = evaluate_model_on_partition("Full Pipeline (GAI + RS + RL)", "leaderboard")
        adv_m = evaluate_model_on_partition("Full Pipeline (GAI + RS + RL)", "adversarial")

        assert val_m.num_scenarios == 6
        assert test_m.num_scenarios == 6
        assert lb_m.num_scenarios == 7
        assert adv_m.num_scenarios == 5
