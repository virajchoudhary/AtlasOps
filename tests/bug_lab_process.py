"""Disposable rule-only Bug Lab server for real-browser application tests."""

import argparse
import tempfile
from pathlib import Path

import uvicorn

from demo.bug_lab.engine import LabOptions
from demo.bug_lab.llm import RULES
from demo.bug_lab.server import LabConfig, create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8793)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="atlasops-browser-lab-") as directory:
        app = create_app(
            LabConfig(
                workdir=Path(directory),
                options=LabOptions(
                    step_delay=0, monitor_interval=0.1, approval_timeout=5,
                    verify_timeout=8,
                ),
            ),
            backend=RULES,
        )
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
