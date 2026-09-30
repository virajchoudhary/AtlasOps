"""Hash-bound SFT preparation admission, separate from execution authority."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import socket
import stat
from pathlib import Path
from typing import Any

from training.sft_candidate import read_candidate_manifest
from training.sft_candidate_compatibility import validate_pilot_candidate
from training.sft_provenance import (
    MAX_VERIFIED_SFT_MANIFEST_BYTES,
    REPO_ROOT,
    TrainingCorpusSnapshot,
    _read_bounded_snapshot,
    canonical_bytes_sha256,
)

PLAN_PATH = REPO_ROOT / "config" / "sft_pilot_v4.json"
BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
CORPUS_HASH = "19606e4fec300f641c7c8b8a989497367a444a870d3491f225004c01df3ee5fd"
MANIFEST_HASH = "35c9fd63328ef1319f616f2a23a025be38ad99c594dcd8bb38b733e0ff44c67c"
PLAN_SHA256 = "914f7a5af5c22a355b235018d997302688598d0bdbdeda8eb8fcba3552f9e83e"
EXECUTION_APPROVAL_SHA256: str | None = None


def _read(path: Path) -> tuple[dict[str, Any], bytes]:
    raw, status = _read_bounded_snapshot(path, MAX_VERIFIED_SFT_MANIFEST_BYTES)
    if raw is None:
        raise ValueError(f"SFT pilot input is unsafe or unavailable ({status})")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError("Invalid SFT pilot JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("SFT pilot JSON must be an object")
    return value, raw


def validate_preparation(
    snapshot: TrainingCorpusSnapshot,
    *,
    model: str,
    model_revision: str,
    tokenizer: str,
    tokenizer_revision: str,
    role: str,
    hyperparameters: dict[str, Any],
) -> dict[str, Any]:
    """Verify the exact approved preparation, without granting execution."""
    if (model, tokenizer) != (BASE_MODEL, BASE_MODEL) or (
        model_revision, tokenizer_revision
    ) != (REVISION, REVISION):
        raise ValueError("SFT pilot requires the pinned Qwen base/tokenizer revision")
    if role != "all":
        raise ValueError("SFT pilot requires the approved four-role corpus")
    if canonical_bytes_sha256(snapshot.raw_bytes) != CORPUS_HASH:
        raise ValueError("SFT pilot corpus hash differs from D3 preparation approval")
    manifest, raw_manifest = _read(snapshot.source_path.parent / "sft_corpus_manifest.json")
    if hashlib.sha256(raw_manifest).hexdigest() != MANIFEST_HASH:
        raise ValueError("SFT pilot corpus manifest differs from D3 preparation approval")
    validate_pilot_candidate(snapshot, read_candidate_manifest(snapshot.source_path))
    plan, raw_plan = _read(PLAN_PATH)
    if canonical_bytes_sha256(raw_plan) != PLAN_SHA256:
        raise ValueError("SFT pilot preparation plan is not the reviewed hash-bound plan")
    if plan.get("schema_version") != "atlasops-sft-pilot-plan-v1":
        raise ValueError("Unknown SFT pilot plan")
    if plan.get("d3_status") != "APPROVED_FOR_PREPARATION":
        raise ValueError("SFT pilot preparation approval missing")
    if plan.get("execution_allowed") is not False:
        raise ValueError("Preparation plan cannot grant SFT execution")
    if plan.get("corpus_sha256") != CORPUS_HASH or plan.get("corpus_manifest_sha256") != MANIFEST_HASH:
        raise ValueError("SFT pilot plan corpus identity mismatch")
    for relative, digest in plan.get("required_files", {}).items():
        path = REPO_ROOT / relative
        if path.resolve().is_relative_to(REPO_ROOT) is False:
            raise ValueError("SFT pilot artifact escapes repository")
        content, status = _read_bounded_snapshot(path, 8 * 1024 * 1024)
        if content is None or canonical_bytes_sha256(content) != digest:
            raise ValueError(f"SFT pilot artifact hash mismatch: {relative} ({status})")
    if set(plan.get("required_files", {})) != {
        "docs/project/G7_D3_PREPARATION_APPROVAL_V1.md",
        "requirements/sft-pilot-linux-py312.lock",
        "infra/training/sft-pilot/Dockerfile",
        "artifacts/evidence/stage7/tokenizer_files_a09a354_v1.json",
        "artifacts/evidence/stage7/sft_tokenizer_preflight_v5.json",
        "training/templates/qwen2_5_tool_sft.jinja",
    }:
        raise ValueError("SFT pilot plan lacks required provenance artifacts")
    settings = plan.get("hyperparameters", {})
    for name, expected in settings.items():
        if hyperparameters.get(name) != expected or type(hyperparameters.get(name)) is not type(expected):
            raise ValueError(f"SFT pilot setting differs from preparation plan: {name}")
    if set(settings) != {
        "epochs", "learning_rate", "batch_size", "gradient_accumulation_steps",
        "max_sequence_length", "seed", "assistant_only_loss",
    }:
        raise ValueError("SFT pilot parameter contract incomplete")
    expected_fixed = {
        "quantization": {
            "load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": "bfloat16", "bnb_4bit_use_double_quant": True,
        },
        "lora": {
            "r": 16, "alpha": 32, "dropout": 0.05,
            "target_modules": [
                "q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj",
            ],
            "bias": "none",
        },
        "optimizer": "paged_adamw_8bit",
        "bf16": True,
    }
    for key, value in expected_fixed.items():
        if hyperparameters.get(key) != value:
            raise ValueError(f"SFT pilot fixed QLoRA setting differs: {key}")
    report, _ = _read(REPO_ROOT / "artifacts/evidence/stage7/sft_tokenizer_preflight_v5.json")
    if (
        report.get("schema_version") != "atlasops-sft-tokenizer-preflight-v1"
        or report.get("status") != "PASS"
        or report.get("candidate", {}).get("corpus_sha256_raw") != CORPUS_HASH
        or report.get("candidate", {}).get("manifest_sha256") != MANIFEST_HASH
        or report.get("settings", {}).get("tokenizer_revision") != REVISION
        or report.get("settings", {}).get("max_seq_length") != settings["max_sequence_length"]
        or report.get("summary", {}).get("checked_rows") != 68
        or report.get("summary", {}).get("truncated_row_count") != 0
        or report.get("summary", {}).get("mask_contract_pass") is not True
    ):
        raise ValueError("SFT pilot all-row tokenizer preflight did not pass")
    from training.sft_tokenizer_preflight import IMPLEMENTATION_PATHS

    source_hashes = report.get("artifacts", {}).get("implementation_file_sha256_canonical_lf", {})
    if set(source_hashes) != set(IMPLEMENTATION_PATHS):
        raise ValueError("SFT pilot preflight implementation inventory is incomplete")
    for relative, digest in source_hashes.items():
        path = REPO_ROOT / relative
        raw, _ = _read_bounded_snapshot(path, 8 * 1024 * 1024)
        if raw is None or canonical_bytes_sha256(raw) != digest:
            raise ValueError("SFT pilot tokenizer implementation differs from checked preflight")
    rows = report.get("rows")
    if not isinstance(rows, list) or len(rows) != len(snapshot.rows):
        raise ValueError("SFT pilot preflight lacks exact per-row evidence")
    for measured, original in zip(rows, snapshot.rows, strict=True):
        if (
            not isinstance(measured, dict)
            or any(measured.get(key) != original[key] for key in ("scenario_id", "case_id", "role"))
            or measured.get("mask_contract_pass") is not True
            or measured.get("lengths_equal") is not True
            or measured.get("truncation_disposition") != "FITS"
            or type(measured.get("token_count")) is not int
            or not 0 < measured["token_count"] <= settings["max_sequence_length"]
            or measured.get("input_ids_length") != measured["token_count"]
            or measured.get("assistant_masks_length") != measured["token_count"]
            or not 0 < measured.get("target_count", 0) < measured["token_count"]
            or not all(v is True or v is None for v in measured.get("region_checks", {}).values())
            or not measured.get("region_checks")
        ):
            raise ValueError("SFT pilot row-level preflight is missing or invalid")
    return {
        "schema_version": "atlasops-sft-pilot-admission-v1",
        "d3_status": "APPROVED_FOR_PREPARATION",
        "preparation_admissible": True,
        "execution_allowed": False,
        "model": model,
        "model_revision": model_revision,
        "tokenizer_revision": tokenizer_revision,
        "corpus_sha256": CORPUS_HASH,
        "corpus_manifest_sha256": MANIFEST_HASH,
        "plan_sha256": canonical_bytes_sha256(raw_plan),
        "remaining_approvals": [
            "D1 verified remote host/entitlement/storage",
            "D2 immutable model-weight transfer and inventory",
            "named bounded SFT execution with verified runtime and independent reload",
        ],
    }


def require_execution_authority(
    admission: dict[str, Any], approval_path: Path | None = None,
    *, output_dir: Path | None = None,
) -> dict[str, Any]:
    """A future reviewed approval digest, not arbitrary JSON/flags, opens a run."""
    if EXECUTION_APPROVAL_SHA256 is None or approval_path is None:
        raise ValueError(
            "SFT pilot preparation is approved; remote host, weight-transfer and "
            "named execution approval remain missing. Training refused."
        )
    approval, raw = _read(approval_path)
    if hashlib.sha256(raw).hexdigest() != EXECUTION_APPROVAL_SHA256:
        raise ValueError("SFT execution approval is not the reviewed pinned record")
    if (
        approval.get("schema_version") != "atlasops-sft-execution-approval-v1"
        or approval.get("execution_allowed") is not True
        or not isinstance(approval.get("approved_by"), str)
        or not approval["approved_by"].strip()
        or approval.get("plan_sha256") != admission["plan_sha256"]
        or approval.get("corpus_sha256") != CORPUS_HASH
        or approval.get("corpus_manifest_sha256") != MANIFEST_HASH
        or not isinstance(approval.get("run_id"), str)
        or not approval["run_id"].startswith("sft-pilot-")
    ):
        raise ValueError("SFT execution approval identity is invalid")
    from training.sft_provenance import has_redirecting_path_component, source_provenance

    source = source_provenance()
    if (
        source["git_dirty"] is not False
        or approval.get("source_git_sha") != source["git_sha"]
        or output_dir is None
        or not Path(approval.get("output_dir", "")).is_absolute()
        or Path(approval["output_dir"]) != output_dir.absolute()
        or output_dir.exists()
        or has_redirecting_path_component(output_dir)
    ):
        raise ValueError("SFT execution source/output differs from the approved clean run")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("SFT execution requires the approved Linux NVIDIA host")
    package_versions = approval.get("package_versions")
    plan, _ = _read(PLAN_PATH)
    if package_versions != plan.get("environment", {}).get("package_versions") or not package_versions:
        raise ValueError("SFT execution requires complete runtime package provenance")
    for package, version in package_versions.items():
        if importlib.metadata.version(package) != version:
            raise ValueError("SFT runtime package version differs from approved environment")
    host = approval.get("host")
    if (
        not isinstance(host, dict)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", host.get("image_digest", "")) is None
        or host.get("entitlement_verified") is not True
        or host.get("storage_verified") is not True
        or host.get("budget_approved") is not True
        or host.get("hostname") != socket.gethostname()
        or host.get("python_version") != platform.python_version()
        or host.get("python_version") != plan.get("environment", {}).get("python_version")
    ):
        raise ValueError("SFT execution host/resource authority is incomplete")
    runtime_manifest_path = host.get("runtime_manifest")
    if not isinstance(runtime_manifest_path, str):
        raise ValueError("SFT execution requires independent host runtime provenance")
    runtime, runtime_raw = _read(Path(runtime_manifest_path))
    if (
        hashlib.sha256(runtime_raw).hexdigest() != host.get("runtime_manifest_sha256")
        or runtime.get("image_digest") != host["image_digest"]
        or runtime.get("hostname") != host["hostname"]
        or runtime.get("package_versions") != package_versions
        or runtime.get("lock_sha256") != plan["required_files"]["requirements/sft-pilot-linux-py312.lock"]
    ):
        raise ValueError("SFT execution host provenance mismatch")
    image_attestation_path = host.get("image_attestation")
    if not isinstance(image_attestation_path, str):
        raise ValueError("SFT execution requires independent image attestation")
    attestation, attestation_raw = _read(Path(image_attestation_path))
    if (
        hashlib.sha256(attestation_raw).hexdigest() != host.get("image_attestation_sha256")
        or attestation.get("image_digest") != host["image_digest"]
        or attestation.get("hostname") != host["hostname"]
        or attestation.get("verified") is not True
        or not isinstance(attestation.get("verified_by"), str)
        or not attestation["verified_by"].strip()
    ):
        raise ValueError("SFT execution image attestation is missing or mismatched")
    storage = runtime.get("storage", {})
    if (
        type(storage.get("free_bytes")) is not int
        or storage["free_bytes"] < 20 * 1024**3
        or attestation.get("persistent_storage_verified") is not True
        or attestation.get("storage_quota_verified") is not True
        or runtime.get("os_package_inventory", {}).get("status") != "RECORDED"
        or re.fullmatch(
            r"[0-9a-f]{64}",
            runtime.get("os_package_inventory", {}).get("sha256", ""),
        ) is None
        or attestation.get("os_package_inventory_sha256")
        != runtime.get("os_package_inventory", {}).get("sha256")
    ):
        raise ValueError("SFT execution requires verified disk/quota and OS package provenance")
    existing_parent = output_dir.absolute().parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    if shutil.disk_usage(existing_parent).free < 20 * 1024**3:
        raise ValueError("SFT execution requires at least 20 GiB free storage")
    inventory_path = approval.get("model_files_manifest")
    if not isinstance(inventory_path, str) or not Path(inventory_path).is_absolute():
        raise ValueError("SFT execution requires an approved local model-file inventory")
    inventory, raw_inventory = _read(Path(inventory_path))
    if (
        hashlib.sha256(raw_inventory).hexdigest() != approval.get("model_files_manifest_sha256")
        or inventory.get("revision") != REVISION
        or inventory.get("repository") != BASE_MODEL
    ):
        raise ValueError("SFT execution model-file identity mismatch")
    files = inventory.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("SFT execution model-file inventory is empty")
    model_snapshot = inventory.get("snapshot_dir")
    if (
        not isinstance(model_snapshot, str)
        or not Path(model_snapshot).is_absolute()
        or Path(model_snapshot).name != REVISION
        or Path(model_snapshot).parent.name != "snapshots"
        or Path(model_snapshot).parents[1].name != "models--Qwen--Qwen2.5-7B-Instruct"
        or has_redirecting_path_component(Path(model_snapshot))
    ):
        raise ValueError("SFT execution needs a stable immutable-revision snapshot directory")
    for absolute, digest in files.items():
        path = Path(absolute)
        from training.sft_provenance import file_sha256, has_redirecting_path_component

        if (
            not path.is_absolute() or not path.is_relative_to(Path(model_snapshot))
            or has_redirecting_path_component(path) or file_sha256(path) != digest
            or not stat.S_ISREG(path.stat().st_mode)
            or path.stat().st_mode & 0o222
        ):
            raise ValueError("SFT execution local model file failed inventory verification")
    from scripts.stage_sft_tokenizer import FILES as REQUIRED_TOKENIZER_FILES

    expected_tokenizer = _read(
        REPO_ROOT / "artifacts/evidence/stage7/tokenizer_files_a09a354_v1.json"
    )[0]["files"]
    for name in REQUIRED_TOKENIZER_FILES:
        if files.get(str(Path(model_snapshot) / name)) != expected_tokenizer[name]["sha256"]:
            raise ValueError("SFT execution model snapshot lacks pinned tokenizer/config/license file")
    shard_index = Path(model_snapshot) / "model.safetensors.index.json"
    index, _ = _read(shard_index)
    shards = set(index.get("weight_map", {}).values())
    if not shards or any(
        not isinstance(name, str) or Path(name).name != name or not name.endswith(".safetensors")
        or str(Path(model_snapshot) / name) not in files for name in shards
    ):
        raise ValueError("SFT execution model snapshot lacks complete shard inventory")
    if Path(model_snapshot).stat().st_mode & 0o222:
        raise ValueError("SFT execution model snapshot directory must be read-only")
    if not any(Path(name).suffix == ".safetensors" for name in files):
        raise ValueError("SFT execution inventory lacks model weights")
    snapshot_files = {
        str(path) for path in Path(model_snapshot).rglob("*") if path.is_file()
    }
    if snapshot_files != set(files):
        raise ValueError("SFT execution snapshot inventory is not complete")
    import torch

    if (
        not torch.cuda.is_available() or not torch.cuda.is_bf16_supported()
        or torch.cuda.device_count() != 1
    ):
        raise ValueError("SFT execution requires verified NVIDIA CUDA/BF16 compatibility")
    if (
        torch.cuda.get_device_name(0) != host.get("gpu_name")
        or torch.cuda.get_device_properties(0).total_memory != host.get("gpu_memory_bytes")
        or torch.version.cuda != host.get("cuda_runtime")
    ):
        raise ValueError("SFT execution GPU identity differs from approved host")
    approval = dict(approval)
    approval["model_cache_dir"] = str(Path(model_snapshot).parents[2])
    return approval
