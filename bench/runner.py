"""AtlasOps zero-shot benchmark runner.

New runs are selected from an active G5 frozen split. Dynamic adversarial
manifests remain available only in an explicit exploration mode and never mix
with validation or final-test populations.

Usage:
  python -m bench.runner --model MODEL --split-role validation
  python -m bench.runner --model MODEL --split-role final_test --allow-final-test

Output:
  bench/results/<run_id>/results_per_episode.jsonl
  bench/results/<run_id>/results_summary.json
  bench/results/comparison_table.md  (updates in place across runs)
"""

import argparse
import asyncio
import json
import logging
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone

from agents.adversarial_designer import design_batch
from agents.coordinator import handle_incident
from agents.judge import judge_trajectory
from bench.scenario_contract import allowed_scenario_ids, canonical_json, sha256_file, sha256_object
from bench.g6_evidence import append_raw_record, build_raw_record, build_run_manifest, compute_g6_metrics
from config.runtime import bounded_speed_score, evaluate_reward_contract

# Backwards-compatible alias — tests import this name from bench.runner
_bounded_speed_score = bounded_speed_score
_evaluate_episode_reward = evaluate_reward_contract


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("runner")

RESULTS_DIR = Path("bench/results")
MANIFESTS_DIR = Path("bench/chaos_manifests")

# Raw-run schema marker.  Existing historical files intentionally retain their
# old shape; this version applies to newly created zero-shot runs only.
RESULT_SCHEMA_VERSION = "atlasops.g6.zero-shot-result/v2"
_INVESTIGATIVE_TOOLS = frozenset({
    "alertmanager_list_alerts", "cloud_monitoring_query", "gcloud_logs_read",
    "jaeger_get_trace", "jaeger_search", "kubectl_describe", "kubectl_logs",
    "promql_query", "promql_query_range",
})
_MUTATING_TOOLS = frozenset({
    "alertmanager_silence", "argocd_rollback", "chaos_stop_experiment",
    "kubectl_rollout", "kubectl_scale",
})
_FAULT_CLASS_KEYWORDS = {
    "Application:argo_scale_to_zero": {"scale", "replicas"},
    "Deployment:deploy_legacy_replicas": {"deployment", "legacy"},
    "DNSChaos": {"dns"},
    "IOChaos": {"disk", "io"},
    "NetworkChaos:corrupt": {"corrupt"},
    "NetworkChaos:duplicate": {"duplicate"},
    "NetworkChaos:loss": {"loss", "packet"},
    "NetworkChaos:partition": {"partition"},
    "NetworkChaos:delay": {"delay", "latency"},
    "PodChaos": {"crash", "kill", "oom"},
    "StressChaos:cpu": {"cpu"},
    "StressChaos:memory": {"memory", "oom"},
    "TimeChaos": {"clock", "time"},
}
_CATALOG_ENTRY_CACHE: dict[str, dict] | None = None


def load_catalog_entry(scenario_id: str) -> dict | None:
    """Load immutable fault metadata; dynamic scenarios have no catalogue entry."""
    global _CATALOG_ENTRY_CACHE
    if _CATALOG_ENTRY_CACHE is None:
        from bench.scenario_contract import build_catalog, catalog_entries

        _CATALOG_ENTRY_CACHE = catalog_entries(build_catalog())
    return _CATALOG_ENTRY_CACHE.get(scenario_id)


def _model_visible_alert(alert: dict) -> dict:
    """Remove evaluation-only identifiers before the alert reaches an agent."""
    model_alert = json.loads(json.dumps(alert))
    model_alert.pop("scenario_id", None)
    return model_alert


def _normalise_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.lower().replace("_", " ").replace("-", " ")
    try:
        return _normalise_text(json.dumps(value, sort_keys=True))
    except (TypeError, ValueError):
        return str(value).lower()


def evaluate_root_cause(incident: dict, faults: list[dict]) -> dict:
    """Score deterministic target/fault-class coverage in the diagnosis output."""
    diagnosis = incident.get("diagnosis", {}).get("final", {})
    haystack = _normalise_text(diagnosis)
    matched_faults = []
    target_hits = 0
    class_hits = 0
    for fault in faults:
        target = str((fault.get("targets") or [""])[0])
        action = str(fault.get("action", ""))
        kind = str(fault.get("kind", ""))
        key = f"{kind}:{action}"
        keywords = set(
            _FAULT_CLASS_KEYWORDS.get(key, set())
            or _FAULT_CLASS_KEYWORDS.get(kind, set())
        )
        if kind == "StressChaos":
            stressors = (fault.get("parameters", {}) or {}).get("stressors", {}) or {}
            if "cpu" in stressors:
                keywords.update(_FAULT_CLASS_KEYWORDS["StressChaos:cpu"])
            if "memory" in stressors:
                keywords.update(_FAULT_CLASS_KEYWORDS["StressChaos:memory"])
        target_hit = bool(target and target.lower() in haystack)
        class_hit = any(keyword in haystack for keyword in keywords)
        target_hits += int(target_hit)
        class_hits += int(class_hit)
        if target_hit and class_hit:
            matched_faults.append(f"{kind}:{action}")
    fault_count = len(faults)
    return {
        "expected_fault_count": fault_count,
        "matched_fault_count": len(matched_faults),
        "matched_faults": sorted(matched_faults),
        "score": round(len(matched_faults) / fault_count, 4) if fault_count else 0.0,
        "target_coverage": round(target_hits / fault_count, 4) if fault_count else 0.0,
        "fault_class_coverage": round(class_hits / fault_count, 4) if fault_count else 0.0,
        "correct": bool(fault_count and len(matched_faults) == fault_count),
    }


def tool_metrics(incident: dict) -> dict:
    counts = {
        "attempts": 0,
        "blocked_by_circuit_breaker": 0,
        "blocked_by_policy": 0,
        "cap_blocked": 0,
        "dedup_blocked": 0,
        "executed_failures": 0,
        "executed_successes": 0,
        "invalid_arguments": 0,
        "mutating_attempts": 0,
        "successful_investigations": 0,
    }
    first_mutation_index: int | None = None
    successful_investigations_before_first_mutation = 0
    flat_index = 0
    for role in ("triage", "diagnosis", "remediation", "comms"):
        for entry in incident.get(role, {}).get("trajectory", []):
            if not isinstance(entry, dict) or "tool" not in entry:
                continue
            flat_index += 1
            counts["attempts"] += 1
            name = str(entry.get("tool", ""))
            output = entry.get("output", {}) or {}
            success = bool(output.get("success", False))
            execution_blocked = any(
                entry.get(flag)
                for flag in (
                    "blocked_by_policy",
                    "blocked_by_circuit_breaker",
                    "cap_blocked",
                    "dedup_blocked",
                )
            )
            if entry.get("blocked_by_policy"):
                counts["blocked_by_policy"] += 1
                counts["invalid_arguments"] += 1 if "arguments" in str(output.get("error", "")).lower() else 0
            elif entry.get("blocked_by_circuit_breaker"):
                counts["blocked_by_circuit_breaker"] += 1
            elif entry.get("cap_blocked"):
                counts["cap_blocked"] += 1
            elif entry.get("dedup_blocked"):
                counts["dedup_blocked"] += 1
            elif success:
                counts["executed_successes"] += 1
                if name in _INVESTIGATIVE_TOOLS:
                    counts["successful_investigations"] += 1
                    if first_mutation_index is None or flat_index < first_mutation_index:
                        successful_investigations_before_first_mutation += 1
            else:
                error = str(output.get("error", "")).lower()
                if "arguments" in error:
                    counts["invalid_arguments"] += 1
                else:
                    counts["executed_failures"] += 1

            if not execution_blocked and name in _MUTATING_TOOLS:
                counts["mutating_attempts"] += 1
                if first_mutation_index is None:
                    first_mutation_index = flat_index

    counts["successful_investigations_before_first_mutation"] = (
        successful_investigations_before_first_mutation
    )
    counts["pre_action_evidence"] = bool(
        counts["mutating_attempts"] == 0
        or successful_investigations_before_first_mutation > 0
    )
    return counts


def failure_reasons(episode: dict) -> list[str]:
    reasons: list[str] = []
    status = episode.get("status")
    if status == "skip":
        reasons.append(str(episode.get("error", "scenario_skipped")))
    elif status == "error":
        reasons.append("agent_exception")
    verification = episode.get("verification", {}) or {}
    verification_status = str(verification.get("verification_status", ""))
    if verification_status in {"failed", "inconclusive", "error"}:
        reasons.append(f"verification_{verification_status}")
    penalties = (episode.get("reward_contract", {}) or {}).get("penalties", {}) or {}
    reasons.extend(name for name, value in penalties.items() if float(value or 0) > 0)
    root_cause = episode.get("root_cause_evaluation", {}) or {}
    if root_cause and root_cause.get("correct") is False:
        reasons.append("root_cause_incomplete")
    if episode.get("alert_was_synthetic_timeout"):
        reasons.append("alert_timeout_fallback")
    approval = episode.get("approval", {}) or {}
    mode = str(approval.get("mode", "")).lower()
    decision = str(approval.get("decision", "")).lower()
    if mode == "manual":
        reasons.append("manual_approval_runbook")
    if decision in {"rejected", "timeout"}:
        reasons.append(f"approval_{decision}")
    return sorted(set(reasons))


def prepare_output_directory(out_dir: Path) -> list[dict]:
    """Open a run fail-closed; return completed episodes when resuming."""
    out_dir.mkdir(parents=True, exist_ok=True)
    episodes_path = out_dir / "results_per_episode.jsonl"
    if (out_dir / ".run_complete.json").exists() or (out_dir / "results_summary.json").exists():
        raise RuntimeError(f"refusing to mutate completed raw run: {out_dir}")
    episodes: list[dict] = []
    if episodes_path.exists():
        try:
            with episodes_path.open("r", encoding="utf-8") as handle:
                episodes = [json.loads(line) for line in handle if line.strip()]
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"raw episode log is truncated/invalid; refusing resume: {exc}") from exc
        contaminated = [
            str(episode.get("scenario_id", "")) for episode in episodes if episode.get("reset_failure") is True
        ]
        if contaminated:
            raise RuntimeError(
                "prior cluster reset failed for scenarios; refusing resume to avoid environment contamination: "
                + ", ".join(contaminated)
            )
    return episodes


def append_episode(out_dir: Path, episode: dict) -> None:
    with (out_dir / "results_per_episode.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(episode, sort_keys=True) + "\n")
        handle.flush()


def write_run_manifest(path: Path, manifest: dict) -> None:
    if path.exists():
        raise RuntimeError(f"run manifest already exists: {path}")
    write_json_atomic(path, manifest)


def read_run_manifest(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read immutable run manifest: {exc}") from exc


def validate_resume_manifest(
    stored: dict,
    *,
    scenario_ids: list[str],
    config_hash: str,
    catalog_sha256: str,
    frozen_split_sha256: str,
    contracts: dict,
) -> None:
    if stored.get("scenario_ids") != scenario_ids:
        raise RuntimeError("resume scenario sequence differs from immutable run manifest")
    if stored.get("config_sha256") != config_hash:
        raise RuntimeError("resume configuration differs from immutable run manifest")
    if stored.get("catalog_sha256") != catalog_sha256:
        raise RuntimeError("resume catalogue differs from immutable run manifest")
    if stored.get("frozen_split_sha256") != frozen_split_sha256:
        raise RuntimeError("resume frozen split differs from immutable run manifest")
    if stored.get("role_and_verifier_contracts") != contracts:
        raise RuntimeError("resume prompt/tool/verifier contract differs from immutable run manifest")


def validate_resume_raw_records(out_dir: Path, episode_count: int) -> None:
    raw_path = out_dir / "raw_records.jsonl"
    if episode_count == 0 and not raw_path.exists():
        return
    try:
        with raw_path.open("r", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"raw-record log is missing/truncated; refusing resume: {exc}") from exc
    if len(records) != episode_count:
        raise RuntimeError("raw-record count does not match completed episodes; refusing resume")


def ensure_environment_safe_for_next_episode(episode: dict) -> None:
    if episode.get("reset_failure") is True:
        raise RuntimeError(
            f"cluster reset failed after {episode.get('scenario_id')}; "
            "stopping to prevent environment contamination"
        )


def finalize_raw_run(
    out_dir: Path,
    summary: dict,
    *,
    episode_count: int,
) -> None:
    marker = {
        "complete_at": datetime.now(timezone.utc).isoformat(),
        "episode_sha256": sha256_file(out_dir / "results_per_episode.jsonl"),
        "episode_count": episode_count,
        "summary_sha256": sha256_object(summary),
    }
    write_json_atomic(out_dir / ".run_complete.json", marker)


def git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
        return result.stdout.strip() or None
    except OSError:
        return None


def configuration_provenance(args: argparse.Namespace) -> tuple[dict, str]:
    values = vars(args).copy()
    values.pop("handler", None)
    config = {
        "arguments": values,
        "git_commit": git_commit(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "runtime_environment": {
            key: ("set" if os.environ.get(key) else "unset")
            for key in (
                "AGENT_MODEL",
                "ATLASOPS_LIVE_JUDGE",
                "BACKEND",
                "JUDGE_MODEL",
                "OPENAI_API_BASE",
            )
        },
    }
    return config, sha256_object(config)


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(canonical_json(value) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_chaos(scenario_id: str) -> bool:
    manifest = MANIFESTS_DIR / f"{scenario_id}.yaml"
    if not manifest.exists():
        log.error("manifest not found: %s", manifest)
        return False
    r = subprocess.run(["kubectl", "apply", "-f", str(manifest)], capture_output=True, text=True)
    return r.returncode == 0


def reset_cluster() -> bool:
    chaos_reset = subprocess.run(
        ["kubectl", "delete", "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
         "--all", "-A"],
        capture_output=True,
    )
    # Also remove any legacy deployment created by named replays
    legacy_reset = subprocess.run(
        ["kubectl", "delete", "deployment", "checkoutservice-legacy",
         "-n", "default", "--ignore-not-found=true"],
        capture_output=True,
    )
    time.sleep(60)
    return chaos_reset.returncode == 0 and legacy_reset.returncode == 0
    time.sleep(60)


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


async def run_scenario(scenario_id: str) -> dict:
    t0 = time.time()
    tier = scenario_id.split("/", 1)[0] if "/" in scenario_id else "unknown"
    ok = apply_chaos(scenario_id)
    if not ok:
        return {"scenario_id": scenario_id, "status": "skip", "error": "manifest_apply_failed"}

    alert = wait_for_alert()
    alert_was_synthetic_timeout = bool(alert.get("synthetic"))
    model_visible_alert = _model_visible_alert(alert)

    agent_error: str | None = None
    incident: dict | None = None
    judge_score: dict | None = None
    try:
        incident = await handle_incident(model_visible_alert, scenario_id=scenario_id)
        judge_score = await judge_trajectory(incident, tier=tier)
    except Exception as e:
        log.exception("scenario %s failed: %s", scenario_id, e)
        agent_error = str(e)

    reset_ok = reset_cluster()
    if agent_error is not None:
        if not reset_ok:
            return {
                "scenario_id": scenario_id,
                "status": "error",
                "error": "cluster_reset_failed",
                "reset_failure": True,
                "environment_invalid_before_trial": True,
            }
        return {"scenario_id": scenario_id, "status": "error", "error": agent_error}

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
        "resolved": env_resolved,
        "verification": verification,
        "time_to_resolve_s": remediation.get("time_to_resolve_seconds", round(time.time() - t0)),
        "severity": triage.get("severity", "unknown"),
        "total_turns": total_turns,
        "judge": judge_score,
        "incident": incident,
        "alert_was_synthetic_timeout": alert_was_synthetic_timeout,
        "approval": incident.get("approval"),
        "postmortem_path": incident.get("comms", {}).get("final", {}).get("postmortem_path"),
        "root_cause_evaluation": (
            evaluate_root_cause(incident, entry.get("faults", []))
            if (entry := load_catalog_entry(scenario_id)) is not None
            else {"available": False, "correct": None}
        ),
        "tool_metrics": tool_metrics(incident),
    }
    # Keep reward evaluation centralized so train/eval/bench cannot drift.
    episode["reward_contract"] = evaluate_reward_contract(episode)
    if not reset_ok:
        episode.update({
            "environment_invalid_before_trial": True,
            "error": "cluster_reset_failed",
            "reset_failure": True,
            "status": "error",
        })
    return episode


def compute_summary(
    results: list[dict],
    tag: str,
    model: str,
    config_provenance: dict | None = None,
) -> dict:
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
    tiers = sorted({r.get("tier", "unknown") for r in results})

    per_tier = {}
    for tier in tiers:
        attempted = [r for r in results if r.get("tier", "unknown") == tier]
        completed = [r for r in attempted if r.get("status") == "ok"]
        tier_resolved = [r for r in completed if r.get("resolved")]
        per_tier[tier] = {
            "attempted_count": len(attempted),
            "completed_count": len(completed),
            "count": len(completed),
            "resolution_rate": round(len(tier_resolved) / max(len(attempted), 1), 3),
            "avg_time_to_resolve_s": mean(completed, "time_to_resolve_s"),
            "avg_reward_contract": round(
                sum(r.get("reward_contract", {}).get("total", 0) for r in completed)
                / max(len(completed), 1),
                3,
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

    reason_counts: dict[str, int] = {}
    tool_totals = {
        key: sum(int((r.get("tool_metrics", {}) or {}).get(key, 0)) for r in valid)
        for key in (
            "attempts", "blocked_by_circuit_breaker", "blocked_by_policy",
            "cap_blocked", "dedup_blocked", "executed_failures",
            "executed_successes", "invalid_arguments", "mutating_attempts",
            "successful_investigations",
        )
    }
    root_available = [r for r in valid if (r.get("root_cause_evaluation", {}) or {}).get("available", True)]
    root_correct = [r for r in root_available if r.get("root_cause_evaluation", {}).get("correct") is True]

    for result in results:
        for reason in failure_reasons(result):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "tag": tag,
        "model": model,
        "run_date": datetime.now(timezone.utc).isoformat(),
        "config_sha256": (config_provenance or {}).get("config_sha256"),
        "config_provenance": config_provenance,
        "total_scenarios": len(results),
        "status_counts": {
            "ok": len(valid),
            "skip": sum(1 for r in results if r.get("status") == "skip"),
            "error": sum(1 for r in results if r.get("status") == "error"),
        },
        "completion_rate": round(len(valid) / max(len(results), 1), 3),
        "resolution_rate": round(len(resolved) / max(len(results), 1), 3),
        "avg_reward": round(sum(judge_scores) / max(len(judge_scores), 1), 3),
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
        "evidence_support": {
            "episodes_with_pre_action_evidence": sum(
                1 for r in valid if (r.get("tool_metrics", {}) or {}).get("pre_action_evidence") is True
            ),
            "successful_investigations_before_first_mutation": sum(
                int((r.get("tool_metrics", {}) or {})
                    .get("successful_investigations_before_first_mutation", 0))
                for r in valid
            ),
        },
        "failure_taxonomy": {
            "reason_counts": dict(sorted(reason_counts.items())),
            "episode_reasons": {
                str(r.get("scenario_id", "")): failure_reasons(r)
                for r in results
                if failure_reasons(r)
            },
        },
        "metrics": compute_g6_metrics(results),
        "tool_metrics": tool_totals,
        "root_cause_metrics": {
            "available_episodes": len(root_available),
            "correct_episodes": len(root_correct),
            "correct_rate_among_available": round(
                len(root_correct) / max(len(root_available), 1), 3
            ),
        },
        "per_tier": per_tier,
    }


def write_comparison_table(summary: dict) -> None:
    table_path = RESULTS_DIR / "comparison_table.md"
    existing_runs: list[dict] = []
    if table_path.exists():
        # naive parse — rebuild from stored JSON summaries
        for d in RESULTS_DIR.iterdir():
            s_file = d / "results_summary.json"
            if s_file.exists():
                existing_runs.append(json.loads(s_file.read_text()))
    existing_runs = [r for r in existing_runs if r.get("tag") != summary["tag"]]
    existing_runs.append(summary)
    existing_runs.sort(key=lambda x: x.get("run_date", ""))

    header = (
        "| Tag | Model | Resolution | Reward (Judge) | Reward (Contract) | Avg Penalty | Avg Turns "
        "| Cascade Res. | Replay Res. | Date |\n"
    )
    header += "|---|---|---|---|---|---|---|---|---|---|\n"
    rows = ""
    for r in existing_runs:
        rows += (
            f"| {r['tag']} | `{Path(r['model']).name}` "
            f"| {r['resolution_rate']:.0%} "
            f"| {r['avg_reward']:.3f} "
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
        f"# AtlasOps — Benchmark Results\n\n{header}{rows}{''.join(per_tier_lines)}",
        encoding="utf-8",
    )
    log.info("comparison table updated: %s", table_path)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Model path or HF ID")
    parser.add_argument("--model-digest", default="", help="Immutable model/provider digest")
    parser.add_argument("--seed", default="", help="Predeclared inference seed where supported")
    parser.add_argument("--tag", default="", help="Run label (e.g. grpo_v3, baseline_v2)")
    parser.add_argument(
        "--split-role",
        choices=("validation", "final_test", "exploration"),
        default="validation",
        help="Active G5 population to measure; exploration is for dynamic-only runs",
    )
    parser.add_argument("--allow-final-test", action="store_true",
                        help="Explicitly authorize a real final-test measurement")
    parser.add_argument("--scenarios", nargs="*", help="Subset within the selected split role")
    parser.add_argument("--output", default="", help="Override output dir")
    parser.add_argument("--adversarial", type=int, default=0,
                        help="Dynamic adversarial count; requires --split-role exploration")
    args = parser.parse_args()

    if args.allow_final_test and args.split_role != "final_test":
        raise RuntimeError("--allow-final-test requires --split-role final_test")
    if args.split_role == "exploration":
        if args.scenarios:
            raise RuntimeError("exploration selects generated scenarios; --scenarios is unavailable")
        if args.adversarial <= 0:
            raise RuntimeError("exploration requires --adversarial greater than zero")
    else:
        if args.adversarial != 0:
            raise RuntimeError("dynamic adversarial scenarios cannot be mixed into a frozen split")

    os.environ["AGENT_MODEL"] = args.model
    if args.seed:
        os.environ["ATLASOPS_BENCHMARK_SEED"] = args.seed
    tag = args.tag or f"run-{int(time.time())}"
    run_id = f"{tag}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    out_dir = Path(args.output) if args.output else (RESULTS_DIR / run_id)

    if args.split_role == "validation":
        if not args.model_digest:
            raise RuntimeError("validation benchmark requires --model-digest for reproducibility")
        selected_scenarios = list(allowed_scenario_ids("validation"))
    elif args.split_role == "final_test":
        if not args.allow_final_test:
            raise RuntimeError("final-test membership is gated; pass --allow-final-test explicitly")
        if not args.model_digest:
            raise RuntimeError("final-test benchmark requires --model-digest for provenance")
        selected_scenarios = list(allowed_scenario_ids("final_test"))
    else:
        selected_scenarios = []

    scenarios = list(selected_scenarios)
    if args.scenarios:
        unknown = sorted(set(args.scenarios).difference(selected_scenarios))
        if unknown:
            raise RuntimeError(f"--scenarios are outside active {args.split_role} split: {unknown}")
        scenarios = list(args.scenarios)

    if args.adversarial > 0:
        log.info("generating %d dynamic adversarial scenarios via 72B judge...", args.adversarial)
        # Seed with any existing failure history from prior runs
        prior_failures = []
        for d in RESULTS_DIR.iterdir():
            ep_file = d / "results_per_episode.jsonl"
            if ep_file.exists():
                for line in ep_file.read_text().splitlines():
                    try:
                        ep = json.loads(line)
                        if not ep.get("resolved"):
                            prior_failures.append(ep)
                    except json.JSONDecodeError:
                        pass
        adv_results = await design_batch(prior_failures, count=args.adversarial)
        for adv in adv_results:
            # Add generated manifest path as a runnable scenario
            rel = str(Path(adv["manifest_path"]).relative_to(Path("bench/chaos_manifests")))
            rel = rel.replace("\\", "/").removesuffix(".yaml")
            scenarios.append(rel)
        log.info("added %d adversarial scenarios to run", len(adv_results))
    if not scenarios:
        raise RuntimeError("refusing to launch an empty zero-shot benchmark")

    results = prepare_output_directory(out_dir)
    config_provenance, config_hash = configuration_provenance(args)
    from bench.g6_evidence import contract_hashes
    from bench.scenario_contract import build_catalog, load_active_split

    current_contracts = contract_hashes()
    catalog_sha256 = build_catalog()["catalog_sha256"]
    if args.split_role == "exploration":
        frozen_split_sha256 = "EXPLORATION_NO_FROZEN_SPLIT"
    else:
        frozen_split_sha256 = sha256_object(load_active_split())
    manifest_path = out_dir / "run_manifest.json"
    if manifest_path.exists():
        stored_manifest = read_run_manifest(manifest_path)
        validate_resume_manifest(
            stored_manifest,
            scenario_ids=scenarios,
            config_hash=config_hash,
            catalog_sha256=catalog_sha256,
            frozen_split_sha256=frozen_split_sha256,
            contracts=current_contracts,
        )
    else:
        immutable_manifest = build_run_manifest(
            run_id=run_id,
            tag=tag,
            model_provider=os.getenv("BACKEND", "undeclared"),
            model_name=args.model,
            model_digest=args.model_digest or "UNDECLARED",
            seed=args.seed,
            split_role=args.split_role,
            scenario_ids=scenarios,
            catalog_sha256=catalog_sha256,
            frozen_split_sha256=frozen_split_sha256,
            benchmark_version=RESULT_SCHEMA_VERSION,
            arguments=vars(args),
        )
        immutable_manifest.update({
            "config_provenance": config_provenance,
            "config_sha256": config_hash,
            "scenario_count": len(scenarios),
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        write_run_manifest(manifest_path, immutable_manifest)

    validate_resume_raw_records(out_dir, len(results))

    completed_ids = [str(r.get("scenario_id", "")) for r in results]
    if completed_ids != scenarios[: len(completed_ids)]:
        raise RuntimeError("completed episode sequence does not match the planned scenario sequence")

    log.info("running %d scenarios for tag=%s model=%s", len(scenarios), tag, args.model)
    for i in range(len(results), len(scenarios)):
        s = scenarios[i]
        log.info("[%d/%d] %s", i + 1, len(scenarios), s)
        r = await run_scenario(s)
        results.append(r)
        append_episode(out_dir, r)
        append_raw_record(
            out_dir,
            build_raw_record(
                r,
                run_manifest=read_run_manifest(manifest_path),
                episode_index=i,
            ),
        )
        ensure_environment_safe_for_next_episode(r)

    summary = compute_summary(results, tag, args.model, {
        **config_provenance,
        "config_sha256": config_hash,
    })
    write_json_atomic(out_dir / "results_summary.json", summary)
    finalize_raw_run(out_dir, summary, episode_count=len(results))
    write_comparison_table(summary)

    log.info("=== Benchmark complete ===")
    log.info("  Resolution rate : %.1f%%", summary["resolution_rate"] * 100)
    log.info("  Avg reward      : %.3f", summary["avg_reward"])
    log.info("  Results         : %s", out_dir)


if __name__ == "__main__":
    asyncio.run(main())
