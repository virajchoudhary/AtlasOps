"""Output admission for optional local recommender research commands."""

from __future__ import annotations

import argparse
from pathlib import Path


def fresh_output_directory(parser: argparse.ArgumentParser, destination: Path) -> Path:
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        parser.error(f"Cannot create fresh output directory: {exc}")
    return destination
