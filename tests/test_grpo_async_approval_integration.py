"""Synthetic localhost approval, sync-generation and async-environment checks."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from agents.approval import ApprovalGate
from agents.approval_http import loopback_approval_server
from training.grpo_async_lifecycle import ManagedAsyncObservationLifecycle
from training.grpo_environment import DirectPolicyEnvironment, parse_policy_action
from training.grpo_observation_first import ObservationFirstGRPOMixin


KEY = "synthetic-bridge-key"
HEADERS = {"X-AtlasOps-Key": KEY}
SCENARIO = "single_fault/sf-002"
COMPLETION = json.dumps({
    "tool": "kubectl_scale",
    "arguments": {"deployment": "paymentservice", "namespace": "default", "replicas": 2},
    "agent_claimed_resolved": False,
})


class _AsyncIncident:
    def __init__(self, timeout=2):
        self.timeout = timeout
        self.base = None
        self.listener = None
        self.gate = None
        self.waiting = None
        self.decisions = []
        self.executions = []
        self.results = []
        self.tokens = []
        self.thread_ids = []
        self.finished = []
        self.state = {
            "incident_id": "synthetic-bridge-incident",
            "alert": {"labels": {"alertname": "HighCpuUsage"}},
            "triage": {"severity": "P1"},
            "observations": {},
        }

    async def begin(self, scenario):
        self.thread_ids.append(threading.get_ident())
        self.gate = ApprovalGate(timeout_seconds=self.timeout)
        self.listener = loopback_approval_server(self.gate, KEY)
        self.base = await self.listener.__aenter__()
        return {"state": self.state, "observed_at": "2026-10-01T00:00:00Z"}

    async def before_action(self, snapshot, index):
        self.thread_ids.append(threading.get_ident())
        return {"state": snapshot, "observed_at": "2026-10-01T00:00:01Z"}

    async def execute(self, completion, state, index):
        self.thread_ids.append(threading.get_ident())
        action = parse_policy_action(completion)
        request = self.gate.request_action(
            incident_id=state["incident_id"], severity="P1", action=action
        )
        self.tokens.append(request.token)
        self.waiting = asyncio.create_task(self.gate.wait_for_action_decision(
            state["incident_id"], request_token=request.token
        ))
        decision, permit = await self.waiting
        self.decisions.append(decision["status"])

        def tool(**arguments):
            self.executions.append(arguments)
            return {"success": True}

        def verifier(**_):
            return {
                "verification_status": "failed", "env_resolved": False,
                "checks": [{"name": "synthetic_failure", "required": True, "passed": False}],
            }

        environment = DirectPolicyEnvironment(
            tool_registry={"kubectl_scale": tool},
            policy_check=lambda *_: None,
            verifier=verifier,
            settle=lambda: {
                "status": "settled", "stable": True, "stable_observations": 2
            },
            execute_live_chaos=True, kube_context="kind-atlasops-test",
            _action_approval_gate=self.gate,
        )
        result = await environment.step(
            completion, scenario_id=SCENARIO, state=state,
            _action_approval_permit=permit,
        )
        result["scenario_id"] = SCENARIO
        result["approval"] = {"decision": decision["status"]}
        self.results.append(result)
        if permit is not None:
            assert not self.gate.consume_action_permit(
                permit, incident_id=state["incident_id"],
                action_digest=self.gate.action_digest(action),
            )
        return result

    async def finish(self, group, status):
        self.thread_ids.append(threading.get_ident())
        self.finished.append(status)
        if self.listener is not None:
            await self.listener.__aexit__(None, None, None)


class _GenerationBoundary:
    def __init__(self, *, reward_funcs, args, generated, release):
        self.model = SimpleNamespace(training=True)
        self.reward_funcs = reward_funcs
        self.num_generations = args.num_generations
        self.max_prompt_length = args.max_prompt_length
        self.generated = generated
        self.release = release

    def _generate_and_score_completions(self, inputs):
        self.generated.set()
        assert self.release.wait(10), "operator did not inspect listener during generation"
        columns = {
            key: [row[key] for row in inputs]
            for key in inputs[0] if key != "prompt"
        }
        return self.reward_funcs[0](
            prompts=[row["prompt"] for row in inputs],
            completions=[COMPLETION] * len(inputs), **columns,
        )


class _Trainer(ObservationFirstGRPOMixin, _GenerationBoundary):
    pass


def _operator(incident, generated, release, decision, errors, posted):
    try:
        assert generated.wait(10)
        with httpx.Client(trust_env=False, timeout=2) as client:
            response = client.get(f"{incident.base}/approval/pending", headers=HEADERS)
            assert response.status_code == 200
            assert client.get(f"{incident.base}/approval/pending").status_code == 401
            release.set()
            if decision == "timeout":
                return
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and len(posted) < (2 if decision == "approved" else 1):
                pending = client.get(
                    f"{incident.base}/approval/pending", headers=HEADERS
                ).json()["pending"]
                if pending and pending[0]["token"] not in posted:
                    token = pending[0]["token"]
                    payload = {"token": token, "decision": decision, "approved_by": "test"}
                    assert client.post(f"{incident.base}/approve", json=payload).status_code == 401
                    assert client.post(
                        f"{incident.base}/approve", headers=HEADERS, json=payload
                    ).status_code == 200
                    posted.append(token)
                time.sleep(0.01)
            assert len(posted) == (2 if decision == "approved" else 1)
    except BaseException as exc:
        errors.append(exc)
        release.set()


@pytest.mark.parametrize("decision", ["approved", "rejected", "timeout"])
def test_real_loopback_decision_survives_sync_generation_and_gates_exact_action(decision):
    incident = _AsyncIncident(timeout=0.08 if decision == "timeout" else 3)
    generated, release = threading.Event(), threading.Event()
    errors, posted = [], []
    operator = threading.Thread(
        target=_operator,
        args=(incident, generated, release, decision, errors, posted),
    )
    operator.start()
    try:
        with ManagedAsyncObservationLifecycle(incident) as lifecycle:
            trainer = _Trainer(
                args=SimpleNamespace(max_prompt_length=None, num_generations=2),
                observation_lifecycle=lifecycle, generated=generated, release=release,
            )
            inputs = [{"scenario_id": SCENARIO, "prompt": "stale"}] * 2
            if decision == "approved":
                assert len(trainer._generate_and_score_completions(inputs)) == 2
                assert incident.decisions == ["approved", "approved"]
                assert len(incident.executions) == 2
                assert all(result["policy_completion"] == COMPLETION for result in incident.results)
                assert incident.finished == ["completed"]
            else:
                with pytest.raises(ValueError, match="(?i)environment.*blocked"):
                    trainer._generate_and_score_completions(inputs)
                assert incident.decisions == [decision]
                assert incident.executions == []
                assert incident.results[0]["status"] == "blocked"
                assert incident.results[0]["terminal_block"]["category"] == "approval_required"
                assert incident.finished == ["failed"]
                record = trainer.observation_evidence[0]["records"][0]
                assert record["failure"] == "environment_blocked:approval_required"
                assert record["approval_decision"] == decision
                assert record["reward"] is None and record["scorable"] is False
            assert len(set(incident.thread_ids)) == 1
            assert incident.thread_ids[0] != threading.get_ident()
            assert trainer.observation_evidence[0]["result_classification"] == "NON_EMPIRICAL"
    finally:
        release.set()
        operator.join(15)
    assert not operator.is_alive()
    assert not errors
    with httpx.Client(trust_env=False, timeout=0.5) as client:
        with pytest.raises(httpx.TransportError):
            client.get(f"{incident.base}/approval/pending", headers=HEADERS)


def test_fresh_bridge_listener_rejects_prior_lifecycle_token():
    first = _AsyncIncident(timeout=0.01)
    with ManagedAsyncObservationLifecycle(first) as bridge:
        bridge.begin(SCENARIO)
        result = bridge.execute(COMPLETION, first.state, 0)
        assert result["status"] == "blocked"
        assert result["terminal_block"]["category"] == "approval_required"
        old_token = first.tokens[0]
        bridge.finish({}, "failed")

    replacement = _AsyncIncident()
    with ManagedAsyncObservationLifecycle(replacement) as bridge:
        bridge.begin(SCENARIO)
        with httpx.Client(trust_env=False, timeout=2) as client:
            response = client.post(
                f"{replacement.base}/approve", headers=HEADERS,
                json={"token": old_token, "decision": "approved", "approved_by": "test"},
            )
            assert response.status_code == 400
        assert replacement.executions == []
        bridge.finish({}, "failed")


def test_cancelled_gate_waiter_clears_request_and_candidate_finalizes():
    incident = _AsyncIncident()
    cleared = []

    async def cancelled_execute(completion, state, index):
        request = incident.gate.request_action(
            incident_id=state["incident_id"], severity="P1",
            action=parse_policy_action(completion),
        )
        waiter = asyncio.create_task(incident.gate.wait_for_action_decision(
            state["incident_id"], request_token=request.token,
        ))
        await asyncio.sleep(0)
        waiter.cancel()
        try:
            await waiter
        finally:
            cleared.append(incident.gate.pending() == [])
            cleared.append(not incident.gate.callback(
                request.token, "approved", "test"
            )["ok"])

    incident.execute = cancelled_execute
    generated, release = threading.Event(), threading.Event()
    release.set()
    with ManagedAsyncObservationLifecycle(incident) as bridge:
        trainer = _Trainer(
            args=SimpleNamespace(max_prompt_length=None, num_generations=2),
            observation_lifecycle=bridge, generated=generated, release=release,
        )
        with pytest.raises(asyncio.CancelledError):
            trainer._generate_and_score_completions(
                [{"scenario_id": SCENARIO, "prompt": "stale"}] * 2
            )
        assert incident.finished == ["interrupted"]
        assert cleared == [True, True]
        assert incident.executions == []
        assert trainer.observation_evidence[0]["status"] == "interrupted"
