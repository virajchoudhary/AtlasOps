"""Tests for Stage 8 SFT evaluation boundaries and deterministic fixtures.

Validates:
1. NON_EMPIRICAL mock fixture behavior across frozen benchmark partitions.
2. Empirical G8 refusal for non-validation splits before split or checkpoint access.
3. Strict benchmark split isolation invariants.
4. Fixture summaries remain explicitly non-empirical.
"""

from __future__ import annotations

import asyncio

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
        assert summary["evaluation_mode"] == "mock"
        assert summary["non_empirical"] is True

        # Check output artifacts
        episodes_file = tmp_path / "sft_val_episodes.jsonl"
        summary_file = tmp_path / "sft_val_summary.json"
        assert episodes_file.exists()
        assert summary_file.exists()

    @pytest.mark.asyncio
    async def test_mock_sft_fixture_runs_on_test_split_without_empirical_claim(self, tmp_path):
        summary = await evaluate_sft_split("test", model_name="qwen2.5:7b-instruct-sft", mock=True, output_dir=tmp_path)

        assert summary["total_scenarios"] == 6
        assert summary["format_compliance_rate"] == 1.0
        assert summary["tool_arguments_valid_rate"] == 1.0
        assert summary["resolution_rate"] > 0.0
        assert summary["avg_reward_contract"] > 0.50
        assert summary["evaluation_mode"] == "mock"
        assert summary["non_empirical"] is True

    @pytest.mark.parametrize("split_name", ["test", "leaderboard"])
    def test_empirical_evaluation_refuses_non_validation_split_before_access(
        self,
        split_name,
        tmp_path,
        monkeypatch,
    ):
        def forbidden_split_access(name):
            pytest.fail(f"empirical evaluation accessed {name!r} split membership")

        async def forbidden_inference(*args, **kwargs):
            pytest.fail("empirical evaluation invoked inference")

        monkeypatch.setattr("bench.sft_eval.get_split", forbidden_split_access)
        output_dir = tmp_path / "must-not-be-created"

        with pytest.raises(ValueError, match="Validation-only"):
            asyncio.run(
                evaluate_sft_split(
                    split_name,
                    mode="empirical",
                    checkpoint=tmp_path / "missing-checkpoint",
                    output_dir=output_dir,
                    inference_fn=forbidden_inference,
                )
            )

        assert not output_dir.exists()

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
        assert summary["evaluation_mode"] == "mock"
        assert summary["non_empirical"] is True

    def test_split_isolation_invariant(self):
        val_scenarios = set(get_split("val"))
        test_scenarios = set(get_split("test"))
        train_scenarios = set(get_split("train"))

        assert val_scenarios.isdisjoint(test_scenarios)
        assert val_scenarios.isdisjoint(train_scenarios)
        assert test_scenarios.isdisjoint(train_scenarios)

    @pytest.mark.asyncio
    async def test_sft_and_baseline_mock_summaries_are_non_empirical_fixtures(self, tmp_path):
        from bench.zero_shot_baseline import evaluate_zero_shot_split

        zero_shot_summary = await evaluate_zero_shot_split(
            "val",
            model_name="qwen2.5:7b-instruct",
            mock=True,
            output_dir=tmp_path / "zs",
        )
        sft_summary = await evaluate_sft_split(
            "val",
            model_name="qwen2.5:7b-instruct-sft",
            mock=True,
            output_dir=tmp_path / "sft",
        )

        # Deterministic fixtures exercise artifact paths only; they imply no measured model delta.
        for summary in (zero_shot_summary, sft_summary):
            assert summary["evaluation_mode"] == "mock"
            assert summary["mock_eval"] is True
            assert summary["non_empirical"] is True

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
