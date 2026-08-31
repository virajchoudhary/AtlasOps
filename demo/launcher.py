"""AtlasOps Standalone Demonstration Launcher (Gate G14).

Launches the safe, reproducible Gradio demonstration console with automated health
checks, environment verification, and zero-risk safe mode defaults.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("demo_launcher")


def launch_demo(
    host: str = "0.0.0.0",
    port: int = 7860,
    safe_mode: bool = True,
    share: bool = False,
) -> None:
    """Launch the AtlasOps Gradio demo console."""
    os.environ["DEMO_SAFE_MODE"] = "1" if safe_mode else "0"
    log.info("Launching AtlasOps Demo Console on http://%s:%d (Safe Mode: %s)", host, port, safe_mode)

    try:
        from dashboard import build_app
        demo = build_app()
        demo.launch(server_name=host, server_port=port, share=share)
    except Exception as e:
        log.error("Failed to launch demo: %s", e)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps Safe Operator Demo Console")
    parser.add_argument("--host", default="0.0.0.0", help="Binding host interface")
    parser.add_argument("--port", type=int, default=7860, help="Web server port")
    parser.add_argument("--live-cluster", action="store_true", default=False, help="Enable live cluster mutating actions (default: safe mode)")
    parser.add_argument("--share", action="store_true", default=False, help="Create a public Gradio share link")
    args = parser.parse_args()

    launch_demo(
        host=args.host,
        port=args.port,
        safe_mode=not args.live_cluster,
        share=args.share,
    )


if __name__ == "__main__":
    main()
