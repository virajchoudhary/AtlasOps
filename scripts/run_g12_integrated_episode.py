"""Archive one governed live G12 episode without creating a second Chaos path.

This wrapper delegates all fault, approval, and cleanup control to the Stage 4
harness. A bundle is evidence for review, never an automatic gate certification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.g4_protocol import METRICS_SERVER_CONTEXT

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = re.compile(r"EXP-STAGE4-[A-Za-z0-9][A-Za-z0-9_-]{0,112}\Z")
POLICY_BLOCK_CATEGORIES = frozenset({
    "invalid_action",
    "already_resolved",
    "tool_unavailable",
    "approval_required",
    "policy_block",
    "missing_evidence",
})
VERIFICATION_STATUSES = frozenset({
    "passed",
    "failed",
    "inconclusive",
    "error",
})
SETTLEMENT_FAILURE_STATUSES = frozenset({"unscorable", "timeout", "error"})


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _source_sha(root: Path) -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        raise RuntimeError("G12 live capture requires a clean disposable source checkout")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("Unable to establish the exact G12 source commit")
    return commit


def _checkpoint_record(checkpoint: Path) -> dict[str, Any]:
    from bench.grpo_eval import validate_grpo_checkpoint

    return validate_grpo_checkpoint(checkpoint).to_record()


def _artifact_copy(source: Path, destination: Path, root: Path) -> dict[str, Any]:
    resolved = source.resolve()
    if source.is_symlink() or not resolved.is_relative_to(root.resolve()) or not source.is_file():
        raise ValueError(f"Refusing a non-repository evidence path: {source}")
    shutil.copyfile(source, destination)
    raw = destination.read_bytes()
    return {
        "path": destination.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def _parse_policy_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _parse_raw_policy_action(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        payload = json.loads(value)
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, dict) or "actions" in payload:
        return None
    tool = payload.get("tool")
    arguments = payload.get("arguments")
    if not isinstance(tool, str) or not tool or not isinstance(arguments, dict):
        return None
    return {
        "tool": tool,
        "arguments": arguments,
        "agent_claimed_resolved": payload.get("agent_claimed_resolved") is True,
    }


def _valid_parsed_policy_action(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("tool"), str)
        and bool(value["tool"])
        and isinstance(value.get("arguments"), dict)
        and isinstance(value.get("agent_claimed_resolved"), bool)
    )


def _pre_action_observation_problems(
    step: dict[str, Any],
    prefix: str,
) -> list[str]:
    if "pre_action_observation" not in step:
        return []

    observation = step.get("pre_action_observation")
    action = step.get("parsed_action")
    action_tool = action.get("tool") if isinstance(action, dict) else None
    readers = {
        "argocd_rollback": "argocd_app_history",
        "chaos_stop_experiment": "chaos_list_experiments",
    }
    expected_reader = readers.get(action_tool) if isinstance(action_tool, str) else None
    if expected_reader is None:
        return (
            []
            if observation is None
            else [f"{prefix}_pre_action_observation_unexpected"]
        )
    if step.get("environment_status") == "blocked":
        if observation is None:
            return []
        terminal_block = step.get("terminal_block")
        if (
            not isinstance(terminal_block, dict)
            or terminal_block.get("category") != "missing_evidence"
        ):
            return [f"{prefix}_pre_action_observation_unexpected"]
        if not isinstance(observation, dict) or set(observation) != {
            "tool",
            "success",
            "observation_status",
            "history",
            "active_experiments",
        } or observation.get("tool") != expected_reader:
            return [f"{prefix}_pre_action_observation_malformed"]
        return [f"{prefix}_pre_action_observation_unverified"]
    if observation is None:
        return [f"{prefix}_pre_action_observation_missing"]
    if not isinstance(observation, dict) or set(observation) != {
        "tool",
        "success",
        "observation_status",
        "history",
        "active_experiments",
    }:
        return [f"{prefix}_pre_action_observation_malformed"]

    problems: list[str] = []
    if observation.get("tool") != expected_reader:
        problems.append(f"{prefix}_pre_action_observation_mismatch")
    if type(observation.get("success")) is not bool:
        problems.append(f"{prefix}_pre_action_observation_malformed")
    elif observation["success"] is not True:
        problems.append(f"{prefix}_pre_action_observation_mismatch")
    observed_status = observation.get("observation_status")
    if observed_status is not None and not isinstance(observed_status, str):
        problems.append(f"{prefix}_pre_action_observation_malformed")
    if action_tool == "chaos_stop_experiment" and observed_status != "observed":
        problems.append(f"{prefix}_pre_action_observation_mismatch")
    if (
        action_tool == "argocd_rollback"
        and isinstance(observed_status, str)
        and observed_status != "observed"
    ):
        problems.append(f"{prefix}_pre_action_observation_mismatch")

    arguments = action.get("arguments") if isinstance(action, dict) else None
    if not isinstance(arguments, dict):
        return problems + [f"{prefix}_pre_action_observation_malformed"]
    if action_tool == "chaos_stop_experiment":
        active = observation.get("active_experiments")
        if observation.get("history") is not None or not isinstance(active, list):
            return problems + [f"{prefix}_pre_action_observation_malformed"]
        expected = (
            str(arguments.get("kind") or "").casefold(),
            str(arguments.get("name") or ""),
            str(arguments.get("namespace") or "chaos-mesh").casefold(),
        )
        if not any(
            isinstance(item, dict)
            and (
                str(item.get("kind") or "").casefold(),
                str(item.get("name") or ""),
                str(item.get("namespace") or "").casefold(),
            )
            == expected
            for item in active
        ):
            problems.append(f"{prefix}_pre_action_observation_mismatch")
    else:
        history = observation.get("history")
        if observation.get("active_experiments") is not None or not isinstance(
            history, list
        ):
            return problems + [f"{prefix}_pre_action_observation_malformed"]
        revision = arguments.get("revision")
        if not arguments.get("app") or not revision or not any(
            isinstance(item, dict)
            and any(
                str(value) == str(revision)
                for value in (item.get("id"), item.get("revision"))
                if value is not None
            )
            for item in history
        ):
            problems.append(f"{prefix}_pre_action_observation_mismatch")
    return problems


def _verification_record_problems(value: Any, prefix: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{prefix}_malformed"]

    problems: list[str] = []
    status = value.get("verification_status")
    env_resolved = value.get("env_resolved")
    if not isinstance(status, str) or status not in VERIFICATION_STATUSES:
        problems.append(f"{prefix}_status_invalid")
    if not isinstance(env_resolved, bool):
        problems.append(f"{prefix}_resolution_malformed")

    checks = value.get("checks")
    failed_required: list[str] = []
    checks_valid = isinstance(checks, list) and bool(checks)
    if not checks_valid:
        problems.append(f"{prefix}_checks_malformed")
    else:
        for check in checks:
            if not isinstance(check, dict):
                checks_valid = False
                continue
            name = check.get("name")
            target = check.get("target")
            passed = check.get("passed")
            required = check.get("required")
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(target, str)
                or not isinstance(passed, bool)
                or not isinstance(required, bool)
            ):
                checks_valid = False
                continue
            if required and not passed:
                failed_required.append(name)
        if not checks_valid:
            problems.append(f"{prefix}_checks_malformed")

    failed_checks = value.get("failed_checks")
    if not isinstance(failed_checks, list) or any(
        not isinstance(name, str) for name in failed_checks
    ):
        problems.append(f"{prefix}_failed_checks_malformed")
    elif checks_valid and failed_checks != failed_required:
        problems.append(f"{prefix}_failed_checks_mismatch")

    if isinstance(status, str) and status in VERIFICATION_STATUSES:
        if status == "passed":
            if env_resolved is not True or failed_required:
                problems.append(f"{prefix}_status_checks_mismatch")
        elif status == "failed":
            if env_resolved is not False or not failed_required:
                problems.append(f"{prefix}_status_checks_mismatch")
        elif env_resolved is True or not failed_required:
            problems.append(f"{prefix}_status_checks_mismatch")

    return problems


def _nonnegative_number(value: Any) -> bool:
    if type(value) is int:
        return value >= 0
    return type(value) is float and math.isfinite(value) and value >= 0


def _settlement_observation_problems(
    observation: Any, prefix: str
) -> list[str]:
    if not isinstance(observation, dict):
        return [f"{prefix}_malformed"]

    problems: list[str] = []
    status = observation.get("verification_status")
    timed_out = observation.get("timed_out", False)
    if "timed_out" in observation and not isinstance(timed_out, bool):
        problems.append(f"{prefix}_timed_out_malformed")
    if (
        (not isinstance(status, str) or status not in VERIFICATION_STATUSES)
        and not (timed_out is True and status is None)
    ):
        problems.append(f"{prefix}_status_invalid")

    has_timestamp = _parse_policy_timestamp(observation.get("timestamp")) is not None
    has_elapsed = any(
        _nonnegative_number(observation.get(field))
        for field in ("elapsed_s", "elapsed_seconds")
    )
    if not has_timestamp and not has_elapsed:
        problems.append(f"{prefix}_time_missing")

    env_resolved = observation.get("env_resolved")
    if isinstance(status, str) and status in {"passed", "failed"}:
        expected_resolution = status == "passed"
        if not (timed_out is True and env_resolved is None) and (
            env_resolved is not expected_resolution
        ):
            problems.append(f"{prefix}_resolution_mismatch")
    elif env_resolved is not None and not isinstance(env_resolved, bool):
        problems.append(f"{prefix}_resolution_malformed")

    failed_checks = observation.get("failed_checks")
    may_omit_failed_checks = (
        timed_out is True
        or status == "error"
        or (
            isinstance(observation.get("error"), str)
            and observation["error"].startswith("malformed_objective_observation:")
        )
    )
    if (not may_omit_failed_checks or "failed_checks" in observation) and (
        not isinstance(failed_checks, list)
        or any(not isinstance(name, str) for name in failed_checks)
    ):
        problems.append(f"{prefix}_failed_checks_malformed")
    return problems


def _has_settlement_failure(settling: Any) -> bool:
    return (
        isinstance(settling, dict)
        and isinstance(settling.get("status"), str)
        and settling["status"] in SETTLEMENT_FAILURE_STATUSES
        and isinstance(settling.get("failure"), str)
        and bool(settling["failure"])
    )


def _postflight_chaos_count(postflight_result: Any) -> int | None:
    if not isinstance(postflight_result, dict):
        return None
    stdout = postflight_result.get("stdout")
    if not isinstance(stdout, str):
        return None
    try:
        payload = json.loads(stdout)
    except (ValueError, RecursionError):
        return None
    items = payload.get("items") if isinstance(payload, dict) else None
    return len(items) if isinstance(items, list) else None


def _settlement_record_problems(settling: Any, prefix: str) -> list[str]:
    if not isinstance(settling, dict):
        return [f"{prefix}_malformed"]

    problems: list[str] = []
    status = settling.get("status")
    if status is not None and (
        not isinstance(status, str)
        or status not in {"settled", *SETTLEMENT_FAILURE_STATUSES}
    ):
        problems.append(f"{prefix}_status_invalid")
    settlement_failure = _has_settlement_failure(settling)
    if (
        isinstance(status, str)
        and status in SETTLEMENT_FAILURE_STATUSES
        and not settlement_failure
    ):
        problems.append(f"{prefix}_failure_missing")
    if settlement_failure and settling.get("settled") is True:
        problems.append(f"{prefix}_failure_conflicts_with_settled")

    observations = settling.get("observations")
    if not isinstance(observations, list):
        problems.append(f"{prefix}_observations_malformed")
    elif observations:
        for index, observation in enumerate(observations):
            problems.extend(
                _settlement_observation_problems(
                    observation, f"{prefix}_observation_{index}"
                )
            )

    if settlement_failure:
        if "settled" in settling and not isinstance(settling["settled"], bool):
            problems.append(f"{prefix}_settled_malformed")
    elif (
        not isinstance(settling.get("settled"), bool)
        or not isinstance(observations, list)
        or not observations
    ):
        problems.append(f"{prefix}_malformed")
    elif status == "settled" and settling["settled"] is not True:
        problems.append(f"{prefix}_status_conflicts_with_settled")
    return problems


def _policy_step_stops_execution(step: dict[str, Any]) -> bool:
    if (
        step.get("environment_status") != "ok"
        or step.get("env_resolved") is True
        or isinstance(step.get("terminal_block"), dict)
    ):
        return True

    settling = step.get("settling")
    if (
        isinstance(settling, dict)
        and settling.get("status") in SETTLEMENT_FAILURE_STATUSES
    ):
        return True

    verification = step.get("verification")
    return (
        isinstance(verification, dict)
        and verification.get("verification_status") in {"inconclusive", "error"}
    )


def _cleanup_evidence_problems(path: Path, experiment_id: str) -> list[str]:
    try:
        if not path.is_file():
            return ["cleanup_evidence_missing"]
        cleanup = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        return [f"cleanup_evidence_unreadable:{type(exc).__name__}"]

    if not isinstance(cleanup, dict):
        return ["cleanup_evidence_malformed"]

    problems: list[str] = []
    if (
        type(cleanup.get("schema_version")) is not int
        or cleanup["schema_version"] != 1
        or cleanup.get("experiment_id") != experiment_id
        or not isinstance(cleanup.get("timing"), str)
        or not cleanup["timing"]
        or cleanup.get("affects_env_resolved") is not False
        or cleanup.get("verdict_preserved") is not True
        or type(cleanup.get("max_attempts")) is not int
        or cleanup["max_attempts"] < 1
        or not isinstance(cleanup.get("verified_zero_chaos"), bool)
        or cleanup.get("poisoned_environment")
        is not (not cleanup.get("verified_zero_chaos"))
        or _parse_policy_timestamp(cleanup.get("timestamp")) is None
    ):
        problems.append("cleanup_evidence_malformed")

    attempts = cleanup.get("attempts")
    if (
        not isinstance(attempts, list)
        or not attempts
        or (
            type(cleanup.get("max_attempts")) is int
            and len(attempts) > cleanup["max_attempts"]
        )
    ):
        return problems + ["cleanup_evidence_malformed"]

    for index, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, dict):
            problems.append("cleanup_evidence_malformed")
            continue
        started = _parse_policy_timestamp(attempt.get("started_at"))
        completed = _parse_policy_timestamp(attempt.get("completed_at"))
        postflight_result = attempt.get("postflight_result")
        active_count = attempt.get("active_chaos_count")
        parsed_count = _postflight_chaos_count(postflight_result)
        verified_zero = (
            isinstance(postflight_result, dict)
            and postflight_result.get("success") is True
            and parsed_count == 0
        )
        if (
            type(attempt.get("attempt")) is not int
            or attempt["attempt"] != index
            or started is None
            or completed is None
            or completed < started
            or not isinstance(attempt.get("delete_result"), dict)
            or not isinstance(postflight_result, dict)
            or not isinstance(postflight_result.get("success"), bool)
            or type(active_count) is not int
            or parsed_count is None
            or parsed_count != active_count
            or not isinstance(attempt.get("verified_zero_chaos"), bool)
            or attempt.get("verified_zero_chaos") is not verified_zero
        ):
            problems.append("cleanup_evidence_malformed")

    last_attempt = attempts[-1] if isinstance(attempts[-1], dict) else {}
    if (
        not isinstance(cleanup.get("result"), dict)
        or cleanup["result"] != last_attempt.get("delete_result")
        or last_attempt.get("verified_zero_chaos")
        is not cleanup.get("verified_zero_chaos")
    ):
        problems.append("cleanup_evidence_malformed")
    return problems


def _policy_steps_problems(
    remediation: dict[str, Any],
    incident_id: str | None,
    incident_alert: Any,
    incident_anchors: Any,
) -> list[str]:
    if "policy_steps" not in remediation:
        return ["policy_steps_missing"]
    steps = remediation["policy_steps"]
    if not isinstance(steps, list):
        return ["policy_steps_malformed"]
    if not steps:
        return ["policy_steps_empty"]

    required_fields = (
        "index",
        "started_at",
        "completed_at",
        "state",
        "environment_status",
        "pre_action_observation",
        "raw_policy_output",
        "parsed_action",
        "executed_actions",
        "verification",
        "settling",
        "env_resolved",
        "terminal_block",
        "next_state",
    )
    problems: list[str] = []
    for index, step in enumerate(steps):
        prefix = f"policy_step_{index}"
        if not isinstance(step, dict):
            problems.append(f"{prefix}_malformed")
            continue

        for field in required_fields:
            if field not in step:
                problems.append(f"{prefix}_{field}_missing")
        if "index" in step and (
            type(step["index"]) is not int or step["index"] != index
        ):
            problems.append(f"{prefix}_index_invalid")

        started = _parse_policy_timestamp(step.get("started_at"))
        completed = _parse_policy_timestamp(step.get("completed_at"))
        environment_status = step.get("environment_status")
        valid_environment_status = (
            isinstance(environment_status, str)
            and environment_status in {"ok", "blocked", "unscorable"}
        )
        if "environment_status" in step and not valid_environment_status:
            problems.append(f"{prefix}_environment_status_invalid")
        if "started_at" in step and started is None:
            problems.append(f"{prefix}_started_at_invalid")
        if "completed_at" in step and completed is None:
            problems.append(f"{prefix}_completed_at_invalid")
        if started is not None and completed is not None and completed < started:
            problems.append(f"{prefix}_timestamps_out_of_order")

        for field in ("state", "next_state"):
            if field in step and (
                not isinstance(step[field], dict) or not step[field]
            ):
                problems.append(f"{prefix}_{field}_malformed")
            elif isinstance(step.get(field), dict) and (
                step[field].get("incident_id") != incident_id
                or not isinstance(step[field].get("alert"), dict)
                or not step[field]["alert"]
                or not isinstance(step[field].get("incident_anchors"), dict)
                or not step[field]["incident_anchors"]
            ):
                problems.append(f"{prefix}_{field}_anchors_missing")
            if isinstance(step.get(field), dict):
                if step[field].get("alert") != incident_alert:
                    problems.append(f"{prefix}_{field}_alert_source_mismatch")
                if step[field].get("incident_anchors") != incident_anchors:
                    problems.append(f"{prefix}_{field}_anchors_source_mismatch")
        if isinstance(step.get("state"), dict) and step["state"].get("env_resolved") is True:
            problems.append(f"{prefix}_state_already_resolved")
        if (
            isinstance(step.get("state"), dict)
            and isinstance(step.get("next_state"), dict)
            and (
                step["next_state"].get("alert") != step["state"].get("alert")
                or step["next_state"].get("incident_anchors")
                != step["state"].get("incident_anchors")
            )
        ):
            problems.append(f"{prefix}_next_state_anchors_mismatch")
        raw_output = step.get("raw_policy_output")
        if "raw_policy_output" in step and (
            not isinstance(raw_output, str) or not raw_output.strip()
        ):
            problems.append(f"{prefix}_raw_policy_output_malformed")

        terminal_block = step.get("terminal_block")
        blocked = isinstance(terminal_block, dict)
        if terminal_block is not None and not blocked:
            problems.append(f"{prefix}_terminal_block_malformed")
        block_category = terminal_block.get("category") if blocked else None
        if blocked:
            if not isinstance(block_category, str) or block_category not in POLICY_BLOCK_CATEGORIES:
                problems.append(f"{prefix}_terminal_block_category_invalid")
            if (
                not isinstance(terminal_block.get("reason"), str)
                or not terminal_block["reason"]
            ):
                problems.append(f"{prefix}_terminal_block_malformed")

        raw_action = _parse_raw_policy_action(raw_output)
        parsed_action = step.get("parsed_action")
        if blocked and isinstance(block_category, str) and block_category in {
            "invalid_action",
            "tool_unavailable",
        }:
            expected_parseable = block_category == "tool_unavailable"
            if parsed_action is not None or (raw_action is not None) != expected_parseable:
                problems.append(f"{prefix}_parsed_action_mismatch")
        elif not _valid_parsed_policy_action(parsed_action):
            if "parsed_action" in step:
                problems.append(f"{prefix}_parsed_action_malformed")
        elif raw_action != parsed_action:
            problems.append(f"{prefix}_raw_parsed_action_mismatch")
        problems.extend(_pre_action_observation_problems(step, prefix))

        executed_actions = step.get("executed_actions")
        if not isinstance(executed_actions, list):
            if "executed_actions" in step:
                problems.append(f"{prefix}_executed_actions_malformed")
        elif blocked:
            if executed_actions:
                problems.append(f"{prefix}_blocked_action_was_executed")
        elif len(executed_actions) != 1:
            problems.append(f"{prefix}_executed_actions_malformed")
        else:
            executed = executed_actions[0]
            if not isinstance(executed, dict):
                problems.append(f"{prefix}_executed_action_malformed")
            else:
                if (
                    not isinstance(executed.get("tool"), str)
                    or not executed["tool"]
                    or not isinstance(executed.get("arguments"), dict)
                    or "result" not in executed
                    or executed.get("result") is None
                ):
                    problems.append(f"{prefix}_executed_action_malformed")
                elif _valid_parsed_policy_action(parsed_action) and (
                    executed["tool"] != parsed_action["tool"]
                    or executed["arguments"] != parsed_action["arguments"]
                ):
                    problems.append(f"{prefix}_executed_action_mismatch")

        verification = step.get("verification")
        settling = step.get("settling")
        settlement_failure = _has_settlement_failure(settling)
        expected_environment_status = (
            "blocked" if blocked else "unscorable" if settlement_failure else "ok"
        )
        if valid_environment_status and environment_status != expected_environment_status:
            problems.append(f"{prefix}_environment_status_mismatch")
        if blocked:
            if "verification" in step and verification is not None:
                problems.append(f"{prefix}_blocked_verification_present")
            if "settling" in step and settling is not None:
                problems.append(f"{prefix}_blocked_settling_present")
            if step.get("env_resolved") is not False:
                problems.append(f"{prefix}_blocked_resolution_malformed")
        else:
            problems.extend(_settlement_record_problems(settling, f"{prefix}_settling"))

            if isinstance(verification, dict):
                problems.extend(
                    _verification_record_problems(
                        verification, f"{prefix}_verification"
                    )
                )
                if (
                    step.get("env_resolved") != verification.get("env_resolved")
                    and not (
                        settlement_failure
                        and step.get("env_resolved") is False
                    )
                ):
                    problems.append(f"{prefix}_verification_resolution_mismatch")
            elif verification is None:
                if not settlement_failure:
                    problems.append(f"{prefix}_verification_missing")
                if step.get("env_resolved") is not False:
                    problems.append(f"{prefix}_unscorable_resolution_malformed")
            elif "verification" in step:
                problems.append(f"{prefix}_verification_malformed")

            if isinstance(settling, dict):
                for field in ("verification", "last_verification"):
                    if field not in settling:
                        continue
                    nested_verification = settling[field]
                    if nested_verification is None:
                        if verification is not None:
                            problems.append(
                                f"{prefix}_settlement_verification_mismatch"
                            )
                    else:
                        problems.extend(
                            _verification_record_problems(
                                nested_verification,
                                f"{prefix}_settlement_{field}",
                            )
                        )
                        if nested_verification != verification:
                            problems.append(
                                f"{prefix}_settlement_verification_mismatch"
                            )

        if "env_resolved" in step and not isinstance(step["env_resolved"], bool):
            problems.append(f"{prefix}_env_resolved_malformed")

        next_state = step.get("next_state")
        if isinstance(next_state, dict):
            expected_feedback = {
                "previous_policy_action": parsed_action,
                "previous_tool_result": (
                    executed_actions[0].get("result")
                    if isinstance(executed_actions, list)
                    and len(executed_actions) == 1
                    and isinstance(executed_actions[0], dict)
                    else None
                ),
                "verification": verification,
                "env_resolved": step.get("env_resolved"),
            }
            if any(
                key not in next_state or next_state[key] != value
                for key, value in expected_feedback.items()
            ):
                problems.append(f"{prefix}_next_state_feedback_mismatch")

    for index in range(1, len(steps)):
        previous = steps[index - 1]
        current = steps[index]
        if not isinstance(previous, dict) or not isinstance(current, dict):
            continue
        if _policy_step_stops_execution(previous):
            problems.append(f"policy_step_{index}_after_terminal_step")
        if (
            isinstance(previous.get("next_state"), dict)
            and isinstance(current.get("state"), dict)
            and previous["next_state"] != current["state"]
        ):
            problems.append(f"policy_step_{index}_state_chain_mismatch")
        previous_completed = _parse_policy_timestamp(previous.get("completed_at"))
        current_started = _parse_policy_timestamp(current.get("started_at"))
        if (
            previous_completed is not None
            and current_started is not None
            and current_started < previous_completed
        ):
            problems.append(f"policy_step_{index}_starts_before_previous_completed")

    last_step = steps[-1]
    if isinstance(last_step, dict):
        final = _object(remediation.get("final"))
        final_fields = (
            "status",
            "outcome",
            "executed_actions",
            "env_resolved",
            "terminal_block",
            "verification",
        )
        for field in final_fields:
            if field not in final:
                problems.append(f"remediation_final_{field}_missing")

        expected_status = (
            "blocked"
            if isinstance(last_step.get("terminal_block"), dict)
            else "resolved"
            if last_step.get("env_resolved") is True
            else "unresolved"
        )
        if (
            final.get("status") != expected_status
            or final.get("outcome") != expected_status
        ):
            problems.append("remediation_final_status_mismatch")
        if final.get("env_resolved") != last_step.get("env_resolved"):
            problems.append("remediation_final_resolution_mismatch")
        if final.get("terminal_block") != last_step.get("terminal_block"):
            problems.append("remediation_final_terminal_block_mismatch")
        if final.get("verification") != last_step.get("verification"):
            problems.append("remediation_final_verification_mismatch")
        expected_executed_actions = [
            action
            for step in steps
            if isinstance(step, dict)
            and isinstance(step.get("executed_actions"), list)
            for action in step["executed_actions"]
        ]
        if final.get("executed_actions") != expected_executed_actions:
            problems.append("remediation_final_executed_actions_mismatch")

    return problems


def collect_bundle(
    *,
    root: Path,
    bundle: Path,
    experiment_id: str,
    source_sha: str,
    checkpoint: dict[str, Any],
    seed: int,
    process_exit_code: int,
    launch_error: str | None = None,
    checkpoint_postflight_error: str | None = None,
    checkpoint_postflight_verified: bool = False,
) -> dict[str, Any]:
    """Preserve raw attempt/trajectory bytes, including negative and partial runs."""
    if not EXPERIMENT_ID.fullmatch(experiment_id):
        raise ValueError("Experiment ID must be a safe EXP-STAGE4-* name")
    evidence_dir = root / "artifacts/evidence/stage4"
    assets: dict[str, dict[str, Any]] = {}
    for source in sorted(evidence_dir.glob(f"{experiment_id}.*")):
        if source.is_file():
            assets[source.name] = _artifact_copy(source, bundle / source.name, root)
    prefault_path = (
        evidence_dir / ".attempts" / f"{experiment_id}.prefault.json"
    )
    if prefault_path.exists() or prefault_path.is_symlink():
        assets[prefault_path.name] = _artifact_copy(
            prefault_path, bundle / prefault_path.name, root
        )

    primary_path = evidence_dir / f"{experiment_id}.json"
    primary: dict[str, Any] | None = None
    incident: dict[str, Any] | None = None
    problems: list[str] = []
    if primary_path.is_file():
        try:
            loaded = json.loads(primary_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise TypeError("primary evidence is not an object")
            primary = loaded
        except (OSError, ValueError, TypeError) as exc:
            problems.append(f"primary_evidence_unreadable:{type(exc).__name__}")
    else:
        problems.append("primary_evidence_missing")
    problems.extend(
        _cleanup_evidence_problems(
            evidence_dir / f"{experiment_id}.cleanup.json",
            experiment_id,
        )
    )
    if launch_error:
        problems.append(launch_error)
    if not checkpoint_postflight_verified:
        problems.append(checkpoint_postflight_error or "checkpoint_postflight_not_verified")

    phases = _object((primary or {}).get("phases"))
    execution = _object(phases.get("coordinator_execution"))
    incident_id = execution.get("incident_id")
    if isinstance(incident_id, str) and re.fullmatch(r"inc-[A-Za-z0-9_-]+", incident_id):
        trajectory = root / "artifacts/trajectories" / f"{incident_id}.json"
        if trajectory.is_file():
            assets[f"trajectory-{incident_id}.json"] = _artifact_copy(
                trajectory, bundle / f"trajectory-{incident_id}.json", root
            )
            try:
                loaded = json.loads(trajectory.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise TypeError("coordinator record is not an object")
                incident = loaded
            except (OSError, ValueError, TypeError) as exc:
                problems.append(f"coordinator_record_unreadable:{type(exc).__name__}")
        else:
            problems.append("coordinator_record_missing")
    else:
        problems.append("incident_identity_missing")

    if primary and primary.get("experiment_id") != experiment_id:
        problems.append("experiment_identity_mismatch")
    if _object((primary or {}).get("source_identity")).get("git_commit") != source_sha:
        problems.append("source_identity_mismatch")
    if not _object((primary or {}).get("preflight_evidence")).get("persisted_before_injection"):
        problems.append("governed_preflight_missing")
    required_fields = (
        "alert", "incident_anchors", "triage", "diagnosis", "recommender", "approval",
        "remediation", "settling", "verification", "comms",
    )
    for key in required_fields:
        if key == "recommender" and (
            incident is None or incident.get(key) is None
        ):
            continue
        if incident is None or incident.get(key) is None:
            problems.append(f"{key}_missing")
    remediation = _object((incident or {}).get("remediation"))
    policy_steps = remediation.get("policy_steps")
    incident_alert = (incident or {}).get("alert")
    from agents.coordinator import build_incident_anchors, model_visible_alert

    model_alert = model_visible_alert(incident_alert)
    incident_anchors = build_incident_anchors(incident_alert)
    if (incident or {}).get("incident_anchors") != incident_anchors:
        problems.append("incident_anchors_source_mismatch")
    primary_incident_anchors = execution.get("incident_anchors")
    if (
        primary_incident_anchors != (incident or {}).get("incident_anchors")
        or primary_incident_anchors != incident_anchors
    ):
        problems.append("primary_incident_anchors_mismatch")
    problems.extend(
        _policy_steps_problems(
            remediation,
            incident_id,
            model_alert,
            incident_anchors,
        )
    )
    policy = _object(remediation.get("final"))
    if type(seed) is not int or seed < 0:
        problems.append("policy_seed_request_malformed")
    if "generation_seed" not in policy:
        problems.append("policy_generation_seed_missing")
    else:
        observed_seed = policy["generation_seed"]
        if type(observed_seed) is not int or observed_seed < 0:
            problems.append("policy_generation_seed_malformed")
        elif observed_seed != seed:
            problems.append("policy_generation_seed_mismatch")
    if policy.get("policy_backend") != "checkpoint":
        problems.append("checkpoint_policy_execution_unverified")
    recommender = (incident or {}).get("recommender")
    if recommender is not None:
        if not isinstance(recommender, dict):
            problems.append("recommender_malformed")
        else:
            recommender_status = recommender.get("status")
            recommendations = recommender.get("recommended_runbooks")
            if (
                recommender_status is not None
                and (
                    not isinstance(recommender_status, str)
                    or recommender_status
                    not in {"disabled", "unavailable", "executed"}
                )
            ):
                problems.append("recommender_status_invalid")
            has_recommendations = "recommended_runbooks" in recommender
            if has_recommendations and not isinstance(recommendations, list):
                problems.append("recommender_output_malformed")
            if recommender_status is not None and not has_recommendations:
                problems.append("recommender_output_malformed")
            if (
                isinstance(recommender_status, str)
                and recommender_status in {"disabled", "unavailable"}
                and isinstance(recommendations, list)
                and recommendations
            ):
                problems.append("recommender_status_output_mismatch")
    if incident and incident.get("incident_id") != incident_id:
        problems.append("incident_identity_mismatch")

    manifest = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "CAPTURED_FOR_REVIEW" if not problems else "INCOMPLETE",
        "empirical_claim_allowed": False,
        "gate_certification": "NOT_CERTIFIED",
        "process_exit_code": process_exit_code,
        "launch_error": launch_error,
        "source_sha": source_sha,
        "checkpoint": checkpoint,
        "checkpoint_postflight_verified": checkpoint_postflight_verified,
        "policy_seed": seed,
        "incident_id": incident_id,
        "model_identity": _object((primary or {}).get("protocol_profile")).get("model"),
        "recorded_g4_verdict": (primary or {}).get("gate_g4_pass"),
        "recorded_env_resolved": _object((incident or {}).get("verification")).get("env_resolved"),
        "elapsed_seconds": (primary or {}).get("duration_seconds"),
        "time_to_resolve_s": None,
        "reward": None,
        "record_fields_present": {
            key: incident.get(key) is not None if incident else False
            for key in required_fields
        },
        "policy_step_count": len(policy_steps) if isinstance(policy_steps, list) else 0,
        "problems": problems,
        "assets": assets,
    }
    target = bundle / "g12_capture_manifest.json"
    temporary = bundle / ".g12_capture_manifest.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, target)
    return manifest


def run_live_episode(
    *,
    checkpoint: Path,
    experiment_id: str,
    bundle: Path,
    seed: int,
    execute_live_chaos: bool = False,
    kube_context: str | None = None,
) -> dict[str, Any]:
    if execute_live_chaos is not True:
        raise PermissionError("G12 live capture requires explicit --execute-live-chaos")
    if kube_context != METRICS_SERVER_CONTEXT:
        raise ValueError(
            f"G12 live capture requires --kube-context {METRICS_SERVER_CONTEXT}"
        )
    if not EXPERIMENT_ID.fullmatch(experiment_id):
        raise ValueError("Experiment ID must be a safe, unique EXP-STAGE4-* name")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("Policy seed must be a non-negative integer")
    root = ROOT.resolve()
    output = bundle.expanduser().resolve()
    if output == root or output.is_relative_to(root) or output.exists():
        raise ValueError("Bundle directory must be new and outside the experiment checkout")
    source_sha = _source_sha(root)
    provenance = _checkpoint_record(checkpoint.expanduser().resolve())
    evidence_dir = root / "artifacts/evidence/stage4"
    existing = next(evidence_dir.glob(f"{experiment_id}.*"), None)
    if existing is not None:
        raise FileExistsError(f"Stage 4 attempt artifact already exists: {existing}")

    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["ATLASOPS_REMEDIATION_BACKEND"] = "rl_policy"
    env["ATLASOPS_RL_POLICY_CHECKPOINT"] = str(checkpoint.expanduser().resolve())
    env["ATLASOPS_RL_POLICY_EXECUTE_ACTIONS"] = "1"
    env["KUBECONFIG_CONTEXT"] = kube_context
    env["ATLASOPS_POLICY_SEED"] = str(seed)
    env["STAGE4_EXPERIMENT_ID"] = experiment_id
    launch_error = None
    try:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.run_stage4_golden_incident"],
            cwd=root,
            env=env,
            check=False,
        )
        exit_code = result.returncode
    except KeyboardInterrupt:
        exit_code = 130
        launch_error = "harness_interrupted"
    except OSError as exc:
        exit_code = 127
        launch_error = f"harness_launch_failed:{type(exc).__name__}"
    try:
        if _checkpoint_record(checkpoint.expanduser().resolve()) != provenance:
            checkpoint_postflight_error = "checkpoint_changed_during_run"
        else:
            checkpoint_postflight_error = None
    except (OSError, ValueError, TypeError) as exc:
        checkpoint_postflight_error = f"checkpoint_postflight_unverifiable:{type(exc).__name__}"
    return collect_bundle(
        root=root,
        bundle=output,
        experiment_id=experiment_id,
        source_sha=source_sha,
        checkpoint=provenance,
        seed=seed,
        process_exit_code=exit_code,
        launch_error=launch_error,
        checkpoint_postflight_error=checkpoint_postflight_error,
        checkpoint_postflight_verified=checkpoint_postflight_error is None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Governed G12 integrated evidence capture")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--execute-live-chaos",
        action="store_true",
        help="Run the Stage 4 harness with real cluster actions after its preflight",
    )
    parser.add_argument(
        "--kube-context",
        help="Explicit Stage 4 Kubernetes context for the integrated policy",
    )
    args = parser.parse_args()
    if not args.execute_live_chaos:
        parser.error("No run started: live Chaos requires --execute-live-chaos and separate authorization")
    if args.kube_context != METRICS_SERVER_CONTEXT:
        parser.error(f"G12 requires --kube-context {METRICS_SERVER_CONTEXT}")
    manifest = run_live_episode(
        checkpoint=args.checkpoint,
        experiment_id=args.experiment_id,
        bundle=args.bundle_dir,
        seed=args.seed,
        execute_live_chaos=args.execute_live_chaos,
        kube_context=args.kube_context,
    )
    print(f"G12 evidence capture: {manifest['status']} (NOT_CERTIFIED)")
    if manifest["process_exit_code"] or manifest["status"] != "CAPTURED_FOR_REVIEW":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
