from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

import scripts.package_source_review as source_review
from scripts.package_submission import (
    REQUIRED_REVIEW_ASSETS,
    compute_asset_inventory_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_LICENSE_PATHS = source_review.BUNDLED_LICENSE_PATHS


def _run_git(root: Path, *arguments: str, input_data: bytes | None = None) -> bytes:
    return subprocess.check_output(
        ["git", *arguments],
        cwd=root,
        input=input_data,
        stderr=subprocess.PIPE,
    )


def _write_fixture_file(root: Path, relative_path: str, data: bytes) -> None:
    path = root.joinpath(*PurePosixPath(relative_path).parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _status_document() -> bytes:
    rows = [
        "| **Stage 0** | Owner | **G0** | Evidence | **NOT_PASSED** | Notes |",
        *(
            f"| **Stage {number}** | Owner | **G{number}** | Evidence | "
            "**NOT_PASSED** | Notes |"
            for number in range(1, 16)
        ),
    ]
    return ("\n".join(rows) + "\n").encode("utf-8")


def _make_git_fixture(
    tmp_path: Path,
    *,
    readme: bytes | None = None,
) -> Path:
    root = tmp_path / "repo"
    root.mkdir()

    helper_files = (
        "scripts/package_source_review.py",
        "scripts/package_submission.py",
        "scripts/check_submission_links.py",
    )
    for relative_path in helper_files:
        source = REPO_ROOT / relative_path
        _write_fixture_file(root, relative_path, source.read_bytes())

    file_paths = set(REQUIRED_REVIEW_ASSETS)
    file_paths.update(source_review.REVIEW_DOCUMENTS)
    file_paths.update(_LICENSE_PATHS)
    file_paths.update(
        {
            ".gitattributes",
            "tracked-only-review-target.txt",
            "crlf-review.txt",
            "reparse-review.txt",
        }
    )
    for relative_path in sorted(file_paths):
        if relative_path in helper_files:
            continue
        if relative_path == "README.md":
            content = readme or b"[Full source link](tracked-only-review-target.txt)\n"
        elif relative_path == "docs/project/MASTER_PIPELINE_STATUS.md":
            content = _status_document()
        elif relative_path == ".gitattributes":
            content = (
                b"*.md text eol=lf\n"
                b"crlf-review.txt text eol=crlf\n"
            )
        elif relative_path == "LICENSE":
            content = b"MIT License\nFixture license text.\n"
        else:
            content = f"fixture:{relative_path}\n".encode()
        _write_fixture_file(root, relative_path, content)

    _write_fixture_file(root, "tracked-only-review-target.txt", b"not a selected asset\n")
    _write_fixture_file(root, "crlf-review.txt", b"first line\r\nsecond line\r\n")
    _write_fixture_file(root, "reparse-review.txt", b"reparse target\n")

    _run_git(root, "init", "-b", "main")
    _run_git(root, "config", "user.name", "Source Package Test")
    _run_git(root, "config", "user.email", "source-package-test@example.invalid")
    _run_git(root, "config", "core.autocrlf", "false")
    _run_git(root, "add", "--all")
    _run_git(root, "commit", "-m", "fixture source")
    _run_git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def _make_linked_git_fixture(tmp_path: Path) -> Path:
    source = tmp_path / "linked-source"
    source.mkdir()
    _write_fixture_file(source, "README.md", b"linked worktree fixture\n")
    _run_git(source, "init", "-b", "main")
    _run_git(source, "config", "user.name", "Linked Worktree Test")
    _run_git(source, "config", "user.email", "linked-worktree-test@example.invalid")
    _run_git(source, "add", "README.md")
    _run_git(source, "commit", "-m", "linked worktree fixture")
    _run_git(source, "update-ref", "refs/remotes/origin/main", "HEAD")
    linked = tmp_path / "linked-worktree"
    _run_git(source, "worktree", "add", "--detach", os.fspath(linked), "HEAD")
    return linked


def _head(root: Path) -> str:
    return _run_git(root, "rev-parse", "HEAD").decode("ascii").strip()


def _package(
    root: Path,
    output: Path,
    expected_sha: str | None = None,
) -> dict[str, object]:
    previous_directory = Path.cwd()
    os.chdir(root)
    try:
        return source_review.build_source_review_package(
            root,
            output,
            expected_sha or _head(root),
        )
    finally:
        os.chdir(previous_directory)


def _run_cli(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        path for path in (str(REPO_ROOT), environment.get("PYTHONPATH")) if path
    )
    environment["ATLASOPS_AUTO_HF_INFERENCE"] = "0"
    return subprocess.run(
        [sys.executable, "-m", "scripts.package_source_review", *arguments],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _repack_package_and_refresh_proof(
    output: Path,
    replacements: dict[str, bytes],
) -> None:
    proof_path = output / source_review.PROOF_PATH
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    archive_path = output / proof["archive"]
    repacked_path = archive_path.with_name(f"{archive_path.stem}.repacked.zip")
    with zipfile.ZipFile(archive_path, "r") as original, zipfile.ZipFile(
        repacked_path,
        "x",
        compression=zipfile.ZIP_DEFLATED,
    ) as repacked:
        for info in original.infolist():
            data = (
                replacements[info.filename]
                if info.filename in replacements
                else original.read(info.filename)
            )
            repacked.writestr(info, data, compress_type=info.compress_type)
    repacked_path.replace(archive_path)

    for archive_member, output_name in source_review.ARCHIVE_EXTRA_MEMBERS:
        if archive_member in replacements:
            (output / output_name).write_bytes(replacements[archive_member])

    archive_bytes = archive_path.read_bytes()
    proof["archive_sha256"] = hashlib.sha256(archive_bytes).hexdigest()
    proof["archive_size_bytes"] = len(archive_bytes)
    proof_path.write_text(
        json.dumps(proof, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def test_cli_builds_exact_tracked_archive_and_verifies_clean_filter_identity(tmp_path):
    root = _make_git_fixture(tmp_path)
    source_sha = _head(root)
    head_before = _run_git(root, "rev-parse", "HEAD")
    origin_before = _run_git(root, "rev-parse", "origin/main")
    status_before = _run_git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=no",
        "--ignore-submodules=none",
    )
    output = tmp_path / "source-review"

    result = _run_cli(
        root,
        "--expected-source-sha",
        source_sha,
        "--output-dir",
        os.fspath(output),
    )

    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    inventory = json.loads((output / "source_inventory.json").read_text(encoding="utf-8"))
    submission = json.loads(
        (output / "inventory" / "SUBMISSION_MANIFEST.json").read_text(encoding="utf-8")
    )
    archive_path = output / proof["archive"]
    tracked_paths = {item["path"] for item in inventory["files"]}
    assert proof["source_commit"] == source_sha
    assert proof["source_tree"] == inventory["source_tree"]
    assert proof["git_clean_filter_blobs"] == "PASS"
    assert proof["raw_checkout_bytes"] == "PASS"
    assert proof["extracted_current_links"] == "PASS"
    assert proof["scientific_status"] == "NOT_CERTIFIED"
    assert proof["empirical_metrics"] is None
    assert proof["execution_authorized"] is False
    assert "tracked-only-review-target.txt" in tracked_paths
    assert "tracked-only-review-target.txt" not in submission["assets"]

    crlf_entry = next(item for item in inventory["files"] if item["path"] == "crlf-review.txt")
    assert crlf_entry["sha256"] == source_review._sha256(b"first line\r\nsecond line\r\n")
    assert crlf_entry["git_blob_sha"] == _run_git(
        root,
        "rev-parse",
        f"{source_sha}:crlf-review.txt",
    ).decode("ascii").strip()
    assert _run_git(root, "cat-file", "blob", crlf_entry["git_blob_sha"]) == (
        b"first line\nsecond line\n"
    )

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        expected_names = tracked_paths | {
            member for member, _ in source_review.ARCHIVE_EXTRA_MEMBERS
        }
        assert len(names) == len(set(names))
        assert set(names) == expected_names
        assert archive.read("crlf-review.txt") == b"first line\r\nsecond line\r\n"
        assert archive.read("tracked-only-review-target.txt") == b"not a selected asset\n"
        for entry in inventory["files"]:
            assert archive.getinfo(entry["path"]).external_attr >> 16 == int(entry["mode"], 8)
        assert archive.testzip() is None

    assert source_review.verify_source_review_package(root, output, source_sha) == proof
    assert _run_git(root, "rev-parse", "HEAD") == head_before
    assert _run_git(root, "rev-parse", "origin/main") == origin_before
    assert (
        _run_git(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=no",
            "--ignore-submodules=none",
        )
        == status_before
    )

    original_archive = archive_path.read_bytes()
    manifest_path = output / "inventory" / "SUBMISSION_MANIFEST.json"
    proof_path = output / "proof.json"
    original_manifest = manifest_path.read_bytes()
    original_proof = proof_path.read_bytes()
    empty_manifest = json.loads(original_manifest)
    empty_manifest["assets"] = {}
    empty_manifest["asset_count"] = 0
    empty_manifest["asset_inventory_sha256"] = compute_asset_inventory_sha256({})
    empty_manifest_bytes = (
        json.dumps(empty_manifest, indent=2, ensure_ascii=True) + "\n"
    ).encode("utf-8")
    manifest_path.write_bytes(empty_manifest_bytes)

    repacked_archive = output / "empty-selected-inventory.zip"
    with zipfile.ZipFile(archive_path, "r") as original, zipfile.ZipFile(
        repacked_archive,
        "x",
        compression=zipfile.ZIP_DEFLATED,
    ) as repacked:
        for info in original.infolist():
            member_bytes = (
                empty_manifest_bytes
                if info.filename == "review/SUBMISSION_MANIFEST.json"
                else original.read(info.filename)
            )
            repacked.writestr(info, member_bytes, compress_type=info.compress_type)
    repacked_archive.replace(archive_path)

    empty_proof = json.loads(original_proof)
    repacked_bytes = archive_path.read_bytes()
    empty_proof["archive_sha256"] = hashlib.sha256(repacked_bytes).hexdigest()
    empty_proof["archive_size_bytes"] = len(repacked_bytes)
    empty_proof["selected_asset_count"] = 0
    empty_proof["selected_asset_inventory_sha256"] = empty_manifest[
        "asset_inventory_sha256"
    ]
    proof_path.write_text(
        json.dumps(empty_proof, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required review assets"):
        source_review.verify_source_review_package(root, output, source_sha)

    manifest_path.write_bytes(original_manifest)
    archive_path.write_bytes(original_archive)
    proof_path.write_bytes(original_proof)
    archive_path.write_bytes(archive_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="Archive SHA-256"):
        source_review.verify_source_review_package(root, output, source_sha)


def test_verifier_recomputes_submission_manifest_and_summary_from_source(tmp_path):
    root = _make_git_fixture(tmp_path)
    source_sha = _head(root)
    output = tmp_path / "source-review"
    _package(root, output, source_sha)

    manifest_path = output / source_review.MANIFEST_PATH
    summary_path = output / source_review.SUMMARY_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["gate_statuses_declared"]["G4"] = "PASS"
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=True).encode("utf-8")
    original_summary = summary_path.read_bytes()
    expected_gate = b"| G4 | NOT_PASSED |"
    assert original_summary.count(expected_gate) == 1
    summary_bytes = original_summary.replace(expected_gate, b"| G4 | PASS |", 1)

    _repack_package_and_refresh_proof(
        output,
        {
            "review/SUBMISSION_MANIFEST.json": manifest_bytes,
            "review/SUBMISSION_SUMMARY.md": summary_bytes,
        },
    )

    with pytest.raises(ValueError, match="submission manifest|submission summary"):
        source_review.verify_source_review_package(root, output, source_sha)


def test_archive_is_deterministic_when_tracked_file_mtimes_change(tmp_path):
    root = _make_git_fixture(tmp_path)
    source_sha = _head(root)
    readme = root / "README.md"
    os.utime(readme, (1609459200, 1609459200))
    first_output = tmp_path / "source-review-first"
    first = _package(root, first_output, source_sha)

    os.utime(readme, (1704067200, 1704067200))
    second_output = tmp_path / "source-review-second"
    second = _package(root, second_output, source_sha)

    first_archive = (first_output / first["archive"]).read_bytes()
    second_archive = (second_output / second["archive"]).read_bytes()
    assert first_archive == second_archive
    assert first["archive_sha256"] == second["archive_sha256"]


def test_verifier_rejects_oversized_member_before_crc_check(tmp_path, monkeypatch):
    root = _make_git_fixture(tmp_path)
    source_sha = _head(root)
    output = tmp_path / "source-review"
    _package(root, output, source_sha)
    archive_path = output / f"atlasops-source-{source_sha[:12]}.zip"
    with zipfile.ZipFile(archive_path, "r") as archive:
        oversized_readme = archive.read("README.md") + b"x"
    _repack_package_and_refresh_proof(output, {"README.md": oversized_readme})

    def reject_crc_before_size_check(_archive):
        raise AssertionError("CRC validation began before member-size validation")

    monkeypatch.setattr(zipfile.ZipFile, "testzip", reject_crc_before_size_check)
    with pytest.raises(ValueError, match="size"):
        source_review.verify_source_review_package(root, output, source_sha)


def test_verifier_keeps_scratch_outside_read_only_package_directory(tmp_path, monkeypatch):
    root = _make_git_fixture(tmp_path)
    source_sha = _head(root)
    output = tmp_path / "source-review"
    proof = _package(root, output, source_sha)
    before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }

    original_temporary_directory = source_review.tempfile.TemporaryDirectory
    scratch_paths: list[Path] = []

    def reject_package_writes(*, prefix=None, dir=None, **kwargs):
        if dir is not None and Path(dir).resolve().is_relative_to(output.resolve()):
            raise PermissionError("the package directory is read-only")
        temporary_directory = original_temporary_directory(
            prefix=prefix,
            dir=dir,
            **kwargs,
        )
        scratch_paths.append(Path(temporary_directory.name).resolve())
        return temporary_directory

    monkeypatch.setattr(
        source_review.tempfile,
        "TemporaryDirectory",
        reject_package_writes,
    )
    assert source_review.verify_source_review_package(root, output, source_sha) == proof
    assert scratch_paths
    assert all(not path.is_relative_to(output.resolve()) for path in scratch_paths)
    after = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize("scratch_location", ("package", "git-metadata"))
def test_verifier_rejects_protected_temp_roots_before_scratch_io(
    tmp_path,
    monkeypatch,
    scratch_location,
):
    root = _make_git_fixture(tmp_path)
    source_sha = _head(root)
    output = tmp_path / "source-review"
    _package(root, output, source_sha)
    git_metadata = source_review._git_metadata_directories(root)
    scratch_root = output if scratch_location == "package" else git_metadata[0]
    package_before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    metadata_before = {
        path.relative_to(metadata_dir).as_posix(): path.read_bytes()
        for metadata_dir in git_metadata
        for path in metadata_dir.rglob("*")
        if path.is_file()
    }

    scratch_paths: list[Path] = []
    default_tempdir_calls: list[bool] = []
    original_temporary_directory = source_review.tempfile.TemporaryDirectory
    original_get_default_tempdir = source_review.tempfile._get_default_tempdir

    def track_temporary_directory(*, prefix=None, dir=None, **kwargs):
        temporary_directory = original_temporary_directory(
            prefix=prefix,
            dir=dir,
            **kwargs,
        )
        scratch_paths.append(Path(temporary_directory.name).resolve())
        return temporary_directory

    def track_default_tempdir():
        default_tempdir_calls.append(True)
        return original_get_default_tempdir()

    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(scratch_root))
    monkeypatch.setattr(source_review.tempfile, "tempdir", None)
    monkeypatch.setattr(
        source_review.tempfile,
        "_get_default_tempdir",
        track_default_tempdir,
    )
    monkeypatch.setattr(
        source_review.tempfile,
        "TemporaryDirectory",
        track_temporary_directory,
    )

    verification_error = None
    try:
        source_review.verify_source_review_package(root, output, source_sha)
    except ValueError as error:
        verification_error = error

    package_after = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    metadata_after = {
        path.relative_to(metadata_dir).as_posix(): path.read_bytes()
        for metadata_dir in git_metadata
        for path in metadata_dir.rglob("*")
        if path.is_file()
    }
    assert package_after == package_before
    assert metadata_after == metadata_before
    assert not scratch_paths, (
        "verification created scratch directories in a protected location: "
        f"{scratch_paths!r}; default-temp probes: {len(default_tempdir_calls)}"
    )
    assert not default_tempdir_calls
    assert verification_error is not None
    assert "temporary" in str(verification_error).casefold()


def test_verifier_accepts_recorded_commit_after_origin_main_advances(tmp_path):
    root = _make_git_fixture(tmp_path)
    source_sha = _head(root)
    output = tmp_path / "source-review"
    proof = _package(root, output, source_sha)
    source_tree = _run_git(root, "rev-parse", f"{source_sha}^{{tree}}").decode(
        "ascii"
    ).strip()
    advanced_main = _run_git(
        root,
        "commit-tree",
        source_tree,
        "-p",
        source_sha,
        input_data=b"advance origin/main\n",
    ).decode("ascii").strip()
    _run_git(root, "update-ref", "refs/remotes/origin/main", advanced_main)

    assert _head(root) == source_sha
    assert advanced_main != source_sha
    assert source_review.verify_source_review_package(root, output, source_sha) == proof


def test_builder_rejects_malformed_and_wrong_expected_source_without_output(tmp_path):
    root = _make_git_fixture(tmp_path)

    with pytest.raises(ValueError, match="full 40- or 64-character"):
        _package(root, tmp_path / "malformed-output", "deadbeef")
    with pytest.raises(ValueError, match="HEAD does not match"):
        _package(root, tmp_path / "wrong-source-output", "0" * 40)

    assert not (tmp_path / "malformed-output").exists()
    assert not (tmp_path / "wrong-source-output").exists()


def test_builder_rejects_origin_main_that_does_not_match_expected_source(tmp_path):
    root = _make_git_fixture(tmp_path)
    previous = _head(root)
    _write_fixture_file(root, "new-commit.txt", b"new commit\n")
    _run_git(root, "add", "new-commit.txt")
    _run_git(root, "commit", "-m", "advance HEAD only")
    current = _head(root)

    with pytest.raises(ValueError, match="origin/main ref"):
        _package(root, tmp_path / "stale-origin-output", current)
    assert _run_git(root, "rev-parse", "origin/main").decode("ascii").strip() == previous
    assert not (tmp_path / "stale-origin-output").exists()


def test_builder_rejects_dirty_tracked_source_before_creating_output(tmp_path):
    root = _make_git_fixture(tmp_path)
    (root / "README.md").write_bytes(b"dirty tracked source\n")
    output = tmp_path / "dirty-output"

    with pytest.raises(ValueError, match="Tracked source and index must be clean"):
        _package(root, output)

    assert not output.exists()


def test_builder_preserves_existing_output_and_rejects_canonical_artifacts(tmp_path):
    root = _make_git_fixture(tmp_path)
    output = tmp_path / "existing-output"
    output.mkdir()
    sentinel = output / "prior-proof.json"
    sentinel.write_bytes(b"preserve this output")

    with pytest.raises(ValueError, match="new directory"):
        _package(root, output)
    assert sentinel.read_bytes() == b"preserve this output"

    with pytest.raises(ValueError, match="separate from the repository's artifacts"):
        _package(root, root / "artifacts" / "new-package")
    assert not (root / "artifacts" / "new-package").exists()


def test_output_resolver_rejects_linked_worktree_git_metadata_directories(tmp_path):
    root = _make_linked_git_fixture(tmp_path)
    metadata_directories = {
        "common": Path(
            _run_git(
                root,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ).decode("utf-8").strip()
        ),
        "worktree": Path(
            _run_git(
                root,
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
            ).decode("utf-8").strip()
        ),
    }
    assert metadata_directories["common"] != metadata_directories["worktree"]

    for kind, metadata_dir in metadata_directories.items():
        output = metadata_dir / f"source-review-{kind}-probe"
        with pytest.raises(ValueError, match="Git metadata"):
            source_review._resolve_new_output_path(root, output)
        assert not output.exists()


@pytest.mark.parametrize("linked", [False, True], ids=["conventional", "linked"])
def test_verifier_rejects_protected_locations_before_package_io(
    tmp_path,
    monkeypatch,
    linked,
):
    root = (
        _make_linked_git_fixture(tmp_path)
        if linked
        else _make_git_fixture(tmp_path)
    )
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    locations = [
        ("repository root", root, "canonical artifacts"),
        ("artifacts", artifacts, "canonical artifacts"),
    ]
    for name, option in (
        ("Git common directory", "--git-common-dir"),
        ("worktree Git directory", "--git-dir"),
    ):
        metadata_dir = Path(
            _run_git(
                root,
                "rev-parse",
                "--path-format=absolute",
                option,
            ).decode("utf-8").strip()
        )
        locations.append((name, metadata_dir, "Git metadata"))
    locations.append(("repository ancestor", root.parent, "ancestor"))

    package_io: list[str] = []

    def reject_package_io(operation):
        def reject(*args, **kwargs):
            package_io.append(operation)
            raise AssertionError(f"{operation} ran before location validation")

        return reject

    for operation in (
        "_package_file",
        "_read_json",
        "_verify_package_artifacts",
    ):
        monkeypatch.setattr(
            source_review,
            operation,
            reject_package_io(operation),
        )
    monkeypatch.setattr(
        source_review.tempfile,
        "TemporaryDirectory",
        reject_package_io("TemporaryDirectory"),
    )

    for location, package_dir, expected_error in locations:
        with pytest.raises(ValueError, match=expected_error):
            source_review.verify_source_review_package(
                root,
                package_dir,
                _head(root),
            )
        assert not package_io, f"Package I/O started for {location}"


@pytest.mark.parametrize(
    ("extra_record", "message"),
    [
        (b"100644 blob " + b"0" * 40 + b"\t../outside.txt\0", "Unsafe archive member"),
        (b"100644 blob " + b"0" * 40 + b"\tREADME.MD\0", "Case-folding"),
        (b"120000 blob " + b"0" * 40 + b"\tlink-outside\0", "Symbolic links"),
    ],
)
def test_builder_rejects_unsafe_git_tree_paths_before_output_or_content_reads(
    tmp_path,
    monkeypatch,
    extra_record,
    message,
):
    root = _make_git_fixture(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"must not be read")
    output = tmp_path / "unsafe-output"
    original_git = source_review._git
    builder_called = False

    def malformed_tree_git(repo_root, *arguments, input_data=None):
        result = original_git(repo_root, *arguments, input_data=input_data)
        if arguments[:1] == ("ls-tree",):
            return result + extra_record
        return result

    def unexpected_inventory(*args, **kwargs):
        nonlocal builder_called
        builder_called = True
        raise AssertionError("selected inventory must not run before tree validation")

    monkeypatch.setattr(source_review, "_git", malformed_tree_git)
    monkeypatch.setattr(source_review, "build_submission_package", unexpected_inventory)

    with pytest.raises(ValueError, match=message):
        _package(root, output)

    assert not builder_called
    assert not output.exists()
    assert outside.read_bytes() == b"must not be read"


def test_builder_rejects_reparse_source_component_before_reading_it(tmp_path, monkeypatch):
    root = _make_git_fixture(tmp_path)
    target = root / "reparse-review.txt"
    original_lstat = Path.lstat
    read_attempts: list[Path] = []
    original_read_bytes = Path.read_bytes

    def reparse_lstat(path: Path):
        if path == target:
            return SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644,
                st_file_attributes=0x400,
            )
        return original_lstat(path)

    def guarded_read_bytes(path: Path) -> bytes:
        if path == target:
            read_attempts.append(path)
            raise AssertionError("reparse target content must not be read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    with pytest.raises(ValueError, match="symbolic link or reparse point"):
        _package(root, tmp_path / "reparse-output")

    assert read_attempts == []
    assert not (tmp_path / "reparse-output").exists()


def test_builder_rejects_broken_review_links_before_creating_output(tmp_path):
    root = _make_git_fixture(
        tmp_path,
        readme=b"[Missing target](not-tracked.txt)\n",
    )
    output = tmp_path / "partial-package"

    with pytest.raises(ValueError, match="Current-facing source links are invalid"):
        _package(root, output)

    assert not output.exists()


def test_builder_retains_partial_output_when_inventory_build_fails(tmp_path, monkeypatch):
    root = _make_git_fixture(tmp_path)
    output = tmp_path / "partial-package"

    def fail_after_sidecar_directory(*, output_dir):
        output_dir.mkdir()
        (output_dir / "SUBMISSION_MANIFEST.json").write_text(
            '{"status":"partial"}\n',
            encoding="utf-8",
        )
        raise ValueError("simulated selected-inventory failure")

    monkeypatch.setattr(source_review, "build_submission_package", fail_after_sidecar_directory)
    with pytest.raises(ValueError, match="simulated selected-inventory failure"):
        _package(root, output)

    assert (output / "inventory" / "SUBMISSION_MANIFEST.json").is_file()
    assert (output / "inventory" / "SUBMISSION_MANIFEST.json").read_text(
        encoding="utf-8"
    ) == '{"status":"partial"}\n'
    assert not (output / "proof.json").exists()
