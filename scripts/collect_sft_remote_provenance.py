"""Collect read-only SFT host or local model-snapshot provenance.

This collector never authorizes execution, downloads model files, or loads a
model. GPU capability inspection is optional and imports only PyTorch.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = REPO_ROOT / "config" / "sft_pilot_v4.json"
LOCK_RELATIVE_PATH = "requirements/sft-pilot-linux-py312.lock"
TOKENIZER_MANIFEST_PATH = (
    REPO_ROOT / "artifacts" / "evidence" / "stage7"
    / "tokenizer_files_a09a354_v1.json"
)
WEIGHT_METADATA_PATH = (
    REPO_ROOT / "artifacts" / "evidence" / "stage7"
    / "qwen_a09a354_weight_metadata_v1.json"
)

MODEL_REPOSITORY = "Qwen/Qwen2.5-7B-Instruct"
MODEL_REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
PLAN_SHA256 = "914f7a5af5c22a355b235018d997302688598d0bdbdeda8eb8fcba3552f9e83e"
MODEL_CACHE_DIRECTORY = "models--Qwen--Qwen2.5-7B-Instruct"
TOKENIZER_FILES = frozenset(
    {
        "LICENSE",
        "config.json",
        "generation_config.json",
        "merges.txt",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
    }
)
PINNED_WEIGHT_SHARDS: dict[str, dict[str, Any]] = {
    "model-00001-of-00004.safetensors": {
        "size_bytes": 3945441440,
        "sha256": "a1333e6293854747c481288ea83b348226af178dd565c49b6f9495ba1966aba7",
    },
    "model-00002-of-00004.safetensors": {
        "size_bytes": 3864726352,
        "sha256": "f5d25a2772cb825164a2a2c0fb6d51a87e282abf21e4dd75bc5cfb3cd0ea6185",
    },
    "model-00003-of-00004.safetensors": {
        "size_bytes": 3864726424,
        "sha256": "8efdec4c1bc12317ae1a38dc42b595ce777738a64deea3fcb8a0a91381bcdfd5",
    },
    "model-00004-of-00004.safetensors": {
        "size_bytes": 3556377672,
        "sha256": "1a72d403cdf0c1ec3cb7f289f17b394a01e64394c2e9b3c0f94dbce3faf879bd",
    },
}
PINNED_WEIGHT_TOTAL_BYTES = 15231271888
PINNED_WEIGHT_TENSOR_BYTES = 15231233024

RUNTIME_SCHEMA = "atlasops-sft-pilot-runtime-provenance-v1"
MODEL_INVENTORY_SCHEMA = "atlasops-sft-model-files-inventory-v1"
IMAGE_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
MAX_PLAN_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_INDEX_BYTES = 32 * 1024 * 1024
MAX_LOCK_BYTES = 8 * 1024 * 1024


def _canonical_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_redirecting_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for component in (absolute, *absolute.parents):
        try:
            details = component.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if stat.S_ISLNK(details.st_mode):
            return True
        if getattr(details, "st_file_attributes", 0) & reparse_attribute:
            return True
        if (
            component == absolute
            and not stat.S_ISDIR(details.st_mode)
            and getattr(details, "st_nlink", 1) > 1
        ):
            return True
    return False


def _read_bounded(path: Path, max_bytes: int) -> bytes:
    path = Path(os.path.abspath(path))
    if _has_redirecting_component(path):
        raise ValueError("Input path contains a symlink, reparse point, or hard link")
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Input path must be a regular file")
        if before.st_size > max_bytes:
            raise ValueError("Input file exceeds the configured size bound")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
                raise ValueError("Input file changed while opening")
            raw = stream.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("Input file exceeds the configured size bound")
            after = path.lstat()
            if (
                not os.path.samestat(opened, after)
                or before.st_size != after.st_size
                or _has_redirecting_component(path)
            ):
                raise ValueError("Input file changed while reading")
    except FileNotFoundError as exc:
        raise ValueError("Required input file is unavailable") from exc
    except OSError as exc:
        raise ValueError("Required input file is unreadable") from exc
    return raw


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON input contains a duplicate key")
        result[key] = value
    return result


def _read_json(path: Path, max_bytes: int) -> tuple[dict[str, Any], bytes]:
    raw = _read_bounded(path, max_bytes)
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON input is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON input must contain an object")
    return value, raw


def _load_plan() -> tuple[dict[str, Any], str]:
    plan, raw = _read_json(PLAN_PATH, MAX_PLAN_BYTES)
    if (
        _canonical_sha256(raw) != PLAN_SHA256
        or plan.get("schema_version") != "atlasops-sft-pilot-plan-v1"
        or plan.get("d3_status") != "APPROVED_FOR_PREPARATION"
        or plan.get("execution_allowed") is not False
        or plan.get("model") != MODEL_REPOSITORY
        or plan.get("model_revision") != MODEL_REVISION
        or plan.get("tokenizer") != MODEL_REPOSITORY
        or plan.get("tokenizer_revision") != MODEL_REVISION
    ):
        raise ValueError("The exact prepared SFT pilot plan is unavailable or changed")
    packages = plan.get("environment", {}).get("package_versions")
    if not isinstance(packages, dict) or not packages:
        raise ValueError("Pilot plan package map is missing")
    for name, version in packages.items():
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise ValueError("Pilot plan package map is incomplete")
    required_files = plan.get("required_files")
    lock_hash = required_files.get(LOCK_RELATIVE_PATH) if isinstance(required_files, dict) else None
    if not isinstance(lock_hash, str) or not SHA256_PATTERN.fullmatch(lock_hash):
        raise ValueError("Pilot plan lock digest is missing")
    return plan, _canonical_sha256(raw)


def _git_provenance() -> dict[str, Any]:
    git = shutil.which("git")
    if git is None:
        raise ValueError("Git is unavailable; host image validation is incomplete")
    try:
        git_version = subprocess.run(
            [git, "--version"], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
        git_sha = subprocess.run(
            [git, "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        dirty_output = subprocess.run(
            [git, "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Git source provenance could not be collected") from exc
    if re.fullmatch(r"[0-9a-f]{40}", git_sha) is None:
        raise ValueError("Git returned an invalid source revision")
    return {
        "git_version": git_version,
        "git_sha": git_sha,
        "git_dirty": bool(dirty_output.strip()),
    }


def _validate_runtime_platform(plan: dict[str, Any]) -> dict[str, str]:
    environment = plan["environment"]
    current = {
        "system": platform.system(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
    }
    if (
        current["system"] != "Linux"
        or current["machine"] != "x86_64"
        or current["python_version"] != environment.get("python_version")
        or current["python_implementation"] != "CPython"
    ):
        raise ValueError("Host must match the planned Linux x86_64 CPython runtime")
    libc_name, libc_version = platform.libc_ver()
    libc_match = re.match(r"(\d+)\.(\d+)", libc_version or "")
    if (
        libc_name.lower() != "glibc"
        or libc_match is None
        or tuple(map(int, libc_match.groups())) < (2, 28)
    ):
        raise ValueError("Host must provide glibc 2.28 or later")
    current["libc_name"] = libc_name
    current["libc_version"] = libc_version
    return current


def _collect_package_versions(expected: dict[str, str]) -> dict[str, str]:
    observed: dict[str, str] = {}
    for name, expected_version in sorted(expected.items()):
        try:
            actual_version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ValueError(f"Required runtime package is missing: {name}") from exc
        if actual_version != expected_version:
            raise ValueError(f"Runtime package version differs from the frozen plan: {name}")
        observed[name] = actual_version
    if set(observed) != set(expected):
        raise ValueError("Runtime package inventory is incomplete")
    return observed


def _inspect_driver() -> dict[str, Any]:
    command = shutil.which("nvidia-smi")
    if command is None:
        return {"status": "UNAVAILABLE", "devices": []}
    try:
        output = subprocess.run(
            [
                command,
                "--query-gpu=name,uuid,driver_version,memory.total,compute_cap",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
        rows = list(csv.reader(io.StringIO(output), skipinitialspace=True))
        if not rows or any(len(row) != 5 for row in rows):
            raise ValueError("NVIDIA driver query returned invalid device metadata")
        return {
            "status": "OBSERVED",
            "devices": [
                {
                    "name": row[0],
                    "uuid": row[1],
                    "driver_version": row[2],
                    "memory_total": row[3],
                    "compute_capability": row[4],
                }
                for row in rows
            ],
        }
    except (OSError, subprocess.SubprocessError, csv.Error, ValueError) as exc:
        return {"status": "PROBE_FAILED", "failure_type": type(exc).__name__, "devices": []}


def _inspect_gpu() -> dict[str, Any]:
    driver = _inspect_driver()
    # This is the only PyTorch import, and it runs only after --inspect-gpu.
    try:
        import torch
    except Exception as exc:
        return {
            "inspection_requested": True,
            "status": "PROBE_FAILED",
            "failure_type": type(exc).__name__,
            "cuda_available": False,
            "device_count": 0,
            "single_gpu": False,
            "bf16_supported": False,
            "cuda_runtime": None,
            "devices": [],
            "driver": driver,
            "memory_fit_verified": False,
        }
    try:
        cuda = torch.cuda
        available = bool(cuda.is_available())
        device_count = int(cuda.device_count())
        bf16_supported = bool(cuda.is_bf16_supported()) if available else False
        devices = []
        for index in range(device_count):
            properties = cuda.get_device_properties(index)
            devices.append(
                {
                    "index": index,
                    "name": str(cuda.get_device_name(index)),
                    "memory_bytes": int(properties.total_memory),
                    "compute_capability": [
                        int(properties.major),
                        int(properties.minor),
                    ],
                }
            )
        capable = available and device_count == 1 and bf16_supported
        return {
            "inspection_requested": True,
            "status": "CAPABILITIES_PRESENT" if capable else "NOT_READY",
            "cuda_available": available,
            "device_count": device_count,
            "single_gpu": device_count == 1,
            "bf16_supported": bf16_supported,
            "cuda_runtime": getattr(torch.version, "cuda", None),
            "devices": devices,
            "driver": driver,
            "memory_fit_verified": False,
        }
    except Exception as exc:
        return {
            "inspection_requested": True,
            "status": "PROBE_FAILED",
            "failure_type": type(exc).__name__,
            "cuda_available": None,
            "device_count": None,
            "single_gpu": None,
            "bf16_supported": None,
            "cuda_runtime": getattr(getattr(torch, "version", None), "cuda", None),
            "devices": [],
            "driver": driver,
            "memory_fit_verified": False,
        }


def collect_runtime_provenance(
    *,
    image_digest: str,
    inspect_gpu: bool = False,
) -> dict[str, Any]:
    if IMAGE_DIGEST_PATTERN.fullmatch(image_digest) is None:
        raise ValueError("Image digest must be a full sha256 digest")
    plan, plan_sha256 = _load_plan()
    host_platform = _validate_runtime_platform(plan)
    packages = _collect_package_versions(plan["environment"]["package_versions"])
    lock_path = REPO_ROOT / LOCK_RELATIVE_PATH
    lock_raw = _read_bounded(lock_path, MAX_LOCK_BYTES)
    lock_sha256 = _canonical_sha256(lock_raw)
    expected_lock_sha256 = plan["required_files"][LOCK_RELATIVE_PATH]
    if lock_sha256 != expected_lock_sha256:
        raise ValueError("SFT environment lock differs from the frozen pilot plan")
    source = _git_provenance()
    usage = shutil.disk_usage(REPO_ROOT)
    os_inventory = Path("/opt/sft-os-packages.txt")
    os_packages = {
        "status": "UNAVAILABLE", "path": str(os_inventory), "sha256": None,
    }
    if os_inventory.is_file() and not _has_redirecting_component(os_inventory):
        os_raw = _read_bounded(os_inventory, MAX_METADATA_BYTES)
        os_packages = {
            "status": "RECORDED", "path": str(os_inventory),
            "sha256": _raw_sha256(os_raw),
        }
    hostname = socket.gethostname()
    if not hostname or any(ord(character) < 32 for character in hostname):
        raise ValueError("Host returned an invalid hostname")
    return {
        "schema_version": RUNTIME_SCHEMA,
        "collected_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "plan_sha256": plan_sha256,
        "hostname": hostname,
        "image_digest": image_digest,
        "image_digest_source": "operator_supplied_unverified",
        "python_version": host_platform.pop("python_version"),
        "python_implementation": host_platform.pop("python_implementation"),
        "platform": host_platform,
        "package_versions": packages,
        "lock_sha256": lock_sha256,
        "source": source,
        "storage": {
            "observed_path": str(REPO_ROOT), "total_bytes": usage.total,
            "used_bytes": usage.used, "free_bytes": usage.free,
            "quota_verified": False, "persistent_mount_verified": False,
        },
        "os_package_inventory": os_packages,
        "gpu": _inspect_gpu() if inspect_gpu else {
            "inspection_requested": False,
            "status": "NOT_INSPECTED",
            "cuda_available": None,
            "device_count": None,
            "single_gpu": None,
            "bf16_supported": None,
            "cuda_runtime": None,
            "devices": [],
            "driver": {"status": "NOT_INSPECTED", "devices": []},
            "memory_fit_verified": False,
        },
        "execution_allowed": False,
    }


def _readonly_regular_file(path: Path) -> os.stat_result:
    if _has_redirecting_component(path):
        raise ValueError(f"Snapshot file contains a redirect: {path.name}")
    try:
        details = path.lstat()
    except OSError as exc:
        raise ValueError(f"Snapshot entry is unavailable: {path.name}") from exc
    if not stat.S_ISREG(details.st_mode):
        raise ValueError(f"Snapshot entry must be a regular file: {path.name}")
    if _has_write_bits(details.st_mode):
        raise ValueError(f"Snapshot file is writable: {path.name}")
    return details


def _has_write_bits(mode: int) -> bool:
    return bool(mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _hash_snapshot_file(path: Path, expected_stat: os.stat_result | None = None) -> str:
    before = _readonly_regular_file(path)
    if expected_stat is not None and not os.path.samestat(expected_stat, before):
        raise ValueError(f"Snapshot file changed after inventory scan: {path.name}")
    digest = _sha256_file(path)
    after = _readonly_regular_file(path)
    if (
        not os.path.samestat(before, after)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise ValueError(f"Snapshot file changed while hashing: {path.name}")
    return digest


def _readonly_directory(path: Path) -> os.stat_result:
    if _has_redirecting_component(path):
        raise ValueError("Snapshot directory contains a redirect")
    try:
        details = path.lstat()
    except OSError as exc:
        raise ValueError("Snapshot directory is unavailable") from exc
    if not stat.S_ISDIR(details.st_mode):
        raise ValueError("Snapshot directory must be a real directory")
    if _has_write_bits(details.st_mode):
        raise ValueError("Snapshot directory is writable")
    return details


def _validated_snapshot_path(snapshot_dir: Path) -> Path:
    if not snapshot_dir.is_absolute():
        raise ValueError("Model snapshot path must be absolute")
    snapshot = Path(os.path.abspath(snapshot_dir))
    if (
        snapshot.name != MODEL_REVISION
        or snapshot.parent.name != "snapshots"
        or snapshot.parents[1].name != MODEL_CACHE_DIRECTORY
        or _has_redirecting_component(snapshot)
    ):
        raise ValueError("Model snapshot path must use the pinned immutable cache revision")
    _readonly_directory(snapshot)
    return snapshot


def _validate_weight_metadata(
    metadata_path: Path,
) -> tuple[dict[str, dict[str, Any]], bytes]:
    metadata, raw = _read_json(metadata_path, MAX_METADATA_BYTES)
    expected_source = (
        f"https://huggingface.co/api/models/{MODEL_REPOSITORY}/revision/"
        f"{MODEL_REVISION}?blobs=true"
    )
    expected_total = sum(item["size_bytes"] for item in PINNED_WEIGHT_SHARDS.values())
    if (
        metadata.get("schema_version") != "atlasops-public-model-metadata-v1"
        or metadata.get("repository") != MODEL_REPOSITORY
        or metadata.get("revision") != MODEL_REVISION
        or metadata.get("source") != expected_source
        or metadata.get("weights_downloaded") is not False
        or type(metadata.get("total_weight_bytes")) is not int
        or metadata.get("total_weight_bytes") != PINNED_WEIGHT_TOTAL_BYTES
        or expected_total != PINNED_WEIGHT_TOTAL_BYTES
        or metadata.get("shards") != PINNED_WEIGHT_SHARDS
    ):
        raise ValueError("Public weight metadata does not match the pinned model revision")
    return PINNED_WEIGHT_SHARDS, raw


def _validate_tokenizer_manifest(
    manifest_path: Path,
    plan: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bytes]:
    manifest, raw = _read_json(manifest_path, MAX_METADATA_BYTES)
    expected_hash = plan["required_files"].get(
        "artifacts/evidence/stage7/tokenizer_files_a09a354_v1.json"
    )
    if (
        manifest.get("schema_version") != "atlasops-tokenizer-files-v1"
        or manifest.get("repository") != MODEL_REPOSITORY
        or manifest.get("revision") != MODEL_REVISION
        or manifest.get("model_weights_downloaded") is not False
        or not isinstance(expected_hash, str)
        or _canonical_sha256(raw) != expected_hash
    ):
        raise ValueError("Tokenizer manifest differs from the frozen pilot plan")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != TOKENIZER_FILES:
        raise ValueError("Pinned tokenizer file allowlist is incomplete")
    result: dict[str, dict[str, Any]] = {}
    for name, details in files.items():
        digest = details.get("sha256") if isinstance(details, dict) else None
        if (
            not isinstance(digest, str)
            or SHA256_PATTERN.fullmatch(digest) is None
            or type(details.get("size_bytes")) is not int
            or details["size_bytes"] <= 0
        ):
            raise ValueError(f"Tokenizer manifest entry is invalid: {name}")
        result[name] = details
    return result, raw


def _snapshot_entries(snapshot: Path) -> list[tuple[Path, os.stat_result]]:
    entries: list[tuple[Path, os.stat_result]] = []
    for path in sorted(snapshot.rglob("*")):
        if _has_redirecting_component(path):
            raise ValueError(f"Snapshot contains a symlink, reparse point, or hard link: {path.name}")
        try:
            details = path.lstat()
        except OSError as exc:
            raise ValueError(f"Snapshot entry is unavailable: {path.name}") from exc
        if stat.S_ISDIR(details.st_mode):
            _readonly_directory(path)
        elif stat.S_ISREG(details.st_mode):
            _readonly_regular_file(path)
        else:
            raise ValueError(f"Snapshot contains a non-regular entry: {path.name}")
        entries.append((path, details))
    if not entries:
        raise ValueError("Model snapshot is empty")
    return entries


def collect_model_inventory(
    *,
    snapshot_dir: Path,
    metadata_path: Path = WEIGHT_METADATA_PATH,
    tokenizer_manifest_path: Path = TOKENIZER_MANIFEST_PATH,
) -> dict[str, Any]:
    plan, plan_sha256 = _load_plan()
    snapshot = _validated_snapshot_path(snapshot_dir)
    pinned_shards, weight_metadata_raw = _validate_weight_metadata(metadata_path)
    tokenizer_hashes, tokenizer_manifest_raw = _validate_tokenizer_manifest(
        tokenizer_manifest_path,
        plan,
    )
    entries = _snapshot_entries(snapshot)
    files: dict[str, str] = {}
    file_details: list[dict[str, Any]] = []
    index_name = "model.safetensors.index.json"
    root_files: dict[str, Path] = {}
    for path, details in entries:
        if not stat.S_ISREG(details.st_mode):
            continue
        digest = _hash_snapshot_file(path, details)
        absolute = str(path.absolute())
        relative = path.relative_to(snapshot)
        files[absolute] = digest
        file_details.append(
            {"path": absolute, "size_bytes": details.st_size, "sha256": digest}
        )
        if relative.parent == Path("."):
            root_files[path.name] = path
            if path.name in tokenizer_hashes:
                expected = tokenizer_hashes[path.name]
                if (
                    details.st_size != expected["size_bytes"]
                    or digest != expected["sha256"]
                ):
                    raise ValueError(
                        f"Model snapshot tokenizer file differs from its pin: {path.name}"
                    )
            if path.name in pinned_shards:
                expected = pinned_shards[path.name]
                if (
                    details.st_size != expected["size_bytes"]
                    or digest != expected["sha256"]
                ):
                    raise ValueError(
                        f"Model snapshot shard differs from public metadata: {path.name}"
                    )
    required_names = set(tokenizer_hashes) | set(pinned_shards) | {index_name}
    if not required_names.issubset(root_files):
        missing = sorted(required_names.difference(root_files))
        raise ValueError(f"Model snapshot lacks required pinned files: {', '.join(missing)}")
    index_path = root_files[index_name]
    index_details = index_path.lstat()
    if index_details.st_size > MAX_INDEX_BYTES:
        raise ValueError("Safetensors index exceeds the configured size bound")
    index, index_raw = _read_json(index_path, MAX_INDEX_BYTES)
    if _raw_sha256(index_raw) != files[str(index_path.absolute())]:
        raise ValueError("Safetensors index changed during inventory collection")
    weight_map = index.get("weight_map")
    if (
        not isinstance(weight_map, dict)
        or not weight_map
        or any(not isinstance(shard, str) for shard in weight_map.values())
        or set(weight_map.values()) != set(pinned_shards)
    ):
        raise ValueError("Safetensors index does not cover the exact pinned shard set")
    index_metadata = index.get("metadata")
    if (
        not isinstance(index_metadata, dict)
        or type(index_metadata.get("total_size")) is not int
        or index_metadata["total_size"] != PINNED_WEIGHT_TENSOR_BYTES
    ):
        raise ValueError("Safetensors index total size differs from public metadata")
    total_bytes = sum(item["size_bytes"] for item in file_details)
    if sum(pinned_shards[name]["size_bytes"] for name in pinned_shards) != PINNED_WEIGHT_TOTAL_BYTES:
        raise ValueError("Pinned shard sizes do not match the frozen model total")
    return {
        "schema_version": MODEL_INVENTORY_SCHEMA,
        "collected_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "plan_sha256": plan_sha256,
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "snapshot_dir": str(snapshot),
        "weight_metadata": {
            "path": str(Path(os.path.abspath(metadata_path))),
            "sha256": _raw_sha256(weight_metadata_raw),
            "source": "pinned_public_revision_metadata",
        },
        "tokenizer_manifest_sha256": _raw_sha256(tokenizer_manifest_raw),
        "files": dict(sorted(files.items())),
        "file_details": file_details,
        "total_files": len(file_details),
        "total_bytes": total_bytes,
        "snapshot_permissions": {
            "mode_bits_readonly": True,
            "owner_acl_verified": False,
            "mount_immutability_verified": False,
        },
        "model_weights_loaded": False,
        "network_accessed": False,
        "execution_allowed": False,
    }


def write_json_new(path: Path, record: dict[str, Any]) -> Path:
    output = _validate_new_output(path)
    if record.get("execution_allowed") is not False:
        raise ValueError("Provenance collector cannot authorize execution")
    raw = (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(output, flags, 0o600)
    except FileExistsError as exc:
        raise ValueError("Output path already exists; refusing to overwrite") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return output


def _validate_new_output(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("Output path must be absolute")
    output = Path(os.path.abspath(path))
    if not output.parent.is_dir() or _has_redirecting_component(output):
        raise ValueError("Output parent must exist and contain no path redirects")
    if os.path.lexists(output):
        raise ValueError("Output path already exists; refusing to overwrite")
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    runtime = commands.add_parser("runtime", help="collect host runtime provenance")
    runtime.add_argument("--image-digest", required=True)
    runtime.add_argument("--output", required=True, type=Path)
    runtime.add_argument(
        "--inspect-gpu",
        action="store_true",
        help="explicitly probe CUDA/BF16/single-GPU metadata using PyTorch",
    )
    inventory = commands.add_parser("model-inventory", help="hash a local pinned model snapshot")
    inventory.add_argument("--snapshot-dir", required=True, type=Path)
    inventory.add_argument("--output", required=True, type=Path)
    inventory.add_argument("--weight-metadata", type=Path, default=WEIGHT_METADATA_PATH)
    inventory.add_argument(
        "--tokenizer-manifest",
        type=Path,
        default=TOKENIZER_MANIFEST_PATH,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        output = _validate_new_output(args.output)
        if args.command == "runtime":
            record = collect_runtime_provenance(
                image_digest=args.image_digest,
                inspect_gpu=args.inspect_gpu,
            )
        else:
            snapshot = Path(os.path.abspath(args.snapshot_dir))
            if output.is_relative_to(snapshot):
                raise ValueError("Inventory output must not be inside the model snapshot")
            record = collect_model_inventory(
                snapshot_dir=args.snapshot_dir,
                metadata_path=args.weight_metadata,
                tokenizer_manifest_path=args.tokenizer_manifest,
            )
        output = write_json_new(output, record)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "status": "RECORDED",
        "output": str(output),
        "execution_allowed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
