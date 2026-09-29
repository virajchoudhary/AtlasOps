"""Durable, fail-closed provenance for online GRPO checkpoints."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

from config.splits import TRAIN_SPLIT
from training.grpo_environment import require_live_kube_context
from training.sft_provenance import (
    TOKENIZER_REVISION_BASIS_LOADER_MATCH,
    TOKENIZER_REVISION_BASIS_PIN_ENFORCED,
    canonical_json_sha256,
    file_sha256,
    has_redirecting_path_component,
    resolve_tokenizer_revision,
    runtime_environment,
    validate_hf_commit_revision,
    validate_hf_reference,
    validate_resolved_hf_commit,
    write_manifest_atomic,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "grpo_run_manifest.json"
ADAPTER_WEIGHT_NAMES = frozenset({"adapter_model.safetensors", "adapter_model.bin"})
PARTIAL_ARTIFACT_NAMES = (
    "rollout_trajectories.jsonl",
    "training_summary.json",
)
MAX_PARTIAL_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_PARTIAL_ARTIFACT_SECONDS = 5.0
PARTIAL_ARTIFACT_HASH_CHUNK_BYTES = 1024 * 1024
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
MODEL_REVISION_BASIS_LOADER_MATCH = "LOADER_EXPOSED_COMMIT_HASH_MATCH"


class RunPlanMismatch(ValueError):
    """A direct G9 invocation differs from the request persisted by the CLI."""


def _plan_values_equal(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
            right,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return False


def validate_grpo_model_references(
    *,
    model_id: str,
    model_revision: str,
    tokenizer_id: str,
    tokenizer_revision: str,
) -> None:
    """Apply the G7 repository-id and immutable-commit contract to G9 inputs."""
    validate_hf_reference(model_id, model_revision, label="base model")
    validate_hf_reference(tokenizer_id, tokenizer_revision, label="tokenizer")


def validate_new_output_directory(path: Path) -> Path:
    """Validate that a prospective output path is fresh and has no redirects."""
    output_dir = Path(os.path.abspath(Path(path).expanduser()))
    if has_redirecting_path_component(output_dir):
        raise ValueError(
            "GRPO output path must not contain symlink, reparse, or hard-link redirects"
        )
    if output_dir.exists():
        raise FileExistsError(f"GRPO output directory already exists: {output_dir}")
    return output_dir


def claim_new_output_directory(path: Path) -> Path:
    """Atomically claim a fresh output directory without reusing an existing run."""
    output_dir = validate_new_output_directory(path)
    output_dir.mkdir(parents=True, exist_ok=False)
    if has_redirecting_path_component(output_dir):
        raise ValueError(
            "GRPO output path must not contain symlink, reparse, or hard-link redirects"
        )
    return output_dir


def validate_tokenizer_loader_revision(
    tokenizer: Any,
    *,
    requested_revision: str,
) -> tuple[str, str]:
    """Verify an exposed tokenizer pin, or preserve the honest pinned-loader basis."""
    init_kwargs = getattr(tokenizer, "init_kwargs", None)
    exposed_revision = (
        init_kwargs.get("_commit_hash")
        if isinstance(init_kwargs, Mapping)
        else None
    )
    return resolve_tokenizer_revision(requested_revision, exposed_revision)


def resolve_loader_provenance(
    model: Any,
    tokenizer: Any,
    *,
    model_revision: str,
    tokenizer_revision: str,
) -> dict[str, Any]:
    """Verify loader-reported commits and return evidence for this loaded pair."""
    validate_hf_commit_revision(model_revision, label="Requested base model revision")
    resolved_model_revision = validate_resolved_hf_commit(
        model_revision,
        getattr(getattr(model, "config", None), "_commit_hash", None),
        label="base model",
    )
    resolved_tokenizer_revision, tokenizer_revision_basis = (
        validate_tokenizer_loader_revision(
            tokenizer,
            requested_revision=tokenizer_revision,
        )
    )
    return {
        "base_model": {
            "resolved_revision": resolved_model_revision,
            "resolved_revision_basis": MODEL_REVISION_BASIS_LOADER_MATCH,
        },
        "tokenizer": {
            "resolved_revision": resolved_tokenizer_revision,
            "resolved_revision_basis": tokenizer_revision_basis,
        },
    }


def record_loader_provenance(
    path: Path,
    *,
    model: Any,
    tokenizer: Any,
    model_id: str,
    model_revision: str,
    tokenizer_id: str,
    tokenizer_revision: str,
) -> dict[str, Any]:
    """Persist verified loader identity while the GRPO run is still in progress."""
    validate_grpo_model_references(
        model_id=model_id,
        model_revision=model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=tokenizer_revision,
    )
    identity = resolve_loader_provenance(
        model,
        tokenizer,
        model_revision=model_revision,
        tokenizer_revision=tokenizer_revision,
    )
    manifest_path = Path(path)
    if has_redirecting_path_component(manifest_path):
        raise ValueError("GRPO run manifest path must not contain redirects")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("status") != "running":
        raise RuntimeError("GRPO loader identity requires a running, claimed run")

    for key, expected_id, expected_revision in (
        ("base_model", model_id, model_revision),
        ("tokenizer", tokenizer_id, tokenizer_revision),
    ):
        record = manifest.get(key)
        if (
            not isinstance(record, dict)
            or record.get("id") != expected_id
            or record.get("requested_revision") != expected_revision
        ):
            raise ValueError(f"GRPO {key} identity differs from the run manifest")

    now = datetime.now(UTC).isoformat()
    for key in ("base_model", "tokenizer"):
        record = dict(manifest[key])
        observed = identity[key]
        existing_revision = record.get("resolved_revision")
        if existing_revision not in (None, observed["resolved_revision"]):
            raise ValueError(f"GRPO loader returned inconsistent {key} revisions")
        existing_basis = record.get("resolved_revision_basis")
        new_basis = observed["resolved_revision_basis"]
        if key == "tokenizer" and existing_basis in {
            TOKENIZER_REVISION_BASIS_LOADER_MATCH,
            TOKENIZER_REVISION_BASIS_PIN_ENFORCED,
        } and new_basis in {
            TOKENIZER_REVISION_BASIS_LOADER_MATCH,
            TOKENIZER_REVISION_BASIS_PIN_ENFORCED,
        }:
            if TOKENIZER_REVISION_BASIS_LOADER_MATCH in {existing_basis, new_basis}:
                new_basis = TOKENIZER_REVISION_BASIS_LOADER_MATCH
        elif existing_basis not in (None, new_basis):
            raise ValueError(f"GRPO loader returned inconsistent {key} revision basis")
        record["resolved_revision"] = observed["resolved_revision"]
        record["resolved_revision_basis"] = new_basis
        manifest[key] = record

    manifest["model_loading"] = {
        "status": "verified",
        "verified_at": now,
    }
    write_manifest_atomic(manifest_path, manifest)
    return manifest


def validate_loader_provenance(manifest: Mapping[str, Any]) -> None:
    """Reject completion unless a loader verified the pinned model/tokenizer pair."""
    model = manifest.get("base_model")
    tokenizer = manifest.get("tokenizer")
    loading = manifest.get("model_loading")
    if (
        not isinstance(model, Mapping)
        or not isinstance(tokenizer, Mapping)
        or not isinstance(loading, Mapping)
        or loading.get("status") != "verified"
        or not isinstance(loading.get("verified_at"), str)
        or not loading["verified_at"]
    ):
        raise ValueError("GRPO run lacks loader-verified model/tokenizer provenance")
    validate_hf_reference(
        model.get("id"),
        model.get("requested_revision"),
        label="base model",
    )
    validate_hf_reference(
        tokenizer.get("id"),
        tokenizer.get("requested_revision"),
        label="tokenizer",
    )
    if model.get("resolved_revision_basis") != MODEL_REVISION_BASIS_LOADER_MATCH:
        raise ValueError("GRPO base model loader commit was not verified")
    validate_resolved_hf_commit(
        model["requested_revision"],
        model.get("resolved_revision"),
        label="base model",
    )
    tokenizer_basis = tokenizer.get("resolved_revision_basis")
    if tokenizer_basis not in {
        TOKENIZER_REVISION_BASIS_LOADER_MATCH,
        TOKENIZER_REVISION_BASIS_PIN_ENFORCED,
    }:
        raise ValueError("GRPO tokenizer loader revision basis is missing or invalid")
    if tokenizer_basis == TOKENIZER_REVISION_BASIS_LOADER_MATCH:
        validate_resolved_hf_commit(
            tokenizer["requested_revision"],
            tokenizer.get("resolved_revision"),
            label="tokenizer",
        )
    else:
        validate_hf_commit_revision(
            tokenizer.get("resolved_revision"),
            label="Resolved tokenizer revision",
        )
        if tokenizer["resolved_revision"] != tokenizer["requested_revision"]:
            raise ValueError("GRPO tokenizer pin differs from the requested revision")


def mark_training_started(
    manifest_path: Path,
    *,
    model_id: str,
    model_revision: str,
    tokenizer_id: str,
    tokenizer_revision: str,
    sft_parent: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist a one-shot execution marker and reject reuse of partial run outputs."""
    manifest_path = Path(manifest_path)
    if has_redirecting_path_component(manifest_path):
        raise ValueError("GRPO run manifest path must not contain redirects")
    if not manifest_path.is_file():
        raise FileNotFoundError("GRPO training requires a claimed run manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("status") != "running":
        raise RuntimeError("GRPO training requires a running run manifest")
    training = manifest.get("training")
    if not isinstance(training, dict):
        raise RuntimeError("GRPO training manifest lacks training provenance")
    if training.get("execution_started_at"):
        raise FileExistsError("GRPO run execution was already started; resume is disabled")
    for key, expected_id, expected_revision in (
        ("base_model", model_id, model_revision),
        ("tokenizer", tokenizer_id, tokenizer_revision),
    ):
        record = manifest.get(key)
        if (
            not isinstance(record, dict)
            or record.get("id") != expected_id
            or record.get("requested_revision") != expected_revision
        ):
            raise ValueError(f"GRPO {key} identity differs from the run manifest")
    if manifest.get("sft_parent") != dict(sft_parent):
        raise ValueError("GRPO SFT parent changed after the run was planned")
    allowed_files = {manifest_path.name}
    unexpected = sorted(
        entry.name
        for entry in manifest_path.parent.iterdir()
        if entry.name not in allowed_files
    )
    if unexpected:
        raise FileExistsError(
            "GRPO output contains prior run artifacts; resume and overwrite are disabled"
        )
    training["execution_started_at"] = datetime.now(UTC).isoformat()
    manifest["training"] = training
    write_manifest_atomic(manifest_path, manifest)
    return manifest


def require_started_grpo_run(
    output_dir: Path,
    *,
    model_id: str,
    model_revision: str,
    tokenizer_id: str,
    tokenizer_revision: str,
    sft_parent: Mapping[str, Any],
) -> dict[str, Any]:
    """Require an in-progress, one-shot run before a caller can start Optuna."""
    output_dir = Path(os.path.abspath(Path(output_dir).expanduser()))
    if has_redirecting_path_component(output_dir):
        raise ValueError("GRPO output path must not contain redirects")
    manifest_path = output_dir / MANIFEST_NAME
    if has_redirecting_path_component(manifest_path) or not manifest_path.is_file():
        raise FileNotFoundError("Optuna requires the claimed GRPO run manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    training = manifest.get("training") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("status") != "running"
        or not isinstance(training, dict)
        or not training.get("execution_started_at")
    ):
        raise RuntimeError("Optuna requires a running, one-shot GRPO execution")
    for key, expected_id, expected_revision in (
        ("base_model", model_id, model_revision),
        ("tokenizer", tokenizer_id, tokenizer_revision),
    ):
        record = manifest.get(key)
        if (
            not isinstance(record, dict)
            or record.get("id") != expected_id
            or record.get("requested_revision") != expected_revision
        ):
            raise ValueError(f"Optuna {key} identity differs from the run manifest")
    if manifest.get("sft_parent") != dict(sft_parent):
        raise ValueError("Optuna SFT parent changed after the run was planned")
    unexpected = sorted(
        entry.name
        for entry in output_dir.iterdir()
        if entry.name != MANIFEST_NAME
    )
    if unexpected:
        raise FileExistsError(
            "GRPO output contains prior Optuna artifacts; resume is disabled"
        )
    return manifest


def validate_sft_parent_matches_run(
    manifest_path: Path,
    sft_parent: Mapping[str, Any],
) -> None:
    """Ensure the completed SFT checkpoint is the exact parent recorded at planning."""
    manifest_path = Path(manifest_path)
    if has_redirecting_path_component(manifest_path):
        raise ValueError("GRPO run manifest path must not contain redirects")
    if not manifest_path.is_file():
        raise FileNotFoundError("GRPO run manifest missing before model loading")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("status") != "running"
        or manifest.get("sft_parent") != dict(sft_parent)
    ):
        raise ValueError("GRPO SFT parent changed after the run was planned")


def validate_grpo_run_plan(
    manifest_path: Path,
    *,
    model_id: str,
    model_revision: str,
    tokenizer_id: str,
    tokenizer_revision: str,
    sft_parent: Mapping[str, Any],
    training_fields: Mapping[str, Any],
    requested_hyperparameters: Mapping[str, Any],
    require_started: bool,
    exact_hyperparameters: bool = True,
) -> dict[str, Any]:
    """Bind direct training/search arguments to the already persisted G9 request."""
    manifest_path = Path(manifest_path)
    if has_redirecting_path_component(manifest_path) or not manifest_path.is_file():
        raise RunPlanMismatch("persisted G9 run manifest is missing or redirected")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    training = manifest.get("training") if isinstance(manifest, dict) else None
    if not isinstance(manifest, dict) or manifest.get("status") != "running":
        raise RunPlanMismatch("persisted G9 run is not in the running state")
    if not isinstance(training, dict):
        raise RunPlanMismatch("persisted G9 training plan is missing")
    has_started = bool(training.get("execution_started_at"))
    if has_started is not require_started:
        phase = "started" if require_started else "not yet started"
        raise RunPlanMismatch(f"persisted G9 run must be {phase}")

    for key, expected_id, expected_revision in (
        ("base_model", model_id, model_revision),
        ("tokenizer", tokenizer_id, tokenizer_revision),
    ):
        record = manifest.get(key)
        if (
            not isinstance(record, Mapping)
            or record.get("id") != expected_id
            or record.get("requested_revision") != expected_revision
        ):
            raise RunPlanMismatch(f"{key} identity differs from the persisted G9 training plan")
    if not _plan_values_equal(manifest.get("sft_parent"), dict(sft_parent)):
        raise RunPlanMismatch("SFT parent differs from the persisted G9 training plan")

    for key, expected in training_fields.items():
        if not _plan_values_equal(training.get(key), expected):
            raise RunPlanMismatch(f"training {key} differs from the persisted G9 training plan")

    recorded_requested = training.get("requested_hyperparameters")
    recorded_legacy = training.get("hyperparameters")
    if (
        not isinstance(recorded_requested, Mapping)
        or not isinstance(recorded_legacy, Mapping)
        or not _plan_values_equal(dict(recorded_legacy), dict(recorded_requested))
    ):
        raise RunPlanMismatch("requested hyperparameter records are inconsistent")
    if exact_hyperparameters:
        matches = _plan_values_equal(
            dict(recorded_requested),
            dict(requested_hyperparameters),
        )
    else:
        matches = all(
            _plan_values_equal(recorded_requested.get(key), value)
            for key, value in requested_hyperparameters.items()
        )
    if not matches:
        raise RunPlanMismatch(
            "requested hyperparameters differ from the persisted G9 training plan"
        )
    return manifest


def validate_sft_parent(
    checkpoint: Path,
    *,
    model_id: str,
    model_revision: str,
    tokenizer_id: str,
    tokenizer_revision: str,
) -> dict[str, Any]:
    """Bind GRPO to the completed, byte-validated G7 adapter."""
    validate_grpo_model_references(
        model_id=model_id,
        model_revision=model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=tokenizer_revision,
    )
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
    if tokenizer.get("resolved_revision_basis") not in {
        TOKENIZER_REVISION_BASIS_LOADER_MATCH,
        TOKENIZER_REVISION_BASIS_PIN_ENFORCED,
    }:
        raise ValueError("SFT tokenizer revision provenance basis is missing or invalid")
    if manifest["source"].get("git_dirty") is not False:
        raise ValueError("GRPO requires an SFT checkpoint trained from clean source")
    return {
        "checkpoint_path": str(checkpoint),
        "manifest_sha256": manifest_sha256,
        "checkpoint_tree_sha256": manifest["checkpoint"]["tree_sha256"],
        "training_source_sha": manifest["source"]["git_sha"],
        "train_corpus_sha256": manifest["dataset"]["corpus_sha256_canonical_lf"],
        "train_split_sha256": manifest["dataset"]["split_sha256"],
        "base_model_id": base["id"],
        "base_model_revision": base["resolved_revision"],
        "tokenizer_id": tokenizer["id"],
        "tokenizer_revision": tokenizer["resolved_revision"],
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
    total_steps = summary.get("total_steps")
    if (
        not isinstance(total_steps, int)
        or isinstance(total_steps, bool)
        or total_steps <= 0
    ):
        raise ValueError("Training summary total_steps must be a positive integer")
    if not isinstance(summary.get("trainer_log_history"), list):
        raise TypeError("Training summary trainer_log_history must be a list")
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
    validate_grpo_model_references(
        model_id=model_id,
        model_revision=model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=tokenizer_revision,
    )
    if seed < 0:
        raise ValueError("GRPO seed must be nonnegative")
    if not sft_parent or not all(
        sft_parent.get(key)
        for key in ("checkpoint_path", "manifest_sha256", "checkpoint_tree_sha256", "train_split_sha256")
    ):
        raise ValueError("GRPO requires validated SFT checkpoint provenance")
    if (
        sft_parent.get("base_model_id") != model_id
        or sft_parent.get("base_model_revision") != model_revision
        or sft_parent.get("tokenizer_id") != tokenizer_id
        or sft_parent.get("tokenizer_revision") != tokenizer_revision
    ):
        raise ValueError("GRPO SFT parent must match the exact base model/tokenizer pins")
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
        "base_model": {
            "id": model_id,
            "requested_revision": model_revision,
            "resolved_revision": None,
            "resolved_revision_basis": None,
        },
        "tokenizer": {
            "id": tokenizer_id,
            "requested_revision": tokenizer_revision,
            "resolved_revision": None,
            "resolved_revision_basis": None,
        },
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


def _is_reparse_point(metadata: os.stat_result) -> bool:
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or (
        reparse_attribute != 0
        and bool(getattr(metadata, "st_file_attributes", 0) & reparse_attribute)
    )


def _has_stable_identity(metadata: os.stat_result) -> bool:
    return bool(metadata.st_dev and metadata.st_ino)


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        _has_stable_identity(left)
        and _has_stable_identity(right)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
    )


_STABLE_DIRECTORY_HANDLES_SUPPORTED = (
    os.name != "nt"
    and all(
        function in getattr(os, "supports_dir_fd", set())
        for function in (os.open, os.stat, os.rename, os.unlink)
    )
    and os.stat in getattr(os, "supports_follow_symlinks", set())
    and all(
        hasattr(os, flag)
        for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    )
)


def _supports_stable_directory_handles() -> bool:
    return _STABLE_DIRECTORY_HANDLES_SUPPORTED


def require_stable_failure_persistence() -> None:
    if not _supports_stable_directory_handles():
        raise RuntimeError(
            "Live GRPO training requires stable directory-handle operations "
            "to persist failed or interrupted evidence"
        )


def _open_stable_directory(
    path: Path,
    *,
    deadline: float,
) -> tuple[int | None, str | None]:
    if not _supports_stable_directory_handles():
        return None, "stable_directory_handle_unavailable"
    absolute = Path(os.path.abspath(path))
    if not absolute.anchor:
        return None, "directory_open_failed"

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        current_fd = os.open(absolute.anchor, flags)
    except OSError:
        return None, "directory_open_failed"

    for part in absolute.parts[1:]:
        if monotonic() >= deadline:
            os.close(current_fd)
            return None, "time_limit_exceeded"
        try:
            child_fd = os.open(part, flags, dir_fd=current_fd)
        except OSError:
            try:
                os.close(current_fd)
            except OSError:
                pass
            return None, "directory_open_failed"
        try:
            os.close(current_fd)
        except OSError:
            os.close(child_fd)
            return None, "directory_open_failed"
        current_fd = child_fd
    return current_fd, None


def _unverified_partial_artifact(
    name: str,
    reason: str,
    *,
    presence: str = "unknown",
) -> dict[str, str]:
    return {"path": name, "presence": presence, "reason": reason}


def _build_partial_inventory(
    files: list[dict[str, Any]],
    unverified: list[dict[str, str]],
    bytes_hashed: int,
    *,
    inventory_status: str | None = None,
) -> dict[str, Any]:
    return {
        "files": files,
        "unverified": unverified,
        "tree_sha256": canonical_json_sha256(files),
        "bytes_hashed": bytes_hashed,
        "inventory_status": inventory_status or ("partial" if unverified else "complete"),
        "limits": {
            "max_total_bytes": MAX_PARTIAL_ARTIFACT_BYTES,
            "max_duration_seconds": MAX_PARTIAL_ARTIFACT_SECONDS,
        },
    }


def _unavailable_partial_inventory(reason: str) -> dict[str, Any]:
    return _build_partial_inventory(
        [],
        [
            _unverified_partial_artifact(name, reason)
            for name in PARTIAL_ARTIFACT_NAMES
        ],
        0,
        inventory_status="unavailable",
    )


def _require_run_directory_identity(
    directory_fd: int,
    manifest: dict[str, Any],
    *,
    allow_initial_binding: bool,
) -> None:
    metadata = os.fstat(directory_fd)
    if not stat.S_ISDIR(metadata.st_mode) or not _has_stable_identity(metadata):
        raise OSError("GRPO run directory identity is unavailable")
    current = {"device": metadata.st_dev, "inode": metadata.st_ino}
    expected = manifest.get("run_directory_identity")
    if expected is None and allow_initial_binding:
        manifest["run_directory_identity"] = current
    elif expected != current:
        raise OSError("GRPO run directory identity changed or was never bound")


def _partial_artifact_record_at(
    directory_fd: int,
    name: str,
    *,
    remaining_bytes: int,
    deadline: float,
) -> tuple[dict[str, Any] | None, int, dict[str, str] | None]:
    if monotonic() >= deadline:
        return None, 0, _unverified_partial_artifact(name, "time_limit_exceeded")
    try:
        initial_metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None, 0, None
    except OSError:
        return None, 0, _unverified_partial_artifact(name, "artifact_stat_failed")
    if _is_reparse_point(initial_metadata):
        return None, 0, _unverified_partial_artifact(name, "path_redirect", presence="redirect")
    if not stat.S_ISREG(initial_metadata.st_mode):
        return None, 0, _unverified_partial_artifact(name, "not_regular_file", presence="present")
    if initial_metadata.st_nlink != 1:
        return None, 0, _unverified_partial_artifact(name, "path_redirect", presence="redirect")
    if initial_metadata.st_size > remaining_bytes:
        return None, 0, _unverified_partial_artifact(name, "max_total_bytes_exceeded", presence="present")

    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError:
        return None, 0, _unverified_partial_artifact(name, "artifact_open_failed", presence="present")

    bytes_read = 0
    try:
        opened_metadata = os.fstat(descriptor)
        if (
            _is_reparse_point(opened_metadata)
            or not stat.S_ISREG(opened_metadata.st_mode)
        ):
            return None, 0, _unverified_partial_artifact(name, "not_regular_file", presence="present")
        if opened_metadata.st_nlink != 1:
            return None, 0, _unverified_partial_artifact(name, "path_redirect", presence="redirect")
        if not _same_file_identity(initial_metadata, opened_metadata):
            return None, 0, _unverified_partial_artifact(name, "file_identity_unavailable", presence="present")
        if (
            initial_metadata.st_size != opened_metadata.st_size
            or initial_metadata.st_mtime_ns != opened_metadata.st_mtime_ns
        ):
            return None, 0, _unverified_partial_artifact(name, "file_changed_during_read", presence="present")
        if opened_metadata.st_size > remaining_bytes:
            return None, 0, _unverified_partial_artifact(name, "max_total_bytes_exceeded", presence="present")

        digest = hashlib.sha256()
        while bytes_read < opened_metadata.st_size:
            if monotonic() >= deadline:
                return None, bytes_read, _unverified_partial_artifact(name, "time_limit_exceeded", presence="present")
            chunk = os.read(
                descriptor,
                min(PARTIAL_ARTIFACT_HASH_CHUNK_BYTES, opened_metadata.st_size - bytes_read),
            )
            if not chunk:
                break
            digest.update(chunk)
            bytes_read += len(chunk)

        if monotonic() >= deadline:
            return None, bytes_read, _unverified_partial_artifact(name, "time_limit_exceeded", presence="present")
        final_metadata = os.fstat(descriptor)
        current_metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None, bytes_read, _unverified_partial_artifact(name, "file_changed_during_read", presence="present")
    except OSError:
        return None, bytes_read, _unverified_partial_artifact(name, "artifact_read_failed", presence="present")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass

    if (
        _is_reparse_point(current_metadata)
        or opened_metadata.st_nlink != 1
        or final_metadata.st_nlink != 1
        or current_metadata.st_nlink != 1
    ):
        return None, bytes_read, _unverified_partial_artifact(name, "path_redirect", presence="redirect")
    if (
        not _same_file_identity(opened_metadata, final_metadata)
        or not _same_file_identity(opened_metadata, current_metadata)
        or bytes_read != opened_metadata.st_size
        or final_metadata.st_size != opened_metadata.st_size
        or final_metadata.st_mtime_ns != opened_metadata.st_mtime_ns
        or current_metadata.st_size != opened_metadata.st_size
        or current_metadata.st_mtime_ns != opened_metadata.st_mtime_ns
    ):
        return None, bytes_read, _unverified_partial_artifact(name, "file_changed_during_read", presence="present")
    return {
        "path": name,
        "size_bytes": bytes_read,
        "sha256": digest.hexdigest(),
    }, bytes_read, None


def _inventory_partial_artifacts_at(
    directory_fd: int,
    *,
    deadline: float,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    unverified: list[dict[str, str]] = []
    bytes_hashed = 0
    for index, name in enumerate(PARTIAL_ARTIFACT_NAMES):
        if monotonic() >= deadline:
            unverified.extend(
                _unverified_partial_artifact(
                    remaining_name,
                    "time_limit_exceeded",
                )
                for remaining_name in PARTIAL_ARTIFACT_NAMES[index:]
            )
            break
        record, bytes_read, skipped = _partial_artifact_record_at(
            directory_fd,
            name,
            remaining_bytes=max(0, MAX_PARTIAL_ARTIFACT_BYTES - bytes_hashed),
            deadline=deadline,
        )
        bytes_hashed += bytes_read
        if record is not None:
            files.append(record)
        if skipped is not None:
            unverified.append(skipped)
    return _build_partial_inventory(files, unverified, bytes_hashed)


def partial_artifact_inventory(output_dir: Path) -> dict[str, Any]:
    """Inventory only allowlisted partial files through a stable directory handle."""
    deadline = monotonic() + MAX_PARTIAL_ARTIFACT_SECONDS
    opened, failure_reason = _open_stable_directory(output_dir, deadline=deadline)
    if opened is None:
        return _unavailable_partial_inventory(
            failure_reason or "stable_directory_handle_unavailable"
        )
    try:
        return _inventory_partial_artifacts_at(
            opened,
            deadline=deadline,
        )
    finally:
        try:
            os.close(opened)
        except OSError:
            pass


def _write_manifest_atomic_at(
    directory_fd: int,
    name: str,
    manifest: dict[str, Any],
) -> None:
    temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.rename(
            temporary_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
    except OSError:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except OSError:
            pass
        raise


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
    if status in {"planned", "running", "failed", "interrupted"}:
        deadline = monotonic() + MAX_PARTIAL_ARTIFACT_SECONDS
        opened, failure_reason = _open_stable_directory(
            path.parent,
            deadline=deadline,
        )
        if opened is None:
            if status in {"planned", "running"} and not _supports_stable_directory_handles():
                write_manifest_atomic(path, updated)
                return updated
            raise OSError(
                "Cannot safely persist GRPO status without a stable "
                f"directory handle ({failure_reason or 'directory_open_failed'})"
            )
        try:
            _require_run_directory_identity(
                opened,
                updated,
                allow_initial_binding=status in {"planned", "running"},
            )
            if status in {"failed", "interrupted"}:
                updated["checkpoint"] = None
                updated["partial_artifacts"] = _inventory_partial_artifacts_at(
                    opened,
                    deadline=deadline,
                )
            _write_manifest_atomic_at(opened, path.name, updated)
        finally:
            try:
                os.close(opened)
            except OSError:
                pass
        return updated
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
        try:
            validate_loader_provenance(updated)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "Completed GRPO run requires loader-verified model/tokenizer provenance"
            ) from exc
        updated["checkpoint"] = checkpoint_inventory(path.parent)
        updated["completed_at"] = updated["updated_at"]
    write_manifest_atomic(path, updated)
    return updated
