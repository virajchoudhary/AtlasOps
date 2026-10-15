"""Durable, fail-closed admission records for incident occurrences."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from os import PathLike
from pathlib import Path


class IncidentAdmissionError(RuntimeError):
    """The durable admission state did not match the dispatcher's expectation."""


@dataclass(frozen=True)
class AdmissionResult:
    admission_token: str | None
    admitted_keys: tuple[str, ...]
    duplicates: tuple[tuple[str, str], ...]


class IncidentAdmissionStore:
    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS incident_occurrence_admissions (
            occurrence_key TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('queued', 'attempted')),
            admission_token TEXT NOT NULL
        )
    """

    def __init__(self, database_path: str | PathLike[str], *, timeout: float = 5.0):
        path = str(database_path)
        if not path or path == ":memory:":
            raise ValueError("incident admission requires an on-disk SQLite path")
        self._path = Path(path).absolute()
        self._timeout = timeout

    def admit(self, incident_id: str, occurrence_keys: tuple[str, ...]) -> AdmissionResult:
        keys = tuple(dict.fromkeys(occurrence_keys))
        if not keys:
            return AdmissionResult(None, (), ())

        token = uuid.uuid4().hex
        admitted: list[str] = []
        duplicates: list[tuple[str, str]] = []
        with self._transaction() as connection:
            for key in keys:
                inserted = connection.execute(
                    """
                    INSERT INTO incident_occurrence_admissions
                        (occurrence_key, incident_id, state, admission_token)
                    VALUES (?, ?, 'queued', ?)
                    ON CONFLICT (occurrence_key) DO NOTHING
                    """,
                    (key, incident_id, token),
                )
                if inserted.rowcount == 1:
                    admitted.append(key)
                    continue
                existing = connection.execute(
                    """
                    SELECT incident_id
                    FROM incident_occurrence_admissions
                    WHERE occurrence_key = ?
                    """,
                    (key,),
                ).fetchone()
                if existing is None:
                    raise IncidentAdmissionError("occurrence admission disappeared")
                duplicates.append((key, str(existing[0])))

        return AdmissionResult(
            token if admitted else None,
            tuple(admitted),
            tuple(duplicates),
        )

    def mark_attempted(self, admission_token: str, occurrence_keys: tuple[str, ...]) -> None:
        keys = tuple(dict.fromkeys(occurrence_keys))
        if not keys:
            return

        with self._transaction() as connection:
            updated = 0
            for key in keys:
                result = connection.execute(
                    """
                    UPDATE incident_occurrence_admissions
                    SET state = 'attempted'
                    WHERE occurrence_key = ?
                      AND admission_token = ?
                      AND state = 'queued'
                    """,
                    (key, admission_token),
                )
                updated += result.rowcount
            if updated != len(keys):
                raise IncidentAdmissionError("queued occurrence admission could not be marked attempted")

    def rollback_queued(self, admission_token: str) -> None:
        if not admission_token:
            return
        with self._transaction() as connection:
            connection.execute(
                """
                DELETE FROM incident_occurrence_admissions
                WHERE admission_token = ? AND state = 'queued'
                """,
                (admission_token,),
            )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self._path,
            timeout=self._timeout,
            isolation_level=None,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(self._CREATE_TABLE)
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise
        finally:
            connection.close()
