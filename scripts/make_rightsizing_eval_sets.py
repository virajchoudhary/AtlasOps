"""Draw fresh development and confirmation sets for the right-sizing experiment.

Both sets come from Train states that no earlier run touched: not a GRPO prompt (every
rollouts.jsonl in the evidence directory, plus the uncommitted smoke run's four prompts), not an
SFT demonstration and not a Train evaluation sample. The original 64 held-out states
were scored by every arm, so v1.2 decides on the development set and confirms once on
the confirmation set. Neither set may become a training prompt (`train --exclude-states`).

    python -m scripts.make_rightsizing_eval_sets --output artifacts/evidence/rl-cpu-rightsizing-v1/eval-sets-v1.2.json

v1.3 does the same for the GPU 7B experiment, from what v1.2 and every v1.2-era run left
untouched. Its development set is balanced between exact and inexact division, because the
pre-registered development bar is read per category; the confirmation set then takes every
remaining exact-division state. v1.2 above is frozen and is reproduced unchanged.

    python -m scripts.make_rightsizing_eval_sets --version v1.3 \
        --output artifacts/evidence/rl-gpu-7b-v1/eval-sets-v1.3.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from training import rightsizing_env as env

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "artifacts" / "evidence" / "rl-cpu-rightsizing-v1"
SEED = 31415
DEV_SIZE, CONFIRMATION_SIZE = 64, 128
V13_SEED = 27182
# The development set is read per category, so it is drawn half and half. The pool holds only
# 74 untouched exact-division states, so a half-and-half confirmation set is impossible; it
# takes every exact-division state the development set leaves.
V13_DEV_EXACT, V13_DEV_INEXACT = 32, 32
V13_CONFIRMATION_EXACT, V13_CONFIRMATION_INEXACT = 42, 86
# Every GRPO run before v1.2; later runs must not change the sets.
PRIOR_GRPO_RUNS = ("grpo-direct-from-base", "grpo-direct-from-sft", "grpo-reasoned-from-sft16",
                   "grpo-reasoned-graded-from-sft16", "grpo-reasoned-continued")
# The plumbing smoke run (two steps, dirty tree, not in the evidence) trained on these prompts.
SMOKE_PROMPTS = (env.CapacityState(2, 43, 4), env.CapacityState(2, 33, 3),
                 env.CapacityState(3, 15, 2), env.CapacityState(8, 34, 3))


def touched_states(evidence: Path = EVIDENCE) -> dict[str, set[env.CapacityState]]:
    train, _ = env.split_states(2026)
    sources: dict[str, set[env.CapacityState]] = {}
    for run in PRIOR_GRPO_RUNS:
        path = evidence / run / "rollouts.jsonl"
        sources[f"prompts:{run}"] = {
            env.CapacityState(row["replicas"], row["demand"], row["capacity"])
            for row in map(json.loads, path.read_text().splitlines())}
    sources["prompts:smoke"] = set(SMOKE_PROMPTS)
    for examples in (64, 8, 16):  # every SFT run used --seed 2026, so demos come from Random(2027)
        sources[f"sft_demos:{examples}"] = set(random.Random(2027).sample(train, examples))
    for seed in (2026, 2027):
        sources[f"train_eval_sample:{seed}"] = set(random.Random(seed).sample(train, 64))
    return sources


def make_sets(evidence: Path = EVIDENCE) -> dict:
    train, held_out = env.split_states(2026)
    sources = touched_states(evidence)
    touched = set().union(*sources.values())
    untouched = [state for state in train if state not in touched]
    drawn = random.Random(SEED).sample(untouched, DEV_SIZE + CONFIRMATION_SIZE)
    sets = {"dev": drawn[:DEV_SIZE], "confirmation": drawn[DEV_SIZE:]}
    assert not set(drawn) & set(held_out) and not set(drawn) & touched
    return {
        "version": "v1.2", "seed": SEED, "train_states": len(train), "touched_train_states": len(touched),
        "touched_by_source": {name: len(states) for name, states in sorted(sources.items())},
        "untouched_train_states": len(untouched),
        "sets": {name: [{"replicas": s.replicas, "demand": s.demand, "capacity_per_replica": s.capacity_per_replica}
                        for s in states] for name, states in sets.items()},
        "sha256": {name: env.states_sha256(states) for name, states in sets.items()},
        "exact_division": {name: sum(s.demand % s.capacity_per_replica == 0 for s in states)
                           for name, states in sets.items()},
    }


def exact_division(state: env.CapacityState) -> bool:
    return state.demand % state.capacity_per_replica == 0


def v13_touched_states(evidence: Path = EVIDENCE) -> dict[str, set[env.CapacityState]]:
    """Every Train state any recorded run has seen, including the whole v1.2 era.

    Each run directory under the evidence folder is read from its own ``RESULT.json``: GRPO
    prompts come from ``rollouts.jsonl``, and SFT demonstrations are re-drawn from the recorded
    configuration and checked against the ``demonstrations_sha256`` the run wrote down, so a
    silent drift in the draw cannot quietly shrink the pool.
    """
    train, _ = env.split_states(2026)
    sources: dict[str, set[env.CapacityState]] = {"prompts:smoke": set(SMOKE_PROMPTS)}
    v12 = json.loads((evidence / "eval-sets-v1.2.json").read_text())
    v12_states = {env.CapacityState(**state) for group in v12["sets"].values() for state in group}
    sources["eval_sets:v1.2"] = v12_states
    for run in sorted(path for path in evidence.iterdir() if (path / "RESULT.json").exists()):
        record = json.loads((run / "RESULT.json").read_text())
        config, data = record.get("config", {}), record.get("data", {})
        rollouts = run / "rollouts.jsonl"
        if rollouts.exists():
            sources[f"prompts:{run.name}"] = {
                env.CapacityState(row["replicas"], row["demand"], row["capacity"])
                for row in map(json.loads, rollouts.read_text().splitlines())}
        if record.get("phase") == "sft":
            excluded = v12_states if config.get("excluded_from_demonstrations") else set()
            pool = [state for state in train if state not in excluded]
            demos = random.Random(config["seed"] + 1).sample(pool, config["examples"])
            if env.states_sha256(demos) != data["demonstrations_sha256"]:
                raise SystemExit(f"{run.name}: re-drawn demonstrations do not match the recorded digest")
            sources[f"sft_demos:{run.name}"] = set(demos)
        for seed in sorted({config.get("seed"), config.get("split_seed")} - {None}):
            sources[f"train_eval_sample:{seed}"] = set(random.Random(seed).sample(train, 64))
    return sources


def make_v13_sets(evidence: Path = EVIDENCE) -> dict:
    train, held_out = env.split_states(2026)
    sources = v13_touched_states(evidence)
    touched = set().union(*sources.values())
    untouched = [state for state in train if state not in touched]
    exact = [state for state in untouched if exact_division(state)]
    inexact = [state for state in untouched if not exact_division(state)]
    draw = random.Random(V13_SEED)
    dev = draw.sample(exact, V13_DEV_EXACT) + draw.sample(inexact, V13_DEV_INEXACT)
    rest_exact = [state for state in exact if state not in set(dev)]
    rest_inexact = [state for state in inexact if state not in set(dev)]
    confirmation = (draw.sample(rest_exact, V13_CONFIRMATION_EXACT)
                    + draw.sample(rest_inexact, V13_CONFIRMATION_INEXACT))
    sets = {"dev": dev, "confirmation": confirmation}
    drawn = set(dev) | set(confirmation)
    assert len(dev) == DEV_SIZE and len(confirmation) == CONFIRMATION_SIZE
    assert len(drawn) == DEV_SIZE + CONFIRMATION_SIZE
    assert not drawn & set(held_out) and not drawn & touched
    return {
        "version": "v1.3", "seed": V13_SEED, "train_states": len(train),
        "touched_train_states": len(touched),
        "touched_by_source": {name: len(states) for name, states in sorted(sources.items())},
        "untouched_train_states": len(untouched),
        "stratification": {"dev": {"exact": V13_DEV_EXACT, "inexact": V13_DEV_INEXACT},
                           "confirmation": {"exact": V13_CONFIRMATION_EXACT,
                                            "inexact": V13_CONFIRMATION_INEXACT}},
        "sets": {name: [{"replicas": s.replicas, "demand": s.demand, "capacity_per_replica": s.capacity_per_replica}
                        for s in states] for name, states in sets.items()},
        "sha256": {name: env.states_sha256(states) for name, states in sets.items()},
        "exact_division": {name: sum(exact_division(state) for state in states)
                           for name, states in sets.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", choices=("v1.2", "v1.3"), default="v1.2")
    args = parser.parse_args()
    data = make_sets() if args.version == "v1.2" else make_v13_sets()
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in data.items() if key != "sets"}, indent=2))


if __name__ == "__main__":
    main()
