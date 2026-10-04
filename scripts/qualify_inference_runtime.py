"""Run the bounded, non-model G4 v3.8 inference-runtime qualification."""

from __future__ import annotations

import json

from bench.inference_runtime import qualify_runtime


def main() -> int:
    record = qualify_runtime()
    print(json.dumps(record, sort_keys=True))
    return 0 if record["status"] == "QUALIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
