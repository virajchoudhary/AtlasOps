"""Recompute the right-sizing CPU GRPO comparisons from the committed evidence files.

    python -m scripts.analyze_rightsizing_runs [--evidence-dir artifacts/evidence/rl-cpu-rightsizing-v1]

Every number in docs/project/RIGHTSIZING_GRPO_CPU_V1.md that compares arms comes from here.
Reads JSON only; loads no model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "artifacts" / "evidence" / "rl-cpu-rightsizing-v1"
ARMS = {
    "base_direct": "sft-direct-64/base_held_out.jsonl",
    "sft_direct_64": "sft-direct-64/sft_held_out.jsonl",
    "grpo_direct": "grpo-direct-from-sft/trained_held_out.jsonl",
    "base_reasoned": "sft-reasoned-16/base_held_out.jsonl",
    "sft_reasoned_64": "sft-reasoned-64/sft_held_out.jsonl",
    "sft_reasoned_8": "sft-reasoned-8/sft_held_out.jsonl",
    "sft_reasoned_16": "sft-reasoned-16/sft_held_out.jsonl",
    "grpo_reasoned": "grpo-reasoned-from-sft16/trained_held_out.jsonl",
    "grpo_reasoned_graded": "grpo-reasoned-graded-from-sft16/trained_held_out.jsonl",
    "grpo_reasoned_continued": "grpo-reasoned-continued/trained_held_out.jsonl",
}
PAIRS = (("base_direct", "sft_direct_64"), ("sft_direct_64", "grpo_direct"),
         ("base_reasoned", "sft_reasoned_16"), ("sft_reasoned_16", "grpo_reasoned"),
         # v1.1 follow-ups, each paired against the run they try to improve on (pre-registered).
         ("grpo_reasoned", "grpo_reasoned_graded"), ("grpo_reasoned", "grpo_reasoned_continued"))
GRPO_RUNS = ("grpo-direct-from-base", "grpo-direct-from-sft", "grpo-reasoned-from-sft16",
             "grpo-reasoned-graded-from-sft16", "grpo-reasoned-continued")
START_POLICIES = (("grpo-direct-from-sft", "sft_direct_64"), ("grpo-reasoned-from-sft16", "sft_reasoned_16"),
                  ("grpo-reasoned-graded-from-sft16", "sft_reasoned_16"),
                  ("grpo-reasoned-continued", "grpo_reasoned"))
RULES = {"ceil": lambda d, c: math.ceil(d / c), "floor": lambda d, c: d // c,
         "nearest": lambda d, c: math.floor(d / c + 0.5)}


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def lines_of(completion: str) -> list[str]:
    return [line for line in completion.strip().splitlines() if line.strip()]


def exact_mcnemar(only_a: int, only_b: int) -> float:
    n, k = only_a + only_b, min(only_a, only_b)
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def arm_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exact = [row for row in rows if row["demand"] % row["capacity"] == 0]
    return {
        "n": len(rows),
        "resolved": sum(row["resolved"] for row in rows),
        "admissible": sum(row["admissible"] for row in rows),
        "mean_reward": round(sum(row["reward"] for row in rows) / len(rows), 4),
        "multi_line": sum(len(lines_of(row["completion"])) > 1 for row in rows),
        "matches_rule": {name: sum(row["action_replicas"] == rule(row["demand"], row["capacity"]) for row in rows)
                         for name, rule in RULES.items()},
        "exact_division_resolved": [sum(row["resolved"] for row in exact), len(exact)],
        "inexact_division_resolved": [sum(row["resolved"] for row in rows) - sum(row["resolved"] for row in exact),
                                      len(rows) - len(exact)],
        "miss_sizes": dict(sorted(_count(row["action_replicas"] - row["needed"]
                                         for row in rows if row["admissible"] and not row["resolved"]).items())),
    }


def _count(values) -> dict[Any, int]:
    counts: dict[Any, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return dict(counts)


def paired(a: list[dict[str, Any]], b: list[dict[str, Any]], inexact_only: bool = False) -> dict[str, Any]:
    if [(r["replicas"], r["demand"], r["capacity"]) for r in a] != [(r["replicas"], r["demand"], r["capacity"]) for r in b]:
        raise ValueError("Arms were not scored on the same states in the same order")
    pairs = [(x, y) for x, y in zip(a, b) if not inexact_only or x["demand"] % x["capacity"]]
    only_a = sum(x["resolved"] and not y["resolved"] for x, y in pairs)
    only_b = sum(y["resolved"] and not x["resolved"] for x, y in pairs)
    return {"lost": only_a, "gained": only_b, "exact_mcnemar_p": round(exact_mcnemar(only_a, only_b), 4)}


def rollout_blocks(rows: list[dict[str, Any]], size: int = 10) -> list[dict[str, Any]]:
    by_step: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_step[row["step"]].append(row)
    steps = sorted(by_step)
    blocks = []
    for start in range(0, len(steps), size):
        block = [row for step in steps[start:start + size] for row in by_step[step]]
        blocks.append({"steps": [steps[start], steps[min(start + size, len(steps)) - 1]], "rollouts": len(block),
                       "resolved": sum(row["resolved"] for row in block),
                       "invalid": sum(row["category"] is not None for row in block),
                       "multi_line": sum(len(lines_of(row["completion"])) > 1 for row in block)})
    groups = [[row["reward"] for row in by_step[step]] for step in steps]
    return blocks, {"groups": len(groups), "varied": sum(len(set(g)) > 1 for g in groups),
                    "all_resolved": sum(len(set(g)) == 1 and g[0] == 1.0 for g in groups),
                    "all_same_unresolved": sum(len(set(g)) == 1 and g[0] != 1.0 for g in groups)}


def verify_checksums(evidence: Path) -> list[str]:
    bad = []
    for line in (evidence / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if hashlib.sha256((evidence / name).read_bytes()).hexdigest() != digest:
            bad.append(name)
    return bad


def analyze(evidence: Path = EVIDENCE) -> dict[str, Any]:
    rows = {name: read_rows(evidence / path) for name, path in ARMS.items()}
    result: dict[str, Any] = {
        "checksum_mismatches": verify_checksums(evidence),
        "arms": {name: arm_summary(arm) for name, arm in rows.items()},
        "paired": {f"{a}->{b}": paired(rows[a], rows[b]) for a, b in PAIRS},
        "paired_inexact_division": {f"{a}->{b}": paired(rows[a], rows[b], inexact_only=True) for a, b in PAIRS},
        "start_policy_reproduced": {
            run: [r["completion"] for r in read_rows(evidence / run / "baseline_held_out.jsonl")]
            == [r["completion"] for r in rows[start]]
            for run, start in START_POLICIES},
        "grpo": {},
    }
    for run in GRPO_RUNS:
        blocks, groups = rollout_blocks(read_rows(evidence / run / "rollouts.jsonl"))
        result["grpo"][run] = {"groups": groups, "blocks_of_10_steps": blocks}
    return result


def _set_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exact = [row for row in rows if row["demand"] % row["capacity"] == 0]
    return {"solved": sum(row["resolved"] for row in rows), "n": len(rows),
            "exact": [sum(row["resolved"] for row in exact), len(exact)],
            "inexact": [sum(row["resolved"] for row in rows) - sum(row["resolved"] for row in exact),
                        len(rows) - len(exact)],
            "valid": sum(row["admissible"] for row in rows)}


def analyze_v12(evidence: Path = EVIDENCE) -> dict[str, Any]:
    """v1.2: development-set checkpoint selection, then the single confirmation run."""
    scores = evidence / "v1.2-scores"
    dev = {path.name: _set_summary(read_rows(path / "dev_rows.jsonl")) for path in sorted((scores / "dev").iterdir())}
    prefix = "grpo-strict-balanced-ckpt-rerun-step-"
    candidates = {name: summary for name, summary in dev.items() if name.startswith(prefix)}
    selected = max(sorted(candidates), key=lambda name: candidates[name]["solved"])  # earliest wins a tie
    target_met = {name: summary["solved"] >= 58 and summary["exact"][0] >= 17 and summary["inexact"][0] >= 38
                  for name, summary in dev.items()}
    confirmation = {path.name: read_rows(path / "confirmation_rows.jsonl")
                    for path in sorted((scores / "confirmation").iterdir())}
    record = json.loads((evidence / "grpo-strict-balanced-ckpt-rerun" / "RESULT.json").read_text())
    confirmed = json.loads((scores / "confirmation" / "h1-step-0030" / "SCORE.json").read_text())
    step = str(int(selected.removeprefix(prefix)))
    paired_start = paired(confirmation["s40-start"], confirmation["h1-step-0030"])
    final = confirmation["h1-step-0030"]
    return {
        "dev": dev, "dev_target_met": target_met, "selected": selected,
        "confirmed_adapter_is_selected_checkpoint": confirmed["adapter"]["inventory"]
        == record["checkpoints"][step]["inventory"] and selected.endswith(confirmed["adapter"]["path"][-4:]),
        "confirmation": {name: _set_summary(rows) for name, rows in confirmation.items()},
        "confirmation_paired_vs_start": paired_start,
        "confirmation_met": sum(row["resolved"] for row in final) >= 116 and paired_start["exact_mcnemar_p"] < 0.01,
        "h1_training": rollout_blocks(read_rows(evidence / "grpo-strict-balanced-ckpt-rerun" / "rollouts.jsonl"))[0],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE)
    evidence = parser.parse_args().evidence_dir
    print(json.dumps({"v1": analyze(evidence), "v1.2": analyze_v12(evidence)}, indent=2))


if __name__ == "__main__":
    main()
