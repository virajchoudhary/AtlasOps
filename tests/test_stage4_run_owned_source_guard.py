from __future__ import annotations

import asyncio
import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

import scripts.run_stage4_golden_incident as runner


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    return result.stdout.strip()


@pytest.fixture
def guarded_checkout(monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    remote = tmp_path / "origin.git"
    checkout.mkdir()
    _git(tmp_path, "init", "--bare", str(remote))
    _git(checkout, "init", "--initial-branch=main")
    _git(checkout, "config", "user.name", "Stage 4 guard test")
    _git(checkout, "config", "user.email", "stage4-guard@example.invalid")
    (checkout / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(checkout, "add", "tracked.txt")
    _git(checkout, "commit", "-m", "guard fixture")
    _git(checkout, "remote", "add", "origin", str(remote))
    _git(checkout, "push", "--set-upstream", "origin", "main")
    _git(checkout, "fetch", "origin", "main")
    main_sha = _git(checkout, "rev-parse", "HEAD")

    monkeypatch.setattr(runner, "REPO_ROOT", str(checkout))
    monkeypatch.setattr(runner, "_RUN_OWNED_PREFLIGHT_BINDING", None)
    monkeypatch.setattr(runner, "EXPERIMENT_ID", "EXP-STAGE4-SF002-GUARD-TEST")
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", main_sha)
    yield checkout, main_sha
    monkeypatch.setattr(runner, "_RUN_OWNED_PREFLIGHT_BINDING", None)


def _persist_preflight(
    checkout: Path,
    main_sha: str,
    profile: dict | None = None,
) -> Path:
    profile = profile or runner.APPROVED_G4_PROTOCOL_PROFILE
    chaos = {"success": True, "stdout": '{"items":[]}', "returncode": 0}
    evidence = {
        "experiment_id": "EXP-STAGE4-SF002-GUARD-TEST",
        "scenario_id": runner.SCENARIO_ID,
        "protocol_marker": runner.G4_PLATFORM_HARDENING_MARKER,
        "protocol_profile": profile,
        "source_identity": {
            "git_commit": main_sha,
            "working_tree_clean": True,
            "protocol_fingerprint": runner.protocol_fingerprint(profile),
        },
        "phases": {
            "telemetry_readiness": {"ready": True},
            "baseline": {"baseline_healthy": True},
            "pre_reservation_chaos_check": {
                "verified_zero": True,
                "result": chaos,
            },
        },
    }
    path = runner._persist_stage4_preflight_evidence(evidence, chaos)
    return Path(path)


def test_initial_clean_source_then_exact_written_preflight_is_allowed(guarded_checkout):
    checkout, main_sha = guarded_checkout
    assert runner._current_main_sha() == main_sha

    preflight = _persist_preflight(checkout, main_sha)
    assert _git(checkout, "status", "--porcelain", "--untracked-files=all") == (
        "?? artifacts/evidence/stage4/EXP-STAGE4-SF002-GUARD-TEST.preflight.json"
    )
    assert preflight.is_file()
    assert runner._current_main_sha(
        expected_sha=main_sha,
        allow_run_owned_preflight=True,
    ) == main_sha
    assert runner._RUN_OWNED_PREFLIGHT_BINDING is None


@pytest.mark.parametrize(
    "tamper",
    [
        "tracked_drift",
        "staged_drift",
        "deleted_source",
        "arbitrary_untracked",
        "raw_bytes",
        "wrong_id",
        "wrong_scenario",
        "wrong_source",
        "wrong_profile",
        "wrong_path",
    ],
)
def test_guard_rejects_noncanonical_or_changed_preflight(guarded_checkout, tamper):
    checkout, main_sha = guarded_checkout
    preflight = _persist_preflight(checkout, main_sha)
    binding = runner._RUN_OWNED_PREFLIGHT_BINDING

    if tamper == "tracked_drift":
        (checkout / "tracked.txt").write_text("drift\n", encoding="utf-8")
    elif tamper == "staged_drift":
        (checkout / "tracked.txt").write_text("staged drift\n", encoding="utf-8")
        _git(checkout, "add", "tracked.txt")
    elif tamper == "deleted_source":
        (checkout / "tracked.txt").unlink()
    elif tamper == "arbitrary_untracked":
        (checkout / "unrelated.txt").write_text("not run-owned\n", encoding="utf-8")
    elif tamper == "raw_bytes":
        with preflight.open("ab") as stream:
            stream.write(b" ")
    elif tamper in {"wrong_id", "wrong_scenario", "wrong_source", "wrong_profile"}:
        assert binding is not None
        field, value = {
            "wrong_id": ("experiment_id", "EXP-STAGE4-SF002-OTHER"),
            "wrong_scenario": ("scenario_id", "single_fault/other"),
            "wrong_source": ("source_sha", "b" * 40),
            "wrong_profile": ("profile_sha256", "f" * 64),
        }[tamper]
        binding[field] = value
    else:
        assert binding is not None
        replacement = preflight.with_name("EXP-STAGE4-SF002-OTHER.preflight.json")
        preflight.rename(replacement)

    with pytest.raises(RuntimeError):
        runner._current_main_sha(
            expected_sha=main_sha,
            allow_run_owned_preflight=True,
        )
    assert runner._RUN_OWNED_PREFLIGHT_BINDING is None


def test_guard_rejects_symlink_or_reparse_preflight(guarded_checkout, tmp_path):
    checkout, main_sha = guarded_checkout
    preflight = _persist_preflight(checkout, main_sha)
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    preflight.unlink()
    try:
        preflight.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {type(exc).__name__}")

    with pytest.raises(RuntimeError, match="symlink or reparse"):
        runner._current_main_sha(
            expected_sha=main_sha,
            allow_run_owned_preflight=True,
        )


def test_writer_rejects_a_foreign_protocol_profile(guarded_checkout):
    checkout, main_sha = guarded_checkout
    foreign_profile = copy.deepcopy(runner.APPROVED_G4_PROTOCOL_PROFILE)
    foreign_profile["protocol_marker"] = "unapproved-profile"

    with pytest.raises(RuntimeError, match="source/profile identity"):
        _persist_preflight(checkout, main_sha, foreign_profile)
    assert not list(
        (checkout / "artifacts" / "evidence" / "stage4").glob("*.preflight.json")
    )


def test_guard_rejects_a_same_byte_file_replacement_during_read(
    guarded_checkout, monkeypatch,
):
    checkout, main_sha = guarded_checkout
    preflight = _persist_preflight(checkout, main_sha)
    original_info = preflight.stat()
    original_bytes = preflight.read_bytes()
    replacement = checkout.parent / "preflight-replacement.tmp"
    replacement.write_bytes(original_bytes)
    real_fdopen = os.fdopen
    replaced = False

    def replace_opened_preflight(fd, mode="r", *args, **kwargs):
        nonlocal replaced
        opened_info = os.fstat(fd)
        if (
            not replaced
            and (opened_info.st_dev, opened_info.st_ino)
            == (original_info.st_dev, original_info.st_ino)
        ):
            stream = real_fdopen(fd, mode, *args, **kwargs)
            try:
                os.replace(replacement, preflight)
            except PermissionError:
                stream.close()
                raise
            replaced = True
            return stream
        return real_fdopen(fd, mode, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", replace_opened_preflight)

    with pytest.raises(RuntimeError, match="file identity changed|cannot be read or parsed"):
        runner._current_main_sha(
            expected_sha=main_sha,
            allow_run_owned_preflight=True,
        )
    assert replaced is True or os.name == "nt"
    assert preflight.read_bytes() == original_bytes


def test_preexisting_attempt_preflight_is_not_a_run_owned_exception(guarded_checkout):
    checkout, main_sha = guarded_checkout
    old_preflight = (
        checkout / "artifacts" / "evidence" / "stage4"
        / "EXP-STAGE4-SF002-016.preflight.json"
    )
    old_preflight.parent.mkdir(parents=True)
    old_preflight.write_text('{"experiment_id":"EXP-STAGE4-SF002-016"}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="clean main checkout"):
        runner._current_main_sha(expected_sha=main_sha)


def test_pre_fault_exception_is_journaled_after_reservation_release(
    guarded_checkout, monkeypatch,
):
    checkout, main_sha = guarded_checkout
    experiment_id = "EXP-STAGE4-SF002-PREFAULT-GUARD-ABORT"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    monkeypatch.setattr(runner, "EXPERIMENT_ID", "")
    monkeypatch.setattr(runner, "_configure_stage4_runtime", lambda: None)
    monkeypatch.setattr(runner, "_start_port_forwards", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_stop_port_forwards", lambda _processes: None)
    monkeypatch.setattr(runner, "_read_json_file", lambda _path: None)
    monkeypatch.setattr(
        runner, "wait_for_telemetry_readiness", lambda: (True, {"stable_probes": 2})
    )
    monkeypatch.setattr(
        runner,
        "wait_for_baseline_readiness",
        lambda: (True, {"stable_probes": 2}, True, {"stdout": "healthy"}),
    )
    monkeypatch.setattr(runner, "collect_sf002_cpu_telemetry", lambda: {"samples": 2})
    monkeypatch.setattr(
        runner,
        "run_kubectl",
        lambda _args: {"success": True, "stdout": '{"items":[]}', "returncode": 0},
    )
    calls = []

    def source_guard(expected_sha=None, **_kwargs):
        calls.append(expected_sha)
        if len(calls) == 3:
            raise RuntimeError("synthetic pre-apply guard failure")
        return main_sha

    monkeypatch.setattr(runner, "_current_main_sha", source_guard)
    reservation = {
        "experiment_id": experiment_id,
        "state": runner.ATTEMPT_STATE_RESERVED,
        "reservation_token": "synthetic-reservation",
        "protocol_profile": runner.APPROVED_G4_PROTOCOL_PROFILE,
        "protocol_fingerprint": runner.protocol_fingerprint(
            runner.APPROVED_G4_PROTOCOL_PROFILE
        ),
        "main_sha": main_sha,
    }
    monkeypatch.setattr(
        runner, "reserve_experiment_attempt", lambda *_args, **_kwargs: reservation
    )
    monkeypatch.setattr(runner, "release_experiment_reservation", lambda _r: True)
    monkeypatch.setattr(
        runner,
        "_persist_stage4_preflight_evidence",
        lambda *_args, **_kwargs: "synthetic-preflight.json",
    )
    journal = {}

    def persist_failure(evidence):
        journal.update(copy.deepcopy(evidence))
        return "synthetic-prefault.json"

    monkeypatch.setattr(runner, "_persist_stage4_prefault_failure", persist_failure)

    with pytest.raises(RuntimeError, match="synthetic pre-apply guard failure"):
        asyncio.run(runner._run_experiment())

    assert journal["attempt_state"] == "RELEASED_PRE_FAULT"
    assert journal["reservation_released"] is True
    assert journal["outcome"] == "INVALID"
    assert journal["failure_phase"] == "pre_fault_exception"
    assert journal["t0_crossed"] is False
    assert journal["observed_exception_message"] == "synthetic pre-apply guard failure"
