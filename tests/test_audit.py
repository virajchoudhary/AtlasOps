"""Tests for audit log append and verification."""

import importlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BrokenBarrierError, Event, get_ident

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

    from agents import audit

    audit = importlib.reload(audit)
    from agents import coordinator

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

    from agents import audit

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


def test_require_audit_log_initializes_once_when_called_concurrently(
    monkeypatch, tmp_path
):
    from agents import audit

    audit = importlib.reload(audit)
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-placeholder-audit-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "concurrent-global.jsonl"))

    worker_count = 6
    start = Barrier(worker_count)
    constructors = Barrier(worker_count, timeout=1)
    original_audit_log = audit.AuditLog
    created = []

    def concurrent_constructor(*args, **kwargs):
        instance = original_audit_log(*args, **kwargs)
        created.append(instance)
        try:
            constructors.wait()
        except BrokenBarrierError:
            pass
        return instance

    monkeypatch.setattr(audit, "AuditLog", concurrent_constructor)

    def get_audit_log():
        start.wait(timeout=5)
        return audit.require_audit_log()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(get_audit_log) for _ in range(worker_count)]
        results = [future.result(timeout=10) for future in futures]

    assert len(created) == 1
    assert all(instance is results[0] for instance in results)


def test_concurrent_audit_records_preserve_hash_chain(tmp_path, monkeypatch):
    from agents.audit import AuditLog

    path = tmp_path / "concurrent-audit.jsonl"
    log = AuditLog(secret_key="test-placeholder-audit-secret", log_path=path)
    worker_count = 8
    start = Barrier(worker_count)
    appends = Barrier(worker_count, timeout=1)
    original_open = Path.open

    def coordinated_open(self, mode="r", *args, **kwargs):
        if self == path and mode == "a":
            try:
                appends.wait()
            except BrokenBarrierError:
                pass
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", coordinated_open)

    def record(index):
        start.wait(timeout=5)
        return log.record(
            f"inc-{index}",
            "coordinator",
            "incident_start",
            result_summary=f"concurrent-{index}",
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(record, index) for index in range(worker_count)]
        entries = [future.result(timeout=10) for future in futures]

    assert len(entries) == worker_count
    assert log.verify_integrity() == {"ok": True, "entries": worker_count}


@pytest.mark.parametrize("reader_name", ["tail", "verify_integrity"])
def test_audit_readers_wait_for_an_in_progress_append(
    reader_name, tmp_path, monkeypatch
):
    from agents.audit import AuditLog

    path = tmp_path / f"{reader_name}-during-append.jsonl"
    log = AuditLog(secret_key="test-placeholder-audit-secret", log_path=path)
    log.record("inc-seed", "coordinator", "incident_start")

    partial_write = Event()
    finish_write = Event()
    reader_started = Event()
    read_open_attempted = Event()
    original_open = Path.open

    class GatedAppend:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            self.file.__enter__()
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def write(self, value):
            split = max(1, len(value) // 2)
            written = self.file.write(value[:split])
            self.file.flush()
            partial_write.set()
            if not finish_write.wait(timeout=5):
                raise TimeoutError("test did not release the audit append")
            return written + self.file.write(value[split:])

    def coordinated_open(self, mode="r", *args, **kwargs):
        if self == path and mode == "a":
            return GatedAppend(original_open(self, mode, *args, **kwargs))
        if self == path and mode == "r":
            read_open_attempted.set()
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", coordinated_open)

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(
            log.record, "inc-writer", "coordinator", "incident_start"
        )
        try:
            assert partial_write.wait(timeout=5)

            def read_audit():
                reader_started.set()
                return getattr(log, reader_name)()

            reader = executor.submit(read_audit)
            assert reader_started.wait(timeout=5)
            read_before_append_completed = read_open_attempted.wait(timeout=0.5)
        finally:
            finish_write.set()

        writer.result(timeout=5)
        result = reader.result(timeout=5)

    assert not read_before_append_completed
    if reader_name == "tail":
        assert [entry["incident_id"] for entry in result] == ["inc-seed", "inc-writer"]
    else:
        assert result == {"ok": True, "entries": 2}


def test_audit_verification_hashing_does_not_block_concurrent_append(
    tmp_path, monkeypatch
):
    from agents.audit import AuditLog

    log = AuditLog(
        secret_key="test-placeholder-audit-secret",
        log_path=tmp_path / "verify-snapshot.jsonl",
    )
    log.record("inc-seed", "coordinator", "incident_start")

    hash_step = Event()
    finish_verification = Event()
    record_finished = Event()
    verifier_thread_id = None
    original_sha256_text = log._sha256_text

    def pause_verification_hash(value):
        if get_ident() == verifier_thread_id:
            hash_step.set()
            if not finish_verification.wait(timeout=5):
                raise TimeoutError("test did not release audit verification")
        return original_sha256_text(value)

    monkeypatch.setattr(log, "_sha256_text", pause_verification_hash)

    def verify():
        nonlocal verifier_thread_id
        verifier_thread_id = get_ident()
        return log.verify_integrity()

    def record():
        try:
            return log.record("inc-writer", "coordinator", "incident_start")
        finally:
            record_finished.set()

    record_future = None
    record_finished_during_verification = False
    with ThreadPoolExecutor(max_workers=2) as executor:
        verification = executor.submit(verify)
        try:
            assert hash_step.wait(timeout=5)
            record_future = executor.submit(record)
            record_finished_during_verification = record_finished.wait(timeout=0.5)
        finally:
            finish_verification.set()

        verification_result = verification.result(timeout=5)
        if record_future is not None:
            record_future.result(timeout=5)

    assert record_finished_during_verification
    assert verification_result == {"ok": True, "entries": 1}
    assert log.verify_integrity() == {"ok": True, "entries": 2}
