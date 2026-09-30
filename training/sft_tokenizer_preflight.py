"""Offline tokenizer and assistant-mask preflight for the frozen Train candidate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from training.sft_candidate import (  # noqa: E402
    CORPUS_VERSION,
    SCHEMA_VERSION,
    validate_candidate_snapshot,
)
from training.sft_provenance import (  # noqa: E402
    MAX_VERIFIED_SFT_MANIFEST_BYTES,
    REPO_ROOT,
    _read_bounded_snapshot,
    canonical_bytes_sha256,
    canonical_json_sha256,
    file_sha256,
    has_redirecting_path_component,
    snapshot_training_corpus,
    validate_hf_commit_revision,
)
from training.sft_rendering import (  # noqa: E402
    TEMPLATE_PATH,
    prepare_example_for_training,
    render_messages,
)

REPORT_SCHEMA_VERSION = "atlasops-sft-tokenizer-preflight-v1"
TOKENIZER_MANIFEST_SCHEMA_VERSION = "atlasops-tokenizer-files-v1"
TOKENIZER_REPOSITORY = "Qwen/Qwen2.5-7B-Instruct"
PINNED_TOKENIZER_REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
TOKENIZER_MANIFEST_NAME = "tokenizer_files_manifest.json"
TOKENIZER_FILES = frozenset(
    {
        "LICENSE",
        "config.json",
        "generation_config.json",
        "merges.txt",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.json",
    }
)
MAX_TOKENIZER_FILE_BYTES = 16 * 1024 * 1024
MAX_TOKENIZER_BYTES = 32 * 1024 * 1024
PINNED_PACKAGES = {"transformers": "4.57.6", "tokenizers": "0.22.2"}
TRAINING_CONFIG_PATH = (
    REPO_ROOT / "artifacts" / "evidence" / "stage7" / "sft_training_config.json"
)
IMPLEMENTATION_PATHS = (
    "agents/coordinator.py",
    "agents/prompts/comms.md",
    "agents/prompts/diagnosis.md",
    "agents/prompts/remediation.md",
    "agents/prompts/triage.md",
    "agents/tool_policy.py",
    "requirements/train-constraints.txt",
    "training/sft_candidate.py",
    "training/sft_provenance.py",
    "training/sft_rendering.py",
    "training/sft_tokenizer_preflight.py",
    "training/templates/qwen2_5_tool_sft.jinja",
)
ASSISTANT_HEADER = "<|im_start|>assistant"
MASK_MARKERS = ("<|im_start|>", "<|im_end|>", "<tool_call>", "</tool_call>")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _local_tokenizer_bundle(
    tokenizer_dir: str | Path,
    revision: str,
) -> tuple[Path, dict[str, Any]]:
    validate_hf_commit_revision(revision, label="tokenizer revision")
    if revision != PINNED_TOKENIZER_REVISION:
        raise ValueError("tokenizer revision does not match the pinned Qwen2.5 revision")
    path_text = os.fspath(tokenizer_dir)
    if "://" in path_text:
        raise ValueError("tokenizer directory must be a local filesystem path")
    directory = Path(os.path.abspath(Path(path_text).expanduser()))
    if has_redirecting_path_component(directory) or not directory.is_dir():
        raise ValueError("tokenizer directory is missing or uses path redirects")

    manifest_path = directory / TOKENIZER_MANIFEST_NAME
    manifest_raw, status = _read_bounded_snapshot(
        manifest_path, MAX_VERIFIED_SFT_MANIFEST_BYTES
    )
    if manifest_raw is None:
        raise ValueError(f"tokenizer file manifest is unavailable ({status})")
    manifest = _json_object(manifest_raw, "tokenizer file manifest")
    if (
        manifest.get("schema_version") != TOKENIZER_MANIFEST_SCHEMA_VERSION
        or manifest.get("repository") != TOKENIZER_REPOSITORY
        or manifest.get("revision") != revision
        or manifest.get("license") != "apache-2.0"
        or manifest.get("source")
        != f"https://huggingface.co/{TOKENIZER_REPOSITORY}/tree/{revision}"
        or manifest.get("model_weights_downloaded") is not False
    ):
        raise ValueError("tokenizer file manifest does not match the pinned no-weights bundle")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != TOKENIZER_FILES:
        raise ValueError("tokenizer manifest must inventory exactly the allowlisted files")

    entries = list(directory.iterdir())
    if {entry.name for entry in entries} != TOKENIZER_FILES | {TOKENIZER_MANIFEST_NAME}:
        raise ValueError("tokenizer directory contains missing or unallowlisted files")
    inventory = []
    total_bytes = 0
    parsed: dict[str, dict[str, Any]] = {}
    for entry in sorted(entries, key=lambda item: item.name):
        if entry.name == TOKENIZER_MANIFEST_NAME:
            continue
        if has_redirecting_path_component(entry):
            raise ValueError("tokenizer files must not use symlink or hard-link redirects")
        record = files[entry.name]
        digest = record.get("sha256") if isinstance(record, dict) else None
        size = record.get("size_bytes") if isinstance(record, dict) else None
        if (
            not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
            or type(size) is not int
            or size < 0
        ):
            raise ValueError("tokenizer file inventory has invalid hashes or sizes")
        raw, status = _read_bounded_snapshot(entry, MAX_TOKENIZER_FILE_BYTES)
        if raw is None:
            raise ValueError(f"tokenizer file is unavailable ({entry.name}: {status})")
        actual_hash = hashlib.sha256(raw).hexdigest()
        if len(raw) != size or actual_hash != digest:
            raise ValueError(f"tokenizer file hash or size mismatch ({entry.name})")
        parsed[entry.name] = (
            _json_object(raw, entry.name)
            if entry.suffix == ".json"
            else {}
        )
        total_bytes += len(raw)
        inventory.append(
            {"name": entry.name, "size_bytes": len(raw), "sha256": actual_hash}
        )
    if total_bytes > MAX_TOKENIZER_BYTES or manifest.get("total_bytes") != total_bytes:
        raise ValueError("tokenizer bundle total byte count is invalid")
    if {entry.name for entry in directory.iterdir()} != {
        entry.name for entry in entries
    }:
        raise ValueError("tokenizer directory changed while it was inspected")

    model = parsed["config.json"]
    tokenizer_config = parsed["tokenizer_config.json"]
    if (
        model.get("model_type") != "qwen2"
        or "Qwen2ForCausalLM" not in model.get("architectures", [])
        or tokenizer_config.get("tokenizer_class")
        not in {"Qwen2Tokenizer", "Qwen2TokenizerFast"}
        or ("model_type" in tokenizer_config and tokenizer_config["model_type"] != "qwen2")
        or "auto_map" in model
        or "auto_map" in tokenizer_config
        or type(model.get("max_position_embeddings")) is not int
        or type(model.get("eos_token_id")) is not int
        or type(model.get("vocab_size")) is not int
    ):
        raise ValueError("local tokenizer/model configuration is not the pinned Qwen2 setup")
    return directory, {
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "file_inventory": inventory,
        "model_type": model["model_type"],
        "tokenizer_class": tokenizer_config["tokenizer_class"],
        "model_max_position_embeddings": model["max_position_embeddings"],
        "model_eos_token_id": model["eos_token_id"],
        "model_vocab_size": model["vocab_size"],
        "model_weights_downloaded": False,
    }


def _runtime_versions() -> dict[str, str]:
    versions = {
        package: importlib.metadata.version(package)
        for package in (*PINNED_PACKAGES, "jinja2")
    }
    if any(versions[name] != value for name, value in PINNED_PACKAGES.items()):
        raise ValueError("preflight requires transformers==4.57.6 and tokenizers==0.22.2")
    versions["python"] = platform.python_version()
    return versions


def _load_tokenizer(tokenizer_dir: Path) -> Any:
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("transformers is required for tokenizer preflight") from exc
    return AutoTokenizer.from_pretrained(
        str(tokenizer_dir),
        local_files_only=True,
        trust_remote_code=False,
        use_fast=True,
    )


def _sequence(value: Any, label: str) -> list[Any]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"tokenizer returned invalid {label}")
    if value and isinstance(value[0], (tuple, list)):
        if label == "offset_mapping" and len(value[0]) == 2:
            return list(value)
        raise ValueError(f"tokenizer returned batched {label}; expected one example")
    return list(value)


def _generation_ranges(
    rendered: str,
    spans: list[str],
    messages: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    if sum(message.get("role") == "assistant" for message in messages) != len(spans):
        raise ValueError("assistant generation spans do not match assistant turns")
    ranges = []
    cursor = 0
    for span in spans:
        header = rendered.find(ASSISTANT_HEADER, cursor)
        start = header + len(ASSISTANT_HEADER)
        end = start + len(span)
        if header < 0 or not span or rendered[start:end] != span:
            raise ValueError("assistant generation span is not an exact rendered substring")
        if not span.endswith("<|im_end|>"):
            raise ValueError("assistant generation span omits its closing im_end marker")
        ranges.append((start, end))
        cursor = end + (rendered[end:end + 1] == "\n")
    return ranges


def _expected_mask(
    start: int,
    end: int,
    ranges: list[tuple[int, int]],
) -> bool | None:
    overlapping = [(a, b) for a, b in ranges if start < b and end > a]
    if not overlapping:
        return False
    if any(start >= a and end <= b for a, b in overlapping):
        return True
    return None


def _range_mask_ok(
    start: int,
    end: int,
    expected: bool,
    offsets: list[tuple[int, int]],
    masks: list[bool],
) -> bool:
    found = False
    for (token_start, token_end), mask in zip(offsets, masks):
        if token_start == token_end == 0 or token_start >= end or token_end <= start:
            continue
        found = True
        if token_start < start or token_end > end or mask is not expected:
            return False
    return found


def _marker_ok(
    marker: str,
    tokenizer: Any,
    text: str,
    ids: list[int],
    masks: list[bool],
    ranges: list[tuple[int, int]],
) -> bool:
    marker_id = tokenizer.convert_tokens_to_ids(marker)
    if type(marker_id) is not int or marker_id == getattr(tokenizer, "unk_token_id", None):
        return False
    text_positions = [match.start() for match in re.finditer(re.escape(marker), text)]
    id_positions = [index for index, token_id in enumerate(ids) if token_id == marker_id]
    if len(text_positions) != len(id_positions):
        return False
    return all(
        (expected := _expected_mask(start, start + len(marker), ranges)) is not None
        and masks[index] is expected
        for start, index in zip(text_positions, id_positions)
    )


def _measure_row(
    tokenizer: Any,
    row: dict[str, Any],
    template: str,
    max_seq_len: int,
) -> dict[str, Any]:
    prepared = prepare_example_for_training(row)
    messages = prepared["messages"]
    rendered, spans = render_messages(messages, prepared["tools"], track_generation=True)
    ranges = _generation_ranges(rendered, spans, messages)
    encoded = tokenizer.apply_chat_template(
        messages,
        tools=prepared["tools"],
        chat_template=template,
        tokenize=True,
        return_dict=True,
        return_assistant_tokens_mask=True,
        add_generation_prompt=False,
    )
    ids = [int(item) for item in _sequence(encoded["input_ids"], "input_ids")]
    raw_masks = _sequence(encoded["assistant_masks"], "assistant_masks")
    if any(type(item) not in {int, bool} or int(item) not in {0, 1} for item in raw_masks):
        raise ValueError("assistant_masks must contain only binary values")
    masks = [bool(item) for item in raw_masks]
    offsets_encoded = tokenizer(
        rendered,
        add_special_tokens=False,
        return_offsets_mapping=True,
    )
    offset_ids = [int(item) for item in _sequence(offsets_encoded["input_ids"], "input_ids")]
    raw_offsets = _sequence(offsets_encoded["offset_mapping"], "offset_mapping")
    offsets = []
    for item in raw_offsets:
        if (
            not isinstance(item, (tuple, list))
            or len(item) != 2
            or type(item[0]) is not int
            or type(item[1]) is not int
        ):
            raise ValueError("fast tokenizer returned malformed offsets")
        offsets.append((item[0], item[1]))

    lengths_equal = len(ids) == len(masks)
    tokenization_matches = ids == offset_ids and len(ids) == len(offsets)
    marker_ids = {
        tokenizer.convert_tokens_to_ids(marker)
        for marker in MASK_MARKERS
        if type(tokenizer.convert_tokens_to_ids(marker)) is int
    }
    offsets_match = lengths_equal and tokenization_matches
    compared = 0
    if offsets_match:
        for token_id, (start, end), mask in zip(ids, offsets, masks):
            if start == end == 0:
                if token_id not in marker_ids:
                    offsets_match = False
                    break
                continue
            expected = _expected_mask(start, end, ranges)
            if start < 0 or end <= start or end > len(rendered) or expected is None:
                offsets_match = False
                break
            if mask is not expected:
                offsets_match = False
                break
            compared += 1

    markers_match = all(
        _marker_ok(marker, tokenizer, rendered, ids, masks, ranges)
        for marker in MASK_MARKERS
    )
    assistant_messages = [
        message for message in messages if message.get("role") == "assistant"
    ]
    span_checks = [
        _range_mask_ok(start, end, True, offsets, masks)
        for start, end in ranges
    ] if offsets_match else [False] * len(ranges)
    calls = sum(len(message.get("tool_calls") or []) for message in assistant_messages)
    conclusions = sum(
        bool(message.get("content")) and not message.get("tool_calls")
        for message in assistant_messages
    )
    call_checks = []
    conclusion_checks = []
    for message, (start, end) in zip(assistant_messages, ranges):
        span = rendered[start:end]
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            call_checks.append(
                span.count("<tool_call>") == len(tool_calls)
                and span.count("</tool_call>") == len(tool_calls)
                and _range_mask_ok(start, end, True, offsets, masks)
            )
        elif message.get("content"):
            conclusion_checks.append(
                message["content"] in span
                and _range_mask_ok(start, end, True, offsets, masks)
            )

    first_header = ranges[0][0] - len(ASSISTANT_HEADER)
    before_assistant = rendered[:first_header]
    schema_block_ok = (
        messages[0]["content"] in before_assistant
        and "# Tools" in before_assistant
        and all(
            json.dumps(schema, ensure_ascii=False) in before_assistant
            for schema in prepared["tools"]
        )
    )
    headers_context_only = all(
        _range_mask_ok(
            start - len(ASSISTANT_HEADER),
            start,
            False,
            offsets,
            masks,
        )
        for start, _end in ranges
    ) if offsets_match else False
    region_checks: dict[str, bool | None] = {
        "canonical_prompt_and_runtime_tool_schemas_present": schema_block_ok,
        "assistant_generation_spans_targeted": bool(span_checks) and all(span_checks),
        "assistant_tool_calls_targeted": None if calls == 0 else all(call_checks),
        "assistant_conclusions_targeted": (
            None if conclusions == 0 else all(conclusion_checks)
        ),
        "assistant_headers_context_only": headers_context_only,
        "assistant_end_markers_targeted": (
            markers_match
            and all(span.endswith("<|im_end|>") for span in spans)
        ),
        "assistant_tool_call_markers_targeted": (
            None
            if calls == 0
            else markers_match
        ),
        "system_user_tool_context_masked_out": offsets_match and markers_match,
    }
    mask_contract_pass = all(
        value is True or value is None for value in region_checks.values()
    )
    target_count = sum(masks)
    token_count = len(ids)
    return {
        "case_id": row["case_id"],
        "scenario_id": row["scenario_id"],
        "role": row["role"],
        "assistant_message_count": len(assistant_messages),
        "assistant_tool_call_count": calls,
        "assistant_conclusion_count": conclusions,
        "user_message_count": sum(message.get("role") == "user" for message in messages),
        "tool_observation_count": sum(message.get("role") == "tool" for message in messages),
        "runtime_tool_schema_count": len(prepared["tools"]),
        "token_count": token_count,
        "target_count": target_count,
        "context_count": len(masks) - target_count,
        "input_ids_length": token_count,
        "assistant_masks_length": len(masks),
        "lengths_equal": lengths_equal,
        "offset_tokens_compared": compared,
        "region_checks": region_checks,
        "mask_contract_pass": mask_contract_pass,
        "truncation_disposition": (
            "WOULD_TRUNCATE" if token_count > max_seq_len else "FITS"
        ),
        "tokens_over_limit": max(0, token_count - max_seq_len),
    }


def _artifact_hashes() -> tuple[dict[str, str], str, str, dict[str, Any]]:
    root = Path(__file__).resolve().parents[1]
    hashes = {
        relative: file_sha256(root / relative) for relative in IMPLEMENTATION_PATHS
    }
    config_hash = file_sha256(TRAINING_CONFIG_PATH)
    config = _json_object(TRAINING_CONFIG_PATH.read_bytes(), "frozen SFT config")
    return hashes, canonical_json_sha256(hashes), config_hash, config


def run_preflight(
    corpus_path: str | Path,
    tokenizer_dir: str | Path,
    revision: str,
    max_seq_len: int,
) -> dict[str, Any]:
    """Tokenize every candidate row locally, without loading weights or truncating."""
    if type(max_seq_len) is not int or max_seq_len < 1:
        raise ValueError("max_seq_len must be a positive integer")
    runtime_versions = _runtime_versions()
    tokenizer_dir, tokenizer = _local_tokenizer_bundle(tokenizer_dir, revision)
    if max_seq_len > tokenizer["model_max_position_embeddings"]:
        raise ValueError("max_seq_len exceeds the local model context limit")
    loaded = _load_tokenizer(tokenizer_dir)
    if (
        getattr(loaded, "is_fast", False) is not True
        or getattr(loaded, "eos_token", None) != "<|im_end|>"
        or getattr(loaded, "eos_token_id", None) != tokenizer["model_eos_token_id"]
    ):
        raise ValueError("local fast tokenizer does not match the pinned Qwen config")

    snapshot = snapshot_training_corpus(Path(corpus_path))
    manifest_path = snapshot.source_path.parent / "sft_corpus_manifest.json"
    manifest_raw, status = _read_bounded_snapshot(
        manifest_path, MAX_VERIFIED_SFT_MANIFEST_BYTES
    )
    if manifest_raw is None:
        raise ValueError(f"SFT candidate manifest is unavailable ({status})")
    manifest = _json_object(manifest_raw, "SFT candidate manifest")
    candidate = validate_candidate_snapshot(snapshot, manifest)
    if (
        candidate["corpus_version"] != CORPUS_VERSION
        or candidate["schema_version"] != SCHEMA_VERSION
        or candidate["split"] != "train"
        or candidate["technical_admissibility"] != "PASS"
        or candidate["d3_approval"] != "PENDING"
    ):
        raise ValueError("preflight requires the frozen Train candidate with D3 still pending")

    hashes, implementation_hash, config_hash, config = _artifact_hashes()
    if config.get("base_model") != TOKENIZER_REPOSITORY:
        raise ValueError("frozen SFT config does not name the pinned Qwen model")
    configured_max_seq_len = config.get("max_seq_length")
    if type(configured_max_seq_len) is not int:
        raise ValueError("frozen SFT config has no integer max_seq_length")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    rows = [
        _measure_row(loaded, row, template, max_seq_len) for row in snapshot.rows
    ]
    maximum_tokens = max(row["token_count"] for row in rows)
    truncated_rows = sum(
        row["truncation_disposition"] == "WOULD_TRUNCATE" for row in rows
    )
    mask_pass = all(row["mask_contract_pass"] for row in rows)
    report_status = (
        "PASS"
        if mask_pass
        and truncated_rows == 0
        and maximum_tokens <= tokenizer["model_max_position_embeddings"]
        else "FAIL"
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": report_status,
        "settings": {
            "tokenizer_repository": TOKENIZER_REPOSITORY,
            "tokenizer_revision": revision,
            "max_seq_length": max_seq_len,
            "frozen_config_max_seq_length": configured_max_seq_len,
            "model_max_position_embeddings": tokenizer[
                "model_max_position_embeddings"
            ],
        },
        "candidate": {
            "corpus_version": manifest["corpus_version"],
            "schema_version": manifest["schema_version"],
            "split": manifest["split"],
            "source_git_sha": manifest["source_git_sha"],
            "corpus_sha256_canonical_lf": canonical_bytes_sha256(snapshot.raw_bytes),
            "corpus_sha256_raw": hashlib.sha256(snapshot.raw_bytes).hexdigest(),
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "total_examples": candidate["total_examples"],
            "total_scenarios": candidate["total_scenarios"],
            "technical_admissibility": candidate["technical_admissibility"],
            "d3_approval": candidate["d3_approval"],
            "assistant_only_loss": manifest["assistant_only_loss"],
        },
        "tokenizer": {
            "repository": TOKENIZER_REPOSITORY,
            "revision": revision,
            "manifest_schema_version": TOKENIZER_MANIFEST_SCHEMA_VERSION,
            "manifest_sha256": tokenizer["manifest_sha256"],
            "model_type": tokenizer["model_type"],
            "tokenizer_class": tokenizer["tokenizer_class"],
            "model_weights_downloaded": tokenizer["model_weights_downloaded"],
            "file_inventory": tokenizer["file_inventory"],
            "runtime_versions": runtime_versions,
        },
        "artifacts": {
            "training_config_sha256": config_hash,
            "template_sha256": file_sha256(TEMPLATE_PATH),
            "implementation_sha256": implementation_hash,
            "implementation_file_sha256": hashes,
        },
        "summary": {
            "total_rows": len(rows),
            "checked_rows": len(rows),
            "mask_contract_pass": mask_pass,
            "truncation_status": (
                "NO_TRUNCATION_REQUIRED" if truncated_rows == 0 else "TRUNCATION_REQUIRED"
            ),
            "truncated_row_count": truncated_rows,
            "maximum_token_count": maximum_tokens,
            "recommended_min_max_seq_length": maximum_tokens,
            "recommended_minimum_supported_by_model": (
                maximum_tokens <= tokenizer["model_max_position_embeddings"]
            ),
            "model_weights_loaded": False,
            "inference_started": False,
            "training_started": False,
            "held_out_outcomes_accessed": False,
            "truncation_applied": False,
        },
        "rows": rows,
    }


def write_report_no_clobber(path: str | Path, report: dict[str, Any]) -> None:
    destination = Path(os.path.abspath(Path(path).expanduser()))
    if has_redirecting_path_component(destination):
        raise ValueError("preflight report path must not use symlink or reparse redirects")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, help="Frozen Train candidate JSONL path")
    parser.add_argument("--tokenizer-dir", required=True, help="Explicit local tokenizer directory")
    parser.add_argument("--revision", required=True, help="Pinned full 40-character tokenizer SHA")
    parser.add_argument("--max-seq-len", required=True, type=int)
    parser.add_argument("--output", required=True, help="New report path; existing files are refused")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = run_preflight(
        args.corpus,
        args.tokenizer_dir,
        args.revision,
        args.max_seq_len,
    )
    write_report_no_clobber(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "checked_rows": report["summary"]["checked_rows"],
                "recommended_min_max_seq_length": report["summary"][
                    "recommended_min_max_seq_length"
                ],
                "truncated_row_count": report["summary"]["truncated_row_count"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
