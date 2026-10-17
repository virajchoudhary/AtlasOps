"""Write a non-executable source inventory for prospective Stage A review."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from agents.stage_a import STAGE_A_VERSION
from config.g4_protocol import APPROVED_G4_PROTOCOL_PROFILE, file_sha256


def inventory(root: Path) -> dict:
    root = root.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
    ).strip()
    files = set(APPROVED_G4_PROTOCOL_PROFILE["causal_evidence_policy"]["source_sha256"])
    files.update({
        "agents/stage_a.py", "scripts/stage_a_telemetry.py",
        "scripts/prepare_stage_a_requalification.py", "agents/verifier.py",
        "training/grpo_environment.py", "agents/tools/chaos.py",
        "bench/chaos_manifests/single_fault/sf-002.yaml",
    })
    hashes = {}
    for name in sorted(files):
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Missing or redirected source: {name}")
        hashes[name] = {
            "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "lf_sha256": file_sha256(path),
        }
    return {
        "protocol_version": STAGE_A_VERSION,
        "source_commit": commit,
        "dirty": bool(subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain"], text=True,
        ).strip()),
        "sources": hashes,
        "declared_model": APPROVED_G4_PROTOCOL_PROFILE["model"],
        "model_runtime_verified": False,
        "active": False,
        "launch_authorized": False,
        "historical_protocol_replaced": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    record = inventory(Path(__file__).resolve().parents[1])
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    main()
