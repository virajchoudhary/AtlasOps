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
import uuid
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
    ScenarioFaultPlan,
    build_scenario_fault_plan,
    build_trl_training_record,
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
_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
EPISODE_STATUSES = {
    "COMPLETED",
    "SCIENTIFIC_VERDICT_CAPTURED",
    "INFRASTRUCTURE_INVALID",
    "HARNESS_EXECUTION_INVALID",
    "POLICY_EXECUTION_INVALID",
    "CLEANUP_INVALID",
    "ROLLOUT_EXCEPTION",
}

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


class HarnessExecutionInvalid(RuntimeError):
    """The multi-agent coordinator/verifier harness failed outside the policy's causal decision."""


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


def observe_cluster_fingerprint(identity: EnvironmentIdentity) -> dict[str, Any]:
    """Capture a stable, read-only cluster fingerprint to verify cluster instance identity.

    Fails closed if any essential cluster identity query cannot be observed.
    """
    ns_result = _run_kubectl(
        identity,
        ["get", "namespace", "kube-system", "--output", "json"],
        timeout=10,
    )
    if not ns_result.get("success"):
        raise InfrastructureInvalid(
            f"cluster_fingerprint_incomplete:kube_system_uid_query_failed:{ns_result.get('stderr')}"
        )
    try:
        ns_payload = json.loads(ns_result.get("stdout") or "{}")
        cluster_uid = str(ns_payload.get("metadata", {}).get("uid", "")).strip()
    except json.JSONDecodeError as exc:
        raise InfrastructureInvalid("cluster_fingerprint_incomplete:kube_system_json_invalid") from exc
    if not cluster_uid or cluster_uid == "unknown":
        raise InfrastructureInvalid("cluster_fingerprint_incomplete:kube_system_uid_empty")

    ver_result = _run_kubectl(identity, ["version", "--output", "json"], timeout=10)
    if not ver_result.get("success"):
        raise InfrastructureInvalid(
            f"cluster_fingerprint_incomplete:server_version_query_failed:{ver_result.get('stderr')}"
        )
    try:
        ver_payload = json.loads(ver_result.get("stdout") or "{}")
        server_version = str(ver_payload.get("serverVersion", {}).get("gitVersion", "")).strip()
    except json.JSONDecodeError as exc:
        raise InfrastructureInvalid("cluster_fingerprint_incomplete:server_version_json_invalid") from exc
    if not server_version or server_version == "unknown":
        raise InfrastructureInvalid("cluster_fingerprint_incomplete:server_version_empty")

    endpoint_result = _run_kubectl(
        identity,
        ["config", "view", "--minify", "--output", "json"],
        timeout=10,
    )
    if not endpoint_result.get("success"):
        raise InfrastructureInvalid(
            f"cluster_fingerprint_incomplete:api_endpoint_query_failed:{endpoint_result.get('stderr')}"
        )
    try:
        cfg_payload = json.loads(endpoint_result.get("stdout") or "{}")
        clusters = cfg_payload.get("clusters", [])
        server_endpoint = ""
        if clusters and isinstance(clusters[0], dict):
            server_endpoint = str(clusters[0].get("cluster", {}).get("server", "")).strip()
    except json.JSONDecodeError as exc:
        raise InfrastructureInvalid("cluster_fingerprint_incomplete:api_endpoint_json_invalid") from exc
    if not server_endpoint or server_endpoint == "unknown":
        raise InfrastructureInvalid("cluster_fingerprint_incomplete:api_endpoint_empty")

    return {
        "api_server_endpoint": server_endpoint,
        "cluster_uid": cluster_uid,
        "kubernetes_context": identity.kubernetes_context,
        "provider": identity.provider,
        "server_version": server_version,
    }


def verify_kubernetes_environment(identity: EnvironmentIdentity) -> dict[str, Any]:
    if identity.provider != CANONICAL_LOCAL_ENVIRONMENT_PROVIDER:
        raise InfrastructureInvalid("environment_provider_invalid")
    result = _run_kubectl(identity, ["config", "get-contexts", "--output", "name"], timeout=10)
    observed_contexts = {
        line.strip() for line in result.get("stdout", "").splitlines() if line.strip()
    }
    if (
        not result["success"]
        or identity.kubernetes_context not in observed_contexts
    ):
        raise InfrastructureInvalid(
            f"kubernetes_context_mismatch:expected={identity.kubernetes_context},"
            f"observed={sorted(observed_contexts) or 'none'}"
        )
    fingerprint = observe_cluster_fingerprint(identity)
    return {
        "fingerprint": fingerprint,
        "identity": identity.to_dict(),
        "status": "CONTEXT_VERIFIED",
    }


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


def detect_active_chaos_resources(identity: EnvironmentIdentity) -> list[dict[str, Any]]:
    """Query active Chaos Mesh CRDs in the chaos-mesh namespace."""
    result = _run_kubectl(
        identity,
        [
            "get",
            "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
            "--namespace", "chaos-mesh", "--output", "json",
        ],
        timeout=15,
    )
    if not result["success"]:
        raise InfrastructureInvalid(f"chaos_resource_query_failed:{result.get('stderr')}")
    try:
        payload = json.loads(result["stdout"] or "{}")
    except json.JSONDecodeError as exc:
        raise InfrastructureInvalid("chaos_resource_payload_invalid") from exc

    resources: list[dict[str, Any]] = []
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", ""))
        metadata = item.get("metadata", {})
        name = str(metadata.get("name", ""))
        namespace = str(metadata.get("namespace", "chaos-mesh"))
        labels = metadata.get("labels", {})
        resources.append({
            "kind": kind,
            "labels": labels,
            "name": name,
            "namespace": namespace,
        })
    return resources


def apply_fault(
    identity: EnvironmentIdentity,
    scenario_id: str,
    *,
    fault_plan: ScenarioFaultPlan | None = None,
) -> dict[str, Any]:
    try:
        plan = fault_plan or build_scenario_fault_plan(scenario_id)
    except ValueError as exc:
        raise InfrastructureInvalid(f"fault_scenario_identity_invalid:{scenario_id}:{exc}") from exc
    result = _run_kubectl(
        identity,
        ["apply", "--filename", plan.manifest_path],
        timeout=60,
    )
    if not result["success"]:
        raise InfrastructureInvalid(f"fault_apply_failed:{scenario_id}")
    return {
        "manifest": plan.manifest_path,
        "phase": "FAULT_APPLIED",
        "resources": [r.to_dict() for r in plan.resources],
        "scenario_id": scenario_id,
        "status": "FAULT_APPLIED",
    }


def fault_established(
    identity: EnvironmentIdentity,
    scenario_id: str,
    *,
    fault_plan: ScenarioFaultPlan | None = None,
    fault_effect_predicate: Any = None,
    require_effect_confirmation: bool = False,
) -> dict[str, Any]:
    """Verify ALL expected scenario resources are present and confirm fault effect.

    Distinguishes INJECTION_RESOURCE_PRESENT from FAULT_EFFECT_CONFIRMED.
    """
    try:
        plan = fault_plan or build_scenario_fault_plan(scenario_id)
    except ValueError as exc:
        raise InfrastructureInvalid(f"fault_scenario_identity_invalid:{scenario_id}:{exc}") from exc
    active_resources = detect_active_chaos_resources(identity)

    missing: list[str] = []
    for expected in plan.resources:
        matched = any(
            active.get("kind", "").lower() == expected.kind.lower()
            and active.get("name") == expected.name
            and active.get("namespace", "").lower() == expected.namespace.lower()
            and active.get("labels", {}).get("scenario") == expected.scenario_label
            for active in active_resources
        )
        if not matched:
            missing.append(f"{expected.kind}/{expected.name}")

    if missing:
        raise InfrastructureInvalid(
            f"fault_not_objectively_established:{scenario_id}:missing={missing}"
        )

    injection_status = "INJECTION_RESOURCE_PRESENT"
    effect_status = "FAULT_EFFECT_CONFIRMED"

    if fault_effect_predicate is not None:
        confirmed, details = fault_effect_predicate(identity, scenario_id)
        if not confirmed:
            raise InfrastructureInvalid(f"fault_effect_not_confirmed:{scenario_id}:{details}")
    elif require_effect_confirmation:
        raise InfrastructureInvalid(
            f"STAGE9_G5_FAULT_EFFECT_CONTRACT_UNBOUND:{scenario_id}; canonical G5 degradation predicate required"
        )
    else:
        effect_status = "INJECTION_RESOURCE_PRESENT_ONLY"

    return {
        "effect_status": effect_status,
        "injection_status": injection_status,
        "matching_resources": len(plan.resources),
        "phase": "FAULT_ESTABLISHED",
        "status": "FAULT_ESTABLISHED",
    }


def resolve_scenario_fault_effect_predicate(
    scenario_id: str,
    *,
    custom_bindings: dict[str, Any] | None = None,
) -> Any:
    """Resolve an objective canonical degradation predicate for a scenario.

    Fails closed if the canonical G5 fault-effect contract is unbound.
    """
    if custom_bindings is not None:
        if scenario_id not in custom_bindings:
            raise InfrastructureInvalid(f"unknown_scenario_fault_effect_binding:{scenario_id}")
        predicate = custom_bindings[scenario_id]
        if not callable(predicate):
            raise InfrastructureInvalid(f"invalid_scenario_fault_effect_predicate:{scenario_id}")
        return predicate

    # Canonical upstream G5 degradation predicate contract is currently unbound
    raise InfrastructureInvalid(
        f"STAGE9_G5_FAULT_EFFECT_CONTRACT_UNBOUND:{scenario_id}; canonical G5 "
        "degradation predicate binding required before real execution"
    )


def validate_fault_effect_predicate_bindings(
    rows: list[dict[str, Any]],
    *,
    custom_bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preflight validate that every scenario in the training set has a valid degradation predicate.

    Fails closed before model loading, trainer construction, or cluster mutation.
    """
    resolved_predicates: dict[str, Any] = {}
    for row in rows:
        scenario_id = (
            row.get("hidden_metadata", {}).get("scenario_id")
            or row.get("scenario_id")
        )
        if not scenario_id:
            raise InfrastructureInvalid("missing_scenario_id_in_training_row")
        if scenario_id not in resolved_predicates:
            resolved_predicates[scenario_id] = resolve_scenario_fault_effect_predicate(
                scenario_id,
                custom_bindings=custom_bindings,
            )
    return resolved_predicates


def cleanup_scenario_faults(
    identity: EnvironmentIdentity,
    fault_plan: ScenarioFaultPlan,
) -> dict[str, Any]:
    """Delete ONLY the exact resources declared in this scenario's fault plan."""
    for resource in fault_plan.resources:
        _run_kubectl(
            identity,
            [
                "delete",
                resource.kind.lower(),
                resource.name,
                "--namespace",
                resource.namespace,
                "--ignore-not-found=true",
            ],
            timeout=30,
        )

    # Verify those exact resources are gone
    remaining_active = detect_active_chaos_resources(identity)
    remaining: list[str] = []
    for resource in fault_plan.resources:
        still_present = any(
            active.get("kind", "").lower() == resource.kind.lower()
            and active.get("name") == resource.name
            and active.get("namespace", "").lower() == resource.namespace.lower()
            for active in remaining_active
        )
        if still_present:
            remaining.append(f"{resource.kind}/{resource.name}")

    if remaining:
        raise InfrastructureInvalid(f"scenario_fault_cleanup_incomplete:{remaining}")

    return {
        "cleaned_resources": [r.to_dict() for r in fault_plan.resources],
        "phase": "RESET",
        "status": "RESET",
    }


def reset_faults(identity: EnvironmentIdentity) -> dict[str, Any]:
    """Emergency/fallback reset that deletes all chaos resources across namespaces."""
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
    return {"phase": "RESET", "status": "FAULTS_RESET"}


def extract_policy_action_record(final: dict[str, Any]) -> dict[str, Any]:
    """Extract canonical policy action record for evidence and attribution.

    Guarantees that pre-cleanup evidence and reward attribution share the exact
    same action identity, tool, arguments, and execution semantics.
    """
    if not isinstance(final, dict):
        return {
            "canonical_action_hash": None,
            "executed_action": None,
            "executed_arguments": None,
            "executed_tool": None,
            "outcome": "unknown",
            "parsed_action": None,
            "policy_action_admitted": False,
            "policy_action_identity_match": False,
            "policy_completion_valid": False,
            "policy_denial_reason": "missing_final_remediation",
            "success": False,
        }

    parsed = final.get("policy_parsed_action")
    executed_action_meta = final.get("policy_executed_action")
    executed_list = final.get("executed_actions")
    executed_item = (
        executed_list[0]
        if isinstance(executed_list, list) and len(executed_list) == 1 and isinstance(executed_list[0], dict)
        else None
    )
    admitted = bool(final.get("policy_action_admitted") is True)
    identity_match = bool(final.get("policy_action_identity_match") is True)
    valid_completion = bool(final.get("policy_completion_valid") is True)

    canonical_hash = None
    if executed_action_meta and isinstance(executed_action_meta, dict):
        canonical_hash = executed_action_meta.get("sha256")
    elif parsed and isinstance(parsed, dict):
        canonical_hash = parsed.get("sha256")

    # If unadmitted, do not fabricate executed action identity
    executed_tool = executed_item.get("tool") if (admitted and executed_item) else None
    executed_args = executed_item.get("args") if (admitted and executed_item) else None
    success = bool(executed_item.get("success") is True) if (admitted and executed_item) else False

    executed_action_record = (
        {
            "arguments": executed_args,
            "sha256": canonical_hash,
            "tool": executed_tool,
        }
        if (admitted and executed_tool is not None)
        else None
    )

    return {
        "canonical_action_hash": canonical_hash if admitted else None,
        "executed_action": executed_action_record,
        "executed_arguments": executed_args,
        "executed_tool": executed_tool,
        "outcome": final.get("outcome", "unknown"),
        "parsed_action": parsed,
        "policy_action_admitted": admitted,
        "policy_action_identity_match": identity_match,
        "policy_completion_valid": valid_completion,
        "policy_denial_reason": final.get("policy_denial_reason"),
        "success": success,
    }


def _policy_owned_action(final: dict[str, Any]) -> dict[str, Any] | None:
    rec = extract_policy_action_record(final)
    if not (
        final.get("mode") == "policy_rollout"
        and rec["policy_completion_valid"]
        and rec["policy_action_identity_match"]
        and rec["policy_action_admitted"]
    ):
        return None
    executed = final.get("executed_actions")
    if not (isinstance(executed, list) and len(executed) == 1 and isinstance(executed[0], dict)):
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
    # Generic mutation success is NOT evidence of remediation progress.
    # Set dense credit and ungrounded "partial" outcome to 0.0.
    dense = 0.0
    partial = 0.0
    resolution = 0.85 if env_resolved and mutating_success else 0.0

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


def validate_reward_pairing(
    prompts: list[Any],
    completions: list[Any],
    metadata: dict[str, list[Any]],
    rows_by_group: dict[str, dict[str, Any]],
    expected_environment: dict[str, str],
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
        if row["hidden_metadata"]["environment_identity"] != expected_environment:
            raise RuntimeError(f"environment_identity_mismatch:group={group}")
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
        fault_effect_predicate: Any = None,
        require_effect_confirmation: bool = True,
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
        self.fault_effect_predicate = fault_effect_predicate
        self.require_effect_confirmation = require_effect_confirmation

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
            self._score_batch(completions, prompts, kwargs)
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
            self.environment.to_dict(),
        )
        verify_kubernetes_environment(self.environment)
        for index, (completion, row) in enumerate(zip(completions, paired_rows)):
            log.info(
                "Rollout %d/%d — group %s",
                index + 1,
                len(completions),
                row["stage9_group_id"],
            )
            try:
                result = await self._score_paired_rollout(row, completion, rollout_index=index)
            except Exception as exc:
                self._persist_invalidation(index, row, completion, exc)
                raise
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
        rollout_index: int = 0,
    ) -> dict[str, Any]:
        """Run strictly ordered healthy/safety/fault/action/verifier/cleanup/post-health lifecycle."""
        from agents.coordinator import handle_incident

        hidden = row["hidden_metadata"]
        scenario_id = hidden["scenario_id"]
        try:
            fault_plan = build_scenario_fault_plan(scenario_id)
        except ValueError as exc:
            raise InfrastructureInvalid(f"scenario_fault_plan_invalid:{scenario_id}:{exc}") from exc
        lifecycle: list[dict[str, Any]] = []

        # 1. Context verify
        verify_kubernetes_environment(self.environment)
        lifecycle.append({"passed": True, "phase": "CONTEXT_VERIFIED"})

        # 2. Pre-rollout healthy check
        pre_health, pre_details = cluster_healthy(self.environment)
        lifecycle.append({"passed": pre_health, "phase": "PRE_ROLLOUT_HEALTHY", **pre_details})
        if not pre_health:
            raise InfrastructureInvalid("pre_rollout_unhealthy")

        # 3. Preexisting-fault/chaos safety check
        preexisting = detect_active_chaos_resources(self.environment)
        if preexisting:
            lifecycle.append({"phase": "PREEXISTING_CHAOS_DETECTED", "resources": preexisting})
            raise InfrastructureInvalid(f"preexisting_chaos_resources_detected:{preexisting}")
        lifecycle.append({"passed": True, "phase": "PREEXISTING_CHAOS_CHECKED"})

        fault_applied_flag = False
        incident = None
        started = time.time()
        try:
            # 4. Apply exact scenario
            lifecycle.append(apply_fault(self.environment, scenario_id, fault_plan=fault_plan))
            fault_applied_flag = True

            # 5. Wait for fault onset settle
            await asyncio.sleep(self.fault_settle_seconds)

            # 6. Objectively establish exact scenario fault with mandatory effect confirmation
            predicate = (
                self.fault_effect_predicate
                if callable(self.fault_effect_predicate)
                else (self.fault_effect_predicate or {}).get(scenario_id)
                if isinstance(self.fault_effect_predicate, dict)
                else None
            )
            established_record = fault_established(
                self.environment,
                scenario_id,
                fault_plan=fault_plan,
                fault_effect_predicate=predicate,
                require_effect_confirmation=self.require_effect_confirmation,
            )
            lifecycle.append(established_record)

            # 7. Policy execution through coordinator
            lifecycle.append({"phase": "POLICY_EXECUTION"})
            try:
                alert = row["observation"]["alert"]
                started = time.time()
                with _approved_tool_context(self.environment):
                    incident = await handle_incident(
                        alert,
                        scenario_id=scenario_id,
                        remediation_policy_completion=completion_text,
                        remediation_observation=row["observation"],
                    )
            except InfrastructureInvalid:
                raise
            except HarnessExecutionInvalid:
                raise
            except Exception as exc:
                raise HarnessExecutionInvalid(f"rollout_harness_error:{exc}") from exc

            # 8. Independent verifier (ran inside handle_incident before cleanup)
            lifecycle.append({
                "phase": "VERIFIER_COMPLETED",
                "verification": incident.get("verification", {}),
            })

            # Durably persist pre-cleanup scientific verdict before environment mutation
            self._persist_pre_cleanup_verdict(
                rollout_index=rollout_index,
                row=row,
                completion=completion_text,
                incident=incident,
                lifecycle=lifecycle,
            )
        finally:
            if fault_applied_flag:
                await self._cleanup_and_verify(lifecycle, fault_plan)

        remediation = incident.get("remediation", {}).get("final", {}) if incident else {}
        env_resolved = bool(incident.get("env_resolved") is True) if incident else False
        agent_claimed_resolved = bool(incident.get("agent_claimed_resolved")) if incident else False
        return {
            "episode": {
                "agent_claimed_resolved": agent_claimed_resolved,
                "comms": incident.get("comms", {}) if incident else {},
                "diagnosis": incident.get("diagnosis", {}) if incident else {},
                "env_resolved": env_resolved,
                "outcome": remediation.get("outcome", "unknown"),
                "remediation": incident.get("remediation", {}) if incident else {},
                "resolved": env_resolved,
                "time_to_resolve_s": round(time.time() - started),
                "tier": hidden["tier"],
                "total_turns": sum(
                    len(incident.get(role, {}).get("trajectory", []))
                    for role in ("triage", "diagnosis", "remediation", "comms")
                ) if incident else 0,
                "triage": incident.get("triage", {}) if incident else {},
                "verification": incident.get("verification", {}) if incident else {},
            },
            "lifecycle": lifecycle,
            "model_visible_alert": row["observation"]["alert"],
        }

    async def _cleanup_and_verify(
        self,
        lifecycle: list[dict[str, Any]],
        fault_plan: ScenarioFaultPlan | None = None,
    ) -> None:
        """Scoped cleanup of only the current rollout's faults followed by health verification."""
        cleanup_error: InfrastructureInvalid | None = None
        try:
            if fault_plan is not None:
                lifecycle.append(cleanup_scenario_faults(self.environment, fault_plan))
            else:
                lifecycle.append(reset_faults(self.environment))
        except InfrastructureInvalid as exc:
            cleanup_error = exc
        await self._reset_settle()
        post_health, post_details = cluster_healthy(self.environment)
        lifecycle.append({
            "passed": post_health,
            "phase": "POST_RESET_HEALTHY",
            **post_details,
        })
        if cleanup_error is not None:
            raise cleanup_error
        if not post_health:
            raise InfrastructureInvalid("post_reset_unhealthy")

    async def _reset_and_verify(self, lifecycle: list[dict[str, Any]]) -> None:
        """Backwards-compatible wrapper for fallback resets."""
        await self._cleanup_and_verify(lifecycle, None)

    async def _reset_settle(self) -> None:
        await asyncio.sleep(self.reset_settle_seconds)

    def _persist_pre_cleanup_verdict(
        self,
        rollout_index: int,
        row: dict[str, Any],
        completion: str,
        incident: dict[str, Any] | None,
        lifecycle: list[dict[str, Any]],
    ) -> None:
        """Atomically persist pre-cleanup scientific verdict before environment mutation."""
        if not self.episodes_path:
            return
        self.episodes_path.parent.mkdir(parents=True, exist_ok=True)
        remediation = incident.get("remediation", {}).get("final", {}) if incident else {}
        env_resolved = bool(incident.get("env_resolved") is True) if incident else False
        agent_claimed_resolved = bool(incident.get("agent_claimed_resolved")) if incident else False
        policy_action_record = extract_policy_action_record(remediation)
        record = {
            "agent_claimed_resolved": agent_claimed_resolved,
            "canonical_action_hash": policy_action_record["canonical_action_hash"],
            "cleanup_status": "pending",
            "env_resolved": env_resolved,
            "event_id": uuid.uuid4().hex,
            "evidence_phase": "SCIENTIFIC_VERDICT_CAPTURED",
            "hidden_metadata": row["hidden_metadata"],
            "lifecycle": [dict(entry) for entry in lifecycle],
            "policy_action": (
                policy_action_record["executed_action"]
                or policy_action_record["parsed_action"]
                or {}
            ),
            "policy_action_admitted": policy_action_record["policy_action_admitted"],
            "policy_action_identity_match": policy_action_record["policy_action_identity_match"],
            "policy_action_outcome": policy_action_record["outcome"],
            "policy_action_record": policy_action_record,
            "policy_completion": completion,
            "policy_completion_sha256": hashlib.sha256(
                completion.encode("utf-8")
            ).hexdigest(),
            "policy_completion_valid": policy_action_record["policy_completion_valid"],
            "policy_executed_action": policy_action_record["executed_action"],
            "policy_parsed_action": policy_action_record["parsed_action"],
            "prompt_sha256": row["prompt_sha256"],
            "rollout_index": rollout_index,
            "row_id": row["row_id"],
            "stage9_group_id": row["stage9_group_id"],
            "status": "SCIENTIFIC_VERDICT_CAPTURED",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "verifier_result": incident.get("verification", {}) if incident else {},
        }
        with self.episodes_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

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
            "cleanup_status": "completed",
            "curriculum_state": {
                "sha256": _curriculum.export_state()["state_sha256"],
                **_curriculum.stats(),
            },
            "episode": result["episode"],
            "event_id": uuid.uuid4().hex,
            "evidence_phase": "EPISODE_FINALIZED",
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
            "status": "COMPLETED",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        with self.episodes_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def validate_existing_episode_log(self) -> int:
        """Fail closed on corrupt, ambiguous, dangling, or illegal state transitions before resume."""
        if not self.episodes_path or not self.episodes_path.is_file():
            return 0
        seen_events: set[str] = set()
        open_verdicts: dict[tuple[str, str, int, str], dict[str, Any]] = {}
        finalized_rollouts: set[tuple[str, str, int, str]] = set()
        count = 0
        with self.episodes_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"episode_evidence_invalid_json:{line_number}") from exc
                if not isinstance(record, dict):
                    raise RuntimeError(f"episode_evidence_not_object:{line_number}")
                missing = sorted(
                    key for key in (
                        "event_id", "hidden_metadata", "policy_completion_sha256",
                        "prompt_sha256", "row_id", "stage9_group_id",
                        "status",
                    )
                    if key not in record
                )
                if missing:
                    raise RuntimeError(
                        f"episode_evidence_fields_missing:{line_number}:{','.join(missing)}"
                    )
                status = record["status"]
                if status not in EPISODE_STATUSES:
                    raise RuntimeError(f"episode_evidence_status_invalid:{line_number}")
                event_id = str(record["event_id"])
                if not event_id or event_id in seen_events:
                    raise RuntimeError(f"episode_evidence_event_id_invalid:{line_number}")
                seen_events.add(event_id)

                rollout_key = (
                    str(record["stage9_group_id"]),
                    str(record["row_id"]),
                    int(record.get("rollout_index", 0)),
                    str(record["policy_completion_sha256"]),
                )

                if status == "SCIENTIFIC_VERDICT_CAPTURED":
                    if record.get("cleanup_status") != "pending":
                        raise RuntimeError(f"captured_verdict_cleanup_status_invalid:{line_number}")
                    if rollout_key in open_verdicts:
                        raise RuntimeError(f"duplicate_scientific_verdict_captured:{line_number}:{rollout_key}")
                    if rollout_key in finalized_rollouts:
                        raise RuntimeError(f"verdict_captured_after_rollout_finalized:{line_number}:{rollout_key}")
                    open_verdicts[rollout_key] = record

                elif status == "COMPLETED":
                    if not isinstance(record.get("reward"), dict):
                        raise RuntimeError(f"completed_episode_reward_missing:{line_number}")
                    if record.get("cleanup_status") != "completed":
                        raise RuntimeError(f"completed_episode_cleanup_status_invalid:{line_number}")
                    if rollout_key in finalized_rollouts:
                        raise RuntimeError(f"duplicate_completed_rollout:{line_number}:{rollout_key}")
                    if rollout_key not in open_verdicts:
                        raise RuntimeError(f"terminal_completed_record_missing_captured_verdict:{line_number}:{rollout_key}")
                    captured = open_verdicts.pop(rollout_key)
                    # Enforce consistency between pre-cleanup verdict and finalized completed record
                    if (
                        captured["row_id"] != record["row_id"]
                        or captured["stage9_group_id"] != record["stage9_group_id"]
                        or captured["prompt_sha256"] != record["prompt_sha256"]
                        or captured["policy_completion_sha256"] != record["policy_completion_sha256"]
                        or captured.get("env_resolved") != record.get("episode", {}).get("env_resolved")
                    ):
                        raise RuntimeError(f"contradictory_verdict_terminal_mismatch:{line_number}:{rollout_key}")
                    finalized_rollouts.add(rollout_key)

                elif status in ("INFRASTRUCTURE_INVALID", "HARNESS_EXECUTION_INVALID", "POLICY_EXECUTION_INVALID", "ROLLOUT_EXCEPTION"):
                    if record.get("reward") is not None:
                        raise RuntimeError(f"invalidated_episode_has_reward:{line_number}")
                    if record.get("evidence_phase") == "CLEANUP_INVALID" or record.get("cleanup_status") == "failed":
                        if rollout_key in finalized_rollouts:
                            raise RuntimeError(f"duplicate_terminal_invalidation:{line_number}:{rollout_key}")
                        if rollout_key not in open_verdicts:
                            raise RuntimeError(f"cleanup_invalidation_missing_captured_verdict:{line_number}:{rollout_key}")
                        captured = open_verdicts.pop(rollout_key)
                        if (
                            captured["row_id"] != record["row_id"]
                            or captured["stage9_group_id"] != record["stage9_group_id"]
                            or captured["prompt_sha256"] != record["prompt_sha256"]
                            or captured["policy_completion_sha256"] != record["policy_completion_sha256"]
                        ):
                            raise RuntimeError(f"contradictory_verdict_invalidation_mismatch:{line_number}:{rollout_key}")
                        finalized_rollouts.add(rollout_key)
                    else:
                        # Pre-rollout / pre-verifier invalidation
                        if rollout_key in open_verdicts:
                            raise RuntimeError(f"early_invalidation_with_dangling_open_verdict:{line_number}:{rollout_key}")

                count += 1

        if open_verdicts:
            dangling = sorted(f"group={k[0]},row={k[1]},idx={k[2]},sha={k[3][:8]}" for k in open_verdicts)
            raise RuntimeError(f"dangling_verdict_detected:unfinalized_rollout_verdicts={dangling}; cleanup_status=pending; resume rejected")

        return count

    def _persist_invalidation(
        self,
        rollout_index: int,
        row: dict[str, Any],
        completion: str,
        exc: Exception,
    ) -> None:
        """Persist why no policy reward exists before aborting the batch."""
        if not self.episodes_path:
            return
        status = (
            "INFRASTRUCTURE_INVALID"
            if isinstance(exc, InfrastructureInvalid)
            else "HARNESS_EXECUTION_INVALID"
            if isinstance(exc, HarnessExecutionInvalid)
            else "POLICY_EXECUTION_INVALID"
            if isinstance(exc, PolicyExecutionInvalid)
            else "ROLLOUT_EXCEPTION"
        )
        is_cleanup_err = "cleanup" in str(exc) or "post_reset" in str(exc)
        evidence_phase = "CLEANUP_INVALID" if is_cleanup_err else "ROLLOUT_INVALIDATED"
        cleanup_status = "failed" if is_cleanup_err else "not_reached"

        self.episodes_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "cleanup_status": cleanup_status,
            "completion_sha256": hashlib.sha256(completion.encode("utf-8")).hexdigest(),
            "event_id": uuid.uuid4().hex,
            "evidence_phase": evidence_phase,
            "hidden_metadata": row["hidden_metadata"],
            "invalidation_reason": str(exc),
            "policy_completion": completion,
            "policy_completion_sha256": hashlib.sha256(completion.encode("utf-8")).hexdigest(),
            "prompt_sha256": row["prompt_sha256"],
            "reward": None,
            "rollout_index": rollout_index,
            "row_id": row["row_id"],
            "stage9_group_id": row["stage9_group_id"],
            "status": status,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
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
    if (
        not isinstance(evaluation, dict)
        or evaluation.get("passed") is not True
        or not str(evaluation.get("run_id", "")).strip()
        or not _GIT_COMMIT_RE.fullmatch(str(evaluation.get("code_commit", "")))
        or not isinstance(evaluation.get("metrics"), dict)
    ):
        raise ValueError("g8_evaluation_not_passed")
    corpus = manifest.get("sft_corpus")
    if not isinstance(corpus, dict) or not _SHA256_RE.fullmatch(str(corpus.get("sha256", ""))):
        raise ValueError("sft_corpus_hash_missing")
    base_model = manifest.get("base_model")
    if (
        not isinstance(base_model, dict)
        or not str(base_model.get("name", "")).strip()
        or not _GIT_COMMIT_RE.fullmatch(str(base_model.get("revision", "")))
        or not str(base_model.get("architecture", "")).strip()
    ):
        raise ValueError("sft_base_identity_missing")
    tokenizer_identity = manifest.get("tokenizer")
    if (
        not isinstance(tokenizer_identity, dict)
        or not str(tokenizer_identity.get("name", "")).strip()
        or not _GIT_COMMIT_RE.fullmatch(str(tokenizer_identity.get("revision", "")))
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

    required_files = ["config.json", "generation_config.json", "tokenizer_config.json"]
    if (path / "tokenizer.json").is_file():
        required_files.append("tokenizer.json")
    else:
        if not ((path / "vocab.json").is_file() and (path / "merges.txt").is_file()):
            raise ValueError("incomplete_tokenizer_artifacts")
        required_files.extend(("vocab.json", "merges.txt"))
    missing = [name for name in required_files if not (path / name).is_file()]
    has_weights = any((path / name).is_file() for name in (
        "model.safetensors", "pytorch_model.bin", "model.safetensors.index.json",
    ))
    if missing or not has_weights:
        raise ValueError(f"incomplete_merged_sft_checkpoint:missing={missing}")

    file_hashes = manifest.get("file_hashes")
    if not isinstance(file_hashes, dict):
        raise ValueError("sft_file_hashes_missing")
    weight_index_name = None
    shard_names: list[str] = []
    hash_checked_files = list(required_files)
    weight_index_name = next(
        (name for name in ("model.safetensors.index.json", "pytorch_model.bin.index.json") if (path / name).is_file()),
        None,
    )
    if weight_index_name:
        hash_checked_files.append(weight_index_name)
        try:
            index = json.loads((path / weight_index_name).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"sft_weight_index_invalid_json:{weight_index_name}") from exc
        weight_map = index.get("weight_map")
        if not isinstance(weight_map, dict) or not weight_map:
            raise ValueError(f"sft_weight_map_missing:{weight_index_name}")
        shard_names = sorted(set(weight_map.values()))
        if any(not isinstance(name, str) or not name for name in shard_names):
            raise ValueError(f"sft_weight_map_invalid:{weight_index_name}")
        missing_shards = [name for name in shard_names if not (path / name).is_file()]
        if missing_shards:
            raise ValueError(f"sft_weight_shards_missing:{','.join(missing_shards)}")
        hash_checked_files.extend(shard_names)

    if not weight_index_name:
        single_weight = next(
            (name for name in ("model.safetensors", "pytorch_model.bin") if (path / name).is_file()),
            None,
        )
        if single_weight:
            hash_checked_files.append(single_weight)

    for relative_name in hash_checked_files:
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
    # Copy only the declared scientific provenance into run manifests. A G8
    # sidecar must never become a path for unrelated credentials to persist.
    return {
        "_raw_manifest_sha256": _sha256_file(manifest_path),
        "base_model": base_model,
        "checkpoint_kind": manifest["checkpoint_kind"],
        "file_hashes": file_hashes,
        "g8_evaluation": evaluation,
        "lora": lora_provenance,
        "schema_version": manifest["schema_version"],
        "sft_corpus": corpus,
        "stage": manifest["stage"],
        "tokenizer": tokenizer_identity,
    }


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
    "environment_observed",
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
    observed_environment = verify_kubernetes_environment(environment)
    training_rows_path = Path(args.training_rows)
    rows = load_remediation_training_rows(
        training_rows_path,
        allowed_eligibility=("training",),
    )
    raw_dataset_hash = _sha256_file(training_rows_path)
    dataset_contract = remediation_dataset_catalogue(rows)
    # Preflight binding gate: fail closed before model loading, trainer construction, or cluster mutation
    validate_fault_effect_predicate_bindings(rows)
    hyperparameters = {
        "beta": args.beta,
        "batch_size": args.batch_size,
        "gradient_accumulation_steps": args.grad_accum,
        "learning_rate": args.lr,
        "max_completion_length": args.max_compl_len,
        "max_steps": args.max_steps,
        "num_generations": args.num_generations,
        "shuffle_dataset": False,
    }
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    curriculum_seed = args.curriculum_seed
    curriculum_state_path = output_dir / "curriculum_state.json"
    episodes_path = output_dir / "grpo_episodes.jsonl"
    resume_checkpoint = args.resume_from_checkpoint
    reward_fn = OnlineRewardFunction(
        rows,
        environment,
        curriculum_seed,
        raw_dataset_hash,
        episodes_path=episodes_path,
        curriculum_state_path=curriculum_state_path,
    )
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
        reward_fn.validate_existing_episode_log()
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
    provenance["environment_observed"] = observed_environment
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
    from datasets import Dataset
    rows = sorted(rows, key=lambda row: (row["stage9_group_id"], row["row_id"]))
    dataset = Dataset.from_list([
        build_trl_training_record(row)
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
        shuffle_dataset=False,
    )
    trainer = GRPOTrainer(
        model=model,
        args=grpo_args,
        train_dataset=dataset,
        processing_class=tokenizer,
        reward_funcs=[reward_fn],
    )
    log.info("Starting local free-first GRPO; each completion gets an independently reset paired environment.")
    trainer.train(resume_from_checkpoint=resume_checkpoint or None)
    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    logs = trainer.state.log_history
    rewards = [entry.get("rewards/mean") for entry in logs if "rewards/mean" in entry]
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
