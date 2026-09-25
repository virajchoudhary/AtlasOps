"""AtlasOps Standalone Demonstration Launcher (Gate G14).

Launches the read-only Gradio demonstration console from preserved project evidence.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("demo_launcher")


def launch_demo(
    host: str = "127.0.0.1",
    port: int = 7860,
    safe_mode: bool = True,
    share: bool = False,
) -> None:
    """Launch the AtlasOps Gradio demo console."""
    if not safe_mode:
        raise ValueError("The Gradio demo is read-only; use the governed Stage 4 harness for live Chaos")
    os.environ["DEMO_SAFE_MODE"] = "1"
    log.info("Launching read-only AtlasOps Demo Console on http://%s:%d", host, port)

    try:
        from dashboard import _UI_CSS, build_app
        demo = build_app()
        demo.launch(server_name=host, server_port=port, share=share, css=_UI_CSS)
    except Exception as e:  # noqa: BLE001 - report optional UI/dependency startup failures
        log.error("Failed to launch demo (%s): %s", type(e).__name__, e)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps Safe Operator Demo Console")
    parser.add_argument("--host", default="127.0.0.1", help="Binding host interface")
    parser.add_argument("--port", type=int, default=7860, help="Web server port")
    parser.add_argument("--share", action="store_true", default=False, help="Create a public Gradio share link")
    args = parser.parse_args()

    launch_demo(
        host=args.host,
        port=args.port,
        safe_mode=True,
        share=args.share,
    )


if __name__ == "__main__":
    main()
