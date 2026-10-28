import asyncio
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from demo.bug_lab.engine import LabOptions
from demo.bug_lab.llm import RULES
from demo.bug_lab.server import LabConfig, create_app
from tests.test_bug_lab import make_app

HEADERS = {"X-Bug-Lab": "1"}


def _config(root, *, resume=False):
    return LabConfig(
        workdir=root, agents="rules", resume=resume,
        options=LabOptions(
            step_delay=0, monitor_interval=0.05, approval_timeout=5, verify_timeout=1,
        ),
    )


def _pending(client):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        incident = client.get("/lab/api/state").json()["incident"]
        if incident and incident["status"] == "awaiting_approval":
            return incident
        time.sleep(0.02)
    pytest.fail("The server-detected P1 incident never reached its approval gate")


def test_health_reports_application_readiness_not_store_resolution(tmp_path):
    with TestClient(make_app(tmp_path), base_url="http://127.0.0.1") as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ready"
        assert health.json()["backend"]["mode"] == "rules"
        assert client.post(
            "/lab/api/inject", headers=HEADERS, json={"bug": "truncated-data"},
        ).status_code == 202
        _pending(client)
        assert client.get("/shop/api/products").status_code == 500
        assert client.get("/health").json()["status"] == "ready"
        assert client.get("/lab/api/state").json()["incident"]["closed_at"] is None


def test_health_is_unready_before_and_after_lifespan(tmp_path):
    app = make_app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/health").status_code == 503
    client.close()
    with TestClient(app, base_url="http://127.0.0.1") as running:
        assert running.get("/health").status_code == 200
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/health").status_code == 503
    client.close()


def test_stopping_an_unfinished_incident_retains_cancelled_evidence(tmp_path):
    lab = make_app(tmp_path).state.lab

    async def run():
        lab.inject("truncated-data")
        await lab.monitor_once()
        await asyncio.sleep(0)
        await lab.stop()
        assert not lab.active
        assert lab.incident["status"] == "cancelled"
        record = json.loads(lab.log_path.read_text())
        assert record["id"] == lab.incident["id"]
        assert record["closed_at"] is not None
        assert record["resolved_by"] is None
        assert record["verification"] is None
        assert record["evidence_classification"] == "LOCAL_DEMONSTRATION_NOT_SCIENTIFIC"

    asyncio.run(run())


def test_owned_resume_retains_history_raw_evidence_and_unique_incident_ids(tmp_path):
    root = tmp_path / "lab"
    with TestClient(create_app(_config(root), backend=RULES), base_url="http://127.0.0.1") as first:
        first.post("/lab/api/inject", headers=HEADERS, json={"bug": "truncated-data"})
        incident = _pending(first)
        first.post("/lab/api/reset", headers=HEADERS)
        before = first.get("/lab/api/evidence").content
        assert json.loads(before)["status"] == "cancelled"
    assert (root / "incidents.jsonl").read_bytes() == before

    with TestClient(
        create_app(_config(root, resume=True), backend=RULES), base_url="http://127.0.0.1",
    ) as second:
        state = second.get("/lab/api/state").json()
        assert state["incident"] is None
        assert state["history"][0]["id"] == incident["id"]
        assert state["history"][0]["status"] == "cancelled"
        assert second.get("/lab/api/evidence").content == before
        assert second.get("/shop/api/products").status_code == 200
        second.post("/lab/api/inject", headers=HEADERS, json={"bug": "truncated-data"})
        current = _pending(second)
        assert current["id"] != incident["id"]
        assert second.post("/lab/api/approval", headers=HEADERS, json={
            "incident_id": incident["id"], "digest": incident["approval"]["digest"],
            "decision": "approve",
        }).status_code == 409
        assert second.get("/shop/api/products").status_code == 500
        second.post("/lab/api/reset", headers=HEADERS)
        after = second.get("/lab/api/evidence").content
        assert after.startswith(before)
        records = [json.loads(line) for line in after.splitlines()]
        assert [record["id"] for record in records] == [incident["id"], current["id"]]
        assert all(record["status"] == "cancelled" for record in records)


def test_resume_never_adopts_an_unowned_nonempty_directory(tmp_path):
    root = tmp_path / "owner-data"
    root.mkdir()
    sentinel = root / "private-data.txt"
    sentinel.write_text("not an AtlasOps workspace", encoding="utf-8")
    with pytest.raises(ValueError, match="owned|marker|empty"):
        create_app(_config(root, resume=True), backend=RULES)
    assert sentinel.read_text(encoding="utf-8") == "not an AtlasOps workspace"
    assert sorted(path.name for path in root.iterdir()) == ["private-data.txt"]


def test_approval_receipt_cannot_authorize_the_same_id_in_another_workspace(tmp_path):
    with TestClient(make_app(tmp_path / "first"), base_url="http://127.0.0.1") as first:
        first.post("/lab/api/inject", headers=HEADERS, json={"bug": "truncated-data"})
        previous = _pending(first)

    with TestClient(make_app(tmp_path / "second"), base_url="http://127.0.0.1") as second:
        second.post("/lab/api/inject", headers=HEADERS, json={"bug": "truncated-data"})
        current = _pending(second)
        assert current["id"] == previous["id"]
        assert current["approval"]["digest"] != previous["approval"]["digest"]
        assert second.post("/lab/api/approval", headers=HEADERS, json={
            "incident_id": previous["id"], "digest": previous["approval"]["digest"],
            "decision": "approve",
        }).status_code == 409
        assert second.get("/shop/api/products").status_code == 500
        assert second.get("/lab/api/state").json()["incident"]["status"] == "awaiting_approval"


def test_pending_evidence_is_durable_before_an_approval_can_apply(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post("/lab/api/inject", headers=HEADERS, json={"bug": "truncated-data"})
        incident = _pending(client)
        pending = json.loads((app.state.lab.workspace.root / "pending.json").read_text())
        assert pending["id"] == incident["id"]
        assert pending["approval"]["digest"] == incident["approval"]["digest"]
        assert pending["status"] == "awaiting_approval"
        assert pending["closed_at"] is None
        assert pending["verification"] is None
        assert client.get("/shop/api/products").status_code == 500


def test_evidence_write_failure_makes_the_app_unready_without_applying(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    replace = os.replace

    def fail_pending_write(source, target):
        if str(target).endswith("pending.json"):
            raise OSError("synthetic disk failure")
        return replace(source, target)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        monkeypatch.setattr(os, "replace", fail_pending_write)
        assert client.post(
            "/lab/api/inject", headers=HEADERS, json={"bug": "truncated-data"},
        ).status_code == 202
        deadline = time.monotonic() + 5
        while client.get("/health").status_code != 503 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert client.get("/health").status_code == 503
        assert client.get("/shop/api/products").status_code == 500
        assert client.post("/lab/api/reset", headers=HEADERS).status_code == 503
        assert client.post("/lab/api/approval", headers=HEADERS, json={
            "incident_id": "INC-0001", "digest": "0" * 64, "decision": "approve",
        }).status_code == 503
