import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"X-Bug-Lab": "1"}


def _port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _kill_owned_process_tree(process):
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10,
        )
    else:
        process.kill()
    process.wait(timeout=10)


@contextmanager
def _server(root, log_path, *, resume=False):
    port = _port()
    command = [
        sys.executable, "-m", "demo.bug_lab", "--agents", "rules",
        "--host", "127.0.0.1", "--port", str(port), "--step-delay", "0",
        "--workdir", str(root),
    ]
    if resume:
        command.append("--resume")
    env = dict(
        os.environ, BUG_LAB_LLM_API_KEY="", BUG_LAB_LLM_BASE_URL="http://model.invalid/v1",
        BUG_LAB_LLM_MODEL="synthetic-unused", HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1", ATLASOPS_AUTO_HF_INFERENCE="0",
    )
    with log_path.open("wb") as output:
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(f"Bug Lab startup exited with {process.returncode}: {log_path.read_text()}")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail("The product CLI did not become ready")
                yield process, client
        finally:
            if process.poll() is None:
                _kill_owned_process_tree(process)


def _incident(client, predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        incident = client.get("/lab/api/state").json()["incident"]
        if incident and predicate(incident):
            return incident
        time.sleep(0.05)
    pytest.fail("The product CLI did not reach the expected incident state")


def test_real_process_crash_preserves_evidence_and_cannot_replay_approval(tmp_path):
    root = tmp_path / "owned"
    with _server(root, tmp_path / "first.log") as (process, client):
        assert client.get("/").status_code == 200
        assert client.get("/shop/").status_code == 200
        assert client.post(
            "/lab/api/inject", headers=HEADERS, json={"bug": "truncated-data"},
        ).status_code == 202
        previous = _incident(client, lambda incident: incident["status"] == "awaiting_approval")
        pending = json.loads((root / "pending.json").read_text(encoding="utf-8"))
        assert pending["approval"]["digest"] == previous["approval"]["digest"]
        _kill_owned_process_tree(process)

    with _server(root, tmp_path / "second.log", resume=True) as (_, client):
        recovered = [json.loads(line) for line in client.get("/lab/api/evidence").text.splitlines()]
        assert len(recovered) == 1
        assert recovered[0]["id"] == previous["id"]
        assert recovered[0]["status"] == "interrupted"
        assert recovered[0]["resolved_by"] is None
        assert recovered[0]["verification"] is None
        assert recovered[0]["events"] == pending["events"]
        assert recovered[0]["interrupted_state"]["approval"] == pending["approval"]
        assert recovered[0]["evidence_classification"] == "LOCAL_DEMONSTRATION_NOT_SCIENTIFIC"
        assert client.get("/shop/api/products").status_code == 500
        current = _incident(client, lambda incident: incident["status"] == "awaiting_approval")
        assert current["sequence"] == previous["sequence"] + 1
        assert current["id"] != previous["id"]
        assert client.post("/lab/api/approval", headers=HEADERS, json={
            "incident_id": previous["id"], "digest": previous["approval"]["digest"],
            "decision": "approve",
        }).status_code == 409
        assert client.get("/shop/api/products").status_code == 500
        assert client.post("/lab/api/approval", headers=HEADERS, json={
            "incident_id": current["id"], "digest": current["approval"]["digest"],
            "decision": "reject",
        }).status_code == 200
        closed = _incident(client, lambda incident: incident["closed_at"] is not None)
        assert closed["status"] == "escalated"
        assert closed["approval"]["decision"] == "rejected"
        evidence = client.get("/lab/api/evidence").content
        assert len(evidence.splitlines()) == 2
        assert client.get("/health").status_code == 200

    assert (root / "incidents.jsonl").read_bytes() == evidence
    assert "Backend: Rule engine (no model)" in (tmp_path / "first.log").read_text()
    assert "Backend: Rule engine (no model)" in (tmp_path / "second.log").read_text()


@pytest.mark.skipif(os.name == "nt", reason="Windows graceful Ctrl+C requires a console smoke test")
def test_foreground_sigterm_retains_cancelled_incident_before_resume(tmp_path):
    root = tmp_path / "owned"
    with _server(root, tmp_path / "graceful.log") as (process, client):
        client.post("/lab/api/inject", headers=HEADERS, json={"bug": "truncated-data"})
        previous = _incident(client, lambda incident: incident["status"] == "awaiting_approval")
        process.terminate()
        process.wait(timeout=10)

    evidence = (root / "incidents.jsonl").read_bytes()
    cancelled = json.loads(evidence)
    assert cancelled["id"] == previous["id"]
    assert cancelled["status"] == "cancelled"
    assert cancelled["verification"] is None
    assert cancelled["resolved_by"] is None
    assert cancelled["interrupted_state"]["approval"]["digest"] == previous["approval"]["digest"]

    with _server(root, tmp_path / "graceful-resume.log", resume=True) as (_, client):
        assert client.get("/lab/api/evidence").content == evidence
        current = _incident(client, lambda incident: incident["status"] == "awaiting_approval")
        assert current["sequence"] == previous["sequence"] + 1
        assert client.post("/lab/api/approval", headers=HEADERS, json={
            "incident_id": previous["id"], "digest": previous["approval"]["digest"],
            "decision": "approve",
        }).status_code == 409
