from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

import pytest

from training import sft_tokenizer_preflight as preflight
from training.sft_provenance import snapshot_training_corpus
from training.sft_rendering import TEMPLATE_PATH, render_messages

REVISION = preflight.PINNED_TOKENIZER_REVISION
CORPUS = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "evidence"
    / "stage7"
    / "candidates"
    / "train-candidate-v1"
    / "sft_corpus_train.jsonl"
)
_MARKERS = tuple(sorted(preflight.MASK_MARKERS, key=len, reverse=True))
_MARKER_IDS = {
    "<|im_start|>": 151644,
    "<|im_end|>": 151645,
    "<tool_call>": 151657,
    "</tool_call>": 151658,
}


def _local_bundle(directory: Path, *, revision: str = REVISION) -> Path:
    directory.mkdir()
    files = {
        "LICENSE": b"Apache-2.0",
        "config.json": json.dumps(
            {
                "architectures": ["Qwen2ForCausalLM"],
                "eos_token_id": 151645,
                "max_position_embeddings": 1_000_000,
                "model_type": "qwen2",
                "vocab_size": 152064,
            },
            sort_keys=True,
        ).encode(),
        "generation_config.json": b"{}",
        "merges.txt": b"#version: 0.2\n",
        "tokenizer.json": b'{"version":"1.0"}',
        "tokenizer_config.json": json.dumps(
            {
                "model_max_length": 1_000_000,
                "tokenizer_class": "Qwen2Tokenizer",
            },
            sort_keys=True,
        ).encode(),
        "vocab.json": b'{"token":0}',
    }
    for name, content in files.items():
        (directory / name).write_bytes(content)
    manifest = {
        "files": {
            name: {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
            for name, content in sorted(files.items())
        },
        "license": "apache-2.0",
        "model_weights_downloaded": False,
        "repository": preflight.TOKENIZER_REPOSITORY,
        "revision": revision,
        "schema_version": preflight.TOKENIZER_MANIFEST_SCHEMA_VERSION,
        "source": (
            f"https://huggingface.co/{preflight.TOKENIZER_REPOSITORY}/tree/{revision}"
        ),
        "total_bytes": sum(len(content) for content in files.values()),
    }
    (directory / preflight.TOKENIZER_MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return directory


class FakeTokenizer:
    is_fast = True
    eos_token = "<|im_end|>"
    eos_token_id = _MARKER_IDS[eos_token]
    unk_token_id = 0

    def __init__(self, *, leak_context: bool = False):
        self.leak_context = leak_context

    def convert_tokens_to_ids(self, token: str) -> int:
        return _MARKER_IDS.get(token, self.unk_token_id)

    def _tokenize(self, text: str) -> tuple[list[int], list[tuple[int, int]]]:
        ids: list[int] = []
        offsets: list[tuple[int, int]] = []
        index = 0
        while index < len(text):
            marker = next(
                (candidate for candidate in _MARKERS if text.startswith(candidate, index)),
                None,
            )
            if marker is not None:
                ids.append(_MARKER_IDS[marker])
                offsets.append((index, index + len(marker)))
                index += len(marker)
            else:
                ids.append(ord(text[index]) + 1)
                offsets.append((index, index + 1))
                index += 1
        return ids, offsets

    def __call__(self, text: str, **kwargs):
        assert kwargs == {
            "add_special_tokens": False,
            "return_offsets_mapping": True,
        }
        ids, offsets = self._tokenize(text)
        return {"input_ids": ids, "offset_mapping": offsets}

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
        rendered, spans = render_messages(messages, tools, track_generation=True)
        ranges = preflight._generation_ranges(rendered, spans, messages)
        ids, offsets = self._tokenize(rendered)
        masks = [
            any(start <= token_start and token_end <= end for start, end in ranges)
            for token_start, token_end in offsets
        ]
        if self.leak_context:
            first_context = next(index for index, value in enumerate(masks) if not value)
            masks[first_context] = True
        return {"input_ids": ids, "assistant_masks": masks}


def _patch_runtime(monkeypatch, tokenizer: FakeTokenizer) -> None:
    monkeypatch.setattr(
        preflight,
        "_runtime_versions",
        lambda: {
            "transformers": "4.57.6",
            "tokenizers": "0.22.2",
            "jinja2": "3.1.6",
            "python": "3.12.0",
        },
    )
    monkeypatch.setattr(preflight, "_load_tokenizer", lambda _directory: tokenizer)


def test_full_candidate_fails_at_2048_then_reports_reproducible_minimum(
    tmp_path,
    monkeypatch,
):
    tokenizer_dir = _local_bundle(tmp_path / "tokenizer")
    _patch_runtime(monkeypatch, FakeTokenizer())

    report = preflight.run_preflight(CORPUS, tokenizer_dir, REVISION, 2048)

    assert report["schema_version"] == "atlasops-sft-tokenizer-preflight-v1"
    assert report["status"] == "FAIL"
    assert report["settings"]["max_seq_length"] == 2048
    assert report["settings"]["tokenizer_repository"] == preflight.TOKENIZER_REPOSITORY
    assert report["settings"]["tokenizer_revision"] == REVISION
    assert report["candidate"]["corpus_version"] == "train-candidate-v1"
    assert report["candidate"]["d3_approval"] == "PENDING"
    assert report["candidate"]["total_examples"] == 68
    assert len(report["rows"]) == 68
    assert report["summary"]["mask_contract_pass"] is True
    assert report["summary"]["truncation_status"] == "TRUNCATION_REQUIRED"
    assert report["summary"]["truncated_row_count"] > 0
    assert report["summary"]["recommended_min_max_seq_length"] > 2048
    assert all(row["lengths_equal"] for row in report["rows"])
    assert all(row["target_count"] > 0 for row in report["rows"])
    assert all(row["mask_contract_pass"] for row in report["rows"])
    assert all(
        row["truncation_disposition"] == "WOULD_TRUNCATE"
        for row in report["rows"]
    )
    assert report["summary"]["model_weights_loaded"] is False
    assert report["summary"]["training_started"] is False
    assert report["summary"]["truncation_applied"] is False

    pass_report = preflight.run_preflight(
        CORPUS,
        tokenizer_dir,
        REVISION,
        report["summary"]["recommended_min_max_seq_length"],
    )
    assert pass_report["status"] == "PASS"
    assert pass_report["summary"]["truncated_row_count"] == 0
    assert all(row["truncation_disposition"] == "FITS" for row in pass_report["rows"])


def test_context_mask_leak_fails_offset_region_audit():
    snapshot = snapshot_training_corpus(CORPUS)

    result = preflight._measure_row(
        FakeTokenizer(leak_context=True),
        snapshot.rows[0],
        template=TEMPLATE_PATH.read_text(encoding="utf-8"),
        max_seq_len=100_000,
    )

    assert result["mask_contract_pass"] is False
    assert result["region_checks"]["system_user_tool_context_masked_out"] is False


def test_tokenizer_manifest_hash_and_allowlist_are_checked_before_loading(
    tmp_path,
    monkeypatch,
):
    tokenizer_dir = _local_bundle(tmp_path / "tokenizer")
    (tokenizer_dir / "tokenizer.json").write_bytes(b"changed")
    _patch_runtime(monkeypatch, FakeTokenizer())
    monkeypatch.setattr(
        preflight,
        "_load_tokenizer",
        lambda _directory: pytest.fail("invalid local bundle must fail before loading"),
    )

    with pytest.raises(ValueError, match="hash or size mismatch"):
        preflight.run_preflight(CORPUS, tokenizer_dir, REVISION, 2048)


def test_weight_file_is_rejected_without_reading_or_loading(tmp_path, monkeypatch):
    tokenizer_dir = _local_bundle(tmp_path / "tokenizer")
    (tokenizer_dir / "model.safetensors").write_bytes(b"not model weights")
    _patch_runtime(monkeypatch, FakeTokenizer())
    monkeypatch.setattr(
        preflight,
        "_load_tokenizer",
        lambda _directory: pytest.fail("unallowlisted file must fail before loading"),
    )

    with pytest.raises(ValueError, match="missing or unallowlisted"):
        preflight.run_preflight(CORPUS, tokenizer_dir, REVISION, 2048)


def test_tokenizer_loader_is_local_only_and_disables_remote_code(
    tmp_path,
    monkeypatch,
):
    calls = {}

    def from_pretrained(path, **kwargs):
        calls["path"] = path
        calls["kwargs"] = kwargs
        return object()

    transformers_stub = types.ModuleType("transformers")
    transformers_stub.AutoTokenizer = types.SimpleNamespace(from_pretrained=from_pretrained)
    monkeypatch.setitem(sys.modules, "transformers", transformers_stub)

    preflight._load_tokenizer(tmp_path)

    assert calls == {
        "path": str(tmp_path),
        "kwargs": {
            "local_files_only": True,
            "trust_remote_code": False,
            "use_fast": True,
        },
    }


def test_report_writer_refuses_to_clobber(tmp_path):
    destination = tmp_path / "report.json"
    preflight.write_report_no_clobber(destination, {"schema_version": "test"})
    original = destination.read_bytes()

    with pytest.raises(FileExistsError):
        preflight.write_report_no_clobber(destination, {"schema_version": "changed"})

    assert destination.read_bytes() == original


def test_manifest_revision_must_be_the_exact_pinned_commit(tmp_path):
    tokenizer_dir = _local_bundle(
        tmp_path / "tokenizer",
        revision="b" * 40,
    )

    with pytest.raises(ValueError, match="pinned Qwen2.5 revision"):
        preflight._local_tokenizer_bundle(tokenizer_dir, "b" * 40)
