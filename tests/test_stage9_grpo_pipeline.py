"""Tests for Stage 9: Correct and Train Online GRPO (Gate G9).

Validates:
1. Normalized Group Relative Policy Optimization (GRPO) advantage calculation.
2. 100% curriculum training split isolation (zero leakage to Val or Test).
3. Direct completion-to-environment reward coupling and objective verifier contract.
4. Synthetic compatibility outputs remain explicitly NON_EMPIRICAL.
5. Mock evaluation does not update the shared comparison table.
6. GRPO generation-batch preflight, reward callback compatibility, and feedback-responsive Train prompts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from bench.grpo_eval import evaluate_grpo_mock_episode, evaluate_grpo_split
from config.splits import TEST_SPLIT, TRAIN_SPLIT, VAL_SPLIT
from config.runtime import CurriculumManager
from training import grpo
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

    def test_map_dataset_resamples_after_curriculum_update(self, monkeypatch):
        curriculum = CurriculumManager()
        monkeypatch.setattr(grpo, "_curriculum", curriculum)
        monkeypatch.setattr(
            grpo.random,
            "choices",
            lambda population, **_kwargs: [population[0]],
        )

        class GenerationGroupConsumer:
            def __init__(self, train_dataset):
                self.train_dataset = train_dataset

            def capture_prompts_around_feedback(self):
                # Simulate repeated-index reads before the reward callback runs.
                generation_group = [self.train_dataset[0] for _ in range(4)]
                for row in generation_group:
                    curriculum.record(
                        scenario_id=row["scenario_id"],
                        resolved=False,
                        reward=0.0,
                    )
                after = self.train_dataset[0]
                return generation_group, after

        consumer = GenerationGroupConsumer(
            grpo.build_curriculum_prompt_dataset(
                ["single_fault", "cascade", "multi_fault", "named_replays"]
            )
        )
        before_group, after = consumer.capture_prompts_around_feedback()

        train_scenarios = set(TRAIN_SPLIT)
        before = before_group[0]
        assert len(before_group) == 4
        assert {row["scenario_id"] for row in before_group} == {before["scenario_id"]}
        assert {row["prompt"] for row in before_group} == {before["prompt"]}
        assert after["scenario_id"] in train_scenarios
        assert before["scenario_id"] in train_scenarios
        assert before["scenario_id"] not in set(VAL_SPLIT) | set(TEST_SPLIT)
        assert after["scenario_id"] not in set(VAL_SPLIT) | set(TEST_SPLIT)
        assert before["prompt"] == grpo._direct_action_prompt(before["scenario_id"])
        assert after["prompt"] == grpo._direct_action_prompt(after["scenario_id"])
        assert after["scenario_id"] != before["scenario_id"]
        assert curriculum.stats()["total_episodes"] == len(before_group)

    def test_pytorch_dataloader_resamples_after_curriculum_update(self, monkeypatch):
        torch = pytest.importorskip("torch")
        curriculum = CurriculumManager()
        monkeypatch.setattr(grpo, "_curriculum", curriculum)
        monkeypatch.setattr(
            grpo.random,
            "choices",
            lambda population, **_kwargs: [population[0]],
        )
        dataset = grpo.build_curriculum_prompt_dataset(
            ["single_fault", "cascade", "multi_fault", "named_replays"]
        )
        dataloader = torch.utils.data.DataLoader(
            dataset,
            batch_size=4,
            sampler=[0, 0, 0, 0],
            num_workers=0,
            collate_fn=lambda rows: rows,
        )
        before_group = next(iter(dataloader))
        before = before_group[0]
        assert {row["scenario_id"] for row in before_group} == {before["scenario_id"]}

        for row in before_group:
            curriculum.record(row["scenario_id"], resolved=False, reward=0.0)

        after_loader = torch.utils.data.DataLoader(
            dataset,
            batch_size=1,
            sampler=[0],
            num_workers=0,
            collate_fn=lambda rows: rows,
        )
        after = next(iter(after_loader))[0]
        assert after["scenario_id"] != before["scenario_id"]
        assert after["scenario_id"] in set(TRAIN_SPLIT)
        assert after["scenario_id"] not in set(VAL_SPLIT) | set(TEST_SPLIT)

    def test_pinned_grpo_batch_preflight_rejects_incompatible_default(self):
        with pytest.raises(ValueError, match="divisible by num_generations"):
            grpo.validate_grpo_batch_configuration(
                per_device_train_batch_size=1,
                gradient_accumulation_steps=4,
                num_generations=8,
            )

    def test_incompatible_batch_fails_before_model_load_or_output(
        self, monkeypatch, tmp_path
    ):
        output_dir = tmp_path / "run"
        args = SimpleNamespace(
            execute_live_chaos=True,
            kube_context="kind-atlasops-test",
            optuna=0,
            tiers="single_fault",
            seed=42,
            model="Qwen/Qwen2.5-7B-Instruct",
            model_revision="resolved-model-revision",
            tokenizer=None,
            tokenizer_revision="resolved-tokenizer-revision",
            sft_checkpoint=tmp_path / "sft",
            lr=1e-6,
            beta=0.04,
            batch_size=1,
            grad_accum=4,
            num_generations=8,
            max_steps=10,
            max_compl_len=128,
        )
        monkeypatch.setattr(
            grpo,
            "_require_live_execution",
            lambda *_args, **_kwargs: "kind-atlasops-test",
        )
        monkeypatch.setattr(
            grpo,
            "load_model_and_tokenizer",
            lambda *_args, **_kwargs: pytest.fail("model loaded before GRPO batch validation"),
        )

        # Inspect the preserved batch contract only; production observation admission stays closed.
        monkeypatch.setattr(grpo, "_require_g9_observation_order_protocol", lambda: None)
        with pytest.raises(ValueError, match="divisible by num_generations"):
            grpo.run_training(args, output_dir)

        assert not output_dir.exists()

    def test_main_rejects_default_batch_before_creating_output(
        self, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.setattr(grpo, "_require_g9_observation_order_protocol", lambda: None)
        output_dir = tmp_path / "run"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "grpo.py",
                "--model",
                "Qwen/Qwen2.5-7B-Instruct",
                "--model-revision",
                "resolved-model-revision",
                "--tokenizer-revision",
                "resolved-tokenizer-revision",
                "--sft-checkpoint",
                str(tmp_path / "sft"),
                "--output",
                str(output_dir),
                "--execute-live-chaos",
                "--kube-context",
                "kind-atlasops-test",
            ],
        )

        with pytest.raises(SystemExit) as exc:
            grpo.main()

        assert exc.value.code == 2
        assert "divisible by num_generations" in capsys.readouterr().err
        assert not output_dir.exists()

    def test_optuna_search_is_explicitly_deferred_without_trial_or_model_load(
        self, monkeypatch, tmp_path
    ):
        optuna = ModuleType("optuna")
        optuna.logging = SimpleNamespace(set_verbosity=lambda *_args: None)
        optuna.create_study = lambda **_kwargs: pytest.fail("Optuna trial started before deferral")
        monkeypatch.setitem(sys.modules, "optuna", optuna)
        curriculum = CurriculumManager()
        monkeypatch.setattr(grpo, "_curriculum", curriculum)
        monkeypatch.setattr(
            grpo,
            "_require_live_execution",
            lambda *_args, **_kwargs: pytest.fail("live execution checked before Optuna deferral"),
        )
        monkeypatch.setattr(
            grpo,
            "load_model_and_tokenizer",
            lambda *_args, **_kwargs: pytest.fail("model loaded before Optuna deferral"),
        )

        output_dir = tmp_path / "optuna"
        with pytest.raises(RuntimeError, match="Optuna GRPO search is deferred"):
            grpo.run_optuna_search(
                "Qwen/Qwen2.5-7B-Instruct",
                ["single_fault"],
                output_dir,
                "resolved-model-revision",
                "Qwen/Qwen2.5-7B-Instruct",
                "resolved-tokenizer-revision",
                tmp_path / "sft",
                n_trials=1,
                execute_live_chaos=True,
                kube_context="kind-atlasops-test",
            )

        assert not output_dir.exists()
        assert curriculum.stats()["total_episodes"] == 0

    def test_main_defers_optuna_before_creating_output(self, monkeypatch, tmp_path, capsys):
        output_dir = tmp_path / "run"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "grpo.py",
                "--model",
                "Qwen/Qwen2.5-7B-Instruct",
                "--model-revision",
                "resolved-model-revision",
                "--tokenizer-revision",
                "resolved-tokenizer-revision",
                "--sft-checkpoint",
                str(tmp_path / "sft"),
                "--output",
                str(output_dir),
                "--optuna",
                "1",
            ],
        )

        with pytest.raises(SystemExit) as exc:
            grpo.main()

        assert exc.value.code == 2
        assert "Optuna GRPO search is deferred" in capsys.readouterr().err
        assert not output_dir.exists()

    def test_online_reward_instance_exposes_trl_name_without_initializing_live_state(self):
        reward = object.__new__(grpo.OnlineRewardFunction)

        assert callable(reward)
        assert reward.__name__ == "online_reward"

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
