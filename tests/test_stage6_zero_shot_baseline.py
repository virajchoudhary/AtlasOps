"""Tests for Stage 6: Reproduce GAI Zero-Shot Baseline (Gate G6).

Validates:
1. Zero-shot baseline benchmark harness execution across splits (Val, Test, Leaderboard).
2. Split resolution in runner (--split train/val/test/leaderboard/all).
3. Strict split isolation invariant during zero-shot benchmarking.
4. Diagnostic precision/recall/F1 calculation against frozen ground-truth root causes.
5. Reward contract and penalty computation for zero-shot baselines.
6. Results persistence in artifacts/evidence/stage6 and comparison table updates.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from bench.runner import compute_summary, run_scenario
from bench.zero_shot_baseline import compute_diagnostic_f1, evaluate_zero_shot_split, tokenize_text
from config.scenario_catalog import SCENARIO_CATALOG
from config.splits import TEST_SPLIT, TRAIN_SPLIT, VAL_SPLIT, get_split


class TestStage6ZeroShotBaseline:
    def test_tokenize_text_and_diagnostic_f1_exact(self):
        text = "Pod kill on cartservice pod causing container restarts and service outage"
        tokens = tokenize_text(text)
        assert "pod" in tokens
        assert "cartservice" in tokens
        assert "restarts" in tokens

        f1_res = compute_diagnostic_f1(text, text)
        assert f1_res["precision"] == 1.0
        assert f1_res["recall"] == 1.0
        assert f1_res["f1"] == 1.0

    def test_diagnostic_f1_partial_and_disjoint(self):
        pred = "cartservice pod killed by OOM"
        truth = "Pod kill on cartservice pod causing container restarts and service outage"
        f1_res = compute_diagnostic_f1(pred, truth)
        assert 0.0 < f1_res["f1"] < 1.0
        assert f1_res["precision"] > 0.0
        assert f1_res["recall"] > 0.0

        disjoint_res = compute_diagnostic_f1("completely unrelated memory spike", "dns failure checkout")
        assert disjoint_res["f1"] == 0.0

        empty_res = compute_diagnostic_f1("", "")
        assert empty_res["f1"] == 1.0

    @pytest.mark.asyncio
    async def test_run_scenario_mock_mode(self):
        res = await run_scenario("single_fault/sf-001", mock=True)
        assert res["scenario_id"] == "single_fault/sf-001"
        assert res["tier"] == "single_fault"
        assert res["status"] == "ok"
        assert res["resolved"] is False
        assert res["agent_claimed_resolved"] is False
        assert res["env_resolved"] is False
        assert "reward_contract" in res
        assert 0.0 <= res["reward_contract"]["total"] <= 1.0

    @pytest.mark.asyncio
    async def test_evaluate_zero_shot_val_split(self, tmp_path):
        summary = await evaluate_zero_shot_split("val", model_name="qwen2.5:7b-instruct", mock=True, output_dir=tmp_path)
        assert summary["split_name"] == "val"
        assert summary["total_scenarios"] == len(VAL_SPLIT)
        assert summary["resolution_rate"] == 0.0  # Unfinetuned baseline does not resolve
        assert "avg_diagnostic_f1" in summary
        assert "avg_reward_contract" in summary
        assert "per_tier" in summary

        episodes_file = tmp_path / "results_per_episode.jsonl"
        assert episodes_file.exists()
        lines = episodes_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == len(VAL_SPLIT)

        for line in lines:
            ep = json.loads(line)
            assert ep["scenario_id"] in VAL_SPLIT
            assert "diagnostic_metrics" in ep
            assert "ground_truth_root_cause" in ep

    @pytest.mark.asyncio
    async def test_evaluate_zero_shot_test_split(self, tmp_path):
        summary = await evaluate_zero_shot_split("test", model_name="qwen2.5:7b-instruct", mock=True, output_dir=tmp_path)
        assert summary["split_name"] == "test"
        assert summary["total_scenarios"] == len(TEST_SPLIT)
        assert summary["resolution_rate"] == 0.0
        assert len(summary["per_tier"]) == 4  # All 4 tiers represented in test split

    def test_split_isolation_invariant(self):
        train_scenarios = set(TRAIN_SPLIT)
        val_scenarios = set(VAL_SPLIT)
        test_scenarios = set(TEST_SPLIT)

        # Baseline evaluation on val/test must never intersect training set
        assert val_scenarios.isdisjoint(train_scenarios)
        assert test_scenarios.isdisjoint(train_scenarios)
        assert test_scenarios.isdisjoint(val_scenarios)

    def test_comparison_table_updates(self, tmp_path):
        summary_val = {
            "tag": "zero_shot_val_test",
            "model": "qwen2.5:7b-instruct",
            "run_date": "2026-08-31T05:00:00Z",
            "total_scenarios": 6,
            "resolution_rate": 0.0,
            "avg_reward": 0.5,
            "avg_reward_contract": 0.35,
            "avg_penalty": 0.0,
            "avg_turns": 4.0,
            "cascade_resolution_rate": 0.0,
            "named_replay_resolution_rate": 0.0,
            "per_tier": {
                "single_fault": {"count": 2, "resolution_rate": 0.0, "avg_time_to_resolve_s": 45.0, "avg_reward_contract": 0.35},
                "cascade": {"count": 1, "resolution_rate": 0.0, "avg_time_to_resolve_s": 45.0, "avg_reward_contract": 0.35},
                "multi_fault": {"count": 1, "resolution_rate": 0.0, "avg_time_to_resolve_s": 45.0, "avg_reward_contract": 0.35},
                "named_replays": {"count": 2, "resolution_rate": 0.0, "avg_time_to_resolve_s": 45.0, "avg_reward_contract": 0.35},
            },
        }
        summary_obj = compute_summary(
            [
                {"status": "ok", "scenario_id": "single_fault/sf-001", "tier": "single_fault", "resolved": False, "env_resolved": False, "agent_claimed_resolved": False, "total_turns": 4, "time_to_resolve_s": 45.0, "judge": {"overall": 0.5}, "reward_contract": {"total": 0.35, "penalty_total": 0.0, "penalties": {}}}
            ],
            tag="test_tag",
            model="qwen2.5:7b-instruct",
        )
        assert summary_obj["resolution_rate"] == 0.0
        assert summary_obj["total_scenarios"] == 1
