"""Software-only operator controls: no model, cluster, or incident execution."""

import json
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from agents.approval import ApprovalGate
from demo.operator import OperatorConfig, OperatorRun
from demo.read_api import create_app


@pytest.fixture
def operator(tmp_path, monkeypatch):
    monkeypatch.setattr("demo.operator.LOCAL_OPERATOR_AUTHORITY", tmp_path / "authority")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    secret_dir = tmp_path / "private"
    secret_dir.mkdir()
    (secret_dir / "atlasops-api-key.secret").write_text("private-operator-key")
    config = OperatorConfig(
        checkout=checkout, capture_root=tmp_path / "captures", secret_dir=secret_dir,
        inference_config=tmp_path / "private/inference.json",
        attempt_ledger_root=tmp_path / "historical",
        attempt_ledger_inventory_sha256="d" * 64,
        source_sha="a" * 40, protocol_fingerprint="b" * 64,
        experiment_id="EXP-STAGE4-SF002-019", approved_by="test-operator",
    )
    return OperatorRun(config)


def test_default_presentation_has_no_operator_authority():
    client = TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1))
    assert client.get("/api/operator").json() == {"enabled": False}
    assert client.post("/api/operator/start").status_code == 405


def test_configuration_rejects_relative_paths_and_browser_commands(operator):
    with pytest.raises(ValueError):
        OperatorConfig.model_validate({**operator.config.model_dump(), "checkout": "relative"})
    with pytest.raises(ValueError):
        OperatorConfig.model_validate({**operator.config.model_dump(), "command": "anything"})
    with pytest.raises(ValueError, match="outside"):
        OperatorRun(OperatorConfig.model_validate({
            **operator.config.model_dump(), "capture_root": operator.config.checkout / "captures",
        }))


def test_dirty_source_and_exhausted_budget_fail_without_launch(operator, monkeypatch):
    monkeypatch.setattr(operator, "_git", lambda *args: "dirty" if args[0] == "status" else "a" * 40)
    monkeypatch.setattr(
        "scripts.run_stage4_golden_incident._claimed_attempts_for_protocol_fingerprint", lambda *args: 2,
    )
    monkeypatch.setattr("demo.operator.subprocess.Popen", lambda *a, **kw: pytest.fail("process started"))
    status = operator.readiness()
    assert not status["can_start"]
    assert "protocol_attempt_budget_exhausted" in status["blockers"]
    assert "tracked_source_dirty" in status["blockers"]
    with pytest.raises(ValueError, match="blocked"):
        operator.start(operator.csrf)
    assert not operator.config.capture_root.exists()


def test_empty_checkout_cannot_hide_preserved_attempts(operator, monkeypatch):
    root = operator.config.attempt_ledger_root / "artifacts/evidence/stage4/.attempts"
    root.mkdir(parents=True)
    (root / "EXP-STAGE4-SF002-018.attempt.json").write_text('{"state":"COMPLETED"}')
    monkeypatch.setattr(operator, "_git", lambda *args: "" if args[0] == "status" else "a" * 40)
    monkeypatch.setattr(
        "scripts.run_stage4_golden_incident._claimed_attempts_for_protocol_fingerprint",
        lambda fingerprint, path: 2 if path == str(operator.config.attempt_ledger_root) else 0,
    )
    status = operator.readiness()
    assert status["attempts_used"] == 2
    assert "preserved_attempt_ledger_mismatch" in status["blockers"]
    assert "protocol_attempt_budget_exhausted" in status["blockers"]


@pytest.mark.parametrize("origin,token", [
    ("https://evil.example", "valid"), (None, "valid"), ("http://localhost", "invalid"),
])
def test_operator_post_requires_same_origin_and_session(operator, monkeypatch, origin, token):
    monkeypatch.setattr(operator, "start", lambda _: pytest.fail("launch called"))
    client = TestClient(create_app(operator=operator), base_url="http://localhost", client=("127.0.0.1", 1))
    headers = {"X-AtlasOps-Operator": operator.csrf if token == "valid" else "wrong"}
    if origin:
        headers["Origin"] = origin
    assert client.post("/api/operator/start", headers=headers, json={}).status_code == 403


def test_unknown_commands_and_browser_supplied_launch_config_rejected(operator, monkeypatch):
    monkeypatch.setattr(operator, "start", lambda _: pytest.fail("launch called"))
    client = TestClient(create_app(operator=operator), base_url="http://localhost", client=("127.0.0.1", 1))
    headers = {"Origin": "http://localhost", "X-AtlasOps-Operator": operator.csrf}
    assert client.post("/api/operator/start", headers=headers, json={"checkout": "elsewhere"}).status_code == 409
    assert client.post("/inject", headers=headers).status_code == 405
    assert client.get("/health").json()["mode"] == "operator"


def setup_channel(operator):
    operator._process = Mock(pid=123)
    operator._process.poll.return_value = None
    operator._capture.mkdir(parents=True)
    (operator._capture / "channel.json").write_text(json.dumps({
        "pid": 123, "source_sha": "a" * 40,
        "experiment_id": operator.config.experiment_id, "url": "http://127.0.0.1:12345",
    }))
    gate = ApprovalGate()
    proposal = gate.request_action(
        incident_id="incident-019", severity="P1",
        action={"tool": "chaos_stop_experiment", "arguments": {
            "name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh",
        }},
        operator_scope={"kube_context": "kind-atlasops-local", "scenario_id": "single_fault/sf-002"},
    ).to_dict()
    return proposal


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_exact_decision_reaches_only_owned_channel(operator, monkeypatch, decision):
    proposal = setup_channel(operator)
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers["X-AtlasOps-Key"] == "private-operator-key"
        if request.url.path == "/operator/events":
            return httpx.Response(200, json={"events": []})
        if request.method == "GET":
            return httpx.Response(200, json={"pending": [proposal]})
        data = json.loads(request.content)
        assert data["token"] == proposal["token"]
        assert data["decision"] == decision
        return httpx.Response(200, json={"ok": True})
    real_client = httpx.Client
    monkeypatch.setattr("demo.operator.httpx.Client", lambda **kw: real_client(
        **kw, transport=httpx.MockTransport(handler),
    ))
    assert operator.decide(operator.csrf, proposal["token"], proposal["action_digest"], decision)["decision"] == decision
    assert [request.method for request in requests] == ["GET", "POST"]
    assert "private-operator-key" not in json.dumps(operator.snapshot())


def test_stale_action_and_reused_pid_cannot_approve(operator, monkeypatch):
    proposal = setup_channel(operator)
    requests = []
    def handler(request):
        requests.append(request.method)
        return httpx.Response(200, json={"pending": [proposal]})
    real_client = httpx.Client
    monkeypatch.setattr("demo.operator.httpx.Client", lambda **kw: real_client(
        **kw, transport=httpx.MockTransport(handler),
    ))
    with pytest.raises(ValueError, match="stale"):
        operator.decide(operator.csrf, proposal["token"], "c" * 64, "approved")
    assert requests == ["GET"]
    operator._process.poll.return_value = 1
    assert operator.pending() == ([], "approval_channel_unavailable")


def test_launch_is_fixed_and_cannot_be_repeated(operator, monkeypatch):
    monkeypatch.setattr(operator, "readiness", lambda: {"can_start": not operator._launched, "blockers": ["used"]})
    monkeypatch.setattr(operator, "_git", lambda *args: "a" * 40 + "\trefs/heads/main")
    launched = []
    class Process:
        pid = 123
        def wait(self): return 1
    def launch(args, **kwargs):
        launched.append((args, kwargs))
        return Process()
    monkeypatch.setattr("demo.operator.subprocess.Popen", launch)
    assert operator.start(operator.csrf)["started"]
    operator._worker.join(timeout=5)
    with pytest.raises(ValueError):
        operator.start(operator.csrf)
    assert len(launched) == 1
    args, kwargs = launched[0]
    assert args[-2:] == ["-m", "scripts.run_stage4_golden_incident"]
    assert kwargs["env"]["KUBECONFIG_CONTEXT"] == "kind-atlasops-local"
    assert kwargs["env"]["ATLASOPS_STAGE4_PROTOCOL"] == "historical"
    assert json.loads((operator._capture / "exit.json").read_text())["exit_code"] == 1


def test_operator_config_explicit_candidate_selection_and_unknown_profile_refusal(operator):
    revised = OperatorConfig.model_validate({
        **operator.config.model_dump(), "protocol_profile": "website-demo-candidate",
    })
    assert revised.protocol_profile == "website-demo-candidate"
    with pytest.raises(ValueError):
        OperatorConfig.model_validate({**operator.config.model_dump(), "protocol_profile": "reset"})


def test_activity_projects_only_metadata_from_owned_runner(operator, monkeypatch):
    setup_channel(operator)
    real_client = httpx.Client
    monkeypatch.setattr("demo.operator.httpx.Client", lambda **kw: real_client(
        **kw, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"events": [{
            "role": "diagnosis", "phase": "tool_call", "tool": "kubectl_get",
            "thought": "private-secret", "result_summary": "credential",
        }]})),
    ))
    assert operator.events() == [{"role": "diagnosis", "phase": "tool_call", "tool": "kubectl_get"}]


def test_launch_record_failure_keeps_owned_process_monitored(operator, monkeypatch):
    monkeypatch.setattr(operator, "readiness", lambda: {"can_start": not operator._launched, "blockers": []})
    monkeypatch.setattr(operator, "_git", lambda *args: "a" * 40 + "\trefs/heads/main")
    class Process:
        pid = 123
        def wait(self): return 1
        def poll(self): return 1
    monkeypatch.setattr("demo.operator.subprocess.Popen", lambda *a, **kw: Process())
    import demo.operator as module
    write_new = module._write_new
    def failing_write(path, value):
        if path.name == "launch.json":
            raise OSError("cannot record launch")
        write_new(path, value)
    monkeypatch.setattr(module, "_write_new", failing_write)
    result = operator.start(operator.csrf)
    operator._worker.join(timeout=5)
    assert result["started"] and result["capture_error"]
    assert operator._process is not None
    assert (operator._capture / "exit.json").exists()
    assert operator.snapshot()["capture"]["status"] == "CAPTURE_ERROR / ACTIVITY_UNVERIFIED"


def test_lifecycle_worker_failure_never_spawns_runner(operator, monkeypatch):
    monkeypatch.setattr(operator, "readiness", lambda: {"can_start": True, "blockers": []})
    monkeypatch.setattr(operator, "_git", lambda *args: "a" * 40 + "\trefs/heads/main")
    monkeypatch.setattr("demo.operator.threading.Thread.start", lambda _: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr("demo.operator.subprocess.Popen", lambda *a, **kw: pytest.fail("runner spawned"))
    with pytest.raises(ValueError, match="no runner launched"):
        operator.start(operator.csrf)


def test_preserved_poison_is_blocked_even_when_current_checkout_is_clean(operator, monkeypatch):
    root = operator.config.attempt_ledger_root / "artifacts/evidence/stage4"
    (root / ".attempts").mkdir(parents=True)
    (root / ".attempts/a.attempt.json").write_text('{"state":"COMPLETED"}')
    (root / ".poisoned-environment.json").write_text('{"poisoned":true}')
    monkeypatch.setattr(operator, "_git", lambda *args: "" if args[0] == "status" else "a" * 40)
    monkeypatch.setattr("scripts.run_stage4_golden_incident._claimed_attempts_for_protocol_fingerprint", lambda *a: 0)
    status = operator.readiness()
    assert "preserved_environment_poisoned" in status["blockers"]
    assert "preserved_ledger_inventory_mismatch" in status["blockers"]


@pytest.mark.asyncio
async def test_operator_runner_checks_source_before_constructing_inference(monkeypatch):
    import scripts.run_stage4_golden_incident as runner
    monkeypatch.setenv("ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE", "channel-file")
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", "a" * 40)
    monkeypatch.setattr(runner, "_current_main_sha", lambda **kw: (_ for _ in ()).throw(RuntimeError("source rejected")))
    monkeypatch.setattr(
        "scripts.qualify_integrated_inference.engine_from_environment",
        lambda: pytest.fail("inference initialized before source check"),
    )
    with pytest.raises(RuntimeError, match="source rejected"):
        await runner.main()


def test_persistent_website_claim_blocks_another_checkout_even_if_capture_changes(operator, monkeypatch):
    monkeypatch.setattr(operator, "readiness", lambda: {"can_start": True, "blockers": []})
    monkeypatch.setattr(operator, "_git", lambda *args: "a" * 40 + "\trefs/heads/main")
    class Process:
        pid = 123
        def wait(self): return 1
    monkeypatch.setattr("demo.operator.subprocess.Popen", lambda *a, **kw: Process())
    assert operator.start(operator.csrf)["started"]
    operator._worker.join(timeout=5)
    other = OperatorRun(OperatorConfig.model_validate({
        **operator.config.model_dump(), "capture_root": operator.config.capture_root.parent / "other",
        "attempt_ledger_root": operator.config.attempt_ledger_root.parent / "copied-ledger",
    }))
    monkeypatch.setattr(other, "readiness", lambda: {"can_start": True, "blockers": []})
    monkeypatch.setattr(other, "_git", lambda *args: "a" * 40 + "\trefs/heads/main")
    monkeypatch.setattr("demo.operator.subprocess.Popen", lambda *a, **kw: pytest.fail("second runner"))
    with pytest.raises(FileExistsError):
        other.start(other.csrf)
    assert not other._capture.exists()


def test_server_restart_retains_capture_and_blocks_approval_without_owned_handle(operator, monkeypatch):
    setup_channel(operator)
    (operator._capture / "launch.json").write_text(json.dumps({
        "mode": "golden", "pid": 123, "started_at_utc": "2026-10-07T00:00:00Z",
    }))
    restarted = OperatorRun(operator.config)
    monkeypatch.setattr("demo.incident_monitor._process_running", lambda launch: True)
    status = restarted.snapshot()
    assert status["capture"]["process_running"] is True
    assert status["channel_error"] == "approval_channel_unavailable"
    assert "capture_already_exists" in status["readiness"]["blockers"]
    with pytest.raises(ValueError, match="No active owned"):
        restarted.decide(restarted.csrf, "token", "a" * 64, "approved")


def test_runner_activity_endpoint_is_authenticated_and_excludes_narration(monkeypatch):
    from agents.stream import clear, emit
    from scripts.run_stage4_golden_incident import stage4_approval_app
    clear()
    emit("diagnosis", "tool_call", "private-secret", tool="kubectl_get")
    client = TestClient(stage4_approval_app("test-key"))
    assert client.get("/operator/events").status_code == 401
    response = client.get("/operator/events", headers={"X-AtlasOps-Key": "test-key"})
    assert response.status_code == 200
    assert "private-secret" not in response.text
    assert response.json()["events"][0]["tool"] == "kubectl_get"
    clear()
