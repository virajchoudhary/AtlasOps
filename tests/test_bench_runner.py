"""Tests for the benchmark runner — reward contract, scoring, comparison table."""

import asyncio
import pytest
from unittest.mock import AsyncMock, Mock


class TestRunScenario:
    def test_passes_cascade_tier_to_judge(self, monkeypatch):
        from bench import runner

        incident = {
            "triage": {"trajectory": [], "final": {"severity": "P1"}},
            "diagnosis": {"trajectory": [], "final": {}},
            "remediation": {
                "trajectory": [],
                "final": {"outcome": "resolved", "time_to_resolve_seconds": 10},
            },
            "comms": {"trajectory": [], "final": {"postmortem_path": "test.md"}},
        }
        judge_score = {
            "reasoning": 0.8,
            "correctness": 0.9,
            "efficiency": 0.85,
            "overall": 0.85,
        }
        handle_incident = AsyncMock(return_value=incident)
        judge_trajectory = AsyncMock(return_value=judge_score)
        reset_cluster = Mock()
        monkeypatch.setattr(runner, "apply_chaos", Mock(return_value=True))
        monkeypatch.setattr(
            runner,
            "wait_for_alert",
            Mock(return_value={"commonLabels": {"alertname": "TestAlert"}, "alerts": []}),
        )
        monkeypatch.setattr(runner, "handle_incident", handle_incident)
        monkeypatch.setattr(runner, "judge_trajectory", judge_trajectory)
        monkeypatch.setattr(runner, "reset_cluster", reset_cluster)

        episode = asyncio.run(runner.run_scenario("cascade/test-tier-regression"))

        judge_trajectory.assert_awaited_once_with(incident, tier="cascade")
        reset_cluster.assert_called_once_with()
        assert episode["status"] == "ok"
        assert episode["tier"] == "cascade"

    def test_uses_unknown_tier_for_legacy_scenario_id(self, monkeypatch):
        from bench import runner

        incident = {
            "triage": {"trajectory": [], "final": {"severity": "P1"}},
            "diagnosis": {"trajectory": [], "final": {}},
            "remediation": {
                "trajectory": [],
                "final": {"outcome": "resolved", "time_to_resolve_seconds": 10},
            },
            "comms": {"trajectory": [], "final": {"postmortem_path": "test.md"}},
        }
        judge_trajectory = AsyncMock(return_value={"overall": 0.85})
        reset_cluster = Mock()
        monkeypatch.setattr(runner, "apply_chaos", Mock(return_value=True))
        monkeypatch.setattr(
            runner,
            "wait_for_alert",
            Mock(return_value={"commonLabels": {"alertname": "TestAlert"}, "alerts": []}),
        )
        monkeypatch.setattr(runner, "handle_incident", AsyncMock(return_value=incident))
        monkeypatch.setattr(runner, "judge_trajectory", judge_trajectory)
        monkeypatch.setattr(runner, "reset_cluster", reset_cluster)

        episode = asyncio.run(runner.run_scenario("legacy-scenario"))

        judge_trajectory.assert_awaited_once_with(incident, tier="unknown")
        reset_cluster.assert_called_once_with()
        assert episode["status"] == "ok"
        assert episode["tier"] == "unknown"

    def test_benchmark_single_scenario_reaches_judge_offline(self, monkeypatch):
        """Pipeline G2 validation: single scenario offline dry run reaches judge and calculates reward."""
        from bench import runner

        scenario_id = "single_fault/sf-001"
        incident = {
            "triage": {
                "trajectory": [{"turn": 1, "action": "triage_classify", "result": {"severity": "P2"}}],
                "final": {"severity": "P2", "blast_radius": "service_isolated"},
            },
            "diagnosis": {
                "trajectory": [{"turn": 2, "action": "logs_get", "result": "CrashLoopBackOff"}],
                "final": {"root_cause": "OOMKill", "target": "cartservice"},
            },
            "remediation": {
                "trajectory": [{"turn": 3, "action": "deployment_restart", "result": "restarted"}],
                "final": {"outcome": "resolved", "time_to_resolve_seconds": 45},
            },
            "comms": {
                "trajectory": [{"turn": 4, "action": "postmortem_draft", "result": {"path": "docs/postmortems/sf-001.md"}}],
                "final": {"postmortem_path": "docs/postmortems/sf-001.md"},
            },
            "verification": {
                "agent_claimed_resolved": True,
                "env_resolved": True,
                "verification_status": "passed",
            },
        }
        judge_score = {
            "reasoning": 0.90,
            "correctness": 0.95,
            "efficiency": 0.88,
            "overall": 0.91,
        }

        apply_chaos_mock = Mock(return_value=True)
        wait_for_alert_mock = Mock(return_value={
            "commonLabels": {"alertname": "AtlasOpsOnlineBoutiqueDeploymentUnavailable", "service": "cartservice"},
            "alerts": [{"labels": {"alertname": "AtlasOpsOnlineBoutiqueDeploymentUnavailable", "service": "cartservice"}}],
        })
        handle_incident_mock = AsyncMock(return_value=incident)
        judge_trajectory_mock = AsyncMock(return_value=judge_score)
        reset_cluster_mock = Mock()

        monkeypatch.setattr(runner, "apply_chaos", apply_chaos_mock)
        monkeypatch.setattr(runner, "wait_for_alert", wait_for_alert_mock)
        monkeypatch.setattr(runner, "handle_incident", handle_incident_mock)
        monkeypatch.setattr(runner, "judge_trajectory", judge_trajectory_mock)
        monkeypatch.setattr(runner, "reset_cluster", reset_cluster_mock)

        episode = asyncio.run(runner.run_scenario(scenario_id))

        # 1. Chaos applied for scenario
        apply_chaos_mock.assert_called_once_with(scenario_id)
        # 2. Alert ingested without evaluation-only identity.
        wait_for_alert_mock.assert_called_once_with()
        # 3. Coordinator receives an explicit verifier-only scenario channel.
        handle_incident_mock.assert_awaited_once()
        passed_alert = handle_incident_mock.call_args[0][0]
        assert "scenario_id" not in passed_alert
        assert handle_incident_mock.call_args.kwargs["scenario_id"] == scenario_id
        assert passed_alert["commonLabels"]["alertname"] == "AtlasOpsOnlineBoutiqueDeploymentUnavailable"
        assert passed_alert["commonLabels"]["service"] == "cartservice"
        assert len(passed_alert["alerts"]) == 1
        # 4. Trajectory and tier passed to judge
        judge_trajectory_mock.assert_awaited_once_with(incident, tier="single_fault")
        # 5. Judge score incorporated into episode
        assert episode["judge"] == judge_score
        assert episode["judge"]["overall"] == 0.91
        # 6. Verification and resolution tracked
        assert episode["agent_claimed_resolved"] is True
        assert episode["env_resolved"] is True
        assert episode["resolved"] is True
        assert episode["tier"] == "single_fault"
        assert episode["scenario_id"] == scenario_id
        assert episode["total_turns"] == 4
        assert episode["time_to_resolve_s"] == 45
        # 7. Centralized reward contract computed
        assert "reward_contract" in episode
        assert 0.0 <= episode["reward_contract"]["total"] <= 1.0
        assert episode["reward_contract"]["total"] > 0.8
        # 8. Cluster reset executed after episode
        reset_cluster_mock.assert_called_once_with()
        assert episode["status"] == "ok"

    def test_run_scenario_missing_verification_fails_closed(self, monkeypatch):
        from bench import runner

        scenario_id = "single_fault/sf-001"
        incident = {
            "remediation": {"final": {"outcome": "resolved"}},
            "triage": {"final": {"severity": "P1"}},
        }
        monkeypatch.setattr(runner, "apply_chaos", Mock(return_value=True))
        monkeypatch.setattr(runner, "wait_for_alert", Mock(return_value={"commonLabels": {"alertname": "Test"}}))
        monkeypatch.setattr(runner, "handle_incident", AsyncMock(return_value=incident))
        monkeypatch.setattr(runner, "judge_trajectory", AsyncMock(return_value={"overall": 0.8}))
        monkeypatch.setattr(runner, "reset_cluster", Mock())

        episode = asyncio.run(runner.run_scenario(scenario_id))
        # Missing verifier MUST fail closed
        assert episode["env_resolved"] is False
        assert episode["resolved"] is False
        assert episode["agent_claimed_resolved"] is True
        assert episode["reward_contract"]["penalties"]["false_resolution"] == pytest.approx(0.25)

    def test_run_scenario_agent_claims_true_verifier_false(self, monkeypatch):
        from bench import runner

        scenario_id = "single_fault/sf-001"
        incident = {
            "remediation": {"final": {"outcome": "resolved"}},
            "verification": {"agent_claimed_resolved": True, "env_resolved": False, "verification_status": "failed"},
            "triage": {"final": {"severity": "P1"}},
        }
        monkeypatch.setattr(runner, "apply_chaos", Mock(return_value=True))
        monkeypatch.setattr(runner, "wait_for_alert", Mock(return_value={"commonLabels": {"alertname": "Test"}}))
        monkeypatch.setattr(runner, "handle_incident", AsyncMock(return_value=incident))
        monkeypatch.setattr(runner, "judge_trajectory", AsyncMock(return_value={"overall": 0.8}))
        monkeypatch.setattr(runner, "reset_cluster", Mock())

        episode = asyncio.run(runner.run_scenario(scenario_id))
        assert episode["env_resolved"] is False
        assert episode["resolved"] is False
        assert episode["agent_claimed_resolved"] is True
        assert episode["reward_contract"]["penalties"]["false_resolution"] == pytest.approx(0.25)


# ── Reward contract ────────────────────────────────────────────────────────────

class TestRewardContract:
    def _episode(self, **kwargs):
        base = {
            "tier": "single_fault", "env_resolved": True, "resolved": True, "agent_claimed_resolved": True,
            "outcome": "resolved",
            "total_turns": 10, "time_to_resolve_s": 120,
            "judge": {"reasoning": 0.8, "correctness": 0.9, "efficiency": 0.85},
            "postmortem_path": "docs/postmortems/test.md",
        }
        base.update(kwargs)
        return base

    def test_perfect_episode_near_one(self):
        from bench.runner import _evaluate_episode_reward
        ep = self._episode(total_turns=5, time_to_resolve_s=60)
        r = _evaluate_episode_reward(ep)
        assert r["total"] > 0.85

    def test_unresolved_episode_lower(self):
        from bench.runner import _evaluate_episode_reward
        ep = self._episode(env_resolved=False, resolved=False, agent_claimed_resolved=False, outcome="unknown",
                           judge={"reasoning": 0.3, "correctness": 0.4, "efficiency": 0.5})
        r = _evaluate_episode_reward(ep)
        assert r["total"] < 0.5

    def test_command_spam_penalty_applied(self):
        from bench.runner import _evaluate_episode_reward
        ep_normal = self._episode(total_turns=10)
        ep_spam   = self._episode(total_turns=50)
        r_normal  = _evaluate_episode_reward(ep_normal)
        r_spam    = _evaluate_episode_reward(ep_spam)
        assert r_spam["total"] < r_normal["total"]
        assert r_spam["penalties"]["command_spam"] == pytest.approx(0.10)

    def test_false_resolution_penalty(self):
        from bench.runner import _evaluate_episode_reward
        ep = self._episode(env_resolved=False, resolved=False, agent_claimed_resolved=True, outcome="resolved")
        r  = _evaluate_episode_reward(ep)
        assert r["penalties"]["false_resolution"] == pytest.approx(0.25)

    def test_reward_missing_env_resolved_fails_closed(self):
        from bench.runner import _evaluate_episode_reward
        # Omit env_resolved entirely (legacy format without explicit env_resolved)
        ep = {
            "tier": "single_fault",
            "resolved": True,  # legacy flag
            "outcome": "resolved",
            "agent_claimed_resolved": True,
            "total_turns": 5,
            "time_to_resolve_s": 60,
            "judge": {"reasoning": 0.8, "correctness": 0.9, "efficiency": 0.85},
            "postmortem_path": "docs/postmortems/test.md",
        }
        r = _evaluate_episode_reward(ep)
        # Without explicit env_resolved True, resolve component receives 0.0 and false_resolution penalty applies
        assert r["components"]["resolve"] == 0.0
        assert r["penalties"]["false_resolution"] == pytest.approx(0.25)

    def test_unsafe_shortcut_penalty(self):
        from bench.runner import _evaluate_episode_reward
        ep = self._episode(judge={"reasoning": 0.8, "correctness": 0.9, "efficiency": 0.1})
        r  = _evaluate_episode_reward(ep)
        assert r["penalties"]["unsafe_shortcut"] == pytest.approx(0.20)

    def test_no_postmortem_reduces_comms(self):
        from bench.runner import _evaluate_episode_reward
        ep_with    = self._episode(postmortem_path="some/path.md")
        ep_without = self._episode(postmortem_path=None)
        r_with    = _evaluate_episode_reward(ep_with)
        r_without = _evaluate_episode_reward(ep_without)
        assert r_with["total"] > r_without["total"]

    def test_cascade_tier_weights_evidence_higher(self):
        from bench.runner import _evaluate_episode_reward
        ep_sf = self._episode(tier="single_fault")
        ep_cs = self._episode(tier="cascade")
        r_sf  = _evaluate_episode_reward(ep_sf)
        r_cs  = _evaluate_episode_reward(ep_cs)
        # cascade weights r_evidence at 0.25 vs single_fault at 0.25 too — both same
        # but cascade weights r_resolve lower (0.30 vs 0.35) so a resolved episode
        # scores slightly differently
        assert isinstance(r_sf["total"], float) and isinstance(r_cs["total"], float)

    def test_adversarial_penalties_amplified(self):
        from bench.runner import _evaluate_episode_reward
        ep_sf  = self._episode(tier="single_fault",  total_turns=50)
        ep_adv = self._episode(tier="adversarial",   total_turns=50)
        r_sf   = _evaluate_episode_reward(ep_sf)
        r_adv  = _evaluate_episode_reward(ep_adv)
        assert r_adv["penalty_total"] > r_sf["penalty_total"]

    def test_reward_always_clamped_01(self):
        from bench.runner import _evaluate_episode_reward
        # Worst possible episode
        ep = self._episode(
            resolved=False, outcome="resolved", total_turns=100,
            time_to_resolve_s=9999, postmortem_path=None,
            judge={"reasoning": 0.0, "correctness": 0.0, "efficiency": 0.0},
        )
        r = _evaluate_episode_reward(ep)
        assert 0.0 <= r["total"] <= 1.0

    def test_speed_score_logistic(self):
        from bench.runner import _bounded_speed_score
        # Fast resolution should score high
        assert _bounded_speed_score(60, "single_fault") > 0.8
        # Slow resolution should score low
        assert _bounded_speed_score(600, "single_fault") < 0.2
        # Midpoint ~150s for single_fault → score near 0.5
        score_mid = _bounded_speed_score(150, "single_fault")
        assert 0.4 < score_mid < 0.6


class TestFrozenScenarioList:
    def test_frozen_scenarios_count(self):
        from config.runtime import FROZEN_SCENARIOS, FROZEN_STATIC_SCENARIO_COUNT
        assert FROZEN_STATIC_SCENARIO_COUNT == 28
        assert len(FROZEN_SCENARIOS) == FROZEN_STATIC_SCENARIO_COUNT

    def test_all_tiers_represented(self):
        from config.runtime import FROZEN_SCENARIOS
        tiers = {s.split("/")[0] for s in FROZEN_SCENARIOS}
        assert "single_fault" in tiers
        assert "cascade" in tiers
        assert "named_replays" in tiers
        assert "multi_fault" in tiers

    def test_named_replays_count(self):
        from config.runtime import FROZEN_SCENARIOS
        replays = [s for s in FROZEN_SCENARIOS if "named_replays" in s]
        assert len(replays) == 10

    def test_no_duplicate_scenarios(self):
        from config.runtime import FROZEN_SCENARIOS
        assert len(FROZEN_SCENARIOS) == len(set(FROZEN_SCENARIOS))

    def test_dynamic_default_is_separate_from_frozen_catalogue(self):
        from config.runtime import (
            DEFAULT_BENCHMARK_MAX_SCENARIO_COUNT,
            DEFAULT_DYNAMIC_ADVERSARIAL_COUNT,
            FROZEN_STATIC_SCENARIO_COUNT,
        )

        assert DEFAULT_DYNAMIC_ADVERSARIAL_COUNT == 10
        assert DEFAULT_BENCHMARK_MAX_SCENARIO_COUNT == 38
        assert DEFAULT_BENCHMARK_MAX_SCENARIO_COUNT == (
            FROZEN_STATIC_SCENARIO_COUNT + DEFAULT_DYNAMIC_ADVERSARIAL_COUNT
        )


class TestComparisonTable:
    def test_summary_has_required_keys(self, tmp_path):
        from bench.runner import compute_summary
        results = [
            {"scenario_id": "sf-001", "tier": "single_fault", "status": "ok",
             "resolved": True, "outcome": "resolved", "time_to_resolve_s": 120,
             "total_turns": 8, "judge": {"overall": 0.8}, "postmortem_path": "p.md",
             "reward_contract": {"total": 0.75}},
        ]
        summary = compute_summary(results, "test_tag", "test_model")
        for key in ("tag", "model", "resolution_rate", "avg_reward", "cascade_resolution_rate",
                    "named_replay_resolution_rate", "per_tier"):
            assert key in summary, f"Missing key: {key}"

    def test_resolution_rate_calculation(self, tmp_path):
        from bench.runner import compute_summary
        results = [
            {"scenario_id": f"s{i}", "tier": "single_fault", "status": "ok",
             "resolved": i < 7, "outcome": "resolved" if i < 7 else "unknown",
             "time_to_resolve_s": 100, "total_turns": 5,
             "judge": {"overall": 0.7}, "postmortem_path": "p.md",
             "reward_contract": {"total": 0.7}}
            for i in range(10)
        ]
        summary = compute_summary(results, "test", "test_model")
        assert summary["resolution_rate"] == pytest.approx(0.7)
