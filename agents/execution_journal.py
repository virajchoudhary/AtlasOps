from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from agents.audit import audit_log
from agents.evidence_redaction import evidence_field_is_secret, redact_persisted_evidence
from agents.incident_history import _valid_execution_id, _valid_incident_id

_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted", "uncertain"})
_PHASES = frozenset({
    "queued", "triage", "diagnosis", "remediation", "approval", "applying",
    "verification", "comms", "finished",
})
_MAX_EVENTS = 10_000


class ExecutionJournalError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _safe_evidence(value: Any, depth: int = 0) -> Any:
    if depth > 16:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            key: _safe_evidence(item, depth + 1)
            for key, item in list(value.items())[:128]
            if isinstance(key, str) and not evidence_field_is_secret(key, item)
        }
    if isinstance(value, list):
        return [_safe_evidence(item, depth + 1) for item in value[:128]]
    if isinstance(value, str):
        return redact_persisted_evidence(value)[:8192]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


class ExecutionJournal:
    def __init__(self, path: Path):
        if str(path) == ":memory:":
            raise ValueError("Operational evidence requires a durable journal")
        self.path = path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    state TEXT NOT NULL,
                    record TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS execution_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL REFERENCES executions(execution_id),
                    event TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL
                )
            """)
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def begin(
        self, incident_id: str, execution_id: str,
        *, alert: dict[str, Any], anchors: dict[str, Any] | None,
        evidence_class: str = "OPERATIONAL_UNCERTIFIED",
    ) -> None:
        if not _valid_incident_id(incident_id) or not _valid_execution_id(execution_id):
            raise ValueError("Invalid operational execution identity")
        record = _safe_evidence({
            "incident_id": incident_id, "execution_id": execution_id,
            "source_sha": os.getenv("ATLASOPS_SOURCE_SHA", "").strip() or None,
            "alert": alert, "incident_anchors": anchors,
            "evidence_class": evidence_class, "certification_status": "NOT_CERTIFIED",
            "resolved": False, "env_resolved": None,
        })
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO executions VALUES (?, ?, 'queued', 'running', ?, ?)",
                (execution_id, incident_id, _canonical(record), time.time()),
            )
            self._note(connection, execution_id, phase="queued", event="admitted")

    def note(
        self, execution_id: str, *, phase: str, event: str,
        patch: dict[str, Any] | None = None, state: str = "running",
    ) -> None:
        if phase not in _PHASES or state not in _TERMINAL | {"running"}:
            raise ValueError("Invalid execution transition")
        with self._connection() as connection:
            self._note(
                connection, execution_id, phase=phase, event=event, patch=patch, state=state,
            )

    def _note(
        self, connection: sqlite3.Connection, execution_id: str, *, phase: str,
        event: str, patch: dict[str, Any] | None = None, state: str = "running",
    ) -> None:
        row = connection.execute(
            "SELECT * FROM executions WHERE execution_id=?", (execution_id,),
        ).fetchone()
        if row is None:
            raise ExecutionJournalError("Execution was not durably admitted")
        if row["state"] in _TERMINAL:
            raise ExecutionJournalError("A terminal execution cannot replay or advance")
        record = json.loads(row["record"])
        for field in ("incident_id", "execution_id", "source_sha"):
            if field in (patch or {}) and patch[field] != record.get(field):
                raise ExecutionJournalError("Execution identity cannot change during progress")
        record.update(_safe_evidence(patch or {}))
        last = connection.execute(
            "SELECT entry_hash FROM execution_events WHERE execution_id=? ORDER BY sequence DESC LIMIT 1",
            (execution_id,),
        ).fetchone()
        previous = last["entry_hash"] if last else ""
        transition = {
            "execution_id": execution_id, "event": event, "phase": phase,
            "state": state, "record": record, "ts": time.time(), "prev_hash": previous,
        }
        _canonical(transition)
        audit = audit_log.record(
            incident_id=row["incident_id"], agent_role="coordinator",
            action_type="execution_progress", tool_args=transition,
            result_summary=_canonical({
                "event": event, "phase": phase, "state": state,
                "severity": (record.get("approval") or {}).get("severity"),
                "policy": (record.get("approval") or {}).get("mode"),
            }),
            policy_check=state,
        )
        payload = _canonical({
            **transition, "audit_entry_hash": audit.get("entry_hash"),
        })
        digest = hashlib.sha256((previous + payload).encode()).hexdigest()
        connection.execute(
            "INSERT INTO execution_events(execution_id,event,prev_hash,entry_hash) VALUES(?,?,?,?)",
            (execution_id, payload, previous, digest),
        )
        connection.execute(
            "UPDATE executions SET phase=?,state=?,record=?,updated_at=? WHERE execution_id=?",
            (phase, state, _canonical(record), transition["ts"], execution_id),
        )

    def read(self, execution_id: str) -> dict[str, Any]:
        if not _valid_execution_id(execution_id):
            raise KeyError(execution_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM executions WHERE execution_id=?", (execution_id,),
            ).fetchone()
            if row is None:
                raise KeyError(execution_id)
            events = connection.execute(
                "SELECT * FROM execution_events WHERE execution_id=? ORDER BY sequence LIMIT ?",
                (execution_id, _MAX_EVENTS + 1),
            ).fetchall()
        if not events or len(events) > _MAX_EVENTS:
            raise ExecutionJournalError("Execution event history is absent or exceeds its limit")
        previous = ""
        for event in events:
            transition = json.loads(event["event"])
            if (
                not isinstance(transition, dict)
                or transition.get("execution_id") != execution_id
                or ("prev_hash" in transition and transition["prev_hash"] != previous)
            ):
                raise ExecutionJournalError("Execution transition identity or linkage is invalid")
            expected = hashlib.sha256((previous + event["event"]).encode()).hexdigest()
            if event["prev_hash"] != previous or event["entry_hash"] != expected:
                raise ExecutionJournalError("Execution evidence hash chain is invalid")
            previous = expected
        last = json.loads(events[-1]["event"])
        if (
            last["record"] != json.loads(row["record"])
            or last["phase"] != row["phase"] or last["state"] != row["state"]
            or ("prev_hash" in last and last["ts"] != row["updated_at"])
        ):
            raise ExecutionJournalError("Execution snapshot differs from its event history")
        return {
            "incident_id": row["incident_id"], "execution_id": execution_id,
            "source_sha": last["record"].get("source_sha"),
            "phase": row["phase"], "state": row["state"], "updated_at": row["updated_at"],
            "record": last["record"], "events": [json.loads(event["event"]) for event in events],
            "integrity": {"ok": True, "events": len(events), "last_hash": previous},
        }

    def list(self, limit: int = 50, *, include_events: bool = False) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT execution_id FROM executions ORDER BY updated_at DESC LIMIT ?",
                (min(max(limit, 1), 100),),
            ).fetchall()
        return [
            {
                key: value for key, value in self.read(row["execution_id"]).items()
                if include_events or key != "events"
            }
            for row in rows
        ]

    def reconcile_restart(self) -> None:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT execution_id,phase FROM executions WHERE state='running'",
            ).fetchall()
        for row in rows:
            self.read(row["execution_id"])
            state = "uncertain" if row["phase"] == "applying" else "interrupted"
            self.note(
                row["execution_id"], phase="finished", state=state,
                event="restart_reconciled",
                patch={"resolved": False, "replay_allowed": False, "interrupted_phase": row["phase"]},
            )


@dataclass(frozen=True)
class ExecutionProgress:
    journal: ExecutionJournal
    execution_id: str

    def note(self, phase: str, event: str, **patch: Any) -> None:
        self.journal.note(self.execution_id, phase=phase, event=event, patch=patch)


current_execution: ContextVar[ExecutionProgress | None] = ContextVar(
    "atlasops_operational_execution", default=None,
)
_journal: ExecutionJournal | None = None


def get_execution_journal() -> ExecutionJournal:
    global _journal
    path = Path(os.getenv("ATLASOPS_RUNTIME_DATA_DIR", "data")) / "incident-executions.sqlite3"
    if _journal is None or _journal.path != path:
        _journal = ExecutionJournal(path)
    return _journal
