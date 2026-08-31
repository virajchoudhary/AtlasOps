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
from pathlib import Path
import pytest

from config.splits import TEST_SPLIT, TRAIN_SPLIT, VAL_SPLIT, get_split
from training.build_sft_dataset import build_sft_corpus
from training.generate_trajectories import SFT_EXAMPLE_FORMAT
from training.sft_rendering import prepare_example_for_training, render_messages


class TestStage7SFTPipeline:
    @pytest.fixture
    def corpus_path(self) -> Path:
        p = Path("data/sft_corpus_train.jsonl")
        if not p.exists():
            build_sft_corpus(p)
        return p

    @pytest.fixture
    def manifest_path(self) -> Path:
        return Path("artifacts/evidence/stage7/sft_corpus_manifest.json")

    @pytest.fixture
    def config_path(self) -> Path:
        return Path("artifacts/evidence/stage7/sft_training_config.json")

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
