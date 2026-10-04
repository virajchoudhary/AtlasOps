from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from bench import integrated_inference as integrated
from bench import integrated_inference_remote as remote

KEY = "2jmj7l5rSw0yVb_vhUN8IY6e3sF9kP4w"
MANIFEST_PIN = remote.PINNED_V17_MANIFEST_SHA256
BASE_PIN = "b" * 64
IDENTITY = {
    "checkpoint_manifest_sha256": MANIFEST_PIN,
    "checkpoint_tree_sha256": "c" * 64,
    "base_snapshot_inventory_sha256": BASE_PIN,
}
RUNTIME = {
    "platform": {
        "system": "Linux",
        "machine": "x86_64",
        "python_version": "3.11.10",
        "python_implementation": "CPython",
    },
    "package_versions": {
        "torch": "2.6.0",
        "transformers": "4.49.0",
        "peft": "0.14.0",
        "bitsandbytes": "0.45.0",
    },
    "cuda_runtime_version": "12.4",
    "cuda_available": True,
    "cuda_device_count": 1,
    "cuda_device_name": "Tesla T4",
    "cuda_current_device": 0,
}
IDENTITY_WITH_RUNTIME = {**IDENTITY, "runtime": RUNTIME}
REQUEST = {"messages": [{"role": "user", "content": "Benign readiness check."}]}


def _wire(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


class Pipe:
    def __init__(self, requests: list[dict[str, Any]]):
        self.requests = [_wire(item) for item in requests]
        self.responses: list[dict[str, Any]] = []
        self.closed = False

    def recv_bytes(self, maxlength: int) -> bytes:
        if not self.requests:
            raise EOFError
        result = self.requests.pop(0)
        assert len(result) <= maxlength
        return result

    def send_bytes(self, value: bytes) -> None:
        assert len(value) <= integrated.MAX_RPC_MESSAGE_BYTES
        self.responses.append(json.loads(value.decode("utf-8")))

    def close(self) -> None:
        self.closed = True


def _worker_config(url: str = "https://inference.example") -> dict[str, Any]:
    return {
        "url": url,
        "inference_key": KEY,
        "checkpoint_manifest_sha256": MANIFEST_PIN,
        "base_snapshot_inventory_sha256": BASE_PIN,
        "rpc_timeout_seconds": 600.0,
        "load_timeout_seconds": 600.0,
        "shutdown_timeout_seconds": 1.0,
        "allow_loopback_http": False,
    }


def _successful_requests() -> list[dict[str, Any]]:
    return [
        {"request_id": 1, "operation": "load"},
        {
            "request_id": 2,
            "operation": "complete",
            "arm": "base",
            "payload": REQUEST,
        },
        {"request_id": 3, "operation": "verify_final"},
        {"request_id": 4, "operation": "close"},
    ]


def test_remote_worker_forwards_bounded_rpc_and_preserves_raw_packets(monkeypatch):
    wire_requests = []
    responses = {
        "load": lambda request_id: [
            {
                "request_id": request_id,
                "kind": "loaded",
                "identity": IDENTITY_WITH_RUNTIME,
            }
        ],
        "complete": lambda request_id: [
            {
                "request_id": request_id,
                "kind": "encoded",
                "prompt_token_ids": [11, 12],
            },
            {
                "request_id": request_id,
                "kind": "completed",
                "generated_token_ids": [91, 92],
                "raw_model_response": '{"status":"raw"}',
            },
        ],
        "verify_final": lambda request_id: [
            {
                "request_id": request_id,
                "kind": "verified",
                "identity": IDENTITY_WITH_RUNTIME,
            }
        ],
        "close": lambda request_id: [
            {"request_id": request_id, "kind": "closed"}
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        wire_requests.append((request, body))
        assert request.url.path == "/rpc"
        packets = responses[body["operation"]](body["request_id"])
        if body["operation"] == "complete":
            return httpx.Response(
                200,
                content=b"".join(_wire(packet) + b"\n" for packet in packets),
                headers={"content-type": "application/x-ndjson"},
            )
        return httpx.Response(200, json={"packets": packets})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(remote.httpx, "AsyncClient", client_factory)
    pipe = Pipe(_successful_requests())

    remote.remote_inference_worker(pipe, _worker_config("https://inference.example/"))

    assert pipe.closed
    assert len(wire_requests) == 4
    assert [body["operation"] for _, body in wire_requests] == [
        "load",
        "complete",
        "verify_final",
        "close",
    ]
    assert all(request.headers[remote.INFERENCE_KEY_HEADER] == KEY for request, _ in wire_requests)
    assert all(KEY.encode("ascii") not in request.content for request, _ in wire_requests)
    assert wire_requests[1][1] == {
        "request_id": 2,
        "operation": "complete",
        "arm": "base",
        "payload": REQUEST,
    }
    assert [response["kind"] for response in pipe.responses] == [
        "transport_observation",
        "transport_observation",
        "loaded",
        "transport_observation",
        "encoded",
        "transport_observation",
        "completed",
        "transport_observation",
        "transport_observation",
        "verified",
        "transport_observation",
        "transport_observation",
        "closed",
    ]
    observations = [
        response["transport"]
        for response in pipe.responses
        if response["kind"] == "transport_observation"
    ]
    assert [
        (item["operation"], item["request_id"], item["http_status"], item["content_type"])
        for item in observations
    ] == [
        ("load", 1, 200, "application/json"),
        ("load", 1, 200, "application/json"),
        ("complete", 2, 200, "application/x-ndjson"),
        ("complete", 2, 200, "application/x-ndjson"),
        ("verify_final", 3, 200, "application/json"),
        ("verify_final", 3, 200, "application/json"),
        ("close", 4, 200, "application/json"),
        ("close", 4, 200, "application/json"),
    ]
    assert all(
        isinstance(item["elapsed_seconds"], float)
        and 0 <= item["elapsed_seconds"] <= 600
        for item in observations
    )
    assert [
        response
        for response in pipe.responses
        if response["kind"] != "transport_observation"
    ] == [
        {"request_id": 1, "kind": "loaded", "identity": IDENTITY_WITH_RUNTIME},
        {"request_id": 2, "kind": "encoded", "prompt_token_ids": [11, 12]},
        {
            "request_id": 2,
            "kind": "completed",
            "generated_token_ids": [91, 92],
            "raw_model_response": '{"status":"raw"}',
        },
        {"request_id": 3, "kind": "verified", "identity": IDENTITY_WITH_RUNTIME},
        {"request_id": 4, "kind": "closed"},
    ]


def test_remote_worker_preserves_encoded_prompt_before_timeout_without_retry(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        packet = {
            "request_id": 7,
            "kind": "encoded",
            "prompt_token_ids": [31, 32],
        }

        class PartialStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield _wire(packet) + b"\n"
                raise httpx.ReadTimeout("private endpoint detail", request=request)

            async def aclose(self):
                return None

        return httpx.Response(
            200,
            stream=PartialStream(),
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(remote.httpx, "AsyncClient", client_factory)
    pipe = Pipe(
        [
            {
                "request_id": 7,
                "operation": "complete",
                "arm": "base",
                "payload": REQUEST,
            }
        ]
    )

    remote.remote_inference_worker(pipe, _worker_config())

    assert len(calls) == 1
    assert len(pipe.responses) == 4
    assert pipe.responses[0]["kind"] == "transport_observation"
    assert pipe.responses[0]["transport"]["http_status"] == 200
    assert pipe.responses[1] == {
        "request_id": 7,
        "kind": "encoded",
        "prompt_token_ids": [31, 32],
    }
    assert pipe.responses[2]["kind"] == "transport_observation"
    assert pipe.responses[2]["transport"]["operation"] == "complete"
    assert pipe.responses[2]["transport"]["request_id"] == 7
    assert pipe.responses[2]["transport"]["http_status"] == 200
    assert pipe.responses[3] == {
        "request_id": 7,
        "kind": "failure",
        "failure_category": "rpc_timeout",
        "generated_token_ids": None,
        "raw_model_response": None,
    }
    assert "private endpoint detail" not in json.dumps(pipe.responses)


def test_remote_worker_rejects_redirect_without_following_it(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            307,
            headers={"location": "https://redirected.example/rpc"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        remote.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    pipe = Pipe([{"request_id": 8, "operation": "load"}])

    remote.remote_inference_worker(pipe, _worker_config())

    assert len(calls) == 1
    assert calls[0].url.host == "inference.example"
    assert [item["kind"] for item in pipe.responses] == [
        "transport_observation",
        "transport_observation",
        "failure",
    ]
    assert pipe.responses[0]["transport"]["http_status"] == 307
    assert pipe.responses[1]["transport"]["http_status"] == 307
    assert pipe.responses[2]["failure_category"] == "bridge_redirect_rejected"


@pytest.mark.parametrize(
    ("operation", "status", "category"),
    [
        ("load", 401, "bridge_authentication_failure"),
        ("complete", 524, "bridge_http_failure"),
        ("verify_final", 500, "bridge_http_failure"),
        ("close", 307, "bridge_redirect_rejected"),
    ],
)
def test_non_200_transport_observation_is_safe_and_precedes_failure(
    monkeypatch, operation, status, category
):
    body_secret = "body-secret-never-journaled"
    header_secret = "header-secret-never-journaled"
    location_secret = "tunnel-secret.never-journaled"

    def handler(request: httpx.Request) -> httpx.Response:
        content_type = (
            f"{header_secret}; token={location_secret}"
            if operation == "verify_final"
            else f"application/json; api_key={header_secret}"
        )
        return httpx.Response(
            status,
            content=json.dumps({"detail": body_secret}).encode("utf-8"),
            headers={
                "content-type": content_type,
                "x-api-key": header_secret,
                "set-cookie": f"session={header_secret}",
                "location": f"https://{location_secret}/rpc",
            },
            request=request,
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        remote.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    request = {"request_id": 20, "operation": operation}
    if operation == "complete":
        request.update({"arm": "base", "payload": REQUEST})
    pipe = Pipe([request])

    remote.remote_inference_worker(pipe, _worker_config())

    assert [item["kind"] for item in pipe.responses] == [
        "transport_observation",
        "transport_observation",
        "failure",
    ]
    observation = pipe.responses[0]
    assert observation["request_id"] == 20
    safe_transport = observation["transport"]
    for observation in pipe.responses[:2]:
        safe_transport = observation["transport"]
        assert {
            key: safe_transport[key]
            for key in ("operation", "request_id", "http_status", "content_type")
        } == {
            "operation": operation,
            "request_id": 20,
            "http_status": status,
            "content_type": None if operation == "verify_final" else "application/json",
        }
        assert 0 <= safe_transport["elapsed_seconds"] <= 600
    assert pipe.responses[2]["failure_category"] == category
    serialized = json.dumps(pipe.responses)
    for secret in (KEY, body_secret, header_secret, location_secret, "inference.example"):
        assert secret not in serialized


def test_remote_worker_rejects_local_operational_secret_before_http(monkeypatch):
    secret = "local-operational-key-do-not-send"
    monkeypatch.setenv("ATLASOPS_API_KEY", secret)
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        remote.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    pipe = Pipe(
        [
            {
                "request_id": 9,
                "operation": "complete",
                "arm": "base",
                "payload": {
                    "messages": [
                        {"role": "user", "content": f"Inspect {secret}."}
                    ]
                },
            }
        ]
    )

    remote.remote_inference_worker(pipe, _worker_config())

    assert calls == []
    assert pipe.responses[0]["failure_category"] == "operational_secret_blocked"
    assert secret not in json.dumps(pipe.responses)


def test_proxy_rejects_non_pinned_adapter_manifest(tmp_path):
    with pytest.raises(ValueError, match="pinned v17"):
        remote.create_remote_engine(
            checkpoint=str(tmp_path / "checkpoint"),
            checkpoint_manifest_sha256="a" * 64,
            base_snapshot=str(tmp_path / "base"),
            base_snapshot_inventory_sha256=BASE_PIN,
            journal_path=str(tmp_path / "inference.jsonl"),
            url="https://inference.example",
            inference_key=KEY,
        )


def test_proxy_rejects_server_identity_that_does_not_match_pinned_manifest(monkeypatch):
    wrong_identity = {**IDENTITY, "checkpoint_manifest_sha256": "d" * 64}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "packets": [
                    {
                        "request_id": 5,
                        "kind": "loaded",
                        "identity": wrong_identity,
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        remote.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    pipe = Pipe([{"request_id": 5, "operation": "load"}])

    remote.remote_inference_worker(pipe, _worker_config())

    assert [item["kind"] for item in pipe.responses] == [
        "transport_observation",
        "transport_observation",
        "failure",
    ]
    assert pipe.responses[0]["transport"]["operation"] == "load"
    assert pipe.responses[0]["transport"]["request_id"] == 5
    assert pipe.responses[0]["transport"]["http_status"] == 200
    assert pipe.responses[2:] == [
        {
            "request_id": 5,
            "kind": "failure",
            "failure_category": "model_identity_mismatch",
            "generated_token_ids": None,
            "raw_model_response": None,
        }
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://inference.example",
        "https://user:password@inference.example",
        "https://inference.example?token=secret",
        "https://inference.example#fragment",
    ],
)
def test_bridge_rejects_non_loopback_http_and_credential_bearing_urls(url):
    with pytest.raises(ValueError):
        remote._validated_url(url)


def test_bridge_accepts_https_and_loopback_test_urls():
    assert remote._validated_url("https://inference.example/") == "https://inference.example"
    assert remote._validated_url(
        "http://127.0.0.1:8000", allow_test_http=True
    ) == "http://127.0.0.1:8000"
    assert remote._validated_url(
        "http://[::1]:8000", allow_test_http=True
    ) == "http://[::1]:8000"
    with pytest.raises(ValueError, match="explicit loopback tests"):
        remote._validated_url("http://127.0.0.1:8000")


@pytest.mark.parametrize("key", ["short", "x" * 40, "has space " + "a" * 30, "x" * 32 + "\n"])
def test_bridge_rejects_weak_or_non_printable_keys(key):
    with pytest.raises(ValueError):
        remote._validate_inference_key(key)


def test_create_remote_engine_keeps_model_paths_out_of_worker_transport_config(tmp_path):
    checkpoint = tmp_path / "checkpoint"
    base = tmp_path / "base"
    checkpoint.mkdir()
    base.mkdir()
    journal = tmp_path / "inference.jsonl"

    engine = remote.create_remote_engine(
        checkpoint=str(checkpoint),
        checkpoint_manifest_sha256=MANIFEST_PIN,
        base_snapshot=str(base),
        base_snapshot_inventory_sha256=BASE_PIN,
        journal_path=str(journal),
        url="https://inference.example",
        inference_key=KEY,
    )

    assert engine._process_worker_target is remote.remote_inference_worker
    assert engine.rpc_timeout_seconds == 600.0
    assert engine.load_timeout_seconds == 600.0
    assert set(engine._process_worker_config) == {
        "url",
        "inference_key",
        "checkpoint_manifest_sha256",
        "base_snapshot_inventory_sha256",
        "rpc_timeout_seconds",
        "load_timeout_seconds",
        "shutdown_timeout_seconds",
        "allow_loopback_http",
    }
    assert str(checkpoint) not in json.dumps(engine._process_worker_config)
    assert str(base) not in json.dumps(engine._process_worker_config)


def test_loopback_engine_is_hardwired_to_ipv4_loopback_and_same_deadlines(tmp_path):
    checkpoint = tmp_path / "checkpoint"
    base = tmp_path / "base"
    checkpoint.mkdir()
    base.mkdir()
    engine = remote.create_loopback_engine(
        checkpoint=str(checkpoint),
        checkpoint_manifest_sha256=MANIFEST_PIN,
        base_snapshot=str(base),
        base_snapshot_inventory_sha256=BASE_PIN,
        journal_path=str(tmp_path / "inference.jsonl"),
        inference_key=KEY,
    )

    assert engine._process_worker_target is remote.remote_inference_worker
    assert engine._process_worker_config["url"] == "http://127.0.0.1:18765"
    assert engine._process_worker_config["allow_loopback_http"] is True
    assert engine.rpc_timeout_seconds == 600
    assert engine.load_timeout_seconds == 600
    for unsafe_url in ("http://localhost:18765", "http://192.0.2.1:18765"):
        with pytest.raises(ValueError):
            remote._read_config(
                {**engine._process_worker_config, "url": unsafe_url}
            )
    with pytest.raises(ValueError, match="port"):
        remote.create_loopback_engine(
            checkpoint=str(checkpoint),
            checkpoint_manifest_sha256=MANIFEST_PIN,
            base_snapshot=str(base),
            base_snapshot_inventory_sha256=BASE_PIN,
            journal_path=str(tmp_path / "other.jsonl"),
            inference_key=KEY,
            port=True,
        )


class FakeServerEngine:
    fixture_backend = False
    _process_worker_target = None
    _process_factory = None
    _process_context_factory = None

    def __init__(self, *, fail_complete: bool = False, identity=None):
        self.checkpoint_manifest_sha256 = MANIFEST_PIN
        self.base_snapshot_inventory_sha256 = BASE_PIN
        self.load_timeout_seconds = 2.0
        self.rpc_timeout_seconds = 2.0
        self._poisoned = False
        self._poison_reason = None
        self._worker_loaded = False
        self._baseline_identity = None
        self._closed = False
        self.fail_complete = fail_complete
        self.identity = identity or IDENTITY
        self.calls = []
        self.terminated = False
        self.gracefully_stopped = False

    async def _start_worker(self, timeout):
        self.calls.append(("start", timeout))

    async def _process_rpc(self, operation, payload, expected, timeout, *, on_encoded=None):
        self.calls.append((operation, payload, expected, timeout))
        if operation == "load":
            return {"identity": self.identity}
        if operation == "complete" and self.fail_complete:
            raise integrated._ProcessRPCFailure(
                "rpc_timeout",
                generated_token_ids=[93],
                raw_model_response="late raw output",
            )
        if operation == "complete":
            on_encoded(
                {
                    "request_id": 10,
                    "kind": "encoded",
                    "prompt_token_ids": [11, 12],
                }
            )
            return {
                "request_id": 11,
                "kind": "completed",
                "generated_token_ids": [91],
                "raw_model_response": "server raw output",
            }
        if operation == "verify_final":
            return {"identity": self.identity}
        raise AssertionError(operation)

    async def _terminate_worker_uncancellable(self):
        self.terminated = True
        return True

    async def _graceful_stop_worker(self):
        self.gracefully_stopped = True
        return True


def _rpc_body(operation: str, request_id: int = 1, **fields) -> dict[str, Any]:
    return {"request_id": request_id, "operation": operation, **fields}


def test_server_authenticates_and_routes_only_validated_inference_packets(monkeypatch):
    monkeypatch.setattr(remote, "_server_runtime_metadata", lambda: RUNTIME)
    engine = FakeServerEngine()
    app = remote.inference_bridge_app(engine, KEY)
    with TestClient(app) as client:
        denied = client.post("/rpc", json=_rpc_body("load"))
        assert denied.status_code == 401
        assert KEY not in denied.text

        path_override = client.post(
            "/rpc",
            headers={remote.INFERENCE_KEY_HEADER: KEY},
            json=_rpc_body("load", checkpoint="/tmp/other-model"),
        )
        assert path_override.status_code == 400
        assert engine.calls == []

        loaded = client.post(
            "/rpc",
            headers={remote.INFERENCE_KEY_HEADER: KEY},
            json=_rpc_body("load", request_id=14),
        )
        assert loaded.status_code == 200
        assert loaded.json() == {
            "packets": [
                {
                    "request_id": 14,
                    "kind": "loaded",
                    "identity": IDENTITY_WITH_RUNTIME,
                }
            ]
        }

        completed = client.post(
            "/rpc",
            headers={remote.INFERENCE_KEY_HEADER: KEY},
            json=_rpc_body("complete", request_id=15, arm="base", payload=REQUEST),
        )
        assert completed.status_code == 200
        assert [
            json.loads(line)
            for line in completed.content.decode("utf-8").splitlines()
        ] == [
                {
                    "request_id": 15,
                    "kind": "encoded",
                    "prompt_token_ids": [11, 12],
                },
                {
                    "request_id": 15,
                    "kind": "completed",
                    "generated_token_ids": [91],
                    "raw_model_response": "server raw output",
                },
        ]
        assert engine.calls[-1][1] == {"arm": "base", "payload": REQUEST}

        verified = client.post(
            "/rpc",
            headers={remote.INFERENCE_KEY_HEADER: KEY},
            json=_rpc_body("verify_final", request_id=16),
        )
        assert verified.json() == {
            "packets": [
                {
                    "request_id": 16,
                    "kind": "verified",
                    "identity": IDENTITY_WITH_RUNTIME,
                }
            ]
        }
        assert client.post("/docs").status_code == 404
        assert client.post("/tools").status_code == 404
        assert client.post(
            "/rpc",
            headers={remote.INFERENCE_KEY_HEADER: KEY},
            json=_rpc_body("execute_tool", request_id=17),
        ).status_code == 400


def test_server_timeout_preserves_partial_raw_output_and_poison_cleans_worker(monkeypatch):
    monkeypatch.setattr(remote, "_server_runtime_metadata", lambda: RUNTIME)
    engine = FakeServerEngine(fail_complete=True)
    app = remote.inference_bridge_app(engine, KEY)
    with TestClient(app) as client:
        client.post(
            "/rpc",
            headers={remote.INFERENCE_KEY_HEADER: KEY},
            json=_rpc_body("load"),
        )
        result = client.post(
            "/rpc",
            headers={remote.INFERENCE_KEY_HEADER: KEY},
            json=_rpc_body("complete", request_id=3, arm="sft", payload=REQUEST),
        )

    assert [
        json.loads(line)
        for line in result.content.decode("utf-8").splitlines()
    ] == [
            {
                "request_id": 3,
                "kind": "failure",
                "failure_category": "rpc_timeout",
                "generated_token_ids": [93],
                "raw_model_response": "late raw output",
            }
    ]
    assert engine._poisoned
    assert engine._poison_reason == "rpc_timeout"
    assert engine.terminated


def test_server_rejects_non_pinned_manifest_or_identity():
    wrong_pin_engine = FakeServerEngine()
    wrong_pin_engine.checkpoint_manifest_sha256 = "a" * 64
    with pytest.raises(ValueError, match="pinned v17"):
        remote.inference_bridge_app(wrong_pin_engine, KEY)

    engine = FakeServerEngine(identity={**IDENTITY, "checkpoint_manifest_sha256": "d" * 64})
    app = remote.inference_bridge_app(engine, KEY)
    with TestClient(app) as client:
        response = client.post(
            "/rpc",
            headers={remote.INFERENCE_KEY_HEADER: KEY},
            json=_rpc_body("load"),
        )

    assert response.json()["packets"] == [
        {
            "request_id": 1,
            "kind": "failure",
            "failure_category": "model_identity_mismatch",
            "generated_token_ids": None,
            "raw_model_response": None,
        }
    ]
    assert engine.terminated


def test_server_rejects_oversized_body_before_model_rpc():
    engine = FakeServerEngine()
    app = remote.inference_bridge_app(engine, KEY)
    with TestClient(app) as client:
        result = client.post(
            "/rpc",
            content=b" " * (remote.MAX_BRIDGE_BODY_BYTES + 1),
            headers={
                remote.INFERENCE_KEY_HEADER: KEY,
                "content-type": "application/json",
            },
        )

    assert result.status_code == 413
    assert engine.calls == []
