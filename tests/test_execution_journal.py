import json
import sqlite3

import pytest

from agents.execution_journal import ExecutionJournal, ExecutionJournalError


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-placeholder-journal-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    result = ExecutionJournal(tmp_path / "executions.sqlite3")
    result.begin("inc-journal", "a" * 32, alert={"commonLabels": {"alertname": "Unavailable"}}, anchors=None)
    return result


def test_severity_and_roles_are_durable_before_any_mutation(journal):
    journal.note("a" * 32, phase="triage", event="role_completed", patch={"triage": {"final": {"severity": "P1"}}})
    journal.note("a" * 32, phase="approval", event="approval_policy", patch={"approval": {"severity": "P1", "mode": "approve"}})
    reopened = ExecutionJournal(journal.path)
    record = reopened.read("a" * 32)
    assert record["record"]["triage"]["final"]["severity"] == "P1"
    assert record["record"]["approval"]["mode"] == "approve"
    assert record["record"]["env_resolved"] is None
    assert record["integrity"]["ok"] is True


@pytest.mark.parametrize("phase,state", [("approval", "interrupted"), ("applying", "uncertain")])
def test_restart_retains_unfinished_execution_without_replay(journal, phase, state):
    journal.note("a" * 32, phase=phase, event="unfinished", patch={"approval": {"action_digest": "b" * 64}})
    restarted = ExecutionJournal(journal.path)
    restarted.reconcile_restart()
    result = restarted.read("a" * 32)
    assert result["state"] == state
    assert result["record"]["resolved"] is False
    assert result["record"]["replay_allowed"] is False
    assert result["record"]["approval"]["action_digest"] == "b" * 64
    with pytest.raises(ExecutionJournalError, match="terminal"):
        restarted.note("a" * 32, phase="applying", event="replay")


def test_corrupted_event_or_snapshot_fails_closed(journal):
    with sqlite3.connect(journal.path) as connection:
        connection.execute("UPDATE executions SET record=?", (json.dumps({"resolved": True}),))
    with pytest.raises(ExecutionJournalError, match="snapshot"):
        journal.read("a" * 32)


def test_journal_never_exports_approval_tokens_or_credential_text(journal):
    journal.note(
        "a" * 32, phase="diagnosis", event="role_completed",
        patch={"diagnosis": {"token": "do-not-export", "error": "Authorization: Bearer fixture-leak"}},
    )
    exported = json.dumps(journal.read("a" * 32))
    assert "do-not-export" not in exported
    assert "fixture-leak" not in exported
    assert "REDACTED" in exported


def test_completed_negative_result_stays_negative_across_restart(journal):
    journal.note(
        "a" * 32, phase="finished", state="completed", event="finished",
        patch={"verification": {"verification_status": "failed"}, "resolved": False, "env_resolved": False},
    )
    reopened = ExecutionJournal(journal.path)
    reopened.reconcile_restart()
    assert reopened.read("a" * 32)["record"]["verification"]["verification_status"] == "failed"


def test_failed_admission_event_cannot_leave_a_startup_fatal_execution(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from agents import execution_journal

    path = tmp_path / "executions.sqlite3"
    journal = ExecutionJournal(path)
    record = Mock(side_effect=RuntimeError("audit write unavailable"))
    monkeypatch.setattr(execution_journal.audit_log, "record", record)
    with pytest.raises(RuntimeError, match="audit write unavailable"):
        journal.begin("inc-admission", "c" * 32, alert={}, anchors=None)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0] == 0
    restarted = ExecutionJournal(path)
    restarted.reconcile_restart()
    record.side_effect = None
    record.return_value = {"entry_hash": "d" * 64}
    restarted.begin("inc-admission", "c" * 32, alert={}, anchors=None)
    assert restarted.read("c" * 32)["integrity"]["ok"] is True
