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
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from peft import PeftModel, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import GRPOConfig, GRPOTrainer
    _HAS_TORCH_RL = True
except ImportError:
    PeftModel = None  # type: ignore
    prepare_model_for_kbit_training = None  # type: ignore
    AutoModelForCausalLM = None  # type: ignore
    AutoTokenizer = None  # type: ignore
    BitsAndBytesConfig = None  # type: ignore
    GRPOConfig = None  # type: ignore
    GRPOTrainer = None  # type: ignore
    _HAS_TORCH_RL = False

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
    create_run_manifest,
    has_verified_final_rollout,
    persist_status,
    validate_sft_parent,
)
from training.grpo_reward import score_direct_action_step

log = logging.getLogger(__name__)


def _require_live_execution(
    execute_live_chaos: bool,
    kube_context: str | None,
) -> str:
    return require_live_kube_context(
        execute_live_chaos,
        kube_context,
        opt_in_flag="--execute-live-chaos",
    )


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


# ── QLoRA config ──────────────────────────────────────────────────────────────

if _HAS_TORCH_RL:
    BNBCONFIG = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype="bfloat16",
        bnb_4bit_use_double_quant=True,
    )
else:
    BNBCONFIG = None


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

            if not zero_chaos_verified(
                execute_live_chaos=self.execute_live_chaos,
                kube_context=kube_context,
            ):
                raise RuntimeError("GRPO rollout requires verified zero-Chaos preflight")
            result = None
            failure_reason = None
            try:
                if not apply_chaos(
                    scenario_id,
                    execute_live_chaos=self.execute_live_chaos,
                    kube_context=kube_context,
                ):
                    log.warning("Chaos apply failed for %s; rollout is unscorable", scenario_id)
                    failure_reason = "chaos_apply_failed"
                else:
                    await asyncio.sleep(15)
                    from bench.runner import wait_for_alert

                    alert = wait_for_alert()
                    if alert is None or alert.get("synthetic") is True:
                        log.warning("No real alert observed for %s", scenario_id)
                        failure_reason = "real_alert_not_observed"
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
                if not reset_chaos(
                    scenario_id,
                    execute_live_chaos=self.execute_live_chaos,
                    kube_context=kube_context,
                ):
                    self._persist_rollout({
                        "scenario_id": scenario_id,
                        "tier": tier,
                        "status": "failed",
                        "failure": "scenario_cleanup_unverified",
                        "prior_failure": failure_reason,
                        "policy_completion": completion,
                        "reward": None,
                    })
                    raise RuntimeError(
                        f"GRPO scenario cleanup was not verified: {scenario_id}"
                    )
            await asyncio.sleep(10)   # let the cluster fully stabilise

            if result is None:
                self._persist_rollout({
                    "scenario_id": scenario_id,
                    "tier": tier,
                    "status": "failed",
                    "failure": failure_reason or "rollout_failed",
                    "policy_completion": completion,
                    "reward": None,
                })
                raise RuntimeError("GRPO rollout is unscorable without policy/verifier evidence")
            else:
                try:
                    r = compute_direct_action_reward(result)
                except ValueError as exc:
                    result["reward"] = None
                    result["failure"] = f"direct_action_reward_unscorable:{type(exc).__name__}"
                    self._persist_rollout(result)
                    raise RuntimeError(
                        "GRPO direct-action rollout cannot be scored"
                    ) from None
                rewards.append(r)
                result["reward"] = r
                result["reward_profile"] = "direct_action_objective_v1"
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
        labels = {
            **(alert.get("commonLabels") or {}),
            **((alert.get("alerts") or [{}])[0].get("labels") or {}),
        }
        alert_severity = str(labels.get("severity", "")).lower()
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

def run_optuna_search(model_path: str, tiers: list[str], output_dir: Path,
                      model_revision: str, tokenizer_id: str, tokenizer_revision: str,
                      sft_checkpoint: Path,
                      n_trials: int = 6,
                      *,
                      execute_live_chaos: bool = False,
                      kube_context: str | None = None,
                      operator_approval_enabled: bool = False) -> dict[str, Any]:
    _require_single_writer()
    kube_context = _require_live_execution(execute_live_chaos, kube_context)
    try:
        import optuna
    except ImportError:
        log.warning("optuna not installed — skipping HP search")
        return {}

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial: optuna.Trial) -> float:
        lr      = trial.suggest_float("lr", 5e-7, 5e-6, log=True)
        beta    = trial.suggest_float("beta", 0.001, 0.05, log=True)
        num_gen = trial.suggest_categorical("num_generations", [4, 8])
        effective_hyperparameters = {
            "tiers": tiers,
            "learning_rate": lr,
            "beta": beta,
            "batch_size": 1,
            "num_generations": num_gen,
            "max_steps": 10,
            "gradient_accumulation_steps": 1,
            "max_completion_length": 256,
        }
        reward_fn = OnlineRewardFunction(
            tiers,
            rollout_log_path=(
                Path(output_dir)
                / "optuna_trials"
                / f"trial_{trial.number}"
                / "rollout_trajectories.jsonl"
            ),
            execute_live_chaos=execute_live_chaos,
            kube_context=kube_context,
            rollout_phase="optuna_trial",
            trial_number=trial.number,
            effective_hyperparameters=effective_hyperparameters,
            operator_approval_enabled=operator_approval_enabled,
        )

        model, tokenizer = load_model_and_tokenizer(
            model_path,
            model_revision=model_revision,
            tokenizer_id=tokenizer_id,
            tokenizer_revision=tokenizer_revision,
            sft_checkpoint=sft_checkpoint,
            execute_live_chaos=execute_live_chaos,
            kube_context=kube_context,
        )

        # Preserve the exact frozen Train scenario identity through TRL.
        from datasets import Dataset
        dataset = Dataset.from_list(build_direct_action_prompts(tiers))

        grpo_args = GRPOConfig(
            output_dir=f"{output_dir}/trial_{trial.number}",
            learning_rate=lr,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=1,
            bf16=True, max_steps=10, report_to=[], optim="paged_adamw_8bit",
            num_generations=num_gen, beta=beta, max_completion_length=256,
        )
        trainer = GRPOTrainer(
            model=model, args=grpo_args, train_dataset=dataset,
            processing_class=tokenizer,
            reward_funcs=[reward_fn],
        )
        trainer.train()
        logs = trainer.state.log_history
        rewards = [
            entry.get("rewards/mean", 0)
            for entry in logs if "rewards/mean" in entry
        ]
        return sum(rewards[-3:]) / max(len(rewards[-3:]), 1)

    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials)
    best = {
        "params": study.best_params,
        "value": study.best_value,
        "live_execution": {
            "execute_live_chaos": execute_live_chaos,
            "kube_context": kube_context,
        },
    }
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    (Path(output_dir) / "optuna_best.json").write_text(json.dumps(best, indent=2))
    log.info("Best HP: %s (value=%.4f)", study.best_params, study.best_value)
    return study.best_params


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
):
    _require_live_execution(execute_live_chaos, kube_context)
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_id, revision=tokenizer_revision, trust_remote_code=True
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        revision=model_revision,
        quantization_config=BNBCONFIG,
        device_map="auto",
        trust_remote_code=True,
        attn_implementation="flash_attention_2" if _flash_attn_available() else "eager",
    )
    model = prepare_model_for_kbit_training(model)
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


def build_direct_action_prompts(tiers: list[str]) -> list[dict[str, str]]:
    """Build training-only prompts with their exact frozen scenario identity."""
    allowed_tiers = set(tiers)
    prompts = [
        {
            "prompt": _direct_action_prompt(scenario_id),
            "scenario_id": scenario_id,
        }
        for scenario_id in get_split("train")
        if SCENARIO_CATALOG[scenario_id].tier in allowed_tiers
    ]
    if not prompts:
        raise ValueError(f"No frozen training scenarios match tiers={sorted(allowed_tiers)}")
    return prompts


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
    try:
        kube_context = _require_live_execution(
            args.execute_live_chaos, args.kube_context
        )
    except (PermissionError, ValueError) as exc:
        parser.error(str(exc))
    args.kube_context = kube_context
    _require_single_writer()
    if args.enable_p1_approval and not os.getenv("ATLASOPS_API_KEY", "").strip():
        parser.error("--enable-p1-approval requires ATLASOPS_API_KEY in the environment")

    tiers      = [t.strip() for t in args.tiers.split(",")]
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / MANIFEST_NAME
    if manifest_path.exists():
        raise FileExistsError(f"GRPO run manifest already exists: {manifest_path}")
    sft_parent = validate_sft_parent(
        args.sft_checkpoint,
        model_id=args.model,
        model_revision=args.model_revision,
        tokenizer_id=args.tokenizer or args.model,
        tokenizer_revision=args.tokenizer_revision,
    )
    prompt_rows = build_direct_action_prompts(tiers)
    manifest = create_run_manifest(
        model_id=args.model,
        model_revision=args.model_revision,
        tokenizer_id=args.tokenizer or args.model,
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
    manifest["training"]["operator_approval"] = {
        "mode": (
            "loopback_exact_action_v1" if args.enable_p1_approval else "disabled"
        ),
        "timeout_seconds": (
            OPERATOR_APPROVAL_TIMEOUT_SECONDS if args.enable_p1_approval else None
        ),
        "identity": "operator_supplied_name_not_independent_attestation",
    }
    manifest = persist_status(manifest_path, manifest, "planned")
    try:
        manifest = persist_status(manifest_path, manifest, "running")
        run_result = run_training(args, output_dir)
        manifest["training"]["effective_hyperparameters"] = run_result[
            "effective_hyperparameters"
        ]
        manifest["training"]["hyperparameter_selection"] = run_result[
            "hyperparameter_selection"
        ]
        persist_status(manifest_path, manifest, "completed")
    except KeyboardInterrupt:
        persist_status(manifest_path, manifest, "interrupted", error_type="KeyboardInterrupt")
        raise
    except Exception as exc:
        persist_status(manifest_path, manifest, "failed", error_type=type(exc).__name__)
        raise


def run_training(args: argparse.Namespace, output_dir: Path) -> dict[str, Any]:
    """Execute the declared online training run after intent is persisted."""
    _require_single_writer()
    kube_context = _require_live_execution(
        getattr(args, "execute_live_chaos", False),
        getattr(args, "kube_context", None),
    )
    rollout_path = output_dir / "rollout_trajectories.jsonl"
    if rollout_path.exists():
        raise FileExistsError(
            f"Final GRPO rollout ledger already exists: {rollout_path}"
        )
    tiers = [tier.strip() for tier in args.tiers.split(",")]
    random.seed(args.seed)
    if _HAS_TORCH_RL:
        import torch

        torch.manual_seed(args.seed)

    # Optional Optuna search runs live rollouts against the configured cluster.
    best_hp: dict[str, Any] = {}
    if args.optuna > 0:
        log.info("Optuna HP search (%d trials × 10 live GKE rollouts each)...", args.optuna)
        best_hp = run_optuna_search(
            args.model,
            tiers,
            output_dir,
            args.model_revision,
            args.tokenizer or args.model,
            args.tokenizer_revision,
            args.sft_checkpoint,
            n_trials=args.optuna,
            execute_live_chaos=args.execute_live_chaos,
            kube_context=kube_context,
            operator_approval_enabled=getattr(args, "enable_p1_approval", False),
        )

    lr      = best_hp.get("lr", args.lr)
    beta    = best_hp.get("beta", args.beta)
    num_gen = best_hp.get("num_generations", args.num_generations)
    if best_hp and not {"lr", "beta", "num_generations"} <= best_hp.keys():
        raise RuntimeError("Optuna returned incomplete effective hyperparameters")
    requested_hyperparameters = {
        "tiers": tiers,
        "learning_rate": args.lr,
        "beta": args.beta,
        "batch_size": args.batch_size,
        "num_generations": args.num_generations,
        "max_steps": args.max_steps,
        "gradient_accumulation_steps": args.grad_accum,
        "optuna_trials": args.optuna,
    }
    effective_hyperparameters = {
        **requested_hyperparameters,
        "learning_rate": lr,
        "beta": beta,
        "num_generations": num_gen,
        "max_completion_length": args.max_compl_len,
    }
    hyperparameter_selection = "optuna" if best_hp else "requested"

    log.info("GRPO config: lr=%.2e beta=%.4f num_gen=%d tiers=%s", lr, beta, num_gen, tiers)

    model, tokenizer = load_model_and_tokenizer(
        args.model,
        model_revision=args.model_revision,
        tokenizer_id=args.tokenizer or args.model,
        tokenizer_revision=args.tokenizer_revision,
        sft_checkpoint=args.sft_checkpoint,
        execute_live_chaos=args.execute_live_chaos,
        kube_context=kube_context,
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

    # Every completion is parsed and executed as one exact structured action.
    from datasets import Dataset
    dataset = Dataset.from_list(build_direct_action_prompts(tiers))

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
    trainer.train()

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
    (output_dir / "training_summary.json").write_text(json.dumps(summary, indent=2))
    log.info("Done. final_reward=%.4f | best=%.4f",
             summary["final_reward_mean"] or 0, summary["best_reward_mean"] or 0)
    return {
        "effective_hyperparameters": effective_hyperparameters,
        "hyperparameter_selection": hyperparameter_selection,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
