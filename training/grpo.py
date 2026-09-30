"""GRPO training for AtlasOps against one controlled Kubernetes environment.

Architecture:
  - Each GRPO step generates a group of policy completions against a live
    Chaos Mesh scenario.
  - Direct-action reward comes from conclusive objective verifier checks.
  - The exact completion action is executed and scored from verifier truth.
  - QLoRA uses a 4-bit base plus LoRA r=16.

Training flow:
  1. Sample a chaos scenario from the tier-weighted curriculum
  2. Apply Chaos Mesh to real GKE cluster
   3. Run serialized policy rollouts (model generates one action each)
  4. Score each rollout from objective verifier evidence.
  5. GRPO updates — policy learns from what actually worked on the real cluster
  6. Reset cluster, next step
"""

import argparse
import asyncio
import json
import logging
import os
import random
import subprocess
import sys
import time
import uuid
from contextvars import ContextVar
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.approval import ApprovalGate
from agents.approval_http import (
    OPERATOR_APPROVAL_TIMEOUT_SECONDS,
    loopback_approval_server,
)
from config.runtime import (
    SCENARIOS_BY_TIER,
    CurriculumManager,
    evaluate_reward_contract,
)
from config.scenario_catalog import SCENARIO_CATALOG
from config.splits import TEST_SPLIT, VAL_SPLIT, get_split
from training.grpo_environment import (
    DirectPolicyEnvironment,
    parse_policy_action,
    policy_action_requires_operator_approval,
    require_live_kube_context,
    strip_untrusted_approval_context,
)
from training.grpo_provenance import (
    MANIFEST_NAME,
    claim_new_output_directory,
    create_run_manifest,
    has_verified_final_rollout,
    mark_training_started,
    persist_status,
    record_loader_provenance,
    require_stable_failure_persistence,
    validate_grpo_model_references,
    validate_grpo_run_plan,
    validate_new_output_directory,
    validate_sft_parent,
    validate_sft_parent_matches_run,
    validate_tokenizer_loader_revision,
)
from training.sft_provenance import validate_resolved_hf_commit
from training.grpo_reward import score_direct_action_step

PeftModel = None
prepare_model_for_kbit_training = None
AutoModelForCausalLM = None
AutoTokenizer = None
BitsAndBytesConfig = None
GRPOConfig = None
GRPOTrainer = None
_HAS_TORCH_RL = False

log = logging.getLogger(__name__)
_RUN_ATTEMPT_STATE: ContextVar[dict[str, bool] | None] = ContextVar(
    "g9_run_attempt_state",
    default=None,
)


def _require_live_execution(
    execute_live_chaos: bool,
    kube_context: str | None,
) -> str:
    return require_live_kube_context(
        execute_live_chaos,
        kube_context,
        opt_in_flag="--execute-live-chaos",
    )


def _operator_approval_profile(enabled: bool) -> dict[str, Any]:
    return {
        "mode": "loopback_exact_action_v1" if enabled else "disabled",
        "timeout_seconds": OPERATOR_APPROVAL_TIMEOUT_SECONDS if enabled else None,
        "identity": "operator_supplied_name_not_independent_attestation",
    }


def _require_single_writer() -> None:
    """Do not let distributed trainers run concurrent faults on one cluster."""
    for name, required in (
        ("WORLD_SIZE", 1),
        ("LOCAL_WORLD_SIZE", 1),
        ("RANK", 0),
        ("LOCAL_RANK", 0),
    ):
        raw = os.getenv(name)
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError as exc:
            raise RuntimeError(f"G9 requires a single training process ({name} invalid)") from exc
        if value != required:
            raise RuntimeError(f"G9 requires a single training process ({name}={value})")
    torch = sys.modules.get("torch")
    distributed = getattr(torch, "distributed", None)
    if (
        distributed is not None
        and distributed.is_available()
        and distributed.is_initialized()
        and distributed.get_world_size() != 1
    ):
        raise RuntimeError("G9 requires a single training process")


def validate_grpo_batch_configuration(
    *,
    per_device_train_batch_size: int,
    gradient_accumulation_steps: int,
    num_generations: int,
) -> int:
    """Validate the pinned TRL GRPO generation-group divisibility without changing budgets."""
    values = {
        "per_device_train_batch_size": per_device_train_batch_size,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "num_generations": num_generations,
    }
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    effective_prompt_batch_size = (
        per_device_train_batch_size * gradient_accumulation_steps
    )
    if effective_prompt_batch_size % num_generations:
        raise ValueError(
            "Pinned TRL 0.19.1 requires the effective prompt batch "
            f"({per_device_train_batch_size} * {gradient_accumulation_steps} * 1 process = "
            f"{effective_prompt_batch_size}) to be divisible by num_generations={num_generations}. "
            "No batch or generation values were changed."
        )
    return effective_prompt_batch_size


def compute_grpo_advantages(rewards: list[float], eps: float = 1e-4) -> list[float]:
    """Compute group-relative advantages across G rollouts: A_i = (r_i - mean) / (std + eps)."""
    if not rewards:
        return []
    n = len(rewards)
    if n == 1:
        return [0.0]
    mean = sum(rewards) / n
    variance = sum((r - mean) ** 2 for r in rewards) / n
    std = variance ** 0.5
    return [round((r - mean) / (std + eps), 4) for r in rewards]


# ── Optional training dependencies ────────────────────────────────────────────

BNBCONFIG = None


def _load_training_dependencies() -> None:
    """Load the optional model stack only after pins and parent provenance pass."""
    global PeftModel, prepare_model_for_kbit_training
    global AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    global GRPOConfig, GRPOTrainer, _HAS_TORCH_RL, BNBCONFIG
    if _HAS_TORCH_RL:
        return

    from peft import PeftModel as peft_model
    from peft import prepare_model_for_kbit_training as prepare_kbit_model
    from transformers import AutoModelForCausalLM as auto_model
    from transformers import AutoTokenizer as auto_tokenizer
    from transformers import BitsAndBytesConfig as bnb_config
    from trl import GRPOConfig as grpo_config
    from trl import GRPOTrainer as grpo_trainer

    PeftModel = peft_model
    prepare_model_for_kbit_training = prepare_kbit_model
    AutoModelForCausalLM = auto_model
    AutoTokenizer = auto_tokenizer
    BitsAndBytesConfig = bnb_config
    GRPOConfig = grpo_config
    GRPOTrainer = grpo_trainer
    BNBCONFIG = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype="bfloat16",
        bnb_4bit_use_double_quant=True,
    )
    _HAS_TORCH_RL = True


def _ensure_model_loader_dependencies() -> None:
    if any(
        dependency is None
        for dependency in (
            AutoTokenizer,
            AutoModelForCausalLM,
            prepare_model_for_kbit_training,
            PeftModel,
        )
    ):
        _load_training_dependencies()


def _persist_started_run_failure(
    output_dir: Path,
    status: str,
    error: BaseException,
) -> None:
    manifest_path = Path(output_dir) / MANIFEST_NAME
    try:
        if not manifest_path.is_file():
            return
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        training = manifest.get("training") if isinstance(manifest, dict) else None
        if (
            not isinstance(manifest, dict)
            or manifest.get("status") != "running"
            or not isinstance(training, dict)
            or not training.get("execution_started_at")
        ):
            return
        persist_status(
            manifest_path,
            manifest,
            status,
            error_type=type(error).__name__,
        )
    except Exception:
        log.exception("Could not persist G9 %s state after %s", status, type(error).__name__)


def _mark_run_attempt_started() -> None:
    attempt = _RUN_ATTEMPT_STATE.get()
    if attempt is not None:
        attempt["started"] = True


def _terminalize_started_run_on_exception(output_dir_position: int):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            output_dir = kwargs.get("output_dir")
            if output_dir is None and len(args) > output_dir_position:
                output_dir = args[output_dir_position]
            attempt = {"started": False}
            token = _RUN_ATTEMPT_STATE.set(attempt)
            try:
                return function(*args, **kwargs)
            except KeyboardInterrupt as exc:
                if attempt["started"] and output_dir is not None:
                    _persist_started_run_failure(
                        Path(output_dir),
                        "interrupted",
                        exc,
                    )
                raise
            except Exception as exc:
                if attempt["started"] and output_dir is not None:
                    _persist_started_run_failure(
                        Path(output_dir),
                        "failed",
                        exc,
                    )
                raise
            finally:
                _RUN_ATTEMPT_STATE.reset(token)

        return wrapped

    return decorate


# ── Reward contract ───────────────────────────────────────────────────────────

# Training-run curriculum singleton (tracks mastery + spaced repetition)
_curriculum = CurriculumManager()


def compute_reward(episode: dict) -> float:
    """Legacy full-agent 70/30 blend; not the direct-action training scorer.

    Dense step rewards sum tool-call-level progress signals from StepRewardTracker.
    Normalised over 10 (typical episode has 15-30 tool calls, each capped at 0.99).
    """
    contract = float(evaluate_reward_contract(episode)["total"])
    # Sum dense rewards across all four agent roles
    step_total = sum(
        role_data.get("step_reward_summary", {}).get("dense_reward_total", 0.0)
        for role in ("triage", "diagnosis", "remediation", "comms")
        for role_data in [episode.get(role, {})]
    )
    step_norm = max(0.0, min(1.0, step_total / 10.0))
    return round(0.7 * contract + 0.3 * step_norm, 4)


def compute_direct_action_reward(result: dict[str, Any]) -> float:
    """Score one verified direct action; never infer a judge or role trajectory."""
    settling = result.get("settling")
    if (
        result.get("status") != "ok"
        or result.get("scorable") is not True
        or not isinstance(settling, Mapping)
        or settling.get("status") != "settled"
        or settling.get("stable") is not True
        or type(settling.get("stable_observations")) is not int
        or settling["stable_observations"] < 2
    ):
        raise ValueError("Direct-action rollout is unscorable")
    verification = result.get("verification")
    if not isinstance(verification, Mapping):
        raise ValueError("Direct-action rollout lacks objective verification")
    decomposition = score_direct_action_step(
        verification,
        agent_claimed_resolved=result.get("agent_claimed_resolved") is True,
    )
    result["direct_reward_decomposition"] = decomposition
    return float(decomposition["total"])


def sample_scenario(tiers: list[str]) -> tuple[str, str]:
    """Sample a scenario strictly from TRAIN_SPLIT using CurriculumManager priority scoring."""
    train_ids = set(get_split("train"))
    val_set = set(VAL_SPLIT)
    test_set = set(TEST_SPLIT)

    pool = [
        (sid, sid.split("/")[0])
        for tier in tiers
        for sid in SCENARIOS_BY_TIER.get(tier, [])
        if sid in train_ids
    ]
    if not pool:
        pool = [(sid, sid.split("/")[0]) for sid in train_ids]

    chosen_sid, chosen_tier = _curriculum.next_scenario(pool)
    if chosen_sid in val_set or chosen_sid in test_set:
        raise ValueError(f"CRITICAL LEAKAGE: GRPO sampled scenario {chosen_sid} from Val/Test split!")
    return chosen_sid, chosen_tier


def _chaos_manifest(scenario_id: str) -> Path:
    return Path("bench/chaos_manifests") / f"{scenario_id}.yaml"


def zero_chaos_verified(
    *,
    execute_live_chaos: bool = False,
    kube_context: str | None = None,
) -> bool:
    context = _require_live_execution(execute_live_chaos, kube_context)
    env = os.environ.copy()
    env["USE_GKE_GCLOUD_AUTH_PLUGIN"] = "True"
    result = subprocess.run(
        [
            "kubectl", "--context", context, "get",
            "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
            "-A", "-o", "json",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("items") == []


def apply_chaos(
    scenario_id: str,
    *,
    execute_live_chaos: bool = False,
    kube_context: str | None = None,
) -> bool:
    context = _require_live_execution(execute_live_chaos, kube_context)
    manifest = _chaos_manifest(scenario_id)
    if not manifest.exists():
        return False
    env = os.environ.copy()
    env["USE_GKE_GCLOUD_AUTH_PLUGIN"] = "True"
    r = subprocess.run(
        ["kubectl", "--context", context, "apply", "-f", str(manifest)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return r.returncode == 0


def reset_chaos(
    scenario_id: str,
    *,
    execute_live_chaos: bool = False,
    kube_context: str | None = None,
) -> bool:
    context = _require_live_execution(execute_live_chaos, kube_context)
    manifest = _chaos_manifest(scenario_id)
    if not manifest.is_file():
        return False
    env = os.environ.copy()
    env["USE_GKE_GCLOUD_AUTH_PLUGIN"] = "True"
    deleted = subprocess.run(
        [
            "kubectl", "--context", context, "delete", "-f", str(manifest),
            "--ignore-not-found=true",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return deleted.returncode == 0 and zero_chaos_verified(
        execute_live_chaos=execute_live_chaos,
        kube_context=context,
    )


# ── Online reward function for TRL GRPOTrainer ────────────────────────────────

class OnlineRewardFunction:
    """Wraps a controlled Kubernetes environment for TRL reward callbacks.

    For each batch of completions TRL generates, this class:
    1. Validates the exact single policy action and live execution context.
    2. Runs serialized fault/action/cleanup cycles in the selected environment.
    3. Settles and scores conclusive objective verifier observations.
    4. Returns rewards for GRPO advantage computation
    """

    @property
    def __name__(self) -> str:
        return "online_reward"

    def __init__(
        self,
        tiers: list[str],
        coordinator_url: str = "http://localhost:9099",
        rollout_log_path: Path | None = None,
        *,
        execute_live_chaos: bool = False,
        kube_context: str | None = None,
        rollout_phase: str = "final_training",
        trial_number: int | None = None,
        effective_hyperparameters: Mapping[str, Any] | None = None,
        operator_approval_enabled: bool = False,
    ):
        self.kube_context = _require_live_execution(
            execute_live_chaos, kube_context
        )
        self.execute_live_chaos = execute_live_chaos
        self.tiers = tiers
        self.coordinator_url = coordinator_url
        self.rollout_log_path = rollout_log_path
        if rollout_phase not in {"final_training", "optuna_trial"}:
            raise ValueError("GRPO rollout phase must be final_training or optuna_trial")
        if rollout_phase == "optuna_trial" and (
            not isinstance(trial_number, int)
            or isinstance(trial_number, bool)
            or trial_number < 0
        ):
            raise ValueError("Optuna rollout provenance requires a non-negative trial number")
        if rollout_phase == "final_training" and trial_number is not None:
            raise ValueError("Final training rollouts cannot carry an Optuna trial number")
        if rollout_phase == "optuna_trial" and not effective_hyperparameters:
            raise ValueError("Optuna rollout provenance requires effective hyperparameters")
        self.rollout_phase = rollout_phase
        self.trial_number = trial_number
        self.effective_hyperparameters = dict(effective_hyperparameters or {})
        self.operator_approval_enabled = operator_approval_enabled
        self._loop = asyncio.new_event_loop()

    def __del__(self):
        loop = getattr(self, "_loop", None)
        if loop is not None and not loop.is_closed():
            loop.close()

    def __call__(self, completions: list[str], prompts: list[str],
                 scenario_id: list[str] | None = None, **kwargs) -> list[float]:
        """Called by TRL after generating G completions. Returns reward per completion."""
        _require_live_execution(self.execute_live_chaos, self.kube_context)
        return self._loop.run_until_complete(
            self._score_batch(completions, prompts, scenario_id)
        )

    async def _score_batch(self, completions: list[str],
                           prompts: list[str], scenario_ids: list[str] | None) -> list[float]:
        _require_single_writer()
        if not self.operator_approval_enabled:
            return await self._score_batch_with_gate(
                completions, prompts, scenario_ids, approval_gate=None
            )
        operator_api_key = os.getenv("ATLASOPS_API_KEY", "").strip()
        if not operator_api_key:
            raise RuntimeError("G9 operator API key is required before live preflight")
        gate = ApprovalGate(timeout_seconds=OPERATOR_APPROVAL_TIMEOUT_SECONDS)
        async with loopback_approval_server(gate, operator_api_key) as base_url:
            log.warning("G9 host operator approval endpoint: %s/approval/pending", base_url)
            return await self._score_batch_with_gate(
                completions, prompts, scenario_ids, approval_gate=gate
            )

    async def _score_batch_with_gate(
        self,
        completions: list[str],
        prompts: list[str],
        scenario_ids: list[str] | None,
        *,
        approval_gate: ApprovalGate | None,
    ) -> list[float]:
        """Score completions by running serialized rollouts on the live cluster.

        Why serialized (not asyncio.gather):
        All G rollouts share one GKE cluster. Running them in parallel causes
        interference — rollout 1 may delete the chaos while rollout 3 is still
        diagnosing, making rewards correlated and gradients incorrect.
        Serializing gives each rollout a clean, independent cluster state:
          apply_chaos → wait → rollout → reset_chaos → wait → next rollout
        This is slower (G × episode_time) but produces correct independent rewards.
        """
        kube_context = _require_live_execution(
            self.execute_live_chaos, self.kube_context
        )
        if scenario_ids is None or not (
            len(completions) == len(prompts) == len(scenario_ids)
        ):
            raise ValueError("GRPO rewards require one scenario_id and prompt per completion")
        train_ids = set(get_split("train"))
        for scenario_id, prompt in zip(scenario_ids, prompts, strict=True):
            if scenario_id not in train_ids:
                raise ValueError(f"GRPO reward scenario is outside frozen Train: {scenario_id}")
            if SCENARIO_CATALOG[scenario_id].tier not in self.tiers:
                raise ValueError(f"GRPO reward scenario is outside configured tiers: {scenario_id}")
            if prompt != _direct_action_prompt(scenario_id):
                raise ValueError(f"GRPO reward prompt/scenario mismatch: {scenario_id}")

        rewards: list[float] = []
        if not completions:
            return rewards

        for i, completion in enumerate(completions):
            scenario_id = scenario_ids[i]
            tier = SCENARIO_CATALOG[scenario_id].tier
            log.info("Rollout %d/%d — scenario %s", i + 1, len(completions), scenario_id)

            lifecycle_observations = {
                "interpretation": (
                    "Host-observed API/query outcomes only; not independent fault "
                    "authorization, observed fault, delivered alert time, or objective recovery."
                ),
                "zero_chaos_preflight": {
                    "call_status": "not_called",
                    "return_value": None,
                    "exception_type": None,
                    "host_observed_at_utc": None,
                },
                "apply_chaos": {
                    "call_status": "not_called",
                    "return_value": None,
                    "exception_type": None,
                    "host_observed_at_utc": None,
                },
                "wait_for_alert": {
                    "call_status": "not_called",
                    "result": None,
                    "exception_type": None,
                    "host_observed_at_utc": None,
                },
                "reset_chaos": {
                    "call_status": "not_called",
                    "return_value": None,
                    "exception_type": None,
                    "host_observed_at_utc": None,
                },
            }
            try:
                preflight_verified = zero_chaos_verified(
                    execute_live_chaos=self.execute_live_chaos,
                    kube_context=kube_context,
                )
            except Exception as exc:
                lifecycle_observations["zero_chaos_preflight"].update(
                    call_status="raised",
                    exception_type=type(exc).__name__,
                    host_observed_at_utc=datetime.now(UTC).isoformat(),
                )
                self._persist_rollout({
                    "scenario_id": scenario_id,
                    "tier": tier,
                    "status": "failed",
                    "failure": f"zero_chaos_preflight_exception:{type(exc).__name__}",
                    "reward": None,
                    "lifecycle_observations": lifecycle_observations,
                })
                raise
            lifecycle_observations["zero_chaos_preflight"].update(
                call_status="returned",
                return_value=preflight_verified,
                host_observed_at_utc=datetime.now(UTC).isoformat(),
            )
            if not preflight_verified:
                self._persist_rollout({
                    "scenario_id": scenario_id,
                    "tier": tier,
                    "status": "failed",
                    "failure": "zero_chaos_preflight_unverified",
                    "reward": None,
                    "lifecycle_observations": lifecycle_observations,
                })
                raise RuntimeError("GRPO rollout requires verified zero-Chaos preflight")
            result = None
            failure_reason = None
            try:
                apply_observation = lifecycle_observations["apply_chaos"]
                try:
                    apply_verified = apply_chaos(
                        scenario_id,
                        execute_live_chaos=self.execute_live_chaos,
                        kube_context=kube_context,
                    )
                except Exception as exc:
                    apply_observation.update(
                        call_status="raised",
                        exception_type=type(exc).__name__,
                        host_observed_at_utc=datetime.now(UTC).isoformat(),
                    )
                    raise
                apply_observation.update(
                    call_status="returned",
                    return_value=apply_verified,
                    host_observed_at_utc=datetime.now(UTC).isoformat(),
                )
                if not apply_verified:
                    log.warning("Chaos apply failed for %s; rollout is unscorable", scenario_id)
                    failure_reason = "chaos_apply_failed"
                else:
                    await asyncio.sleep(15)
                    from bench.runner import wait_for_alert

                    alert_observation = lifecycle_observations["wait_for_alert"]
                    try:
                        alert = wait_for_alert()
                    except Exception as exc:
                        alert_observation.update(
                            call_status="raised",
                            exception_type=type(exc).__name__,
                            host_observed_at_utc=datetime.now(UTC).isoformat(),
                        )
                        raise
                    alert_observation.update(
                        call_status="returned",
                        host_observed_at_utc=datetime.now(UTC).isoformat(),
                    )
                    alert_binding_result = _g9_alert_binding_result(
                        alert,
                        SCENARIO_CATALOG[scenario_id].expected_alert,
                    )
                    alert_observation["result"] = alert_binding_result
                    if alert_binding_result != "scenario_matched":
                        log.warning(
                            "Alert observation did not bind to %s (%s)",
                            scenario_id,
                            alert_binding_result,
                        )
                        failure_reason = (
                            f"g9_alert_binding_failed:{alert_binding_result}"
                        )
                    else:
                        if approval_gate is None:
                            result = await self._run_one_rollout(
                                completion, scenario_id, tier, alert
                            )
                        else:
                            result = await self._run_one_rollout(
                                completion, scenario_id, tier, alert,
                                approval_gate=approval_gate,
                            )
            except Exception as exc:
                log.warning("Rollout %d failed (%s)", i + 1, type(exc).__name__)
                failure_reason = f"rollout_exception:{type(exc).__name__}"
            finally:
                cleanup_exception_type = None
                cleanup_observation = lifecycle_observations["reset_chaos"]
                try:
                    cleanup_verified = reset_chaos(
                        scenario_id,
                        execute_live_chaos=self.execute_live_chaos,
                        kube_context=kube_context,
                    )
                except Exception as exc:
                    cleanup_verified = False
                    cleanup_exception_type = type(exc).__name__
                    cleanup_observation.update(
                        call_status="raised",
                        exception_type=cleanup_exception_type,
                        host_observed_at_utc=datetime.now(UTC).isoformat(),
                    )
                else:
                    cleanup_observation.update(
                        call_status="returned",
                        return_value=cleanup_verified,
                        host_observed_at_utc=datetime.now(UTC).isoformat(),
                    )
                if not cleanup_verified:
                    cleanup_failure = {
                        "scenario_id": scenario_id,
                        "tier": tier,
                        "status": "failed",
                        "failure": "scenario_cleanup_unverified",
                        "prior_failure": failure_reason,
                        "scorable": False,
                        "reward": None,
                        "cleanup_exception_type": cleanup_exception_type,
                        "lifecycle_observations": lifecycle_observations,
                    }
                    if result is not None:
                        cleanup_failure["rollout_result"] = result
                    elif not (failure_reason or "").startswith(
                        "g9_alert_binding_failed:"
                    ):
                        cleanup_failure["policy_completion"] = completion
                    self._persist_rollout(cleanup_failure)
                    raise RuntimeError(
                        f"GRPO scenario cleanup was not verified: {scenario_id}"
                    ) from None
            await asyncio.sleep(10)   # let the cluster fully stabilise

            if result is None:
                failure_record = {
                    "scenario_id": scenario_id,
                    "tier": tier,
                    "status": "failed",
                    "failure": failure_reason or "rollout_failed",
                    "reward": None,
                    "lifecycle_observations": lifecycle_observations,
                }
                if not (failure_reason or "").startswith(
                    "g9_alert_binding_failed:"
                ):
                    failure_record["policy_completion"] = completion
                self._persist_rollout(failure_record)
                raise RuntimeError("GRPO rollout is unscorable without policy/verifier evidence")
            else:
                try:
                    r = compute_direct_action_reward(result)
                except ValueError as exc:
                    result["reward"] = None
                    result["failure"] = f"direct_action_reward_unscorable:{type(exc).__name__}"
                    result["lifecycle_observations"] = lifecycle_observations
                    self._persist_rollout(result)
                    raise RuntimeError(
                        "GRPO direct-action rollout cannot be scored"
                    ) from None
                rewards.append(r)
                result["reward"] = r
                result["reward_profile"] = "direct_action_objective_v1"
                result["lifecycle_observations"] = lifecycle_observations
                self._persist_rollout(result)
                _curriculum.record(
                    scenario_id=scenario_id,
                    resolved=bool(result.get("resolved", False)),
                    reward=r,
                )

        cur_stats = _curriculum.stats()
        log.info(
            "Batch done | scenarios=%d rewards: min=%.3f max=%.3f mean=%.3f | "
            "curriculum: %d tried, %d graduated, %d due for resurface",
            len(set(scenario_ids)),
            min(rewards), max(rewards), sum(rewards) / len(rewards),
            cur_stats["scenarios_tried"], cur_stats["graduated"],
            cur_stats["due_for_resurface"],
        )
        return rewards

    def _persist_rollout(self, result: dict[str, Any]) -> None:
        kube_context = _require_live_execution(
            self.execute_live_chaos, self.kube_context
        )
        if self.rollout_log_path is None:
            return
        self.rollout_log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            **result,
            "rollout_phase": self.rollout_phase,
            "trial_number": self.trial_number,
            "effective_hyperparameters": self.effective_hyperparameters,
            "live_execution": {
                "execute_live_chaos": self.execute_live_chaos,
                "kube_context": kube_context,
            },
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        with self.rollout_log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    async def _run_one_rollout(
        self,
        completion_text: str,
        scenario_id: str,
        tier: str,
        alert: dict[str, Any],
        *,
        approval_gate: ApprovalGate | None = None,
    ) -> dict:
        """Execute the policy completion itself as one atomic environment action."""
        t0 = time.time()
        common_labels = alert.get("commonLabels") or {}
        alert_record = (alert.get("alerts") or [{}])[0]
        nested_labels = alert_record.get("labels") or {}
        severities = {
            str(value).lower()
            for value in (
                common_labels.get("severity"),
                alert_record.get("severity"),
                nested_labels.get("severity"),
            )
            if value
        }
        alert_severity = next(iter(severities)) if len(severities) == 1 else ""
        triage_severity = {
            "critical": "P1",
            "warning": "P2",
            "info": "P3",
        }.get(alert_severity, "P0")
        public_alert = strip_untrusted_approval_context(alert)
        state = {
            "incident_id": f"g9-{uuid.uuid4().hex}",
            "alert": public_alert,
            "triage": {"severity": triage_severity},
            "observations": public_alert.get("observations", {}),
        }
        environment_kwargs: dict[str, Any] = {
            "execute_live_chaos": self.execute_live_chaos,
            "kube_context": self.kube_context,
        }
        if approval_gate is not None:
            environment_kwargs["_action_approval_gate"] = approval_gate
        environment = DirectPolicyEnvironment(**environment_kwargs)
        permit = None
        approval_record = None
        try:
            action = parse_policy_action(completion_text)
        except (TypeError, ValueError):
            action = None
        if (
            approval_gate is not None
            and action is not None
            and policy_action_requires_operator_approval(action, state)
        ):
            request = approval_gate.request_action(
                incident_id=state["incident_id"],
                severity=triage_severity,
                action=action,
                operator_scope={
                    "kube_context": self.kube_context,
                    "scenario_id": scenario_id,
                },
            )
            decision, permit = await approval_gate.wait_for_action_decision(
                state["incident_id"],
                request_token=request.token,
            )
            approval_record = {
                "decision": decision.get("status"),
                "approved_by": decision.get("approved_by"),
                "action_digest": request.action_digest,
            }
        step_kwargs = {"_action_approval_permit": permit} if approval_gate is not None else {}
        result = await environment.step(
            completion_text,
            scenario_id=scenario_id,
            state=state,
            **step_kwargs,
        )
        result.update(
            {
                "incident_id": state["incident_id"],
                "approval": approval_record,
                "scenario_id": scenario_id,
                "tier": tier,
                "total_turns": 1,
                "time_to_resolve_s": round(time.time() - t0),
                "postmortem_path": None,
            }
        )
        return result


# ── Optuna HP search ──────────────────────────────────────────────────────────

_OPTUNA_DEFERRED_MESSAGE = (
    "Optuna GRPO search is deferred pending an explicitly approved trial batch/generation budget. "
    "Its fixed settings use per_device_train_batch_size=1 and gradient_accumulation_steps=1 "
    "with num_generations in [4, 8], which violate pinned TRL 0.19.1's divisibility rule. "
    "No trials were started and no replacement hyperparameters were selected."
)


def run_optuna_search(model_path: str, tiers: list[str], output_dir: Path,
                      model_revision: str, tokenizer_id: str, tokenizer_revision: str,
                      sft_checkpoint: Path,
                      n_trials: int = 6,
                      *,
                      execute_live_chaos: bool = False,
                      kube_context: str | None = None,
                      operator_approval_enabled: bool = False,
                      seed: int = 42) -> dict[str, Any]:
    if n_trials < 0:
        raise ValueError("n_trials must be non-negative")
    if n_trials == 0:
        return {}
    raise RuntimeError(_OPTUNA_DEFERRED_MESSAGE)


# ── Model loading ─────────────────────────────────────────────────────────────

def load_model_and_tokenizer(
    model_path: str,
    *,
    model_revision: str,
    tokenizer_id: str,
    tokenizer_revision: str,
    sft_checkpoint: Path,
    execute_live_chaos: bool = False,
    kube_context: str | None = None,
    manifest_path: Path | None = None,
):
    validate_grpo_model_references(
        model_id=model_path,
        model_revision=model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=tokenizer_revision,
    )
    _require_live_execution(execute_live_chaos, kube_context)
    sft_parent = validate_sft_parent(
        sft_checkpoint,
        model_id=model_path,
        model_revision=model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=tokenizer_revision,
    )
    if manifest_path is not None:
        validate_sft_parent_matches_run(manifest_path, sft_parent)
    _ensure_model_loader_dependencies()
    _require_single_writer()
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_id, revision=tokenizer_revision, trust_remote_code=False
    )
    validate_tokenizer_loader_revision(
        tokenizer,
        requested_revision=tokenizer_revision,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        revision=model_revision,
        quantization_config=BNBCONFIG,
        device_map="auto",
        trust_remote_code=False,
        attn_implementation="flash_attention_2" if _flash_attn_available() else "eager",
    )
    validate_resolved_hf_commit(
        model_revision,
        getattr(getattr(model, "config", None), "_commit_hash", None),
        label="base model",
    )
    if manifest_path is not None:
        record_loader_provenance(
            manifest_path,
            model=model,
            tokenizer=tokenizer,
            model_id=model_path,
            model_revision=model_revision,
            tokenizer_id=tokenizer_id,
            tokenizer_revision=tokenizer_revision,
        )
    model = prepare_model_for_kbit_training(model)
    current_sft_parent = validate_sft_parent(
        sft_checkpoint,
        model_id=model_path,
        model_revision=model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=tokenizer_revision,
    )
    if current_sft_parent != sft_parent:
        raise ValueError("GRPO SFT parent changed during base-model loading")
    if manifest_path is not None:
        validate_sft_parent_matches_run(manifest_path, current_sft_parent)
    model = PeftModel.from_pretrained(
        model, str(sft_checkpoint), is_trainable=True
    )
    model.print_trainable_parameters()
    return model, tokenizer


def _flash_attn_available() -> bool:
    try:
        import flash_attn  # noqa: F401
        return True
    except ImportError:
        return False


def _g9_alert_binding_result(alert: Any, expected_alert: str) -> str:
    """Return a sanitized status for the live alert-to-Train-scenario binding."""
    if alert is None:
        return "none"
    if not isinstance(alert, dict):
        return "malformed"
    synthetic = alert.get("synthetic")
    if synthetic is True:
        return "synthetic"
    if synthetic is not None and synthetic is not False:
        return "malformed"

    common_labels = alert.get("commonLabels")
    alerts = alert.get("alerts")
    if not isinstance(common_labels, dict) or not isinstance(alerts, list):
        return "malformed"
    if not alerts:
        return "none"
    if len(alerts) != 1:
        return "ambiguous"
    alert_record = alerts[0]
    if not isinstance(alert_record, dict):
        return "malformed"

    envelope_alertname = common_labels.get("alertname")
    observed_alertname = alert_record.get("alertname")
    if (
        not isinstance(envelope_alertname, str)
        or not envelope_alertname.strip()
        or not isinstance(observed_alertname, str)
        or not observed_alertname.strip()
    ):
        return "identity_missing"
    if envelope_alertname != observed_alertname:
        return "envelope_mismatch"
    if (
        not isinstance(expected_alert, str)
        or not expected_alert.strip()
        or observed_alertname != expected_alert
    ):
        return "scenario_mismatch"

    status = alert_record.get("status")
    if not isinstance(status, str) or not status:
        return "malformed"
    if status != "active":
        return "not_active"
    return "scenario_matched"


def _direct_action_prompt(scenario_id: str) -> str:
    """Build the public policy prompt for one frozen Train scenario."""
    if scenario_id not in set(get_split("train")):
        raise ValueError(f"GRPO prompt scenario is outside frozen Train: {scenario_id}")
    meta = SCENARIO_CATALOG[scenario_id]
    state = {
        "alert": {
            "labels": {
                "alertname": meta.expected_alert,
                "namespace": "default",
            },
            "status": "firing",
        },
        "instruction": (
            "Return exactly one JSON object with keys tool, arguments, and "
            "agent_claimed_resolved. The tool and arguments are the exact action "
            "that will be safety-checked and executed. Do not include an actions list."
        ),
    }
    return json.dumps(state, sort_keys=True)


def _eligible_train_scenario_ids(tiers: list[str]) -> list[str]:
    allowed_tiers = set(tiers)
    scenario_ids = [
        scenario_id
        for scenario_id in get_split("train")
        if SCENARIO_CATALOG[scenario_id].tier in allowed_tiers
    ]
    if not scenario_ids:
        raise ValueError(f"No frozen training scenarios match tiers={sorted(allowed_tiers)}")
    return scenario_ids


def build_direct_action_prompts(tiers: list[str]) -> list[dict[str, str]]:
    """Build training-only prompts with their exact frozen scenario identity."""
    return [
        {
            "prompt": _direct_action_prompt(scenario_id),
            "scenario_id": scenario_id,
        }
        for scenario_id in _eligible_train_scenario_ids(tiers)
    ]


def _sample_curriculum_prompt(
    tiers: tuple[str, ...],
    allowed_scenarios: frozenset[str],
) -> dict[str, str]:
    scenario_id, _tier = sample_scenario(list(tiers))
    if scenario_id not in allowed_scenarios:
        raise ValueError(
            f"GRPO curriculum sampled scenario outside configured Train tiers: {scenario_id}"
        )
    return {
        "prompt": _direct_action_prompt(scenario_id),
        "scenario_id": scenario_id,
    }


class _CurriculumPromptMapDataset:
    """Map-style prompt source for TRL's repeated-index generation sampler."""

    def __init__(self, tiers: tuple[str, ...], scenario_ids: list[str]):
        self.tiers = tiers
        self.scenario_ids = frozenset(scenario_ids)
        self.length = len(scenario_ids)
        self.feedback_count = _curriculum.stats()["total_episodes"]
        self.rows: dict[int, dict[str, str]] = {}

    def __len__(self) -> int:
        return self.length

    def __getitem__(self, index: int) -> dict[str, str]:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.length:
            raise IndexError(index)
        # RepeatSampler reuses indices for a generation group; invalidate after reward feedback lands.
        feedback_count = _curriculum.stats()["total_episodes"]
        if feedback_count != self.feedback_count:
            self.rows.clear()
            self.feedback_count = feedback_count
        if index not in self.rows:
            self.rows[index] = _sample_curriculum_prompt(self.tiers, self.scenario_ids)
        return dict(self.rows[index])


def build_curriculum_prompt_dataset(tiers: list[str]):
    """Create a lazily sampled map dataset compatible with pinned TRL 0.19.1."""
    return _CurriculumPromptMapDataset(
        tuple(tiers),
        _eligible_train_scenario_ids(tiers),
    )


def _has_verified_rollout(path: Path) -> bool:
    return has_verified_final_rollout(path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",           required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--tokenizer")
    parser.add_argument("--tokenizer-revision", required=True)
    parser.add_argument("--sft-checkpoint", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output",          required=True)
    parser.add_argument("--tiers",           default="cascade,multi_fault,named_replays")
    parser.add_argument("--lr",              type=float, default=1e-6)
    parser.add_argument("--beta",            type=float, default=0.04)
    parser.add_argument("--batch-size",      type=int,   default=1)
    parser.add_argument("--num-generations", type=int,   default=8)
    parser.add_argument("--max-steps",       type=int,   default=200)
    parser.add_argument("--max-compl-len",   type=int,   default=512)
    parser.add_argument("--grad-accum",      type=int,   default=4)
    parser.add_argument("--optuna",          type=int,   default=0)
    parser.add_argument(
        "--execute-live-chaos",
        action="store_true",
        help="Opt in to live G9 cluster mutations for this invocation",
    )
    parser.add_argument(
        "--kube-context",
        help="Named Kubernetes context explicitly targeted by every G9 kubectl call",
    )
    parser.add_argument(
        "--enable-p1-approval",
        action="store_true",
        help="Start a same-process localhost operator channel for exact P1 actions",
    )
    args = parser.parse_args()
    if args.optuna > 0:
        parser.error(_OPTUNA_DEFERRED_MESSAGE)
    if args.optuna < 0:
        parser.error("--optuna must be non-negative")
    tokenizer_id = args.tokenizer or args.model
    try:
        _require_single_writer()
        validate_grpo_batch_configuration(
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            num_generations=args.num_generations,
        )
        validate_grpo_model_references(
            model_id=args.model,
            model_revision=args.model_revision,
            tokenizer_id=tokenizer_id,
            tokenizer_revision=args.tokenizer_revision,
        )
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    try:
        kube_context = _require_live_execution(
            args.execute_live_chaos, args.kube_context
        )
    except (PermissionError, ValueError) as exc:
        parser.error(str(exc))
    args.kube_context = kube_context
    require_stable_failure_persistence()
    if args.enable_p1_approval and not os.getenv("ATLASOPS_API_KEY", "").strip():
        parser.error("--enable-p1-approval requires ATLASOPS_API_KEY in the environment")

    tiers      = [t.strip() for t in args.tiers.split(",")]
    output_dir = validate_new_output_directory(Path(args.output))
    sft_parent = validate_sft_parent(
        args.sft_checkpoint,
        model_id=args.model,
        model_revision=args.model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=args.tokenizer_revision,
    )
    prompt_rows = build_direct_action_prompts(tiers)
    manifest = create_run_manifest(
        model_id=args.model,
        model_revision=args.model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=args.tokenizer_revision,
        seed=args.seed,
        generation_config={"max_completion_length": args.max_compl_len},
        hyperparameters={
            "tiers": tiers,
            "learning_rate": args.lr,
            "beta": args.beta,
            "batch_size": args.batch_size,
            "num_generations": args.num_generations,
            "max_steps": args.max_steps,
            "gradient_accumulation_steps": args.grad_accum,
            "optuna_trials": args.optuna,
        },
        execute_live_chaos=args.execute_live_chaos,
        kube_context=kube_context,
        sft_parent=sft_parent,
        prompt_rows=prompt_rows,
    )
    manifest["training"]["operator_approval"] = _operator_approval_profile(
        args.enable_p1_approval
    )
    output_dir = claim_new_output_directory(output_dir)
    manifest_path = output_dir / MANIFEST_NAME
    manifest = persist_status(manifest_path, manifest, "planned")
    try:
        manifest = persist_status(manifest_path, manifest, "running")
        run_result = run_training(args, output_dir)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["training"]["effective_hyperparameters"] = run_result[
            "effective_hyperparameters"
        ]
        manifest["training"]["hyperparameter_selection"] = run_result[
            "hyperparameter_selection"
        ]
        persist_status(manifest_path, manifest, "completed")
    except KeyboardInterrupt:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        persist_status(manifest_path, manifest, "interrupted", error_type="KeyboardInterrupt")
        raise
    except Exception as exc:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        persist_status(manifest_path, manifest, "failed", error_type=type(exc).__name__)
        raise


@_terminalize_started_run_on_exception(output_dir_position=1)
def run_training(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    """Execute the declared online training run after intent is persisted."""
    optuna_trials = getattr(args, "optuna", 0)
    if optuna_trials > 0:
        raise RuntimeError(_OPTUNA_DEFERRED_MESSAGE)
    if optuna_trials < 0:
        raise ValueError("Optuna trial count must be non-negative")
    validate_grpo_batch_configuration(
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        num_generations=args.num_generations,
    )
    _require_single_writer()
    kube_context = _require_live_execution(
        getattr(args, "execute_live_chaos", False),
        getattr(args, "kube_context", None),
    )
    tokenizer_id = args.tokenizer or args.model
    validate_grpo_model_references(
        model_id=args.model,
        model_revision=args.model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=args.tokenizer_revision,
    )
    sft_parent = validate_sft_parent(
        args.sft_checkpoint,
        model_id=args.model,
        model_revision=args.model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=args.tokenizer_revision,
    )
    tiers = [tier.strip() for tier in args.tiers.split(",")]
    manifest_path = Path(output_dir) / MANIFEST_NAME
    requested_hyperparameters = {
        "tiers": tiers,
        "learning_rate": args.lr,
        "beta": args.beta,
        "batch_size": args.batch_size,
        "num_generations": args.num_generations,
        "max_steps": args.max_steps,
        "gradient_accumulation_steps": args.grad_accum,
        "optuna_trials": optuna_trials,
    }
    validate_grpo_run_plan(
        manifest_path,
        model_id=args.model,
        model_revision=args.model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=args.tokenizer_revision,
        sft_parent=sft_parent,
        training_fields={
            "seed": args.seed,
            "generation_config": {
                "max_completion_length": args.max_compl_len,
            },
            "live_execution": {
                "execute_live_chaos": args.execute_live_chaos,
                "kube_context": kube_context,
            },
            "operator_approval": _operator_approval_profile(
                getattr(args, "enable_p1_approval", False)
            ),
        },
        requested_hyperparameters=requested_hyperparameters,
        require_started=False,
    )
    _require_single_writer()
    require_stable_failure_persistence()
    rollout_path = output_dir / "rollout_trajectories.jsonl"
    if rollout_path.exists():
        raise FileExistsError(
            f"Final GRPO rollout ledger already exists: {rollout_path}"
        )
    mark_training_started(
        manifest_path,
        model_id=args.model,
        model_revision=args.model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=args.tokenizer_revision,
        sft_parent=sft_parent,
    )
    _mark_run_attempt_started()
    random.seed(args.seed)
    _load_training_dependencies()
    _require_single_writer()
    import torch

    torch.manual_seed(args.seed)

    lr = args.lr
    beta = args.beta
    num_gen = args.num_generations
    requested_hyperparameters = {
        "tiers": tiers,
        "learning_rate": args.lr,
        "beta": args.beta,
        "batch_size": args.batch_size,
        "num_generations": args.num_generations,
        "max_steps": args.max_steps,
        "gradient_accumulation_steps": args.grad_accum,
        "optuna_trials": optuna_trials,
    }
    effective_hyperparameters = {
        **requested_hyperparameters,
        "learning_rate": lr,
        "beta": beta,
        "num_generations": num_gen,
        "max_completion_length": args.max_compl_len,
    }
    hyperparameter_selection = "requested"

    log.info("GRPO config: lr=%.2e beta=%.4f num_gen=%d tiers=%s", lr, beta, num_gen, tiers)

    model, tokenizer = load_model_and_tokenizer(
        args.model,
        model_revision=args.model_revision,
        tokenizer_id=tokenizer_id,
        tokenizer_revision=args.tokenizer_revision,
        sft_checkpoint=args.sft_checkpoint,
        execute_live_chaos=args.execute_live_chaos,
        kube_context=kube_context,
        manifest_path=manifest_path,
    )

    # Online reward function runs real serialized cluster rollouts during training.
    reward_fn = OnlineRewardFunction(
        tiers,
        rollout_log_path=rollout_path,
        execute_live_chaos=args.execute_live_chaos,
        kube_context=kube_context,
        rollout_phase="final_training",
        effective_hyperparameters=effective_hyperparameters,
        operator_approval_enabled=getattr(args, "enable_p1_approval", False),
    )

    # Each consumed prompt is sampled after the preceding verifier feedback.
    dataset = build_curriculum_prompt_dataset(tiers)

    grpo_args = GRPOConfig(
        output_dir=str(output_dir),
        learning_rate=lr,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        dataloader_num_workers=0,
        remove_unused_columns=False,
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
        seed=args.seed,
    )

    trainer = GRPOTrainer(
        model=model,
        args=grpo_args,
        train_dataset=dataset,
        processing_class=tokenizer,
        reward_funcs=[reward_fn],  # ← online RL against real GKE cluster
    )

    log.info("Starting online GRPO against the configured Kubernetes environment...")
    log.info("Each step: apply chaos → G=%d rollouts → objective reward → gradient update", num_gen)
    trainer.train(resume_from_checkpoint=None)

    if not rollout_path.is_file() or not has_verified_final_rollout(
        rollout_path,
        live_execution={
            "execute_live_chaos": args.execute_live_chaos,
            "kube_context": kube_context,
        },
        effective_hyperparameters=effective_hyperparameters,
    ):
        raise RuntimeError("GRPO training returned without a verified real rollout trajectory")

    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    logs = trainer.state.log_history
    rewards = [
        entry.get("rewards/mean")
        for entry in logs if "rewards/mean" in entry
    ]
    summary = {
        "model": args.model, "tiers": tiers,
        "total_steps": trainer.state.global_step,
        "final_reward_mean": rewards[-1] if rewards else None,
        "best_reward_mean": max(rewards) if rewards else None,
        "reward_history": rewards,
        "config": {"lr": lr, "beta": beta, "num_generations": num_gen},
        "generation_config": {"max_completion_length": args.max_compl_len},
        "requested_hyperparameters": requested_hyperparameters,
        "effective_hyperparameters": effective_hyperparameters,
        "hyperparameter_selection": hyperparameter_selection,
        "training_mode": "online_rl_real_environment",
        "seed": args.seed,
        "live_execution": {
            "execute_live_chaos": args.execute_live_chaos,
            "kube_context": kube_context,
        },
        "operator_approval": {
            "mode": (
                "loopback_exact_action_v1"
                if getattr(args, "enable_p1_approval", False)
                else "disabled"
            ),
            "timeout_seconds": (
                OPERATOR_APPROVAL_TIMEOUT_SECONDS
                if getattr(args, "enable_p1_approval", False)
                else None
            ),
            "identity": "operator_supplied_name_not_independent_attestation",
        },
        "trainer_log_history": logs,
    }
    with (output_dir / "training_summary.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2)
        stream.write("\n")
    log.info("Done. final_reward=%.4f | best=%.4f",
             summary["final_reward_mean"] or 0, summary["best_reward_mean"] or 0)
    return {
        "effective_hyperparameters": effective_hyperparameters,
        "hyperparameter_selection": hyperparameter_selection,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
