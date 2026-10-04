"""One benign pinned Base request to the existing server on 127.0.0.1 only."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from bench.inference_runtime import validate_runtime
from scripts.qualify_integrated_inference import (
    preserve_final_transport_reference,
    qualify_engine,
)


async def run(args: argparse.Namespace) -> dict:
    from bench.integrated_inference_remote import create_loopback_engine

    for path in (args.checkpoint, args.base_snapshot, args.journal, args.inference_key_file):
        if not path.is_absolute():
            raise ValueError("Loopback qualification paths must be absolute")
    validate_runtime()
    engine = create_loopback_engine(
        checkpoint=str(args.checkpoint),
        checkpoint_manifest_sha256=args.checkpoint_manifest_sha256,
        base_snapshot=str(args.base_snapshot),
        base_snapshot_inventory_sha256=args.base_inventory_sha256,
        journal_path=str(args.journal),
        inference_key=args.inference_key_file.read_text().strip(),
        port=args.port,
    )
    try:
        return await qualify_engine(engine)
    finally:
        # Keep the same server/model for the subsequent tunneled qualification.
        # Only its local proxy is closed here; the operator owns server cleanup.
        try:
            if not await engine._terminate_worker_uncancellable():
                raise RuntimeError("Loopback proxy cleanup could not be confirmed")
            await engine.close()
        finally:
            preserve_final_transport_reference(engine)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-manifest-sha256", required=True)
    parser.add_argument("--base-snapshot", type=Path, required=True)
    parser.add_argument("--base-inventory-sha256", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--inference-key-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18765)
    record = asyncio.run(run(parser.parse_args()))
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
