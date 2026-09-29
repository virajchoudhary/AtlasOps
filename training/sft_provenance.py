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
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from config.splits import TRAIN_SEED, TRAIN_SPLIT

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAMES = ("accelerate", "bitsandbytes", "datasets", "peft", "torch", "transformers", "trl")
ADAPTER_WEIGHT_NAMES = frozenset({"adapter_model.safetensors", "adapter_model.bin"})
CORPUS_MANIFEST_NAME = "sft_corpus_manifest.json"
UNVERIFIED_DATA_ORIGIN = "UNVERIFIED"
SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256 = (
    "523cad3478e2018ebb830bab973bc02811045c6131dd0bf8f59328d756287e81"
)
SCENARIO_DERIVED_SYNTHETIC_TOTAL_EXAMPLES = 64
SCENARIO_DERIVED_SYNTHETIC_TOTAL_SCENARIOS = 16
MAX_VERIFIED_SFT_CORPUS_BYTES = 16 * 1024 * 1024
MAX_VERIFIED_SFT_MANIFEST_BYTES = 1024 * 1024
_DATA_ORIGIN_PROVENANCE_FIELDS = frozenset(
    {"data_origin", "synthetic", "data_origin_source", "corpus_manifest"}
)
_HF_COMMIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
# Mirror Hub repository-id validation without making the optional training stack a dependency.
_HF_REPO_ID_PATTERN = re.compile(r"(?:\b[\w.-]+\b/)?\b[\w.-]{1,96}\b\Z")
TOKENIZER_REVISION_BASIS_LOADER_MATCH = "LOADER_EXPOSED_COMMIT_HASH_MATCH"
TOKENIZER_REVISION_BASIS_PIN_ENFORCED = (
    "PIN_ENFORCED_BY_LOADER_ARGUMENT/NOT_INDEPENDENTLY_RETURNED"
)
_TOKENIZER_REVISION_BASES = frozenset(
    {
        TOKENIZER_REVISION_BASIS_LOADER_MATCH,
        TOKENIZER_REVISION_BASIS_PIN_ENFORCED,
    }
)


@dataclass(frozen=True)
class TrainingCorpusSnapshot:
    source_path: Path
    raw_bytes: bytes
    rows: tuple[dict[str, Any], ...]
    inventory: dict[str, Any]


def canonical_file_sha256(path: Path) -> str:
    """Hash a text corpus with CRLF normalized to LF."""
    return canonical_bytes_sha256(path.read_bytes())


def canonical_bytes_sha256(raw: bytes) -> str:
    """Hash a corpus byte snapshot with CRLF normalized to LF."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_hf_commit_revision(revision: Any, *, label: str) -> None:
    if not isinstance(revision, str) or _HF_COMMIT_SHA_PATTERN.fullmatch(revision) is None:
        raise ValueError(
            f"{label} must be a full 40-character immutable Hugging Face commit SHA"
        )


def validate_hf_reference(source: Any, revision: Any, *, label: str) -> None:
    if (
        not isinstance(source, str)
        or not source.strip()
        or source != source.strip()
    ):
        raise ValueError(f"{label} must be a valid Hugging Face repository id")
    if "://" in source:
        raise ValueError(
            f"{label} must be a valid Hugging Face repository id; URLs are unsupported"
        )
    source_path = Path(source).expanduser()
    windows_source_path = PureWindowsPath(source)
    if (
        source_path.is_absolute()
        or windows_source_path.is_absolute()
        or bool(windows_source_path.drive)
        or source.startswith((".", "~"))
        or "\\" in source
    ):
        raise ValueError(
            f"Local {label} paths are unsupported; use a Hugging Face repository id "
            "with a pinned commit"
        )
    if (
        len(source) > 96
        or source.count("/") > 1
        or _HF_REPO_ID_PATTERN.fullmatch(source) is None
        or "--" in source
        or ".." in source
        or source.endswith(".git")
    ):
        raise ValueError(
            f"{label} must be a valid Hugging Face repository id "
            "(one name or namespace/name; local paths are unsupported)"
        )
    if source_path.exists():
        raise ValueError(
            f"Local {label} paths are unsupported; use a Hugging Face repository id "
            "with a pinned commit"
        )
    validate_hf_commit_revision(revision, label=f"{label} revision")


def validate_resolved_hf_commit(
    requested_revision: str,
    resolved_revision: Any,
    *,
    label: str,
) -> str:
    validate_hf_commit_revision(
        requested_revision,
        label=f"Requested {label} revision",
    )
    validate_hf_commit_revision(
        resolved_revision,
        label=f"Loaded {label} revision",
    )
    if resolved_revision != requested_revision:
        raise ValueError(
            f"Loaded {label} commit does not match the requested immutable revision"
        )
    return resolved_revision


def resolve_tokenizer_revision(
    requested_revision: str,
    exposed_revision: Any,
) -> tuple[str, str]:
    """Record the pinned tokenizer commit without overstating loader evidence."""
    if exposed_revision is None:
        validate_hf_commit_revision(
            requested_revision,
            label="Requested tokenizer revision",
        )
        return requested_revision, TOKENIZER_REVISION_BASIS_PIN_ENFORCED
    return (
        validate_resolved_hf_commit(
            requested_revision,
            exposed_revision,
            label="tokenizer",
        ),
        TOKENIZER_REVISION_BASIS_LOADER_MATCH,
    )


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
    return snapshot_training_corpus(path).inventory


def inspect_training_corpus_bytes(raw: bytes) -> dict[str, Any]:
    """Inspect JSONL rows from the same bounded byte snapshot used for hashing."""
    return _parse_training_corpus_bytes(raw)[1]


def _parse_training_corpus_bytes(
    raw: bytes,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("SFT corpus is not valid UTF-8") from exc
    return _parse_training_corpus_lines(lines)


def _parse_training_corpus_lines(
    lines: list[str],
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    train_ids = set(TRAIN_SPLIT)
    scenario_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
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
        rows.append(row)
        scenario_counts[scenario_id] = scenario_counts.get(scenario_id, 0) + 1
        role_counts[role] = role_counts.get(role, 0) + 1

    if set(scenario_counts) != train_ids:
        missing = sorted(train_ids.difference(scenario_counts))
        raise ValueError(f"SFT corpus does not cover the frozen training split: missing={missing}")
    inventory = {
        "total_examples": len(rows),
        "observed_scenarios": sorted(scenario_counts),
        "scenario_counts": dict(sorted(scenario_counts.items())),
        "role_counts": dict(sorted(role_counts.items())),
    }
    return tuple(rows), inventory


def _has_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def has_redirecting_path_component(path: Path) -> bool:
    """Identify symlink, Windows reparse, or hard-link path redirects."""
    absolute_path = Path(os.path.abspath(path))
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for component in (absolute_path, *absolute_path.parents):
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
            component == absolute_path
            and not stat.S_ISDIR(details.st_mode)
            and getattr(details, "st_nlink", 1) > 1
        ):
            return True
    return False


def _read_bounded_snapshot(path: Path, max_bytes: int) -> tuple[bytes | None, str]:
    try:
        if has_redirecting_path_component(path):
            return None, "redirected"
        path_details = path.lstat()
        if not stat.S_ISREG(path_details.st_mode):
            return None, "not_regular"
        if path_details.st_size > max_bytes:
            return None, "too_large"
        with path.open("rb") as stream:
            details = os.fstat(stream.fileno())
            if not stat.S_ISREG(details.st_mode):
                return None, "not_regular"
            if not os.path.samestat(path_details, details):
                return None, "changed"
            if details.st_size > max_bytes:
                return None, "too_large"
            chunks = []
            total_bytes = 0
            while total_bytes <= max_bytes:
                chunk = stream.read(min(64 * 1024, max_bytes + 1 - total_bytes))
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    return None, "too_large"
                chunks.append(chunk)
            final_details = path.lstat()
            if not os.path.samestat(details, final_details):
                return None, "changed"
            if has_redirecting_path_component(path):
                return None, "redirected"
        return b"".join(chunks), "read"
    except FileNotFoundError:
        return None, "unavailable"
    except OSError:
        return None, "unavailable"


def snapshot_training_corpus(path: Path) -> TrainingCorpusSnapshot:
    """Read and validate one bounded, redirect-free corpus snapshot for a run."""
    source_path = Path(os.path.abspath(path))
    if has_redirecting_path_component(source_path):
        raise ValueError(
            "SFT corpus path must not contain symlink, reparse, or hard-link redirects"
        )
    try:
        source_path.lstat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"SFT corpus not found: {source_path}") from exc
    except OSError as exc:
        raise ValueError("SFT corpus path is unavailable") from exc

    raw_bytes, status = _read_bounded_snapshot(
        source_path,
        MAX_VERIFIED_SFT_CORPUS_BYTES,
    )
    if raw_bytes is None:
        messages = {
            "too_large": "SFT corpus exceeds the 16 MiB snapshot limit",
            "not_regular": "SFT corpus must be a regular file",
            "redirected": (
                "SFT corpus path must not contain symlink, reparse, or hard-link redirects"
            ),
            "changed": "SFT corpus path changed while its snapshot was being read",
        }
        raise ValueError(messages.get(status, "SFT corpus is unavailable"))

    rows, inventory = _parse_training_corpus_bytes(raw_bytes)
    return TrainingCorpusSnapshot(
        source_path=source_path,
        raw_bytes=raw_bytes,
        rows=rows,
        inventory=inventory,
    )


def _verify_recorded_corpus_manifest(
    dataset: dict[str, Any],
    corpus_manifest: dict[str, Any],
    *,
    approved_corpus_path: Path | None,
) -> tuple[bool, str]:
    if not corpus_manifest.get("present"):
        return False, "absent"
    if approved_corpus_path is None:
        return False, "approval_required"

    corpus_path_text = dataset.get("corpus_path")
    manifest_path_text = corpus_manifest.get("path")
    if (
        not isinstance(corpus_path_text, str)
        or not Path(corpus_path_text).is_absolute()
        or not isinstance(manifest_path_text, str)
        or not Path(manifest_path_text).is_absolute()
    ):
        return False, "invalid_path"

    approved_path = Path(os.path.abspath(approved_corpus_path))
    recorded_corpus_path = Path(os.path.abspath(corpus_path_text))
    if recorded_corpus_path != approved_path:
        return False, "approval_mismatch"
    corpus_path = approved_path
    expected_manifest_path = Path(
        os.path.abspath(corpus_path.parent / CORPUS_MANIFEST_NAME)
    )
    recorded_manifest_path = Path(os.path.abspath(manifest_path_text))
    if recorded_manifest_path != expected_manifest_path:
        return False, "path_mismatch"
    if has_redirecting_path_component(corpus_path) or has_redirecting_path_component(
        expected_manifest_path
    ):
        return False, "redirected_path"

    corpus_bytes, corpus_status = _read_bounded_snapshot(
        corpus_path,
        MAX_VERIFIED_SFT_CORPUS_BYTES,
    )
    if corpus_bytes is None:
        return False, (
            "corpus_too_large" if corpus_status == "too_large"
            else f"corpus_{corpus_status}"
        )
    if canonical_bytes_sha256(corpus_bytes) != dataset.get(
        "corpus_sha256_canonical_lf"
    ):
        return False, "corpus_hash_mismatch"
    try:
        corpus_inventory = inspect_training_corpus_bytes(corpus_bytes)
    except (TypeError, ValueError):
        return False, "corpus_invalid"
    if (
        corpus_inventory["total_examples"] != dataset.get("total_examples")
        or len(corpus_inventory["observed_scenarios"])
        != dataset.get("total_scenarios")
    ):
        return False, "corpus_metadata_mismatch"

    manifest_bytes, manifest_status = _read_bounded_snapshot(
        expected_manifest_path,
        MAX_VERIFIED_SFT_MANIFEST_BYTES,
    )
    if manifest_bytes is None:
        return False, (
            "manifest_too_large" if manifest_status == "too_large"
            else f"manifest_{manifest_status}"
        )
    if hashlib.sha256(manifest_bytes).hexdigest() != corpus_manifest.get("sha256"):
        return False, "hash_mismatch"
    try:
        source_manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, "invalid_manifest"
    if not isinstance(source_manifest, dict):
        return False, "invalid_manifest"

    if (
        source_manifest.get("corpus_sha256_canonical_lf")
        != dataset.get("corpus_sha256_canonical_lf")
        or source_manifest.get("split") != dataset.get("split")
        or type(source_manifest.get("total_examples")) is not int
        or source_manifest.get("total_examples") != dataset.get("total_examples")
        or type(source_manifest.get("total_scenarios")) is not int
        or source_manifest.get("total_scenarios") != dataset.get("total_scenarios")
    ):
        return False, "metadata_mismatch"

    if dataset.get("data_origin") == "scenario_derived_synthetic" and (
        source_manifest.get("data_origin") != "scenario_derived_synthetic"
        or source_manifest.get("synthetic") is not True
    ):
        return False, "origin_mismatch"
    return True, "verified"


def normalize_training_data_provenance(
    dataset: dict[str, Any],
    *,
    approved_corpus_path: Path | None = None,
) -> dict[str, Any]:
    """Validate source metadata, reading corpus paths only after explicit approval."""
    normalized = dict(dataset)
    present_fields = _DATA_ORIGIN_PROVENANCE_FIELDS.intersection(normalized)
    if not present_fields:
        normalized.update(
            {
                "data_origin": UNVERIFIED_DATA_ORIGIN,
                "synthetic": None,
                "data_origin_source": "unverified",
                "corpus_manifest": {
                    "present": False,
                    "path": None,
                    "sha256": None,
                    "content_verified": False,
                    "verification_status": "absent",
                },
            }
        )
        return normalized

    missing = _DATA_ORIGIN_PROVENANCE_FIELDS.difference(normalized)
    if missing:
        raise ValueError(
            f"SFT training data origin provenance is incomplete: {sorted(missing)}"
        )

    data_origin = normalized["data_origin"]
    synthetic = normalized["synthetic"]
    origin_source = normalized["data_origin_source"]
    corpus_manifest = normalized["corpus_manifest"]
    if not isinstance(data_origin, str) or not data_origin.strip():
        raise ValueError("SFT training data origin must be a non-empty string")
    if synthetic is not None and type(synthetic) is not bool:
        raise ValueError("SFT training synthetic flag must be true, false, or null")
    if not isinstance(corpus_manifest, dict):
        raise ValueError("SFT corpus manifest provenance must be an object")

    manifest_present = corpus_manifest.get("present")
    manifest_path = corpus_manifest.get("path")
    manifest_sha256 = corpus_manifest.get("sha256")
    if type(manifest_present) is not bool:
        raise ValueError("SFT corpus manifest provenance requires a boolean present flag")
    if manifest_present:
        if not isinstance(manifest_path, str) or not manifest_path:
            raise ValueError("Present SFT corpus manifest provenance requires a path")
        if not _has_sha256(manifest_sha256):
            raise ValueError("Present SFT corpus manifest provenance requires a SHA-256")
    elif manifest_path is not None or manifest_sha256 is not None:
        raise ValueError("Absent SFT corpus manifest provenance cannot include a path or hash")

    if origin_source == "adjacent_corpus_manifest":
        if not manifest_present:
            raise ValueError("Adjacent SFT data origin requires manifest provenance")
    elif origin_source != "unverified":
        raise ValueError("Unknown SFT data origin source")

    if not _has_sha256(normalized.get("corpus_sha256_canonical_lf")):
        raise ValueError("SFT training data provenance lacks the corpus SHA-256")
    total_examples = normalized.get("total_examples")
    total_scenarios = normalized.get("total_scenarios")
    if type(total_examples) is not int or total_examples < 1:
        raise ValueError("SFT training data provenance lacks a positive example count")
    if type(total_scenarios) is not int or total_scenarios != len(TRAIN_SPLIT):
        raise ValueError("SFT training data provenance has an invalid scenario count")

    manifest_content_verified, verification_status = _verify_recorded_corpus_manifest(
        normalized,
        corpus_manifest,
        approved_corpus_path=approved_corpus_path,
    )
    corpus_manifest = dict(corpus_manifest)
    corpus_manifest["content_verified"] = manifest_content_verified
    corpus_manifest["verification_status"] = verification_status
    normalized["corpus_manifest"] = corpus_manifest

    known_synthetic_source = (
        data_origin == "scenario_derived_synthetic"
        and synthetic is True
        and origin_source == "adjacent_corpus_manifest"
        and normalized["corpus_sha256_canonical_lf"]
        == SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256
        and total_examples == SCENARIO_DERIVED_SYNTHETIC_TOTAL_EXAMPLES
        and total_scenarios == SCENARIO_DERIVED_SYNTHETIC_TOTAL_SCENARIOS
        and manifest_content_verified
    )
    if not known_synthetic_source:
        normalized.update(
            {
                "data_origin": UNVERIFIED_DATA_ORIGIN,
                "synthetic": None,
                "data_origin_source": "unverified",
            }
        )
    return normalized


def _corpus_manifest_provenance(
    corpus_path: Path,
    *,
    corpus_sha256: str,
    corpus_inventory: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = Path(
        os.path.abspath(corpus_path.parent / CORPUS_MANIFEST_NAME)
    )
    if has_redirecting_path_component(manifest_path):
        raise ValueError(
            "Adjacent SFT corpus manifest must not use symlink, reparse, "
            "or hard-link redirects"
        )
    try:
        manifest_details = manifest_path.lstat()
    except FileNotFoundError:
        return {
            "data_origin": UNVERIFIED_DATA_ORIGIN,
            "synthetic": None,
            "data_origin_source": "unverified",
            "corpus_manifest": {"present": False, "path": None, "sha256": None},
        }
    except OSError as exc:
        raise ValueError("Adjacent SFT corpus manifest is unavailable") from exc
    if not stat.S_ISREG(manifest_details.st_mode):
        raise ValueError("Adjacent SFT corpus manifest must be a regular file")

    manifest_bytes, _ = _read_bounded_snapshot(
        manifest_path,
        MAX_VERIFIED_SFT_MANIFEST_BYTES,
    )
    if manifest_bytes is None:
        raise ValueError("Adjacent SFT corpus manifest is unreadable or unsafe")
    try:
        source_manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Adjacent SFT corpus manifest is unreadable or invalid JSON") from exc
    if not isinstance(source_manifest, dict):
        raise ValueError("Adjacent SFT corpus manifest must be a JSON object")
    if source_manifest.get("corpus_sha256_canonical_lf") != corpus_sha256:
        raise ValueError("Adjacent SFT corpus manifest corpus hash does not match the corpus")
    if source_manifest.get("split") != "train":
        raise ValueError("Adjacent SFT corpus manifest must identify the Train split")
    if (
        type(source_manifest.get("total_examples")) is not int
        or source_manifest["total_examples"] != corpus_inventory["total_examples"]
    ):
        raise ValueError("Adjacent SFT corpus manifest example count does not match the corpus")
    if (
        type(source_manifest.get("total_scenarios")) is not int
        or source_manifest["total_scenarios"] != len(corpus_inventory["observed_scenarios"])
    ):
        raise ValueError("Adjacent SFT corpus manifest scenario count does not match the corpus")

    data_origin = source_manifest.get("data_origin")
    synthetic = source_manifest.get("synthetic")
    is_known_synthetic_corpus = (
        corpus_sha256 == SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256
        and data_origin == "scenario_derived_synthetic"
        and synthetic is True
    )
    if is_known_synthetic_corpus:
        origin_source = "adjacent_corpus_manifest"
    else:
        data_origin = UNVERIFIED_DATA_ORIGIN
        synthetic = None
        origin_source = "unverified"

    return {
        "data_origin": data_origin,
        "synthetic": synthetic,
        "data_origin_source": origin_source,
        "corpus_manifest": {
            "present": True,
            "path": str(manifest_path),
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
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
    corpus_snapshot: TrainingCorpusSnapshot | None = None,
    output_dir: Path,
    base_model: str,
    base_model_revision: str,
    tokenizer: str,
    tokenizer_revision: str,
    role: str,
    hyperparameters: dict[str, Any],
    seed: int = TRAIN_SEED,
) -> dict[str, Any]:
    validate_hf_reference(
        base_model,
        base_model_revision,
        label="base model",
    )
    validate_hf_reference(
        tokenizer,
        tokenizer_revision,
        label="tokenizer",
    )

    source_path = Path(os.path.abspath(corpus_path))
    if corpus_snapshot is None:
        corpus_snapshot = snapshot_training_corpus(source_path)
    elif corpus_snapshot.source_path != source_path:
        raise ValueError("SFT corpus snapshot does not match the requested corpus path")

    corpus_inventory = corpus_snapshot.inventory
    corpus_sha256 = canonical_bytes_sha256(corpus_snapshot.raw_bytes)
    origin_provenance = _corpus_manifest_provenance(
        source_path,
        corpus_sha256=corpus_sha256,
        corpus_inventory=corpus_inventory,
    )
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
            "resolved_revision_basis": None,
        },
        "dataset": {
            "corpus_path": str(corpus_snapshot.source_path),
            "corpus_sha256_canonical_lf": corpus_sha256,
            "split": "train",
            "split_seed": TRAIN_SEED,
            "split_scenarios": list(TRAIN_SPLIT),
            "split_sha256": canonical_json_sha256(list(TRAIN_SPLIT)),
            **corpus_inventory,
            "total_scenarios": len(corpus_inventory["observed_scenarios"]),
            **origin_provenance,
        },
        "role_filter": role,
        "training_seed": seed,
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
    resolved_tokenizer_revision_basis: str,
) -> dict[str, Any]:
    if resolved_tokenizer_revision_basis not in _TOKENIZER_REVISION_BASES:
        raise ValueError("SFT tokenizer revision provenance basis is missing or invalid")
    validate_resolved_hf_commit(
        manifest["base_model"]["requested_revision"],
        resolved_model_revision,
        label="base model",
    )
    validate_resolved_hf_commit(
        manifest["tokenizer"]["requested_revision"],
        resolved_tokenizer_revision,
        label="tokenizer",
    )
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
        "resolved_revision_basis": resolved_tokenizer_revision_basis,
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
                "message": "SFT run failed; exception details are withheld",
            },
        }
    )
    return updated
