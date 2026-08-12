"""Tests for audit log append and verification."""

import importlib
from pathlib import Path

import pytest


def test_audit_log_rejects_blank_explicit_secret(tmp_path):
    from agents.audit import AuditLog

    with pytest.raises(
        ValueError,
        match="audit_configuration_error: secret_key is required for audit integrity",
    ):
        AuditLog(secret_key="   ", log_path=tmp_path / "audit.jsonl")


def test_import_is_safe_but_global_audit_fails_closed_without_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("ATLASOPS_AUDIT_SECRET", raising=False)
    audit_path = tmp_path / "global-audit.jsonl"
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(audit_path))

    import agents.audit as audit

    audit = importlib.reload(audit)
    import agents.coordinator as coordinator

    assert coordinator is not None
    with pytest.raises(
        RuntimeError,
        match="audit_configuration_error: ATLASOPS_AUDIT_SECRET is required for audit integrity",
    ):
        audit.audit_log.record("inc-test", "coordinator", "incident_start")
    assert not audit_path.exists()


def test_configured_global_audit_records_without_exposing_secret(monkeypatch, tmp_path):
    test_secret = "test-placeholder-audit-secret"
    audit_path = tmp_path / "configured-audit.jsonl"
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", test_secret)
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(audit_path))

    import agents.audit as audit

    audit = importlib.reload(audit)
    audit.audit_log.record("inc-test", "coordinator", "incident_start")

    assert audit.audit_log.verify_integrity() == {"ok": True, "entries": 1}
    assert test_secret not in repr(audit.audit_log)
    assert test_secret not in audit_path.read_text(encoding="utf-8")


def test_audit_log_record_and_verify(tmp_path):
    from agents.audit import AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog(secret_key="test-placeholder-audit-secret", log_path=path)
    log.record("inc-1", "triage", "tool_call", tool_name="kubectl_get", tool_args={"resource": "pods"})
    log.record("inc-1", "triage", "tool_result", result_summary="ok")
    verify = log.verify_integrity()
    assert verify["ok"] is True
    assert verify["entries"] == 2


def test_audit_verify_fails_on_tamper(tmp_path):
    from agents.audit import AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog(secret_key="test-placeholder-audit-secret", log_path=path)
    log.record("inc-2", "coordinator", "incident_start", result_summary="test")
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("incident_start", "incident_starx"), encoding="utf-8")
    verify = log.verify_integrity()
    assert verify["ok"] is False


def test_audit_tail_limit_and_offset(tmp_path):
    from agents.audit import AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog(secret_key="test-placeholder-audit-secret", log_path=path)
    for idx in range(5):
        log.record(f"inc-{idx}", "coordinator", "incident_start", result_summary=f"r{idx}")
    entries = log.tail(limit=2)
    assert len(entries) == 2
    shifted = log.tail(limit=10, offset=3)
    assert len(shifted) == 2
