"""GRPO training for AtlasOps against one controlled Kubernetes environment.

Architecture:
  - Each GRPO step generates a group of policy completions against a live
    Chaos Mesh scenario.
  - Reward comes from the AtlasOps reward contract (same as bench/runner.py)
  - The exact completion action is executed and scored from verifier truth.
  - QLoRA uses a 4-bit base plus LoRA r=16.

Training flow:
  1. Sample a chaos scenario from the tier-weighted curriculum
  2. Apply Chaos Mesh to real GKE cluster
   3. Run serialized policy rollouts (model generates one action each)
  4. Score each rollout with reward contract (kubectl/promql verify real cluster state)
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

from config.runtime import (
    SCENARIOS_BY_TIER,
    CurriculumManager,
    evaluate_reward_contract,
)
from config.scenario_catalog import SCENARIO_CATALOG
from config.splits import TEST_SPLIT, VAL_SPLIT, get_split
from training.grpo_environment import DirectPolicyEnvironment
from training.grpo_provenance import (
    MANIFEST_NAME,
    create_run_manifest,
    persist_status,
    validate_sft_parent,
)

log = logging.getLogger(__name__)


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
    """Blend episode-level contract reward (70%) with dense step rewards (30%).

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


def zero_chaos_verified() -> bool:
    env = os.environ.copy()
    env["USE_GKE_GCLOUD_AUTH_PLUGIN"] = "True"
    result = subprocess.run(
        [
            "kubectl", "get",
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


def apply_chaos(scenario_id: str) -> bool:
    manifest = _chaos_manifest(scenario_id)
    if not manifest.exists():
        return False
    env = os.environ.copy()
    env["USE_GKE_GCLOUD_AUTH_PLUGIN"] = "True"
    r = subprocess.run(
        ["kubectl", "apply", "-f", str(manifest)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return r.returncode == 0


def reset_chaos(scenario_id: str) -> bool:
    manifest = _chaos_manifest(scenario_id)
    if not manifest.is_file():
        return False
    env = os.environ.copy()
    env["USE_GKE_GCLOUD_AUTH_PLUGIN"] = "True"
    deleted = subprocess.run(
        ["kubectl", "delete", "-f", str(manifest), "--ignore-not-found=true"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return deleted.returncode == 0 and zero_chaos_verified()


# ── Online reward function for TRL GRPOTrainer ────────────────────────────────

class OnlineRewardFunction:
    """Wraps the real GKE environment as a TRL-compatible reward function.

    For each batch of completions TRL generates, this class:
    1. Parses the model's tool call sequence from the completion text
    2. Executes it against the real GKE cluster (via coordinator)
    3. Scores the outcome with the reward contract
    4. Returns rewards for GRPO advantage computation
    """

    def __init__(
        self,
        tiers: list[str],
        coordinator_url: str = "http://localhost:9099",
        rollout_log_path: Path | None = None,
    ):
        self.tiers = tiers
        self.coordinator_url = coordinator_url
        self.rollout_log_path = rollout_log_path
        self._loop = asyncio.new_event_loop()

    def __del__(self):
        if not self._loop.is_closed():
            self._loop.close()

    def __call__(self, completions: list[str], prompts: list[str],
                 scenario_id: list[str] | None = None, **kwargs) -> list[float]:
        """Called by TRL after generating G completions. Returns reward per completion."""
        return self._loop.run_until_complete(
            self._score_batch(completions, prompts, scenario_id)
        )

    async def _score_batch(self, completions: list[str],
                           prompts: list[str], scenario_ids: list[str] | None) -> list[float]:
        """Score completions by running serialized rollouts on the live cluster.

        Why serialized (not asyncio.gather):
        All G rollouts share one GKE cluster. Running them in parallel causes
        interference — rollout 1 may delete the chaos while rollout 3 is still
        diagnosing, making rewards correlated and gradients incorrect.
        Serializing gives each rollout a clean, independent cluster state:
          apply_chaos → wait → rollout → reset_chaos → wait → next rollout
        This is slower (G × episode_time) but produces correct independent rewards.
        """
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

            if not zero_chaos_verified():
                raise RuntimeError("GRPO rollout requires verified zero-Chaos preflight")
            result = None
            failure_reason = None
            try:
                if not apply_chaos(scenario_id):
                    log.warning("Chaos apply failed for %s — assigning 0 reward", scenario_id)
                    failure_reason = "chaos_apply_failed"
                else:
                    await asyncio.sleep(15)
                    from bench.runner import wait_for_alert

                    alert = wait_for_alert()
                    if alert is None or alert.get("synthetic") is True:
                        log.warning("No real alert observed for %s", scenario_id)
                        failure_reason = "real_alert_not_observed"
                    else:
                        result = await self._run_one_rollout(
                            completion, scenario_id, tier, alert
                        )
            except Exception as exc:
                log.exception("Rollout %d failed", i + 1)
                failure_reason = f"{type(exc).__name__}: {exc}"
            finally:
                if not reset_chaos(scenario_id):
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
                rewards.append(0.0)
                self._persist_rollout({
                    "scenario_id": scenario_id,
                    "tier": tier,
                    "status": "failed",
                    "failure": failure_reason or "rollout_failed",
                    "policy_completion": completion,
                    "reward": 0.0,
                })
            else:
                r = compute_reward(result)
                rewards.append(r)
                result["reward"] = r
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
        if self.rollout_log_path is None:
            return
        self.rollout_log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            **result,
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
        state = {
            "alert": alert,
            "triage": {"severity": triage_severity},
            "approval": alert.get("approval"),
            "observations": alert.get("observations", {}),
        }
        result = await DirectPolicyEnvironment().step(
            completion_text,
            scenario_id=scenario_id,
            state=state,
        )
        result.update(
            {
                "scenario_id": scenario_id,
                "tier": tier,
                "total_turns": 1,
                "time_to_resolve_s": round(time.time() - t0),
                "postmortem_path": None,
            }
        )
        result["reward_contract"] = evaluate_reward_contract(result)
        return result


# ── Optuna HP search ──────────────────────────────────────────────────────────

def run_optuna_search(model_path: str, tiers: list[str], output_dir: Path,
                      model_revision: str, tokenizer_id: str, tokenizer_revision: str,
                      sft_checkpoint: Path,
                      n_trials: int = 6) -> dict[str, Any]:
    try:
        import optuna
    except ImportError:
        log.warning("optuna not installed — skipping HP search")
        return {}

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    reward_fn = OnlineRewardFunction(
        tiers,
        rollout_log_path=output_dir / "rollout_trajectories.jsonl",
    )

    def objective(trial: optuna.Trial) -> float:
        lr      = trial.suggest_float("lr", 5e-7, 5e-6, log=True)
        beta    = trial.suggest_float("beta", 0.001, 0.05, log=True)
        num_gen = trial.suggest_categorical("num_generations", [4, 8])

        model, tokenizer = load_model_and_tokenizer(
            model_path,
            model_revision=model_revision,
            tokenizer_id=tokenizer_id,
            tokenizer_revision=tokenizer_revision,
            sft_checkpoint=sft_checkpoint,
        )

        # Preserve the exact frozen Train scenario identity through TRL.
        from datasets import Dataset
        dataset = Dataset.from_list(build_direct_action_prompts(tiers))

        grpo_args = GRPOConfig(
            output_dir=f"{output_dir}/trial_{trial.number}",
            learning_rate=lr,
            per_device_train_batch_size=1,
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
        rewards = [l.get("rewards/mean", 0) for l in logs if "rewards/mean" in l]
        return sum(rewards[-3:]) / max(len(rewards[-3:]), 1)

    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials)
    best = {"params": study.best_params, "value": study.best_value}
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
):
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
    verified = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        row = json.loads(line)
        if (
            row.get("status") == "ok"
            and isinstance(row.get("verification"), dict)
            and isinstance(row["verification"].get("env_resolved"), bool)
        ):
            verified = True
    return verified


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
    args = parser.parse_args()

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
        sft_parent=sft_parent,
        prompt_rows=prompt_rows,
    )
    manifest = persist_status(manifest_path, manifest, "planned")
    try:
        manifest = persist_status(manifest_path, manifest, "running")
        run_training(args, output_dir)
        persist_status(manifest_path, manifest, "completed")
    except KeyboardInterrupt:
        persist_status(manifest_path, manifest, "interrupted", error_type="KeyboardInterrupt")
        raise
    except Exception as exc:
        persist_status(manifest_path, manifest, "failed", error_type=type(exc).__name__)
        raise


def run_training(args: argparse.Namespace, output_dir: Path) -> None:
    """Execute the declared online training run after intent is persisted."""
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
        )

    lr      = best_hp.get("lr", args.lr)
    beta    = best_hp.get("beta", args.beta)
    num_gen = best_hp.get("num_generations", args.num_generations)

    log.info("GRPO config: lr=%.2e beta=%.4f num_gen=%d tiers=%s", lr, beta, num_gen, tiers)

    model, tokenizer = load_model_and_tokenizer(
        args.model,
        model_revision=args.model_revision,
        tokenizer_id=args.tokenizer or args.model,
        tokenizer_revision=args.tokenizer_revision,
        sft_checkpoint=args.sft_checkpoint,
    )

    # Online reward function runs real serialized cluster rollouts during training.
    rollout_path = output_dir / "rollout_trajectories.jsonl"
    reward_fn = OnlineRewardFunction(tiers, rollout_log_path=rollout_path)

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
    log.info("Each step: apply chaos → G=%d rollouts → reward contract → gradient update", num_gen)
    trainer.train()

    if not rollout_path.is_file() or not _has_verified_rollout(rollout_path):
        raise RuntimeError("GRPO training returned without a verified real rollout trajectory")

    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    logs = trainer.state.log_history
    rewards = [l.get("rewards/mean") for l in logs if "rewards/mean" in l]
    summary = {
        "model": args.model, "tiers": tiers,
        "total_steps": trainer.state.global_step,
        "final_reward_mean": rewards[-1] if rewards else None,
        "best_reward_mean": max(rewards) if rewards else None,
        "reward_history": rewards,
        "config": {"lr": lr, "beta": beta, "num_generations": num_gen},
        "training_mode": "online_rl_real_environment",
        "seed": args.seed,
        "trainer_log_history": logs,
    }
    (output_dir / "training_summary.json").write_text(json.dumps(summary, indent=2))
    log.info("Done. final_reward=%.4f | best=%.4f",
             summary["final_reward_mean"] or 0, summary["best_reward_mean"] or 0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
