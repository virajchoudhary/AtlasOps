"""NON_EMPIRICAL tests for the process-local integrated completion provider."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import agents.coordinator as coord


def _response(message: dict, finish_reason: str = "stop") -> dict:
    return {"choices": [{"message": message, "finish_reason": finish_reason}]}


def _content(value: dict) -> dict:
    return _response({"role": "assistant", "content": json.dumps(value)})


def _tool(name: str, arguments: dict, call_id: str) -> dict:
    return _response(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        },
        "tool_calls",
    )


@pytest.fixture
def safe_runtime(monkeypatch, tmp_path):
    from agents.tools import comms

    monkeypatch.setattr(coord, "circuit_breaker", type(coord.circuit_breaker)())
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-provider-audit-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("ATLASOPS_LIVE_JUDGE", "0")
    monkeypatch.setenv("ATLASOPS_USE_HF_INFERENCE", "0")
    monkeypatch.setenv("ATLASOPS_REMEDIATION_BACKEND", "agent")
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(coord, "TRAJECTORIES_DIR", tmp_path / "trajectories")
    monkeypatch.setattr(comms, "_LOG_PATH", tmp_path / "slack.jsonl")
    monkeypatch.setattr(comms, "SLACK_WEBHOOK", "")
    monkeypatch.setattr(comms, "DISCORD_WEBHOOK", "")
    return tmp_path / "trajectories"


def _inconclusive_verification():
    class Result:
        env_resolved = False
        verification_status = "inconclusive"

        @staticmethod
        def to_dict():
            return {"env_resolved": False, "verification_status": "inconclusive"}

    return Result()


async def _no_settle(**_kwargs):
    return {"settled": False, "timed_out": False, "observations": []}


@pytest.mark.asyncio
async def test_handle_incident_uses_provider_for_all_four_roles_and_real_tool_loops(
    monkeypatch, safe_runtime
):
    monkeypatch.setattr(coord, "settle_environment", _no_settle)
    monkeypatch.setattr(
        "agents.verifier.verify_environment",
        lambda **_kwargs: _inconclusive_verification(),
    )
    tool_calls = []

    def tool(name):
        def execute(**arguments):
            tool_calls.append((name, arguments))
            return {"success": True, "stub": name}
        return execute

    for name in ("kubectl_get", "promql_query", "slack_post_update", "postmortem_draft"):
        monkeypatch.setitem(coord.TOOL_REGISTRY, name, tool(name))

    counts = {role: 0 for role in ("triage", "diagnosis", "remediation", "comms")}
    exchanges = []

    async def provider(role, request):
        counts[role] += 1
        exchanges.append((role, request))
        user = json.loads(request["messages"][1]["content"])
        serialized = json.dumps(user)
        assert "validation-secret" not in serialized
        assert "expected_root_cause" not in serialized
        assert "_runtime_control" not in serialized
        if role == "triage":
            return (
                _tool("kubectl_get", {"resource": "pods", "namespace": "default"}, "t1")
                if counts[role] == 1
                else _content({
                    "severity": "P3",
                    "title": "Provider contract",
                    "affected_services": ["frontend"],
                    "blast_radius": {"services": ["frontend"]},
                })
            )
        if role == "diagnosis":
            return (
                _tool("promql_query", {"query": "up"}, "d1")
                if counts[role] == 1
                else _content({"root_cause": "unknown", "confidence": 0.2})
            )
        if role == "remediation":
            return (
                _tool("kubectl_get", {"resource": "pods", "namespace": "default"}, "r1")
                if counts[role] == 1
                else _content({"outcome": "unresolved", "proposed_actions": []})
            )
        if counts[role] == 1:
            return _tool(
                "slack_post_update",
                {
                    "channel": "#incident-response",
                    "severity": "P3",
                    "title": "Provider contract",
                    "summary": "Fixture only.",
                    "action_items": ["Review"],
                },
                "c1",
            )
        if counts[role] == 2:
            return _tool("postmortem_draft", {"incident": {"incident_id": "provider-fixture"}}, "c2")
        return _content({"slack_posted": True, "summary": "Fixture only."})

    result = await coord.handle_incident(
        {
            "commonLabels": {"alertname": "ProviderFixture", "service": "frontend"},
            "alerts": [{"labels": {"alertname": "ProviderFixture", "service": "frontend"}}],
            "scenario_id": "validation-secret",
            "expected_root_cause": "hidden fixture truth",
        },
        incident_id="inc-provider-four-roles",
        scenario_id="validation-secret",
        completion_provider=provider,
    )

    assert [role for role, _request in exchanges] == [
        "triage", "triage", "diagnosis", "diagnosis",
        "remediation", "remediation", "remediation", "comms", "comms", "comms",
    ]
    assert counts == {"triage": 2, "diagnosis": 2, "remediation": 3, "comms": 3}
    assert {name for name, _args in tool_calls} == {
        "kubectl_get", "promql_query", "slack_post_update", "postmortem_draft",
    }
    for role in ("triage", "diagnosis", "remediation", "comms"):
        role_exchanges = [
            item for item in result[role]["trajectory"]
            if item.get("kind") == "inference_exchange"
        ]
        assert role_exchanges
        assert all(item["status"] == "completed" for item in role_exchanges)
        assert all(item["result_classification"] == "NON_EMPIRICAL" for item in role_exchanges)
        assert all(item["certification_status"] == "NOT_CERTIFIED" for item in role_exchanges)
        assert all("request" in item and "response" in item for item in role_exchanges)
    assert result["resolved"] is False
    assert result["execution_mode"] == "NON_EMPIRICAL"
    assert result["evidence_class"] == "NON_EMPIRICAL"
    assert result["certification_status"] == "NOT_CERTIFIED"
    assert result["provider_injected"] is True


@pytest.mark.asyncio
async def test_default_transport_is_unchanged_and_provider_copies_are_isolated(
    monkeypatch, safe_runtime
):
    response = MagicMock()
    response.json.return_value = _content({"severity": "P3", "title": "default"})
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(coord, "post_with_retry", post)

    await coord.call_agent("triage", {"incident_id": "inc-default", "alert": {}}, max_turns=1)

    post.assert_awaited_once()
    args = post.await_args
    assert args.args[1] == f"{coord.VLLM_BASE}/chat/completions"
    request = args.args[2]
    assert request["model"] == coord.MODEL_NAME
    assert request["temperature"] == 0.2
    assert request["tool_choice"] == "auto"
    assert request["tools"]
    assert args.kwargs == {
        "context": "triage/turn-0",
        "max_attempts": coord.LLM_MAX_ATTEMPTS,
        "base_backoff": coord.LLM_BASE_BACKOFF_SECONDS,
    }
    response.raise_for_status.assert_called_once()

    post.reset_mock()
    seen = []

    def make_provider(title):
        async def provider(role, request_payload):
            raw = _content({"severity": "P3", "title": title})
            seen.append((role, request_payload, raw))
            request_payload["messages"][0]["content"] = "provider mutation"
            return raw
        return provider

    first, second = await asyncio.gather(
        coord.call_agent(
            "triage", {"incident_id": "inc-a", "alert": {}},
            completion_provider=make_provider("first"),
        ),
        coord.call_agent(
            "triage", {"incident_id": "inc-b", "alert": {}},
            completion_provider=make_provider("second"),
        ),
    )
    assert first["final"]["title"] == "first"
    assert second["final"]["title"] == "second"
    assert post.await_count == 0
    for result in (first, second):
        exchange = next(item for item in result["trajectory"] if item.get("kind") == "inference_exchange")
        assert exchange["request"]["messages"][0]["content"] != "provider mutation"
        assert "tool_calls" not in exchange["response"]["choices"][0]["message"]
    assert all(raw["choices"][0]["message"].get("tool_calls") is None for _, _, raw in seen)


@pytest.mark.asyncio
async def test_explicit_provider_handles_in_loop_and_max_turn_forced_conclusions(
    monkeypatch, safe_runtime
):
    post = AsyncMock(side_effect=AssertionError("explicit provider must not fall back to HTTP"))
    monkeypatch.setattr(coord, "post_with_retry", post)
    monkeypatch.setitem(
        coord.TOOL_REGISTRY,
        "kubectl_get",
        lambda **_kwargs: {"success": True, "items": []},
    )
    responses = iter([
        _response({"role": "assistant", "content": "not JSON"}),
        _content({"severity": "P3", "title": "forced"}),
    ])
    requests = []

    async def forced_provider(_role, request):
        requests.append(request)
        return next(responses)

    forced = await coord.call_agent(
        "triage", {"incident_id": "inc-forced", "alert": {}},
        max_turns=1, completion_provider=forced_provider,
    )
    assert forced["final"]["title"] == "forced"
    assert len([item for item in forced["trajectory"] if item.get("kind") == "inference_exchange"]) == 2
    assert "tools" not in requests[1]

    responses = iter([
        _tool("kubectl_get", {"resource": "pods", "namespace": "default"}, "max-turn"),
        _content({"outcome": "unresolved"}),
    ])
    requests.clear()

    async def max_turn_provider(_role, request):
        requests.append(request)
        return next(responses)

    exhausted = await coord.call_agent(
        "remediation", {"incident_id": "inc-max-forced"},
        max_turns=1, completion_provider=max_turn_provider,
    )
    exchanges = [item for item in exhausted["trajectory"] if item.get("kind") == "inference_exchange"]
    assert len(exchanges) == 2
    assert exchanges[-1]["turn"] == 1
    assert "tools" not in exchanges[-1]["request"]
    post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "expected_category"),
    [
        ("malformed", "malformed_response"),
        ("unencodable", "malformed_response"),
        ("exception", "RuntimeError"),
    ],
)
async def test_provider_failures_keep_partial_episode_and_never_fall_back(
    monkeypatch, safe_runtime, failure_kind, expected_category
):
    post = AsyncMock(side_effect=AssertionError("provider failure must not fall back"))
    monkeypatch.setattr(coord, "post_with_retry", post)
    monkeypatch.setattr(coord, "settle_environment", _no_settle)
    monkeypatch.setattr(
        "agents.verifier.verify_environment",
        lambda **_kwargs: _inconclusive_verification(),
    )
    monkeypatch.setitem(
        coord.TOOL_REGISTRY,
        "kubectl_get",
        lambda **_kwargs: {"success": True, "items": []},
    )
    calls = []
    diagnosis_calls = 0

    async def provider(role, _request):
        nonlocal diagnosis_calls
        calls.append(role)
        if role == "triage":
            return _content({
                "severity": "P3",
                "title": "Partial fixture",
                "affected_services": [],
            })
        diagnosis_calls += 1
        if diagnosis_calls == 1:
            return _tool(
                "kubectl_get",
                {"resource": "pods", "namespace": "default"},
                "diagnosis-observation",
            )
        if failure_kind == "exception":
            raise RuntimeError("secret token must not be persisted")
        if failure_kind == "unencodable":
            return {"choices": [], "unencodable": object()}
        return {"choices": []}

    with pytest.raises(coord.CompletionProviderError) as error:
        await coord.handle_incident(
            {"commonLabels": {"alertname": "PartialFixture"}},
            incident_id="inc-provider-partial",
            scenario_id="validation-partial",
            completion_provider=provider,
        )

    assert error.value.category == expected_category
    assert "secret token" not in str(error.value)
    assert calls == ["triage", "diagnosis", "diagnosis"]
    post.assert_not_awaited()
    partial_path = next(coord.TRAJECTORIES_DIR.glob("inc-provider-partial.partial-*.json"))
    partial = json.loads(partial_path.read_text(encoding="utf-8"))
    assert partial["alert"]["commonLabels"]["alertname"] == "PartialFixture"
    assert partial["scenario_id"] == "validation-partial"
    assert partial["triage"]["final"]["severity"] == "P3"
    assert partial["diagnosis"] is None
    assert partial["remediation"] is None
    assert partial["verification"] is None
    assert partial["settling"] is None
    assert partial["comms"] is None
    assert partial["completed_roles"] == ["triage"]
    assert partial["resolved"] is False
    assert partial["failure"]["phase"] == "diagnosis"
    assert partial["failure"]["category"] == expected_category
    failed_trace = partial["failed_role_trajectory"]
    assert any(item.get("tool") == "kubectl_get" for item in failed_trace)
    failed_exchanges = [
        item for item in failed_trace if item.get("kind") == "inference_exchange"
    ]
    assert len(failed_exchanges) == 2
    assert failed_exchanges[-1]["error_category"] == expected_category
    if failure_kind == "malformed":
        assert failed_exchanges[-1]["response"] == {"choices": []}
    elif failure_kind == "unencodable":
        assert failed_exchanges[-1]["response"] is None
        assert failed_exchanges[-1]["response_omitted_unencodable"] is True
    else:
        assert failed_exchanges[-1]["response"] is None
    assert "outcome" not in partial
    assert "secret token" not in json.dumps(partial)
    failure_audit = [
        item for item in coord.audit_log.tail(100)
        if item["action_type"] == "inference_provider_failure"
    ]
    assert failure_audit[-1]["result_summary"].endswith(expected_category)
    assert "secret token" not in json.dumps(failure_audit)


@pytest.mark.asyncio
async def test_explicit_provider_refuses_to_overwrite_existing_episode(
    safe_runtime,
):
    safe_runtime.mkdir(parents=True, exist_ok=True)
    existing = safe_runtime / "inc-existing-provider.json"
    existing.write_text("preserve existing evidence", encoding="utf-8")
    called = []

    async def provider(role, _request):
        called.append(role)
        return _content({"severity": "P3"})

    with pytest.raises(FileExistsError):
        await coord.handle_incident(
            {},
            incident_id="inc-existing-provider",
            completion_provider=provider,
        )
    assert called == []
    assert existing.read_text(encoding="utf-8") == "preserve existing evidence"


@pytest.mark.asyncio
async def test_injected_policy_uses_matched_settings_and_p1_denial_stays_blocked(
    monkeypatch, safe_runtime
):
    monkeypatch.setenv("ATLASOPS_RL_POLICY_EXECUTE_ACTIONS", "1")
    monkeypatch.setenv("KUBECONFIG_CONTEXT", "kind-atlasops-synthetic")
    monkeypatch.setattr(coord, "settle_environment", _no_settle)
    monkeypatch.setattr(
        "agents.verifier.verify_environment",
        lambda **_kwargs: _inconclusive_verification(),
    )
    policy = object()
    match_config = {"max_new_tokens": 512, "temperature": 0.0, "top_p": 1.0}
    policy_calls = []
    action = {
        "tool": "kubectl_scale",
        "arguments": {
            "deployment": "paymentservice",
            "namespace": "default",
            "replicas": 2,
        },
        "agent_claimed_resolved": False,
    }

    async def fake_policy_run(
        *, policy, state, scenario_id, environment, seed, generation_config,
        policy_origin, approval_provider,
    ):
        policy_calls.append((policy, seed, deepcopy(generation_config), policy_origin))
        assert scenario_id == "single_fault/provider-policy"
        assert environment is not None
        assert "_runtime_control" not in state
        assert await approval_provider(action, state) is None
        generation_config["temperature"] = 9.0
        return {
            "role": "remediation",
            "trajectory": [],
            "final": {"status": "blocked", "outcome": "blocked", "executed_actions": []},
        }

    monkeypatch.setattr("agents.policy_remediation.run_policy_remediation", fake_policy_run)
    request = SimpleNamespace(
        token="test-approval-token",
        action_digest="test-action-digest",
        action_summary="scale paymentservice",
    )
    monkeypatch.setattr(coord.approval_gate, "request_action", lambda **_kwargs: request)

    async def reject(_incident_id, *, request_token):
        assert request_token == request.token
        return {"status": "rejected", "approved_by": "fixture-operator"}, None

    monkeypatch.setattr(coord.approval_gate, "wait_for_action_decision", reject)
    monkeypatch.setitem(
        coord.TOOL_REGISTRY,
        "slack_post_update",
        lambda **_kwargs: {"success": True},
    )
    mutation = MagicMock(return_value={"success": True})
    monkeypatch.setitem(coord.TOOL_REGISTRY, "kubectl_scale", mutation)

    async def provider(role, _request):
        if role == "triage":
            return _content({
                "severity": "P1",
                "title": "Policy fixture",
                "affected_services": ["paymentservice"],
                "blast_radius": {"services": ["paymentservice"]},
            })
        assert role == "diagnosis"
        return _content({"root_cause": "unknown", "confidence": 0.1})

    result = await coord.handle_incident(
        {
            "commonLabels": {"alertname": "PolicyFixture", "service": "paymentservice"},
            "alerts": [{"labels": {"alertname": "PolicyFixture", "service": "paymentservice"}}],
        },
        incident_id="inc-provider-policy",
        scenario_id="single_fault/provider-policy",
        remediation_policy=policy,
        completion_provider=provider,
        policy_seed=1337,
        policy_generation_config=match_config,
    )
    assert policy_calls == [(policy, 1337, match_config, "injected_non_empirical")]
    assert match_config == {"max_new_tokens": 512, "temperature": 0.0, "top_p": 1.0}
    assert result["approval"]["decision"] == "rejected"
    assert result["comms"]["final"]["status"] == "approval_rejected"
    assert "approval outcome: rejected" in result["comms"]["final"]["summary"]
    assert result["resolved"] is False
    mutation.assert_not_called()


@pytest.mark.parametrize("seed", [True, -1])
def test_policy_seed_override_requires_injected_policy_and_valid_integer(
    safe_runtime, seed
):
    with pytest.raises(ValueError):
        asyncio.run(coord.handle_incident({}, policy_seed=seed))
    with pytest.raises(ValueError):
        asyncio.run(coord.handle_incident({}, remediation_policy=object(), policy_seed=seed))


@pytest.mark.asyncio
async def test_injected_policy_cannot_remove_trusted_rollback_preconditions(
    monkeypatch, safe_runtime
):
    monkeypatch.setenv("ATLASOPS_RL_POLICY_EXECUTE_ACTIONS", "1")
    monkeypatch.setenv("KUBECONFIG_CONTEXT", "kind-atlasops-synthetic")
    monkeypatch.setattr(coord, "settle_environment", _no_settle)
    monkeypatch.setattr(
        "agents.verifier.verify_environment",
        lambda **_kwargs: _inconclusive_verification(),
    )

    async def policy_run(*, environment, state, **_kwargs):
        assert "_runtime_control" not in state
        error = environment.policy_check(
            "remediation", "kubectl_rollout",
            {"resource": "deployment/paymentservice", "namespace": "default", "action": "undo"},
            state,
        )
        assert error and "positive, non-empty deployment history" in error
        return {"final": {"status": "blocked", "executed_actions": []}, "trajectory": []}

    monkeypatch.setattr("agents.policy_remediation.run_policy_remediation", policy_run)

    async def provider(role, _request):
        if role == "triage":
            return _content({"severity": "P3", "affected_services": ["paymentservice"]})
        return _content({"root_cause": "unknown", "confidence": 0.1})

    await coord.handle_incident(
        {"commonLabels": {"alertname": "Fixture", "service": "paymentservice"}},
        incident_id="inc-no-history", completion_provider=provider,
        remediation_policy=object(), scenario_id="single_fault/fixture",
    )
