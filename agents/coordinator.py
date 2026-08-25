"""AtlasOps Coordinator.

Routes alerts through: Triage → Diagnosis → Remediation → Comms.
Receives Alertmanager webhooks at POST /webhook on port 9099.
Each agent is a vLLM endpoint co-hosted on the AMD MI300X.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agents._http_retry import post_with_retry
from agents.approval import approval_gate, approval_mode_for_severity
from agents.audit import audit_log, require_audit_log
from agents.circuit_breaker import CircuitBreakerTripped, circuit_breaker
from agents.correlator import correlator
from agents.prometheus_metrics import build_dashboard_metrics_payload
from agents.stream import emit as thought_emit
from agents.tool_policy import (
    AGENT_EXPOSED_TOOLS,
    CLUSTER_MUTATING_TOOLS,
    ROLE_ALLOWED_TOOLS,
)
from agents.tools import TOOL_REGISTRY
from agents.tools.alertmanager import alertmanager_list_alerts
from agents.tools.chaos import ALLOWED_CHAOS_KINDS, ALLOWED_CHAOS_NAMESPACES
from config.runtime import StepRewardTracker


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("coordinator")


_RUNTIME_API_KEY = os.getenv("ATLASOPS_API_KEY", "")
_runtime_api_key_header = APIKeyHeader(name="X-AtlasOps-Key", auto_error=False)


def _require_runtime_api_key(key: str | None = Security(_runtime_api_key_header)) -> None:
    """Fail closed on privileged coordinator operator endpoints."""
    if not _RUNTIME_API_KEY:
        raise HTTPException(status_code=503, detail="Coordinator operator API is not configured")
    import hmac

    if not key or not hmac.compare_digest(key.encode(), _RUNTIME_API_KEY.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing X-AtlasOps-Key header")


# Backend selection:
#   BACKEND=vllm   → self-hosted vLLM on AMD MI300X (default)
#   BACKEND=fireworks → Fireworks AI API (AMD GPUs, managed)
#   BACKEND=openai  → any OpenAI-compatible endpoint
BACKEND = os.getenv("BACKEND", "vllm")

_BACKEND_DEFAULTS = {
    "vllm":      ("http://localhost:8000/v1",          "Qwen/Qwen2.5-7B-Instruct"),
    "fireworks": ("https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/qwen2p5-7b-instruct"),
    "openai":    ("https://api.openai.com/v1",         "gpt-4o-mini"),
}
_default_base, _default_model = _BACKEND_DEFAULTS.get(BACKEND, _BACKEND_DEFAULTS["vllm"])

VLLM_BASE  = os.getenv("VLLM_BASE",    _default_base)
MODEL_NAME = os.getenv("AGENT_MODEL",  _default_model)
API_KEY    = os.getenv("LLM_API_KEY",  "")  # required for fireworks/openai, empty for local vllm
PROMPTS_DIR = Path(__file__).parent / "prompts"
TRAJECTORIES_DIR = Path(os.getenv("TRAJECTORIES_DIR", "data/trajectories"))
TRAJECTORIES_DIR.mkdir(parents=True, exist_ok=True)

class AlertWebhookPayload(BaseModel):
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    commonLabels: dict[str, Any] = Field(default_factory=dict)
    status: str | None = None


class ApprovalCallbackPayload(BaseModel):
    token: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    approved_by: str = ""
    reason: str = ""


def load_prompt(role: str) -> str:
    return (PROMPTS_DIR / f"{role}.md").read_text(encoding="utf-8")


_ROLE_CONCLUSION_KEYS = frozenset({
    "outcome",
    "severity",
    "root_cause",
    "slack_posted",
    "proposed_actions",
    "executed_actions",
    "actions_taken",
})


def _extract_tool_calls_from_content(content: str) -> list[dict[str, Any]]:
    """Fallback: parse structured JSON tool calls from content.

    Handles two provider formats:
    1. {"type":"function","name":"fn","parameters":{...}}
    2. {"name":"fn","arguments":{...}}

    Prose such as ``chaos_stop_experiment(...)`` is NOT a tool call and must
    not be executed. Role-conclusion JSON is also ignored.
    """
    if not content or "{" not in content:
        return []
    try:
        start = content.index("{")
        end = content.rindex("}") + 1
        obj = json.loads(content[start:end])
        if not isinstance(obj, dict):
            return []
        if _ROLE_CONCLUSION_KEYS.intersection(obj.keys()):
            return []
        fn_name = obj.get("name") or obj.get("function", {}).get("name")
        if not fn_name:
            return []
        args = obj.get("parameters") or obj.get("arguments") or obj.get("function", {}).get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return [{
            "id": f"call_{fn_name}",
            "type": "function",
            "function": {"name": fn_name, "arguments": json.dumps(args)},
        }]
    except (ValueError, json.JSONDecodeError, KeyError):
        return []


def _normalize_assistant_tool_calls(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Return structured tool calls from an OpenAI-compatible assistant message.

    Accepts ``message.tool_calls``, legacy ``message.function_call``, and
    structured JSON-in-content. Does not parse prose function names.
    """
    existing = msg.get("tool_calls") or []
    if existing:
        return existing
    function_call = msg.get("function_call")
    if isinstance(function_call, dict) and function_call.get("name"):
        arguments = function_call.get("arguments") or "{}"
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        return [{
            "id": str(function_call.get("id") or f"call_{function_call['name']}"),
            "type": "function",
            "function": {
                "name": function_call["name"],
                "arguments": arguments,
            },
        }]
    return _extract_tool_calls_from_content(msg.get("content") or "")


def _parse_tool_arguments(fn_name: str, raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Parse native tool-call arguments without crashing the incident loop.

    OpenAI-compatible providers emit ``arguments`` as a JSON string, but the
    empty string (no-argument calls) and truncated/malformed JSON both occur in
    practice. Empty input parses to no arguments; anything unparseable becomes
    a tool-level error the model can correct on its next turn instead of an
    uncaught JSONDecodeError aborting the whole incident.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, (
            f"tool `{fn_name}` arguments are not valid JSON: {str(raw)[:120]!r}. "
            "Re-issue the tool call with a valid JSON object of arguments."
        )
    if not isinstance(parsed, dict):
        return None, f"tool `{fn_name}` arguments must be a JSON object, got {type(parsed).__name__}."
    return parsed, None


def _parse_policy_remediation_completion(completion_text: str) -> dict[str, Any]:
    """Parse the sole approved Stage 9 policy wire format.

    The completion must be exactly ``{"name": ..., "arguments": {...}}``.
    Provider artifacts, prose wrappers, stringified argument objects, and
    multi-action payloads are intentionally invalid. A parse failure is
    returned as data so the caller can preserve and score it without executing
    another model's interpretation.
    """
    raw_text = completion_text.strip()
    if not raw_text:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [],
            "policy_parse_error": "empty_completion",
        }

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return {
            "role": "assistant",
            "content": raw_text,
            "tool_calls": [],
            "policy_parse_error": "not_canonical_json",
        }

    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"name", "arguments"}
        or not isinstance(parsed["name"], str)
        or not parsed["name"].strip()
        or not isinstance(parsed["arguments"], dict)
    ):
        return {
            "role": "assistant",
            "content": raw_text,
            "tool_calls": [],
            "policy_parse_error": "invalid_canonical_action_shape",
        }

    return {
        "role": "assistant",
        "content": "",
        "policy_parse_error": None,
        "tool_calls": [{
            "id": "policy_call_0",
            "type": "function",
            "function": {
                "name": parsed["name"],
                "arguments": json.dumps(parsed["arguments"], sort_keys=True),
            },
        }],
    }


def _invalid_policy_remediation_record(
    completion_text: str,
    reason: str,
) -> dict[str, Any]:
    trajectory_record = {
        "role": "remediation",
        "turn": 0,
        "kind": "model_turn",
        "turn_kind": "policy_completion",
        "assistant_text": completion_text[:_MODEL_TURN_TEXT_CAP],
        "assistant_text_truncated": len(completion_text) > _MODEL_TURN_TEXT_CAP,
        "native_tool_calls": [],
        "parsed_tool_calls": 0,
        "finish_reason": "policy_completion_invalid",
        "executed_tool_calls": [],
        "conclusion_present": False,
        "retry": {"triggered": False, "reason": None},
        "termination_reason": f"policy_completion_{reason}",
        "validation_state": "invalid_policy_completion",
        "execution_state": "not_executed",
        "tool_outcomes": [],
    }


    conclusion = {
        "mode": "policy_rollout",
        "outcome": "unresolved",
        "status": "unresolved",
        "policy_completion_valid": False,
        "policy_completion_error": reason,
        "proposed_actions": [],
        "executed_actions": [],
        "actions_taken": [],
    }
    return {
        "role": "remediation",
        "trajectory": [trajectory_record],
        "final": conclusion,
        "step_reward_summary": StepRewardTracker().summary(),
    }


def _canonical_action_identity(tool: str, arguments: dict[str, Any]) -> dict[str, str]:
    """Return deterministic hashes for the exact tool/argument identity."""
    canonical = json.dumps(
        {"tool": tool, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {"canonical_json": canonical, "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}


def _validate_policy_action_schema(tool: str, args: Any) -> str | None:
    """Validate the policy-owned call against the same schema exposed to tools."""
    if not isinstance(args, dict):
        return "arguments_must_be_object"
    schema = _TOOL_PARAMETER_SCHEMAS.get(tool)
    if schema is None:
        return "unknown_tool_schema"
    if schema.get("type") != "object":
        return "unsupported_tool_schema"
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    missing = [name for name in required if name not in args]
    if missing:
        return f"missing_required_arguments:{','.join(missing)}"
    extra = [name for name in args if name not in properties]
    if extra:
        return f"unexpected_arguments:{','.join(sorted(extra))}"

    type_checks = {
        "string": lambda value: isinstance(value, str),
        "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda value: isinstance(value, bool),
        "array": lambda value: isinstance(value, list),
        "object": lambda value: isinstance(value, dict),
    }
    for name, value in args.items():
        expected = properties[name].get("type")
        if expected not in type_checks or not type_checks[expected](value):
            return f"invalid_argument_type:{name}"
        allowed = properties[name].get("enum")
        if allowed is not None and value not in allowed:
            return f"invalid_argument_value:{name}"
    return None


# Maximum persisted characters of raw assistant text per model-turn record.
# Deterministic cap; truncation is always flagged explicitly on the record.
_MODEL_TURN_TEXT_CAP = 2000

_RETRY_REASON_NO_TOOL_CALL = "remediation_no_tool_call_retry"
_TERMINATION_MAX_TURNS = "max_turns_exhausted"
SETTLE_POLL_INTERVAL_SECONDS = 2
SETTLE_TIMEOUT_SECONDS = 30


def _model_turn_record(
    role: str,
    turn: int,
    choice: dict[str, Any],
    raw_msg: dict[str, Any],
    *,
    parsed_count: int,
    executed_names: list[str] | None = None,
    retry_triggered: bool = False,
    retry_reason: str | None = None,
    termination_reason: str | None = None,
    conclusion_present: bool = False,
) -> dict[str, Any]:
    """Canonical forensic record for one raw model response.

    Observability-only: appended to the trajectory before any control path
    (retry/continue/termination) could discard the turn. Raw provider payloads
    (assistant text, native tool-call id/name/arguments) are preserved as
    returned; parsed/executed outcomes are recorded separately so malformed
    calls are never misrepresented.
    """
    text = raw_msg.get("content") or ""
    native_calls = []
    for tc in raw_msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        native_calls.append({
            "id": tc.get("id"),
            "name": fn.get("name"),
            "arguments": fn.get("arguments"),
        })
    return {
        "role": role,
        "turn": turn,
        "kind": "model_turn",
        "assistant_text": text[:_MODEL_TURN_TEXT_CAP],
        "assistant_text_truncated": len(text) > _MODEL_TURN_TEXT_CAP,
        # Raw provider payloads exactly as returned (never normalized here).
        "native_tool_calls": native_calls,
        # Derived: how many calls survived adapter normalization for this turn.
        "parsed_tool_calls": parsed_count,
        "finish_reason": choice.get("finish_reason"),
        # Derived: tools whose executor was actually invoked during this turn.
        "executed_tool_calls": list(executed_names or []),
        "conclusion_present": conclusion_present,
        "retry": {"triggered": retry_triggered, "reason": retry_reason},
        "termination_reason": termination_reason,
        "validation_state": "received",
        "execution_state": "not_executed",
        "tool_outcomes": [],
    }


_CONCLUSION_PROMPTS = {
    "triage":      "Based on the tool results above, output ONLY a JSON object with keys: incident_id, severity, title, blast_radius, affected_services. No prose.",
    "diagnosis":   "Based on the tool results above, output ONLY a JSON object with keys: root_cause, confidence, evidence, recommended_fix. No prose.",
    "remediation": (
        "Based on the actions taken above, output ONLY a JSON object with keys: "
        "outcome (resolved/unresolved/escalated), proposed_actions (list of intended tools), "
        "executed_actions (only tools that actually returned a runtime result), verified_by. "
        "Never claim resolved unless a mutating tool returned success. No prose."
    ),
    "comms":       "Based on the incident above, output ONLY a JSON object with keys: incident_id, slack_posted, postmortem_path, summary. No prose.",
}


async def _force_json_conclusion(
    role: str,
    messages: list[dict],
    client: httpx.AsyncClient,
    *,
    telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One extra turn with tools disabled, forcing a clean JSON conclusion.

    Trims the message history to the system prompt + last 4 turns to avoid
    context overflow after 10-turn diagnosis runs.
    """
    prompt = _CONCLUSION_PROMPTS.get(role, "Summarise your findings as a JSON object.")
    # Keep system message + last 4 assistant/tool message pairs + forced prompt
    system_msgs = [m for m in messages if m.get("role") == "system"][:1]
    recent = [m for m in messages if m.get("role") != "system"][-8:]
    forced_msgs = system_msgs + recent + [{"role": "user", "content": prompt}]
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=60, headers=headers) as c:
            r = await post_with_retry(
                c,
                f"{VLLM_BASE}/chat/completions",
                {"model": MODEL_NAME, "messages": forced_msgs, "temperature": 0.0},
                context=f"forced_conclusion/{role}",
            )
            r.raise_for_status()
            choice = r.json()["choices"][0]
            message = choice.get("message") or {}
            if telemetry is not None:
                telemetry.clear()
                telemetry.update({"choice": choice, "message": message})
            content = message.get("content", "")
            parsed = _try_parse_json(content)
            return parsed if "raw" not in parsed else {"summary": content[:300]}
    except Exception as e:
        if telemetry is not None:
            telemetry.clear()
            telemetry.update({"error": str(e)})
        return {"error": f"forced_conclusion failed: {e}"}


async def settle_environment(
    *,
    scenario_id: str,
    agent_claimed_resolved: bool,
    alert: dict[str, Any],
    incident_context: dict[str, Any],
) -> dict[str, Any]:
    """Poll frozen verifier prerequisites for bounded convergence.

    This is timing-only convergence observation. The authoritative verifier is
    still invoked after this function returns; no success predicate is relaxed.
    """
    from agents.verifier import verify_environment

    started_at_wall = datetime_now_utc()
    started_monotonic = time.monotonic()
    observations = []
    settled = False
    while True:
        result = verify_environment(
            scenario_id=scenario_id,
            agent_claimed_resolved=agent_claimed_resolved,
            alert=alert,
            incident_context=incident_context,
        )
        observation = {
            "timestamp": datetime_now_utc(),
            "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
            "env_resolved": bool(result.env_resolved),
            "verification_status": result.verification_status,
            "failed_checks": list(result.failed_checks),
        }
        observations.append(observation)
        if result.env_resolved:
            settled = True
            break
        elapsed = time.monotonic() - started_monotonic
        if elapsed >= SETTLE_TIMEOUT_SECONDS:
            break
        await asyncio.sleep(min(SETTLE_POLL_INTERVAL_SECONDS, SETTLE_TIMEOUT_SECONDS - elapsed))
    return {
        "started_at": started_at_wall,
        "completed_at": datetime_now_utc(),
        "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        "timeout_seconds": SETTLE_TIMEOUT_SECONDS,
        "poll_interval_seconds": SETTLE_POLL_INTERVAL_SECONDS,
        "settled": settled,
        "observations": observations,
    }


def datetime_now_utc() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


async def call_agent(
    role: str,
    user_input: dict[str, Any],
    max_turns: int = 10,
    *,
    policy_completion: str | None = None,
) -> dict[str, Any]:
    """Run a single agent with a tool-calling loop. Returns final JSON output."""
    require_audit_log()
    if policy_completion is not None and role != "remediation":
        raise ValueError("policy_completion is supported only for remediation")
    policy_message: dict[str, Any] | None = None
    if policy_completion is not None:
        policy_message = _parse_policy_remediation_completion(policy_completion)
        parse_error = policy_message.get("policy_parse_error")
        if not parse_error and len(policy_message.get("tool_calls") or []) == 1:
            call = policy_message["tool_calls"][0]["function"]
            try:
                candidate_args = json.loads(call["arguments"])
            except json.JSONDecodeError:
                candidate_args = None
            parse_error = _validate_policy_action_schema(call["name"], candidate_args)
        if parse_error or len(policy_message.get("tool_calls") or []) != 1:
            return _invalid_policy_remediation_record(
                policy_completion,
                parse_error or "expected_exactly_one_tool_call",
            )
    system_prompt = load_prompt(role)
    incident_id = str(user_input.get("incident_id", "unknown"))
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_input, indent=2)},
    ]
    trajectory: list[dict[str, Any]] = []
    step_tracker = StepRewardTracker()
    _seen_calls: dict[str, int] = {}   # (tool+args hash) → call count (exact-args dedup)
    _tool_counts: dict[str, int] = {}  # tool_name → total calls this run (per-tool cap)
    _mutating_tool_executed: bool = False
    _executed_actions: list[dict[str, Any]] = []
    _remediation_retry_given: bool = False
    _last_choice: dict[str, Any] | None = None
    _last_raw_msg: dict[str, Any] | None = None
    _remediation_turn_record: dict[str, Any] | None = None

    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    async with httpx.AsyncClient(timeout=120, headers=headers) as client:
        for turn in range(max_turns):
            turn_executed_names: list[str] = []
            if policy_message is not None:
                if turn != 0:
                    break
                policy_denial_reason: str | None = None
                choice = {"finish_reason": "policy_completion"}
                msg = json.loads(json.dumps(policy_message))
            else:
                tool_choice = (
                    "required"
                    if (role == "remediation" and _remediation_retry_given and not _mutating_tool_executed)
                    else "auto"
                )
                r = await post_with_retry(
                    client,
                    f"{VLLM_BASE}/chat/completions",
                    {
                        "model": MODEL_NAME,
                        "messages": messages,
                        "temperature": 0.2,
                        "tools": _tool_schemas_for_role(role),
                        "tool_choice": tool_choice,
                    },
                    context=f"{role}/turn-{turn}",
                )
                r.raise_for_status()
                choice = r.json()["choices"][0]
                msg = choice["message"]
            # Snapshot the RAW provider payload before adapter normalization so
            # the forensic record preserves exactly what the model returned.
            raw_msg_snapshot = json.loads(json.dumps(msg)) if isinstance(msg, dict) else {}
            messages.append(msg)

            # Normalise tool calls — some providers return function calls as
            # JSON in content or as legacy message.function_call.
            msg["tool_calls"] = _normalize_assistant_tool_calls(msg)
            _parsed_call_count = len(msg.get("tool_calls") or [])
            _last_choice = choice
            _last_raw_msg = raw_msg_snapshot

            # Every remediation response is persisted before retry, execution,
            # conclusion, or termination control flow can discard it.
            if role == "remediation":
                _remediation_turn_record = _model_turn_record(
                    role,
                    turn,
                    choice,
                    raw_msg_snapshot,
                    parsed_count=_parsed_call_count,
                    executed_names=turn_executed_names,
                )
                if policy_message is not None:
                    _remediation_turn_record["turn_kind"] = "policy_completion"
                trajectory.append(_remediation_turn_record)

            if not msg.get("tool_calls"):
                if policy_message is not None:
                    # Parser already rejects this case; retain the fail-closed
                    # branch for direct callers of the internal loop.
                    return _invalid_policy_remediation_record(
                        policy_completion or "",
                        "expected_exactly_one_tool_call",
                    )
                # Generic Remediation Tool Execution Retry:
                # If remediation model returned conclusion text with NO actual tool calls
                # and no mutating tool has executed yet, grant one structured retry.
                if role == "remediation" and not _mutating_tool_executed and not _remediation_retry_given:
                    _remediation_retry_given = True
                    # Persist the discarded model turn BEFORE continuing (Run-004
                    # observability gap): prose/empty responses must survive.
                    if _remediation_turn_record is not None:
                        _remediation_turn_record["retry"] = {
                            "triggered": True,
                            "reason": _RETRY_REASON_NO_TOOL_CALL,
                        }
                        _remediation_turn_record["validation_state"] = "no_tool_calls"
                    audit_log.record(
                        incident_id=incident_id,
                        agent_role=role,
                        action_type="remediation_retry",
                        result_summary=(
                            f"turn {turn}: no executable tool call; "
                            f"retry {_RETRY_REASON_NO_TOOL_CALL}"
                        )[:300],
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "You have proposed a remediation but no remediation tool has actually executed. "
                            "If you intend to perform the action, invoke the provided function/tool now. "
                            "Do not report success until a real tool result is returned. If no safe action "
                            "can be executed, conclude unresolved/escalated."
                        ),
                    })
                    continue

                conclusion = msg["content"] or ""
                parsed = _try_parse_json(conclusion)
                # If raw, or if conclusion looks like tool args instead of a role conclusion,
                # do one forced-JSON turn to get a clean structured summary.
                _ROLE_REQUIRED_KEYS = {
                    "triage": {"severity"},
                    "diagnosis": {"root_cause"},
                    "remediation": {"outcome"},
                    "comms": {"slack_posted"},
                }
                _required = _ROLE_REQUIRED_KEYS.get(role, set())
                if "raw" in parsed or (_required and not _required.intersection(parsed.keys())):
                    forced_telemetry: dict[str, Any] = {}
                    parsed = await _force_json_conclusion(
                        role,
                        messages,
                        client,
                        telemetry=forced_telemetry,
                    )
                    if role == "remediation":
                        trajectory.append(_model_turn_record(
                            role,
                            turn,
                            forced_telemetry.get("choice") or {},
                            forced_telemetry.get("message") or {"content": ""},
                            parsed_count=0,
                            executed_names=[],
                            conclusion_present=True,
                        ))
                        trajectory[-1]["turn_kind"] = "forced_conclusion"
                        if forced_telemetry.get("error"):
                            trajectory[-1]["finish_reason"] = "forced_conclusion_error"
                            trajectory[-1]["validation_state"] = "forced_conclusion_error"
                        else:
                            trajectory[-1]["validation_state"] = "final_conclusion"

                if role == "remediation":
                    # Strictly distinguish proposals from proven executions.
                    # Never allow hallucinated or unexecuted actions in executed_actions/actions_taken.
                    proposed = parsed.get("proposed_actions") or parsed.get("actions_taken") or []
                    parsed["proposed_actions"] = proposed if isinstance(proposed, list) else [proposed]
                    parsed["executed_actions"] = list(_executed_actions)
                    parsed["actions_taken"] = list(_executed_actions)
                    if not _mutating_tool_executed:
                        if parsed.get("outcome") == "resolved":
                            parsed["outcome"] = "unresolved"
                        if parsed.get("status") == "resolved":
                            parsed["status"] = "unresolved"

                thought_emit(role, "conclusion", _summarise_conclusion(role, conclusion))
                if _remediation_turn_record is not None:
                    _remediation_turn_record["conclusion_present"] = True
                    _remediation_turn_record["validation_state"] = "final_conclusion"
                trajectory.append({"role": role, "turn": turn, "content": conclusion})
                return {
                    "role": role,
                    "trajectory": trajectory,
                    "final": parsed,
                    "step_reward_summary": step_tracker.summary(),
                }

            for tc in msg["tool_calls"]:
                fn_name = tc["function"]["name"]
                fn_args, args_error = _parse_tool_arguments(fn_name, tc["function"].get("arguments"))
                if args_error is not None:
                    tool_output = {"success": False, "error": args_error}
                    if _remediation_turn_record is not None:
                        _remediation_turn_record["validation_state"] = "invalid_arguments"
                        _remediation_turn_record["tool_outcomes"].append(
                            {"id": tc.get("id"), "name": fn_name, "validation": "failed", "executed": False}
                        )
                    audit_log.record(
                        incident_id=incident_id,
                        agent_role=role,
                        action_type="tool_result",
                        tool_name=fn_name,
                        result_summary=args_error,
                        policy_check="invalid_tool_arguments",
                    )
                    thought_emit(role, "tool_result", f"⚠️ invalid tool arguments: {args_error}", tool=fn_name)
                    trajectory.append(
                        {
                            "role": role,
                            "turn": turn,
                            "tool": fn_name,
                            "raw_arguments": tc["function"].get("arguments"),
                            "output": tool_output,
                            "invalid_arguments": True,
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(tool_output),
                        }
                    )
                    continue
                policy_error = _check_tool_policy(role, fn_name, fn_args, user_input)
                if policy_error:
                    policy_denial_reason = f"policy_blocked:{policy_error}"
                    tool_output = {"success": False, "error": policy_error}
                    if _remediation_turn_record is not None:
                        _remediation_turn_record["validation_state"] = "blocked_by_policy"
                        _remediation_turn_record["tool_outcomes"].append(
                            {"id": tc.get("id"), "name": fn_name, "validation": "blocked", "executed": False}
                        )
                    audit_log.record(
                        incident_id=incident_id,
                        agent_role=role,
                        action_type="tool_result",
                        tool_name=fn_name,
                        tool_args=fn_args,
                        result_summary=policy_error,
                        policy_check="blocked_by_policy",
                    )
                    thought_emit(role, "tool_result", f"⚠️ blocked by policy: {policy_error}", tool=fn_name)
                    trajectory.append(
                        {
                            "role": role,
                            "turn": turn,
                            "tool": fn_name,
                            "args": fn_args,
                            "output": tool_output,
                            "blocked_by_policy": True,
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(tool_output),
                        }
                    )
                    continue
                # Narrate the tool call
                thought_emit(role, "tool_call",
                             _narrate_tool_call(role, fn_name, fn_args),
                             tool=fn_name)
                audit_log.record(
                    incident_id=incident_id,
                    agent_role=role,
                    action_type="tool_call",
                    tool_name=fn_name,
                    tool_args=fn_args,
                    policy_check="allowed",
                )
                try:
                    circuit_breaker.check_before_tool_call(
                        incident_id=incident_id,
                        tool_name=fn_name,
                        is_cluster_mutating=fn_name in CLUSTER_MUTATING_TOOLS,
                    )
                except CircuitBreakerTripped as e:
                    policy_denial_reason = f"circuit_breaker_blocked:{e}"
                    tool_output = {"success": False, "error": str(e), "blocked_by_circuit_breaker": True}
                    if _remediation_turn_record is not None:
                        _remediation_turn_record["validation_state"] = "blocked_by_circuit_breaker"
                        _remediation_turn_record["tool_outcomes"].append(
                            {"id": tc.get("id"), "name": fn_name, "validation": "allowed", "execution": "blocked", "executed": False}
                        )
                    audit_log.record(
                        incident_id=incident_id,
                        agent_role=role,
                        action_type="tool_result",
                        tool_name=fn_name,
                        tool_args=fn_args,
                        result_summary=str(e),
                        policy_check="blocked_by_circuit_breaker",
                    )
                    thought_emit(role, "tool_result", f"⛔ blocked by circuit breaker: {e}", tool=fn_name)
                    trajectory.append(
                        {
                            "role": role,
                            "turn": turn,
                            "tool": fn_name,
                            "args": fn_args,
                            "output": tool_output,
                            "blocked_by_circuit_breaker": True,
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(tool_output),
                        }
                    )
                    continue
                # Dedup guard 1: same (tool + exact args) called >3 times
                _call_key = f"{fn_name}:{json.dumps(fn_args, sort_keys=True)}"
                _seen_calls[_call_key] = _seen_calls.get(_call_key, 0) + 1
                if _seen_calls[_call_key] > 3:
                    policy_denial_reason = "dedup_blocked"
                    tool_output = {"error": f"Duplicate call blocked — {fn_name} already called with these exact args. Try different parameters or produce your conclusion."}
                    if _remediation_turn_record is not None:
                        _remediation_turn_record["validation_state"] = "dedup_blocked"
                        _remediation_turn_record["tool_outcomes"].append(
                            {"id": tc.get("id"), "name": fn_name, "validation": "allowed", "execution": "dedup_blocked", "executed": False}
                        )
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(tool_output)})
                    trajectory.append({"role": role, "turn": turn, "tool": fn_name, "args": fn_args, "output": tool_output, "dedup_blocked": True})
                    continue
                # Dedup guard 2: same tool called >6 times total (catches arg-variation loops)
                _TOOL_CAPS = {"promql_query": 6, "promql_query_range": 4, "kubectl_get": 5, "kubectl_logs": 4}
                _tool_counts[fn_name] = _tool_counts.get(fn_name, 0) + 1
                _cap = _TOOL_CAPS.get(fn_name, 8)
                if _tool_counts[fn_name] > _cap:
                    policy_denial_reason = "tool_cap_blocked"
                    tool_output = {"error": f"Tool cap reached — {fn_name} called {_tool_counts[fn_name]} times this run (limit {_cap}). You have enough data; produce your conclusion now."}
                    if _remediation_turn_record is not None:
                        _remediation_turn_record["validation_state"] = "cap_blocked"
                        _remediation_turn_record["tool_outcomes"].append(
                            {"id": tc.get("id"), "name": fn_name, "validation": "allowed", "execution": "cap_blocked", "executed": False}
                        )
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(tool_output)})
                    trajectory.append({"role": role, "turn": turn, "tool": fn_name, "args": fn_args, "output": tool_output, "cap_blocked": True})
                    continue

                fn = TOOL_REGISTRY.get(fn_name)
                if not fn:
                    policy_denial_reason = "unknown_tool"
                    tool_output = {"error": f"Unknown tool: {fn_name}"}
                else:
                    try:
                        tool_output = fn(**fn_args)
                    except Exception as e:
                        tool_output = {"error": f"Tool execution failed: {e}"}
                if fn_name in CLUSTER_MUTATING_TOOLS and bool(tool_output.get("success", False)):
                    _mutating_tool_executed = True
                if fn is not None:
                    turn_executed_names.append(fn_name)
                if _remediation_turn_record is not None:
                    _remediation_turn_record["executed_tool_calls"] = list(turn_executed_names)
                    executed = bool(tool_output.get("success", False))
                    _remediation_turn_record["validation_state"] = "validated"
                    known_tool = fn_name in TOOL_REGISTRY and fn is not None
                    _remediation_turn_record["execution_state"] = (
                        "executed" if known_tool else "unknown_tool"
                    )
                    _remediation_turn_record["tool_outcomes"].append(
                        {
                            "id": tc.get("id"),
                            "name": fn_name,
                            "validation": "allowed",
                            "execution": "completed" if known_tool else "not_executed",
                            "success": executed,
                            "executed": known_tool,
                        }
                    )
                _executed_actions.append({
                    "step": len(_executed_actions) + 1,
                    "tool": fn_name,
                    "args": fn_args,
                    "output": tool_output,
                    "success": bool(tool_output.get("success", False)),
                })
                # Dense per-step reward signal
                step_reward = step_tracker.record(fn_name, fn_args, tool_output)
                # Narrate the result
                audit_log.record(
                    incident_id=incident_id,
                    agent_role=role,
                    action_type="tool_result",
                    tool_name=fn_name,
                    tool_args=fn_args,
                    result_summary=str(tool_output)[:300],
                    policy_check="allowed",
                )
                thought_emit(role, "tool_result",
                             _narrate_tool_result(fn_name, tool_output),
                             tool=fn_name,
                             result_summary=str(tool_output)[:200])
                trajectory.append({
                    "role": role, "turn": turn, "tool": fn_name,
                    "args": fn_args, "output": tool_output,
                    "step_reward": step_reward,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(tool_output)[:8000],
                })

            if policy_message is not None:
                proposed_tools: list[str] = [
                    call["function"]["name"] for call in policy_message["tool_calls"]
                ]
                plan_call = policy_message["tool_calls"][0]
                try:
                    plan_args = json.loads(plan_call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    plan_args = {"__invalid_json__": True}
                parsed_identity = _canonical_action_identity(
                    plan_call["function"]["name"], plan_args
                )
                executed_record = _executed_actions[0] if len(_executed_actions) == 1 else None
                executed_identity = (
                    _canonical_action_identity(executed_record["tool"], executed_record["args"])
                    if executed_record
                    else None
                )
                identity_match = bool(
                    executed_identity
                    and executed_identity["sha256"] == parsed_identity["sha256"]
                )
                admitted = identity_match and policy_denial_reason is None
                outcome = (
                    "resolved"
                    if _mutating_tool_executed
                    else "escalated" if any(a.get("success") for a in _executed_actions) else "unresolved"
                )
                conclusion = {
                    "incident_id": incident_id,
                    "mode": "policy_rollout",
                    "outcome": outcome,
                    "status": outcome,
                    "policy_completion_valid": True,
                    "policy_action_identity_match": identity_match,
                    "policy_action_admitted": admitted,
                    "policy_denial_reason": policy_denial_reason,
                    "policy_parsed_action": {
                        "tool": plan_call["function"]["name"],
                        "arguments": plan_args,
                        **parsed_identity,
                    },
                    "policy_executed_action": {
                        "tool": executed_record.get("tool"),
                        "arguments": executed_record.get("args"),
                        **executed_identity,
                    } if executed_identity else None,
                    "policy_completion_sha256": hashlib.sha256(
                        (policy_completion or "").encode("utf-8")
                    ).hexdigest(),
                    "proposed_actions": proposed_tools,
                    "executed_actions": list(_executed_actions),
                    "actions_taken": list(_executed_actions),
                }
                if _remediation_turn_record is not None:
                    _remediation_turn_record["conclusion_present"] = True
                    _remediation_turn_record["validation_state"] = "final_conclusion"
                trajectory.append({
                    "role": role,
                    "turn": 0,
                    "content": "",
                    "note": "execution_ground_policy_conclusion",
                })
                return {
                    "role": role,
                    "trajectory": trajectory,
                    "final": conclusion,
                    "step_reward_summary": step_tracker.summary(),
                }

    log.warning("%s exceeded %d turns", role, max_turns)
    # Persist the final (turn-limit) model response before the forced
    # conclusion replaces it — otherwise its raw shape is lost.
    if role == "remediation" and _remediation_turn_record is not None:
        _remediation_turn_record["termination_reason"] = _TERMINATION_MAX_TURNS
        if not _remediation_turn_record["tool_outcomes"]:
            _remediation_turn_record["validation_state"] = "no_tool_calls"
    elif _last_raw_msg is not None:
        trajectory.append(_model_turn_record(
            role,
            max_turns - 1,
            _last_choice or {},
            _last_raw_msg,
            parsed_count=len(_normalize_assistant_tool_calls(_last_raw_msg)),
            retry_triggered=False,
            retry_reason=None,
            termination_reason=_TERMINATION_MAX_TURNS,
            conclusion_present=False,
        ))
    # Ask the model to summarise whatever it found rather than returning an error
    forced_telemetry: dict[str, Any] = {}
    forced = await _force_json_conclusion(role, messages, client, telemetry=forced_telemetry)
    if role == "remediation":
        trajectory.append(_model_turn_record(
            role,
            max_turns,
            forced_telemetry.get("choice") or {},
            forced_telemetry.get("message") or {"content": ""},
            parsed_count=0,
            executed_names=[],
            conclusion_present=True,
        ))
        trajectory[-1]["turn_kind"] = "forced_conclusion"
        trajectory[-1]["validation_state"] = (
            "forced_conclusion_error"
            if forced_telemetry.get("error")
            else "final_conclusion"
        )
        if forced_telemetry.get("error"):
            trajectory[-1]["finish_reason"] = "forced_conclusion_error"
    if role == "remediation":
        proposed = forced.get("proposed_actions") or forced.get("actions_taken") or []
        forced["proposed_actions"] = proposed if isinstance(proposed, list) else [proposed]
        forced["executed_actions"] = list(_executed_actions)
        forced["actions_taken"] = list(_executed_actions)
        if not _mutating_tool_executed:
            if forced.get("outcome") == "resolved":
                forced["outcome"] = "unresolved"
            if forced.get("status") == "resolved":
                forced["status"] = "unresolved"
    thought_emit(role, "conclusion", _summarise_conclusion(role, ""))
    return {"role": role, "trajectory": trajectory, "final": forced, "step_reward_summary": step_tracker.summary()}


def _configured_comms_targets() -> tuple[bool, bool]:
    discord_on = bool(os.getenv("DISCORD_WEBHOOK_URL", "").strip())
    slack_on = bool(os.getenv("SLACK_WEBHOOK_URL", "").strip())
    return discord_on, slack_on


def _comms_destination_phrase(short: bool = False) -> str:
    discord_on, slack_on = _configured_comms_targets()
    if discord_on and slack_on:
        return "Discord and Slack" if not short else "Discord / Slack"
    if discord_on:
        return "Discord"
    if slack_on:
        return "Slack"
    return "incident log (add DISCORD_WEBHOOK_URL and/or SLACK_WEBHOOK_URL)"


def _narrate_slack_post_tool_result(output: dict) -> str:
    errs = output.get("errors") or []
    if errs:
        return f"⚠️ Comms delivery issues: {'; '.join(errs)[:220]}"
    mode = str(output.get("mode", ""))
    notified: list[str] = []
    if "discord" in mode:
        notified.append("Discord")
    if "slack" in mode:
        notified.append("Slack")
    if not notified:
        return "✅ Logged locally (no external webhooks)."
    return f"✅ {' + '.join(notified)} notified."


def _narrate_tool_call(role: str, tool: str, args: dict) -> str:
    narrations = {
        "kubectl_get":           lambda a: f"Checking {a.get('resource','pods')} across the cluster...",
        "kubectl_logs":          lambda a: f"Reading logs from {a.get('pod','pod')} — looking for errors...",
        "kubectl_describe":      lambda a: f"Describing {a.get('resource','')} {a.get('name','')} — checking events...",
        "kubectl_top_pods":      lambda a: "Checking CPU/memory pressure across all pods...",
        "kubectl_rollout":       lambda a: f"Running rollout {a.get('action','')} on {a.get('resource','')}...",
        "kubectl_scale":         lambda a: f"Scaling {a.get('deployment','')} to {a.get('replicas','')} replicas...",
        "promql_query":          lambda a: f"Querying Prometheus: `{str(a.get('query',''))[:80]}`",
        "promql_query_range":    lambda a: f"Checking metric trend: `{str(a.get('query',''))[:80]}`",
        "jaeger_search":         lambda a: f"Searching traces for {a.get('service','')} (last {a.get('lookback','15m')})...",
        "jaeger_get_trace":      lambda a: f"Fetching trace {a.get('trace_id','')[:16]}... — following the span chain...",
        "argocd_list_apps":      lambda a: "Checking Argo CD for recent deployments...",
        "argocd_app_history":    lambda a: f"Checking deploy history for {a.get('app','')}...",
        "argocd_rollback":       lambda a: f"Rolling back {a.get('app','')} to revision {a.get('revision','')}...",
        "chaos_stop_experiment": lambda a: f"Stopping Chaos Mesh experiment {a.get('kind','')} {a.get('name','')} in {a.get('namespace','chaos-mesh')}...",
        "gcloud_logs_read":      lambda a: f"Reading Cloud Logging: `{str(a.get('filter_query',''))[:80]}`",
        "cloud_monitoring_query":lambda a: f"Querying GCP metric: {a.get('metric_type','')}",
        "alertmanager_silence":  lambda a: f"Silencing alert for {a.get('duration_minutes',30)} min — suppressing noise...",
        "slack_post_update":     lambda a: (
            f"Posting [{a.get('severity', '')}] incident update to {_comms_destination_phrase(short=True)}..."
        ),
        "postmortem_draft":      lambda a: "Drafting postmortem — building timeline from incident data...",
    }
    fn = narrations.get(tool)
    return fn(args) if fn else f"Calling {tool}..."


def _narrate_tool_result(tool: str, output: dict) -> str:
    if not output.get("success", True):
        return f"⚠️ {tool} returned an error: {str(output.get('error',''))[:100]}"
    if tool == "slack_post_update":
        return _narrate_slack_post_tool_result(output)
    result_narrations = {
        "kubectl_get":            "Got cluster state.",
        "kubectl_logs":           "Got pod logs — scanning for stack traces and errors.",
        "promql_query":           f"Got metric data — analysing values.",
        "jaeger_search":          f"Found traces — checking for slow spans.",
        "argocd_rollback":        "✅ Rollback executed.",
        "chaos_stop_experiment":  "✅ Chaos experiment stopped and cleared.",
        "kubectl_scale":          "✅ Scale applied.",
        "postmortem_draft":       "✅ Postmortem saved.",
    }
    return result_narrations.get(tool, f"{tool} completed.")


def _summarise_conclusion(role: str, content: str) -> str:
    _comms_close = (
        f"Incident closed — {_comms_destination_phrase()} updated, postmortem saved."
    )
    summaries = {
        "triage":      "Triage complete — severity assigned, blast radius mapped, handing to Diagnosis.",
        "diagnosis":   "Root cause identified — handing remediation plan to Remediation agent.",
        "remediation": "Remediation complete — verifying resolution with Prometheus.",
        "comms":       _comms_close,
    }
    return summaries.get(role, f"{role} agent finished.")


def _tool_schemas_for_role(role: str) -> list[dict[str, Any]]:
    return [_tool_schema(name) for name in sorted(ROLE_ALLOWED_TOOLS.get(role, frozenset()))]


_TOOL_PARAMETER_SCHEMAS: dict[str, dict[str, Any]] = {
    "kubectl_get": {"type": "object", "properties": {"resource": {"type": "string"}, "namespace": {"type": "string"}, "output": {"type": "string"}}, "required": ["resource"], "additionalProperties": False},
    "kubectl_describe": {"type": "object", "properties": {"resource": {"type": "string"}, "name": {"type": "string"}, "namespace": {"type": "string"}}, "required": ["resource", "name"], "additionalProperties": False},
    "kubectl_logs": {"type": "object", "properties": {"pod": {"type": "string"}, "namespace": {"type": "string"}, "tail": {"type": "integer"}, "container": {"type": "string"}}, "required": ["pod"], "additionalProperties": False},
    "kubectl_top_pods": {"type": "object", "properties": {"namespace": {"type": "string"}}, "additionalProperties": False},
    "kubectl_rollout": {"type": "object", "properties": {"action": {"type": "string"}, "resource": {"type": "string"}, "namespace": {"type": "string"}}, "required": ["action", "resource"], "additionalProperties": False},
    "kubectl_scale": {"type": "object", "properties": {"deployment": {"type": "string"}, "replicas": {"type": "integer"}, "namespace": {"type": "string"}}, "required": ["deployment", "replicas"], "additionalProperties": False},
    "promql_query": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False},
    "promql_query_range": {"type": "object", "properties": {"query": {"type": "string"}, "start": {"type": "number"}, "end": {"type": "number"}, "step": {"type": "string"}}, "required": ["query"], "additionalProperties": False},
    "jaeger_search": {"type": "object", "properties": {"service": {"type": "string"}, "lookback": {"type": "string"}, "limit": {"type": "integer"}, "min_duration": {"type": "string"}}, "required": ["service"], "additionalProperties": False},
    "jaeger_get_trace": {"type": "object", "properties": {"trace_id": {"type": "string"}}, "required": ["trace_id"], "additionalProperties": False},
    "argocd_list_apps": {"type": "object", "properties": {}, "additionalProperties": False},
    "argocd_app_history": {"type": "object", "properties": {"app": {"type": "string"}}, "required": ["app"], "additionalProperties": False},
    "argocd_rollback": {"type": "object", "properties": {"app": {"type": "string"}, "revision": {"type": "string"}}, "required": ["app", "revision"], "additionalProperties": False},
    "chaos_stop_experiment": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["PodChaos", "StressChaos", "NetworkChaos", "DNSChaos", "IOChaos", "TimeChaos"]},
            "name": {"type": "string", "description": "The exact name of the active Chaos Mesh experiment resource."},
            "namespace": {
                "type": "string",
                "enum": sorted(ALLOWED_CHAOS_NAMESPACES),
                "default": "chaos-mesh",
                "description": "Namespace of the chaos resource (allowed: chaos-mesh only; no generic override).",
            },
        },
        "required": ["kind", "name"],
        "additionalProperties": False,
    },
    "gcloud_logs_read": {"type": "object", "properties": {"filter_query": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["filter_query"], "additionalProperties": False},
    "cloud_monitoring_query": {"type": "object", "properties": {"metric_type": {"type": "string"}, "lookback_seconds": {"type": "integer"}}, "required": ["metric_type"], "additionalProperties": False},
    "alertmanager_list_alerts": {"type": "object", "properties": {"active_only": {"type": "boolean"}}, "additionalProperties": False},
    "alertmanager_silence": {"type": "object", "properties": {"matchers": {"type": "array"}, "duration_minutes": {"type": "integer"}, "comment": {"type": "string"}}, "required": ["matchers"], "additionalProperties": False},
    "slack_post_update": {"type": "object", "properties": {"channel": {"type": "string"}, "severity": {"type": "string"}, "title": {"type": "string"}, "summary": {"type": "string"}, "action_items": {"type": "array"}}, "required": ["channel", "severity", "title", "summary"], "additionalProperties": False},
    "postmortem_draft": {"type": "object", "properties": {"incident": {"type": "object"}, "output_path": {"type": "string"}}, "required": ["incident"], "additionalProperties": False},
}


def _tool_schema(name: str) -> dict[str, Any]:
    """Generate OpenAI-format tool schema with strict known arguments."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Real SRE tool: {name}",
            "parameters": _TOOL_PARAMETER_SCHEMAS[name],
        },
    }


def _extract_severity(user_input: dict[str, Any]) -> str:
    triage = user_input.get("triage", {}) if isinstance(user_input, dict) else {}
    sev = str(triage.get("severity", "")).upper()
    return sev if sev in {"P0", "P1", "P2", "P3"} else "UNKNOWN"


def _comms_trajectory_delivered_externally(comms_doc: dict[str, Any]) -> bool:
    for step in comms_doc.get("trajectory", []) or []:
        if not isinstance(step, dict) or step.get("tool") != "slack_post_update":
            continue
        out = step.get("output") or {}
        if out.get("errors"):
            continue
        mode = str(out.get("mode", ""))
        if "discord" in mode or "slack" in mode:
            return True
    return False


def _check_tool_policy(role: str, tool: str, args: dict[str, Any], user_input: dict[str, Any]) -> str | None:
    if tool not in ROLE_ALLOWED_TOOLS.get(role, set()):
        return f"tool `{tool}` not allowed for role `{role}`"

    if role != "remediation" and tool in CLUSTER_MUTATING_TOOLS:
        return f"cluster-mutating tool `{tool}` blocked outside remediation role"

    # Guard high-impact operations to avoid accidental destructive actions.
    if role == "remediation":
        severity = _extract_severity(user_input)
        if tool == "alertmanager_silence":
            duration = int(args.get("duration_minutes", 30))
            if duration > 30:
                return "silence duration cannot exceed 30 minutes"
        if tool == "kubectl_scale":
            replicas = int(args.get("replicas", 0))
            if replicas < 0 or replicas > 20:
                return "replicas must stay within 0..20"
        if tool == "argocd_rollback" and severity in {"UNKNOWN", "P3"}:
            return "rollback requires confirmed incident severity P0/P1/P2"
        if tool == "chaos_stop_experiment":
            kind = str(args.get("kind", "")).strip().lower()
            if kind not in ALLOWED_CHAOS_KINDS:
                return (
                    f"chaos kind '{args.get('kind')}' not allowed; "
                    f"must be one of {sorted(ALLOWED_CHAOS_KINDS)}"
                )
            namespace = str(args.get("namespace", "chaos-mesh")).strip().lower()
            if namespace not in ALLOWED_CHAOS_NAMESPACES:
                return (
                    f"chaos namespace '{args.get('namespace')}' unauthorized; "
                    f"allowed={sorted(ALLOWED_CHAOS_NAMESPACES)}"
                )
    return None


_TOOL_NAMES_SET = AGENT_EXPOSED_TOOLS


def _try_parse_json(content: str) -> dict[str, Any]:
    """Extract the largest outermost JSON object from content using bracket-matching.

    Finds each top-level '{...}' block (depth=0) in left-to-right order,
    skipping pure tool-call fragments. Returns the first non-tool-call match
    that has the most keys (largest result).
    """
    if not content:
        return {}
    candidates: list[dict] = []
    i = 0
    while i < len(content):
        if content[i] != "{":
            i += 1
            continue
        depth = 0
        in_string = False
        escape_next = False
        j = i
        while j < len(content):
            c = content[j]
            if escape_next:
                escape_next = False
            elif c == "\\" and in_string:
                escape_next = True
            elif c == '"':
                in_string = not in_string
            elif not in_string:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(content[i : j + 1])
                            if isinstance(obj, dict) and obj.get("name") not in _TOOL_NAMES_SET:
                                candidates.append(obj)
                        except json.JSONDecodeError:
                            pass
                        break
            j += 1
        i += 1
    if candidates:
        return max(candidates, key=lambda o: len(o))
    return {"raw": content[:500]}


def _remediation_plan_summary(triage: dict[str, Any], diagnosis: dict[str, Any]) -> str:
    severity = str(triage.get("severity", "UNKNOWN"))
    root_cause = str(diagnosis.get("root_cause", "unspecified root cause"))
    plan = str(diagnosis.get("recommended_fix", diagnosis.get("next_action", "apply safe rollback/scale")))
    return f"{severity} incident; root cause: {root_cause}; proposal: {plan}"


def _manual_remediation_record(incident_id: str, triage: dict[str, Any], diagnosis: dict[str, Any]) -> dict[str, Any]:
    summary = _remediation_plan_summary(triage, diagnosis)
    runbook = [
        "Validate blast radius with kubectl_get, promql_query, and jaeger_search.",
        "Review latest Argo CD history for affected app and identify safe rollback target.",
        "Apply remediation manually (rollback/scale) using change-management policy.",
        "Verify error rate and saturation return to baseline for at least 5 minutes.",
        "Document resolution and communicate timeline in comms update.",
    ]
    return {
        "role": "remediation",
        "trajectory": [],
        "final": {
            "incident_id": incident_id,
            "mode": "manual",
            "status": "skipped_execution",
            "summary": summary,
            "runbook": runbook,
        },
    }


def _live_judge_requested() -> bool:
    """Post-incident scoring with the external judge (typically 72B on HF or vLLM).

    Default: ON when ATLASOPS_USE_HF_INFERENCE=1 so Space demos exercise both models.
    Opt out: ATLASOPS_LIVE_JUDGE=0
    """
    flag = os.getenv("ATLASOPS_LIVE_JUDGE", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    return os.getenv("ATLASOPS_USE_HF_INFERENCE", "").strip().lower() in ("1", "true", "yes")


async def handle_incident(
    alert: dict[str, Any],
    incident_id: str | None = None,
    scenario_id: str | None = None,
    remediation_policy_completion: str | None = None,
) -> dict[str, Any]:
    """Run the full agent chain for one incident.

    ``scenario_id`` is an evaluation-only channel: it selects the frozen
    verifier spec without ever being placed inside the model-visible alert.
    """
    incident_id = incident_id or f"inc-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    log.info("[%s] handling alert: %s", incident_id, alert.get("commonLabels", {}).get("alertname"))
    audit_log.record(
        incident_id=incident_id,
        agent_role="coordinator",
        action_type="incident_start",
        result_summary=alert.get("commonLabels", {}).get("alertname", "unknown-alert"),
    )
    circuit_breaker.start_incident()

    resolved = False
    tri_snap: dict[str, Any] = {}
    run_error: str | None = None
    finish_reason = ""
    approval_record: dict[str, Any] | None = None
    try:
        triage = await call_agent("triage", {"incident_id": incident_id, "alert": alert})
        tri_snap = triage.get("final") or {}
        diagnosis = await call_agent("diagnosis", {"incident_id": incident_id, "triage": triage["final"]})
        severity = _extract_severity({"triage": triage.get("final", {})})
        approval_mode = approval_mode_for_severity(severity)
        approval_record = {"mode": approval_mode, "severity": severity}
        remediation_input = {
            "incident_id": incident_id,
            "triage": triage["final"],
            "diagnosis": diagnosis["final"],
            "approval_mode": approval_mode,
        }
        if approval_mode == "manual":
            thought_emit(
                "remediation",
                "waiting_approval",
                "Manual mode for P0 incident — generating runbook for human execution.",
            )
            remediation = _manual_remediation_record(incident_id, triage["final"], diagnosis["final"])
        elif approval_mode == "approve":
            summary = _remediation_plan_summary(triage["final"], diagnosis["final"])
            req = approval_gate.request(incident_id=incident_id, severity=severity, summary=summary)
            public_base = os.getenv("ATLASOPS_PUBLIC_BASE_URL", "").rstrip("/")
            approve_url = f"{public_base}/approve" if public_base else "/approve"
            reject_url = f"{public_base}/approve" if public_base else "/approve"
            audit_log.record(
                incident_id=incident_id,
                agent_role="remediation",
                action_type="approval_requested",
                result_summary=summary,
                policy_check="requires_approval",
            )
            thought_emit(
                "remediation",
                "waiting_approval",
                f"Awaiting human approval for remediation plan (token: {req.token}).",
            )
            # Notify humans before remediation executes so they can truly intervene.
            try:
                TOOL_REGISTRY["slack_post_update"](
                    channel="#incident-response",
                    severity=severity,
                    title=f"Approval required: {triage['final'].get('title', incident_id)}",
                    summary=(
                        f"Remediation is paused pending human decision for {incident_id}. "
                        f"Token: {req.token}"
                    ),
                    action_items=[
                        f"Approve: POST {approve_url} with X-AtlasOps-Key and {{\"token\":\"{req.token}\",\"decision\":\"approved\",\"approved_by\":\"<name>\"}}",
                        f"Reject: POST {reject_url} with X-AtlasOps-Key and {{\"token\":\"{req.token}\",\"decision\":\"rejected\",\"approved_by\":\"<name>\",\"reason\":\"...\"}}",
                        "If no response, auto-timeout policy may proceed per config.",
                    ],
                )
                thought_emit(
                    "comms",
                    "tool_result",
                    "Human approval request sent to incident channel.",
                    tool="slack_post_update",
                )
            except Exception as e:
                log.warning("failed to send approval notification: %s", e)
            approval_result = await approval_gate.wait_for_decision(incident_id)
            approval_record["decision"] = approval_result.get("status")
            if approval_result.get("approved_by"):
                approval_record["approved_by"] = approval_result.get("approved_by")
            status = approval_result["status"]
            # "timeout" auto-approves so demos/unattended runs still complete.
            # Only an explicit "rejected" decision skips remediation.
            if status == "rejected":
                audit_log.record(
                    incident_id=incident_id,
                    agent_role="remediation",
                    action_type="approval_decision",
                    result_summary=status,
                    approved_by=approval_result.get("approved_by", ""),
                    policy_check="approval_denied",
                )
                thought_emit("remediation", "conclusion", "Remediation skipped — approval rejected by operator.")
                remediation = {
                    "role": "remediation",
                    "trajectory": [],
                    "final": {
                        "incident_id": incident_id,
                        "mode": "approve",
                        "status": "approval_rejected",
                        "approval": approval_result,
                    },
                }
            else:
                approver = approval_result.get("approved_by") or (
                    "auto-timeout" if status == "timeout" else "human-operator"
                )
                audit_log.record(
                    incident_id=incident_id,
                    agent_role="remediation",
                    action_type="approval_decision",
                    result_summary="approved",
                    approved_by=approver,
                    policy_check="approval_granted",
                )
                thought_emit("remediation", "thinking", f"Approval granted by {approver}; executing plan.")
                remediation_input["approval"] = approval_result
                remediation = await call_agent(
                    "remediation",
                    remediation_input,
                    policy_completion=remediation_policy_completion,
                )
        else:
            remediation = await call_agent(
                "remediation",
                remediation_input,
                policy_completion=remediation_policy_completion,
            )
        # Remediation execution is complete. Now execute objective environment verification.
        remediation_final = remediation.get("final", {})
        scenario_id = str(scenario_id or alert.get("scenario_id") or "")
        agent_claimed_resolved = bool(
            remediation_final.get("outcome") == "resolved"
            or remediation_final.get("status") == "resolved"
        )
        incident_context = {
            "incident_id": incident_id,
            "alert": alert,
            "triage": triage,
            "diagnosis": diagnosis,
            "remediation": remediation,
        }
        settling_report = await settle_environment(
            scenario_id=scenario_id,
            agent_claimed_resolved=agent_claimed_resolved,
            alert=alert,
            incident_context=incident_context,
        )
        from agents.verifier import verify_environment
        verification_result = verify_environment(
            scenario_id=scenario_id,
            agent_claimed_resolved=agent_claimed_resolved,
            alert=alert,
            incident_context=incident_context,
        )
        env_resolved = bool(verification_result.env_resolved)
        verification_dict = verification_result.to_dict()

        # Comms agent runs after environment verification and receives objective truth
        comms = await call_agent("comms", {
            "incident_id": incident_id,
            "triage": triage.get("final", {}),
            "diagnosis": diagnosis.get("final", {}),
            "remediation": remediation_final,
            "verification": verification_dict,
            "settling": settling_report,
            "env_resolved": env_resolved,
            "agent_claimed_resolved": agent_claimed_resolved,
        })

        # If the LLM skipped webhooks, still deliver a closure/status message (judges expect Discord/Slack pings).
        _webhook_out = bool(os.getenv("DISCORD_WEBHOOK_URL", "").strip() or os.getenv("SLACK_WEBHOOK_URL", "").strip())
        if _webhook_out and not _comms_trajectory_delivered_externally(comms):
            try:
                sev = _extract_severity({"triage": triage.get("final", {})}) or "P1"
                if sev not in {"P0", "P1", "P2", "P3"}:
                    sev = "P1"
                fin = comms.get("final") or {}
                summ = fin.get("summary") or fin.get("postmortem_path") or (
                    "Incident resolved and verified." if env_resolved else "Remediation attempted — incident remains unverified / unresolved."
                )
                if not isinstance(summ, str):
                    summ = json.dumps(summ)
                title = (triage.get("final") or {}).get("title") or incident_id
                status_label = "Resolved" if env_resolved else "Unresolved"
                out = TOOL_REGISTRY["slack_post_update"](
                    channel="#incident-response",
                    severity=sev,
                    title=f"[{sev}] {status_label}: {title}"[:220],
                    summary=summ[:3500],
                    action_items=["AtlasOps — full tool trace in live UI"],
                )
                thought_emit(
                    "comms",
                    "tool_result",
                    _narrate_slack_post_tool_result(out),
                    tool="slack_post_update",
                )
                comms.setdefault("trajectory", []).append({
                    "role": "comms",
                    "tool": "slack_post_update",
                    "output": out,
                    "note": "coordinator_closure_notify",
                })
            except Exception as e:
                log.warning("closure notify failed: %s", e)

        full_record = {
            "incident_id": incident_id,
            "alert": alert,
            "scenario_id": scenario_id,
            "approval": approval_record,
            "triage": triage,
            "diagnosis": diagnosis,
            "remediation": remediation,
            "verification": verification_dict,
            "agent_claimed_resolved": agent_claimed_resolved,
            "env_resolved": env_resolved,
            "comms": comms,
        }
        TRAJECTORIES_DIR.mkdir(parents=True, exist_ok=True)
        (TRAJECTORIES_DIR / f"{incident_id}.json").write_text(
            json.dumps(full_record, indent=2), encoding="utf-8",
        )
        if _live_judge_requested():
            from agents.judge import infer_tier_from_alert, judge_trajectory

            tier = infer_tier_from_alert(alert)
            jm = os.getenv("JUDGE_MODEL", "judge-model")
            thought_emit("comms", "tool_call", f"Scoring incident with external judge ({tier} rubric)…", tool="judge")
            try:
                scores = await judge_trajectory(full_record, tier=tier)
                ov = float(scores.get("overall", 0.0))
                crit = str(scores.get("critique", ""))[:400]
                thought_emit(
                    "comms",
                    "tool_result",
                    f"{jm} — overall {ov:.2f}. {crit}".strip(),
                    tool="judge_trajectory",
                )
            except Exception as e:
                log.warning("live judge failed: %s", e)
                thought_emit(
                    "comms",
                    "tool_result",
                    f"Judge request failed ({type(e).__name__}: {e}). Scores skipped.",
                    tool="judge_trajectory",
                )

        # Fail-closed operational resolution: environment verifier is authoritative
        resolved = env_resolved
        # Classify the outcome so the circuit breaker can distinguish
        # designed human decisions from real system failures.
        rem_status = str(remediation_final.get("status", ""))
        rem_mode = str(remediation_final.get("mode", ""))
        if rem_status == "approval_rejected":
            finish_reason = "approval_rejected"
        elif rem_status == "approval_timeout":
            finish_reason = "approval_timeout"
        elif rem_mode == "manual":
            finish_reason = "manual_runbook"
        elif verification_result.verification_status == "inconclusive":
            finish_reason = "verification_inconclusive"
        elif not resolved:
            finish_reason = "unresolved"
        else:
            finish_reason = ""
        audit_log.record(
            incident_id=incident_id,
            agent_role="coordinator",
            action_type="incident_end",
            result_summary="resolved" if resolved else "not_resolved",
        )
        return full_record
    except Exception as e:
        run_error = str(e)
        finish_reason = "system_error"
        raise
    finally:
        circuit_breaker.finish_incident(incident_id, resolved=resolved, reason=finish_reason if run_error else finish_reason)
        try:
            from agents.tools.comms import discord_scenario_run_ping

            discord_scenario_run_ping(
                incident_id,
                alert,
                resolved=resolved if not run_error else False,
                triage_title=str(tri_snap.get("title", "")),
                triage_severity=str(tri_snap.get("severity", "")),
                error=run_error,
            )
        except Exception as ping_ex:
            log.warning("Discord every-run ping failed: %s", ping_ex)


app = FastAPI(title="AtlasOps Coordinator")


_WEBHOOK_SECRET = os.getenv("ALERTMANAGER_WEBHOOK_SECRET", "")


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()

    # Validate Bearer token when secret is configured
    if _WEBHOOK_SECRET:
        import hmac as _hmac
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        token = auth.removeprefix("Bearer ").strip()
        if not _hmac.compare_digest(token.encode(), _WEBHOOK_SECRET.encode()):
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    payload = AlertWebhookPayload.model_validate(json.loads(body)).model_dump()
    log.info("received alertmanager webhook: %d alerts", len(payload.get("alerts", [])))
    incident_id, _is_new, should_dispatch = correlator.ingest(payload)
    if not should_dispatch:
        return JSONResponse({"ok": True, "incident_id": incident_id, "correlated": True, "dispatched": False})
    correlator.mark_processing(incident_id, True)
    try:
        result = await handle_incident(payload, incident_id=incident_id)
        return JSONResponse({"ok": True, "incident_id": result["incident_id"], "correlated": True, "dispatched": True})
    finally:
        correlator.mark_processing(incident_id, False)


@app.post("/approve", dependencies=[Security(_require_runtime_api_key)])
async def approve(request: Request):
    """Token-scoped approval callback used by the dedicated coordinator runtime."""
    payload = ApprovalCallbackPayload.model_validate(await request.json())
    result = approval_gate.callback(
        token=payload.token,
        decision=payload.decision,
        approved_by=payload.approved_by or "human-operator",
        reason=payload.reason,
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.get("/approval/pending", dependencies=[Security(_require_runtime_api_key)])
async def approval_pending():
    """Expose non-secret pending approval metadata to in-cluster operators."""
    return JSONResponse({"pending": approval_gate.pending()})


@app.get("/stream")
async def stream_thoughts():
    """SSE endpoint — dashboard subscribes here for live agent thoughts."""
    from agents.stream import subscribe
    return StreamingResponse(
        subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/thoughts")
async def get_thoughts():
    """Return full thought history for the timeline tab."""
    from agents.stream import get_history
    return {"thoughts": get_history()}


@app.get("/health")
async def health():
    ju = os.getenv("JUDGE_URL", "").rstrip("/")
    return {
        "status": "ok",
        "vllm": VLLM_BASE,
        "model": MODEL_NAME,
        "judge_url": ju,
        "judge_model": os.getenv("JUDGE_MODEL", ""),
        "live_judge": _live_judge_requested(),
        "hf_inference_pack": os.getenv("ATLASOPS_USE_HF_INFERENCE", ""),
    }


@app.get("/healthz")
async def healthz():
    """Side-effect-free Kubernetes liveness/readiness probe."""
    return {"status": "ok"}


@app.get("/metrics")
async def coordinator_prometheus_metrics():
    """When this app is mounted at `/api` on the Space, this serves `GET /api/metrics`."""
    return JSONResponse(await build_dashboard_metrics_payload())


@app.get("/alertmanager/alerts")
async def coordinator_alertmanager_feed():
    """Serves `GET /api/alertmanager/alerts` on the Space (mounted at `/api`)."""
    result = alertmanager_list_alerts(active_only=True)
    payload: dict[str, Any] = {"count": result.get("count", 0), "alerts": result.get("alerts", [])}
    if not result.get("success"):
        payload["error"] = result.get("error", "alertmanager_unreachable")
    return JSONResponse(payload)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9099)
