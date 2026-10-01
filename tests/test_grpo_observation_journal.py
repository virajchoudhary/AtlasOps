"""Contracts for the bounded non-empirical GRPO observation journal."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from training import grpo_observation_journal as journal_module
from training.grpo_observation_journal import (
    ObservationJournal,
    read_observation_journal,
)


SCENARIO_ID = "single_fault/sf-002"
GROUP_ID = "a" * 32


def _complete_group(journal: ObservationJournal) -> None:
    journal.append("group_started", GROUP_ID, {"scenario_id": SCENARIO_ID})
    journal.append(
        "group_observed",
        GROUP_ID,
        {
            "scenario_id": SCENARIO_ID,
            "observation_digest": "b" * 64,
            "state_prompt_sha256": "c" * 64,
            "observed_at": "2026-10-01T00:00:00Z",
        },
    )
    journal.append(
        "sample_started",
        GROUP_ID,
        {
            "scenario_id": SCENARIO_ID,
            "sample_index": 0,
            "completion_sha256": "d" * 64,
        },
    )
    journal.append(
        "sample_execute_started",
        GROUP_ID,
        {
            "scenario_id": SCENARIO_ID,
            "sample_index": 0,
            "completion_sha256": "d" * 64,
        },
    )
    journal.append(
        "group_finished",
        GROUP_ID,
        {
            "scenario_id": SCENARIO_ID,
            "status": "failed",
            "error_type": "ValueError",
            "finish_callback_status": "returned",
            "finish_error_type": None,
            "records": [
                {
                    "sample_index": 0,
                    "result_status": "blocked",
                    "verifier_status": None,
                    "failure": "environment_blocked:approval_required",
                    "reward": None,
                    "scorable": False,
                    "approval_decision": "rejected",
                    "terminal_block": {"category": "approval_required"},
                }
            ],
        },
    )


def test_complete_negative_group_is_fsynced_redacted_and_not_resumable(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    fsync_calls = []
    real_fsync = os.fsync

    def record_fsync(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", record_fsync)
    with ObservationJournal(path) as journal:
        _complete_group(journal)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2, 3, 4, 5]
    assert all(row["result_classification"] == "NON_EMPIRICAL" for row in rows)
    assert all(row["certification_status"] == "NOT_CERTIFIED" for row in rows)
    assert rows[-1]["data"]["records"][0]["approval_decision"] == "rejected"
    assert "action" not in rows[-1]["data"]["records"][0]
    report = read_observation_journal(path)
    assert report["status"] == "RECORDED"
    assert report["resume_allowed"] is False
    assert report["result_classification"] == "NON_EMPIRICAL"
    assert len(fsync_calls) == len(rows)


def test_writer_is_exclusive_and_failed_append_poisons_it(tmp_path):
    path = tmp_path / "journal.jsonl"
    with ObservationJournal(path) as journal:
        with pytest.raises(ValueError):
            journal.append(
                "group_started",
                GROUP_ID,
                {"scenario_id": SCENARIO_ID, "prompt": "must not persist"},
            )
        assert path.read_bytes() == b""
        with pytest.raises(RuntimeError):
            journal.append("group_started", GROUP_ID, {"scenario_id": SCENARIO_ID})

    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        with ObservationJournal(path):
            pass
    assert path.read_bytes() == before


def test_fsync_failure_poisoned_writer_leaves_only_nonresumable_evidence(
    tmp_path, monkeypatch
):
    path = tmp_path / "journal.jsonl"

    def fail_fsync(_fd):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(journal_module.os, "fsync", fail_fsync)
    with ObservationJournal(path) as journal:
        with pytest.raises(OSError, match="fsync failure"):
            journal.append("group_started", GROUP_ID, {"scenario_id": SCENARIO_ID})
        with pytest.raises(RuntimeError, match="poisoned"):
            journal.append("group_started", GROUP_ID, {"scenario_id": SCENARIO_ID})

    report = read_observation_journal(path)
    assert report["status"] == "INCOMPLETE"
    assert report["resume_allowed"] is False


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        (
            b'{"schema":"atlasops.grpo_observation_journal",'
            b'"schema":"atlasops.grpo_observation_journal"}\n',
            "INVALID",
        ),
        (
            b'{"schema":"atlasops.grpo_observation_journal",'
            b'"schema_version":true,"sequence":1}\n',
            "INVALID",
        ),
        (b'{"schema":"atlasops.grpo_observation_journal",\n', "INVALID"),
        (b'{"schema":"atlasops.grpo_observation_journal"', "INCOMPLETE"),
    ],
)
def test_reader_rejects_malformed_rows_and_never_repairs_file(
    tmp_path, payload, expected_status
):
    path = tmp_path / "journal.jsonl"
    path.write_bytes(payload)
    before = path.read_bytes()

    report = read_observation_journal(path)

    assert report["status"] == expected_status
    assert report["resume_allowed"] is False
    assert path.read_bytes() == before


def test_writer_rejects_hard_link_redirect_without_touching_target(tmp_path):
    target = tmp_path / "target.jsonl"
    linked = tmp_path / "linked.jsonl"
    target.write_bytes(b"preserve")
    try:
        os.link(target, linked)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {type(exc).__name__}")

    with pytest.raises(ValueError, match="redirect"):
        with ObservationJournal(linked):
            pass

    assert target.read_bytes() == b"preserve"
    assert linked.read_bytes() == b"preserve"


def test_reader_rejects_sequence_gap(tmp_path):
    path = tmp_path / "journal.jsonl"
    row = {
        "schema": journal_module.SCHEMA,
        "schema_version": journal_module.SCHEMA_VERSION,
        "sequence": 2,
        "group_id": GROUP_ID,
        "event": "group_started",
        "result_classification": "NON_EMPIRICAL",
        "certification_status": "NOT_CERTIFIED",
        "data": {"scenario_id": SCENARIO_ID},
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert read_observation_journal(path)["status"] == "INVALID"


@pytest.mark.parametrize(
    ("finish_callback_status", "finish_error_type", "scorable", "reward"),
    [
        ("raised", "RuntimeError", True, 0.5),
        ("returned", None, False, None),
    ],
)
def test_completed_summary_rejects_failed_finish_or_unscorable_record(
    tmp_path, finish_callback_status, finish_error_type, scorable, reward
):
    path = tmp_path / "journal.jsonl"
    with ObservationJournal(path) as journal:
        journal.append("group_started", GROUP_ID, {"scenario_id": SCENARIO_ID})
        journal.append(
            "group_observed",
            GROUP_ID,
            {
                "scenario_id": SCENARIO_ID,
                "observation_digest": "b" * 64,
                "state_prompt_sha256": "c" * 64,
                "observed_at": "2026-10-01T00:00:00Z",
            },
        )
        sample = {
            "scenario_id": SCENARIO_ID,
            "sample_index": 0,
            "completion_sha256": "d" * 64,
        }
        journal.append("sample_started", GROUP_ID, sample)
        journal.append("sample_execute_started", GROUP_ID, sample)
        with pytest.raises(ValueError, match="completed"):
            journal.append(
                "group_finished",
                GROUP_ID,
                {
                    "scenario_id": SCENARIO_ID,
                    "status": "completed",
                    "error_type": None,
                    "finish_callback_status": finish_callback_status,
                    "finish_error_type": finish_error_type,
                    "records": [
                        {
                            "sample_index": 0,
                            "result_status": "ok",
                            "verifier_status": "passed",
                            "failure": None,
                            "reward": reward,
                            "scorable": scorable,
                            "approval_decision": None,
                            "terminal_block": None,
                        }
                    ],
                },
            )

    assert read_observation_journal(path)["status"] == "INCOMPLETE"


def test_replaced_path_is_never_used_as_the_open_writer_target(tmp_path):
    path = tmp_path / "journal.jsonl"
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(b"replacement sentinel")

    with ObservationJournal(path) as journal:
        journal.append("group_started", GROUP_ID, {"scenario_id": SCENARIO_ID})
        try:
            os.replace(replacement, path)
        except OSError as exc:
            pytest.skip(f"open file replacement unavailable: {type(exc).__name__}")
        with pytest.raises(OSError, match="changed"):
            journal.append(
                "group_observed",
                GROUP_ID,
                {
                    "scenario_id": SCENARIO_ID,
                    "observation_digest": "b" * 64,
                    "state_prompt_sha256": "c" * 64,
                    "observed_at": "2026-10-01T00:00:00Z",
                },
            )

    assert path.read_bytes() == b"replacement sentinel"


def test_concurrent_append_fails_closed_without_interleaving_rows(
    tmp_path, monkeypatch
):
    path = tmp_path / "journal.jsonl"
    write_started = threading.Event()
    release_write = threading.Event()
    real_write = os.write
    failures = []

    def blocking_write(fd, data):
        write_started.set()
        assert release_write.wait(timeout=5)
        return real_write(fd, data)

    monkeypatch.setattr(journal_module.os, "write", blocking_write)
    with ObservationJournal(path) as journal:
        def first_append():
            try:
                journal.append("group_started", GROUP_ID, {"scenario_id": SCENARIO_ID})
            except BaseException as exc:
                failures.append(exc)

        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(first_append)
            assert write_started.wait(timeout=5)
            with pytest.raises(RuntimeError, match="Concurrent"):
                journal.append(
                    "group_started",
                    "e" * 32,
                    {"scenario_id": SCENARIO_ID},
                )
            release_write.set()
            first.result(timeout=5)
        with pytest.raises(RuntimeError, match="poisoned"):
            journal.append(
                "group_observed",
                GROUP_ID,
                {
                    "scenario_id": SCENARIO_ID,
                    "observation_digest": "b" * 64,
                    "state_prompt_sha256": "c" * 64,
                    "observed_at": "2026-10-01T00:00:00Z",
                },
            )

    assert failures == []
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["sequence"] == 1
