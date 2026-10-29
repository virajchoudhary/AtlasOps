import gc
import json
import os
import weakref
from pathlib import Path

import pytest

import demo.bug_lab.workspace as workspace_module
from demo.bug_lab.workspace import Workspace

CLASSIFICATION = "LOCAL_DEMONSTRATION_NOT_SCIENTIFIC"
PERSISTENCE_ERROR = "Bug Lab workspace persistence failed"


def _record(
    sequence,
    *,
    workspace_id=None,
    status="open",
    stage="monitor",
    closed_at=None,
):
    record = {
        "id": f"INC-{sequence:04d}",
        "sequence": sequence,
        "status": status,
        "stage": stage,
        "active_agent": "Monitor",
        "active_backend": "checks",
        "opened_at": 1.0,
        "closed_at": closed_at,
        "severity": "P3",
        "title": "Synthetic incident",
        "injected": [{"id": "synthetic", "name": "Synthetic incident"}],
        "alerts": [],
        "detect_s": 0.0,
        "resolve_s": None,
        "diagnosis": None,
        "patch": None,
        "approval": None,
        "attempts": [],
        "events": [],
        "resolved_by": None,
        "outcome": None,
        "summary": None,
        "diagnosis_matched": None,
        "version_at_open": "0" * 16,
        "backend": {"mode": "rules", "label": "rules"},
        "evidence_schema_version": 1,
        "evidence_classification": CLASSIFICATION,
        "verification": None,
    }
    if workspace_id is not None:
        record["workspace_id"] = workspace_id
    return record


def _terminal_record(workspace_id, sequence, status="resolved"):
    record = _record(
        sequence,
        workspace_id=workspace_id,
        status=status,
        stage="closed",
        closed_at=sequence + 10.0,
    )
    record["active_agent"] = None
    record["active_backend"] = None
    record["outcome"] = "Synthetic terminal outcome"
    return record


def _serialized(record):
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def test_fresh_workspace_preserves_constructor_reset_and_log_path_contract(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    try:
        assert workspace.root == root.resolve()
        assert workspace.site.is_dir()
        assert workspace.workspace_id
        assert workspace.log_path == root / "incidents.jsonl"
        assert not workspace.log_path.exists()
        assert workspace.healthy is True
        assert workspace.recovery().sequence == 0

        pristine = workspace.pristine("index.html")
        workspace.write("index.html", "synthetic change")
        assert workspace.read("index.html") == "synthetic change"
        workspace.reset()
        assert workspace.read("index.html") == pristine
        assert not workspace.log_path.exists()
    finally:
        workspace.close()

    assert workspace.healthy is False
    workspace.close()


def test_fresh_workspace_refuses_unowned_nonempty_root_without_writing(tmp_path):
    root = tmp_path / "lab"
    root.mkdir()
    sentinel = root / "owner-data.txt"
    sentinel.write_text("preserve this data", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        Workspace(root)

    assert [path.name for path in root.iterdir()] == ["owner-data.txt"]
    assert sentinel.read_text(encoding="utf-8") == "preserve this data"


def test_store_write_preserves_literal_newline_bytes(tmp_path):
    workspace = Workspace(tmp_path / "lab")
    path = workspace.site / "index.html"
    try:
        workspace.write("index.html", "first\nsecond\n")
        assert path.read_bytes() == b"first\nsecond\n"

        workspace.write("index.html", "first\r\nsecond\r\n")
        assert path.read_bytes() == b"first\r\nsecond\r\n"
        assert workspace.read("index.html") == "first\nsecond\n"
    finally:
        workspace.close()


def test_checkpoint_resume_finalizes_interruption_without_replaying_or_losing_evidence(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    original_bytes = (workspace.site / "index.html").read_bytes()
    record = _record(
        1,
        workspace_id=workspace.workspace_id,
        status="awaiting_approval",
        stage="approval",
    )
    record["approval"] = {
        "required": True,
        "decision": None,
        "digest": "a" * 64,
    }
    record["verification"] = {
        "verdict": "failed",
        "checked_at": 3.0,
        "checks": [{"name": "synthetic", "ok": False}],
    }
    record["resolved_by"] = "Synthetic agent"
    record["resolve_s"] = 2.0
    record["events"] = [{"stage": "approval", "text": "Waiting for approval"}]
    record["attempts"] = [{"verdict": "failed", "verification": record["verification"]}]

    workspace.checkpoint(record)
    assert json.loads((root / "pending.json").read_text(encoding="utf-8")) == record
    workspace.close()

    resumed = Workspace(root, resume=True)
    try:
        recovery = resumed.recovery()
        assert recovery.workspace_id == workspace.workspace_id
        assert recovery.sequence == 1
        assert len(recovery.records) == 1
        interrupted = recovery.records[0]
        assert interrupted["status"] == "interrupted"
        assert interrupted["stage"] == "closed"
        assert interrupted["closed_at"] is not None
        assert interrupted["verification"] is None
        assert interrupted["resolved_by"] is None
        assert interrupted["resolve_s"] is None
        assert interrupted["interrupted_state"] == {
            "status": "awaiting_approval",
            "stage": "approval",
            "approval": record["approval"],
            "verification": record["verification"],
            "resolved_by": "Synthetic agent",
            "resolve_s": 2.0,
            "summary": None,
            "outcome": None,
        }
        assert interrupted["summary"] == "Interrupted before completion; no workflow was replayed."
        assert interrupted["events"] == record["events"]
        assert interrupted["attempts"] == record["attempts"]
        assert not (root / "pending.json").exists()
        assert len(resumed.log_path.read_text(encoding="utf-8").splitlines()) == 1
        assert (resumed.site / "index.html").read_bytes() == original_bytes
    finally:
        resumed.close()

    reopened = Workspace(root, resume=True)
    try:
        assert reopened.recovery().sequence == 1
        assert len(reopened.recovery().records) == 1
        assert reopened.recovery().records[0]["status"] == "interrupted"
    finally:
        reopened.close()


def test_complete_is_idempotent_and_recovery_history_is_bounded_newest_first(tmp_path):
    workspace = Workspace(tmp_path / "lab")
    try:
        for sequence in range(1, 5):
            record = _terminal_record(workspace.workspace_id, sequence)
            workspace.complete(record)
            workspace.complete(record)

        recovery = workspace.recovery(history_limit=2)
        assert recovery.sequence == 4
        assert [record["id"] for record in recovery.records] == ["INC-0004", "INC-0003"]
        assert workspace.recovery(history_limit=0).records == ()
        assert len(workspace.log_path.read_text(encoding="utf-8").splitlines()) == 4
    finally:
        workspace.close()


def test_resume_requires_explicit_opt_in_and_releases_lifetime_writer_lock(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    workspace.checkpoint(_record(1, workspace_id=workspace.workspace_id))

    with pytest.raises(ValueError, match="already in use"):
        Workspace(root, resume=True)

    workspace.close()
    with pytest.raises(ValueError, match="new or empty"):
        Workspace(root)

    resumed = Workspace(root, resume=True)
    reference = weakref.ref(resumed)
    del resumed
    gc.collect()
    assert reference() is None

    final = Workspace(root, resume=True)
    final.close()


@pytest.mark.parametrize("writer_running", [False, True])
def test_resume_refuses_missing_writer_lock_without_recreating_it(tmp_path, writer_running):
    if writer_running and os.name == "nt":
        pytest.skip("Windows does not allow unlinking the held writer lock")
    root = tmp_path / "lab"
    workspace = Workspace(root)
    lock_path = root / ".bug-lab-workspace.lock"
    try:
        workspace.complete(_terminal_record(workspace.workspace_id, 1))
        if not writer_running:
            workspace.close()
        before = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and path != lock_path
        }
        lock_path.unlink()

        with pytest.raises(ValueError, match="missing"):
            resumed = Workspace(root, resume=True)
            resumed.close()

        assert not lock_path.exists()
        assert {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        } == before
    finally:
        workspace.close()


@pytest.mark.parametrize("corruption", ["marker", "journal"])
def test_resume_rejects_malformed_marker_or_journal_without_rewriting_it(tmp_path, corruption):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    if corruption == "marker":
        path = root / ".bug-lab-workspace.json"
        marker = json.loads(path.read_text(encoding="utf-8"))
        marker["schema_version"] = 99
        path.write_text(json.dumps(marker), encoding="utf-8")
    else:
        workspace.complete(_terminal_record(workspace.workspace_id, 1))
        path = workspace.log_path
        path.write_bytes(b'{"broken": true}\n')
    workspace.close()
    before = path.read_bytes()

    with pytest.raises(ValueError):
        Workspace(root, resume=True)

    assert path.read_bytes() == before


def test_resume_refuses_unknown_root_entries_without_deleting_them(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    workspace.close()
    extra = root / "owner-note.txt"
    extra.write_text("preserve this data", encoding="utf-8")

    with pytest.raises(ValueError, match="layout"):
        Workspace(root, resume=True)

    assert extra.read_text(encoding="utf-8") == "preserve this data"


def test_partial_final_append_is_repaired_only_from_matching_terminal_pending(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    workspace.complete(_terminal_record(workspace.workspace_id, 1))
    terminal = _terminal_record(workspace.workspace_id, 2)
    workspace.checkpoint(terminal)
    pending = json.loads((root / "pending.json").read_text(encoding="utf-8"))
    workspace.close()

    with (root / "incidents.jsonl").open("ab") as stream:
        serialized = _serialized(pending)
        stream.write(serialized[: len(serialized) // 2])

    resumed = Workspace(root, resume=True)
    try:
        recovery = resumed.recovery()
        assert recovery.sequence == 2
        assert [record["id"] for record in recovery.records] == ["INC-0002", "INC-0001"]
        assert len(resumed.log_path.read_text(encoding="utf-8").splitlines()) == 2
        assert not (root / "pending.json").exists()
    finally:
        resumed.close()


def test_partial_final_append_without_valid_proof_is_preserved_and_rejected(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    workspace.checkpoint(_terminal_record(workspace.workspace_id, 1))
    workspace.close()

    partial = b'{"id":"INC-0001"'
    workspace.log_path.write_bytes(partial)
    (root / "pending.json").unlink()

    with pytest.raises(ValueError):
        Workspace(root, resume=True)

    assert workspace.log_path.read_bytes() == partial


def test_partial_append_does_not_repair_an_invalid_complete_prefix(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    workspace.checkpoint(_terminal_record(workspace.workspace_id, 1))
    pending = json.loads((root / "pending.json").read_text(encoding="utf-8"))
    workspace.close()

    partial = _serialized(pending)[:10]
    raw = b"{not-json}\n" + partial
    workspace.log_path.write_bytes(raw)

    with pytest.raises(ValueError):
        Workspace(root, resume=True)

    assert workspace.log_path.read_bytes() == raw


def test_completed_append_with_pending_snapshot_is_deduplicated_on_resume(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    terminal = _terminal_record(workspace.workspace_id, 1)
    workspace.checkpoint(terminal)
    workspace.close()
    workspace.log_path.write_bytes(_serialized(terminal) + b"\n")

    resumed = Workspace(root, resume=True)
    try:
        assert resumed.recovery().sequence == 1
        assert len(resumed.recovery().records) == 1
        assert len(resumed.log_path.read_text(encoding="utf-8").splitlines()) == 1
        assert not (root / "pending.json").exists()
    finally:
        resumed.close()


def test_resume_rejects_record_missing_history_projection_shape(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    workspace.complete(_terminal_record(workspace.workspace_id, 1))
    workspace.close()
    malformed = json.loads(workspace.log_path.read_text(encoding="utf-8"))
    del malformed["injected"][0]["name"]
    workspace.log_path.write_bytes(_serialized(malformed) + b"\n")
    before = workspace.log_path.read_bytes()

    with pytest.raises(ValueError, match="history"):
        Workspace(root, resume=True)

    assert workspace.log_path.read_bytes() == before


def test_resume_refuses_hardlinked_owned_journal_without_touching_external_file(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    workspace.complete(_terminal_record(workspace.workspace_id, 1))
    workspace.close()
    external = tmp_path / "outside.jsonl"
    external.write_bytes(workspace.log_path.read_bytes())
    workspace.log_path.unlink()
    try:
        os.link(external, workspace.log_path)
    except OSError as exc:
        pytest.skip(f"hardlinks are unavailable on this filesystem: {exc}")
    before = external.read_bytes()

    with pytest.raises(ValueError):
        Workspace(root, resume=True)

    assert external.read_bytes() == before


def test_store_read_and_reset_refuse_hardlinked_files(tmp_path):
    workspace = Workspace(tmp_path / "lab")
    path = workspace.site / "index.html"
    external = tmp_path / "outside.html"
    external.write_bytes(path.read_bytes())
    path.unlink()
    try:
        os.link(external, path)
    except OSError as exc:
        pytest.skip(f"hardlinks are unavailable on this filesystem: {exc}")

    with pytest.raises(ValueError):
        workspace.read("index.html")
    with pytest.raises(ValueError):
        workspace.reset()

    assert external.read_bytes() == path.read_bytes()
    workspace.close()


def test_store_read_and_reset_refuse_symlinked_files(tmp_path):
    workspace = Workspace(tmp_path / "lab")
    path = workspace.site / "index.html"
    external = tmp_path / "outside.html"
    external.write_bytes(path.read_bytes())
    original = external.read_bytes()
    path.unlink()
    try:
        os.symlink(external, path)
    except OSError as exc:
        workspace.close()
        pytest.skip(f"symlinks are unavailable on this filesystem: {exc}")

    try:
        with pytest.raises(ValueError):
            workspace.read("index.html")
        with pytest.raises(ValueError):
            workspace.reset()
        assert external.read_bytes() == original
    finally:
        workspace.close()


def test_persistence_failure_disables_writes_but_keeps_store_reads(tmp_path, monkeypatch):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    pristine = workspace.read("index.html")
    real_replace = os.replace

    def fail_pending_replace(source, destination):
        if Path(destination) == root / "pending.json":
            raise OSError("synthetic credential-echo")
        return real_replace(source, destination)

    monkeypatch.setattr(workspace_module.os, "replace", fail_pending_replace)
    try:
        with pytest.raises(RuntimeError, match=PERSISTENCE_ERROR) as caught:
            workspace.checkpoint(_record(1, workspace_id=workspace.workspace_id))

        assert "synthetic credential-echo" not in str(caught.value)
        assert workspace.healthy is False
        assert workspace.read("index.html") == pristine
        with pytest.raises(RuntimeError, match=PERSISTENCE_ERROR):
            workspace.write("index.html", "must not be written")
        with pytest.raises(RuntimeError, match=PERSISTENCE_ERROR):
            workspace.reset()
        with pytest.raises(RuntimeError, match=PERSISTENCE_ERROR):
            workspace.complete(_terminal_record(workspace.workspace_id, 1))
        assert workspace.read("index.html") == pristine
    finally:
        workspace.close()


def test_partial_append_with_a_sequence_gap_is_rejected_without_rewriting(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    workspace.complete(_terminal_record(workspace.workspace_id, 1, status="cancelled"))
    skipped = _terminal_record(workspace.workspace_id, 3, status="cancelled")
    workspace.close()
    pending = _serialized(skipped) + b"\n"
    (root / "pending.json").write_bytes(pending)
    prefix = workspace.log_path.read_bytes()
    broken = prefix + _serialized(skipped)[:50]
    workspace.log_path.write_bytes(broken)

    with pytest.raises(ValueError, match="sequence"):
        Workspace(root, resume=True)
    assert workspace.log_path.read_bytes() == broken
    assert (root / "pending.json").read_bytes() == pending


def test_resume_refuses_unknown_store_files_without_adopting_or_deleting(tmp_path):
    root = tmp_path / "lab"
    workspace = Workspace(root)
    workspace.close()
    unexpected = root / "site" / "owner-note.txt"
    unexpected.write_text("not a Store asset", encoding="utf-8")

    with pytest.raises(ValueError, match="layout|unknown"):
        Workspace(root, resume=True)
    assert unexpected.read_text(encoding="utf-8") == "not a Store asset"


def test_atomic_store_replacement_flushes_its_containing_directory(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "lab")
    synchronized = []
    original_sync = workspace_module._sync_directory

    def record_sync(path):
        synchronized.append(path)
        original_sync(path)

    monkeypatch.setattr(workspace_module, "_sync_directory", record_sync)
    try:
        workspace.write("config.json", '{"api_delay_ms": 0}')
        assert synchronized == [workspace.site]
        assert (workspace.site / "config.json").read_bytes() == b'{"api_delay_ms": 0}'
        synchronized.clear()
        workspace.checkpoint(_record(1, workspace_id=workspace.workspace_id))
        assert synchronized == [workspace.root]
    finally:
        workspace.close()
