from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from training.grpo_async_lifecycle import ManagedAsyncObservationLifecycle
from training.grpo_environment import parse_policy_action
from training.grpo_observation_first import ObservationFirstGRPOMixin


SCENARIO_ID = "single_fault/sf-002"
COMPLETION = (
    '{"tool":"kubectl_get","arguments":{},"agent_claimed_resolved":false}'
)
STATE = {
    "incident_id": "async-incident",
    "alert": {"labels": {"alertname": "ObservedCpu"}, "status": "firing"},
    "triage": {"severity": "P2"},
    "observations": {"ready_replicas": 1},
}


def _environment_result(completion: str) -> dict:
    action = parse_policy_action(completion)
    return {
        "scenario_id": SCENARIO_ID,
        "status": "ok",
        "scorable": True,
        "settling": {"status": "settled", "stable": True, "stable_observations": 2},
        "policy_completion": completion,
        "policy_action": action,
        "executed_actions": [
            {"tool": action["tool"], "arguments": action["arguments"], "result": {}}
        ],
        "verification": {
            "verification_status": "failed",
            "env_resolved": False,
            "checks": [
                {"name": "workload_ready", "required": True, "passed": True},
                {"name": "chaos_cleared", "required": True, "passed": False},
            ],
        },
        "agent_claimed_resolved": False,
    }


class _AsyncLifecycle:
    def __init__(self):
        self.callback_threads = []
        self.finish_statuses = []
        self.execute_error = None
        self.tick = threading.Event()
        self.stop_tick = False
        self.pending_cancelled = threading.Event()
        self.owner_loop = None
        self.owner_thread = None
        self.pending_task = None

    async def begin(self, scenario_id):
        self._record_thread()
        self.owner_loop = asyncio.get_running_loop()
        self.pending_task = asyncio.create_task(self._pending_worker())
        return {"state": STATE, "observed_at": "2026-10-01T00:00:00Z"}

    async def _pending_worker(self):
        try:
            while not self.stop_tick:
                self.tick.set()
                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            self.pending_cancelled.set()
            raise

    async def before_action(self, snapshot, index):
        self._record_thread()
        return {"state": snapshot, "observed_at": "2026-10-01T00:00:01Z"}

    async def execute(self, completion, state, index):
        self._record_thread()
        if self.execute_error is not None:
            raise self.execute_error
        return _environment_result(completion)

    async def finish(self, group, status):
        self._record_thread()
        self.finish_statuses.append(status)
        self.stop_tick = True
        if self.pending_task is not None:
            await self.pending_task

    def _record_thread(self):
        self.callback_threads.append(threading.current_thread())
        self.owner_thread = threading.current_thread()


class _InterruptedCallerLifecycle(_AsyncLifecycle):
    def __init__(self):
        super().__init__()
        self.interrupt_next_submission = threading.Event()
        self.execute_started = threading.Event()
        self.release_execute = threading.Event()
        self.execute_finished = threading.Event()
        self.finish_called = threading.Event()
        self.finish_saw_execute_finished = None

    async def before_action(self, snapshot, index):
        result = await super().before_action(snapshot, index)
        self.interrupt_next_submission.set()
        return result

    async def execute(self, completion, state, index):
        self._record_thread()
        self.execute_started.set()
        try:
            while not self.release_execute.is_set():
                await asyncio.sleep(0.005)
            return _environment_result(completion)
        finally:
            self.execute_finished.set()

    async def finish(self, group, status):
        self.finish_saw_execute_finished = self.execute_finished.is_set()
        self.finish_called.set()
        await super().finish(group, status)


class _GenerationBoundary:
    def __init__(self, *, reward_funcs, args, generation_gate=None):
        self.model = SimpleNamespace(training=True)
        self.reward_funcs = reward_funcs
        self.num_generations = args.num_generations
        self.max_prompt_length = args.max_prompt_length
        self.generation_gate = generation_gate
        self.generated_inputs = None

    def _generate_and_score_completions(self, inputs):
        self.generated_inputs = inputs
        if self.generation_gate is not None:
            self.generation_gate()
        columns = {
            key: [row[key] for row in inputs]
            for key in inputs[0]
            if key != "prompt"
        }
        return self.reward_funcs[0](
            prompts=[row["prompt"] for row in inputs],
            completions=[COMPLETION] * len(inputs),
            **columns,
        )


class _Trainer(ObservationFirstGRPOMixin, _GenerationBoundary):
    pass


def _inputs():
    return [{"scenario_id": SCENARIO_ID, "prompt": "stale"}] * 2


def _trainer(lifecycle, **kwargs):
    return _Trainer(
        args=SimpleNamespace(max_prompt_length=None, num_generations=2),
        observation_lifecycle=lifecycle,
        **kwargs,
    )


def test_async_lifecycle_runs_through_sync_generation_on_one_responsive_loop():
    lifecycle = _AsyncLifecycle()
    observed_during_generation = threading.Event()

    def block_generation():
        observed_during_generation.set()
        lifecycle.tick.clear()
        assert lifecycle.tick.wait(1)

    with ManagedAsyncObservationLifecycle(lifecycle) as bridge:
        trainer = _trainer(bridge, generation_gate=block_generation)
        rewards = trainer._generate_and_score_completions(_inputs())

    assert rewards == [0.125, 0.125]
    assert observed_during_generation.is_set()
    assert {id(thread) for thread in lifecycle.callback_threads} == {
        id(lifecycle.owner_thread)
    }
    assert lifecycle.owner_thread is not threading.current_thread()
    assert lifecycle.finish_statuses == ["completed"]
    assert lifecycle.pending_cancelled.is_set() is False
    assert trainer.observation_evidence[0]["result_classification"] == "NON_EMPIRICAL"


def test_callback_exception_is_preserved_and_trainer_finishes_as_failed():
    lifecycle = _AsyncLifecycle()
    expected = RuntimeError("synthetic async execution failure")
    lifecycle.execute_error = expected

    with ManagedAsyncObservationLifecycle(lifecycle) as bridge:
        trainer = _trainer(bridge)
        with pytest.raises(RuntimeError) as caught:
            trainer._generate_and_score_completions(_inputs())

    assert caught.value is expected
    assert lifecycle.finish_statuses == ["failed"]
    assert trainer.observation_evidence[0]["error_type"] == "RuntimeError"


def test_async_cancellation_reaches_candidate_and_explicit_finish_runs():
    lifecycle = _AsyncLifecycle()

    async def cancel_execute(*_args, **_kwargs):
        lifecycle._record_thread()
        raise asyncio.CancelledError()

    lifecycle.execute = cancel_execute
    with ManagedAsyncObservationLifecycle(lifecycle) as bridge:
        trainer = _trainer(bridge)
        with pytest.raises(asyncio.CancelledError):
            trainer._generate_and_score_completions(_inputs())

    assert lifecycle.finish_statuses == ["interrupted"]
    assert trainer.observation_evidence[0]["status"] == "interrupted"


def test_context_state_missing_callbacks_and_non_awaitable_callback_fail_closed():
    lifecycle = _AsyncLifecycle()
    bridge = ManagedAsyncObservationLifecycle(lifecycle)
    with pytest.raises(RuntimeError, match="entered"):
        bridge.begin(SCENARIO_ID)
    with pytest.raises(RuntimeError, match="already entered"):
        with bridge:
            bridge.__enter__()

    bridge.close()
    with pytest.raises(RuntimeError, match="closed"):
        bridge.begin(SCENARIO_ID)
    incomplete = _AsyncLifecycle()
    incomplete.finish = None
    with pytest.raises(TypeError, match="requires callable finish"):
        ManagedAsyncObservationLifecycle(incomplete)

    lifecycle = _AsyncLifecycle()
    lifecycle.begin = lambda *_args: {"state": STATE}
    with ManagedAsyncObservationLifecycle(lifecycle) as bridge:
        with pytest.raises(TypeError, match="must return an awaitable"):
            bridge.begin(SCENARIO_ID)


def test_callback_cannot_reenter_or_close_its_owned_loop_synchronously():
    lifecycle = _AsyncLifecycle()
    bridge_ref = {}

    async def reentrant_begin(scenario_id):
        lifecycle._record_thread()
        with pytest.raises(RuntimeError, match="owned loop thread"):
            bridge_ref["bridge"].begin(scenario_id)
        with pytest.raises(RuntimeError, match="owned loop thread"):
            bridge_ref["bridge"].close()
        return {"state": STATE, "observed_at": "2026-10-01T00:00:00Z"}

    lifecycle.begin = reentrant_begin
    bridge = ManagedAsyncObservationLifecycle(lifecycle)
    bridge_ref["bridge"] = bridge
    with bridge:
        assert bridge.begin(SCENARIO_ID)["state"] == STATE


def test_caller_interruption_waits_for_callback_before_trainer_finish(monkeypatch):
    lifecycle = _InterruptedCallerLifecycle()
    original_submit = asyncio.run_coroutine_threadsafe

    class _InterruptOnce:
        def __init__(self, future):
            self.future = future
            self.interrupted = False

        def result(self, *args, **kwargs):
            if not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt("synthetic caller interruption")
            return self.future.result(*args, **kwargs)

        def done(self):
            return self.future.done()

        def cancelled(self):
            return self.future.cancelled()

    def interrupt_execute_once(coroutine, loop):
        future = original_submit(coroutine, loop)
        if lifecycle.interrupt_next_submission.is_set():
            lifecycle.interrupt_next_submission.clear()
            return _InterruptOnce(future)
        return future

    monkeypatch.setattr(
        asyncio, "run_coroutine_threadsafe", interrupt_execute_once
    )
    errors = []

    with ManagedAsyncObservationLifecycle(lifecycle) as bridge:
        trainer = _trainer(bridge)

        def generate_with_trainer():
            try:
                trainer._generate_and_score_completions(_inputs())
            except BaseException as exc:
                errors.append(exc)

        caller = threading.Thread(target=generate_with_trainer)
        caller.start()
        try:
            assert lifecycle.execute_started.wait(1)
            assert not lifecycle.finish_called.wait(0.05)
        finally:
            lifecycle.release_execute.set()
            caller.join(2)

        assert not caller.is_alive()

    assert len(errors) == 1
    assert isinstance(errors[0], KeyboardInterrupt)
    assert lifecycle.execute_finished.is_set()
    assert lifecycle.finish_saw_execute_finished is True
    assert lifecycle.finish_statuses == ["interrupted"]
    assert trainer.observation_evidence[0]["status"] == "interrupted"


def test_overlapping_calls_fail_closed_and_close_drains_pending_tasks():
    lifecycle = _AsyncLifecycle()
    entered = threading.Event()
    callback_error = []

    async def blocked_begin(scenario_id):
        lifecycle._record_thread()
        entered.set()
        await asyncio.Event().wait()

    lifecycle.begin = blocked_begin
    bridge = ManagedAsyncObservationLifecycle(lifecycle)
    bridge.__enter__()

    def call_begin():
        try:
            bridge.begin(SCENARIO_ID)
        except BaseException as exc:
            callback_error.append(exc)

    caller = threading.Thread(target=call_begin)
    caller.start()
    try:
        assert entered.wait(1)
        with pytest.raises(RuntimeError, match="(?i)overlapping"):
            bridge.before_action(STATE, 0)
    finally:
        bridge.close()
    caller.join(1)
    assert not caller.is_alive()
    assert len(callback_error) == 1
    assert isinstance(callback_error[0], asyncio.CancelledError)
    assert lifecycle.owner_thread is not None
    assert lifecycle.owner_thread.is_alive() is False
    assert lifecycle.finish_statuses == []


def test_new_context_uses_new_callbacks_after_a_closed_context():
    first = _AsyncLifecycle()
    first.stop_tick = True
    with ManagedAsyncObservationLifecycle(first) as bridge:
        bridge.begin(SCENARIO_ID)
    assert first.owner_thread is not None
    assert first.owner_thread.is_alive() is False

    second = _AsyncLifecycle()
    second.stop_tick = True
    with ManagedAsyncObservationLifecycle(second) as bridge:
        bridge.begin(SCENARIO_ID)

    assert second.owner_thread is not first.owner_thread
    assert first.callback_threads != second.callback_threads
    assert first.owner_loop is not second.owner_loop
