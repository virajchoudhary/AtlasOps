"""Tests for Stage 7: Generate SFT Data and Train (Gate G7).

Validates:
1. Complete SFT training corpus generation strictly bounded to TRAIN_SPLIT.
2. 100% test-set isolation invariant (zero scenarios from VAL_SPLIT or TEST_SPLIT).
3. Equal multi-agent role distribution (triage, diagnosis, remediation, comms).
4. All four curriculum tiers represented in the training corpus.
5. Strict adherence to openai-tool-messages-v1 format and message sequencing.
6. Qwen2.5 tool-calling SFT chat template renderability and generation span masking.
7. Training configuration schema and artifact persistence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import pytest

from config.splits import TEST_SPLIT, TRAIN_SPLIT, VAL_SPLIT
from training.generate_trajectories import SFT_EXAMPLE_FORMAT
from training.sft_rendering import prepare_example_for_training, render_messages


def _stub_builder_render_validation(monkeypatch, build_sft_dataset):
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


class TestStage7SFTPipeline:
    @pytest.fixture(scope="module")
    def generated_artifacts(self, tmp_path_factory):
        from training import build_sft_dataset

        root = tmp_path_factory.mktemp("stage7-sft")
        data_dir = root / "data"
        evidence_dir = root / "canonical-evidence"
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(build_sft_dataset, "DATA_DIR", data_dir)
        monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", evidence_dir)
        _stub_builder_render_validation(monkeypatch, build_sft_dataset)
        try:
            corpus, _ = build_sft_dataset.build_sft_corpus()
        finally:
            monkeypatch.undo()
        return (
            corpus,
            data_dir / "sft_corpus_manifest.json",
            data_dir / "sft_training_config.json",
        )

    @pytest.fixture
    def corpus_path(self, generated_artifacts) -> Path:
        return generated_artifacts[0]

    @pytest.fixture
    def manifest_path(self, generated_artifacts) -> Path:
        return generated_artifacts[1]

    @pytest.fixture
    def config_path(self, generated_artifacts) -> Path:
        return generated_artifacts[2]

    def test_sft_corpus_and_manifest_exist(self, corpus_path, manifest_path, config_path):
        assert corpus_path.exists(), f"Corpus file missing: {corpus_path}"
        assert manifest_path.exists(), f"Manifest file missing: {manifest_path}"
        assert config_path.exists(), f"Config file missing: {config_path}"

    def test_corpus_hash_matches_manifest(self, corpus_path, manifest_path):
        raw = corpus_path.read_bytes()
        canonical_sha = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["corpus_sha256_canonical_lf"] == canonical_sha
        assert manifest["total_examples"] == 64
        assert manifest["total_scenarios"] == 16
        assert manifest["data_origin"] == "scenario_derived_synthetic"
        assert manifest["synthetic"] is True

    def test_strict_split_isolation_no_val_or_test_leakage(self, corpus_path):
        val_scenarios = set(VAL_SPLIT)
        test_scenarios = set(TEST_SPLIT)
        train_scenarios = set(TRAIN_SPLIT)

        seen_scenarios = set()
        lines = corpus_path.read_text(encoding="utf-8").strip().splitlines()
        for line in lines:
            ex = json.loads(line)
            sid = ex["scenario_id"]
            seen_scenarios.add(sid)

            assert sid in train_scenarios, f"Scenario {sid} is not in TRAIN_SPLIT!"
            assert sid not in val_scenarios, f"CRITICAL LEAKAGE: Scenario {sid} from VAL_SPLIT in training corpus!"
            assert sid not in test_scenarios, f"CRITICAL LEAKAGE: Scenario {sid} from TEST_SPLIT in training corpus!"

        assert seen_scenarios == train_scenarios, "Training corpus must cover all 16 scenarios in TRAIN_SPLIT!"

    def test_role_and_tier_distribution(self, corpus_path):
        lines = corpus_path.read_text(encoding="utf-8").strip().splitlines()
        examples = [json.loads(line) for line in lines]

        roles = [ex["role"] for ex in examples]
        tiers = [ex["tier"] for ex in examples]

        for role in ("triage", "diagnosis", "remediation", "comms"):
            assert roles.count(role) == 16, f"Expected 16 examples for role {role}, got {roles.count(role)}"

        for tier in ("single_fault", "cascade", "multi_fault", "named_replays"):
            assert tier in tiers, f"Tier {tier} missing from training corpus!"

    def test_schema_and_tool_call_message_pairing(self, corpus_path):
        lines = corpus_path.read_text(encoding="utf-8").strip().splitlines()
        for line in lines:
            ex = json.loads(line)
            assert ex["format"] == SFT_EXAMPLE_FORMAT
            assert ex["messages"], "Messages list cannot be empty"

            messages = ex["messages"]
            for idx, msg in enumerate(messages):
                if msg.get("tool_calls"):
                    # Next message must be role: tool with matching tool_call_id
                    assert idx + 1 < len(messages), f"Missing tool observation after tool_calls at index {idx}"
                    tool_obs = messages[idx + 1]
                    assert tool_obs.get("role") == "tool"
                    assert tool_obs.get("tool_call_id") == msg["tool_calls"][0]["id"]

    def test_qwen_template_renderability_and_loss_masking(self, corpus_path):
        lines = corpus_path.read_text(encoding="utf-8").strip().splitlines()
        for line in lines:
            ex = json.loads(line)
            prepared = prepare_example_for_training(ex)
            rendered_text, gen_spans = render_messages(
                prepared["messages"], tools=prepared["tools"], track_generation=True
            )
            assert len(rendered_text) > 0
            assert len(gen_spans) > 0, f"No generation spans found for {ex['scenario_id']} role={ex['role']}"
            for span in gen_spans:
                assert len(span.strip()) > 0

    def test_sft_training_config_integrity(self, config_path, manifest_path):
        config = json.loads(config_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert config["train_corpus_sha256"] == manifest["corpus_sha256_canonical_lf"]
        assert config["lora_r"] == 16
        assert config["lora_alpha"] == 32
        assert config["assistant_only_loss"] is True


def test_custom_corpus_keeps_canonical_evidence_unchanged(tmp_path, monkeypatch):
    from training import build_sft_dataset

    _stub_builder_render_validation(monkeypatch, build_sft_dataset)
    canonical = tmp_path / "canonical-evidence"
    canonical.mkdir()
    original = {
        "sft_corpus_manifest.json": b"preserved manifest\n",
        "sft_training_config.json": b"preserved config\n",
    }
    for name, content in original.items():
        (canonical / name).write_bytes(content)
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", canonical)

    output = tmp_path / "isolated-run" / "sft_corpus_train.jsonl"
    corpus, manifest = build_sft_dataset.build_sft_corpus(output)

    assert corpus == output
    assert manifest["total_examples"] == 64
    assert manifest["corpus_sha256_canonical_lf"] == (
        "523cad3478e2018ebb830bab973bc02811045c6131dd0bf8f59328d756287e81"
    )
    assert manifest["data_origin"] == "scenario_derived_synthetic"
    assert manifest["synthetic"] is True
    for name, content in original.items():
        assert (canonical / name).read_bytes() == content
        assert (output.parent / name).is_file()
    adjacent = json.loads((output.parent / "sft_corpus_manifest.json").read_text(encoding="utf-8"))
    assert adjacent["corpus_file"] == output.as_posix()


def test_builder_refuses_symlinked_corpus_output(tmp_path, monkeypatch):
    from training import build_sft_dataset

    canonical = tmp_path / "canonical-evidence"
    canonical.mkdir()
    target = tmp_path / "redirect-target.jsonl"
    target.write_bytes(b"preserved target bytes\n")
    output = tmp_path / "corpus-link.jsonl"
    try:
        output.symlink_to(target)
    except OSError:
        pytest.skip("file symlink creation is unavailable on this host")
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", canonical)

    with pytest.raises(ValueError, match="symlink, junction, or hard link"):
        build_sft_dataset.build_sft_corpus(output)

    assert target.read_bytes() == b"preserved target bytes\n"


def test_builder_refuses_hardlinked_corpus_output(tmp_path, monkeypatch):
    from training import build_sft_dataset

    canonical = tmp_path / "canonical-evidence"
    canonical.mkdir()
    protected = canonical / "protected-corpus.jsonl"
    protected.write_bytes(b"preserved canonical corpus bytes\n")
    output = tmp_path / "corpus-hardlink.jsonl"
    try:
        os.link(protected, output)
    except OSError:
        pytest.skip("hard-link creation is unavailable on this host")
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", canonical)

    with pytest.raises(ValueError, match="symlink, junction, or hard link"):
        build_sft_dataset.build_sft_corpus(output)

    assert protected.read_bytes() == b"preserved canonical corpus bytes\n"
    assert output.read_bytes() == b"preserved canonical corpus bytes\n"


def test_builder_refuses_symlinked_corpus_output_directory(tmp_path, monkeypatch):
    from training import build_sft_dataset

    canonical = tmp_path / "canonical-evidence"
    canonical.mkdir()
    target_dir = tmp_path / "redirect-target"
    target_dir.mkdir()
    output_dir = tmp_path / "corpus-directory-link"
    try:
        output_dir.symlink_to(target_dir, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this host")
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", canonical)
    output = output_dir / "sft_corpus_train.jsonl"

    with pytest.raises(ValueError, match="symlink, junction, or hard link"):
        build_sft_dataset.build_sft_corpus(output)

    assert not (target_dir / "sft_corpus_train.jsonl").exists()
    assert not (target_dir / "sft_corpus_manifest.json").exists()


def test_builder_refuses_junctioned_corpus_output_directory(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows junctions are unavailable on this platform")
    from training import build_sft_dataset

    canonical = tmp_path / "canonical-evidence"
    canonical.mkdir()
    target_dir = tmp_path / "junction-target"
    target_dir.mkdir()
    output_dir = tmp_path / "corpus-junction"
    result = subprocess.run(
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/c",
            "mklink",
            "/J",
            str(output_dir),
            str(target_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation is unavailable on this host")
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", canonical)

    try:
        with pytest.raises(ValueError, match="symlink, junction, or hard link"):
            build_sft_dataset.build_sft_corpus(
                output_dir / "sft_corpus_train.jsonl"
            )
    finally:
        try:
            os.rmdir(output_dir)
        except FileNotFoundError:
            pass

    assert not (target_dir / "sft_corpus_train.jsonl").exists()
    assert not (target_dir / "sft_corpus_manifest.json").exists()


@pytest.mark.parametrize(
    "sidecar_name",
    ["sft_corpus_manifest.json", "sft_training_config.json"],
)
def test_builder_refuses_symlinked_sidecar_redirects(tmp_path, monkeypatch, sidecar_name):
    from training import build_sft_dataset

    canonical = tmp_path / "canonical-evidence"
    canonical.mkdir()
    protected = canonical / sidecar_name
    protected.write_bytes(b"preserved canonical bytes\n")
    output_dir = tmp_path / "isolated-output"
    output_dir.mkdir()
    try:
        (output_dir / sidecar_name).symlink_to(protected)
    except OSError:
        pytest.skip("file symlink creation is unavailable on this host")
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", canonical)
    output = output_dir / "sft_corpus_train.jsonl"

    with pytest.raises(ValueError, match="symlink, junction, or hard link"):
        build_sft_dataset.build_sft_corpus(output)

    assert protected.read_bytes() == b"preserved canonical bytes\n"
    assert not output.exists()


@pytest.mark.parametrize(
    "sidecar_name",
    ["sft_corpus_manifest.json", "sft_training_config.json"],
)
def test_builder_refuses_hardlinked_sidecar_redirects(
    tmp_path,
    monkeypatch,
    sidecar_name,
):
    from training import build_sft_dataset

    canonical = tmp_path / "canonical-evidence"
    canonical.mkdir()
    protected = canonical / sidecar_name
    protected.write_bytes(b"preserved canonical bytes\n")
    output_dir = tmp_path / "isolated-output"
    output_dir.mkdir()
    try:
        os.link(protected, output_dir / sidecar_name)
    except OSError:
        pytest.skip("hard-link creation is unavailable on this host")
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", canonical)
    output = output_dir / "sft_corpus_train.jsonl"

    with pytest.raises(ValueError, match="symlink, junction, or hard link"):
        build_sft_dataset.build_sft_corpus(output)

    assert protected.read_bytes() == b"preserved canonical bytes\n"
    assert not output.exists()


@pytest.mark.parametrize(
    "destination_name",
    [
        "sft_corpus_train.jsonl",
        "sft_corpus_manifest.json",
        "sft_training_config.json",
    ],
)
def test_builder_atomic_replace_does_not_follow_swapped_final_symlink(
    tmp_path,
    monkeypatch,
    destination_name,
):
    from training import build_sft_dataset

    _stub_builder_render_validation(monkeypatch, build_sft_dataset)
    output_dir = tmp_path / "isolated-output"
    output_dir.mkdir()
    canonical = tmp_path / "canonical-evidence"
    canonical.mkdir()
    monkeypatch.setattr(build_sft_dataset, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", canonical)

    protected = tmp_path / "protected-bytes.json"
    protected.write_bytes(b"preserved target bytes\n")
    output = output_dir / "sft_corpus_train.jsonl"
    destination = output_dir / destination_name
    original_validation = build_sft_dataset._validate_generated_parents
    injected = False

    def inject_final_symlink(paths):
        nonlocal injected
        if not injected:
            try:
                destination.symlink_to(protected)
            except OSError:
                pytest.skip("file symlink creation is unavailable on this host")
            injected = True
        original_validation(paths)

    monkeypatch.setattr(
        build_sft_dataset,
        "_validate_generated_parents",
        inject_final_symlink,
    )
    build_sft_dataset.build_sft_corpus(output)

    assert injected
    assert protected.read_bytes() == b"preserved target bytes\n"
    assert destination.is_file()
    assert not destination.is_symlink()


def test_custom_corpus_refuses_canonical_evidence_directory(tmp_path, monkeypatch):
    from training import build_sft_dataset

    canonical = tmp_path / "canonical-evidence"
    canonical.mkdir()
    marker = canonical / "sft_corpus_manifest.json"
    marker.write_bytes(b"preserved manifest\n")
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", canonical)

    with pytest.raises(ValueError, match="canonical Stage 7 evidence"):
        build_sft_dataset.build_sft_corpus(canonical / "custom.jsonl")
    assert marker.read_bytes() == b"preserved manifest\n"
    assert not (canonical / "custom.jsonl").exists()


def test_default_corpus_sidecars_preserve_frozen_evidence(tmp_path, monkeypatch):
    from training import build_sft_dataset

    _stub_builder_render_validation(monkeypatch, build_sft_dataset)
    data_dir = tmp_path / "data"
    evidence_dir = tmp_path / "canonical-evidence"
    frozen = {
        "sft_corpus_manifest.json": b"frozen manifest bytes\n",
        "sft_training_config.json": b"frozen config bytes\n",
    }
    evidence_dir.mkdir()
    for name, contents in frozen.items():
        (evidence_dir / name).write_bytes(contents)
    monkeypatch.setattr(build_sft_dataset, "DATA_DIR", data_dir)
    monkeypatch.setattr(build_sft_dataset, "EVIDENCE_DIR", evidence_dir)

    corpus, manifest = build_sft_dataset.build_sft_corpus()

    assert corpus == data_dir / "sft_corpus_train.jsonl"
    assert manifest["total_examples"] == 64
    assert manifest["data_origin"] == "scenario_derived_synthetic"
    assert manifest["synthetic"] is True
    assert (data_dir / "sft_corpus_manifest.json").is_file()
    assert (data_dir / "sft_training_config.json").is_file()
    for name, contents in frozen.items():
        assert (evidence_dir / name).read_bytes() == contents
