"""GRPO training for AtlasOps — online RL against real GKE cluster on AMD MI300X.

Architecture:
  - Each GRPO step generates G=8 rollouts by running the full agent chain
    against a live chaos scenario on the real GKE cluster
  - Reward comes from the AtlasOps reward contract (same as bench/runner.py)
  - This is TRUE online RL — not offline reward-weighted SFT
  - QLoRA: 4-bit base + LoRA r=16 for memory efficiency on MI300X

Training flow:
  1. Sample a chaos scenario from the tier-weighted curriculum
  2. Apply Chaos Mesh to real GKE cluster
  3. Run G=8 parallel agent rollouts (model generates tool calls)
  4. Score each rollout with reward contract (kubectl/promql verify real cluster state)
  5. GRPO updates — policy learns from what actually worked on the real cluster
  6. Reset cluster, next step
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config.runtime import (
    FINAL_TEST_SCENARIOS,
    TRAINING_SCENARIOS_BY_TIER,
    TIER_SAMPLING_WEIGHTS,
    CurriculumManager,
    evaluate_reward_contract,
)
from agents.tool_policy import CLUSTER_MUTATING_TOOLS

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


def compute_reward(episode: dict) -> float:
    """Return a verifier-grounded, policy-attributed Stage 9 training reward.

    Resolution is positive only when the exact policy action executed
    successfully and the independent verifier then observed recovery. Dense
    reward is capped at 15 percent so failed actions cannot masquerade as
    resolution. Judge prose, comms output, and wall-clock infrastructure delay
    are intentionally outside the GRPO reward.
    """
    audit = evaluate_reward_contract({
        **episode,
        "judge": {},
        "postmortem_path": None,
    })
    outcome = str(episode.get("outcome", "unknown"))
    env_resolved = bool(episode.get("env_resolved") is True)
    attributed_action = _policy_action_succeeded(episode)
    resolution = 1.0 if env_resolved and attributed_action else (
        0.5 if outcome == "partial" else 0.0
    )
    step_total = sum(
        role_data.get("step_reward_summary", {}).get("dense_reward_total", 0.0)
        for role in ("triage", "diagnosis", "remediation", "comms")
        for role_data in [episode.get(role, {})]
    )
    step_norm = max(0.0, min(1.0, step_total / 10.0))
    penalty_total = sum(float(value) for value in audit["penalties"].values())
    reward = 0.85 * resolution + 0.15 * step_norm - penalty_total
    return round(max(0.0, min(1.0, reward)), 4)


def _policy_action_succeeded(episode: dict) -> bool:
    remediation = episode.get("remediation", {})
    final = remediation.get("final", {}) if isinstance(remediation, dict) else {}
    return bool(
        final.get("mode") == "policy_rollout"
        and final.get("policy_completion_valid") is True
        and final.get("policy_action_identity_match") is True
        and any(
            isinstance(action, dict)
            and action.get("tool") in CLUSTER_MUTATING_TOOLS
            and action.get("success") is True
            for action in final.get("executed_actions", [])
        )
    )


def sample_scenario(tiers: list[str]) -> tuple[str, str]:
    """Use CurriculumManager priority scoring (spaced repetition + weakness targeting)."""
    pool = [
        (sid, sid.split("/")[0])
        for tier in tiers
        for sid in TRAINING_SCENARIOS_BY_TIER.get(tier, [])
    ]
    return _curriculum.next_scenario(pool)


def apply_chaos(scenario_id: str) -> bool:
    manifest = Path("bench/chaos_manifests") / f"{scenario_id}.yaml"
    if not manifest.exists():
        return False
    env = os.environ.copy()
    env["USE_GKE_GCLOUD_AUTH_PLUGIN"] = "True"
    r = subprocess.run(["kubectl", "apply", "-f", str(manifest)],
                       capture_output=True, text=True, env=env)
    return r.returncode == 0


def reset_chaos() -> bool:
    env = os.environ.copy()
    env["USE_GKE_GCLOUD_AUTH_PLUGIN"] = "True"
    result = subprocess.run(
        ["kubectl", "delete",
         "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
         "--all", "-A", "--ignore-not-found=true"],
        capture_output=True, env=env,
    )
    ok = result.returncode == 0
    time.sleep(20)
    return ok


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
        episodes_path: str | Path | None = None,
        curriculum_state_path: str | Path | None = None,
    ):
        self.tiers = tiers
        self.coordinator_url = coordinator_url
        self.episodes_path = Path(episodes_path) if episodes_path else None
        self.curriculum_state_path = (
            Path(curriculum_state_path) if curriculum_state_path else None
        )
        self._loop = asyncio.new_event_loop()

    def __del__(self):
        if not self._loop.is_closed():
            self._loop.close()

    def __call__(self, completions: list[str], prompts: list[str],
                 **kwargs) -> list[float]:
        """Called by TRL after generating G completions. Returns reward per completion."""
        return self._loop.run_until_complete(
            self._score_batch(completions, prompts)
        )

    async def _score_batch(self, completions: list[str],
                           prompts: list[str]) -> list[float]:
        """Score G completions by running SERIALIZED rollouts on the live cluster.

        Why serialized (not asyncio.gather):
        All G rollouts share one GKE cluster. Running them in parallel causes
        interference — rollout 1 may delete the chaos while rollout 3 is still
        diagnosing, making rewards correlated and gradients incorrect.
        Serializing gives each rollout a clean, independent cluster state:
          apply_chaos → wait → rollout → reset_chaos → wait → next rollout
        This is slower (G × episode_time) but produces correct independent rewards.
        """
        rewards: list[float] = []
        scenario_id, tier = sample_scenario(self.tiers)

        for i, completion in enumerate(completions):
            log.info("Rollout %d/%d — scenario %s", i + 1, len(completions), scenario_id)

            if not apply_chaos(scenario_id):
                raise RuntimeError(f"chaos_apply_failed:{scenario_id}")

            # Wait for Alertmanager to fire
            await asyncio.sleep(15)

            try:
                result = await self._run_one_rollout(completion, scenario_id, tier)
            except Exception as e:
                log.exception("Rollout %d failed: %s", i + 1, e)
                result = None

            # Always reset before the next rollout — even on failure
            if not reset_chaos():
                raise RuntimeError(f"chaos_reset_failed:{scenario_id}")
            await asyncio.sleep(10)   # let the cluster fully stabilise

            if result is None:
                rewards.append(0.0)
            else:
                r = compute_reward(result)
                rewards.append(r)
                _curriculum.record(
                    scenario_id=scenario_id,
                    resolved=bool(result.get("resolved", False)),
                    reward=r,
                )
                self._persist_episode(i, completion, scenario_id, tier, r, result)
                self._persist_curriculum_state()

        cur_stats = _curriculum.stats()
        log.info(
            "Batch done | scenario=%s rewards: min=%.3f max=%.3f mean=%.3f | "
            "curriculum: %d tried, %d graduated, %d due for resurface",
            scenario_id,
            min(rewards), max(rewards), sum(rewards) / len(rewards),
            cur_stats["scenarios_tried"], cur_stats["graduated"],
            cur_stats["due_for_resurface"],
        )
        return rewards

    async def _run_one_rollout(self, completion_text: str,
                               scenario_id: str, tier: str) -> dict:
        """Execute the exact TRL completion as the sole rewarded remediation action.

        Other runtime agents may prepare context, but they cannot replace or
        reinterpret the policy's one-action plan. No external judge contributes
        to the returned reward episode.
        """
        from agents.coordinator import handle_incident

        alert = {
            "commonLabels": {"alertname": "GRPOTrainingAlert"},
            "scenario_id": scenario_id,
            "alerts": [],
        }

        t0 = time.time()
        incident = await handle_incident(
            alert,
            scenario_id=scenario_id,
            remediation_policy_completion=completion_text,
        )

        remediation = incident.get("remediation", {}).get("final", {})
        env_resolved = bool(incident.get("env_resolved") is True)
        agent_claimed_resolved = bool(incident.get("agent_claimed_resolved"))
        total_turns = sum(
            len(incident.get(r, {}).get("trajectory", []))
            for r in ("triage", "diagnosis", "remediation", "comms")
        )

        return {
            "tier": tier,
            "resolved": env_resolved,
            "outcome": remediation.get("outcome", "unknown"),
            "agent_claimed_resolved": agent_claimed_resolved,
            "env_resolved": env_resolved,
            "verification": incident.get("verification", {}),
            "total_turns": total_turns,
            "time_to_resolve_s": round(time.time() - t0),
            "triage": incident.get("triage", {}),
            "diagnosis": incident.get("diagnosis", {}),
            "remediation": incident.get("remediation", {}),
            "comms": incident.get("comms", {}),
            "causal_coupling": {
                "completion_sha256": hashlib.sha256(completion_text.encode()).hexdigest(),
                "completion_length": len(completion_text),
                "mode": "direct_remediation_policy_completion",
            },
        }

    def _persist_episode(self, rollout_index: int, completion: str,
                         scenario_id: str, tier: str, reward: float,
                         episode: dict) -> None:
        if not self.episodes_path:
            return
        self.episodes_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "rollout_index": rollout_index,
            "scenario_id": scenario_id,
            "tier": tier,
            "reward": reward,
            "policy_completion": completion,
            "episode": episode,
        }
        with self.episodes_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def _persist_curriculum_state(self) -> None:
        if not self.curriculum_state_path:
            return
        path = self.curriculum_state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(_curriculum.export_state(), sort_keys=True), encoding="utf-8")
        temporary.replace(path)


# ── Optuna HP search ──────────────────────────────────────────────────────────

def run_optuna_search(model_path: str, tiers: list[str], output_dir: str,
                      n_trials: int = 6) -> dict[str, Any]:
    from peft import get_peft_model
    from transformers import AutoTokenizer
    from trl import GRPOConfig, GRPOTrainer

    try:
        import optuna
    except ImportError:
        log.warning("optuna not installed — skipping HP search")
        return {}

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    reward_fn = OnlineRewardFunction(tiers)

    def objective(trial: optuna.Trial) -> float:
        lr      = trial.suggest_float("lr", 5e-7, 5e-6, log=True)
        beta    = trial.suggest_float("beta", 0.001, 0.05, log=True)
        num_gen = trial.suggest_categorical("num_generations", [4, 8])

        tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        base_model = load_quantized_base_model(model_path)
        model = get_peft_model(base_model, _lora_config())

        # Minimal dataset: GRPOTrainer needs a prompt dataset
        from datasets import Dataset
        prompts = [{"prompt": "Respond as SRE triage agent."} for _ in range(20)]
        dataset = Dataset.from_list(prompts)

        grpo_args = GRPOConfig(
            output_dir=f"{output_dir}/trial_{trial.number}",
            learning_rate=lr,
            per_device_train_batch_size=1,
            bf16=True, max_steps=10, report_to=[], optim="paged_adamw_8bit",
            num_generations=num_gen, beta=beta, max_completion_length=256,
            seed=42,
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

def load_model_and_tokenizer(model_path: str):
    from peft import get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoTokenizer

    adapter_marker = Path(model_path) / "adapter_config.json"
    if adapter_marker.exists():
        raise ValueError(
            "sft_checkpoint_contract: pass the merged SFT decoder checkpoint; "
            "an adapter-only directory cannot be silently used as the base"
        )
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

    adapter_marker = Path(model_path) / "adapter_config.json"
    if adapter_marker.exists():
        raise ValueError(
            "sft_checkpoint_contract: pass the merged SFT decoder checkpoint; "
            "an adapter-only directory cannot be silently used as the base"
        )
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


def _prompt_dataset_provenance(path: Path) -> dict[str, Any]:
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
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_commit": _git_commit(),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "model_path": model_path,
        "checkpoint_contract": "merged_sft_decoder_required",
        "dataset": _prompt_dataset_provenance(dataset_path),
        "training_scenarios": TRAINING_SCENARIOS_BY_TIER,
        "final_test_scenarios": sorted(FINAL_TEST_SCENARIOS),
        "curriculum_seed": curriculum_seed,
        "lora": LORA_HYPERPARAMETERS,
        "quantization": BNB_CONFIG_ARGUMENTS,
        "output_dir": str(output_dir),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    from trl import GRPOConfig, GRPOTrainer

    parser = argparse.ArgumentParser()
    parser.add_argument("--model",           required=True)
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
    parser.add_argument("--curriculum-seed", type=int, default=42)
    parser.add_argument("--sft-corpus", default="data/sft_corpus.jsonl")
    parser.add_argument("--resume-from-checkpoint", default="")
    args = parser.parse_args()

    tiers      = [t.strip() for t in args.tiers.split(",")]
    unknown_final_test = FINAL_TEST_SCENARIOS & {
        sid
        for tier in tiers
        for sid in TRAINING_SCENARIOS_BY_TIER.get(tier, [])
    }
    if unknown_final_test:
        raise RuntimeError(f"training_scenario_leak:{sorted(unknown_final_test)}")
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    sft_data_path = Path(args.sft_corpus)
    curriculum_seed = args.curriculum_seed
    curriculum_state_path = output_dir / "curriculum_state.json"
    resume_checkpoint = args.resume_from_checkpoint
    if resume_checkpoint:
        if not curriculum_state_path.exists():
            raise RuntimeError(
                "resume_provenance_missing: curriculum_state.json is required "
                "when resuming a GRPO checkpoint"
            )
        _curriculum.restore_state(json.loads(curriculum_state_path.read_text(encoding="utf-8")))
    else:
        _curriculum._rng = random.Random(curriculum_seed)
        _curriculum._history.clear()
        _curriculum._graduated.clear()
        _curriculum._next_resurface.clear()
        _curriculum._recent.clear()
        _curriculum._episode_count = 0
    provenance = build_training_provenance(
        args.model,
        output_dir,
        sft_data_path,
        curriculum_seed,
    )
    provenance["resumed_from"] = resume_checkpoint or None
    (output_dir / "run_manifest.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8",
    )

    # Optional Optuna HP search (runs live rollouts against GKE)
    best_hp: dict[str, Any] = {}
    if args.optuna > 0:
        log.info("Optuna HP search (%d trials × 10 live GKE rollouts each)...", args.optuna)
        best_hp = run_optuna_search(args.model, tiers, str(output_dir), n_trials=args.optuna)

    lr      = best_hp.get("lr", args.lr)
    beta    = best_hp.get("beta", args.beta)
    num_gen = best_hp.get("num_generations", args.num_generations)

    log.info("GRPO config: lr=%.2e beta=%.4f num_gen=%d tiers=%s", lr, beta, num_gen, tiers)

    model, tokenizer = load_model_and_tokenizer(args.model)

    # Online reward function — runs real GKE rollouts during training
    reward_fn = OnlineRewardFunction(
        tiers,
        episodes_path=output_dir / "grpo_episodes.jsonl",
        curriculum_state_path=curriculum_state_path,
    )

    # Minimal prompt dataset (GRPO generates its own completions online)
    from datasets import Dataset
    if sft_data_path.exists():
        prompts = []
        with sft_data_path.open() as f:
            for line in f:
                try:
                    item = json.loads(line)
                    msgs = item.get("messages", [])
                    if msgs:
                        prompts.append({"prompt": json.dumps(msgs[:-1])})
                except json.JSONDecodeError:
                    pass
        dataset = Dataset.from_list(prompts[:5000])
    else:
        # Fallback: prompt-only dataset with role instructions
        dataset = Dataset.from_list([
            {"prompt": f"You are the AtlasOps {role} agent responding to a real Kubernetes incident."}
            for role in ["triage", "diagnosis", "remediation", "comms"] * 250
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
    )

    trainer = GRPOTrainer(
        model=model,
        args=grpo_args,
        train_dataset=dataset,
        processing_class=tokenizer,
        reward_funcs=[reward_fn],  # ← online RL against real GKE cluster
    )

    log.info("Starting online GRPO against real GKE cluster on AMD MI300X...")
    log.info("Each step: apply chaos → G=%d rollouts → reward contract → gradient update", num_gen)
    trainer.train(resume_from_checkpoint=resume_checkpoint or None)

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
        "training_mode": "online_rl_real_gke_direct_policy_completion",
        "provenance": provenance,
    }
    (output_dir / "training_summary.json").write_text(json.dumps(summary, indent=2))
    log.info("Done. final_reward=%.4f | best=%.4f",
             summary["final_reward_mean"] or 0, summary["best_reward_mean"] or 0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
