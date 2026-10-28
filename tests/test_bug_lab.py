"""Bug lab contracts: injection, objective checks, agent repairs, approvals and the HTTP surface.

Browser reports here are test doubles shaped like the in-page monitor's output.
The real monitor runs only in a browser; these tests do not establish that it
detects anything in a real page.
"""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from demo.bug_lab import agents, server
from demo.bug_lab.catalog import BUGS, BUGS_BY_ID
from demo.bug_lab.checks import browser_checks, failing, server_checks
from demo.bug_lab.engine import ApprovalError, LabBusy, LabOptions
from demo.bug_lab.llm import RULES, Backend, detect_backend
from demo.bug_lab.server import LabConfig, create_app
from demo.bug_lab.workspace import EDITABLE_FILES, MONITOR_TAG, Workspace

CLEAN = {"stylesheet_applied": True, "products_rendered": 8, "nan_prices": 0, "broken_images": [],
         "overflow_px": 0, "add_to_cart": "ok", "api_ms": 20}

# What the in-page monitor reports in a real browser for each bug that only a browser can see.
BROWSER_SYMPTOMS = {
    "js-reference-error": ({"products_rendered": 0, "add_to_cart": "skipped"},
                           [{"kind": "console_error", "message": "ReferenceError: renderProdcuts is not defined"}]),
    "cart-handler": ({"add_to_cart": "failed"},
                     [{"kind": "js_error", "message": "TypeError: Cannot set properties of null (setting 'textContent')"}]),
    "layout-overflow": ({"overflow_px": 1980}, []),
    "broken-stylesheet": ({"stylesheet_applied": False},
                          [{"kind": "resource", "message": "link failed to load: /shop/style.css"}]),
}


def make_app(tmp_path, **options):
    defaults = {"step_delay": 0, "verify_timeout": 2.0, "monitor_interval": 0.05, "approval_timeout": 5.0}
    config = LabConfig(workdir=tmp_path / "lab", options=LabOptions(**(defaults | options)))
    return create_app(config, backend=RULES)


def test_store_response_remains_a_snapshot_during_reset(tmp_path):
    app = make_app(tmp_path)
    lab = app.state.lab
    lab.inject("broken-stylesheet")
    before = lab.workspace.read("index.html").encode()

    async def run():
        headers_sent = asyncio.Event()
        resume = asyncio.Event()

        async def paused_app(scope, receive, send):
            async def paused_send(message):
                if message["type"] == "http.response.start":
                    headers_sent.set()
                    await resume.wait()
                await send(message)

            await app(scope, receive, paused_send)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=paused_app), base_url="http://127.0.0.1",
        ) as client:
            request = asyncio.create_task(client.get("/shop/"))
            try:
                await asyncio.wait_for(headers_sent.wait(), timeout=2)
                await lab.reset()
            finally:
                resume.set()
            response = await request
        assert response.status_code == 200
        assert response.content == before
        assert int(response.headers["content-length"]) == len(before)
        assert lab.workspace.changed_files() == []

    asyncio.run(run())


def browser_report(lab, bug_id=None):
    checks, issues = dict(CLEAN), []
    if bug_id in BROWSER_SYMPTOMS:
        changes, issues = BROWSER_SYMPTOMS[bug_id]
        checks.update(changes)
    return {"version": lab.workspace.version(), "checks": checks, "issues": issues}


async def fake_browser(lab, bug_id):
    """Reload on every version change and report what a visitor would see."""
    seen = None
    while True:
        version = lab.workspace.version()
        if version != seen:
            seen = version
            broken = BUGS_BY_ID[bug_id].file in lab.workspace.changed_files()
            lab.record_browser_report(browser_report(lab, bug_id if broken else None))
        await asyncio.sleep(0.02)


async def wait_for(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


@pytest.mark.parametrize("bug", BUGS, ids=lambda bug: bug.id)
def test_each_bug_edits_exactly_its_own_file(tmp_path, bug):
    workspace = Workspace(tmp_path)
    workspace.write(bug.file, bug.mutate(workspace.read(bug.file)))
    assert workspace.changed_files() == [bug.file]
    assert MONITOR_TAG in workspace.read("index.html")


def test_startup_refuses_existing_workdir_without_deleting_user_data(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    sentinel = site / "owner-data.txt"
    sentinel.write_text("preserve this data")
    with pytest.raises(ValueError, match="empty"):
        create_app(LabConfig(workdir=tmp_path), backend=RULES)
    assert sentinel.read_text() == "preserve this data"


def test_foreign_host_cannot_mutate_the_loopback_lab(tmp_path):
    with TestClient(make_app(tmp_path), base_url="http://127.0.0.1") as client:
        response = client.post("/lab/api/inject", json={"bug": "slow-api"},
                               headers={"X-Bug-Lab": "1", "Host": "attacker.example"})
        assert response.status_code == 400
        assert client.get("/lab/api/state").json()["site"]["changed_files"] == []


def test_foreign_origin_cannot_mutate_the_loopback_lab(tmp_path):
    with TestClient(make_app(tmp_path), base_url="http://127.0.0.1") as client:
        response = client.post("/lab/api/inject", json={"bug": "slow-api"},
                               headers={"X-Bug-Lab": "1", "Origin": "http://attacker.example"})
        assert response.status_code == 403
        assert client.get("/lab/api/state").json()["site"]["changed_files"] == []


def test_stale_browser_report_cannot_verify_a_restored_version(tmp_path):
    lab = make_app(tmp_path).state.lab
    version = lab.workspace.version()
    lab.record_browser_report(browser_report(lab))
    assert lab.browser[version]["received_at"] > 0

    async def run():
        assert await lab._wait_for_browser(version, after=lab.browser[version]["received_at"]) is None

    asyncio.run(run())


def test_verifier_exception_reverts_the_applied_patch(tmp_path, monkeypatch):
    lab = make_app(tmp_path).state.lab
    lab.options.step_delay = 0

    async def exploding_verifier(_incident):
        raise RuntimeError("verifier unavailable")

    monkeypatch.setattr(lab, "_verify", exploding_verifier)

    async def run():
        lab.inject("broken-image-paths")
        injected = lab.workspace.read("products.json")
        await lab.monitor_once()
        await lab._pipeline
        assert lab.incident["status"] == "escalated"
        assert lab.workspace.read("products.json") == injected

    asyncio.run(run())


@pytest.mark.parametrize("products", [{"products": 42}, {"products": ["bad"]}, [1], {"products": {}}])
def test_invalid_product_schema_is_a_failed_check_not_an_exception(tmp_path, products):
    app = make_app(tmp_path)
    app.state.lab.workspace.write("products.json", json.dumps(products))

    async def run():
        checks = await app.state.lab.run_server_checks()
        assert next(check for check in checks if check.name == "products_api").ok is False

    asyncio.run(run())


def test_body_limit_stops_reading_before_buffering_all_chunks():
    from fastapi import HTTPException, Request

    consumed = []
    chunks = iter([b"x" * 8193, b"y" * 100_000])

    async def receive():
        chunk = next(chunks)
        consumed.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def run():
        request = Request({"type": "http", "method": "POST", "headers": []}, receive)
        with pytest.raises(HTTPException) as caught:
            await server._json_body(request)
        assert caught.value.status_code == 413
        assert len(consumed) == 1

    asyncio.run(run())


def test_pristine_store_passes_every_check(tmp_path):
    app = make_app(tmp_path)

    async def run():
        checks = await app.state.lab.run_server_checks()
        assert [check.name for check in checks if check.ok is not True] == []
        assert all(check.ok for check in browser_checks({"checks": CLEAN, "issues": []}))

    asyncio.run(run())


@pytest.mark.parametrize("bug_id, check_name", [
    ("broken-stylesheet", "stylesheets_load"),
    ("truncated-data", "products_api"),
    ("string-prices", "prices_numeric"),
    ("broken-image-paths", "images_load"),
    ("slow-api", "api_fast"),
])
def test_server_checks_catch_server_visible_bugs(tmp_path, monkeypatch, bug_id, check_name):
    monkeypatch.setattr(server, "_sleep", lambda seconds: asyncio.sleep(seconds / 20))
    app = make_app(tmp_path, slow_api_ms=150)
    lab = app.state.lab
    lab.inject(bug_id)

    async def run():
        names = [check.name for check in failing(await lab.run_server_checks())]
        assert check_name in names

    asyncio.run(run())


@pytest.mark.parametrize("bug", BUGS, ids=lambda bug: bug.id)
def test_rule_agents_diagnose_and_repair_every_bug(tmp_path, monkeypatch, bug):
    monkeypatch.setattr(server, "_sleep", lambda seconds: asyncio.sleep(seconds / 20))
    app = make_app(tmp_path, slow_api_ms=150)
    lab = app.state.lab
    lab.inject(bug.id)

    async def run():
        async with lab.client_factory() as client:
            problems = failing(await server_checks(client, slow_api_ms=150))
        problems += failing(browser_checks(browser_report(lab, bug.id)))
        assert problems, "the bug must be observable"
        diagnosis = agents.diagnose_rules(problems, lab.workspace)
        assert diagnosis is not None and diagnosis.file == bug.file
        patch = agents.remediate_rules(diagnosis, lab.workspace)
        after, _ = agents.safety_check(lab.workspace, patch)
        lab.workspace.write(patch.file, after)
        pristine = lab.workspace.pristine(bug.file)
        if bug.file.endswith(".json"):
            assert json.loads(after) == json.loads(pristine)
        else:
            assert after == pristine
        async with lab.client_factory() as client:
            assert failing(await server_checks(client, slow_api_ms=150)) == []

    asyncio.run(run())


def test_reference_errors_are_read_in_safari_wording_too(tmp_path):
    workspace = Workspace(tmp_path)
    bug = BUGS_BY_ID["js-reference-error"]
    workspace.write(bug.file, bug.mutate(workspace.read(bug.file)))
    report = {"checks": dict(CLEAN, products_rendered=0),
              "issues": [{"kind": "console_error", "message": "ReferenceError: Can't find variable: renderProdcuts"}]}
    diagnosis = agents.diagnose_rules(failing(browser_checks(report)), workspace)
    assert diagnosis.data == {"fix": "rename_call", "old": "renderProdcuts", "new": "renderProducts"}


def test_triage_uses_atlasops_severities():
    report = {"checks": dict(CLEAN, add_to_cart="failed", overflow_px=900), "issues": []}
    severity, title, _ = agents.triage(failing(browser_checks(report)))
    assert (severity, title) == ("P1", "Add to cart is broken")
    severity, _, _ = agents.triage(failing(browser_checks({"checks": dict(CLEAN, overflow_px=900), "issues": []})))
    assert severity == "P3"


@pytest.mark.parametrize("edits, message", [
    ([{"find": MONITOR_TAG, "replace": ""}], "monitor script"),
    ([{"find": "<", "replace": "&lt;"}], "matches"),
    ([{"find": "not in the file", "replace": "x"}], "not found"),
    ([{"find": "", "replace": "x"}], "empty"),
])
def test_safety_check_refuses_unsafe_or_ambiguous_patches(tmp_path, edits, message):
    workspace = Workspace(tmp_path)
    with pytest.raises(agents.PatchError, match=message):
        agents.safety_check(workspace, agents.Patch("index.html", edits, "", "test"))


def test_safety_check_refuses_files_outside_the_store_and_invalid_json(tmp_path):
    workspace = Workspace(tmp_path)
    with pytest.raises(agents.PatchError, match="not an editable"):
        agents.safety_check(workspace, agents.Patch("../server.py", [{"find": "a", "replace": "b"}], "", "t"))
    with pytest.raises(agents.PatchError, match="valid JSON"):
        agents.safety_check(workspace, agents.Patch("config.json", [{"find": '"api_delay_ms": 0', "replace": '"api_delay_ms": '}], "", "t"))


def test_p3_bug_is_fixed_automatically_and_verified(tmp_path):
    lab = make_app(tmp_path).state.lab

    async def run():
        browser = asyncio.create_task(fake_browser(lab, "broken-image-paths"))
        await asyncio.sleep(0.05)
        lab.inject("broken-image-paths")
        await lab.monitor_once()
        incident = lab.incident
        await asyncio.wait_for(lab._pipeline, 5)
        browser.cancel()
        assert incident["severity"] == "P3" and incident["status"] == "resolved"
        assert incident["approval"]["decision"] == "automatic"
        assert incident["resolved_by"] == agents.RULES_AUTHOR
        assert incident["diagnosis_matched"] is True
        assert lab.workspace.changed_files() == []
        assert lab.history[0]["injected"] == ["Broken image paths"]
        agents_seen = [event["agent"] for event in incident["events"]]
        for agent in ("Monitor", "Triage agent", "Diagnosis agent", "Remediation agent",
                      "Approval gate", "Verifier", "Comms agent"):
            assert agent in agents_seen

    asyncio.run(run())


def test_p1_fix_waits_for_approval_of_the_exact_change(tmp_path):
    lab = make_app(tmp_path).state.lab

    async def run():
        browser = asyncio.create_task(fake_browser(lab, "cart-handler"))
        await asyncio.sleep(0.05)
        lab.inject("cart-handler")
        await wait_for(lambda: lab.workspace.version() in lab.browser)
        await lab.monitor_once()
        incident = lab.incident
        await wait_for(lambda: incident["status"] == "awaiting_approval")
        assert incident["severity"] == "P1"
        assert "cart-cuont" in lab.workspace.read("app.js"), "nothing may change before approval"
        digest = incident["approval"]["digest"]
        with pytest.raises(ApprovalError, match="different change"):
            lab.decide(incident["id"], "0" * 64, "approve")
        lab.decide(incident["id"], digest, "approve")
        with pytest.raises(ApprovalError):
            lab.decide(incident["id"], digest, "approve")
        await asyncio.wait_for(lab._pipeline, 5)
        browser.cancel()
        assert incident["status"] == "resolved"
        assert lab.workspace.read("app.js") == lab.workspace.pristine("app.js")

    asyncio.run(run())


@pytest.mark.parametrize("decision", ["reject", None])
def test_rejected_or_expired_approval_never_changes_the_store(tmp_path, decision):
    lab = make_app(tmp_path, approval_timeout=0.3).state.lab

    async def run():
        lab.inject("string-prices")
        await lab.monitor_once()
        incident = lab.incident
        await wait_for(lambda: incident["status"] == "awaiting_approval")
        broken = lab.workspace.read("products.json")
        if decision:
            lab.decide(incident["id"], incident["approval"]["digest"], decision)
        await asyncio.wait_for(lab._pipeline, 5)
        assert incident["status"] == "escalated"
        assert incident["approval"]["decision"] == ("rejected" if decision else "timeout")
        assert lab.workspace.read("products.json") == broken
        await lab.monitor_once()
        assert lab.incident is incident, "a blocked version must not reopen in a loop"

    asyncio.run(run())


def test_approve_every_fix_setting_holds_p3_fixes_too(tmp_path):
    lab = make_app(tmp_path).state.lab
    lab.require_approval_all = True

    async def run():
        lab.inject("broken-image-paths")
        await lab.monitor_once()
        incident = lab.incident
        await wait_for(lambda: incident["status"] == "awaiting_approval")
        assert incident["severity"] == "P3"
        assert incident["approval"]["policy"] == "You asked to approve every fix"
        lab.decide(incident["id"], incident["approval"]["digest"], "reject")
        await asyncio.wait_for(lab._pipeline, 5)
        assert incident["status"] == "escalated"
        assert "img/" in lab.workspace.read("products.json")

    asyncio.run(run())


def test_rollback_policy_runs_when_no_agent_patch_exists(tmp_path, monkeypatch):
    lab = make_app(tmp_path).state.lab
    monkeypatch.setattr(agents, "remediate_rules", lambda diagnosis, workspace: None)

    async def run():
        browser = asyncio.create_task(fake_browser(lab, "layout-overflow"))
        await asyncio.sleep(0.05)
        lab.inject("layout-overflow")
        await wait_for(lambda: lab.workspace.version() in lab.browser)
        await lab.monitor_once()
        await asyncio.wait_for(lab._pipeline, 5)
        browser.cancel()
        assert lab.incident["resolved_by"] == "Rollback policy (not AI)"
        assert lab.incident["patch"]["kind"] == "rollback"
        assert lab.workspace.changed_files() == []

    asyncio.run(run())


def test_missing_browser_evidence_is_reported_as_unverified(tmp_path):
    lab = make_app(tmp_path, verify_timeout=0.2).state.lab

    async def run():
        lab.inject("broken-image-paths")
        await lab.monitor_once()
        await asyncio.wait_for(lab._pipeline, 5)
        assert lab.incident["status"] == "unverified"
        assert lab.workspace.changed_files() == [], "the fix stays applied; only its verification is missing"

    asyncio.run(run())


def test_injection_is_refused_while_a_bug_or_incident_is_pending(tmp_path):
    lab = make_app(tmp_path).state.lab
    lab.inject("slow-api")
    with pytest.raises(LabBusy):
        lab.inject("broken-stylesheet")
    asyncio.run(lab.reset())
    assert lab.injected == [] and lab.workspace.changed_files() == []
    with pytest.raises(ValueError, match="Unknown bug"):
        lab.inject("no-such-bug")


def test_reset_drains_the_monitor_before_restoring_store_files(tmp_path, monkeypatch):
    lab = make_app(tmp_path).state.lab

    async def run():
        started, closed = asyncio.Event(), asyncio.Event()

        async def reader():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        lab._monitor_task = asyncio.create_task(reader())
        await started.wait()
        restore = lab.workspace.reset

        def checked_restore():
            assert closed.is_set(), "Reset must first close active monitor reads"
            restore()

        monkeypatch.setattr(lab.workspace, "reset", checked_restore)
        await lab.reset()
        await lab.stop()

    asyncio.run(run())


def fake_model(answers):
    calls = []

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:7b-instruct"}]})
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "qwen2.5:7b-instruct",
                                        "choices": [{"finish_reason": "stop",
                                                     "message": {"content": answers[len(calls) - 1]}}]})

    return httpx.MockTransport(handler), calls


def test_model_backend_diagnoses_and_patches_through_the_safety_check(tmp_path):
    transport, calls = fake_model([
        "FILE: index.html\nROOT_CAUSE: The stylesheet link points to style.css, which does not exist.",
        'FIND:\n<link rel="stylesheet" href="style.css">\nREPLACE:\n<link rel="stylesheet" href="styles.css">\nEND\n'
        "EXPLANATION: Link the stylesheet that exists.",
    ])
    lab = make_app(tmp_path).state.lab

    async def run():
        lab.backend = await detect_backend("auto", "http://model.test/v1", None, transport=transport)
        assert lab.backend.mode == "llm"
        browser = asyncio.create_task(fake_browser(lab, "broken-stylesheet"))
        await asyncio.sleep(0.05)
        lab.inject("broken-stylesheet")
        await lab.monitor_once()
        await asyncio.wait_for(lab._pipeline, 5)
        browser.cancel()
        assert lab.incident["status"] == "resolved"
        assert lab.incident["resolved_by"] == "qwen2.5:7b-instruct (model)"
        assert "style.css" in calls[0]["messages"][1]["content"]
        assert "broken-stylesheet" not in json.dumps(calls), "the model must not see the injected bug id"

    asyncio.run(run())


def test_unsafe_model_patch_falls_back_to_the_rule_engine(tmp_path):
    transport, _ = fake_model([
        "FILE: index.html\nROOT_CAUSE: Bad stylesheet link.",
        f"FIND:\n{MONITOR_TAG}\nREPLACE:\n\nEND\nEXPLANATION: Remove the monitor.",
    ])
    lab = make_app(tmp_path).state.lab

    async def run():
        lab.backend = await detect_backend("llm", "http://model.test/v1", None, transport=transport)
        browser = asyncio.create_task(fake_browser(lab, "broken-stylesheet"))
        await asyncio.sleep(0.05)
        lab.inject("broken-stylesheet")
        await lab.monitor_once()
        await asyncio.wait_for(lab._pipeline, 5)
        browser.cancel()
        texts = " ".join(event["text"] for event in lab.incident["events"])
        assert "would remove the monitor script" in texts
        assert lab.incident["resolved_by"] == agents.RULES_AUTHOR
        assert MONITOR_TAG in lab.workspace.read("index.html")

    asyncio.run(run())


def test_auto_backend_falls_back_to_rules_when_no_model_answers():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    backend = asyncio.run(detect_backend("auto", "http://model.test/v1", None,
                                         transport=httpx.MockTransport(refuse)))
    assert backend.mode == "rules" and "No model endpoint answered" in backend.note


def test_model_timeout_reaches_the_backend_and_the_command_line(monkeypatch):
    def models(request):
        return httpx.Response(200, json={"data": [{"id": "slow-model"}]})

    backend = asyncio.run(detect_backend("llm", "http://model.test/v1", "slow-model",
                                         transport=httpx.MockTransport(models), timeout=600.0))
    assert backend.timeout == 600.0
    default = asyncio.run(detect_backend("llm", "http://model.test/v1", "slow-model",
                                         transport=httpx.MockTransport(models)))
    assert default.timeout == 120.0

    from demo.bug_lab import __main__ as cli

    captured = {}
    monkeypatch.setattr(cli, "create_app", lambda config: captured.setdefault("config", config))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: None)
    cli.main(["--llm-timeout", "900", "--agents", "rules"])
    assert captured["config"].llm_timeout == 900.0


def test_http_surface_serves_both_pages_and_guards_writes(tmp_path):
    with TestClient(make_app(tmp_path), base_url="http://127.0.0.1") as client:
        control = client.get("/")
        assert control.status_code == 200 and "AtlasOps Bug Lab" in control.text
        assert "default-src 'self'" in control.headers["content-security-policy"]
        store = client.get("/shop/")
        assert store.status_code == 200 and MONITOR_TAG in store.text
        assert client.get("/shop/api/products").json()["products"]
        for path in ("/shop/../server.py", "/shop/%2e%2e/server.py", "/shop/.lab", "/lab/static/monitor.js"):
            assert client.get(path).status_code == 404, path
        assert client.post("/lab/api/inject", json={"bug": "slow-api"}).status_code == 403
        headers = {"X-Bug-Lab": "1"}
        assert client.post("/lab/api/inject", json={"bug": "slow-api"}, headers=headers).status_code == 202
        assert client.post("/lab/api/inject", json={"bug": "slow-api"}, headers=headers).status_code == 409
        assert client.post("/lab/api/telemetry", json={"checks": {}}, headers=headers).status_code == 400
        state = client.get("/lab/api/state").json()
        assert {bug["id"] for bug in state["catalog"]} == set(BUGS_BY_ID)
        assert state["injected"][0]["id"] == "slow-api"
        assert set(state["site"]["changed_files"]) <= set(EDITABLE_FILES)
        assert client.post("/lab/api/reset", headers=headers).json() == {"reset": True}
        assert client.get("/lab/api/state").json()["site"]["changed_files"] == []


def test_failed_patch_is_reverted_then_gated_rollback_retains_each_attempt(tmp_path, monkeypatch):
    lab = make_app(tmp_path).state.lab
    lab.require_approval_all = True
    original = agents.remediate_rules

    def ineffective(diagnosis, workspace):
        patch = original(diagnosis, workspace)
        patch.edits = [{"find": "width: 2600px;", "replace": "width: 2500px;"}]
        return patch

    monkeypatch.setattr(agents, "remediate_rules", ineffective)

    async def run():
        lab.inject("layout-overflow")
        broken = lab.workspace.read("styles.css")
        lab.record_browser_report(browser_report(lab, "layout-overflow"))
        await lab.monitor_once()
        await wait_for(lambda: lab.incident["status"] == "awaiting_approval")
        first_digest = lab.incident["approval"]["digest"]
        lab.decide(lab.incident["id"], first_digest, "approve")
        await wait_for(lambda: "2500px" in lab.workspace.read("styles.css"))
        lab.record_browser_report(browser_report(lab, "layout-overflow"))
        await wait_for(lambda: lab.incident["patch"]["kind"] == "rollback"
                       and lab.incident["status"] == "awaiting_approval")
        assert lab.workspace.read("styles.css") == broken
        rollback_digest = lab.incident["approval"]["digest"]
        assert rollback_digest != first_digest
        with pytest.raises(ApprovalError, match="different change"):
            lab.decide(lab.incident["id"], first_digest, "approve")
        lab.decide(lab.incident["id"], rollback_digest, "approve")
        await wait_for(lambda: lab.workspace.changed_files() == [])
        lab.record_browser_report(browser_report(lab))
        await lab._pipeline
        assert lab.incident["status"] == "resolved"
        assert lab.incident["resolved_by"] == "Rollback policy (not AI)"
        record = json.loads(lab.log_path.read_text().splitlines()[-1])
        failed, rollback = record["attempts"]
        assert failed["verdict"] == "failed"
        assert failed["patch"]["digest"] == first_digest
        assert failed["approval"]["decision"] == "approved"
        assert "2500px" in "".join(failed["patch"]["diff"])
        assert any(check["ok"] is False for check in failed["verification"]["checks"])
        assert rollback["patch"]["digest"] == rollback_digest
        assert rollback["verification"]["verdict"] == "verified"
        assert record["evidence_classification"] == "LOCAL_DEMONSTRATION_NOT_SCIENTIFIC"

    asyncio.run(run())


def test_export_is_downloadable_and_never_contains_model_credentials(tmp_path):
    app = make_app(tmp_path)
    app.state.lab.backend.api_key = "synthetic-test-secret"
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.get("/lab/api/evidence")
            assert response.status_code == 200
            assert "attachment" in response.headers["content-disposition"]
            assert response.content == b""
            assert client.get("/lab/api/state").json()["backend"].get("api_key") is None
            assert "synthetic-test-secret" not in response.text
    finally:
        app.state.lab.backend.api_key = ""


def test_stale_telemetry_is_not_used_to_open_an_incident(tmp_path):
    lab = make_app(tmp_path).state.lab
    lab.record_browser_report(browser_report(lab, "cart-handler"))
    lab.latest_browser["received_at"] -= 60
    asyncio.run(lab.monitor_once())
    assert lab.incident is None
    assert lab.snapshot()["checks"]["browser_stale"] is True


def test_version_change_during_verification_cannot_resolve(tmp_path, monkeypatch):
    lab = make_app(tmp_path, verify_timeout=0.2).state.lab
    real_checks = lab.run_server_checks

    async def change_after_checks():
        checks = await real_checks()
        lab.workspace.write("config.json", '{"api_delay_ms": 4000}')
        return checks

    async def run():
        incident = {"opened_at": 0, "events": [], "applied_at": 0}
        lab.record_browser_report(browser_report(lab))
        monkeypatch.setattr(lab, "run_server_checks", change_after_checks)
        assert await lab._verify(incident) == "failed"

    asyncio.run(run())


def test_authenticated_wrong_model_completion_is_not_attributed_as_a_model_fix(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-auth-key"
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:7b-instruct"}]})
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={
            "model": "another-model",
            "choices": [{"finish_reason": "stop", "message": {
                "content": "FILE: index.html\nROOT_CAUSE: Bad stylesheet link."}}],
        })

    lab = make_app(tmp_path).state.lab
    diagnosis_backends = []
    real_event = lab._event

    def record_event(incident, stage, kind, text, detail=""):
        if stage == "diagnosis" and kind == "tool":
            diagnosis_backends.append(incident["active_backend"])
        real_event(incident, stage, kind, text, detail)

    lab._event = record_event

    async def run():
        lab.backend = await detect_backend(
            "llm", "http://model.test/v1", None, api_key="synthetic-auth-key",
            transport=httpx.MockTransport(handler),
        )
        browser = asyncio.create_task(fake_browser(lab, "broken-stylesheet"))
        try:
            lab.inject("broken-stylesheet")
            await lab.monitor_once()
            await lab._pipeline
        finally:
            browser.cancel()
        assert lab.incident["status"] == "resolved"
        assert lab.incident["resolved_by"] == agents.RULES_AUTHOR
        assert lab.incident["diagnosis"]["author"] == agents.RULES_AUTHOR
        assert diagnosis_backends and set(diagnosis_backends) == {agents.RULES_AUTHOR}
        assert any("Model fallback: invalid response" in event["text"]
                   for event in lab.incident["events"])
        assert "synthetic-auth-key" not in lab.log_path.read_text()
        assert len(requests) == 2

    asyncio.run(run())


@pytest.mark.parametrize("checks, issues", [
    (dict(CLEAN, add_to_cart=[]), []),
    (dict(CLEAN, broken_images=42), []),
    (dict(CLEAN, products_rendered=True), []),
    (dict(CLEAN, overflow_px=-1), []),
    (CLEAN, 42),
])
def test_invalid_telemetry_is_rejected_at_the_api_boundary(tmp_path, checks, issues):
    with TestClient(make_app(tmp_path), base_url="http://127.0.0.1") as client:
        version = client.get("/lab/api/site-version").json()["version"]
        response = client.post("/lab/api/telemetry", headers={"X-Bug-Lab": "1"},
                               json={"version": version, "checks": checks, "issues": issues})
        assert response.status_code == 400
        assert client.get("/lab/api/state").status_code == 200


def test_reset_before_triage_closes_incident_and_restores_store(tmp_path):
    lab = make_app(tmp_path).state.lab

    async def run():
        lab.inject("broken-image-paths")
        await lab.monitor_once()
        assert lab.incident["severity"] is None
        incident = lab.incident
        await lab.reset()
        assert incident["status"] == "cancelled"
        assert lab.incident is None
        assert lab.workspace.changed_files() == []
        assert json.loads(lab.log_path.read_text())["status"] == "cancelled"

    asyncio.run(run())


def test_stale_prior_report_then_fresh_failure_still_reaches_rule_repair(tmp_path):
    lab = make_app(tmp_path).state.lab
    real_stage = lab._stage

    async def run():
        verifying = asyncio.Event()

        def stage(incident, name, backend):
            real_stage(incident, name, backend)
            if name == "verification":
                verifying.set()

        lab._stage = stage
        lab.inject("layout-overflow")
        lab.record_browser_report(browser_report(lab, "layout-overflow"))
        await lab.monitor_once()
        lab.latest_browser["received_at"] -= 60

        async def delayed_browser():
            await verifying.wait()
            lab.record_browser_report(browser_report(lab, "layout-overflow"))
            seen = lab.workspace.version()
            while True:
                version = lab.workspace.version()
                if version != seen:
                    seen = version
                    lab.record_browser_report(browser_report(lab))
                await asyncio.sleep(0.02)

        browser = asyncio.create_task(delayed_browser())
        try:
            await lab._pipeline
        finally:
            browser.cancel()
        assert lab.incident["status"] == "resolved"
        assert lab.incident["resolved_by"] == agents.RULES_AUTHOR
        assert lab.workspace.changed_files() == []
        assert any(event["text"] == "Not resolved: checks still fail."
                   for event in lab.incident["events"])
        assert lab.incident["attempts"][-1]["verdict"] == "verified"

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["diagnosis", "patch"])
def test_credential_echo_is_rejected_before_nonempty_incident_export(tmp_path, phase):
    app = make_app(tmp_path)
    lab = app.state.lab
    secret = "synthetic-credential-never-export"
    calls = []

    def handler(request):
        assert request.headers["authorization"] == f"Bearer {secret}"
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:7b-instruct"}]})
        calls.append(request)
        answer = "FILE: index.html\nROOT_CAUSE: The stylesheet path is wrong."
        if phase == "diagnosis":
            answer += f" {secret}"
        elif len(calls) == 2:
            answer = ('FIND:\n<link rel="stylesheet" href="style.css">\nREPLACE:\n'
                      '<link rel="stylesheet" href="styles.css">\nEND\n'
                      f"EXPLANATION: Restore styles. {secret}")
        return httpx.Response(200, json={
            "model": "qwen2.5:7b-instruct", "choices": [
                {"finish_reason": "stop", "message": {"content": answer}},
            ],
        })

    async def run():
        lab.backend = await detect_backend(
            "llm", "http://model.test/v1", None, api_key=secret,
            transport=httpx.MockTransport(handler),
        )
        browser = asyncio.create_task(fake_browser(lab, "broken-stylesheet"))
        try:
            lab.inject("broken-stylesheet")
            await lab.monitor_once()
            await lab._pipeline
        finally:
            browser.cancel()
        assert lab.incident["status"] == "resolved"
        assert lab.incident["resolved_by"] == agents.RULES_AUTHOR

    asyncio.run(run())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        exported = client.get("/lab/api/evidence")
        assert exported.status_code == 200 and exported.content
        assert secret not in exported.text
        assert secret not in client.get("/lab/api/state").text
        record = json.loads(exported.text.splitlines()[0])
        assert record["attempts"][-1]["patch"]["author"] == agents.RULES_AUTHOR


def test_direct_backend_label_is_redacted_in_active_api_state(tmp_path):
    import time

    secret = "synthetic-direct-backend-secret"

    async def stalled_model(request):
        assert request.headers["authorization"] == f"Bearer {secret}"
        await asyncio.sleep(300)

    backend = Backend(
        mode="llm", label=f"Model using {secret}", model="qwen2.5:7b-instruct",
        base_url="http://model.test/v1", api_key=secret,
        transport=httpx.MockTransport(stalled_model),
    )
    app = create_app(LabConfig(workdir=tmp_path / "lab", options=LabOptions(
        step_delay=0, monitor_interval=0.01,
    )), backend=backend)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post("/lab/api/inject", json={"bug": "broken-image-paths"},
                    headers={"X-Bug-Lab": "1"})
        for _ in range(100):
            response = client.get("/lab/api/state")
            incident = response.json()["incident"]
            if incident and incident["stage"] == "diagnosis":
                assert secret not in response.text
                assert "[redacted]" in incident["active_backend"]
                break
            time.sleep(0.01)
        else:
            raise AssertionError("The model call did not reach active diagnosis")


@pytest.mark.parametrize("decision", ["reject", None])
def test_blocked_rollback_has_no_verification_from_the_previous_patch(tmp_path, monkeypatch, decision):
    lab = make_app(tmp_path, approval_timeout=0.5).state.lab
    lab.require_approval_all = True

    def ineffective(diagnosis, workspace):
        return agents.Patch("styles.css", [
            {"find": "width: 2600px;", "replace": "width: 2500px;"},
        ], "Attempt to reduce overflow", agents.RULES_AUTHOR)

    monkeypatch.setattr(agents, "remediate_rules", ineffective)

    async def run():
        lab.inject("layout-overflow")
        broken = lab.workspace.read("styles.css")
        lab.record_browser_report(browser_report(lab, "layout-overflow"))
        await lab.monitor_once()
        await wait_for(lambda: lab.incident["status"] == "awaiting_approval")
        lab.decide(lab.incident["id"], lab.incident["approval"]["digest"], "approve")
        await wait_for(lambda: "2500px" in lab.workspace.read("styles.css"))
        lab.record_browser_report(browser_report(lab, "layout-overflow"))
        await wait_for(lambda: lab.incident["patch"]["kind"] == "rollback"
                       and lab.incident["status"] == "awaiting_approval")
        if decision:
            lab.decide(lab.incident["id"], lab.incident["approval"]["digest"], decision)
        await lab._pipeline
        record = json.loads(lab.log_path.read_text().splitlines()[-1])
        failed, blocked = record["attempts"]
        assert failed["verification"]["verdict"] == "failed"
        assert blocked["verdict"] == "blocked"
        assert blocked["patch"]["kind"] == "rollback"
        assert blocked["verification"] is None
        assert lab.workspace.read("styles.css") == broken

    asyncio.run(run())
