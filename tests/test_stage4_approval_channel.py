"""Synthetic process-boundary tests; these do not reserve or run a G4 attempt."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = Path(__file__).with_name("stage4_approval_process.py")
TEST_KEY = "synthetic-operator-key"


def _wait_for(path: Path, process: subprocess.Popen, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            raise AssertionError(f"synthetic process exited with status {process.returncode}")
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path.name}")


def _start_process(tmp_path: Path, timeout: float) -> subprocess.Popen:
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "ATLASOPS_API_KEY": TEST_KEY,
        "ATLASOPS_AUDIT_SECRET": "synthetic-audit-secret-for-tests",
        "TEST_APPROVAL_TIMEOUT": str(timeout),
        "TRAJECTORIES_DIR": str(tmp_path / "trajectories"),
        "PYTHONPATH": str(REPO_ROOT),
    })
    return subprocess.Popen(
        [sys.executable, str(HELPER), str(tmp_path)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def _pending(base_url: str, process: subprocess.Popen, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"synthetic process exited with status {process.returncode}")
        try:
            response = httpx.get(f"{base_url}/approval/pending", headers={"X-AtlasOps-Key": TEST_KEY}, timeout=1)
            response.raise_for_status()
            pending = response.json()["pending"]
            if pending:
                return pending[0]
        except httpx.TransportError:
            pass
        time.sleep(0.02)
    raise AssertionError("coordinator never opened a pending P1 request")


@pytest.fixture
def approval_process(tmp_path):
    processes = []

    def start(name: str, timeout: float = 5):
        output_dir = tmp_path / name
        process = _start_process(output_dir, timeout)
        processes.append(process)
        return process, output_dir, _wait_for(output_dir / "ready.json", process)["base_url"]

    yield start
    for process in processes:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        if process.stderr:
            process.stderr.close()


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_operator_decision_reaches_waiting_host_coordinator(approval_process, decision):
    process, output, base_url = approval_process(decision)
    pending = _pending(base_url, process)
    payload = {"token": pending["token"], "decision": decision, "approved_by": "test-operator"}
    response = httpx.post(
        f"{base_url}/approve", json=payload, headers={"X-AtlasOps-Key": TEST_KEY}, timeout=2,
    )
    assert response.status_code == 200
    result = _wait_for(output / "result.json", process)
    assert result["approval"]["decision"] == decision
    assert result["approval"]["approved_by"] == "test-operator"
    assert ("remediation" in result["roles"]) is (decision == "approved")
    if decision == "rejected":
        assert result["remediation"]["status"] == "approval_rejected"
        assert result["remediation"]["executed_actions"] == []


def test_unauthenticated_and_unrelated_process_cannot_decide_host_request(approval_process, monkeypatch):
    process, output, base_url = approval_process("auth")
    pending = _pending(base_url, process)
    payload = {"token": pending["token"], "decision": "approved", "approved_by": "test-operator"}
    assert httpx.get(f"{base_url}/approval/pending", timeout=2).status_code == 401
    assert httpx.post(f"{base_url}/approve", json=payload, timeout=2).status_code == 401
    assert httpx.post(
        f"{base_url}/approve", json=payload, headers={"X-AtlasOps-Key": "wrong"}, timeout=2,
    ).status_code == 401

    import agents.coordinator as other_process
    monkeypatch.setattr(other_process, "_RUNTIME_API_KEY", TEST_KEY)
    with TestClient(other_process.app) as client:
        assert client.post("/approve", json=payload, headers={"X-AtlasOps-Key": TEST_KEY}).json() == {
            "ok": False, "error": "unknown_token",
        }
    assert httpx.get(
        f"{base_url}/approval/pending", headers={"X-AtlasOps-Key": TEST_KEY}, timeout=2,
    ).json()["pending"][0]["decision"] == "pending"
    assert httpx.post(
        f"{base_url}/approve", json=payload, headers={"X-AtlasOps-Key": TEST_KEY}, timeout=2,
    ).status_code == 200
    assert _wait_for(output / "result.json", process)["approval"]["decision"] == "approved"


def test_timeout_fails_closed_and_late_decision_is_rejected(approval_process):
    process, output, base_url = approval_process("timeout", timeout=3)
    _pending(base_url, process)
    result = _wait_for(output / "result.json", process)
    assert result["approval"]["decision"] == "timeout"
    assert result["remediation"]["status"] == "approval_timeout"
    assert "remediation" not in result["roles"]
    # The listener shuts down with the incident; the gate itself must not retain the token.
    from agents.approval import ApprovalGate
    gate = ApprovalGate(timeout_seconds=0.001)
    req = gate.request("expired", "P1", "synthetic")
    import asyncio
    assert asyncio.run(gate.wait_for_decision("expired"))["status"] == "timeout"
    assert gate.callback(req.token, "approved") == {"ok": False, "error": "unknown_token"}


def test_restart_drops_old_token_and_requires_a_fresh_decision(approval_process):
    process, _, base_url = approval_process("before-restart", timeout=10)
    old_token = _pending(base_url, process)["token"]
    process.terminate()
    process.wait(timeout=10)
    new_process, output, new_base_url = approval_process("after-restart")
    new_token = _pending(new_base_url, new_process)["token"]
    assert new_token != old_token
    headers = {"X-AtlasOps-Key": TEST_KEY}
    assert httpx.post(
        f"{new_base_url}/approve",
        json={"token": old_token, "decision": "approved", "approved_by": "test-operator"},
        headers=headers, timeout=2,
    ).status_code == 400
    assert httpx.post(
        f"{new_base_url}/approve",
        json={"token": new_token, "decision": "rejected", "approved_by": "test-operator"},
        headers=headers, timeout=2,
    ).status_code == 200
    assert _wait_for(output / "result.json", new_process)["approval"]["decision"] == "rejected"


def test_stage4_secret_loading_requires_real_external_or_environment_values(monkeypatch, tmp_path):
    from scripts.run_stage4_golden_incident import load_stage4_secrets

    secret_files = {
        "ARGOCD_PASS": "argocd-pass.secret",
        "ATLASOPS_AUDIT_SECRET": "atlasops-audit-secret.secret",
        "ATLASOPS_API_KEY": "atlasops-api-key.secret",
        "ALERTMANAGER_WEBHOOK_SECRET": "alertmanager-webhook-secret.secret",
    }
    for name in secret_files:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ATLASOPS_STAGE4_SECRET_DIR", raising=False)
    with pytest.raises(RuntimeError, match="Missing Stage 4 runtime secrets"):
        load_stage4_secrets()
    assert all(name not in os.environ for name in secret_files)

    secret_dir = tmp_path / "external-secrets"
    secret_dir.mkdir()
    monkeypatch.setenv("ATLASOPS_STAGE4_SECRET_DIR", str(secret_dir))
    for name, filename in secret_files.items():
        (secret_dir / filename).write_text(f"synthetic-{name.lower()}", encoding="utf-8")
    loaded = load_stage4_secrets()
    assert loaded == {name: f"synthetic-{name.lower()}" for name in secret_files}
    for name in secret_files:
        monkeypatch.delenv(name)
    (secret_dir / "atlasops-api-key.secret").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ATLASOPS_API_KEY"):
        load_stage4_secrets()
    assert all(name not in os.environ for name in secret_files)


def test_stage4_secret_dir_is_explicit_and_absolute(monkeypatch):
    from scripts.run_stage4_golden_incident import load_stage4_secrets

    monkeypatch.setenv("ATLASOPS_STAGE4_SECRET_DIR", "relative-secrets")
    with pytest.raises(RuntimeError, match="absolute path"):
        load_stage4_secrets()


def test_stage4_secret_dir_cannot_be_inside_checkout(monkeypatch):
    import scripts.run_stage4_golden_incident as runner

    monkeypatch.setenv("ATLASOPS_STAGE4_SECRET_DIR", str(Path(runner.REPO_ROOT) / "secrets"))
    with pytest.raises(RuntimeError, match="outside the checkout"):
        runner.load_stage4_secrets()


def test_stage4_secret_sources_must_agree(monkeypatch, tmp_path):
    from scripts.run_stage4_golden_incident import load_stage4_secrets

    monkeypatch.setenv("ATLASOPS_STAGE4_SECRET_DIR", str(tmp_path))
    monkeypatch.setenv("ATLASOPS_API_KEY", "synthetic-env-key")
    (tmp_path / "atlasops-api-key.secret").write_text("synthetic-file-key", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Conflicting Stage 4 secret sources for ATLASOPS_API_KEY"):
        load_stage4_secrets()


def test_missing_secrets_stop_runner_before_cluster_or_reservation(monkeypatch):
    import asyncio
    import scripts.run_stage4_golden_incident as runner

    for name in ("ARGOCD_PASS", "ATLASOPS_AUDIT_SECRET", "ATLASOPS_API_KEY", "ALERTMANAGER_WEBHOOK_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ATLASOPS_STAGE4_SECRET_DIR", raising=False)
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("cluster contacted"))
    monkeypatch.setattr(runner, "reserve_experiment_attempt", lambda *args, **kwargs: pytest.fail("attempt reserved"))
    with pytest.raises(RuntimeError, match="Missing Stage 4 runtime secrets"):
        asyncio.run(runner.main())


def test_runner_starts_authenticated_host_listener_before_experiment(monkeypatch):
    import asyncio
    import scripts.run_stage4_golden_incident as runner
    from agents.approval import approval_gate

    monkeypatch.delenv("ATLASOPS_STAGE4_SECRET_DIR", raising=False)
    for name in ("ARGOCD_PASS", "ATLASOPS_AUDIT_SECRET", "ATLASOPS_API_KEY", "ALERTMANAGER_WEBHOOK_SECRET"):
        monkeypatch.setenv(name, f"synthetic-{name.lower()}")
    previous_base = os.environ.get("ATLASOPS_PUBLIC_BASE_URL")

    async def synthetic_experiment():
        base_url = os.environ["ATLASOPS_PUBLIC_BASE_URL"]
        assert base_url.startswith("http://127.0.0.1:")
        assert approval_gate.timeout_seconds == 300
        request = approval_gate.request("inc-main-synthetic", "P1", "synthetic")
        async with httpx.AsyncClient(timeout=2) as client:
            assert (await client.get(f"{base_url}/approval/pending")).status_code == 401
            response = await client.get(
                f"{base_url}/approval/pending",
                headers={"X-AtlasOps-Key": "synthetic-atlasops_api_key"},
            )
            assert response.status_code == 200
            assert any(item["token"] == request.token for item in response.json()["pending"])
            decision = await client.post(
                f"{base_url}/approve",
                json={"token": request.token, "decision": "rejected", "approved_by": "test-operator"},
                headers={"X-AtlasOps-Key": "synthetic-atlasops_api_key"},
            )
            assert decision.status_code == 200
        assert (await approval_gate.wait_for_decision("inc-main-synthetic"))["status"] == "rejected"
        return {"attempt_state": "NOT_RESERVED"}

    monkeypatch.setattr(runner, "_run_experiment", synthetic_experiment)
    assert asyncio.run(runner.main()) == {"attempt_state": "NOT_RESERVED"}
    assert os.environ.get("ATLASOPS_PUBLIC_BASE_URL") == previous_base


def test_first_decision_cannot_be_reversed_before_waiter_resumes():
    from agents.approval import ApprovalGate

    gate = ApprovalGate(timeout_seconds=1)
    req = gate.request("inc-one-decision", "P1", "synthetic")
    assert gate.callback(req.token, "approved", approved_by="first")["ok"]
    assert gate.callback(req.token, "rejected", approved_by="second") == {
        "ok": False, "error": "already_decided",
    }
    import asyncio
    assert asyncio.run(gate.wait_for_decision("inc-one-decision"))["approved_by"] == "first"
