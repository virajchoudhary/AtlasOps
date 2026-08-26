import asyncio

import pytest
from agents import coordinator
from agents import judge
from unittest.mock import AsyncMock, Mock

from bench.alert_contract import (
    AlertObservationContaminated,
    AlertObservationTimeout,
)
import eval as legacy_eval


@pytest.mark.parametrize(
    ("exception", "error_code"),
    [
        (AlertObservationTimeout("timeout"), "alert_observation_timeout"),
        (
            AlertObservationContaminated("unrelated"),
            "alert_observation_contaminated",
        ),
    ],
)
def test_legacy_eval_rejects_invalid_alert_observations(
    monkeypatch, exception, error_code
):
    handle_incident = AsyncMock()
    judge_trajectory = AsyncMock()
    reset_chaos = Mock()
    monkeypatch.setattr(
        legacy_eval,
        "assert_consumer_may_use_scenario",
        Mock(),
    )
    monkeypatch.setattr(legacy_eval, "apply_chaos", Mock(return_value=True))
    monkeypatch.setattr(legacy_eval, "wait_for_alert", Mock(side_effect=exception))
    monkeypatch.setattr(legacy_eval, "reset_chaos", reset_chaos)
    monkeypatch.setattr(coordinator, "handle_incident", handle_incident)
    monkeypatch.setattr(judge, "judge_trajectory", judge_trajectory)

    episode = asyncio.run(
        legacy_eval.run_episode("single_fault/sf-001")
    )

    assert episode["status"] == "error"
    assert episode["error"] == error_code
    assert episode["alert_observation_failure"] is True
    assert episode["environment_invalid_before_trial"] is True
    reset_chaos.assert_called_once_with()
    handle_incident.assert_not_awaited()
    judge_trajectory.assert_not_awaited()


def test_legacy_eval_ttr_excludes_observation_and_agent_claim(monkeypatch):
    incident = {
        "triage": {"trajectory": [], "final": {"severity": "P1"}},
        "diagnosis": {"trajectory": [], "final": {}},
        "remediation": {
            "trajectory": [],
            "final": {"outcome": "resolved", "time_to_resolve_seconds": 9999},
        },
        "comms": {"trajectory": [], "final": {}},
        "verification": {"agent_claimed_resolved": True, "env_resolved": True},
    }
    monkeypatch.setattr(legacy_eval.time, "time", Mock(side_effect=[100.0, 130.0]))
    monkeypatch.setattr(
        legacy_eval,
        "assert_consumer_may_use_scenario",
        Mock(),
    )
    monkeypatch.setattr(legacy_eval, "apply_chaos", Mock(return_value=True))
    monkeypatch.setattr(
        legacy_eval,
        "wait_for_alert",
        Mock(return_value={"commonLabels": {}, "alerts": []}),
    )
    monkeypatch.setattr(legacy_eval, "reset_chaos", Mock())
    monkeypatch.setattr(
        coordinator,
        "handle_incident",
        AsyncMock(return_value=incident),
    )
    monkeypatch.setattr(judge, "judge_trajectory", AsyncMock(return_value={"overall": 0.8}))

    episode = asyncio.run(legacy_eval.run_episode("single_fault/sf-001"))

    assert episode["status"] == "ok"
    assert episode["time_to_resolve_s"] == 30
    assert episode["time_to_resolve_source"] == "harness_wall_clock"
    assert episode["agent_declared_time_to_resolve_s"] == 9999
