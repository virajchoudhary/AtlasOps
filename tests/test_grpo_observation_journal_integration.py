"""Non-live trainer journaling and process interruption evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_grpo_observation_first import _Lifecycle, _Trainer, _inputs
from training.grpo_observation_journal import ObservationJournal, read_observation_journal


def _trainer(lifecycle, journal):
    return _Trainer(
        model=SimpleNamespace(training=True),
        args=SimpleNamespace(max_prompt_length=None, num_generations=2),
        train_dataset=[],
        observation_lifecycle=lifecycle,
        observation_journal=journal,
    )


def test_trainer_journals_before_callbacks_and_retains_final_negative_result(tmp_path):
    path = tmp_path / "journal.jsonl"
    lifecycle = _Lifecycle()
    original_begin = lifecycle.begin
    original_execute = lifecycle.execute
    seen = []

    def begin(scenario):
        seen.append(json.loads(path.read_text().splitlines()[-1])["event"])
        return original_begin(scenario)

    def execute(completion, state, index):
        seen.append(json.loads(path.read_text().splitlines()[-1])["event"])
        return original_execute(completion, state, index)

    lifecycle.begin, lifecycle.execute = begin, execute
    with ObservationJournal(path) as journal:
        trainer = _trainer(lifecycle, journal)
        trainer._generate_and_score_completions(_inputs())

    assert seen == ["group_started", "sample_execute_started", "sample_execute_started"]
    report = read_observation_journal(path)
    assert report["status"] == "RECORDED"
    assert report["resume_allowed"] is False
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event"] for row in events] == [
        "group_started", "group_observed", "sample_started", "sample_execute_started",
        "sample_started", "sample_execute_started", "group_finished",
    ]
    assert events[-1]["data"]["records"][0]["verifier_status"] == "failed"
    assert events[-1]["result_classification"] == "NON_EMPIRICAL"
    serialized = path.read_text()
    assert "ObservedCpu" not in serialized
    assert "action" not in events[-1]["data"]["records"][0]
    assert trainer.generated_inputs[0]["g9_group_token"] not in serialized


def test_closed_journal_refuses_before_lifecycle_setup(tmp_path):
    with ObservationJournal(tmp_path / "journal.jsonl") as journal:
        pass
    lifecycle = _Lifecycle()
    trainer = _trainer(lifecycle, journal)
    with pytest.raises((RuntimeError, ValueError)):
        trainer._generate_and_score_completions(_inputs())
    assert lifecycle.events == []
    assert trainer.observation_evidence == ()


def test_failed_pre_action_retains_finished_failed_group(tmp_path):
    path = tmp_path / "journal.jsonl"
    lifecycle = _Lifecycle()
    lifecycle.before_error = RuntimeError("private callback detail")
    with ObservationJournal(path) as journal:
        trainer = _trainer(lifecycle, journal)
        with pytest.raises(RuntimeError, match="private callback"):
            trainer._generate_and_score_completions(_inputs())
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    final = rows[-1]["data"]
    assert final["status"] == "failed"
    assert final["records"][0]["failure"] == "before_action_exception:RuntimeError"
    assert final["records"][0]["reward"] is None
    assert "private callback detail" not in path.read_text()
    assert read_observation_journal(path)["resume_allowed"] is False


@pytest.mark.parametrize("phase", ["begin", "execute"])
def test_terminated_trainer_is_incomplete_and_cannot_reopen_for_execution(tmp_path, phase):
    path = tmp_path / "journal.jsonl"
    root = Path(__file__).resolve().parents[1]
    code = """
import os, sys
from types import SimpleNamespace
from tests.test_grpo_observation_first import _Lifecycle, _Trainer, _inputs
from training.grpo_observation_journal import ObservationJournal
lifecycle = _Lifecycle()
def terminate(*args):
    os._exit(23)
setattr(lifecycle, sys.argv[2], terminate)
with ObservationJournal(sys.argv[1]) as journal:
    trainer = _Trainer(model=SimpleNamespace(training=True),
        args=SimpleNamespace(max_prompt_length=None, num_generations=2),
        train_dataset=[], observation_lifecycle=lifecycle, observation_journal=journal)
    trainer._generate_and_score_completions(_inputs())
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(path), phase], cwd=root
    )
    assert result.returncode == 23
    before = path.read_bytes()
    report = read_observation_journal(path)
    assert report["status"] == "INCOMPLETE"
    assert report["resume_allowed"] is False
    events = [json.loads(line)["event"] for line in before.decode().splitlines()]
    assert events[-1] == (
        "group_started" if phase == "begin" else "sample_execute_started"
    )
    with pytest.raises(FileExistsError):
        with ObservationJournal(path):
            pass
    assert path.read_bytes() == before


def test_failed_execution_marker_blocks_action_and_retains_failure(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    lifecycle = _Lifecycle()
    original_append = ObservationJournal.append

    def fail_execution_marker(self, event, group_id, data):
        if event == "sample_execute_started":
            raise OSError("synthetic disk failure")
        return original_append(self, event, group_id, data)

    monkeypatch.setattr(ObservationJournal, "append", fail_execution_marker)
    with ObservationJournal(path) as journal:
        trainer = _trainer(lifecycle, journal)
        with pytest.raises(OSError, match="disk failure"):
            trainer._generate_and_score_completions(_inputs())
    assert not any(event[0] == "execute" for event in lifecycle.events)
    assert lifecycle.finished[0][1] == "failed"
    assert trainer.observation_evidence[0]["error_type"] == "OSError"
    assert read_observation_journal(path)["resume_allowed"] is False


def test_failed_final_marker_cannot_return_completed_result(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    original_append = ObservationJournal.append

    def fail_final_marker(self, event, group_id, data):
        if event == "group_finished":
            raise OSError("synthetic final disk failure")
        return original_append(self, event, group_id, data)

    monkeypatch.setattr(ObservationJournal, "append", fail_final_marker)
    with ObservationJournal(path) as journal:
        trainer = _trainer(_Lifecycle(), journal)
        with pytest.raises(OSError, match="final disk failure"):
            trainer._generate_and_score_completions(_inputs())
    assert trainer.observation_evidence[0]["status"] == "failed"
    assert trainer.observation_evidence[0]["journal_error_type"] == "OSError"
    assert read_observation_journal(path)["status"] == "INCOMPLETE"


def test_finish_failure_is_recorded_without_discarding_prior_samples(tmp_path):
    path = tmp_path / "journal.jsonl"
    lifecycle = _Lifecycle()
    lifecycle.finish_error = RuntimeError("private cleanup exception")
    with ObservationJournal(path) as journal:
        trainer = _trainer(lifecycle, journal)
        with pytest.raises(RuntimeError, match="private cleanup"):
            trainer._generate_and_score_completions(_inputs())
    final = json.loads(path.read_text().splitlines()[-1])["data"]
    assert final["status"] == "failed"
    assert final["finish_callback_status"] == "raised"
    assert final["finish_error_type"] == "RuntimeError"
    assert len(final["records"]) == 2
    assert "private cleanup exception" not in path.read_text()
    assert read_observation_journal(path)["status"] == "RECORDED"
    assert read_observation_journal(path)["resume_allowed"] is False


@pytest.mark.parametrize("mutation", ["truncated", "sequence", "schema-bool", "secret-field"])
def test_reader_preserves_invalid_or_truncated_bytes_without_repair(tmp_path, mutation):
    path = tmp_path / "journal.jsonl"
    with ObservationJournal(path) as journal:
        trainer = _trainer(_Lifecycle(), journal)
        trainer._generate_and_score_completions(_inputs())
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if mutation == "truncated":
        content = path.read_bytes()[:-5]
    else:
        if mutation == "sequence":
            rows[1]["sequence"] = 99
        elif mutation == "schema-bool":
            rows[0]["schema_version"] = True
        else:
            rows[-1]["data"]["operator_token"] = "not-a-real-secret"
        content = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
    path.write_bytes(content)
    report = read_observation_journal(path)
    assert report["status"] == ("INCOMPLETE" if mutation == "truncated" else "INVALID")
    assert report["resume_allowed"] is False
    assert path.read_bytes() == content
    assert "not-a-real-secret" not in json.dumps(report)


def test_deeply_nested_journal_returns_invalid_without_raising(tmp_path):
    path = tmp_path / "journal.jsonl"
    content = b"[" * 10_000 + b"0" + b"]" * 10_000 + b"\n"
    path.write_bytes(content)
    assert read_observation_journal(path)["status"] == "INVALID"
    assert path.read_bytes() == content


@pytest.mark.parametrize("mutation", ["error_type", "inconclusive", "blocked"])
def test_reader_refuses_contradictory_completed_records(tmp_path, mutation):
    path = tmp_path / "journal.jsonl"
    with ObservationJournal(path) as journal:
        _trainer(_Lifecycle(), journal)._generate_and_score_completions(_inputs())
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    final = rows[-1]["data"]
    if mutation == "error_type":
        final["error_type"] = "RuntimeError"
    elif mutation == "inconclusive":
        final["records"][0]["verifier_status"] = "inconclusive"
    else:
        final["records"][0]["result_status"] = "blocked"
    content = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
    path.write_bytes(content)
    assert read_observation_journal(path)["status"] == "INVALID"
    assert path.read_bytes() == content
