"""Regression: the argument-varying retry loop that consumed Gate G4 run 008.

Run ``EXP-STAGE4-SF002-008`` spent every remediation turn calling
``argocd_rollback`` with a fresh guess at ``revision`` — ``latest``,
``previous``, ``1``, ``0``, ``-1``, ``0``, ``-2``, ``-3``, ``-4`` — against an
Argo CD instance that owned zero Applications. Each call returned the identical
transport error. Neither existing guard stopped it: exact-argument dedup never
matched because every call differed, and the per-tool cap only fired on call 9.

These tests pin the runtime guard that ends such loops, and prove the discovery
path that makes the correct action reachable at all.

Mocks only — no network, no Kubernetes, no Ollama.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import uuid
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _audit_environment(monkeypatch):
    audit_root = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scratch"
        / "g4-retry-loop-audit"
        / uuid.uuid4().hex
    )
    audit_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "retry-loop-synthetic-secret-0123456789")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(audit_root / "audit_log.jsonl"))


@pytest.fixture(autouse=True)
def _isolated_circuit_breaker():
    """The breaker is a module singleton; its hourly quota leaks between tests."""
    from agents.circuit_breaker import circuit_breaker

    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


def _response(content="", tool_calls=None, finish_reason="stop"):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    response.json.return_value = {"choices": [{"message": message, "finish_reason": finish_reason}]}
    return response


def _call(name, args, call_id="call_x"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


CONCLUSION = json.dumps(
    {"outcome": "unresolved", "proposed_actions": [], "executed_actions": [], "verified_by": "none"}
)


def _completed(value):
    """Wrap a value in an already-finished awaitable."""
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    future.set_result(value)
    return future

# Verbatim revision sequence from EXP-STAGE4-SF002-008.
RUN_008_REVISIONS = ["latest", "previous", "1", "0", "-1", "0", "-2", "-3", "-4"]

ARGOCD_TRANSPORT_ERROR = {
    "success": False,
    "error": "argocd_request_error: request failed",
    "error_class": "request_failed",
}


class TestRepeatedFailureLoopIsHalted:
    def test_run_008_revision_loop_is_cut_short(self):
        from agents.coordinator import _REPEATED_FAILURE_LIMIT, call_agent

        responses = [
            _response(tool_calls=[_call("argocd_rollback", {"app": "paymentservice", "revision": rev})])
            for rev in RUN_008_REVISIONS
        ] + [_response(CONCLUSION)]

        rollback = MagicMock(return_value=dict(ARGOCD_TRANSPORT_ERROR))
        with patch("agents.coordinator.post_with_retry", side_effect=responses):
            with patch("agents.coordinator.require_audit_log"):
                with patch.dict(
                    "agents.coordinator.TOOL_REGISTRY", {"argocd_rollback": rollback}
                ):
                    result = asyncio.run(
                        call_agent(
                            "remediation",
                            {"incident_id": "inc-008-replay", "triage": {"severity": "P1"}},
                            max_turns=len(RUN_008_REVISIONS) + 1,
                        )
                    )

        # The wrapper is invoked only until the guard trips — not nine times.
        assert rollback.call_count == _REPEATED_FAILURE_LIMIT

        blocked = [e for e in result["trajectory"] if e.get("repeated_failure_blocked")]
        assert blocked, "expected the repeated-failure guard to record blocked calls"
        assert blocked[0]["output"]["tool_unavailable"] is True
        assert "will not help" in blocked[0]["output"]["error"]

    def test_guard_tells_the_model_to_change_branch(self):
        """The blocking message must be actionable, not a bare failure."""
        from agents.coordinator import call_agent

        responses = [
            _response(tool_calls=[_call("argocd_rollback", {"app": "svc", "revision": rev})])
            for rev in RUN_008_REVISIONS
        ] + [_response(CONCLUSION)]

        with patch("agents.coordinator.post_with_retry", side_effect=responses):
            with patch("agents.coordinator.require_audit_log"):
                with patch.dict(
                    "agents.coordinator.TOOL_REGISTRY",
                    {"argocd_rollback": MagicMock(return_value=dict(ARGOCD_TRANSPORT_ERROR))},
                ):
                    result = asyncio.run(
                        call_agent(
                            "remediation",
                            {"incident_id": "inc-008-msg", "triage": {"severity": "P1"}},
                            max_turns=len(RUN_008_REVISIONS) + 1,
                        )
                    )

        message = [e for e in result["trajectory"] if e.get("repeated_failure_blocked")][0]
        error = message["output"]["error"]
        assert "argocd_rollback" in error
        assert "different remediation branch or escalate" in error

    def test_distinct_errors_do_not_trip_the_guard(self):
        """Only a *repeating* failure signature halts a tool; real progress must not."""
        from agents.coordinator import call_agent

        outputs = [
            {"success": False, "error": "a", "error_class": "not_found"},
            {"success": False, "error": "b", "error_class": "conflict"},
            {"success": True, "action": "stopped_chaos_experiment"},
        ]
        responses = [
            _response(tool_calls=[_call("chaos_stop_experiment", {"kind": "StressChaos", "name": f"n{i}"})])
            for i in range(3)
        ] + [_response(CONCLUSION)]

        tool = MagicMock(side_effect=outputs)
        with patch("agents.coordinator.post_with_retry", side_effect=responses):
            with patch("agents.coordinator.require_audit_log"):
                with patch.dict("agents.coordinator.TOOL_REGISTRY", {"chaos_stop_experiment": tool}):
                    result = asyncio.run(
                        call_agent(
                            "remediation",
                            {"incident_id": "inc-distinct", "triage": {"severity": "P1"}},
                            max_turns=4,
                        )
                    )

        assert tool.call_count == 3
        assert not [e for e in result["trajectory"] if e.get("repeated_failure_blocked")]
        assert result["final"]["executed_actions"][-1]["success"] is True

    def test_unrelated_kubectl_failures_do_not_retire_the_tool(self):
        """The guard must not cost the agent its main investigative tool.

        kubectl failures carry no `error` key, so a literal fallback collapsed
        every cause to one signature: a missing pod followed by a transient
        apiserver timeout would disable `kubectl_get` for the whole incident,
        even though the third call would have succeeded.
        """
        from agents.coordinator import call_agent

        outputs = [
            {"stdout": "", "returncode": 1, "success": False,
             "stderr": 'Error from server (NotFound): pods "x" not found'},
            {"stdout": "", "returncode": 1, "success": False,
             "stderr": "Unable to connect to the server: dial tcp i/o timeout"},
            {"success": True, "parsed": {"items": []}},
        ]
        responses = [
            _response(tool_calls=[_call("kubectl_get", {"resource": r}, f"c{i}")])
            for i, r in enumerate(("pods", "deployments", "services"))
        ] + [_response(CONCLUSION)]

        tool = MagicMock(side_effect=outputs)
        with patch("agents.coordinator.post_with_retry", side_effect=responses):
            with patch("agents.coordinator.require_audit_log"):
                with patch.dict("agents.coordinator.TOOL_REGISTRY", {"kubectl_get": tool}):
                    result = asyncio.run(
                        call_agent(
                            "remediation",
                            {"incident_id": "inc-unrelated", "triage": {"severity": "P1"}},
                            max_turns=4,
                        )
                    )

        assert tool.call_count == 3, "the third, succeeding call was never made"
        assert not [e for e in result["trajectory"] if e.get("repeated_failure_blocked")]

    def test_successful_calls_never_accumulate_a_signature(self):
        """A tool used many times successfully must never be blocked."""
        from agents.coordinator import call_agent

        responses = [
            _response(tool_calls=[_call("promql_query", {"query": f"up{{i='{i}'}}"})])
            for i in range(4)
        ] + [_response(CONCLUSION)]

        tool = MagicMock(return_value={"success": True, "result": []})
        with patch("agents.coordinator.post_with_retry", side_effect=responses):
            with patch("agents.coordinator.require_audit_log"):
                with patch.dict("agents.coordinator.TOOL_REGISTRY", {"promql_query": tool}):
                    result = asyncio.run(
                        call_agent(
                            "remediation",
                            {"incident_id": "inc-success", "triage": {"severity": "P1"}},
                            max_turns=5,
                        )
                    )

        assert tool.call_count == 4
        assert not [e for e in result["trajectory"] if e.get("repeated_failure_blocked")]


class TestChaosRemediationPathIsReachable:
    def test_discover_then_stop_completes_in_two_turns(self):
        """The goal state Gate G4 requires is now reachable from inside the loop.

        Before the discovery wrapper existed, no runtime path produced the exact
        experiment name that chaos_stop_experiment demands.
        """
        from agents.coordinator import call_agent

        listed = {
            "success": True,
            "count": 1,
            "experiments": [
                {
                    "kind": "StressChaos",
                    "name": "sf-002-paymentservice-cpu",
                    "namespace": "chaos-mesh",
                    "target": {"namespaces": ["default"], "app": "paymentservice", "mode": "all"},
                }
            ],
        }
        stopped = {
            "success": True,
            "action": "stopped_chaos_experiment",
            "kind": "StressChaos",
            "name": "sf-002-paymentservice-cpu",
        }

        responses = [
            _response(tool_calls=[_call("chaos_list_experiments", {})]),
            _response(
                tool_calls=[
                    _call(
                        "chaos_stop_experiment",
                        {
                            "kind": "StressChaos",
                            "name": "sf-002-paymentservice-cpu",
                            "namespace": "chaos-mesh",
                        },
                    )
                ]
            ),
            _response(
                json.dumps(
                    {
                        "outcome": "resolved",
                        "proposed_actions": [],
                        "executed_actions": [],
                        "verified_by": "chaos_stop_experiment",
                    }
                )
            ),
        ]

        with patch("agents.coordinator.post_with_retry", side_effect=responses):
            with patch("agents.coordinator.require_audit_log"):
                with patch.dict(
                    "agents.coordinator.TOOL_REGISTRY",
                    {
                        "chaos_list_experiments": MagicMock(return_value=listed),
                        "chaos_stop_experiment": MagicMock(return_value=stopped),
                    },
                ):
                    result = asyncio.run(
                        call_agent(
                            "remediation",
                            {"incident_id": "inc-chaos-path", "triage": {"severity": "P1"}},
                            max_turns=3,
                        )
                    )

        executed = result["final"]["executed_actions"]
        assert [action["tool"] for action in executed] == [
            "chaos_list_experiments",
            "chaos_stop_experiment",
        ]
        # A mutating tool genuinely succeeded, so the resolved claim survives.
        assert result["final"]["outcome"] == "resolved"

    def test_resolved_claim_is_downgraded_without_a_successful_mutation(self):
        """Discovery alone must not let the agent claim resolution."""
        from agents.coordinator import call_agent

        # Turn 1's bare conclusion earns the single "you executed nothing" retry,
        # so the model gets a third turn before the claim is finally downgraded.
        responses = [
            _response(tool_calls=[_call("chaos_list_experiments", {})]),
            _response(json.dumps({"outcome": "resolved", "verified_by": "nothing"})),
            _response(json.dumps({"outcome": "resolved", "verified_by": "still nothing"})),
        ]
        with patch("agents.coordinator.post_with_retry", side_effect=responses):
            with patch("agents.coordinator.require_audit_log"):
                with patch.dict(
                    "agents.coordinator.TOOL_REGISTRY",
                    {"chaos_list_experiments": MagicMock(return_value={"success": True, "count": 0, "experiments": []})},
                ):
                    result = asyncio.run(
                        call_agent(
                            "remediation",
                            {"incident_id": "inc-no-mutation", "triage": {"severity": "P1"}},
                            max_turns=3,
                        )
                    )

        assert result["final"]["outcome"] == "unresolved"
        # Only the read-only discovery call ran; nothing mutated the cluster.
        executed_tools = [action["tool"] for action in result["final"]["executed_actions"]]
        assert executed_tools == ["chaos_list_experiments"]


class TestBlastRadiusQuotaAccounting:
    """The refund must fire from call_agent, not only from direct unit calls.

    All three earlier refund tests drove CircuitBreaker directly and never
    touched the coordinator, so the production wiring was unverified.
    """

    def _run(self, tool_name, tool_output, turns, severity="P1"):
        from agents.circuit_breaker import circuit_breaker
        from agents.coordinator import call_agent

        args_seq = [{"deployment": f"svc-{i}", "replicas": 2, "namespace": "default"}
                    for i in range(turns)]
        responses = [
            _response(tool_calls=[_call(tool_name, a, f"c{i}")])
            for i, a in enumerate(args_seq)
        ] + [_response(CONCLUSION)]

        before = circuit_breaker.status()["cluster_mutating_actions_this_hour"]
        with patch("agents.coordinator.post_with_retry", side_effect=responses):
            with patch("agents.coordinator.require_audit_log"):
                with patch.dict("agents.coordinator.TOOL_REGISTRY",
                                {tool_name: MagicMock(return_value=tool_output)}):
                    asyncio.run(call_agent(
                        "remediation",
                        {"incident_id": "inc-quota", "triage": {"severity": severity}},
                        max_turns=turns + 1,
                    ))
        after = circuit_breaker.status()["cluster_mutating_actions_this_hour"]
        return after - before

    def test_failed_mutations_consume_no_quota_through_the_coordinator(self):
        spent = self._run(
            "kubectl_scale",
            {"success": False, "error": "connection refused", "error_class": "conn"},
            turns=2,
        )
        assert spent == 0

    def test_successful_mutations_do_consume_quota(self):
        spent = self._run("kubectl_scale", {"success": True, "stdout": "scaled"}, turns=2)
        assert spent == 2

    def test_calls_blocked_by_the_per_tool_cap_consume_no_quota(self):
        """A skipped call changes nothing, so it must not be charged."""
        from agents.circuit_breaker import circuit_breaker
        from agents.coordinator import call_agent

        # 10 distinct-arg calls; the default per-tool cap is 8, so the last two
        # are skipped before execution.
        responses = [
            _response(tool_calls=[_call("kubectl_scale",
                                        {"deployment": f"svc-{i}", "replicas": 2}, f"c{i}")])
            for i in range(10)
        ] + [_response(CONCLUSION)]

        before = circuit_breaker.status()["cluster_mutating_actions_this_hour"]
        tool = MagicMock(return_value={"success": True, "stdout": "scaled"})
        with patch("agents.coordinator.post_with_retry", side_effect=responses):
            with patch("agents.coordinator.require_audit_log"):
                with patch.dict("agents.coordinator.TOOL_REGISTRY", {"kubectl_scale": tool}):
                    asyncio.run(call_agent(
                        "remediation",
                        {"incident_id": "inc-cap", "triage": {"severity": "P1"}},
                        max_turns=11,
                    ))
        spent = circuit_breaker.status()["cluster_mutating_actions_this_hour"] - before
        assert tool.call_count == 8, "cap should stop execution at 8"
        assert spent == tool.call_count, "quota must match executed mutations, not attempts"


class TestIncidentRecordCarriesSettlingReport:
    """Gate G4 criterion 13 reads incident["settling"]["settled"].

    The coordinator computed the bounded-convergence report and passed it to the
    Comms agent, but never placed it in the returned record. The Stage 4 runner
    therefore read {} and evaluated settling_completed as False on every run, so
    criterion 13 was unsatisfiable no matter how completely the cluster
    recovered — a second unreachable gate condition alongside the unobservable
    goal state.
    """

    def test_returned_record_actually_contains_settling(self, monkeypatch):
        """Behavioural: run handle_incident and inspect the dict it returns.

        A source-substring check was defeated by this module's own explanatory
        comment, which quotes `incident["settling"]["settled"]` — so the
        assertion held even with the key removed. Drive the function instead.
        """
        import agents.coordinator as coordinator

        stub = {"role": "x", "trajectory": [], "final": {"severity": "P0"}}
        monkeypatch.setattr(coordinator, "call_agent", MagicMock(
            side_effect=lambda *a, **k: _completed(dict(stub))))
        monkeypatch.setattr(coordinator, "build_grounding_reports", lambda *_a, **_k: {})
        monkeypatch.setenv("ATLASOPS_LIVE_JUDGE", "0")

        async def fake_settle(**_kwargs):
            return {"settled": True, "observations": [], "duration_seconds": 0.1}

        monkeypatch.setattr(coordinator, "settle_environment", fake_settle)

        result = asyncio.run(
            coordinator.handle_incident({"commonLabels": {"alertname": "T"}}, scenario_id="")
        )
        assert "settling" in result, "criterion 13 reads this key; it must be returned"
        assert result["settling"]["settled"] is True

    def test_the_key_name_matches_on_both_sides(self):
        """Writer and reader must agree, checked on parsed keys not raw text."""
        import inspect
        import re

        import agents.coordinator as coordinator
        import scripts.run_stage4_golden_incident as runner

        record = inspect.getsource(coordinator.handle_incident)
        record = record.split("full_record = {")[1].split("}")[0]
        keys = re.findall(r'"(\w+)":', record)
        assert "settling" in keys, f"full_record keys were {keys}"

        assert 'get("settling", {})' in inspect.getsource(runner.main)

    def test_settle_environment_reports_whether_it_settled(self):
        """The consumed field must exist in what settle_environment returns."""
        import inspect

        import agents.coordinator as coordinator

        source = inspect.getsource(coordinator.settle_environment)
        assert '"settled": settled' in source
