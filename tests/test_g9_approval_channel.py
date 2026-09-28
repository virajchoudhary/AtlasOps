"""Non-live operator channel checks for standalone direct-action policy runs."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agents.approval import ApprovalGate
from agents.approval_http import loopback_approval_server
from bench import grpo_eval
from config.scenario_catalog import SCENARIO_CATALOG
from config.splits import get_split
from training import grpo


ACTION = {
    "tool": "kubectl_scale",
    "arguments": {"deployment": "paymentservice", "namespace": "default", "replicas": 2},
}
KEY = "synthetic-g9-operator-key"
HEADERS = {"X-AtlasOps-Key": KEY}
PROCESS_HELPER = Path(__file__).with_name("g9_approval_process.py")


def _wait_for_process_file(path: Path, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            raise AssertionError(f"synthetic G9 process exited: {process.returncode}")
        time.sleep(0.02)
    raise AssertionError(f"synthetic G9 process did not write {path.name}")


def _start_approval_process(output: Path) -> tuple[subprocess.Popen, str]:
    output.mkdir()
    environment = os.environ.copy()
    environment["ATLASOPS_API_KEY"] = KEY
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    process = subprocess.Popen(
        [sys.executable, str(PROCESS_HELPER), str(output)],
        cwd=output,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = _wait_for_process_file(output / "ready.json", process)["base_url"]
    except BaseException:
        process.terminate()
        process.wait(timeout=10)
        raise
    return process, base


def _wait_for_process_pending(base: str, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 15
    with httpx.Client(trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"synthetic G9 process exited: {process.returncode}")
            try:
                response = client.get(f"{base}/approval/pending", headers=HEADERS)
                response.raise_for_status()
                if response.json()["pending"]:
                    return response.json()["pending"][0]
            except httpx.TransportError:
                pass
            time.sleep(0.02)
    raise AssertionError("synthetic G9 process never requested action approval")


def test_authenticated_operator_decision_issues_one_exact_action_permit():
    async def exercise():
        gate = ApprovalGate(timeout_seconds=2)
        async with loopback_approval_server(gate, KEY) as base:
            assert base.startswith("http://127.0.0.1:")
            request = gate.request_action(
                incident_id="synthetic-g9-incident",
                severity="P1",
                action=ACTION,
                operator_scope={
                    "kube_context": "kind-atlasops-test",
                    "scenario_id": "single_fault/sf-002",
                },
            )
            waiting = asyncio.create_task(gate.wait_for_action_decision(
                request.incident_id, request_token=request.token
            ))
            async with httpx.AsyncClient(trust_env=False) as client:
                assert (await client.get(f"{base}/approval/pending")).status_code == 401
                assert (await client.post(
                    f"{base}/approve",
                    json={"token": request.token, "decision": "approved", "approved_by": "operator"},
                )).status_code == 401
                pending = (await client.get(
                    f"{base}/approval/pending", headers=HEADERS
                )).json()["pending"]
                assert len(pending) == 1
                assert pending[0]["action"] == ACTION
                assert pending[0]["action_digest"] == gate.action_digest(ACTION)
                assert pending[0]["operator_scope"] == {
                    "kube_context": "kind-atlasops-test",
                    "scenario_id": "single_fault/sf-002",
                }
                response = await client.post(
                    f"{base}/approve",
                    json={"token": request.token, "decision": "approved", "approved_by": "operator"},
                    headers=HEADERS,
                )
                assert response.status_code == 200
            decision, permit = await waiting
            assert decision["status"] == "approved"
            assert permit is not None
            assert gate.consume_action_permit(
                permit, incident_id=request.incident_id,
                action_digest=gate.action_digest(ACTION),
            )
            assert not gate.consume_action_permit(
                permit, incident_id=request.incident_id,
                action_digest=gate.action_digest(ACTION),
            )
    asyncio.run(exercise())


def test_rejection_timeout_and_restart_never_release_old_action():
    async def exercise():
        gate = ApprovalGate(timeout_seconds=0.05)
        async with loopback_approval_server(gate, KEY) as base:
            rejected = gate.request_action(incident_id="reject", severity="P1", action=ACTION)
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.post(
                    f"{base}/approve",
                    json={"token": rejected.token, "decision": "rejected", "approved_by": "operator"},
                    headers=HEADERS,
                )
                assert response.status_code == 200
                decision, permit = await gate.wait_for_action_decision(
                    "reject", request_token=rejected.token
                )
                assert decision["status"] == "rejected" and permit is None
                expired = gate.request_action(incident_id="timeout", severity="P1", action=ACTION)
                decision, permit = await gate.wait_for_action_decision(
                    "timeout", request_token=expired.token
                )
                assert decision["status"] == "timeout" and permit is None
                assert (await client.post(
                    f"{base}/approve",
                    json={"token": expired.token, "decision": "approved", "approved_by": "operator"},
                    headers=HEADERS,
                )).status_code == 400
        replacement = ApprovalGate(timeout_seconds=1)
        async with loopback_approval_server(replacement, KEY) as new_base:
            async with httpx.AsyncClient(trust_env=False) as client:
                assert (await client.post(
                    f"{new_base}/approve",
                    json={"token": rejected.token, "decision": "approved", "approved_by": "operator"},
                    headers=HEADERS,
                )).status_code == 400
    asyncio.run(exercise())


def test_incomplete_operator_scope_cannot_create_pending_request():
    gate = ApprovalGate(timeout_seconds=1)
    with pytest.raises(ValueError, match="Operator scope"):
        gate.request_action(
            incident_id="synthetic-g9-incident",
            severity="P1",
            action=ACTION,
            operator_scope={"kube_context": "", "scenario_id": "single_fault/sf-002"},
        )
    assert gate.pending() == []


def test_operator_decision_crosses_process_and_restart_invalidates_token(tmp_path):
    first, base = _start_approval_process(tmp_path / "first")
    second = None
    third = None
    try:
        with httpx.Client(trust_env=False) as client:
            assert client.get(f"{base}/approval/pending").status_code == 401
            pending = _wait_for_process_pending(base, first)
            old_token = pending["token"]
            assert client.post(
                f"{base}/approve",
                json={"token": old_token, "decision": "approved", "approved_by": "operator"},
                headers={"X-AtlasOps-Key": "wrong-key"},
            ).status_code == 401
            assert client.post(
                f"{base}/approve",
                json={"token": old_token, "decision": "approved", "approved_by": "operator"},
                headers=HEADERS,
            ).status_code == 200
            assert _wait_for_process_file(tmp_path / "first" / "result.json", first) == {
                "decision": "approved", "allowed": True,
            }
            first.wait(timeout=10)

            second, next_base = _start_approval_process(tmp_path / "second")
            pending_token = _wait_for_process_pending(next_base, second)["token"]
            second.terminate()
            second.wait(timeout=10)
            assert not (tmp_path / "second" / "result.json").exists()

            third, fresh_base = _start_approval_process(tmp_path / "third")
            assert client.post(
                f"{fresh_base}/approve",
                json={"token": pending_token, "decision": "approved", "approved_by": "operator"},
                headers=HEADERS,
            ).status_code == 400
            next_token = _wait_for_process_pending(fresh_base, third)["token"]
            assert next_token not in {old_token, pending_token}
            assert client.post(
                f"{fresh_base}/approve",
                json={"token": next_token, "decision": "rejected", "approved_by": "operator"},
                headers=HEADERS,
            ).status_code == 200
            assert _wait_for_process_file(tmp_path / "third" / "result.json", third) == {
                "decision": "rejected", "allowed": False,
            }
            third.wait(timeout=10)
    finally:
        for process in (first, second, third):
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)


def test_training_listener_lifecycle_brackets_cluster_preflight(monkeypatch):
    events = []
    actual_listener = grpo.loopback_approval_server
    monkeypatch.setenv("ATLASOPS_API_KEY", KEY)

    @asynccontextmanager
    async def observed_listener(gate, key):
        async with actual_listener(gate, key) as base:
            events.append(("started", base))
            try:
                yield base
            finally:
                events.append(("stopped", base))

    def no_chaos_preflight(**_kwargs):
        events.append(("preflight", None))
        return False

    monkeypatch.setattr(grpo, "loopback_approval_server", observed_listener)
    monkeypatch.setattr(grpo, "zero_chaos_verified", no_chaos_preflight)
    scenario_id = get_split("train")[0]
    tier = SCENARIO_CATALOG[scenario_id].tier
    reward_function = grpo.OnlineRewardFunction(
        [tier],
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
        operator_approval_enabled=True,
    )
    try:
        with pytest.raises(RuntimeError, match="zero-Chaos preflight"):
            asyncio.run(reward_function._score_batch(
                ['{"tool":"kubectl_get","arguments":{}}'],
                [grpo._direct_action_prompt(scenario_id)],
                [scenario_id],
            ))
    finally:
        reward_function._loop.close()
    assert [event for event, _ in events] == ["started", "preflight", "stopped"]


def test_missing_operator_key_stops_before_cluster_preflight(monkeypatch):
    monkeypatch.delenv("ATLASOPS_API_KEY", raising=False)
    monkeypatch.setattr(
        grpo,
        "zero_chaos_verified",
        lambda **_kwargs: pytest.fail("cluster preflight was reached"),
    )
    scenario_id = get_split("train")[0]
    reward_function = grpo.OnlineRewardFunction(
        [SCENARIO_CATALOG[scenario_id].tier],
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
        operator_approval_enabled=True,
    )
    try:
        with pytest.raises(RuntimeError, match="operator API key is required"):
            asyncio.run(reward_function._score_batch(
                ['{"tool":"kubectl_get","arguments":{}}'],
                [grpo._direct_action_prompt(scenario_id)],
                [scenario_id],
            ))
    finally:
        reward_function._loop.close()
    assert "operator_api_key" not in vars(reward_function)


def test_empirical_evaluator_uses_trusted_p1_and_exact_action_permit(monkeypatch):
    gate = ApprovalGate(timeout_seconds=2)
    observed = {}
    events = []
    action = dict(ACTION, agent_claimed_resolved=False)
    policy = object.__new__(grpo_eval.LocalGRPOPolicy)
    policy.execute_actions = True
    policy.kube_context = "kind-atlasops-test"
    policy.provenance = SimpleNamespace(to_record=lambda: {"synthetic": True})

    def generate(state, **_kwargs):
        observed["severity"] = state["triage"]["severity"]
        return json.dumps(action)

    policy.generate = generate

    class FakeEnvironment:
        _action_approval_gate = gate
        verifier = staticmethod(lambda **_kwargs: None)

        async def invoke_tool(self, *_args, **_kwargs):
            return SimpleNamespace(to_dict=lambda: {
                "verification_status": "failed", "env_resolved": False,
            })

        async def step(
            self, completion, *, scenario_id, state, _action_approval_permit=None
        ):
            observed["permitted"] = gate.consume_action_permit(
                _action_approval_permit,
                incident_id=state["incident_id"],
                action_digest=gate.action_digest(json.loads(completion)),
            )
            raise RuntimeError("synthetic environment stop")

    monkeypatch.setattr(grpo_eval, "_authoritative_environment", lambda *_args: True)

    async def exercise():
        task = asyncio.create_task(grpo_eval.evaluate_grpo_episode(
            "single_fault/sf-002",
            {"alert": {"status": "firing"}, "triage": {"severity": "P2"}},
            policy,
            FakeEnvironment(),
            seed=1,
            max_steps=1,
            event_sink=events.append,
            execute_actions=True,
            kube_context="kind-atlasops-test",
            approval_gate=gate,
        ))
        for _ in range(100):
            if gate.pending():
                break
            await asyncio.sleep(0.01)
        pending = gate.pending()
        assert len(pending) == 1
        assert pending[0]["action"]["tool"] == "kubectl_scale"
        assert pending[0]["operator_scope"] == {
            "kube_context": "kind-atlasops-test",
            "scenario_id": "single_fault/sf-002",
        }
        assert gate.callback(pending[0]["token"], "approved", approved_by="operator")["ok"]
        return await task, pending[0]["token"]

    episode, token = asyncio.run(exercise())
    assert observed == {"severity": "P1", "permitted": True}
    assert episode["status"] != "ok"
    assert all(token not in json.dumps(event) for event in events)


@pytest.mark.parametrize("entrypoint", ["training", "evaluation"])
def test_cli_missing_operator_key_fails_before_output_or_cluster(
    monkeypatch, tmp_path, entrypoint,
):
    monkeypatch.delenv("ATLASOPS_API_KEY", raising=False)
    output = tmp_path / "new-output"
    if entrypoint == "training":
        argv = [
            "grpo", "--model", "synthetic", "--model-revision", "synthetic-revision",
            "--tokenizer-revision", "synthetic-revision",
            "--sft-checkpoint", str(tmp_path / "missing-adapter"),
            "--output", str(output),
            "--execute-live-chaos", "--kube-context", "kind-atlasops-test",
            "--enable-p1-approval",
        ]
        entry = grpo.main
    else:
        argv = [
            "grpo_eval", "--checkpoint", str(tmp_path / "missing-adapter"),
            "--state-dir", str(tmp_path / "missing-state"),
            "--output-dir", str(output),
            "--execute-actions", "--kube-context", "kind-atlasops-test",
            "--enable-p1-approval",
        ]
        entry = grpo_eval.main
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exit_info:
        entry()
    assert exit_info.value.code == 2
    assert not output.exists()


def test_distributed_reward_callback_rejects_before_cluster(monkeypatch):
    monkeypatch.setenv("WORLD_SIZE", "2")
    monkeypatch.setenv("ATLASOPS_API_KEY", KEY)
    monkeypatch.setattr(
        grpo,
        "zero_chaos_verified",
        lambda **_kwargs: pytest.fail("cluster preflight was reached"),
    )
    scenario_id = get_split("train")[0]
    reward_function = grpo.OnlineRewardFunction(
        [SCENARIO_CATALOG[scenario_id].tier],
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
        operator_approval_enabled=True,
    )
    try:
        with pytest.raises(RuntimeError, match="single training process"):
            asyncio.run(reward_function._score_batch(
                ['{"tool":"kubectl_get","arguments":{}}'],
                [grpo._direct_action_prompt(scenario_id)],
                [scenario_id],
            ))
    finally:
        reward_function._loop.close()


def test_failed_rollout_does_not_persist_exception_secret(monkeypatch, tmp_path, caplog):
    secret = "synthetic-secret-must-not-persist"
    monkeypatch.setattr(grpo, "zero_chaos_verified", lambda **_kwargs: True)
    monkeypatch.setattr(
        grpo, "apply_chaos",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    monkeypatch.setattr(grpo, "reset_chaos", lambda *_args, **_kwargs: True)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(grpo.asyncio, "sleep", no_sleep)
    scenario_id = get_split("train")[0]
    ledger = tmp_path / "rollouts.jsonl"
    reward_function = grpo.OnlineRewardFunction(
        [SCENARIO_CATALOG[scenario_id].tier],
        rollout_log_path=ledger,
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
    )
    try:
        with pytest.raises(RuntimeError, match="unscorable"):
            asyncio.run(reward_function._score_batch(
                ['{"tool":"kubectl_get","arguments":{}}'],
                [grpo._direct_action_prompt(scenario_id)],
                [scenario_id],
            ))
    finally:
        reward_function._loop.close()
    record = json.loads(ledger.read_text(encoding="utf-8"))
    assert record["failure"] == "rollout_exception:RuntimeError"
    assert record["reward"] is None
    assert secret not in ledger.read_text(encoding="utf-8")
    assert secret not in caplog.text


def test_empirical_policy_failure_records_category_not_exception_text(monkeypatch):
    secret = "synthetic-provider-secret-must-not-persist"
    policy = object.__new__(grpo_eval.LocalGRPOPolicy)
    policy.execute_actions = True
    policy.kube_context = "kind-atlasops-test"
    policy.provenance = SimpleNamespace(to_record=lambda: {"synthetic": True})

    def fail_generation(*_args, **_kwargs):
        raise RuntimeError(secret)

    policy.generate = fail_generation

    class FakeEnvironment:
        verifier = staticmethod(lambda **_kwargs: None)

        async def invoke_tool(self, *_args, **_kwargs):
            return SimpleNamespace(to_dict=lambda: {
                "verification_status": "failed", "env_resolved": False,
            })

        async def step(self, *_args, **_kwargs):
            pytest.fail("policy failed before environment step")

    monkeypatch.setattr(grpo_eval, "_authoritative_environment", lambda *_args: True)
    events = []
    episode = asyncio.run(grpo_eval.evaluate_grpo_episode(
        "single_fault/sf-002",
        {"alert": {"status": "firing"}, "triage": {"severity": "P1"}},
        policy, FakeEnvironment(), seed=1, max_steps=1, event_sink=events.append,
        execute_actions=True, kube_context="kind-atlasops-test",
    ))
    assert episode["failure"] == "policy_generation_error: RuntimeError"
    assert secret not in json.dumps(events)
    assert secret not in json.dumps(episode)


def test_mock_evaluation_cannot_open_operator_approval(tmp_path):
    with pytest.raises(ValueError, match="Mock mode.*operator"):
        asyncio.run(grpo_eval.evaluate_grpo_split(
            "val", mock=True, output_dir=tmp_path,
            approval_gate=ApprovalGate(timeout_seconds=1),
        ))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("decision", ["rejected", "timeout"])
def test_standalone_training_denial_never_supplies_a_permit(monkeypatch, decision):
    gate = ApprovalGate(timeout_seconds=0.05)
    observed = []

    class FakeEnvironment:
        def __init__(self, *, _action_approval_gate, **_kwargs):
            assert _action_approval_gate is gate

        async def step(self, _completion, *, _action_approval_permit=None, **_kwargs):
            observed.append(_action_approval_permit)
            return {"status": "blocked", "env_resolved": False}

    monkeypatch.setattr(grpo, "DirectPolicyEnvironment", FakeEnvironment)
    reward_function = grpo.OnlineRewardFunction(
        ["single_fault"], execute_live_chaos=True, kube_context="kind-atlasops-test"
    )

    async def exercise():
        task = asyncio.create_task(reward_function._run_one_rollout(
            json.dumps(dict(ACTION, agent_claimed_resolved=False)),
            "single_fault/sf-002", "single_fault",
            {"commonLabels": {"severity": "critical"}, "alerts": []},
            approval_gate=gate,
        ))
        if decision == "rejected":
            for _ in range(100):
                if gate.pending():
                    break
                await asyncio.sleep(0.001)
            assert len(gate.pending()) == 1
            assert gate.callback(
                gate.pending()[0]["token"], "rejected", approved_by="operator"
            )["ok"]
        return await task

    try:
        result = asyncio.run(exercise())
    finally:
        reward_function._loop.close()
    assert observed == [None]
    assert result["status"] == "blocked"
    assert result["approval"]["decision"] == decision
    assert gate.pending() == []
