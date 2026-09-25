"""Legacy NON_EMPIRICAL benchmark fixture runner.

Creates deterministic compatibility fixtures. Real model evaluation uses the
dedicated Stage 6/8/9 evaluators; live incidents use the governed Stage 4 harness.

Usage:
  python -m bench.runner --model fixture --mock --adversarial 0

Output:
  bench/results/<run_id>/results_per_episode.jsonl
  bench/results/<run_id>/results_summary.json
  bench/results/<run_id>/comparison_table.md
"""

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from agents.coordinator import handle_incident
from agents.judge import judge_trajectory
from config.runtime import (
    FROZEN_SCENARIOS,
    evaluate_reward_contract,
    bounded_speed_score as _bounded_speed_score,
)
from config.scenario_catalog import SCENARIO_CATALOG
from config.splits import get_split

# Backwards-compatible alias — tests import this name from bench.runner
_evaluate_episode_reward = evaluate_reward_contract


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("runner")

RESULTS_DIR = Path("bench/results")


def apply_chaos(scenario_id: str) -> bool:
    """Legacy API retained for tests; this runner cannot apply live faults."""
    log.error("Legacy benchmark runner cannot apply Chaos; use the governed Stage 4 harness")
    return False


def reset_cluster() -> None:
    """Never perform cluster-wide cleanup from the legacy runner."""
    raise RuntimeError("Legacy benchmark runner cannot clean a cluster; use scoped Stage 4 cleanup")


def wait_for_alert(timeout_s: int = 300) -> dict | None:
    from agents.tools.alertmanager import alertmanager_list_alerts
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = alertmanager_list_alerts(active_only=True)
        if result.get("success") and result.get("count", 0) > 0:
            return {"commonLabels": {"alertname": result["alerts"][0]["alertname"]},
                    "alerts": result["alerts"]}
        time.sleep(20)
    log.warning("no alert fired within %ds — synthesising fallback", timeout_s)
    return {"commonLabels": {"alertname": "BenchmarkTimeout"}, "alerts": [],
            "scenario": "unknown", "synthetic": True}


async def run_scenario(scenario_id: str, mock: bool = False) -> dict:
    t0 = time.time()
    tier = scenario_id.split("/", 1)[0] if "/" in scenario_id else "unknown"
    is_mock = mock

    if is_mock:
        meta = SCENARIO_CATALOG.get(scenario_id)
        expected_alert = meta.expected_alert if meta else f"Alert-{scenario_id}"
        expected_root_cause = meta.expected_root_cause if meta else f"Root cause for {scenario_id}"
        incident = {
            "triage": {
                "final": {
                    "severity": "P1",
                    "impact": "Service degradation",
                    "affected_services": list(meta.target_services if meta else ["frontend"]),
                },
                "trajectory": [{"role": "triage", "content": f"Alert {expected_alert} triaged as P1"}],
            },
            "diagnosis": {
                "final": {"root_cause": expected_root_cause, "confidence": 0.85},
                "trajectory": [{"role": "diagnosis", "content": f"Investigated services, identified: {expected_root_cause}"}],
            },
            "remediation": {
                "final": {
                    "outcome": "unresolved",
                    "actions_taken": [
                        {"action": "restart_pod", "target": meta.target_services[0] if meta and meta.target_services else "frontend"}
                    ],
                },
                "trajectory": [{"role": "remediation", "content": "Attempted unfinetuned baseline remediation"}],
            },
            "comms": {
                "final": {"postmortem_path": None},
                "trajectory": [{"role": "comms", "content": "Broadcasted incident update to stakeholders"}],
            },
            "verification": {
                "env_resolved": False,
                "agent_claimed_resolved": False,
                "chaos_mesh_cleared": False,
                "workload_checks": {
                    wl: {"healthy": False} for wl in (meta.verification_workloads if meta else ["frontend"])
                },
            },
        }
        judge_score = {
            "correctness": 0.5,
            "efficiency": 0.5,
            "reasoning": 0.5,
            "red_herring_handling": 0.5,
            "overall": 0.5,
            "critique": "mock_baseline_judge",
        }
        episode = {
            "scenario_id": scenario_id,
            "tier": tier,
            "status": "ok",
            "evaluation_mode": "mock",
            "non_empirical": True,
            "outcome": "unresolved",
            "agent_claimed_resolved": False,
            "env_resolved": False,
            "resolved": False,
            "verification": incident["verification"],
            "time_to_resolve_s": 45.0,
            "severity": "P1",
            "total_turns": 4,
            "judge": judge_score,
            "postmortem_path": None,
        }
        episode["reward_contract"] = evaluate_reward_contract(episode)
        return episode

    ok = apply_chaos(scenario_id)
    if not ok:
        return {"scenario_id": scenario_id, "status": "skip", "error": "manifest_apply_failed"}

    alert = wait_for_alert()
    alert["scenario_id"] = scenario_id

    try:
        incident = await handle_incident(alert)
        judge_score = await judge_trajectory(incident, tier=tier)
    except Exception as e:
        log.exception("scenario %s failed: %s", scenario_id, e)
        reset_cluster()
        return {"scenario_id": scenario_id, "status": "error", "error": str(e)}

    reset_cluster()

    remediation = incident.get("remediation", {}).get("final", {})
    triage = incident.get("triage", {}).get("final", {})
    verification = incident.get("verification", {})
    agent_claimed_resolved = bool(
        verification.get(
            "agent_claimed_resolved",
            remediation.get("outcome") == "resolved" or remediation.get("status") == "resolved",
        )
    )
    # Fail-closed benchmark truth: env_resolved must come strictly from objective verifier evidence
    env_resolved = bool(verification.get("env_resolved", False))
    total_turns = sum(
        len(incident.get(role, {}).get("trajectory", []))
        for role in ("triage", "diagnosis", "remediation", "comms")
    )

    episode = {
        "scenario_id": scenario_id,
        "tier": tier,
        "status": "ok",
        "outcome": remediation.get("outcome", "unknown"),
        "agent_claimed_resolved": agent_claimed_resolved,
        "env_resolved": env_resolved,
        "resolved": env_resolved and incident.get("resolved", True) is True,
        "verification": verification,
        "time_to_resolve_s": remediation.get("time_to_resolve_seconds", round(time.time() - t0)),
        "severity": triage.get("severity", "unknown"),
        "total_turns": total_turns,
        "judge": judge_score,
        "postmortem_path": incident.get("comms", {}).get("final", {}).get("postmortem_path"),
    }
    # Keep reward evaluation centralized so train/eval/bench cannot drift.
    episode["reward_contract"] = evaluate_reward_contract(episode)
    return episode


def compute_summary(results: list[dict], tag: str, model: str) -> dict:
    valid = [r for r in results if r.get("status") == "ok"]
    resolved = [r for r in valid if r.get("resolved")]
    cascades = [r for r in valid if r.get("tier") == "cascade"]
    replays = [r for r in valid if r.get("tier") == "named_replays"]

    def mean(xs, key, default=0.0):
        vals = [x.get(key, default) for x in xs if x.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    judge_scores = [r.get("judge", {}).get("overall", 0) for r in valid if r.get("judge")]
    contract_scores = [r.get("reward_contract", {}).get("total", 0) for r in valid]
    penalties = [r.get("reward_contract", {}).get("penalty_total", 0) for r in valid]

    per_tier = {}
    tiers = sorted({r.get("tier", "unknown") for r in valid})
    for tier in tiers:
        trows = [r for r in valid if r.get("tier") == tier]
        t_resolved = [r for r in trows if r.get("resolved")]
        per_tier[tier] = {
            "count": len(trows),
            "resolution_rate": round(len(t_resolved) / max(len(trows), 1), 3),
            "avg_time_to_resolve_s": mean(trows, "time_to_resolve_s"),
            "avg_reward_contract": round(
                sum(r.get("reward_contract", {}).get("total", 0) for r in trows) / max(len(trows), 1), 3
            ),
        }

    unsafe_action_count = sum(
        1 for r in valid if r.get("reward_contract", {}).get("penalties", {}).get("unsafe_shortcut", 0) > 0
    )
    false_resolution_count = sum(
        1 for r in valid if r.get("reward_contract", {}).get("penalties", {}).get("false_resolution", 0) > 0
    )
    hallucinated_evidence_count = sum(
        1
        for r in valid
        if r.get("reward_contract", {}).get("penalties", {}).get("hallucinated_evidence", 0) > 0
    )

    return {
        "tag": tag,
        "model": model,
        "run_date": datetime.now(timezone.utc).isoformat(),
        "total_scenarios": len(results),
        "resolution_rate": round(len(resolved) / max(len(valid), 1), 3),
        "avg_reward": round(sum(judge_scores) / len(judge_scores), 3) if judge_scores else None,
        "judged_episode_count": len(judge_scores),
        "avg_reward_contract": round(sum(contract_scores) / max(len(contract_scores), 1), 3),
        "avg_penalty": round(sum(penalties) / max(len(penalties), 1), 3),
        "avg_turns": mean(valid, "total_turns"),
        "avg_time_to_resolve_s": mean(valid, "time_to_resolve_s"),
        "cascade_resolution_rate": round(
            len([r for r in cascades if r.get("resolved")]) / max(len(cascades), 1), 3
        ),
        "named_replay_resolution_rate": round(
            len([r for r in replays if r.get("resolved")]) / max(len(replays), 1), 3
        ),
        "unsafe_action_count": unsafe_action_count,
        "false_resolution_count": false_resolution_count,
        "hallucinated_evidence_count": hallucinated_evidence_count,
        "per_tier": per_tier,
    }


def write_comparison_table(summary: dict, output_dir: Path) -> None:
    """Write one run's non-empirical table inside its unique output directory."""
    table_path = output_dir / "comparison_table.md"
    existing_runs = [summary]
    existing_runs.sort(key=lambda x: x.get("run_date", ""))

    header = (
        "| Tag | Model | Resolution | Reward (Judge) | Reward (Contract) | Avg Penalty | Avg Turns "
        "| Cascade Res. | Replay Res. | Date |\n"
    )
    header += "|---|---|---|---|---|---|---|---|---|---|\n"
    rows = ""
    for r in existing_runs:
        judge_mean = f"{r['avg_reward']:.3f}" if r.get("avg_reward") is not None else "n/a"
        rows += (
            f"| {r['tag']} | `{Path(r['model']).name}` "
            f"| {r['resolution_rate']:.0%} "
            f"| {judge_mean} "
            f"| {r.get('avg_reward_contract', 0):.3f} "
            f"| {r.get('avg_penalty', 0):.3f} "
            f"| {r['avg_turns']:.1f} "
            f"| {r['cascade_resolution_rate']:.0%} "
            f"| {r['named_replay_resolution_rate']:.0%} "
            f"| {r['run_date'][:10]} |\n"
        )
    per_tier_lines = ["\n## Per-tier Breakdown\n"]
    for r in existing_runs:
        per_tier_lines.append(f"\n### {r['tag']}\n")
        per_tier_lines.append("| Tier | Count | Resolution | Avg TTR (s) | Avg Contract Reward |\n")
        per_tier_lines.append("|---|---|---|---|---|\n")
        for tier, item in sorted((r.get("per_tier") or {}).items()):
            per_tier_lines.append(
                f"| {tier} | {item.get('count', 0)} | {item.get('resolution_rate', 0):.0%} "
                f"| {item.get('avg_time_to_resolve_s', 0):.1f} | {item.get('avg_reward_contract', 0):.3f} |\n"
            )
        per_tier_lines.append(
            f"\n- unsafe actions: `{r.get('unsafe_action_count', 0)}`"
            f", false resolutions: `{r.get('false_resolution_count', 0)}`"
            f", hallucinated evidence: `{r.get('hallucinated_evidence_count', 0)}`\n"
        )

    table_path.write_text(
        f"# AtlasOps — NON_EMPIRICAL Benchmark Fixtures\n\n{header}{rows}{''.join(per_tier_lines)}",
        encoding="utf-8",
    )
    log.info("comparison table updated: %s", table_path)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Model path or HF ID")
    parser.add_argument("--tag", default="", help="Run label (e.g. grpo_v3, baseline_v2)")
    parser.add_argument("--split", choices=["train", "val", "test", "leaderboard", "all"], default=None,
                        help="Benchmark split to evaluate (from config.splits)")
    parser.add_argument("--scenarios", nargs="*", help="Override scenario list")
    parser.add_argument("--mock", action="store_true", help="Run in mock/offline baseline replay mode")
    parser.add_argument("--output", default="", help="Override output dir")
    parser.add_argument("--adversarial", type=int, default=0,
                        help="Legacy option; only 0 is supported by this mock-only runner")
    args = parser.parse_args()

    if not args.mock:
        raise RuntimeError(
            "Legacy benchmark runner is mock-only; use the dedicated real evaluators "
            "or the governed Stage 4 harness"
        )
    if args.adversarial != 0:
        raise ValueError("Dynamic adversarial generation is unavailable in the mock-only runner")
    os.environ["AGENT_MODEL"] = args.model

    tag = args.tag or f"run-{int(time.time())}"
    run_id = f"{tag}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    out_dir = Path(args.output) if args.output else (RESULTS_DIR / run_id)
    out_dir.mkdir(parents=True, exist_ok=False)

    if args.split:
        if args.split == "all":
            scenarios = list(FROZEN_SCENARIOS)
        else:
            scenarios = list(get_split(args.split))
    elif args.scenarios:
        scenarios = list(args.scenarios)
    else:
        scenarios = list(FROZEN_SCENARIOS)

    log.info("running %d scenarios for tag=%s model=%s (mock=%s)", len(scenarios), tag, args.model, args.mock)

    results = []
    episodes_file = out_dir / "results_per_episode.jsonl"
    with episodes_file.open("x", encoding="utf-8", newline="\n") as f:
        for i, s in enumerate(scenarios, 1):
            log.info("[%d/%d] %s", i, len(scenarios), s)
            r = await run_scenario(s, mock=args.mock)
            results.append(r)
            f.write(json.dumps(r) + "\n")
            f.flush()

    summary = compute_summary(results, tag, args.model)
    summary["evaluation_mode"] = "mock"
    summary["non_empirical"] = True
    (out_dir / "results_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_comparison_table(summary, out_dir)

    log.info("=== Benchmark complete ===")
    log.info("  Resolution rate : %.1f%%", summary["resolution_rate"] * 100)
    log.info(
        "  Avg reward      : %s",
        f"{summary['avg_reward']:.3f}" if summary["avg_reward"] is not None else "n/a",
    )
    log.info("  Results         : %s", out_dir)


if __name__ == "__main__":
    asyncio.run(main())
