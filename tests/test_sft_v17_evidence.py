"""Check preserved pilot evidence without loading weights or running evaluation."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from scripts.package_submission import collect_submission_assets

ROOT = Path("artifacts/evidence/stage7/free-t4-v17")
RUN_ID = "sft-pilot-free-t4-20261003-v17"


def record(name):
    return json.loads((ROOT / name).read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v17_records_bind_completed_run_and_independent_reload():
    result = record("RESULT.json")
    manifest = record("sft_run_manifest.json")
    approval = record("execution-approval-v17.json")
    reload = record("reload-v17.json")
    runtime = record("runtime-at-launch-v17.json")
    assert result["goal_complete"] is True
    assert manifest["status"] == "completed"
    assert record("training-exit-v17.json")["exit_code"] == 0
    assert manifest["trainer_state"]["epoch"] == 1
    assert manifest["trainer_state"]["global_step"] == 9
    assert manifest["dataset"]["total_examples"] == 68
    assert manifest["dataset"]["synthetic"] is True
    assert manifest["dataset"]["split"] == "train"
    assert {result["run_id"], manifest["run_id"], approval["run_id"], reload["run_id"]} == {RUN_ID}
    assert manifest["source"]["git_sha"] == result["source_git_sha"] == approval["source_git_sha"]
    assert manifest["source"]["git_dirty"] is False
    assert digest(ROOT / "sft_run_manifest.json") == result["adapter"]["run_manifest_sha256"]
    approval_hash = digest(ROOT / "execution-approval-v17.json")
    assert approval_hash == result["execution_approval"]["sha256"]
    assert approval_hash == manifest["execution_approval"]["approval_record_sha256"]
    assert approval_hash == reload["approval_record_sha256"]
    assert datetime.fromisoformat(approval["approved_at_utc"]) < datetime.fromisoformat(manifest["started_at"])
    assert approval["execution_allowed"] is True
    assert approval["spend_usd_max"] == 0
    assert all(value is False for value in approval["scope"].values())
    assert digest(ROOT / "runtime-at-launch-v17.json") == approval["host"]["runtime_manifest_sha256"]
    assert runtime["package_versions"] == approval["package_versions"]
    parent = json.loads(Path("config/sft_pilot_v4.json").read_bytes())
    assert runtime["package_versions"] == parent["environment"]["package_versions"]
    assert len(runtime["package_versions"]) == 72
    assert runtime["gpu_name"] == "Tesla T4" and runtime["gpu_count"] == 1
    assert runtime["python_version"] == "3.12.11"
    assert digest(Path("requirements/sft-pilot-linux-py312.lock")) == runtime["lock_sha256"]
    assert digest(ROOT / "model-inventory-v1.json") == approval["model_files_manifest_sha256"]
    assert record("base-transfer-report.json")["status"] == "COMPLETE"
    assert reload["status"] == "PASS" and reload["adapter_tensor_count"] == 392
    assert reload["checkpoint_manifest_sha256_before"] == reload["checkpoint_manifest_sha256_after"]
    assert reload["checkpoint_manifest_sha256_before"] == digest(ROOT / "sft_run_manifest.json")
    assert reload["inference_performed"] is False
    assert reload["held_out_outcomes_accessed"] is False


def test_v17_preservation_inventory_matches_checkpoint_and_small_records():
    result = record("RESULT.json")
    inventory = record("local-verification.json")
    manifest = record("sft_run_manifest.json")
    assert inventory["status"] == "PASS" and inventory["extracted_files"] == 100
    assert inventory["archive_sha256"] == result["preservation"]["archive_sha256"]
    assert inventory["archive_bytes"] == result["preservation"]["archive_bytes"] == 377494628
    files = {item["path"]: item for item in inventory["file_inventory"]}
    assert len(files) == 100
    prefix = f"runs/{RUN_ID}/"
    for item in manifest["checkpoint"]["files"]:
        assert files[prefix + item["path"]] == {**item, "path": prefix + item["path"]}
    assert len(manifest["checkpoint"]["files"]) == 27
    assert files[prefix + "adapter_model.safetensors"]["sha256"] == result["adapter"]["weight_sha256"]
    for path in ROOT.iterdir():
        if path.name in files:
            assert digest(path) == files[path.name]["sha256"]
            assert path.stat().st_size == files[path.name]["size_bytes"]
    assert result["preservation"]["drive_private"] is True
    assert result["preservation"]["local_archive_and_extraction_verified"] is True
    assert result["preservation"]["prior_negative_archive_unchanged"] is True
    assert result["grpo_started"] is False
    assert result["final_test_accessed"] is False
    assert result["empirical_gate_promoted"] is False


def test_free_t4_evidence_and_runtime_sources_are_in_submission_inventory():
    required = {
        "config/sft_free_t4_v1.json",
        "docs/project/G7_FREE_T4_PILOT_V1.md",
        "scripts/reload_sft_free_t4.py",
        "scripts/transfer_t4_model_files.py",
        "training/sft_free_t4_gate.py",
        "training/sft_chunked_loss.py",
        "training/sft_supervised_trainer.py",
        "training/sft_t4_attention.py",
        *(path.as_posix() for path in ROOT.iterdir()),
    }
    assets = collect_submission_assets()
    assert required <= assets.keys()
    assert not any(path.endswith((".safetensors", ".zip", ".pt", ".bin")) for path in assets)
