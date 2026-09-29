"""Regression tests for Chaos Mesh inventory phase classification."""

from __future__ import annotations

import json

import pytest

from agents.tools import chaos


def _resource(*, phase: str | None) -> dict:
    return {
        "kind": "StressChaos",
        "metadata": {
            "name": "observed-experiment",
            "namespace": "chaos-mesh",
            "labels": {"purpose": "test"},
        },
        "status": {
            "experiment": {"phase": phase},
            "conditions": [],
        },
    }


def _observe(monkeypatch, payload: object) -> dict:
    monkeypatch.setattr(
        chaos,
        "_run",
        lambda *_args, **_kwargs: {
            "success": True,
            "stdout": json.dumps(payload),
        },
    )
    return chaos.chaos_list_experiments()


def test_unavailable_chaos_api_is_not_reported_as_an_empty_inventory(monkeypatch):
    monkeypatch.setattr(
        chaos,
        "_run",
        lambda *_args, **_kwargs: {
            "success": False,
            "error": "connection refused",
        },
    )

    result = chaos.chaos_list_experiments()

    assert result["success"] is False
    assert result["observation_status"] == "unavailable"
    assert result["evidence_status"] == "environment_unavailable"
    assert result["active_experiments"] == []
    assert result["inventory_complete"] is False
    assert "inventory_count" not in result


def test_running_chaos_mesh_resource_is_the_only_stoppable_phase(monkeypatch):
    result = _observe(monkeypatch, {"items": [_resource(phase="Running")]})

    assert result["success"] is True
    assert result["observation_status"] == "observed"
    assert result["evidence_status"] == "active_experiments"
    assert result["active_experiments"] == result["inventory"]
    assert result["count"] == 1
    assert result["inventory_count"] == 1


@pytest.mark.parametrize("phase", ["Finished", "Paused"])
def test_finished_or_paused_resources_remain_in_inventory_but_are_not_stoppable(
    monkeypatch, phase,
):
    result = _observe(monkeypatch, {"items": [_resource(phase=phase)]})

    assert result["success"] is True
    assert result["observation_status"] == "observed"
    assert result["evidence_status"] == "no_active_experiments"
    assert result["active_experiments"] == []
    assert result["inventory_count"] == 1
    assert result["inventory"][0]["status"]["phase"] == phase


def test_unknown_phase_is_not_active_or_reported_as_a_clean_observation(monkeypatch):
    result = _observe(monkeypatch, {"items": [_resource(phase="FuturePhase")]})

    assert result["success"] is True
    assert result["observation_status"] == "unclassified"
    assert result["evidence_status"] == "active_state_unknown"
    assert result["active_experiments"] == []
    assert result["inventory_count"] == 1
    assert result["inventory"][0]["status"]["phase"] == "FuturePhase"


def test_paused_condition_overrides_a_running_phase(monkeypatch):
    resource = _resource(phase="Running")
    resource["status"]["conditions"] = [{"type": "Paused", "status": "True"}]

    result = _observe(monkeypatch, {"items": [resource]})

    assert result["active_experiments"] == []
    assert result["observation_status"] == "unclassified"
    assert result["evidence_status"] == "active_state_unknown"
    assert result["inventory"][0]["status"]["conditions"] == [
        {"type": "Paused", "status": "True"}
    ]


def test_running_phase_with_all_recovered_condition_is_unclassified(monkeypatch):
    resource = _resource(phase="Running")
    resource["status"]["conditions"] = [
        {"type": "AllRecovered", "status": "True"},
        {"type": "Paused", "status": "False"},
    ]

    result = _observe(monkeypatch, {"items": [resource]})

    assert result["active_experiments"] == []
    assert result["observation_status"] == "unclassified"
    assert result["evidence_status"] == "active_state_unknown"
    assert result["inventory"][0]["status"]["phase"] == "Running"


def test_pinned_chaos_mesh_records_confirm_partially_injected_experiment(monkeypatch):
    result = _observe(
        monkeypatch,
        {
            "items": [{
                "kind": "StressChaos",
                "metadata": {"name": "partial", "namespace": "chaos-mesh"},
                "status": {
                    "experiment": {
                        "desiredPhase": "Run",
                        "containerRecords": [
                            {"phase": "Injected"},
                            {"phase": "Not Injected"},
                        ],
                    },
                    "conditions": [
                        {"type": "AllInjected", "status": "False"},
                        {"type": "AllRecovered", "status": "False"},
                        {"type": "Paused", "status": "False"},
                    ],
                },
            }],
        },
    )

    assert result["active_experiments"][0]["name"] == "partial"
    assert result["inventory"][0]["status"]["desired_phase"] == "Run"
    assert result["inventory"][0]["status"]["container_records"] == [
        {"phase": "Injected"},
        {"phase": "Not Injected"},
    ]


def test_pinned_chaos_mesh_recovered_record_is_not_active(monkeypatch):
    result = _observe(
        monkeypatch,
        {
            "items": [{
                "kind": "StressChaos",
                "metadata": {"name": "recovered", "namespace": "chaos-mesh"},
                "status": {
                    "experiment": {
                        "desiredPhase": "Stop",
                        "containerRecords": [{"phase": "Not Injected"}],
                    },
                    "conditions": [
                        {"type": "AllRecovered", "status": "True"},
                        {"type": "Paused", "status": "False"},
                    ],
                },
            }],
        },
    )

    assert result["active_experiments"] == []
    assert result["inventory"][0]["name"] == "recovered"
    assert result["evidence_status"] == "no_active_experiments"


def test_unknown_pinned_container_phase_is_not_hidden_by_recovered_condition(monkeypatch):
    result = _observe(
        monkeypatch,
        {
            "items": [{
                "kind": "StressChaos",
                "metadata": {"name": "unknown", "namespace": "chaos-mesh"},
                "status": {
                    "experiment": {
                        "desiredPhase": "Stop",
                        "containerRecords": [{"phase": "FuturePhase"}],
                    },
                    "conditions": [
                        {"type": "AllRecovered", "status": "True"},
                        {"type": "Paused", "status": "False"},
                    ],
                },
            }],
        },
    )

    assert result["active_experiments"] == []
    assert result["observation_status"] == "unclassified"
    assert result["evidence_status"] == "active_state_unknown"
    assert result["inventory"][0]["status"]["container_records"] == [
        {"phase": "FuturePhase"}
    ]


def test_recovered_condition_without_pinned_records_is_unclassified(monkeypatch):
    result = _observe(
        monkeypatch,
        {
            "items": [{
                "kind": "StressChaos",
                "metadata": {"name": "missing-records", "namespace": "chaos-mesh"},
                "status": {
                    "experiment": {"desiredPhase": "Stop"},
                    "conditions": [
                        {"type": "AllRecovered", "status": "True"},
                        {"type": "Paused", "status": "False"},
                    ],
                },
            }],
        },
    )

    assert result["active_experiments"] == []
    assert result["observation_status"] == "unclassified"
    assert result["evidence_status"] == "active_state_unknown"
    assert result["inventory_count"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"items": {}},
        {"items": [None]},
        {"items": [{
            "kind": "StressChaos",
            "metadata": "not-an-object",
            "status": {"experiment": {"phase": "Running"}},
        }]},
    ],
)
def test_malformed_chaos_api_response_cannot_authorize_stop_or_claim_clean(
    monkeypatch, payload,
):
    result = _observe(monkeypatch, payload)

    assert result["success"] is False
    assert result["observation_status"] == "invalid_response"
    assert result["evidence_status"] == "environment_observation_error"
    assert result["active_experiments"] == []
