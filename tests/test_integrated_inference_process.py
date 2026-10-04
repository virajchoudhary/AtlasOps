from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bench import integrated_inference as integrated
from bench.integrated_inference_process import local_inference_worker

MANIFEST_PIN = "a" * 64
MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
MODEL_REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
MAX_RPC_MESSAGE_BYTES = 5 * 1024 * 1024


def _send(connection: Any, message: dict[str, Any]) -> None:
    connection.send_bytes(
        json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
    )


def _receive(connection: Any) -> dict[str, Any]:
    return json.loads(connection.recv_bytes(MAX_RPC_MESSAGE_BYTES).decode("utf-8"))


def _send_transport_observation(
    connection: Any, request_id: int, operation: str, mode: str
) -> None:
    if mode not in {"transport_observation", "transport_bad_correlation"}:
        return
    correlated_id = request_id + (mode == "transport_bad_correlation")
    _send(
        connection,
        {
            "request_id": request_id,
            "kind": "transport_observation",
            "transport": {
                "operation": operation,
                "request_id": correlated_id,
                "http_status": 200,
                "content_type": "application/json",
                "elapsed_seconds": 0.0125,
            },
        },
    )


def _spawn_fixture_worker(connection: Any, config: dict[str, Any]) -> None:
    mode = config["mode"]
    identity = {
        "checkpoint_manifest_sha256": MANIFEST_PIN,
        "checkpoint_tree_sha256": "e" * 64,
        "base_snapshot_inventory_sha256": config["base_inventory_sha256"],
    }
    while True:
        try:
            request = _receive(connection)
        except EOFError:
            return
        request_id = request["request_id"]
        operation = request["operation"]
        if operation == "load":
            if mode == "hang_load":
                time.sleep(10)
            _send_transport_observation(connection, request_id, operation, mode)
            _send(
                connection,
                {"request_id": request_id, "kind": "loaded", "identity": identity}
            )
            if mode == "stop_reading":
                time.sleep(10)
        elif operation == "complete":
            _send(
                connection,
                {
                    "request_id": request_id,
                    "kind": "encoded",
                    "prompt_token_ids": [31, 32],
                }
            )
            if mode == "hang_rpc":
                time.sleep(10)
            if mode == "exit_after_encode":
                os._exit(17)
            _send_transport_observation(connection, request_id, operation, mode)
            _send(
                connection,
                {
                    "request_id": request_id,
                    "kind": "completed",
                    "generated_token_ids": [91],
                    "raw_model_response": f"fixture-raw-{os.getpid()}",
                }
            )
        elif operation == "verify_final":
            _send_transport_observation(connection, request_id, operation, mode)
            _send(
                connection,
                {"request_id": request_id, "kind": "verified", "identity": identity}
            )
        elif operation == "close":
            _send_transport_observation(connection, request_id, operation, mode)
            _send(connection, {"request_id": request_id, "kind": "closed"})
            return


def _partial_frame_worker(connection: Any, config: dict[str, Any]) -> None:
    while True:
        request = _receive(connection)
        request_id = request["request_id"]
        if request["operation"] == "load":
            _send(
                connection,
                {
                    "request_id": request_id,
                    "kind": "loaded",
                    "identity": {
                        "checkpoint_manifest_sha256": MANIFEST_PIN,
                        "checkpoint_tree_sha256": "e" * 64,
                        "base_snapshot_inventory_sha256": config[
                            "base_inventory_sha256"
                        ],
                    },
                },
            )
        elif request["operation"] == "complete":
            connection._send(struct.pack("!i", 1024 * 1024) + b'{"request_id":')
            time.sleep(10)


def _request() -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": "Inspect paymentservice."}]}


def _engine(
    tmp_path: Path,
    mode: str,
    *,
    worker_target: Any = _spawn_fixture_worker,
    **timeouts: Any,
):
    checkpoint = tmp_path / "checkpoint"
    base = tmp_path / "base"
    checkpoint.mkdir()
    base.mkdir()
    inventory = {
        "repository": MODEL_ID,
        "revision": MODEL_REVISION,
        "snapshot_dir": str(base.resolve()),
        "model_weights_loaded": False,
        "network_accessed": False,
        "files": {str(base / "model.safetensors"): "b" * 64},
        "weight_metadata": {"sha256": "c" * 64},
        "tokenizer_manifest_sha256": "d" * 64,
        "total_files": 1,
        "total_bytes": 10,
    }
    inventory_sha256 = integrated.base_snapshot_inventory_sha256(inventory)
    return integrated.PairedCompletionEngine(
        checkpoint=checkpoint,
        checkpoint_manifest_sha256=MANIFEST_PIN,
        base_snapshot=base,
        base_snapshot_inventory_sha256=inventory_sha256,
        journal_path=tmp_path / "raw-inference.jsonl",
        process_worker_target=worker_target,
        process_worker_config={
            "mode": mode,
            "base_inventory_sha256": inventory_sha256,
        },
        **timeouts,
    )


def _rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def _wait_for_encoded(path: Path, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if path.exists() and any(row["record"] == "attempt_encoded" for row in _rows(path)):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("spawned worker did not durably report prompt encoding")


def test_spawned_worker_is_persistent_and_returns_only_raw_content(tmp_path):
    engine = _engine(
        tmp_path, "success", rpc_timeout_seconds=2.0, load_timeout_seconds=2.0
    )
    base = engine.provider("base")
    sft = engine.provider("sft")

    async def run():
        first = await base("triage", _request())
        first_pid = engine.worker_pid
        second = await sft("remediation", _request())
        assert engine.worker_pid == first_pid
        assert first["choices"][0]["message"]["content"].startswith("fixture-raw-")
        assert second["choices"][0]["message"]["content"].startswith("fixture-raw-")
        assert "tool_calls" not in first["choices"][0]["message"]
        final = await engine.verify_final()
        assert final["base_snapshot_inventory_sha256"] == (
            engine.base_snapshot_inventory_sha256
        )
        await engine.close()

    asyncio.run(run())
    rows = _rows(engine.journal_path)
    assert [row["record"] for row in rows] == [
        "attempt_started",
        "attempt_encoded",
        "attempt_finished",
        "attempt_started",
        "attempt_encoded",
        "attempt_finished",
        "final_verification",
    ]
    for row in rows:
        if row["record"] == "attempt_finished":
            assert row["prompt_token_ids"] == [31, 32]
            assert row["generated_token_ids"] == [91]
            assert row["raw_model_response_sha256"]
    assert not engine.worker_alive


def test_transport_observations_are_request_bound_and_durable(tmp_path):
    engine = _engine(
        tmp_path,
        "transport_observation",
        rpc_timeout_seconds=2.0,
        load_timeout_seconds=2.0,
    )

    async def run():
        await engine.provider("base")("triage", _request())
        await engine.verify_final()
        await engine.close()

    asyncio.run(run())
    observations = [
        row for row in _rows(engine.journal_path)
        if row["record"] == "transport_observation"
    ]
    assert [
        (row["operation"], row["request_id"], row["http_status"], row["content_type"])
        for row in observations
    ] == [
        ("load", 1, 200, "application/json"),
        ("complete", 2, 200, "application/json"),
        ("verify_final", 3, 200, "application/json"),
        ("close", 4, 200, "application/json"),
    ]
    assert all(row["elapsed_seconds"] == 0.0125 for row in observations)


def test_transport_observation_rejects_correlation_mismatch_without_retry(tmp_path):
    engine = _engine(
        tmp_path,
        "transport_bad_correlation",
        rpc_timeout_seconds=2.0,
        load_timeout_seconds=2.0,
    )

    async def run():
        with pytest.raises(integrated.InferenceProviderError) as failure:
            await engine.provider("base")("triage", _request())
        assert failure.value.failure_category == "worker_protocol_failure"
        assert engine._next_request_id == 1
        assert engine._process is not None and not engine._process.is_alive()
        await engine.close()

    asyncio.run(run())
    rows = _rows(engine.journal_path)
    assert not any(row["record"] == "transport_observation" for row in rows)
    assert rows[-1]["failure_category"] == "worker_protocol_failure"


def test_transport_journal_failure_fails_closed_and_does_not_retry(tmp_path):
    engine = _engine(
        tmp_path,
        "transport_observation",
        rpc_timeout_seconds=2.0,
        load_timeout_seconds=2.0,
    )
    write = engine._write

    def fail_transport_write(row):
        if row.get("record") == "transport_observation":
            raise OSError("private journal failure detail")
        write(row)

    engine._write = fail_transport_write

    async def run():
        with pytest.raises(integrated.InferenceProviderError) as failure:
            await engine.provider("base")("triage", _request())
        assert failure.value.failure_category == "journal_failure"
        assert engine._next_request_id == 1
        assert engine._process is not None and not engine._process.is_alive()
        await engine.close()

    asyncio.run(run())
    rows = _rows(engine.journal_path)
    assert rows[-1]["failure_category"] == "journal_failure"
    assert "private journal failure detail" not in engine.journal_path.read_text(
        encoding="utf-8"
    )


def test_rpc_timeout_is_bounded_kills_worker_and_poison_prevents_fallback(tmp_path):
    engine = _engine(
        tmp_path,
        "hang_rpc",
        rpc_timeout_seconds=0.2,
        load_timeout_seconds=2.0,
        shutdown_timeout_seconds=0.3,
    )
    provider = engine.provider("base")

    async def run():
        started = time.perf_counter()
        with pytest.raises(integrated.InferenceProviderError) as failure:
            await provider("triage", _request())
        elapsed = time.perf_counter() - started
        assert failure.value.failure_category == "rpc_timeout"
        assert elapsed < 2.5
        process = engine._process
        assert process is not None and not process.is_alive()
        with pytest.raises(integrated.InferenceProviderError, match="rpc_timeout"):
            await provider("triage", _request())
        assert engine._process is process
        assert not process.is_alive()
        await engine.close()

    asyncio.run(run())
    rows = _rows(engine.journal_path)
    assert rows[-2]["record"] == "attempt_encoded"
    assert rows[-1]["inference_status"] == "failed"
    assert rows[-1]["failure_category"] == "rpc_timeout"
    assert rows[-1]["prompt_token_ids"] == [31, 32]
    assert rows[-1]["generated_token_ids"] is None
    assert rows[-1]["raw_model_response"] is None


def test_cancellation_terminates_worker_and_records_interruption(tmp_path):
    engine = _engine(
        tmp_path,
        "hang_rpc",
        rpc_timeout_seconds=5.0,
        load_timeout_seconds=2.0,
        shutdown_timeout_seconds=0.3,
    )
    provider = engine.provider("sft")

    async def run():
        task = asyncio.create_task(provider("diagnosis", _request()))
        await _wait_for_encoded(engine.journal_path)
        started = time.perf_counter()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.perf_counter() - started < 2.5
        assert engine._process is not None and not engine._process.is_alive()
        with pytest.raises(integrated.InferenceProviderError, match="cancelled"):
            await provider("diagnosis", _request())
        await engine.close()

    asyncio.run(run())
    rows = _rows(engine.journal_path)
    assert rows[-1]["inference_status"] == "interrupted"
    assert rows[-1]["failure_category"] == "cancelled"
    assert rows[-1]["prompt_token_ids"] == [31, 32]
    assert rows[-1]["generated_token_ids"] is None
    assert rows[-1]["raw_model_response"] is None


def test_load_timeout_uses_its_own_deadline_and_kills_worker(tmp_path):
    engine = _engine(
        tmp_path,
        "hang_load",
        rpc_timeout_seconds=2.0,
        load_timeout_seconds=0.2,
        shutdown_timeout_seconds=0.3,
    )

    async def run():
        started = time.perf_counter()
        with pytest.raises(integrated.InferenceProviderError) as failure:
            await engine.provider("base")("remediation", _request())
        assert failure.value.failure_category == "load_timeout"
        assert time.perf_counter() - started < 2.5
        assert engine._process is not None and not engine._process.is_alive()

    asyncio.run(run())
    row = _rows(engine.journal_path)[-1]
    assert row["failure_category"] == "load_timeout"
    assert row["prompt_token_ids"] is None
    assert row["raw_model_response"] is None


def test_abrupt_worker_exit_preserves_encoded_prompt_but_not_unknown_output(tmp_path):
    engine = _engine(
        tmp_path, "exit_after_encode", rpc_timeout_seconds=2.0, load_timeout_seconds=2.0
    )

    async def run():
        with pytest.raises(integrated.InferenceProviderError) as failure:
            await engine.provider("base")("comms", _request())
        assert failure.value.failure_category == "worker_process_exit"
        assert engine._process is not None and not engine._process.is_alive()
        await engine.close()

    asyncio.run(run())
    rows = _rows(engine.journal_path)
    assert rows[-2]["record"] == "attempt_encoded"
    assert rows[-1]["failure_category"] == "worker_process_exit"
    assert rows[-1]["prompt_token_ids"] == [31, 32]
    assert rows[-1]["generated_token_ids"] is None
    assert rows[-1]["raw_model_response"] is None


def test_large_send_to_nonreading_worker_does_not_block_event_loop(tmp_path):
    engine = _engine(
        tmp_path,
        "stop_reading",
        rpc_timeout_seconds=0.2,
        load_timeout_seconds=2.0,
        shutdown_timeout_seconds=0.3,
    )
    provider = engine.provider("base")

    async def run():
        started = time.perf_counter()
        request = {
            "messages": [{"role": "user", "content": "x" * 3_500_000}],
        }
        call = asyncio.create_task(provider("diagnosis", request))
        ticks = 0
        while not call.done():
            ticks += 1
            await asyncio.sleep(0.01)
        with pytest.raises(integrated.InferenceProviderError) as failure:
            await call
        assert failure.value.failure_category == "rpc_timeout"
        assert time.perf_counter() - started < 2.5
        assert ticks >= 3
        assert engine._process is not None and not engine._process.is_alive()
        assert all(not thread.is_alive() for thread in engine._io_threads)
        await engine.close()

    asyncio.run(run())
    rows = _rows(engine.journal_path)
    assert rows[-1]["failure_category"] == "rpc_timeout"
    assert rows[-1]["prompt_token_ids"] is None
    assert rows[-1]["generated_token_ids"] is None
    assert rows[-1]["raw_model_response"] is None


def test_blocked_recv_bytes_fixture_is_unblocked_by_connection_close(tmp_path):
    reader_blocked = threading.Event()
    release_reader = threading.Event()
    real_context = multiprocessing.get_context("spawn")

    class BlockingRecvConnection:
        def __init__(self, connection):
            self.connection = connection
            self.calls = 0

        def recv_bytes(self, maxlength=None):
            self.calls += 1
            if self.calls == 2:
                reader_blocked.set()
                release_reader.wait()
                raise EOFError
            return self.connection.recv_bytes(maxlength)

        def send_bytes(self, value):
            return self.connection.send_bytes(value)

        def close(self):
            release_reader.set()
            self.connection.close()

    class ContextWithBlockingRecv:
        def Pipe(self, duplex=True):
            parent, child = real_context.Pipe(duplex=duplex)
            return BlockingRecvConnection(parent), child

        def Process(self, *args, **kwargs):
            return real_context.Process(*args, **kwargs)

    engine = _engine(
        tmp_path,
        "hang_rpc",
        rpc_timeout_seconds=0.2,
        load_timeout_seconds=2.0,
        shutdown_timeout_seconds=0.3,
        process_context_factory=ContextWithBlockingRecv,
    )

    async def run():
        started = time.perf_counter()
        call = asyncio.create_task(engine.provider("base")("triage", _request()))
        deadline = asyncio.get_running_loop().time() + 2.0
        while not reader_blocked.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert reader_blocked.is_set()
        with pytest.raises(integrated.InferenceProviderError) as failure:
            await call
        assert failure.value.failure_category == "rpc_timeout"
        assert time.perf_counter() - started < 2.5
        assert engine._process is not None and not engine._process.is_alive()
        assert all(not thread.is_alive() for thread in engine._io_threads)
        await engine.close()

    asyncio.run(run())
    row = _rows(engine.journal_path)[-1]
    assert row["failure_category"] == "rpc_timeout"
    assert row["prompt_token_ids"] is None
    assert row["generated_token_ids"] is None
    assert row["raw_model_response"] is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX Connection byte-stream framing only")
def test_posix_partial_worker_frame_cannot_block_parent_deadline_or_cleanup(tmp_path):
    engine = _engine(
        tmp_path,
        "partial",
        worker_target=_partial_frame_worker,
        rpc_timeout_seconds=0.2,
        load_timeout_seconds=2.0,
        shutdown_timeout_seconds=0.3,
    )

    async def run():
        started = time.perf_counter()
        with pytest.raises(integrated.InferenceProviderError) as failure:
            await engine.provider("base")("triage", _request())
        assert failure.value.failure_category == "rpc_timeout"
        assert time.perf_counter() - started < 2.5
        assert engine._process is not None and not engine._process.is_alive()
        assert all(not thread.is_alive() for thread in engine._io_threads)
        await engine.close()

    asyncio.run(run())
    row = _rows(engine.journal_path)[-1]
    assert row["failure_category"] == "rpc_timeout"
    assert row["prompt_token_ids"] is None
    assert row["generated_token_ids"] is None
    assert row["raw_model_response"] is None


class BlockingStartProcess:
    def __init__(self, *, start_entered: threading.Event, release_start: threading.Event):
        self.start_entered = start_entered
        self.release_start = release_start
        self.started = False
        self.alive = False
        self.terminated = False
        self.pid = 4242

    def start(self):
        self.start_entered.set()
        self.release_start.wait()
        self.started = True
        self.alive = True

    def is_alive(self):
        if not self.started:
            raise AssertionError("is_alive called before start returned")
        return self.alive

    def terminate(self):
        if not self.started:
            raise AssertionError("terminate called before start returned")
        self.terminated = True
        self.alive = False

    def kill(self):
        self.terminate()

    def join(self, timeout=None):
        return None


async def _wait_for_start_cleanup(engine, timeout: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if engine._process_start_finished.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("late startup thread did not clean up")


def _blocking_start_engine(tmp_path: Path):
    entered = threading.Event()
    release = threading.Event()
    processes = []

    def factory(**kwargs):
        process = BlockingStartProcess(
            start_entered=entered,
            release_start=release,
        )
        processes.append(process)
        return process

    engine = _engine(
        tmp_path,
        "unused",
        process_factory=factory,
        load_timeout_seconds=0.15,
        rpc_timeout_seconds=1.0,
    )
    return engine, entered, release, processes


def test_blocked_process_start_times_out_poisoned_and_late_started_child_is_cleaned(tmp_path):
    engine, entered, release, processes = _blocking_start_engine(tmp_path)
    provider = engine.provider("base")

    async def run():
        task = asyncio.create_task(provider("triage", _request()))
        deadline = asyncio.get_running_loop().time() + 1.0
        while not entered.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert entered.is_set()
        ticks = 0
        while not task.done():
            ticks += 1
            await asyncio.sleep(0.01)
        with pytest.raises(integrated.InferenceProviderError) as failure:
            await task
        assert failure.value.failure_category == "worker_startup_unconfirmed"
        assert ticks >= 3
        assert engine._lock.locked()
        assert not engine._process_started.is_set()
        with pytest.raises(integrated.InferenceProviderError):
            await provider("triage", _request())
        release.set()
        await _wait_for_start_cleanup(engine)
        assert processes[0].started
        assert processes[0].terminated
        assert not processes[0].alive

    asyncio.run(run())
    row = _rows(engine.journal_path)[-1]
    assert row["failure_category"] == "worker_startup_unconfirmed"
    assert row["raw_model_response"] is None


def test_cancellation_during_blocked_start_signals_late_cleanup(tmp_path):
    engine, entered, release, processes = _blocking_start_engine(tmp_path)
    provider = engine.provider("sft")

    async def run():
        task = asyncio.create_task(provider("diagnosis", _request()))
        deadline = asyncio.get_running_loop().time() + 1.0
        while not entered.is_set() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert entered.is_set()
        started = time.perf_counter()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.perf_counter() - started < 2.0
        assert engine._lock.locked()
        assert engine._poisoned
        release.set()
        await _wait_for_start_cleanup(engine)
        assert processes[0].started
        assert processes[0].terminated
        assert not processes[0].alive

    asyncio.run(run())
    row = _rows(engine.journal_path)[-1]
    assert row["inference_status"] == "interrupted"
    assert row["failure_category"] == "cancelled_worker_startup_unconfirmed"
    assert row["raw_model_response"] is None


@pytest.mark.parametrize("post_generation_failure", [False, True])
def test_production_worker_actor_protocol_with_stub_core(monkeypatch, post_generation_failure):
    identity = {
        "checkpoint_manifest_sha256": MANIFEST_PIN,
        "checkpoint_tree_sha256": "e" * 64,
        "base_snapshot_inventory_sha256": "f" * 64,
    }

    class StubCore:
        def __init__(self, **kwargs):
            self._baseline_identity = None
            self.checks = 0
            self._runner = SimpleNamespace(
                generate=lambda arm, inputs: ([92], f"actor-{arm}")
            )

        def _prepare(self):
            self._baseline_identity = identity

        def _revalidate_checkpoint(self):
            self.checks += 1
            if post_generation_failure and self.checks == 2:
                raise integrated._StageFailure("checkpoint_integrity_failure")

        def _encode(self, payload):
            return object(), [41, 42]

        def _admit(self):
            return identity

    monkeypatch.setattr(integrated, "PairedCompletionEngine", StubCore)
    parent, child = multiprocessing.get_context("spawn").Pipe(duplex=True)
    thread = threading.Thread(
        target=local_inference_worker,
        args=(
            child,
            {
                "checkpoint": "unused",
                "checkpoint_manifest_sha256": MANIFEST_PIN,
                "base_snapshot": "unused",
                "base_snapshot_inventory_sha256": "f" * 64,
            },
        ),
        daemon=True,
    )
    thread.start()

    def exchange(operation, **fields):
        _send(parent, {"request_id": 7, "operation": operation, **fields})
        response = _receive(parent)
        while response["kind"] == "encoded":
            exchange.responses.append(response)
            response = _receive(parent)
        return response

    exchange.responses = []
    try:
        assert exchange("load")["kind"] == "loaded"
        result = exchange(
            "complete",
            arm="sft",
            payload={"messages": [{"role": "user", "content": "safe"}]},
        )
        assert exchange.responses[0]["prompt_token_ids"] == [41, 42]
        assert result["kind"] == ("failure" if post_generation_failure else "completed")
        if post_generation_failure:
            assert result["failure_category"] == "checkpoint_integrity_failure"
        assert result["generated_token_ids"] == [92]
        assert result["raw_model_response"] == "actor-sft"
        assert exchange("verify_final")["kind"] == "verified"
        assert exchange("close")["kind"] == "closed"
        thread.join(timeout=1.0)
        assert not thread.is_alive()
    finally:
        parent.close()
        child.close()
        if thread.is_alive():
            thread.join(timeout=1.0)
