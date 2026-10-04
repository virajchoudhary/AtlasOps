"""Authenticated, inference-only HTTPS transport for integrated inference RPC."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from bench import integrated_inference as integrated
from bench.integrated_inference import PairedCompletionEngine
from bench.integrated_inference_process import _receive, _send

INFERENCE_KEY_HEADER = "x-atlasops-inference-key"
MAX_BRIDGE_BODY_BYTES = integrated.MAX_RPC_MESSAGE_BYTES
PINNED_V17_MANIFEST_SHA256 = "7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440"
_OPERATIONS = frozenset({"load", "complete", "verify_final", "close"})
_SAFE_RESPONSE_CONTENT_TYPES = integrated._SAFE_TRANSPORT_CONTENT_TYPES
_IDENTITY_FIELDS = frozenset(
    {
        "checkpoint_manifest_sha256",
        "checkpoint_tree_sha256",
        "base_snapshot_inventory_sha256",
    }
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SECRET_ENV_NAMES = frozenset(
    {
        "ALERTMANAGER_WEBHOOK_SECRET",
        "APPROVAL_API_KEY",
        "APPROVAL_SECRET",
        "ARGOCD_PASS",
        "ATLASOPS_API_KEY",
        "AUDIT_SECRET",
        "KUBECONFIG",
        "SLACK_WEBHOOK",
    }
)
_SECRET_ENV_MARKERS = (
    "APPROVAL",
    "APIKEY",
    "AUDITSECRET",
    "ARGOCDPASS",
    "CREDENTIAL",
    "KUBECONFIG",
    "PASSWORD",
    "WEBHOOK",
)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.I),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|approval[_-]?key|client[_-]?secret|"
        r"password)\b\s*[:=]\s*['\"]?[^\s,'\"}]{8,}",
        re.I,
    ),
    re.compile(
        r"\b(?:client-key-data|client-certificate-data|certificate-authority-data)\s*:",
        re.I,
    ),
)
_RUNTIME_FIELDS = frozenset(
    {
        "platform",
        "package_versions",
        "cuda_runtime_version",
        "cuda_available",
        "cuda_device_count",
        "cuda_device_name",
        "cuda_current_device",
    }
)
_RUNTIME_PLATFORM_FIELDS = frozenset(
    {"system", "machine", "python_version", "python_implementation"}
)
_RUNTIME_PACKAGE_FIELDS = frozenset({"torch", "transformers", "peft", "bitsandbytes"})


def _validate_inference_key(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not 32 <= len(value) <= 256
        or not value.isascii()
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
        or len(set(value)) < 12
    ):
        raise ValueError("Inference key must be bounded, high-variation printable text")
    _ensure_inference_key_is_separate(value)
    return value


def _known_operational_secret_values() -> tuple[str, ...]:
    values = []
    for name, value in os.environ.items():
        normalized = re.sub(r"[^A-Z0-9]", "", name.upper())
        if (
            name.upper() in _SECRET_ENV_NAMES
            or any(marker in normalized for marker in _SECRET_ENV_MARKERS)
        ) and isinstance(value, str) and value.strip():
            values.append(value)
    return tuple(dict.fromkeys(values))


def _ensure_inference_key_is_separate(value: str) -> None:
    if any(secrets.compare_digest(value, secret) for secret in _known_operational_secret_values()):
        raise ValueError("Inference key must be separate from operational credentials")


def _reject_operational_secrets(request: Mapping[str, Any]) -> None:
    text = json.dumps(request, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if any(pattern.search(text) for pattern in _SECRET_TEXT_PATTERNS):
        raise _RemoteBridgeFailure("operational_secret_blocked")
    for secret in _known_operational_secret_values():
        if len(secret) >= 8 and secret in text:
            raise _RemoteBridgeFailure("operational_secret_blocked")
        if (
            1 <= len(secret) < 8
            and secret.casefold() not in {"0", "1", "false", "true", "no", "yes"}
            and re.search(
                rf"(?<![A-Za-z0-9]){re.escape(secret)}(?![A-Za-z0-9])",
                text,
                re.IGNORECASE,
            )
        ):
            raise _RemoteBridgeFailure("operational_secret_blocked")


def _validated_url(value: Any, *, allow_test_http: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("Inference bridge URL must be an absolute URL")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("Inference bridge URL is invalid") from None
    del port
    if (
        parsed.scheme not in {"https", "http"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Inference bridge URL must not contain credentials or query data")
    if parsed.scheme == "http":
        try:
            loopback = hostname.casefold() == "localhost" or ipaddress.ip_address(
                hostname
            ).is_loopback
        except ValueError:
            loopback = False
        if not allow_test_http or not loopback:
            raise ValueError("Plain HTTP is allowed only for explicit loopback tests")
    return value.rstrip("/")


def _safe_response_content_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    media_type = value.split(";", 1)[0].strip().casefold()
    return media_type if media_type in _SAFE_RESPONSE_CONTENT_TYPES else None


def _transport_metadata(
    operation: str,
    request_id: int,
    http_status: int | None,
    content_type: str | None,
    started: float,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "request_id": request_id,
        "http_status": http_status,
        "content_type": content_type,
        "elapsed_seconds": round(
            min(
                integrated.MAX_PROCESS_TIMEOUT_SECONDS,
                max(0.0, time.monotonic() - started),
            ),
            6,
        ),
    }


def _bounded_timeout(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 < value <= integrated.MAX_PROCESS_TIMEOUT_SECONDS
    ):
        raise ValueError(f"{name} must be finite, positive and at most 600 seconds")
    return float(value)


def _json_bytes(value: Any, *, limit: int = MAX_BRIDGE_BODY_BYTES) -> bytes:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        raise ValueError("Bridge message must be finite JSON data") from None
    if len(raw) > limit:
        raise ValueError("Bridge message exceeds the bounded frame size")
    return raw


def _valid_token_ids(value: Any) -> bool:
    return isinstance(value, list) and all(type(item) is int for item in value)


def _validate_packet(packet: Any, operation: str, request_id: int) -> dict[str, Any]:
    if (
        not isinstance(packet, dict)
        or type(packet.get("request_id")) is not int
        or packet.get("request_id") != request_id
    ):
        raise ValueError("Bridge response request id is invalid")
    kind = packet.get("kind")
    if kind == "failure":
        category = packet.get("failure_category")
        if not isinstance(category, str) or not category or len(category) > 128:
            raise ValueError("Bridge failure category is invalid")
        generated = packet.get("generated_token_ids")
        raw_text = packet.get("raw_model_response")
        if generated is not None and not _valid_token_ids(generated):
            raise ValueError("Bridge failure token ids are invalid")
        if raw_text is not None and (
            not isinstance(raw_text, str)
            or len(raw_text.encode("utf-8")) > MAX_BRIDGE_BODY_BYTES
        ):
            raise ValueError("Bridge failure response text is invalid")
        return packet

    if operation == "load" and kind == "loaded":
        _validate_identity(packet.get("identity"))
    elif operation == "complete" and kind == "encoded":
        if not _valid_token_ids(packet.get("prompt_token_ids")):
            raise ValueError("Bridge prompt token ids are invalid")
    elif operation == "complete" and kind == "completed":
        if not _valid_token_ids(packet.get("generated_token_ids")):
            raise ValueError("Bridge generated token ids are invalid")
        raw_text = packet.get("raw_model_response")
        if not isinstance(raw_text, str) or len(raw_text.encode("utf-8")) > MAX_BRIDGE_BODY_BYTES:
            raise ValueError("Bridge completion text is invalid")
    elif operation == "verify_final" and kind == "verified":
        _validate_identity(packet.get("identity"))
    elif operation == "close" and kind == "closed":
        pass
    else:
        raise ValueError("Bridge response kind does not match its operation")
    return packet


def _validate_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) not in (
        _IDENTITY_FIELDS,
        _IDENTITY_FIELDS | {"runtime"},
    ):
        raise ValueError("Bridge model identity is incomplete")
    digests = {key: value[key] for key in _IDENTITY_FIELDS}
    if any(
        not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest)
        for digest in digests.values()
    ):
        raise ValueError("Bridge model identity contains an invalid digest")
    if "runtime" in value:
        _validate_runtime(value["runtime"])
    return value


def _validate_runtime(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _RUNTIME_FIELDS:
        raise ValueError("Bridge runtime metadata is incomplete")
    platform = value["platform"]
    packages = value["package_versions"]
    if (
        not isinstance(platform, dict)
        or set(platform) != _RUNTIME_PLATFORM_FIELDS
        or not isinstance(packages, dict)
        or set(packages) != _RUNTIME_PACKAGE_FIELDS
    ):
        raise ValueError("Bridge runtime metadata schema is invalid")
    for item in (*platform.values(), *packages.values(), value["cuda_device_name"]):
        if not isinstance(item, str) or not re.fullmatch(
            r"[A-Za-z0-9 .()+_-]{1,128}", item
        ):
            raise ValueError("Bridge runtime metadata text is invalid")
    if (
        not isinstance(value["cuda_runtime_version"], str)
        or not re.fullmatch(r"[0-9.]{1,32}", value["cuda_runtime_version"])
    ):
        raise ValueError("Bridge CUDA runtime version is invalid")
    if (
        value["cuda_available"] is not True
        or type(value["cuda_device_count"]) is not int
        or value["cuda_device_count"] != 1
        or type(value["cuda_current_device"]) is not int
        or value["cuda_current_device"] != 0
        or "t4" not in value["cuda_device_name"].casefold()
    ):
        raise ValueError("Bridge runtime is not one pinned Tesla T4 device")
    return value


def _server_runtime_metadata() -> dict[str, Any]:
    import importlib.metadata
    import platform

    import peft
    import torch
    import transformers

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise integrated._StageFailure("runtime_device_mismatch")
    name = str(torch.cuda.get_device_name(0))
    if "t4" not in name.casefold() or torch.cuda.current_device() != 0:
        raise integrated._StageFailure("runtime_device_mismatch")
    return _validate_runtime(
        {
            "platform": {
                "system": platform.system(),
                "machine": platform.machine(),
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
            },
            "package_versions": {
                "torch": str(torch.__version__),
                "transformers": str(transformers.__version__),
                "peft": str(peft.__version__),
                "bitsandbytes": importlib.metadata.version("bitsandbytes"),
            },
            "cuda_runtime_version": getattr(torch.version, "cuda", None),
            "cuda_available": True,
            "cuda_device_count": 1,
            "cuda_device_name": name,
            "cuda_current_device": 0,
        }
    )


def _failure_packet(
    request_id: int,
    category: str,
    *,
    generated_token_ids: Any = None,
    raw_model_response: Any = None,
) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "request_id": request_id,
        "kind": "failure",
        "failure_category": category[:128],
        "generated_token_ids": (
            generated_token_ids if _valid_token_ids(generated_token_ids) else None
        ),
        "raw_model_response": (
            raw_model_response
            if isinstance(raw_model_response, str)
            and len(raw_model_response.encode("utf-8")) <= MAX_BRIDGE_BODY_BYTES
            else None
        ),
    }
    try:
        _json_bytes(packet)
    except ValueError:
        packet["generated_token_ids"] = None
        packet["raw_model_response"] = None
    return packet


def _request_failure_kind(operation: str) -> str:
    return "load_timeout" if operation == "load" else "rpc_timeout"


async def _read_request_body(request: Request) -> Any:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().casefold() != (
        "application/json"
    ):
        raise ValueError("Bridge requests must use JSON")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            parsed_length = int(content_length)
        except ValueError:
            raise ValueError("Bridge content length is invalid") from None
        if parsed_length < 0 or parsed_length > MAX_BRIDGE_BODY_BYTES:
            raise ValueError("Bridge request exceeds the bounded frame size")
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BRIDGE_BODY_BYTES:
            raise ValueError("Bridge request exceeds the bounded frame size")
        chunks.append(chunk)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Bridge request is not valid JSON") from None
    if not isinstance(value, dict):
        raise ValueError("Bridge request must be a JSON object")
    return value


def _validate_rpc_request(value: Mapping[str, Any]) -> tuple[int, str]:
    request_id = value.get("request_id")
    operation = value.get("operation")
    if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 1:
        raise ValueError("Bridge request id is invalid")
    if operation not in _OPERATIONS:
        raise ValueError("Bridge operation is unsupported")
    expected = (
        {"request_id", "operation", "arm", "payload"}
        if operation == "complete"
        else {"request_id", "operation"}
    )
    if set(value) != expected:
        raise ValueError("Bridge request contains unsupported fields")
    if operation == "complete":
        if value["arm"] not in integrated.ARMS or not isinstance(value["arm"], str):
            raise ValueError("Bridge arm is invalid")
        try:
            integrated._validate_request(value["payload"])
        except (TypeError, ValueError):
            raise ValueError("Bridge completion payload is invalid") from None
    return request_id, operation


def _server_identity(engine: Any, identity: Any) -> dict[str, str]:
    result = _validate_identity(identity)
    if (
        engine.checkpoint_manifest_sha256 != PINNED_V17_MANIFEST_SHA256
        or result["checkpoint_manifest_sha256"] != engine.checkpoint_manifest_sha256
        or result["base_snapshot_inventory_sha256"]
        != engine.base_snapshot_inventory_sha256
    ):
        raise integrated._StageFailure("model_identity_mismatch")
    return result


def _wire_packet(packet: Mapping[str, Any], request_id: int) -> dict[str, Any]:
    result = dict(packet)
    result["request_id"] = request_id
    return result


async def _poison_server_engine(engine: Any, category: str) -> str:
    if engine._poisoned and engine._poison_reason:
        return engine._poison_reason
    engine._poisoned = True
    engine._poison_reason = category
    try:
        stopped = await engine._terminate_worker_uncancellable()
    except Exception:  # noqa: BLE001 - only a safe category may cross the bridge
        stopped = False
    if not stopped:
        category = f"{category}_kill_failure"
        engine._poison_reason = category
    return category


async def _handle_server_rpc(
    engine: Any,
    request_id: int,
    operation: str,
    body: Mapping[str, Any],
    *,
    on_encoded: Any = None,
) -> list[dict[str, Any]]:
    if engine._poisoned and operation != "close":
        return [_failure_packet(request_id, engine._poison_reason or "engine_poisoned")]

    try:
        if operation == "load":
            if engine._worker_loaded and engine._baseline_identity is not None:
                identity = engine._baseline_identity
            else:
                deadline = asyncio.get_running_loop().time() + engine.load_timeout_seconds
                await engine._start_worker(engine.load_timeout_seconds)
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise integrated._ProcessRPCFailure("load_timeout")
                loaded = await engine._process_rpc("load", {}, "loaded", remaining)
                identity = _server_identity(engine, loaded.get("identity"))
                engine._baseline_identity = identity
                engine._worker_loaded = True
                engine._loaded = True
            return [
                {"request_id": request_id, "kind": "loaded", "identity": identity}
            ]

        if operation == "complete":
            if not engine._worker_loaded or engine._baseline_identity is None:
                return [_failure_packet(request_id, "worker_not_loaded")]
            arm = body["arm"]
            payload, _, _ = integrated._validate_request(body["payload"])
            encoded: list[dict[str, Any]] = []

            def collect_encoded(packet: dict[str, Any]) -> None:
                wire_packet = _wire_packet(packet, request_id)
                _validate_packet(wire_packet, "complete", request_id)
                encoded.append(wire_packet)
                if on_encoded is not None:
                    on_encoded(wire_packet)

            try:
                completed = await engine._process_rpc(
                    "complete",
                    {"arm": arm, "payload": payload},
                    "completed",
                    engine.rpc_timeout_seconds,
                    on_encoded=collect_encoded,
                )
            except integrated._ProcessRPCFailure as exc:
                category = await _poison_server_engine(engine, exc.category)
                failure = _failure_packet(
                    request_id,
                    category,
                    generated_token_ids=exc.generated_token_ids,
                    raw_model_response=exc.raw_model_response,
                )
                return encoded + [failure] if on_encoded is None else [failure]
            if len(encoded) != 1:
                raise integrated._ProcessRPCFailure("worker_protocol_failure")
            completed_packet = _wire_packet(completed, request_id)
            packets = (
                [completed_packet]
                if on_encoded is not None
                else [encoded[0], completed_packet]
            )
            for packet in packets:
                _validate_packet(packet, "complete", request_id)
            return packets

        if operation == "verify_final":
            if not engine._worker_loaded or engine._baseline_identity is None:
                return [_failure_packet(request_id, "final_verification_without_load")]
            verified = await engine._process_rpc(
                "verify_final",
                {},
                "verified",
                engine.rpc_timeout_seconds,
            )
            identity = _server_identity(engine, verified.get("identity"))
            if identity != engine._baseline_identity:
                raise integrated._StageFailure("final_integrity_failure")
            return [
                {"request_id": request_id, "kind": "verified", "identity": identity}
            ]

        if operation == "close":
            if engine._poisoned:
                stopped = await engine._terminate_worker_uncancellable()
            else:
                stopped = await engine._graceful_stop_worker()
            if not stopped:
                category = await _poison_server_engine(engine, "worker_kill_failure")
                return [_failure_packet(request_id, category)]
            engine._closed = True
            return [{"request_id": request_id, "kind": "closed"}]

        return [_failure_packet(request_id, "worker_protocol_failure")]
    except asyncio.CancelledError:
        await _poison_server_engine(engine, "cancelled")
        raise
    except integrated._ProcessRPCFailure as exc:
        category = await _poison_server_engine(engine, exc.category)
        return [
            _failure_packet(
                request_id,
                category,
                generated_token_ids=exc.generated_token_ids,
                raw_model_response=exc.raw_model_response,
            )
        ]
    except integrated.InferenceProviderError as exc:
        category = await _poison_server_engine(engine, exc.failure_category)
        return [_failure_packet(request_id, category)]
    except integrated._StageFailure as exc:
        category = await _poison_server_engine(engine, exc.category)
        return [_failure_packet(request_id, category)]
    except Exception:  # noqa: BLE001 - never return exception paths or secret-bearing text
        category = await _poison_server_engine(engine, "bridge_server_failure")
        return [_failure_packet(request_id, category)]


def _packet_list(value: Any, operation: str, request_id: int) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"packets"}:
        raise ValueError("Bridge response envelope is invalid")
    packets = value["packets"]
    if not isinstance(packets, list) or not 1 <= len(packets) <= 2:
        raise ValueError("Bridge response packet list is invalid")
    result = [_validate_packet(packet, operation, request_id) for packet in packets]
    kinds = [packet["kind"] for packet in result]
    expected = {
        "load": {("loaded",), ("failure",)},
        "complete": {("encoded", "completed"), ("encoded", "failure"), ("failure",)},
        "verify_final": {("verified",), ("failure",)},
        "close": {("closed",), ("failure",)},
    }
    if tuple(kinds) not in expected[operation]:
        raise ValueError("Bridge response packet sequence is invalid")
    return result


async def _post_rpc(
    client: httpx.AsyncClient,
    url: str,
    inference_key: str,
    request: dict[str, Any],
    timeout_seconds: float,
    *,
    on_packet: Any = None,
    on_transport: Any = None,
) -> list[dict[str, Any]]:
    raw_request = _json_bytes(request)
    started = time.monotonic()
    http_status = None
    content_type = None
    packets = None
    try:
        async with asyncio.timeout(timeout_seconds):
            async with client.stream(
                "POST",
                f"{url}/rpc",
                content=raw_request,
                headers={
                    "content-type": "application/json",
                    "accept": (
                        "application/x-ndjson"
                        if request["operation"] == "complete"
                        else "application/json"
                    ),
                    INFERENCE_KEY_HEADER: inference_key,
                },
            ) as response:
                http_status = response.status_code
                content_type = _safe_response_content_type(
                    response.headers.get("content-type")
                )
                if on_transport is not None:
                    on_transport(
                        _transport_metadata(
                            request["operation"],
                            request["request_id"],
                            http_status,
                            content_type,
                            started,
                        )
                    )
                if response.status_code != 200:
                    category = (
                        "bridge_authentication_failure"
                        if response.status_code == 401
                        else "bridge_redirect_rejected"
                        if 300 <= response.status_code < 400
                        else "bridge_request_rejected"
                        if 400 <= response.status_code < 500
                        else "bridge_http_failure"
                    )
                    raise _RemoteBridgeFailure(category)
                if request["operation"] == "complete":
                    if content_type != "application/x-ndjson":
                        raise _RemoteBridgeFailure("bridge_protocol_failure")

                    def forward_encoded(packet: dict[str, Any]) -> None:
                        if packet["kind"] == "encoded" and on_packet is not None:
                            on_packet(packet)

                    packets = await _read_ndjson_packets(
                        response, request["request_id"], on_packet=forward_encoded
                    )
                    _validate_complete_sequence(packets)
                elif content_type != "application/json":
                    raise _RemoteBridgeFailure("bridge_protocol_failure")
                else:
                    chunks = await _read_bounded_response(response)
                    payload = json.loads(b"".join(chunks).decode("utf-8"))
                    packets = _packet_list(
                        payload, request["operation"], request["request_id"]
                    )
    except (TimeoutError, httpx.TimeoutException):
        raise _RemoteBridgeFailure(_request_failure_kind(request["operation"])) from None
    except _RemoteBridgeFailure:
        raise
    except httpx.HTTPError:
        raise _RemoteBridgeFailure("bridge_transport_failure") from None
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise _RemoteBridgeFailure("bridge_protocol_failure") from None
    finally:
        if on_transport is not None:
            on_transport(
                _transport_metadata(
                    request["operation"],
                    request["request_id"],
                    http_status,
                    content_type,
                    started,
                )
            )
    if packets is None:
        raise _RemoteBridgeFailure("bridge_protocol_failure")
    if on_packet is not None:
        for packet in packets:
            if request["operation"] == "complete" and packet["kind"] == "encoded":
                continue
            on_packet(packet)
    return packets


class _RemoteBridgeFailure(Exception):
    def __init__(self, category: str):
        self.category = category


async def _read_bounded_response(response: httpx.Response) -> list[bytes]:
    chunks = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_BRIDGE_BODY_BYTES:
            raise _RemoteBridgeFailure("bridge_response_too_large")
        chunks.append(chunk)
    return chunks


async def _read_ndjson_packets(
    response: httpx.Response, request_id: int, *, on_packet: Any = None
) -> list[dict[str, Any]]:
    packets = []
    buffer = b""
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_BRIDGE_BODY_BYTES:
            raise _RemoteBridgeFailure("bridge_response_too_large")
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line:
                continue
            packet = _decode_stream_packet(line, request_id)
            packets.append(packet)
            if len(packets) > 2:
                raise _RemoteBridgeFailure("bridge_protocol_failure")
            _validate_complete_sequence(packets, allow_incomplete=True)
            if on_packet is not None:
                on_packet(packet)
    if buffer:
        packet = _decode_stream_packet(buffer, request_id)
        packets.append(packet)
        if len(packets) > 2:
            raise _RemoteBridgeFailure("bridge_protocol_failure")
        _validate_complete_sequence(packets, allow_incomplete=True)
        if on_packet is not None:
            on_packet(packet)
    if not packets:
        raise _RemoteBridgeFailure("bridge_protocol_failure")
    return packets


def _decode_stream_packet(line: bytes, request_id: int) -> dict[str, Any]:
    try:
        packet = json.loads(line.decode("utf-8"))
        return _validate_packet(packet, "complete", request_id)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise _RemoteBridgeFailure("bridge_protocol_failure") from None


def _validate_complete_sequence(
    packets: list[dict[str, Any]], *, allow_incomplete: bool = False
) -> None:
    kinds = [packet["kind"] for packet in packets]
    valid = (
        kinds == ["failure"]
        or kinds == ["encoded", "completed"]
        or kinds == ["encoded", "failure"]
        or (allow_incomplete and kinds == ["encoded"])
    )
    if not valid:
        raise _RemoteBridgeFailure("bridge_protocol_failure")


def _read_config(config: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "url",
        "inference_key",
        "checkpoint_manifest_sha256",
        "base_snapshot_inventory_sha256",
        "rpc_timeout_seconds",
        "load_timeout_seconds",
        "shutdown_timeout_seconds",
        "allow_loopback_http",
    }
    if not isinstance(config, Mapping) or set(config) != expected:
        raise ValueError("Remote inference worker config contains unsupported fields")
    checkpoint_pin = config["checkpoint_manifest_sha256"]
    base_pin = config["base_snapshot_inventory_sha256"]
    if not isinstance(checkpoint_pin, str) or not _SHA256_RE.fullmatch(checkpoint_pin):
        raise ValueError("A lowercase checkpoint manifest SHA-256 pin is required")
    if checkpoint_pin != PINNED_V17_MANIFEST_SHA256:
        raise ValueError("Remote inference worker requires the pinned v17 adapter manifest")
    if not isinstance(base_pin, str) or not _SHA256_RE.fullmatch(base_pin):
        raise ValueError("A lowercase base inventory SHA-256 pin is required")
    allow_loopback_http = config["allow_loopback_http"]
    if not isinstance(allow_loopback_http, bool):
        raise ValueError("Loopback HTTP permission must be explicit")
    url = _validated_url(config["url"], allow_test_http=allow_loopback_http)
    if allow_loopback_http:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Plain HTTP is allowed only for the pinned loopback host")
    return {
        "url": url,
        "inference_key": _validate_inference_key(config["inference_key"]),
        "checkpoint_manifest_sha256": checkpoint_pin,
        "base_snapshot_inventory_sha256": base_pin,
        "rpc_timeout_seconds": _bounded_timeout(
            config["rpc_timeout_seconds"], "rpc_timeout_seconds"
        ),
        "load_timeout_seconds": _bounded_timeout(
            config["load_timeout_seconds"], "load_timeout_seconds"
        ),
        "shutdown_timeout_seconds": _bounded_timeout(
            config["shutdown_timeout_seconds"], "shutdown_timeout_seconds"
        ),
        "allow_loopback_http": allow_loopback_http,
    }


async def _remote_worker_loop(connection: Any, config: dict[str, Any]) -> None:
    baseline_identity = None
    timeout = httpx.Timeout(integrated.MAX_PROCESS_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        while True:
            try:
                request = _receive(connection)
            except EOFError:
                break
            request_id = request.get("request_id")
            operation = request.get("operation")
            if (
                isinstance(request_id, bool)
                or not isinstance(request_id, int)
                or request_id < 1
            ):
                break
            if operation not in _OPERATIONS:
                _send(
                    connection,
                    _failure_packet(request_id, "worker_protocol_failure"),
                )
                break
            forwarded = []
            failure = None
            try:
                _, operation = _validate_rpc_request(request)
                if operation == "complete":
                    _reject_operational_secrets(request["payload"])
                deadline = (
                    config["load_timeout_seconds"]
                    if operation == "load"
                    else config["shutdown_timeout_seconds"]
                    if operation == "close"
                    else config["rpc_timeout_seconds"]
                )
                def forward(packet: dict[str, Any]) -> None:
                    nonlocal baseline_identity
                    if operation == "load" and packet["kind"] == "loaded":
                        identity = _validate_identity(packet.get("identity"))
                        if (
                            identity["checkpoint_manifest_sha256"]
                            != config["checkpoint_manifest_sha256"]
                            or identity["base_snapshot_inventory_sha256"]
                            != config["base_snapshot_inventory_sha256"]
                        ):
                            raise _RemoteBridgeFailure("model_identity_mismatch")
                        baseline_identity = identity
                    elif operation == "verify_final" and packet["kind"] == "verified":
                        if packet.get("identity") != baseline_identity:
                            raise _RemoteBridgeFailure("final_integrity_failure")
                    _send(connection, packet)
                    forwarded.append(packet)

                def observe(transport: dict[str, Any]) -> None:
                    _send(
                        connection,
                        {
                            "request_id": request_id,
                            "kind": "transport_observation",
                            "transport": transport,
                        },
                    )

                await _post_rpc(
                    client,
                    config["url"],
                    config["inference_key"],
                    request,
                    deadline,
                    on_packet=forward,
                    on_transport=observe,
                )
            except _RemoteBridgeFailure as exc:
                if not any(packet.get("kind") == "failure" for packet in forwarded):
                    failure = _failure_packet(request_id, exc.category)
            except ValueError:
                if not any(packet.get("kind") == "failure" for packet in forwarded):
                    failure = _failure_packet(request_id, "bridge_request_rejected")
            if failure is not None:
                _send(connection, failure)
                break
            if operation == "close":
                break


def remote_inference_worker(connection: Any, config: dict[str, Any]) -> None:
    """Forward bounded local worker RPC to one authenticated inference bridge."""
    try:
        validated = _read_config(config)
        asyncio.run(_remote_worker_loop(connection, validated))
    except (EOFError, BrokenPipeError, OSError, ValueError):
        pass
    finally:
        connection.close()


def inference_bridge_app(engine: PairedCompletionEngine, inference_key: str) -> FastAPI:
    """Expose only authenticated model-inference RPC over HTTPS."""
    expected_key = _validate_inference_key(inference_key)
    if (
        engine.fixture_backend
        or engine._process_worker_target is not None
        or engine._process_factory is not None
        or engine._process_context_factory is not None
    ):
        raise ValueError("Inference bridge requires the standard spawned model worker")
    if engine.checkpoint_manifest_sha256 != PINNED_V17_MANIFEST_SHA256:
        raise ValueError("Inference bridge requires the pinned v17 adapter manifest")
    lock = asyncio.Lock()
    bridge_state: dict[str, Any] = {"runtime": None}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            try:
                stopped = await engine._graceful_stop_worker()
            except Exception:  # noqa: BLE001 - do not log runtime details
                stopped = False
            if not stopped:
                await engine._terminate_worker_uncancellable()

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.post("/rpc")
    async def rpc(request: Request) -> Response:
        supplied_key = request.headers.get(INFERENCE_KEY_HEADER, "")
        if not secrets.compare_digest(supplied_key, expected_key):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        try:
            body = await _read_request_body(request)
            request_id, operation = _validate_rpc_request(body)
        except ValueError as exc:
            status = 413 if "bounded frame size" in str(exc) else 400
            return JSONResponse({"detail": str(exc)}, status_code=status)

        if operation == "complete":
            return StreamingResponse(
                _stream_completion(engine, lock, request_id, body),
                media_type="application/x-ndjson",
                headers={
                    "cache-control": "no-store",
                    "x-accel-buffering": "no",
                },
            )

        packets = await _run_control_rpc(
            engine,
            lock,
            request_id,
            operation,
            body,
        )
        if packets[0]["kind"] in {"loaded", "verified"}:
            try:
                if bridge_state["runtime"] is None:
                    bridge_state["runtime"] = _server_runtime_metadata()
                identity = dict(packets[0]["identity"])
                identity["runtime"] = bridge_state["runtime"]
                packets[0]["identity"] = _validate_identity(identity)
            except integrated._StageFailure as exc:
                category = await _poison_server_engine(engine, exc.category)
                packets = [_failure_packet(request_id, category)]
            except Exception:  # noqa: BLE001 - metadata failure is fail-closed
                category = await _poison_server_engine(
                    engine, "runtime_metadata_failure"
                )
                packets = [_failure_packet(request_id, category)]
        try:
            raw = _json_bytes({"packets": packets})
        except ValueError:
            raw = _json_bytes(
                {
                    "packets": [
                        _failure_packet(request_id, "bridge_response_too_large")
                    ]
                }
            )
        return Response(content=raw, media_type="application/json")

    return app


async def _run_control_rpc(
    engine: Any,
    lock: asyncio.Lock,
    request_id: int,
    operation: str,
    body: Mapping[str, Any],
) -> list[dict[str, Any]]:
    timeout_seconds = (
        engine.load_timeout_seconds
        if operation == "load"
        else engine.shutdown_timeout_seconds * 2
        if operation == "close"
        else engine.rpc_timeout_seconds
    )

    try:
        async with asyncio.timeout(timeout_seconds):
            async with lock:
                return await _handle_server_rpc(engine, request_id, operation, body)
    except TimeoutError:
        category = "load_timeout" if operation == "load" else "rpc_timeout"
        category = await _poison_server_engine(engine, category)
        return [_failure_packet(request_id, category)]


async def _stream_completion(
    engine: Any,
    lock: asyncio.Lock,
    request_id: int,
    body: Mapping[str, Any],
):
    queue: asyncio.Queue[Any] = asyncio.Queue()
    finished = object()

    def emit(packet: dict[str, Any]) -> None:
        queue.put_nowait(packet)

    async def run() -> None:
        try:
            async with lock:
                packets = await _handle_server_rpc(
                    engine,
                    request_id,
                    "complete",
                    body,
                    on_encoded=emit,
                )
            for packet in packets:
                queue.put_nowait(packet)
        finally:
            queue.put_nowait(finished)

    operation = asyncio.create_task(run())
    try:
        while True:
            item = await queue.get()
            if item is finished:
                break
            yield _json_bytes(item) + b"\n"
        await operation
    except asyncio.CancelledError:
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        raise
    finally:
        if not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


def create_remote_engine(
    *,
    checkpoint: str,
    checkpoint_manifest_sha256: str,
    base_snapshot: str,
    base_snapshot_inventory_sha256: str,
    journal_path: str,
    url: str,
    inference_key: str,
    rpc_timeout_seconds: float = integrated.DEFAULT_RPC_TIMEOUT_SECONDS,
    load_timeout_seconds: float = integrated.DEFAULT_LOAD_TIMEOUT_SECONDS,
    shutdown_timeout_seconds: float = integrated.PROCESS_STOP_TIMEOUT_SECONDS,
) -> PairedCompletionEngine:
    """Create a local journal-owning engine that sends model RPC to a remote bridge."""
    config = _read_config(
        {
            "url": url,
            "inference_key": inference_key,
            "checkpoint_manifest_sha256": checkpoint_manifest_sha256,
            "base_snapshot_inventory_sha256": base_snapshot_inventory_sha256,
            "rpc_timeout_seconds": rpc_timeout_seconds,
            "load_timeout_seconds": load_timeout_seconds,
            "shutdown_timeout_seconds": shutdown_timeout_seconds,
            "allow_loopback_http": False,
        }
    )
    return PairedCompletionEngine(
        checkpoint=checkpoint,
        checkpoint_manifest_sha256=checkpoint_manifest_sha256,
        base_snapshot=base_snapshot,
        base_snapshot_inventory_sha256=base_snapshot_inventory_sha256,
        journal_path=journal_path,
        rpc_timeout_seconds=config["rpc_timeout_seconds"],
        load_timeout_seconds=config["load_timeout_seconds"],
        shutdown_timeout_seconds=config["shutdown_timeout_seconds"],
        process_worker_target=remote_inference_worker,
        process_worker_config=config,
    )


def create_loopback_engine(
    *,
    checkpoint: str,
    checkpoint_manifest_sha256: str,
    base_snapshot: str,
    base_snapshot_inventory_sha256: str,
    journal_path: str,
    inference_key: str,
    port: int = 18765,
    rpc_timeout_seconds: float = integrated.DEFAULT_RPC_TIMEOUT_SECONDS,
    load_timeout_seconds: float = integrated.DEFAULT_LOAD_TIMEOUT_SECONDS,
    shutdown_timeout_seconds: float = integrated.PROCESS_STOP_TIMEOUT_SECONDS,
) -> PairedCompletionEngine:
    """Create the same authenticated worker bound only to server-local loopback."""
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("Loopback inference port must be between 1024 and 65535")
    config = _read_config(
        {
            "url": f"http://127.0.0.1:{port}",
            "inference_key": inference_key,
            "checkpoint_manifest_sha256": checkpoint_manifest_sha256,
            "base_snapshot_inventory_sha256": base_snapshot_inventory_sha256,
            "rpc_timeout_seconds": rpc_timeout_seconds,
            "load_timeout_seconds": load_timeout_seconds,
            "shutdown_timeout_seconds": shutdown_timeout_seconds,
            "allow_loopback_http": True,
        }
    )
    return PairedCompletionEngine(
        checkpoint=checkpoint,
        checkpoint_manifest_sha256=checkpoint_manifest_sha256,
        base_snapshot=base_snapshot,
        base_snapshot_inventory_sha256=base_snapshot_inventory_sha256,
        journal_path=journal_path,
        rpc_timeout_seconds=config["rpc_timeout_seconds"],
        load_timeout_seconds=config["load_timeout_seconds"],
        shutdown_timeout_seconds=config["shutdown_timeout_seconds"],
        process_worker_target=remote_inference_worker,
        process_worker_config=config,
    )
