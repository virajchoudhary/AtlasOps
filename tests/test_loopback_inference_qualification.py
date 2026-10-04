"""NON_EMPIRICAL qualification path tests; no model or cluster is loaded."""

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import qualify_loopback_inference as loopback


def _ready_remote_worker(connection, config):
    from bench.integrated_inference_process import _send
    from bench.integrated_inference_remote import remote_inference_worker

    _send(connection, {"kind": "fixture_ready"})
    remote_inference_worker(connection, config)


def arguments(tmp_path):
    key = tmp_path / "inference.key"
    key.write_text("fixture-key-not-an-operational-secret")
    return argparse.Namespace(
        checkpoint=tmp_path / "checkpoint", checkpoint_manifest_sha256="a" * 64,
        base_snapshot=tmp_path / "base", base_inventory_sha256="b" * 64,
        journal=tmp_path / "loopback.jsonl", inference_key_file=key, port=18765,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_qualification_uses_loopback_factory_and_closes_only_proxy(tmp_path, monkeypatch, fails):
    from bench import integrated_inference_remote as remote

    engine = SimpleNamespace(
        _terminate_worker_uncancellable=AsyncMock(return_value=True),
        close=AsyncMock(), journal_path=None,
    )
    calls = []
    monkeypatch.setattr(loopback, "validate_runtime", lambda: calls.append("runtime"))
    monkeypatch.setattr(remote, "create_loopback_engine", lambda **kwargs: calls.append(kwargs) or engine)
    qualified = AsyncMock(
        return_value={"status": "QUALIFIED", "incident_attempt_reserved": False},
        side_effect=RuntimeError("safe fixture failure") if fails else None,
    )
    monkeypatch.setattr(loopback, "qualify_engine", qualified)
    if fails:
        with pytest.raises(RuntimeError, match="safe fixture"):
            await loopback.run(arguments(tmp_path))
    else:
        result = await loopback.run(arguments(tmp_path))
        assert result["incident_attempt_reserved"] is False
    assert calls[0] == "runtime"
    assert "url" not in calls[1]
    assert calls[1]["port"] == 18765
    qualified.assert_awaited_once_with(engine)
    engine._terminate_worker_uncancellable.assert_awaited_once()
    engine.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_failure_prevents_inference_factory(tmp_path, monkeypatch):
    from bench import integrated_inference_remote as remote

    monkeypatch.setattr(loopback, "validate_runtime", lambda: (_ for _ in ()).throw(ValueError("runtime drift")))
    factory = lambda **kwargs: pytest.fail("Inference factory called before runtime verification")
    monkeypatch.setattr(remote, "create_loopback_engine", factory)
    with pytest.raises(ValueError, match="runtime drift"):
        await loopback.run(arguments(tmp_path))


def test_final_transport_reference_includes_close_without_rewriting_prefix(tmp_path):
    import hashlib
    import json

    from scripts.qualify_integrated_inference import preserve_final_transport_reference

    journal = tmp_path / "raw.jsonl"
    prefix = b'{"status":"NOT_QUALIFIED"}\n'
    journal.write_bytes(prefix)
    historical = journal.with_suffix(".qualification.json")
    historical.write_bytes(b'{"preserved":"original-prefix"}\n')
    journal.write_bytes(prefix + b'{"record":"transport_observation","operation":"close"}\n')
    candidate = SimpleNamespace(journal_path=journal, _closed=True)
    preserve_final_transport_reference(candidate)
    reference = json.loads(journal.with_suffix(".transport-final.json").read_bytes())
    assert reference["journal"]["raw_sha256"] == hashlib.sha256(journal.read_bytes()).hexdigest()
    assert reference["proxy_cleanup_confirmed"] is True
    assert reference["incident_attempt_reserved"] is False
    assert historical.read_bytes() == b'{"preserved":"original-prefix"}\n'
    with pytest.raises(FileExistsError):
        preserve_final_transport_reference(candidate)


def test_actual_loopback_server_rpc_with_fixture_model_only(tmp_path, monkeypatch):
    import asyncio
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    from bench import integrated_inference_remote as remote
    from tests.test_integrated_inference_remote import FakeServerEngine, KEY, RUNTIME

    monkeypatch.setattr(remote, "_server_runtime_metadata", lambda: RUNTIME)
    model = FakeServerEngine()
    model.shutdown_timeout_seconds = 1.0
    server = uvicorn.Server(uvicorn.Config(
        remote.inference_bridge_app(model, KEY),
        host="127.0.0.1", port=0, access_log=False, log_level="warning",
    ))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    observations = []
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started

        async def run():
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                for request in (
                    {"request_id": 1, "operation": "load"},
                    {"request_id": 2, "operation": "complete", "arm": "base", "payload": {
                        "messages": [{"role": "user", "content": "Reply READY."}],
                    }},
                    {"request_id": 3, "operation": "verify_final"},
                    {"request_id": 4, "operation": "close"},
                ):
                    packets = await remote._post_rpc(
                        client, f"http://127.0.0.1:{port}", KEY, request, 2,
                        on_transport=observations.append,
                    )
                    assert packets
        asyncio.run(run())
        assert {row["operation"] for row in observations} == {
            "load", "complete", "verify_final", "close",
        }
        assert all(row["http_status"] == 200 for row in observations)
        assert model.gracefully_stopped
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
    assert not thread.is_alive()


def test_silent_loopback_timeout_has_parent_transport_observation(tmp_path):
    import asyncio
    import json
    import socket
    import threading

    from bench import integrated_inference as integrated
    from bench.integrated_inference_remote import create_loopback_engine
    from tests.test_integrated_inference_remote import BASE_PIN, KEY, MANIFEST_PIN

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.settimeout(10)
    stop = threading.Event()
    accepted = threading.Event()

    def silent_server():
        try:
            conn, _ = sock.accept()
        except OSError:
            return
        with conn:
            accepted.set()
            stop.wait(10)

    thread = threading.Thread(target=silent_server, daemon=True)
    thread.start()
    checkpoint = tmp_path / "checkpoint"
    base = tmp_path / "base"
    checkpoint.mkdir()
    base.mkdir()
    engine = create_loopback_engine(
        checkpoint=str(checkpoint), checkpoint_manifest_sha256=MANIFEST_PIN,
        base_snapshot=str(base), base_snapshot_inventory_sha256=BASE_PIN,
        journal_path=str(tmp_path / "silent.jsonl"), inference_key=KEY,
        port=sock.getsockname()[1],
    )
    engine._process_worker_target = _ready_remote_worker

    async def run():
        engine._open_journal()
        await engine._start_worker(3)
        try:
            deadline = asyncio.get_running_loop().time() + 5
            while engine._responses.empty() and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.01)
            assert engine._responses.get_nowait() == {"kind": "fixture_ready"}
            with pytest.raises(integrated._ProcessRPCFailure) as failure:
                await engine._process_rpc("load", {}, "loaded", 2)
            assert failure.value.category == "load_timeout"
            rows = [json.loads(line) for line in engine.journal_path.read_text().splitlines()]
            assert rows[-1]["record"] == "transport_observation"
            assert rows[-1]["operation"] == "load"
            assert rows[-1]["request_id"] == 1
            assert rows[-1]["http_status"] is None
            assert rows[-1]["content_type"] is None
            assert 2 <= rows[-1]["elapsed_seconds"] <= 3
        finally:
            assert await engine._terminate_worker_uncancellable()
            await engine.close()
    try:
        asyncio.run(run())
        assert accepted.is_set()
    finally:
        stop.set()
        sock.close()
        thread.join(timeout=5)
    assert not thread.is_alive()
