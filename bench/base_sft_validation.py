"""Matched local Base-vs-SFT diagnostic evaluation on the frozen Validation split."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUN_SCHEMA = "atlasops-base-sft-validation-run-v1"
RAW_SCHEMA = "atlasops-base-sft-validation-raw-v1"
SCORED_SCHEMA = "atlasops-base-sft-validation-scored-v1"
ARMS = ("base", "sft")
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
EVALUATION_CONFIG: dict[str, Any] = {
    "split": "val",
    "seed": 1337,
    "temperature": 0.0,
    "top_p": 1.0,
    "max_new_tokens": 512,
    "do_sample": False,
    "device": "cuda:0",
    "torch_dtype": "float16",
    "attention_implementation": "sdpa",
    "quantization": {
        "load_in_4bit": True,
        "bnb_4bit_quant_type": "nf4",
        "bnb_4bit_use_double_quant": True,
        "bnb_4bit_compute_dtype": "float16",
    },
}


def _runtime_modules() -> tuple[Any, Any]:
    from bench import sft_eval
    from scripts import collect_sft_remote_provenance

    return sft_eval, collect_sft_remote_provenance


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _exception_record(exc: BaseException) -> dict[str, str]:
    message = str(exc).replace("\x00", "")[:2000]
    return {
        "error_type": type(exc).__name__,
        "error_message": message or type(exc).__name__,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    raw = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
    with temporary.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _write_jsonl_row(stream: Any, row: dict[str, Any]) -> None:
    stream.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def _validate_output_dir(output_dir: Path, checkpoint: Path, base_snapshot: Path) -> Path:
    output = Path(output_dir)
    if not output.is_absolute():
        raise ValueError("Output directory must be an absolute, unique path")
    if not output.parent.is_dir():
        raise ValueError("Output directory parent must already exist")
    if os.path.lexists(output):
        raise FileExistsError("Output directory already exists; refusing to overwrite")

    output_identity = output.parent.resolve() / output.name
    for input_path, label in (
        (checkpoint, "checkpoint"),
        (base_snapshot, "base snapshot"),
    ):
        input_identity = Path(input_path).resolve(strict=False)
        if output_identity == input_identity or output_identity.is_relative_to(input_identity):
            raise ValueError(f"Output directory must not be inside the {label}")
    return output


def _validate_device_map(model: Any) -> None:
    device_map = getattr(model, "hf_device_map", None)
    if isinstance(device_map, dict) and device_map:
        for device in device_map.values():
            if device not in (0, "0", "cuda:0"):
                raise RuntimeError("Model device map must place every layer on cuda:0")

    for tensor in (*list(model.parameters()), *list(model.buffers())):
        if str(tensor.device) != "cuda:0":
            raise RuntimeError("Model parameters and buffers must remain resident on cuda:0")


class RawDecodeError(RuntimeError):
    def __init__(self, token_ids: list[int], cause: Exception):
        super().__init__(str(cause))
        self.token_ids = token_ids
        self.cause = cause


class LocalPairedInference:
    """One pinned, quantized PEFT model used for both ordered evaluation arms."""

    def __init__(self, base_snapshot: Path, checkpoint: Path):
        self.base_snapshot = Path(base_snapshot)
        self.checkpoint = Path(checkpoint)
        self.torch: Any = None
        self.tokenizer: Any = None
        self.model: Any = None
        self.runtime: dict[str, Any] = {}

    def load(self) -> None:
        import peft
        import torch
        import transformers
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        self.torch = torch
        self.runtime = {
            "platform": {
                "system": platform.system(),
                "machine": platform.machine(),
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
            },
            "package_versions": {
                "torch": torch.__version__,
                "transformers": transformers.__version__,
                "peft": peft.__version__,
                "bitsandbytes": importlib.metadata.version("bitsandbytes"),
            },
            "cuda_runtime_version": getattr(torch.version, "cuda", None),
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_device_count": int(torch.cuda.device_count()),
            "cuda_device_name": None,
            "cuda_current_device": None,
            "model_device_map": None,
        }
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeError(
                "Evaluation requires exactly one visible CUDA device; expose only cuda:0"
            )
        torch.cuda.set_device(0)
        if torch.cuda.current_device() != 0:
            raise RuntimeError("Evaluation CUDA device must be cuda:0")

        self.runtime["cuda_device_name"] = str(torch.cuda.get_device_name(0))
        self.runtime["cuda_current_device"] = int(torch.cuda.current_device())
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(self.base_snapshot),
            local_files_only=True,
            trust_remote_code=False,
        )
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        base_model = AutoModelForCausalLM.from_pretrained(
            str(self.base_snapshot),
            local_files_only=True,
            trust_remote_code=False,
            quantization_config=quantization,
            torch_dtype=torch.float16,
            attn_implementation="sdpa",
            device_map={"": 0},
        )
        self.model = PeftModel.from_pretrained(
            base_model,
            str(self.checkpoint),
            is_trainable=False,
            local_files_only=True,
        )
        self.model.eval()
        _validate_device_map(self.model)
        self.runtime["model_device_map"] = getattr(self.model, "hf_device_map", None)

    def encode(self, messages: list[dict[str, str]]) -> tuple[Any, list[int]]:
        if self.tokenizer is None:
            raise RuntimeError("Tokenizer did not load")
        prompt = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        if not isinstance(prompt, str):
            raise TypeError("Tokenizer chat template must return a string prompt")
        encoding = self.tokenizer(prompt, return_tensors="pt")
        input_ids = encoding["input_ids"].tolist()[0]
        moved = encoding.to("cuda:0")
        return moved, [int(token_id) for token_id in input_ids]

    def generate(self, arm: str, inputs: Any) -> tuple[list[int], str]:
        if arm not in ARMS:
            raise ValueError(f"Unsupported evaluation arm: {arm!r}")
        if self.model is None or self.torch is None or self.tokenizer is None:
            raise RuntimeError("Evaluation model is not loaded")

        self.torch.manual_seed(EVALUATION_CONFIG["seed"])
        self.torch.cuda.manual_seed_all(EVALUATION_CONFIG["seed"])

        def _run_generation() -> Any:
            with self.torch.no_grad():
                return self.model.generate(
                    **inputs,
                    max_new_tokens=EVALUATION_CONFIG["max_new_tokens"],
                    do_sample=EVALUATION_CONFIG["do_sample"],
                    temperature=EVALUATION_CONFIG["temperature"],
                    top_p=EVALUATION_CONFIG["top_p"],
                )

        if arm == "base":
            with self.model.disable_adapter():
                output = _run_generation()
        else:
            output = _run_generation()

        prompt_length = inputs["input_ids"].shape[1]
        output_token_ids = output[0][prompt_length:].tolist()
        try:
            raw_text = self.tokenizer.decode(output_token_ids, skip_special_tokens=True)
        except Exception as exc:
            raise RawDecodeError(output_token_ids, exc) from exc
        if not isinstance(raw_text, str):
            raise TypeError("Tokenizer decode must return text")
        return [int(token_id) for token_id in output_token_ids], raw_text


def _base_and_tokenizer_match(
    manifest: dict[str, Any],
    collector: Any,
    base_snapshot: Path,
    inventory: dict[str, Any],
) -> None:
    expected_identity = (collector.MODEL_REPOSITORY, collector.MODEL_REVISION)
    for field in ("base_model", "tokenizer"):
        record = manifest.get(field)
        actual = (
            (
                record.get("id"),
                record.get("resolved_revision"),
            )
            if isinstance(record, dict)
            else (None, None)
        )
        if actual != expected_identity:
            raise ValueError(
                f"Checkpoint {field} identity does not match the pinned model inventory"
            )
    if (
        inventory.get("repository") != collector.MODEL_REPOSITORY
        or inventory.get("revision") != collector.MODEL_REVISION
        or Path(inventory.get("snapshot_dir", "")).resolve() != Path(base_snapshot).resolve()
        or inventory.get("model_weights_loaded") is not False
        or inventory.get("network_accessed") is not False
    ):
        raise ValueError("Local base snapshot does not match the collector's pinned inventory")


def _recorded_training_data_provenance(
    checkpoint: Path,
    expected_manifest_sha256: str,
) -> dict[str, Any]:
    raw = (checkpoint / "sft_run_manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
        raise ValueError("Checkpoint provenance manifest changed after verification")
    source_manifest = json.loads(raw.decode("utf-8"))
    dataset = source_manifest.get("dataset")
    if not isinstance(dataset, dict):
        raise TypeError("Checkpoint provenance has no recorded dataset object")
    keys = (
        "data_origin",
        "synthetic",
        "data_origin_source",
        "corpus_sha256_canonical_lf",
        "split",
        "split_sha256",
        "total_examples",
        "total_scenarios",
        "corpus_manifest",
    )
    return {
        "source": "checkpoint_manifest_as_recorded",
        "fields": {key: dataset[key] for key in keys if key in dataset},
    }


def _prompt_hash(token_ids: list[int] | None) -> str | None:
    return None if token_ids is None else _canonical_sha256(token_ids)


def _new_episode(
    *,
    run_id: str,
    pair_index: int,
    scenario_id: str,
    arm: str,
    messages: list[dict[str, str]],
    token_ids: list[int] | None,
    evaluation_config_sha256: str,
    checkpoint_identity: dict[str, Any] | None,
    model_identity: dict[str, Any] | None,
    inference_status: str,
    inference_seconds: float | None,
    evaluation_generation_config: dict[str, Any],
    raw_text: str | None = None,
    output_token_ids: list[int] | None = None,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema_version": RAW_SCHEMA,
        "run_id": run_id,
        "pair_index": pair_index,
        "scenario_id": scenario_id,
        "arm": arm,
        "arm_order": ARMS.index(arm),
        "request_messages": messages,
        "prompt_token_ids": token_ids,
        "prompt_token_ids_sha256": _prompt_hash(token_ids),
        "evaluation_config_sha256": evaluation_config_sha256,
        "evaluation_generation_config": evaluation_generation_config,
        "generation_config": evaluation_generation_config,
        "model_identity": copy.deepcopy(model_identity),
        "checkpoint_identity": copy.deepcopy(checkpoint_identity),
        "adapter_state": "disabled" if arm == "base" else "enabled",
        "inference_status": inference_status,
        "inference_attempted": inference_status not in {"not_executed"},
        "inference_seconds": inference_seconds,
        "response_received": inference_status == "response_received",
        "generated_token_ids": output_token_ids,
        "raw_model_response": raw_text,
        "error": error,
        "recorded_at_utc": _utc_now(),
    }
    return row


def _score_episode(
    raw_row: dict[str, Any],
    *,
    expected_root_cause: str,
    evaluator: Any,
) -> dict[str, Any]:
    row = dict(raw_row)
    row["schema_version"] = SCORED_SCHEMA
    row["format_compliant"] = False
    row["prediction"] = None
    row["diagnostic_precision"] = None
    row["diagnostic_recall"] = None
    row["diagnostic_f1"] = None
    row["scoring_reference"] = {"expected_root_cause": expected_root_cause}
    row["environment_resolution_evaluated"] = False
    row["env_resolved"] = None
    row["resolved"] = None
    row["reward_contract"] = None
    row["safety_evaluated"] = False
    row["safety"] = None
    row["time_to_resolve_s"] = None

    if raw_row["inference_status"] != "response_received":
        row["status"] = raw_row["inference_status"]
        return row

    try:
        prediction = evaluator._parse_prediction(raw_row["raw_model_response"] or "")
        diagnostic = evaluator._compute_diagnostic_f1(
            str(prediction["root_cause"]),
            expected_root_cause,
        )
        row["prediction"] = prediction
        row["format_compliant"] = True
        row["diagnostic_precision"] = diagnostic["precision"]
        row["diagnostic_recall"] = diagnostic["recall"]
        row["diagnostic_f1"] = diagnostic["f1"]
        row["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        row["status"] = "invalid"
        row["error"] = {
            "error_type": type(exc).__name__,
            "error_message": str(exc).replace("\x00", "")[:2000],
        }
    return row


def _summary(
    *,
    run_id: str,
    episode_rows: list[dict[str, Any]],
    scheduled_count: int,
    lifecycle: str,
    fatal_error: dict[str, str] | None,
) -> dict[str, Any]:
    per_arm: dict[str, Any] = {}
    for arm in ARMS:
        rows = [row for row in episode_rows if row["arm"] == arm]
        scored = [row for row in rows if row["status"] == "ok"]
        f1_values = [row["diagnostic_f1"] for row in scored]
        per_arm[arm] = {
            "scheduled_count": scheduled_count // len(ARMS),
            "recorded_count": len(rows),
            "not_executed_count": (
                max(0, scheduled_count // len(ARMS) - len(rows))
                + sum(row["status"] == "not_executed" for row in rows)
            ),
            "response_received_count": sum(row["response_received"] for row in rows),
            "valid_prediction_count": len(scored),
            "invalid_prediction_count": sum(row["status"] == "invalid" for row in rows),
            "inference_error_count": sum(row["status"] in {"error", "interrupted"} for row in rows),
            "format_compliant_count": sum(row["format_compliant"] for row in rows),
            "format_compliance_denominator": "all scheduled arm rows, including failures",
            "format_compliance_rate": (
                round(
                    sum(row["format_compliant"] for row in rows) / (scheduled_count // 2),
                    6,
                )
                if scheduled_count
                else None
            ),
            "diagnostic_scored_count": len(f1_values),
            "avg_diagnostic_f1": (round(sum(f1_values) / len(f1_values), 6) if f1_values else None),
            "resolution_rate": None,
            "avg_reward": None,
            "safety_result": None,
            "avg_time_to_resolve_s": None,
        }
    return {
        "schema_version": "atlasops-base-sft-validation-summary-v1",
        "run_id": run_id,
        "lifecycle": lifecycle,
        "fatal_error": fatal_error,
        "scheduled_arm_row_count": scheduled_count,
        "recorded_arm_row_count": len(episode_rows),
        "not_executed_arm_row_count": (
            max(0, scheduled_count - len(episode_rows))
            + sum(row["status"] == "not_executed" for row in episode_rows)
        ),
        "format_compliance_denominator": "all scheduled arm rows, including failures",
        "per_arm": per_arm,
        "environment_resolution_evaluated": False,
        "resolution_rate": None,
        "avg_reward": None,
        "safety_result": None,
        "avg_time_to_resolve_s": None,
        "claims": {
            "validation_only": True,
            "matched_base_vs_sft_diagnostic_comparison": (
                lifecycle == "completed"
                and len(episode_rows) == scheduled_count
                and all(row["status"] == "ok" for row in episode_rows)
            ),
            "g6_or_g8_gate_closure": False,
        },
    }


def run_base_sft_validation(
    *,
    checkpoint: Path,
    checkpoint_manifest_sha256: str,
    base_snapshot: Path,
    output_dir: Path,
    run_id: str,
    split_name: str = "val",
) -> dict[str, Any]:
    """Run the ordered Base/SFT comparison; only the literal Validation split is accepted."""
    if split_name != "val":
        raise ValueError("Base-vs-SFT empirical evaluation is Validation-only")
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("run_id must be 1-128 safe ASCII letters, digits, '.', '_' or '-'")
    if (
        not isinstance(checkpoint_manifest_sha256, str)
        or SHA256_PATTERN.fullmatch(checkpoint_manifest_sha256) is None
    ):
        raise ValueError("Checkpoint manifest pin must be a lowercase SHA-256 digest")

    checkpoint = Path(checkpoint)
    base_snapshot = Path(base_snapshot)
    output = _validate_output_dir(output_dir, checkpoint, base_snapshot)
    evaluator, collector = _runtime_modules()
    source = evaluator._source_provenance()
    if source.get("git_dirty") is not False:
        raise ValueError("Empirical comparison requires a clean evaluator source tree")

    scenario_ids = list(evaluator.get_split("val"))
    if not scenario_ids:
        raise ValueError("Frozen Validation split is empty")
    seed = evaluator.SPLIT_SEEDS.get("val")
    if seed != EVALUATION_CONFIG["seed"]:
        raise ValueError("Frozen Validation seed no longer matches the pinned evaluation seed")

    output.mkdir(exist_ok=False)
    raw_path = output / "raw_episodes.jsonl"
    episodes_path = output / "episodes.jsonl"
    summary_path = output / "summary.json"
    run_manifest_path = output / "run_manifest.json"
    evaluation_config = dict(EVALUATION_CONFIG)
    evaluation_config["quantization"] = dict(EVALUATION_CONFIG["quantization"])
    evaluation_config_sha256 = _canonical_sha256(evaluation_config)
    scheduled_count = len(scenario_ids) * len(ARMS)
    run_manifest: dict[str, Any] = {
        "schema_version": RUN_SCHEMA,
        "run_id": run_id,
        "lifecycle": "planned",
        "status": "planned",
        "created_at_utc": _utc_now(),
        "source": source,
        "split": "val",
        "scenario_ids": scenario_ids,
        "split_sha256": _canonical_sha256(scenario_ids),
        "evaluation_split": {
            "name": "val",
            "seed": seed,
            "scenario_ids": scenario_ids,
            "ordered_ids_sha256": _canonical_sha256(scenario_ids),
            "ordered_scenario_ids_sha256": _canonical_sha256(scenario_ids),
            "arms_per_scenario_ordered": list(ARMS),
            "scheduled_arm_row_count": scheduled_count,
        },
        "evaluation_config": evaluation_config,
        "evaluation_config_sha256": evaluation_config_sha256,
        "input_identity": {
            "checkpoint_path": str(checkpoint.resolve(strict=False)),
            "checkpoint_manifest_sha256_pin": checkpoint_manifest_sha256,
            "base_snapshot_path": str(base_snapshot.resolve(strict=False)),
        },
        "base_snapshot_inventory_before": None,
        "base_snapshot_inventory_after": None,
        "checkpoint_identity": None,
        "checkpoint_data_provenance_as_recorded": None,
        "runtime": None,
        "artifacts": {
            "raw_episodes": raw_path.name,
            "episodes": episodes_path.name,
            "summary": summary_path.name,
        },
        "fatal_error": None,
        "completed_at_utc": None,
    }
    _write_json_atomic(run_manifest_path, run_manifest)

    raw_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    fatal_error: dict[str, str] | None = None
    interrupted: BaseException | None = None
    checkpoint_manifest: dict[str, Any] | None = None
    checkpoint_hash_before: str | None = None
    inventory_before: dict[str, Any] | None = None
    runner = LocalPairedInference(base_snapshot, checkpoint)
    generation_config = {
        key: EVALUATION_CONFIG[key]
        for key in ("seed", "temperature", "top_p", "max_new_tokens", "do_sample")
    }

    with raw_path.open("x", encoding="utf-8", newline="\n") as raw_stream:
        run_manifest["lifecycle"] = "running"
        run_manifest["status"] = "running"
        run_manifest["started_at_utc"] = _utc_now()
        _write_json_atomic(run_manifest_path, run_manifest)

        def persist_raw(
            pair_index: int,
            scenario_id: str,
            arm: str,
            messages: list[dict[str, str]],
            token_ids: list[int] | None,
            inference_status: str,
            inference_seconds: float | None,
            error: dict[str, str] | None,
            *,
            raw_text: str | None = None,
            output_token_ids: list[int] | None = None,
            prompt_encoding_seconds: float | None = None,
        ) -> dict[str, Any]:
            row = _new_episode(
                run_id=run_id,
                pair_index=pair_index,
                scenario_id=scenario_id,
                arm=arm,
                messages=messages,
                token_ids=token_ids,
                evaluation_config_sha256=evaluation_config_sha256,
                checkpoint_identity=run_manifest.get("checkpoint_identity"),
                model_identity=run_manifest.get("model_identity"),
                inference_status=inference_status,
                inference_seconds=inference_seconds,
                evaluation_generation_config=dict(generation_config),
                raw_text=raw_text,
                output_token_ids=output_token_ids,
                error=error,
            )
            row["prompt_encoding_seconds"] = prompt_encoding_seconds
            _write_jsonl_row(raw_stream, row)
            raw_rows.append(row)
            return row

        try:
            checkpoint_manifest, checkpoint_hash_before = evaluator._load_checkpoint_manifest(
                checkpoint
            )
            if checkpoint_hash_before != checkpoint_manifest_sha256:
                raise ValueError("Checkpoint manifest does not match the explicit SHA-256 pin")
            run_manifest["checkpoint_data_provenance_as_recorded"] = (
                _recorded_training_data_provenance(checkpoint, checkpoint_hash_before)
            )
            inventory_before = collector.collect_model_inventory(
                snapshot_dir=base_snapshot,
            )
            _base_and_tokenizer_match(
                checkpoint_manifest,
                collector,
                base_snapshot,
                inventory_before,
            )
            run_manifest["model_identity"] = {
                "id": collector.MODEL_REPOSITORY,
                "revision": collector.MODEL_REVISION,
                "tokenizer_id": collector.MODEL_REPOSITORY,
                "tokenizer_revision": collector.MODEL_REVISION,
            }
            checkpoint_record = checkpoint_manifest["checkpoint"]
            run_manifest["checkpoint_identity"] = {
                "manifest_sha256": checkpoint_hash_before,
                "tree_sha256": checkpoint_record["tree_sha256"],
                "files": checkpoint_record["files"],
            }
            run_manifest["base_snapshot_inventory_before"] = inventory_before
            runner.load()
        except (KeyboardInterrupt, SystemExit) as exc:
            fatal_error = _exception_record(exc)
            interrupted = exc
        except Exception as exc:  # noqa: BLE001
            fatal_error = _exception_record(exc)
        run_manifest["runtime"] = runner.runtime or None
        _write_json_atomic(run_manifest_path, run_manifest)

        if fatal_error is None:
            for pair_index, scenario_id in enumerate(scenario_ids):
                meta = evaluator.SCENARIO_CATALOG[scenario_id]
                messages: list[dict[str, str]] = []
                encode_seconds: float | None = None
                try:
                    messages = evaluator._messages(evaluator._public_input(meta))
                    encode_started = time.perf_counter()
                    inputs, prompt_ids = runner.encode(messages)
                    encode_seconds = round(time.perf_counter() - encode_started, 6)
                except (KeyboardInterrupt, SystemExit) as exc:
                    encode_error = _exception_record(exc)
                    persist_raw(
                        pair_index,
                        scenario_id,
                        "base",
                        messages,
                        None,
                        "interrupted",
                        None,
                        encode_error,
                    )
                    fatal_error = encode_error
                    interrupted = exc
                    break
                except Exception as exc:  # noqa: BLE001
                    encode_error = _exception_record(exc)
                    for arm in ARMS:
                        persist_raw(
                            pair_index,
                            scenario_id,
                            arm,
                            messages,
                            None,
                            "not_executed",
                            None,
                            encode_error,
                        )
                    continue

                for arm in ARMS:
                    raw_text: str | None = None
                    output_token_ids: list[int] | None = None
                    inference_status = "response_received"
                    inference_error = None
                    started = time.perf_counter()
                    try:
                        output_token_ids, raw_text = runner.generate(arm, inputs)
                    except RawDecodeError as exc:
                        output_token_ids = exc.token_ids
                        inference_status = "error"
                        inference_error = _exception_record(exc.cause)
                    except Exception as exc:  # noqa: BLE001
                        inference_status = "error"
                        inference_error = _exception_record(exc)
                    except (KeyboardInterrupt, SystemExit) as exc:
                        inference_status = "interrupted"
                        inference_error = _exception_record(exc)
                        fatal_error = inference_error
                        interrupted = exc
                    elapsed = round(time.perf_counter() - started, 6)
                    persist_raw(
                        pair_index,
                        scenario_id,
                        arm,
                        messages,
                        prompt_ids,
                        inference_status,
                        elapsed,
                        inference_error,
                        raw_text=raw_text,
                        output_token_ids=output_token_ids,
                        prompt_encoding_seconds=encode_seconds,
                    )
                    if interrupted is not None:
                        break
                if interrupted is not None:
                    break

    if checkpoint_hash_before is not None and inventory_before is not None:
        try:
            after_manifest, checkpoint_hash_after = evaluator._load_checkpoint_manifest(checkpoint)
            inventory_after = collector.collect_model_inventory(
                snapshot_dir=base_snapshot,
            )
            run_manifest["base_snapshot_inventory_after"] = inventory_after
            run_manifest["checkpoint_identity"]["manifest_sha256_after"] = checkpoint_hash_after
            run_manifest["checkpoint_identity"]["tree_sha256_after"] = after_manifest["checkpoint"][
                "tree_sha256"
            ]
            if (
                checkpoint_hash_after != checkpoint_hash_before
                or after_manifest["checkpoint"]["tree_sha256"]
                != checkpoint_manifest["checkpoint"]["tree_sha256"]
                or inventory_after.get("files") != inventory_before.get("files")
            ):
                raise RuntimeError("Pinned model or checkpoint changed during evaluation")
        except (KeyboardInterrupt, SystemExit) as exc:
            fatal_error = fatal_error or _exception_record(exc)
            interrupted = interrupted or exc
        except Exception as exc:  # noqa: BLE001
            fatal_error = fatal_error or _exception_record(exc)

    with episodes_path.open("x", encoding="utf-8", newline="\n") as scored_stream:
        try:
            for raw_row in raw_rows:
                meta = evaluator.SCENARIO_CATALOG[raw_row["scenario_id"]]
                scored_row = _score_episode(
                    raw_row,
                    expected_root_cause=meta.expected_root_cause,
                    evaluator=evaluator,
                )
                _write_jsonl_row(scored_stream, scored_row)
                episode_rows.append(scored_row)
        except (KeyboardInterrupt, SystemExit) as exc:
            fatal_error = fatal_error or _exception_record(exc)
            interrupted = interrupted or exc
        except Exception as exc:  # noqa: BLE001
            fatal_error = fatal_error or _exception_record(exc)

    if len(raw_rows) != scheduled_count and fatal_error is None:
        fatal_error = {
            "error_type": "IncompleteSchedule",
            "error_message": (f"Recorded {len(raw_rows)} of {scheduled_count} scheduled arm rows"),
        }

    lifecycle = "completed" if fatal_error is None else "failed"
    summary = _summary(
        run_id=run_id,
        episode_rows=episode_rows,
        scheduled_count=scheduled_count,
        lifecycle=lifecycle,
        fatal_error=fatal_error,
    )
    _write_json_atomic(summary_path, summary)
    run_manifest["lifecycle"] = lifecycle
    run_manifest["status"] = lifecycle
    run_manifest["fatal_error"] = fatal_error
    run_manifest["completed_at_utc"] = _utc_now()
    run_manifest["recorded_arm_row_count"] = len(episode_rows)
    run_manifest["not_executed_arm_row_count"] = summary["not_executed_arm_row_count"]
    run_manifest["artifacts"]["raw_episodes_sha256"] = _file_sha256(raw_path)
    run_manifest["artifacts"]["episodes_sha256"] = _file_sha256(episodes_path)
    run_manifest["artifacts"]["summary_sha256"] = _file_sha256(summary_path)
    run_manifest["raw_predictions_sha256"] = run_manifest["artifacts"]["episodes_sha256"]
    _write_json_atomic(run_manifest_path, run_manifest)
    if interrupted is not None:
        raise interrupted
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--checkpoint-manifest-sha256", required=True)
    parser.add_argument("--base-snapshot", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run_base_sft_validation(
            checkpoint=args.checkpoint,
            checkpoint_manifest_sha256=args.checkpoint_manifest_sha256,
            base_snapshot=args.base_snapshot,
            output_dir=args.output_dir,
            run_id=args.run_id,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["lifecycle"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
