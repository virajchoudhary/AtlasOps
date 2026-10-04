"""Serve pinned model inference on loopback, with no operational authority."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from bench.integrated_inference import PairedCompletionEngine
from bench.integrated_inference_remote import (
    PINNED_V17_MANIFEST_SHA256,
    inference_bridge_app,
)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--base-snapshot", type=Path, required=True)
    parser.add_argument("--base-inventory-sha256", required=True)
    parser.add_argument("--inference-key-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise ValueError("Inference bridge requires an unprivileged TCP port")
    for path in (args.checkpoint, args.base_snapshot, args.inference_key_file):
        if not path.is_absolute():
            raise ValueError("Inference input paths must be absolute")
    if any(name in os.environ for name in (
        "KUBECONFIG", "ATLASOPS_API_KEY", "ATLASOPS_AUDIT_SECRET",
        "ARGOCD_PASS", "ALERTMANAGER_WEBHOOK_SECRET", "SLACK_WEBHOOK", "DISCORD_WEBHOOK",
    )):
        raise RuntimeError("Inference host must not carry operational credentials")
    engine = PairedCompletionEngine(
        checkpoint=args.checkpoint,
        checkpoint_manifest_sha256=PINNED_V17_MANIFEST_SHA256,
        base_snapshot=args.base_snapshot,
        base_snapshot_inventory_sha256=args.base_inventory_sha256,
        journal_path=None,
    )
    app = inference_bridge_app(engine, args.inference_key_file.read_text().strip())
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
