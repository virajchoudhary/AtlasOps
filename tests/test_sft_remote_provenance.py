"""No-training tests for read-only SFT remote provenance collection."""

from __future__ import annotations

import builtins
import hashlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import collect_sft_remote_provenance as collector


def _runtime_test_environment(monkeypatch):
    plan, _plan_sha256 = collector._load_plan()
    packages = plan["environment"]["package_versions"]
    monkeypatch.setattr(collector.platform, "system", lambda: "Linux")
    monkeypatch.setattr(collector.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(collector.platform, "python_version", lambda: "3.12.11")
    monkeypatch.setattr(collector.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(collector.platform, "libc_ver", lambda: ("glibc", "2.36"))
    monkeypatch.setattr(collector.socket, "gethostname", lambda: "approved-host-fixture")

    def which(name):
        return f"/usr/bin/{name}"

    def fake_run(command, **_kwargs):
        if command[-1] == "--version":
            stdout = "git version 2.43.0\n"
        elif command[-1] == "HEAD":
            stdout = "a" * 40 + "\n"
        elif command[0].endswith("nvidia-smi"):
            stdout = "NVIDIA A100, GPU-fixture, 550.54.15, 81920 MiB, 8.0\n"
        else:
            stdout = ""
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(collector.shutil, "which", which)
    monkeypatch.setattr(
        collector.subprocess,
        "run",
        fake_run,
    )
    monkeypatch.setattr(
        collector.importlib.metadata,
        "version",
        lambda name: packages[name],
    )


def _set_readonly(path: Path) -> None:
    current = path.stat().st_mode
    if stat.S_ISDIR(current):
        path.chmod(current & ~0o222)
    else:
        path.chmod(current & ~0o222)


def _fake_snapshot(
    tmp_path: Path,
) -> tuple[Path, dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    revision = collector.MODEL_REVISION
    snapshot = (
        tmp_path
        / collector.MODEL_CACHE_DIRECTORY
        / "snapshots"
        / revision
    )
    snapshot.mkdir(parents=True)
    tokenizer_files: dict[str, dict[str, object]] = {}
    for name in sorted(collector.TOKENIZER_FILES):
        raw = f"unit-test tokenizer fixture: {name}".encode("utf-8")
        path = snapshot / name
        path.write_bytes(raw)
        tokenizer_files[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
    index_map: dict[str, str] = {}
    fake_shards: dict[str, dict[str, object]] = {}
    for index, name in enumerate(sorted(collector.PINNED_WEIGHT_SHARDS), 1):
        shard_name = f"model-0000{index}-of-00004.safetensors"
        raw = f"unit-test fake weights shard {index}".encode("utf-8")
        path = snapshot / shard_name
        path.write_bytes(raw)
        fake_shards[name] = {
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        index_map[f"fixture.tensor_{index}"] = shard_name
    index = {
        "metadata": {
            "total_size": sum(item["size_bytes"] for item in fake_shards.values())
        },
        "weight_map": index_map,
    }
    index_path = snapshot / "model.safetensors.index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")
    for path in snapshot.rglob("*"):
        _set_readonly(path)
    _set_readonly(snapshot)
    return snapshot, fake_shards, tokenizer_files


def _fake_weight_metadata(shards: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "atlasops-public-model-metadata-v1",
        "repository": collector.MODEL_REPOSITORY,
        "revision": collector.MODEL_REVISION,
        "source": (
            f"https://huggingface.co/api/models/{collector.MODEL_REPOSITORY}/revision/"
            f"{collector.MODEL_REVISION}?blobs=true"
        ),
        "retrieved_date": "unit-test",
        "weights_downloaded": False,
        "total_weight_bytes": collector.PINNED_WEIGHT_TOTAL_BYTES,
        "shards": shards,
    }


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def test_runtime_records_complete_plan_packages_without_torch_import(monkeypatch):
    _runtime_test_environment(monkeypatch)
    original_import = builtins.__import__

    def reject_torch(name, *args, **kwargs):
        if name.split(".", 1)[0] == "torch":
            raise AssertionError("default host collection must not import PyTorch")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_torch)
    plan, plan_sha256 = collector._load_plan()
    record = collector.collect_runtime_provenance(image_digest="sha256:" + "b" * 64)

    assert record["schema_version"] == collector.RUNTIME_SCHEMA
    assert record["plan_sha256"] == plan_sha256
    assert record["hostname"] == "approved-host-fixture"
    assert record["image_digest_source"] == "operator_supplied_unverified"
    assert record["package_versions"] == plan["environment"]["package_versions"]
    assert len(record["package_versions"]) == 72
    assert record["lock_sha256"] == plan["required_files"][collector.LOCK_RELATIVE_PATH]
    assert record["gpu"]["status"] == "NOT_INSPECTED"
    assert record["execution_allowed"] is False


def test_runtime_rejects_wrong_platform_or_incomplete_package_map(monkeypatch):
    _runtime_test_environment(monkeypatch)
    monkeypatch.setattr(collector.platform, "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="Linux x86_64"):
        collector.collect_runtime_provenance(image_digest="sha256:" + "b" * 64)

    _runtime_test_environment(monkeypatch)
    monkeypatch.setattr(
        collector.importlib.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(collector.importlib.metadata.PackageNotFoundError()),
    )
    with pytest.raises(ValueError, match="package is missing"):
        collector.collect_runtime_provenance(image_digest="sha256:" + "b" * 64)


def test_runtime_gpu_probe_is_explicit_and_reports_only_capability_metadata(monkeypatch):
    _runtime_test_environment(monkeypatch)
    properties = SimpleNamespace(total_memory=80 * 1024**3, major=8, minor=0)
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            is_bf16_supported=lambda: True,
            get_device_name=lambda _index: "fixture-A100",
            get_device_properties=lambda _index: properties,
        ),
        version=SimpleNamespace(cuda="12.6"),
    )
    monkeypatch.setitem(__import__("sys").modules, "torch", fake_torch)
    record = collector.collect_runtime_provenance(
        image_digest="sha256:" + "b" * 64,
        inspect_gpu=True,
    )

    assert record["gpu"]["status"] == "CAPABILITIES_PRESENT"
    assert record["gpu"]["device_count"] == 1
    assert record["gpu"]["single_gpu"] is True
    assert record["gpu"]["bf16_supported"] is True
    assert record["gpu"]["cuda_runtime"] == "12.6"
    assert record["gpu"]["devices"][0]["memory_bytes"] == 80 * 1024**3
    assert record["gpu"]["memory_fit_verified"] is False
    assert record["gpu"]["driver"]["status"] == "OBSERVED"
    assert record["gpu"]["driver"]["devices"][0]["driver_version"] == "550.54.15"
    assert record["execution_allowed"] is False


def test_runtime_fails_closed_when_git_is_unavailable(monkeypatch):
    _runtime_test_environment(monkeypatch)
    monkeypatch.setattr(collector.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="Git is unavailable"):
        collector.collect_runtime_provenance(image_digest="sha256:" + "b" * 64)


def test_model_inventory_is_exact_and_does_not_load_fixture_weights(tmp_path, monkeypatch):
    snapshot, fake_shards, tokenizer_files = _fake_snapshot(tmp_path)
    fake_total = sum(item["size_bytes"] for item in fake_shards.values())
    monkeypatch.setattr(collector, "PINNED_WEIGHT_SHARDS", fake_shards)
    monkeypatch.setattr(collector, "PINNED_WEIGHT_TOTAL_BYTES", fake_total)

    metadata_path = _write_json(
        tmp_path / "public-weight-metadata.json",
        _fake_weight_metadata(fake_shards),
    )
    manifest_path = _write_json(
        tmp_path / "tokenizer-files.json",
        {
            "schema_version": "atlasops-tokenizer-files-v1",
            "repository": collector.MODEL_REPOSITORY,
            "revision": collector.MODEL_REVISION,
            "model_weights_downloaded": False,
            "files": tokenizer_files,
        },
    )
    plan, _plan_sha256 = collector._load_plan()
    plan["required_files"] = dict(plan["required_files"])
    plan["required_files"][
        "artifacts/evidence/stage7/tokenizer_files_a09a354_v1.json"
    ] = collector._canonical_sha256(manifest_path.read_bytes())
    monkeypatch.setattr(collector, "_load_plan", lambda: (plan, _plan_sha256))

    record = collector.collect_model_inventory(
        snapshot_dir=snapshot,
        metadata_path=metadata_path,
        tokenizer_manifest_path=manifest_path,
    )

    assert record["schema_version"] == collector.MODEL_INVENTORY_SCHEMA
    assert record["repository"] == collector.MODEL_REPOSITORY
    assert record["revision"] == collector.MODEL_REVISION
    assert set(record["files"]) == {
        str(path.absolute()) for path in snapshot.iterdir()
    }
    assert record["total_files"] == len(record["files"])
    assert record["total_bytes"] == sum(item["size_bytes"] for item in record["file_details"])
    assert record["model_weights_loaded"] is False
    assert record["network_accessed"] is False
    assert record["execution_allowed"] is False
    assert plan["execution_allowed"] is False
    assert record["snapshot_permissions"] == {
        "mode_bits_readonly": True,
        "owner_acl_verified": False,
        "mount_immutability_verified": False,
    }


def test_model_inventory_rejects_redirected_or_writable_snapshot(tmp_path, monkeypatch):
    snapshot, _fake_shards, _tokenizer_files = _fake_snapshot(tmp_path)
    assert collector._has_write_bits(stat.S_IFREG | 0o644)
    assert not collector._has_write_bits(stat.S_IFREG | 0o444)
    writable = snapshot / "tokenizer.json"
    monkeypatch.setattr(collector, "_has_write_bits", lambda _mode: True)
    with pytest.raises(ValueError, match="writable"):
        collector._readonly_regular_file(writable)

    monkeypatch.undo()
    linked = snapshot / "redirected.json"
    snapshot.chmod(snapshot.stat().st_mode | stat.S_IWUSR)
    try:
        linked.symlink_to(snapshot / "tokenizer.json")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable in this test environment")
    snapshot.chmod(snapshot.stat().st_mode & ~stat.S_IWUSR)
    with pytest.raises(ValueError, match="redirect"):
        collector._snapshot_entries(snapshot)


def test_pinned_shards_match_stored_public_weight_metadata():
    metadata, raw = collector._read_json(
        collector.WEIGHT_METADATA_PATH,
        collector.MAX_METADATA_BYTES,
    )
    assert metadata["schema_version"] == "atlasops-public-model-metadata-v1"
    assert metadata["repository"] == collector.MODEL_REPOSITORY
    assert metadata["revision"] == collector.MODEL_REVISION
    assert metadata["shards"] == collector.PINNED_WEIGHT_SHARDS
    assert sum(shard["size_bytes"] for shard in metadata["shards"].values()) == (
        metadata["total_weight_bytes"]
    )
    assert metadata["weights_downloaded"] is False
    assert raw


def test_new_output_is_no_clobber_and_never_authorizes_execution(tmp_path):
    output = tmp_path / "runtime.json"
    record = {"execution_allowed": False, "value": "first"}
    assert collector.write_json_new(output, record) == output
    original = output.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        collector.write_json_new(output, {"execution_allowed": False})
    assert output.read_bytes() == original
    with pytest.raises(ValueError, match="cannot authorize"):
        collector.write_json_new(tmp_path / "unauthorized.json", {"execution_allowed": True})
    assert not (tmp_path / "unauthorized.json").exists()


def test_inventory_rejects_wrong_snapshot_revision_and_bad_digest(tmp_path):
    wrong = tmp_path / collector.MODEL_CACHE_DIRECTORY / "snapshots" / ("f" * 40)
    wrong.mkdir(parents=True)
    with pytest.raises(ValueError, match="pinned immutable cache revision"):
        collector._validated_snapshot_path(wrong)
    with pytest.raises(ValueError, match="full sha256 digest"):
        collector.collect_runtime_provenance(image_digest="not-a-digest")
