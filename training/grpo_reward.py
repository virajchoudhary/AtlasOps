"""Objective reward for one directly executed GRPO policy action."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

_TRL_ADVANTAGE_EPSILON = 1e-4
ENVIRONMENTAL_REWARD_COMPONENTS = (
    "r_verified_resolution",
    "r_required_check_coverage",
)
ENVIRONMENTAL_GRADIENT_COMPONENT = "environmental_verification_policy"


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

    expected_advantages = normalize_group_advantages(reward_values)
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


def normalize_group_advantages(rewards: Sequence[float]) -> list[float]:
    """Return the pinned TRL 0.19.1 sample-standard-deviation advantages."""
    if isinstance(rewards, (str, bytes)) or not isinstance(rewards, Sequence):
        raise TypeError("GRPO group rewards must be a sequence")
    if len(rewards) < 2:
        raise ValueError("GRPO group evidence requires at least two rewards")

    values = []
    for value in rewards:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
        ):
            raise ValueError("GRPO group contains an invalid objective reward")
        values.append(float(value))

    reward_mean = sum(values) / len(values)
    reward_std = math.sqrt(
        sum((value - reward_mean) ** 2 for value in values)
        / (len(values) - 1)
    )
    return [
        (value - reward_mean) / (reward_std + _TRL_ADVANTAGE_EPSILON)
        for value in values
    ]


def has_environmental_reward_signal(
    components: Sequence[Mapping[str, Any]],
    admissible: Sequence[bool],
    advantages: Sequence[float],
) -> bool:
    """Require varied verifier components among admissible samples in one group."""
    if (
        isinstance(components, (str, bytes))
        or not isinstance(components, Sequence)
        or isinstance(admissible, (str, bytes))
        or not isinstance(admissible, Sequence)
    ):
        raise TypeError("GRPO environmental components and admissibility must be sequences")
    if len(components) < 2 or len(components) != len(admissible):
        raise ValueError("GRPO environmental group sample counts differ")

    environmental_rewards = []
    admissible_indices = []
    component_values = []
    for index, (sample_components, sample_admissible) in enumerate(
        zip(components, admissible, strict=True)
    ):
        if type(sample_admissible) is not bool:
            raise ValueError("GRPO environmental admissibility must be boolean")
        if not isinstance(sample_components, Mapping) or set(sample_components) != set(
            ENVIRONMENTAL_REWARD_COMPONENTS
        ):
            raise ValueError("GRPO environmental verifier components are incomplete")
        values = []
        for name in ENVIRONMENTAL_REWARD_COMPONENTS:
            value = sample_components[name]
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
            ):
                raise ValueError("GRPO environmental verifier component is invalid")
            values.append(float(value))
        environmental_rewards.append(sum(values))
        component_values.append(values)
        if sample_admissible:
            admissible_indices.append(index)

    varied_components = any(
        component_values[left] != component_values[right]
        for position, left in enumerate(admissible_indices)
        for right in admissible_indices[position + 1 :]
    )
    reward_signal = has_reward_driven_advantages(
        environmental_rewards, advantages
    )
    return len(admissible_indices) >= 2 and varied_components and reward_signal


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
