"""Durable provenance records for AtlasOps SFT runs.

This module intentionally depends only on the Python standard library so run
intent and failures can be persisted before optional training packages or model
weights are loaded.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.splits import TRAIN_SEED, TRAIN_SPLIT

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAMES = ("accelerate", "bitsandbytes", "datasets", "peft", "torch", "transformers", "trl")
ADAPTER_WEIGHT_NAMES = frozenset({"adapter_model.safetensors", "adapter_model.bin"})


def canonical_file_sha256(path: Path) -> str:
    """Hash a text corpus with CRLF normalized to LF."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def source_provenance() -> dict[str, Any]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Unable to capture required Git source provenance") from exc
    return {"git_sha": sha, "git_dirty": dirty}


def package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in PACKAGE_NAMES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def runtime_environment() -> dict[str, Any]:
    environment: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": package_versions(),
    }
    try:
        import torch

        environment["torch"] = {
            "version": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "hip_version": getattr(torch.version, "hip", None),
            "devices": [
                torch.cuda.get_device_name(index)
                for index in range(torch.cuda.device_count())
            ],
        }
    except (ImportError, RuntimeError) as exc:
        environment["torch_probe_error"] = f"{type(exc).__name__}: {exc}"
    return environment


def inspect_training_corpus(path: Path) -> dict[str, Any]:
    train_ids = set(TRAIN_SPLIT)
    scenario_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL row at line {line_number}") from exc
        if not isinstance(row, dict):
            raise TypeError(f"SFT corpus row {line_number} must be a JSON object")
        scenario_id = row.get("scenario_id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError(f"SFT corpus row {line_number} lacks scenario_id")
        if scenario_id not in train_ids:
            raise ValueError(
                f"SFT corpus row {line_number} contains non-training scenario {scenario_id!r}"
            )
        role = row.get("role")
        if not isinstance(role, str) or not role:
            raise ValueError(f"SFT corpus row {line_number} lacks role")
        scenario_counts[scenario_id] = scenario_counts.get(scenario_id, 0) + 1
        role_counts[role] = role_counts.get(role, 0) + 1

    if set(scenario_counts) != train_ids:
        missing = sorted(train_ids.difference(scenario_counts))
        raise ValueError(f"SFT corpus does not cover the frozen training split: missing={missing}")
    return {
        "total_examples": sum(scenario_counts.values()),
        "observed_scenarios": sorted(scenario_counts),
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "role_counts": dict(sorted(role_counts.items())),
    }


def write_manifest_atomic(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temp_path = Path(stream.name)
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temp_path, path)


def create_run_manifest(
    *,
    corpus_path: Path,
    output_dir: Path,
    base_model: str,
    base_model_revision: str,
    tokenizer: str,
    tokenizer_revision: str,
    role: str,
    hyperparameters: dict[str, Any],
    seed: int = TRAIN_SEED,
) -> dict[str, Any]:
    if not corpus_path.is_file():
        raise FileNotFoundError(f"SFT corpus not found: {corpus_path}")
    if not base_model_revision.strip():
        raise ValueError("An exact base model revision is required")
    if not tokenizer_revision.strip():
        raise ValueError("An exact tokenizer revision is required")

    corpus_inventory = inspect_training_corpus(corpus_path)
    started_at = datetime.now(UTC).isoformat()
    return {
        "schema_version": 1,
        "run_id": f"sft-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
        "status": "planned",
        "started_at": started_at,
        "completed_at": None,
        "base_model": {
            "id": base_model,
            "requested_revision": base_model_revision,
            "resolved_revision": None,
        },
        "tokenizer": {
            "id": tokenizer,
            "requested_revision": tokenizer_revision,
            "resolved_revision": None,
        },
        "dataset": {
            "corpus_path": str(corpus_path.resolve()),
            "corpus_sha256_canonical_lf": canonical_file_sha256(corpus_path),
            "split": "train",
            "split_seed": seed,
            "split_scenarios": list(TRAIN_SPLIT),
            "split_sha256": canonical_json_sha256(list(TRAIN_SPLIT)),
            **corpus_inventory,
        },
        "role_filter": role,
        "hyperparameters": hyperparameters,
        "source": source_provenance(),
        "environment": runtime_environment(),
        "output_dir": str(output_dir.resolve()),
        "trainer_state": None,
        "training_history": [],
        "checkpoint": None,
        "failure": None,
    }


def mark_running(
    manifest: dict[str, Any],
    *,
    resolved_model_revision: str,
    resolved_tokenizer_revision: str,
) -> dict[str, Any]:
    updated = dict(manifest)
    updated["status"] = "running"
    updated["model_loaded_at"] = datetime.now(UTC).isoformat()
    updated["base_model"] = {
        **manifest["base_model"],
        "resolved_revision": resolved_model_revision,
    }
    updated["tokenizer"] = {
        **manifest["tokenizer"],
        "resolved_revision": resolved_tokenizer_revision,
    }
    return updated


def checkpoint_inventory(output_dir: Path, manifest_path: Path) -> dict[str, Any]:
    files = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Checkpoint contains a symlink: {path}")
        if not path.is_file():
            continue
        if path.resolve() == manifest_path.resolve() or path.suffix == ".tmp":
            continue
        files.append(
            {
                "path": path.relative_to(output_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    if not files:
        raise RuntimeError("Training completed without checkpoint files")
    if not any(row["path"] == "adapter_config.json" for row in files):
        raise RuntimeError("Completed SFT checkpoint lacks adapter_config.json")
    if not any(row["path"] in ADAPTER_WEIGHT_NAMES for row in files):
        raise RuntimeError("Completed SFT checkpoint lacks adapter model weights")
    return {
        "files": files,
        "tree_sha256": canonical_json_sha256(files),
        "total_files": len(files),
        "total_bytes": sum(item["size_bytes"] for item in files),
    }


def mark_completed(
    manifest: dict[str, Any],
    *,
    output_dir: Path,
    manifest_path: Path,
    trainer_state: dict[str, Any],
    training_history: list[dict[str, Any]],
) -> dict[str, Any]:
    updated = dict(manifest)
    updated.update(
        {
            "status": "completed",
            "completed_at": datetime.now(UTC).isoformat(),
            "trainer_state": trainer_state,
            "training_history": training_history,
            "checkpoint": checkpoint_inventory(output_dir, manifest_path),
            "failure": None,
        }
    )
    return updated


def mark_failed(
    manifest: dict[str, Any],
    exc: BaseException,
    *,
    interrupted: bool = False,
) -> dict[str, Any]:
    updated = dict(manifest)
    updated.update(
        {
            "status": "interrupted" if interrupted else "failed",
            "completed_at": datetime.now(UTC).isoformat(),
            "failure": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
    )
    return updated
