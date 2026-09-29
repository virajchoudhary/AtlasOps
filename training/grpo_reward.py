"""Objective reward for one directly executed GRPO policy action."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def score_direct_action_step(
    verification: Mapping[str, Any],
    *,
    agent_claimed_resolved: bool,
) -> dict[str, float | int]:
    """Score a conclusive verifier observation without inventing judge evidence."""
    status = verification.get("verification_status")
    resolved = verification.get("env_resolved")
    if status not in {"passed", "failed"} or type(resolved) is not bool:
        raise ValueError("Direct-action reward requires conclusive verification")
    if (status == "passed") is not resolved:
        raise ValueError("Direct-action verifier status conflicts with resolution")

    checks = verification.get("checks")
    if not isinstance(checks, list):
        raise ValueError("Direct-action reward requires objective checks")
    required = []
    for check in checks:
        if not isinstance(check, Mapping) or type(check.get("passed")) is not bool:
            raise ValueError("Direct-action reward received an invalid objective check")
        required_flag = check.get("required", True)
        if type(required_flag) is not bool:
            raise ValueError("Direct-action reward received an invalid required flag")
        if required_flag:
            required.append(check)
    if not required:
        raise ValueError("Direct-action reward requires a required objective check")

    passed = sum(check["passed"] for check in required)
    if (passed == len(required)) is not resolved:
        raise ValueError("Direct-action verifier status conflicts with required checks")
    coverage = passed / len(required)
    resolution_reward = 0.75 if resolved else 0.0
    check_reward = 0.25 * coverage
    false_claim_penalty = -0.25 if agent_claimed_resolved and not resolved else 0.0
    return {
        "r_verified_resolution": round(resolution_reward, 6),
        "r_required_check_coverage": round(check_reward, 6),
        "penalty_false_resolution": false_claim_penalty,
        "required_checks": len(required),
        "passed_required_checks": passed,
        "required_check_coverage": round(coverage, 6),
        "total": round(
            resolution_reward + check_reward + false_claim_penalty,
            6,
        ),
    }


def aggregate_direct_action_steps(
    steps: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Average recorded, scorable step totals without rewarding extra tool calls."""
    if not steps:
        raise ValueError("Direct-action episode reward requires at least one step")
    totals = []
    for step in steps:
        total = step.get("total")
        if (
            not isinstance(total, int | float)
            or isinstance(total, bool)
            or not math.isfinite(float(total))
        ):
            raise ValueError("Direct-action episode contains an invalid step reward")
        totals.append(float(total))
    return {
        "aggregation": "mean",
        "step_count": len(totals),
        "step_totals": totals,
        "total": round(sum(totals) / len(totals), 6),
    }
