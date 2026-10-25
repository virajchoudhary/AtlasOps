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
from demo.bug_lab.llm import RULES, detect_backend
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
    lab.reset()
    assert lab.injected == [] and lab.workspace.changed_files() == []
    with pytest.raises(ValueError, match="Unknown bug"):
        lab.inject("no-such-bug")


def fake_model(answers):
    calls = []

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen2.5:7b-instruct"}]})
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": answers[len(calls) - 1]}}]})

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


def test_http_surface_serves_both_pages_and_guards_writes(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
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
