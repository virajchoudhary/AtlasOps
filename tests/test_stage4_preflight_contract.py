"""Read-only preflight contracts for the prospective Stage 4 v3.4 runner."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

import scripts.run_stage4_golden_incident as runner

MAIN_SHA = "a" * 40
OTHER_SHA = "b" * 40
SECRET_FILES = {
    "ARGOCD_PASS": "argocd-pass.secret",
    "ATLASOPS_AUDIT_SECRET": "atlasops-audit-secret.secret",
    "ATLASOPS_API_KEY": "atlasops-api-key.secret",
    "ALERTMANAGER_WEBHOOK_SECRET": "alertmanager-webhook-secret.secret",
}


def test_import_does_not_override_host_runtime_or_create_output(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repo_root)
    settings = {
        "BACKEND": "synthetic-test-backend",
        "VLLM_BASE": "http://127.0.0.1:59099/v1",
        "LLM_API_KEY": "synthetic-test-key-not-printed",
        "KUBECONFIG_CONTEXT": "synthetic-current-context",
        "TRAJECTORIES_DIR": str(tmp_path / "trajectories"),
        "POSTMORTEM_DIR": str(tmp_path / "postmortems"),
    }
    environment.update(settings)
    script = (
        "import os,json; keys="
        + repr(list(settings))
        + "; before={k:os.environ[k] for k in keys};"
        + "import scripts.run_stage4_golden_incident;"
        + "print(json.dumps({k:os.environ.get(k)==before[k] for k in keys}))"
    )
    process = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 0
    assert all(json.loads(process.stdout).values())
    assert not (tmp_path / "trajectories").exists()
    assert not (tmp_path / "postmortems").exists()


@pytest.fixture(autouse=True)
def isolated_runner(monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.setattr(runner, "REPO_ROOT", str(checkout))
    monkeypatch.setattr(runner, "EXPERIMENT_ID", "")
    monkeypatch.delenv("STAGE4_EXPERIMENT_ID", raising=False)
    monkeypatch.delenv("STAGE4_APPROVED_MAIN_SHA", raising=False)
    yield checkout


def _install_git_mock(
    monkeypatch,
    *,
    status: str = "",
    branch: str | None = "main",
    head: str = MAIN_SHA,
    origin_main: str = MAIN_SHA,
    approved_sha: str | None = MAIN_SHA,
    remote_main: str | None = MAIN_SHA,
    remote_returncode: int = 0,
    remote_stderr: str = "",
) -> list[list[str]]:
    calls: list[list[str]] = []
    if approved_sha is None:
        monkeypatch.delenv("STAGE4_APPROVED_MAIN_SHA", raising=False)
    else:
        monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", approved_sha)

    def run(args, **kwargs):
        calls.append(list(args))
        assert kwargs["cwd"] == runner.REPO_ROOT
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        assert kwargs["timeout"] > 0
        if args == ["git", "status", "--porcelain", "--untracked-files=all"]:
            return CompletedProcess(args, 0, status, "")
        if args == ["git", "symbolic-ref", "--quiet", "--short", "HEAD"]:
            return CompletedProcess(args, 0 if branch else 128, branch or "", "")
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return CompletedProcess(args, 0, head, "")
        if args == ["git", "rev-parse", "--verify", "origin/main^{commit}"]:
            return CompletedProcess(args, 0 if origin_main else 128, origin_main, "")
        if args == ["git", "ls-remote", "--exit-code", "origin", "refs/heads/main"]:
            assert kwargs["timeout"] <= 15
            assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
            output = (
                f"{remote_main}\trefs/heads/main\n"
                if remote_main is not None
                else ""
            )
            return CompletedProcess(args, remote_returncode, output, remote_stderr)
        pytest.fail(f"unexpected Git command: {args!r}")

    monkeypatch.setattr(runner.subprocess, "run", run)
    return calls


def test_current_main_sha_requires_clean_main_at_origin_main(isolated_runner, monkeypatch):
    calls = _install_git_mock(monkeypatch)

    assert runner._current_main_sha() == MAIN_SHA
    assert calls == [
        ["git", "status", "--porcelain", "--untracked-files=all"],
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        ["git", "rev-parse", "--verify", "HEAD"],
        ["git", "rev-parse", "--verify", "origin/main^{commit}"],
        ["git", "ls-remote", "--exit-code", "origin", "refs/heads/main"],
    ]


@pytest.mark.parametrize("approved_sha", [None, "", "abc123", "g" * 40])
def test_current_main_sha_requires_explicit_full_approved_sha(
    isolated_runner, monkeypatch, approved_sha,
):
    calls = _install_git_mock(monkeypatch, approved_sha=approved_sha)

    with pytest.raises(RuntimeError, match="STAGE4_APPROVED_MAIN_SHA"):
        runner._current_main_sha()

    assert calls == []


@pytest.mark.parametrize(
    ("git_state", "message"),
    [
        ({"status": " M agents/approval.py\n"}, "clean"),
        ({"branch": "fix/g4-preflight-contract"}, "main"),
        ({"branch": None}, "main"),
        ({"head": OTHER_SHA}, "origin/main"),
        ({"origin_main": OTHER_SHA}, "STAGE4_APPROVED_MAIN_SHA"),
        ({"origin_main": ""}, "origin/main"),
        ({"remote_main": OTHER_SHA}, "remote origin/main"),
    ],
)
def test_current_main_sha_rejects_dirty_detached_feature_or_stale_checkout(
    isolated_runner, monkeypatch, git_state, message,
):
    _install_git_mock(monkeypatch, **git_state)

    with pytest.raises(RuntimeError, match=message):
        runner._current_main_sha()


def test_current_main_sha_rejects_approval_change_from_startup(
    isolated_runner, monkeypatch,
):
    calls = _install_git_mock(
        monkeypatch,
        approved_sha=OTHER_SHA,
        head=OTHER_SHA,
        origin_main=OTHER_SHA,
        remote_main=OTHER_SHA,
    )

    with pytest.raises(RuntimeError, match="changed after Stage 4 source preflight"):
        runner._current_main_sha(expected_sha=MAIN_SHA)

    assert calls == []


def test_current_main_sha_fails_closed_when_remote_is_unreachable(
    isolated_runner, monkeypatch,
):
    calls = _install_git_mock(
        monkeypatch,
        remote_main=None,
        remote_returncode=128,
        remote_stderr="credential-bearing remote diagnostic",
    )

    with pytest.raises(RuntimeError, match="remote origin/main") as exc_info:
        runner._current_main_sha()

    assert "credential-bearing remote diagnostic" not in str(exc_info.value)
    assert "https://" not in str(exc_info.value)
    assert calls[-1] == [
        "git", "ls-remote", "--exit-code", "origin", "refs/heads/main",
    ]


def test_remote_origin_unreachable_stops_runner_before_cluster_contact(
    isolated_runner, monkeypatch,
):
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", "EXP-STAGE4-SF002-REMOTE-UNREACHABLE")
    calls = _install_git_mock(
        monkeypatch,
        remote_main=None,
        remote_returncode=128,
        remote_stderr="synthetic remote failure",
    )

    with pytest.raises(RuntimeError, match="remote origin/main"):
        asyncio.run(runner._run_experiment())

    assert len(calls) == 5
    assert calls[-1] == [
        "git", "ls-remote", "--exit-code", "origin", "refs/heads/main",
    ]


def _forbid_cluster_or_reservation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("runner contacted the cluster or reserved an attempt")

    monkeypatch.setattr(runner.subprocess, "run", forbidden)
    monkeypatch.setattr(runner.subprocess, "Popen", forbidden)
    monkeypatch.setattr(runner, "run_kubectl", forbidden)
    monkeypatch.setattr(runner, "reserve_experiment_attempt", forbidden)


def _redirect_attempts_directory_or_skip(isolated_runner, tmp_path):
    attempts_dir = runner._attempts_directory_path(str(isolated_runner))
    attempts_dir.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside-attempt-accounting"
    outside.mkdir()
    sentinel = outside / "sentinel.json"
    sentinel.write_bytes(b"preserve-outside-attempt-data\n")
    try:
        attempts_dir.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks are unavailable in this environment: {exc}")
    return attempts_dir, outside, sentinel


def test_redirecting_attempts_directory_stops_before_preflight_or_reservation(
    isolated_runner, monkeypatch, tmp_path,
):
    experiment_id = "EXP-STAGE4-SF002-REDIRECT-PREFLIGHT-TEST"
    attempts_dir, outside, sentinel = _redirect_attempts_directory_or_skip(
        isolated_runner, tmp_path
    )
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    _forbid_cluster_or_reservation(monkeypatch)
    monkeypatch.setattr(
        runner,
        "_current_main_sha",
        lambda **_kwargs: pytest.fail("source preflight followed redirected evidence"),
    )

    with pytest.raises(RuntimeError, match="attempt accounting directory must not redirect"):
        asyncio.run(runner._run_experiment())

    assert attempts_dir.is_symlink()
    assert sentinel.read_bytes() == b"preserve-outside-attempt-data\n"
    assert sorted(path.name for path in outside.iterdir()) == [sentinel.name]


@pytest.mark.parametrize("boundary", ["lock", "write", "transition"])
def test_attempts_redirect_is_rejected_at_each_accounting_boundary(
    isolated_runner, tmp_path, boundary,
):
    experiment_id = "EXP-STAGE4-SF002-REDIRECT-BOUNDARY-TEST"
    attempts_dir, outside, sentinel = _redirect_attempts_directory_or_skip(
        isolated_runner, tmp_path
    )
    marker_path = outside / f"{experiment_id}.attempt.json"
    marker_bytes = json.dumps({
        "experiment_id": experiment_id,
        "state": runner.ATTEMPT_STATE_RESERVED,
        "reservation_token": "synthetic-test-token",
    }).encode("utf-8") + b"\n"

    with pytest.raises(RuntimeError, match="attempt accounting directory must not redirect"):
        if boundary == "lock":
            with runner._reservation_budget_lock(str(isolated_runner)):
                pytest.fail("redirected accounting lock was acquired")
        elif boundary == "write":
            runner._write_json_atomic(
                str(attempts_dir / f"{experiment_id}.attempt.json"),
                {"experiment_id": experiment_id},
            )
        else:
            marker_path.write_bytes(marker_bytes)
            runner.consume_experiment_attempt(
                {
                    "experiment_id": experiment_id,
                    "reservation_token": "synthetic-test-token",
                },
                attempt_root=str(isolated_runner),
            )

    assert sentinel.read_bytes() == b"preserve-outside-attempt-data\n"
    if boundary == "transition":
        assert marker_path.read_bytes() == marker_bytes
    else:
        assert not marker_path.exists()
    assert not (outside / runner.ATTEMPT_BUDGET_LOCK_FILENAME).exists()


def test_direct_reservation_requires_approved_source_sha_before_profile_or_marker(
    isolated_runner, monkeypatch,
):
    experiment_id = "EXP-STAGE4-SF002-NO-APPROVED-RESERVATION-TEST"
    attempts_dir = runner._attempts_directory_path(str(isolated_runner))
    profile_calls = []
    monkeypatch.delenv("STAGE4_APPROVED_MAIN_SHA", raising=False)
    monkeypatch.setattr(
        runner,
        "_observe_protocol_profile",
        lambda _selected_model: profile_calls.append("profile")
        or pytest.fail("model/protocol profile queried without approved source SHA"),
    )

    with pytest.raises(RuntimeError, match="STAGE4_APPROVED_MAIN_SHA"):
        runner.reserve_experiment_attempt(
            experiment_id,
            selected_model="synthetic-model",
            main_sha=MAIN_SHA,
            attempt_root=str(isolated_runner),
        )

    assert profile_calls == []
    assert not attempts_dir.exists()
    assert not Path(
        runner._attempt_marker_path(experiment_id, str(isolated_runner))
    ).exists()


def test_runner_requires_explicit_experiment_id_before_cluster_contact(
    isolated_runner, monkeypatch,
):
    monkeypatch.delenv("STAGE4_EXPERIMENT_ID", raising=False)
    _forbid_cluster_or_reservation(monkeypatch)

    with pytest.raises(RuntimeError, match="STAGE4_EXPERIMENT_ID.*explicitly"):
        asyncio.run(runner._run_experiment())


def test_runner_requires_explicit_approved_sha_before_cluster_contact(
    isolated_runner, monkeypatch,
):
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", "EXP-STAGE4-SF002-NO-APPROVAL")
    monkeypatch.delenv("STAGE4_APPROVED_MAIN_SHA", raising=False)
    _forbid_cluster_or_reservation(monkeypatch)

    with pytest.raises(RuntimeError, match="STAGE4_APPROVED_MAIN_SHA"):
        asyncio.run(runner._run_experiment())


def test_runner_checks_clean_origin_main_without_global_context_switch(
    isolated_runner, monkeypatch,
):
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", "EXP-STAGE4-SF002-PREFLIGHT-ORDER")
    git_calls = _install_git_mock(monkeypatch)
    git_run = runner.subprocess.run
    cluster_calls = []

    class ClusterCommandReached(Exception):
        pass

    def run(args, **kwargs):
        if args[0] == "git":
            return git_run(args, **kwargs)
        pytest.fail(f"runner changed global kubeconfig: {args!r}")

    monkeypatch.setattr(runner.subprocess, "run", run)
    def port_forward(args, **_kwargs):
        cluster_calls.append(list(args))
        raise ClusterCommandReached

    monkeypatch.setattr(runner.subprocess, "Popen", port_forward)

    with pytest.raises(ClusterCommandReached):
        asyncio.run(runner._run_experiment())

    assert len(git_calls) == 5
    assert cluster_calls[0][:4] == [
        "kubectl", "--context", runner.KIND_CONTEXT, "port-forward",
    ]


@pytest.mark.parametrize(
    "chaos_result",
    [
        {"success": True, "stdout": '{"items":[{"kind":"StressChaos"}]}'},
        {"success": False, "stdout": '{"items":[]}'},
    ],
)
def test_zero_chaos_must_be_verified_before_attempt_reservation(
    isolated_runner, monkeypatch, chaos_result,
):
    experiment_id = "EXP-STAGE4-SF002-ZERO-PRE-RESERVATION"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", MAIN_SHA)
    monkeypatch.setattr(runner, "_current_main_sha", lambda **_kwargs: MAIN_SHA)
    _install_mocked_preflight_edges(monkeypatch)
    calls = []

    def kubectl(args, timeout=20):
        calls.append(list(args))
        return chaos_result

    monkeypatch.setattr(runner, "run_kubectl", kubectl)
    monkeypatch.setattr(
        runner, "reserve_experiment_attempt",
        lambda *_args, **_kwargs: pytest.fail("attempt reserved before zero-Chaos proof"),
    )

    result = asyncio.run(runner._run_experiment())
    assert result["outcome"] == "PREFLIGHT_ABORT"
    assert result["attempt_state"] == "NOT_RESERVED"
    assert result["failure_phase"] == "pre_reservation_chaos_not_zero"
    assert calls == [["get", runner.CHAOS_RESOURCE_KINDS, "-A", "-o", "json"]]
    assert not Path(runner._attempt_marker_path(experiment_id)).exists()


def test_new_chaos_after_reservation_releases_marker_before_apply(
    isolated_runner, monkeypatch,
):
    experiment_id = "EXP-STAGE4-SF002-CHAOS-DRIFT"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", MAIN_SHA)
    monkeypatch.setattr(runner, "_current_main_sha", lambda **_kwargs: MAIN_SHA)
    _install_mocked_preflight_edges(monkeypatch)
    events = []

    def kubectl(args, timeout=20):
        assert args == ["get", runner.CHAOS_RESOURCE_KINDS, "-A", "-o", "json"]
        events.append("chaos_read")
        items = [] if len(events) == 1 else [{"kind": "StressChaos"}]
        return {
            "success": True,
            "stdout": json.dumps({"items": items}),
            "returncode": 0,
        }

    def reserve(experiment_id, *, selected_model, main_sha, **_kwargs):
        events.append("reserve")
        reservation = {
            "experiment_id": experiment_id,
            "state": runner.ATTEMPT_STATE_RESERVED,
            "reservation_token": "synthetic-reservation-token",
            "protocol_profile": {
                "model": {"name": selected_model},
                "protocol_marker": runner.G4_PLATFORM_HARDENING_MARKER,
            },
            "protocol_fingerprint": "synthetic-protocol-fingerprint",
            "main_sha": main_sha,
        }
        runner._write_json_atomic(
            runner._attempt_marker_path(experiment_id), reservation
        )
        return reservation

    monkeypatch.setattr(runner, "run_kubectl", kubectl)
    monkeypatch.setattr(runner, "reserve_experiment_attempt", reserve)
    result = asyncio.run(runner._run_experiment())

    assert events == ["chaos_read", "reserve", "chaos_read"]
    assert result["failure_phase"] == "pre_fault_chaos_not_zero"
    assert result["reservation_released"] is True
    assert result["attempt_state"] == "RELEASED_PRE_FAULT"
    assert not Path(runner._attempt_marker_path(experiment_id)).exists()


@pytest.mark.parametrize(
    "final_result",
    [
        {"success": True, "stdout": '{"items":[{"kind":"StressChaos"}]}'},
        {"success": False, "stdout": '{"items":[]}'},
    ],
)
def test_final_chaos_read_after_source_check_blocks_apply(
    isolated_runner, monkeypatch, final_result,
):
    experiment_id = "EXP-STAGE4-SF002-FINAL-CHAOS-DRIFT"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", MAIN_SHA)
    events = []
    monkeypatch.setattr(
        runner, "_current_main_sha",
        lambda **_kwargs: events.append("source") or MAIN_SHA,
    )
    _install_mocked_preflight_edges(monkeypatch)

    def kubectl(args, timeout=20):
        assert args == ["get", runner.CHAOS_RESOURCE_KINDS, "-A", "-o", "json"]
        events.append("chaos_read")
        if events.count("chaos_read") == 3:
            return final_result
        return {"success": True, "stdout": '{"items":[]}'}

    def reserve(*_args, **_kwargs):
        events.append("reserve")
        return {
            "experiment_id": experiment_id,
            "state": runner.ATTEMPT_STATE_RESERVED,
            "reservation_token": "synthetic-reservation-token",
            "protocol_profile": {
                "model": {"name": runner.SELECTED_STAGE4_AGENT_MODEL},
                "protocol_marker": runner.G4_PLATFORM_HARDENING_MARKER,
            },
            "protocol_fingerprint": "synthetic-protocol-fingerprint",
            "main_sha": MAIN_SHA,
        }

    monkeypatch.setattr(runner, "run_kubectl", kubectl)
    monkeypatch.setattr(runner, "reserve_experiment_attempt", reserve)
    monkeypatch.setattr(
        runner, "_persist_stage4_preflight_evidence",
        lambda *_args: events.append("durable_preflight") or "synthetic-preflight",
    )
    monkeypatch.setattr(
        runner, "release_experiment_reservation",
        lambda *_args: events.append("release") or True,
    )
    monkeypatch.setattr(
        runner, "_persist_stage4_prefault_failure",
        lambda *_args: events.append("prefault_evidence") or "synthetic-prefault",
    )
    result = asyncio.run(runner._run_experiment())

    assert events == [
        "source", "source", "chaos_read", "reserve", "chaos_read",
        "durable_preflight", "source", "chaos_read", "release", "prefault_evidence",
    ]
    assert result["failure_phase"] == "immediate_pre_apply_chaos_not_zero"
    assert result["reservation_released"] is True
    assert result["attempt_state"] == "RELEASED_PRE_FAULT"
    assert result["phases"]["immediate_pre_apply_chaos_check"]["result"] == final_result
    assert not Path(runner._attempt_marker_path(experiment_id)).exists()


class _ProcessStub:
    def terminate(self):
        return None

    def wait(self, timeout=None):
        return 0


def test_partial_port_forward_startup_cleans_started_process(
    isolated_runner, monkeypatch,
):
    experiment_id = "EXP-STAGE4-SF002-PARTIAL-FORWARD"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", MAIN_SHA)
    monkeypatch.setattr(runner, "_current_main_sha", lambda **_kwargs: MAIN_SHA)
    events = []

    class StartedForward:
        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            events.append("wait")

    def popen(*_args, **_kwargs):
        events.append("start")
        if events.count("start") == 2:
            raise OSError("synthetic second forward failure")
        return StartedForward()

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(
        runner, "run_kubectl",
        lambda *_args, **_kwargs: pytest.fail("cluster read after failed forward"),
    )
    monkeypatch.setattr(
        runner, "reserve_experiment_attempt",
        lambda *_args, **_kwargs: pytest.fail("attempt reserved after failed forward"),
    )
    with pytest.raises(OSError, match="synthetic second forward failure"):
        asyncio.run(runner._run_experiment())
    assert events == ["start", "start", "terminate", "wait"]
    assert not Path(runner._attempt_marker_path(experiment_id)).exists()


def test_port_forward_startup_interrupt_cleans_every_started_process(
    isolated_runner, monkeypatch,
):
    experiment_id = "EXP-STAGE4-SF002-INTERRUPTED-FORWARD"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", MAIN_SHA)
    monkeypatch.setattr(runner, "_current_main_sha", lambda **_kwargs: MAIN_SHA)
    events = []

    class StartedForward:
        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            events.append("wait")

    def popen(*_args, **_kwargs):
        events.append("start")
        return StartedForward()

    def interrupted_sleep(_seconds):
        events.append("settle")
        raise KeyboardInterrupt

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner.time, "sleep", interrupted_sleep)
    monkeypatch.setattr(
        runner, "wait_for_telemetry_readiness",
        lambda: pytest.fail("telemetry read after interrupted startup"),
    )
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(runner._run_experiment())
    assert events == (
        ["start"] * 5 + ["settle"] + ["terminate"] * 5 + ["wait"] * 5
    )
    assert not Path(runner._attempt_marker_path(experiment_id)).exists()


def test_port_forwards_stop_if_evidence_setup_is_interrupted(
    isolated_runner, monkeypatch,
):
    experiment_id = "EXP-STAGE4-SF002-INTERRUPTED-EVIDENCE"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", MAIN_SHA)
    monkeypatch.setattr(runner, "_current_main_sha", lambda **_kwargs: MAIN_SHA)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    events = []

    class StartedForward:
        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            events.append("wait")

    monkeypatch.setattr(
        runner.subprocess, "Popen",
        lambda *_args, **_kwargs: events.append("start") or StartedForward(),
    )
    monkeypatch.setattr(
        runner, "stage4_evidence_metadata",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(
        runner, "wait_for_telemetry_readiness",
        lambda: pytest.fail("telemetry read after interrupted evidence setup"),
    )
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(runner._run_experiment())
    assert events == ["start"] * 5 + ["terminate"] * 5 + ["wait"] * 5
    assert not Path(runner._attempt_marker_path(experiment_id)).exists()


def _install_mocked_preflight_edges(monkeypatch):
    context_calls = []
    port_forward_calls = []

    def run(args, **kwargs):
        context_calls.append(list(args))
        return CompletedProcess(args, 0, "", "")

    def popen(args, **kwargs):
        port_forward_calls.append(list(args))
        return _ProcessStub()

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        runner,
        "wait_for_telemetry_readiness",
        lambda: (True, {"attempts": 2, "stable_probes": 2}),
    )
    monkeypatch.setattr(
        runner,
        "wait_for_baseline_readiness",
        lambda: (True, {"attempts": 2, "stable_probes": 2}, True, {"stdout": "ready"}),
    )
    monkeypatch.setattr(
        runner,
        "collect_sf002_cpu_telemetry",
        lambda: {"success": True, "max_cores": 0.1},
    )
    return context_calls, port_forward_calls


def test_source_change_before_reservation_stops_without_marker_or_injection(
    isolated_runner, monkeypatch,
):
    experiment_id = "EXP-STAGE4-SF002-SOURCE-CHANGE-PRE-RESERVATION"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", MAIN_SHA)
    source_checks = []

    def changed_source(expected_sha=None, **_kwargs):
        source_checks.append(expected_sha)
        if len(source_checks) == 1:
            return MAIN_SHA
        raise RuntimeError("source changed since startup")

    monkeypatch.setattr(runner, "_current_main_sha", changed_source)
    _install_mocked_preflight_edges(monkeypatch)
    kubectl_calls = []

    def kubectl(args, timeout=20):
        kubectl_calls.append(list(args))
        pytest.fail("cluster readiness or mutation reached after source drift")

    monkeypatch.setattr(
        runner,
        "_observe_protocol_profile",
        lambda _selected_model: {"synthetic": "profile"},
    )
    monkeypatch.setattr(runner, "run_kubectl", kubectl)

    with pytest.raises(RuntimeError, match="source changed since startup"):
        asyncio.run(runner._run_experiment())

    assert source_checks == [None, MAIN_SHA]
    assert kubectl_calls == []
    assert not Path(runner._attempt_marker_path(experiment_id)).exists()
    assert not Path(runner._attempt_marker_path(experiment_id)).with_name(
        runner.ATTEMPT_BUDGET_LOCK_FILENAME
    ).exists()


def test_source_change_before_t0_releases_reservation_without_apply(
    isolated_runner, monkeypatch,
):
    experiment_id = "EXP-STAGE4-SF002-SOURCE-CHANGE-PRE-T0"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", MAIN_SHA)
    source_checks = []

    def changed_source(expected_sha=None, **_kwargs):
        source_checks.append(expected_sha)
        if len(source_checks) <= 3:
            return MAIN_SHA
        raise RuntimeError("source changed before T0")

    monkeypatch.setattr(runner, "_current_main_sha", changed_source)
    _install_mocked_preflight_edges(monkeypatch)
    reserve_calls = []
    kubectl_calls = []

    def reserve(
        experiment_id,
        *,
        selected_model,
        main_sha,
        expected_main_sha=None,
        attempt_root=None,
    ):
        runner._current_main_sha(expected_sha=expected_main_sha)
        reservation = {
            "experiment_id": experiment_id,
            "state": runner.ATTEMPT_STATE_RESERVED,
            "reservation_token": "synthetic-reservation-token",
            "protocol_profile": runner.APPROVED_G4_PROTOCOL_PROFILE,
            "protocol_fingerprint": runner.protocol_fingerprint(
                runner.APPROVED_G4_PROTOCOL_PROFILE
            ),
            "main_sha": main_sha,
        }
        marker_path = Path(runner._attempt_marker_path(experiment_id, attempt_root))
        runner._write_json_atomic(str(marker_path), reservation)
        reserve_calls.append(reservation)
        return reservation

    def kubectl(args, timeout=20):
        kubectl_calls.append(list(args))
        if args[0] == "get" and args[1] == runner.CHAOS_RESOURCE_KINDS:
            return {
                "success": True,
                "stdout": '{"items": []}',
                "stderr": "",
                "returncode": 0,
            }
        pytest.fail(f"unexpected cluster command before T0 guard: {args!r}")

    monkeypatch.setattr(runner, "reserve_experiment_attempt", reserve)
    monkeypatch.setattr(runner, "run_kubectl", kubectl)

    with pytest.raises(RuntimeError, match="source changed before T0"):
        asyncio.run(runner._run_experiment())

    marker_path = Path(runner._attempt_marker_path(experiment_id))
    assert source_checks == [None, MAIN_SHA, MAIN_SHA, MAIN_SHA]
    assert len(reserve_calls) == 1
    assert kubectl_calls == [
        ["get", runner.CHAOS_RESOURCE_KINDS, "-A", "-o", "json"],
        ["get", runner.CHAOS_RESOURCE_KINDS, "-A", "-o", "json"],
    ]
    assert not marker_path.exists()
    assert not any(args[0] == "apply" for args in kubectl_calls)


@pytest.mark.parametrize(
    "existing_record",
    ["attempt-marker", "primary-evidence", "runlog", "leftover-chaos"],
)
def test_runner_rejects_used_experiment_id_without_reserving_or_overwriting(
    isolated_runner, monkeypatch, existing_record,
):
    experiment_id = "EXP-STAGE4-SF002-015"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    if existing_record == "attempt-marker":
        path = Path(runner._attempt_marker_path(experiment_id))
    elif existing_record == "primary-evidence":
        path = Path(runner._experiment_evidence_dir(experiment_id)) / f"{experiment_id}.json"
    elif existing_record == "runlog":
        path = Path(runner._experiment_evidence_dir(experiment_id)) / f"{experiment_id}.runlog.txt"
    else:
        path = Path(runner._experiment_evidence_dir(experiment_id)) / f"{experiment_id}.leftover-chaos.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"preserved": true}\n'
    path.write_bytes(original)
    _forbid_cluster_or_reservation(monkeypatch)

    with pytest.raises(RuntimeError, match="already exists"):
        asyncio.run(runner._run_experiment())

    assert path.read_bytes() == original


def test_poison_latch_stops_runner_before_context_switch_or_port_forward(
    isolated_runner, monkeypatch,
):
    experiment_id = "EXP-STAGE4-SF002-POISONED"
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", MAIN_SHA)
    path = Path(runner._poisoned_environment_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"poisoned": true}\n'
    path.write_bytes(original)
    _forbid_cluster_or_reservation(monkeypatch)
    monkeypatch.setattr(
        runner,
        "_current_main_sha",
        lambda *args, **kwargs: pytest.fail("git preflight ran before poison check"),
    )

    with pytest.raises(RuntimeError, match="environment is poisoned"):
        asyncio.run(runner._run_experiment())

    assert path.read_bytes() == original


def _set_test_secrets(monkeypatch, secret_dir: Path, *, file_backed_name: str) -> None:
    monkeypatch.setenv("ATLASOPS_STAGE4_SECRET_DIR", str(secret_dir))
    for name in SECRET_FILES:
        if name == file_backed_name:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, f"synthetic-{name.lower()}")


def _create_file_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks are unavailable in this environment: {exc}")


@pytest.mark.parametrize("target_kind", ["outside-secret-dir", "inside-checkout"])
def test_stage4_secret_file_symlink_cannot_escape_external_directory(
    isolated_runner, monkeypatch, tmp_path, target_kind,
):
    secret_dir = tmp_path / "external-secrets"
    secret_dir.mkdir()
    if target_kind == "inside-checkout":
        target = isolated_runner / "checkout-secret.secret"
    else:
        target = tmp_path / "outside-secret.secret"
    target.write_text("secret-value-must-not-appear-in-errors", encoding="utf-8")
    link = secret_dir / SECRET_FILES["ATLASOPS_API_KEY"]
    _create_file_symlink(link, target)
    _set_test_secrets(monkeypatch, secret_dir, file_backed_name="ATLASOPS_API_KEY")

    with pytest.raises(RuntimeError, match="Stage 4 secret file") as exc_info:
        runner.load_stage4_secrets()

    assert "secret-value-must-not-appear-in-errors" not in str(exc_info.value)


def test_stage4_secret_file_symlink_inside_external_directory_is_allowed(
    isolated_runner, monkeypatch, tmp_path,
):
    secret_dir = tmp_path / "external-secrets"
    secret_dir.mkdir()
    target = secret_dir / "api-key-target.secret"
    target.write_text("synthetic-internal-api-key", encoding="utf-8")
    link = secret_dir / SECRET_FILES["ATLASOPS_API_KEY"]
    _create_file_symlink(link, target)
    _set_test_secrets(monkeypatch, secret_dir, file_backed_name="ATLASOPS_API_KEY")

    loaded = runner.load_stage4_secrets()

    assert loaded["ATLASOPS_API_KEY"] == "synthetic-internal-api-key"
