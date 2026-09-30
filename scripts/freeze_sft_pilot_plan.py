"""Freeze preparation inputs only; cannot issue an SFT execution approval."""

import json
import re
from pathlib import Path

from training.sft_pilot_gate import BASE_MODEL, CORPUS_HASH, MANIFEST_HASH, REVISION
from training.sft_provenance import REPO_ROOT, canonical_bytes_sha256

FILES = (
    "docs/project/G7_D3_PREPARATION_APPROVAL_V1.md",
    "requirements/sft-pilot-linux-py312.lock",
    "infra/training/sft-pilot/Dockerfile",
    "artifacts/evidence/stage7/tokenizer_files_a09a354_v1.json",
    "artifacts/evidence/stage7/sft_tokenizer_preflight_v4.json",
    "training/templates/qwen2_5_tool_sft.jinja",
)


def freeze(path: Path) -> dict:
    packages = dict(re.findall(
        r"^([a-zA-Z0-9_-]+)==([^\s\\]+)",
        (REPO_ROOT / "requirements/sft-pilot-linux-py312.lock").read_text(),
        re.MULTILINE,
    ))
    if len(packages) != 72:
        raise ValueError("Expected complete reviewed 72-package environment")
    report = json.loads(
        (REPO_ROOT / "artifacts/evidence/stage7/sft_tokenizer_preflight_v4.json").read_text()
    )
    if report["status"] != "PASS" or report["summary"]["checked_rows"] != 68:
        raise ValueError("Cannot freeze a failing or incomplete tokenizer preflight")
    plan = {
        "schema_version": "atlasops-sft-pilot-plan-v1",
        "d3_status": "APPROVED_FOR_PREPARATION",
        "execution_allowed": False,
        "model": BASE_MODEL,
        "model_revision": REVISION,
        "tokenizer": BASE_MODEL,
        "tokenizer_revision": REVISION,
        "corpus_version": "train-candidate-v1",
        "corpus_sha256": CORPUS_HASH,
        "corpus_manifest_sha256": MANIFEST_HASH,
        "hyperparameters": {
            "epochs": 1, "learning_rate": 0.0002, "batch_size": 2,
            "gradient_accumulation_steps": 4, "max_sequence_length": 8192,
            "seed": 2026, "assistant_only_loss": True,
        },
        "environment": {
            "python_version": "3.12.11",
            "platform": "x86_64-manylinux_2_28",
            "base_image": "python:3.12.11-slim-bookworm@sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49",
            "built_image_digest": None,
            "package_versions": packages,
            "gpu_recommendation": "NVIDIA A100 80 GB; L40S 48 GB only after measured fit",
            "actual_gpu_verified": False,
        },
        "required_files": {
            name: canonical_bytes_sha256((REPO_ROOT / name).read_bytes()) for name in FILES
        },
        "scope": {
            "training_authorized": False, "model_weights_downloaded": False,
            "paid_compute_authorized": False, "held_out_outcomes_accessed": False,
            "live_remediation_authorized": False,
        },
    }
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(plan, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return plan


if __name__ == "__main__":
    destination = REPO_ROOT / "config/sft_pilot_v3.json"
    freeze(destination)
    print(canonical_bytes_sha256(destination.read_bytes()))
