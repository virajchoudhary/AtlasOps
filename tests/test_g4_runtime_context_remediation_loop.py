"""Focused G4-02A runtime context, evidence, and remediation-loop regressions."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate_audit(monkeypatch):
    import agents.coordinator as coordinator

    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "g4-02a-test-audit-secret")
    monkeypatch.setattr(coordinator, "audit_log", MagicMock())


def _response(message: dict, finish_reason: str = "stop") -> MagicMock:
    response = MagicMock()
    response.json.return_value = {
        "choices": [{"message": message, "finish_reason": finish_reason}]
    }
    response.raise_for_status = MagicMock()
    return response


def _native_call(name: str, args: dict, call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_alert_anchors_survive_and_evaluation_metadata_is_removed():
    from agents.coordinator import build_incident_anchors, model_visible_alert

    alert = {
        "status": "firing",
        "commonLabels": {
            "alertname": "ServiceLatencyHigh",
            "service": "service-a",
            "namespace": "default",
            "tier": "single_fault",
        },
        "alerts": [{
            "labels": {
                "service": "service-a",
                "namespace": "default",
                "scenario_id": "single_fault/sf-002",
            },
            "annotations": {
                "summary": "Service A is slow",
                "description": "Observed latency is above the SLO.",
                "expected_root_cause": "benchmark truth",
            },
        }],
        "scenario_id": "single_fault/sf-002",
        "expected_root_cause": "benchmark truth",
    }

    visible = model_visible_alert(alert)
    anchors = build_incident_anchors(alert)

    assert visible["commonLabels"]["service"] == "service-a"
    assert anchors["primary_service"] == "service-a"
    assert anchors["original_description"] == "Observed latency is above the SLO."
    serialized = json.dumps(visible)
    assert "scenario_id" not in serialized
    assert "expected_root_cause" not in serialized
    assert "single_fault" not in serialized


def test_target_mismatch_is_explicit_and_observed_support_is_distinguished():
    from agents.coordinator import validate_target_consistency

    anchors = {
        "primary_service": "service-a",
        "namespace": "default",
    }
    wrong = {"affected_services": ["service-b"], "blast_radius": {"services": ["service-b"]}}

    unsubstantiated = validate_target_consistency(anchors, wrong, [])
    supported = validate_target_consistency(
        anchors,
        wrong,
        [{
            "tool": "kubectl_get",
            "args": {"resource": "pods"},
            "output": {"items": [{"metadata": {"name": "service-b-123"}}]},
        }],
    )

    assert unsubstantiated["status"] == "mismatch_unsubstantiated"
    assert unsubstantiated["requires_review"] is True
    assert supported["status"] == "mismatch_observed"
    assert supported["requires_review"] is False


def test_prometheus_transport_empty_and_positive_results_are_distinct(monkeypatch):
    import agents.tools.prometheus as prometheus

    failure = MagicMock()
    failure.raise_for_status.side_effect = prometheus.requests.RequestException("down")
    query_error = MagicMock()
    query_error.raise_for_status.return_value = None
    query_error.json.return_value = {
        "status": "error",
        "error": "bad query",
        "data": {"resultType": "vector", "result": []},
    }
    empty = MagicMock()
    empty.raise_for_status.return_value = None
    empty.json.return_value = {
        "status": "success",
        "data": {"resultType": "vector", "result": []},
    }
    positive = MagicMock()
    positive.raise_for_status.return_value = None
    positive.json.return_value = {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"value": [1, "0.5"]}]},
    }
    calls = [failure, query_error, empty, positive]
    monkeypatch.setattr(prometheus.requests, "get", lambda *args, **kwargs: calls.pop(0))

    transport = prometheus.promql_query("up")
    query_failure = prometheus.promql_query("up")
    no_series = prometheus.promql_query("up")
    series = prometheus.promql_query("up")

    assert transport["success"] is False
    assert transport["evidence_status"] == "transport_error"
    assert query_failure["success"] is False
    assert query_failure["evidence_status"] == "query_error"
    assert no_series["success"] is True
    assert no_series["evidence_status"] == "no_series"
    assert no_series["has_data"] is False
    assert series["success"] is True
    assert series["evidence_status"] == "series_present"
    assert series["has_data"] is True


def test_chaos_inventory_is_generic_observation_only(monkeypatch):
    import agents.tools.chaos as chaos

    monkeypatch.setattr(
        chaos,
        "_run",
        lambda command, timeout: {
            "success": True,
            "stdout": json.dumps({
                "items": [{
                    "kind": "StressChaos",
                    "metadata": {
                        "name": "observed-experiment",
                        "namespace": "chaos-mesh",
                        "labels": {"purpose": "test"},
                    },
                    "status": {
                        "experiment": {"phase": "Running"},
                        "conditions": [],
                    },
                }],
            }),
        },
    )

    result = chaos.chaos_list_experiments()

    assert result["success"] is True
    assert result["observation_status"] == "observed"
    assert result["active_experiments"][0]["kind"] == "StressChaos"
    assert result["active_experiments"][0]["name"] == "observed-experiment"
    assert result["active_experiments"][0]["namespace"] == "chaos-mesh"
    assert "labels" not in result["active_experiments"][0]
    assert "scenario_id" not in result
    assert "expected_root_cause" not in result


def test_malformed_chaos_inventory_cannot_be_observed_empty(monkeypatch):
    import agents.coordinator as coordinator
    import agents.tools.chaos as chaos

    monkeypatch.setattr(
        chaos,
        "_run",
        lambda command, timeout: {"success": True, "stdout": "{}"},
    )
    result = chaos.chaos_list_experiments()

    assert result["success"] is False
    assert result["observation_status"] == "invalid_response"
    observation = coordinator._chaos_observation_from_observations(
        [{"tool": "chaos_list_experiments", "output": result}]
    )
    assert observation["observation_status"] == "unavailable"
    assert observation["active_experiments"] == []


def test_diagnosis_model_context_contains_alert_and_triage_observations_without_truth(
    monkeypatch,
):
    import agents.coordinator as coordinator

    monkeypatch.setattr(coordinator, "require_audit_log", MagicMock())
    response = _response({
        "role": "assistant",
        "content": json.dumps({
            "root_cause": {"category": "unknown", "specific": "insufficient evidence"},
            "confidence": 0.1,
            "evidence": [],
        }),
    })
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(coordinator, "post_with_retry", post)

    asyncio.run(coordinator.call_agent(
        "diagnosis",
        {
            "incident_id": "inc-context",
            "alert": {
                "commonLabels": {"service": "service-a"},
                "scenario_id": "single_fault/sf-002",
                "expected_root_cause": "truth",
            },
            "incident_anchors": {"primary_service": "service-a", "namespace": "default"},
            "triage": {"affected_services": ["service-a"]},
            "triage_observations": [{
                "tool": "kubectl_get",
                "args": {"resource": "pods"},
                "output": {"items": [{"metadata": {"name": "service-a-123"}}]},
            }],
            "target_consistency": {"status": "primary_target_preserved"},
            "environment_observation": {"observation_status": "not_observed"},
            "_runtime_control": {"enforce_action_preconditions": True},
        },
    ))

    prompt_context = post.await_args.args[2]["messages"][1]["content"]
    assert "service-a" in prompt_context
    assert "triage_observations" in prompt_context
    assert "service-a-123" in prompt_context
    assert "scenario_id" not in prompt_context
    assert "expected_root_cause" not in prompt_context
    assert "_runtime_control" not in prompt_context


def test_remediation_model_context_contains_provenance_and_environment_state(
    monkeypatch,
):
    import agents.coordinator as coordinator

    monkeypatch.setattr(coordinator, "require_audit_log", MagicMock())
    response = _response({
        "role": "assistant",
        "content": json.dumps({"outcome": "unresolved"}),
    })
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(coordinator, "post_with_retry", post)

    asyncio.run(coordinator.call_agent(
        "remediation",
        {
            "incident_id": "inc-remediation-context",
            "alert": {
                "commonLabels": {"service": "service-a"},
                "scenario_id": "single_fault/sf-002",
            },
            "incident_anchors": {
                "primary_service": "service-a",
                "namespace": "default",
            },
            "triage": {"affected_services": ["service-a"]},
            "triage_observations": [{
                "tool": "kubectl_get",
                "args": {"resource": "pods"},
                "output": {"items": [{"metadata": {"name": "service-a-1"}}]},
            }],
            "diagnosis": {
                "root_cause": {
                    "category": "resource",
                    "specific": "CPU evidence is present.",
                },
            },
            "diagnosis_observations": [{
                "tool": "chaos_list_experiments",
                "args": {},
                "output": {
                    "observation_status": "observed",
                    "active_experiments": [{
                        "kind": "StressChaos",
                        "name": "observed-experiment",
                        "namespace": "chaos-mesh",
                    }],
                },
            }],
            "environment_observation": {
                "observation_status": "observed",
                "active_experiments": [{
                    "kind": "StressChaos",
                    "name": "observed-experiment",
                    "namespace": "chaos-mesh",
                }],
            },
            "_runtime_control": {"enforce_action_preconditions": True},
        },
    ))

    prompt_context = post.await_args.args[2]["messages"][1]["content"]
    assert "service-a-1" in prompt_context
    assert "observed-experiment" in prompt_context
    assert "CPU evidence is present" in prompt_context
    assert "scenario_id" not in prompt_context
    assert "_runtime_control" not in prompt_context


def test_rollback_and_chaos_stop_require_observed_evidence():
    from agents.coordinator import _check_remediation_action_preconditions

    control = {"_runtime_control": {"enforce_action_preconditions": True}}
    rollback_args = {"app": "service-a", "revision": "2"}
    assert _check_remediation_action_preconditions(
        "argocd_rollback", rollback_args, control
    )

    with_history = {
        **control,
        "diagnosis_observations": [{
            "tool": "argocd_app_history",
            "args": {"app": "service-a"},
            "output": {
                "success": True,
                "history": [{"id": 2, "revision": "sha-2"}],
            },
        }],
    }
    assert _check_remediation_action_preconditions(
        "argocd_rollback", rollback_args, with_history
    ) is None
    assert _check_remediation_action_preconditions(
        "kubectl_rollout",
        {"action": "undo", "resource": "deployment/service-a", "namespace": "default"},
        with_history,
    ) is None
    assert _check_remediation_action_preconditions(
        "argocd_rollback",
        {"app": "service-a", "revision": "9"},
        with_history,
    )

    chaos_args = {
        "kind": "StressChaos",
        "name": "observed-experiment",
        "namespace": "chaos-mesh",
    }
    with_chaos = {
        **control,
        "environment_observation": {
            "observation_status": "observed",
            "active_experiments": [{
                "kind": "StressChaos",
                "name": "observed-experiment",
                "namespace": "chaos-mesh",
            }],
        },
    }
    assert _check_remediation_action_preconditions(
        "chaos_stop_experiment", chaos_args, with_chaos
    ) is None
    assert _check_remediation_action_preconditions(
        "chaos_stop_experiment",
        {**chaos_args, "name": "guessed-experiment"},
        with_chaos,
    )


@pytest.mark.parametrize(
    "terminal_output",
    [
        {
            "error": "argocd_authorization_failed (HTTP 403)",
            "error_class": "authorization_failed",
        },
        {
            "error": "argocd_invalid_revision: revision must be a non-negative integer",
            "error_class": "invalid_revision",
        },
    ],
)
def test_terminal_action_errors_block_revision_variants(monkeypatch, terminal_output):
    import agents.coordinator as coordinator

    monkeypatch.setattr(coordinator, "require_audit_log", MagicMock())
    rollback = MagicMock(return_value={
        "success": False,
        **terminal_output,
    })
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "argocd_rollback", rollback)
    responses = [
        _response({
            "role": "assistant",
            "content": "",
            "tool_calls": [_native_call(
                "argocd_rollback",
                {"app": "service-a", "revision": "2"},
                "rollback-1",
            )],
        }, finish_reason="tool_calls"),
        _response({
            "role": "assistant",
            "content": "",
            "tool_calls": [_native_call(
                "argocd_rollback",
                {"app": "service-a", "revision": "3"},
                "rollback-2",
            )],
        }, finish_reason="tool_calls"),
        _response({
            "role": "assistant",
            "content": json.dumps({"outcome": "escalated"}),
        }),
    ]
    monkeypatch.setattr(coordinator, "post_with_retry", AsyncMock(side_effect=responses))

    result = asyncio.run(coordinator.call_agent(
        "remediation",
        {
            "incident_id": "inc-terminal",
            "triage": {"severity": "P1"},
            "diagnosis_observations": [{
                "tool": "argocd_app_history",
                "args": {"app": "service-a"},
                "output": {
                    "success": True,
                    "history": [{"id": 2, "revision": "sha-2"}, {"id": 3, "revision": "sha-3"}],
                },
            }],
            "_runtime_control": {"enforce_action_preconditions": True},
        },
    ))

    rollback.assert_called_once()
    assert result["final"]["outcome"] == "escalated"
    assert any(
        step.get("blocked_by_terminal_error")
        for step in result["trajectory"]
    )


def test_one_mutation_is_observed_before_next_and_resolved_stops_loop(monkeypatch):
    import agents.coordinator as coordinator

    monkeypatch.setattr(coordinator, "require_audit_log", MagicMock())
    stop = MagicMock(return_value={
        "success": True,
        "action": "stopped_chaos_experiment",
    })
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "chaos_stop_experiment", stop)
    responses = [
        _response({
            "role": "assistant",
            "content": "",
            "tool_calls": [
                _native_call(
                    "chaos_stop_experiment",
                    {
                        "kind": "StressChaos",
                        "name": "observed-experiment",
                        "namespace": "chaos-mesh",
                    },
                    "mutation-1",
                ),
                _native_call(
                    "kubectl_scale",
                    {"deployment": "service-a", "replicas": 2},
                    "mutation-2",
                ),
            ],
        }, finish_reason="tool_calls"),
        _response({
            "role": "assistant",
            "content": "",
            "tool_calls": [_native_call(
                "chaos_stop_experiment",
                {
                    "kind": "StressChaos",
                    "name": "observed-experiment",
                    "namespace": "chaos-mesh",
                },
                "mutation-3",
            )],
        }, finish_reason="tool_calls"),
        _response({
            "role": "assistant",
            "content": json.dumps({"outcome": "resolved"}),
        }),
    ]
    post = AsyncMock(side_effect=responses)
    monkeypatch.setattr(coordinator, "post_with_retry", post)
    observations = []

    async def observe(action):
        observations.append(action)
        return {
            "env_resolved": len(observations) == 2,
            "verification_status": "passed" if len(observations) == 2 else "failed",
        }

    token = coordinator._ACTIVE_MUTATION_OBSERVER.set(observe)
    try:
        result = asyncio.run(coordinator.call_agent(
            "remediation",
            {
                "incident_id": "inc-loop",
                "environment_observation": {
                    "observation_status": "observed",
                    "active_experiments": [{
                        "kind": "StressChaos",
                        "name": "observed-experiment",
                        "namespace": "chaos-mesh",
                    }],
                },
                "_runtime_control": {"enforce_action_preconditions": True},
            },
        ))
    finally:
        coordinator._ACTIVE_MUTATION_OBSERVER.reset(token)

    assert stop.call_count == 2
    assert len(observations) == 2
    assert result["final"]["outcome"] == "resolved"
    assert result["final"]["verified_by"] == "environment_verifier"
    assert any(
        step.get("blocked_by_action_observation")
        for step in result["trajectory"]
    )
    second_turn_messages = post.await_args_list[1].args[2]["messages"]
    assert any(
        message.get("role") == "tool"
        and "verifier_observation" in message.get("content", "")
        for message in second_turn_messages
    )
    assert post.await_count == 2


@pytest.mark.asyncio
async def test_handle_incident_preserves_context_and_blocks_unsubstantiated_target(
    monkeypatch,
    tmp_path,
):
    import agents.coordinator as coordinator
    import agents.verifier as verifier

    monkeypatch.setattr(coordinator, "require_audit_log", MagicMock())
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path)
    monkeypatch.setattr(coordinator, "_live_judge_requested", lambda: False)
    monkeypatch.setattr(coordinator, "settle_environment", AsyncMock(return_value={
        "settled": False,
        "observations": [],
    }))
    monkeypatch.setattr(
        verifier,
        "verify_environment",
        MagicMock(return_value=verifier.EnvironmentVerificationResult(
            scenario_id="",
            agent_claimed_resolved=False,
            env_resolved=False,
            verification_status="failed",
        )),
    )
    monkeypatch.setattr(
        "recommender.hybrid.HybridRecommender.load_checkpoint",
        MagicMock(side_effect=RuntimeError("isolated test")),
    )
    calls = []

    async def fake_call_agent(role, payload, max_turns=10):
        calls.append((role, payload))
        if role == "triage":
            return {
                "role": "triage",
                "trajectory": [{
                    "tool": "kubectl_get",
                    "args": {"resource": "pods"},
                    "output": {"items": [{"metadata": {"name": "service-c-1"}}]},
                }],
                "final": {
                    "severity": "P2",
                    "affected_services": ["service-b"],
                    "blast_radius": {"services": ["service-b"]},
                },
            }
        if role == "diagnosis":
            assert payload["incident_anchors"]["primary_service"] == "service-a"
            assert payload["triage_observations"][0]["tool"] == "kubectl_get"
            assert payload["target_consistency"]["requires_review"] is True
            return {
                "role": "diagnosis",
                "trajectory": [],
                "final": {"root_cause": {"category": "unknown"}},
            }
        raise AssertionError(f"unexpected agent call: {role}")

    monkeypatch.setattr(coordinator, "call_agent", fake_call_agent)
    result = await coordinator.handle_incident(
        {
            "commonLabels": {
                "alertname": "ServiceAHighLatency",
                "service": "service-a",
                "namespace": "default",
            },
            "alerts": [{
                "labels": {"service": "service-a", "namespace": "default"},
                "annotations": {"description": "Service A is slow"},
            }],
            "scenario_id": "single_fault/sf-002",
            "expected_root_cause": "must not reach model",
        },
        scenario_id="single_fault/sf-002",
    )

    assert [role for role, _ in calls] == ["triage", "diagnosis"]
    assert result["remediation"]["final"]["status"] == "target_mismatch"
    assert result["remediation"]["final"]["executed_actions"] == []
    assert result["resolved"] is False
    assert "settling" in result
    assert "scenario_id" not in json.dumps(calls[0][1])
    assert "expected_root_cause" not in json.dumps(calls[0][1])
