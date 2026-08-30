"""Reward and evaluation integrity contracts.

Each test pins a defect that made a reported number mean something other than
what it claimed:

* a judge outage scoring strictly better than a working strict judge,
* GRPO rewarding resolution it structurally could not observe,
* the benchmark handing the agent its own answer key,
* the dense tracker withholding credit from the only resolving action.
"""

from __future__ import annotations

import pytest

from config.runtime import StepRewardTracker, evaluate_reward_contract


def _episode(**overrides):
    episode = {
        "tier": "single_fault",
        "env_resolved": True,
        "agent_claimed_resolved": True,
        "outcome": "resolved",
        "total_turns": 8,
        "time_to_resolve_s": 120,
        "postmortem_path": "docs/postmortems/x.md",
        "judge": {
            "reasoning": 0.8,
            "correctness": 0.8,
            "efficiency": 0.8,
            "red_herring_handling": 0.8,
            "overall": 0.8,
        },
    }
    episode.update(overrides)
    return episode


# The exact pre-fix fallback: mid scores on every dimension, no availability flag.
PRE_FIX_FALLBACK = {
    "correctness": 0.5, "efficiency": 0.5, "reasoning": 0.5,
    "red_herring_handling": 0.5, "overall": 0.5, "critique": "judge_fallback",
}


class TestJudgeOutageCannotInflateReward:
    def test_fallback_assigns_no_score_and_flags_itself(self):
        """Pins the fix directly: the old constant would fail every assertion here."""
        from agents.judge import _FALLBACK

        assert _FALLBACK["judge_available"] is False
        for dimension in ("correctness", "efficiency", "reasoning", "red_herring_handling", "overall"):
            assert _FALLBACK[dimension] == 0.0, dimension

    def test_current_fallback_scores_strictly_below_the_pre_fix_one(self):
        """The regression this replaces: an outage used to be worth ~0.74.

        Comparing an outage against a *good* grade was vacuous — the pre-fix
        fallback also scored below a good grade. The meaningful comparison is
        against the pre-fix fallback itself.
        """
        from agents.judge import _FALLBACK

        current = evaluate_reward_contract(_episode(judge=dict(_FALLBACK)))["total"]
        pre_fix = evaluate_reward_contract(_episode(judge=dict(PRE_FIX_FALLBACK)))["total"]
        assert current < pre_fix

    def test_pre_fix_fallback_would_have_earned_unjustified_credit(self):
        """Demonstrates what the fix removes, so the test cannot pass vacuously."""
        components = evaluate_reward_contract(_episode(judge=dict(PRE_FIX_FALLBACK)))["components"]
        assert components["evidence"] == 0.5
        assert components["safety"] == 0.5

    def test_outage_earns_nothing_on_judge_derived_components(self):
        """No judge graded this episode, so no judge-derived credit may accrue.

        The scalar total of an ungraded episode is not itself meaningful — that
        is what the judge_available flag is for — but it must never contain
        invented evidence or safety credit.
        """
        from agents.judge import _FALLBACK

        components = evaluate_reward_contract(_episode(judge=dict(_FALLBACK)))["components"]
        assert components["evidence"] == 0.0
        assert components["safety"] == 0.0
        assert components["red_herring_bonus"] == 0.0

    def test_outage_is_flagged_for_exclusion(self):
        from agents.judge import _FALLBACK

        assert evaluate_reward_contract(_episode(judge=dict(_FALLBACK)))["judge_available"] is False
        assert evaluate_reward_contract(_episode())["judge_available"] is True

    def test_judge_derived_penalties_are_suppressed_during_an_outage(self):
        """Infrastructure failure must not be charged to the agent as misbehaviour."""
        from agents.judge import _FALLBACK

        penalties = evaluate_reward_contract(_episode(judge=dict(_FALLBACK)))["penalties"]
        assert penalties["unsafe_shortcut"] == 0.0
        assert penalties["hallucinated_evidence"] == 0.0

    def test_penalties_still_apply_when_the_judge_did_grade(self):
        graded = {
            "reasoning": 0.1, "correctness": 0.1, "efficiency": 0.1,
            "red_herring_handling": 0.1, "overall": 0.1, "judge_available": True,
        }
        penalties = evaluate_reward_contract(_episode(judge=graded))["penalties"]
        assert penalties["unsafe_shortcut"] > 0.0
        assert penalties["hallucinated_evidence"] > 0.0


class TestResolutionTruth:
    def test_reward_follows_the_verifier_not_the_agent_claim(self):
        lying = evaluate_reward_contract(
            _episode(env_resolved=False, agent_claimed_resolved=True)
        )
        assert lying["components"]["resolve"] == 0.0
        assert lying["penalties"]["false_resolution"] > 0.0

    def test_missing_env_resolved_scores_zero_resolution(self):
        """The exact shape GRPO used to emit: no env_resolved key at all."""
        episode = _episode()
        del episode["env_resolved"]
        assert evaluate_reward_contract(episode)["components"]["resolve"] == 0.0

    def test_grpo_rollout_reports_verifier_truth(self, monkeypatch):
        """Behavioural: run the real rollout against a stubbed coordinator.

        A source-substring assertion stayed green while the rollout still scored
        the agent's self-claim, so this drives the actual code path instead.
        """
        import asyncio

        import agents.coordinator as coordinator
        import agents.judge as judge_module
        from training.grpo import OnlineRewardFunction

        # Agent claims success; the objective verifier disagrees.
        incident = {
            "remediation": {"final": {"outcome": "resolved"}, "step_reward_summary": {}},
            "triage": {"trajectory": [], "step_reward_summary": {}},
            "diagnosis": {"trajectory": [], "step_reward_summary": {}},
            "comms": {"final": {"postmortem_path": "x.md"}, "step_reward_summary": {}},
            "verification": {"env_resolved": False},
            "agent_claimed_resolved": True,
        }

        async def fake_handle_incident(alert, scenario_id=None, **kwargs):
            assert "scenario_id" not in alert, "scenario_id must not reach the model prompt"
            return incident

        async def fake_judge(*_a, **_k):
            return {"overall": 0.5, "judge_available": True}

        monkeypatch.setattr(coordinator, "handle_incident", fake_handle_incident)
        monkeypatch.setattr(judge_module, "judge_trajectory", fake_judge)
        monkeypatch.setenv(OnlineRewardFunction.COUPLING_ACK_ENV, "1")

        fn = OnlineRewardFunction(tiers=["single_fault"])
        episode = asyncio.run(fn._run_one_rollout("completion", "single_fault/sf-002", "single_fault"))

        assert episode["env_resolved"] is False
        assert episode["resolved"] is False, "resolution must follow the verifier, not the claim"
        assert episode["agent_claimed_resolved"] is True
        # And the reward contract must therefore award nothing for resolution.
        assert evaluate_reward_contract(episode)["components"]["resolve"] == 0.0

    def test_grpo_rollout_carries_dense_step_rewards(self, monkeypatch):
        """compute_reward blends 30% dense signal; the keys must actually arrive."""
        import asyncio

        import agents.coordinator as coordinator
        import agents.judge as judge_module
        from training.grpo import OnlineRewardFunction, compute_reward

        incident = {
            role: {"step_reward_summary": {"dense_reward_total": 1.0}, "trajectory": [], "final": {}}
            for role in ("triage", "diagnosis", "remediation", "comms")
        }
        incident["verification"] = {"env_resolved": True}
        incident["remediation"]["final"] = {"outcome": "resolved"}

        async def fake_handle_incident(alert, scenario_id=None, **kwargs):
            return incident

        async def fake_judge(*_a, **_k):
            return {"overall": 0.5, "judge_available": True}

        monkeypatch.setattr(coordinator, "handle_incident", fake_handle_incident)
        monkeypatch.setattr(judge_module, "judge_trajectory", fake_judge)
        monkeypatch.setenv(OnlineRewardFunction.COUPLING_ACK_ENV, "1")

        fn = OnlineRewardFunction(tiers=["single_fault"])
        episode = asyncio.run(fn._run_one_rollout("c", "single_fault/sf-002", "single_fault"))

        dense = sum(
            episode.get(role, {}).get("step_reward_summary", {}).get("dense_reward_total", 0.0)
            for role in ("triage", "diagnosis", "remediation", "comms")
        )
        assert dense == 4.0, "dense step rewards never reached the episode dict"

        stripped = {k: v for k, v in episode.items()
                    if k not in ("triage", "diagnosis", "remediation", "comms")}
        assert compute_reward(episode) > compute_reward(stripped)

    def test_grpo_refuses_to_run_with_an_uncoupled_reward(self, monkeypatch):
        from training.grpo import OnlineRewardFunction, UncoupledRewardError

        monkeypatch.delenv(OnlineRewardFunction.COUPLING_ACK_ENV, raising=False)
        with pytest.raises(UncoupledRewardError, match="policy-gradient estimate is invalid"):
            OnlineRewardFunction(tiers=["single_fault"])

    def test_grpo_runs_when_the_defect_is_explicitly_acknowledged(self, monkeypatch):
        from training.grpo import OnlineRewardFunction

        monkeypatch.setenv(OnlineRewardFunction.COUPLING_ACK_ENV, "1")
        fn = OnlineRewardFunction(tiers=["single_fault"])
        assert fn.tiers == ["single_fault"]


class TestDenseRewardCreditsTheResolvingAction:
    def test_mutating_set_covers_every_cluster_mutating_tool(self):
        """Asserting equality with the alias it is bound to proved nothing.

        Name the tools instead: the local copy that drifted omitted
        chaos_stop_experiment, so the only action able to satisfy a scenario
        predicate earned no remediation credit.
        """
        from agents.tool_policy import CLUSTER_MUTATING_TOOLS

        for tool in ("chaos_stop_experiment", "argocd_rollback", "kubectl_rollout",
                     "kubectl_scale", "alertmanager_silence"):
            assert tool in StepRewardTracker._MUTATING, tool
        # And no read-only tool may leak in and earn the mutation bonus.
        assert "chaos_list_experiments" not in StepRewardTracker._MUTATING
        assert "promql_query" not in StepRewardTracker._MUTATING
        assert set(StepRewardTracker._MUTATING) == set(CLUSTER_MUTATING_TOOLS)

    def test_stopping_chaos_earns_the_remediation_bonus(self):
        """The only action able to satisfy any scenario predicate must be rewarded."""
        credited = StepRewardTracker()
        credited.record("chaos_stop_experiment", {"name": "sf-002"}, {"success": True})

        neutral = StepRewardTracker()
        neutral.record("some_unclassified_tool", {}, {"success": True})

        assert credited.total() > neutral.total()

    def test_discovery_is_scored_as_investigation(self):
        assert "chaos_list_experiments" in StepRewardTracker._INVESTIGATIVE
        assert "chaos_list_experiments" not in StepRewardTracker._MUTATING


class TestBenchmarkSummaryExcludesUngradedEpisodes:
    def test_ungraded_episodes_do_not_drag_the_judge_mean(self):
        from bench.runner import compute_summary

        graded = {
            "status": "ok", "tier": "single_fault", "resolved": True,
            "judge": {"overall": 0.8, "judge_available": True},
            "reward_contract": {"total": 0.8, "penalty_total": 0.0, "penalties": {}},
        }
        ungraded = {
            "status": "ok", "tier": "single_fault", "resolved": True,
            "judge": {"overall": 0.0, "judge_available": False},
            "reward_contract": {"total": 0.8, "penalty_total": 0.0, "penalties": {}},
        }
        summary = compute_summary([graded, ungraded], tag="t", model="m")

        assert summary["avg_reward"] == 0.8  # not 0.4
        assert summary["judged_episode_count"] == 1
        assert summary["judge_unavailable_count"] == 1


class TestUngradedRunReportsNoJudgeMean:
    """A mean over zero graded episodes is not a measurement.

    Dividing by max(n, 1) published avg_reward: 0.0 for a run where the judge
    never ran once — indistinguishable from a genuinely terrible model.
    """

    def _episodes(self, judge_available):
        return [
            {
                "status": "ok", "tier": "single_fault", "resolved": False,
                "judge": {"overall": 0.0, "judge_available": judge_available},
                "reward_contract": {
                    "total": 0.3, "penalty_total": 0.0,
                    "penalties": {"unsafe_shortcut": 0.0, "hallucinated_evidence": 0.0},
                },
            }
            for _ in range(38)
        ]

    def test_no_graded_episode_reports_none_not_zero(self):
        from bench.runner import compute_summary

        summary = compute_summary(self._episodes(False), tag="t", model="m")
        assert summary["avg_reward"] is None
        assert summary["judged_episode_count"] == 0
        assert summary["judge_unavailable_count"] == 38

    def test_a_genuinely_zero_scoring_run_still_reports_zero(self):
        """The None must mean 'unmeasured', never 'measured as bad'."""
        from bench.runner import compute_summary

        summary = compute_summary(self._episodes(True), tag="t", model="m")
        assert summary["avg_reward"] == 0.0
        assert summary["judged_episode_count"] == 38

    def test_judge_derived_safety_counts_exclude_ungraded_episodes(self):
        """Reporting 0 unsafe actions for an ungraded run implies a check nobody ran."""
        from bench.runner import compute_summary

        ungraded = self._episodes(False)
        for episode in ungraded:
            # A pre-fix contract could still carry a stale penalty value.
            episode["reward_contract"]["penalties"]["unsafe_shortcut"] = 0.2
        summary = compute_summary(ungraded, tag="t", model="m")
        assert summary["unsafe_action_count"] == 0
        assert summary["hallucinated_evidence_count"] == 0

    def test_comparison_table_renders_an_unmeasured_run(self, monkeypatch, tmp_path):
        """Formatting None with %.3f would crash the report writer."""
        import bench.runner as runner

        monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
        summary = runner.compute_summary(self._episodes(False), tag="unmeasured", model="m")
        runner.write_comparison_table(summary)  # must not raise

        rendered = (tmp_path / "results" / "comparison_table.md").read_text(encoding="utf-8")
        assert "n/a" in rendered, "an unmeasured run must not render as a number"
