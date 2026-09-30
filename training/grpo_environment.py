"""Direct GRPO policy-action-environment adapter.

The generated completion is parsed as one atomic action and is the exact action
validated and executed. No second operational LLM is involved.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from agents.approval import (
    ActionApprovalPermit,
    ApprovalGate,
    approval_mode_for_severity,
)
from agents.coordinator import _check_tool_policy
from agents.tool_policy import CLUSTER_MUTATING_TOOLS
from agents.tools import TOOL_REGISTRY
from agents.tools.kubectl import _G9_KUBECONFIG_CONTEXT
from agents.verifier import verify_environment

_BUILTIN_TOOL_REGISTRY = TOOL_REGISTRY
POST_ACTION_SETTLE_TIMEOUT_SECONDS = 30.0
POST_ACTION_SETTLE_POLL_INTERVAL_SECONDS = 1.0
POST_ACTION_SETTLE_STABLE_OBSERVATIONS = 2
_UNTRUSTED_AUTHORIZATION_KEYS = frozenset(
    {"approval", "_runtime_control"}
)

TERMINAL_BLOCK_CATEGORIES = frozenset(
    {
        "already_resolved",
        "approval_required",
        "invalid_action",
        "missing_evidence",
        "policy_block",
        "tool_unavailable",
    }
)


def uses_builtin_tool_registry(tool_registry: Any) -> bool:
    if tool_registry is _BUILTIN_TOOL_REGISTRY:
        return True
    return isinstance(tool_registry, Mapping) and any(
        name in _BUILTIN_TOOL_REGISTRY and function is _BUILTIN_TOOL_REGISTRY[name]
        for name, function in tool_registry.items()
    )


def strip_untrusted_approval_context(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: strip_untrusted_approval_context(child)
            for key, child in value.items()
            if str(key).strip().casefold() not in _UNTRUSTED_AUTHORIZATION_KEYS
        }
    if isinstance(value, list):
        return [strip_untrusted_approval_context(child) for child in value]
    return value


def require_live_kube_context(
    execute_actions: bool,
    kube_context: str | None,
    *,
    opt_in_flag: str,
) -> str:
    if execute_actions is not True:
        raise PermissionError(
            f"G9 live environment calls require the explicit {opt_in_flag} opt-in"
        )
    if not isinstance(kube_context, str) or not kube_context.strip():
        raise ValueError("G9 live environment calls require a named --kube-context")
    context = kube_context.strip()
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in context):
        raise ValueError("--kube-context must not contain control characters")
    return context


@contextmanager
def _scoped_kube_context(kube_context: str) -> Iterator[None]:
    token = _G9_KUBECONFIG_CONTEXT.set(kube_context)
    try:
        yield
    finally:
        _G9_KUBECONFIG_CONTEXT.reset(token)


def _call_with_kube_context(
    function: Callable[..., Any],
    kube_context: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    with _scoped_kube_context(kube_context):
        result = function(*args, **kwargs)
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise TypeError("Live G9 Kubernetes tool and verifier callbacks must be synchronous")
    return result


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"Policy completion contains duplicate object key: {key}")
        value[key] = child
    return value


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"Policy completion numbers must be finite: {value}")


def _validate_finite_json_numbers(value: Any) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Policy completion numbers must be finite")
    elif isinstance(value, dict):
        for child in value.values():
            _validate_finite_json_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _validate_finite_json_numbers(child)


def parse_policy_action(completion_text: str) -> dict[str, Any]:
    """Parse exactly one structured policy action from a completion."""
    try:
        payload = json.loads(
            completion_text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_nonfinite_json_constant,
        )
        _validate_finite_json_numbers(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Policy completion must be one JSON object") from exc
    except RecursionError as exc:
        raise ValueError("Policy completion JSON nesting is too deep") from exc
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
    if "agent_claimed_resolved" not in payload:
        raise ValueError("Policy action requires agent_claimed_resolved")
    agent_claimed_resolved = payload["agent_claimed_resolved"]
    if type(agent_claimed_resolved) is not bool:
        raise TypeError("Policy action agent_claimed_resolved must be a JSON boolean")
    return {
        "tool": tool,
        "arguments": arguments,
        "agent_claimed_resolved": agent_claimed_resolved,
    }


def policy_action_requires_operator_approval(
    action: Mapping[str, Any],
    state: Mapping[str, Any],
) -> bool:
    if action.get("tool") not in CLUSTER_MUTATING_TOOLS:
        return False
    triage = state.get("triage") or {}
    return approval_mode_for_severity(str(triage.get("severity", ""))) == "approve"


def _approval_allows_mutation(
    state: Mapping[str, Any],
    action: Mapping[str, Any],
    *,
    approval_gate: ApprovalGate | None,
    approval_permit: ActionApprovalPermit | None,
) -> bool:
    mode = approval_mode_for_severity(
        str((state.get("triage") or {}).get("severity", ""))
    )
    if mode == "manual":
        return False
    if mode == "auto":
        return True
    incident_id = state.get("incident_id")
    if approval_gate is None or not isinstance(incident_id, str) or not incident_id:
        return False
    try:
        digest = approval_gate.action_digest(action)
    except (TypeError, ValueError):
        return False
    return approval_gate.consume_action_permit(
        approval_permit,
        incident_id=incident_id,
        action_digest=digest,
    )


def _rollback_has_evidence(
    arguments: dict[str, Any], observation: Mapping[str, Any]
) -> bool:
    observed_status = observation.get("observation_status")
    if (
        observation.get("success") is not True
        or (
            observed_status is not None
            and (not isinstance(observed_status, str) or observed_status != "observed")
        )
        or not arguments.get("app")
        or not arguments.get("revision")
    ):
        return False
    history = observation.get("history") or []
    if not isinstance(history, list):
        return False
    return any(
        str(value) == str(arguments["revision"])
        for item in history
        if isinstance(item, dict)
        for value in (item.get("id"), item.get("revision"))
        if value is not None
    )


def _chaos_has_evidence(
    arguments: dict[str, Any], observation: Mapping[str, Any]
) -> bool:
    if (
        observation.get("success") is not True
        or observation.get("observation_status") != "observed"
    ):
        return False
    active = observation.get("active_experiments")
    if not isinstance(active, list):
        return False
    expected = (
        str(arguments.get("kind") or "").casefold(),
        str(arguments.get("name") or ""),
        str(arguments.get("namespace") or "chaos-mesh").casefold(),
    )
    return any(
        isinstance(item, dict)
        and (
            str(item.get("kind") or "").casefold(),
            str(item.get("name") or ""),
            str(item.get("namespace") or "").casefold(),
        ) == expected
        for item in active
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
        execute_live_chaos: bool = False,
        kube_context: str | None = None,
        _action_approval_gate: ApprovalGate | None = None,
        settle_clock: Callable[[], float] | None = None,
        settle_sleep: Callable[[float], Any] | None = None,
    ) -> None:
        self.tool_registry = TOOL_REGISTRY if tool_registry is None else tool_registry
        self.policy_check = policy_check or _check_tool_policy
        self.verifier = verifier or verify_environment
        self.settle = settle or (lambda: asyncio.sleep(0))
        self.has_custom_settle = settle is not None
        self.uses_builtin_settle = settle is None and verifier is None
        self.has_custom_settle_timing = (
            settle_clock is not None or settle_sleep is not None
        )
        self._settle_clock = settle_clock or time.monotonic
        self._settle_sleep = settle_sleep or asyncio.sleep
        if execute_live_chaos or kube_context is not None:
            self.kube_context = require_live_kube_context(
                execute_live_chaos,
                kube_context,
                opt_in_flag="--execute-live-chaos",
            )
        else:
            self.kube_context = None
        self.execute_live_chaos = execute_live_chaos
        self._action_approval_gate = _action_approval_gate

    def invoke_tool(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        kube_context = require_live_kube_context(
            self.execute_live_chaos,
            self.kube_context,
            opt_in_flag="--execute-live-chaos",
        )
        return _call_with_kube_context(
            function,
            kube_context,
            *args,
            **kwargs,
        )

    @staticmethod
    def _verification_record(result: Any) -> dict[str, Any]:
        value = result.to_dict() if callable(getattr(result, "to_dict", None)) else result
        if not isinstance(value, Mapping):
            raise TypeError("Environment verifier must return an object")
        return dict(value)

    @staticmethod
    def _settle_observation(
        verification: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        status = verification.get("verification_status")
        env_resolved = verification.get("env_resolved")
        if status not in {"passed", "failed"} or not isinstance(env_resolved, bool):
            raise ValueError("Verifier did not return a conclusive objective status")
        if (status == "passed") is not env_resolved:
            raise ValueError("Verifier status conflicts with env_resolved")
        checks = verification.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("Verifier did not return objective checks")

        objective_checks = []
        signature_checks = []
        failed_required = False
        for check in checks:
            if not isinstance(check, Mapping) or not isinstance(check.get("passed"), bool):
                raise ValueError("Verifier returned a malformed objective check")
            required_flag = check.get("required", True)
            if type(required_flag) is not bool:
                raise ValueError("Verifier returned a malformed required check flag")
            required = required_flag
            failed_required = failed_required or (required and not check["passed"])
            signature_checks.append(
                {
                    "name": check.get("name"),
                    "target": check.get("target"),
                    "required": required,
                    "passed": check["passed"],
                }
            )
            objective_checks.append(
                {
                    "name": check.get("name"),
                    "target": check.get("target"),
                    "required": required,
                    "passed": check["passed"],
                    "observed": check.get("observed"),
                }
            )
        if status == "failed" and not failed_required:
            raise ValueError("Verifier failed status lacks a failed required check")
        if status == "passed" and failed_required:
            raise ValueError("Verifier passed status has a failed required check")

        observed_metrics = verification.get("observed_metrics", {})
        if not isinstance(observed_metrics, Mapping):
            raise ValueError("Verifier returned malformed observed metrics")
        snapshot = {
            "verification_status": status,
            "env_resolved": env_resolved,
            "failed_checks": verification.get("failed_checks", []),
            "checks": signature_checks,
        }
        signature = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        observation = {
            **snapshot,
            "checks": objective_checks,
            "observed_metrics": dict(observed_metrics),
        }
        return signature, observation

    async def _settle_after_action(
        self,
        *,
        scenario_id: str,
        state: Mapping[str, Any],
        action: Mapping[str, Any],
        pre_action_observation: Mapping[str, Any] | None,
        tool_result: Any,
    ) -> dict[str, Any]:
        started = self._settle_clock()
        deadline = started + POST_ACTION_SETTLE_TIMEOUT_SECONDS
        max_observations = (
            math.ceil(
                POST_ACTION_SETTLE_TIMEOUT_SECONDS
                / POST_ACTION_SETTLE_POLL_INTERVAL_SECONDS
            )
            + 1
        )
        observations: list[dict[str, Any]] = []
        previous_signature: str | None = None
        stable_observations = 0
        last_verification: dict[str, Any] | None = None
        last_status: str | None = None

        def failure(status: str, reason: str) -> dict[str, Any]:
            return {
                "status": status,
                "settled": False,
                "stable": False,
                "timeout_seconds": POST_ACTION_SETTLE_TIMEOUT_SECONDS,
                "poll_interval_seconds": POST_ACTION_SETTLE_POLL_INTERVAL_SECONDS,
                "required_stable_observations": POST_ACTION_SETTLE_STABLE_OBSERVATIONS,
                "stable_observations": stable_observations,
                "observation_count": len(observations),
                "elapsed_s": round(self._settle_clock() - started, 6),
                "last_verification_status": last_status,
                "last_verification": last_verification,
                "observations": observations,
                "failure": reason,
            }

        for _ in range(max_observations):
            now = self._settle_clock()
            if observations and now >= deadline:
                return failure("timeout", "post_action_settle_timeout")
            try:
                result = self.invoke_tool(
                    self.verifier,
                    scenario_id=scenario_id,
                    agent_claimed_resolved=action.get("agent_claimed_resolved") is True,
                    alert=state.get("alert"),
                    incident_context={
                        **dict(state),
                        "policy_action": dict(action),
                        "pre_action_observation": pre_action_observation,
                        "tool_result": tool_result,
                        "settle_observation": len(observations) + 1,
                    },
                )
                verification = self._verification_record(result)
            except Exception as exc:  # noqa: BLE001 - an observation error cannot be scored
                return failure(
                    "error",
                    f"post_action_verifier_error:{type(exc).__name__}",
                )

            last_verification = verification
            status = verification.get("verification_status")
            last_status = status if isinstance(status, str) else None
            verification_finished_at = self._settle_clock()
            if verification_finished_at >= deadline:
                observations.append(
                    {
                        "elapsed_s": round(verification_finished_at - started, 6),
                        "verification_status": last_status,
                        "timed_out": True,
                    }
                )
                return failure("timeout", "post_action_settle_timeout")
            if status == "error":
                observations.append(
                    {
                        "elapsed_s": round(now - started, 6),
                        "verification_status": "error",
                        "env_resolved": verification.get("env_resolved"),
                        "error": verification.get("error"),
                    }
                )
                return failure("error", "post_action_verifier_error")
            if status not in {"passed", "failed", "inconclusive"}:
                return failure("error", "post_action_verifier_status_missing_or_invalid")

            if status == "inconclusive":
                stable_observations = 0
                previous_signature = None
                observation = {
                    "elapsed_s": round(now - started, 6),
                    "verification_status": status,
                    "env_resolved": verification.get("env_resolved"),
                    "error": verification.get("error"),
                    "failed_checks": verification.get("failed_checks", []),
                }
            else:
                try:
                    signature, objective = self._settle_observation(verification)
                except (TypeError, ValueError) as exc:
                    observations.append(
                        {
                            "elapsed_s": round(now - started, 6),
                            "verification_status": status,
                            "env_resolved": verification.get("env_resolved"),
                            "error": f"malformed_objective_observation:{type(exc).__name__}",
                        }
                    )
                    return failure("error", "post_action_objective_observation_invalid")
                stable_observations = (
                    stable_observations + 1
                    if signature == previous_signature
                    else 1
                )
                previous_signature = signature
                observation = {
                    "elapsed_s": round(now - started, 6),
                    **objective,
                }

            observations.append(observation)
            if (
                status in {"passed", "failed"}
                and stable_observations >= POST_ACTION_SETTLE_STABLE_OBSERVATIONS
            ):
                return {
                    "status": "settled",
                    "settled": True,
                    "stable": True,
                    "timeout_seconds": POST_ACTION_SETTLE_TIMEOUT_SECONDS,
                    "poll_interval_seconds": POST_ACTION_SETTLE_POLL_INTERVAL_SECONDS,
                    "required_stable_observations": POST_ACTION_SETTLE_STABLE_OBSERVATIONS,
                    "stable_observations": stable_observations,
                    "observation_count": len(observations),
                    "elapsed_s": round(self._settle_clock() - started, 6),
                    "verification_status": status,
                    "observations": observations,
                    "verification": verification,
                }

            remaining = deadline - self._settle_clock()
            if remaining <= 0:
                return failure("timeout", "post_action_settle_timeout")
            if len(observations) >= max_observations:
                return failure("timeout", "post_action_settle_observation_limit")
            try:
                await _maybe_await(
                    self._settle_sleep(
                        min(POST_ACTION_SETTLE_POLL_INTERVAL_SECONDS, remaining)
                    )
                )
            except Exception as exc:  # noqa: BLE001 - a failed wait cannot establish settling
                return failure(
                    "error",
                    f"post_action_settle_wait_error:{type(exc).__name__}",
                )

        return failure("timeout", "post_action_settle_observation_limit")

    @staticmethod
    def _unscorable(
        completion_text: str,
        action: Mapping[str, Any],
        pre_action_observation: Mapping[str, Any] | None,
        tool: str,
        arguments: Mapping[str, Any],
        tool_result: Any,
        settling: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": "unscorable",
            "scorable": False,
            "policy_completion": completion_text,
            "policy_action": dict(action),
            "pre_action_observation": pre_action_observation,
            "executed_actions": [
                {
                    "tool": tool,
                    "arguments": dict(arguments),
                    "result": tool_result,
                }
            ],
            "verification": settling.get("verification")
            or settling.get("last_verification"),
            "settling": dict(settling),
            "env_resolved": False,
            "resolved": False,
            "agent_claimed_resolved": action.get("agent_claimed_resolved") is True,
            "outcome": "unscorable",
            "terminal_block": None,
            "failure": settling.get("failure", "post_action_settle_unscorable"),
        }

    async def step(
        self,
        completion_text: str,
        *,
        scenario_id: str,
        state: dict[str, Any],
        _action_approval_permit: ActionApprovalPermit | None = None,
    ) -> dict[str, Any]:
        """Apply at most one exact policy action, followed by one verifier read."""
        require_live_kube_context(
            self.execute_live_chaos,
            self.kube_context,
            opt_in_flag="--execute-live-chaos",
        )
        try:
            action = parse_policy_action(completion_text)
        except (TypeError, ValueError) as exc:
            return self._blocked("invalid_action", str(exc), completion_text)

        if state.get("env_resolved") is True:
            return self._blocked(
                "already_resolved",
                "Environment was already verified resolved",
                completion_text,
                action,
            )

        tool = action["tool"]
        arguments = action["arguments"]
        if tool not in self.tool_registry:
            return self._blocked("tool_unavailable", f"Unknown tool: {tool}", completion_text)
        if tool in CLUSTER_MUTATING_TOOLS and not _approval_allows_mutation(
            state,
            action,
            approval_gate=self._action_approval_gate,
            approval_permit=_action_approval_permit,
        ):
            return self._blocked(
                "approval_required",
                "Mutation requires the severity-specific approval decision",
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

        pre_action_observation = None
        if tool in {"argocd_rollback", "chaos_stop_experiment"}:
            reader_name = (
                "argocd_app_history"
                if tool == "argocd_rollback"
                else "chaos_list_experiments"
            )
            reader = self.tool_registry.get(reader_name)
            if reader is None:
                return self._blocked(
                    "missing_evidence",
                    f"{tool} requires a live {reader_name} observation",
                    completion_text,
                    action,
                )
            try:
                read_result = await _maybe_await(
                    self.invoke_tool(
                        reader,
                        **({"app": arguments.get("app")} if tool == "argocd_rollback" else {}),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - a failed read cannot authorize mutation
                return self._blocked(
                    "missing_evidence",
                    f"{reader_name} observation failed: {type(exc).__name__}",
                    completion_text,
                    action,
                )
            if not isinstance(read_result, Mapping):
                return self._blocked(
                    "missing_evidence",
                    f"{reader_name} observation was not an object",
                    completion_text,
                    action,
                )
            pre_action_observation = {
                "tool": reader_name,
                "success": read_result.get("success") is True,
                "observation_status": read_result.get("observation_status"),
                "history": read_result.get("history")
                if tool == "argocd_rollback"
                else None,
                "active_experiments": read_result.get("active_experiments")
                if tool == "chaos_stop_experiment"
                else None,
            }
            supported = (
                _rollback_has_evidence(arguments, read_result)
                if tool == "argocd_rollback"
                else _chaos_has_evidence(arguments, read_result)
            )
            if not supported:
                return self._blocked(
                    "missing_evidence",
                    f"{tool} target was not confirmed by the live observation",
                    completion_text,
                    action,
                    pre_action_observation=pre_action_observation,
                )

        tool_result = await _maybe_await(
            self.invoke_tool(self.tool_registry[tool], **arguments)
        )
        incident_context = {
            **state,
            "policy_action": action,
            "pre_action_observation": pre_action_observation,
            "tool_result": tool_result,
        }
        if self.uses_builtin_settle:
            settling = await self._settle_after_action(
                scenario_id=scenario_id,
                state=state,
                action=action,
                pre_action_observation=pre_action_observation,
                tool_result=tool_result,
            )
            if settling.get("status") != "settled" or settling.get("stable") is not True:
                return self._unscorable(
                    completion_text,
                    action,
                    pre_action_observation,
                    tool,
                    arguments,
                    tool_result,
                    settling,
                )
            verification = settling["verification"]
        else:
            settling = await _maybe_await(self.settle())
            verification_result = await _maybe_await(
                self.invoke_tool(
                    self.verifier,
                    scenario_id=scenario_id,
                    agent_claimed_resolved=action["agent_claimed_resolved"],
                    alert=state.get("alert"),
                    incident_context=incident_context,
                )
            )
            verification = self._verification_record(verification_result)
            checks = verification.get("checks")
            invalid_checks = "checks" in verification and (
                not isinstance(checks, list)
                or not checks
                or any(
                    not isinstance(check, Mapping)
                    or type(check.get("passed")) is not bool
                    or type(check.get("required", True)) is not bool
                    for check in checks
                )
            )
            if invalid_checks or verification.get("verification_status") in {
                "inconclusive", "error"
            }:
                unscorable_settling = {
                    "status": "unscorable",
                    "settled": False,
                    "stable": False,
                    "stable_observations": 0,
                    "observation_count": 1,
                    "verification_status": verification.get("verification_status"),
                    "last_verification": verification,
                    "observations": [],
                    "failure": (
                        "post_action_objective_observation_invalid"
                        if invalid_checks
                        else "post_action_verification_nonconclusive"
                    ),
                }
                return self._unscorable(
                    completion_text,
                    action,
                    pre_action_observation,
                    tool,
                    arguments,
                    tool_result,
                    unscorable_settling,
                )
        env_resolved = verification.get("env_resolved") is True
        return {
            "status": "ok",
            "scorable": True,
            "policy_completion": completion_text,
            "policy_action": action,
            "pre_action_observation": pre_action_observation,
            "executed_actions": [
                {
                    "tool": tool,
                    "arguments": arguments,
                    "result": tool_result,
                }
            ],
            "verification": verification,
            "settling": settling,
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
        *,
        pre_action_observation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "blocked",
            "scorable": True,
            "policy_completion": completion_text,
            "policy_action": action,
            "pre_action_observation": pre_action_observation,
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
