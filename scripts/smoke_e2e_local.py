"""Run the shared local software smoke suite; no live incident is dispatched."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE_TESTS = (
    "tests/test_app_endpoints.py",
    "tests/test_coordinator.py",
    "tests/test_tools.py",
    "tests/test_bench_runner.py",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    return subprocess.run(
        [sys.executable, "-m", "pytest", *SMOKE_TESTS, "-q" if args.quiet else "-v"],
        cwd=ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
