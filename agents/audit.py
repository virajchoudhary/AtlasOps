"""Append-only audit trail with hash-chain integrity checks."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any


class AuditConfigurationError(RuntimeError):
    """Raised when secure global audit configuration is unavailable."""


class AuditLog:
    def __init__(self, secret_key: str, log_path: Path):
        if not secret_key.strip():
            raise ValueError(
                "audit_configuration_error: secret_key is required for audit integrity"
            )
        self.secret_key = secret_key.encode("utf-8")
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash = self._load_last_hash()

    def _load_last_hash(self) -> str:
        if not self.log_path.exists():
            return ""
        lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return ""
        try:
            last = json.loads(lines[-1])
            return str(last.get("entry_hash", ""))
        except json.JSONDecodeError:
            return ""

    @staticmethod
    def _sha256_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def record(
        self,
        incident_id: str,
        agent_role: str,
        action_type: str,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        result_summary: str = "",
        approved_by: str = "",
        policy_check: str = "allowed",
    ) -> dict[str, Any]:
        tool_args = tool_args or {}
        entry = {
            "ts": round(time.time(), 3),
            "incident_id": incident_id,
            "agent_role": agent_role,
            "action_type": action_type,
            "tool_name": tool_name or "",
            "tool_args_hash": self._sha256_text(json.dumps(tool_args, sort_keys=True)),
            "result_summary": result_summary[:300],
            "approved_by": approved_by,
            "policy_check": policy_check,
            "prev_hash": self._last_hash,
        }
        canonical = json.dumps(entry, sort_keys=True)
        signature = hmac.new(self.secret_key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        entry_hash = self._sha256_text(canonical + signature)
        entry["signature"] = signature
        entry["entry_hash"] = entry_hash
        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry) + "\n")
        self._last_hash = entry_hash
        return entry

    def tail(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return []
        entries = [json.loads(line) for line in lines]
        if offset:
            entries = entries[offset:]
        return entries[-limit:]

    def verify_integrity(self) -> dict[str, Any]:
        if not self.log_path.exists():
            return {"ok": True, "entries": 0}
        lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
        prev_hash = ""
        for index, line in enumerate(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return {"ok": False, "error": f"invalid_json_at_line_{index + 1}"}
            expected_prev = entry.get("prev_hash", "")
            if expected_prev != prev_hash:
                return {"ok": False, "error": f"hash_chain_mismatch_at_line_{index + 1}"}
            check = dict(entry)
            signature = str(check.pop("signature", ""))
            entry_hash = str(check.pop("entry_hash", ""))
            canonical = json.dumps(check, sort_keys=True)
            expected_signature = hmac.new(
                self.secret_key,
                canonical.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if expected_signature != signature:
                return {"ok": False, "error": f"signature_mismatch_at_line_{index + 1}"}
            expected_hash = self._sha256_text(canonical + signature)
            if expected_hash != entry_hash:
                return {"ok": False, "error": f"entry_hash_mismatch_at_line_{index + 1}"}
            prev_hash = entry_hash
        return {"ok": True, "entries": len(lines)}


_configured_audit_log: AuditLog | None = None
_configured_audit_identity: tuple[bytes, Path] | None = None


def require_audit_log() -> AuditLog:
    """Return the runtime audit log or fail before consequential execution."""
    secret = os.getenv("ATLASOPS_AUDIT_SECRET", "")
    if not secret.strip():
        raise AuditConfigurationError(
            "audit_configuration_error: ATLASOPS_AUDIT_SECRET is required for audit integrity"
        )
    configured_path = os.getenv("ATLASOPS_AUDIT_LOG", "").strip()
    log_path = Path(configured_path or "data/audit_log.jsonl")
    identity = (hashlib.sha256(secret.encode("utf-8")).digest(), log_path)

    global _configured_audit_log, _configured_audit_identity
    if _configured_audit_log is None or _configured_audit_identity != identity:
        _configured_audit_log = AuditLog(secret_key=secret, log_path=log_path)
        _configured_audit_identity = identity
    return _configured_audit_log


class _ConfiguredAuditLog:
    """Import-safe proxy that requires explicit runtime secret configuration."""

    def record(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return require_audit_log().record(*args, **kwargs)

    def tail(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return require_audit_log().tail(limit=limit, offset=offset)

    def verify_integrity(self) -> dict[str, Any]:
        return require_audit_log().verify_integrity()


audit_log = _ConfiguredAuditLog()
