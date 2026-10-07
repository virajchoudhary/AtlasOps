"""Serialized raw Base/SFT completions for the integrated local evaluator."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import multiprocessing
import os
import queue
import re
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench import base_sft_validation as validation

ARMS = validation.ARMS
ROLES = ("triage", "diagnosis", "remediation", "comms")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GENERATION_KEYS = ("seed", "temperature", "top_p", "max_new_tokens", "max_tokens", "do_sample")
DEFAULT_RPC_TIMEOUT_SECONDS = 600.0
DEFAULT_LOAD_TIMEOUT_SECONDS = 600.0
MAX_PROCESS_TIMEOUT_SECONDS = 600.0
PROCESS_POLL_INTERVAL_SECONDS = 0.02
PROCESS_STOP_TIMEOUT_SECONDS = 1.0
STARTUP_CLEANUP_GRACE_SECONDS = 1.0
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RPC_MESSAGE_BYTES = 5 * 1024 * 1024
_TRANSPORT_OPERATIONS = frozenset({"load", "complete", "verify_final", "close"})
_SAFE_TRANSPORT_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/problem+json",
        "application/x-ndjson",
        "text/html",
        "text/plain",
    }
)
EFFECTIVE_GENERATION_CONFIG = {
    key: validation.EVALUATION_CONFIG[key]
    for key in ("seed", "temperature", "top_p", "max_new_tokens", "do_sample")
}
ACTION_INSTRUCTION = (
    "Return exactly one JSON object with keys tool, arguments, and "
    "agent_claimed_resolved. The tool and arguments are the exact single action "
    "to submit to the existing safety and approval gate. Do not include prose, "
    "tool-call wrappers, or an actions list."
)


def _json_copy(value: Any, label: str) -> Any:
    def check_keys(item: Any) -> None:
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ValueError(f"{label} object keys must be strings")
            for child in item.values():
                check_keys(child)
        elif isinstance(item, list):
            for child in item:
                check_keys(child)

    try:
        check_keys(value)
        return json.loads(
            json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite JSON data") from exc


def _reject_sensitive_keys(value: Any) -> None:
    forbidden = {"approval", "runtimecontrol", "apikey", "authorization"}
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized in forbidden or any(
                marker in normalized for marker in ("password", "secret", "credential")
            ):
                raise ValueError("Request contains a forbidden control or secret field")
            _reject_sensitive_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_keys(child)


def _strip_untrusted_controls(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_untrusted_controls(child)
            for key, child in value.items()
            if re.sub(r"[^a-z0-9]", "", str(key).casefold())
            not in {"approval", "runtimecontrol"}
        }
    if isinstance(value, list):
        return [_strip_untrusted_controls(child) for child in value]
    return value


def _inventory_identity(inventory: Mapping[str, Any]) -> dict[str, Any]:
    files = inventory.get("files")
    weights = inventory.get("weight_metadata")
    if (
        not isinstance(files, Mapping)
        or not files
        or not isinstance(weights, Mapping)
        or not isinstance(inventory.get("snapshot_dir"), str)
    ):
        raise ValueError("Base inventory lacks its pinned file identity")
    return {
        "repository": inventory.get("repository"),
        "revision": inventory.get("revision"),
        "snapshot_dir": str(Path(inventory["snapshot_dir"]).resolve(strict=False)),
        "weight_metadata_sha256": weights.get("sha256"),
        "tokenizer_manifest_sha256": inventory.get("tokenizer_manifest_sha256"),
        "files": dict(sorted(files.items())),
        "total_files": inventory.get("total_files"),
        "total_bytes": inventory.get("total_bytes"),
    }


def base_snapshot_inventory_sha256(inventory: Mapping[str, Any]) -> str:
    """Fingerprint stable fields from a fresh collect_model_inventory() result."""
    if not isinstance(inventory, Mapping):
        raise TypeError("Base inventory must be an object")
    return validation._canonical_sha256(_inventory_identity(inventory))


def _validate_request(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise TypeError("Completion request must be an object")
    request = _json_copy(dict(payload), "Completion request")
    allowed = {"messages", "tools", "model", "tool_choice", *GENERATION_KEYS}
    if set(request).difference(allowed):
        raise ValueError("Completion request contains unsupported fields")
    if "model" in request and not isinstance(request["model"], str):
        raise ValueError("Requested model must be text")
    if "tool_choice" in request and not (
        isinstance(request["tool_choice"], (str, dict))
        and (
            not isinstance(request["tool_choice"], str)
            or request["tool_choice"] in {"auto", "none", "required"}
        )
    ):
        raise ValueError("tool_choice must use a supported chat-completion shape")
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Completion request requires a non-empty messages list")
    for message in messages:
        if not isinstance(message, dict) or set(message).difference(
            {"role", "content", "name", "tool_call_id", "tool_calls"}
        ):
            raise ValueError("Each message contains unsupported fields")
        role = message.get("role")
        calls = message.get("tool_calls")
        content = message.get("content")
        if role not in {"system", "developer", "user", "assistant", "tool"}:
            raise ValueError("Each message requires a supported role")
        if role == "assistant" and content is None:
            if not isinstance(calls, list) or not calls:
                raise ValueError("Null assistant content requires tool-call history")
        elif not isinstance(content, str):
            raise ValueError("Message content must be text")
        if "name" in message and not isinstance(message["name"], str):
            raise ValueError("Message name must be text")
        if "tool_call_id" in message and (
            role != "tool" or not isinstance(message["tool_call_id"], str)
        ):
            raise ValueError("tool_call_id is valid only as tool-message text metadata")
        if "tool_calls" in message and (
            role != "assistant"
            or not isinstance(calls, list)
            or any(not isinstance(call, dict) for call in calls)
        ):
            raise ValueError("tool_calls must be an assistant tool-call list")
        _reject_sensitive_keys(message)
        if isinstance(content, str) and content.lstrip().startswith(("{", "[")):
            try:
                embedded = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                pass
            else:
                _reject_sensitive_keys(embedded)
    if "tools" in request:
        tools = request["tools"]
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            raise ValueError("tools must be a list of JSON schema objects")
        _reject_sensitive_keys(tools)

    requested = {key: request[key] for key in GENERATION_KEYS if key in request}
    for key in ("temperature", "top_p"):
        value = requested.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{key} must be finite")
    seed = requested.get("seed", EFFECTIVE_GENERATION_CONFIG["seed"])
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("Requested seed must be a non-negative integer")
    for key in ("max_new_tokens", "max_tokens"):
        value = requested.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value != EFFECTIVE_GENERATION_CONFIG["max_new_tokens"]
        ):
            raise ValueError("Requested token budget differs from the frozen Validation budget")
    if requested.get("top_p", 1.0) != EFFECTIVE_GENERATION_CONFIG["top_p"]:
        raise ValueError("Requested top_p differs from the frozen Validation configuration")
    if requested.get("do_sample", False) is not EFFECTIVE_GENERATION_CONFIG["do_sample"]:
        raise ValueError("Sampling is disabled for the frozen Validation configuration")
    if len(
        json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ) > MAX_REQUEST_BYTES:
        raise ValueError("Completion request exceeds the local RPC size limit")
    effective = dict(EFFECTIVE_GENERATION_CONFIG)
    return request, requested, effective


def _validated_evidence_context(
    arm: str, value: Mapping[str, Any] | None
) -> dict[str, str] | None:
    if value is None:
        return None
    copied = _json_copy(dict(value), "Evidence context")
    if set(copied) != {"run_id", "incident_id", "arm", "scenario_id"}:
        raise ValueError("Evidence context must identify run, incident, arm and scenario")
    if copied["arm"] != arm:
        raise ValueError("Evidence context arm differs from the provider arm")
    for key, item in copied.items():
        if (
            not isinstance(item, str)
            or not item
            or len(item) > 128
            or any(ord(char) < 32 for char in item)
        ):
            raise ValueError(f"Evidence context {key} must be bounded text")
    return copied


class InferenceProviderError(RuntimeError):
    def __init__(self, failure_category: str):
        self.failure_category = failure_category
        super().__init__(f"paired inference failed: {failure_category}")


class _StageFailure(Exception):
    def __init__(self, category: str):
        self.category = category


class _ProcessRPCFailure(Exception):
    def __init__(
        self,
        category: str,
        generated_token_ids: list[int] | None = None,
        raw_model_response: str | None = None,
    ):
        self.category = category
        self.generated_token_ids = generated_token_ids
        self.raw_model_response = raw_model_response


def _validated_transport_observation(
    message: Any, operation: str, request_id: int
) -> dict[str, Any]:
    if (
        not isinstance(message, dict)
        or set(message) != {"request_id", "kind", "transport"}
        or type(message.get("request_id")) is not int
        or message.get("request_id") != request_id
        or message.get("kind") != "transport_observation"
    ):
        raise _ProcessRPCFailure("worker_protocol_failure")
    transport = message["transport"]
    if (
        not isinstance(transport, dict)
        or set(transport)
        != {
            "operation",
            "request_id",
            "http_status",
            "content_type",
            "elapsed_seconds",
        }
        or transport.get("operation") != operation
        or operation not in _TRANSPORT_OPERATIONS
        or type(transport.get("request_id")) is not int
        or transport.get("request_id") != request_id
    ):
        raise _ProcessRPCFailure("worker_protocol_failure")
    status = transport["http_status"]
    if status is not None and (
        type(status) is not int or not 100 <= status <= 599
    ):
        raise _ProcessRPCFailure("worker_protocol_failure")
    content_type = transport["content_type"]
    if content_type is not None and content_type not in _SAFE_TRANSPORT_CONTENT_TYPES:
        raise _ProcessRPCFailure("worker_protocol_failure")
    elapsed = transport["elapsed_seconds"]
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(elapsed)
        or not 0 <= elapsed <= MAX_PROCESS_TIMEOUT_SECONDS
    ):
        raise _ProcessRPCFailure("worker_protocol_failure")
    return {
        "request_id": request_id,
        "operation": operation,
        "http_status": status,
        "content_type": content_type,
        "elapsed_seconds": float(elapsed),
    }


class PairedCompletionEngine:
    """One model shared by explicitly arm-bound providers and a durable journal."""

    def __init__(
        self,
        *,
        checkpoint: str | Path,
        checkpoint_manifest_sha256: str,
        base_snapshot: str | Path,
        base_snapshot_inventory_sha256: str,
        journal_path: str | Path | None,
        rpc_timeout_seconds: float = DEFAULT_RPC_TIMEOUT_SECONDS,
        load_timeout_seconds: float = DEFAULT_LOAD_TIMEOUT_SECONDS,
        shutdown_timeout_seconds: float = PROCESS_STOP_TIMEOUT_SECONDS,
        fixture_backend: bool = False,
        process_worker_target: Any = None,
        process_worker_config: Mapping[str, Any] | None = None,
        process_factory: Any = None,
        process_context_factory: Any = None,
    ) -> None:
        if not isinstance(checkpoint_manifest_sha256, str) or not SHA256_RE.fullmatch(
            checkpoint_manifest_sha256
        ):
            raise ValueError("A lowercase checkpoint manifest SHA-256 pin is required")
        if not isinstance(base_snapshot_inventory_sha256, str) or not SHA256_RE.fullmatch(
            base_snapshot_inventory_sha256
        ):
            raise ValueError("A lowercase base inventory SHA-256 pin is required")
        for name, value in (
            ("rpc_timeout_seconds", rpc_timeout_seconds),
            ("load_timeout_seconds", load_timeout_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 < value <= MAX_PROCESS_TIMEOUT_SECONDS
            ):
                raise ValueError(f"{name} must be finite, positive and at most 600 seconds")
        if (
            isinstance(shutdown_timeout_seconds, bool)
            or not isinstance(shutdown_timeout_seconds, (int, float))
            or not math.isfinite(shutdown_timeout_seconds)
            or not 0 < shutdown_timeout_seconds <= MAX_PROCESS_TIMEOUT_SECONDS
        ):
            raise ValueError("shutdown timeout must be finite and bounded")
        if not isinstance(fixture_backend, bool):
            raise TypeError("fixture_backend must be a boolean")
        if fixture_backend and (
            process_worker_target is not None
            or process_factory is not None
            or process_context_factory is not None
        ):
            raise ValueError("Inline fixture mode cannot select a process worker")
        self.checkpoint = Path(checkpoint)
        self.base_snapshot = Path(base_snapshot)
        self.checkpoint_manifest_sha256 = checkpoint_manifest_sha256
        self.base_snapshot_inventory_sha256 = base_snapshot_inventory_sha256
        self.journal_path = Path(journal_path) if journal_path is not None else None
        self.rpc_timeout_seconds = float(rpc_timeout_seconds)
        self.load_timeout_seconds = float(load_timeout_seconds)
        self.shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self.fixture_backend = fixture_backend
        self._process_worker_target = process_worker_target
        self._process_factory = process_factory
        self._process_context_factory = process_context_factory
        self._process_factory = process_factory
        self._process_worker_config = _json_copy(
            dict(process_worker_config or {}), "Process worker config"
        )
        self._runner = validation.LocalPairedInference(self.base_snapshot, self.checkpoint)
        self._lock = asyncio.Lock()
        self._episode_lock = asyncio.Lock()
        self._journal: Any = None
        self._loaded = False
        self._baseline_identity: dict[str, str] | None = None
        self._process: Any = None
        self._connection: Any = None
        self._process_started = threading.Event()
        self._process_start_thread: threading.Thread | None = None
        self._startup_unconfirmed = False
        self._process_start_timed_out = threading.Event()
        self._process_start_finished = threading.Event()
        self._startup_state_lock = threading.Lock()
        self._responses: queue.Queue[Any] | None = None
        self._io_threads: list[threading.Thread] = []
        self._reader_failed = threading.Event()
        self._worker_loaded = False
        self._next_request_id = 0
        self._poisoned = False
        self._poison_reason: str | None = None
        self._closed = False

    @property
    def worker_pid(self) -> int | None:
        if self._process is None or not self._process_started.is_set():
            return None
        return self._process.pid

    @property
    def worker_alive(self) -> bool:
        return bool(
            self._process is not None
            and self._process_started.is_set()
            and self._process.is_alive()
        )

    def provider(
        self,
        arm: str,
        *,
        evidence_context: Mapping[str, Any] | None = None,
    ) -> RoleCompletionProvider:
        if arm not in ARMS:
            raise ValueError("Provider arm must be explicitly bound to base or sft")
        context = _validated_evidence_context(arm, evidence_context)
        return RoleCompletionProvider(self, arm, context)

    def _open_journal(self) -> None:
        path = self.journal_path
        if path is None:
            raise ValueError("This worker instance does not own a journal")
        if not path.is_absolute() or not path.parent.is_dir():
            raise ValueError("Journal must be an absolute path with an existing parent")
        target = path.parent.resolve() / path.name
        protected = (
            Path(__file__).resolve().parents[1],
            self.checkpoint.resolve(strict=False),
            self.base_snapshot.resolve(strict=False),
        )
        if any(target.is_relative_to(item) for item in protected):
            raise ValueError("Journal must be outside source and model directories")
        if os.path.lexists(target):
            raise FileExistsError("Journal already exists; refusing to overwrite")
        self._journal = target.open("x", encoding="utf-8", newline="\n")

    def _write(self, row: dict[str, Any]) -> None:
        self._journal.write(json.dumps(row, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n")
        self._journal.flush()
        os.fsync(self._journal.fileno())

    async def _start_worker(self, timeout: float) -> None:
        if self._process is not None:
            if not self._process_started.is_set():
                raise _ProcessRPCFailure("worker_startup_unconfirmed")
            if self._process.is_alive():
                return
            raise _ProcessRPCFailure("worker_process_exit")
        from bench.integrated_inference_process import local_inference_worker

        context = (
            self._process_context_factory()
            if self._process_context_factory is not None
            else multiprocessing.get_context("spawn")
        )
        parent, child = context.Pipe(duplex=True)
        target = self._process_worker_target or local_inference_worker
        config = (
            {
                "checkpoint": str(self.checkpoint),
                "checkpoint_manifest_sha256": self.checkpoint_manifest_sha256,
                "base_snapshot": str(self.base_snapshot),
                "base_snapshot_inventory_sha256": self.base_snapshot_inventory_sha256,
            }
            if self._process_worker_target is None
            else self._process_worker_config
        )
        factory = self._process_factory or context.Process
        process = factory(target=target, args=(child, config), daemon=True)
        self._process = process
        self._connection = parent
        self._process_started.clear()
        self._process_start_timed_out.clear()
        self._process_start_finished.clear()
        self._startup_unconfirmed = False
        self._reader_failed.clear()
        self._responses = queue.Queue(maxsize=8)
        start_result: queue.Queue[tuple[str, bool | None]] = queue.Queue(maxsize=1)
        with self._startup_state_lock:
            self._process_start_timed_out.clear()
        deadline = asyncio.get_running_loop().time() + timeout

        def start_process() -> None:
            try:
                process.start()
            except Exception:  # noqa: BLE001 - hide platform-specific startup details
                child.close()
                parent.close()
                start_result.put(("start_failure", None))
                self._process_start_finished.set()
                return
            child.close()
            self._process_started.set()
            with self._startup_state_lock:
                late = self._process_start_timed_out.is_set()
                if not late:
                    reader = threading.Thread(
                        target=self._read_worker_messages,
                        args=(parent, self._responses, self._reader_failed),
                        daemon=True,
                    )
                    self._io_threads = [reader]
                    reader.start()
                    start_result.put(("started", True))
                    self._process_start_finished.set()
                    return
            stopped = self._terminate_started_process(process)
            if stopped:
                parent.close()
            start_result.put(("late_cleaned", stopped))
            self._process_start_finished.set()

        start_thread = threading.Thread(target=start_process, daemon=True)
        self._process_start_thread = start_thread
        try:
            start_thread.start()
        except RuntimeError:
            parent.close()
            child.close()
            self._process = None
            self._connection = None
            raise _ProcessRPCFailure("worker_start_failure") from None

        loop = asyncio.get_running_loop()
        while True:
            try:
                outcome, stopped = start_result.get_nowait()
            except queue.Empty:
                outcome, stopped = "", None
            if outcome == "start_failure":
                self._process = None
                self._connection = None
                raise _ProcessRPCFailure("worker_start_failure")
            if outcome == "late_cleaned":
                if stopped:
                    raise _ProcessRPCFailure("load_timeout")
                self._startup_unconfirmed = True
                raise _ProcessRPCFailure("worker_startup_unconfirmed")
            if outcome == "started":
                if loop.time() < deadline:
                    return
                with self._startup_state_lock:
                    self._process_start_timed_out.set()
                raise _ProcessRPCFailure("load_timeout")

            remaining = deadline - loop.time()
            if remaining <= 0:
                with self._startup_state_lock:
                    self._process_start_timed_out.set()
                cleanup_deadline = loop.time() + STARTUP_CLEANUP_GRACE_SECONDS
                while loop.time() < cleanup_deadline:
                    try:
                        outcome, stopped = start_result.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(
                            min(
                                PROCESS_POLL_INTERVAL_SECONDS,
                                cleanup_deadline - loop.time(),
                            )
                        )
                        continue
                    if outcome == "late_cleaned" and stopped:
                        raise _ProcessRPCFailure("load_timeout")
                    if outcome == "start_failure":
                        self._process = None
                        self._connection = None
                        raise _ProcessRPCFailure("worker_start_failure")
                    if outcome == "started":
                        raise _ProcessRPCFailure("load_timeout")
                    self._startup_unconfirmed = True
                    raise _ProcessRPCFailure("worker_startup_unconfirmed")
                self._startup_unconfirmed = True
                raise _ProcessRPCFailure("worker_startup_unconfirmed")
            await asyncio.sleep(min(PROCESS_POLL_INTERVAL_SECONDS, remaining))

    @staticmethod
    def _terminate_started_process(process: Any) -> bool:
        try:
            process.terminate()
            process.join(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
            return not process.is_alive()
        except (AttributeError, OSError, ValueError, AssertionError):
            return False

    @staticmethod
    def _read_worker_messages(
        connection: Any,
        responses: queue.Queue[Any],
        failed: threading.Event,
    ) -> None:
        while True:
            try:
                raw = connection.recv_bytes(MAX_RPC_MESSAGE_BYTES)
                message = json.loads(raw.decode("utf-8"))
                responses.put_nowait(message)
            except (EOFError, OSError, ValueError, queue.Full):
                failed.set()
                return

    @staticmethod
    def _send_worker_message(connection: Any, message: dict[str, Any]) -> None:
        raw = json.dumps(
            message, sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if len(raw) > MAX_RPC_MESSAGE_BYTES:
            raise ValueError("worker RPC message exceeds the bounded frame size")
        connection.send_bytes(raw)

    def _write_worker_message(
        self, message: dict[str, Any], result: queue.Queue[str | None]
    ) -> None:
        try:
            self._send_worker_message(self._connection, message)
            result.put_nowait(None)
        except (BrokenPipeError, EOFError, OSError, ValueError):
            result.put_nowait("worker_process_exit")

    async def _wait_process_exit(self, process: Any, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while process.is_alive():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(PROCESS_POLL_INTERVAL_SECONDS, remaining))
        process.join(timeout=0)
        return True

    def _close_process_connection(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def _join_io_threads_bounded(self) -> bool:
        deadline = time.monotonic() + PROCESS_STOP_TIMEOUT_SECONDS
        for thread in list(self._io_threads):
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        alive = any(thread.is_alive() for thread in self._io_threads)
        if not alive:
            self._io_threads.clear()
            self._responses = None
        return not alive

    async def _terminate_worker(self) -> bool:
        process = self._process
        if process is None:
            return True
        if not self._process_started.is_set():
            if self._startup_unconfirmed or (
                self._process_start_thread is not None
                and self._process_start_thread.is_alive()
            ):
                return False
            self._close_process_connection()
            return self._join_io_threads_bounded()
        if not process.is_alive():
            process.join(timeout=0)
            self._close_process_connection()
            self._worker_loaded = False
            return self._join_io_threads_bounded()
        try:
            process.terminate()
        except (OSError, ValueError):
            pass
        if await self._wait_process_exit(process, PROCESS_STOP_TIMEOUT_SECONDS):
            self._close_process_connection()
            self._worker_loaded = False
            return self._join_io_threads_bounded()
        try:
            process.kill()
        except (AttributeError, OSError, ValueError):
            pass
        if await self._wait_process_exit(process, PROCESS_STOP_TIMEOUT_SECONDS):
            self._close_process_connection()
            self._worker_loaded = False
            return self._join_io_threads_bounded()
        return False

    async def _terminate_worker_uncancellable(self) -> bool:
        task = asyncio.create_task(self._terminate_worker())
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        return task.result()

    async def _cancel_startup(self) -> bool:
        with self._startup_state_lock:
            self._process_start_timed_out.set()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STARTUP_CLEANUP_GRACE_SECONDS
        while not self._process_start_finished.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                self._startup_unconfirmed = True
                return False
            await asyncio.sleep(min(PROCESS_POLL_INTERVAL_SECONDS, remaining))
        if not self._process_started.is_set():
            self._process = None
            self._connection = None
            return True
        return await self._terminate_worker()

    async def _cancel_startup_uncancellable(self) -> bool:
        task = asyncio.create_task(self._cancel_startup())
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        return task.result()

    async def _graceful_stop_worker(self) -> bool:
        if self._process is None:
            return True
        if not self._process_started.is_set():
            if self._startup_unconfirmed:
                return False
            return await self._terminate_worker()
        if not self._process.is_alive():
            self._process.join(timeout=0)
            self._close_process_connection()
            self._worker_loaded = False
            return self._join_io_threads_bounded()
        try:
            await self._process_rpc(
                "close", {}, "closed", self.shutdown_timeout_seconds
            )
        except _ProcessRPCFailure:
            return await self._terminate_worker()
        if await self._wait_process_exit(
            self._process, self.shutdown_timeout_seconds
        ):
            self._close_process_connection()
            self._worker_loaded = False
            return self._join_io_threads_bounded()
        return await self._terminate_worker()

    async def _process_rpc(
        self,
        operation: str,
        payload: dict[str, Any],
        expected_kind: str,
        timeout: float,
        *,
        on_encoded: Any = None,
    ) -> dict[str, Any]:
        if operation == "close" and self._process is None:
            raise _ProcessRPCFailure("worker_not_started")
        if self._process is None or self._connection is None:
            raise _ProcessRPCFailure("worker_not_started")
        if self._responses is None:
            raise _ProcessRPCFailure("worker_protocol_failure")
        self._next_request_id += 1
        request_id = self._next_request_id
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + timeout
        send_result: queue.Queue[str | None] = queue.Queue(maxsize=1)
        writer = threading.Thread(
            target=self._write_worker_message,
            args=(
                {"request_id": request_id, "operation": operation, **payload},
                send_result,
            ),
            daemon=True,
        )
        self._io_threads.append(writer)
        writer.start()
        sent = False
        transport_observed = False
        while True:
            if not sent:
                try:
                    send_error = send_result.get_nowait()
                    sent = True
                    writer.join(timeout=0)
                    if not writer.is_alive() and writer in self._io_threads:
                        self._io_threads.remove(writer)
                    if send_error is not None:
                        raise _ProcessRPCFailure(send_error)
                except queue.Empty:
                    pass
            try:
                message = self._responses.get_nowait() if sent else None
            except queue.Empty:
                message = None
            if message is None and sent and self._reader_failed.is_set():
                raise _ProcessRPCFailure("worker_process_exit")
            if message is not None:
                if message.get("request_id") != request_id:
                    raise _ProcessRPCFailure("worker_protocol_failure")
                kind = message.get("kind")
                if kind == "transport_observation":
                    observation = _validated_transport_observation(
                        message, operation, request_id
                    )
                    try:
                        if self._journal is None:
                            raise OSError
                        self._write(
                            {
                                "schema_version": "atlasops-integrated-inference-v1",
                                "record": "transport_observation",
                                **observation,
                                "recorded_at_utc": datetime.now(UTC).isoformat(),
                            }
                        )
                    except Exception:  # noqa: BLE001 - never include journal exception text
                        raise _ProcessRPCFailure("journal_failure") from None
                    transport_observed = True
                    continue
                if kind == "encoded" and operation == "complete":
                    if on_encoded is not None:
                        on_encoded(message)
                    if loop.time() >= deadline:
                        raise _ProcessRPCFailure("rpc_timeout")
                    continue
                if kind == "failure":
                    if loop.time() >= deadline:
                        raise _ProcessRPCFailure(
                            "load_timeout" if operation == "load" else "rpc_timeout",
                            message.get("generated_token_ids"),
                            message.get("raw_model_response"),
                        )
                    raise _ProcessRPCFailure(
                        message.get("failure_category", "worker_failure"),
                        message.get("generated_token_ids"),
                        message.get("raw_model_response"),
                    )
                if loop.time() >= deadline:
                    raise _ProcessRPCFailure(
                        "load_timeout" if operation == "load" else "rpc_timeout",
                        message.get("generated_token_ids"),
                        message.get("raw_model_response"),
                    )
                if kind != expected_kind:
                    raise _ProcessRPCFailure("worker_protocol_failure")
                return message
            if not self._process.is_alive():
                self._process.join(timeout=0)
                raise _ProcessRPCFailure("worker_process_exit")
            remaining = deadline - loop.time()
            if remaining <= 0:
                if "url" in (self._process_worker_config or {}) and not transport_observed:
                    try:
                        self._write({
                            "schema_version": "atlasops-integrated-inference-v1",
                            "record": "transport_observation",
                            "operation": operation,
                            "request_id": request_id,
                            "http_status": None,
                            "content_type": None,
                            "elapsed_seconds": min(
                                MAX_PROCESS_TIMEOUT_SECONDS, loop.time() - started,
                            ),
                            "recorded_at_utc": datetime.now(UTC).isoformat(),
                        })
                    except Exception:  # noqa: BLE001 - hide journal exception text
                        raise _ProcessRPCFailure("journal_failure") from None
                category = "load_timeout" if operation == "load" else "rpc_timeout"
                raise _ProcessRPCFailure(category)
            await asyncio.sleep(min(PROCESS_POLL_INTERVAL_SECONDS, remaining))

    @staticmethod
    def _validate_action_checkpoint(manifest: Mapping[str, Any]) -> None:
        dataset = manifest.get("dataset")
        counts = dataset.get("role_counts") if isinstance(dataset, Mapping) else None
        role_counts = (
            [counts.get(role) for role in ROLES]
            if isinstance(counts, Mapping)
            else []
        )
        if (
            manifest.get("role_filter") != "all"
            or any(
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 1
                for count in role_counts
            )
        ):
            raise ValueError("Checkpoint provenance does not establish all four agent roles")

    def _admit(self) -> dict[str, str]:
        try:
            evaluator, collector = validation._runtime_modules()
            manifest, manifest_sha256 = evaluator._load_checkpoint_manifest(self.checkpoint)
            if manifest_sha256 != self.checkpoint_manifest_sha256:
                raise ValueError
            self._validate_action_checkpoint(manifest)
            inventory = collector.collect_model_inventory(snapshot_dir=self.base_snapshot)
            validation._base_and_tokenizer_match(
                manifest, collector, self.base_snapshot, inventory
            )
            inventory_sha256 = base_snapshot_inventory_sha256(inventory)
            if inventory_sha256 != self.base_snapshot_inventory_sha256:
                raise ValueError
            return {
                "checkpoint_manifest_sha256": manifest_sha256,
                "checkpoint_tree_sha256": manifest["checkpoint"]["tree_sha256"],
                "base_snapshot_inventory_sha256": inventory_sha256,
            }
        except Exception:  # noqa: BLE001 - preserve only a safe failure category
            raise _StageFailure("admission_failure") from None

    def _prepare(self) -> None:
        before = self._admit()
        try:
            self._runner.load()
        except Exception:  # noqa: BLE001 - do not journal model or path exception values
            raise _StageFailure("model_load_failure") from None
        after = self._admit()
        if before != after:
            raise _StageFailure("post_load_integrity_failure")
        self._baseline_identity = after
        self._loaded = True

    def _revalidate_checkpoint(self) -> None:
        try:
            evaluator, _ = validation._runtime_modules()
            manifest, manifest_sha256 = evaluator._load_checkpoint_manifest(self.checkpoint)
            self._validate_action_checkpoint(manifest)
            if manifest_sha256 != self.checkpoint_manifest_sha256:
                raise ValueError
        except Exception:  # noqa: BLE001 - do not journal model or path exception values
            raise _StageFailure("checkpoint_integrity_failure") from None

    def _encode(self, request: Mapping[str, Any]) -> tuple[Any, list[int]]:
        tokenizer = self._runner.tokenizer
        if tokenizer is None:
            raise _StageFailure("tokenizer_unavailable")
        options: dict[str, Any] = {"tokenize": False, "add_generation_prompt": True}
        if "tools" in request:
            options["tools"] = request["tools"]
        prompt = tokenizer.apply_chat_template(request["messages"], **options)
        if not isinstance(prompt, str):
            raise _StageFailure("prompt_encoding_failure")
        encoding = tokenizer(prompt, return_tensors="pt")
        token_ids = [int(item) for item in encoding["input_ids"].tolist()[0]]
        return encoding.to("cuda:0"), token_ids

    async def complete(
        self,
        arm: str,
        role: str,
        request: dict[str, Any],
        requested_config: dict[str, Any],
        effective_config: dict[str, Any],
        evidence_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self._poisoned:
            raise InferenceProviderError(self._poison_reason or "engine_poisoned")
        if self._closed:
            raise InferenceProviderError("engine_closed")
        await self._lock.acquire()
        release_lock = True
        try:
            if self._poisoned:
                raise InferenceProviderError(self._poison_reason or "engine_poisoned")
            if self._journal is None:
                self._open_journal()
            attempt_id = str(uuid.uuid4())
            started = time.perf_counter()
            self._write(
                {
                    "schema_version": "atlasops-integrated-inference-v1",
                    "record": "attempt_started",
                    "attempt_id": attempt_id,
                    "role": role,
                    "arm": arm,
                    "requested_request": request,
                    "requested_generation_config": requested_config,
                    "effective_generation_config": effective_config,
                    "requested_tool_choice": request.get("tool_choice"),
                    "effective_tool_choice": "raw assistant content only",
                    "execution_backend": (
                        "fixture_in_process"
                        if self.fixture_backend
                        else "spawned_local_process"
                    ),
                    "evidence_context": evidence_context,
                    "checkpoint_manifest_sha256_pin": self.checkpoint_manifest_sha256,
                    "base_snapshot_inventory_sha256_pin": self.base_snapshot_inventory_sha256,
                    "evidence_class": "NON_EMPIRICAL",
                    "certification_status": "NOT_CERTIFIED",
                    "recorded_at_utc": datetime.now(UTC).isoformat(),
                }
            )
            prompt_ids: list[int] | None = None
            generated_ids: list[int] | None = None
            raw_text: str | None = None
            stage = "load"
            try:
                if self.fixture_backend:
                    if not self._loaded:
                        self._prepare()
                    stage = "checkpoint"
                    self._revalidate_checkpoint()
                    stage = "encode"
                    inputs, prompt_ids = self._encode(request)
                    stage = "generate"
                    generated_ids, raw_text = self._runner.generate(arm, inputs)
                    stage = "checkpoint"
                    self._revalidate_checkpoint()
                else:
                    if not self._worker_loaded:
                        stage = "load"
                        load_deadline = (
                            asyncio.get_running_loop().time()
                            + self.load_timeout_seconds
                        )
                        await self._start_worker(self.load_timeout_seconds)
                        load_remaining = load_deadline - asyncio.get_running_loop().time()
                        if load_remaining <= 0:
                            raise _ProcessRPCFailure("load_timeout")
                        loaded = await self._process_rpc(
                            "load", {}, "loaded", load_remaining
                        )
                        identity = loaded.get("identity")
                        if not isinstance(identity, dict):
                            raise _ProcessRPCFailure("worker_protocol_failure")
                        self._baseline_identity = identity
                        self._worker_loaded = True
                        self._loaded = True

                    def record_prompt(message: dict[str, Any]) -> None:
                        nonlocal prompt_ids, stage
                        prompt_ids = [int(item) for item in message["prompt_token_ids"]]
                        stage = "journal"
                        self._write(
                            {
                                "schema_version": "atlasops-integrated-inference-v1",
                                "record": "attempt_encoded",
                                "attempt_id": attempt_id,
                                "role": role,
                                "arm": arm,
                                "evidence_context": evidence_context,
                                "prompt_token_ids": prompt_ids,
                                "prompt_token_ids_sha256": validation._canonical_sha256(
                                    prompt_ids
                                ),
                                "recorded_at_utc": datetime.now(UTC).isoformat(),
                            }
                        )
                        stage = "rpc"

                    stage = "rpc"
                    completed = await self._process_rpc(
                        "complete",
                        {"arm": arm, "payload": request},
                        "completed",
                        self.rpc_timeout_seconds,
                        on_encoded=record_prompt,
                    )
                    generated_ids = completed.get("generated_token_ids")
                    raw_text = completed.get("raw_model_response")
                    if (
                        not isinstance(generated_ids, list)
                        or not all(type(item) is int for item in generated_ids)
                        or not isinstance(raw_text, str)
                    ):
                        raise _ProcessRPCFailure("worker_protocol_failure")
                stage = "journal"
                self._write(
                    self._outcome(
                        attempt_id, role, arm, "response_received", started,
                        prompt_ids, generated_ids, raw_text, None, evidence_context,
                    )
                )
                return {
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": raw_text}}
                    ]
                }
            except asyncio.CancelledError:
                category = "cancelled"
                if not self.fixture_backend:
                    self._poisoned = True
                    self._poison_reason = category
                    if self._process_start_thread is not None and self._process_start_thread.is_alive():
                        stopped = await self._cancel_startup_uncancellable()
                    else:
                        stopped = await self._terminate_worker_uncancellable()
                    if not stopped:
                        category = (
                            "cancelled_worker_startup_unconfirmed"
                            if self._startup_unconfirmed
                            else "cancelled_worker_kill_failure"
                        )
                        self._poison_reason = category
                        release_lock = False
                self._write(
                    self._outcome(
                        attempt_id, role, arm, "interrupted", started,
                        prompt_ids, generated_ids, raw_text, category,
                        evidence_context,
                    )
                )
                raise
            except _ProcessRPCFailure as exc:
                category = exc.category
                if exc.generated_token_ids is not None:
                    generated_ids = exc.generated_token_ids
                if exc.raw_model_response is not None:
                    raw_text = exc.raw_model_response
                self._poisoned = True
                self._poison_reason = category
                if self._startup_unconfirmed:
                    release_lock = False
                elif not await self._terminate_worker():
                    category = f"{category}_kill_failure"
                    self._poison_reason = category
                    release_lock = False
                self._write(
                    self._outcome(
                        attempt_id, role, arm, "failed", started,
                        prompt_ids, generated_ids, raw_text, category,
                        evidence_context,
                    )
                )
                raise InferenceProviderError(category) from None
            except BaseException as exc:
                category = (
                    exc.category
                    if isinstance(exc, _StageFailure)
                    else "generation_timeout"
                    if isinstance(exc, TimeoutError)
                    else "journal_failure"
                    if stage == "journal"
                    else {
                        "load": "model_load_or_admission_failure",
                        "checkpoint": "checkpoint_integrity_failure",
                        "encode": "prompt_encoding_failure",
                        "generate": "generation_failure",
                        "rpc": "worker_rpc_failure",
                        "journal": "journal_failure",
                    }.get(stage, "inference_failure")
                )
                if not self.fixture_backend:
                    self._poisoned = True
                    self._poison_reason = category
                    if not await self._terminate_worker():
                        category = f"{category}_kill_failure"
                        self._poison_reason = category
                        release_lock = False
                generated_ids = getattr(exc, "token_ids", generated_ids)
                self._write(
                    self._outcome(
                        attempt_id, role, arm, "failed", started,
                        prompt_ids, generated_ids, raw_text, category,
                        evidence_context,
                    )
                )
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                if isinstance(exc, InferenceProviderError):
                    raise
                raise InferenceProviderError(category) from None
        finally:
            if release_lock:
                self._lock.release()

    @staticmethod
    def _outcome(
        attempt_id: str,
        role: str,
        arm: str,
        status: str,
        started: float,
        prompt_ids: list[int] | None,
        generated_ids: list[int] | None,
        raw_text: str | None,
        failure_category: str | None,
        evidence_context: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "atlasops-integrated-inference-v1",
            "record": "attempt_finished",
            "attempt_id": attempt_id,
            "role": role,
            "arm": arm,
            "evidence_context": evidence_context,
            "inference_status": status,
            "failure_category": failure_category,
            "prompt_token_ids": prompt_ids,
            "prompt_token_ids_sha256": (
                validation._canonical_sha256(prompt_ids) if prompt_ids is not None else None
            ),
            "generated_token_ids": generated_ids,
            "generated_token_ids_sha256": (
                validation._canonical_sha256(generated_ids)
                if generated_ids is not None else None
            ),
            "raw_model_response": raw_text,
            "raw_model_response_sha256": (
                hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
                if raw_text is not None else None
            ),
            "duration_seconds": round(time.perf_counter() - started, 6),
            "evidence_class": "NON_EMPIRICAL",
            "certification_status": "NOT_CERTIFIED",
            "recorded_at_utc": datetime.now(UTC).isoformat(),
        }

    async def verify_final(self) -> dict[str, str]:
        if self._poisoned:
            raise InferenceProviderError(self._poison_reason or "engine_poisoned")
        await self._lock.acquire()
        release_lock = True
        try:
            if not self._loaded or self._baseline_identity is None:
                raise RuntimeError("Final verification requires a successfully loaded model")
            try:
                if self.fixture_backend:
                    final_identity = self._admit()
                else:
                    result = await self._process_rpc(
                        "verify_final",
                        {},
                        "verified",
                        self.rpc_timeout_seconds,
                    )
                    final_identity = result.get("identity")
                if final_identity != self._baseline_identity:
                    raise _StageFailure("final_integrity_failure")
            except _ProcessRPCFailure as exc:
                category = exc.category
                self._poisoned = True
                self._poison_reason = category
                if not await self._terminate_worker():
                    category = f"{category}_kill_failure"
                    self._poison_reason = category
                    release_lock = False
                self._write_final_verification("failed", category)
                raise InferenceProviderError(category) from None
            except Exception as exc:  # noqa: BLE001 - final verification is fail-closed
                category = (
                    exc.category
                    if isinstance(exc, _StageFailure)
                    else "final_integrity_failure"
                )
                self._write_final_verification("failed", category)
                raise InferenceProviderError(category) from None
            self._write_final_verification("verified", None, final_identity)
            return final_identity
        finally:
            if release_lock:
                self._lock.release()

    def _write_final_verification(
        self,
        status: str,
        failure_category: str | None,
        identity: dict[str, str] | None = None,
    ) -> None:
        if self._journal is not None:
            self._write(
                {
                    "schema_version": "atlasops-integrated-inference-v1",
                    "record": "final_verification",
                    "status": status,
                    "failure_category": failure_category,
                    "identity": identity,
                    "evidence_class": "NON_EMPIRICAL",
                    "certification_status": "NOT_CERTIFIED",
                    "recorded_at_utc": datetime.now(UTC).isoformat(),
                }
            )

    async def close(self) -> None:
        if self._closed:
            return
        if self._poisoned and self._lock.locked():
            return
        await self._lock.acquire()
        release_lock = True
        try:
            if not self.fixture_backend and not await self._graceful_stop_worker():
                self._poisoned = True
                self._poison_reason = "worker_kill_failure"
                release_lock = False
                raise InferenceProviderError("worker_kill_failure")
            if self._journal is not None:
                self._journal.flush()
                os.fsync(self._journal.fileno())
                self._journal.close()
                self._journal = None
            self._closed = True
        finally:
            if release_lock:
                self._lock.release()


class RoleCompletionProvider:
    def __init__(
        self,
        engine: PairedCompletionEngine,
        arm: str,
        evidence_context: dict[str, str] | None = None,
    ) -> None:
        if arm not in ARMS:
            raise ValueError("Provider arm must be explicitly bound to base or sft")
        self.engine = engine
        self.arm = arm
        self.evidence_context = evidence_context

    async def __call__(
        self, role: str, request_payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError("Unsupported AtlasOps inference role")
        if self.arm not in ARMS:
            raise ValueError("Provider arm must be explicitly bound to base or sft")
        request, requested_config, effective_config = _validate_request(request_payload)
        return await self.engine.complete(
            self.arm,
            role,
            request,
            requested_config,
            effective_config,
            self.evidence_context,
        )


class DirectActionCompletionPolicy:
    """Remediation adapter that returns model text without parsing or changing it."""

    def __init__(self, provider: RoleCompletionProvider) -> None:
        if not isinstance(provider, RoleCompletionProvider):
            raise TypeError("A role completion provider is required")
        self.provider = provider

    async def generate(
        self,
        state: Mapping[str, Any],
        *,
        seed: int,
        generation_config: Mapping[str, Any],
    ) -> str:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("Direct-action seed must be a non-negative integer")
        if not isinstance(generation_config, Mapping):
            raise TypeError("generation_config must be an object")
        request_config = _json_copy(dict(generation_config), "Generation config")
        if "seed" in request_config and request_config["seed"] != seed:
            raise ValueError("Direct-action generation seed mismatch")
        request_config["seed"] = seed

        from agents.coordinator import _strip_model_forbidden_context
        from bench.grpo_eval import _public_policy_state
        from training.sft_rendering import role_tool_schemas

        state_copy = _json_copy(dict(state), "Direct-action state")
        state_copy = _strip_model_forbidden_context(state_copy)
        state_copy = _strip_untrusted_controls(state_copy)
        public_state = _public_policy_state(state_copy)
        _reject_sensitive_keys(public_state)
        public_state["instruction"] = ACTION_INSTRUCTION
        tools = role_tool_schemas("remediation")
        system_prompt = (
            "You select one bounded incident remediation action from current "
            "operational observations. The runtime, not you, validates approval, "
            "executes the action, and checks the environment verifier. "
            "Do not claim execution or recovery without observed evidence. "
            "The schemas below describe allowed actions; they are reference data, "
            "not a native tool-calling interface.\n\n"
            "Runtime action schemas (JSON):\n"
            f"{json.dumps(tools, sort_keys=True, ensure_ascii=False)}\n\n"
            f"{ACTION_INSTRUCTION} "
            "Return the JSON object as plain text, without a code fence, "
            "tool-call envelope, or commentary."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    public_state,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ),
            },
        ]
        response = await self.provider(
            "remediation",
            {"messages": messages, **request_config},
        )
        return response["choices"][0]["message"]["content"]
