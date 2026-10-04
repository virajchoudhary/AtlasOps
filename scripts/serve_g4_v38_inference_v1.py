"""Validate the exact v3.8 runtime before starting pinned model inference."""

from __future__ import annotations

from bench.inference_runtime import validate_runtime


def _existing_server_main():
    from scripts.serve_integrated_inference import main

    return main


def main() -> None:
    validate_runtime()
    _existing_server_main()()


if __name__ == "__main__":
    main()
