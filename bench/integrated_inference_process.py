"""Spawned, persistent local inference worker for paired Validation calls."""

from __future__ import annotations

import json
from typing import Any

MAX_RPC_MESSAGE_BYTES = 5 * 1024 * 1024


def _receive(connection: Any) -> dict[str, Any]:
    raw = connection.recv_bytes(MAX_RPC_MESSAGE_BYTES)
    message = json.loads(raw.decode("utf-8"))
    if not isinstance(message, dict):
        raise TypeError("worker RPC messages must be JSON objects")
    return message


def _send(connection: Any, message: dict[str, Any]) -> None:
    raw = json.dumps(
        message, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    connection.send_bytes(raw)


def local_inference_worker(connection: Any, config: dict[str, Any]) -> None:
    """Own the pinned model in a child process and exchange bounded RPC messages."""
    from bench import base_sft_validation as validation
    from bench.integrated_inference import PairedCompletionEngine, _StageFailure

    core = PairedCompletionEngine(
        checkpoint=config["checkpoint"],
        checkpoint_manifest_sha256=config["checkpoint_manifest_sha256"],
        base_snapshot=config["base_snapshot"],
        base_snapshot_inventory_sha256=config["base_snapshot_inventory_sha256"],
        journal_path=None,
        fixture_backend=True,
    )
    loaded = False
    while True:
        try:
            request = _receive(connection)
        except EOFError:
            break
        request_id = request.get("request_id")
        operation = request.get("operation")
        stage = operation
        generated_ids = None
        raw_text = None
        try:
            if operation == "load":
                core._prepare()
                loaded = True
                _send(
                    connection,
                    {
                        "request_id": request_id,
                        "kind": "loaded",
                        "identity": core._baseline_identity,
                    }
                )
            elif operation == "complete":
                if not loaded:
                    raise _StageFailure("worker_not_loaded")
                core._revalidate_checkpoint()
                stage = "encode"
                inputs, prompt_ids = core._encode(request["payload"])
                _send(
                    connection,
                    {
                        "request_id": request_id,
                        "kind": "encoded",
                        "prompt_token_ids": prompt_ids,
                    }
                )
                stage = "generate"
                generated_ids, raw_text = core._runner.generate(
                    request["arm"], inputs
                )
                core._revalidate_checkpoint()
                _send(
                    connection,
                    {
                        "request_id": request_id,
                        "kind": "completed",
                        "generated_token_ids": generated_ids,
                        "raw_model_response": raw_text,
                    }
                )
            elif operation == "verify_final":
                if not loaded or core._baseline_identity is None:
                    raise _StageFailure("final_verification_without_load")
                final = core._admit()
                if final != core._baseline_identity:
                    raise _StageFailure("final_integrity_failure")
                _send(
                    connection,
                    {"request_id": request_id, "kind": "verified", "identity": final}
                )
            elif operation == "close":
                _send(connection, {"request_id": request_id, "kind": "closed"})
                break
            else:
                raise _StageFailure("worker_protocol_failure")
        except Exception as exc:  # noqa: BLE001 - only safe categories cross the process boundary
            category = (
                exc.category
                if isinstance(exc, _StageFailure)
                else "decode_failure"
                if isinstance(exc, validation.RawDecodeError)
                else "generation_failure"
                if stage == "generate"
                else "checkpoint_integrity_failure"
                if stage == "checkpoint"
                else "prompt_encoding_failure"
                if stage == "encode"
                else "worker_failure"
            )
            generated = getattr(exc, "token_ids", generated_ids)
            try:
                _send(
                    connection,
                    {
                        "request_id": request_id,
                        "kind": "failure",
                        "failure_category": category,
                        "generated_token_ids": generated,
                        "raw_model_response": raw_text,
                    }
                )
            except (BrokenPipeError, EOFError, OSError):
                break
    connection.close()
