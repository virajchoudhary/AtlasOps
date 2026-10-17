"""Objective reward for one directly executed GRPO policy action."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

_TRL_ADVANTAGE_EPSILON = 1e-4


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


def has_reward_driven_advantages(
    rewards: Sequence[float],
    advantages: Sequence[float],
) -> bool:
    """Validate the pinned TRL 0.19.1 group-normalized reward advantages."""
    if (
        isinstance(rewards, (str, bytes))
        or not isinstance(rewards, Sequence)
        or isinstance(advantages, (str, bytes))
        or not isinstance(advantages, Sequence)
    ):
        raise TypeError("GRPO group rewards and advantages must be sequences")
    if len(rewards) < 2 or not advantages:
        raise ValueError("GRPO group evidence requires rewards and advantages")
    if len(rewards) != len(advantages):
        raise ValueError("GRPO group reward and advantage sample counts differ")

    reward_values = []
    for value in rewards:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
        ):
            raise ValueError("GRPO group contains an invalid objective reward")
        reward_values.append(float(value))

    advantage_values = []
    for value in advantages:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
        ):
            raise ValueError("GRPO group contains an invalid advantage")
        advantage_values.append(float(value))

    reward_mean = sum(reward_values) / len(reward_values)
    reward_std = math.sqrt(
        sum((value - reward_mean) ** 2 for value in reward_values)
        / (len(reward_values) - 1)
    )
    expected_advantages = [
        (value - reward_mean) / (reward_std + _TRL_ADVANTAGE_EPSILON)
        for value in reward_values
    ]
    for observed, expected in zip(
        advantage_values, expected_advantages, strict=True
    ):
        if not math.isclose(observed, expected, rel_tol=1e-5, abs_tol=1e-6):
            raise ValueError(
                "GRPO advantages do not match pinned TRL group normalization"
            )

    rewards_varied = any(value != reward_values[0] for value in reward_values[1:])
    advantages_nonzero = any(value != 0.0 for value in advantage_values)
    if advantages_nonzero and not rewards_varied:
        raise ValueError("Equal-reward groups cannot have reward-driven advantages")
    return rewards_varied and advantages_nonzero


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
