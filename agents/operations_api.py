from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from typing import Any, Callable, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agents.action_execution import operational_mode
from agents.audit import audit_log, require_audit_log
from agents.execution_journal import ExecutionJournalError, get_execution_journal
from agents.telemetry import configure_telemetry, shutdown_telemetry, telemetry_status


class OperationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=256)
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "rejected"]
    reason: str = Field(default="", max_length=2000)


def operator_identity(request: Request, *, fallback: str = "") -> str:
    if not operational_mode():
        return fallback
    principal = os.getenv("ATLASOPS_OPERATOR_ID", "").strip()
    if not principal:
        raise HTTPException(status_code=503, detail="A server-configured operator identity is required")
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Operator decisions require the same origin")
    if request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
        raise HTTPException(status_code=403, detail="Cross-site operator decisions are blocked")
    return principal


@asynccontextmanager
async def operations_lifespan(_application: FastAPI):
    enabled = operational_mode()
    try:
        if enabled:
            configure_telemetry()
            require_audit_log()
            get_execution_journal().reconcile_restart()
        yield
    finally:
        if enabled:
            shutdown_telemetry()


def operations_router(
    *, gate: Callable[[], Any], breaker: Callable[[], Any], require_key: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(dependencies=[Security(require_key)])

    def configured():
        if not operational_mode():
            raise HTTPException(status_code=503, detail="Operational controls are not enabled")
        require_audit_log()
        return get_execution_journal()

    def verified_audit():
        try:
            integrity = require_audit_log().verify_integrity(include_records=True)
        except (OSError, ValueError):
            raise HTTPException(status_code=503, detail="Audit evidence is unavailable") from None
        records = integrity.pop("records", [])
        if not integrity.get("ok"):
            raise HTTPException(status_code=503, detail="Audit integrity verification failed")
        return integrity, records

    def require_execution_audit(execution, by_hash):
        for event in execution["events"]:
            row = by_hash.get(event.get("audit_entry_hash"))
            transition = {key: value for key, value in event.items() if key != "audit_entry_hash"}
            transition_hash = hashlib.sha256(
                json.dumps(transition, sort_keys=True).encode("utf-8"),
            ).hexdigest()
            if (
                row is None or row.get("incident_id") != execution["incident_id"]
                or row.get("agent_role") != "coordinator"
                or row.get("action_type") != "execution_progress"
                or row.get("tool_args_hash") != transition_hash
            ):
                raise HTTPException(status_code=503, detail="Execution audit evidence is incomplete")

    @router.get("/operations")
    def operations_status():
        journal = configured()
        try:
            executions = journal.list(include_events=True)
        except (OSError, ExecutionJournalError, ValueError):
            raise HTTPException(status_code=503, detail="Operational evidence is unavailable") from None
        _integrity, records = verified_audit()
        by_hash = {row["entry_hash"]: row for row in records}
        for execution in executions:
            require_execution_audit(execution, by_hash)
            execution.pop("events")
        return {
            "enabled": True,
            "source_sha": os.getenv("ATLASOPS_SOURCE_SHA", ""),
            "certification_status": "NOT_CERTIFIED",
            "operator_id": os.getenv("ATLASOPS_OPERATOR_ID", "").strip() or None,
            "approval_policy": "exact-action-all-mutations",
            "telemetry": telemetry_status(),
            "executions": executions, "pending": gate().pending(),
            "circuit_breaker": breaker().status(),
        }

    @router.get("/operations/{execution_id}/evidence")
    def operation_evidence(execution_id: str):
        journal = configured()
        try:
            execution = journal.read(execution_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Execution not found") from None
        except (OSError, ExecutionJournalError, ValueError):
            raise HTTPException(status_code=503, detail="Operational evidence is unavailable") from None
        integrity, records = verified_audit()
        by_hash = {row["entry_hash"]: row for row in records}
        require_execution_audit(execution, by_hash)
        payload = {
            **execution, "source_sha": os.getenv("ATLASOPS_SOURCE_SHA", ""),
            "certification_status": "NOT_CERTIFIED", "audit_integrity": integrity,
            "audit_scope": "incident_records",
            "audit": [row for row in records if row.get("incident_id") == execution["incident_id"]],
        }
        return JSONResponse(payload, headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="atlasops-{execution_id}.json"',
            "X-Content-Type-Options": "nosniff",
        })

    @router.post("/operations/decision")
    async def operation_decision(payload: OperationDecision, request: Request):
        configured()
        principal = operator_identity(request)
        pending = next(
            (item for item in gate().pending() if item.get("token") == payload.token),
            None,
        )
        if pending is None or not (pending.get("action") or {}).get("target"):
            raise HTTPException(status_code=400, detail="No matching prepared operational action")
        result = gate().callback(
            payload.token, payload.decision, approved_by=principal,
            reason=payload.reason, action_digest=payload.action_digest,
        )
        audit_log.record(
            incident_id=pending["incident_id"], agent_role="operator",
            action_type="operator_decision", approved_by=principal,
            tool_args={"action_digest": payload.action_digest, "decision": payload.decision},
            result_summary=str(result.get("error") or payload.decision),
            policy_check="accepted" if result.get("ok") else "blocked",
        )
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @router.post("/operations/pause")
    def operation_pause(request: Request):
        configured()
        principal = operator_identity(request)
        status = breaker().pause()
        audit_log.record(
            incident_id="operator", agent_role="operator", action_type="automation_paused",
            approved_by=principal, result_summary="Further tool execution is blocked",
        )
        return status

    return router
