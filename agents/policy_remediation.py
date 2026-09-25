"""Checkpoint policy remediation for the integrated incident path.

Each policy decision executes through the direct action environment, which
validates one action and observes the verifier before another decision.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

from agents.coordinator import _strip_model_forbidden_context
from training.grpo_environment import DirectPolicyEnvironment


async def run_policy_remediation(
    *,
    policy: Any,
    state: dict[str, Any],
    scenario_id: str,
    environment: DirectPolicyEnvironment,
    seed: int,
    generation_config: dict[str, Any],
    max_actions: int = 3,
    policy_origin: str = "injected_non_empirical",
) -> dict[str, Any]:
    """Run bounded policy decisions with verifier feedback after each action."""
    if max_actions < 1:
        raise ValueError("max_actions must be positive")

    current_state = _strip_model_forbidden_context(state)
    steps: list[dict[str, Any]] = []
    tool_trajectory: list[dict[str, Any]] = []
    all_executed: list[dict[str, Any]] = []

    for index in range(max_actions):
        if current_state.get("env_resolved") is True:
            break
        started_at = datetime.now(UTC).isoformat()
        generated = policy.generate(
            current_state,
            seed=seed + index,
            generation_config=generation_config,
        )
        raw_completion = await generated if inspect.isawaitable(generated) else generated
        if not isinstance(raw_completion, str):
            raise TypeError("Policy generation must return the raw completion string")

        result = await environment.step(
            raw_completion,
            scenario_id=scenario_id,
            state=current_state,
        )
        executed = result.get("executed_actions") or []
        all_executed.extend(executed)
        for action in executed:
            tool_trajectory.append(
                {
                    "role": "remediation",
                    "tool": action["tool"],
                    "arguments": action["arguments"],
                    "output": action.get("result"),
                    "verifier_observation": result.get("verification"),
                }
            )
        next_state = {
            **current_state,
            "previous_policy_action": result.get("policy_action"),
            "previous_tool_result": executed[0].get("result") if executed else None,
            "verification": result.get("verification"),
            "env_resolved": result.get("env_resolved") is True,
        }
        next_state = _strip_model_forbidden_context(next_state)
        steps.append(
            {
                "index": index,
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "state": current_state,
                "raw_policy_output": raw_completion,
                "parsed_action": result.get("policy_action"),
                "executed_actions": executed,
                "verification": result.get("verification"),
                "settling": result.get("settling"),
                "env_resolved": result.get("env_resolved") is True,
                "terminal_block": result.get("terminal_block"),
                "next_state": next_state,
            }
        )
        current_state = next_state
        if result.get("status") != "ok" or current_state["env_resolved"]:
            break

    last = steps[-1] if steps else None
    env_resolved = bool(last and last["env_resolved"])
    status = "resolved" if env_resolved else "unresolved"
    if last and last["terminal_block"]:
        status = "blocked"
    return {
        "role": "remediation",
        "trajectory": tool_trajectory,
        "policy_steps": steps,
        "final": {
            "incident_id": state.get("incident_id"),
            "status": status,
            "outcome": status,
            "executed_actions": all_executed,
            "verification": last["verification"] if last else None,
            "env_resolved": env_resolved,
            "terminal_block": last["terminal_block"] if last else None,
            "policy_backend": policy_origin,
            "evidence_class": (
                "runtime_execution_unclassified"
                if policy_origin == "checkpoint"
                else "NON_EMPIRICAL"
            ),
            "generation_seed": seed,
            "generation_config": generation_config,
        },
    }
