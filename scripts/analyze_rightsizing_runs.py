"""Recompute the right-sizing GRPO comparisons from the committed evidence files.

    python -m scripts.analyze_rightsizing_runs [--evidence-dir artifacts/evidence/rl-cpu-rightsizing-v1]
    python -m scripts.analyze_rightsizing_runs --gpu

Every number in docs/project/RIGHTSIZING_GRPO_CPU_V1.md and
docs/project/RIGHTSIZING_GRPO_GPU_7B_V1.md that compares arms comes from here. Reads JSON
only; loads no model. The GPU figures are recomputed from the committed per-state rows, never
copied from the summaries the runs themselves wrote.
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
GPU_EVIDENCE = REPO / "artifacts" / "evidence" / "rl-gpu-7b-v1"
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


def _rows_or_none(path: Path) -> list[dict[str, Any]] | None:
    return read_rows(path) if path.exists() else None


def _learning_signal(path: Path) -> dict[str, Any] | None:
    """Where the gradient actually was.

    The reward is bounded above, so once every rollout in a group resolves the advantage is
    zero and the update is zero however many steps remain. That saturation is invisible in
    the collapse watch -- which sees a perfect score -- and plain in the step log.
    """
    rows = _rows_or_none(path)
    if rows is None:
        return None
    moved = [row for row in rows if (row.get("grad_norm") or 0) > 1e-9]
    return {
        "steps_logged": len(rows),
        "steps_with_gradient": len(moved),
        "last_step_with_gradient": max((row["step"] for row in moved), default=None),
        "saturated": bool(rows) and all((row.get("reward_std") or 0) == 0 for row in rows[-10:]),
    }


def _paired_held_out(before: list[dict[str, Any]] | None,
                     after: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The same states before and after, so a gain is a gain on a state and not on an average."""
    if before is None or after is None or len(before) != len(after):
        return None
    lost = sum(1 for x, y in zip(before, after) if x["resolved"] and not y["resolved"])
    gained = sum(1 for x, y in zip(before, after) if not x["resolved"] and y["resolved"])
    return {"n": len(before), "lost": lost, "gained": gained,
            "resolved_before": sum(row["resolved"] for row in before),
            "resolved_after": sum(row["resolved"] for row in after),
            "exact_mcnemar_p": exact_mcnemar(lost, gained)}


def _grpo_runs(phase: Path) -> dict[str, Any]:
    runs = sorted(p.parent for p in phase.glob("*/RESULT.json")) if phase.exists() else []
    out: dict[str, Any] = {}
    for run in runs:
        entry: dict[str, Any] = {"failed": (phase / "FAILED.txt").exists()}
        rollouts = run / "rollouts.jsonl"
        if rollouts.exists():
            blocks, groups = rollout_blocks(read_rows(rollouts))
            entry["groups"] = groups
            entry["blocks_of_10_steps"] = blocks
            entry["collapse_watch"] = _collapse_watch(read_rows(rollouts))
        record = json.loads((run / "RESULT.json").read_text())
        entry["checkpoints"] = sorted(record.get("checkpoints", {}), key=int)
        entry["lora"] = record.get("lora")
        entry["device"] = record["config"].get("device")
        entry["learning_signal"] = _learning_signal(run / "training_log.jsonl")
        # A run that leaves the policy exactly where it found it has trained nothing, however
        # many steps it logged. Three independent signs, each from a file the run wrote.
        start = record.get("init_adapter", {}).get("inventory", {})
        weights = "adapter_model.safetensors"
        rows_before, rows_after = (_rows_or_none(run / name) for name in
                                   ("baseline_held_out.jsonl", "trained_in_process_held_out.jsonl"))
        entry["paired_held_out"] = _paired_held_out(rows_before, rows_after)
        entry["null_run"] = {
            "lora_tensors_changed": (record.get("lora") or {}).get("changed"),
            "adapter_identical_to_start": bool(start) and
                record["adapter_inventory"][weights]["sha256"] == start[weights]["sha256"],
            "held_out_predictions_unchanged": rows_before is not None and rows_before == rows_after,
        }
        entry["trained_nothing"] = (entry["null_run"]["lora_tensors_changed"] == 0
                                    and entry["null_run"]["adapter_identical_to_start"]
                                    and entry["null_run"]["held_out_predictions_unchanged"])
        out[run.name] = entry
    return out


def separation(dev: dict[str, Any], candidates_prefix: str = "step-") -> dict[str, Any]:
    """Can this development set tell the checkpoints apart?

    v1 could not: nine of twelve candidates scored a perfect 64/64, so the tie-break chose
    `step-0030` and the evidence chose nothing. A selection is only as meaningful as the
    spread of the scores it is made from, so that spread is computed and reported beside it
    rather than left for a reader to notice.
    """
    scores = {name: row["solved"] for name, row in dev.items() if name.startswith(candidates_prefix)}
    if not scores:
        return {"candidates": 0, "separates": None}
    best = max(scores.values())
    tied = [name for name, solved in scores.items() if solved == best]
    ceiling = max((row["n"] for row in dev.values()), default=0)
    return {
        "candidates": len(scores),
        "distinct_scores": len(set(scores.values())),
        "best": best,
        "tied_at_best": len(tied),
        "at_ceiling": sum(1 for solved in scores.values() if solved == ceiling),
        "spread": best - min(scores.values()),
        # One candidate strictly ahead of the rest: the scores chose, not the tie-break.
        "separates": len(tied) == 1,
    }


def analyze_gpu_v1(evidence: Path = GPU_EVIDENCE) -> dict[str, Any]:
    """The 7B GPU experiment: warm starts, the GRPO run, selection, and the one confirmation.

    Everything is recomputed from the per-state rows each phase committed. Phases that have
    not run, or that failed, are reported as such rather than silently skipped.
    """
    result: dict[str, Any] = {"checksum_mismatches": verify_checksums(evidence)}

    # Phase A: the SFT warm starts, and the size the fixed rule chose. Earlier attempts keep
    # their own directory, so a failed one stays in the record beside the one that worked.
    arms: dict[str, Any] = {}
    for run in sorted(run for attempt in evidence.glob("phaseA*")
                      for run in attempt.glob("sft-strict-*")):
        entry: dict[str, Any] = {"failed": (run / "FAILED.txt").exists()}
        for name, filename in (("base", "base_held_out.jsonl"), ("sft", "sft_held_out.jsonl")):
            rows = _rows_or_none(run / filename)
            if rows is not None:
                entry[name] = _set_summary(rows)
        record = run / "RESULT.json"
        if record.exists():
            data = json.loads(record.read_text())
            entry["demonstrations"] = data["data"]["demonstrations"]
            entry["excluded_states_overlap"] = data["data"]["excluded_states_overlap"]
            if "sft" in data:
                entry["train_sample"] = data["sft"]["train_sample"]
        arms[f"{run.parent.name}/{run.name}"] = entry
    result["sft_arms"] = arms
    # Each attempt records the rule it read and what it chose; the warm start is the one
    # attempt that chose something, and more than one would mean the record is ambiguous.
    selections = {path.parent.name: json.loads(path.read_text())
                  for path in sorted(evidence.glob("phaseA*/SFT_SELECTION.json"))}
    result["sft_selections"] = selections
    chose = {name: sel["chosen"] for name, sel in selections.items() if sel["chosen"] is not None}
    if len(chose) > 1:
        raise ValueError(f"more than one attempt chose a warm start: {chose}")
    result["sft_start"] = ({"attempt": next(iter(chose)), "demonstrations": next(iter(chose.values()))}
                           if chose else None)

    # Phase B: the GRPO run, and the collapse watch read per ten-step block. phaseB is the
    # null run that trained nothing; phaseB2 is the rerun with generation forced to eval mode.
    # Both are reported -- a failed run stays in the record beside the one that replaced it.
    result["grpo"] = _grpo_runs(evidence / "phaseB")
    result["grpo_rerun"] = _grpo_runs(evidence / "phaseB2")

    # Phase C1: every checkpoint on the development set, and the selection.
    dev_dir = evidence / "phaseC1" / "dev"
    dev = {path.name: _set_summary(read_rows(path / "dev_rows.jsonl"))
           for path in sorted(dev_dir.iterdir())} if dev_dir.exists() else {}
    steps = {name: summary for name, summary in dev.items() if name.startswith("step-")}
    selected = max(sorted(steps), key=lambda name: steps[name]["solved"]) if steps else None
    result["dev"] = dev
    result["selected"] = selected
    result["dev_separation"] = separation(dev)
    result["development_bar_met"] = bool(
        selected and dev[selected]["solved"] >= 58
        and dev[selected]["exact"][0] >= 28 and dev[selected]["inexact"][0] >= 28)

    # Phase C2: the single confirmation run, paired against the policy's own SFT start.
    conf_dir = evidence / "phaseC2" / "confirmation"
    confirmation = {path.name: read_rows(path / "confirmation_rows.jsonl")
                    for path in sorted(conf_dir.iterdir())} if conf_dir.exists() else {}
    result["confirmation"] = {name: _set_summary(rows) for name, rows in confirmation.items()}
    if "sft-start" in confirmation and selected in confirmation:
        pair = paired(confirmation["sft-start"], confirmation[selected])
        solved = sum(row["resolved"] for row in confirmation[selected])
        result["confirmation_paired_vs_start"] = pair
        checkpoint_path = (
            evidence / "phaseB2" / "grpo-strict-balanced-7b-from-sft18"
            / "CHECKPOINT_SHA256.json"
        )
        selected_path = evidence / "phaseC2" / "SELECTED_ADAPTER.json"
        scored_path = conf_dir / selected / "SCORE.json"
        identity_matches = False
        if all(path.is_file() for path in (checkpoint_path, selected_path, scored_path)):
            checkpoints = json.loads(checkpoint_path.read_text())
            selected_adapter = json.loads(selected_path.read_text())
            scored_adapter = json.loads(scored_path.read_text())
            selected_hash = checkpoints.get(selected)
            scored_hash = (
                scored_adapter.get("adapter", {}).get("inventory", {})
                .get("adapter_model.safetensors", {}).get("sha256")
            )
            identity_matches = bool(
                selected_hash
                and selected_adapter.get("selected_checkpoint") == selected
                and selected_hash == selected_adapter.get("member_sha256") == scored_hash
            )
        result["confirmation_adapter_is_the_selected_checkpoint"] = identity_matches
        result["criterion"] = ">=116/128 solved and exact McNemar p < 0.01 against its own SFT start"
        result["criterion_met"] = bool(
            identity_matches and solved >= 116 and pair["exact_mcnemar_p"] < 0.01
        )
    else:
        result["criterion_met"] = None
    result["gate_status"] = ("Exploratory. Not G9 evidence; G9 remains NOT_PASSED/FROZEN and "
                             "no gate changes.")
    return result


def _collapse_watch(rows: list[dict[str, Any]], size: int = 10) -> list[dict[str, Any]]:
    """Solved counts per ten training steps, split by whether the division is exact."""
    blocks = []
    steps = sorted({row["step"] for row in rows})
    for start in range(0, len(steps), size):
        block = [row for row in rows if row["step"] in set(steps[start:start + size])]
        exact = [row for row in block if row["demand"] % row["capacity"] == 0]
        inexact = [row for row in block if row["demand"] % row["capacity"]]
        blocks.append({
            "steps": [steps[start], steps[min(start + size, len(steps)) - 1]],
            "rollouts": len(block),
            "resolved": sum(row["resolved"] for row in block),
            "invalid": sum(row["category"] is not None for row in block),
            "exact_resolved": [sum(row["resolved"] for row in exact), len(exact)],
            "inexact_resolved": [sum(row["resolved"] for row in inexact), len(inexact)]})
    return blocks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--gpu", action="store_true", help="the 7B GPU experiment instead")
    args = parser.parse_args()
    if args.gpu:
        print(json.dumps(analyze_gpu_v1(args.evidence_dir or GPU_EVIDENCE), indent=2))
        return
    evidence = args.evidence_dir or EVIDENCE
    print(json.dumps({"v1": analyze(evidence), "v1.2": analyze_v12(evidence)}, indent=2))


if __name__ == "__main__":
    main()
