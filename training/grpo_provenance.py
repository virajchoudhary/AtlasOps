"""Durable, fail-closed provenance for online GRPO checkpoints."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.splits import TRAIN_SPLIT
from training.grpo_environment import require_live_kube_context
from training.sft_provenance import (
    canonical_json_sha256,
    file_sha256,
    runtime_environment,
    write_manifest_atomic,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "grpo_run_manifest.json"
ADAPTER_WEIGHT_NAMES = frozenset({"adapter_model.safetensors", "adapter_model.bin"})
EFFECTIVE_HYPERPARAMETERS = frozenset(
    {
        "tiers",
        "learning_rate",
        "beta",
        "batch_size",
        "num_generations",
        "max_steps",
        "gradient_accumulation_steps",
        "max_completion_length",
        "optuna_trials",
    }
)
OPTUNA_TUNED_HYPERPARAMETERS = {
    "learning_rate": "lr",
    "beta": "beta",
    "num_generations": "num_generations",
}


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


def validate_hyperparameter_provenance(
    training: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    requested = training.get("requested_hyperparameters")
    legacy_requested = training.get("hyperparameters")
    effective = training.get("effective_hyperparameters")
    selection = training.get("hyperparameter_selection")
    if not isinstance(requested, Mapping) or not isinstance(legacy_requested, Mapping):
        raise TypeError("GRPO training provenance requires requested hyperparameters")
    requested_values = dict(requested)
    if dict(legacy_requested) != requested_values:
        raise ValueError("Requested GRPO hyperparameters disagree with legacy hyperparameters")
    if not isinstance(effective, Mapping):
        raise TypeError("GRPO training provenance requires effective hyperparameters")
    effective_values = dict(effective)
    if set(effective_values) != EFFECTIVE_HYPERPARAMETERS:
        raise ValueError("GRPO effective hyperparameters do not match the required configuration")
    if not isinstance(selection, str) or selection not in {"requested", "optuna"}:
        raise ValueError("GRPO hyperparameter selection must be requested or optuna")
    tiers = effective_values.get("tiers")
    if (
        not isinstance(tiers, list)
        or not tiers
        or any(not isinstance(tier, str) for tier in tiers)
        or tiers != requested_values.get("tiers")
    ):
        raise ValueError("Effective GRPO tiers differ from the requested tiers")
    for key in ("learning_rate", "beta"):
        value = effective_values[key]
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            raise ValueError(f"Effective GRPO {key} must be finite and non-negative")
    for key in (
        "batch_size",
        "num_generations",
        "max_steps",
        "gradient_accumulation_steps",
        "max_completion_length",
        "optuna_trials",
    ):
        value = effective_values[key]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < (0 if key == "optuna_trials" else 1)
        ):
            raise ValueError(f"Effective GRPO {key} is invalid")
    for key in (
        "batch_size",
        "max_steps",
        "gradient_accumulation_steps",
        "optuna_trials",
    ):
        if effective_values[key] != requested_values.get(key):
            raise ValueError(f"Effective GRPO {key} differs from the requested value")
    generation_config = training.get("generation_config")
    if not isinstance(generation_config, Mapping):
        raise TypeError("GRPO training provenance requires generation configuration")
    if effective_values["max_completion_length"] != generation_config.get(
        "max_completion_length"
    ):
        raise ValueError("Effective GRPO completion length differs from generation configuration")
    if selection == "requested":
        if any(
            effective_values[key] != requested_values.get(key)
            for key in OPTUNA_TUNED_HYPERPARAMETERS
        ):
            raise ValueError("Requested hyperparameter selection differs from effective GRPO settings")
    elif effective_values["optuna_trials"] <= 0:
        raise ValueError("Optuna-selected GRPO settings require at least one requested trial")
    return requested_values, effective_values, str(selection)


def validate_training_summary(
    training: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> None:
    if not isinstance(summary, Mapping):
        raise TypeError("GRPO training summary must be an object")
    requested, effective, selection = validate_hyperparameter_provenance(training)
    if summary.get("requested_hyperparameters") != requested:
        raise ValueError("Training summary requested hyperparameters differ from the run manifest")
    if summary.get("effective_hyperparameters") != effective:
        raise ValueError("Training summary effective hyperparameters differ from the run manifest")
    if summary.get("hyperparameter_selection") != selection:
        raise ValueError("Training summary hyperparameter selection differs from the run manifest")
    if summary.get("generation_config") != training.get("generation_config"):
        raise ValueError("Training summary generation configuration differs from the run manifest")
    if summary.get("live_execution") != training.get("live_execution"):
        raise ValueError("Training summary live execution differs from the run manifest")
    operator_approval = training.get("operator_approval")
    if operator_approval is not None:
        if (
            not isinstance(operator_approval, Mapping)
            or set(operator_approval) != {"mode", "timeout_seconds", "identity"}
            or operator_approval.get("mode") not in {
                "disabled", "loopback_exact_action_v1",
            }
            or operator_approval.get("timeout_seconds") != (
                300 if operator_approval.get("mode") == "loopback_exact_action_v1"
                else None
            )
            or operator_approval.get("identity")
            != "operator_supplied_name_not_independent_attestation"
        ):
            raise ValueError("GRPO operator approval profile is invalid")
        if summary.get("operator_approval") != dict(operator_approval):
            raise ValueError("Training summary operator approval differs from the run manifest")


def validate_optuna_best(
    path: Path,
    *,
    effective_hyperparameters: Mapping[str, Any],
    live_execution: Mapping[str, Any],
) -> None:
    if not path.is_file():
        raise ValueError("Optuna-selected GRPO training requires optuna_best.json")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, Mapping):
        raise TypeError("Optuna best-parameter record must be an object")
    expected_params = {
        "lr": effective_hyperparameters["learning_rate"],
        "beta": effective_hyperparameters["beta"],
        "num_generations": effective_hyperparameters["num_generations"],
    }
    params = record.get("params")
    if not isinstance(params, Mapping) or dict(params) != expected_params:
        raise ValueError("Optuna best parameters differ from effective GRPO hyperparameters")
    if record.get("live_execution") != dict(live_execution):
        raise ValueError("Optuna best-parameter live execution differs from the run manifest")


def create_run_manifest(
    *,
    model_id: str,
    model_revision: str,
    tokenizer_id: str,
    tokenizer_revision: str,
    seed: int,
    generation_config: dict[str, Any],
    hyperparameters: dict[str, Any],
    execute_live_chaos: bool,
    kube_context: str,
    sft_parent: dict[str, Any],
    prompt_rows: list[dict[str, str]],
) -> dict[str, Any]:
    kube_context = require_live_kube_context(
        execute_live_chaos,
        kube_context,
        opt_in_flag="--execute-live-chaos",
    )
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
            "hyperparameters": dict(hyperparameters),
            "requested_hyperparameters": dict(hyperparameters),
            "effective_hyperparameters": None,
            "hyperparameter_selection": "pending",
            "live_execution": {
                "execute_live_chaos": execute_live_chaos,
                "kube_context": kube_context,
            },
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


def has_verified_final_rollout(
    path: Path,
    *,
    live_execution: dict[str, Any] | None = None,
    effective_hyperparameters: dict[str, Any] | None = None,
) -> bool:
    """Require at least one eligible final rollout and reject every invalid final row."""
    if not path.is_file():
        return False
    found_final_training_rollout = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        if row.get("rollout_phase") != "final_training":
            continue
        found_final_training_rollout = True
        verification = row.get("verification")
        settling = row.get("settling")
        verification_status = (
            verification.get("verification_status")
            if isinstance(verification, dict)
            else None
        )
        env_resolved = (
            verification.get("env_resolved")
            if isinstance(verification, dict)
            else None
        )
        stable_observations = (
            settling.get("stable_observations")
            if isinstance(settling, dict)
            else None
        )
        required_stable_observations = (
            settling.get("required_stable_observations")
            if isinstance(settling, dict)
            else None
        )
        if not (
            row.get("status") == "ok"
            and row.get("scorable") is True
            and isinstance(verification, dict)
            and verification_status in {"passed", "failed"}
            and isinstance(env_resolved, bool)
            and ((verification_status == "passed") is env_resolved)
            and isinstance(settling, dict)
            and settling.get("status") == "settled"
            and settling.get("stable") is True
            and settling.get("verification_status") == verification_status
            and required_stable_observations == 2
            and isinstance(stable_observations, int)
            and not isinstance(stable_observations, bool)
            and stable_observations >= required_stable_observations
            and isinstance(row.get("effective_hyperparameters"), dict)
            and bool(row["effective_hyperparameters"])
            and (
                live_execution is None
                or row.get("live_execution") == live_execution
            )
            and (
                effective_hyperparameters is None
                or row.get("effective_hyperparameters") == effective_hyperparameters
            )
        ):
            return False
    return found_final_training_rollout


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
        training = updated.get("training")
        if not isinstance(training, dict):
            raise TypeError("Completed GRPO checkpoint requires training provenance")
        _, effective_hyperparameters, selection = validate_hyperparameter_provenance(training)
        live_execution = (
            training.get("live_execution")
        )
        live_execution = live_execution if isinstance(live_execution, dict) else {}
        selected_context = require_live_kube_context(
            live_execution.get("execute_live_chaos"),
            live_execution.get("kube_context"),
            opt_in_flag="--execute-live-chaos",
        )
        expected_live_execution = {
            "execute_live_chaos": True,
            "kube_context": selected_context,
        }
        updated["checkpoint"] = checkpoint_inventory(path.parent)
        if not has_verified_final_rollout(
            path.parent / "rollout_trajectories.jsonl",
            live_execution=expected_live_execution,
            effective_hyperparameters=effective_hyperparameters,
        ):
            raise RuntimeError(
                "Completed GRPO checkpoint requires a verified final-training rollout row"
            )
        summary_path = path.parent / "training_summary.json"
        if not summary_path.is_file():
            raise RuntimeError("Completed GRPO checkpoint requires training_summary.json")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        validate_training_summary(training, summary)
        if selection == "optuna":
            validate_optuna_best(
                path.parent / "optuna_best.json",
                effective_hyperparameters=effective_hyperparameters,
                live_execution=expected_live_execution,
            )
        updated["completed_at"] = updated["updated_at"]
    write_manifest_atomic(path, updated)
    return updated
