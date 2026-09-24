"""Empirical G9 evaluation for a checkpointed GRPO remediation policy.

The empirical path accepts only a provenance-checked local GRPO checkpoint and
passes each raw policy completion directly to the one-action environment adapter.
The deterministic compatibility path is explicit and always NON_EMPIRICAL.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.coordinator import _check_tool_policy
from agents.tools import TOOL_REGISTRY
from agents.verifier import verify_environment
from bench.runner import compute_summary
from bench.zero_shot_baseline import compute_diagnostic_f1
from config.scenario_catalog import SCENARIO_CATALOG
from config.splits import get_split
from training.grpo_environment import (
    DirectPolicyEnvironment,
    parse_policy_action,
)
from training.grpo_provenance import (
    ADAPTER_WEIGHT_NAMES,
    source_identity,
    validate_sft_parent,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("grpo_eval")

RESULTS_DIR = Path("bench/results")
CHECKPOINT_MANIFEST = "grpo_run_manifest.json"
ACTION_INSTRUCTION = (
    "Return exactly one JSON object with keys tool, arguments, and "
    "agent_claimed_resolved. The tool and arguments are the exact action that "
    "will be safety-checked and executed. Do not include an actions list."
)
FORBIDDEN_TRUTH_KEYS = frozenset(
    {
        "benchmark_truth",
        "expected_action",
        "expected_arguments",
        "expected_resolution",
        "expected_root_cause",
        "expected_tool",
        "gold_action",
        "ground_truth",
        "known_good_action",
        "reference_action",
        "root_cause_truth",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_GIT_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$", re.IGNORECASE)


@dataclass(frozen=True)
class CheckpointProvenance:
    """Validated identity for a completed, locally available GRPO checkpoint."""

    checkpoint_path: Path
    checkpoint_sha256: str
    manifest_sha256: str
    base_model: Mapping[str, str]
    tokenizer: Mapping[str, str]
    code_sha: str
    source_state: str
    training_mode: str
    train_split_sha256: str
    training_seed: int
    training_generation_config: Mapping[str, Any]
    sft_parent: Mapping[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {
            "checkpoint_path": str(self.checkpoint_path),
            "checkpoint_sha256": self.checkpoint_sha256,
            "manifest_sha256": self.manifest_sha256,
            "base_model": dict(self.base_model),
            "tokenizer": dict(self.tokenizer),
            "code_sha": self.code_sha,
            "source_state": self.source_state,
            "training_mode": self.training_mode,
            "train_split_sha256": self.train_split_sha256,
            "training_seed": self.training_seed,
            "training_generation_config": dict(self.training_generation_config),
            "sft_parent": dict(self.sft_parent),
        }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _required_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"GRPO checkpoint provenance requires {label} to be an object")
    return value


def validate_grpo_checkpoint(checkpoint: str | Path) -> CheckpointProvenance:
    """Validate required provenance and every checkpoint byte before loading it.

    Required manifest shape is documented by the fields read below. The manifest
    itself is excluded from the checkpoint file inventory to avoid a recursive hash.
    """
    checkpoint_path = Path(checkpoint).expanduser().resolve()
    if not checkpoint_path.is_dir():
        raise FileNotFoundError(f"GRPO checkpoint directory missing: {checkpoint_path}")
    manifest_path = checkpoint_path / CHECKPOINT_MANIFEST
    if manifest_path.is_symlink():
        raise ValueError("GRPO checkpoint provenance manifest must not be a symlink")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"GRPO checkpoint provenance manifest missing: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("GRPO checkpoint provenance schema_version must be 1")
    if manifest.get("status") != "completed":
        raise ValueError("GRPO checkpoint provenance status must be completed")

    training = _required_mapping(manifest.get("training"), "training")
    if str(training.get("algorithm", "")).upper() != "GRPO":
        raise ValueError("Checkpoint provenance must identify GRPO training")
    training_mode = training.get("mode")
    if training_mode not in {"online_rl_real_environment", "online_rl_real_kind"}:
        raise ValueError("Empirical G9 requires a completed real-environment GRPO checkpoint")
    seed = training.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("GRPO training provenance requires a non-negative integer seed")
    training_generation_config = _required_mapping(
        training.get("generation_config"), "training.generation_config"
    )
    if not training_generation_config:
        raise ValueError("GRPO training provenance requires generation configuration")

    identities: dict[str, Mapping[str, str]] = {}
    for identity in ("base_model", "tokenizer"):
        record = _required_mapping(manifest.get(identity), identity)
        model_id = record.get("id")
        revision = record.get("resolved_revision")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError(f"Checkpoint provenance lacks {identity}.id")
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError(f"Checkpoint provenance lacks {identity}.resolved_revision")
        identities[identity] = {"id": model_id, "resolved_revision": revision}

    source = _required_mapping(manifest.get("source"), "source")
    code_sha = source.get("code_sha")
    source_state = source.get("source_state")
    if not isinstance(code_sha, str) or not _GIT_SHA_RE.fullmatch(code_sha):
        raise ValueError("Checkpoint provenance requires a full source code SHA")
    if source_state not in {"clean", "dirty"}:
        raise ValueError("Checkpoint provenance source_state must be clean or dirty")
    if source_state != "clean":
        raise ValueError("Empirical G9 requires a clean immutable training source")
    if source_state == "dirty" and not _SHA256_RE.fullmatch(
        str(source.get("dirty_diff_sha256", ""))
    ):
        raise ValueError("Dirty training source provenance requires dirty_diff_sha256")

    splits = _required_mapping(manifest.get("splits"), "splits")
    train_split_sha256 = splits.get("train_sha256")
    if not isinstance(train_split_sha256, str) or not _SHA256_RE.fullmatch(train_split_sha256):
        raise ValueError("Checkpoint provenance requires a SHA-256 hash for the frozen Train split")
    if train_split_sha256.lower() != _canonical_sha256(list(get_split("train"))):
        raise ValueError("Checkpoint provenance Train split differs from the frozen split")
    hyperparameters = _required_mapping(training.get("hyperparameters"), "training.hyperparameters")
    tiers = hyperparameters.get("tiers")
    if not isinstance(tiers, list) or not tiers or any(not isinstance(tier, str) for tier in tiers):
        raise ValueError("GRPO checkpoint requires the selected training tiers")
    from training.grpo import build_direct_action_prompts

    expected_prompts = build_direct_action_prompts(tiers)
    selected_ids = [row["scenario_id"] for row in expected_prompts]
    if (
        splits.get("selected_scenario_ids") != selected_ids
        or splits.get("selected_scenario_ids_sha256") != _canonical_sha256(selected_ids)
        or splits.get("selected_prompt_rows_sha256") != _canonical_sha256(expected_prompts)
    ):
        raise ValueError("GRPO checkpoint selected prompts differ from frozen Train tiers")

    parent = _required_mapping(manifest.get("sft_parent"), "sft_parent")
    parent_path = parent.get("checkpoint_path")
    if not isinstance(parent_path, str) or not parent_path:
        raise ValueError("GRPO checkpoint lacks its SFT parent path")
    validated_parent = validate_sft_parent(
        Path(parent_path),
        model_id=identities["base_model"]["id"],
        model_revision=identities["base_model"]["resolved_revision"],
        tokenizer_id=identities["tokenizer"]["id"],
        tokenizer_revision=identities["tokenizer"]["resolved_revision"],
    )
    if dict(parent) != validated_parent:
        raise ValueError("GRPO checkpoint SFT parent provenance changed")

    checkpoint_record = _required_mapping(manifest.get("checkpoint"), "checkpoint")
    declared_files = checkpoint_record.get("files")
    if not isinstance(declared_files, list) or not declared_files:
        raise ValueError("Checkpoint provenance lacks a non-empty file inventory")
    expected_by_path: dict[str, Mapping[str, Any]] = {}
    for record in declared_files:
        if not isinstance(record, Mapping):
            raise TypeError("Checkpoint file inventory entries must be objects")
        relative = record.get("path")
        expected_hash = record.get("sha256")
        if not isinstance(relative, str) or not relative or not isinstance(expected_hash, str):
            raise TypeError("Checkpoint file inventory entry requires path and sha256")
        rel_path = Path(relative)
        if rel_path.is_absolute() or ".." in rel_path.parts or relative == CHECKPOINT_MANIFEST:
            raise ValueError(f"Unsafe or recursive checkpoint inventory path: {relative}")
        normalized = rel_path.as_posix()
        if normalized in expected_by_path:
            raise ValueError(f"Duplicate checkpoint inventory path: {relative}")
        if not _SHA256_RE.fullmatch(expected_hash):
            raise ValueError(f"Invalid checkpoint SHA-256 for {relative}")
        expected_by_path[normalized] = record

    actual_files: list[dict[str, Any]] = []
    actual_paths: set[str] = set()
    for path in checkpoint_path.rglob("*"):
        if path == manifest_path:
            continue
        if path.is_symlink():
            raise ValueError(f"Checkpoint contains a symlink and cannot be hashed safely: {path}")
        if path.is_dir():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(checkpoint_path):
            raise ValueError(f"Checkpoint file escapes its directory: {path}")
        relative = path.relative_to(checkpoint_path).as_posix()
        actual_hash = _sha256_file(path)
        size_bytes = path.stat().st_size
        declared = expected_by_path.get(relative)
        if declared is None:
            raise ValueError(f"Checkpoint file missing from provenance inventory: {relative}")
        if actual_hash.lower() != str(declared["sha256"]).lower():
            raise ValueError(f"Checkpoint hash mismatch: {relative}")
        declared_size = declared.get("size_bytes")
        if declared_size is not None and declared_size != size_bytes:
            raise ValueError(f"Checkpoint size mismatch: {relative}")
        actual_paths.add(relative)
        actual_files.append({"path": relative, "size_bytes": size_bytes, "sha256": actual_hash})

    if actual_paths != set(expected_by_path):
        missing = sorted(set(expected_by_path) - actual_paths)
        raise FileNotFoundError(f"Checkpoint files missing from disk: {missing}")
    actual_files.sort(key=lambda item: item["path"])
    tree_sha256 = _canonical_sha256(actual_files)
    if tree_sha256 != checkpoint_record.get("tree_sha256"):
        raise ValueError("Checkpoint tree hash does not match provenance")
    if not (checkpoint_path / "adapter_config.json").is_file():
        raise ValueError("GRPO checkpoint is missing adapter_config.json")
    if not any((checkpoint_path / name).is_file() for name in ADAPTER_WEIGHT_NAMES):
        raise ValueError("GRPO checkpoint is missing adapter model weights")

    return CheckpointProvenance(
        checkpoint_path=checkpoint_path,
        checkpoint_sha256=tree_sha256,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        base_model=identities["base_model"],
        tokenizer=identities["tokenizer"],
        code_sha=code_sha,
        source_state=str(source_state),
        training_mode=str(training_mode),
        train_split_sha256=train_split_sha256,
        training_seed=seed,
        training_generation_config=dict(training_generation_config),
        sft_parent=dict(parent),
    )


def _normalize_generation_config(
    generation_config: Mapping[str, Any] | None,
) -> dict[str, int | float]:
    config = dict(generation_config or {})
    allowed = {"max_new_tokens", "temperature", "top_p"}
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f"Unsupported policy generation configuration keys: {sorted(unknown)}")
    normalized: dict[str, int | float] = {
        "max_new_tokens": config.get("max_new_tokens", 256),
        "temperature": config.get("temperature", 0.0),
        "top_p": config.get("top_p", 1.0),
    }
    max_new_tokens = normalized["max_new_tokens"]
    temperature = normalized["temperature"]
    top_p = normalized["top_p"]
    if not isinstance(max_new_tokens, int) or isinstance(max_new_tokens, bool) or max_new_tokens <= 0:
        raise ValueError("max_new_tokens must be a positive integer")
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
        raise TypeError("temperature must be numeric")
    if not math.isfinite(float(temperature)) or float(temperature) < 0:
        raise ValueError("temperature must be finite and non-negative")
    if not isinstance(top_p, (int, float)) or isinstance(top_p, bool):
        raise TypeError("top_p must be numeric")
    if not math.isfinite(float(top_p)) or not 0 < float(top_p) <= 1:
        raise ValueError("top_p must be finite and in (0, 1]")
    normalized["temperature"] = float(temperature)
    normalized["top_p"] = float(top_p)
    return normalized


def _reject_benchmark_truth(value: Any, path: str = "state") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
            if normalized_key in FORBIDDEN_TRUTH_KEYS:
                raise ValueError(f"Benchmark truth field is forbidden in policy input: {path}.{key}")
            _reject_benchmark_truth(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_benchmark_truth(child, f"{path}[{index}]")


def _public_policy_state(state: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(state, Mapping):
        raise TypeError("Initial policy state must be an object")
    state_copy = json.loads(json.dumps(dict(state), ensure_ascii=False, allow_nan=False))
    _reject_benchmark_truth(state_copy)
    state_copy["instruction"] = ACTION_INSTRUCTION
    return state_copy


def _authoritative_environment(environment: Any) -> bool:
    return (
        type(environment) is DirectPolicyEnvironment
        and environment.tool_registry is TOOL_REGISTRY
        and environment.policy_check is _check_tool_policy
        and environment.verifier is verify_environment
    )


class LocalGRPOPolicy:
    """Lazy Hugging Face base model plus the provenance-checked GRPO adapter."""

    def __init__(self, checkpoint: str | Path, *, device: str | None = None):
        self.provenance = validate_grpo_checkpoint(checkpoint)
        self.device = device
        self._model: Any = None
        self._tokenizer: Any = None

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: str | Path,
        *,
        device: str | None = None,
    ) -> LocalGRPOPolicy:
        return cls(checkpoint, device=device)

    def _load(self) -> None:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        current_provenance = validate_grpo_checkpoint(self.provenance.checkpoint_path)
        if current_provenance != self.provenance:
            raise ValueError("GRPO checkpoint changed after provenance validation")
        base = self.provenance.base_model
        tokenizer_record = self.provenance.tokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_record["id"],
            revision=tokenizer_record["resolved_revision"],
            local_files_only=True,
        )
        if self._tokenizer.pad_token_id is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token
        base_model = AutoModelForCausalLM.from_pretrained(
            base["id"],
            revision=base["resolved_revision"],
            torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            device_map=self.device or "auto",
            local_files_only=True,
        )
        self._model = PeftModel.from_pretrained(
            base_model,
            str(self.provenance.checkpoint_path),
            local_files_only=True,
        )
        self._model.eval()

    async def generate(
        self,
        state: Mapping[str, Any],
        *,
        seed: int,
        generation_config: Mapping[str, Any],
    ) -> str:
        return await asyncio.to_thread(
            self._generate,
            dict(state),
            seed,
            dict(generation_config),
        )

    def _generate(
        self,
        state: dict[str, Any],
        seed: int,
        generation_config: dict[str, Any],
    ) -> str:
        import torch

        if self._model is None or self._tokenizer is None:
            self._load()
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        prompt = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        encoded = self._tokenizer(prompt, return_tensors="pt")
        device = self._model.device
        encoded = {key: value.to(device) for key, value in encoded.items()}
        temperature = float(generation_config["temperature"])
        do_sample = temperature > 0
        options: dict[str, Any] = {
            "max_new_tokens": int(generation_config["max_new_tokens"]),
            "do_sample": do_sample,
            "pad_token_id": self._tokenizer.pad_token_id,
        }
        if do_sample:
            options["temperature"] = temperature
            options["top_p"] = float(generation_config["top_p"])
        with torch.inference_mode():
            output = self._model.generate(**encoded, **options)
        generated = output[0][encoded["input_ids"].shape[1] :]
        return self._tokenizer.decode(generated, skip_special_tokens=True)


async def _maybe_await(value: Any) -> Any:
    return await value if hasattr(value, "__await__") else value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _step_reward(
    verification: Mapping[str, Any] | None,
    *,
    agent_claimed_resolved: bool,
) -> dict[str, Any]:
    verified = isinstance(verification, Mapping) and verification.get("env_resolved") is True
    checks = verification.get("checks", []) if isinstance(verification, Mapping) else []
    required_checks = [
        check
        for check in checks
        if isinstance(check, Mapping) and check.get("required", True) is True
    ]
    passed = sum(1 for check in required_checks if check.get("passed") is True)
    coverage = passed / len(required_checks) if required_checks else 0.0
    resolution_reward = 0.75 if verified else 0.0
    check_reward = 0.25 * coverage
    false_resolution_penalty = -0.25 if agent_claimed_resolved and not verified else 0.0
    return {
        "r_verified_resolution": round(resolution_reward, 6),
        "r_required_check_coverage": round(check_reward, 6),
        "penalty_false_resolution": false_resolution_penalty,
        "required_checks": len(required_checks),
        "passed_required_checks": passed,
        "required_check_coverage": round(coverage, 6),
        "total": round(resolution_reward + check_reward + false_resolution_penalty, 6),
    }


def _validate_environment_result(
    completion_text: str,
    parsed_action: Mapping[str, Any] | None,
    result: Mapping[str, Any],
) -> str | None:
    if result.get("policy_completion") != completion_text:
        return "environment did not preserve the raw policy output"
    reported_action = result.get("policy_action")
    if parsed_action is None:
        if reported_action is not None:
            return "environment parsed an action differently from the shared action parser"
        expected_status = "blocked"
    else:
        if reported_action != dict(parsed_action):
            return "environment policy action differs from the parsed policy output"
        expected_status = str(result.get("status", ""))

    executed = result.get("executed_actions")
    if not isinstance(executed, list):
        return "environment result lacks an executed_actions list"
    if result.get("status") == "ok":
        if parsed_action is None or len(executed) != 1:
            return "an accepted policy step must execute exactly one parsed action"
        action = executed[0]
        if not isinstance(action, Mapping):
            return "executed action record is malformed"
        if action.get("tool") != parsed_action.get("tool"):
            return "executed tool differs from the policy action"
        if action.get("arguments") != parsed_action.get("arguments"):
            return "executed arguments differ from the policy action"
        verification = result.get("verification")
        if not isinstance(verification, Mapping):
            return "accepted policy step lacks an authoritative verifier result"
        if result.get("env_resolved") is not (verification.get("env_resolved") is True):
            return "environment resolution flag differs from the authoritative verifier"
    elif result.get("status") == "blocked":
        if executed:
            return "blocked policy step must not report an executed mutation"
        if result.get("env_resolved") is True:
            return "blocked action cannot claim verified resolution"
    else:
        return f"unsupported environment status: {result.get('status')!r}"
    if expected_status == "blocked" and result.get("status") != "blocked":
        return "invalid policy output was not blocked by the environment"
    return None


async def evaluate_grpo_episode(
    scenario_id: str,
    initial_state: Mapping[str, Any],
    policy: Any,
    environment: Any,
    *,
    seed: int,
    generation_config: Mapping[str, Any] | None = None,
    max_steps: int = 5,
    evaluation_mode: str = "EMPIRICAL",
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run one trajectory, submitting each raw completion as one environment step.

    Empirical calls require `LocalGRPOPolicy`, whose checkpoint provenance has
    already been validated. Tests and synthetic runs must pass the explicit
    `NON_EMPIRICAL` mode and are never labelled empirical.
    """
    mode = evaluation_mode.upper()
    if mode not in {"EMPIRICAL", "NON_EMPIRICAL"}:
        raise ValueError("evaluation_mode must be EMPIRICAL or NON_EMPIRICAL")
    if mode == "EMPIRICAL" and not isinstance(policy, LocalGRPOPolicy):
        raise ValueError("Empirical G9 evaluation requires a provenance-checked LocalGRPOPolicy checkpoint")
    if environment is None or not callable(getattr(environment, "step", None)):
        raise TypeError("G9 evaluation requires an explicit one-step environment interface")
    if mode == "EMPIRICAL" and not _authoritative_environment(environment):
        raise ValueError("Empirical G9 evaluation requires the built-in tool and environment verifier")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("Evaluation seed must be a non-negative integer")
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
        raise ValueError("max_steps must be a positive integer")
    config = _normalize_generation_config(generation_config)
    state = _public_policy_state(initial_state)
    episode_started = time.monotonic()
    started_at = _utc_now()
    trajectory: list[dict[str, Any]] = []
    failure: str | None = None

    def emit(event: dict[str, Any]) -> None:
        if event_sink is not None:
            event_sink({"recorded_at": _utc_now(), **event})

    emit(
        {
            "event": "episode_started",
            "evaluation_mode": mode,
            "scenario_id": scenario_id,
            "seed": seed,
            "generation_config": config,
            "state": state,
            "started_at": started_at,
        }
    )

    if mode == "EMPIRICAL":
        preflight = await _maybe_await(
            environment.verifier(
                scenario_id=scenario_id,
                agent_claimed_resolved=False,
                alert=state.get("alert"),
                incident_context=state,
            )
        )
        preflight_record = preflight.to_dict()
        emit(
            {
                "event": "pre_action_verification",
                "scenario_id": scenario_id,
                "verification": preflight_record,
            }
        )
        if (
            preflight_record.get("verification_status") != "failed"
            or preflight_record.get("env_resolved") is not False
        ):
            raise ValueError("Empirical G9 requires a reachable, unresolved fault before policy action")

    for step_index in range(max_steps):
        step_started_at = _utc_now()
        elapsed_before_generation = time.monotonic() - episode_started
        try:
            completion_text = await _maybe_await(
                policy.generate(
                    state,
                    seed=seed + step_index,
                    generation_config=config,
                )
            )
        except asyncio.CancelledError:
            emit({"event": "episode_interrupted", "scenario_id": scenario_id, "step": step_index})
            raise
        except Exception as exc:  # noqa: BLE001 - preserve model/runtime failures in the trajectory
            failure = f"policy_generation_error: {type(exc).__name__}: {exc}"
            emit(
                {
                    "event": "step_failure",
                    "scenario_id": scenario_id,
                    "step": step_index,
                    "failure": failure,
                    "state": state,
                    "step_started_at": step_started_at,
                }
            )
            break
        if not isinstance(completion_text, str):
            failure = "policy_generation_error: policy output was not text"
            emit({"event": "step_failure", "scenario_id": scenario_id, "step": step_index, "failure": failure})
            break

        try:
            parsed_action: dict[str, Any] | None = parse_policy_action(completion_text)
            parse_error = None
        except (TypeError, ValueError) as exc:
            parsed_action = None
            parse_error = f"{type(exc).__name__}: {exc}"

        proposal = {
            "event": "policy_output",
            "evaluation_mode": mode,
            "scenario_id": scenario_id,
            "step": step_index,
            "state": state,
            "raw_policy_output": completion_text,
            "parsed_action": parsed_action,
            "parse_error": parse_error,
            "generated_at": _utc_now(),
            "elapsed_before_generation_s": round(elapsed_before_generation, 6),
        }
        # Persist the exact proposal before submitting it to an environment that may mutate state.
        emit(proposal)

        try:
            environment_result = await _maybe_await(
                environment.step(
                    completion_text,
                    scenario_id=scenario_id,
                    state=state,
                )
            )
        except asyncio.CancelledError:
            emit({"event": "episode_interrupted", "scenario_id": scenario_id, "step": step_index})
            raise
        except Exception as exc:  # noqa: BLE001 - preserve environment failures in the trajectory
            failure = f"environment_step_error: {type(exc).__name__}: {exc}"
            failed_step = {
                "step": step_index,
                "state": state,
                "raw_policy_output": completion_text,
                "parsed_action": parsed_action,
                "parse_error": parse_error,
                "submitted_at": _utc_now(),
                "environment_result": None,
                "verification": None,
                "reward_decomposition": _step_reward(None, agent_claimed_resolved=bool(parsed_action and parsed_action.get("agent_claimed_resolved"))),
                "failure": failure,
            }
            trajectory.append(failed_step)
            emit({"event": "step_result", "scenario_id": scenario_id, "record": failed_step})
            break

        if not isinstance(environment_result, Mapping):
            failure = "environment_contract_error: result was not an object"
            result_mapping: Mapping[str, Any] = {}
        else:
            result_mapping = environment_result
            failure = _validate_environment_result(completion_text, parsed_action, result_mapping)
            if failure:
                failure = f"environment_contract_error: {failure}"

        verification = result_mapping.get("verification") if isinstance(result_mapping.get("verification"), Mapping) else None
        claimed = bool(parsed_action and parsed_action.get("agent_claimed_resolved") is True)
        verified_resolved = bool(
            failure is None
            and result_mapping.get("status") == "ok"
            and isinstance(verification, Mapping)
            and verification.get("env_resolved") is True
            and result_mapping.get("env_resolved") is True
        )
        reward = _step_reward(
            verification if failure is None else None,
            agent_claimed_resolved=claimed,
        )
        previous_history = state.get("history", [])
        history = list(previous_history) if isinstance(previous_history, list) else []
        observed_step = {
            "step": step_index,
            "policy_action": parsed_action,
            "executed_actions": result_mapping.get("executed_actions", []),
            "verification": verification,
            "terminal_block": result_mapping.get("terminal_block"),
            "env_resolved": verified_resolved,
        }
        next_state = {
            **state,
            "history": [*history, observed_step],
            "last_step": observed_step,
            "env_resolved": verified_resolved,
        }
        step_record = {
            "step": step_index,
            "state": state,
            "raw_policy_output": completion_text,
            "parsed_action": parsed_action,
            "parse_error": parse_error,
            "executed_action": (
                result_mapping.get("executed_actions", [None])[0]
                if isinstance(result_mapping.get("executed_actions"), list)
                and len(result_mapping.get("executed_actions", [])) == 1
                else None
            ),
            "environment_result": dict(result_mapping),
            "tool_result": (
                result_mapping.get("executed_actions", [{}])[0].get("result")
                if isinstance(result_mapping.get("executed_actions"), list)
                and len(result_mapping.get("executed_actions", [])) == 1
                and isinstance(result_mapping.get("executed_actions", [{}])[0], Mapping)
                else None
            ),
            "verification": verification,
            "env_resolved": verified_resolved,
            "agent_claimed_resolved": claimed,
            "reward_decomposition": reward,
            "next_state": next_state,
            "step_started_at": step_started_at,
            "step_finished_at": _utc_now(),
            "elapsed_episode_s": round(time.monotonic() - episode_started, 6),
            "failure": failure,
        }
        trajectory.append(step_record)
        emit({"event": "step_result", "scenario_id": scenario_id, "record": step_record})
        state = next_state
        if failure or verified_resolved or result_mapping.get("status") == "blocked":
            break

    final_verification = next(
        (step.get("verification") for step in reversed(trajectory) if isinstance(step.get("verification"), Mapping)),
        None,
    )
    resolved = bool(trajectory and trajectory[-1].get("env_resolved") is True)
    claimed_resolved = any(step.get("agent_claimed_resolved") is True for step in trajectory)
    elapsed = time.monotonic() - episode_started
    termination_reason = (
        "max_steps_without_verified_resolution"
        if not resolved and failure is None and len(trajectory) >= max_steps
        else None
    )
    episode_reward_decomposition = _step_reward(
        final_verification if failure is None else None,
        agent_claimed_resolved=claimed_resolved,
    )
    episode_reward = episode_reward_decomposition["total"]
    episode = {
        "schema_version": 1,
        "evaluation_mode": mode,
        "empirical": mode == "EMPIRICAL",
        "scenario_id": scenario_id,
        "status": "ok" if failure is None else "failed",
        "resolved": resolved,
        "env_resolved": resolved,
        "agent_claimed_resolved": claimed_resolved,
        "verification": final_verification,
        "seed": seed,
        "generation_config": config,
        "time_to_resolve_s": round(elapsed, 6) if resolved else None,
        "elapsed_s": round(elapsed, 6),
        "turns": len(trajectory),
        "reward": episode_reward,
        "reward_decomposition": episode_reward_decomposition,
        "failure": failure,
        "termination_reason": termination_reason,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "trajectory": trajectory,
        "provenance": policy.provenance.to_record() if mode == "EMPIRICAL" else None,
    }
    emit(
        {
            "event": "episode_completed" if failure is None else "episode_failed",
            "scenario_id": scenario_id,
            "result": {key: value for key, value in episode.items() if key != "trajectory"},
        }
    )
    return episode


async def _evaluate_mock_split(
    split_name: str,
    *,
    model_name: str,
    output_dir: Path | None,
) -> dict[str, Any]:
    scenario_ids = get_split(split_name)
    results = [evaluate_grpo_mock_episode(sid, model_name) for sid in scenario_ids]
    tag = f"grpo-{split_name}-{model_name.replace(':', '-').replace('/', '-')}"
    summary = compute_summary(results, tag=tag, model=model_name)
    valid = [row for row in results if row.get("status") == "ok"]
    summary.update(
        {
            "evaluation_mode": "NON_EMPIRICAL",
            "empirical": False,
            "avg_diagnostic_f1": round(
                sum(row.get("diagnostic_f1", 0.0) for row in valid) / max(len(valid), 1), 3
            ),
            "format_compliance_rate": sum(bool(row.get("format_compliant")) for row in valid)
            / max(len(valid), 1),
            "tool_arguments_valid_rate": sum(bool(row.get("tool_arguments_valid")) for row in valid)
            / max(len(valid), 1),
            "split": split_name,
        }
    )
    out_dir = output_dir or (RESULTS_DIR / "non_empirical" / tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / f"grpo_{split_name}_episodes.jsonl").open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out_dir / f"grpo_{split_name}_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary


async def evaluate_grpo_split(
    split_name: str,
    model_name: str = "qwen2.5:7b-instruct-grpo",
    mock: bool = False,
    output_dir: Path | None = None,
    *,
    checkpoint: str | Path | None = None,
    state_provider: Callable[[str], Any] | None = None,
    environment: Any = None,
    seed: int = 0,
    generation_config: Mapping[str, Any] | None = None,
    max_steps: int = 5,
    device: str | None = None,
) -> dict[str, Any]:
    """Evaluate a frozen split; real evaluation never falls back to mocks.

    Empirical mode requires a checkpoint, public-state provider, explicit
    environment adapter, and output directory. `mock=True` is only for tests and
    writes outputs marked NON_EMPIRICAL.
    """
    if mock:
        if checkpoint is not None:
            raise ValueError("Mock mode cannot be combined with an empirical checkpoint")
        return await _evaluate_mock_split(
            split_name,
            model_name=model_name,
            output_dir=Path(output_dir) if output_dir is not None else None,
        )
    if checkpoint is None:
        raise ValueError("Empirical G9 evaluation requires an actual --checkpoint")
    if state_provider is None:
        raise ValueError("Empirical G9 evaluation requires a public-state provider")
    if environment is None:
        raise ValueError("Empirical G9 evaluation requires an explicit environment interface")
    if not _authoritative_environment(environment):
        raise ValueError("Empirical G9 evaluation requires the built-in tool and environment verifier")
    if output_dir is None:
        raise ValueError("Empirical G9 evaluation requires an explicit output directory")
    if split_name.strip().lower() == "train":
        raise ValueError("G9 evaluation cannot use the frozen Train split")

    policy = LocalGRPOPolicy.from_checkpoint(checkpoint, device=device)
    provenance = policy.provenance
    evaluator_source = source_identity()
    config = _normalize_generation_config(generation_config)
    scenario_ids = get_split(split_name)
    split_sha256 = _canonical_sha256(list(scenario_ids))
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    event_path = out_dir / f"grpo_{split_name}_{run_id}_events.jsonl"
    summary_path = out_dir / f"grpo_{split_name}_{run_id}_summary.json"
    results: list[dict[str, Any]] = []

    with event_path.open("x", encoding="utf-8") as events:
        def persist(event: dict[str, Any]) -> None:
            events.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            events.flush()
            os.fsync(events.fileno())

        persist(
            {
                "recorded_at": _utc_now(),
                "event": "run_started",
                "evaluation_mode": "EMPIRICAL",
                "split": split_name,
                "split_sha256": split_sha256,
                "seed": seed,
                "generation_config": config,
                "provenance": provenance.to_record(),
                "evaluator_source": evaluator_source,
            }
        )
        for index, scenario_id in enumerate(scenario_ids):
            try:
                initial_state = await _maybe_await(state_provider(scenario_id))
                episode = await evaluate_grpo_episode(
                    scenario_id,
                    initial_state,
                    policy,
                    environment,
                    seed=seed + index,
                    generation_config=config,
                    max_steps=max_steps,
                    evaluation_mode="EMPIRICAL",
                    event_sink=persist,
                )
                episode["split"] = split_name
                episode["split_sha256"] = split_sha256
                episode["provenance"] = provenance.to_record()
                results.append(episode)
            except asyncio.CancelledError:
                persist(
                    {
                        "recorded_at": _utc_now(),
                        "event": "run_interrupted",
                        "scenario_id": scenario_id,
                        "completed_episodes": len(results),
                    }
                )
                raise
            except Exception as exc:  # noqa: BLE001 - preserve per-episode failures and continue the split
                failed = {
                    "schema_version": 1,
                    "evaluation_mode": "EMPIRICAL",
                    "scenario_id": scenario_id,
                    "split": split_name,
                    "split_sha256": split_sha256,
                    "status": "failed",
                    "resolved": False,
                    "env_resolved": False,
                    "failure": f"{type(exc).__name__}: {exc}",
                    "seed": seed + index,
                    "generation_config": config,
                    "started_at": _utc_now(),
                    "finished_at": _utc_now(),
                    "trajectory": [],
                    "provenance": provenance.to_record(),
                }
                results.append(failed)
                persist({"recorded_at": _utc_now(), "event": "episode_failed", "result": failed})

        successful = [row for row in results if row.get("status") == "ok"]
        resolved_times = [
            float(row["time_to_resolve_s"])
            for row in successful
            if row.get("env_resolved") is True
            and isinstance(row.get("time_to_resolve_s"), int | float)
            and not isinstance(row.get("time_to_resolve_s"), bool)
        ]
        format_compliant = [
            bool(row.get("trajectory"))
            and all(
                isinstance(step.get("parsed_action"), Mapping)
                and step.get("parse_error") is None
                for step in row["trajectory"]
            )
            for row in results
        ]
        mean_reward = sum(float(row.get("reward", 0.0)) for row in results) / max(
            len(results), 1
        )
        claim_allowed = bool(results) and len(successful) == len(results)
        summary = {
            "schema_version": 1,
            "run_id": run_id,
            "variant": "Online GRPO RL",
            "evaluation_mode": "empirical",
            "empirical": True,
            "non_empirical": False,
            "empirical_claim_allowed": claim_allowed,
            "split": split_name,
            "split_sha256": split_sha256,
            "scenario_count": len(scenario_ids),
            "completed_episodes": len(results),
            "resolution_rate": sum(row.get("env_resolved") is True for row in results)
            / max(len(results), 1),
            "avg_time_to_resolve_s": (
                sum(resolved_times) / len(resolved_times)
                if resolved_times
                else None
            ),
            "avg_reward_contract": mean_reward,
            "mean_reward": mean_reward,
            "format_compliance_rate": sum(format_compliant) / max(len(results), 1),
            "failed_episodes": sum(row.get("status") != "ok" for row in results),
            "seed": seed,
            "generation_config": config,
            "provenance": provenance.to_record(),
            "evaluator_source": evaluator_source,
            "model": provenance.base_model["id"],
            "raw_trajectory_path": str(event_path.resolve()),
            "finished_at": _utc_now(),
        }
        persist({"recorded_at": _utc_now(), "event": "run_completed", "summary": summary})
    summary["raw_trajectory_sha256"] = _sha256_file(event_path)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def evaluate_grpo_mock_episode(scenario_id: str, model_name: str) -> dict[str, Any]:
    """Generate deterministic NON_EMPIRICAL telemetry for compatibility tests."""
    meta = SCENARIO_CATALOG.get(scenario_id)
    tier = meta.tier if meta else scenario_id.split("/", 1)[0]
    expected_root = meta.expected_root_cause if meta else "pod failure"
    target_svc = meta.target_services[0] if meta and meta.target_services else "frontend"
    predicted = f"Root cause identified: {expected_root} causing latency and 5xx errors on {target_svc}."
    diagnostic = compute_diagnostic_f1(predicted, expected_root)
    is_resolved = sum(ord(char) for char in scenario_id) % 6 != 0
    ttr = 22.0 if is_resolved else 42.0
    reward = {
        "total": round(
            (0.35 if is_resolved else 0.0)
            + max(0.0, min(0.20, (60.0 - ttr) / 60.0 * 0.20))
            + 0.20 * min(diagnostic["f1"], 1.0)
            + 0.15
            + 0.10,
            3,
        ),
        "penalties": {
            "unsafe_shortcut": 0.0,
            "false_resolution": 0.0,
            "hallucinated_evidence": 0.0,
            "command_spam": 0.0,
        },
        "penalty_total": 0.0,
    }
    return {
        "evaluation_mode": "NON_EMPIRICAL",
        "empirical": False,
        "scenario_id": scenario_id,
        "tier": tier,
        "status": "ok",
        "resolved": is_resolved,
        "time_to_resolve_s": ttr,
        "turns": 2 if is_resolved else 3,
        "judge": {
            "correctness": 0.98 if is_resolved else 0.75,
            "efficiency": 0.96 if is_resolved else 0.70,
            "reasoning": 0.95,
            "red_herring_handling": 0.95,
            "overall": 0.92 if is_resolved else 0.65,
            "critique": "Synthetic compatibility fixture; not an empirical judge result.",
        },
        "reward_contract": reward,
        "diagnostic_f1": diagnostic["f1"],
        "diagnostic_precision": diagnostic["precision"],
        "diagnostic_recall": diagnostic["recall"],
        "format_compliant": True,
        "tool_arguments_valid": True,
    }


def _load_public_state(input_dir: Path, scenario_id: str) -> dict[str, Any]:
    relative = Path(*scenario_id.replace("\\", "/").split("/")).with_suffix(".json")
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe scenario path: {scenario_id}")
    root = input_dir.expanduser().resolve()
    state_path = (root / relative).resolve()
    if not state_path.is_relative_to(root):
        raise ValueError(f"State path escapes input directory: {scenario_id}")
    if not state_path.is_file():
        raise FileNotFoundError(f"Public policy state missing: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise TypeError(f"Public policy state must be a JSON object: {state_path}")
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps G9 direct-action policy evaluator")
    parser.add_argument("--split", default="val", choices=["val", "test", "leaderboard"])
    parser.add_argument("--checkpoint", type=Path, help="Completed GRPO checkpoint with grpo_run_manifest.json")
    parser.add_argument("--state-dir", type=Path, help="Public state JSON root, preserving split scenario paths")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=5)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--device")
    parser.add_argument("--execute-actions", action="store_true", help="Allow policy actions to reach the configured environment tools")
    parser.add_argument("--mock", action="store_true", help="Run deterministic NON_EMPIRICAL compatibility fixtures")
    parser.add_argument("--model", default="qwen2.5:7b-instruct-grpo", help="NON_EMPIRICAL compatibility label")
    args = parser.parse_args()

    if args.mock:
        asyncio.run(
            evaluate_grpo_split(
                args.split,
                model_name=args.model,
                mock=True,
                output_dir=args.output_dir,
            )
        )
        return
    if not args.checkpoint or not args.state_dir or not args.output_dir:
        parser.error("Empirical mode requires --checkpoint, --state-dir, and --output-dir")
    if not args.execute_actions:
        parser.error("Empirical mode requires --execute-actions to enable environment interaction")
    asyncio.run(
        evaluate_grpo_split(
            args.split,
            checkpoint=args.checkpoint,
            state_provider=lambda scenario_id: _load_public_state(args.state_dir, scenario_id),
            environment=DirectPolicyEnvironment(),
            seed=args.seed,
            generation_config={
                "max_new_tokens": args.max_new_tokens,
                "temperature": args.temperature,
                "top_p": args.top_p,
            },
            max_steps=args.max_steps,
            output_dir=args.output_dir,
            device=args.device,
        )
    )


if __name__ == "__main__":
    main()
