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
from training.sft_provenance import canonical_json_sha256
from training.grpo_provenance import (
    claim_new_output_directory,
    resolve_loader_provenance,
)

MODEL_COMMIT = "f" * 40
TOKENIZER_COMMIT = "e" * 40
MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
TOKENIZER_ID = "test-org/tokenizer"


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
    monkeypatch, tmp_path, capsys
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


def test_cli_rejects_local_tokenizer_path_before_optional_training_imports(
    monkeypatch, tmp_path, capsys
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


def test_cli_rejects_sft_parent_mismatch_before_output_or_model_loading(monkeypatch, tmp_path):
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


def test_cli_rejects_existing_output_before_reading_sft_parent(monkeypatch, tmp_path):
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


def test_cli_rejects_redirected_output_before_reading_sft_parent(monkeypatch, tmp_path):
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
):
    from training import grpo

    calls = []

    class FakeTokenizer:
        pad_token = "pad"
        eos_token = "eos"
        init_kwargs = {"_commit_hash": tokenizer_commit} if tokenizer_commit is not None else {}

        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            calls.append("tokenizer")
            return cls()

    class FakeModel:
        config = SimpleNamespace(_commit_hash=model_commit)

        def print_trainable_parameters(self):
            pass

        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            calls.append("model")
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
    return grpo, calls


@pytest.mark.parametrize("model_commit", [None, "d" * 40])
def test_model_loader_rejects_missing_or_mismatched_model_commit_before_adapter(
    monkeypatch, tmp_path, model_commit
):
    grpo, calls = _install_fake_loader(
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
    grpo, calls = _install_fake_loader(
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
    grpo, calls = _install_fake_loader(monkeypatch, tokenizer_commit=None)
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


def test_interrupted_cli_output_is_preserved_and_cannot_be_retried(monkeypatch, tmp_path):
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


def test_cli_retains_outer_failure_status_write_after_direct_training_write(monkeypatch, tmp_path):
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
