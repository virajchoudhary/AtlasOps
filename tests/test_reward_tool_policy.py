"""Dense step rewards follow the canonical tool side-effect policy."""

import pytest

from agents.tool_policy import CLUSTER_MUTATING_TOOLS
from config.runtime import StepRewardTracker


def test_all_cluster_mutations_receive_mutation_step_reward():
    assert "chaos_stop_experiment" in CLUSTER_MUTATING_TOOLS
    for tool in CLUSTER_MUTATING_TOOLS:
        tracker = StepRewardTracker()
        ordinary = StepRewardTracker()
        success = tracker.record(tool, {"name": "test"}, {"success": True})
        baseline = ordinary.record("unclassified_tool", {"name": "test"}, {"success": True})
        assert success == pytest.approx(baseline + 0.08), tool


def test_failed_chaos_stop_is_penalized_as_a_mutation():
    tracker = StepRewardTracker()
    ordinary = StepRewardTracker()
    failed = tracker.record(
        "chaos_stop_experiment", {"name": "test"}, {"success": False}
    )
    baseline = ordinary.record(
        "unclassified_tool", {"name": "test"}, {"success": False}
    )
    assert failed == pytest.approx(baseline - 0.08)


def test_chaos_discovery_receives_investigation_credit():
    tracker = StepRewardTracker()
    ordinary = StepRewardTracker()
    observed = tracker.record("chaos_list_experiments", {}, {"success": True})
    baseline = ordinary.record("unclassified_tool", {}, {"success": True})
    assert observed == pytest.approx(baseline + 0.05)
