"""Tests for durable SFT run and checkpoint provenance."""

from __future__ import annotations

import builtins
import hashlib
import json
import os
import sys
import types
from pathlib import Path

import pytest

from config.splits import TEST_SPLIT, TRAIN_SEED, TRAIN_SPLIT
from training import sft_provenance
from training.sft_provenance import (
    SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256,
    canonical_file_sha256,
    create_run_manifest,
    inspect_training_corpus,
    mark_completed,
    mark_failed,
    mark_running,
    normalize_training_data_provenance,
    snapshot_training_corpus,
    write_manifest_atomic,
)


@pytest.fixture(autouse=True)
def _lightweight_runtime_probe(monkeypatch):
    monkeypatch.setattr(
        sft_provenance,
        "runtime_environment",
        lambda: {"packages": {}},
    )


def _manifest(
    tmp_path: Path, *, training_seed: int = TRAIN_SEED
) -> tuple[dict, Path, Path]:
    corpus = tmp_path / "train.jsonl"
    corpus.write_text(
        "".join(
            json.dumps({"scenario_id": scenario_id, "role": "triage"}) + "\r\n"
            for scenario_id in TRAIN_SPLIT
        ),
        encoding="utf-8",
    )
    output = tmp_path / "checkpoint"
    output.mkdir()
    manifest_path = output / "sft_run_manifest.json"
    manifest = create_run_manifest(
        corpus_path=corpus,
        output_dir=output,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        base_model_revision="model-commit",
        tokenizer="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision="tokenizer-commit",
        role="all",
        hyperparameters={"seed": training_seed},
        seed=training_seed,
    )
    return manifest, output, manifest_path


def test_planned_manifest_captures_exact_inputs_and_source(tmp_path):
    manifest, output, _ = _manifest(tmp_path)

    assert manifest["status"] == "planned"
    assert manifest["base_model"]["requested_revision"] == "model-commit"
    assert manifest["tokenizer"]["requested_revision"] == "tokenizer-commit"
    assert manifest["dataset"]["split"] == "train"
    assert manifest["dataset"]["split_scenarios"] == list(TRAIN_SPLIT)
    assert manifest["dataset"]["observed_scenarios"] == sorted(TRAIN_SPLIT)
    assert manifest["dataset"]["total_examples"] == len(TRAIN_SPLIT)
    assert manifest["dataset"]["split_seed"] == TRAIN_SEED
    assert manifest["dataset"]["corpus_sha256_canonical_lf"]
    assert manifest["dataset"]["split_sha256"]
    assert manifest["dataset"]["total_examples"] == len(TRAIN_SPLIT)
    assert manifest["dataset"]["total_scenarios"] == len(TRAIN_SPLIT)
    assert manifest["dataset"]["data_origin"] == "UNVERIFIED"
    assert manifest["dataset"]["synthetic"] is None
    assert manifest["dataset"]["data_origin_source"] == "unverified"
    assert manifest["dataset"]["corpus_manifest"] == {
        "present": False,
        "path": None,
        "sha256": None,
    }
    assert manifest["source"]["git_sha"]
    assert manifest["output_dir"] == str(output.resolve())
    assert "packages" in manifest["environment"]


def test_split_seed_is_independent_of_training_seed(tmp_path):
    manifest, _, _ = _manifest(tmp_path, training_seed=17)

    assert manifest["dataset"]["split_seed"] == TRAIN_SEED
    assert manifest["dataset"]["split_scenarios"] == list(TRAIN_SPLIT)
    assert manifest["training_seed"] == 17
    assert manifest["hyperparameters"]["seed"] == 17


def _generated_corpus(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    from training import build_sft_dataset

    data_dir = tmp_path / "dataset"
    monkeypatch.setattr(build_sft_dataset, "DATA_DIR", data_dir)
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", tmp_path / "frozen-evidence")
    monkeypatch.setattr(
        build_sft_dataset,
        "prepare_example_for_training",
        lambda example: {"messages": example["messages"], "tools": []},
    )
    monkeypatch.setattr(
        build_sft_dataset,
        "render_messages",
        lambda messages, **kwargs: ("rendered", ["generated"]),
    )
    corpus, _ = build_sft_dataset.build_sft_corpus()
    return corpus, data_dir / "sft_corpus_manifest.json"


def _create_manifest_for_corpus(corpus: Path, output_dir: Path) -> dict:
    return create_run_manifest(
        corpus_path=corpus,
        output_dir=output_dir,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        base_model_revision="model-commit",
        tokenizer="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision="tokenizer-commit",
        role="all",
        hyperparameters={"seed": TRAIN_SEED},
    )


def test_adjacent_corpus_origin_is_validated_and_preserved(
    tmp_path,
    monkeypatch,
):
    corpus, corpus_manifest_path = _generated_corpus(tmp_path, monkeypatch)
    source_manifest_bytes = corpus_manifest_path.read_bytes()
    run_output = tmp_path / "checkpoint"
    run = _create_manifest_for_corpus(corpus, run_output)
    dataset = run["dataset"]

    assert dataset["data_origin"] == "scenario_derived_synthetic"
    assert dataset["synthetic"] is True
    assert dataset["data_origin_source"] == "adjacent_corpus_manifest"
    assert dataset["corpus_manifest"] == {
        "present": True,
        "path": str(corpus_manifest_path.resolve()),
        "sha256": hashlib.sha256(corpus_manifest_path.read_bytes()).hexdigest(),
    }
    assert dataset["corpus_sha256_canonical_lf"] == json.loads(
        corpus_manifest_path.read_text(encoding="utf-8")
    )["corpus_sha256_canonical_lf"]
    assert dataset["total_examples"] == 64
    assert dataset["total_scenarios"] == 16
    assert dataset["split"] == "train"

    run_output.mkdir()
    (run_output / "adapter_config.json").write_text("{}", encoding="utf-8")
    (run_output / "adapter_model.safetensors").write_bytes(b"adapter-fixture")
    running = mark_running(
        run,
        resolved_model_revision="resolved-model-commit",
        resolved_tokenizer_revision="resolved-tokenizer-commit",
    )
    completed = mark_completed(
        running,
        output_dir=run_output,
        manifest_path=run_output / "sft_run_manifest.json",
        trainer_state={"global_step": 1},
        training_history=[],
    )
    assert completed["dataset"] == dataset
    assert completed["dataset"]["data_origin"] == "scenario_derived_synthetic"

    for field, value, message in (
        ("corpus_sha256_canonical_lf", "0" * 64, "corpus hash"),
        ("split", "val", "Train split"),
        ("total_examples", 63, "example count"),
        ("total_scenarios", 15, "scenario count"),
    ):
        tampered_manifest = json.loads(source_manifest_bytes)
        tampered_manifest[field] = value
        corpus_manifest_path.write_text(
            json.dumps(tampered_manifest),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=message):
            _create_manifest_for_corpus(corpus, run_output)

    legacy_manifest = json.loads(source_manifest_bytes)
    legacy_manifest.pop("data_origin")
    legacy_manifest.pop("synthetic")
    corpus_manifest_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")
    legacy_dataset = _create_manifest_for_corpus(corpus, run_output)["dataset"]
    assert legacy_dataset["data_origin"] == "UNVERIFIED"
    assert legacy_dataset["synthetic"] is None
    assert legacy_dataset["data_origin_source"] == "unverified"
    assert legacy_dataset["corpus_manifest"]["present"] is True
    assert legacy_dataset["corpus_manifest"]["sha256"] == hashlib.sha256(
        corpus_manifest_path.read_bytes()
    ).hexdigest()

    unsupported_manifest = json.loads(source_manifest_bytes)
    unsupported_manifest["data_origin"] = "human_expert_verified"
    unsupported_manifest["synthetic"] = False
    assert (
        unsupported_manifest["corpus_sha256_canonical_lf"]
        == SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256
    )
    corpus_manifest_path.write_text(
        json.dumps(unsupported_manifest),
        encoding="utf-8",
    )
    unsupported_dataset = _create_manifest_for_corpus(corpus, run_output)["dataset"]
    assert unsupported_dataset["data_origin"] == "UNVERIFIED"
    assert unsupported_dataset["synthetic"] is None
    assert unsupported_dataset["data_origin_source"] == "unverified"


def test_corpus_hash_is_newline_stable(tmp_path):
    crlf = tmp_path / "crlf.jsonl"
    lf = tmp_path / "lf.jsonl"
    crlf.write_bytes(b'{"a":1}\r\n{"b":2}\r\n')
    lf.write_bytes(b'{"a":1}\n{"b":2}\n')
    assert canonical_file_sha256(crlf) == canonical_file_sha256(lf)


def test_exact_revisions_are_required(tmp_path):
    corpus = tmp_path / "train.jsonl"
    corpus.write_text(
        "".join(
            json.dumps({"scenario_id": scenario_id, "role": "triage"}) + "\n"
            for scenario_id in TRAIN_SPLIT
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="base model revision"):
        create_run_manifest(
            corpus_path=corpus,
            output_dir=tmp_path / "out",
            base_model="model",
            base_model_revision="",
            tokenizer="tokenizer",
            tokenizer_revision="revision",
            role="all",
            hyperparameters={},
        )


def test_sft_rejects_manifest_path_incompatible_with_evaluators(monkeypatch, tmp_path):
    from training import sft

    output = tmp_path / "checkpoint"
    monkeypatch.setattr(sys, "argv", [
        "sft.py",
        "--model", "Qwen/Qwen2.5-7B-Instruct",
        "--model-revision", "model-commit",
        "--data", str(tmp_path / "missing-corpus.jsonl"),
        "--output", str(output),
        "--provenance-output", str(tmp_path / "external-manifest.json"),
    ])
    with pytest.raises(ValueError, match="so G8 and G9"):
        sft.main()
    assert not output.exists()
    assert not (tmp_path / "external-manifest.json").exists()


def _write_training_corpus(path: Path) -> Path:
    path.write_text(
        "".join(
            json.dumps({"scenario_id": scenario_id, "role": "triage"}) + "\n"
            for scenario_id in TRAIN_SPLIT
        ),
        encoding="utf-8",
    )
    return path


def _prepare_sft_output_cli(monkeypatch, corpus_path: Path, output_path: Path):
    from training import sft

    attempted_ml_imports = []
    original_import = builtins.__import__
    ml_packages = {"datasets", "peft", "transformers", "trl"}

    def block_ml_imports(name, *args, **kwargs):
        if name.split(".", 1)[0] in ml_packages:
            attempted_ml_imports.append(name)
            raise AssertionError("training dependencies must not be imported")
        return original_import(name, *args, **kwargs)

    snapshot_calls = []
    original_snapshot = sft.snapshot_training_corpus

    def record_snapshot(path):
        snapshot_calls.append(path)
        return original_snapshot(path)

    monkeypatch.setattr(builtins, "__import__", block_ml_imports)
    monkeypatch.setattr(sft, "snapshot_training_corpus", record_snapshot)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sft.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            "model-commit",
            "--data",
            str(corpus_path),
            "--output",
            str(output_path),
        ],
    )
    return sft, snapshot_calls, attempted_ml_imports


def test_sft_refuses_existing_populated_output_without_touching_run(
    monkeypatch,
    tmp_path,
):
    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    output = tmp_path / "checkpoint"
    output.mkdir()
    manifest_path = output / "sft_run_manifest.json"
    manifest_bytes = b'{"run_id":"previous-run","status":"completed"}\n'
    manifest_path.write_bytes(manifest_bytes)
    checkpoint_path = output / "adapter_model.safetensors"
    checkpoint_bytes = b"existing checkpoint"
    checkpoint_path.write_bytes(checkpoint_bytes)
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
    )

    with pytest.raises(FileExistsError):
        sft.main()

    assert snapshot_calls == []
    assert attempted_ml_imports == []
    assert manifest_path.read_bytes() == manifest_bytes
    assert checkpoint_path.read_bytes() == checkpoint_bytes
    assert {path.name for path in output.iterdir()} == {
        "adapter_model.safetensors",
        "sft_run_manifest.json",
    }


def test_sft_rejects_existing_empty_output_directory(monkeypatch, tmp_path):
    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    output = tmp_path / "empty-checkpoint"
    output.mkdir()
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
    )

    with pytest.raises(FileExistsError):
        sft.main()

    assert snapshot_calls == []
    assert attempted_ml_imports == []
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("redirected_component", ["output", "parent"])
def test_sft_rejects_redirected_output_before_snapshot_or_training_import(
    monkeypatch,
    tmp_path,
    redirected_component,
):
    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    target_parent = tmp_path / "target-parent"
    target_parent.mkdir()
    redirected_target = target_parent / "checkpoint"

    if redirected_component == "output":
        redirected_target.mkdir()
        output = tmp_path / "redirected-checkpoint"
        output.parent.mkdir(exist_ok=True)
    else:
        output = tmp_path / "redirected-parent" / "checkpoint"
    try:
        if redirected_component == "output":
            output.symlink_to(redirected_target, target_is_directory=True)
        else:
            output.parent.symlink_to(target_parent, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")

    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
    )

    with pytest.raises(ValueError, match="redirect"):
        sft.main()

    assert snapshot_calls == []
    assert attempted_ml_imports == []
    if redirected_component == "output":
        assert list(redirected_target.iterdir()) == []
    else:
        assert not redirected_target.exists()


def test_sft_uses_manifested_corpus_snapshot_if_source_changes_before_load(
    monkeypatch,
    tmp_path,
):
    from training import sft

    corpus, corpus_manifest_path = _generated_corpus(tmp_path, monkeypatch)
    original_bytes = corpus.read_bytes()
    original_rows = [
        json.loads(line)
        for line in original_bytes.decode("utf-8").splitlines()
        if line
    ]
    replacement_rows = [
        {**row, "race_marker": "replacement"} for row in original_rows
    ]
    replacement_bytes = "".join(
        json.dumps(row) + "\n" for row in replacement_rows
    ).encode("utf-8")
    output = tmp_path / "checkpoint"
    captured: dict[str, list[dict]] = {}

    def load_dataset(_format, *, data_files, split):
        assert split == "train"
        corpus.write_bytes(replacement_bytes)
        captured["rows"] = [
            json.loads(line)
            for line in Path(data_files).read_text(encoding="utf-8").splitlines()
            if line
        ]
        raise RuntimeError("stop before model loading")

    class Dataset:
        @classmethod
        def from_list(cls, rows):
            corpus.write_bytes(replacement_bytes)
            captured["rows"] = rows
            raise RuntimeError("stop before model loading")

    datasets_module = types.ModuleType("datasets")
    datasets_module.load_dataset = load_dataset
    datasets_module.Dataset = Dataset
    monkeypatch.setitem(sys.modules, "datasets", datasets_module)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sft.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            "model-commit",
            "--data",
            str(corpus),
            "--output",
            str(output),
        ],
    )

    with pytest.raises(RuntimeError, match="stop before model loading"):
        sft.main()

    persisted = json.loads(
        (output / "sft_run_manifest.json").read_text(encoding="utf-8")
    )
    assert [row["scenario_id"] for row in captured["rows"]] == [
        row["scenario_id"] for row in original_rows
    ]
    assert all(
        row["provenance"]["scenario_id"] == row["scenario_id"]
        for row in captured["rows"]
    )
    assert all("race_marker" not in row for row in captured["rows"])
    assert persisted["dataset"]["corpus_path"] == str(corpus.resolve())
    assert persisted["dataset"]["corpus_sha256_canonical_lf"] == hashlib.sha256(
        original_bytes.replace(b"\r\n", b"\n")
    ).hexdigest()
    assert persisted["dataset"]["total_examples"] == len(original_rows)
    assert persisted["dataset"]["total_scenarios"] == len(TRAIN_SPLIT)
    assert persisted["dataset"]["data_origin"] == "scenario_derived_synthetic"
    assert persisted["dataset"]["synthetic"] is True
    assert persisted["dataset"]["data_origin_source"] == (
        "adjacent_corpus_manifest"
    )
    assert persisted["dataset"]["corpus_manifest"] == {
        "present": True,
        "path": str(corpus_manifest_path.resolve()),
        "sha256": hashlib.sha256(corpus_manifest_path.read_bytes()).hexdigest(),
    }
    assert persisted["status"] == "failed"
    assert persisted["failure"]["type"] == "RuntimeError"
    assert persisted["failure"]["message"] == (
        "SFT run failed; exception details are withheld"
    )
    assert corpus.read_bytes() == replacement_bytes
    revalidated_dataset = normalize_training_data_provenance(
        persisted["dataset"],
        approved_corpus_path=corpus,
    )
    assert revalidated_dataset["data_origin"] == "UNVERIFIED"
    assert revalidated_dataset["synthetic"] is None
    assert (
        revalidated_dataset["corpus_manifest"]["verification_status"]
        == "corpus_hash_mismatch"
    )


def test_corpus_rejects_validation_or_test_scenarios(tmp_path):
    corpus = tmp_path / "leaky.jsonl"
    corpus.write_text(
        json.dumps({"scenario_id": TEST_SPLIT[0], "role": "triage"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-training scenario"):
        inspect_training_corpus(corpus)


@pytest.mark.parametrize("redirect_type", ["symlink", "hardlink"])
def test_training_corpus_snapshot_rejects_file_redirects(
    tmp_path,
    monkeypatch,
    redirect_type,
):
    corpus, _ = _generated_corpus(tmp_path, monkeypatch)
    redirected_path = tmp_path / f"{redirect_type}-corpus.jsonl"
    try:
        if redirect_type == "symlink":
            redirected_path.symlink_to(corpus)
        else:
            os.link(corpus, redirected_path)
    except OSError:
        pytest.skip(f"{redirect_type} creation is unavailable on this host")

    with pytest.raises(ValueError, match="redirect"):
        snapshot_training_corpus(redirected_path)


def test_running_and_completed_manifest_hash_checkpoint_files(tmp_path):
    manifest, output, manifest_path = _manifest(tmp_path)
    running = mark_running(
        manifest,
        resolved_model_revision="resolved-model-commit",
        resolved_tokenizer_revision="resolved-tokenizer-commit",
    )
    assert running["status"] == "running"

    (output / "adapter_config.json").write_text('{"r":16}\n', encoding="utf-8")
    (output / "adapter_model.safetensors").write_bytes(b"adapter-bytes")
    completed = mark_completed(
        running,
        output_dir=output,
        manifest_path=manifest_path,
        trainer_state={"global_step": 4, "epoch": 1.0},
        training_history=[{"loss": 1.25, "step": 4}],
    )
    write_manifest_atomic(manifest_path, completed)

    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"
    assert persisted["base_model"]["resolved_revision"] == "resolved-model-commit"
    assert persisted["trainer_state"]["global_step"] == 4
    assert persisted["training_history"] == [{"loss": 1.25, "step": 4}]
    assert persisted["checkpoint"]["total_files"] == 2
    assert persisted["checkpoint"]["tree_sha256"]
    assert persisted["dataset"]["data_origin"] == "UNVERIFIED"
    assert persisted["dataset"]["corpus_manifest"]["present"] is False
    assert {
        item["path"] for item in persisted["checkpoint"]["files"]
    } == {"adapter_config.json", "adapter_model.safetensors"}


def test_completed_manifest_requires_adapter_files(tmp_path):
    manifest, output, manifest_path = _manifest(tmp_path)
    (output / "tokenizer.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="adapter_config.json"):
        mark_completed(
            manifest,
            output_dir=output,
            manifest_path=manifest_path,
            trainer_state={},
            training_history=[],
        )


def test_completed_manifest_rejects_symlinked_checkpoint_content(tmp_path):
    manifest, output, manifest_path = _manifest(tmp_path)
    (output / "adapter_config.json").write_text('{"r":16}\n', encoding="utf-8")
    (output / "adapter_model.safetensors").write_bytes(b"adapter-bytes")
    external = tmp_path / "external.txt"
    external.write_text("not part of the checkpoint", encoding="utf-8")
    try:
        (output / "external.txt").symlink_to(external)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(ValueError, match="symlink"):
        mark_completed(
            manifest,
            output_dir=output,
            manifest_path=manifest_path,
            trainer_state={},
            training_history=[],
        )


@pytest.mark.parametrize(
    ("exc", "interrupted", "expected_status"),
    [
        (RuntimeError("out of memory"), False, "failed"),
        (KeyboardInterrupt(), True, "interrupted"),
    ],
)
def test_failure_and_interruption_are_durable(
    tmp_path,
    exc,
    interrupted,
    expected_status,
):
    manifest, _, manifest_path = _manifest(tmp_path)
    failed = mark_failed(manifest, exc, interrupted=interrupted)
    write_manifest_atomic(manifest_path, failed)

    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["status"] == expected_status
    assert persisted["completed_at"]
    assert persisted["failure"]["type"] == type(exc).__name__
    assert persisted["failure"]["message"] == (
        "SFT run failed; exception details are withheld"
    )


def test_failure_manifest_omits_exception_text(tmp_path):
    manifest, _, _ = _manifest(tmp_path)
    marker = "INJECTED_SFT_SECRET_MARKER"

    failed = mark_failed(manifest, RuntimeError(marker))
    serialized = json.dumps(failed)

    assert failed["failure"]["type"] == "RuntimeError"
    assert failed["failure"]["message"] == (
        "SFT run failed; exception details are withheld"
    )
    assert marker not in serialized
