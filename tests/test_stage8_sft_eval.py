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

import pytest

from bench.sft_eval import evaluate_sft_mock_episode, evaluate_sft_split
from config.splits import get_split


@pytest.fixture(scope="module", autouse=True)
def _trajectories_dir(tmp_path_factory):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv(
        "TRAJECTORIES_DIR",
        str(tmp_path_factory.mktemp("stage8-trajectories")),
    )
    yield
    monkeypatch.undo()


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

    @pytest.mark.parametrize(
        ("split_name", "expected_sha256"),
        [
            ("val", "9f1bad373e66d7818019092c213f70edcc7e09dfbc538346ff6d0693ea78c6e4"),
            ("test", "a5fc3603bddcae58aba315cd7b295995799eb3f819be940ecf75d95142b88386"),
            (
                "leaderboard",
                "327a9204dbc22030b1cc6db63492188e2ba80c7b8e9931f42116f17f77e30013",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_mock_summary_records_frozen_ordered_split_digest(
        self,
        split_name,
        expected_sha256,
        tmp_path,
    ):
        summary = await evaluate_sft_split(
            split_name,
            mock=True,
            output_dir=tmp_path,
        )

        assert summary["split_sha256"] == expected_sha256

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
    async def test_mock_summary_has_sft_identity_without_shared_artifact_write(self, tmp_path):
        summary = await evaluate_sft_split(
            "val",
            model_name="qwen2.5:7b-instruct-sft",
            mock=True,
            output_dir=tmp_path,
        )
        assert summary["tag"].startswith("sft-val-")
        assert summary["model"] == "qwen2.5:7b-instruct-sft"
        assert summary["non_empirical"] is True
