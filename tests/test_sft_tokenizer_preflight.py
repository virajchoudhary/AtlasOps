"""Offline contract tests for the frozen Stage 7 tokenizer preflight."""

from __future__ import annotations

import json
import os
import sys
import types

import pytest

from training import sft_tokenizer_preflight as preflight_module
from training.build_sft_candidate import build_candidate_rows
from training.sft_candidate import SOURCE_FILES
from training.sft_provenance import REPO_ROOT, snapshot_training_corpus
from training.sft_rendering import TEMPLATE_PATH
from training.sft_tokenizer_preflight import (
    _UnverifiedEncoding,
    _assistant_reference_mask,
    _render_template_with_generation_offsets,
    load_pinned_local_tokenizer,
    _parse_args,
    preflight_candidate,
    preflight_rows,
)


def test_each_assistant_turn_requires_token_coverage():
    with pytest.raises(_UnverifiedEncoding, match="ASSISTANT_TURN_WITHOUT_TOKENS"):
        _assistant_reference_mask([(0, 1), (2, 3)], [(0, 1), (4, 5)])


class CharacterTokenizer:
    is_fast = True
    all_special_ids: list[int] = []

    def __init__(self, mask_mode: str = "valid") -> None:
        self.mask_mode = mask_mode
        self.last_ids: list[int] = []
        self.last_mask: list[int] = []
        self.last_text = ""

    def apply_chat_template(
        self,
        messages,
        *,
        tools,
        chat_template,
        tokenize,
        return_dict,
        return_assistant_tokens_mask,
        add_generation_prompt,
    ):
        assert chat_template == TEMPLATE_PATH.read_text(encoding="utf-8")
        assert tokenize is True
        assert return_dict is True
        assert return_assistant_tokens_mask is True
        assert add_generation_prompt is False
        self.last_text, ranges = _render_template_with_generation_offsets(messages, tools)
        self.last_ids = [ord(char) for char in self.last_text]
        self.last_mask = [
            int(any(start <= index < end for start, end in ranges))
            for index in range(len(self.last_text))
        ]
        if self.mask_mode == "all_context":
            self.last_mask = [0] * len(self.last_mask)
        elif self.mask_mode == "all_target":
            self.last_mask = [1] * len(self.last_mask)
        elif self.mask_mode == "misaligned":
            self.last_mask[0] = 1 - self.last_mask[0]
        elif self.mask_mode == "short":
            self.last_mask = self.last_mask[:-1]
        if self.mask_mode == "missing":
            return {"input_ids": self.last_ids}
        return {"input_ids": self.last_ids, "assistant_masks": self.last_mask}

    def __call__(
        self,
        text,
        *,
        add_special_tokens,
        return_offsets_mapping,
        return_attention_mask,
        truncation,
    ):
        assert add_special_tokens is False
        assert return_offsets_mapping is True
        assert return_attention_mask is False
        assert truncation is False
        return {
            "input_ids": [ord(char) for char in text],
            "offset_mapping": [(index, index + 1) for index in range(len(text))],
        }


def test_preflight_verifies_project_template_mask_and_redacts_messages():
    row = build_candidate_rows()[0]
    tokenizer = CharacterTokenizer()

    report = preflight_rows([row], tokenizer, max_seq_length=1_000_000)

    assert report["status"] == "PASS"
    assert "candidate_version" not in report
    assert report["training_started"] is False
    assert report["model_weights_loaded"] is False
    assert report["evidence_written"] is False
    assert report["d2_approval"] == "NOT_ASSESSED"
    assert report["training_authorized"] is False
    assert report["truncation_applied"] is False
    assert report["tokenizer"]["revision_attestation"] == "CALLER_SUPPLIED_UNATTESTED"
    assert report["summary"]["rows"] == 1
    assert report["rows"] == [
        {
            "row_index": 0,
            "length": len(tokenizer.last_ids),
            "supervised_tokens": sum(tokenizer.last_mask),
            "tokens_requiring_truncation": 0,
            "truncated_supervised_tokens": 0,
            "final_assistant_tokens_after_limit": 0,
            "disposition": "FITS",
        }
    ]
    serialized_report = json.dumps(report, sort_keys=True)
    for message in row["messages"]:
        content = message.get("content")
        if content:
            assert content not in serialized_report


def test_max_sequence_length_boundaries_refuse_lost_final_assistant_tokens():
    row = build_candidate_rows()[0]
    tokenizer = CharacterTokenizer()

    unbounded = preflight_rows([row], tokenizer, max_seq_length=1_000_000)
    length = unbounded["rows"][0]["length"]
    last_target_index = max(
        index for index, is_target in enumerate(tokenizer.last_mask) if is_target
    )

    one_short = preflight_rows(
        [row],
        CharacterTokenizer(),
        max_seq_length=length - 1,
    )
    assert one_short["status"] == "REFUSE"
    assert one_short["rows"][0]["disposition"] == "REFUSE_CONTEXT_TRUNCATION"
    assert one_short["rows"][0]["tokens_requiring_truncation"] == 1
    assert one_short["rows"][0]["truncated_supervised_tokens"] == 0
    exact = preflight_rows([row], CharacterTokenizer(), max_seq_length=length)
    assert exact["status"] == "PASS"
    assert exact["rows"][0]["disposition"] == "FITS"

    truncated = preflight_rows([row], CharacterTokenizer(), max_seq_length=last_target_index)
    assert truncated["status"] == "REFUSE"
    assert truncated["rows"][0]["disposition"] == "REFUSE_FINAL_ASSISTANT_TRUNCATION"
    assert truncated["rows"][0]["final_assistant_tokens_after_limit"] > 0
    assert truncated["rows"][0]["truncated_supervised_tokens"] > 0


@pytest.mark.parametrize(
    ("mask_mode", "reason_code"),
    [
        ("short", "ASSISTANT_MASK_LENGTH_MISMATCH"),
        ("missing", "ASSISTANT_MASK_MISSING"),
        ("all_context", "ZERO_ASSISTANT_TARGETS"),
        ("all_target", "ALL_TOKENS_MARKED_AS_TARGET"),
        ("misaligned", "ASSISTANT_MASK_ATTRIBUTION_MISMATCH"),
    ],
)
def test_malformed_or_ambiguous_assistant_masks_are_unverified(mask_mode, reason_code):
    report = preflight_rows(
        [build_candidate_rows()[0]],
        CharacterTokenizer(mask_mode),
        max_seq_length=1_000_000,
    )

    assert report["status"] == "UNVERIFIED"
    assert report["summary"]["unverified"] == 1
    assert report["rows"][0]["disposition"] == "UNVERIFIED"
    assert report["rows"][0]["reason_code"] == reason_code


def test_tokenizer_without_fast_offsets_is_unverified():
    tokenizer = CharacterTokenizer()
    tokenizer.is_fast = False

    report = preflight_rows(
        [build_candidate_rows()[0]],
        tokenizer,
        max_seq_length=1_000_000,
    )

    assert report["status"] == "UNVERIFIED"
    assert report["rows"][0]["reason_code"] == "FAST_TOKENIZER_OFFSETS_UNAVAILABLE"


def test_frozen_candidate_traverses_all_rows_without_mutating_candidate_or_source():
    candidate_dir = REPO_ROOT / "artifacts/evidence/stage7/candidates/train-candidate-v1"
    corpus_path = candidate_dir / "sft_corpus_train.jsonl"
    manifest_path = candidate_dir / "sft_corpus_manifest.json"
    source_paths = [REPO_ROOT / relative_path for relative_path in SOURCE_FILES]
    before = {path: path.read_bytes() for path in [corpus_path, manifest_path, *source_paths]}

    tokenizer = CharacterTokenizer()
    row_report = preflight_rows(
        list(snapshot_training_corpus(corpus_path).rows),
        tokenizer,
        max_seq_length=1_000_000,
    )
    report = preflight_candidate(tokenizer, max_seq_length=1_000_000)

    after = {path: path.read_bytes() for path in before}
    assert after == before
    assert row_report["status"] == "PASS"
    assert row_report["summary"] == {
        "rows": 68,
        "fits": 68,
        "refused": 0,
        "unverified": 0,
    }
    assert report["status"] == "UNVERIFIED"
    assert report["reason_code"] == "CANDIDATE_ADMISSION_FAILED"
    assert report["candidate"]["d3_approval"] == "PENDING"


def test_tokenizer_loader_is_pinned_local_only_lazy_and_never_loads_weights(monkeypatch):
    revision = "a" * 40
    calls = []

    class FakeTokenizer:
        is_fast = True
        init_kwargs = {"_commit_hash": revision}

    class FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            assert os.environ["HF_HUB_OFFLINE"] == "1"
            assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
            assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
            calls.append((args, kwargs))
            return FakeTokenizer()

    class TransformersStub(types.ModuleType):
        def __getattr__(self, name):
            if name == "AutoModelForCausalLM":
                raise AssertionError("preflight must not import or load model weights")
            raise AttributeError(name)

    stub = TransformersStub("transformers")
    stub.AutoTokenizer = FakeAutoTokenizer
    monkeypatch.setitem(sys.modules, "transformers", stub)
    offline_keys = (
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
    )
    previous_environment = {key: os.environ.get(key) for key in offline_keys}

    tokenizer, provenance = load_pinned_local_tokenizer(
        "Qwen/Qwen2.5-7B-Instruct",
        revision,
    )

    assert isinstance(tokenizer, FakeTokenizer)
    assert calls == [
        (
            ("Qwen/Qwen2.5-7B-Instruct",),
            {
                "revision": revision,
                "local_files_only": True,
                "trust_remote_code": False,
                "use_fast": True,
            },
        )
    ]
    assert provenance == {
        "repository": "Qwen/Qwen2.5-7B-Instruct",
        "requested_revision": revision,
        "resolved_revision": revision,
        "revision_basis": "LOADER_EXPOSED_COMMIT_HASH_MATCH",
        "revision_attestation": "LOADER_PIN_ONLY_UNATTESTED",
        "local_file_inventory": "NOT_COLLECTED",
        "local_file_hashes": "NOT_COLLECTED",
        "load_mode": "LOCAL_CACHE_ONLY",
        "weights_loaded": False,
    }
    assert {key: os.environ.get(key) for key in offline_keys} == previous_environment


def test_local_cache_miss_is_redacted_and_keeps_network_disabled(monkeypatch):
    revision = "d" * 40
    calls = []

    class FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            calls.append((args, kwargs))
            raise OSError("raw prompt and secret should never appear in the report")

    stub = types.ModuleType("transformers")
    stub.AutoTokenizer = FakeAutoTokenizer
    monkeypatch.setitem(sys.modules, "transformers", stub)

    with pytest.raises(preflight_module._TokenizerLoadFailure) as failure:
        load_pinned_local_tokenizer("Qwen/Qwen2.5-7B-Instruct", revision)

    assert failure.value.reason_code == "PINNED_TOKENIZER_NOT_CACHED_OR_LOAD_FAILED"
    assert "raw prompt" not in str(failure.value)
    assert "secret" not in str(failure.value)
    assert calls[0][1] == {
        "revision": revision,
        "local_files_only": True,
        "trust_remote_code": False,
        "use_fast": True,
    }


def test_cli_requires_full_revision_and_positive_max_sequence_length():
    with pytest.raises(SystemExit):
        _parse_args(["--tokenizer", "Qwen/Qwen2.5-7B-Instruct"])
    with pytest.raises(SystemExit):
        _parse_args(
            [
                "--tokenizer",
                "Qwen/Qwen2.5-7B-Instruct",
                "--tokenizer-revision",
                "short",
            ]
        )
    with pytest.raises(SystemExit):
        _parse_args(
            [
                "--tokenizer",
                "Qwen/Qwen2.5-7B-Instruct",
                "--tokenizer-revision",
                "a" * 40,
                "--max-seq-length",
                "0",
            ]
        )


def test_cli_admits_candidate_before_refusing_missing_cached_tokenizer(
    monkeypatch,
    capsys,
):
    events = []
    revision = "b" * 40
    manifest = {
        "corpus_version": "train-candidate-v1",
        "corpus_sha256_canonical_lf": "c" * 64,
        "data_origin": "scenario_derived_synthetic_review_candidate",
        "synthetic": True,
        "split": "train",
        "held_out_outcomes_accessed": False,
    }
    inventory = {
        "total_examples": 68,
        "total_scenarios": 16,
        "technical_admissibility": "PASS",
        "d3_approval": "PENDING",
    }

    def admit():
        events.append("candidate-admitted")
        return None, manifest, inventory

    def missing_tokenizer(tokenizer_repository, tokenizer_revision):
        events.append("tokenizer-requested")
        raise preflight_module._TokenizerLoadFailure("PINNED_TOKENIZER_NOT_CACHED_OR_LOAD_FAILED")

    monkeypatch.setattr(preflight_module, "_admit_candidate", admit)
    monkeypatch.setattr(
        preflight_module,
        "load_pinned_local_tokenizer",
        missing_tokenizer,
    )

    exit_code = preflight_module.main(
        [
            "--tokenizer",
            "Qwen/Qwen2.5-7B-Instruct",
            "--tokenizer-revision",
            revision,
        ]
    )
    output = capsys.readouterr().out
    report = json.loads(output)

    assert exit_code == 2
    assert events == ["candidate-admitted", "tokenizer-requested"]
    assert report["status"] == "UNVERIFIED"
    assert report["reason_code"] == "PINNED_TOKENIZER_NOT_CACHED_OR_LOAD_FAILED"
    assert report["candidate"]["d3_approval"] == "PENDING"
    assert report["d2_approval"] == "NOT_ASSESSED"
    assert report["training_authorized"] is False
    assert report["truncation_applied"] is False
    assert report["training_started"] is False
    assert "secret" not in output.casefold()
    assert "prompt" not in output.casefold()
