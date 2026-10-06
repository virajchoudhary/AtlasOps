"""AtlasOps Standalone Demonstration Launcher (Gate G14).

Serves the built React frontend and read-only API from preserved project evidence.
"""

from __future__ import annotations

import argparse
import logging
import os

from demo.read_api import create_app, is_loopback_host

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("demo_launcher")


def launch_demo(
    host: str = "127.0.0.1",
    port: int = 7860,
    safe_mode: bool = True,
    share: bool = False,
) -> None:
    """Launch the localhost-only presentation, without an operational runtime."""
    if not safe_mode:
        raise ValueError("The presentation demo is read-only")
    if not is_loopback_host(host):
        raise ValueError("The read-only demo may bind only to a loopback host")
    if share:
        raise ValueError("Public sharing is disabled for this local evidence demo")
    os.environ["DEMO_SAFE_MODE"] = "1"
    log.info("Launching read-only AtlasOps Demo Console on http://%s:%d", host, port)

    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, access_log=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps Safe Operator Demo Console")
    parser.add_argument("--host", default="127.0.0.1", help="Binding host interface")
    parser.add_argument("--port", type=int, default=7860, help="Web server port")
    args = parser.parse_args()

    launch_demo(
        host=args.host,
        port=args.port,
        safe_mode=True,
        share=False,
    )


if __name__ == "__main__":
    main()
