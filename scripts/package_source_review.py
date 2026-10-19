"""Build and verify an exact tracked-source review package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.check_submission_links import (
    CURRENT_MARKDOWN_PATHS,
    check_current_markdown_links,
)
from scripts.package_submission import (
    REQUIRED_REVIEW_ASSETS,
    build_submission_package,
    compute_asset_inventory_sha256,
    generate_submission_summary_md,
)

UPSTREAM_BASELINE = "bf9bd197c9f4a05ae55ade254802a9eef1a74356"
REVIEW_DOCUMENTS = tuple(
    dict.fromkeys(
        (
            *CURRENT_MARKDOWN_PATHS,
            "docs/project/COMPLETION_CHECKLIST.md",
            "docs/project/PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md",
            "docs/project/SOURCE_REVIEW_PACKAGE.md",
        )
    )
)
BUNDLED_LICENSE_PATHS = (
    "LICENSE",
    "frontend/THIRD_PARTY_NOTICES.md",
    "frontend/public/assets/fonts/OFL.txt",
    "frontend/src/reference/resources/OFL.txt",
    "static/vendor/LUCIDE-LICENSE",
)
INVENTORY_PATH = "source_inventory.json"
PROOF_PATH = "proof.json"
MANIFEST_PATH = "inventory/SUBMISSION_MANIFEST.json"
SUMMARY_PATH = "inventory/SUBMISSION_SUMMARY.md"
MAX_PROOF_BYTES = 64 * 1024
MAX_SIDECAR_BYTES = 4 * 1024 * 1024
ARCHIVE_EXTRA_MEMBERS = (
    ("review/SUBMISSION_MANIFEST.json", MANIFEST_PATH),
    ("review/SUBMISSION_SUMMARY.md", SUMMARY_PATH),
)
_REPO_SHA_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_WINDOWS_FORBIDDEN_CHARS = frozenset('<>:"|?*')
_FROZEN_EVIDENCE_PATHS = {
    "artifacts/SUBMISSION_MANIFEST.json",
    "artifacts/SUBMISSION_SUMMARY.md",
}


@dataclass(frozen=True)
class GitFile:
    path: str
    mode: str
    blob_sha: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _git(root: Path, *arguments: str, input_data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        input=input_data,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"Git command failed: git {arguments[0]}")
    return result.stdout


def _decode_ascii(value: bytes, description: str) -> str:
    try:
        return value.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ValueError(f"Git returned an invalid {description}") from error


def _resolve_repo_root(repo_root: Path) -> Path:
    try:
        root = Path(repo_root).resolve(strict=True)
        top_level = Path(
            os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()
        ).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("repo_root must be an accessible Git worktree root") from error
    if not root.is_dir() or root != top_level:
        raise ValueError("repo_root must be the Git worktree root")
    return root


def _validate_expected_source_sha(expected_source_sha: str) -> str:
    if not isinstance(expected_source_sha, str) or not _REPO_SHA_PATTERN.fullmatch(
        expected_source_sha
    ):
        raise ValueError("expected_source_sha must be a full 40- or 64-character Git SHA")
    return expected_source_sha.lower()


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_safe_scratch_directory(repo_root: Path, output_dir: Path) -> Path:
    output_root = output_dir.resolve(strict=False)
    metadata_directories = _git_metadata_directories(repo_root)
    candidates = (
        [tempfile.tempdir]
        if tempfile.tempdir is not None
        else tempfile._candidate_tempdir_list()
    )
    for raw_candidate in candidates:
        try:
            candidate = Path(os.fsdecode(raw_candidate)).resolve(strict=False)
        except (OSError, RuntimeError, TypeError) as error:
            raise ValueError("Cannot resolve a system temporary directory") from error
        if not candidate.is_dir() or not os.access(
            candidate,
            os.W_OK | os.X_OK,
        ):
            continue
        if _within(candidate, output_root) or any(
            _within(candidate, metadata_dir) for metadata_dir in metadata_directories
        ):
            raise ValueError(
                "System temporary directory must be outside package output and Git metadata"
            )
        return candidate
    raise ValueError("No writable system temporary directory is available")


def _git_metadata_directories(repo_root: Path) -> tuple[Path, ...]:
    directories: list[Path] = []
    for option in ("--git-common-dir", "--git-dir"):
        raw_path = _git(
            repo_root,
            "rev-parse",
            "--path-format=absolute",
            option,
        )
        try:
            directory = Path(os.fsdecode(raw_path).strip()).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError(f"Cannot resolve Git metadata path: {option}") from error
        if not directory.is_dir():
            raise ValueError(f"Git metadata path is not a directory: {option}")
        if directory not in directories:
            directories.append(directory)
    return tuple(directories)


def _metadata_is_reparse_point(path: Path, metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if getattr(metadata, "st_file_attributes", 0) & reparse_attribute:
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        try:
            return bool(is_junction())
        except (OSError, RuntimeError) as error:
            raise ValueError(f"Cannot inspect reparse point: {path}") from error
    return False


def _check_path_components(path: Path, *, require_directory: bool) -> None:
    parts = path.parts
    if not parts:
        raise ValueError(f"Invalid path: {path}")
    component = Path(parts[0])
    for index, part in enumerate(parts[1:], start=1):
        component = component / part
        is_leaf = index == len(parts) - 1
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError(f"Cannot inspect path component: {component}") from error
        if _metadata_is_reparse_point(component, metadata):
            raise ValueError(f"Path traverses a symbolic link or reparse point: {component}")
        if not is_leaf and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"Path component is not a directory: {component}")
        if is_leaf and require_directory and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"Path is not a directory: {component}")


def _resolve_package_location(
    repo_root: Path,
    package_dir: Path,
    *,
    require_new: bool,
) -> Path:
    candidate = Path(package_dir)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    candidate = Path(os.path.abspath(candidate))
    _check_path_components(candidate, require_directory=False)

    artifacts = (repo_root / "artifacts").resolve(strict=False)
    if candidate == repo_root or _within(candidate, artifacts):
        if require_new:
            raise ValueError("output_dir must be separate from the repository's artifacts")
        raise ValueError("package_dir must be separate from canonical artifacts")
    if any(
        candidate == metadata_dir or _within(candidate, metadata_dir)
        for metadata_dir in _git_metadata_directories(repo_root)
    ):
        directory_name = "output_dir" if require_new else "package_dir"
        raise ValueError(f"{directory_name} must be separate from Git metadata")
    if _within(repo_root, candidate):
        directory_name = "output_dir" if require_new else "package_dir"
        raise ValueError(f"{directory_name} cannot be an ancestor of the repository")

    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        if require_new:
            return candidate
        raise ValueError("package_dir must be an existing directory")
    except OSError as error:
        directory_name = "output_dir" if require_new else "package_dir"
        raise ValueError(f"Cannot inspect {directory_name}: {candidate}") from error

    if require_new:
        raise ValueError("output_dir must be a new directory that does not already exist")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("package_dir must be an existing directory")
    return candidate


def _resolve_new_output_path(repo_root: Path, output_dir: Path) -> Path:
    return _resolve_package_location(repo_root, output_dir, require_new=True)


def _package_file(output_dir: Path, relative_path: str) -> Path:
    relative = _validate_member_path(relative_path)
    root = output_dir.resolve(strict=True)
    candidate = root
    for index, part in enumerate(relative.parts):
        candidate = candidate / part
        try:
            metadata = candidate.lstat()
        except OSError as error:
            raise ValueError(f"Package file cannot be inspected: {relative_path}") from error
        if _metadata_is_reparse_point(candidate, metadata):
            raise ValueError(
                f"Package path traverses a symbolic link or reparse point: {relative_path}"
            )
        is_leaf = index == len(relative.parts) - 1
        if not is_leaf and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError(f"Package path component is not a directory: {relative_path}")
        if is_leaf and not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"Package path is not a regular file: {relative_path}")
        resolved = candidate.resolve(strict=True)
        if not _within(resolved, root):
            raise ValueError(f"Package path escapes its output directory: {relative_path}")
    return candidate


def _create_output_directory(output_dir: Path) -> None:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    _check_path_components(output_dir, require_directory=False)
    try:
        output_dir.mkdir(exist_ok=False)
    except FileExistsError as error:
        raise ValueError("output_dir must be a new directory that does not already exist") from error


def _validate_member_path(value: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
    ):
        raise ValueError(f"Unsafe archive member path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or not path.parts:
        raise ValueError(f"Unsafe archive member path: {value!r}")

    for part in path.parts:
        if (
            part in {".", ".."}
            or part.casefold() == ".git"
            or part.endswith((" ", "."))
            or any(ord(character) < 32 or character in _WINDOWS_FORBIDDEN_CHARS for character in part)
        ):
            raise ValueError(f"Unsafe archive member path: {value!r}")
        device_name = part.split(".", 1)[0].casefold()
        if device_name in _WINDOWS_RESERVED_NAMES:
            raise ValueError(f"Unsafe archive member path: {value!r}")
    return path


def _validate_member_set(paths: list[str]) -> None:
    seen_exact: set[str] = set()
    folded_spellings: dict[str, str] = {}
    file_paths: set[str] = set()
    directory_paths: set[str] = set()

    for value in paths:
        path = _validate_member_path(value)
        if value in seen_exact:
            raise ValueError(f"Duplicate archive member path: {value}")
        seen_exact.add(value)
        parts = path.parts
        for index in range(1, len(parts) + 1):
            spelling = "/".join(parts[:index])
            folded = spelling.casefold()
            previous = folded_spellings.get(folded)
            if previous is not None and previous != spelling:
                raise ValueError(
                    f"Case-folding archive path collision: {previous!r} and {spelling!r}"
                )
            folded_spellings[folded] = spelling
            if index < len(parts):
                if folded in file_paths:
                    raise ValueError(f"Archive file/directory path collision: {spelling}")
                directory_paths.add(folded)
            else:
                if folded in directory_paths:
                    raise ValueError(f"Archive file/directory path collision: {spelling}")
                file_paths.add(folded)


def _read_git_tree(repo_root: Path, source_commit: str) -> list[GitFile]:
    output = _git(repo_root, "ls-tree", "-r", "-z", "--full-tree", source_commit)
    entries: list[GitFile] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode_bytes, kind, blob_bytes = metadata.split()
            path = encoded_path.decode("utf-8", errors="strict")
            mode = _decode_ascii(mode_bytes, "file mode")
            blob_sha = _decode_ascii(blob_bytes, "blob id")
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("Git tree contains an invalid entry") from error

        _validate_member_path(path)
        if kind != b"blob":
            raise ValueError(f"Unsupported Git tree object at {path}: submodules are not supported")
        if mode not in {"100644", "100755"}:
            if mode == "120000":
                raise ValueError(f"Symbolic links are not supported in source packages: {path}")
            raise ValueError(f"Unsupported Git file mode {mode} at {path}")
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", blob_sha):
            raise ValueError(f"Git tree contains an invalid blob id at {path}")
        entries.append(GitFile(path=path, mode=mode, blob_sha=blob_sha))

    if not entries:
        raise ValueError("Source commit has no tracked files")
    _validate_member_set([entry.path for entry in entries])
    return sorted(entries, key=lambda entry: entry.path)


def _source_path(repo_root: Path, relative_path: str) -> Path:
    relative = _validate_member_path(relative_path)
    candidate = repo_root
    for part in relative.parts:
        candidate = candidate / part
        try:
            metadata = candidate.lstat()
        except OSError as error:
            raise ValueError(f"Tracked source path cannot be inspected: {relative_path}") from error
        if _metadata_is_reparse_point(candidate, metadata):
            raise ValueError(
                f"Tracked source path traverses a symbolic link or reparse point: {relative_path}"
            )
        resolved = candidate.resolve(strict=True)
        if not _within(resolved, repo_root):
            raise ValueError(f"Tracked source path escapes repository root: {relative_path}")
    if not stat.S_ISREG(candidate.lstat().st_mode):
        raise ValueError(f"Tracked source path is not a regular file: {relative_path}")
    return candidate


def _clean_filter_blob_sha(repo_root: Path, entry: GitFile, data: bytes) -> str:
    digest = _decode_ascii(
        _git(
            repo_root,
            "hash-object",
            "--path",
            entry.path,
            "--stdin",
            input_data=data,
        ),
        f"clean-filter blob id for {entry.path}",
    )
    if digest != entry.blob_sha:
        raise ValueError(
            f"Checkout bytes do not match the Git clean-filter blob for {entry.path}"
        )
    return digest


def _snapshot_files(repo_root: Path, entries: list[GitFile]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for entry in entries:
        source_path = _source_path(repo_root, entry.path)
        data = source_path.read_bytes()
        _clean_filter_blob_sha(repo_root, entry, data)
        files.append(
            {
                "path": entry.path,
                "mode": entry.mode,
                "git_blob_sha": entry.blob_sha,
                "sha256": _sha256(data),
                "size_bytes": len(data),
            }
        )
    return files


def _verify_git_state(
    repo_root: Path,
    expected_source_sha: str,
    *,
    allow_origin_main_advance: bool = False,
) -> tuple[str, str, str]:
    head = _decode_ascii(_git(repo_root, "rev-parse", "--verify", "HEAD^{commit}"), "HEAD")
    origin_main = _decode_ascii(
        _git(repo_root, "rev-parse", "--verify", "origin/main^{commit}"),
        "origin/main",
    )
    if head != expected_source_sha:
        raise ValueError("HEAD does not match expected_source_sha")
    if origin_main != expected_source_sha:
        if not allow_origin_main_advance:
            raise ValueError("The fetched local origin/main ref does not match expected_source_sha")
        ancestry = subprocess.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                expected_source_sha,
                origin_main,
            ],
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if ancestry.returncode == 1:
            raise ValueError(
                "expected_source_sha is not in the fetched local origin/main history"
            )
        if ancestry.returncode != 0:
            raise ValueError("Could not verify expected_source_sha ancestry in origin/main")
    if _git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
        "--ignore-submodules=none",
    ).strip():
        raise ValueError("Tracked source and index must be clean before packaging")
    tree = _decode_ascii(
        _git(repo_root, "rev-parse", "--verify", f"{expected_source_sha}^{{tree}}"),
        "source tree",
    )
    return head, origin_main, tree


def _source_timestamp(repo_root: Path, source_commit: str) -> datetime:
    try:
        timestamp = int(
            _decode_ascii(
                _git(repo_root, "show", "-s", "--format=%ct", source_commit),
                "source commit timestamp",
            )
        )
        return datetime.fromtimestamp(timestamp, UTC)
    except (OSError, OverflowError, ValueError) as error:
        raise ValueError("Git returned an invalid source commit timestamp") from error


def _zip_datetime(timestamp: datetime) -> tuple[int, int, int, int, int, int]:
    earliest = datetime(1980, 1, 1, tzinfo=UTC)
    latest = datetime(2107, 12, 31, 23, 59, 58, tzinfo=UTC)
    normalized = min(max(timestamp, earliest), latest)
    second = normalized.second - normalized.second % 2
    return (
        normalized.year,
        normalized.month,
        normalized.day,
        normalized.hour,
        normalized.minute,
        second,
    )


def _archive_member_info(
    member: str,
    mode: int,
    source_timestamp: datetime,
) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(member, date_time=_zip_datetime(source_timestamp))
    info.create_system = 3
    info.external_attr = mode << 16
    return info


def _validate_source_links(repo_root: Path) -> None:
    errors = check_current_markdown_links(repo_root, documents=REVIEW_DOCUMENTS)
    if errors:
        raise ValueError("Current-facing source links are invalid: " + "; ".join(errors))


def _validate_submission_manifest(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if manifest.get("package_ready") is not True:
        raise ValueError("Selected submission inventory is not READY_FOR_REVIEW")
    if manifest.get("status") != "NOT_CERTIFIED" or manifest.get("empirical_metrics") is not None:
        raise ValueError("Submission inventory must preserve NOT_CERTIFIED and null metrics")
    assets = manifest.get("assets")
    if not isinstance(assets, dict):
        raise TypeError("Selected submission inventory has no asset map")
    missing_required = sorted(set(_required_asset_paths()) - assets.keys())
    if missing_required:
        raise ValueError(
            "Selected submission inventory is missing required review assets: "
            + ", ".join(missing_required)
        )
    for path, metadata in assets.items():
        _validate_member_path(path)
        if not isinstance(metadata, dict):
            raise TypeError(f"Invalid selected asset metadata for {path}")
        if (
            not isinstance(metadata.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
            or not isinstance(metadata.get("size_bytes"), int)
            or metadata["size_bytes"] < 0
        ):
            raise ValueError(f"Invalid selected asset hash or size for {path}")
    if manifest.get("asset_inventory_sha256") != compute_asset_inventory_sha256(assets):
        raise ValueError("Selected submission inventory hash does not match its assets")
    return assets


def _required_asset_paths() -> tuple[str, ...]:
    return tuple(REQUIRED_REVIEW_ASSETS)


def _write_json_exclusive(path: Path, data: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=True)
        stream.write("\n")


def _read_bounded_bytes(
    path: Path,
    label: str,
    max_bytes: int = MAX_SIDECAR_BYTES,
) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"{label} exceeds its {max_bytes}-byte limit")
    return data


def _read_json(
    path: Path,
    label: str,
    max_bytes: int = MAX_SIDECAR_BYTES,
) -> dict[str, Any]:
    try:
        value = json.loads(_read_bounded_bytes(path, label, max_bytes).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read {label}") from error
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a JSON object")
    return value


def _prepare_submission_artifacts(
    repo_root: Path,
    output_dir: Path,
    source_commit: str,
    source_timestamp: datetime,
) -> tuple[dict[str, Any], bytes, bytes]:
    if Path.cwd().resolve() == repo_root:
        manifest = build_submission_package(output_dir=output_dir)
    else:
        command = (
            "import sys; "
            "from pathlib import Path; "
            "from scripts.package_submission import build_submission_package; "
            "build_submission_package(output_dir=Path(sys.argv[1]))"
        )
        result = subprocess.run(
            [sys.executable, "-c", command, os.fspath(output_dir)],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("Could not regenerate submission artifacts from the source tree")
        manifest = _read_json(
            output_dir / "SUBMISSION_MANIFEST.json",
            "regenerated submission inventory",
        )

    manifest["generated_at"] = source_timestamp.isoformat()
    manifest_bytes = json.dumps(
        manifest,
        indent=2,
        ensure_ascii=True,
    ).encode("utf-8")
    summary_bytes = generate_submission_summary_md(manifest).encode("utf-8")
    (output_dir / "SUBMISSION_MANIFEST.json").write_bytes(manifest_bytes)
    (output_dir / "SUBMISSION_SUMMARY.md").write_bytes(summary_bytes)
    return manifest, manifest_bytes, summary_bytes


def _check_upstream_lineage(repo_root: Path, source_commit: str) -> dict[str, Any]:
    if _git(repo_root, "rev-parse", "--is-shallow-repository").strip() == b"true":
        raise ValueError("Source review requires a full-history Git checkout")
    available = subprocess.run(
        ["git", "cat-file", "-e", f"{UPSTREAM_BASELINE}^{{commit}}"],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if available.returncode != 0:
        raise ValueError(
            "Required upstream baseline object is unavailable; use a full-history Git checkout"
        )

    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", UPSTREAM_BASELINE, source_commit],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if ancestor.returncode != 0:
        raise ValueError("Source commit does not descend from the upstream AtlasOps baseline")
    source_license = _git(repo_root, "show", f"{source_commit}:LICENSE")
    upstream_license = _git(repo_root, "show", f"{UPSTREAM_BASELINE}:LICENSE")
    if source_license != upstream_license:
        raise ValueError("The source LICENSE differs from the upstream MIT license")
    return {
        "status": "PASS",
        "baseline": UPSTREAM_BASELINE,
        "license_sha256": _sha256(source_license),
    }


def _write_archive(
    repo_root: Path,
    output_dir: Path,
    source_commit: str,
    entries: list[GitFile],
    source_timestamp: datetime,
) -> tuple[Path, list[dict[str, Any]]]:
    archive_path = output_dir / f"atlasops-source-{source_commit[:12]}.zip"
    files: list[dict[str, Any]] = []
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for entry in entries:
            source_path = _source_path(repo_root, entry.path)
            data = source_path.read_bytes()
            _clean_filter_blob_sha(repo_root, entry, data)
            files.append(
                {
                    "path": entry.path,
                    "mode": entry.mode,
                    "git_blob_sha": entry.blob_sha,
                    "sha256": _sha256(data),
                    "size_bytes": len(data),
                }
            )
            info = _archive_member_info(
                entry.path,
                int(entry.mode, 8),
                source_timestamp,
            )
            bundle.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)

        for archive_name, output_name in ARCHIVE_EXTRA_MEMBERS:
            data = (output_dir / output_name).read_bytes()
            info = _archive_member_info(
                archive_name,
                0o100644,
                source_timestamp,
            )
            bundle.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    return archive_path, files


def _compare_archive_member_to_checkout(
    bundle: zipfile.ZipFile,
    repo_root: Path,
    entry: GitFile,
    metadata: dict[str, Any],
) -> None:
    source_path = _source_path(repo_root, entry.path)
    digest = hashlib.sha256()
    size = 0
    with source_path.open("rb") as checkout, bundle.open(entry.path, "r") as archived:
        while True:
            checkout_chunk = checkout.read(1024 * 1024)
            archive_chunk = archived.read(1024 * 1024)
            if checkout_chunk != archive_chunk:
                raise ValueError(f"Archive bytes differ from the checkout for {entry.path}")
            if not checkout_chunk:
                break
            digest.update(checkout_chunk)
            size += len(checkout_chunk)
    if digest.hexdigest() != metadata["sha256"] or size != metadata["size_bytes"]:
        raise ValueError(f"Checkout hash or size changed for {entry.path}")
    data = source_path.read_bytes()
    _clean_filter_blob_sha(repo_root, entry, data)


def _verify_package_artifacts(
    repo_root: Path,
    output_dir: Path,
    expected_source_sha: str,
    *,
    scratch_root: Path,
    persisted_proof: dict[str, Any] | None = None,
    allow_origin_main_advance: bool = False,
) -> dict[str, Any]:
    source_commit, _, source_tree = _verify_git_state(
        repo_root,
        expected_source_sha,
        allow_origin_main_advance=allow_origin_main_advance,
    )
    source_timestamp = _source_timestamp(repo_root, source_commit)
    entries = _read_git_tree(repo_root, source_commit)
    tree_inventory = _snapshot_files(repo_root, entries)
    inventory_by_path = {item["path"]: item for item in tree_inventory}
    inventory_path = _package_file(output_dir, INVENTORY_PATH)
    source_inventory = _read_json(inventory_path, "source inventory")
    expected_inventory = {
        "schema_version": 1,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "files": tree_inventory,
    }
    if source_inventory != expected_inventory:
        raise ValueError("Source inventory does not match the pinned Git tree and checkout")

    manifest_path = _package_file(output_dir, MANIFEST_PATH)
    summary_path = _package_file(output_dir, SUMMARY_PATH)
    manifest = _read_json(manifest_path, "selected submission inventory")
    assets = _validate_submission_manifest(manifest)
    manifest_bytes = _read_bounded_bytes(manifest_path, "selected submission inventory")
    summary_bytes = _read_bounded_bytes(summary_path, "selected submission summary")
    if persisted_proof is not None:
        with tempfile.TemporaryDirectory(
            prefix=".source-review-manifest-",
            dir=scratch_root,
        ) as generated_dir:
            expected_manifest, expected_manifest_bytes, expected_summary_bytes = (
                _prepare_submission_artifacts(
                    repo_root,
                    Path(generated_dir) / "inventory",
                    source_commit,
                    source_timestamp,
                )
            )
        if manifest_bytes != expected_manifest_bytes:
            raise ValueError("Selected submission manifest does not match the pinned source")
        if summary_bytes != expected_summary_bytes:
            raise ValueError("Selected submission summary does not match the pinned source")
        manifest = expected_manifest
    missing_required = sorted(set(_required_asset_paths()) - inventory_by_path.keys())
    if missing_required:
        raise ValueError("Required review assets are untracked or missing: " + ", ".join(missing_required))
    missing_licenses = sorted(set(BUNDLED_LICENSE_PATHS) - inventory_by_path.keys())
    if missing_licenses:
        raise ValueError("Required license/notice paths are missing: " + ", ".join(missing_licenses))
    for path in assets:
        if path not in inventory_by_path:
            raise ValueError(f"Selected review asset is not in the tracked source tree: {path}")
        metadata = inventory_by_path[path]
        selected = assets[path]
        if metadata["sha256"] != selected["sha256"] or metadata["size_bytes"] != selected["size_bytes"]:
            raise ValueError(f"Selected asset hash or size differs from the source inventory: {path}")

    archive_name = f"atlasops-source-{source_commit[:12]}.zip"
    archive_path = _package_file(output_dir, archive_name)
    archive_sha256, archive_size = _sha256_file(archive_path)
    if persisted_proof is not None:
        if persisted_proof.get("archive") != archive_name:
            raise ValueError("proof.json names an unexpected source archive")
        if persisted_proof.get("archive_sha256") != archive_sha256:
            raise ValueError("Archive SHA-256 does not match proof.json")
        if persisted_proof.get("archive_size_bytes") != archive_size:
            raise ValueError("Archive size does not match proof.json")

    expected_names = [entry.path for entry in entries] + [
        member for member, _ in ARCHIVE_EXTRA_MEMBERS
    ]
    _validate_member_set(expected_names)
    sidecar_bytes = {
        MANIFEST_PATH: manifest_bytes,
        SUMMARY_PATH: summary_bytes,
    }
    expected_member_sizes = {
        item["path"]: item["size_bytes"]
        for item in tree_inventory
    }
    for archive_member, output_name in ARCHIVE_EXTRA_MEMBERS:
        expected_member_sizes[archive_member] = len(sidecar_bytes[output_name])
    expected_total_size = sum(expected_member_sizes.values())
    expected_zip_datetime = _zip_datetime(source_timestamp)
    with zipfile.ZipFile(archive_path, "r") as bundle:
        infos = bundle.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or set(names) != set(expected_names):
            raise ValueError("Source ZIP members do not match tracked files plus review sidecars")
        for info in infos:
            if info.file_size != expected_member_sizes[info.filename]:
                raise ValueError(
                    f"Source ZIP member size does not match the source inventory: {info.filename}"
                )
            if info.date_time != expected_zip_datetime:
                raise ValueError(
                    f"Source ZIP timestamp does not match the source commit: {info.filename}"
                )
        if sum(info.file_size for info in infos) != expected_total_size:
            raise ValueError("Source ZIP total uncompressed size does not match the source inventory")

        with tempfile.TemporaryDirectory(
            prefix=".source-review-canonical-",
            dir=scratch_root,
        ) as canonical_dir:
            canonical_root = Path(canonical_dir)
            (canonical_root / "inventory").mkdir()
            (canonical_root / MANIFEST_PATH).write_bytes(manifest_bytes)
            (canonical_root / SUMMARY_PATH).write_bytes(summary_bytes)
            canonical_archive, _ = _write_archive(
                repo_root,
                canonical_root,
                source_commit,
                entries,
                source_timestamp,
            )
            canonical_identity = _sha256_file(canonical_archive)
        if canonical_identity != (archive_sha256, archive_size):
            raise ValueError("Source ZIP bytes do not match the canonical source archive")

        damaged_member = bundle.testzip()
        if damaged_member is not None:
            raise ValueError(f"Source ZIP CRC failed for {damaged_member}")

        for entry in entries:
            info = bundle.getinfo(entry.path)
            if info.external_attr >> 16 != int(entry.mode, 8):
                raise ValueError(f"Source ZIP mode does not match Git for {entry.path}")
            _compare_archive_member_to_checkout(
                bundle,
                repo_root,
                entry,
                inventory_by_path[entry.path],
            )

        for archive_member, output_name in ARCHIVE_EXTRA_MEMBERS:
            info = bundle.getinfo(archive_member)
            if info.external_attr >> 16 != 0o100644:
                raise ValueError(f"Review sidecar mode is invalid: {archive_member}")
            sidecar_path = _package_file(output_dir, output_name)
            if bundle.read(archive_member) != sidecar_path.read_bytes():
                raise ValueError(f"Review sidecar differs from its ZIP copy: {archive_member}")

        with tempfile.TemporaryDirectory(
            prefix=".source-review-verify-",
            dir=scratch_root,
        ) as extracted_dir:
            extracted_root = Path(extracted_dir)
            bundle.extractall(extracted_root)
            link_errors = check_current_markdown_links(
                extracted_root,
                documents=REVIEW_DOCUMENTS,
            )
            if link_errors:
                raise ValueError(
                    "Extracted source links are invalid: " + "; ".join(link_errors)
                )
            for path, metadata in assets.items():
                extracted_path = extracted_root.joinpath(*PurePosixPath(path).parts)
                actual_sha256, actual_size = _sha256_file(extracted_path)
                if actual_sha256 != metadata["sha256"] or actual_size != metadata["size_bytes"]:
                    raise ValueError(f"Selected extracted asset hash or size differs: {path}")

    upstream = _check_upstream_lineage(repo_root, source_commit)
    inventory_sha256, _ = _sha256_file(inventory_path)
    frozen_paths = [
        item
        for item in tree_inventory
        if item["path"].startswith("artifacts/evidence/")
        or item["path"] in _FROZEN_EVIDENCE_PATHS
    ]
    frozen_payload = json.dumps(
        frozen_paths,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")

    proof = {
        "schema_version": 1,
        "expected_source_sha": expected_source_sha,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "origin_main_commit": source_commit,
        "archive": archive_name,
        "archive_sha256": archive_sha256,
        "archive_size_bytes": archive_size,
        "source_inventory": INVENTORY_PATH,
        "source_inventory_sha256": inventory_sha256,
        "source_tracked_files": len(entries),
        "archive_entries": len(expected_names),
        "selected_asset_count": len(assets),
        "selected_asset_inventory_sha256": manifest["asset_inventory_sha256"],
        "required_review_assets": "PASS",
        "required_review_asset_count": len(_required_asset_paths()),
        "bundled_license_paths": list(BUNDLED_LICENSE_PATHS),
        "git_file_modes": "PASS",
        "git_clean_filter_blobs": "PASS",
        "raw_checkout_bytes": "PASS",
        "archive_crc": "PASS",
        "extracted_current_links": "PASS",
        "selected_asset_hashes": "PASS",
        "review_documents_checked": list(REVIEW_DOCUMENTS),
        "tracked_frozen_evidence_files": len(frozen_paths),
        "tracked_frozen_evidence_inventory_sha256": _sha256(frozen_payload),
        "upstream_ancestry_and_mit_license": upstream,
        "third_party_publication_rights_recertified": False,
        "package_readiness": "READY_FOR_REVIEW",
        "scientific_status": "NOT_CERTIFIED",
        "empirical_metrics": None,
        "execution_authorized": False,
    }
    if persisted_proof is not None and persisted_proof != proof:
        raise ValueError("proof.json does not match the verified source package")
    return proof


def build_source_review_package(
    repo_root: Path,
    output_dir: Path,
    expected_source_sha: str,
) -> dict[str, Any]:
    """Build a source-exact ZIP and its inventory/proof sidecars.

    The caller must run from repo_root because the established submission
    inventory API resolves its source checkout from the current directory.
    """
    root = _resolve_repo_root(repo_root)
    if Path.cwd().resolve() != root:
        raise ValueError("Run the package builder with the current directory set to repo_root")
    expected = _validate_expected_source_sha(expected_source_sha)
    source_commit, _, source_tree = _verify_git_state(root, expected)
    source_timestamp = _source_timestamp(root, source_commit)
    destination = _resolve_new_output_path(root, output_dir)
    _check_upstream_lineage(root, source_commit)
    scratch_root = _resolve_safe_scratch_directory(root, destination)
    entries = _read_git_tree(root, source_commit)
    _validate_member_set(
        [entry.path for entry in entries]
        + [member for member, _ in ARCHIVE_EXTRA_MEMBERS]
    )
    tracked_paths = {entry.path for entry in entries}
    missing_required = sorted(set(_required_asset_paths()) - tracked_paths)
    if missing_required:
        raise ValueError("Required review assets are untracked or missing: " + ", ".join(missing_required))
    missing_licenses = sorted(set(BUNDLED_LICENSE_PATHS) - tracked_paths)
    if missing_licenses:
        raise ValueError("Required license/notice paths are missing: " + ", ".join(missing_licenses))
    for entry in entries:
        _source_path(root, entry.path)
    _validate_source_links(root)

    _create_output_directory(destination)

    manifest, _, _ = _prepare_submission_artifacts(
        root,
        destination / "inventory",
        source_commit,
        source_timestamp,
    )
    _validate_submission_manifest(manifest)
    _, files = _write_archive(
        root,
        destination,
        source_commit,
        entries,
        source_timestamp,
    )
    by_path = {item["path"]: item for item in files}
    for path, selected in manifest["assets"].items():
        source_metadata = by_path.get(path)
        if source_metadata is None:
            raise ValueError(f"Selected review asset is not tracked: {path}")
        if (
            source_metadata["sha256"] != selected["sha256"]
            or source_metadata["size_bytes"] != selected["size_bytes"]
        ):
            raise ValueError(f"Selected review asset changed after source preflight: {path}")

    source_inventory = {
        "schema_version": 1,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "files": files,
    }
    _write_json_exclusive(destination / INVENTORY_PATH, source_inventory)
    final_state = _verify_git_state(root, expected)
    if final_state != (source_commit, source_commit, source_tree):
        raise ValueError("Git source state changed while building the package")
    proof = _verify_package_artifacts(
        root,
        destination,
        expected,
        scratch_root=scratch_root,
    )
    _write_json_exclusive(destination / PROOF_PATH, proof)
    return proof


def verify_source_review_package(
    repo_root: Path,
    package_dir: Path,
    expected_source_sha: str,
) -> dict[str, Any]:
    """Recheck an existing package against the pinned clean Git checkout."""
    root = _resolve_repo_root(repo_root)
    expected = _validate_expected_source_sha(expected_source_sha)
    package_root = _resolve_package_location(root, package_dir, require_new=False)
    _verify_git_state(root, expected, allow_origin_main_advance=True)
    _check_upstream_lineage(root, expected)
    scratch_root = _resolve_safe_scratch_directory(root, package_root)
    proof = _read_json(
        _package_file(package_root, PROOF_PATH),
        "source package proof",
        MAX_PROOF_BYTES,
    )
    result = _verify_package_artifacts(
        root,
        package_root,
        expected,
        scratch_root=scratch_root,
        persisted_proof=proof,
        allow_origin_main_advance=True,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an exact tracked-source ZIP for review without certifying research gates."
    )
    parser.add_argument(
        "--expected-source-sha",
        required=True,
        help="Full commit SHA that must match clean HEAD and the fetched local origin/main ref",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New output directory, separate from artifacts/ and Git metadata",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify an existing package at --output-dir instead of building it",
    )
    args = parser.parse_args(argv)

    try:
        if args.verify:
            proof = verify_source_review_package(
                Path.cwd(),
                args.output_dir,
                args.expected_source_sha,
            )
        else:
            proof = build_source_review_package(
                Path.cwd(),
                args.output_dir,
                args.expected_source_sha,
            )
    except (OSError, TypeError, ValueError, zipfile.BadZipFile) as error:
        parser.error(str(error))
    print(json.dumps(proof, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
