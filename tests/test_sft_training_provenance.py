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
from training.generate_trajectories import SFT_EXAMPLE_FORMAT
from training.sft_provenance import (
    SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256,
    canonical_bytes_sha256,
    canonical_file_sha256,
    create_run_manifest,
    inspect_training_corpus,
    mark_completed,
    mark_failed,
    mark_running,
    normalize_training_data_provenance,
    snapshot_training_corpus,
    validate_hf_reference,
    write_manifest_atomic,
)

MODEL_COMMIT = "a" * 40
TOKENIZER_COMMIT = "b" * 40


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
        base_model_revision=MODEL_COMMIT,
        tokenizer="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
        role="all",
        hyperparameters={"seed": training_seed},
        seed=training_seed,
    )
    return manifest, output, manifest_path


def test_planned_manifest_captures_exact_inputs_and_source(tmp_path):
    manifest, output, _ = _manifest(tmp_path)

    assert manifest["status"] == "planned"
    assert manifest["base_model"]["requested_revision"] == MODEL_COMMIT
    assert manifest["tokenizer"]["requested_revision"] == TOKENIZER_COMMIT
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
        base_model_revision=MODEL_COMMIT,
        tokenizer="Qwen/Qwen2.5-7B-Instruct",
        tokenizer_revision=TOKENIZER_COMMIT,
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
        resolved_model_revision=MODEL_COMMIT,
        resolved_tokenizer_revision=TOKENIZER_COMMIT,
        resolved_tokenizer_revision_basis="LOADER_EXPOSED_COMMIT_HASH_MATCH",
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


def test_full_immutable_hugging_face_revisions_are_required(tmp_path):
    corpus = tmp_path / "train.jsonl"
    corpus.write_text(
        "".join(
            json.dumps({"scenario_id": scenario_id, "role": "triage"}) + "\n"
            for scenario_id in TRAIN_SPLIT
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="full 40-character"):
        create_run_manifest(
            corpus_path=corpus,
            output_dir=tmp_path / "out",
            base_model="model",
            base_model_revision="main",
            tokenizer="tokenizer",
            tokenizer_revision=TOKENIZER_COMMIT,
            role="all",
            hyperparameters={},
        )
    with pytest.raises(ValueError, match="full 40-character"):
        create_run_manifest(
            corpus_path=corpus,
            output_dir=tmp_path / "out",
            base_model="model",
            base_model_revision=MODEL_COMMIT,
            tokenizer="tokenizer",
            tokenizer_revision="refs/heads/main",
            role="all",
            hyperparameters={},
        )


def test_sft_rejects_manifest_path_incompatible_with_evaluators(monkeypatch, tmp_path):
    from training import sft

    output = tmp_path / "checkpoint"
    monkeypatch.setattr(sys, "argv", [
        "sft.py",
        "--model", "Qwen/Qwen2.5-7B-Instruct",
        "--model-revision", MODEL_COMMIT,
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
            json.dumps(
                {
                    "format": SFT_EXAMPLE_FORMAT,
                    "scenario_id": scenario_id,
                    "role": "triage",
                    "messages": [
                        {"role": "system", "content": "generic system placeholder"},
                        {"role": "user", "content": "Diagnose the test incident."},
                        {"role": "assistant", "content": "Investigate the service."},
                    ],
                }
            )
            + "\n"
            for scenario_id in TRAIN_SPLIT
        ),
        encoding="utf-8",
    )
    return path


def _write_role_tool_training_corpus(
    path: Path,
    *,
    invalid_remediation: bool = False,
) -> tuple[Path, Path]:
    rows = []
    for index, scenario_id in enumerate(TRAIN_SPLIT):
        for role in ("triage", "remediation", "comms"):
            messages = [
                {"role": "system", "content": "generic system placeholder"},
                {"role": "user", "content": "Review the synthetic fixture incident."},
            ]
            if role == "comms":
                messages.append(
                    {"role": "assistant", "content": "Synthetic fixture update."}
                )
            else:
                tool_name = (
                    "alertmanager_list_alerts"
                    if role == "triage"
                    else "k8s_delete_pod" if invalid_remediation else "kubectl_get"
                )
                tool_arguments = (
                    '{"pod_name":"forbidden-fixture-detail"}'
                    if role == "remediation" and invalid_remediation
                    else "{}"
                )
                call_id = f"fixture_{index}_{role}"
                messages.extend(
                    [
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": call_id,
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": tool_arguments,
                                    },
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "tool_name": tool_name,
                            "content": "{}",
                        },
                        {
                            "role": "assistant",
                            "content": "Synthetic fixture result.",
                        },
                    ]
                )
            rows.append(
                {
                    "format": SFT_EXAMPLE_FORMAT,
                    "scenario_id": scenario_id,
                    "role": role,
                    "fixture": (
                        "noncanonical_synthetic_acl_invalid"
                        if invalid_remediation
                        else "noncanonical_synthetic_acl_valid"
                    ),
                    "messages": messages,
                }
            )

    corpus_bytes = "".join(
        json.dumps(row, sort_keys=True) + "\n" for row in rows
    ).encode("utf-8")
    path.write_bytes(corpus_bytes)
    manifest_path = path.parent / "sft_corpus_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "data_origin": "scenario_derived_synthetic",
                "synthetic": True,
                "corpus_sha256_canonical_lf": canonical_bytes_sha256(corpus_bytes),
                "total_examples": len(rows),
                "total_scenarios": len(TRAIN_SPLIT),
                "split": "train",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path, manifest_path


def _prepare_sft_output_cli(
    monkeypatch,
    corpus_path: Path,
    output_path: Path,
    *,
    model: str = "Qwen/Qwen2.5-7B-Instruct",
    model_revision: str = MODEL_COMMIT,
    extra_args: tuple[str, ...] = (),
):
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
            model,
            "--model-revision",
            model_revision,
            "--data",
            str(corpus_path),
            "--output",
            str(output_path),
            *extra_args,
        ],
    )
    return sft, snapshot_calls, attempted_ml_imports


@pytest.mark.parametrize(
    ("model_revision", "extra_args"),
    [
        ("main", ()),
        (MODEL_COMMIT, ("--tokenizer-revision", "refs/heads/main")),
    ],
)
def test_sft_rejects_mutable_revisions_before_output_or_training_import(
    monkeypatch,
    tmp_path,
    model_revision,
    extra_args,
):
    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    output = tmp_path / "checkpoint"
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
        model_revision=model_revision,
        extra_args=extra_args,
    )

    with pytest.raises(ValueError, match="full 40-character"):
        sft.main()

    assert not output.exists()
    assert snapshot_calls == []
    assert attempted_ml_imports == []


def test_sft_rejects_local_model_snapshot_before_output_or_training_import(
    monkeypatch,
    tmp_path,
):
    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    local_model = tmp_path / "model-snapshot"
    local_model.mkdir()
    output = tmp_path / "checkpoint"
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
        model=str(local_model),
    )

    with pytest.raises(ValueError, match="Local base model paths are unsupported"):
        sft.main()

    assert not output.exists()
    assert snapshot_calls == []
    assert attempted_ml_imports == []


@pytest.mark.parametrize(
    "model",
    [
        "https://huggingface.co/org/model",
        "org/../model",
        "org/model/nested",
        "invalid--repo",
        "invalid..repo",
        "invalid-repo.git",
    ],
)
def test_sft_rejects_non_hugging_face_repository_ids_before_output_or_load(
    monkeypatch,
    tmp_path,
    model,
):
    monkeypatch.chdir(tmp_path)
    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    output = tmp_path / "checkpoint"
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
        model=model,
    )

    with pytest.raises(ValueError, match="valid Hugging Face repository id"):
        sft.main()

    assert not output.exists()
    assert snapshot_calls == []
    assert attempted_ml_imports == []


def test_nonexistent_owner_repo_is_treated_as_a_repository_id(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    reference = "atlasops-missing-owner-fixture-2026/nonexistent-model"
    assert not Path(reference).exists()

    validate_hf_reference(reference, MODEL_COMMIT, label="base model")


def test_sft_cli_help_describes_repository_ids_and_immutable_commits(
    monkeypatch,
    capsys,
):
    from training import sft

    monkeypatch.setattr(sys, "argv", ["sft.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        sft._parse_args()

    help_text = " ".join(capsys.readouterr().out.split())
    assert exc.value.code == 0
    normalized_help = help_text.casefold()
    assert "hugging face repository id" in normalized_help
    assert "local paths are unsupported" in normalized_help
    assert "full 40-character immutable hugging face commit sha" in normalized_help


def test_sft_requires_separate_tokenizer_pin_before_output_or_training_import(
    monkeypatch,
    tmp_path,
):
    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    output = tmp_path / "checkpoint"
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
        extra_args=("--tokenizer", "test-org/tokenizer"),
    )

    with pytest.raises(ValueError, match="explicit --tokenizer-revision"):
        sft.main()

    assert not output.exists()
    assert snapshot_calls == []
    assert attempted_ml_imports == []


def test_sft_invalid_corpus_does_not_create_run_output(monkeypatch, tmp_path):
    corpus = tmp_path / "leaky.jsonl"
    corpus.write_text(
        json.dumps({"scenario_id": TEST_SPLIT[0], "role": "triage"}) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "checkpoint"
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
    )

    with pytest.raises(ValueError, match="non-training scenario"):
        sft.main()

    assert not output.exists()
    assert snapshot_calls == [corpus]
    assert attempted_ml_imports == []


@pytest.mark.parametrize("role", ["all", "triage", "diagnosis", "remediation", "comms"])
def test_sft_rejects_canonical_synthetic_corpus_for_every_role_before_output_or_ml_import(
    monkeypatch,
    tmp_path,
    role,
):
    corpus, _ = _generated_corpus(tmp_path, monkeypatch)
    assert (
        canonical_bytes_sha256(corpus.read_bytes())
        == SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256
    )
    output = tmp_path / "checkpoint"
    extra_args = () if role == "all" else ("--role", role)
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
        extra_args=extra_args,
    )

    with pytest.raises(ValueError, match="canonical Train corpus"):
        sft.main()

    assert not output.exists()
    assert snapshot_calls == [corpus]
    assert attempted_ml_imports == []


@pytest.mark.parametrize(
    "variant",
    [
        "blank_lines",
        "bare_cr",
        "json_whitespace",
        "reordered",
        "metadata_only",
        "system_placeholder",
        "tool_argument_whitespace",
        "paired_call_ids",
        "scenario_labels",
    ],
)
def test_sft_rejects_reserialized_canonical_teacher_rows_before_output(
    monkeypatch, tmp_path, variant
):
    source_corpus, _ = _generated_corpus(tmp_path, monkeypatch)
    original = source_corpus.read_bytes()
    rows = [json.loads(line) for line in original.decode("utf-8").splitlines()]
    lf_bytes = original.replace(b"\r\n", b"\n")
    if variant == "blank_lines":
        altered = lf_bytes.replace(b"\n", b"\n\n")
    elif variant == "bare_cr":
        altered = lf_bytes.replace(b"\n", b"\r")
    else:
        if variant == "reordered":
            rows.reverse()
        elif variant == "metadata_only":
            rows[0]["judge"]["critique"] = "Formatting-only fixture variant"
        elif variant == "system_placeholder":
            rows[0]["messages"][0]["content"] = "Different stored placeholder"
        elif variant == "tool_argument_whitespace":
            call = next(
                message["tool_calls"][0]
                for message in rows[0]["messages"]
                if message.get("tool_calls")
            )
            arguments = call["function"]["arguments"]
            call["function"]["arguments"] = json.dumps(
                json.loads(arguments) if isinstance(arguments, str) else arguments,
                indent=2,
            )
        elif variant == "paired_call_ids":
            call = next(
                message["tool_calls"][0]
                for message in rows[0]["messages"]
                if message.get("tool_calls")
            )
            old_id = call["id"]
            call["id"] = f"{old_id}_rekeyed"
            next(
                message
                for message in rows[0]["messages"]
                if message.get("tool_call_id") == old_id
            )["tool_call_id"] = call["id"]
        elif variant == "scenario_labels":
            rows[0]["scenario_id"], rows[4]["scenario_id"] = (
                rows[4]["scenario_id"],
                rows[0]["scenario_id"],
            )
        altered = (
            "\n".join(
                json.dumps(row, sort_keys=True, separators=(",", ":"))
                for row in rows
            )
            + "\n"
        ).encode("utf-8")
    candidate_dir = tmp_path / "reserialized"
    candidate_dir.mkdir()
    corpus = candidate_dir / "train.jsonl"
    corpus.write_bytes(altered)
    assert canonical_bytes_sha256(altered) != SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256
    output = tmp_path / "checkpoint"
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch, corpus, output, extra_args=("--role", "triage")
    )

    with pytest.raises(ValueError, match="canonical Train corpus"):
        sft.main()

    assert not output.exists()
    assert snapshot_calls == [corpus]
    assert attempted_ml_imports == []


@pytest.mark.parametrize("role", ["all", "remediation"])
def test_sft_rejects_selected_role_acl_violation_before_output_or_ml_import(
    monkeypatch,
    tmp_path,
    role,
):
    corpus, _ = _write_role_tool_training_corpus(
        tmp_path / "invalid-noncanonical.jsonl",
        invalid_remediation=True,
    )
    assert (
        canonical_file_sha256(corpus)
        != SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256
    )
    output = tmp_path / "checkpoint"
    extra_args = () if role == "all" else ("--role", role)
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
        extra_args=extra_args,
    )

    with pytest.raises(ValueError, match="SFT training admission rejected") as exc:
        sft.main()

    message = str(exc.value)
    assert "role='remediation'" in message
    assert "tool='k8s_delete_pod'" in message
    assert "role ACL" in message
    assert "forbidden-fixture-detail" not in message
    assert not output.exists()
    assert snapshot_calls == [corpus]
    assert attempted_ml_imports == []


def test_sft_role_filter_admits_only_selected_acl_rows_before_model_import(
    monkeypatch,
    tmp_path,
):
    from agents.tool_policy import ROLE_ALLOWED_TOOLS
    from training import sft

    corpus, _ = _write_role_tool_training_corpus(tmp_path / "acl-valid.jsonl")
    assert (
        canonical_file_sha256(corpus)
        != SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256
    )
    output = tmp_path / "checkpoint"
    captured = {}
    attempted_model_imports = []
    original_import = builtins.__import__

    class Dataset:
        @classmethod
        def from_list(cls, rows):
            captured["rows"] = rows
            raise RuntimeError("stop after dataset admission")

    def block_model_imports(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"peft", "transformers", "trl"}:
            attempted_model_imports.append(name)
            raise AssertionError("model dependencies must not be imported")
        return original_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "datasets", types.SimpleNamespace(Dataset=Dataset))
    monkeypatch.setattr(builtins, "__import__", block_model_imports)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sft.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            MODEL_COMMIT,
            "--data",
            str(corpus),
            "--output",
            str(output),
            "--role",
            "triage",
        ],
    )

    with pytest.raises(RuntimeError, match="stop after dataset admission"):
        sft.main()

    # This proves only code-level admission and handoff; it does not authenticate
    # or authorize a D3 human-approval decision.
    rows = captured["rows"]
    assert len(rows) == 16
    assert {row["role"] for row in rows} == {"triage"}
    assert all(
        call["function"]["name"] in ROLE_ALLOWED_TOOLS["triage"]
        for row in rows
        for message in row["messages"]
        for call in message.get("tool_calls") or []
    )
    assert attempted_model_imports == []
    persisted = json.loads(
        (output / "sft_run_manifest.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"


def test_sft_missing_corpus_does_not_create_run_output(monkeypatch, tmp_path):
    corpus = tmp_path / "missing.jsonl"
    output = tmp_path / "checkpoint"
    sft, snapshot_calls, attempted_ml_imports = _prepare_sft_output_cli(
        monkeypatch,
        corpus,
        output,
    )

    with pytest.raises(FileNotFoundError, match="SFT corpus not found"):
        sft.main()

    assert not output.exists()
    assert snapshot_calls == [corpus]
    assert attempted_ml_imports == []


def _install_fake_sft_dependencies(
    monkeypatch,
    *,
    resolved_model_revision,
    resolved_tokenizer_revision,
):
    calls = {
        "tokenizer": [],
        "tokenizer_kwargs": [],
        "model": [],
        "model_kwargs": [],
        "trainer": 0,
    }

    class Dataset:
        def __init__(self, rows):
            self.rows = rows

        @classmethod
        def from_list(cls, rows):
            return cls(rows)

        def filter(self, predicate):
            self.rows = [row for row in self.rows if predicate(row)]
            return self

        def __len__(self):
            return len(self.rows)

    class FakeTokenizer:
        def __init__(self):
            self.pad_token = "pad"
            self.eos_token = "eos"
            self.chat_template = None
            self.init_kwargs = {}
            if resolved_tokenizer_revision is not None:
                self.init_kwargs["_commit_hash"] = resolved_tokenizer_revision

    class FakeModel:
        def __init__(self):
            self.config = types.SimpleNamespace(
                _commit_hash=resolved_model_revision,
            )

        def print_trainable_parameters(self):
            pass

    class SFTConfig:
        def __init__(self, *, assistant_only_loss, **kwargs):
            self.assistant_only_loss = assistant_only_loss
            self.kwargs = kwargs

    class SFTTrainer:
        def __init__(self, **kwargs):
            calls["trainer"] += 1
            raise RuntimeError("stop before fake training")

    datasets_module = types.ModuleType("datasets")
    datasets_module.Dataset = Dataset

    peft_module = types.ModuleType("peft")
    peft_module.LoraConfig = lambda **kwargs: kwargs
    peft_module.TaskType = types.SimpleNamespace(CAUSAL_LM="CAUSAL_LM")
    peft_module.get_peft_model = lambda model, config: model
    peft_module.prepare_model_for_kbit_training = lambda model: model

    transformers_module = types.ModuleType("transformers")

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(source, *, revision, **kwargs):
            calls["tokenizer"].append((source, revision))
            calls["tokenizer_kwargs"].append(kwargs)
            return FakeTokenizer()

    class AutoModelForCausalLM:
        @staticmethod
        def from_pretrained(source, *, revision, **kwargs):
            calls["model"].append((source, revision))
            calls["model_kwargs"].append(kwargs)
            return FakeModel()

    transformers_module.AutoTokenizer = AutoTokenizer
    transformers_module.AutoModelForCausalLM = AutoModelForCausalLM
    transformers_module.BitsAndBytesConfig = lambda **kwargs: kwargs
    transformers_module.set_seed = lambda seed: None

    trl_module = types.ModuleType("trl")
    trl_module.SFTConfig = SFTConfig
    trl_module.SFTTrainer = SFTTrainer

    for name, module in (
        ("datasets", datasets_module),
        ("peft", peft_module),
        ("transformers", transformers_module),
        ("trl", trl_module),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return calls


@pytest.mark.parametrize(
    ("resolved_model_revision", "resolved_tokenizer_revision", "message"),
    [
        (None, MODEL_COMMIT, "Loaded base model revision"),
        ("c" * 40, MODEL_COMMIT, "does not match the requested"),
        (MODEL_COMMIT, "d" * 40, "does not match the requested"),
    ],
)
def test_sft_rejects_missing_or_mismatched_loader_commit_identity(
    monkeypatch,
    tmp_path,
    resolved_model_revision,
    resolved_tokenizer_revision,
    message,
):
    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    output = tmp_path / "checkpoint"
    calls = _install_fake_sft_dependencies(
        monkeypatch,
        resolved_model_revision=resolved_model_revision,
        resolved_tokenizer_revision=resolved_tokenizer_revision,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sft.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            MODEL_COMMIT,
            "--data",
            str(corpus),
            "--output",
            str(output),
        ],
    )

    with pytest.raises(ValueError, match=message):
        from training import sft

        sft.main()

    persisted = json.loads(
        (output / "sft_run_manifest.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"
    assert persisted["base_model"]["resolved_revision"] is None
    if resolved_tokenizer_revision == MODEL_COMMIT:
        assert persisted["tokenizer"]["resolved_revision"] == MODEL_COMMIT
        assert persisted["tokenizer"]["resolved_revision_basis"] == (
            "LOADER_EXPOSED_COMMIT_HASH_MATCH"
        )
    else:
        assert persisted["tokenizer"]["resolved_revision"] is None
        assert persisted["tokenizer"]["resolved_revision_basis"] is None
    assert "model_loaded_at" not in persisted
    assert calls["trainer"] == 0
    if resolved_tokenizer_revision == MODEL_COMMIT:
        assert len(calls["model"]) == 1
    else:
        assert calls["model"] == []


def test_sft_records_exact_loader_commits_before_training(
    monkeypatch,
    tmp_path,
):
    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    output = tmp_path / "checkpoint"
    calls = _install_fake_sft_dependencies(
        monkeypatch,
        resolved_model_revision=MODEL_COMMIT,
        resolved_tokenizer_revision=TOKENIZER_COMMIT,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sft.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            MODEL_COMMIT,
            "--tokenizer",
            "test-org/tokenizer",
            "--tokenizer-revision",
            TOKENIZER_COMMIT,
            "--data",
            str(corpus),
            "--output",
            str(output),
        ],
    )

    with pytest.raises(RuntimeError, match="stop before fake training"):
        from training import sft

        sft.main()

    persisted = json.loads(
        (output / "sft_run_manifest.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"
    assert persisted["base_model"]["requested_revision"] == MODEL_COMMIT
    assert persisted["base_model"]["resolved_revision"] == MODEL_COMMIT
    assert persisted["tokenizer"]["requested_revision"] == TOKENIZER_COMMIT
    assert persisted["tokenizer"]["resolved_revision"] == TOKENIZER_COMMIT
    assert persisted["tokenizer"]["resolved_revision_basis"] == (
        "LOADER_EXPOSED_COMMIT_HASH_MATCH"
    )
    assert calls["tokenizer"] == [
        ("test-org/tokenizer", TOKENIZER_COMMIT)
    ]
    assert calls["model"] == [
        ("Qwen/Qwen2.5-7B-Instruct", MODEL_COMMIT)
    ]
    assert [
        kwargs.get("trust_remote_code") for kwargs in calls["tokenizer_kwargs"]
    ] == [False]
    assert [
        kwargs.get("trust_remote_code") for kwargs in calls["model_kwargs"]
    ] == [False]
    assert calls["trainer"] == 1


def test_sft_records_pinned_tokenizer_revision_when_loader_exposes_no_hash(
    monkeypatch,
    tmp_path,
):
    from training import sft

    corpus = _write_training_corpus(tmp_path / "train.jsonl")
    output = tmp_path / "checkpoint"
    calls = _install_fake_sft_dependencies(
        monkeypatch,
        resolved_model_revision=MODEL_COMMIT,
        resolved_tokenizer_revision=None,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sft.py",
            "--model",
            "Qwen/Qwen2.5-7B-Instruct",
            "--model-revision",
            MODEL_COMMIT,
            "--tokenizer",
            "test-org/tokenizer",
            "--tokenizer-revision",
            TOKENIZER_COMMIT,
            "--data",
            str(corpus),
            "--output",
            str(output),
        ],
    )

    with pytest.raises(RuntimeError, match="stop before fake training"):
        sft.main()

    persisted = json.loads(
        (output / "sft_run_manifest.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"
    assert persisted["base_model"]["resolved_revision"] == MODEL_COMMIT
    assert persisted["tokenizer"]["requested_revision"] == TOKENIZER_COMMIT
    assert persisted["tokenizer"]["resolved_revision"] == TOKENIZER_COMMIT
    assert persisted["tokenizer"]["resolved_revision_basis"] == (
        "PIN_ENFORCED_BY_LOADER_ARGUMENT/NOT_INDEPENDENTLY_RETURNED"
    )
    assert calls["tokenizer"] == [
        ("test-org/tokenizer", TOKENIZER_COMMIT)
    ]
    assert calls["model"] == [
        ("Qwen/Qwen2.5-7B-Instruct", MODEL_COMMIT)
    ]
    assert calls["trainer"] == 1


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

    corpus, corpus_manifest_path = _write_role_tool_training_corpus(
        tmp_path / "snapshot-race.jsonl"
    )
    original_bytes = corpus.read_bytes()
    assert canonical_bytes_sha256(original_bytes) != (
        SCENARIO_DERIVED_SYNTHETIC_CORPUS_SHA256
    )
    original_rows = [
        json.loads(line)
        for line in original_bytes.decode("utf-8").splitlines()
        if line
    ]
    selected_rows = [row for row in original_rows if row["role"] == "triage"]
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
            MODEL_COMMIT,
            "--data",
            str(corpus),
            "--output",
            str(output),
            "--role",
            "triage",
        ],
    )

    with pytest.raises(RuntimeError, match="stop before model loading"):
        sft.main()

    persisted = json.loads(
        (output / "sft_run_manifest.json").read_text(encoding="utf-8")
    )
    assert [row["scenario_id"] for row in captured["rows"]] == [
        row["scenario_id"] for row in selected_rows
    ]
    assert {row["role"] for row in captured["rows"]} == {"triage"}
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
    assert persisted["dataset"]["data_origin"] == "UNVERIFIED"
    assert persisted["dataset"]["synthetic"] is None
    assert persisted["dataset"]["data_origin_source"] == "unverified"
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
        resolved_model_revision=MODEL_COMMIT,
        resolved_tokenizer_revision=TOKENIZER_COMMIT,
        resolved_tokenizer_revision_basis="LOADER_EXPOSED_COMMIT_HASH_MATCH",
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
    assert persisted["base_model"]["resolved_revision"] == MODEL_COMMIT
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
