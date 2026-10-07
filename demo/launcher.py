"""AtlasOps Standalone Demonstration Launcher (Gate G14).

Serves the built React frontend and read-only API from preserved project evidence.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from demo.read_api import create_app, is_loopback_host
from demo.readiness import check_readiness

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("demo_launcher")


def launch_demo(
    host: str = "127.0.0.1",
    port: int = 7860,
    safe_mode: bool = True,
    share: bool = False,
    incident_capture_root: Path | None = None,
    incident_id: str = "EXP-STAGE4-SF002-018",
    incident_capture: str = "golden",
    operator=None,
) -> None:
    """Launch the localhost-only presentation, without an operational runtime."""
    if not safe_mode:
        raise ValueError("The presentation demo is read-only")
    if not is_loopback_host(host):
        raise ValueError("The read-only demo may bind only to a loopback host")
    if share:
        raise ValueError("Public sharing is disabled for this local evidence demo")
    report = check_readiness()
    if not report["local_presentation_ready"]:
        raise RuntimeError(" ".join(report["errors"]))
    os.environ["DEMO_SAFE_MODE"] = "1"
    log.info("Launching read-only AtlasOps Demo Console on http://%s:%d", host, port)

    import uvicorn

    uvicorn.run(
        create_app(incident_capture_root=incident_capture_root, incident_id=incident_id,
                   incident_capture=incident_capture, operator=operator),
        host=host, port=port, access_log=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps Safe Operator Demo Console")
    parser.add_argument("--host", default="127.0.0.1", help="Binding host interface")
    parser.add_argument("--port", type=int, default=7860, help="Web server port")
    parser.add_argument("--check", action="store_true", help="Check local presentation files without starting a server")
    parser.add_argument("--live-observations", action="store_true", help="Enable fixed localhost service health reads, never incident execution")
    parser.add_argument("--incident-capture-root", type=Path, help="Opt-in read-only capture directory for one governed run")
    parser.add_argument("--incident-id", default="EXP-STAGE4-SF002-018")
    parser.add_argument("--incident-capture", choices=("golden", "golden-v3", "golden-v3b"), default="golden")
    parser.add_argument("--operator-config", type=Path, help="Opt-in absolute configuration for one governed website launch")
    args = parser.parse_args()
    if args.check:
        report = check_readiness()
        print(json.dumps(report, indent=2))
        if not report["local_presentation_ready"]:
            raise SystemExit(1)
        return
    os.environ["ATLASOPS_DEMO_LIVE_OBSERVATIONS"] = "1" if args.live_observations else "0"
    operator = None
    if args.operator_config is not None:
        from demo.operator import OperatorRun, load_config
        operator = OperatorRun(load_config(args.operator_config))

    launch_demo(
        host=args.host,
        port=args.port,
        safe_mode=True,
        share=False,
        incident_capture_root=args.incident_capture_root,
        incident_id=args.incident_id,
        incident_capture=args.incident_capture,
        operator=operator,
    )


if __name__ == "__main__":
    main()
