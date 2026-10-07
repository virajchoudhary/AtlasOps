import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from demo.incident_monitor import _process_running, read_incident
from demo.read_api import create_app

EXPERIMENT = "EXP-STAGE4-SF002-018"


def write(root, path, value):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value), encoding="utf-8")


def test_disabled_monitor_reads_no_capture(monkeypatch):
    monkeypatch.setattr("demo.read_api.read_incident", lambda *args: pytest.fail("capture read"))
    client = TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1))
    assert client.get("/api/incident-monitor").json() == {"enabled": False}


def test_startup_failure_is_not_an_incident_result_and_secrets_are_not_exposed(tmp_path):
    write(tmp_path, "golden/launch.json", {
        "mode": "golden", "pid": 999999, "started_at_utc": "2026-10-07T00:00:00Z",
        "source_sha": "a" * 40, "secret": "do-not-expose",
    })
    write(tmp_path, "golden/exit.json", {"exit_code": 1, "secret": "do-not-expose"})
    write(tmp_path, "startup-evidence/g4-inference-v1.qualification.json", {
        "qualification": {"status": "NOT_QUALIFIED", "failure_category": "bridge_transport_failure",
                          "key": "do-not-expose", "url": "https://private.example"},
    })
    (tmp_path / "golden/stdout.log").write_text("password=do-not-expose", encoding="utf-8")
    result = read_incident(tmp_path, EXPERIMENT)
    assert result["status"] == "STARTUP_FAILED / NOT_RESERVED"
    assert result["reserved"] is False
    assert result["process_running"] is False
    assert result["gate_g4_pass"] is None
    assert result["env_resolved"] is None
    assert result["failure"] == "bridge_transport_failure"
    assert "do-not-expose" not in json.dumps(result)
    assert "private.example" not in json.dumps(result)


def test_launch_file_alone_does_not_establish_active_process(tmp_path, monkeypatch):
    write(tmp_path, "golden/launch.json", {"mode": "golden"})
    monkeypatch.setattr("demo.incident_monitor._process_running", lambda launch: None)
    assert read_incident(tmp_path, EXPERIMENT)["status"] == "ACTIVITY_UNVERIFIED"


def test_selected_v3_capture_does_not_reuse_old_startup_failure(tmp_path, monkeypatch):
    write(tmp_path, "golden/exit.json", {"exit_code": 1})
    write(tmp_path, "startup-evidence/g4-inference-v1.qualification.json", {
        "qualification": {"status": "NOT_QUALIFIED", "failure_category": "bridge_transport_failure"},
    })
    write(tmp_path, "golden-v3/launch.json", {"mode": "golden"})
    monkeypatch.setattr("demo.incident_monitor._process_running", lambda launch: True)
    (tmp_path / "golden-v3/stdout.log").write_text(">>> Phase 0: Telemetry Readiness")
    result = read_incident(tmp_path, EXPERIMENT, "golden-v3")
    assert result["status"] == "RUNNING"
    assert result["failure"] is None
    assert result["phases"][0]["observed"]
    client = TestClient(create_app(incident_capture_root=tmp_path, incident_capture="golden-v3"),
                        base_url="http://localhost", client=("127.0.0.1", 1))
    assert client.get("/api/incident-monitor").json()["status"] == "RUNNING"


@pytest.mark.parametrize("capture", ["../golden", "https://private.example", "arbitrary"])
def test_capture_selection_is_a_fixed_startup_allowlist(tmp_path, capture):
    with pytest.raises(ValueError):
        read_incident(tmp_path, EXPERIMENT, capture)


def test_retry_capture_reads_only_its_execution_checkout(tmp_path):
    write(tmp_path, f"execution/artifacts/evidence/stage4/{EXPERIMENT}.json", {
        "experiment_id": EXPERIMENT, "gate_g4_pass": True,
    })
    write(tmp_path, f"execution-v3b/artifacts/evidence/stage4/{EXPERIMENT}.json", {
        "experiment_id": EXPERIMENT, "gate_g4_pass": False,
    })
    assert read_incident(tmp_path, EXPERIMENT, "golden-v3b")["status"] == "RECORDED_NEGATIVE"


@pytest.mark.parametrize("category,expected", [
    ("invalid_action", "invalid_action"), ("https://private.example", None),
])
def test_terminal_policy_block_is_projected_without_raw_reason(tmp_path, category, expected):
    write(tmp_path, f"execution-v3b/artifacts/evidence/stage4/{EXPERIMENT}.json", {
        "experiment_id": EXPERIMENT, "gate_g4_pass": False,
        "phases": {"coordinator_execution": {"model_proposed_action": {
            "terminal_block": {"category": category, "reason": "password=private-secret"},
        }}},
    })
    result = read_incident(tmp_path, EXPERIMENT, "golden-v3b")
    assert result["policy_block"] == expected
    assert "private-secret" not in json.dumps(result)
    assert "private.example" not in json.dumps(result)


def test_recorded_negative_and_cleanup_do_not_become_pass(tmp_path):
    base = "execution/artifacts/evidence/stage4/"
    write(tmp_path, base + f".attempts/{EXPERIMENT}.attempt.json", {
        "experiment_id": EXPERIMENT, "state": "COMPLETED",
    })
    write(tmp_path, base + EXPERIMENT + ".json", {
        "experiment_id": EXPERIMENT, "scenario_id": "single_fault/sf-002",
        "gate_g4_pass": False,
        "phases": {
            "verification": {"env_resolved": False},
            "coordinator_execution": {
                "approval": {"decision": "timeout"},
                "triage": {"severity": "P1", "affected_services": ["paymentservice"]},
                "executed_tool_actions": [{"tool": "chaos_stop_experiment",
                                          "args": {"name": "sf-002-paymentservice-cpu"},
                                          "output": {"success": True, "secret": "hidden"}}],
            },
        },
    })
    write(tmp_path, base + EXPERIMENT + ".cleanup.json", {
        "experiment_id": EXPERIMENT, "verified_zero_chaos": True,
    })
    result = read_incident(tmp_path, EXPERIMENT)
    assert result["status"] == "RECORDED_NEGATIVE"
    assert result["gate_g4_pass"] is False
    assert result["env_resolved"] is False
    assert result["cleanup_verified_zero"] is True
    assert result["approval"] == "timeout"
    assert result["actions"][0]["success"] is True
    assert "hidden" not in str(result)


@pytest.mark.parametrize("relative,value", [
    ("golden/exit.json", "not-an-object"),
    ("execution/artifacts/evidence/stage4/" + EXPERIMENT + ".json", {"experiment_id": "wrong"}),
])
def test_invalid_or_mismatched_capture_fails_closed(tmp_path, relative, value):
    write(tmp_path, relative, value)
    with pytest.raises((ValueError, TypeError)):
        read_incident(tmp_path, EXPERIMENT)


def test_capture_route_is_get_only_and_no_raw_file_route(tmp_path):
    client = TestClient(create_app(incident_capture_root=tmp_path),
                        base_url="http://localhost", client=("127.0.0.1", 1))
    assert client.get("/api/incident-monitor").status_code == 200
    assert client.post("/api/incident-monitor").status_code == 405
    assert client.get("/api/incident-monitor", headers={"host": "evil.example"}).status_code == 403
    assert client.get("/golden/stdout.log").status_code == 404


def test_oversize_capture_is_unavailable(tmp_path):
    (tmp_path / "golden").mkdir()
    (tmp_path / "golden/launch.json").write_bytes(b"x" * 16385)
    client = TestClient(create_app(incident_capture_root=tmp_path),
                        base_url="http://localhost", client=("127.0.0.1", 1))
    assert client.get("/api/incident-monitor").status_code == 503


def test_capture_symlink_is_rejected(tmp_path):
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    (tmp_path / "golden").mkdir()
    try:
        (tmp_path / "golden/launch.json").symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="redirects"):
        read_incident(tmp_path, EXPERIMENT)


def test_relative_capture_root_and_wrong_experiment_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        read_incident(Path("relative"), EXPERIMENT)
    with pytest.raises(ValueError):
        read_incident(tmp_path, "../wrong")


def test_unscored_record_is_inconclusive_not_negative_or_pass(tmp_path):
    write(tmp_path, "execution/artifacts/evidence/stage4/" + EXPERIMENT + ".json",
          {"experiment_id": EXPERIMENT, "phases": {}})
    result = read_incident(tmp_path, EXPERIMENT)
    assert result["status"] == "UNSCORED / INCONCLUSIVE"
    assert result["gate_g4_pass"] is None


def test_unexpected_free_text_identifiers_are_not_exposed(tmp_path):
    write(tmp_path, "execution/artifacts/evidence/stage4/" + EXPERIMENT + ".json", {
        "experiment_id": EXPERIMENT, "phases": {"coordinator_execution": {
            "triage": {"severity": "hidden-secret", "affected_services": ["hidden-secret"]},
            "executed_tool_actions": [{"tool": "hidden-secret", "args": {
                "name": "hidden-secret", "namespace": "hidden-secret",
            }, "output": {"success": True}}],
        }},
    })
    result = read_incident(tmp_path, EXPERIMENT)
    assert "hidden-secret" not in json.dumps(result)
    assert result["actions"][0]["target"] == "OTHER_TARGET"


@pytest.mark.skipif(os.name != "nt", reason="Windows process handle contract")
def test_process_check_uses_current_handle_and_rejects_creation_time_mismatch():
    started = datetime.now(UTC)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"])
    try:
        record = {"pid": process.pid, "started_at_utc": started.isoformat()}
        assert _process_running(record) is True
        assert _process_running({
            **record, "started_at_utc": (started - timedelta(days=1)).isoformat(),
        }) is False
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert _process_running(record) is not True


@pytest.mark.skipif(os.name != "nt", reason="Windows process handle contract")
def test_terminated_process_with_exit_code_259_is_not_active():
    started = datetime.now(UTC)
    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(259)"])
    process.wait(timeout=5)
    assert process.returncode == 259
    assert _process_running({
        "pid": process.pid, "started_at_utc": started.isoformat(),
    }) is not True


def test_invalid_marker_state_cannot_expose_free_text(tmp_path):
    write(tmp_path, f"execution/artifacts/evidence/stage4/.attempts/{EXPERIMENT}.attempt.json",
          {"experiment_id": EXPERIMENT, "state": "https://private.example/secret"})
    with pytest.raises(ValueError, match="accounting state"):
        read_incident(tmp_path, EXPERIMENT)
