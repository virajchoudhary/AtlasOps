"""Focused no-model tests for the separately frozen free-Colab-T4 SFT gate."""

from __future__ import annotations

import builtins
import copy
import hashlib
import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from training import sft, sft_pilot_gate
from training import sft_free_t4_gate as gate
from training.sft_provenance import REPO_ROOT, snapshot_training_corpus

CORPUS = (
    REPO_ROOT
    / "artifacts/evidence/stage7/candidates/train-candidate-v1/sft_corpus_train.jsonl"
)


def _free_hyperparameters() -> dict:
    return sft._hyperparameters(
        SimpleNamespace(
            epochs=1,
            lr=2e-4,
            batch_size=1,
            grad_accum=8,
            max_seq_len=8192,
            seed=2026,
            pilot_profile="free-t4-v1",
        )
    )


def _preparation_arguments(hyperparameters: dict | None = None) -> dict:
    return {
        "model": gate.BASE_MODEL,
        "model_revision": gate.REVISION,
        "tokenizer": gate.BASE_MODEL,
        "tokenizer_revision": gate.REVISION,
        "role": "all",
        "hyperparameters": hyperparameters or _free_hyperparameters(),
    }


def _admission() -> dict:
    return {
        "schema_version": gate.ADMISSION_SCHEMA,
        "profile": gate.PROFILE,
        "preparation_admissible": True,
        "execution_allowed": False,
        "d3_status": "APPROVED_FOR_PREPARATION",
        "plan_sha256": gate.PLAN_SHA256,
        "free_t4_plan_sha256": gate.PLAN_SHA256,
        "parent_plan_sha256": gate.PARENT_PLAN_SHA256,
        "corpus_sha256": gate.CORPUS_HASH,
        "corpus_manifest_sha256": gate.MANIFEST_HASH,
        "model": gate.BASE_MODEL,
        "model_revision": gate.REVISION,
        "tokenizer": gate.BASE_MODEL,
        "tokenizer_revision": gate.REVISION,
        "row_count": 68,
        "corpus_version": "train-candidate-v1",
        "hyperparameters": _free_hyperparameters(),
        "parent_preparation": {
            "schema_version": "atlasops-sft-pilot-admission-v1",
            "d3_status": "APPROVED_FOR_PREPARATION",
            "preparation_admissible": True,
            "execution_allowed": False,
            "model": gate.BASE_MODEL,
            "model_revision": gate.REVISION,
            "tokenizer_revision": gate.REVISION,
            "corpus_sha256": gate.CORPUS_HASH,
            "corpus_manifest_sha256": gate.MANIFEST_HASH,
            "plan_sha256": gate.PARENT_PLAN_SHA256,
        },
    }


def test_free_t4_preparation_delegates_to_original_gate_without_model_import(
    monkeypatch,
):
    original = builtins.__import__
    blocked = {"datasets", "transformers", "torch", "trl", "peft"}

    def guarded_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in blocked:
            raise AssertionError("preparation imported a model/trainer dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = gate.validate_preparation(
        snapshot_training_corpus(CORPUS),
        **_preparation_arguments(),
    )

    assert result["profile"] == "free-t4-v1"
    assert result["preparation_admissible"] is True
    assert result["execution_allowed"] is False
    assert result["plan_sha256"] == gate.PLAN_SHA256
    assert result["parent_plan_sha256"] == gate.PARENT_PLAN_SHA256
    assert result["parent_preparation"]["plan_sha256"] == gate.PARENT_PLAN_SHA256
    assert result["row_count"] == 68


def test_free_t4_preparation_uses_baseline_parameters_only_for_original_gate(
    monkeypatch,
):
    captured = {}

    def parent_validator(_snapshot, **kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "atlasops-sft-pilot-admission-v1",
            "d3_status": "APPROVED_FOR_PREPARATION",
            "preparation_admissible": True,
            "execution_allowed": False,
            "model": gate.BASE_MODEL,
            "model_revision": gate.REVISION,
            "tokenizer_revision": gate.REVISION,
            "corpus_sha256": gate.CORPUS_HASH,
            "corpus_manifest_sha256": gate.MANIFEST_HASH,
            "plan_sha256": gate.PARENT_PLAN_SHA256,
            "remaining_approvals": [],
        }

    monkeypatch.setattr(sft_pilot_gate, "validate_preparation", parent_validator)
    free_parameters = _free_hyperparameters()
    result = gate.validate_preparation(
        snapshot_training_corpus(CORPUS),
        **_preparation_arguments(free_parameters),
    )

    baseline = captured["hyperparameters"]
    assert baseline["batch_size"] == 2
    assert baseline["gradient_accumulation_steps"] == 4
    assert baseline["quantization"]["bnb_4bit_compute_dtype"] == "bfloat16"
    assert baseline["bf16"] is True
    assert "fp16" not in baseline
    assert free_parameters["batch_size"] == 1
    assert free_parameters["gradient_accumulation_steps"] == 8
    assert free_parameters["quantization"]["bnb_4bit_compute_dtype"] == "float16"
    assert result["parent_preparation"]["execution_allowed"] is False


def test_free_t4_preparation_rejects_hyperparameter_drift_before_parent_gate(
    monkeypatch,
):
    monkeypatch.setattr(
        sft_pilot_gate,
        "validate_preparation",
        lambda *_args, **_kwargs: pytest.fail("parent gate ran for altered profile"),
    )
    hyperparameters = _free_hyperparameters()
    hyperparameters["batch_size"] = 2

    with pytest.raises(ValueError, match="free-T4"):
        gate.validate_preparation(
            snapshot_training_corpus(CORPUS),
            **_preparation_arguments(hyperparameters),
        )


def test_free_t4_preparation_rejects_config_hash_drift_before_parent_gate(
    monkeypatch, tmp_path,
):
    tampered = tmp_path / "tampered-free-t4.json"
    tampered.write_text('{"schema_version":"changed"}\n', encoding="utf-8")
    monkeypatch.setattr(gate, "PLAN_PATH", tampered)
    monkeypatch.setattr(
        sft_pilot_gate,
        "validate_preparation",
        lambda *_args, **_kwargs: pytest.fail("parent gate ran for altered config"),
    )

    with pytest.raises(ValueError, match="Free-T4.*hash"):
        gate.validate_preparation(
            snapshot_training_corpus(CORPUS),
            **_preparation_arguments(),
        )


def test_execution_authority_fails_closed_without_external_approval_digest(
    monkeypatch,
):
    monkeypatch.setattr(
        gate,
        "_read",
        lambda _path: pytest.fail("authority read occurred before external digest"),
    )
    with pytest.raises(ValueError, match="Training refused"):
        gate.require_execution_authority(_admission())


def _execution_fixture(monkeypatch, tmp_path):
    from scripts import collect_sft_remote_provenance as collector
    from scripts.transfer_t4_model_files import FILES as pinned_model_files
    from training import sft_provenance

    base_plan = json.loads(sft_pilot_gate.PLAN_PATH.read_text(encoding="utf-8"))
    package_versions = base_plan["environment"]["package_versions"]
    assert len(package_versions) == 72
    monkeypatch.setattr(
        gate.importlib.metadata,
        "version",
        lambda name: package_versions[name],
    )
    monkeypatch.setattr(sft_provenance, "source_provenance", lambda: {
        "git_sha": "a" * 40,
        "git_dirty": False,
    })

    platform_details = {
        "system": "Linux",
        "machine": "x86_64",
        "release": "test-kernel",
        "platform": "Linux-test-kernel-x86_64",
    }
    monkeypatch.setattr(gate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gate.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(gate.platform, "release", lambda: "test-kernel")
    monkeypatch.setattr(gate.platform, "platform", lambda: "Linux-test-kernel-x86_64")
    monkeypatch.setattr(gate.platform, "python_version", lambda: "3.12.11")
    monkeypatch.setattr(gate.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(gate.socket, "gethostname", lambda: "colab-test-host")
    monkeypatch.setattr(gate.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(gate.sys, "base_prefix", str(tmp_path / "python"))
    monkeypatch.setattr(
        gate.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=21 * 1024**3),
    )

    torch = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True,
            device_count=lambda: 1,
            get_device_name=lambda _index: "Tesla T4",
            get_device_capability=lambda _index: (7, 5),
            get_device_properties=lambda _index: SimpleNamespace(
                total_memory=15637086208
            ),
        ),
        version=SimpleNamespace(cuda="12.6"),
    )
    monkeypatch.setitem(sys.modules, "torch", torch)

    snapshot = (
        tmp_path
        / "cache"
        / "models--Qwen--Qwen2.5-7B-Instruct"
        / "snapshots"
        / gate.REVISION
    )
    snapshot.mkdir(parents=True)
    details = []
    files = {}
    for name, (size_bytes, digest) in pinned_model_files.items():
        path = snapshot / name
        path.write_bytes(b"fixture only; never a model weight")
        files[str(path.absolute())] = digest
        details.append({
            "path": str(path.absolute()),
            "size_bytes": size_bytes,
            "sha256": digest,
        })

    weight_metadata_path = collector.WEIGHT_METADATA_PATH
    tokenizer_manifest_path = collector.TOKENIZER_MANIFEST_PATH
    collected = {
        "schema_version": collector.MODEL_INVENTORY_SCHEMA,
        "collected_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "plan_sha256": gate.PARENT_PLAN_SHA256,
        "repository": gate.BASE_MODEL,
        "revision": gate.REVISION,
        "snapshot_dir": str(snapshot.absolute()),
        "weight_metadata": {
            "path": str(weight_metadata_path.absolute()),
            "sha256": hashlib.sha256(weight_metadata_path.read_bytes()).hexdigest(),
            "source": "pinned_public_revision_metadata",
        },
        "tokenizer_manifest_sha256": hashlib.sha256(
            tokenizer_manifest_path.read_bytes()
        ).hexdigest(),
        "files": dict(sorted(files.items())),
        "file_details": details,
        "total_files": len(details),
        "total_bytes": sum(item["size_bytes"] for item in details),
        "snapshot_permissions": {
            "mode_bits_readonly": True,
            "owner_acl_verified": False,
            "mount_immutability_verified": False,
        },
        "model_weights_loaded": False,
        "network_accessed": False,
        "execution_allowed": False,
    }
    monkeypatch.setattr(
        collector,
        "collect_model_inventory",
        lambda *, snapshot_dir: copy.deepcopy(collected),
    )

    storage = {
        "free_bytes": 21 * 1024**3,
        "used_bytes": 100 * 1024**3,
        "total_bytes": 121 * 1024**3,
    }
    gpu = {
        "gpu_name": "Tesla T4",
        "gpu_count": 1,
        "compute_capability": [7, 5],
        "memory_bytes": 15637086208,
        "cuda_runtime": "12.6",
    }
    runtime = {
        "schema_version": gate.RUNTIME_SCHEMA,
        "runtime_kind": "colab_isolated_venv",
        "hostname": "colab-test-host",
        "package_versions": package_versions,
        "lock_sha256": gate.LOCK_SHA256,
        **gpu,
        "storage": storage,
    }
    runtime_path = tmp_path / "runtime.json"
    runtime_raw = (json.dumps(runtime, sort_keys=True, indent=2) + "\n").encode()
    runtime_path.write_bytes(runtime_raw)
    host = {key: runtime[key] for key in gate._RUNTIME_IDENTITY_FIELDS}
    host.update({
        "python_version": "3.12.11",
        "python_implementation": "CPython",
        "platform": platform_details,
    })
    host["runtime_manifest"] = str(runtime_path.absolute())
    host["runtime_manifest_sha256"] = hashlib.sha256(runtime_raw).hexdigest()

    output = tmp_path / "run-output"
    inventory_path = tmp_path / "model-inventory.json"
    inventory_raw = (json.dumps(collected, sort_keys=True, indent=2) + "\n").encode()
    inventory_path.write_bytes(inventory_raw)
    approval = {
        "schema_version": gate.EXECUTION_APPROVAL_SCHEMA,
        "profile": gate.PROFILE,
        "execution_allowed": True,
        "plan_sha256": gate.PLAN_SHA256,
        "free_t4_plan_sha256": gate.PLAN_SHA256,
        "parent_plan_sha256": gate.PARENT_PLAN_SHA256,
        "corpus_sha256": gate.CORPUS_HASH,
        "corpus_manifest_sha256": gate.MANIFEST_HASH,
        "row_count": 68,
        "model": gate.BASE_MODEL,
        "model_revision": gate.REVISION,
        "tokenizer": gate.BASE_MODEL,
        "tokenizer_revision": gate.REVISION,
        "role": "all",
        "hyperparameters": _free_hyperparameters(),
        "zero_cost_verified": True,
        "spend_usd_max": 0,
        "approved_by": "test operator",
        "rationale": "Bounded one-epoch free-Colab-T4 SFT pilot only.",
        "approved_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "approval_date": datetime.now(UTC).date().isoformat(),
        "run_id": "sft-pilot-free-t4-test",
        "source_git_sha": "a" * 40,
        "output_dir": str(output.absolute()),
        "package_versions": package_versions,
        "host": host,
        "model_files_manifest": str(inventory_path.absolute()),
        "model_files_manifest_sha256": hashlib.sha256(inventory_raw).hexdigest(),
        "evidence_retention": {
            "colab_runtime_storage_persistent": False,
            "method": "download_to_local_evidence_store",
            "user_evidence_store": r"C:\AtlasOps\artifacts\evidence\stage7\free_t4",
            "google_drive_all_files_mount": False,
        },
        "scope": {
            "paid_compute_authorized": False,
            "p1_remediation_authorized": False,
            "grpo_authorized": False,
            "final_test_accessed": False,
        },
    }
    approval_path = tmp_path / "approval.json"
    approval_raw = (json.dumps(approval, sort_keys=True, indent=2) + "\n").encode()
    approval_path.write_bytes(approval_raw)
    return SimpleNamespace(
        approval=approval,
        approval_path=approval_path,
        approval_sha256=hashlib.sha256(approval_raw).hexdigest(),
        output=output,
        runtime=runtime,
        runtime_path=runtime_path,
        collected=collected,
        inventory_path=inventory_path,
        snapshot=snapshot,
        pinned_model_files=pinned_model_files,
    )


def test_execution_authority_accepts_only_a_bound_free_t4_runtime(
    monkeypatch, tmp_path,
):
    case = _execution_fixture(monkeypatch, tmp_path)

    result = gate.require_execution_authority(
        _admission(),
        case.approval_path,
        output_dir=case.output,
        approval_sha256=case.approval_sha256,
    )

    assert result["run_id"] == "sft-pilot-free-t4-test"
    assert result["model_cache_dir"] == str(case.snapshot.parents[2])
    assert not case.output.exists()


def test_execution_authority_rejects_unpinned_or_nonzero_cost_record(
    monkeypatch, tmp_path,
):
    case = _execution_fixture(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="approval.*digest|hash-bound"):
        gate.require_execution_authority(
            _admission(),
            case.approval_path,
            output_dir=case.output,
            approval_sha256="f" * 64,
        )

    case.approval["spend_usd_max"] = 0.01
    raw = (json.dumps(case.approval, sort_keys=True, indent=2) + "\n").encode()
    case.approval_path.write_bytes(raw)
    with pytest.raises(ValueError, match="cost"):
        gate.require_execution_authority(
            _admission(),
            case.approval_path,
            output_dir=case.output,
            approval_sha256=hashlib.sha256(raw).hexdigest(),
        )


def test_execution_authority_rejects_t4_or_inventory_drift(monkeypatch, tmp_path):
    case = _execution_fixture(monkeypatch, tmp_path)
    altered_runtime = dict(case.runtime)
    altered_runtime["gpu_name"] = "Tesla A100"
    runtime_raw = (json.dumps(altered_runtime, sort_keys=True, indent=2) + "\n").encode()
    case.runtime_path.write_bytes(runtime_raw)
    case.approval["host"]["runtime_manifest_sha256"] = hashlib.sha256(
        runtime_raw
    ).hexdigest()
    case.approval["host"]["gpu_name"] = altered_runtime["gpu_name"]
    raw = (json.dumps(case.approval, sort_keys=True, indent=2) + "\n").encode()
    case.approval_path.write_bytes(raw)

    with pytest.raises(ValueError, match="Tesla T4"):
        gate.require_execution_authority(
            _admission(),
            case.approval_path,
            output_dir=case.output,
            approval_sha256=hashlib.sha256(raw).hexdigest(),
        )

    case = _execution_fixture(monkeypatch, tmp_path / "second")
    case.collected["files"][str(case.snapshot / "unapproved.bin")] = "b" * 64
    inventory_raw = (json.dumps(case.collected, sort_keys=True, indent=2) + "\n").encode()
    case.inventory_path.write_bytes(inventory_raw)
    case.approval["model_files_manifest_sha256"] = hashlib.sha256(
        inventory_raw
    ).hexdigest()
    raw = (json.dumps(case.approval, sort_keys=True, indent=2) + "\n").encode()
    case.approval_path.write_bytes(raw)
    with pytest.raises(ValueError, match="inventory|snapshot|extraneous"):
        gate.require_execution_authority(
            _admission(),
            case.approval_path,
            output_dir=case.output,
            approval_sha256=hashlib.sha256(raw).hexdigest(),
        )
