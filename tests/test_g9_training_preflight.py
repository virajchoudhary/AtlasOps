"""Non-live G9 CLI and model-loader preflight contracts."""

from __future__ import annotations

import builtins
import importlib
import json
import os
import sys
from types import SimpleNamespace

import pytest

from config.splits import TRAIN_SPLIT
from training.sft_provenance import (
    canonical_json_sha256,
    create_run_manifest as create_sft_run_manifest,
    mark_completed as complete_sft_run,
    mark_running as start_sft_run,
    write_manifest_atomic,
)
from training.grpo_provenance import (
    claim_new_output_directory,
    resolve_loader_provenance,
)

MODEL_COMMIT = "f" * 40
TOKENIZER_COMMIT = "e" * 40
MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
TOKENIZER_ID = "test-org/tokenizer"


@pytest.fixture
def allow_legacy_body_for_unit_test(monkeypatch):
    """Test-only opt-in for inspecting downstream preflight and lifecycle behavior."""
    from training import grpo

    monkeypatch.setattr(grpo, "_require_g9_observation_order_protocol", lambda: None)


def _cli_argv(
    output_dir,
    sft_checkpoint,
    *,
    model_id=MODEL_ID,
    model_revision=MODEL_COMMIT,
    tokenizer_id=TOKENIZER_ID,
    tokenizer_revision=TOKENIZER_COMMIT,
):
    return [
        "grpo.py",
        "--model",
        model_id,
        "--model-revision",
        model_revision,
        "--tokenizer",
        tokenizer_id,
        "--tokenizer-revision",
        tokenizer_revision,
        "--batch-size",
        "2",
        "--sft-checkpoint",
        str(sft_checkpoint),
        "--output",
        str(output_dir),
        "--execute-live-chaos",
        "--kube-context",
        "kind-atlasops-test",
    ]


def _parent():
    return {
        "checkpoint_path": "test-sft-checkpoint",
        "manifest_sha256": "c" * 64,
        "checkpoint_tree_sha256": "d" * 64,
        "training_source_sha": "a" * 40,
        "train_corpus_sha256": "b" * 64,
        "train_split_sha256": canonical_json_sha256(list(TRAIN_SPLIT)),
        "base_model_id": MODEL_ID,
        "base_model_revision": MODEL_COMMIT,
        "tokenizer_id": TOKENIZER_ID,
        "tokenizer_revision": TOKENIZER_COMMIT,
    }


def _valid_sft_checkpoint(tmp_path):
    corpus_path = tmp_path / "train.jsonl"
    corpus_path.write_text(
        "".join(
            json.dumps({"scenario_id": scenario_id, "role": "triage"}) + "\n"
            for scenario_id in TRAIN_SPLIT
        ),
        encoding="utf-8",
    )
    checkpoint = tmp_path / "sft"
    checkpoint.mkdir()
    manifest = create_sft_run_manifest(
        corpus_path=corpus_path,
        output_dir=checkpoint,
        base_model=MODEL_ID,
        base_model_revision=MODEL_COMMIT,
        tokenizer=TOKENIZER_ID,
        tokenizer_revision=TOKENIZER_COMMIT,
        role="all",
        hyperparameters={"lora": {
            "r": 16, "alpha": 32, "dropout": 0.05,
            "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
            "bias": "none",
        }},
    )
    manifest = start_sft_run(
        manifest,
        resolved_model_revision=MODEL_COMMIT,
        resolved_tokenizer_revision=TOKENIZER_COMMIT,
        resolved_tokenizer_revision_basis="LOADER_EXPOSED_COMMIT_HASH_MATCH",
    )
    manifest["source"] = {"git_sha": "a" * 40, "git_dirty": False}
    (checkpoint / "adapter_config.json").write_text(json.dumps({
        "peft_type": "LORA", "task_type": "CAUSAL_LM",
        "base_model_name_or_path": MODEL_ID,
        "revision": None, "r": 16, "lora_alpha": 32, "lora_dropout": 0.05,
        "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        "bias": "none",
    }), encoding="utf-8")
    adapter_weights = checkpoint / "adapter_model.safetensors"
    adapter_weights.write_bytes(b"sft-adapter-fixture")
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = complete_sft_run(
        manifest,
        output_dir=checkpoint,
        manifest_path=manifest_path,
        trainer_state={"global_step": 1},
        training_history=[],
    )
    write_manifest_atomic(manifest_path, manifest)
    return checkpoint, adapter_weights


def test_importing_grpo_does_not_load_optional_training_libraries(monkeypatch):
    module_name = "training.grpo"
    module = sys.modules.get(module_name)
    attempted_imports = []
    real_import = builtins.__import__

    def reject_optional_training_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in {
            "datasets",
            "optuna",
            "peft",
            "torch",
            "transformers",
            "trl",
        }:
            attempted_imports.append(name)
            raise AssertionError(f"optional training import occurred: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_optional_training_import)
    if module is None:
        importlib.import_module(module_name)
    else:
        importlib.reload(module)

    assert attempted_imports == []


def test_cli_rejects_mutable_revision_before_parent_output_or_training(
    monkeypatch, tmp_path, capsys, allow_legacy_body_for_unit_test
):
    from training import grpo

    output_dir = tmp_path / "run"
    monkeypatch.setattr(
        sys,
        "argv",
        _cli_argv(
            output_dir,
            tmp_path / "sft",
            model_revision="main",
        ),
    )
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: pytest.fail("SFT parent read before pin validation"),
    )
    monkeypatch.setattr(
        grpo,
        "build_direct_action_prompts",
        lambda *_args, **_kwargs: pytest.fail("prompts built before pin validation"),
    )
    monkeypatch.setattr(
        grpo,
        "run_training",
        lambda *_args, **_kwargs: pytest.fail("training started before pin validation"),
    )
    attempted_imports = []
    real_import = builtins.__import__

    def reject_optional_training_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in {
            "datasets",
            "optuna",
            "peft",
            "torch",
            "transformers",
            "trl",
        }:
            attempted_imports.append(name)
            raise AssertionError(f"optional training import occurred: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_optional_training_import)

    with pytest.raises(SystemExit) as exc:
        grpo.main()

    assert exc.value.code == 2
    assert "full 40-character immutable" in capsys.readouterr().err
    assert not output_dir.exists()
    assert attempted_imports == []


def test_cli_blocks_unreviewed_observation_order_before_admission_or_output(
    monkeypatch, tmp_path, capsys
):
    from training import grpo

    output_dir = tmp_path / "run"
    output_dir.mkdir()
    manifest_path = output_dir / grpo.MANIFEST_NAME
    existing_manifest = b'{"status":"running","training":{"sentinel":"preserve"}}\n'
    manifest_path.write_bytes(existing_manifest)
    monkeypatch.setattr(sys, "argv", _cli_argv(output_dir, tmp_path / "sft"))

    def unexpected_work(*_args, **_kwargs):
        pytest.fail("G9 admission or run state was touched before observation-order refusal")

    for name in (
        "_require_single_writer",
        "validate_grpo_batch_configuration",
        "validate_grpo_model_references",
        "_require_live_execution",
        "require_stable_failure_persistence",
        "validate_new_output_directory",
        "validate_sft_parent",
        "build_direct_action_prompts",
        "create_run_manifest",
        "claim_new_output_directory",
        "run_training",
    ):
        monkeypatch.setattr(grpo, name, unexpected_work)

    with pytest.raises(SystemExit) as exc:
        grpo.main()

    error = capsys.readouterr().err
    assert exc.value.code == 2
    assert "static scenario-catalog data" in error
    assert "before the fault and actual alert are observed" in error
    assert "prospective observation-first protocol revision" in error
    assert manifest_path.read_bytes() == existing_manifest
    assert list(output_dir.iterdir()) == [manifest_path]


def test_cli_rejects_local_tokenizer_path_before_optional_training_imports(
    monkeypatch, tmp_path, capsys, allow_legacy_body_for_unit_test
):
    from training import grpo

    output_dir = tmp_path / "run"
    monkeypatch.setattr(
        sys,
        "argv",
        _cli_argv(
            output_dir,
            tmp_path / "sft",
            tokenizer_id="../local-tokenizer",
        ),
    )
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: pytest.fail("SFT parent read before pin validation"),
    )
    with pytest.raises(SystemExit) as exc:
        grpo.main()

    assert exc.value.code == 2
    assert "Local tokenizer paths are unsupported" in capsys.readouterr().err
    assert not output_dir.exists()


def test_cli_rejects_sft_parent_mismatch_before_output_or_model_loading(
    monkeypatch, tmp_path, allow_legacy_body_for_unit_test
):
    from training import grpo

    monkeypatch.setattr(grpo, "require_stable_failure_persistence", lambda: None)
    output_dir = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", _cli_argv(output_dir, tmp_path / "sft"))

    def reject_parent(*_args, **_kwargs):
        raise ValueError("GRPO base model/tokenizer must match the completed SFT checkpoint")

    monkeypatch.setattr(grpo, "validate_sft_parent", reject_parent)
    monkeypatch.setattr(
        grpo,
        "build_direct_action_prompts",
        lambda *_args, **_kwargs: pytest.fail("prompts built before SFT parent validation"),
    )
    monkeypatch.setattr(
        grpo,
        "_load_training_dependencies",
        lambda: pytest.fail("training libraries imported before SFT parent validation"),
    )

    with pytest.raises(ValueError, match="must match the completed SFT checkpoint"):
        grpo.main()

    assert not output_dir.exists()


def test_cli_rejects_existing_output_before_reading_sft_parent(
    monkeypatch, tmp_path, allow_legacy_body_for_unit_test
):
    from training import grpo

    monkeypatch.setattr(grpo, "require_stable_failure_persistence", lambda: None)
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    monkeypatch.setattr(sys, "argv", _cli_argv(output_dir, tmp_path / "sft"))
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: pytest.fail("SFT parent read before output preflight"),
    )

    with pytest.raises(FileExistsError, match="already exists"):
        grpo.main()

    assert list(output_dir.iterdir()) == []


def test_cli_rejects_redirected_output_before_reading_sft_parent(
    monkeypatch, tmp_path, allow_legacy_body_for_unit_test
):
    from training import grpo

    monkeypatch.setattr(grpo, "require_stable_failure_persistence", lambda: None)
    target = tmp_path / "target"
    target.mkdir()
    output_dir = tmp_path / "redirected"
    try:
        output_dir.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")
    monkeypatch.setattr(sys, "argv", _cli_argv(output_dir, tmp_path / "sft"))
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: pytest.fail("SFT parent read before output preflight"),
    )

    with pytest.raises(ValueError, match="redirect"):
        grpo.main()

    assert list(target.iterdir()) == []


def test_exclusive_output_claim_refuses_a_path_created_after_preflight(tmp_path):
    from training.grpo_provenance import validate_new_output_directory

    output_dir = tmp_path / "run"
    checked = validate_new_output_directory(output_dir)
    output_dir.mkdir()

    with pytest.raises(FileExistsError, match="already exists"):
        claim_new_output_directory(checked)

    assert list(output_dir.iterdir()) == []


def _install_fake_loader(
    monkeypatch,
    *,
    model_commit=MODEL_COMMIT,
    tokenizer_commit=TOKENIZER_COMMIT,
    on_model_load=None,
):
    from training import grpo

    calls = []
    loader_kwargs = {}

    class FakeTokenizer:
        pad_token = "pad"
        eos_token = "eos"
        init_kwargs = {"_commit_hash": tokenizer_commit} if tokenizer_commit is not None else {}

        @classmethod
        def from_pretrained(cls, *_args, **kwargs):
            calls.append("tokenizer")
            loader_kwargs["tokenizer"] = dict(kwargs)
            return cls()

    class FakeModel:
        config = SimpleNamespace(_commit_hash=model_commit)

        def print_trainable_parameters(self):
            pass

        @classmethod
        def from_pretrained(cls, *_args, **kwargs):
            calls.append("model")
            loader_kwargs["model"] = dict(kwargs)
            if on_model_load is not None:
                on_model_load()
            return cls()

    class FakeAdapter:
        @staticmethod
        def from_pretrained(model, *_args, **_kwargs):
            calls.append("adapter")
            return model

    monkeypatch.setattr(grpo, "AutoTokenizer", FakeTokenizer)
    monkeypatch.setattr(grpo, "AutoModelForCausalLM", FakeModel)
    monkeypatch.setattr(grpo, "PeftModel", FakeAdapter)
    monkeypatch.setattr(grpo, "prepare_model_for_kbit_training", lambda model: model)
    monkeypatch.setattr(grpo, "_flash_attn_available", lambda: False)
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: _parent(),
    )
    return grpo, calls, loader_kwargs


@pytest.mark.parametrize("model_commit", [None, "d" * 40])
def test_model_loader_rejects_missing_or_mismatched_model_commit_before_adapter(
    monkeypatch, tmp_path, model_commit
):
    grpo, calls, _loader_kwargs = _install_fake_loader(
        monkeypatch,
        model_commit=model_commit,
    )

    with pytest.raises(ValueError, match="Loaded base model"):
        grpo.load_model_and_tokenizer(
            MODEL_ID,
            model_revision=MODEL_COMMIT,
            tokenizer_id=TOKENIZER_ID,
            tokenizer_revision=TOKENIZER_COMMIT,
            sft_checkpoint=tmp_path / "sft",
            execute_live_chaos=True,
            kube_context="kind-atlasops-test",
        )

    assert calls == ["tokenizer", "model"]


def test_model_loader_rejects_mismatched_tokenizer_commit_before_model_load(monkeypatch, tmp_path):
    grpo, calls, _loader_kwargs = _install_fake_loader(
        monkeypatch,
        tokenizer_commit="d" * 40,
    )

    with pytest.raises(ValueError, match="Loaded tokenizer commit does not match"):
        grpo.load_model_and_tokenizer(
            MODEL_ID,
            model_revision=MODEL_COMMIT,
            tokenizer_id=TOKENIZER_ID,
            tokenizer_revision=TOKENIZER_COMMIT,
            sft_checkpoint=tmp_path / "sft",
            execute_live_chaos=True,
            kube_context="kind-atlasops-test",
        )

    assert calls == ["tokenizer"]


def test_model_loader_records_full_tokenizer_pin_when_loader_exposes_no_hash(monkeypatch, tmp_path):
    grpo, calls, loader_kwargs = _install_fake_loader(
        monkeypatch,
        tokenizer_commit=None,
    )
    model, tokenizer = grpo.load_model_and_tokenizer(
        MODEL_ID,
        model_revision=MODEL_COMMIT,
        tokenizer_id=TOKENIZER_ID,
        tokenizer_revision=TOKENIZER_COMMIT,
        sft_checkpoint=tmp_path / "sft",
        execute_live_chaos=True,
        kube_context="kind-atlasops-test",
    )
    provenance = resolve_loader_provenance(
        model,
        tokenizer,
        model_revision=MODEL_COMMIT,
        tokenizer_revision=TOKENIZER_COMMIT,
    )

    assert calls == ["tokenizer", "model", "adapter"]
    assert provenance["base_model"]["resolved_revision"] == MODEL_COMMIT
    assert provenance["tokenizer"] == {
        "resolved_revision": TOKENIZER_COMMIT,
        "resolved_revision_basis": ("PIN_ENFORCED_BY_LOADER_ARGUMENT/NOT_INDEPENDENTLY_RETURNED"),
    }
    assert loader_kwargs["tokenizer"]["trust_remote_code"] is False
    assert loader_kwargs["model"]["trust_remote_code"] is False


def test_model_loader_revalidates_checkpoint_bytes_after_base_load_before_peft(
    monkeypatch, tmp_path
):
    from training import grpo
    from training.grpo_provenance import validate_sft_parent

    checkpoint, adapter_weights = _valid_sft_checkpoint(tmp_path)
    sft_parent = validate_sft_parent(
        checkpoint,
        model_id=MODEL_ID,
        model_revision=MODEL_COMMIT,
        tokenizer_id=TOKENIZER_ID,
        tokenizer_revision=TOKENIZER_COMMIT,
    )
    run_manifest_path = tmp_path / "grpo_run_manifest.json"
    run_manifest_path.write_text(
        json.dumps({"status": "running", "sft_parent": sft_parent}),
        encoding="utf-8",
    )

    def mutate_checkpoint():
        adapter_weights.write_bytes(adapter_weights.read_bytes() + b"-changed")

    _loader_grpo, calls, _loader_kwargs = _install_fake_loader(
        monkeypatch,
        on_model_load=mutate_checkpoint,
    )
    monkeypatch.setattr(grpo, "validate_sft_parent", validate_sft_parent)
    monkeypatch.setattr(grpo, "record_loader_provenance", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="Checkpoint hash mismatch"):
        grpo.load_model_and_tokenizer(
            MODEL_ID,
            model_revision=MODEL_COMMIT,
            tokenizer_id=TOKENIZER_ID,
            tokenizer_revision=TOKENIZER_COMMIT,
            sft_checkpoint=checkpoint,
            execute_live_chaos=True,
            kube_context="kind-atlasops-test",
            manifest_path=run_manifest_path,
        )

    assert adapter_weights.read_bytes() == b"sft-adapter-fixture-changed"
    assert calls == ["tokenizer", "model"]


@pytest.mark.parametrize("change", ["rank", "trainable_tokens"])
def test_invalid_sft_adapter_configuration_blocks_g9_before_any_loader(
    monkeypatch, tmp_path, change,
):
    from training import grpo
    from training.grpo_provenance import validate_sft_parent
    from training.sft_provenance import checkpoint_inventory

    checkpoint, _ = _valid_sft_checkpoint(tmp_path)
    config_path = checkpoint / "adapter_config.json"
    config = json.loads(config_path.read_text())
    if change == "rank":
        config["r"] = 8
    else:
        config["trainable_token_indices"] = [1]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    manifest_path = checkpoint / "sft_run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["checkpoint"] = checkpoint_inventory(checkpoint, manifest_path)
    write_manifest_atomic(manifest_path, manifest)
    _, calls, _ = _install_fake_loader(monkeypatch)
    monkeypatch.setattr(grpo, "validate_sft_parent", validate_sft_parent)

    with pytest.raises(ValueError, match="adapter"):
        grpo.load_model_and_tokenizer(
            MODEL_ID,
            model_revision=MODEL_COMMIT,
            tokenizer_id=TOKENIZER_ID,
            tokenizer_revision=TOKENIZER_COMMIT,
            sft_checkpoint=checkpoint,
            execute_live_chaos=True,
            kube_context="kind-atlasops-test",
        )
    assert calls == []


def test_model_loader_rejects_changed_sft_parent_after_base_load_before_peft(
    monkeypatch, tmp_path
):
    from training import grpo
    from training.grpo_provenance import validate_sft_parent

    checkpoint, _adapter_weights = _valid_sft_checkpoint(tmp_path)
    run_manifest_path = tmp_path / "grpo_run_manifest.json"
    sft_parent = validate_sft_parent(
        checkpoint,
        model_id=MODEL_ID,
        model_revision=MODEL_COMMIT,
        tokenizer_id=TOKENIZER_ID,
        tokenizer_revision=TOKENIZER_COMMIT,
    )
    run_manifest_path.write_text(
        json.dumps({"status": "running", "sft_parent": sft_parent}),
        encoding="utf-8",
    )
    sft_manifest_path = checkpoint / "sft_run_manifest.json"

    def mutate_sft_parent():
        sft_manifest = json.loads(sft_manifest_path.read_text(encoding="utf-8"))
        sft_manifest["source"]["git_sha"] = "f" * 40
        write_manifest_atomic(sft_manifest_path, sft_manifest)

    _loader_grpo, calls, _loader_kwargs = _install_fake_loader(
        monkeypatch,
        on_model_load=mutate_sft_parent,
    )
    monkeypatch.setattr(grpo, "validate_sft_parent", validate_sft_parent)
    monkeypatch.setattr(grpo, "record_loader_provenance", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="SFT parent changed during base-model loading"):
        grpo.load_model_and_tokenizer(
            MODEL_ID,
            model_revision=MODEL_COMMIT,
            tokenizer_id=TOKENIZER_ID,
            tokenizer_revision=TOKENIZER_COMMIT,
            sft_checkpoint=checkpoint,
            execute_live_chaos=True,
            kube_context="kind-atlasops-test",
            manifest_path=run_manifest_path,
        )

    current_parent = validate_sft_parent(
        checkpoint,
        model_id=MODEL_ID,
        model_revision=MODEL_COMMIT,
        tokenizer_id=TOKENIZER_ID,
        tokenizer_revision=TOKENIZER_COMMIT,
    )
    assert current_parent["training_source_sha"] == "f" * 40
    assert current_parent != sft_parent
    assert calls == ["tokenizer", "model"]


def test_model_loader_rechecks_persisted_sft_parent_plan_before_peft(
    monkeypatch, tmp_path
):
    from training import grpo
    from training.grpo_provenance import validate_sft_parent

    checkpoint, _adapter_weights = _valid_sft_checkpoint(tmp_path)
    sft_parent = validate_sft_parent(
        checkpoint,
        model_id=MODEL_ID,
        model_revision=MODEL_COMMIT,
        tokenizer_id=TOKENIZER_ID,
        tokenizer_revision=TOKENIZER_COMMIT,
    )
    run_manifest_path = tmp_path / "grpo_run_manifest.json"
    run_manifest_path.write_text(
        json.dumps({"status": "running", "sft_parent": sft_parent}),
        encoding="utf-8",
    )

    def mutate_run_plan():
        run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        run_manifest["sft_parent"] = {
            **sft_parent,
            "manifest_sha256": "f" * 64,
        }
        run_manifest_path.write_text(json.dumps(run_manifest), encoding="utf-8")

    _loader_grpo, calls, _loader_kwargs = _install_fake_loader(
        monkeypatch,
        on_model_load=mutate_run_plan,
    )
    monkeypatch.setattr(grpo, "validate_sft_parent", validate_sft_parent)
    monkeypatch.setattr(grpo, "record_loader_provenance", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="GRPO SFT parent changed after the run was planned"):
        grpo.load_model_and_tokenizer(
            MODEL_ID,
            model_revision=MODEL_COMMIT,
            tokenizer_id=TOKENIZER_ID,
            tokenizer_revision=TOKENIZER_COMMIT,
            sft_checkpoint=checkpoint,
            execute_live_chaos=True,
            kube_context="kind-atlasops-test",
            manifest_path=run_manifest_path,
        )

    assert calls == ["tokenizer", "model"]


def test_interrupted_cli_output_is_preserved_and_cannot_be_retried(
    monkeypatch, tmp_path, allow_legacy_body_for_unit_test
):
    from training import grpo
    from training.grpo_provenance import MANIFEST_NAME

    output_dir = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", _cli_argv(output_dir, tmp_path / "sft"))
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: _parent(),
    )
    monkeypatch.setattr(
        grpo,
        "build_direct_action_prompts",
        lambda _tiers: [{"scenario_id": TRAIN_SPLIT[0], "prompt": "fixture"}],
    )
    calls = []

    def interrupt(_args, _output_dir):
        calls.append("training")
        raise KeyboardInterrupt

    monkeypatch.setattr(grpo, "run_training", interrupt)
    if os.name == "nt":
        with pytest.raises(RuntimeError, match="stable directory-handle operations"):
            grpo.main()
        assert calls == []
        assert not output_dir.exists()
        return

    with pytest.raises(KeyboardInterrupt):
        grpo.main()

    manifest_path = output_dir / MANIFEST_NAME
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert saved["status"] == "interrupted"
    with pytest.raises(FileExistsError, match="already exists"):
        grpo.main()
    assert calls == ["training"]
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "interrupted"


def test_cli_retains_outer_failure_status_write_after_direct_training_write(
    monkeypatch, tmp_path, allow_legacy_body_for_unit_test
):
    from training import grpo
    from training.grpo_provenance import MANIFEST_NAME

    output_dir = tmp_path / "run"
    monkeypatch.setattr(sys, "argv", _cli_argv(output_dir, tmp_path / "sft"))
    monkeypatch.setattr(
        grpo,
        "validate_sft_parent",
        lambda *_args, **_kwargs: _parent(),
    )
    monkeypatch.setattr(
        grpo,
        "build_direct_action_prompts",
        lambda _tiers: [{"scenario_id": TRAIN_SPLIT[0], "prompt": "fixture"}],
    )
    monkeypatch.setattr(
        grpo,
        "_load_training_dependencies",
        lambda: (_ for _ in ()).throw(RuntimeError("original load failure")),
    )
    status_writes = []
    persist_status = grpo.persist_status

    def track_status_write(path, manifest, status, **kwargs):
        status_writes.append(status)
        return persist_status(path, manifest, status, **kwargs)

    monkeypatch.setattr(grpo, "persist_status", track_status_write)
    if os.name == "nt":
        with pytest.raises(RuntimeError, match="stable directory-handle operations"):
            grpo.main()
        assert status_writes == []
        assert not output_dir.exists()
        return

    with pytest.raises(RuntimeError, match="original load failure"):
        grpo.main()

    assert status_writes.count("failed") == 2
    saved = json.loads((output_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["failure"]["error_type"] == "RuntimeError"
