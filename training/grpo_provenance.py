"""Durable, fail-closed provenance for online GRPO checkpoints."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.splits import TRAIN_SPLIT
from training.sft_provenance import (
    canonical_json_sha256,
    file_sha256,
    runtime_environment,
    write_manifest_atomic,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "grpo_run_manifest.json"
ADAPTER_WEIGHT_NAMES = frozenset({"adapter_model.safetensors", "adapter_model.bin"})


def validate_sft_parent(
    checkpoint: Path,
    *,
    model_id: str,
    model_revision: str,
    tokenizer_id: str,
    tokenizer_revision: str,
) -> dict[str, Any]:
    """Bind GRPO to the completed, byte-validated G7 adapter."""
    from bench.sft_eval import _load_checkpoint_manifest

    checkpoint = checkpoint.expanduser().resolve()
    manifest, manifest_sha256 = _load_checkpoint_manifest(checkpoint)
    base = manifest["base_model"]
    tokenizer = manifest["tokenizer"]
    if (
        base["id"] != model_id
        or base["resolved_revision"] != model_revision
        or tokenizer["id"] != tokenizer_id
        or tokenizer["resolved_revision"] != tokenizer_revision
    ):
        raise ValueError("GRPO base model/tokenizer must match the completed SFT checkpoint")
    if manifest["source"].get("git_dirty") is not False:
        raise ValueError("GRPO requires an SFT checkpoint trained from clean source")
    return {
        "checkpoint_path": str(checkpoint),
        "manifest_sha256": manifest_sha256,
        "checkpoint_tree_sha256": manifest["checkpoint"]["tree_sha256"],
        "training_source_sha": manifest["source"]["git_sha"],
        "train_corpus_sha256": manifest["dataset"]["corpus_sha256_canonical_lf"],
        "train_split_sha256": manifest["dataset"]["split_sha256"],
    }


def _git_output(*arguments: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=text,
        check=True,
    )
    return result.stdout


def source_identity() -> dict[str, Any]:
    """Record exact HEAD and a digest of the tracked diff/status at launch."""
    code_sha = str(_git_output("rev-parse", "HEAD")).strip()
    status = str(_git_output("status", "--porcelain"))
    source: dict[str, Any] = {
        "code_sha": code_sha,
        "source_state": "dirty" if status.strip() else "clean",
    }
    if source["source_state"] == "dirty":
        diff = _git_output("diff", "HEAD", "--binary", text=False)
        source["dirty_diff_sha256"] = hashlib.sha256(
            status.encode("utf-8") + bytes(diff)
        ).hexdigest()
    return source


def create_run_manifest(
    *,
    model_id: str,
    model_revision: str,
    tokenizer_id: str,
    tokenizer_revision: str,
    seed: int,
    generation_config: dict[str, Any],
    hyperparameters: dict[str, Any],
    sft_parent: dict[str, Any],
    prompt_rows: list[dict[str, str]],
) -> dict[str, Any]:
    if not all((model_id, model_revision, tokenizer_id, tokenizer_revision)):
        raise ValueError("Exact base model and tokenizer identities are required")
    if seed < 0:
        raise ValueError("GRPO seed must be nonnegative")
    if not sft_parent or not all(
        sft_parent.get(key)
        for key in ("checkpoint_path", "manifest_sha256", "checkpoint_tree_sha256", "train_split_sha256")
    ):
        raise ValueError("GRPO requires validated SFT checkpoint provenance")
    if sft_parent["train_split_sha256"] != canonical_json_sha256(list(TRAIN_SPLIT)):
        raise ValueError("SFT parent Train split differs from frozen GRPO Train split")
    selected_ids = [row.get("scenario_id") for row in prompt_rows]
    if (
        not selected_ids
        or len(selected_ids) != len(set(selected_ids))
        or any(sid not in TRAIN_SPLIT for sid in selected_ids)
        or any(not isinstance(row.get("prompt"), str) or not row["prompt"] for row in prompt_rows)
    ):
        raise ValueError("GRPO prompt rows require unique frozen Train scenario identities")
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": 1,
        "status": "planned",
        "created_at": now,
        "updated_at": now,
        "training": {
            "algorithm": "GRPO",
            "mode": "online_rl_real_environment",
            "seed": seed,
            "generation_config": generation_config,
            "hyperparameters": hyperparameters,
        },
        "base_model": {"id": model_id, "resolved_revision": model_revision},
        "tokenizer": {"id": tokenizer_id, "resolved_revision": tokenizer_revision},
        "source": source_identity(),
        "sft_parent": sft_parent,
        "splits": {
            "train_sha256": canonical_json_sha256(list(TRAIN_SPLIT)),
            "selected_scenario_ids": selected_ids,
            "selected_scenario_ids_sha256": canonical_json_sha256(selected_ids),
            "selected_prompt_rows_sha256": canonical_json_sha256(prompt_rows),
        },
        "environment": runtime_environment(),
        "checkpoint": None,
    }


def checkpoint_inventory(output_dir: Path) -> dict[str, Any]:
    files = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Checkpoint contains a symlink: {path}")
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        relative = path.relative_to(output_dir).as_posix()
        files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    if not any(row["path"] == "adapter_config.json" for row in files):
        raise RuntimeError("Completed GRPO checkpoint lacks adapter_config.json")
    if not any(row["path"] in ADAPTER_WEIGHT_NAMES for row in files):
        raise RuntimeError("Completed GRPO checkpoint lacks adapter model weights")
    return {
        "files": files,
        "tree_sha256": canonical_json_sha256(files),
    }


def persist_status(
    path: Path,
    manifest: dict[str, Any],
    status: str,
    *,
    error_type: str | None = None,
) -> dict[str, Any]:
    if status not in {"planned", "running", "completed", "failed", "interrupted"}:
        raise ValueError(f"Unknown GRPO run status: {status}")
    updated = dict(manifest)
    updated["status"] = status
    updated["updated_at"] = datetime.now(UTC).isoformat()
    if error_type:
        updated["failure"] = {"error_type": error_type}
    if status == "completed":
        updated["checkpoint"] = checkpoint_inventory(path.parent)
        updated["completed_at"] = updated["updated_at"]
    write_manifest_atomic(path, updated)
    return updated
