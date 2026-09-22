"""Direct GRPO policy-action-environment adapter.

The generated completion is parsed as one atomic action and is the exact action
validated and executed. No second operational LLM is involved.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Mapping
from typing import Any

from agents.coordinator import _check_tool_policy
from agents.tool_policy import CLUSTER_MUTATING_TOOLS
from agents.tools import TOOL_REGISTRY
from agents.verifier import verify_environment

TERMINAL_BLOCK_CATEGORIES = frozenset(
    {
        "approval_required",
        "invalid_action",
        "missing_evidence",
        "policy_block",
        "tool_unavailable",
    }
)


def parse_policy_action(completion_text: str) -> dict[str, Any]:
    """Parse exactly one structured policy action from a completion."""
    try:
        payload = json.loads(completion_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Policy completion must be one JSON object") from exc
    if not isinstance(payload, dict):
        raise TypeError("Policy completion must be a JSON object")
    if "actions" in payload:
        raise ValueError("Policy completion must not contain multiple actions")
    tool = payload.get("tool")
    arguments = payload.get("arguments")
    if not isinstance(tool, str) or not tool:
        raise ValueError("Policy action requires a tool name")
    if not isinstance(arguments, dict):
        raise TypeError("Policy action arguments must be a JSON object")
    return {
        "tool": tool,
        "arguments": arguments,
        "agent_claimed_resolved": payload.get("agent_claimed_resolved") is True,
    }


def _approval_allows_mutation(state: dict[str, Any]) -> bool:
    triage = state.get("triage") or {}
    if str(triage.get("severity", "")).upper() != "P1":
        return True
    approval = state.get("approval")
    if not isinstance(approval, dict):
        return False
    decision = approval.get("decision") or approval.get("status")
    return str(decision).lower() == "approved"


def _rollback_has_evidence(arguments: dict[str, Any], state: dict[str, Any]) -> bool:
    if not arguments.get("revision"):
        return False
    observations = state.get("observations") or {}
    history = observations.get("argocd_history") or []
    return any(
        str(item.get("revision")) == str(arguments["revision"])
        for item in history
        if isinstance(item, dict)
    )


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class DirectPolicyEnvironment:
    """Validate, execute, settle, and verify one policy-produced action."""

    def __init__(
        self,
        *,
        tool_registry: Mapping[str, Callable[..., Any]] | None = None,
        policy_check: Callable[[str, str, dict[str, Any], dict[str, Any]], str | None]
        | None = None,
        verifier: Callable[..., Any] | None = None,
        settle: Callable[[], Any] | None = None,
    ) -> None:
        self.tool_registry = TOOL_REGISTRY if tool_registry is None else tool_registry
        self.policy_check = policy_check or _check_tool_policy
        self.verifier = verifier or verify_environment
        self.settle = settle or (lambda: asyncio.sleep(0))

    async def step(
        self,
        completion_text: str,
        *,
        scenario_id: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply at most one exact policy action, followed by one verifier read."""
        try:
            action = parse_policy_action(completion_text)
        except (TypeError, ValueError) as exc:
            return self._blocked("invalid_action", str(exc), completion_text)

        tool = action["tool"]
        arguments = action["arguments"]
        if tool not in self.tool_registry:
            return self._blocked("tool_unavailable", f"Unknown tool: {tool}", completion_text)
        if tool in CLUSTER_MUTATING_TOOLS and not _approval_allows_mutation(state):
            return self._blocked(
                "approval_required",
                "P1 mutation requires explicit approved decision",
                completion_text,
                action,
            )
        if tool == "argocd_rollback" and not _rollback_has_evidence(arguments, state):
            return self._blocked(
                "missing_evidence",
                "argocd_rollback requires observed history for the exact revision",
                completion_text,
                action,
            )
        policy_error = self.policy_check("remediation", tool, arguments, state)
        if policy_error:
            return self._blocked(
                "policy_block",
                policy_error,
                completion_text,
                action,
            )

        tool_result = await _maybe_await(self.tool_registry[tool](**arguments))
        await _maybe_await(self.settle())
        verification_result = await _maybe_await(
            self.verifier(
                scenario_id=scenario_id,
                agent_claimed_resolved=action["agent_claimed_resolved"],
                alert=state.get("alert"),
                incident_context={
                    **state,
                    "policy_action": action,
                    "tool_result": tool_result,
                },
            )
        )
        verification = (
            verification_result.to_dict()
            if hasattr(verification_result, "to_dict")
            else dict(verification_result)
        )
        env_resolved = verification.get("env_resolved") is True
        return {
            "status": "ok",
            "policy_completion": completion_text,
            "policy_action": action,
            "executed_actions": [
                {
                    "tool": tool,
                    "arguments": arguments,
                    "result": tool_result,
                }
            ],
            "verification": verification,
            "env_resolved": env_resolved,
            "resolved": env_resolved,
            "agent_claimed_resolved": action["agent_claimed_resolved"],
            "outcome": "resolved" if env_resolved else "unresolved",
            "terminal_block": None,
        }

    @staticmethod
    def _blocked(
        category: str,
        reason: str,
        completion_text: str,
        action: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "blocked",
            "policy_completion": completion_text,
            "policy_action": action,
            "executed_actions": [],
            "verification": None,
            "env_resolved": False,
            "resolved": False,
            "agent_claimed_resolved": bool(
                action and action.get("agent_claimed_resolved")
            ),
            "outcome": "blocked",
            "terminal_block": {"category": category, "reason": reason},
        }
