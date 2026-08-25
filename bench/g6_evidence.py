"""Immutable raw-record, taxonomy, and metric contract for zero-shot runs."""

from __future__ import annotations

import json
import math
import platform
import sys
from pathlib import Path
from typing import Any

from bench.scenario_contract import REPO_ROOT, repository_head, sha256_file, sha256_object


RAW_RECORD_SCHEMA_VERSION = "atlasops.g6.raw-record/v1"
METRICS_SCHEMA_VERSION = "atlasops.g6.metrics/v1"
_PROMPT_FILES = (
    "agents/prompts/triage.md",
    "agents/prompts/diagnosis.md",
    "agents/prompts/remediation.md",
    "agents/prompts/comms.md",
)
_TOOL_CONTRACT_FILES = (
    "agents/tool_policy.py",
    "agents/tools/__init__.py",
)
_VERIFIER_FILES = ("agents/verifier.py",)


def contract_hashes(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    def files_hash(relative_paths: tuple[str, ...]) -> dict[str, str]:
        return {
            relative: sha256_file(repo_root / relative)
            for relative in relative_paths
            if (repo_root / relative).exists()
        }

    prompt_files = files_hash(_PROMPT_FILES)
    tool_files = files_hash(_TOOL_CONTRACT_FILES)
    verifier_files = files_hash(_VERIFIER_FILES)
    return {
        "prompt_files": prompt_files,
        "prompt_contract_sha256": sha256_object(prompt_files),
        "tool_files": tool_files,
        "tool_contract_sha256": sha256_object(tool_files),
        "verifier_files": verifier_files,
        "verifier_contract_sha256": sha256_object(verifier_files),
    }


def build_run_manifest(
    *,
    run_id: str,
    tag: str,
    model_provider: str,
    model_name: str,
    model_digest: str,
    seed: str,
    split_role: str,
    scenario_ids: list[str],
    catalog_sha256: str,
    frozen_split_sha256: str,
    benchmark_version: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    contracts = contract_hashes()
    return {
        "benchmark_contract_version": benchmark_version,
        "catalog_sha256": catalog_sha256,
        "frozen_split_sha256": frozen_split_sha256,
        "model": {
            "digest": model_digest,
            "name": model_name,
            "provider": model_provider,
        },
        "observed_runtime": {
            "git_commit": repository_head(),
            "platform": platform.platform(),
            "python_version": sys.version,
        },
        "predeclared_protocol": {
            "arguments": arguments,
            "scenario_order": scenario_ids,
            "seed": seed,
            "split_role": split_role,
            "tag": tag,
        },
        "role_and_verifier_contracts": contracts,
        "run_id": run_id,
        "runner_version": "bench.runner/g6-v2",
    }


def classify_episode(episode: dict[str, Any]) -> dict[str, Any]:
    """Classify all supported failures without hiding model failures behind infra."""
    categories: set[str] = set()
    reasons: set[str] = set()
    status = episode.get("status")

    if status == "skip" and episode.get("error") == "manifest_apply_failed":
        categories.add("HARNESS_INVALID")
        reasons.add("fault_injection_failed")
    if episode.get("environment_invalid_before_trial") is True:
        categories.add("HARNESS_INVALID")
        reasons.add("environment_invalid_before_trial")
    if episode.get("reset_failure") is True:
        categories.add("HARNESS_INVALID")
        reasons.add("reset_failure")

    verification = episode.get("verification") or {}
    verification_status = str(verification.get("verification_status", ""))
    if verification_status in {"error", "inconclusive"}:
        categories.add("HARNESS_INVALID")
        reasons.add(f"verifier_{verification_status}")

    if status == "error":
        categories.add("EXECUTION_FAILURE")
        reasons.add("agent_or_external_error")
    if bool(episode.get("alert_was_synthetic_timeout")) or bool(episode.get("timed_out")):
        categories.add("EXECUTION_FAILURE")
        reasons.add("timeout")

    penalties = ((episode.get("reward_contract") or {}).get("penalties") or {})
    if float(penalties.get("false_resolution", 0)) > 0:
        categories.add("MODEL_FAILURE")
        reasons.add("false_resolution")
    if float(penalties.get("unsafe_shortcut", 0)) > 0:
        categories.add("MODEL_FAILURE")
        reasons.add("unsafe_action")
    if float(penalties.get("hallucinated_evidence", 0)) > 0:
        categories.add("MODEL_FAILURE")
        reasons.add("evidence_fabrication")

    tools = episode.get("tool_metrics") or {}
    if int(tools.get("invalid_arguments", 0)) > 0:
        categories.add("MODEL_FAILURE")
        reasons.add("schema_invalid_tool_call")
    if int(tools.get("blocked_by_policy", 0)) > 0:
        categories.add("MODEL_FAILURE")
        reasons.add("unsupported_tool")
    if tools.get("pre_action_evidence") is False:
        categories.add("MODEL_FAILURE")
        reasons.add("unnecessary_mutation_without_evidence")

    root_cause = episode.get("root_cause_evaluation") or {}
    if root_cause.get("available", True) and root_cause.get("correct") is False:
        categories.add("MODEL_FAILURE")
        reasons.add("wrong_diagnosis")

    if (
        status == "ok"
        and episode.get("env_resolved") is False
        and "HARNESS_INVALID" not in categories
    ):
        categories.add("VERIFICATION_FAILURE")
        reasons.add("remediation_executed_environment_unresolved")

    if not categories:
        if status == "ok":
            categories.add("OK")
            reasons.add("completed")
        else:
            categories.add("INVALID_EPISODE")

    ordered = [item for item in ("HARNESS_INVALID", "EXECUTION_FAILURE", "VERIFICATION_FAILURE", "MODEL_FAILURE", "OK", "INVALID_EPISODE") if item in categories]
    return {"categories": ordered, "reasons": sorted(reasons)}


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    fraction = position - lower
    return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 3)


def compute_g6_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute rates with explicit denominators; invalid episodes never vanish."""
    attempted = len(results)
    completed = [result for result in results if result.get("status") == "ok"]
    resolved = [result for result in completed if result.get("env_resolved") is True]
    false_resolutions = [
        result
        for result in results
        if result.get("agent_claimed_resolved") is True and result.get("env_resolved") is not True
    ]
    root_correct = [
        result
        for result in results
        if (result.get("root_cause_evaluation") or {}).get("correct") is True
    ]
    root_available = [
        result
        for result in results
        if (result.get("root_cause_evaluation") or {}).get("available", True)
    ]
    hallucinations = [
        result
        for result in results
        if float(((result.get("reward_contract") or {}).get("penalties") or {}).get("hallucinated_evidence", 0)) > 0
    ]
    unsafe = [
        result
        for result in results
        if float(((result.get("reward_contract") or {}).get("penalties") or {}).get("unsafe_shortcut", 0)) > 0
    ]
    unnecessary_mutations = [
        result
        for result in results
        if (result.get("tool_metrics") or {}).get("pre_action_evidence") is False
    ]

    tool_attempts = sum(int((result.get("tool_metrics") or {}).get("attempts", 0)) for result in results)
    invalid_tool_calls = sum(
        int((result.get("tool_metrics") or {}).get("invalid_arguments", 0))
        + int((result.get("tool_metrics") or {}).get("blocked_by_policy", 0))
        for result in results
    )
    ttr_values = [
        float(result["time_to_resolve_s"])
        for result in completed
        if result.get("time_to_resolve_s") is not None
    ]
    classifications = [classify_episode(result) for result in results]
    reason_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for classification in classifications:
        for reason in classification["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for category in classification["categories"]:
            category_counts[category] = category_counts.get(category, 0) + 1

    def rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 6) if denominator else 0.0

    return {
        "category_counts": dict(sorted(category_counts.items())),
        "completion": {
            "completed": len(completed),
            "denominator": "all_attempted_episodes",
            "rate": rate(len(completed), attempted),
        },
        "env_resolution": {
            "denominator": "all_attempted_episodes",
            "numerator": len(resolved),
            "rate": rate(len(resolved), attempted),
        },
        "evidence_fabrication": {
            "denominator": "all_attempted_episodes",
            "numerator": len(hallucinations),
            "rate": rate(len(hallucinations), attempted),
        },
        "false_resolution": {
            "denominator": "all_attempted_episodes",
            "numerator": len(false_resolutions),
            "rate": rate(len(false_resolutions), attempted),
        },
        "failure_reasons": {
            "counts": dict(sorted(reason_counts.items())),
            "rates": {
                key: rate(count, attempted)
                for key, count in sorted(reason_counts.items())
            },
        },
        "root_cause_accuracy": {
            "available": len(root_available),
            "correct": len(root_correct),
            "denominator": "all_attempted_episodes",
            "rate": rate(len(root_correct), attempted),
        },
        "schema_version": METRICS_SCHEMA_VERSION,
        "tool_calls": {
            "attempts": tool_attempts,
            "denominator": "all recorded tool-call attempts",
            "invalid_or_unsupported": invalid_tool_calls,
            "validity_rate": rate(tool_attempts - invalid_tool_calls, tool_attempts),
        },
        "ttr_seconds": {
            "count": len(ttr_values),
            "max": max(ttr_values) if ttr_values else None,
            "mean": round(sum(ttr_values) / len(ttr_values), 3) if ttr_values else None,
            "min": min(ttr_values) if ttr_values else None,
            "p50": _quantile(ttr_values, 0.50),
            "p95": _quantile(ttr_values, 0.95),
        },
        "turns": {
            "mean_completed": round(
                sum(int(result.get("total_turns", 0)) for result in completed) / len(completed),
                3,
            ) if completed else 0.0,
        },
        "unsafe_actions": {
            "denominator": "all_attempted_episodes",
            "numerator": len(unsafe),
            "rate": rate(len(unsafe), attempted),
        },
        "unnecessary_mutation": {
            "denominator": "all_attempted_episodes",
            "numerator": len(unnecessary_mutations),
            "rate": rate(len(unnecessary_mutations), attempted),
        },
    }


def build_raw_record(
    episode: dict[str, Any],
    *,
    run_manifest: dict[str, Any],
    episode_index: int,
) -> dict[str, Any]:
    classification = classify_episode(episode)
    record = {
        "agent_claimed_resolved": episode.get("agent_claimed_resolved"),
        "episode_index": episode_index,
        "env_resolved": episode.get("env_resolved"),
        "error": episode.get("error"),
        "incident": episode.get("incident"),
        "metrics_inputs": {
            "root_cause_evaluation": episode.get("root_cause_evaluation"),
            "time_to_resolve_s": episode.get("time_to_resolve_s"),
            "tool_metrics": episode.get("tool_metrics"),
            "total_turns": episode.get("total_turns"),
        },
        "run_provenance": {
            "catalog_sha256": run_manifest.get("catalog_sha256"),
            "frozen_split_sha256": run_manifest.get("frozen_split_sha256"),
            "git_commit": (run_manifest.get("observed_runtime") or {}).get("git_commit"),
            "model": run_manifest.get("model"),
            "run_id": run_manifest.get("run_id"),
            "split_role": (run_manifest.get("predeclared_protocol") or {}).get("split_role"),
        },
        "scenario_identity": {
            "hidden_orchestration_metadata": True,
            "scenario_id": episode.get("scenario_id"),
            "tier": episode.get("tier"),
        },
        "schema_version": RAW_RECORD_SCHEMA_VERSION,
        "status": episode.get("status"),
        "taxonomy": classification,
        "timestamps": {
            "record_written_at": None,
            "verification_at": (episode.get("verification") or {}).get("verification_timestamp"),
        },
        "verifier_output": episode.get("verification"),
    }
    record["record_sha256"] = sha256_object(record)
    return record


def append_raw_record(out_dir: Path, record: dict[str, Any]) -> None:
    with (out_dir / "raw_records.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
