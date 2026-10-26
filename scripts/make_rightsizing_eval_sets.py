"""Draw fresh development and confirmation sets for the right-sizing experiment (v1.2).

Both sets come from Train states that no earlier run touched: not a GRPO prompt (every
rollouts.jsonl in the evidence directory, plus the uncommitted smoke run's four prompts), not an
SFT demonstration and not a Train evaluation sample. The original 64 held-out states
were scored by every arm, so v1.2 decides on the development set and confirms once on
the confirmation set. Neither set may become a training prompt (`train --exclude-states`).

    python -m scripts.make_rightsizing_eval_sets --output artifacts/evidence/rl-cpu-rightsizing-v1/eval-sets-v1.2.json
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = make_sets()
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in data.items() if key != "sets"}, indent=2))


if __name__ == "__main__":
    main()
