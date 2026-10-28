import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from demo.bug_lab import agents, server
from demo.bug_lab.llm import Backend, detect_backend
from tests.test_bug_lab import fake_browser, make_app


def test_explicit_model_startup_failure_never_reports_successful_backend(tmp_path, monkeypatch, capsys):
    def unavailable(request):
        raise httpx.ConnectError("synthetic-startup-key", request=request)

    async def detect(choice, base_url, model, api_key, *, timeout):
        return await detect_backend(
            choice, base_url, model, api_key, timeout=timeout,
            transport=httpx.MockTransport(unavailable),
        )

    monkeypatch.setattr(server, "detect_backend", detect)
    app = server.create_app(server.LabConfig(
        workdir=tmp_path / "lab", agents="llm", llm_base_url="http://model.test/v1",
        llm_model="test", llm_api_key="synthetic-startup-key",
    ))
    with pytest.raises(RuntimeError, match="did not answer") as error:
        with TestClient(app):
            pytest.fail("An unavailable explicit model must not start")
    assert "Backend:" not in capsys.readouterr().out
    assert "synthetic-startup-key" not in str(error.value)
    assert app.state.lab._monitor_task is None


def test_reset_clears_current_incident_without_erasing_cancelled_evidence(tmp_path):
    lab = make_app(tmp_path).state.lab

    async def run():
        lab.inject("broken-image-paths")
        await lab.monitor_once()
        incident_id = lab.incident["id"]
        await lab.reset()
        state = lab.snapshot()
        assert state["incident"] is None
        assert state["incident_state"] == "idle"
        assert state["incident_sequence"] == 1
        assert state["history"][0]["id"] == incident_id
        assert state["history"][0]["status"] == "cancelled"
        record = json.loads(lab.log_path.read_text())
        assert record["id"] == incident_id and record["status"] == "cancelled"

    asyncio.run(run())


def test_new_injection_cannot_be_mistaken_for_previous_closed_incident(tmp_path):
    lab = make_app(tmp_path).state.lab

    async def run():
        browser = asyncio.create_task(fake_browser(lab, "broken-image-paths"))
        try:
            lab.inject("broken-image-paths")
            await lab.monitor_once()
            await lab._pipeline
        finally:
            browser.cancel()
        previous = lab.incident["id"]
        evidence = lab.log_path.read_bytes()
        assert lab.snapshot()["incident_state"] == "closed"
        lab.inject("layout-overflow")
        pending = lab.snapshot()
        assert pending["incident"] is None
        assert pending["incident_state"] == "pending"
        assert pending["incident_sequence"] == 1
        assert pending["history"][0]["id"] == previous
        assert lab.log_path.read_bytes() == evidence
        lab.record_browser_report({
            "version": lab.workspace.version(),
            "checks": {"overflow_px": 2000}, "issues": [],
        })
        await lab.monitor_once()
        try:
            current = lab.snapshot()
            assert current["incident_state"] == "active"
            assert current["incident_sequence"] == 2
            assert current["incident"]["id"] != previous
        finally:
            await lab.stop()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["diagnosis", "patch"])
def test_model_outage_logs_safe_fallback_and_preserves_rule_attribution(tmp_path, caplog, phase):
    lab = make_app(tmp_path).state.lab
    secret = "synthetic-outage-key"
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        assert request.headers["Authorization"] == f"Bearer {secret}"
        if phase == "diagnosis" or calls > 1:
            raise httpx.ConnectError(
                f"provider failure {secret} http://user:password@host/?token=private",
                request=request,
            )
        return httpx.Response(200, json={
            "model": "qwen2.5:7b-instruct",
            "choices": [{"finish_reason": "stop", "message": {
                "content": "FILE: index.html\nROOT_CAUSE: The stylesheet path is wrong.",
            }}],
        })

    lab.backend = Backend(
        mode="llm", label="qwen2.5:7b-instruct", model="qwen2.5:7b-instruct",
        api_key=secret, base_url="http://model.test/v1", transport=httpx.MockTransport(handler),
    )

    async def run():
        browser = asyncio.create_task(fake_browser(lab, "broken-stylesheet"))
        try:
            lab.inject("broken-stylesheet")
            await lab.monitor_once()
            await lab._pipeline
        finally:
            browser.cancel()
        assert lab.incident["status"] == "resolved"
        assert lab.incident["resolved_by"] == agents.RULES_AUTHOR
        assert lab.incident["attempts"][-1]["patch"]["author"] == agents.RULES_AUTHOR

    with caplog.at_level(logging.WARNING, logger="atlasops.bug_lab"):
        asyncio.run(run())
    assert "rule engine" in caplog.text.lower()
    assert "connection failed" in caplog.text
    assert "INC-0001" in caplog.text
    assert secret not in caplog.text
    assert "password" not in caplog.text and "token=private" not in caplog.text
    assert secret not in lab.log_path.read_text()


def test_unknown_model_exception_never_reaches_logs_or_evidence(tmp_path, caplog, monkeypatch):
    lab = make_app(tmp_path).state.lab
    lab.backend = Backend(mode="llm", label="Model", model="test", api_key="synthetic-key")

    async def fail(*args):
        raise RuntimeError("synthetic-key https://user:password@host/v1?token=private")

    monkeypatch.setattr(agents, "diagnose_llm", fail)

    async def run():
        browser = asyncio.create_task(fake_browser(lab, "broken-image-paths"))
        try:
            lab.inject("broken-image-paths")
            await lab.monitor_once()
            await lab._pipeline
        finally:
            browser.cancel()
        assert lab.incident["resolved_by"] == agents.RULES_AUTHOR

    with caplog.at_level(logging.WARNING, logger="atlasops.bug_lab"):
        asyncio.run(run())
    text = caplog.text + lab.log_path.read_text()
    assert "rule engine" in text.lower()
    assert "synthetic-key" not in text
    assert "password" not in text and "token=private" not in text
