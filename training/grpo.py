"""Free-first Stage 9 GRPO training with paired remediation-policy rollouts.

A dataset row owns one model-visible remediation observation and its hidden,
split-safe environment identity. TRL repeats that row for the policy group, so
every completion is scored against the same hidden scenario. No scenario is
sampled after generation.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.tool_policy import CLUSTER_MUTATING_TOOLS
from config.runtime import (
    CurriculumManager,
    evaluate_reward_contract,
)
from training.stage9_contract import (
    load_remediation_training_rows,
    remediation_dataset_catalogue,
)


EXPECTED_TRAINING_VERSIONS = {
    "torch": "2.7.1",
    "transformers": "4.57.6",
    "trl": "0.19.1",
    "peft": "0.17.1",
    "datasets": "4.8.5",
    "accelerate": "1.14.0",
}
CANONICAL_LOCAL_ENVIRONMENT_PROVIDER = "local-kind"
CANONICAL_LOCAL_KUBERNETES_CONTEXT = "kind-atlasops-local"
_CONTEXT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
REWARD_CONTRACT_VERSION = "stage9-policy-attributed-v2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Training-time-only dependencies are imported inside the functions that need
# them so coupling audits do not require a GPU/TRL installation.

log = logging.getLogger(__name__)


# ── QLoRA config ──────────────────────────────────────────────────────────────

LORA_HYPERPARAMETERS = {
    "task_type": "CAUSAL_LM",
    "r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "target_modules": [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    "bias": "none",
}

BNB_CONFIG_ARGUMENTS = {
    "load_in_4bit": True,
    "bnb_4bit_quant_type": "nf4",
    "bnb_4bit_compute_dtype": "bfloat16",
    "bnb_4bit_use_double_quant": True,
}


# ── Reward contract ───────────────────────────────────────────────────────────

# Training-run curriculum singleton (tracks mastery + spaced repetition)
_curriculum = CurriculumManager(seed=0)


@dataclass(frozen=True)
class EnvironmentIdentity:
    provider: str
    kubernetes_context: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kubernetes_context": self.kubernetes_context,
            "provider": self.provider,
        }


class InfrastructureInvalid(RuntimeError):
    """An environment/setup/reset failure is never a policy reward of zero."""


class PolicyExecutionInvalid(RuntimeError):
    """The rollout harness failed outside the policy's one-action decision."""


def resolve_environment_identity(provider: str, context: str) -> EnvironmentIdentity:
    if provider != CANONICAL_LOCAL_ENVIRONMENT_PROVIDER:
        raise ValueError(
            "stage9_environment_unapproved: only local-kind has an execution "
            "contract; paid/cloud execution requires a separate reviewed path"
        )
    if context != CANONICAL_LOCAL_KUBERNETES_CONTEXT or not _CONTEXT_RE.fullmatch(context):
        raise ValueError(
            f"stage9_context_unapproved:{context!r}; expected "
            f"{CANONICAL_LOCAL_KUBERNETES_CONTEXT}"
        )
    return EnvironmentIdentity(provider=provider, kubernetes_context=context)


def _run_kubectl(
    identity: EnvironmentIdentity,
    args: list[str],
    *,
    timeout: int = 30,
) -> dict[str, Any]:
    command = ["kubectl", "--context", identity.kubernetes_context, *args]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InfrastructureInvalid(f"kubectl_execution_failed:{exc}") from exc
    return {
        "command": command,
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "stdout": completed.stdout,
        "success": completed.returncode == 0,
    }


@contextmanager
def _approved_tool_context(identity: EnvironmentIdentity):
    """Make coordinator-owned kubectl wrappers use the same explicit context."""
    watched_keys = (
        "DISCORD_WEBHOOK_URL",
        "KUBECONFIG_CONTEXT",
        "SLACK_WEBHOOK_URL",
    )
    previous = {key: os.environ.get(key) for key in watched_keys}
    os.environ["KUBECONFIG_CONTEXT"] = identity.kubernetes_context
    for webhook_key in ("DISCORD_WEBHOOK_URL", "SLACK_WEBHOOK_URL"):
        os.environ.pop(webhook_key, None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def verify_kubernetes_environment(identity: EnvironmentIdentity) -> dict[str, Any]:
    if identity.provider != CANONICAL_LOCAL_ENVIRONMENT_PROVIDER:
        raise InfrastructureInvalid("environment_provider_invalid")
    result = _run_kubectl(identity, ["config", "current-context"], timeout=10)
    observed = result.get("stdout", "").strip()
    if not result["success"] or observed != identity.kubernetes_context:
        raise InfrastructureInvalid(
            f"kubernetes_context_mismatch:expected={identity.kubernetes_context},"
            f"observed={observed or 'none'}"
        )
    return {"status": "CONTEXT_VERIFIED", "identity": identity.to_dict()}


def cluster_healthy(
    identity: EnvironmentIdentity,
    *,
    minimum_ready_deployments: int = 12,
) -> tuple[bool, dict[str, Any]]:
    result = _run_kubectl(
        identity,
        ["get", "deployments", "--namespace", "default", "--output", "json"],
    )
    if not result["success"]:
        return False, {"reason": "deployment_query_failed", "result": result}
    try:
        payload = json.loads(result["stdout"] or "{}")
    except json.JSONDecodeError:
        return False, {"reason": "deployment_payload_invalid"}
    deployments = [
        item for item in payload.get("items", [])
        if isinstance(item, dict)
    ]
    ready = [
        item for item in deployments
        if (item.get("spec", {}).get("replicas") or 0)
        == (item.get("status", {}).get("readyReplicas") or 0)
    ]
    details = {
        "deployments": len(deployments),
        "ready_deployments": len(ready),
        "minimum_required": minimum_ready_deployments,
    }
    return (
        len(deployments) >= minimum_ready_deployments and len(ready) == len(deployments),
        details,
    )


def apply_fault(identity: EnvironmentIdentity, scenario_id: str) -> dict[str, Any]:
    scenario_parts = scenario_id.split("/")
    if (
        len(scenario_parts) != 2
        or any(not part or part in {".", ".."} for part in scenario_parts)
        or any(not _CONTEXT_RE.fullmatch(part) for part in scenario_parts)
    ):
        raise InfrastructureInvalid(f"fault_scenario_identity_invalid:{scenario_id}")
    manifest = Path("bench/chaos_manifests") / f"{scenario_id}.yaml"
    resolved_manifest = manifest.resolve()
    try:
        resolved_manifest.relative_to((Path("bench/chaos_manifests")).resolve())
    except ValueError as exc:
        raise InfrastructureInvalid(f"fault_manifest_path_escape:{scenario_id}") from exc
    if not resolved_manifest.is_file():
        raise InfrastructureInvalid(f"fault_manifest_missing:{scenario_id}")
    result = _run_kubectl(
        identity,
        ["apply", "--filename", str(resolved_manifest)],
        timeout=60,
    )
    if not result["success"]:
        raise InfrastructureInvalid(f"fault_apply_failed:{scenario_id}")
    return {"status": "FAULT_APPLIED", "manifest": str(manifest)}


def fault_established(identity: EnvironmentIdentity, scenario_id: str) -> dict[str, Any]:
    scenario_name = scenario_id.rsplit("/", 1)[-1]
    result = _run_kubectl(
        identity,
        [
            "get",
            "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
            "--namespace", "chaos-mesh", "--output", "json",
        ],
    )
    if not result["success"]:
        raise InfrastructureInvalid("fault_state_query_failed")
    try:
        payload = json.loads(result["stdout"] or "{}")
    except json.JSONDecodeError as exc:
        raise InfrastructureInvalid("fault_state_payload_invalid") from exc
    matches = [
        item for item in payload.get("items", [])
        if isinstance(item, dict)
        and item.get("metadata", {}).get("name") == scenario_name
        and item.get("metadata", {}).get("labels", {}).get("scenario") == scenario_name
    ]
    if len(matches) != 1:
        raise InfrastructureInvalid(
            f"fault_not_objectively_established:{scenario_id}:matches={len(matches)}"
        )
    return {"status": "FAULT_ESTABLISHED", "matching_resources": len(matches)}


def reset_faults(identity: EnvironmentIdentity) -> dict[str, Any]:
    result = _run_kubectl(
        identity,
        [
            "delete",
            "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
            "--all", "--all-namespaces", "--ignore-not-found=true",
        ],
        timeout=60,
    )
    if not result["success"]:
        raise InfrastructureInvalid("fault_reset_failed")
    return {"status": "FAULTS_RESET"}


def _policy_owned_action(final: dict[str, Any]) -> dict[str, Any] | None:
    executed = final.get("executed_actions")
    if (
        final.get("mode") != "policy_rollout"
        or final.get("policy_completion_valid") is not True
        or final.get("policy_action_identity_match") is not True
        or final.get("policy_action_admitted") is not True
        or not isinstance(executed, list)
        or len(executed) != 1
        or not isinstance(executed[0], dict)
    ):
        return None
    return executed[0]


def compute_reward_breakdown(episode: dict) -> dict[str, Any]:
    """Compute a policy-attributed reward without reading other agents' rewards."""
    audit = evaluate_reward_contract({
        **episode,
        "judge": {},
        "postmortem_path": None,
    })
    remediation = episode.get("remediation", {})
    final = remediation.get("final", {}) if isinstance(remediation, dict) else {}
    owned = _policy_owned_action(final)
    env_resolved = episode.get("env_resolved") is True
    mutating_success = bool(
        owned
        and owned.get("tool") in CLUSTER_MUTATING_TOOLS
        and owned.get("success") is True
    )
    nonmutating_success = bool(
        owned
        and owned.get("tool") not in CLUSTER_MUTATING_TOOLS
        and owned.get("success") is True
    )
    resolution = 0.85 if env_resolved and mutating_success else 0.0
    partial = 0.25 if (
        not resolution
        and str(episode.get("outcome")) == "partial"
        and (mutating_success or nonmutating_success)
    ) else 0.0
    dense = 0.08 if mutating_success else (0.04 if nonmutating_success else 0.0)
    penalties = {
        key: float(value)
        for key, value in audit.get("penalties", {}).items()
    }
    # Stage 9 deliberately optimizes one remediation decision. Generic
    # turn-count heuristics would measure the surrounding multi-agent harness,
    # not the optimized policy.
    penalties.pop("phase_skip", None)
    penalties.pop("lazy_investigation", None)
    penalties["invalid_policy_completion"] = (
        0.25 if final.get("policy_completion_valid") is False else 0.0
    )
    penalties["unadmitted_policy_action"] = (
        0.20 if final.get("policy_denial_reason") else 0.0
    )
    penalties["failed_policy_mutation"] = (
        0.05
        if owned
        and owned.get("tool") in CLUSTER_MUTATING_TOOLS
        and owned.get("success") is not True
        else 0.0
    )
    penalty_total = sum(penalties.values())
    raw_total = resolution + partial + dense - penalty_total
    total = max(-1.0, min(1.0, raw_total))
    return {
        "components": {
            "dense_policy_action": dense,
            "partial_progress": partial,
            "resolution": resolution,
        },
        "penalties": penalties,
        "penalty_total": round(penalty_total, 4),
        "raw_total": round(raw_total, 4),
        "total": round(total, 4),
    }


def compute_reward(episode: dict) -> float:
    """Return the policy-attributed Stage 9 scalar reward."""
    return float(compute_reward_breakdown(episode)["total"])


def sample_training_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Select one immutable prompt/scenario pair using curriculum state."""
    representatives: dict[str, dict[str, Any]] = {}
    pool: list[tuple[str, str]] = []
    for row in rows:
        group = row["stage9_group_id"]
        if group not in representatives:
            representatives[group] = row
            pool.append((group, row["hidden_metadata"]["tier"]))
    if not pool:
        raise RuntimeError("stage9_training_pool_empty")
    selected_group, _tier = _curriculum.next_scenario(pool)
    return representatives[selected_group]


def validate_reward_pairing(
    prompts: list[Any],
    completions: list[Any],
    metadata: dict[str, list[Any]],
    rows_by_group: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Guarantee each completion stays paired with its original hidden row."""
    count = len(completions)
    if len(prompts) != count:
        raise RuntimeError("prompt_completion_count_mismatch")
    required = {
        "prompt_sha256", "provenance_hash", "role", "row_id", "stage9_group_id",
    }
    missing_columns = sorted(required.difference(metadata))
    if missing_columns:
        raise RuntimeError(f"reward_metadata_missing:{','.join(missing_columns)}")
    columns = {key: metadata[key] for key in required}
    if any(len(values) != count for values in columns.values()):
        raise RuntimeError("reward_metadata_count_mismatch")

    paired_rows: list[dict[str, Any]] = []
    for index, prompt in enumerate(prompts):
        expected_prompt_hash = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
        if columns["prompt_sha256"][index] != expected_prompt_hash:
            raise RuntimeError(f"prompt_metadata_mismatch:row={index}")
        group = columns["stage9_group_id"][index]
        row = rows_by_group.get(group)
        if row is None:
            raise RuntimeError(f"unknown_stage9_group:{group}")
        if row["prompt_sha256"] != expected_prompt_hash:
            raise RuntimeError(f"prompt_row_mismatch:group={group}")
        if row["provenance_hash"] != columns["provenance_hash"][index]:
            raise RuntimeError(f"scenario_metadata_mismatch:group={group}")
        if row["role"] != columns["role"][index] or row["row_id"] != columns["row_id"][index]:
            raise RuntimeError(f"row_identity_mismatch:group={group}")
        paired_rows.append(row)
    return paired_rows


# ── Online reward function for TRL GRPOTrainer ────────────────────────────────

class OnlineRewardFunction:
    """Score each completion against its own dataset-paired hidden scenario."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        environment: EnvironmentIdentity,
        curriculum_seed: int,
        dataset_sha256: str,
        episodes_path: str | Path | None = None,
        curriculum_state_path: str | Path | None = None,
        fault_settle_seconds: int = 15,
        reset_settle_seconds: int = 10,
    ):
        self.rows = list(rows)
        self.rows_by_group = {row["stage9_group_id"]: row for row in rows}
        self.environment = environment
        self.curriculum_seed = curriculum_seed
        self.dataset_sha256 = dataset_sha256
        self.episodes_path = Path(episodes_path) if episodes_path else None
        self.curriculum_state_path = (
            Path(curriculum_state_path) if curriculum_state_path else None
        )
        self._loop = asyncio.new_event_loop()
        self.fault_settle_seconds = fault_settle_seconds
        self.reset_settle_seconds = reset_settle_seconds

    def __del__(self):
        if not self._loop.is_closed():
            self._loop.close()

    def __call__(
        self,
        completions: list[str],
        prompts: list[str],
        completion_ids: list[Any] | None = None,
        **kwargs,
    ) -> list[float]:
        """Called by TRL after generating G completions. Returns reward per completion."""
        if completion_ids is not None and len(completion_ids) != len(completions):
            raise RuntimeError("completion_id_count_mismatch")
        return self._loop.run_until_complete(
            self._score_batch(prompts, completions, kwargs)
        )

    async def _score_batch(self, completions: list[str],
                           prompts: list[str],
                           metadata: dict[str, list[Any]]) -> list[float]:
        """Execute serialized, independently reset equivalent environments."""
        rewards: list[float] = []
        paired_rows = validate_reward_pairing(
            prompts,
            completions,
            metadata,
            self.rows_by_group,
        )
        verify_kubernetes_environment(self.environment)
        for index, (completion, row) in enumerate(zip(completions, paired_rows)):
            log.info(
                "Rollout %d/%d — group %s",
                index + 1,
                len(completions),
                row["stage9_group_id"],
            )
            result = await self._score_paired_rollout(row, completion)
            reward_breakdown = compute_reward_breakdown(result["episode"])
            reward = float(reward_breakdown["total"])
            rewards.append(reward)
            _curriculum.record(
                scenario_id=row["stage9_group_id"],
                resolved=bool(result["episode"].get("env_resolved") is True),
                reward=reward,
            )
            self._persist_episode(index, row, completion, reward_breakdown, result)
            self._persist_curriculum_state()

        cur_stats = _curriculum.stats()
        log.info(
            "Batch done | rewards: min=%.3f max=%.3f mean=%.3f | "
            "curriculum: %d tried, %d graduated, %d due for resurface",
            min(rewards), max(rewards), sum(rewards) / len(rewards),
            cur_stats["scenarios_tried"], cur_stats["graduated"],
            cur_stats["due_for_resurface"],
        )
        return rewards

    async def _score_paired_rollout(
        self,
        row: dict[str, Any],
        completion_text: str,
    ) -> dict[str, Any]:
        """Run healthy/fault/action/verifier/reset/post-health lifecycle."""
        from agents.coordinator import handle_incident

        hidden = row["hidden_metadata"]
        scenario_id = hidden["scenario_id"]
        lifecycle: list[dict[str, Any]] = []
        verify_kubernetes_environment(self.environment)
        lifecycle.append({"phase": "CONTEXT_VERIFIED", "passed": True})
        pre_health, pre_details = cluster_healthy(self.environment)
        lifecycle.append({"phase": "PRE_ROLLOUT_HEALTHY", "passed": pre_health, **pre_details})
        if not pre_health:
            raise InfrastructureInvalid("pre_rollout_unhealthy")
        lifecycle.append(apply_fault(self.environment, scenario_id))
        try:
            await asyncio.sleep(self.fault_settle_seconds)
            lifecycle.append(fault_established(self.environment, scenario_id))
        finally:
            await self._reset_and_verify(lifecycle)

        try:
            alert = {
                "alerts": [],
                "commonLabels": {"alertname": "GRPOTrainingAlert"},
            }
            started = time.time()
            with _approved_tool_context(self.environment):
                incident = await handle_incident(
                    alert,
                    scenario_id=scenario_id,
                    remediation_policy_completion=completion_text,
                )
        except Exception as exc:
            raise PolicyExecutionInvalid(f"rollout_harness_error:{exc}") from exc
        finally:
            await self._reset_and_verify(lifecycle)

        remediation = incident.get("remediation", {}).get("final", {})
        env_resolved = bool(incident.get("env_resolved") is True)
        agent_claimed_resolved = bool(incident.get("agent_claimed_resolved"))
        return {
            "episode": {
                "agent_claimed_resolved": agent_claimed_resolved,
                "comms": incident.get("comms", {}),
                "diagnosis": incident.get("diagnosis", {}),
                "env_resolved": env_resolved,
                "outcome": remediation.get("outcome", "unknown"),
                "remediation": incident.get("remediation", {}),
                "resolved": env_resolved,
                "time_to_resolve_s": round(time.time() - started),
                "tier": hidden["tier"],
                "total_turns": sum(
                    len(incident.get(role, {}).get("trajectory", []))
                    for role in ("triage", "diagnosis", "remediation", "comms")
                ),
                "triage": incident.get("triage", {}),
                "verification": incident.get("verification", {}),
            },
            "lifecycle": lifecycle,
            "model_visible_alert": alert,
        }

    async def _reset_and_verify(self, lifecycle: list[dict[str, Any]]) -> None:
        """Reset even after setup/harness failure and classify invalidity."""
        reset_error: InfrastructureInvalid | None = None
        try:
            lifecycle.append(reset_faults(self.environment))
        except InfrastructureInvalid as exc:
            reset_error = exc
        await self._reset_settle()
        post_health, post_details = cluster_healthy(self.environment)
        lifecycle.append({
            "phase": "POST_RESET_HEALTHY",
            "passed": post_health,
            **post_details,
        })
        if reset_error is not None:
            raise reset_error
        if not post_health:
            raise InfrastructureInvalid("post_reset_unhealthy")

    async def _reset_settle(self) -> None:
        await asyncio.sleep(self.reset_settle_seconds)

    def _persist_episode(
        self,
        rollout_index: int,
        row: dict[str, Any],
        completion: str,
        reward_breakdown: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if not self.episodes_path:
            return
        self.episodes_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "episode": result["episode"],
            "hidden_metadata": row["hidden_metadata"],
            "lifecycle": result["lifecycle"],
            "model_visible_alert": result["model_visible_alert"],
            "policy_completion": completion,
            "policy_completion_sha256": hashlib.sha256(
                completion.encode("utf-8")
            ).hexdigest(),
            "prompt_sha256": row["prompt_sha256"],
            "reward": reward_breakdown,
            "reward_contract_version": REWARD_CONTRACT_VERSION,
            "rollout_index": rollout_index,
            "row_id": row["row_id"],
            "stage9_group_id": row["stage9_group_id"],
        }
        with self.episodes_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def _persist_curriculum_state(self) -> None:
        if not self.curriculum_state_path:
            return
        write_curriculum_state(
            self.curriculum_state_path,
            self.curriculum_seed,
            self.dataset_sha256,
        )


# ── Optuna HP search ──────────────────────────────────────────────────────────

def run_optuna_search(**_kwargs: Any) -> dict[str, Any]:
    """HP search remains disabled until legal G5/G7/G8 inputs exist."""
    raise RuntimeError(
        "stage9_hp_search_disabled: legal split-safe remediation rows and a "
        "frozen G5 API are prerequisites; final-test leakage is prohibited"
    )


# ── Model loading ─────────────────────────────────────────────────────────────

def validate_sft_checkpoint(
    model_path: str | Path,
    *,
    verify_files: bool = True,
) -> dict[str, Any]:
    """Validate a complete merged G8 decoder and its sidecar provenance."""
    path = Path(model_path)
    if not path.is_dir():
        raise ValueError("sft_checkpoint_missing")
    if (path / "adapter_config.json").exists():
        raise ValueError("adapter_only_checkpoint_rejected")
    manifest_path = path / "checkpoint_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("sft_provenance_manifest_missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("sft_provenance_manifest_invalid_json") from exc
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported_sft_manifest_schema")
    if manifest.get("checkpoint_kind") != "merged_decoder":
        raise ValueError("sft_checkpoint_kind_rejected")
    if manifest.get("stage") != "G8":
        raise ValueError("sft_stage_must_be_g8")
    evaluation = manifest.get("g8_evaluation")
    if not isinstance(evaluation, dict) or evaluation.get("passed") is not True:
        raise ValueError("g8_evaluation_not_passed")
    corpus = manifest.get("sft_corpus")
    if not isinstance(corpus, dict) or not _SHA256_RE.fullmatch(str(corpus.get("sha256", ""))):
        raise ValueError("sft_corpus_hash_missing")
    base_model = manifest.get("base_model")
    if (
        not isinstance(base_model, dict)
        or not str(base_model.get("name", "")).strip()
        or not str(base_model.get("revision", "")).strip()
        or not str(base_model.get("architecture", "")).strip()
    ):
        raise ValueError("sft_base_identity_missing")
    tokenizer_identity = manifest.get("tokenizer")
    if (
        not isinstance(tokenizer_identity, dict)
        or not str(tokenizer_identity.get("name", "")).strip()
        or not str(tokenizer_identity.get("revision", "")).strip()
    ):
        raise ValueError("sft_tokenizer_identity_missing")
    lora_provenance = manifest.get("lora")
    required_lora = {
        key: LORA_HYPERPARAMETERS[key]
        for key in ("r", "lora_alpha", "lora_dropout", "target_modules", "bias")
    }
    if not isinstance(lora_provenance, dict) or {
        key: lora_provenance.get(key) for key in required_lora
    } != required_lora:
        raise ValueError("sft_lora_provenance_mismatch")

    required_files = ("config.json", "generation_config.json", "tokenizer_config.json")
    missing = [name for name in required_files if not (path / name).is_file()]
    has_weights = any((path / name).is_file() for name in (
        "model.safetensors", "pytorch_model.bin", "model.safetensors.index.json",
    ))
    if missing or not has_weights:
        raise ValueError(f"incomplete_merged_sft_checkpoint:missing={missing}")

    file_hashes = manifest.get("file_hashes")
    if not isinstance(file_hashes, dict):
        raise ValueError("sft_file_hashes_missing")
    for relative_name in required_files:
        expected = file_hashes.get(relative_name)
        actual = _sha256_file(path / relative_name) if (path / relative_name).is_file() else None
        if expected != actual:
            raise ValueError(f"sft_file_hash_mismatch:{relative_name}")
    try:
        architecture_config = json.loads((path / "config.json").read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("sft_model_config_invalid_json") from exc
    if architecture_config.get("model_type") != base_model["architecture"]:
        raise ValueError("sft_architecture_mismatch")
    if verify_files:
        for relative_name, expected in file_hashes.items():
            target = (path / relative_name).resolve()
            try:
                target.relative_to(path.resolve())
            except ValueError as exc:
                raise ValueError(f"sft_manifest_path_escape:{relative_name}") from exc
            if not target.is_file():
                raise ValueError(f"sft_manifest_file_missing:{relative_name}")
            if _SHA256_RE.fullmatch(str(expected)) and _sha256_file(target) != expected:
                raise ValueError(f"sft_file_hash_mismatch:{relative_name}")
    return manifest


def load_model_and_tokenizer(model_path: str):
    from peft import get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoTokenizer

    validate_sft_checkpoint(model_path)
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = load_quantized_base_model(model_path)
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, _lora_config())
    model.print_trainable_parameters()
    return model, tokenizer


def load_quantized_base_model(model_path: str):
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    return AutoModelForCausalLM.from_pretrained(
        model_path,
        quantization_config=BitsAndBytesConfig(**BNB_CONFIG_ARGUMENTS),
        device_map="auto",
        trust_remote_code=True,
        attn_implementation="flash_attention_2" if _flash_attn_available() else "eager",
    )


def _lora_config():
    from peft import LoraConfig

    return LoraConfig(**LORA_HYPERPARAMETERS)


def _flash_attn_available() -> bool:
    try:
        import flash_attn  # noqa: F401
        return True
    except ImportError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_file_sha(path: Path) -> str:
    content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(content).hexdigest()


def _raw_dataset_provenance(path: Path) -> dict[str, Any]:
    count = 0
    if not path.exists():
        return {"path": str(path), "exists": False, "count": 0, "sha256": None}
    with path.open("rb") as f:
        for _ in f:
            count += 1
    return {
        "path": str(path),
        "exists": True,
        "count": count,
        "sha256": _sha256_file(path),
    }


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def build_training_provenance(
    model_path: str,
    output_dir: Path,
    dataset_path: Path,
    curriculum_seed: int,
    environment: EnvironmentIdentity,
    hyperparameters: dict[str, Any],
) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    sft_manifest = validate_sft_checkpoint(model_path, verify_files=False)
    rows = load_remediation_training_rows(dataset_path)
    catalogue = remediation_dataset_catalogue(rows)
    contracts = {
        name: _normalized_file_sha(root / relative)
        for name, relative in {
            "remediation_prompt": Path("agents/prompts/remediation.md"),
            "tool_policy": Path("agents/tool_policy.py"),
            "tool_schemas_coordinator": Path("agents/coordinator.py"),
            "kubectl_response_contract": Path("agents/tools/kubectl.py"),
            "argocd_response_contract": Path("agents/tools/argocd.py"),
            "verifier": Path("agents/verifier.py"),
            "reward_implementation": Path("training/grpo.py"),
            "row_contract": Path("training/stage9_contract.py"),
        }.items()
    }
    return {
        "schema_version": 2,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_commit": _git_commit(),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "model_path": model_path,
        "sft_manifest": sft_manifest,
        "dataset": {
            **_raw_dataset_provenance(dataset_path),
            "contract": catalogue,
            "role_policy": "remediation_only",
            "split_source": "canonical_frozen_rows_required",
        },
        "environment_identity": environment.to_dict(),
        "contracts": {
            **contracts,
            "reward_version": REWARD_CONTRACT_VERSION,
        },
        "curriculum_seed": curriculum_seed,
        "lora": LORA_HYPERPARAMETERS,
        "quantization": BNB_CONFIG_ARGUMENTS,
        "hyperparameters": hyperparameters,
        "output_dir": str(output_dir),
    }


def observed_dependency_versions() -> dict[str, str]:
    import accelerate
    import datasets
    import peft
    import torch
    import transformers
    import trl

    observed = {
        "accelerate": accelerate.__version__,
        "datasets": datasets.__version__,
        "peft": peft.__version__,
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "trl": trl.__version__,
    }
    mismatched = {
        name: {"expected": expected, "observed": observed.get(name)}
        for name, expected in EXPECTED_TRAINING_VERSIONS.items()
        if observed.get(name) != expected
    }
    if mismatched:
        raise RuntimeError(f"stage9_training_dependency_mismatch:{mismatched}")
    return observed


def validate_trl_runtime() -> dict[str, str]:
    """Fail closed unless the audited TRL metadata-preservation contract is exact."""
    return observed_dependency_versions()


def curriculum_state_payload(seed: int, dataset_hash: str) -> dict[str, Any]:
    return {
        "curriculum": _curriculum.export_state(),
        "provenance": {
            "curriculum_seed": seed,
            "dataset_sha256": dataset_hash,
            "reward_version": REWARD_CONTRACT_VERSION,
        },
    }


def restore_curriculum_payload(payload: dict[str, Any], seed: int, dataset_hash: str) -> None:
    if payload.get("provenance") != {
        "curriculum_seed": seed,
        "dataset_sha256": dataset_hash,
        "reward_version": REWARD_CONTRACT_VERSION,
    }:
        raise RuntimeError("curriculum_resume_provenance_mismatch")
    _curriculum.restore_state(payload["curriculum"])


def write_curriculum_state(path: Path, seed: int, dataset_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(curriculum_state_payload(seed, dataset_hash), sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


RESUME_IDENTITY_FIELDS = frozenset({
    "code_commit",
    "contracts",
    "curriculum_seed",
    "dataset",
    "dependency_versions",
    "environment_identity",
    "hyperparameters",
    "model_path",
    "sft_manifest",
})


def validate_resume_identity(previous: dict[str, Any], current: dict[str, Any]) -> None:
    """Reject model, split, contract, seed, environment, or dependency drift."""
    changed = sorted(
        field for field in RESUME_IDENTITY_FIELDS
        if previous.get(field) != current.get(field)
    )
    if changed:
        raise RuntimeError(f"incompatible_resume_identity:{changed}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    from trl import GRPOConfig, GRPOTrainer

    parser = argparse.ArgumentParser()
    parser.add_argument("--model",           required=True)
    parser.add_argument("--output",          required=True)
    parser.add_argument("--training-rows",   required=True)
    parser.add_argument("--environment-provider", required=True)
    parser.add_argument("--kubernetes-context", required=True)
    parser.add_argument("--lr",              type=float, default=1e-6)
    parser.add_argument("--beta",            type=float, default=0.04)
    parser.add_argument("--batch-size",      type=int,   default=1)
    parser.add_argument("--num-generations", type=int,   default=8)
    parser.add_argument("--max-steps",       type=int,   default=200)
    parser.add_argument("--max-compl-len",   type=int,   default=512)
    parser.add_argument("--grad-accum",      type=int,   default=4)
    parser.add_argument("--optuna",          type=int,   default=0)
    parser.add_argument("--curriculum-seed", type=int, default=42)
    parser.add_argument("--resume-from-checkpoint", default="")
    args = parser.parse_args()

    environment = resolve_environment_identity(
        args.environment_provider,
        args.kubernetes_context,
    )
    training_rows_path = Path(args.training_rows)
    rows = load_remediation_training_rows(
        training_rows_path,
        allowed_eligibility=("training",),
    )
    raw_dataset_hash = _sha256_file(training_rows_path)
    dataset_contract = remediation_dataset_catalogue(rows)
    hyperparameters = {
        "beta": args.beta,
        "batch_size": args.batch_size,
        "gradient_accumulation_steps": args.grad_accum,
        "learning_rate": args.lr,
        "max_completion_length": args.max_compl_len,
        "max_steps": args.max_steps,
        "num_generations": args.num_generations,
    }
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    curriculum_seed = args.curriculum_seed
    curriculum_state_path = output_dir / "curriculum_state.json"
    episodes_path = output_dir / "grpo_episodes.jsonl"
    resume_checkpoint = args.resume_from_checkpoint
    validate_sft_checkpoint(args.model, verify_files=True)
    if resume_checkpoint:
        if not curriculum_state_path.exists():
            raise RuntimeError(
                "resume_provenance_missing: curriculum_state.json is required "
                "when resuming a GRPO checkpoint"
            )
        restore_curriculum_payload(
            json.loads(curriculum_state_path.read_text(encoding="utf-8")),
            curriculum_seed,
            raw_dataset_hash,
        )
        if not episodes_path.is_file():
            raise RuntimeError("resume_episode_evidence_missing")
    else:
        if episodes_path.exists():
            raise RuntimeError("refusing_to_overwrite_existing_episode_evidence")
        _curriculum._rng = random.Random(curriculum_seed)
        _curriculum._history.clear()
        _curriculum._graduated.clear()
        _curriculum._next_resurface.clear()
        _curriculum._recent.clear()
        _curriculum._episode_count = 0
    provenance = build_training_provenance(
        args.model,
        output_dir,
        training_rows_path,
        curriculum_seed,
        environment,
        hyperparameters,
    )
    provenance["resumed_from"] = resume_checkpoint or None
    provenance["dependency_versions"] = validate_trl_runtime()
    manifest_path = output_dir / "run_manifest.json"
    if resume_checkpoint:
        if not manifest_path.is_file():
            raise RuntimeError("resume_run_manifest_missing")
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_resume_identity(previous_manifest, provenance)
    else:
        manifest_path.write_text(
            json.dumps(provenance, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    write_curriculum_state(curriculum_state_path, curriculum_seed, raw_dataset_hash)
    if args.optuna:
        raise RuntimeError(
            "stage9_hp_search_disabled: use the explicitly authorized main trainer only"
        )

    observed_versions = provenance["dependency_versions"]
    lr = args.lr
    beta = args.beta
    num_gen = args.num_generations
    log.info(
        "Stage 9 GRPO config: lr=%.2e beta=%.4f num_gen=%d rows=%d groups=%d",
        lr,
        beta,
        num_gen,
        len(rows),
        dataset_contract["unique_stage9_groups"],
    )

    model, tokenizer = load_model_and_tokenizer(args.model)

    reward_fn = OnlineRewardFunction(
        rows,
        environment,
        curriculum_seed,
        raw_dataset_hash,
        episodes_path=output_dir / "grpo_episodes.jsonl",
        curriculum_state_path=curriculum_state_path,
    )

    from datasets import Dataset
    metadata_columns = (
        "prompt", "prompt_sha256", "provenance_hash", "role", "row_id",
        "stage9_group_id", "hidden_metadata",
    )
    dataset = Dataset.from_list([
        {column: row[column] for column in metadata_columns}
        for row in rows
    ])

    grpo_args = GRPOConfig(
        output_dir=str(output_dir),
        learning_rate=lr,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        bf16=True,
        logging_steps=5,
        save_strategy="steps",
        save_steps=50,
        max_steps=args.max_steps,
        report_to=[],
        optim="paged_adamw_8bit",
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
        num_generations=num_gen,
        max_completion_length=args.max_compl_len,
        beta=beta,
        seed=curriculum_seed,
        data_seed=curriculum_seed,
        save_safetensors=True,
        save_total_limit=3,
        remove_unused_columns=False,
    )

    trainer = GRPOTrainer(
        model=model,
        args=grpo_args,
        train_dataset=dataset,
        processing_class=tokenizer,
        reward_funcs=[reward_fn],  # ← online RL against real GKE cluster
    )

    log.info("Starting local free-first GRPO; each completion gets an independently reset paired environment.")
    trainer.train(resume_from_checkpoint=resume_checkpoint or None)

    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    logs = trainer.state.log_history
    rewards = [l.get("rewards/mean") for l in logs if "rewards/mean" in l]
    summary = {
        "model": args.model,
        "total_steps": trainer.state.global_step,
        "final_reward_mean": rewards[-1] if rewards else None,
        "best_reward_mean": max(rewards) if rewards else None,
        "reward_history": rewards,
        "config": {"lr": lr, "beta": beta, "num_generations": num_gen},
        "training_mode": "local_free_first_remediation_only_stage9",
        "dependency_versions": observed_versions,
        "provenance": provenance,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "training_summary.json").write_text(json.dumps(summary, indent=2))
    log.info("Done. final_reward=%.4f | best=%.4f",
             summary["final_reward_mean"] or 0, summary["best_reward_mean"] or 0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
