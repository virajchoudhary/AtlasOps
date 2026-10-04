"""Benign transport qualification only: no incident, tool, or approval execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from bench.integrated_inference import EFFECTIVE_GENERATION_CONFIG, PairedCompletionEngine
from config.g4_protocol import APPROVED_G4_V38_INFERENCE_CONTRACT, APPROVED_G4_V38_MODEL

BENIGN_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant. Do not call any tools."},
    {"role": "user", "content": "Reply with the single word READY."},
]


def engine_from_environment() -> PairedCompletionEngine:
    from bench.integrated_inference_remote import create_remote_engine

    config_path = Path(os.environ["ATLASOPS_INFERENCE_CONFIG"])
    if not config_path.is_absolute():
        raise ValueError("Inference configuration requires an absolute local path")
    config = json.loads(config_path.read_bytes())
    if set(config) != {
        "checkpoint", "checkpoint_manifest_sha256", "base_snapshot",
        "base_snapshot_inventory_sha256", "journal_path", "url", "inference_key_file",
    }:
        raise ValueError("Inference configuration contains unsupported fields")
    if config["checkpoint_manifest_sha256"] != APPROVED_G4_V38_MODEL["checkpoint_manifest_sha256"]:
        raise ValueError("Inference checkpoint is not the approved unchanged v17 artifact")
    key_path = Path(config.pop("inference_key_file"))
    if not key_path.is_absolute():
        raise ValueError("Inference-only key requires an absolute local path")
    return create_remote_engine(**config, inference_key=key_path.read_text().strip())


async def qualify_engine(engine: PairedCompletionEngine) -> dict:
    if engine.fixture_backend:
        raise ValueError("Fixture inference cannot qualify a live G4 transport")
    if engine.load_timeout_seconds != 600 or engine.rpc_timeout_seconds != 600:
        raise ValueError("G4 qualification requires the reviewed 600-second deadlines")
    if any(
        value != APPROVED_G4_V38_INFERENCE_CONTRACT[key]
        for key, value in EFFECTIVE_GENERATION_CONFIG.items()
    ):
        raise ValueError("Effective decoding differs from the approved integrated contract")
    started = time.monotonic()
    record = {
        "schema_version": "atlasops-benign-inference-qualification-v1",
        "model": dict(APPROVED_G4_V38_MODEL),
        "started_at_utc": datetime.now(UTC).isoformat(),
        "qualification_only": True,
        "incident_attempt_reserved": False,
        "operational_tools_executed": False,
        "load_timeout_seconds": engine.load_timeout_seconds,
        "request_timeout_seconds": engine.rpc_timeout_seconds,
        "effective_generation_config": dict(EFFECTIVE_GENERATION_CONFIG),
    }
    try:
        response = await engine.provider("base")(
            "triage", {
                "messages": BENIGN_MESSAGES,
                **{key: EFFECTIVE_GENERATION_CONFIG[key] for key in (
                    "seed", "temperature", "top_p", "max_new_tokens", "do_sample"
                )},
            },
        )
        text = response["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Benign inference did not return nonempty model text")
        identity = await engine.verify_final()
        if identity["checkpoint_manifest_sha256"] != APPROVED_G4_V38_MODEL["checkpoint_manifest_sha256"]:
            raise RuntimeError("Qualified model identity differs from the v17 pin")
        from bench.integrated_inference_remote import _validate_runtime

        _validate_runtime(identity.get("runtime"))
        record.update(status="QUALIFIED", response=response, loaded_identity=identity)
    except BaseException as exc:
        record.update(
            status="NOT_QUALIFIED",
            failure_category=getattr(exc, "failure_category", type(exc).__name__),
        )
        raise
    finally:
        record["elapsed_seconds"] = time.monotonic() - started
        record["finished_at_utc"] = datetime.now(UTC).isoformat()
        if engine._journal is not None:
            engine._write(record)
        journal = engine.journal_path
        if journal is not None and journal.is_file():
            raw = journal.read_bytes()
            linked = {
                "qualification": record,
                "journal": {
                    "path": str(journal), "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                },
                "incident_attempt_reserved": False,
            }
            with journal.with_suffix(".qualification.json").open("x", encoding="utf-8") as stream:
                json.dump(linked, stream, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
    return record


async def main() -> None:
    engine = engine_from_environment()
    try:
        record = await qualify_engine(engine)
        print(json.dumps(record, sort_keys=True))
    finally:
        try:
            await engine.close()
        finally:
            preserve_final_transport_reference(engine)


def preserve_final_transport_reference(engine: PairedCompletionEngine) -> None:
    """Link the final journal separately; never rewrite the qualification prefix."""
    journal = engine.journal_path
    if journal is None or not journal.is_file():
        return
    raw = journal.read_bytes()
    reference = {
        "schema_version": "atlasops-transport-final-reference-v1",
        "journal": {
            "path": str(journal),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        },
        "proxy_cleanup_confirmed": engine._closed,
        "incident_attempt_reserved": False,
        "qualification_establishes_incident_resolution": False,
    }
    with journal.with_suffix(".transport-final.json").open("x", encoding="utf-8") as stream:
        json.dump(reference, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    asyncio.run(main())
