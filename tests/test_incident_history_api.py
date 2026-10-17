"""Tests for authenticated, read-only access to persisted incident records."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_API_KEY = "atlasops-history-test-key"


@pytest.fixture(autouse=True)
def configured_test_api_key(monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_API_KEY", TEST_API_KEY)


def _client():
    from app import app

    return TestClient(app, headers={"X-AtlasOps-Key": TEST_API_KEY})


def _record(incident_id: str, execution_id: str, summary: str) -> dict:
    return {
        "incident_id": incident_id,
        "execution_id": execution_id,
        "scenario_id": "single_fault/sf-001",
        "execution_mode": "NON_EMPIRICAL",
        "evidence_class": "NON_EMPIRICAL",
        "certification_status": "NOT_CERTIFIED",
        "resolved": False,
        "agent_claimed_resolved": True,
        "env_resolved": False,
        "triage": {
            "trajectory": [{"content": "Authorization: raw-telemetry-secret"}],
            "final": {
                "severity": "P1",
                "title": summary,
                "blast_radius": {
                    "services": ["checkoutservice"],
                    "namespaces": ["default"],
                    "user_impact_pct": 5.0,
                    "revenue_path_affected": True,
                    "api_key": "must-not-escape",
                },
                "affected_services": ["checkoutservice"],
                "handoff_notes": "Password=triage-secret",
                "api_key": "must-not-escape",
            },
        },
        "diagnosis": {
            "trajectory": [{"output": "token=raw-model-token"}],
            "final": {
                "root_cause": "A dependency timed out",
                "category": "dependency",
                "confidence": 0.8,
                "blast_radius_update": "checkoutservice only",
                "recommended_fix": ["Inspect the dependency health"],
                "evidence": [
                    {
                        "tool": "promql_query",
                        "query": "up{service='checkoutservice'}",
                        "finding": "telemetry-secret-value",
                    }
                ],
                "secret": "must-not-escape",
            },
        },
        "remediation": {
            "trajectory": [{"output": "secret=raw-remediation-secret"}],
            "final": {
                "status": "skipped_execution",
                "outcome": "unresolved",
                "proposed_actions": [
                    {"tool": "kubectl_get", "args": {"resource": "pods"}}
                ],
                "executed_actions": [],
                "verified_by": "environment_verifier",
                "summary": "No mutation was run",
                "credential": "must-not-escape",
            },
        },
        "approval": {
            "mode": "required",
            "decision": "rejected",
            "action_summary": "Inspect checkoutservice",
            "token": "approval-token-must-not-escape",
        },
        "verification": {
            "scenario_id": "single_fault/sf-001",
            "agent_claimed_resolved": True,
            "env_resolved": False,
            "verification_status": "failed",
            "is_false_resolution": True,
            "is_false_negative": False,
            "failed_checks": ["checkoutservice_ready_replicas"],
            "checks": [
                {
                    "name": "checkoutservice_ready_replicas",
                    "target": "checkoutservice",
                    "passed": False,
                    "required": True,
                    "details": "telemetry-secret-value",
                    "observed": "telemetry-secret-value",
                }
            ],
            "observed_metrics": {
                "rate(http_requests_total[5m])": 0.12,
                "private_metric": "telemetry-secret-value",
            },
            "evidence": ["telemetry-secret-value"],
            "error": "C:\\private\\storage\\secret.txt",
            "verification_timestamp": 123.0,
        },
        "comms": {
            "trajectory": [{"output": "Bearer comms-secret"}],
            "final": {
                "summary": summary,
                "slack_posted": False,
                "postmortem_path": "docs/postmortems/example.md",
                "token": "must-not-escape",
            },
        },
        "alert": {"commonLabels": {"api_key": "alert-secret"}},
        "environment_observation": {"session_token": "telemetry-secret-value"},
        "trajectory": [{"content": "Bearer raw-top-level-token"}],
    }


def _write_record(
    directory: Path,
    incident_id: str,
    execution_id: str,
    summary: str,
    *,
    filename: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (filename or f"{incident_id}.json")
    path.write_text(
        json.dumps(_record(incident_id, execution_id, summary)),
        encoding="utf-8",
    )
    return path


def test_history_keeps_canonical_and_each_execution_separate(tmp_path, monkeypatch):
    import app as app_module

    incident_id = "inc-history-20261015"
    unrecorded_execution = "00000000000000000000000000000001"
    execution_one = "11111111111111111111111111111111"
    execution_two = "22222222222222222222222222222222"
    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", tmp_path)
    canonical_record = _record(incident_id, unrecorded_execution, "first saved record")
    canonical_record.pop("execution_id")
    (tmp_path / f"{incident_id}.json").write_text(
        json.dumps(canonical_record),
        encoding="utf-8",
    )
    _write_record(
        tmp_path,
        incident_id,
        execution_one,
        "execution one",
        filename=f"{incident_id}.execution-{execution_one}.json",
    )
    _write_record(
        tmp_path,
        incident_id,
        execution_two,
        "execution two",
        filename=f"{incident_id}.execution-{execution_two}.json",
    )

    client = _client()
    history = client.get("/incidents/history?limit=100&offset=0")
    assert history.status_code == 200
    body = history.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert {item["record_type"] for item in body["items"]} == {
        "canonical",
        "execution",
    }
    assert {item["execution_id"] for item in body["items"]} == {
        None,
        execution_one,
        execution_two,
    }

    canonical = client.get(f"/incidents/{incident_id}")
    first_execution = client.get(
        f"/incidents/{incident_id}?execution_id={execution_one}"
    )
    second_execution = client.get(
        f"/incidents/{incident_id}?execution_id={execution_two}"
    )
    assert canonical.status_code == first_execution.status_code == 200
    assert second_execution.status_code == 200
    assert canonical.json()["record_type"] == "canonical"
    assert canonical.json()["execution_id"] is None
    assert canonical.json()["triage"]["title"] == "first saved record"
    assert first_execution.json()["triage"]["title"] == "execution one"
    assert second_execution.json()["triage"]["title"] == "execution two"
    assert client.get(
        f"/incidents/{incident_id}?execution_id={unrecorded_execution}"
    ).status_code == 404

    paged = client.get("/incidents/history?limit=1&offset=1")
    assert paged.status_code == 200
    assert paged.json()["total"] == 3
    assert len(paged.json()["items"]) == 1


def test_history_requires_operator_auth_before_reading_store(tmp_path, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", tmp_path)
    read_history = MagicMock(side_effect=AssertionError("storage must not be read"))
    monkeypatch.setattr(app_module.incident_history, "list_incident_history", read_history)

    unauthenticated = TestClient(app_module.app)
    wrong_key = TestClient(
        app_module.app,
        headers={"X-AtlasOps-Key": "incorrect-test-key"},
    )
    assert unauthenticated.get("/incidents/history").status_code == 401
    assert wrong_key.get("/incidents/inc-auth").status_code == 401
    read_history.assert_not_called()

    monkeypatch.setattr(app_module, "_API_KEY", "")
    missing_configuration = TestClient(app_module.app)
    assert missing_configuration.get("/incidents/history").status_code == 503
    assert missing_configuration.get("/incidents/inc-auth").status_code == 503
    read_history.assert_not_called()


def test_active_process_view_remains_separate_and_public():
    from app import app

    response = TestClient(app).get("/incidents/active")
    assert response.status_code == 200
    assert isinstance(response.json()["incidents"], list)


@pytest.mark.parametrize(
    "contents",
    [
        "{",
        "[]",
        json.dumps(
            {
                "incident_id": "inc-someone-else",
                "execution_id": "33333333333333333333333333333333",
            }
        ),
    ],
)
def test_malformed_or_identity_mismatched_records_are_unavailable(
    contents,
    tmp_path,
    monkeypatch,
):
    import app as app_module

    incident_id = "inc-corrupt"
    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", tmp_path)
    (tmp_path / f"{incident_id}.json").write_text(contents, encoding="utf-8")

    response = _client().get(f"/incidents/{incident_id}")
    assert response.status_code == 503
    assert str(tmp_path) not in response.text


@pytest.mark.parametrize(
    "stored_execution_id",
    [None, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
)
def test_execution_snapshot_requires_matching_identity(
    stored_execution_id,
    tmp_path,
    monkeypatch,
):
    import app as app_module

    incident_id = "inc-explicit-execution"
    execution_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", tmp_path)
    record = _record(incident_id, execution_id, "execution")
    if stored_execution_id is None:
        record.pop("execution_id")
    else:
        record["execution_id"] = stored_execution_id
    (tmp_path / f"{incident_id}.execution-{execution_id}.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )

    client = _client()
    detail = client.get(f"/incidents/{incident_id}?execution_id={execution_id}")
    listing = client.get("/incidents/history")
    assert detail.status_code == 503
    assert listing.status_code == 503
    assert str(tmp_path) not in detail.text + listing.text


def test_missing_oversized_and_invalid_identifiers_have_distinct_responses(
    tmp_path,
    monkeypatch,
):
    import app as app_module
    from agents.incident_history import MAX_RECORD_BYTES

    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", tmp_path)
    client = _client()
    assert client.get("/incidents/inc-missing").status_code == 404
    assert client.get("/incidents/inc-missing?execution_id=../outside").status_code == 404
    assert client.get("/incidents/%2e%2e%2foutside").status_code == 404

    oversized = tmp_path / "inc-large.json"
    oversized.write_bytes(
        b'{"incident_id":"inc-large","padding":"'
        + b"x" * MAX_RECORD_BYTES
        + b'"}'
    )
    response = client.get("/incidents/inc-large")
    assert response.status_code == 503
    assert str(tmp_path) not in response.text

    assert client.get("/incidents/history?limit=101").status_code == 422
    assert client.get("/incidents/history?offset=10001").status_code == 422


def test_listing_ignores_partial_records_and_external_symlinks(tmp_path, monkeypatch):
    import app as app_module

    root = tmp_path / "store"
    outside = tmp_path / "outside"
    incident_id = "inc-outside"
    execution_id = "44444444444444444444444444444444"
    root.mkdir()
    outside.mkdir()
    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", root)
    _write_record(
        root,
        "inc-partial",
        "55555555555555555555555555555555",
        "partial provider episode",
        filename="inc-partial.partial-55555555555555555555555555555555.json",
    )
    outside_record = _write_record(
        outside,
        incident_id,
        execution_id,
        "outside record",
    )
    link = root / f"{incident_id}.json"
    try:
        link.symlink_to(outside_record)
    except (OSError, NotImplementedError):
        pytest.skip("the filesystem does not permit symlink creation")

    client = _client()
    history = client.get("/incidents/history")
    detail = client.get(f"/incidents/{incident_id}")
    assert history.status_code == 200
    assert history.json()["total"] == 0
    assert history.json()["items"] == []
    assert detail.status_code == 404
    assert str(outside) not in history.text + detail.text


def test_read_projection_keeps_operational_truth_and_omits_raw_secrets(
    tmp_path,
    monkeypatch,
):
    import app as app_module

    incident_id = "inc-projection"
    execution_id = "66666666666666666666666666666666"
    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", tmp_path)
    _write_record(tmp_path, incident_id, execution_id, "summary")

    response = _client().get(f"/incidents/{incident_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["agent_claimed_resolved"] is True
    assert body["env_resolved"] is False
    assert body["resolved"] is False
    assert body["verification"]["verification_status"] == "failed"
    assert body["verification"]["checks"] == [
        {
            "name": "checkoutservice_ready_replicas",
            "target": "checkoutservice",
            "passed": False,
            "required": True,
        }
    ]
    assert body["diagnosis"]["evidence"] == [{"tool": "promql_query"}]
    assert body["remediation"]["outcome"] == "unresolved"
    assert body["approval"]["decision"] == "rejected"
    assert body["comms"]["postmortem_path"] == "docs/postmortems/example.md"
    assert body["triage"]["handoff_notes"] == "[REDACTED]"
    for secret in (
        "must-not-escape",
        "approval-token-must-not-escape",
        "telemetry-secret-value",
        "raw-model-token",
        "raw-top-level-token",
        "C:\\private\\storage\\secret.txt",
        str(tmp_path),
    ):
        assert secret not in response.text


def test_action_projection_redacts_nested_credentials_and_sanitizes_keys(
    tmp_path,
    monkeypatch,
):
    import app as app_module

    incident_id = "inc-nested-credentials"
    execution_id = "12121212121212121212121212121212"
    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", tmp_path)
    record = _record(incident_id, execution_id, "summary")
    record["remediation"]["final"]["proposed_actions"][0]["args"] = {
        "resource": "pods",
        "metadata": {
            "phase": "Running",
            "token": "synthetic-proposed-token",
            "session_token": "synthetic-proposed-session-token",
            "bearer": "synthetic-proposed-bearer",
            r"C:\synthetic\private\argument.json": "preserved argument data",
        },
    }
    record["remediation"]["final"]["executed_actions"] = [
        {
            "tool": "kubectl_get",
            "args": {"resource": "pods"},
            "result": {
                "status": "observed",
                "data": {
                    "phase": "Running",
                    "token": "synthetic-result-token",
                    "session_token": "synthetic-result-session-token",
                    "bearer": "synthetic-result-bearer",
                    r"C:\synthetic\private\result.json": "preserved result data",
                },
            },
        }
    ]
    record["remediation"]["final"]["summary"] = (
        "Action completed; token=synthetic-remediation-summary-token"
    )
    record["comms"]["final"]["summary"] = (
        "Report ready; session_token=synthetic-comms-summary-token"
    )
    (tmp_path / f"{incident_id}.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )

    response = _client().get(f"/incidents/{incident_id}")

    assert response.status_code == 200
    body = response.json()
    for sentinel in (
        "synthetic-proposed-token",
        "synthetic-proposed-session-token",
        "synthetic-proposed-bearer",
        "synthetic-result-token",
        "synthetic-result-session-token",
        "synthetic-result-bearer",
        "synthetic-remediation-summary-token",
        "synthetic-comms-summary-token",
        r"C:\synthetic\private\argument.json",
        r"C:\synthetic\private\result.json",
    ):
        assert sentinel not in response.text

    proposed_args = body["remediation"]["proposed_actions"][0]["args"]
    assert proposed_args == {
        "resource": "pods",
        "metadata": {
            "phase": "Running",
            "[PATH]": "preserved argument data",
        },
    }
    executed_result = body["remediation"]["executed_actions"][0]["result"]
    assert executed_result == {
        "status": "observed",
        "data": {
            "phase": "Running",
            "[PATH]": "preserved result data",
        },
    }
    assert body["remediation"]["summary"] == "Action completed; [REDACTED]"
    assert body["comms"]["summary"] == "Report ready; [REDACTED]"


def test_public_history_api_redacts_nested_session_ids_and_embedded_text(
    tmp_path,
    monkeypatch,
):
    import app as app_module

    incident_id = "inc-session-id-redaction"
    execution_id = "19191919191919191919191919191919"
    nested_session_values = {
        "session_id": "synthetic-nested-session-id-underscore",
        "session-id": "synthetic-nested-session-id-hyphen",
        "sessionId": "synthetic-nested-session-id-camel",
    }
    embedded_summary = (
        "History detail: "
        "session_id=synthetic-text-session-id-underscore "
        "session-id=synthetic-text-session-id-hyphen "
        "sessionId=synthetic-text-session-id-camel "
        "Automatic retries remain disabled."
    )
    sanitized_summary = (
        "History detail: "
        "[REDACTED] "
        "[REDACTED] "
        "[REDACTED] "
        "Automatic retries remain disabled."
    )
    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", tmp_path)
    record = _record(incident_id, execution_id, "summary")
    record["remediation"]["final"]["proposed_actions"][0]["args"] = {
        "resource": "pods",
        "metadata": {
            **nested_session_values,
            "execution_id": execution_id,
            "incident_id": incident_id,
            "source": "persisted result",
        },
    }
    record["comms"]["final"]["summary"] = embedded_summary
    (tmp_path / f"{incident_id}.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )

    client = _client()
    listing = client.get("/incidents/history")
    detail = client.get(f"/incidents/{incident_id}")

    assert listing.status_code == detail.status_code == 200
    assert listing.json()["total"] == 1
    listed = listing.json()["items"][0]
    body = detail.json()
    assert (listed["incident_id"], listed["execution_id"]) == (
        incident_id,
        execution_id,
    )
    assert (body["incident_id"], body["execution_id"]) == (
        incident_id,
        execution_id,
    )
    assert listed["summary"] == sanitized_summary
    assert body["comms"]["summary"] == sanitized_summary
    assert body["remediation"]["status"] == "skipped_execution"
    assert body["remediation"]["outcome"] == "unresolved"
    assert body["verification"]["verification_status"] == "failed"
    assert body["remediation"]["proposed_actions"][0]["args"] == {
        "resource": "pods",
        "metadata": {
            "execution_id": execution_id,
            "incident_id": incident_id,
            "source": "persisted result",
        },
    }
    for sentinel in (
        *nested_session_values.values(),
        "synthetic-text-session-id-underscore",
        "synthetic-text-session-id-hyphen",
        "synthetic-text-session-id-camel",
    ):
        assert sentinel not in listing.text + detail.text


def test_authenticated_history_routes_redact_auth_headers_and_preserve_action_data(
    tmp_path,
    monkeypatch,
):
    import app as app_module

    incident_id = "inc-auth-header-redaction"
    execution_id = "13131313131313131313131313131313"
    basic_secret = "synthetic-basic-auth-base64"
    cookie_secret = "synthetic-cookie-session"
    set_cookie_secret = "synthetic-set-cookie-session"
    json_basic_secret = "synthetic-json-basic-auth-base64"
    json_cookie_preference = "synthetic-json-cookie-preference"
    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", tmp_path)
    record = _record(incident_id, execution_id, "summary")
    record["remediation"]["final"]["proposed_actions"][0]["args"] = {
        "namespace": "default",
        "headers": {
            "Accept": "application/json",
            "Authorization": f"Basic {basic_secret}",
            "Cookie": f"session={cookie_secret}; preference=dark",
            "Set-Cookie": f"session={set_cookie_secret}; HttpOnly",
        },
    }
    record["remediation"]["final"]["executed_actions"] = [
        {
            "tool": "http_get",
            "args": {"path": "/ready"},
            "result": {
                "status": "observed",
                "headers": {
                    "Content-Type": "application/json",
                    "Set-Cookie": f"session={set_cookie_secret}; HttpOnly",
                },
            },
            "status": "completed",
        }
    ]
    record["comms"]["final"]["summary"] = (
        "Request failed:\n"
        f"Authorization: Basic {basic_secret}\n"
        f"Cookie: session={cookie_secret}; preference=dark\n"
        f"Set-Cookie: session={set_cookie_secret}; HttpOnly; Secure\n"
        "Automatic retries remain disabled.\n"
        f'{{"Authorization": "Basic {json_basic_secret}"}}\n'
        f'{{"Cookie": "session={cookie_secret}; '
        f'preference={json_cookie_preference}"}}\n'
        f'{{"Set-Cookie": "session={set_cookie_secret}; HttpOnly"}}'
    )
    (tmp_path / f"{incident_id}.json").write_text(
        json.dumps(record),
        encoding="utf-8",
    )

    client = _client()
    history = client.get("/incidents/history")
    detail = client.get(f"/incidents/{incident_id}")

    assert history.status_code == detail.status_code == 200
    for sentinel in (
        basic_secret,
        cookie_secret,
        set_cookie_secret,
        json_basic_secret,
        json_cookie_preference,
    ):
        assert sentinel not in history.text + detail.text
    assert "Automatic retries remain disabled." in history.text + detail.text
    body = detail.json()
    assert body["remediation"]["proposed_actions"][0]["args"] == {
        "namespace": "default",
        "headers": {"Accept": "application/json"},
    }
    assert body["remediation"]["executed_actions"][0]["result"] == {
        "status": "observed",
        "headers": {"Content-Type": "application/json"},
    }


def test_history_lists_legacy_canonical_execution_shaped_filename_and_neighbors(
    tmp_path,
    monkeypatch,
):
    import app as app_module

    incident_id = "inc-canonical-shape"
    canonical_execution_id = "14141414141414141414141414141414"
    real_execution_id = "15151515151515151515151515151515"
    canonical_id = f"{incident_id}.execution-{canonical_execution_id}"
    monkeypatch.setattr(app_module.coordinator_module, "TRAJECTORIES_DIR", tmp_path)

    canonical = _record(canonical_id, canonical_execution_id, "legacy canonical")
    canonical.pop("execution_id")
    (tmp_path / f"{canonical_id}.json").write_text(
        json.dumps(canonical),
        encoding="utf-8",
    )
    _write_record(
        tmp_path,
        incident_id,
        real_execution_id,
        "neighboring execution",
        filename=f"{incident_id}.execution-{real_execution_id}.json",
    )

    client = _client()
    canonical_detail = client.get(f"/incidents/{canonical_id}")
    listing = client.get("/incidents/history")
    real_detail = client.get(
        f"/incidents/{incident_id}?execution_id={real_execution_id}"
    )

    assert canonical_detail.status_code == 200
    assert canonical_detail.json()["record_type"] == "canonical"
    assert canonical_detail.json()["execution_id"] is None
    assert listing.status_code == 200
    assert {
        (item["incident_id"], item["execution_id"], item["record_type"])
        for item in listing.json()["items"]
    } == {
        (canonical_id, None, "canonical"),
        (incident_id, real_execution_id, "execution"),
    }
    assert real_detail.status_code == 200
    assert real_detail.json()["record_type"] == "execution"
    assert real_detail.json()["triage"]["title"] == "neighboring execution"

    malformed_execution_id = "16161616161616161616161616161616"
    malformed = _record(
        incident_id,
        "17171717171717171717171717171717",
        "mismatched execution identity",
    )
    (tmp_path / f"{incident_id}.execution-{malformed_execution_id}.json").write_text(
        json.dumps(malformed),
        encoding="utf-8",
    )
    assert client.get("/incidents/history").status_code == 503
    assert (
        client.get(
            f"/incidents/{incident_id}?execution_id={malformed_execution_id}"
        ).status_code
        == 503
    )
    assert client.get(f"/incidents/{canonical_id}").status_code == 200


def test_history_lists_legacy_partial_shaped_canonical(tmp_path, monkeypatch):
    import app as app_module

    incident_id = "inc-partial-shape"
    partial_execution_id = "18181818181818181818181818181818"
    canonical_id = f"{incident_id}.partial-{partial_execution_id}"
    monkeypatch.setattr(
        app_module.coordinator_module,
        "TRAJECTORIES_DIR",
        tmp_path,
    )

    canonical = _record(canonical_id, partial_execution_id, "legacy canonical")
    canonical.pop("execution_id")
    (tmp_path / f"{canonical_id}.json").write_text(
        json.dumps(canonical),
        encoding="utf-8",
    )
    client = _client()
    detail = client.get(f"/incidents/{canonical_id}")
    listing = client.get("/incidents/history")

    assert detail.status_code == 200
    assert detail.json()["record_type"] == "canonical"
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["incident_id"] == canonical_id


def test_history_reader_is_import_safe_in_a_fresh_process(tmp_path):
    root = tmp_path / "fresh-store"
    incident_id = "inc-fresh-process"
    canonical_execution = "77777777777777777777777777777777"
    execution_id = "88888888888888888888888888888888"
    _write_record(root, incident_id, canonical_execution, "canonical")
    _write_record(
        root,
        incident_id,
        execution_id,
        "execution",
        filename=f"{incident_id}.execution-{execution_id}.json",
    )

    script = """
import json
import sys

from agents.incident_history import list_incident_history, read_incident_history

assert "agents.coordinator" not in sys.modules
assert "agents.verifier" not in sys.modules
page = list_incident_history(limit=10, offset=0)
canonical = read_incident_history("inc-fresh-process")
execution = read_incident_history(
    "inc-fresh-process",
    execution_id="88888888888888888888888888888888",
)
print(json.dumps({
    "total": page["total"],
    "types": sorted(item["record_type"] for item in page["items"]),
    "canonical": canonical["triage"]["title"],
    "execution": execution["triage"]["title"],
}))
"""
    environment = {
        "PYTHONPATH": str(REPO_ROOT),
        "TRAJECTORIES_DIR": str(root),
        "PATH": os.environ.get("PATH", ""),
    }
    if "SystemRoot" in os.environ:
        environment["SystemRoot"] = os.environ["SystemRoot"]
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "total": 2,
        "types": ["canonical", "execution"],
        "canonical": "canonical",
        "execution": "execution",
    }


def test_history_reads_records_without_agent_or_live_hooks(tmp_path, monkeypatch):
    import agents.coordinator as coordinator_module
    import agents.verifier as verifier_module
    import app as app_module

    incident_id = "inc-read-only"
    execution_id = "99999999999999999999999999999999"
    monkeypatch.setattr(coordinator_module, "TRAJECTORIES_DIR", tmp_path)
    _write_record(tmp_path, incident_id, execution_id, "persisted")
    forbidden = MagicMock(side_effect=AssertionError("live workflow must not run"))
    monkeypatch.setattr(coordinator_module, "handle_incident", forbidden)
    monkeypatch.setattr(coordinator_module, "call_agent", forbidden)
    monkeypatch.setattr(verifier_module, "verify_environment", forbidden)
    monkeypatch.setattr(app_module.subprocess, "run", forbidden)

    client = _client()
    assert client.get("/incidents/history").status_code == 200
    assert client.get(f"/incidents/{incident_id}").status_code == 200
    forbidden.assert_not_called()
