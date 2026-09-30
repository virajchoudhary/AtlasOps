"""Offline, read-only token-length and assistant-mask preflight for SFT."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

import jinja2
import jinja2.sandbox

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.sft_candidate import read_candidate_manifest, validate_candidate_snapshot
from training.sft_provenance import (
    REPO_ROOT,
    resolve_tokenizer_revision,
    snapshot_training_corpus,
    validate_hf_commit_revision,
    validate_hf_reference,
)
from training.sft_rendering import (
    GenerationMarkerExtension,
    TEMPLATE_PATH,
    encode_example,
    prepare_example_for_training,
    render_messages,
)

DEFAULT_MAX_SEQ_LENGTH = 2048
_SCOPE = "offline_tokenizer_technical_preflight"
CANDIDATE_CORPUS_PATH = (
    REPO_ROOT / "artifacts/evidence/stage7/candidates/train-candidate-v1/sft_corpus_train.jsonl"
)


class _UnverifiedEncoding(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _TokenizerLoadFailure(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _OffsetGenerationExtension(GenerationMarkerExtension):
    """Wrap each template generation region with per-run offset markers."""

    def _capture(self, caller):  # noqa: D102 - Jinja extension protocol
        rendered = caller()
        index = self.environment._offset_generation_count
        self.environment._offset_generation_count += 1
        nonce = self.environment._offset_generation_nonce
        return (
            f"\x00ATLASOPS_{nonce}_BEGIN_{index}\x00"
            + rendered
            + f"\x00ATLASOPS_{nonce}_END_{index}\x00"
        )


def _render_template_with_generation_offsets(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> tuple[str, list[tuple[int, int]]]:
    """Render the owned template and return exact character ranges for assistant turns."""
    nonce = uuid.uuid4().hex
    env = jinja2.sandbox.ImmutableSandboxedEnvironment(
        trim_blocks=True,
        lstrip_blocks=True,
        extensions=[_OffsetGenerationExtension],
    )
    env.filters["tojson"] = lambda value: json.dumps(value, ensure_ascii=False)
    env._offset_generation_nonce = nonce
    env._offset_generation_count = 0
    tagged_text = env.from_string(TEMPLATE_PATH.read_text(encoding="utf-8")).render(
        messages=messages,
        tools=tools,
        add_generation_prompt=False,
    )

    ranges: list[tuple[int, int]] = []
    chunks: list[str] = []
    cursor = 0
    rendered_length = 0
    for index in range(env._offset_generation_count):
        start_marker = f"\x00ATLASOPS_{nonce}_BEGIN_{index}\x00"
        end_marker = f"\x00ATLASOPS_{nonce}_END_{index}\x00"
        if tagged_text.count(start_marker) != 1 or tagged_text.count(end_marker) != 1:
            raise _UnverifiedEncoding("GENERATION_BOUNDARIES_UNRESOLVED")
        start = tagged_text.find(start_marker, cursor)
        end = tagged_text.find(end_marker, start + len(start_marker))
        if start < cursor or end < 0:
            raise _UnverifiedEncoding("GENERATION_BOUNDARIES_UNRESOLVED")

        prefix = tagged_text[cursor:start]
        target = tagged_text[start + len(start_marker) : end]
        chunks.extend((prefix, target))
        rendered_length += len(prefix)
        ranges.append((rendered_length, rendered_length + len(target)))
        rendered_length += len(target)
        cursor = end + len(end_marker)
    chunks.append(tagged_text[cursor:])
    text = "".join(chunks)

    expected_text, expected_spans = render_messages(messages, tools, track_generation=True)
    if (
        text != expected_text
        or len(ranges) != len(expected_spans)
        or any(text[start:end] != span for (start, end), span in zip(ranges, expected_spans))
    ):
        raise _UnverifiedEncoding("GENERATION_BOUNDARIES_UNRESOLVED")
    return text, ranges


def _flat_values(value: Any, *, label: str) -> list[Any]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        raise _UnverifiedEncoding(f"{label}_SHAPE_INVALID")
    if any(isinstance(item, (list, tuple)) for item in value):
        raise _UnverifiedEncoding(f"{label}_BATCHED_OR_NESTED")
    return list(value)


def _integer_values(value: Any, *, label: str) -> list[int]:
    values = _flat_values(value, label=label)
    if any(type(item) is not int for item in values):
        raise _UnverifiedEncoding(f"{label}_VALUES_INVALID")
    return values


def _encoded_mask(encoded: Any, token_ids: list[int]) -> list[int]:
    if not hasattr(encoded, "get"):
        raise _UnverifiedEncoding("TOKENIZER_OUTPUT_INVALID")
    mask_fields = [
        name
        for name in ("assistant_masks", "assistant_tokens_mask")
        if encoded.get(name) is not None
    ]
    if not mask_fields:
        raise _UnverifiedEncoding("ASSISTANT_MASK_MISSING")

    masks: list[list[int]] = []
    for name in mask_fields:
        values = _flat_values(encoded.get(name), label=name.upper())
        if any(type(value) not in (bool, int) or value not in (0, 1) for value in values):
            raise _UnverifiedEncoding("ASSISTANT_MASK_VALUES_INVALID")
        masks.append([int(value) for value in values])
    if any(mask != masks[0] for mask in masks[1:]):
        raise _UnverifiedEncoding("ASSISTANT_MASK_FIELDS_CONFLICT")
    mask = masks[0]
    if len(mask) != len(token_ids):
        raise _UnverifiedEncoding("ASSISTANT_MASK_LENGTH_MISMATCH")
    if not any(mask):
        raise _UnverifiedEncoding("ZERO_ASSISTANT_TARGETS")
    if all(mask):
        raise _UnverifiedEncoding("ALL_TOKENS_MARKED_AS_TARGET")
    return mask


def _aligned_offsets(
    tokenizer: Any,
    rendered_text: str,
    token_ids: list[int],
) -> list[tuple[int, int]]:
    if getattr(tokenizer, "is_fast", False) is not True:
        raise _UnverifiedEncoding("FAST_TOKENIZER_OFFSETS_UNAVAILABLE")
    try:
        offset_output = tokenizer(
            rendered_text,
            add_special_tokens=False,
            return_offsets_mapping=True,
            return_attention_mask=False,
            truncation=False,
        )
    except Exception as exc:
        raise _UnverifiedEncoding("TOKENIZER_OFFSETS_UNAVAILABLE") from exc

    if not hasattr(offset_output, "get"):
        raise _UnverifiedEncoding("OFFSET_OUTPUT_INVALID")
    offset_ids = _integer_values(offset_output.get("input_ids"), label="OFFSET_INPUT_IDS")
    raw_offsets = offset_output.get("offset_mapping")
    if hasattr(raw_offsets, "tolist"):
        raw_offsets = raw_offsets.tolist()
    if (
        not isinstance(raw_offsets, (list, tuple))
        or len(raw_offsets) != len(offset_ids)
        or offset_ids != token_ids
    ):
        raise _UnverifiedEncoding("TOKENIZER_ID_OR_OFFSET_ALIGNMENT_MISMATCH")

    special_ids = set(getattr(tokenizer, "all_special_ids", ()) or ())
    previous_start = 0
    search_cursor = 0
    offsets: list[tuple[int, int]] = []
    for token_id, item in zip(offset_ids, raw_offsets):
        if hasattr(item, "tolist"):
            item = item.tolist()
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise _UnverifiedEncoding("OFFSET_ITEM_INVALID")
        start, end = item
        if type(start) is not int or type(end) is not int:
            raise _UnverifiedEncoding("OFFSET_ITEM_INVALID")
        if start == end:
            if (start, end) != (0, 0) or token_id not in special_ids:
                raise _UnverifiedEncoding("EMPTY_NON_SPECIAL_TOKEN_OFFSET")
            try:
                token_text = tokenizer.convert_ids_to_tokens(token_id)
            except Exception as exc:
                raise _UnverifiedEncoding("SPECIAL_TOKEN_OFFSET_UNRESOLVED") from exc
            if not isinstance(token_text, str) or not token_text:
                raise _UnverifiedEncoding("SPECIAL_TOKEN_OFFSET_UNRESOLVED")
            start = rendered_text.find(token_text, search_cursor)
            if start < 0:
                raise _UnverifiedEncoding("SPECIAL_TOKEN_OFFSET_UNRESOLVED")
            end = start + len(token_text)
        if start < previous_start or not 0 <= start < end <= len(rendered_text):
            raise _UnverifiedEncoding("OFFSET_ITEM_INVALID")
        previous_start = start
        search_cursor = max(search_cursor, end)
        offsets.append((start, end))
    return offsets


def _assistant_reference_mask(
    offsets: list[tuple[int, int]],
    generation_ranges: list[tuple[int, int]],
) -> tuple[list[int], list[list[int]]]:
    expected: list[int] = []
    token_indexes_by_turn: list[list[int]] = [[] for _ in generation_ranges]
    for token_index, (start, end) in enumerate(offsets):
        overlapping = [
            index
            for index, (turn_start, turn_end) in enumerate(generation_ranges)
            if start < turn_end and end > turn_start
        ]
        containing = [
            index
            for index, (turn_start, turn_end) in enumerate(generation_ranges)
            if turn_start <= start and end <= turn_end
        ]
        if len(containing) > 1 or (overlapping and len(containing) != 1):
            raise _UnverifiedEncoding("TOKEN_CROSSES_ASSISTANT_BOUNDARY")
        target = bool(containing)
        expected.append(int(target))
        if target:
            token_indexes_by_turn[containing[0]].append(token_index)
    if not any(expected) or all(expected):
        raise _UnverifiedEncoding("REFERENCE_MASK_HAS_NO_CONTEXT_OR_TARGET")
    if any(not indexes for indexes in token_indexes_by_turn):
        raise _UnverifiedEncoding("ASSISTANT_TURN_WITHOUT_TOKENS")
    return expected, token_indexes_by_turn


def load_pinned_local_tokenizer(
    tokenizer_repository: str,
    tokenizer_revision: str,
) -> tuple[Any, dict[str, Any]]:
    """Load a pinned tokenizer from the local Hugging Face cache only."""
    try:
        validate_hf_reference(
            tokenizer_repository,
            tokenizer_revision,
            label="tokenizer",
        )
    except ValueError as exc:
        raise _TokenizerLoadFailure("TOKENIZER_REFERENCE_INVALID") from exc
    offline_environment = {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    }
    try:
        with patch.dict(os.environ, offline_environment):
            from transformers import AutoTokenizer
    except Exception as exc:
        raise _TokenizerLoadFailure("TRANSFORMERS_UNAVAILABLE") from exc
    try:
        with patch.dict(os.environ, offline_environment):
            tokenizer = AutoTokenizer.from_pretrained(
                tokenizer_repository,
                revision=tokenizer_revision,
                local_files_only=True,
                trust_remote_code=False,
                use_fast=True,
            )
    except Exception as exc:
        raise _TokenizerLoadFailure("PINNED_TOKENIZER_NOT_CACHED_OR_LOAD_FAILED") from exc
    if getattr(tokenizer, "is_fast", False) is not True:
        raise _TokenizerLoadFailure("FAST_TOKENIZER_REQUIRED")

    init_kwargs = getattr(tokenizer, "init_kwargs", {})
    exposed_revision = init_kwargs.get("_commit_hash") if isinstance(init_kwargs, dict) else None
    try:
        resolved_revision, revision_basis = resolve_tokenizer_revision(
            tokenizer_revision,
            exposed_revision,
        )
    except ValueError as exc:
        raise _TokenizerLoadFailure("TOKENIZER_REVISION_UNVERIFIED") from exc
    return tokenizer, {
        "repository": tokenizer_repository,
        "requested_revision": tokenizer_revision,
        "resolved_revision": resolved_revision,
        "revision_basis": revision_basis,
        "revision_attestation": "LOADER_PIN_ONLY_UNATTESTED",
        "local_file_inventory": "NOT_COLLECTED",
        "local_file_hashes": "NOT_COLLECTED",
        "load_mode": "LOCAL_CACHE_ONLY",
        "weights_loaded": False,
    }


def _preflight_row(
    row: dict[str, Any],
    tokenizer: Any,
    *,
    row_index: int,
    max_seq_length: int,
) -> dict[str, Any]:
    try:
        encoded = encode_example(tokenizer, row)
        token_ids = _integer_values(encoded.get("input_ids"), label="INPUT_IDS")
        assistant_mask = _encoded_mask(encoded, token_ids)
        prepared = prepare_example_for_training(row)
        messages = prepared["messages"]
        assistants = [message for message in messages if message.get("role") == "assistant"]
        if (
            not assistants
            or not messages
            or messages[-1].get("role") != "assistant"
            or not (
                (isinstance(messages[-1].get("content"), str) and messages[-1]["content"].strip())
                or messages[-1].get("tool_calls")
            )
        ):
            raise _UnverifiedEncoding("FINAL_ASSISTANT_RESPONSE_MISSING")
        rendered, generation_ranges = _render_template_with_generation_offsets(
            messages, prepared["tools"]
        )
        if len(generation_ranges) != len(assistants):
            raise _UnverifiedEncoding("GENERATION_TURN_COUNT_MISMATCH")
        offsets = _aligned_offsets(tokenizer, rendered, token_ids)
        expected_mask, token_indexes_by_turn = _assistant_reference_mask(offsets, generation_ranges)
        if assistant_mask != expected_mask:
            raise _UnverifiedEncoding("ASSISTANT_MASK_ATTRIBUTION_MISMATCH")
    except _UnverifiedEncoding as exc:
        return {
            "row_index": row_index,
            "length": None,
            "supervised_tokens": None,
            "tokens_requiring_truncation": None,
            "truncated_supervised_tokens": None,
            "final_assistant_tokens_after_limit": None,
            "disposition": "UNVERIFIED",
            "reason_code": exc.reason_code,
        }
    except Exception:
        return {
            "row_index": row_index,
            "length": None,
            "supervised_tokens": None,
            "tokens_requiring_truncation": None,
            "truncated_supervised_tokens": None,
            "final_assistant_tokens_after_limit": None,
            "disposition": "UNVERIFIED",
            "reason_code": "TOKENIZER_ENCODING_FAILED",
        }

    length = len(token_ids)
    supervised_tokens = sum(assistant_mask)
    tokens_requiring_truncation = max(0, length - max_seq_length)
    truncated_supervised_tokens = sum(assistant_mask[max_seq_length:])
    final_assistant_tokens_after_limit = sum(
        token_index >= max_seq_length for token_index in token_indexes_by_turn[-1]
    )
    if length <= max_seq_length:
        disposition = "FITS"
    elif final_assistant_tokens_after_limit:
        disposition = "REFUSE_FINAL_ASSISTANT_TRUNCATION"
    elif truncated_supervised_tokens:
        disposition = "REFUSE_ASSISTANT_TARGET_TRUNCATION"
    else:
        disposition = "REFUSE_CONTEXT_TRUNCATION"
    return {
        "row_index": row_index,
        "length": length,
        "supervised_tokens": supervised_tokens,
        "tokens_requiring_truncation": tokens_requiring_truncation,
        "truncated_supervised_tokens": truncated_supervised_tokens,
        "final_assistant_tokens_after_limit": final_assistant_tokens_after_limit,
        "disposition": disposition,
    }


def preflight_rows(
    rows: Sequence[dict[str, Any]],
    tokenizer: Any,
    *,
    max_seq_length: int = DEFAULT_MAX_SEQ_LENGTH,
) -> dict[str, Any]:
    """Check assistant-mask attribution and max-length fit without training or writes."""
    if type(max_seq_length) is not int or max_seq_length < 1:
        raise ValueError("max_seq_length must be a positive integer")
    if not rows:
        raise ValueError("cannot preflight an empty SFT candidate")

    row_reports = [
        _preflight_row(
            row,
            tokenizer,
            row_index=index,
            max_seq_length=max_seq_length,
        )
        for index, row in enumerate(rows)
    ]
    dispositions = [row["disposition"] for row in row_reports]
    unverified = sum(disposition == "UNVERIFIED" for disposition in dispositions)
    refused = sum(disposition.startswith("REFUSE_") for disposition in dispositions)
    status = "UNVERIFIED" if unverified else "REFUSE" if refused else "PASS"
    return {
        "scope": _SCOPE,
        "status": status,
        "tokenizer": {
            "revision_attestation": "CALLER_SUPPLIED_UNATTESTED",
            "local_file_inventory": "NOT_COLLECTED",
            "local_file_hashes": "NOT_COLLECTED",
        },
        "max_seq_length": max_seq_length,
        "rows": row_reports,
        "summary": {
            "rows": len(row_reports),
            "fits": sum(disposition == "FITS" for disposition in dispositions),
            "refused": refused,
            "unverified": unverified,
        },
        "technical_admissibility": "TOKENIZER_PREFLIGHT_ONLY",
        "d3_approval": "PENDING",
        "d2_approval": "NOT_ASSESSED",
        "training_authorized": False,
        "training_started": False,
        "model_weights_loaded": False,
        "truncation_applied": False,
        "evidence_written": False,
    }


def _candidate_admission_failure(
    max_seq_length: int = DEFAULT_MAX_SEQ_LENGTH,
) -> dict[str, Any]:
    return {
        "scope": _SCOPE,
        "status": "UNVERIFIED",
        "candidate_version": "train-candidate-v1",
        "candidate": {
            "version": "train-candidate-v1",
            "examples": None,
            "scenarios": None,
            "corpus_sha256": None,
            "technical_admissibility": "UNVERIFIED",
            "d3_approval": "PENDING",
        },
        "max_seq_length": max_seq_length,
        "rows": [],
        "summary": {"rows": 0, "fits": 0, "refused": 0, "unverified": 0},
        "technical_admissibility": "UNVERIFIED",
        "d2_approval": "NOT_ASSESSED",
        "training_authorized": False,
        "reason_code": "CANDIDATE_ADMISSION_FAILED",
        "training_started": False,
        "model_weights_loaded": False,
        "truncation_applied": False,
        "evidence_written": False,
    }


def _admit_candidate() -> tuple[Any, dict[str, Any], dict[str, Any]]:
    snapshot = snapshot_training_corpus(CANDIDATE_CORPUS_PATH)
    manifest = read_candidate_manifest(CANDIDATE_CORPUS_PATH)
    inventory = validate_candidate_snapshot(snapshot, manifest)
    return snapshot, manifest, inventory


def _candidate_metadata(
    manifest: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": manifest["corpus_version"],
        "examples": inventory["total_examples"],
        "scenarios": inventory["total_scenarios"],
        "corpus_sha256": manifest["corpus_sha256_canonical_lf"],
        "data_origin": manifest["data_origin"],
        "synthetic": manifest["synthetic"],
        "split": manifest["split"],
        "held_out_outcomes_accessed": manifest["held_out_outcomes_accessed"],
        "technical_admissibility": inventory["technical_admissibility"],
        "d3_approval": inventory["d3_approval"],
    }


def _preflight_admitted_candidate(
    snapshot: Any,
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    tokenizer: Any,
    *,
    max_seq_length: int,
) -> dict[str, Any]:
    report = preflight_rows(
        snapshot.rows,
        tokenizer,
        max_seq_length=max_seq_length,
    )
    report["candidate_version"] = manifest["corpus_version"]
    report["candidate"] = _candidate_metadata(manifest, inventory)
    return report


def preflight_candidate(
    tokenizer: Any,
    *,
    max_seq_length: int = DEFAULT_MAX_SEQ_LENGTH,
) -> dict[str, Any]:
    """Admit and inspect the single frozen Train-only review candidate."""
    try:
        snapshot, manifest, inventory = _admit_candidate()
    except Exception:
        return _candidate_admission_failure(max_seq_length)
    return _preflight_admitted_candidate(
        snapshot,
        manifest,
        inventory,
        tokenizer,
        max_seq_length=max_seq_length,
    )


def _positive_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _full_commit_sha(value: str) -> str:
    try:
        validate_hf_commit_revision(value, label="tokenizer revision")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only offline tokenizer/mask/length preflight for "
            "train-candidate-v1. Does not train or load model weights."
        )
    )
    parser.add_argument(
        "--tokenizer",
        required=True,
        help="Hugging Face tokenizer repository id already present in the local cache",
    )
    parser.add_argument(
        "--tokenizer-revision",
        required=True,
        type=_full_commit_sha,
        help="Full 40-character immutable Hugging Face commit SHA",
    )
    parser.add_argument(
        "--max-seq-length",
        "--max-seq-len",
        dest="max_seq_length",
        type=_positive_limit,
        default=DEFAULT_MAX_SEQ_LENGTH,
        help=f"Refuse any context truncation; default: {DEFAULT_MAX_SEQ_LENGTH}",
    )
    return parser.parse_args(argv)


def _tokenizer_failure_report(
    reason_code: str,
    max_seq_length: int,
    *,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "scope": _SCOPE,
        "status": "UNVERIFIED",
        "candidate_version": "train-candidate-v1",
        "candidate": candidate,
        "max_seq_length": max_seq_length,
        "rows": [],
        "summary": {"rows": 0, "fits": 0, "refused": 0, "unverified": 0},
        "technical_admissibility": "UNVERIFIED",
        "reason_code": reason_code,
        "d2_approval": "NOT_ASSESSED",
        "d3_approval": "PENDING",
        "training_authorized": False,
        "training_started": False,
        "model_weights_loaded": False,
        "truncation_applied": False,
        "evidence_written": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        validate_hf_reference(
            args.tokenizer,
            args.tokenizer_revision,
            label="tokenizer",
        )
    except ValueError:
        report = _tokenizer_failure_report(
            "TOKENIZER_REFERENCE_INVALID",
            args.max_seq_length,
        )
        print(json.dumps(report, sort_keys=True))
        return 2

    try:
        snapshot, manifest, inventory = _admit_candidate()
    except Exception:
        report = _candidate_admission_failure(args.max_seq_length)
        print(json.dumps(report, sort_keys=True))
        return 2

    candidate_metadata = _candidate_metadata(manifest, inventory)
    try:
        tokenizer, tokenizer_provenance = load_pinned_local_tokenizer(
            args.tokenizer,
            args.tokenizer_revision,
        )
    except _TokenizerLoadFailure as exc:
        report = _tokenizer_failure_report(
            exc.reason_code,
            args.max_seq_length,
            candidate=candidate_metadata,
        )
        print(json.dumps(report, sort_keys=True))
        return 2
    except Exception:
        report = _tokenizer_failure_report(
            "TOKENIZER_PREFLIGHT_FAILED",
            args.max_seq_length,
            candidate=candidate_metadata,
        )
        print(json.dumps(report, sort_keys=True))
        return 2

    report = _preflight_admitted_candidate(
        snapshot,
        manifest,
        inventory,
        tokenizer,
        max_seq_length=args.max_seq_length,
    )
    report["tokenizer"] = tokenizer_provenance
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
