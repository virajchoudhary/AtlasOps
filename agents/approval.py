"""Human-in-the-loop approval gate for remediation actions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


APPROVAL_POLICY = {
    "P0": "manual",
    "P1": "approve",
    "P2": "auto",
    "P3": "auto",
}


def approval_mode_for_severity(severity: str) -> str:
    return APPROVAL_POLICY.get(str(severity).upper(), "approve")


@dataclass
class ApprovalRequest:
    incident_id: str
    severity: str
    summary: str
    token: str = field(default_factory=lambda: f"apr-{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)
    decision: str = "pending"
    approved_by: str = ""
    reason: str = ""
    action: dict[str, Any] | None = None
    action_digest: str | None = None
    action_summary: str | None = None
    operator_scope: dict[str, str] | None = None
    waiter_active: bool = False
    event: asyncio.Event = field(default_factory=asyncio.Event)

    def to_dict(self) -> dict[str, Any]:
        record = {
            "incident_id": self.incident_id,
            "severity": self.severity,
            "summary": self.summary,
            "token": self.token,
            "created_at": self.created_at,
            "decision": self.decision,
            "approved_by": self.approved_by,
            "reason": self.reason,
        }
        if self.action is not None:
            record["action"] = json.loads(
                json.dumps(self.action, ensure_ascii=False, allow_nan=False)
            )
            record["action_digest"] = self.action_digest
            record["action_summary"] = self.action_summary
            if self.operator_scope is not None:
                record["operator_scope"] = dict(self.operator_scope)
        return record


@dataclass(frozen=True, slots=True)
class ActionApprovalPermit:
    permit_id: str
    incident_id: str
    action_digest: str
    approved_by: str
    issued_at: float


class ApprovalGate:
    def __init__(self, timeout_seconds: int | None = None):
        # Default 300s so humans can intervene during demos.
        # Override via APPROVAL_TIMEOUT_SECONDS as needed.
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None
            else int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "300"))
        )
        self._pending_by_incident: dict[str, ApprovalRequest] = {}
        self._pending_by_token: dict[str, ApprovalRequest] = {}
        self._action_permits: dict[str, ActionApprovalPermit] = {}

    @staticmethod
    def action_digest(action: Mapping[str, Any]) -> str:
        tool = action.get("tool")
        arguments = action.get("arguments")
        if not isinstance(tool, str) or not tool:
            raise ValueError("Approved policy action requires a tool name")
        if not isinstance(arguments, Mapping):
            raise TypeError("Approved policy action arguments must be an object")
        canonical = json.dumps(
            {"tool": tool, "arguments": dict(arguments)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _register_request(self, req: ApprovalRequest) -> ApprovalRequest:
        self._pending_by_incident[req.incident_id] = req
        self._pending_by_token[req.token] = req
        return req

    def request(self, incident_id: str, severity: str, summary: str) -> ApprovalRequest:
        return self._register_request(
            ApprovalRequest(incident_id=incident_id, severity=severity, summary=summary)
        )

    def request_action(
        self,
        *,
        incident_id: str,
        severity: str,
        action: Mapping[str, Any],
        operator_scope: Mapping[str, str] | None = None,
    ) -> ApprovalRequest:
        if incident_id in self._pending_by_incident:
            raise RuntimeError("An approval request is already pending for this incident")
        digest = self.action_digest(action)
        tool = str(action["tool"])
        arguments = dict(action["arguments"])
        canonical_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        action_record = json.loads(
            json.dumps(
                {"tool": tool, "arguments": arguments},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        action_summary = f"{tool} arguments={canonical_arguments}"
        scope = None
        if operator_scope is not None:
            scope = dict(operator_scope)
            if (
                set(scope) != {"kube_context", "scenario_id"}
                or any(not isinstance(value, str) or not value.strip() for value in scope.values())
            ):
                raise ValueError("Operator scope requires a Kubernetes context and scenario ID")
        return self._register_request(
            ApprovalRequest(
                incident_id=incident_id,
                severity=severity,
                summary=f"{severity} exact policy action approval: {action_summary}",
                action=action_record,
                action_digest=digest,
                action_summary=action_summary,
                operator_scope=scope,
            )
        )

    async def wait_for_decision(self, incident_id: str) -> dict[str, Any]:
        req = self._pending_by_incident.get(incident_id)
        if not req or req.action_digest is not None:
            return {"status": "missing", "incident_id": incident_id}
        return await self._wait_for_request(req)

    async def wait_for_action_decision(
        self,
        incident_id: str,
        *,
        request_token: str,
    ) -> tuple[dict[str, Any], ActionApprovalPermit | None]:
        req = self._pending_by_incident.get(incident_id)
        if (
            req is None
            or req.token != request_token
            or req.action_digest is None
            or req.waiter_active
        ):
            return {"status": "missing", "incident_id": incident_id}, None
        result = await self._wait_for_request(req)
        if result.get("status") != "approved":
            return result, None
        if not isinstance(result.get("approved_by"), str) or not result["approved_by"].strip():
            return {**result, "status": "identity_missing"}, None
        permit = ActionApprovalPermit(
            permit_id=uuid.uuid4().hex,
            incident_id=req.incident_id,
            action_digest=req.action_digest,
            approved_by=result["approved_by"].strip(),
            issued_at=time.time(),
        )
        self._action_permits[permit.permit_id] = permit
        return result, permit

    def consume_action_permit(
        self,
        permit: ActionApprovalPermit | None,
        *,
        incident_id: str,
        action_digest: str,
    ) -> bool:
        if not isinstance(permit, ActionApprovalPermit):
            return False
        stored = self._action_permits.get(permit.permit_id)
        if stored is not permit:
            return False
        self._action_permits.pop(permit.permit_id, None)
        age = time.time() - permit.issued_at
        return (
            permit.incident_id == incident_id
            and permit.action_digest == action_digest
            and 0 <= age <= self.timeout_seconds
        )

    async def _wait_for_request(self, req: ApprovalRequest) -> dict[str, Any]:
        if req.waiter_active:
            return {"status": "missing", "incident_id": req.incident_id}
        req.waiter_active = True
        try:
            await asyncio.wait_for(req.event.wait(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            req.decision = "timeout"
            self._clear(req)
            return {"status": "timeout", "incident_id": req.incident_id}
        except asyncio.CancelledError:
            self._clear(req)
            raise
        result = {
            "status": req.decision,
            "incident_id": req.incident_id,
            "approved_by": req.approved_by,
            "reason": req.reason,
        }
        self._clear(req)
        return result

    def callback(self, token: str, decision: str, approved_by: str = "", reason: str = "") -> dict[str, Any]:
        req = self._pending_by_token.get(token)
        if not req:
            return {"ok": False, "error": "unknown_token"}
        if req.decision != "pending":
            return {"ok": False, "error": "already_decided"}
        normalized = decision.lower().strip()
        if normalized not in {"approved", "rejected"}:
            return {"ok": False, "error": "decision must be approved or rejected"}
        operator_identity = approved_by.strip() if isinstance(approved_by, str) else ""
        if not operator_identity:
            return {"ok": False, "error": "approved_by is required"}
        req.decision = normalized
        req.approved_by = operator_identity
        req.reason = reason
        req.event.set()
        return {"ok": True, "incident_id": req.incident_id, "decision": normalized}

    def pending(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._pending_by_incident.values()]

    def _clear(self, req: ApprovalRequest) -> None:
        if self._pending_by_incident.get(req.incident_id) is req:
            self._pending_by_incident.pop(req.incident_id, None)
        self._pending_by_token.pop(req.token, None)


approval_gate = ApprovalGate()
