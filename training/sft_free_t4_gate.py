"""Hash-bound free-Colab-T4 SFT preparation and execution authority."""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import socket
import sys
from datetime import UTC, date, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from training.sft_provenance import (
    MAX_VERIFIED_SFT_MANIFEST_BYTES,
    REPO_ROOT,
    TrainingCorpusSnapshot,
    _read_bounded_snapshot,
    canonical_bytes_sha256,
    file_sha256,
)

PROFILE = "free-t4-v1"
PLAN_PATH = REPO_ROOT / "config" / "sft_free_t4_v1.json"
PLAN_SHA256 = "29a217a46e2d7cdcc1b85938b1a2aecac1043462980ca0b05f4fa0681adddc82"
PLAN_RAW_SHA256 = "29a217a46e2d7cdcc1b85938b1a2aecac1043462980ca0b05f4fa0681adddc82"
PARENT_PLAN_SHA256 = "914f7a5af5c22a355b235018d997302688598d0bdbdeda8eb8fcba3552f9e83e"
BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
CORPUS_HASH = "19606e4fec300f641c7c8b8a989497367a444a870d3491f225004c01df3ee5fd"
MANIFEST_HASH = "35c9fd63328ef1319f616f2a23a025be38ad99c594dcd8bb38b733e0ff44c67c"
LOCK_PATH = "requirements/sft-pilot-linux-py312.lock"
LOCK_SHA256 = "b649bfa91f1232b9b0fbf247927516a281c8769a6556330d561c7a9bd6992d9b"
PACKAGE_COUNT = 72
ROW_COUNT = 68
GIB = 1024**3
MIN_GPU_MEMORY_BYTES = 14 * GIB
MIN_FREE_BYTES_EXCLUSIVE = 20 * GIB

ADMISSION_SCHEMA = "atlasops-sft-free-t4-admission-v1"
EXECUTION_APPROVAL_SCHEMA = "atlasops-sft-free-t4-execution-approval-v1"
RUNTIME_SCHEMA = "atlasops-free-t4-runtime-v1"
MODEL_INVENTORY_SCHEMA = "atlasops-sft-model-files-inventory-v1"

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
_RUN_ID_PATTERN = re.compile(r"sft-pilot-[a-z0-9][a-z0-9-]{0,63}\Z")
_REQUIRED_APPROVAL_KEYS = frozenset(
    {
        "schema_version",
        "profile",
        "execution_allowed",
        "plan_sha256",
        "free_t4_plan_sha256",
        "parent_plan_sha256",
        "corpus_sha256",
        "corpus_manifest_sha256",
        "row_count",
        "model",
        "model_revision",
        "tokenizer",
        "tokenizer_revision",
        "role",
        "hyperparameters",
        "zero_cost_verified",
        "spend_usd_max",
        "approved_by",
        "rationale",
        "approved_at_utc",
        "approval_date",
        "run_id",
        "source_git_sha",
        "output_dir",
        "package_versions",
        "host",
        "model_files_manifest",
        "model_files_manifest_sha256",
        "evidence_retention",
        "scope",
    }
)
_RUNTIME_IDENTITY_FIELDS = (
    "runtime_kind",
    "hostname",
    "package_versions",
    "lock_sha256",
    "storage",
    "gpu_name",
    "gpu_count",
    "memory_bytes",
    "cuda_runtime",
    "compute_capability",
)
_HOST_KEYS = frozenset(
    {
        *_RUNTIME_IDENTITY_FIELDS,
        "python_version",
        "python_implementation",
        "platform",
        "runtime_manifest",
        "runtime_manifest_sha256",
    }
)
_INVENTORY_KEYS = frozenset(
    {
        "schema_version",
        "collected_at_utc",
        "plan_sha256",
        "repository",
        "revision",
        "snapshot_dir",
        "weight_metadata",
        "tokenizer_manifest_sha256",
        "files",
        "file_details",
        "total_files",
        "total_bytes",
        "snapshot_permissions",
        "model_weights_loaded",
        "network_accessed",
        "execution_allowed",
    }
)


def _read(path: Path) -> tuple[dict[str, Any], bytes]:
    raw, status = _read_bounded_snapshot(Path(path), MAX_VERIFIED_SFT_MANIFEST_BYTES)
    if raw is None:
        raise ValueError(f"Free-T4 SFT input is unsafe or unavailable ({status})")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid free-T4 SFT JSON") from exc
    if not isinstance(value, dict):
        raise TypeError("Free-T4 SFT JSON must be an object")
    return value, raw


def _strict_equal(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _strict_equal(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _strict_equal(found, wanted)
            for found, wanted in zip(actual, expected, strict=True)
        )
    return actual == expected


def _frozen_hyperparameters() -> dict[str, Any]:
    return {
        "epochs": 1,
        "learning_rate": 2e-4,
        "batch_size": 1,
        "gradient_accumulation_steps": 8,
        "max_sequence_length": 8192,
        "seed": 2026,
        "assistant_only_loss": True,
        "quantization": {
            "load_in_4bit": True,
            "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": "float16",
            "bnb_4bit_use_double_quant": True,
        },
        "lora": {
            "r": 16,
            "alpha": 32,
            "dropout": 0.05,
            "target_modules": [
                "q_proj",
                "k_proj",
                "v_proj",
                "o_proj",
                "gate_proj",
                "up_proj",
                "down_proj",
            ],
            "bias": "none",
        },
        "optimizer": "paged_adamw_8bit",
        "bf16": False,
        "fp16": True,
    }


def _expected_runtime_hyperparameters(plan: dict[str, Any]) -> dict[str, Any]:
    from training.sft_rendering import TEMPLATE_PATH

    expected = copy.deepcopy(plan["hyperparameters"])
    expected["chat_template_path"] = str(TEMPLATE_PATH)
    expected["chat_template_sha256"] = file_sha256(TEMPLATE_PATH)
    return expected


def _load_plan() -> tuple[dict[str, Any], dict[str, Any]]:
    plan, raw_plan = _read(PLAN_PATH)
    if (
        hashlib.sha256(raw_plan).hexdigest() != PLAN_RAW_SHA256
        or canonical_bytes_sha256(raw_plan) != PLAN_SHA256
    ):
        raise ValueError("Free-T4 SFT configuration hash mismatch")
    expected = {
        "schema_version": "atlasops-sft-free-t4-plan-v1",
        "profile": PROFILE,
        "parent_plan_sha256": PARENT_PLAN_SHA256,
        "corpus_version": "train-candidate-v1",
        "d3_status": "APPROVED_FOR_PREPARATION",
        "execution_allowed": False,
        "corpus_sha256": CORPUS_HASH,
        "corpus_manifest_sha256": MANIFEST_HASH,
        "row_count": ROW_COUNT,
        "model": BASE_MODEL,
        "model_revision": REVISION,
        "tokenizer": BASE_MODEL,
        "tokenizer_revision": REVISION,
        "role": "all",
        "environment": {
            "runtime_kind": "colab_isolated_venv",
            "python_implementation": "CPython",
            "python_version": "3.12.11",
            "platform_system": "Linux",
            "platform_machine": "x86_64",
            "package_lock_path": LOCK_PATH,
            "package_lock_sha256": LOCK_SHA256,
            "package_versions_source": "hash-bound-parent-plan-v4",
            "package_count": PACKAGE_COUNT,
            "oci_image_digest_required": False,
        },
        "gpu": {
            "provider": "Google Colab",
            "tier": "free",
            "name": "Tesla T4",
            "count": 1,
            "compute_capability": [7, 5],
            "cuda_runtime": "12.6",
            "minimum_memory_bytes_inclusive": MIN_GPU_MEMORY_BYTES,
        },
        "resources": {
            "zero_cost_required": True,
            "spend_usd_max": 0,
            "minimum_free_bytes_exclusive": MIN_FREE_BYTES_EXCLUSIVE,
            "persistent_runtime_storage": False,
        },
        "hyperparameters": _frozen_hyperparameters(),
        "evidence_retention": {
            "colab_runtime_storage_persistent": False,
            "required_methods": [
                "download_to_local_evidence_store",
                "mirror_to_local_evidence_store",
            ],
            "google_drive_all_files_mount_allowed": False,
        },
        "scope": {
            "training_authorized": False,
            "paid_compute_authorized": False,
            "p1_remediation_authorized": False,
            "grpo_authorized": False,
            "final_test_accessed": False,
        },
    }
    if not _strict_equal(plan, expected):
        raise ValueError("Free-T4 SFT configuration differs from its frozen contract")
    parent_plan, parent_raw = _read(REPO_ROOT / "config" / "sft_pilot_v4.json")
    from training import sft_pilot_gate

    if (
        sft_pilot_gate.PLAN_SHA256 != PARENT_PLAN_SHA256
        or canonical_bytes_sha256(parent_raw) != PARENT_PLAN_SHA256
        or parent_plan.get("execution_allowed") is not False
        or parent_plan.get("schema_version") != "atlasops-sft-pilot-plan-v1"
    ):
        raise ValueError("Free-T4 SFT parent v4 plan is not the pinned preparation plan")
    packages = parent_plan.get("environment", {}).get("package_versions")
    required_files = parent_plan.get("required_files")
    if (
        not isinstance(packages, dict)
        or len(packages) != PACKAGE_COUNT
        or any(not isinstance(name, str) or not isinstance(version, str) for name, version in packages.items())
        or not isinstance(required_files, dict)
        or required_files.get(LOCK_PATH) != LOCK_SHA256
    ):
        raise ValueError("Free-T4 SFT package lock differs from the pinned 72-package parent")
    return plan, parent_plan


def _require_exact_keys(value: Any, expected: frozenset[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label} fields are incomplete or contain unapproved fields")


def _verify_parent_admission(admission: dict[str, Any]) -> None:
    expected = {
        "schema_version": "atlasops-sft-pilot-admission-v1",
        "d3_status": "APPROVED_FOR_PREPARATION",
        "preparation_admissible": True,
        "execution_allowed": False,
        "model": BASE_MODEL,
        "model_revision": REVISION,
        "tokenizer_revision": REVISION,
        "corpus_sha256": CORPUS_HASH,
        "corpus_manifest_sha256": MANIFEST_HASH,
        "plan_sha256": PARENT_PLAN_SHA256,
    }
    if not isinstance(admission, dict) or any(
        not _strict_equal(admission.get(key), value) for key, value in expected.items()
    ):
        raise ValueError("Original v4 SFT preparation evidence is missing or invalid")


def _baseline_hyperparameters(hyperparameters: dict[str, Any]) -> dict[str, Any]:
    baseline = copy.deepcopy(hyperparameters)
    baseline["batch_size"] = 2
    baseline["gradient_accumulation_steps"] = 4
    baseline["quantization"]["bnb_4bit_compute_dtype"] = "bfloat16"
    baseline["bf16"] = True
    baseline.pop("fp16", None)
    return baseline


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
    """Admit only the exact free-T4 profile, then reuse original v4 evidence."""
    plan, _ = _load_plan()
    from training import sft_pilot_gate

    if (model, tokenizer) != (BASE_MODEL, BASE_MODEL) or (
        model_revision,
        tokenizer_revision,
    ) != (REVISION, REVISION):
        raise ValueError("Free-T4 SFT requires the pinned Qwen base/tokenizer revision")
    if role != "all":
        raise ValueError("Free-T4 SFT requires all approved train-candidate-v1 rows")
    if (
        canonical_bytes_sha256(snapshot.raw_bytes) != CORPUS_HASH
        or len(snapshot.rows) != ROW_COUNT
    ):
        raise ValueError("Free-T4 SFT requires the exact 68-row approved candidate")
    expected_hyperparameters = _expected_runtime_hyperparameters(plan)
    if not _strict_equal(hyperparameters, expected_hyperparameters):
        raise ValueError("SFT hyperparameters differ from the frozen free-T4 profile")

    parent_admission = sft_pilot_gate.validate_preparation(
        snapshot,
        model=model,
        model_revision=model_revision,
        tokenizer=tokenizer,
        tokenizer_revision=tokenizer_revision,
        role=role,
        hyperparameters=_baseline_hyperparameters(hyperparameters),
    )
    _verify_parent_admission(parent_admission)
    return {
        "schema_version": ADMISSION_SCHEMA,
        "profile": PROFILE,
        "d3_status": "APPROVED_FOR_PREPARATION",
        "preparation_admissible": True,
        "execution_allowed": False,
        "model": BASE_MODEL,
        "model_revision": REVISION,
        "tokenizer": BASE_MODEL,
        "tokenizer_revision": REVISION,
        "corpus_version": "train-candidate-v1",
        "corpus_sha256": CORPUS_HASH,
        "corpus_manifest_sha256": MANIFEST_HASH,
        "row_count": ROW_COUNT,
        "plan_sha256": PLAN_SHA256,
        "free_t4_plan_sha256": PLAN_SHA256,
        "parent_plan_sha256": PARENT_PLAN_SHA256,
        "hyperparameters": copy.deepcopy(hyperparameters),
        "parent_preparation": {
            key: parent_admission[key]
            for key in (
                "schema_version",
                "d3_status",
                "preparation_admissible",
                "execution_allowed",
                "model",
                "model_revision",
                "tokenizer_revision",
                "corpus_sha256",
                "corpus_manifest_sha256",
                "plan_sha256",
            )
        },
        "remaining_approvals": [
            "verified zero-cost Colab Free entitlement and live Tesla T4 runtime",
            "complete pinned model-file transfer and immutable inventory",
            "externally hash-pinned bounded SFT execution record",
            "adapter reload verification and local evidence download/mirror",
        ],
    }


def _verify_admission(admission: dict[str, Any], plan: dict[str, Any]) -> None:
    expected = {
        "schema_version": ADMISSION_SCHEMA,
        "profile": PROFILE,
        "d3_status": "APPROVED_FOR_PREPARATION",
        "preparation_admissible": True,
        "execution_allowed": False,
        "model": BASE_MODEL,
        "model_revision": REVISION,
        "tokenizer": BASE_MODEL,
        "tokenizer_revision": REVISION,
        "corpus_version": "train-candidate-v1",
        "corpus_sha256": CORPUS_HASH,
        "corpus_manifest_sha256": MANIFEST_HASH,
        "row_count": ROW_COUNT,
        "plan_sha256": PLAN_SHA256,
        "free_t4_plan_sha256": PLAN_SHA256,
        "parent_plan_sha256": PARENT_PLAN_SHA256,
        "hyperparameters": _expected_runtime_hyperparameters(plan),
    }
    if not isinstance(admission, dict) or any(
        not _strict_equal(admission.get(key), value) for key, value in expected.items()
    ):
        raise ValueError("Free-T4 SFT admission is missing or differs from its frozen plan")
    _verify_parent_admission(admission.get("parent_preparation"))


def _utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Free-T4 SFT {label} timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Free-T4 SFT {label} timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Free-T4 SFT {label} timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _expected_platform() -> dict[str, str]:
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "release": platform.release(),
        "platform": platform.platform(),
    }


def _verify_package_versions(package_versions: Any, expected: dict[str, str]) -> None:
    if not _strict_equal(package_versions, expected) or len(expected) != PACKAGE_COUNT:
        raise ValueError("Free-T4 SFT runtime package inventory differs from the locked 72 packages")
    for package, version in expected.items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f"Free-T4 SFT runtime package is missing: {package}") from exc
        if installed != version:
            raise ValueError(f"Free-T4 SFT runtime package version mismatch: {package}")


def _verify_runtime(
    approval: dict[str, Any], output_dir: Path, parent_plan: dict[str, Any]
) -> dict[str, Any]:
    host = approval.get("host")
    _require_exact_keys(host, _HOST_KEYS, "Free-T4 SFT host")
    runtime_path_value = host.get("runtime_manifest")
    if not isinstance(runtime_path_value, str) or not Path(runtime_path_value).is_absolute():
        raise ValueError("Free-T4 SFT requires an absolute runtime-manifest path")
    runtime_path = Path(runtime_path_value)
    runtime, runtime_raw = _read(runtime_path)
    if hashlib.sha256(runtime_raw).hexdigest() != host.get("runtime_manifest_sha256"):
        raise ValueError("Free-T4 SFT runtime-manifest digest mismatch")
    if (
        runtime.get("schema_version") != RUNTIME_SCHEMA
        or runtime.get("runtime_kind") != "colab_isolated_venv"
        or runtime.get("image_digest") is not None
    ):
        raise ValueError("Free-T4 SFT requires a Colab isolated venv and no OCI image digest")

    expected_environment = parent_plan["environment"]
    package_versions = expected_environment["package_versions"]
    expected_host = {
        "runtime_kind": "colab_isolated_venv",
        "hostname": socket.gethostname(),
        "package_versions": package_versions,
        "lock_sha256": LOCK_SHA256,
    }
    for key, value in expected_host.items():
        if not _strict_equal(runtime.get(key), value) or not _strict_equal(
            host.get(key), value
        ):
            raise ValueError(f"Free-T4 SFT runtime host identity mismatch: {key}")
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or platform.python_version() != expected_environment["python_version"]
        or platform.python_implementation() != "CPython"
        or sys.prefix == sys.base_prefix
        or host.get("python_version") != expected_environment["python_version"]
        or host.get("python_implementation") != "CPython"
        or not _strict_equal(host.get("platform"), _expected_platform())
    ):
        raise ValueError(
            "Free-T4 SFT requires an isolated Linux x86_64 CPython 3.12.11 runtime"
        )
    _verify_package_versions(runtime.get("package_versions"), package_versions)

    if (
        approval.get("zero_cost_verified") is not True
        or type(approval.get("spend_usd_max")) not in {int, float}
        or approval.get("spend_usd_max") != 0
    ):
        raise ValueError("Free-T4 SFT execution approval does not prove zero cost")

    storage = runtime.get("storage")
    _require_exact_keys(
        storage,
        frozenset({"free_bytes", "total_bytes", "used_bytes"}),
        "Free-T4 SFT storage",
    )
    if (
        type(storage.get("free_bytes")) is not int
        or type(storage.get("total_bytes")) is not int
        or type(storage.get("used_bytes")) is not int
        or storage["free_bytes"] <= MIN_FREE_BYTES_EXCLUSIVE
        or storage["total_bytes"] != storage["used_bytes"] + storage["free_bytes"]
        or not _strict_equal(host.get("storage"), storage)
    ):
        raise ValueError("Free-T4 SFT requires more than 20 GiB of measured runtime storage")
    from training.sft_provenance import has_redirecting_path_component

    output_path = Path(output_dir)
    if (
        not output_path.is_absolute()
        or not isinstance(approval.get("output_dir"), str)
        or Path(approval["output_dir"]) != output_path.absolute()
        or output_path.exists()
        or has_redirecting_path_component(output_path)
    ):
        raise ValueError("SFT execution source/output differs from the fresh approved output path")
    existing_parent = output_path.absolute().parent
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    if shutil.disk_usage(existing_parent).free <= MIN_FREE_BYTES_EXCLUSIVE:
        raise ValueError("Free-T4 SFT output filesystem has insufficient verified free space")

    try:
        import torch

        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise ValueError("Free-T4 SFT requires exactly one live CUDA GPU")
        actual_gpu = {
            "gpu_name": torch.cuda.get_device_name(0),
            "gpu_count": torch.cuda.device_count(),
            "compute_capability": list(torch.cuda.get_device_capability(0)),
            "memory_bytes": torch.cuda.get_device_properties(0).total_memory,
            "cuda_runtime": torch.version.cuda,
        }
    except (ImportError, RuntimeError, AttributeError, TypeError) as exc:
        raise ValueError("Free-T4 SFT could not measure the live CUDA device") from exc
    expected_gpu = {
        "gpu_name": "Tesla T4",
        "gpu_count": 1,
        "compute_capability": [7, 5],
        "memory_bytes": actual_gpu["memory_bytes"],
        "cuda_runtime": "12.6",
    }
    if (
        actual_gpu["gpu_name"] != "Tesla T4"
        or actual_gpu["gpu_count"] != 1
        or actual_gpu["compute_capability"] != [7, 5]
        or type(actual_gpu["memory_bytes"]) is not int
        or actual_gpu["memory_bytes"] < MIN_GPU_MEMORY_BYTES
        or actual_gpu["cuda_runtime"] != "12.6"
        or any(not _strict_equal(runtime.get(key), value) for key, value in expected_gpu.items())
        or any(not _strict_equal(host.get(key), value) for key, value in expected_gpu.items())
    ):
        raise ValueError("Free-T4 SFT requires the measured single Tesla T4, compute 7.5, CUDA 12.6")

    _require_exact_keys(
        approval.get("evidence_retention"),
        frozenset(
            {
                "colab_runtime_storage_persistent",
                "method",
                "user_evidence_store",
                "google_drive_all_files_mount",
            }
        ),
        "evidence-retention",
    )
    evidence_retention = approval["evidence_retention"]
    retention_path = evidence_retention.get("user_evidence_store")
    if (
        evidence_retention.get("colab_runtime_storage_persistent") is not False
        or evidence_retention.get("method")
        not in {
            "download_to_local_evidence_store",
            "mirror_to_local_evidence_store",
        }
        or not isinstance(retention_path, str)
        or not PureWindowsPath(retention_path).is_absolute()
        or evidence_retention.get("google_drive_all_files_mount") is not False
    ):
        raise ValueError(
            "Free-T4 SFT requires a local evidence download/mirror and no all-files Drive mount"
        )
    return runtime


def _verify_model_inventory(approval: dict[str, Any]) -> str:
    inventory_path_value = approval.get("model_files_manifest")
    if (
        not isinstance(inventory_path_value, str)
        or not Path(inventory_path_value).is_absolute()
        or not isinstance(approval.get("model_files_manifest_sha256"), str)
        or _SHA256_PATTERN.fullmatch(approval["model_files_manifest_sha256"]) is None
    ):
        raise ValueError("Free-T4 SFT requires an absolute pinned model inventory")
    inventory_path = Path(inventory_path_value)
    inventory, raw_inventory = _read(inventory_path)
    if hashlib.sha256(raw_inventory).hexdigest() != approval["model_files_manifest_sha256"]:
        raise ValueError("Free-T4 SFT model inventory digest mismatch")
    _require_exact_keys(inventory, _INVENTORY_KEYS, "model inventory")

    snapshot_value = inventory.get("snapshot_dir")
    if not isinstance(snapshot_value, str) or not Path(snapshot_value).is_absolute():
        raise ValueError("Free-T4 SFT inventory requires an absolute model snapshot")
    snapshot = Path(snapshot_value)
    from scripts import collect_sft_remote_provenance as collector
    from scripts.transfer_t4_model_files import FILES as pinned_files
    from training.sft_provenance import has_redirecting_path_component

    if (
        collector.MODEL_INVENTORY_SCHEMA != MODEL_INVENTORY_SCHEMA
        or not pinned_files
        or has_redirecting_path_component(snapshot)
    ):
        raise ValueError("Free-T4 SFT pinned model inventory contract is unavailable")
    actual = collector.collect_model_inventory(snapshot_dir=snapshot)
    if (
        actual.get("schema_version") != MODEL_INVENTORY_SCHEMA
        or actual.get("repository") != BASE_MODEL
        or actual.get("revision") != REVISION
        or actual.get("snapshot_dir") != str(snapshot.absolute())
        or actual.get("plan_sha256") != PARENT_PLAN_SHA256
        or actual.get("model_weights_loaded") is not False
        or actual.get("network_accessed") is not False
        or actual.get("execution_allowed") is not False
    ):
        raise ValueError("Free-T4 SFT collector did not verify the pinned immutable snapshot")
    _utc_timestamp(inventory.get("collected_at_utc"), "model inventory")
    files = inventory.get("files")
    actual_files = actual.get("files")
    if not isinstance(files, dict) or not isinstance(actual_files, dict):
        raise TypeError("Free-T4 SFT model inventory file list is invalid")
    normalized_files: dict[str, str] = {}
    for absolute, digest in files.items():
        path = Path(absolute) if isinstance(absolute, str) else Path()
        if (
            not path.is_absolute()
            or not path.is_relative_to(snapshot)
            or has_redirecting_path_component(path)
            or not isinstance(digest, str)
            or _SHA256_PATTERN.fullmatch(digest) is None
        ):
            raise ValueError("Free-T4 SFT model inventory contains an unsafe file path/hash")
        relative = path.relative_to(snapshot)
        if relative.parent != Path(".") or relative.name in normalized_files:
            raise ValueError("Free-T4 SFT model snapshot contains nested or duplicate files")
        normalized_files[relative.name] = digest
    if set(normalized_files) != set(pinned_files):
        raise ValueError("Free-T4 SFT model snapshot inventory has missing or extraneous files")
    for key in _INVENTORY_KEYS.difference({"collected_at_utc"}):
        if not _strict_equal(inventory.get(key), actual.get(key)):
            raise ValueError(f"Free-T4 SFT model inventory differs from the live snapshot: {key}")
    details = actual.get("file_details")
    if not isinstance(details, list) or len(details) != len(pinned_files):
        raise ValueError("Free-T4 SFT model inventory lacks per-file size/hash details")
    observed_details: dict[str, dict[str, Any]] = {}
    for detail in details:
        if not isinstance(detail, dict) or set(detail) != {"path", "size_bytes", "sha256"}:
            raise ValueError("Free-T4 SFT model inventory file detail is malformed")
        path = Path(detail["path"])
        if not path.is_absolute() or not path.is_relative_to(snapshot):
            raise ValueError("Free-T4 SFT model inventory detail escapes its snapshot")
        name = path.relative_to(snapshot).name
        if name in observed_details:
            raise ValueError("Free-T4 SFT model inventory has duplicate file details")
        observed_details[name] = detail
    for name, (size_bytes, digest) in pinned_files.items():
        detail = observed_details.get(name)
        if (
            not isinstance(detail, dict)
            or detail.get("size_bytes") != size_bytes
            or detail.get("sha256") != digest
            or files.get(str(snapshot / name)) != digest
        ):
            raise ValueError(f"Free-T4 SFT model file differs from its upstream pin: {name}")
    return str(snapshot.parents[2])


def require_execution_authority(
    admission: dict[str, Any],
    approval_path: Path | None = None,
    *,
    output_dir: Path | None = None,
    approval_sha256: str | None = None,
) -> dict[str, Any]:
    """Require the operator's external record digest plus current Colab evidence."""
    if (
        not isinstance(approval_sha256, str)
        or _SHA256_PATTERN.fullmatch(approval_sha256) is None
        or approval_path is None
        or output_dir is None
    ):
        raise ValueError(
            "SFT execution is not authorized. Training refused: an external approval "
            "digest, approval record, and fresh absolute output path are required."
        )
    plan, parent_plan = _load_plan()
    _verify_admission(admission, plan)
    approval_path = Path(approval_path)
    if not approval_path.is_absolute():
        raise ValueError("SFT execution approval path must be absolute")
    approval, raw_approval = _read(approval_path)
    if hashlib.sha256(raw_approval).hexdigest() != approval_sha256:
        raise ValueError("SFT execution approval digest is not the external hash-bound record")
    _require_exact_keys(approval, _REQUIRED_APPROVAL_KEYS, "execution approval")

    expected_approval = {
        "schema_version": EXECUTION_APPROVAL_SCHEMA,
        "profile": PROFILE,
        "execution_allowed": True,
        "plan_sha256": PLAN_SHA256,
        "free_t4_plan_sha256": PLAN_SHA256,
        "parent_plan_sha256": PARENT_PLAN_SHA256,
        "corpus_sha256": CORPUS_HASH,
        "corpus_manifest_sha256": MANIFEST_HASH,
        "row_count": ROW_COUNT,
        "model": BASE_MODEL,
        "model_revision": REVISION,
        "tokenizer": BASE_MODEL,
        "tokenizer_revision": REVISION,
        "role": "all",
        "hyperparameters": _expected_runtime_hyperparameters(plan),
        "zero_cost_verified": True,
        "scope": {
            "paid_compute_authorized": False,
            "p1_remediation_authorized": False,
            "grpo_authorized": False,
            "final_test_accessed": False,
        },
    }
    if any(
        not _strict_equal(approval.get(key), value)
        for key, value in expected_approval.items()
    ):
        raise ValueError("Free-T4 SFT execution approval does not match its bounded plan")
    if (
        type(approval.get("spend_usd_max")) not in {int, float}
        or approval.get("spend_usd_max") != 0
        or not isinstance(approval.get("approved_by"), str)
        or not approval["approved_by"].strip()
        or not isinstance(approval.get("rationale"), str)
        or not approval["rationale"].strip()
        or not isinstance(approval.get("approval_date"), str)
        or _RUN_ID_PATTERN.fullmatch(approval.get("run_id", "")) is None
        or _GIT_SHA_PATTERN.fullmatch(approval.get("source_git_sha", "")) is None
        or not isinstance(approval.get("output_dir"), str)
    ):
        raise ValueError("Free-T4 SFT execution approval is incomplete or permits cost")
    try:
        approval_date = date.fromisoformat(approval["approval_date"])
    except ValueError as exc:
        raise ValueError("Free-T4 SFT approval date is invalid") from exc
    if _utc_timestamp(approval.get("approved_at_utc"), "approval").date() != approval_date:
        raise ValueError("Free-T4 SFT approval date and timestamp differ")

    from training.sft_provenance import (
        has_redirecting_path_component,
        source_provenance,
    )

    source = source_provenance()
    output_path = Path(output_dir)
    if (
        source.get("git_dirty") is not False
        or source.get("git_sha") != approval["source_git_sha"]
        or not _GIT_SHA_PATTERN.fullmatch(source.get("git_sha", ""))
        or not output_path.is_absolute()
        or Path(approval["output_dir"]) != output_path.absolute()
        or output_path.exists()
        or has_redirecting_path_component(output_path)
    ):
        raise ValueError("SFT execution source/output differs from the clean approved run")

    runtime = _verify_runtime(approval, output_path, parent_plan)
    model_cache_dir = _verify_model_inventory(approval)
    verified_approval = dict(approval)
    verified_approval["approval_record_sha256"] = approval_sha256
    verified_approval["model_cache_dir"] = model_cache_dir
    verified_approval["runtime_kind"] = runtime["runtime_kind"]
    return verified_approval
