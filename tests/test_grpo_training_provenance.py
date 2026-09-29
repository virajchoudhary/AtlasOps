"""GRPO run records must precede training and validate saved checkpoint bytes."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.splits import TRAIN_SPLIT
from training import grpo_provenance
from training.grpo_provenance import (
    MANIFEST_NAME,
    checkpoint_inventory,
    create_run_manifest,
    has_verified_final_rollout,
    mark_training_started,
    persist_status,
    record_loader_provenance,
    validate_sft_parent,
    validate_training_summary,
)
from training.sft_provenance import (
    canonical_json_sha256,
    write_manifest_atomic,
)
from training.sft_provenance import (
    create_run_manifest as create_sft_manifest,
)
from training.sft_provenance import (
    mark_completed as complete_sft,
)
from training.sft_provenance import (
    mark_running as run_sft,
)

MODEL_COMMIT = "f" * 40
TOKENIZER_COMMIT = "e" * 40
TOKENIZER_LOADER_MATCH_BASIS = "LOADER_EXPOSED_COMMIT_HASH_MATCH"
TOKENIZER_PIN_ONLY_BASIS = "PIN_ENFORCED_BY_LOADER_ARGUMENT/NOT_INDEPENDENTLY_RETURNED"


def _sft_parent_record(
    tmp_path,
    *,
    tokenizer_revision_basis: str = TOKENIZER_LOADER_MATCH_BASIS,
):
    corpus = tmp_path / "train.jsonl"
    corpus.write_text(
        "".join(json.dumps({"scenario_id": scenario_id, "role": "triage"}) + "\n"
                for scenario_id in TRAIN_SPLIT),
        encoding="utf-8",
    )
    checkpoint = tmp_path / "sft"
    checkpoint.mkdir()
    manifest = create_sft_manifest(
        corpus_path=corpus,
        output_dir=checkpoint,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        base_model_revision=MODEL_COMMIT,
        tokenizer="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
        role="all",
        hyperparameters={},
    )
    manifest = run_sft(
        manifest,
        resolved_model_revision=MODEL_COMMIT,
        resolved_tokenizer_revision=TOKENIZER_COMMIT,
        resolved_tokenizer_revision_basis=tokenizer_revision_basis,
    )
    manifest["source"] = {"git_sha": "a" * 40, "git_dirty": False}
    (checkpoint / "adapter_config.json").write_text("{}", encoding="utf-8")
    (checkpoint / "adapter_model.safetensors").write_bytes(b"sft-adapter-fixture")
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = complete_sft(
        manifest,
        output_dir=checkpoint,
        manifest_path=manifest_path,
        trainer_state={"global_step": 1},
        training_history=[],
    )
    write_manifest_atomic(manifest_path, manifest)
    return checkpoint, validate_sft_parent(
        checkpoint,
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
    )


def _parent():
    return {
        "checkpoint_path": "test-sft-checkpoint",
        "manifest_sha256": "c" * 64,
        "checkpoint_tree_sha256": "d" * 64,
        "training_source_sha": "a" * 40,
        "train_corpus_sha256": "e" * 64,
        "train_split_sha256": canonical_json_sha256(list(TRAIN_SPLIT)),
        "base_model_id": "Qwen/Qwen2.5-7B-Instruct",
        "base_model_revision": MODEL_COMMIT,
        "tokenizer_id": "Qwen/Qwen2.5-7B-Instruct",
        "tokenizer_revision": TOKENIZER_COMMIT,
    }


def _planned_manifest(
    *,
    seed=42,
    optuna_trials=0,
    tiers=None,
    include_operator_approval=False,
):
    requested = {
        "tiers": tiers or ["single_fault"],
        "learning_rate": 1e-6,
        "beta": 0.04,
        "batch_size": 1,
        "num_generations": 8,
        "max_steps": 1,
        "gradient_accumulation_steps": 4,
        "optuna_trials": optuna_trials,
    }
    manifest = create_run_manifest(
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
        seed=seed,
        generation_config={"max_completion_length": 256},
        hyperparameters=requested,
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
        sft_parent=_parent(),
        prompt_rows=[{"scenario_id": TRAIN_SPLIT[0], "prompt": "public fixture"}],
    )
    manifest["training"]["effective_hyperparameters"] = {
        **requested,
        "max_completion_length": 256,
    }
    manifest["training"]["hyperparameter_selection"] = "requested"
    if include_operator_approval:
        manifest["training"]["operator_approval"] = {
            "mode": "disabled",
            "timeout_seconds": None,
            "identity": "operator_supplied_name_not_independent_attestation",
        }
    return manifest


def _training_args(tmp_path, **overrides):
    values = {
        "execute_live_chaos": True,
        "kube_context": "kind-atlasops-test",
        "tiers": "single_fault",
        "seed": 42,
        "optuna": 0,
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "model_revision": MODEL_COMMIT,
        "tokenizer": None,
        "tokenizer_revision": TOKENIZER_COMMIT,
        "sft_checkpoint": tmp_path / "sft",
        "lr": 1e-6,
        "beta": 0.04,
        "batch_size": 1,
        "num_generations": 8,
        "max_steps": 1,
        "grad_accum": 4,
        "max_compl_len": 256,
        "enable_p1_approval": False,
    }
    values.update(overrides)
    return Namespace(**values)


def _valid_training_summary(training):
    summary = {
        "requested_hyperparameters": training["requested_hyperparameters"],
        "effective_hyperparameters": training["effective_hyperparameters"],
        "hyperparameter_selection": training["hyperparameter_selection"],
        "generation_config": training["generation_config"],
        "live_execution": training["live_execution"],
        "total_steps": 1,
        "trainer_log_history": [],
    }
    if training.get("operator_approval") is not None:
        summary["operator_approval"] = training["operator_approval"]
    return summary


def _write_valid_completion_records(output_dir, training):
    effective = training["effective_hyperparameters"]
    live_execution = training["live_execution"]
    (output_dir / "rollout_trajectories.jsonl").write_text(
        json.dumps({
            "status": "ok",
            "scorable": True,
            "rollout_phase": "final_training",
            "effective_hyperparameters": effective,
            "verification": {
                "verification_status": "failed",
                "env_resolved": False,
            },
            "settling": {
                "status": "settled",
                "stable": True,
                "verification_status": "failed",
                "required_stable_observations": 2,
                "stable_observations": 2,
            },
            "live_execution": live_execution,
        }) + "\n",
        encoding="utf-8",
    )
    (output_dir / "training_summary.json").write_text(
        json.dumps(_valid_training_summary(training)),
        encoding="utf-8",
    )


def test_operator_approval_profile_must_match_training_summary():
    training = _planned_manifest()["training"]
    training["operator_approval"] = {
        "mode": "loopback_exact_action_v1",
        "timeout_seconds": 300,
        "identity": "operator_supplied_name_not_independent_attestation",
    }
    summary = _valid_training_summary(training)
    summary["operator_approval"] = {
        "mode": "disabled",
        "timeout_seconds": None,
    }
    with pytest.raises(ValueError, match="operator approval"):
        validate_training_summary(training, summary)
    summary["operator_approval"] = training["operator_approval"]
    validate_training_summary(training, summary)


@pytest.mark.parametrize(
    ("summary_field", "bad_value"),
    [
        ("total_steps", 0),
        ("total_steps", -1),
        ("total_steps", True),
        ("total_steps", 1.0),
        ("trainer_log_history", None),
        ("trainer_log_history", {}),
    ],
)
def test_training_summary_rejects_invalid_training_progress(
    summary_field, bad_value
):
    training = _planned_manifest()["training"]
    summary = _valid_training_summary(training)
    summary[summary_field] = bad_value

    with pytest.raises((TypeError, ValueError), match=summary_field):
        validate_training_summary(training, summary)


@pytest.mark.parametrize("summary_field", ["total_steps", "trainer_log_history"])
def test_training_summary_requires_training_progress_fields(summary_field):
    training = _planned_manifest()["training"]
    summary = _valid_training_summary(training)
    del summary[summary_field]

    with pytest.raises((TypeError, ValueError), match=summary_field):
        validate_training_summary(training, summary)


@pytest.mark.parametrize(
    ("summary_field", "bad_value", "remove_field"),
    [
        ("total_steps", 0, False),
        ("total_steps", None, True),
        ("total_steps", True, False),
        ("total_steps", 1.0, False),
        ("trainer_log_history", None, True),
        ("trainer_log_history", {}, False),
    ],
)
def test_invalid_training_summary_is_rejected_before_inventory_or_completion(
    monkeypatch, tmp_path, summary_field, bad_value, remove_field
):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    _write_valid_completion_records(tmp_path, manifest["training"])
    rollout_path = tmp_path / "rollout_trajectories.jsonl"
    raw_rollout = rollout_path.read_bytes()
    summary_path = tmp_path / "training_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if remove_field:
        del summary[summary_field]
    else:
        summary[summary_field] = bad_value
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    monkeypatch.setattr(
        grpo_provenance,
        "checkpoint_inventory",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid training summary reached checkpoint inventory"
        ),
    )
    with pytest.raises((TypeError, ValueError), match=summary_field):
        persist_status(manifest_path, manifest, "completed")

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["status"] == "running"
    assert saved["checkpoint"] is None
    assert rollout_path.read_bytes() == raw_rollout


def test_completed_manifest_hashes_all_checkpoint_files(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "planned")
    assert manifest["splits"]["train_sha256"] == canonical_json_sha256(list(TRAIN_SPLIT))
    assert manifest["training"]["mode"] == "online_rl_real_environment"
    assert manifest["training"]["live_execution"] == {
        "execute_live_chaos": True,
        "kube_context": "kind-atlasops-test",
    }
    assert manifest["sft_parent"]["checkpoint_tree_sha256"] == "d" * 64
    assert manifest["splits"]["selected_scenario_ids"] == [TRAIN_SPLIT[0]]
    assert manifest["splits"]["selected_prompt_rows_sha256"]

    manifest = persist_status(manifest_path, manifest, "running")
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "adapter_model.safetensors").write_bytes(b"real-bytes-for-test-only")
    effective = manifest["training"]["effective_hyperparameters"]
    (tmp_path / "rollout_trajectories.jsonl").write_text(
        json.dumps({
            "status": "ok",
            "scorable": True,
            "rollout_phase": "final_training",
            "effective_hyperparameters": effective,
            "verification": {
                "verification_status": "failed",
                "env_resolved": False,
                "checks": [{
                    "name": "workload_ready",
                    "target": "default/paymentservice",
                    "required": True,
                    "passed": False,
                    "observed": {"ready_replicas": 1, "desired_replicas": 2},
                }],
            },
            "settling": {
                "status": "settled",
                "stable": True,
                "verification_status": "failed",
                "required_stable_observations": 2,
                "stable_observations": 2,
            },
            "live_execution": {
                "execute_live_chaos": True,
                "kube_context": "kind-atlasops-test",
            },
        }) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "training_summary.json").write_text(
        json.dumps(_valid_training_summary(manifest["training"])),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="loader-verified model/tokenizer provenance"):
        persist_status(manifest_path, manifest, "completed")
    manifest = record_loader_provenance(
        manifest_path,
        model=SimpleNamespace(config=SimpleNamespace(_commit_hash=MODEL_COMMIT)),
        tokenizer=SimpleNamespace(init_kwargs={}),
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
    )
    assert manifest["tokenizer"]["resolved_revision"] == TOKENIZER_COMMIT
    assert manifest["tokenizer"]["resolved_revision_basis"] == TOKENIZER_PIN_ONLY_BASIS
    completed = persist_status(manifest_path, manifest, "completed")
    assert completed["status"] == "completed"
    assert {row["path"] for row in completed["checkpoint"]["files"]} == {
        "adapter_config.json",
        "adapter_model.safetensors",
        "rollout_trajectories.jsonl",
        "training_summary.json",
    }
    assert completed["checkpoint"] == checkpoint_inventory(tmp_path)


def test_repeated_loaders_preserve_strongest_tokenizer_revision_basis(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    model = SimpleNamespace(config=SimpleNamespace(_commit_hash=MODEL_COMMIT))
    identity = {
        "model_id": "Qwen/Qwen2.5-7B-Instruct",
        "model_revision": MODEL_COMMIT,
        "tokenizer_id": "Qwen/Qwen2.5-7B-Instruct",
        "tokenizer_revision": TOKENIZER_COMMIT,
    }

    manifest = record_loader_provenance(
        manifest_path,
        model=model,
        tokenizer=SimpleNamespace(init_kwargs={}),
        **identity,
    )
    assert manifest["tokenizer"]["resolved_revision_basis"] == TOKENIZER_PIN_ONLY_BASIS

    manifest = record_loader_provenance(
        manifest_path,
        model=model,
        tokenizer=SimpleNamespace(init_kwargs={"_commit_hash": TOKENIZER_COMMIT}),
        **identity,
    )
    assert manifest["tokenizer"]["resolved_revision_basis"] == TOKENIZER_LOADER_MATCH_BASIS

    manifest = record_loader_provenance(
        manifest_path,
        model=model,
        tokenizer=SimpleNamespace(init_kwargs={}),
        **identity,
    )
    assert manifest["tokenizer"]["resolved_revision_basis"] == TOKENIZER_LOADER_MATCH_BASIS


def test_completion_rejects_verified_optuna_ledger_without_final_rollout(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "adapter_model.safetensors").write_bytes(b"adapter-fixture")
    trial_ledger = tmp_path / "optuna_trials" / "trial_0" / "rollout_trajectories.jsonl"
    trial_ledger.parent.mkdir(parents=True)
    trial_ledger.write_text(
        json.dumps({
            "status": "ok",
            "rollout_phase": "optuna_trial",
            "trial_number": 0,
            "effective_hyperparameters": {
                "learning_rate": 1e-6,
                "beta": 0.01,
                "num_generations": 4,
            },
            "verification": {"env_resolved": True},
            "live_execution": {
                "execute_live_chaos": True,
                "kube_context": "kind-atlasops-test",
            },
        }) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="final-training rollout"):
        persist_status(manifest_path, manifest, "completed")

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "running"


@pytest.mark.parametrize("invalid_first", [False, True])
def test_completion_rejects_mixed_final_rollout_ledger_in_either_order(
    tmp_path, invalid_first
):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "adapter_model.safetensors").write_bytes(b"adapter-fixture")
    effective = manifest["training"]["effective_hyperparameters"]
    live_execution = manifest["training"]["live_execution"]
    valid_row = {
        "status": "ok",
        "scorable": True,
        "rollout_phase": "final_training",
        "effective_hyperparameters": effective,
        "verification": {
            "verification_status": "failed",
            "env_resolved": False,
        },
        "settling": {
            "status": "settled",
            "stable": True,
            "verification_status": "failed",
            "required_stable_observations": 2,
            "stable_observations": 2,
        },
        "live_execution": live_execution,
    }
    invalid_row = {
        **valid_row,
        "status": "unscorable",
        "scorable": False,
        "verification": {
            "verification_status": "inconclusive",
            "env_resolved": False,
        },
        "settling": {
            "status": "timeout",
            "stable": False,
            "verification_status": "inconclusive",
            "required_stable_observations": 2,
            "stable_observations": 0,
        },
    }
    rows = (
        [invalid_row, valid_row]
        if invalid_first
        else [valid_row, invalid_row]
    )
    ledger_path = tmp_path / "rollout_trajectories.jsonl"
    ledger_contents = "".join(json.dumps(row) + "\n" for row in rows)
    ledger_path.write_text(ledger_contents, encoding="utf-8")
    (tmp_path / "training_summary.json").write_text(
        json.dumps(_valid_training_summary(manifest["training"])),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="final-training rollout row"):
        persist_status(manifest_path, manifest, "completed")

    assert ledger_path.read_text(encoding="utf-8") == ledger_contents
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "running"


def test_optuna_rollout_is_not_final_training_evidence(tmp_path):
    ledger = tmp_path / "rollout_trajectories.jsonl"
    ledger.write_text(
        json.dumps({
            "status": "ok",
            "scorable": True,
            "rollout_phase": "optuna_trial",
            "trial_number": 0,
            "effective_hyperparameters": {
                "learning_rate": 1e-6,
                "beta": 0.01,
                "num_generations": 4,
            },
            "verification": {
                "verification_status": "failed",
                "env_resolved": False,
            },
            "settling": {
                "status": "settled",
                "stable": True,
                "verification_status": "failed",
                "required_stable_observations": 2,
                "stable_observations": 2,
            },
        }) + "\n",
        encoding="utf-8",
    )

    assert has_verified_final_rollout(ledger) is False


def test_final_rollout_accepts_conclusive_failed_settle_as_a_negative(tmp_path):
    ledger = tmp_path / "rollout_trajectories.jsonl"
    ledger.write_text(
        json.dumps({
            "status": "ok",
            "scorable": True,
            "rollout_phase": "final_training",
            "effective_hyperparameters": {"learning_rate": 1e-6},
            "verification": {
                "verification_status": "failed",
                "env_resolved": False,
            },
            "settling": {
                "status": "settled",
                "stable": True,
                "verification_status": "failed",
                "required_stable_observations": 2,
                "stable_observations": 2,
            },
        }) + "\n",
        encoding="utf-8",
    )

    assert has_verified_final_rollout(ledger) is True


@pytest.mark.parametrize(
    ("scorable", "verification_status", "settle_status", "stable"),
    [
        (False, "inconclusive", "timeout", False),
        (True, "inconclusive", "timeout", False),
        (True, "error", "error", False),
        (True, "failed", "timeout", False),
        (True, "failed", "settled", False),
    ],
)
def test_final_rollout_rejects_unscorable_or_unsettled_verification(
    tmp_path, scorable, verification_status, settle_status, stable
):
    ledger = tmp_path / "rollout_trajectories.jsonl"
    ledger.write_text(
        json.dumps({
            "status": "ok",
            "scorable": scorable,
            "rollout_phase": "final_training",
            "effective_hyperparameters": {"learning_rate": 1e-6},
            "verification": {
                "verification_status": verification_status,
                "env_resolved": False,
            },
            "settling": {
                "status": settle_status,
                "stable": stable,
                "verification_status": verification_status,
                "required_stable_observations": 2,
                "stable_observations": 0 if not stable else 2,
            },
        }) + "\n",
        encoding="utf-8",
    )

    assert has_verified_final_rollout(ledger) is False


@pytest.mark.parametrize(
    ("status", "error_type", "include_summary"),
    [
        ("failed", "RuntimeError", True),
        ("interrupted", "KeyboardInterrupt", False),
        ("interrupted", "KeyboardInterrupt", True),
    ],
)
def test_noncompleted_status_preserves_and_inventories_partial_evidence(
    tmp_path, status, error_type, include_summary
):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    rollout_path = tmp_path / "rollout_trajectories.jsonl"
    raw_rollout = (
        b'{"status":"interrupted","raw":"verbatim",'
        b'"private_marker":"rollout-secret-fixture"}\n'
    )
    rollout_path.write_bytes(raw_rollout)
    raw_summary = b'{"partial":true,"private_marker":"summary-secret-fixture"}\n'
    summary_path = tmp_path / "training_summary.json"
    if include_summary:
        summary_path.write_bytes(raw_summary)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "unrelated-secret.txt").write_text(
        "not an allowed partial artifact",
        encoding="utf-8",
    )

    if os.name == "nt":
        with pytest.raises(OSError, match="stable directory handle"):
            persist_status(
                manifest_path,
                manifest,
                status,
                error_type=error_type,
            )
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "running"
        assert rollout_path.read_bytes() == raw_rollout
        if include_summary:
            assert summary_path.read_bytes() == raw_summary
        return

    saved = persist_status(
        manifest_path,
        manifest,
        status,
        error_type=error_type,
    )

    assert saved["status"] == status
    assert saved["failure"]["error_type"] == error_type
    assert saved["checkpoint"] is None
    expected_files = [
        {
            "path": "rollout_trajectories.jsonl",
            "size_bytes": len(raw_rollout),
            "sha256": hashlib.sha256(raw_rollout).hexdigest(),
        }
    ]
    if include_summary:
        expected_files.append(
            {
                "path": "training_summary.json",
                "size_bytes": len(raw_summary),
                "sha256": hashlib.sha256(raw_summary).hexdigest(),
            }
        )
    assert saved["partial_artifacts"]["unverified"] == []
    assert saved["partial_artifacts"]["files"] == expected_files
    serialized_manifest = json.dumps(saved)
    assert "rollout-secret-fixture" not in serialized_manifest
    assert "summary-secret-fixture" not in serialized_manifest
    assert "unrelated-secret.txt" not in serialized_manifest
    assert rollout_path.read_bytes() == raw_rollout
    if include_summary:
        assert summary_path.read_bytes() == raw_summary
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["status"] == status
    assert persisted["failure"]["error_type"] == error_type
    assert persisted["checkpoint"] is None
    assert persisted["partial_artifacts"]["files"] == expected_files
    assert persisted["partial_artifacts"]["unverified"] == (
        saved["partial_artifacts"]["unverified"]
    )


def test_failed_status_does_not_follow_redirected_partial_artifacts(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    raw_rollout = b'{"status":"interrupted","raw":"verbatim"}\n'
    rollout_path = tmp_path / "rollout_trajectories.jsonl"
    rollout_path.write_bytes(raw_rollout)
    summary_target = tmp_path / "external-summary.json"
    raw_summary = b'{"private_marker":"redirect-target-secret-fixture"}\n'
    summary_target.write_bytes(raw_summary)
    summary_path = tmp_path / "training_summary.json"
    try:
        summary_path.symlink_to(summary_target)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")

    if os.name == "nt":
        with pytest.raises(OSError, match="stable directory handle"):
            persist_status(
                manifest_path,
                manifest,
                "failed",
                error_type="RuntimeError",
            )
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "running"
    else:
        saved = persist_status(
            manifest_path,
            manifest,
            "failed",
            error_type="RuntimeError",
        )
        assert saved["partial_artifacts"]["files"] == [
            {
                "path": "rollout_trajectories.jsonl",
                "size_bytes": len(raw_rollout),
                "sha256": hashlib.sha256(raw_rollout).hexdigest(),
            }
        ]
    serialized = (
        manifest_path.read_text(encoding="utf-8")
        if os.name == "nt"
        else json.dumps(saved)
    )
    assert summary_target.read_bytes() == raw_summary
    assert "redirect-target-secret-fixture" not in serialized
    assert "external-summary.json" not in serialized


def test_failed_status_keeps_partial_inventory_bound_during_parent_replacement(
    monkeypatch, tmp_path
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    original_bytes = b'{"private_marker":"original-rollout-fixture"}\n'
    replacement_bytes = b'{"private_marker":"replacement-rollout-secret"}\n'
    (run_dir / "rollout_trajectories.jsonl").write_bytes(original_bytes)

    if os.name == "nt":
        with pytest.raises(OSError, match="stable directory handle"):
            persist_status(
                manifest_path,
                manifest,
                "failed",
                error_type="RuntimeError",
            )
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "running"
        assert (run_dir / "rollout_trajectories.jsonl").read_bytes() == original_bytes
        return

    moved_dir = tmp_path / "moved-run"
    real_open = os.open
    replaced = False

    def replace_parent_before_artifact_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if (
            not replaced
            and Path(path).name == "rollout_trajectories.jsonl"
        ):
            replaced = True
            run_dir.rename(moved_dir)
            run_dir.mkdir()
            (run_dir / "rollout_trajectories.jsonl").write_bytes(replacement_bytes)
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(grpo_provenance.os, "open", replace_parent_before_artifact_open)
    failed = persist_status(
        manifest_path,
        manifest,
        "failed",
        error_type="RuntimeError",
    )

    assert replaced is True
    assert failed["status"] == "failed"
    assert failed["checkpoint"] is None
    assert failed["partial_artifacts"]["files"] == [
        {
            "path": "rollout_trajectories.jsonl",
            "size_bytes": len(original_bytes),
            "sha256": hashlib.sha256(original_bytes).hexdigest(),
        }
    ]
    assert failed["partial_artifacts"]["unverified"] == []
    assert "replacement-rollout-secret" not in json.dumps(failed)
    assert (moved_dir / "rollout_trajectories.jsonl").read_bytes() == original_bytes
    assert (run_dir / "rollout_trajectories.jsonl").read_bytes() == replacement_bytes
    assert not (run_dir / MANIFEST_NAME).exists()
    persisted = json.loads((moved_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["partial_artifacts"] == failed["partial_artifacts"]


@pytest.mark.skipif(os.name == "nt", reason="Requires POSIX directory handles")
def test_failed_status_rejects_normal_directory_replacement_before_open(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / MANIFEST_NAME
    running = persist_status(manifest_path, _planned_manifest(), "running")
    assert running["run_directory_identity"]["inode"] == run_dir.stat().st_ino
    moved_dir = tmp_path / "moved-run"
    run_dir.rename(moved_dir)
    run_dir.mkdir()
    replacement_bytes = b'{"private_marker":"replacement-secret"}\n'
    (run_dir / "rollout_trajectories.jsonl").write_bytes(replacement_bytes)

    with pytest.raises(OSError, match="run directory identity changed"):
        persist_status(
            manifest_path,
            running,
            "failed",
            error_type="RuntimeError",
        )

    assert not manifest_path.exists()
    persisted = json.loads((moved_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert persisted["status"] == "running"
    assert persisted["run_directory_identity"] == running["run_directory_identity"]
    assert (run_dir / "rollout_trajectories.jsonl").read_bytes() == replacement_bytes
    assert "replacement-secret" not in json.dumps(persisted)


@pytest.mark.skipif(os.name == "nt", reason="Requires POSIX directory handles")
def test_failed_status_does_not_write_through_replaced_parent_on_error(
    monkeypatch, tmp_path
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    (run_dir / "rollout_trajectories.jsonl").write_bytes(b"raw evidence\n")
    moved_dir = tmp_path / "moved-run"

    def fail_pinned_write(directory_fd, name, updated):
        run_dir.rename(moved_dir)
        run_dir.mkdir()
        raise OSError("synthetic pinned write failure")

    monkeypatch.setattr(grpo_provenance, "_write_manifest_atomic_at", fail_pinned_write)
    with pytest.raises(OSError, match="synthetic pinned write failure"):
        persist_status(manifest_path, manifest, "failed", error_type="RuntimeError")

    assert not manifest_path.exists()
    assert json.loads((moved_dir / MANIFEST_NAME).read_text(encoding="utf-8"))[
        "status"
    ] == "running"
    assert (moved_dir / "rollout_trajectories.jsonl").read_bytes() == b"raw evidence\n"


@pytest.mark.skipif(os.name == "nt", reason="Requires POSIX directory handles")
def test_failed_status_does_not_write_through_redirected_parent(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest_path = run_dir / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    moved_dir = tmp_path / "moved-run"
    run_dir.rename(moved_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        run_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(OSError, match="stable directory handle"):
        persist_status(manifest_path, manifest, "failed", error_type="RuntimeError")

    assert not (outside / MANIFEST_NAME).exists()
    assert json.loads((moved_dir / MANIFEST_NAME).read_text(encoding="utf-8"))[
        "status"
    ] == "running"


@pytest.mark.skipif(os.name == "nt", reason="Requires POSIX directory handles")
def test_failed_status_does_not_hash_hardlinked_partial_artifact(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    outside = tmp_path / "outside-secret"
    raw = b"hardlinked-private-marker\n"
    outside.write_bytes(raw)
    os.link(outside, tmp_path / "rollout_trajectories.jsonl")

    saved = persist_status(
        manifest_path, manifest, "failed", error_type="RuntimeError"
    )

    assert saved["partial_artifacts"]["files"] == []
    assert saved["partial_artifacts"]["unverified"] == [
        {
            "path": "rollout_trajectories.jsonl",
            "presence": "redirect",
            "reason": "path_redirect",
        }
    ]
    assert "hardlinked-private-marker" not in json.dumps(saved)
    assert outside.read_bytes() == raw


@pytest.mark.skipif(os.name == "nt", reason="Windows fallback cannot verify file hashes")
def test_failed_status_bounds_total_partial_artifact_bytes(monkeypatch, tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    raw_rollout = b"r" * 6
    raw_summary = b"s" * 5
    (tmp_path / "rollout_trajectories.jsonl").write_bytes(raw_rollout)
    (tmp_path / "training_summary.json").write_bytes(raw_summary)
    monkeypatch.setattr(grpo_provenance, "MAX_PARTIAL_ARTIFACT_BYTES", 8)

    saved = persist_status(
        manifest_path,
        manifest,
        "failed",
        error_type="RuntimeError",
    )

    assert saved["partial_artifacts"]["limits"]["max_total_bytes"] == 8
    assert saved["partial_artifacts"]["bytes_hashed"] == len(raw_rollout)
    assert saved["partial_artifacts"]["files"] == [
        {
            "path": "rollout_trajectories.jsonl",
            "size_bytes": len(raw_rollout),
            "sha256": hashlib.sha256(raw_rollout).hexdigest(),
        }
    ]
    assert saved["partial_artifacts"]["unverified"] == [
        {
            "path": "training_summary.json",
            "presence": "present",
            "reason": "max_total_bytes_exceeded",
        }
    ]
    assert (tmp_path / "rollout_trajectories.jsonl").read_bytes() == raw_rollout
    assert (tmp_path / "training_summary.json").read_bytes() == raw_summary


@pytest.mark.skipif(os.name == "nt", reason="Windows fallback cannot verify file hashes")
def test_failed_status_bounds_partial_artifact_inventory_duration(
    monkeypatch, tmp_path
):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    raw_rollout = b'{"private_marker":"slow-rollout-fixture"}\n'
    (tmp_path / "rollout_trajectories.jsonl").write_bytes(raw_rollout)
    expired = False
    real_open = os.open

    def expire_after_artifact_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal expired
        if dir_fd is None:
            descriptor = real_open(path, flags, mode)
        else:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path).name == "rollout_trajectories.jsonl":
            expired = True
        return descriptor

    monkeypatch.setattr(grpo_provenance.os, "open", expire_after_artifact_open)
    monkeypatch.setattr(grpo_provenance, "monotonic", lambda: 10.0 if expired else 0.0)
    saved = persist_status(
        manifest_path,
        manifest,
        "interrupted",
        error_type="KeyboardInterrupt",
    )

    assert saved["partial_artifacts"]["limits"]["max_duration_seconds"] == (
        grpo_provenance.MAX_PARTIAL_ARTIFACT_SECONDS
    )
    assert saved["partial_artifacts"]["bytes_hashed"] == 0
    assert saved["partial_artifacts"]["files"] == []
    assert saved["partial_artifacts"]["unverified"][0]["reason"] == (
        "time_limit_exceeded"
    )
    assert "slow-rollout-fixture" not in json.dumps(saved)
    assert (tmp_path / "rollout_trajectories.jsonl").read_bytes() == raw_rollout


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific stable-handle fallback")
def test_windows_partial_artifacts_are_reported_unverified(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    raw_rollout = b'{"private_marker":"windows-rollout-secret"}\n'
    raw_summary = b'{"private_marker":"windows-summary-secret"}\n'
    (tmp_path / "rollout_trajectories.jsonl").write_bytes(raw_rollout)
    (tmp_path / "training_summary.json").write_bytes(raw_summary)

    inventory = grpo_provenance.partial_artifact_inventory(tmp_path)
    assert inventory["inventory_status"] == "unavailable"
    assert inventory["files"] == []
    assert {item["path"] for item in inventory["unverified"]} == {
        "rollout_trajectories.jsonl",
        "training_summary.json",
    }
    assert all(
        item["reason"] == "stable_directory_handle_unavailable"
        and item["presence"] == "unknown"
        for item in inventory["unverified"]
    )
    with pytest.raises(OSError, match="stable directory handle"):
        persist_status(
            manifest_path,
            manifest,
            "interrupted",
            error_type="KeyboardInterrupt",
        )
    serialized = manifest_path.read_text(encoding="utf-8")
    assert json.loads(serialized)["status"] == "running"
    assert "windows-rollout-secret" not in serialized
    assert "windows-summary-secret" not in serialized
    assert (tmp_path / "rollout_trajectories.jsonl").read_bytes() == raw_rollout
    assert (tmp_path / "training_summary.json").read_bytes() == raw_summary


def _record_valid_loader(manifest_path):
    return record_loader_provenance(
        manifest_path,
        model=SimpleNamespace(config=SimpleNamespace(_commit_hash=MODEL_COMMIT)),
        tokenizer=SimpleNamespace(init_kwargs={}),
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
    )


def test_missing_adapter_cannot_be_marked_completed(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    _write_valid_completion_records(tmp_path, manifest["training"])
    manifest = _record_valid_loader(manifest_path)
    with pytest.raises(RuntimeError, match="adapter_config.json"):
        persist_status(manifest_path, manifest, "completed")
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "running"


def test_adapter_config_without_weights_cannot_be_marked_completed(tmp_path):
    manifest_path = tmp_path / MANIFEST_NAME
    manifest = persist_status(manifest_path, _planned_manifest(), "running")
    _write_valid_completion_records(tmp_path, manifest["training"])
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")
    manifest = _record_valid_loader(manifest_path)
    with pytest.raises(RuntimeError, match="adapter model weights"):
        persist_status(manifest_path, manifest, "completed")


@pytest.mark.skipif(os.name == "nt", reason="Live GRPO preflight requires POSIX handles")
def test_training_failure_persists_failed_state_before_optional_ml_imports(
    monkeypatch, tmp_path
):
    from training import grpo

    output_dir = tmp_path / "run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grpo.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            MODEL_COMMIT,
            "--tokenizer-revision",
            TOKENIZER_COMMIT,
            "--sft-checkpoint",
            str(tmp_path / "sft"),
            "--output",
            str(output_dir),
            "--execute-live-chaos",
            "--kube-context",
            "kind-atlasops-test",
        ],
    )
    monkeypatch.setattr(grpo, "validate_sft_parent", lambda *args, **kwargs: _parent())

    def fail_training(args, output_dir):
        assert args.execute_live_chaos is True
        assert args.kube_context == "kind-atlasops-test"
        assert json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))[
            "status"
        ] == "running"
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(grpo, "run_training", fail_training)
    with pytest.raises(RuntimeError, match="fixture failure"):
        grpo.main()
    saved = json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["failure"]["error_type"] == "RuntimeError"
    assert saved["checkpoint"] is None
    assert saved["training"]["live_execution"] == {
        "execute_live_chaos": True,
        "kube_context": "kind-atlasops-test",
    }


def test_cli_rejects_unavailable_failure_persistence_before_output(
    monkeypatch, tmp_path
):
    from training import grpo

    output_dir = tmp_path / "run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grpo.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            MODEL_COMMIT,
            "--tokenizer-revision",
            TOKENIZER_COMMIT,
            "--sft-checkpoint",
            str(tmp_path / "sft"),
            "--output",
            str(output_dir),
            "--execute-live-chaos",
            "--kube-context",
            "kind-atlasops-test",
        ],
    )
    monkeypatch.setattr(
        grpo_provenance, "_supports_stable_directory_handles", lambda: False
    )
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: pytest.fail("SFT parent read after failed preflight"),
    )
    monkeypatch.setattr(
        grpo,
        "run_training",
        lambda *_args, **_kwargs: pytest.fail("training started after failed preflight"),
    )

    with pytest.raises(RuntimeError, match="stable directory-handle operations"):
        grpo.main()
    assert not output_dir.exists()


def test_main_records_optuna_effective_hyperparameters_separately(
    monkeypatch, tmp_path
):
    from training import grpo

    monkeypatch.setattr(grpo, "require_stable_failure_persistence", lambda: None)
    output_dir = tmp_path / "run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grpo.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            MODEL_COMMIT,
            "--tokenizer-revision",
            TOKENIZER_COMMIT,
            "--sft-checkpoint",
            str(tmp_path / "sft"),
            "--output",
            str(output_dir),
            "--lr",
            "0.000001",
            "--beta",
            "0.04",
            "--num-generations",
            "8",
            "--optuna",
            "1",
            "--execute-live-chaos",
            "--kube-context",
            "kind-atlasops-test",
        ],
    )
    monkeypatch.setattr(grpo, "validate_sft_parent", lambda *_args, **_kwargs: _parent())
    monkeypatch.setattr(grpo, "require_stable_failure_persistence", lambda: None)
    effective = {
        "tiers": ["cascade", "multi_fault", "named_replays"],
        "learning_rate": 2e-6,
        "beta": 0.02,
        "batch_size": 1,
        "num_generations": 4,
        "max_steps": 200,
        "gradient_accumulation_steps": 4,
        "optuna_trials": 1,
        "max_completion_length": 512,
    }

    def complete_fake_training(args, run_dir):
        run_manifest = json.loads((run_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        assert run_manifest["status"] == "running"
        (run_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
        (run_dir / "adapter_model.safetensors").write_bytes(b"adapter-fixture")
        (run_dir / "rollout_trajectories.jsonl").write_text(
            json.dumps({
                "status": "ok",
                "scorable": True,
                "rollout_phase": "final_training",
                "effective_hyperparameters": effective,
                "verification": {
                    "verification_status": "failed",
                    "env_resolved": False,
                    "checks": [{
                        "name": "workload_ready",
                        "target": "default/paymentservice",
                        "required": True,
                        "passed": False,
                        "observed": {"ready_replicas": 1, "desired_replicas": 2},
                    }],
                },
                "settling": {
                    "status": "settled",
                    "stable": True,
                    "verification_status": "failed",
                    "required_stable_observations": 2,
                    "stable_observations": 2,
                },
                "live_execution": {
                    "execute_live_chaos": True,
                    "kube_context": args.kube_context,
                },
            }) + "\n",
            encoding="utf-8",
        )
        summary = _valid_training_summary(run_manifest["training"])
        summary["effective_hyperparameters"] = effective
        summary["hyperparameter_selection"] = "optuna"
        (run_dir / "training_summary.json").write_text(
            json.dumps(summary),
            encoding="utf-8",
        )
        (run_dir / "optuna_best.json").write_text(
            json.dumps({
                "params": {
                    "lr": effective["learning_rate"],
                    "beta": effective["beta"],
                    "num_generations": effective["num_generations"],
                },
                "value": 0.5,
                "live_execution": run_manifest["training"]["live_execution"],
            }),
            encoding="utf-8",
        )
        record_loader_provenance(
            run_dir / MANIFEST_NAME,
            model=SimpleNamespace(config=SimpleNamespace(_commit_hash=MODEL_COMMIT)),
            tokenizer=SimpleNamespace(
                init_kwargs={"_commit_hash": TOKENIZER_COMMIT}
            ),
            model_id="Qwen/Qwen2.5-7B-Instruct",
            model_revision=MODEL_COMMIT,
            tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
            tokenizer_revision=TOKENIZER_COMMIT,
        )
        return {
            "effective_hyperparameters": effective,
            "hyperparameter_selection": "optuna",
        }

    monkeypatch.setattr(grpo, "run_training", complete_fake_training)
    grpo.main()

    completed = json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    training = completed["training"]
    assert completed["status"] == "completed"
    assert training["hyperparameter_selection"] == "optuna"
    assert training["requested_hyperparameters"]["learning_rate"] == 1e-6
    assert training["requested_hyperparameters"]["beta"] == 0.04
    assert training["requested_hyperparameters"]["num_generations"] == 8
    assert training["effective_hyperparameters"] == effective


@pytest.mark.parametrize(
    ("live_args", "error"),
    [
        (["--kube-context", "kind-atlasops-test"], "--execute-live-chaos"),
        (["--execute-live-chaos"], "--kube-context"),
    ],
)
def test_cli_rejects_incomplete_live_execution_before_output_or_work(
    monkeypatch, tmp_path, capsys, live_args, error
):
    from training import grpo

    output_dir = tmp_path / "run"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grpo.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            MODEL_COMMIT,
            "--tokenizer-revision",
            TOKENIZER_COMMIT,
            "--sft-checkpoint",
            str(tmp_path / "sft"),
            "--output",
            str(output_dir),
            *live_args,
        ],
    )
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: pytest.fail("SFT provenance read before validation"),
    )
    monkeypatch.setattr(
        grpo,
        "build_direct_action_prompts",
        lambda *_args, **_kwargs: pytest.fail("prompts built before validation"),
    )
    monkeypatch.setattr(
        grpo, "run_training", lambda *_args, **_kwargs: pytest.fail("training started")
    )
    monkeypatch.setattr(
        grpo.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("subprocess invoked before validation"),
    )

    with pytest.raises(SystemExit) as exc:
        grpo.main()

    assert exc.value.code == 2
    assert error in capsys.readouterr().err
    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("execute_live_chaos", "kube_context", "error_type"),
    [
        (False, "kind-atlasops-test", PermissionError),
        (True, None, ValueError),
    ],
)
def test_direct_run_training_validates_before_model_load_or_output(
    monkeypatch, tmp_path, execute_live_chaos, kube_context, error_type
):
    from training import grpo

    output_dir = tmp_path / "run"
    args = Namespace(
        execute_live_chaos=execute_live_chaos,
        kube_context=kube_context,
    )
    monkeypatch.setattr(
        grpo,
        "load_model_and_tokenizer",
        lambda *_args, **_kwargs: pytest.fail("model load before validation"),
    )

    with pytest.raises(error_type):
        grpo.run_training(args, output_dir)

    assert not output_dir.exists()


def test_direct_optuna_search_requires_live_execution_before_output(tmp_path):
    from training import grpo

    output_dir = tmp_path / "optuna"
    with pytest.raises(PermissionError, match="--execute-live-chaos"):
        grpo.run_optuna_search(
            "Qwen/Qwen2.5-7B-Instruct",
            ["single_fault"],
            output_dir,
            MODEL_COMMIT,
            "Qwen/Qwen2.5-7B-Instruct",
            TOKENIZER_COMMIT,
            tmp_path / "sft",
        )
    assert not output_dir.exists()


def test_training_rejects_sft_parent_changed_after_run_was_planned(tmp_path):
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    manifest_path = output_dir / MANIFEST_NAME
    planned = persist_status(manifest_path, _planned_manifest(), "planned")
    persist_status(manifest_path, planned, "running")

    with pytest.raises(ValueError, match="SFT parent changed after the run was planned"):
        mark_training_started(
            manifest_path,
            model_id="Qwen/Qwen2.5-7B-Instruct",
            model_revision=MODEL_COMMIT,
            tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
            tokenizer_revision=TOKENIZER_COMMIT,
            sft_parent={**_parent(), "manifest_sha256": "1" * 64},
        )

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["status"] == "running"
    assert "execution_started_at" not in saved["training"]


def test_model_loader_requires_live_opt_in_before_tokenizer_load(monkeypatch, tmp_path):
    from training import grpo

    calls = []

    class Tokenizer:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            calls.append("tokenizer")
            pytest.fail("tokenizer loaded before live execution validation")

    monkeypatch.setattr(grpo, "AutoTokenizer", Tokenizer)
    with pytest.raises(PermissionError, match="--execute-live-chaos"):
        grpo.load_model_and_tokenizer(
            "Qwen/Qwen2.5-7B-Instruct",
            model_revision=MODEL_COMMIT,
            tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
            tokenizer_revision=TOKENIZER_COMMIT,
            sft_checkpoint=tmp_path / "sft",
        )
    assert calls == []


def test_run_training_passes_live_context_to_optuna(monkeypatch, tmp_path):
    from training import grpo

    monkeypatch.setattr(grpo, "_HAS_TORCH_RL", False)
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: _parent(),
    )
    observed = {}
    events = []
    monkeypatch.setattr(
        grpo,
        "_load_training_dependencies",
        lambda: events.append(("load_dependencies", None)),
    )
    args = Namespace(
        execute_live_chaos=True,
        kube_context=" kind-atlasops-test ",
        tiers="single_fault",
        seed=917018,
        optuna=1,
        model="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer=None,
        tokenizer_revision=TOKENIZER_COMMIT,
        sft_checkpoint=tmp_path / "sft",
        lr=1e-6,
        beta=0.04,
        batch_size=1,
        num_generations=8,
        max_steps=1,
        grad_accum=4,
        max_compl_len=256,
        enable_p1_approval=False,
    )
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    manifest_path = output_dir / MANIFEST_NAME
    planned = persist_status(
        manifest_path,
        _planned_manifest(
            seed=917018,
            optuna_trials=1,
            include_operator_approval=True,
        ),
        "planned",
    )
    persist_status(manifest_path, planned, "running")
    manifest_before = manifest_path.read_bytes()
    if os.name == "nt":
        with pytest.raises(RuntimeError, match="stable directory-handle operations"):
            grpo.run_training(args, output_dir)
        assert events == []
        assert manifest_path.read_bytes() == manifest_before
        assert list(output_dir.iterdir()) == [manifest_path]
        return
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            distributed=None,
            manual_seed=lambda seed: events.append(("torch_seed", seed)),
        ),
    )

    class StopAtOptuna(RuntimeError):
        pass

    def stop_at_optuna(*_args, **kwargs):
        observed.update(kwargs)
        events.append(("optuna", kwargs.get("seed")))
        raise StopAtOptuna

    monkeypatch.setattr(grpo, "run_optuna_search", stop_at_optuna)
    with pytest.raises(StopAtOptuna):
        grpo.run_training(args, output_dir)

    assert observed["execute_live_chaos"] is True
    assert observed["kube_context"] == "kind-atlasops-test"
    assert observed["seed"] == 917018
    assert events == [
        ("load_dependencies", None),
        ("torch_seed", 917018),
        ("optuna", 917018),
    ]
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["failure"]["error_type"] == "StopAtOptuna"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"seed": 917018, "optuna": 1}, id="seed-and-optuna-count"),
        pytest.param({"tiers": "multi_fault"}, id="tiers"),
        pytest.param({"lr": 2e-6}, id="learning-rate"),
        pytest.param({"batch_size": True}, id="boolean-is-not-integer"),
        pytest.param({"max_steps": 2}, id="max-steps"),
        pytest.param({"max_compl_len": 128}, id="generation-config"),
        pytest.param({"kube_context": "kind-atlasops-other"}, id="live-context"),
        pytest.param({"enable_p1_approval": True}, id="approval-profile"),
        pytest.param({"model": "other-org/model"}, id="model-id"),
        pytest.param({"model_revision": "a" * 40}, id="model-revision"),
        pytest.param({"tokenizer": "other-org/tokenizer"}, id="tokenizer-id"),
        pytest.param({"tokenizer_revision": "b" * 40}, id="tokenizer-revision"),
        pytest.param({"sft_parent": "changed"}, id="sft-parent"),
    ],
)
def test_direct_run_training_rejects_unplanned_request_without_mutation(
    monkeypatch, tmp_path, overrides
):
    from training import grpo

    args_overrides = dict(overrides)
    parent = _parent()
    if args_overrides.pop("sft_parent", None):
        parent = {**parent, "manifest_sha256": "1" * 64}
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: parent,
    )
    monkeypatch.setattr(
        grpo,
        "_load_training_dependencies",
        lambda: pytest.fail("training imports before plan validation"),
    )
    monkeypatch.setattr(
        grpo,
        "run_optuna_search",
        lambda *_args, **_kwargs: pytest.fail("Optuna started before plan validation"),
    )
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    manifest_path = output_dir / MANIFEST_NAME
    planned = persist_status(
        manifest_path,
        _planned_manifest(include_operator_approval=True),
        "planned",
    )
    persist_status(manifest_path, planned, "running")
    before = manifest_path.read_bytes()

    with pytest.raises(ValueError, match="persisted G9 training plan"):
        grpo.run_training(_training_args(tmp_path, **args_overrides), output_dir)

    assert manifest_path.read_bytes() == before
    assert list(output_dir.iterdir()) == [manifest_path]


def test_nondefault_seed_reaches_final_grpo_config_without_optuna(
    monkeypatch, tmp_path
):
    from types import ModuleType, SimpleNamespace

    from training import grpo

    observed = {}
    monkeypatch.setattr(grpo, "_load_training_dependencies", lambda: None)
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: _parent(),
    )
    monkeypatch.setattr(
        grpo,
        "load_model_and_tokenizer",
        lambda *_args, **_kwargs: (object(), object()),
    )
    monkeypatch.setattr(
        grpo,
        "OnlineRewardFunction",
        lambda *_args, **_kwargs: object(),
    )
    datasets = ModuleType("datasets")

    class Dataset:
        @staticmethod
        def from_list(rows):
            return rows

    datasets.Dataset = Dataset
    monkeypatch.setitem(sys.modules, "datasets", datasets)

    def grpo_config(**kwargs):
        observed["config_seed"] = kwargs["seed"]
        return kwargs

    class StopBeforeTraining(RuntimeError):
        pass

    class FakeTrainer:
        def __init__(self, **_kwargs):
            pass

        def train(self, *, resume_from_checkpoint):
            observed["resume_from_checkpoint"] = resume_from_checkpoint
            raise StopBeforeTraining("training intentionally not started")

    monkeypatch.setattr(grpo, "GRPOConfig", grpo_config)
    monkeypatch.setattr(grpo, "GRPOTrainer", FakeTrainer)
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    manifest_path = output_dir / MANIFEST_NAME
    planned = persist_status(
        manifest_path,
        _planned_manifest(seed=917018, include_operator_approval=True),
        "planned",
    )
    persist_status(manifest_path, planned, "running")
    manifest_before = manifest_path.read_bytes()
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            distributed=None,
            manual_seed=lambda seed: observed.update(torch_seed=seed),
        ),
    )
    args = Namespace(
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
        tiers="single_fault",
        seed=917018,
        optuna=0,
        model="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer=None,
        tokenizer_revision=TOKENIZER_COMMIT,
        sft_checkpoint=tmp_path / "sft",
        enable_p1_approval=False,
        lr=1e-6,
        beta=0.04,
        batch_size=1,
        num_generations=8,
        max_steps=1,
        grad_accum=4,
        max_compl_len=256,
    )

    if os.name == "nt":
        with pytest.raises(RuntimeError, match="stable directory-handle operations"):
            grpo.run_training(args, output_dir)
        assert observed == {}
        assert manifest_path.read_bytes() == manifest_before
        assert list(output_dir.iterdir()) == [manifest_path]
        return

    with pytest.raises(StopBeforeTraining, match="training intentionally not started"):
        grpo.run_training(args, output_dir)

    assert observed == {
        "torch_seed": 917018,
        "config_seed": 917018,
        "resume_from_checkpoint": None,
    }


def test_direct_run_training_keyboard_interrupt_persists_interrupted_state(
    monkeypatch, tmp_path
):
    from training import grpo

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: _parent(),
    )
    monkeypatch.setattr(
        grpo,
        "_load_training_dependencies",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    manifest_path = output_dir / MANIFEST_NAME
    planned = persist_status(
        manifest_path,
        _planned_manifest(include_operator_approval=True),
        "planned",
    )
    persist_status(manifest_path, planned, "running")
    manifest_before = manifest_path.read_bytes()
    args = Namespace(
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
        tiers="single_fault",
        seed=42,
        optuna=0,
        model="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer=None,
        tokenizer_revision=TOKENIZER_COMMIT,
        sft_checkpoint=tmp_path / "sft",
        enable_p1_approval=False,
        lr=1e-6,
        beta=0.04,
        batch_size=1,
        num_generations=8,
        max_steps=1,
        grad_accum=4,
        max_compl_len=256,
    )

    if os.name == "nt":
        with pytest.raises(RuntimeError, match="stable directory-handle operations"):
            grpo.run_training(args, output_dir)
        assert manifest_path.read_bytes() == manifest_before
        assert list(output_dir.iterdir()) == [manifest_path]
        return

    with pytest.raises(KeyboardInterrupt):
        grpo.run_training(args, output_dir)

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["status"] == "interrupted"
    assert saved["failure"]["error_type"] == "KeyboardInterrupt"


def test_run_training_rechecks_distributed_state_after_lazy_torch_import(
    monkeypatch, tmp_path
):
    from training import grpo

    monkeypatch.delitem(sys.modules, "torch", raising=False)
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: _parent(),
    )
    events = []
    distributed = SimpleNamespace(
        is_available=lambda: True,
        is_initialized=lambda: True,
        get_world_size=lambda: 2,
    )
    torch = SimpleNamespace(
        distributed=distributed,
        manual_seed=lambda _seed: events.append("manual_seed"),
    )
    monkeypatch.setattr(
        grpo,
        "_load_training_dependencies",
        lambda: monkeypatch.setitem(sys.modules, "torch", torch),
    )
    monkeypatch.setattr(
        grpo,
        "run_optuna_search",
        lambda *_args, **_kwargs: pytest.fail("Optuna ran before distributed recheck"),
    )
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    manifest_path = output_dir / MANIFEST_NAME
    planned = persist_status(
        manifest_path,
        _planned_manifest(
            seed=917018,
            optuna_trials=1,
            include_operator_approval=True,
        ),
        "planned",
    )
    persist_status(manifest_path, planned, "running")
    manifest_before = manifest_path.read_bytes()
    args = Namespace(
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
        tiers="single_fault",
        seed=917018,
        optuna=1,
        model="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer=None,
        tokenizer_revision=TOKENIZER_COMMIT,
        sft_checkpoint=tmp_path / "sft",
        enable_p1_approval=False,
        lr=1e-6,
        beta=0.04,
        batch_size=1,
        num_generations=8,
        max_steps=1,
        grad_accum=4,
        max_compl_len=256,
    )

    if os.name == "nt":
        with pytest.raises(RuntimeError, match="stable directory-handle operations"):
            grpo.run_training(args, output_dir)
        assert events == []
        assert manifest_path.read_bytes() == manifest_before
        assert list(output_dir.iterdir()) == [manifest_path]
        return

    with pytest.raises(RuntimeError, match="G9 requires a single training process"):
        grpo.run_training(args, output_dir)

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["failure"]["error_type"] == "RuntimeError"
    assert events == []


def test_optuna_rollouts_receive_and_record_selected_context(monkeypatch, tmp_path):
    from types import ModuleType, SimpleNamespace

    from training import grpo

    monkeypatch.setattr(grpo, "require_stable_failure_persistence", lambda: None)
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: _parent(),
    )
    observed = {}
    monkeypatch.setattr(grpo, "_load_training_dependencies", lambda: None)

    class Trial:
        number = 0

        def suggest_float(self, name, *_args, **_kwargs):
            return {"lr": 1e-6, "beta": 0.01}[name]

        def suggest_categorical(self, _name, choices):
            return choices[0]

    class Study:
        def __init__(self):
            self.best_params = {
                "lr": 1e-6,
                "beta": 0.01,
                "num_generations": 4,
            }
            self.best_value = 0.5

        def optimize(self, objective, *, n_trials):
            assert n_trials == 1
            objective(Trial())

    class Sampler:
        def __init__(self, *, seed):
            assert seed == 42

    optuna = ModuleType("optuna")
    optuna.logging = SimpleNamespace(
        WARNING="warning",
        set_verbosity=lambda _level: None,
    )
    optuna.Trial = Trial
    optuna.samplers = SimpleNamespace(TPESampler=Sampler)
    optuna.create_study = lambda **_kwargs: Study()
    monkeypatch.setitem(sys.modules, "optuna", optuna)

    datasets = ModuleType("datasets")

    class Dataset:
        @staticmethod
        def from_list(rows):
            return rows

    datasets.Dataset = Dataset
    monkeypatch.setitem(sys.modules, "datasets", datasets)

    class FakeRewardFunction:
        def __init__(
            self,
            _tiers,
            *,
            rollout_log_path,
            rollout_phase,
            trial_number,
            effective_hyperparameters,
            **kwargs,
        ):
            observed["reward_options"] = kwargs
            observed["rollout_log_path"] = rollout_log_path
            observed["rollout_phase"] = rollout_phase
            observed["trial_number"] = trial_number
            observed["effective_hyperparameters"] = effective_hyperparameters

    class FakeTrainer:
        def __init__(self, **_kwargs):
            self.state = SimpleNamespace(log_history=[{"rewards/mean": 0.5}])

        def train(self, *, resume_from_checkpoint):
            observed["resume_from_checkpoint"] = resume_from_checkpoint

    def fake_load_model(*_args, **kwargs):
        observed["model_options"] = kwargs
        return object(), object()

    monkeypatch.setattr(grpo, "OnlineRewardFunction", FakeRewardFunction)
    monkeypatch.setattr(grpo, "load_model_and_tokenizer", fake_load_model)

    def fake_grpo_config(**kwargs):
        observed["grpo_config_seed"] = kwargs["seed"]
        return kwargs

    monkeypatch.setattr(grpo, "GRPOConfig", fake_grpo_config)
    monkeypatch.setattr(grpo, "GRPOTrainer", FakeTrainer)
    output_dir = tmp_path / "optuna"
    output_dir.mkdir()
    manifest_path = output_dir / MANIFEST_NAME
    planned = persist_status(
        manifest_path,
        _planned_manifest(seed=917018, optuna_trials=1, include_operator_approval=True),
        "planned",
    )
    persist_status(manifest_path, planned, "running")
    mark_training_started(
        manifest_path,
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
        sft_parent=_parent(),
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            distributed=None,
            manual_seed=lambda seed: observed.update(torch_seed=seed),
        ),
    )
    result = grpo.run_optuna_search(
        "Qwen/Qwen2.5-7B-Instruct",
        ["single_fault"],
        output_dir,
        MODEL_COMMIT,
        "Qwen/Qwen2.5-7B-Instruct",
        TOKENIZER_COMMIT,
        tmp_path / "sft",
        n_trials=1,
        execute_live_chaos=True,
        kube_context=" kind-atlasops-test ",
        operator_approval_enabled=False,
        seed=917018,
    )

    expected_live_execution = {
        "execute_live_chaos": True,
        "kube_context": "kind-atlasops-test",
    }
    assert result == {
        "lr": 1e-6,
        "beta": 0.01,
        "num_generations": 4,
    }
    assert observed["torch_seed"] == 917018
    assert observed["resume_from_checkpoint"] is None
    assert observed["grpo_config_seed"] == 917018
    assert observed["reward_options"] == {
        **expected_live_execution,
        "operator_approval_enabled": False,
    }
    assert {
        key: observed["model_options"][key]
        for key in expected_live_execution
    } == expected_live_execution
    assert observed["rollout_log_path"] == (
        output_dir / "optuna_trials" / "trial_0" / "rollout_trajectories.jsonl"
    )
    assert observed["rollout_phase"] == "optuna_trial"
    assert observed["trial_number"] == 0
    assert observed["effective_hyperparameters"] == {
        "tiers": ["single_fault"],
        "learning_rate": 1e-6,
        "beta": 0.01,
        "batch_size": 1,
        "num_generations": 4,
        "max_steps": 10,
        "gradient_accumulation_steps": 1,
        "max_completion_length": 256,
    }
    assert json.loads(
        (output_dir / "optuna_best.json").read_text(encoding="utf-8")
    )["live_execution"] == expected_live_execution


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"seed": 917018, "n_trials": 1}, id="seed-and-trial-count"),
        pytest.param({"seed": 917018}, id="seed"),
        pytest.param({"n_trials": 1}, id="trial-count"),
        pytest.param({"tiers": ["multi_fault"]}, id="tiers"),
        pytest.param({"kube_context": "kind-atlasops-other"}, id="live-context"),
        pytest.param({"operator_approval_enabled": True}, id="approval-profile"),
        pytest.param({"model_id": "other-org/model"}, id="model-id"),
        pytest.param({"model_revision": "a" * 40}, id="model-revision"),
        pytest.param({"tokenizer_id": "other-org/tokenizer"}, id="tokenizer-id"),
        pytest.param({"tokenizer_revision": "b" * 40}, id="tokenizer-revision"),
        pytest.param({"sft_parent": "changed"}, id="sft-parent"),
    ],
)
def test_direct_optuna_rejects_unplanned_request_without_mutation(
    monkeypatch, tmp_path, overrides
):
    from training import grpo

    parent = _parent()
    if "sft_parent" in overrides:
        parent = {**parent, "manifest_sha256": "1" * 64}
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: parent,
    )
    monkeypatch.setattr(
        grpo,
        "_load_training_dependencies",
        lambda: pytest.fail("training imports before Optuna plan validation"),
    )
    output_dir = tmp_path / "optuna"
    output_dir.mkdir()
    manifest_path = output_dir / MANIFEST_NAME
    planned = persist_status(
        manifest_path,
        _planned_manifest(include_operator_approval=True),
        "planned",
    )
    persist_status(manifest_path, planned, "running")
    mark_training_started(
        manifest_path,
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
        sft_parent=_parent(),
    )
    before = manifest_path.read_bytes()
    arguments = {
        "model_id": "Qwen/Qwen2.5-7B-Instruct",
        "model_revision": MODEL_COMMIT,
        "tokenizer_id": "Qwen/Qwen2.5-7B-Instruct",
        "tokenizer_revision": TOKENIZER_COMMIT,
        "tiers": ["single_fault"],
        "n_trials": 0,
        "execute_live_chaos": True,
        "kube_context": "kind-atlasops-test",
        "operator_approval_enabled": False,
        "seed": 42,
    }
    arguments.update(overrides)
    arguments.pop("sft_parent", None)

    with pytest.raises(ValueError, match="persisted G9 training plan"):
        grpo.run_optuna_search(
            arguments.pop("model_id"),
            arguments.pop("tiers"),
            output_dir,
            arguments.pop("model_revision"),
            arguments.pop("tokenizer_id"),
            arguments.pop("tokenizer_revision"),
            tmp_path / "sft",
            **arguments,
        )

    assert manifest_path.read_bytes() == before
    assert list(output_dir.iterdir()) == [manifest_path]


@pytest.mark.parametrize(
    ("exception_type", "expected_status"),
    [(RuntimeError, "failed"), (KeyboardInterrupt, "interrupted")],
)
@pytest.mark.skipif(
    os.name == "nt",
    reason="Terminal Optuna persistence requires POSIX stable directory handles",
)
def test_direct_optuna_failure_persists_terminal_state(
    monkeypatch, tmp_path, exception_type, expected_status
):
    from types import ModuleType, SimpleNamespace

    from training import grpo

    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: _parent(),
    )
    monkeypatch.setattr(grpo, "_load_training_dependencies", lambda: None)

    class Study:
        def optimize(self, *_args, **_kwargs):
            if exception_type is KeyboardInterrupt:
                raise KeyboardInterrupt
            raise RuntimeError("original Optuna failure")

    optuna = ModuleType("optuna")
    optuna.logging = SimpleNamespace(
        WARNING="warning",
        set_verbosity=lambda _level: None,
    )
    optuna.Trial = object
    optuna.samplers = SimpleNamespace(TPESampler=lambda **_kwargs: object())
    optuna.create_study = lambda **_kwargs: Study()
    monkeypatch.setitem(sys.modules, "optuna", optuna)

    output_dir = tmp_path / "optuna"
    output_dir.mkdir()
    manifest_path = output_dir / MANIFEST_NAME
    planned = persist_status(
        manifest_path,
        _planned_manifest(seed=917018, optuna_trials=1, include_operator_approval=True),
        "planned",
    )
    persist_status(manifest_path, planned, "running")
    mark_training_started(
        manifest_path,
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
        sft_parent=_parent(),
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(distributed=None, manual_seed=lambda _seed: None),
    )

    if exception_type is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            grpo.run_optuna_search(
                "Qwen/Qwen2.5-7B-Instruct",
                ["single_fault"],
                output_dir,
                MODEL_COMMIT,
                "Qwen/Qwen2.5-7B-Instruct",
                TOKENIZER_COMMIT,
                tmp_path / "sft",
                n_trials=1,
                execute_live_chaos=True,
                kube_context="kind-atlasops-test",
                operator_approval_enabled=False,
                seed=917018,
            )
    else:
        with pytest.raises(RuntimeError, match="original Optuna failure"):
            grpo.run_optuna_search(
                "Qwen/Qwen2.5-7B-Instruct",
                ["single_fault"],
                output_dir,
                MODEL_COMMIT,
                "Qwen/Qwen2.5-7B-Instruct",
                TOKENIZER_COMMIT,
                tmp_path / "sft",
                n_trials=1,
                execute_live_chaos=True,
                kube_context="kind-atlasops-test",
                operator_approval_enabled=False,
                seed=917018,
            )

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["status"] == expected_status
    assert saved["failure"]["error_type"] == exception_type.__name__


@pytest.mark.skipif(
    os.name != "nt",
    reason="Windows-specific G9 capability refusal",
)
def test_direct_optuna_rejects_unsupported_failure_persistence_before_mutation(
    monkeypatch, tmp_path
):
    from training import grpo

    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: _parent(),
    )
    monkeypatch.setattr(
        grpo,
        "_load_training_dependencies",
        lambda: pytest.fail("dependencies loaded after unsupported-host refusal"),
    )
    output_dir = tmp_path / "optuna"
    output_dir.mkdir()
    manifest_path = output_dir / MANIFEST_NAME
    planned = persist_status(
        manifest_path,
        _planned_manifest(seed=917018, optuna_trials=1, include_operator_approval=True),
        "planned",
    )
    persist_status(manifest_path, planned, "running")
    mark_training_started(
        manifest_path,
        model_id="Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
        sft_parent=_parent(),
    )
    before = manifest_path.read_bytes()

    with pytest.raises(RuntimeError, match="stable directory-handle operations"):
        grpo.run_optuna_search(
            "Qwen/Qwen2.5-7B-Instruct",
            ["single_fault"],
            output_dir,
            MODEL_COMMIT,
            "Qwen/Qwen2.5-7B-Instruct",
            TOKENIZER_COMMIT,
            tmp_path / "sft",
            n_trials=1,
            execute_live_chaos=True,
            kube_context="kind-atlasops-test",
            operator_approval_enabled=False,
            seed=917018,
        )

    assert manifest_path.read_bytes() == before
    assert list(output_dir.iterdir()) == [manifest_path]


def test_grpo_parent_requires_byte_valid_sft_checkpoint(tmp_path):
    checkpoint, parent = _sft_parent_record(tmp_path)
    assert parent["checkpoint_tree_sha256"]
    assert parent["train_split_sha256"] == canonical_json_sha256(list(TRAIN_SPLIT))
    with pytest.raises(ValueError, match="must match"):
        validate_sft_parent(
            checkpoint,
            model_id="another-model",
            model_revision=MODEL_COMMIT,
            tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
            tokenizer_revision=TOKENIZER_COMMIT,
        )
    with pytest.raises(ValueError, match="must match"):
        validate_sft_parent(
            checkpoint,
            model_id="Qwen/Qwen2.5-7B-Instruct",
            model_revision="a" * 40,
            tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
            tokenizer_revision=TOKENIZER_COMMIT,
        )
    with pytest.raises(ValueError, match="must match"):
        validate_sft_parent(
            checkpoint,
            model_id="Qwen/Qwen2.5-7B-Instruct",
            model_revision=MODEL_COMMIT,
            tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
            tokenizer_revision="b" * 40,
        )
    (checkpoint / "adapter_model.safetensors").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_sft_parent(
            checkpoint,
            model_id="Qwen/Qwen2.5-7B-Instruct",
            model_revision=MODEL_COMMIT,
            tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
            tokenizer_revision=TOKENIZER_COMMIT,
        )


def test_pin_enforced_tokenizer_basis_passes_g8_and_g9_parent_validation(tmp_path):
    checkpoint, g9_parent = _sft_parent_record(
        tmp_path,
        tokenizer_revision_basis=TOKENIZER_PIN_ONLY_BASIS,
    )

    from bench.sft_eval import _load_checkpoint_manifest

    g8_manifest, g8_manifest_sha256 = _load_checkpoint_manifest(checkpoint)
    manifest_bytes = (checkpoint / "sft_run_manifest.json").read_bytes()

    assert g8_manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()
    assert g9_parent["manifest_sha256"] == g8_manifest_sha256
    assert g9_parent["checkpoint_tree_sha256"] == (
        g8_manifest["checkpoint"]["tree_sha256"]
    )
    assert g8_manifest["status"] == "completed"
    assert g8_manifest["source"] == {"git_sha": "a" * 40, "git_dirty": False}
    assert g8_manifest["dataset"]["split"] == "train"
    assert g8_manifest["dataset"]["split_scenarios"] == list(TRAIN_SPLIT)
    assert g8_manifest["dataset"]["split_sha256"] == canonical_json_sha256(
        list(TRAIN_SPLIT)
    )
    assert g8_manifest["dataset"]["data_origin"] == "UNVERIFIED"
    assert g8_manifest["dataset"]["synthetic"] is None
    assert g8_manifest["base_model"]["resolved_revision"] == MODEL_COMMIT
    assert g8_manifest["tokenizer"]["requested_revision"] == TOKENIZER_COMMIT
    assert g8_manifest["tokenizer"]["resolved_revision"] == TOKENIZER_COMMIT
    assert g8_manifest["tokenizer"]["resolved_revision_basis"] == (
        TOKENIZER_PIN_ONLY_BASIS
    )
    assert g9_parent["train_corpus_sha256"] == (
        g8_manifest["dataset"]["corpus_sha256_canonical_lf"]
    )
    assert g9_parent["train_split_sha256"] == canonical_json_sha256(
        list(TRAIN_SPLIT)
    )
    # G9 binds the complete manifest bytes; it does not promote this basis to attestation.
    assert set(g9_parent) == {
        "checkpoint_path",
        "manifest_sha256",
        "checkpoint_tree_sha256",
        "training_source_sha",
        "train_corpus_sha256",
        "train_split_sha256",
        "base_model_id",
        "base_model_revision",
        "tokenizer_id",
        "tokenizer_revision",
    }


def test_grpo_loader_trains_from_sft_adapter(monkeypatch, tmp_path):
    from training import grpo

    checkpoint, _ = _sft_parent_record(tmp_path)
    calls = []

    class Tokenizer:
        pad_token = None
        eos_token = "eos"
        init_kwargs = {"_commit_hash": TOKENIZER_COMMIT}

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return cls()

    class BaseModel:
        def __init__(self):
            self.config = SimpleNamespace(_commit_hash=MODEL_COMMIT)

        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            return cls()

    class AdapterModel:
        @staticmethod
        def from_pretrained(base, path, *, is_trainable):
            calls.append((path, is_trainable))
            return AdapterModel()

        def print_trainable_parameters(self):
            pass

    monkeypatch.setattr(grpo, "AutoTokenizer", Tokenizer)
    monkeypatch.setattr(grpo, "AutoModelForCausalLM", BaseModel)
    monkeypatch.setattr(grpo, "PeftModel", AdapterModel)
    monkeypatch.setattr(grpo, "prepare_model_for_kbit_training", lambda model: model)
    monkeypatch.setattr(grpo, "_flash_attn_available", lambda: False)
    grpo.load_model_and_tokenizer(
        "Qwen/Qwen2.5-7B-Instruct",
        model_revision=MODEL_COMMIT,
        tokenizer_id="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
        sft_checkpoint=checkpoint,
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
    )
    assert calls == [(str(checkpoint), True)]
