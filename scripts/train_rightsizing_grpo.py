"""CPU GRPO experiment on the right-sizing environment. Not the G9 protocol.

Train (baseline evaluation, GRPO with LoRA, in-process evaluation, adapter save):
    python -m scripts.train_rightsizing_grpo train --model-dir <hf model> --output-dir <new dir>
Evaluate the saved adapter after a fresh reload, in a new process:
    python -m scripts.train_rightsizing_grpo evaluate --model-dir <hf model> --run-dir <run dir>

Every output goes to a new directory outside the repository. Results are recorded as
measured, including failures; nothing here changes a gate status.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from training import rightsizing_env as env

REPO = Path(__file__).resolve().parents[1]
LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(directory: Path) -> dict[str, dict[str, Any]]:
    return {path.relative_to(directory).as_posix(): {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in sorted(directory.rglob("*")) if path.is_file()}


def source_identity() -> dict[str, Any]:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, text=True)
    return {"commit": commit, "dirty": bool(status.strip())}


def runtime() -> dict[str, Any]:
    import peft
    import torch
    import transformers
    import trl

    return {"python": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
            "torch": torch.__version__, "transformers": transformers.__version__, "trl": trl.__version__,
            "peft": peft.__version__, "cuda_available": torch.cuda.is_available(),
            "torch_threads": torch.get_num_threads()}


def load_base(model_dir: Path):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(model_dir, local_files_only=True, dtype=torch.float32)
    return model, tokenizer


def evaluate(model, tokenizer, states: list[env.CapacityState], *, batch_size: int = 16,
             max_new_tokens: int = 64) -> dict[str, Any]:
    """Greedy decoding on fixed states; every completion is scored by the environment."""
    import torch

    tokenizer.padding_side = "left"
    model.eval()
    rows = []
    for start in range(0, len(states), batch_size):
        batch = states[start:start + batch_size]
        texts = [tokenizer.apply_chat_template(env.prompt_messages(state), add_generation_prompt=True,
                                               tokenize=False) for state in batch]
        encoded = tokenizer(texts, return_tensors="pt", padding=True)
        with torch.no_grad():
            output = model.generate(**encoded, max_new_tokens=max_new_tokens, do_sample=False,
                                    pad_token_id=tokenizer.pad_token_id)
        completions = tokenizer.batch_decode(output[:, encoded["input_ids"].shape[1]:], skip_special_tokens=True)
        for state, completion in zip(batch, completions):
            result = env.score(state, completion)
            rows.append({"replicas": state.replicas, "demand": state.demand,
                         "capacity": state.capacity_per_replica, "needed": state.needed,
                         "completion": completion, "reward": result["reward"],
                         "admissible": result["admissible"], "resolved": result["resolved"],
                         "category": result["category"], "action_replicas": result["replicas"]})
    admissible = [row for row in rows if row["admissible"]]
    return {
        "n": len(rows),
        "admissible_rate": round(len(admissible) / len(rows), 4),
        "resolved_rate": round(sum(row["resolved"] for row in rows) / len(rows), 4),
        "mean_reward": round(sum(row["reward"] for row in rows) / len(rows), 4),
        "under_provisioned": sum(1 for row in admissible if (row["action_replicas"] or 0) < row["needed"]),
        "over_provisioned": sum(1 for row in admissible if (row["action_replicas"] or 0) > row["needed"]),
        "invalid_categories": {category: sum(1 for row in rows if row["category"] == category)
                               for category in sorted({row["category"] for row in rows if row["category"]})},
        "rows": rows,
    }


def summary(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "rows"}


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def train(args: argparse.Namespace) -> None:
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import TrainerCallback
    from trl import GRPOConfig, GRPOTrainer

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(os.cpu_count() or 1)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    train_states, held_out = env.split_states(args.split_seed)
    eval_train = random.Random(args.seed).sample(train_states, 64)
    config = {
        "experiment": env.EXPERIMENT, "seed": args.seed, "split_seed": args.split_seed,
        "max_steps": args.max_steps, "num_generations": args.num_generations,
        "prompts_per_step": args.prompts_per_step, "learning_rate": args.learning_rate,
        "beta": 0.0, "temperature": 1.0, "max_completion_length": args.max_completion_length,
        "curriculum": {"easy_fraction": args.easy_fraction, "easy_max_capacity": 2},
        "lora": {"r": 16, "alpha": 32, "dropout": 0.0, "targets": LORA_TARGETS},
        "eval": {"decoding": "greedy", "max_new_tokens": 64},
    }
    record: dict[str, Any] = {
        "experiment": env.EXPERIMENT,
        "classification": "SMALL_MODEL_CPU_RL_EXPERIMENT",
        "gate_status": "Not a G9 result. G9 remains NOT_PASSED/FROZEN; this run was not reviewed or "
                       "authorized under the G9 protocol.",
        "started_at": datetime.now(UTC).isoformat(),
        "source": source_identity(),
        "runtime": runtime(),
        "model": {"dir_inventory": inventory(args.model_dir),
                  "provenance": json.loads((args.model_dir / "provenance.json").read_text())
                  if (args.model_dir / "provenance.json").exists() else None},
        "data": {"train_states": len(train_states), "held_out_states": len(held_out),
                 "train_sha256": env.states_sha256(train_states),
                 "held_out_sha256": env.states_sha256(held_out),
                 "train_eval_sample_sha256": env.states_sha256(eval_train)},
        "config": config,
        "status": "started",
    }
    write_json(out / "RESULT.json", record)

    model, tokenizer = load_base(args.model_dir)
    started = time.time()
    baseline = {"held_out": evaluate(model, tokenizer, held_out),
                "train_sample": evaluate(model, tokenizer, eval_train)}
    record["baseline"] = {name: summary(metrics) for name, metrics in baseline.items()}
    record["baseline_eval_seconds"] = round(time.time() - started, 1)
    write_rows(out / "baseline_held_out.jsonl", baseline["held_out"]["rows"])
    write_json(out / "RESULT.json", record)
    print("baseline", json.dumps(record["baseline"]["held_out"]), flush=True)

    # Easy-first curriculum: early steps draw from low per-replica capacities, where an
    # exact answer is likelier to be sampled; later steps draw from the whole Train set.
    shuffler = random.Random(args.seed)
    needed_prompts = args.max_steps * args.prompts_per_step
    easy_prompts = int(needed_prompts * args.easy_fraction)
    easy = [state for state in train_states if state.capacity_per_replica <= 2]
    chosen = ([shuffler.choice(easy) for _ in range(easy_prompts)]
              + [shuffler.choice(train_states) for _ in range(needed_prompts - easy_prompts)])
    dataset = Dataset.from_list([
        {"prompt": env.prompt_messages(state), "replicas": state.replicas, "demand": state.demand,
         "capacity": state.capacity_per_replica}
        for state in chosen
    ])
    ledger = (out / "rollouts.jsonl").open("a", encoding="utf-8")
    progress = {"step": 0}

    def reward_fn(prompts, completions, replicas, demand, capacity, **_):
        rewards = []
        for completion, r, d, c in zip(completions, replicas, demand, capacity):
            text = completion[0]["content"] if isinstance(completion, list) else str(completion)
            state = env.CapacityState(int(r), int(d), int(c))
            result = env.score(state, text)
            rewards.append(result["reward"])
            ledger.write(json.dumps({"step": progress["step"], "replicas": state.replicas,
                                     "demand": state.demand, "capacity": state.capacity_per_replica,
                                     "needed": state.needed, "completion": text, "reward": result["reward"],
                                     "resolved": result["resolved"], "category": result["category"]},
                                    sort_keys=True) + "\n")
        ledger.flush()
        return rewards

    log_path = out / "training_log.jsonl"

    class Recorder(TrainerCallback):
        def on_step_begin(self, args_, state, control, **kwargs):
            progress["step"] = state.global_step + 1

        def on_log(self, args_, state, control, logs=None, **kwargs):
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"step": state.global_step, "time": round(time.time() - started, 1),
                                         **(logs or {})}, sort_keys=True) + "\n")

    grpo_config = GRPOConfig(
        output_dir=str(out / "trainer"), max_steps=args.max_steps,
        per_device_train_batch_size=args.prompts_per_step * args.num_generations,
        num_generations=args.num_generations, learning_rate=args.learning_rate,
        lr_scheduler_type="constant", warmup_steps=0, beta=0.0, temperature=1.0,
        max_prompt_length=512, max_completion_length=args.max_completion_length,
        logging_steps=1, save_strategy="no", report_to=[], seed=args.seed, use_cpu=True,
        bf16=False, fp16=False, gradient_accumulation_steps=1, dataloader_num_workers=0,
        remove_unused_columns=False, shuffle_dataset=False,
    )
    trainer = GRPOTrainer(
        model=model, reward_funcs=reward_fn, args=grpo_config, train_dataset=dataset,
        processing_class=tokenizer, callbacks=[Recorder()],
        peft_config=LoraConfig(r=16, lora_alpha=32, lora_dropout=0.0, target_modules=LORA_TARGETS,
                               task_type="CAUSAL_LM"),
    )
    initial = {name: tensor.detach().clone() for name, tensor in trainer.model.named_parameters()
               if "lora_" in name}
    started = time.time()
    trainer.train()
    record["training_seconds"] = round(time.time() - started, 1)
    ledger.close()
    changed = [name for name, tensor in trainer.model.named_parameters()
               if "lora_" in name and not torch.equal(tensor.detach(), initial[name])]
    record["lora"] = {"tensors": len(initial), "changed": len(changed)}

    adapter_dir = out / "adapter"
    trainer.model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    record["adapter_inventory"] = inventory(adapter_dir)
    started = time.time()
    in_process = evaluate(trainer.model, tokenizer, held_out)
    record["trained_in_process"] = {"held_out": summary(in_process)}
    record["trained_eval_seconds"] = round(time.time() - started, 1)
    write_rows(out / "trained_in_process_held_out.jsonl", in_process["rows"])

    rewards = [json.loads(line) for line in (out / "rollouts.jsonl").read_text().splitlines()]
    by_step: dict[int, list[float]] = {}
    for row in rewards:
        by_step.setdefault(row["step"], []).append(row["reward"])
    steps = sorted(by_step)
    window = max(1, len(steps) // 10)

    def mean_of(selected: list[int]) -> float:
        values = [reward for step in selected for reward in by_step[step]]
        return round(sum(values) / len(values), 4)

    groups = [(step, rewards_[i:i + args.num_generations]) for step, rewards_ in by_step.items()
              for i in range(0, len(rewards_), args.num_generations)]
    record["training"] = {
        "steps": len(steps), "rollouts": len(rewards),
        "mean_reward_first_10pct_steps": mean_of(steps[:window]),
        "mean_reward_last_10pct_steps": mean_of(steps[-window:]),
        "groups": len(groups),
        "groups_with_reward_variation": sum(1 for _, group in groups if len(set(group)) > 1),
        "resolved_rate_first_10pct": round(sum(r["resolved"] for r in rewards if r["step"] in steps[:window])
                                           / sum(1 for r in rewards if r["step"] in steps[:window]), 4),
        "resolved_rate_last_10pct": round(sum(r["resolved"] for r in rewards if r["step"] in steps[-window:])
                                          / sum(1 for r in rewards if r["step"] in steps[-window:]), 4),
    }
    record["status"] = "trained_reload_pending"
    record["finished_at"] = datetime.now(UTC).isoformat()
    write_json(out / "RESULT.json", record)
    print("trained (in process)", json.dumps(record["trained_in_process"]["held_out"]), flush=True)


def evaluate_saved(args: argparse.Namespace) -> None:
    import torch
    from peft import PeftModel

    run = args.run_dir
    record = json.loads((run / "RESULT.json").read_text())
    adapter_dir = run / "adapter"
    if inventory(adapter_dir) != record["adapter_inventory"]:
        raise SystemExit("Adapter files changed since training; refusing to evaluate")
    torch.set_num_threads(os.cpu_count() or 1)
    base, tokenizer = load_base(args.model_dir)
    model = PeftModel.from_pretrained(base, adapter_dir, is_trainable=False, local_files_only=True)
    train_states, held_out = env.split_states(record["config"]["split_seed"])
    if env.states_sha256(held_out) != record["data"]["held_out_sha256"]:
        raise SystemExit("Held-out states differ from the training record")
    eval_train = random.Random(record["config"]["seed"]).sample(train_states, 64)
    started = time.time()
    results = {"held_out": evaluate(model, tokenizer, held_out),
               "train_sample": evaluate(model, tokenizer, eval_train)}
    record["trained_reloaded"] = {name: summary(metrics) for name, metrics in results.items()}
    record["trained_reloaded_eval_seconds"] = round(time.time() - started, 1)
    record["reload_matches_in_process"] = (
        [row["completion"] for row in results["held_out"]["rows"]]
        == [json.loads(line)["completion"]
            for line in (run / "trained_in_process_held_out.jsonl").read_text().splitlines()])
    record["status"] = "trained_and_reloaded"
    write_rows(run / "trained_held_out.jsonl", results["held_out"]["rows"])
    write_json(run / "RESULT.json", record)
    print("trained (fresh reload)", json.dumps(record["trained_reloaded"]["held_out"]), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    train_parser = commands.add_parser("train")
    train_parser.add_argument("--model-dir", type=Path, required=True)
    train_parser.add_argument("--output-dir", type=Path, required=True)
    train_parser.add_argument("--max-steps", type=int, default=120)
    train_parser.add_argument("--num-generations", type=int, default=8)
    train_parser.add_argument("--prompts-per-step", type=int, default=1)
    train_parser.add_argument("--learning-rate", type=float, default=5e-5)
    train_parser.add_argument("--max-completion-length", type=int, default=64)
    train_parser.add_argument("--easy-fraction", type=float, default=0.4)
    train_parser.add_argument("--seed", type=int, default=2026)
    train_parser.add_argument("--split-seed", type=int, default=2026)
    evaluate_parser = commands.add_parser("evaluate")
    evaluate_parser.add_argument("--model-dir", type=Path, required=True)
    evaluate_parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    for path in (getattr(args, "output_dir", None), getattr(args, "run_dir", None)):
        if path is not None and path.resolve().is_relative_to(REPO):
            parser.error("outputs must live outside the repository")
    train(args) if args.command == "train" else evaluate_saved(args)


if __name__ == "__main__":
    main()
