"""No-model tests of preparation admission versus execution authority."""

import builtins
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from training import sft, sft_pilot_gate
from training.sft_provenance import REPO_ROOT, snapshot_training_corpus

CORPUS = REPO_ROOT / "artifacts/evidence/stage7/candidates/train-candidate-v1/sft_corpus_train.jsonl"


def _args(monkeypatch, output, *, extra=()):
    monkeypatch.setattr(sys, "argv", [
        "sft.py", "--model", sft_pilot_gate.BASE_MODEL,
        "--model-revision", sft_pilot_gate.REVISION,
        "--data", str(CORPUS), "--output", str(output),
        "--max-seq-len", "8192", *extra,
    ])


def test_execution_refusal_cannot_be_opened_by_mutable_input():
    with pytest.raises(ValueError, match="Training refused"):
        sft_pilot_gate.require_execution_authority({"execution_allowed": True})


def test_preparation_cli_has_no_model_import_or_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        sft_pilot_gate, "validate_preparation",
        lambda *_a, **_k: {"preparation_admissible": True, "execution_allowed": False},
    )
    original = builtins.__import__
    blocked = {"datasets", "transformers", "torch", "trl", "peft"}

    def guard(name, *args, **kwargs):
        if name.split(".", 1)[0] in blocked:
            raise AssertionError("preparation attempted model/trainer import")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)
    output = tmp_path / "never-created"
    _args(monkeypatch, output, extra=("--preflight-only",))
    sft.main()
    assert json.loads(capsys.readouterr().out)["execution_allowed"] is False
    assert not output.exists()


def test_cli_execution_stops_before_output_and_load(monkeypatch, tmp_path):
    monkeypatch.setattr(sft_pilot_gate, "validate_preparation", lambda *_a, **_k: {})
    output = tmp_path / "never-created"
    _args(monkeypatch, output)
    with pytest.raises(ValueError, match="Training refused"):
        sft.main()
    assert not output.exists()


def test_exact_candidate_requires_exact_model_and_role():
    snapshot = snapshot_training_corpus(CORPUS)
    common = {
        "model": sft_pilot_gate.BASE_MODEL,
        "model_revision": sft_pilot_gate.REVISION,
        "tokenizer": sft_pilot_gate.BASE_MODEL,
        "tokenizer_revision": sft_pilot_gate.REVISION,
        "role": "all",
        "hyperparameters": {},
    }
    for field, invalid in [
        ("model_revision", "a" * 40), ("tokenizer_revision", "main"),
        ("model", "another/model"), ("role", "triage"),
    ]:
        altered = {**common, field: invalid}
        with pytest.raises(ValueError):
            sft_pilot_gate.validate_preparation(snapshot, **altered)


def test_frozen_plan_and_preflight_integrity():
    assert sft_pilot_gate.PLAN_PATH.is_file()
    plan = json.loads(sft_pilot_gate.PLAN_PATH.read_text())
    from types import SimpleNamespace

    settings = plan["hyperparameters"]
    actual = sft._hyperparameters(SimpleNamespace(
        epochs=settings["epochs"], lr=settings["learning_rate"],
        batch_size=settings["batch_size"], grad_accum=settings["gradient_accumulation_steps"],
        max_seq_len=settings["max_sequence_length"], seed=settings["seed"],
    ))
    result = sft_pilot_gate.validate_preparation(
        snapshot_training_corpus(CORPUS),
        model=sft_pilot_gate.BASE_MODEL, model_revision=sft_pilot_gate.REVISION,
        tokenizer=sft_pilot_gate.BASE_MODEL, tokenizer_revision=sft_pilot_gate.REVISION,
        role="all", hyperparameters=actual,
    )
    assert result["d3_status"] == "APPROVED_FOR_PREPARATION"
    assert result["execution_allowed"] is False


def test_preflight_source_hashes_accept_linux_lf_checkout(monkeypatch):
    original = sft_pilot_gate._read_bounded_snapshot

    def linux_read(path, bound):
        raw, status = original(path, bound)
        if raw is not None and path == REPO_ROOT / "requirements/train-constraints.txt":
            raw = raw.replace(b"\r\n", b"\n")
        return raw, status

    monkeypatch.setattr(sft_pilot_gate, "_read_bounded_snapshot", linux_read)
    plan = json.loads(sft_pilot_gate.PLAN_PATH.read_text())
    settings = plan["hyperparameters"]
    hyperparameters = sft._hyperparameters(SimpleNamespace(
        epochs=settings["epochs"], lr=settings["learning_rate"],
        batch_size=settings["batch_size"], grad_accum=settings["gradient_accumulation_steps"],
        max_seq_len=settings["max_sequence_length"], seed=settings["seed"],
    ))
    result = sft_pilot_gate.validate_preparation(
        snapshot_training_corpus(CORPUS), model=sft_pilot_gate.BASE_MODEL,
        model_revision=sft_pilot_gate.REVISION, tokenizer=sft_pilot_gate.BASE_MODEL,
        tokenizer_revision=sft_pilot_gate.REVISION, role="all",
        hyperparameters=hyperparameters,
    )
    assert result["execution_allowed"] is False


def test_plan_cannot_approve_execution_or_change_setting(monkeypatch, tmp_path):
    assert sft_pilot_gate.PLAN_PATH.is_file()
    original = json.loads(sft_pilot_gate.PLAN_PATH.read_text())
    common = dict(
        model=sft_pilot_gate.BASE_MODEL, model_revision=sft_pilot_gate.REVISION,
        tokenizer=sft_pilot_gate.BASE_MODEL, tokenizer_revision=sft_pilot_gate.REVISION,
        role="all", hyperparameters=original["hyperparameters"],
    )
    changed = copy.deepcopy(original)
    changed["execution_allowed"] = True
    alternate = tmp_path / "plan.json"
    alternate.write_text(json.dumps(changed))
    monkeypatch.setattr(sft_pilot_gate, "PLAN_PATH", alternate)
    monkeypatch.setattr(
        sft_pilot_gate, "PLAN_SHA256",
        hashlib.sha256(alternate.read_bytes()).hexdigest(),
    )
    with pytest.raises(ValueError, match="cannot grant"):
        sft_pilot_gate.validate_preparation(snapshot_training_corpus(CORPUS), **common)


def test_prep_manifest_preserves_exact_review_candidate_origin(monkeypatch, tmp_path):
    from training import sft_provenance

    monkeypatch.setattr(sft_provenance, "runtime_environment", lambda: {"packages": {}})
    manifest = sft_provenance.create_run_manifest(
        corpus_path=CORPUS, output_dir=tmp_path / "no-run",
        base_model=sft_pilot_gate.BASE_MODEL, base_model_revision=sft_pilot_gate.REVISION,
        tokenizer=sft_pilot_gate.BASE_MODEL, tokenizer_revision=sft_pilot_gate.REVISION,
        role="all", hyperparameters={},
    )
    assert manifest["dataset"]["data_origin"] == sft_provenance.REVIEW_CANDIDATE_ORIGIN
    assert manifest["dataset"]["synthetic"] is True
    normalized = sft_provenance.normalize_training_data_provenance(
        manifest["dataset"], approved_corpus_path=CORPUS,
    )
    assert normalized["data_origin"] == sft_provenance.REVIEW_CANDIDATE_ORIGIN
    assert normalized["corpus_manifest"]["content_verified"] is True


def test_future_permit_schema_is_closed_on_incomplete_source(monkeypatch, tmp_path):
    approval = {
        "schema_version": "atlasops-sft-execution-approval-v1",
        "execution_allowed": True, "approved_by": "unit-test operator",
        "plan_sha256": "a" * 64, "corpus_sha256": sft_pilot_gate.CORPUS_HASH,
        "corpus_manifest_sha256": sft_pilot_gate.MANIFEST_HASH,
        "run_id": "sft-pilot-test",
    }
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval))
    monkeypatch.setattr(sft_pilot_gate, "EXECUTION_APPROVAL_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="source/output"):
        sft_pilot_gate.require_execution_authority(
            {"plan_sha256": "a" * 64}, path, output_dir=tmp_path / "run",
        )


def test_future_valid_permit_uses_exact_host_and_single_gpu(monkeypatch, tmp_path):
    """Fake attestations and tiny files exercise schema only; no model is loaded."""
    from training import sft_provenance

    monkeypatch.setattr(sft_provenance, "source_provenance", lambda: {"git_sha": "a" * 40, "git_dirty": False})
    monkeypatch.setattr(sft_pilot_gate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sft_pilot_gate.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(sft_pilot_gate.platform, "python_version", lambda: "3.12.11")
    monkeypatch.setattr(sft_pilot_gate.socket, "gethostname", lambda: "fake-approved-host")
    monkeypatch.setattr(sft_pilot_gate.importlib.metadata, "version", lambda _name: "1.0")
    monkeypatch.setattr(sft_pilot_gate.shutil, "disk_usage", lambda _p: SimpleNamespace(free=50 * 1024**3))
    torch = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True, is_bf16_supported=lambda: True,
            device_count=lambda: 1, get_device_name=lambda _i: "fake-A100",
            get_device_properties=lambda _i: SimpleNamespace(total_memory=80 * 1024**3),
        ),
        version=SimpleNamespace(cuda="12.6"),
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    package_versions = {"fake-package": "1.0"}
    snapshot = tmp_path / "cache/models--Qwen--Qwen2.5-7B-Instruct/snapshots" / sft_pilot_gate.REVISION
    snapshot.mkdir(parents=True)
    from scripts.stage_sft_tokenizer import FILES

    expected_files = {}
    files = {}
    for name in (*FILES, "model-00001-of-00001.safetensors", "model.safetensors.index.json"):
        raw = b"fake fixture, not weights"
        if name == "model.safetensors.index.json":
            raw = json.dumps({"weight_map": {"fake.weight": "model-00001-of-00001.safetensors"}}).encode()
        path = snapshot / name
        path.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        files[str(path)] = digest
        expected_files[name] = {"sha256": digest}
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({
        "revision": sft_pilot_gate.REVISION, "repository": sft_pilot_gate.BASE_MODEL,
        "snapshot_dir": str(snapshot), "files": files,
    }))
    runtime = tmp_path / "runtime.json"
    image = "sha256:" + "b" * 64
    runtime.write_text(json.dumps({
        "image_digest": image, "hostname": "fake-approved-host",
        "package_versions": package_versions, "lock_sha256": "c" * 64,
        "storage": {"free_bytes": 50 * 1024**3},
        "os_package_inventory": {"status": "RECORDED", "sha256": "e" * 64},
    }))
    attestation = tmp_path / "attestation.json"
    attestation.write_text(json.dumps({
        "image_digest": image, "hostname": "fake-approved-host",
        "verified": True, "verified_by": "independent-fake-reviewer",
        "persistent_storage_verified": True, "storage_quota_verified": True,
        "os_package_inventory_sha256": "e" * 64,
    }))
    plan = {
        "environment": {"package_versions": package_versions, "python_version": "3.12.11"},
        "required_files": {"requirements/sft-pilot-linux-py312.lock": "c" * 64},
    }
    original_read = sft_pilot_gate._read

    def fake_read(path):
        if path == sft_pilot_gate.PLAN_PATH:
            return plan, b"fake-plan"
        if path == REPO_ROOT / "artifacts/evidence/stage7/tokenizer_files_a09a354_v1.json":
            return {"files": expected_files}, b"fake-tokenizer-record"
        return original_read(path)

    monkeypatch.setattr(sft_pilot_gate, "_read", fake_read)
    original_stat = Path.stat

    def readonly_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == snapshot or path.is_relative_to(snapshot):
            data = {
                name: getattr(value, name)
                for name in dir(value) if name.startswith("st_")
            }
            data["st_mode"] = value.st_mode & ~0o222
            return SimpleNamespace(**data)
        return value

    monkeypatch.setattr(Path, "stat", readonly_stat)
    output = tmp_path / "never-run"
    approval = {
        "schema_version": "atlasops-sft-execution-approval-v1",
        "execution_allowed": True, "approved_by": "fake-test-operator",
        "plan_sha256": "d" * 64, "corpus_sha256": sft_pilot_gate.CORPUS_HASH,
        "corpus_manifest_sha256": sft_pilot_gate.MANIFEST_HASH, "run_id": "sft-pilot-test",
        "source_git_sha": "a" * 40, "output_dir": str(output),
        "package_versions": package_versions,
        "host": {
            "image_digest": image, "hostname": "fake-approved-host", "python_version": "3.12.11",
            "entitlement_verified": True, "storage_verified": True, "budget_approved": True,
            "runtime_manifest": str(runtime), "runtime_manifest_sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
            "image_attestation": str(attestation),
            "image_attestation_sha256": hashlib.sha256(attestation.read_bytes()).hexdigest(),
            "gpu_name": "fake-A100", "gpu_memory_bytes": 80 * 1024**3, "cuda_runtime": "12.6",
        },
        "model_files_manifest": str(inventory),
        "model_files_manifest_sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
    }
    permit = tmp_path / "permit.json"
    permit.write_text(json.dumps(approval))
    monkeypatch.setattr(sft_pilot_gate, "EXECUTION_APPROVAL_SHA256", hashlib.sha256(permit.read_bytes()).hexdigest())
    result = sft_pilot_gate.require_execution_authority({"plan_sha256": "d" * 64}, permit, output_dir=output)
    assert result["run_id"] == "sft-pilot-test"
    assert not output.exists()
    altered = json.loads(runtime.read_text())
    altered["os_package_inventory"].pop("sha256")
    runtime.write_text(json.dumps(altered))
    approval["host"]["runtime_manifest_sha256"] = hashlib.sha256(runtime.read_bytes()).hexdigest()
    permit.write_text(json.dumps(approval))
    monkeypatch.setattr(sft_pilot_gate, "EXECUTION_APPROVAL_SHA256", hashlib.sha256(permit.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="OS package"):
        sft_pilot_gate.require_execution_authority({"plan_sha256": "d" * 64}, permit, output_dir=output)
    altered["os_package_inventory"]["sha256"] = "e" * 64
    runtime.write_text(json.dumps(altered))
    approval["host"]["runtime_manifest_sha256"] = hashlib.sha256(runtime.read_bytes()).hexdigest()
    permit.write_text(json.dumps(approval))
    monkeypatch.setattr(sft_pilot_gate, "EXECUTION_APPROVAL_SHA256", hashlib.sha256(permit.read_bytes()).hexdigest())
    torch.cuda.device_count = lambda: 2
    with pytest.raises(ValueError, match="CUDA/BF16"):
        sft_pilot_gate.require_execution_authority({"plan_sha256": "d" * 64}, permit, output_dir=output)
