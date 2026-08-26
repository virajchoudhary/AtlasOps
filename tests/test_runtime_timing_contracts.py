import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from agents import coordinator
from agents import judge

import leaderboard
from bench.alert_contract import (
    AlertObservationContaminated,
    AlertObservationTimeout,
)
from training import generate_trajectories_fast as fast_sft


@pytest.mark.parametrize(
    ("exception", "failure_name"),
    [
        (AlertObservationTimeout("timeout"), "alert_observation_failure"),
        (
            AlertObservationContaminated("unrelated"),
            "environment_invalid_before_trial",
        ),
    ],
)
def test_leaderboard_rejects_invalid_alert_observations(
    monkeypatch, exception, failure_name
):
    model_cfg = {"base_url": "unit", "model_id": "unit-model"}
    reset_chaos = Mock()
    monkeypatch.setattr(leaderboard, "assert_consumer_may_use_scenario", Mock())
    monkeypatch.setattr(leaderboard, "apply_chaos", Mock(return_value=True))
    monkeypatch.setattr(leaderboard, "wait_for_alert", Mock(side_effect=exception))
    monkeypatch.setattr(leaderboard, "reset_chaos", reset_chaos)
    monkeypatch.setattr(coordinator, "handle_incident", AsyncMock())
    monkeypatch.setattr(judge, "judge_trajectory", AsyncMock())

    episode = asyncio.run(
        leaderboard.run_episode(model_cfg, "single_fault/sf-001", "single_fault")
    )

    assert episode["status"] == "error"
    assert episode[failure_name] is True
    reset_chaos.assert_called_once_with()
    coordinator.handle_incident.assert_not_awaited()


def test_leaderboard_ttr_excludes_agent_declared_duration(monkeypatch):
    model_cfg = {"base_url": "unit", "model_id": "unit-model"}
    incident = {
        "triage": {"trajectory": [], "final": {}},
        "diagnosis": {"trajectory": [], "final": {}},
        "remediation": {
            "trajectory": [],
            "final": {"outcome": "resolved", "time_to_resolve_seconds": 9999},
        },
        "comms": {"trajectory": [], "final": {}},
        "verification": {"agent_claimed_resolved": True, "env_resolved": True},
    }
    monkeypatch.setattr(leaderboard.time, "time", Mock(side_effect=[100.0, 130.0]))
    monkeypatch.setattr(leaderboard, "assert_consumer_may_use_scenario", Mock())
    monkeypatch.setattr(leaderboard, "apply_chaos", Mock(return_value=True))
    monkeypatch.setattr(
        leaderboard,
        "wait_for_alert",
        Mock(return_value={"commonLabels": {}, "alerts": []}),
    )
    monkeypatch.setattr(leaderboard, "reset_chaos", Mock())
    monkeypatch.setattr(coordinator, "handle_incident", AsyncMock(return_value=incident))
    monkeypatch.setattr(judge, "judge_trajectory", AsyncMock(return_value={"overall": 0.8}))

    episode = asyncio.run(
        leaderboard.run_episode(model_cfg, "single_fault/sf-001", "single_fault")
    )

    assert episode["time_to_resolve_s"] == 30
    assert episode["time_to_resolve_source"] == "harness_wall_clock"
    assert episode["agent_declared_time_to_resolve_s"] == 9999


def test_fast_sft_records_harness_timing_provenance(monkeypatch):
    incident = {
        "remediation": {
            "final": {"outcome": "resolved", "time_to_resolve_seconds": 9999}
        }
    }
    monkeypatch.setattr(coordinator, "handle_incident", AsyncMock(return_value=incident))

    result = asyncio.run(fast_sft.run_scenario("single_fault/sf-001"))

    assert result[1] >= 0
    provenance = result[0]["timing_provenance"]
    assert provenance["time_to_resolve_source"] == "harness_wall_clock"
    assert provenance["agent_declared_time_to_resolve_s"] == 9999
