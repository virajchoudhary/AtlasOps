import asyncio
import gc
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from httpx import ASGITransport, AsyncClient

from agents.correlator import IncidentCorrelator
from agents.incident_admission import IncidentAdmissionStore
from agents.incident_dispatch import IncidentDispatcher


def _webhook_payload(alertname: str, fingerprint: str, starts_at: str) -> dict:
    labels = {
        "alertname": alertname,
        "namespace": "default",
        "service": "frontend",
    }
    return {
        "status": "firing",
        "commonLabels": labels,
        "alerts": [
            {
                "status": "firing",
                "fingerprint": fingerprint,
                "startsAt": starts_at,
                "labels": labels,
            }
        ],
    }


@pytest.mark.asyncio
async def test_completed_occurrence_is_not_replayed_after_fresh_instances(tmp_path):
    payload = _webhook_payload(
        "HighCPU",
        "restart-fingerprint",
        "2026-10-15T10:00:00Z",
    )
    calls: list[str] = []

    async def handler(_payload, *, incident_id):
        calls.append(incident_id)
        return {"incident_id": incident_id}

    first_correlator = IncidentCorrelator()
    first_candidate = first_correlator.ingest(payload)
    database_path = tmp_path / "admissions.sqlite3"
    first_dispatcher = IncidentDispatcher(first_correlator, database_path)
    first_submission = first_dispatcher.submit(first_candidate, handler)
    assert await first_submission.wait() == {"incident_id": first_candidate.incident_id}
    first_correlator.reset()

    replay_correlator = IncidentCorrelator()
    replay_candidate = replay_correlator.ingest(payload)
    replay_dispatcher = IncidentDispatcher(replay_correlator, database_path)
    replay_submission = replay_dispatcher.submit(replay_candidate, handler)

    assert replay_submission.state == "duplicate"
    assert replay_candidate.incident_id != first_candidate.incident_id
    assert replay_submission.incident_id == first_candidate.incident_id
    assert replay_correlator.get_active()[0]["active_processing"] is False
    assert calls == [first_candidate.incident_id]


@pytest.mark.parametrize("outcome", ["completed", "failed", "prestart-cancelled"])
def test_occurrence_admission_survives_fresh_subprocesses(tmp_path, outcome):
    database_path = tmp_path / "admissions.sqlite3"
    calls_path = tmp_path / "handler-calls.txt"
    payload = _webhook_payload(
        "HighCPU",
        "subprocess-fingerprint",
        "2026-10-15T10:00:00Z",
    )
    script = """
import asyncio
import json
import sys
from pathlib import Path

from agents.correlator import IncidentCorrelator
from agents.incident_dispatch import IncidentDispatcher

database_path, payload_text, calls_path, outcome = sys.argv[1:]
payload = json.loads(payload_text)
async def handler(_payload, *, incident_id):
    with Path(calls_path).open("a", encoding="utf-8") as output:
        output.write(incident_id + chr(10))
    if outcome == "failed":
        raise RuntimeError("synthetic subprocess handler failure")
    return {"incident_id": incident_id}

async def main():
    correlator = IncidentCorrelator()
    candidate = correlator.ingest(payload)
    dispatcher = IncidentDispatcher(correlator, database_path)
    submission = dispatcher.submit(candidate, handler)
    result = "duplicate" if submission.state == "duplicate" else "completed"
    if submission.state != "duplicate" and outcome == "prestart-cancelled":
        worker = dispatcher._queues[candidate.incident_id].worker
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            result = "cancelled"
    elif submission.completion is not None:
        try:
            await submission.wait()
        except RuntimeError:
            result = "failed"
    print(json.dumps({"state": submission.state, "incident_id": submission.incident_id, "outcome": result}))

asyncio.run(main())
"""

    def run_worker() -> dict:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(database_path),
                json.dumps(payload),
                str(calls_path),
                outcome,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(result.stdout.strip())

    first = run_worker()
    replay = run_worker()

    assert first["state"] == "started"
    assert replay["state"] == "duplicate"
    assert replay["incident_id"] == first["incident_id"]
    expected_calls = [] if outcome == "prestart-cancelled" else [first["incident_id"]]
    actual_calls = calls_path.read_text(encoding="utf-8").splitlines() if calls_path.exists() else []
    assert actual_calls == expected_calls


def test_separate_dispatcher_instances_race_at_durable_admission(tmp_path):
    database_path = tmp_path / "admissions.sqlite3"
    payload = _webhook_payload(
        "HighCPU",
        "race-fingerprint",
        "2026-10-15T10:00:00Z",
    )
    correlators = [IncidentCorrelator() for _ in range(2)]
    candidates = [correlator.ingest(payload) for correlator in correlators]
    dispatchers = [
        IncidentDispatcher(correlator, database_path) for correlator in correlators
    ]
    barrier = Barrier(2)
    calls: list[str] = []

    async def handler(_payload, *, incident_id):
        calls.append(incident_id)
        return {"incident_id": incident_id}

    def submit(index: int) -> tuple[str, str | None]:
        async def run():
            barrier.wait(timeout=10)
            submission = dispatchers[index].submit(candidates[index], handler)
            if submission.completion is not None:
                await submission.wait()
            return submission.state, submission.incident_id

        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))

    started_ids = [incident_id for state, incident_id in results if state == "started"]
    duplicate_ids = [incident_id for state, incident_id in results if state == "duplicate"]
    assert len(started_ids) == len(duplicate_ids) == 1
    assert duplicate_ids == started_ids
    assert calls == started_ids


@pytest.mark.asyncio
async def test_mixed_batch_filters_new_alerts_and_binds_their_original_anchor(tmp_path):
    from agents.coordinator import build_incident_anchors

    database_path = tmp_path / "admissions.sqlite3"
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(incident_correlator, database_path)
    duplicate_alert = {
        "status": "firing",
        "fingerprint": "duplicate-fingerprint",
        "startsAt": "2026-10-15T10:00:00Z",
        "labels": {"alertname": "DuplicateAlert", "namespace": "default", "service": "paymentservice"},
        "annotations": {"summary": "original first-alert context"},
    }
    initial_payload = {
        "status": "firing",
        "commonLabels": {"alertname": "BatchAlert", "namespace": "default"},
        "alerts": [duplicate_alert],
    }
    received: list[dict] = []

    async def handler(alert_payload, *, incident_id, incident_anchors=None):
        received.append(
            {
                "payload": alert_payload,
                "incident_id": incident_id,
                "incident_anchors": incident_anchors,
            }
        )
        return {"incident_id": incident_id}

    first_candidate = incident_correlator.ingest(initial_payload)
    first_submission = dispatcher.submit(
        first_candidate,
        handler,
        anchor_builder=build_incident_anchors,
    )
    await first_submission.wait()

    new_alert = {
        "status": "firing",
        "fingerprint": "new-fingerprint",
        "startsAt": "2026-10-15T10:01:00Z",
        "labels": {"alertname": "NewAlert", "namespace": "default", "service": "checkoutservice"},
        "annotations": {"summary": "new admitted-alert context"},
    }
    batch_payload = {
        "status": "resolved",
        "commonLabels": {
            "alertname": "CombinedEnvelope",
            "namespace": "default",
            "region": "west",
        },
        "alerts": [duplicate_alert, new_alert],
    }
    expected_anchors = build_incident_anchors(batch_payload, original_alert_index=1)
    batch_candidate = incident_correlator.ingest(batch_payload)
    batch_submission = dispatcher.submit(
        batch_candidate,
        handler,
        anchor_builder=build_incident_anchors,
    )
    await batch_submission.wait()

    assert len(received) == 2
    filtered = received[1]
    assert filtered["payload"]["alerts"] == [new_alert]
    assert filtered["payload"]["commonLabels"] == batch_payload["commonLabels"]
    assert filtered["payload"]["status"] == batch_payload["status"]
    expected_primary = ("checkoutservice", "new admitted-alert context")
    assert (
        expected_anchors["primary_service"],
        expected_anchors["original_description"],
    ) == expected_primary
    assert (
        filtered["incident_anchors"]["primary_service"],
        filtered["incident_anchors"]["original_description"],
    ) == expected_primary


@pytest.mark.parametrize("failure_point", ["admit", "mark_attempted"])
@pytest.mark.asyncio
async def test_storage_failure_fails_closed_before_handler(failure_point, monkeypatch, tmp_path):
    database_path = tmp_path / "admissions.sqlite3"
    incident_correlator = IncidentCorrelator()
    candidate = incident_correlator.ingest(
        _webhook_payload(
            "HighCPU",
            f"storage-{failure_point}",
            "2026-10-15T10:00:00Z",
        )
    )
    dispatcher = IncidentDispatcher(incident_correlator, database_path)
    calls = []

    async def handler(_payload, *, incident_id):
        calls.append(incident_id)

    def fail(*_args, **_kwargs):
        raise OSError("synthetic admission storage failure")

    monkeypatch.setattr(IncidentAdmissionStore, failure_point, fail)
    if failure_point == "admit":
        with pytest.raises(OSError, match="synthetic admission storage failure"):
            dispatcher.submit(candidate, handler)
    else:
        submission = dispatcher.submit(candidate, handler)
        with pytest.raises(OSError, match="synthetic admission storage failure"):
            await submission.wait()
        await asyncio.sleep(0)
        with sqlite3.connect(database_path) as connection:
            state = connection.execute(
                "SELECT state FROM incident_occurrence_admissions"
            ).fetchone()[0]
        assert state == "queued"

    assert calls == []
    assert incident_correlator.get_active()[0]["active_processing"] is False


def test_admission_rejects_memory_database_and_token_rollback_is_state_scoped(tmp_path):
    with pytest.raises(ValueError, match="on-disk SQLite path"):
        IncidentDispatcher(IncidentCorrelator(), ":memory:")

    database_path = tmp_path / "admissions.sqlite3"
    store = IncidentAdmissionStore(database_path)
    admission = store.admit("inc-test", ("queued-key", "attempted-key"))
    assert admission.admission_token is not None
    store.mark_attempted(admission.admission_token, ("attempted-key",))
    store.rollback_queued("different-token")
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT occurrence_key, state
            FROM incident_occurrence_admissions
            ORDER BY occurrence_key
            """
        ).fetchall()
    assert rows == [("attempted-key", "attempted"), ("queued-key", "queued")]

    store.rollback_queued(admission.admission_token)
    with sqlite3.connect(database_path) as connection:
        remaining = connection.execute(
            "SELECT occurrence_key, state FROM incident_occurrence_admissions"
        ).fetchall()
    assert remaining == [("attempted-key", "attempted")]


@pytest.mark.asyncio
async def test_default_admission_path_is_lazy_and_pinned(monkeypatch, tmp_path):
    runtime_data_dir = "runtime-data"
    monkeypatch.setenv("ATLASOPS_RUNTIME_DATA_DIR", runtime_data_dir)
    monkeypatch.chdir(tmp_path)
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(incident_correlator)
    assert dispatcher._admission_store is None
    assert not (tmp_path / runtime_data_dir).exists()

    async def handler(_payload, *, incident_id):
        return {"incident_id": incident_id}

    payload = _webhook_payload(
        "HighCPU",
        "lazy-path-first",
        "2026-10-15T10:00:00Z",
    )
    first_candidate = incident_correlator.ingest(payload)
    first_submission = dispatcher.submit(first_candidate, handler)
    await first_submission.wait()
    database_path = tmp_path / runtime_data_dir / "incident-admissions.sqlite3"
    assert database_path.is_file()
    assert dispatcher._admission_store._path == database_path.absolute()

    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    replay_correlator = IncidentCorrelator()
    replay_candidate = replay_correlator.ingest(payload)
    replay = dispatcher.submit(replay_candidate, handler)

    assert replay.state == "duplicate"
    assert replay.incident_id == first_submission.incident_id
    assert replay_correlator.get_active()[0]["active_processing"] is False
    assert not (other_cwd / runtime_data_dir).exists()
    assert dispatcher._admission_store._path == database_path.absolute()


@pytest.mark.parametrize("route", ["root", "coordinator"])
@pytest.mark.parametrize(
    ("body", "expected_status", "expected_detail", "marker"),
    [
        (
            b'{"marker":"untrusted-body-marker",',
            400,
            "Invalid JSON payload",
            "untrusted-body-marker",
        ),
        (
            b'{"alerts":[123],"marker":"untrusted-body-marker"}',
            422,
            "Invalid Alertmanager webhook payload",
            "untrusted-body-marker",
        ),
        (
            b'{"alerts":[{"labels":{}}],"commonLabels":[],"marker":"untrusted-body-marker"}',
            422,
            "Invalid Alertmanager webhook payload",
            "untrusted-body-marker",
        ),
    ],
)
@pytest.mark.asyncio
async def test_webhook_uses_shared_json_and_schema_boundary_without_dispatch(
    monkeypatch,
    route,
    body,
    expected_status,
    expected_detail,
    marker,
):
    import app as app_module
    from agents import coordinator

    class UnexpectedDispatcher:
        def submit(self, **_kwargs):
            raise AssertionError("invalid webhook payload reached dispatch")

    incident_correlator = IncidentCorrelator()
    monkeypatch.setattr(app_module, "correlator", incident_correlator)
    monkeypatch.setattr(coordinator, "correlator", incident_correlator)
    monkeypatch.setattr(app_module, "incident_dispatcher", UnexpectedDispatcher())
    monkeypatch.setattr(coordinator, "incident_dispatcher", UnexpectedDispatcher())
    monkeypatch.setattr(app_module, "_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(coordinator, "_WEBHOOK_SECRET", "test-webhook-secret")
    application = app_module.app if route == "root" else coordinator.app

    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/webhook",
            content=body,
            headers={
                "Authorization": "Bearer test-webhook-secret",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
    assert marker not in response.text
    assert "test-webhook-secret" not in response.text


@pytest.mark.parametrize("route", ["root", "coordinator"])
@pytest.mark.asyncio
async def test_empty_alertmanager_heartbeats_are_noops_without_mutating_correlation(
    monkeypatch,
    route,
    tmp_path,
):
    from unittest.mock import AsyncMock

    import agents.correlator as correlator_module
    import app as app_module
    from agents import coordinator

    incident_correlator = IncidentCorrelator()
    existing = incident_correlator.ingest(
        _webhook_payload(
            "ExistingAlert",
            "existing-fingerprint",
            "2026-10-15T10:00:00Z",
        )
    )
    incident_correlator.mark_processing(existing.incident_id, False)
    before_heartbeats = incident_correlator.get_active()
    incident_dispatcher = IncidentDispatcher(
        incident_correlator,
        tmp_path / "admissions.sqlite3",
    )
    handler = AsyncMock(return_value={"incident_id": existing.incident_id})
    monkeypatch.setattr(correlator_module, "correlator", incident_correlator)
    monkeypatch.setattr(app_module, "correlator", incident_correlator)
    monkeypatch.setattr(coordinator, "correlator", incident_correlator)
    monkeypatch.setattr(app_module, "incident_dispatcher", incident_dispatcher)
    monkeypatch.setattr(coordinator, "incident_dispatcher", incident_dispatcher)
    monkeypatch.setattr(coordinator, "handle_incident", handler)
    monkeypatch.setattr(app_module, "_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(coordinator, "_WEBHOOK_SECRET", "test-webhook-secret")
    application = app_module.app if route == "root" else coordinator.app

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        for payload in ({}, {"alerts": []}, {}):
            response = await client.post(
                "/webhook",
                json=payload,
                headers={"Authorization": "Bearer test-webhook-secret"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is True
            assert body["incident_id"] is None
            assert body["dispatched"] is False
            if route == "root":
                assert body["queued"] is False
                assert body["dispatch_state"] == "noop"
            else:
                assert body["correlated"] is False
            assert incident_correlator.get_active() == before_heartbeats
            await asyncio.sleep(0)
            handler.assert_not_awaited()


@pytest.mark.parametrize("route", ["root", "coordinator"])
@pytest.mark.asyncio
async def test_webhook_authentication_still_precedes_json_validation(monkeypatch, route):
    import app as app_module
    from agents import coordinator

    monkeypatch.setattr(app_module, "_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(coordinator, "_WEBHOOK_SECRET", "test-webhook-secret")
    application = app_module.app if route == "root" else coordinator.app

    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/webhook",
            content=b'{"marker":"untrusted-body-marker",',
            headers={"Authorization": "Bearer wrong-secret"},
        )

    assert response.status_code == 401
    assert "untrusted-body-marker" not in response.text
    assert "wrong-secret" not in response.text
    assert "test-webhook-secret" not in response.text


@pytest.mark.parametrize("route", ["root", "coordinator"])
@pytest.mark.asyncio
async def test_unconfigured_webhook_still_returns_503_before_payload_validation(
    monkeypatch,
    route,
):
    import app as app_module
    from agents import coordinator

    monkeypatch.setattr(app_module, "_WEBHOOK_SECRET", "")
    monkeypatch.setattr(coordinator, "_WEBHOOK_SECRET", "")
    application = app_module.app if route == "root" else coordinator.app

    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/webhook",
            content=b'{"alerts":[123],',
            headers={"Authorization": "Bearer unused"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "ALERTMANAGER_WEBHOOK_SECRET is required"


@pytest.mark.parametrize("route", ["root", "coordinator"])
@pytest.mark.asyncio
async def test_webhook_rejects_body_over_limit_before_dispatch(monkeypatch, route):
    import app as app_module
    from agents import coordinator

    class UnexpectedDispatcher:
        def submit(self, **_kwargs):
            raise AssertionError("oversize webhook payload reached dispatch")

    monkeypatch.setattr(app_module, "incident_dispatcher", UnexpectedDispatcher())
    monkeypatch.setattr(coordinator, "incident_dispatcher", UnexpectedDispatcher())
    monkeypatch.setattr(app_module, "_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(coordinator, "_WEBHOOK_SECRET", "test-webhook-secret")
    application = app_module.app if route == "root" else coordinator.app
    body = b" " * (1_048_576 + 1)

    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/webhook",
            content=body,
            headers={"Authorization": "Bearer test-webhook-secret"},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "Alertmanager webhook payload exceeds size limit"
    assert "test-webhook-secret" not in response.text


@pytest.mark.parametrize("route", ["root", "coordinator"])
@pytest.mark.asyncio
async def test_webhook_queues_each_new_related_occurrence_once(monkeypatch, route, tmp_path):
    import agents.correlator as correlator_module
    import app as app_module
    from agents import coordinator

    incident_correlator = IncidentCorrelator()
    incident_dispatcher = IncidentDispatcher(
        incident_correlator,
        tmp_path / "admissions.sqlite3",
    )
    monkeypatch.setattr(correlator_module, "correlator", incident_correlator)
    monkeypatch.setattr(app_module, "correlator", incident_correlator)
    monkeypatch.setattr(coordinator, "correlator", incident_correlator)
    monkeypatch.setattr(app_module, "incident_dispatcher", incident_dispatcher)
    monkeypatch.setattr(coordinator, "incident_dispatcher", incident_dispatcher)
    monkeypatch.setattr(app_module, "_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(coordinator, "_WEBHOOK_SECRET", "test-webhook-secret")

    ingested_results = asyncio.Queue()
    original_ingest = incident_correlator.ingest

    def tracked_ingest(payload):
        result = original_ingest(payload)
        ingested_results.put_nowait(result)
        return result

    monkeypatch.setattr(incident_correlator, "ingest", tracked_ingest)

    handler_started = asyncio.Event()
    follow_up_completed = asyncio.Event()
    release_handler = asyncio.Event()
    active_handlers = 0
    maximum_active_handlers = 0
    handled_fingerprints: list[str] = []
    anchors_by_fingerprint: dict[str, dict | None] = {}

    async def controlled_handler(payload, *, incident_id, incident_anchors=None):
        nonlocal active_handlers, maximum_active_handlers
        fingerprint = payload["alerts"][0]["fingerprint"]
        active_handlers += 1
        maximum_active_handlers = max(maximum_active_handlers, active_handlers)
        handled_fingerprints.append(fingerprint)
        anchors_by_fingerprint[fingerprint] = incident_anchors
        try:
            if fingerprint == "fingerprint-a":
                handler_started.set()
                await release_handler.wait()
            return {"incident_id": incident_id}
        finally:
            active_handlers -= 1
            if fingerprint == "fingerprint-b":
                follow_up_completed.set()

    monkeypatch.setattr(coordinator, "handle_incident", controlled_handler)
    application = app_module.app if route == "root" else coordinator.app
    headers = {"Authorization": "Bearer test-webhook-secret"}
    first_payload = _webhook_payload(
        "HighCPU",
        "fingerprint-a",
        "2026-10-15T10:00:00Z",
    )
    follow_up_payload = _webhook_payload(
        "High5xx",
        "fingerprint-b",
        "2026-10-15T10:01:00Z",
    )

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        request_tasks = []
        try:
            first_task = asyncio.create_task(
                client.post("/webhook", json=first_payload, headers=headers)
            )
            request_tasks.append(first_task)
            await asyncio.wait_for(handler_started.wait(), timeout=5)
            await asyncio.wait_for(ingested_results.get(), timeout=5)

            if route == "root":
                first_response = await asyncio.wait_for(first_task, timeout=5)
                assert first_response.status_code == 200
                assert first_response.json()["dispatch_state"] == "started"
            else:
                assert not first_task.done()

            second_task = asyncio.create_task(
                client.post(
                    "/webhook",
                    json=follow_up_payload,
                    headers=headers,
                )
            )
            request_tasks.append(second_task)
            await asyncio.wait_for(ingested_results.get(), timeout=5)

            duplicate_task = asyncio.create_task(
                client.post(
                    "/webhook",
                    json=follow_up_payload,
                    headers=headers,
                )
            )
            request_tasks.append(duplicate_task)
            await asyncio.wait_for(ingested_results.get(), timeout=5)
            duplicate_response = await asyncio.wait_for(duplicate_task, timeout=5)
            assert duplicate_response.status_code == 200
            assert incident_correlator.get_active()[0]["alert_count"] == 2
            assert incident_correlator.get_active()[0]["active_processing"] is True
            assert handled_fingerprints == ["fingerprint-a"]
            assert maximum_active_handlers == 1

            if route == "root":
                second_response = await asyncio.wait_for(second_task, timeout=5)
                assert second_response.json()["dispatch_state"] == "queued"
                assert second_response.json()["queued"] is True
                assert second_response.json()["dispatched"] is False
                assert duplicate_response.json()["dispatch_state"] == "duplicate"
            else:
                assert not second_task.done()
                assert duplicate_response.json()["dispatched"] is False

            release_handler.set()
            responses = await asyncio.wait_for(
                asyncio.gather(*request_tasks),
                timeout=10,
            )
            await asyncio.wait_for(follow_up_completed.wait(), timeout=5)
        finally:
            release_handler.set()
            if request_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*request_tasks, return_exceptions=True),
                        timeout=5,
                    )
                except TimeoutError:
                    pass

    assert all(response.status_code == 200 for response in responses)
    assert handled_fingerprints == ["fingerprint-a", "fingerprint-b"]
    assert maximum_active_handlers == 1
    assert [
        anchors_by_fingerprint[fingerprint]["primary_service"]
        for fingerprint in ("fingerprint-a", "fingerprint-b")
    ] == ["frontend", "frontend"]

    if route == "coordinator":
        assert responses[0].json()["dispatched"] is True
        assert responses[1].json()["dispatched"] is True
        assert responses[2].json()["dispatched"] is False


@pytest.mark.parametrize("route", ["root", "coordinator"])
@pytest.mark.asyncio
async def test_webhook_fresh_route_replay_returns_original_incident_id(
    monkeypatch,
    route,
    tmp_path,
):
    import agents.correlator as correlator_module
    import app as app_module
    from agents import coordinator

    database_path = tmp_path / "admissions.sqlite3"
    calls: list[str] = []
    handler_finished = asyncio.Event()

    async def handler(_payload, *, incident_id, incident_anchors=None):
        calls.append(incident_id)
        handler_finished.set()
        return {"incident_id": incident_id}

    def install_runtime():
        incident_correlator = IncidentCorrelator()
        incident_dispatcher = IncidentDispatcher(incident_correlator, database_path)
        monkeypatch.setattr(correlator_module, "correlator", incident_correlator)
        monkeypatch.setattr(app_module, "correlator", incident_correlator)
        monkeypatch.setattr(coordinator, "correlator", incident_correlator)
        monkeypatch.setattr(app_module, "incident_dispatcher", incident_dispatcher)
        monkeypatch.setattr(coordinator, "incident_dispatcher", incident_dispatcher)

    install_runtime()
    monkeypatch.setattr(coordinator, "handle_incident", handler)
    monkeypatch.setattr(app_module, "_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(coordinator, "_WEBHOOK_SECRET", "test-webhook-secret")
    application = app_module.app if route == "root" else coordinator.app
    payload = _webhook_payload(
        "HighCPU",
        "route-replay-fingerprint",
        "2026-10-15T10:00:00Z",
    )
    headers = {"Authorization": "Bearer test-webhook-secret"}

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        first = await client.post("/webhook", json=payload, headers=headers)
        assert first.status_code == 200
        if route == "root":
            assert first.json()["dispatch_state"] == "started"
            await asyncio.wait_for(handler_finished.wait(), timeout=5)
        first_incident_id = first.json()["incident_id"]

        install_runtime()
        replay = await client.post("/webhook", json=payload, headers=headers)

    assert replay.status_code == 200
    assert replay.json()["incident_id"] == first_incident_id
    assert replay.json()["dispatched"] is False
    if route == "root":
        assert replay.json()["dispatch_state"] == "duplicate"
    assert calls == [first_incident_id]


@pytest.mark.asyncio
async def test_cancelled_request_wait_does_not_cancel_owned_worker(tmp_path):
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(
        incident_correlator,
        tmp_path / "admissions.sqlite3",
    )
    payload = {"commonLabels": {"service": "frontend"}}
    candidate = incident_correlator.ingest(payload)
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()

    async def controlled_handler(_payload, *, incident_id):
        handler_started.set()
        await release_handler.wait()
        return {"incident_id": incident_id}

    submission = dispatcher.submit(candidate, controlled_handler)
    request_wait = asyncio.create_task(submission.wait())
    await handler_started.wait()

    request_wait.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_wait
    assert not submission.completion.cancelled()

    release_handler.set()
    assert await submission.wait() == {"incident_id": candidate.incident_id}


@pytest.mark.asyncio
async def test_cancelled_admission_does_not_lose_follow_up_occurrence(tmp_path):
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(
        incident_correlator,
        tmp_path / "admissions.sqlite3",
    )
    first_payload = _webhook_payload(
        "HighCPU",
        "fingerprint-a",
        "2026-10-15T10:00:00Z",
    )
    first_correlation = incident_correlator.ingest(first_payload)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    follow_up_ingested = asyncio.Event()
    calls: list[str] = []

    async def handler(payload, *, incident_id):
        fingerprint = payload["alerts"][0]["fingerprint"]
        calls.append(fingerprint)
        if fingerprint == "fingerprint-a":
            first_started.set()
            await release_first.wait()
        return {"incident_id": incident_id, "fingerprint": fingerprint}

    first_submission = dispatcher.submit(first_correlation, handler)
    await first_started.wait()

    follow_up_payload = _webhook_payload(
        "High5xx",
        "fingerprint-b",
        "2026-10-15T10:01:00Z",
    )
    submitted = []

    async def submit_follow_up():
        correlation = incident_correlator.ingest(follow_up_payload)
        follow_up_ingested.set()
        submission = dispatcher.submit(correlation, handler)
        submitted.append(submission)
        return await submission.wait()

    admission_lock = getattr(dispatcher, "_lock", None)
    held_async_lock = isinstance(admission_lock, asyncio.Lock)
    if held_async_lock:
        await admission_lock.acquire()

    follow_up_request = asyncio.create_task(submit_follow_up())
    try:
        await follow_up_ingested.wait()
        await asyncio.sleep(0)
        follow_up_request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await follow_up_request

        assert len(submitted) == 1, "admission must finish before request cancellation"
        assert not submitted[0].completion.cancelled()
    finally:
        if held_async_lock and admission_lock.locked():
            admission_lock.release()
        release_first.set()

    assert await first_submission.wait() == {
        "incident_id": first_correlation.incident_id,
        "fingerprint": "fingerprint-a",
    }
    assert await submitted[0].wait() == {
        "incident_id": first_correlation.incident_id,
        "fingerprint": "fingerprint-b",
    }
    replay = incident_correlator.ingest(follow_up_payload)
    assert replay.occurrence_refs
    assert calls == ["fingerprint-a", "fingerprint-b"]
    assert incident_correlator.get_active()[0]["active_processing"] is False


@pytest.mark.asyncio
async def test_cancelled_worker_settles_queued_completions_and_releases_queue(tmp_path):
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(
        incident_correlator,
        tmp_path / "admissions.sqlite3",
    )
    first_payload = _webhook_payload(
        "HighCPU",
        "fingerprint-a",
        "2026-10-15T10:00:00Z",
    )
    first_correlation = incident_correlator.ingest(first_payload)
    first_started = asyncio.Event()
    calls: list[str] = []

    async def handler(payload, *, incident_id):
        fingerprint = payload["alerts"][0]["fingerprint"]
        calls.append(fingerprint)
        if fingerprint == "fingerprint-a":
            first_started.set()
            await asyncio.Event().wait()
        return {"incident_id": incident_id, "fingerprint": fingerprint}

    first_submission = dispatcher.submit(first_correlation, handler)
    await first_started.wait()

    queued_payload = _webhook_payload(
        "High5xx",
        "fingerprint-b",
        "2026-10-15T10:01:00Z",
    )
    queued_correlation = incident_correlator.ingest(queued_payload)
    queued_submission = dispatcher.submit(queued_correlation, handler)
    worker = dispatcher._queues[first_correlation.incident_id].worker

    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    assert first_submission.completion.cancelled()
    assert queued_submission.completion.cancelled()
    assert first_correlation.incident_id not in dispatcher._queues
    assert incident_correlator.get_active()[0]["active_processing"] is False
    assert incident_correlator.ingest(queued_payload).occurrence_refs
    assert calls == ["fingerprint-a"]

    replay_correlator = IncidentCorrelator()
    replay_dispatcher = IncidentDispatcher(
        replay_correlator,
        tmp_path / "admissions.sqlite3",
    )
    first_replay = replay_correlator.ingest(first_payload)
    queued_replay = replay_correlator.ingest(queued_payload)
    first_duplicate = replay_dispatcher.submit(first_replay, handler)
    queued_duplicate = replay_dispatcher.submit(queued_replay, handler)
    assert first_duplicate.state == "duplicate"
    assert first_duplicate.incident_id == first_correlation.incident_id
    assert queued_duplicate.state == "duplicate"
    assert queued_duplicate.incident_id == first_correlation.incident_id

    next_payload = _webhook_payload(
        "HighLatency",
        "fingerprint-c",
        "2026-10-15T10:02:00Z",
    )
    next_correlation = incident_correlator.ingest(next_payload)
    next_submission = dispatcher.submit(next_correlation, handler)
    assert await next_submission.wait() == {
        "incident_id": first_correlation.incident_id,
        "fingerprint": "fingerprint-c",
    }
    assert calls == ["fingerprint-a", "fingerprint-c"]
    assert incident_correlator.get_active()[0]["active_processing"] is False


@pytest.mark.asyncio
async def test_worker_cancelled_before_first_step_settles_and_allows_later_occurrences(
    monkeypatch,
    tmp_path,
):
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(
        incident_correlator,
        tmp_path / "admissions.sqlite3",
    )
    payload = _webhook_payload(
        "HighCPU",
        "fingerprint-a",
        "2026-10-15T10:00:00Z",
    )
    correlation = incident_correlator.ingest(payload)
    calls: list[str] = []

    async def handler(alert_payload, *, incident_id):
        fingerprint = alert_payload["alerts"][0]["fingerprint"]
        calls.append(fingerprint)
        return {"incident_id": incident_id, "fingerprint": fingerprint}

    loop = asyncio.get_running_loop()
    create_task = loop.create_task
    worker_tasks = []

    def capture_worker(coro, *, name=None, context=None):
        task = create_task(coro, name=name, context=context)
        if name == f"incident-dispatch-{correlation.incident_id}":
            worker_tasks.append(task)
        return task

    monkeypatch.setattr(loop, "create_task", capture_worker)
    submission = dispatcher.submit(correlation, handler)

    assert submission.state == "started"
    assert len(worker_tasks) == 1
    worker_tasks[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker_tasks[0]
    await asyncio.sleep(0)

    assert submission.completion.done()
    assert submission.completion.cancelled()
    assert incident_correlator.get_active()[0]["active_processing"] is False
    replay_candidate = incident_correlator.ingest(payload)
    assert replay_candidate.occurrence_refs
    replay_submission = dispatcher.submit(replay_candidate, handler)
    assert replay_submission.state == "duplicate"
    assert replay_submission.incident_id == correlation.incident_id

    follow_up = _webhook_payload(
        "High5xx",
        "fingerprint-b",
        "2026-10-15T10:01:00Z",
    )
    follow_up_correlation = incident_correlator.ingest(follow_up)
    assert follow_up_correlation.incident_id == correlation.incident_id
    follow_up_submission = dispatcher.submit(follow_up_correlation, handler)

    assert await asyncio.wait_for(follow_up_submission.wait(), timeout=5) == {
        "incident_id": correlation.incident_id,
        "fingerprint": "fingerprint-b",
    }
    assert calls == ["fingerprint-b"]
    assert incident_correlator.get_active()[0]["active_processing"] is False


@pytest.mark.asyncio
async def test_prestart_cancel_preserves_occurrence_submitted_before_done_callback(
    monkeypatch,
    tmp_path,
):
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(
        incident_correlator,
        tmp_path / "admissions.sqlite3",
    )
    cancelled_payload = _webhook_payload(
        "HighCPU",
        "race-a",
        "2026-10-15T10:00:00Z",
    )
    cancelled_correlation = incident_correlator.ingest(cancelled_payload)
    calls: list[str] = []

    async def handler(payload, *, incident_id):
        fingerprint = payload["alerts"][0]["fingerprint"]
        calls.append(fingerprint)
        return {"incident_id": incident_id, "fingerprint": fingerprint}

    loop = asyncio.get_running_loop()
    create_task = loop.create_task
    worker_tasks = []

    def capture_worker(coro, *, name=None, context=None):
        task = create_task(coro, name=name, context=context)
        if name == f"incident-dispatch-{cancelled_correlation.incident_id}":
            worker_tasks.append(task)
        return task

    monkeypatch.setattr(loop, "create_task", capture_worker)
    cancelled_submission = dispatcher.submit(cancelled_correlation, handler)
    assert cancelled_submission.state == "started"
    assert len(worker_tasks) == 1

    worker_tasks[0].cancel()
    assert not worker_tasks[0].done()
    follow_up_payload = _webhook_payload(
        "High5xx",
        "race-b",
        "2026-10-15T10:01:00Z",
    )
    follow_up_correlation = incident_correlator.ingest(follow_up_payload)
    assert follow_up_correlation.incident_id == cancelled_correlation.incident_id
    follow_up_submission = dispatcher.submit(follow_up_correlation, handler)
    assert follow_up_submission.state == "queued"

    with pytest.raises(asyncio.CancelledError):
        await worker_tasks[0]

    assert cancelled_submission.completion.cancelled()
    assert await asyncio.wait_for(follow_up_submission.wait(), timeout=5) == {
        "incident_id": cancelled_correlation.incident_id,
        "fingerprint": "race-b",
    }
    cancelled_replay = incident_correlator.ingest(cancelled_payload)
    follow_up_replay = incident_correlator.ingest(follow_up_payload)
    assert dispatcher.submit(cancelled_replay, handler).state == "duplicate"
    assert dispatcher.submit(follow_up_replay, handler).state == "duplicate"

    later_payload = _webhook_payload(
        "HighLatency",
        "race-c",
        "2026-10-15T10:02:00Z",
    )
    later_correlation = incident_correlator.ingest(later_payload)
    later_submission = dispatcher.submit(later_correlation, handler)
    assert await asyncio.wait_for(later_submission.wait(), timeout=5) == {
        "incident_id": cancelled_correlation.incident_id,
        "fingerprint": "race-c",
    }
    assert calls == ["race-b", "race-c"]
    assert incident_correlator.get_active()[0]["active_processing"] is False


@pytest.mark.asyncio
async def test_worker_creation_failure_rolls_back_queued_admission(monkeypatch, tmp_path):
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(
        incident_correlator,
        tmp_path / "admissions.sqlite3",
    )
    payload = _webhook_payload(
        "HighCPU",
        "fingerprint-a",
        "2026-10-15T10:00:00Z",
    )
    correlation = incident_correlator.ingest(payload)
    calls: list[str] = []

    async def handler(alert_payload, *, incident_id):
        fingerprint = alert_payload["alerts"][0]["fingerprint"]
        calls.append(fingerprint)
        return {"incident_id": incident_id, "fingerprint": fingerprint}

    loop = asyncio.get_running_loop()
    create_task = loop.create_task

    def reject_worker(coro, *, name=None, context=None):
        if name == f"incident-dispatch-{correlation.incident_id}":
            coro.close()
            raise RuntimeError("synthetic task creation failure")
        return create_task(coro, name=name, context=context)

    with monkeypatch.context() as patch:
        patch.setattr(loop, "create_task", reject_worker)
        with pytest.raises(RuntimeError, match="synthetic task creation failure"):
            dispatcher.submit(correlation, handler)

    assert incident_correlator.get_active()[0]["active_processing"] is False
    retry_correlation = incident_correlator.ingest(payload)
    assert retry_correlation.incident_id == correlation.incident_id
    assert retry_correlation.occurrence_refs == correlation.occurrence_refs

    retry_submission = dispatcher.submit(retry_correlation, handler)
    assert await asyncio.wait_for(retry_submission.wait(), timeout=5) == {
        "incident_id": correlation.incident_id,
        "fingerprint": "fingerprint-a",
    }
    assert calls == ["fingerprint-a"]


@pytest.mark.asyncio
async def test_anchor_preparation_failure_rolls_back_only_new_occurrence_and_can_replay(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "admissions.sqlite3"
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(incident_correlator, database_path)
    duplicate_alert = {
        "status": "firing",
        "fingerprint": "duplicate-fingerprint",
        "startsAt": "2026-10-15T10:00:00Z",
        "labels": {"alertname": "DuplicateAlert", "namespace": "default", "service": "paymentservice"},
        "annotations": {"summary": "original first-alert context"},
    }
    new_alert = {
        "status": "firing",
        "fingerprint": "new-fingerprint",
        "startsAt": "2026-10-15T10:01:00Z",
        "labels": {"alertname": "NewAlert", "namespace": "default", "service": "checkoutservice"},
        "annotations": {"summary": "new admitted-alert context"},
    }
    batch_payload = {
        "status": "resolved",
        "commonLabels": {
            "alertname": "CombinedEnvelope",
            "namespace": "default",
            "region": "west",
        },
        "alerts": [duplicate_alert, new_alert],
    }
    candidate = incident_correlator.ingest(batch_payload)
    existing_key, new_key = (
        ref.occurrence_key for ref in candidate.occurrence_refs
    )
    store = IncidentAdmissionStore(database_path)
    existing_admission = store.admit(candidate.incident_id, (existing_key,))
    assert existing_admission.admission_token is not None
    store.mark_attempted(
        existing_admission.admission_token,
        existing_admission.admitted_keys,
    )
    received = []

    async def handler(alert_payload, *, incident_id, incident_anchors=None):
        received.append((alert_payload["alerts"], incident_anchors))
        return {"incident_id": incident_id}

    loop = asyncio.get_running_loop()
    create_future = loop.create_future
    completions = []

    def capture_future():
        completion = create_future()
        completions.append(completion)
        return completion

    def fail_anchor_builder(_payload, _original_alert_index):
        raise RuntimeError("synthetic anchor preparation failure")

    with monkeypatch.context() as patch:
        patch.setattr(loop, "create_future", capture_future)
        with pytest.raises(
            RuntimeError,
            match="synthetic anchor preparation failure",
        ):
            dispatcher.submit(
                candidate,
                handler,
                anchor_builder=fail_anchor_builder,
            )

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT occurrence_key, state
            FROM incident_occurrence_admissions
            WHERE occurrence_key IN (?, ?)
            """,
            (existing_key, new_key),
        ).fetchall()
    assert dict(rows) == {existing_key: "attempted"}
    assert received == []
    assert len(completions) == 1
    assert completions[0].cancelled()

    replay_correlator = IncidentCorrelator()
    replay_candidate = replay_correlator.ingest(batch_payload)
    replay_dispatcher = IncidentDispatcher(replay_correlator, database_path)
    replay_submission = replay_dispatcher.submit(
        replay_candidate,
        handler,
        anchor_builder=lambda _payload, index: {"original_alert_index": index},
    )

    assert replay_submission.state == "started"
    assert await asyncio.wait_for(replay_submission.wait(), timeout=5) == {
        "incident_id": replay_candidate.incident_id
    }
    assert received == [([new_alert], {"original_alert_index": 1})]


@pytest.mark.asyncio
async def test_anchor_preparation_rollback_failure_fails_closed_with_cause(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "admissions.sqlite3"
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(incident_correlator, database_path)
    payload = _webhook_payload(
        "HighCPU",
        "anchor-rollback-fingerprint",
        "2026-10-15T10:00:00Z",
    )
    candidate = incident_correlator.ingest(payload)
    calls = []

    async def handler(_payload, *, incident_id):
        calls.append(incident_id)
        return {"incident_id": incident_id}

    loop = asyncio.get_running_loop()
    create_future = loop.create_future
    completions = []

    def capture_future():
        completion = create_future()
        completions.append(completion)
        return completion

    def fail_anchor_builder(_payload, _original_alert_index):
        raise RuntimeError("synthetic anchor preparation failure")

    def fail_rollback(_store, _admission_token):
        raise OSError("synthetic admission rollback failure")

    with monkeypatch.context() as patch:
        patch.setattr(loop, "create_future", capture_future)
        patch.setattr(IncidentAdmissionStore, "rollback_queued", fail_rollback)
        with pytest.raises(
            OSError,
            match="synthetic admission rollback failure",
        ) as error:
            dispatcher.submit(
                candidate,
                handler,
                anchor_builder=fail_anchor_builder,
            )

    assert isinstance(error.value.__cause__, RuntimeError)
    assert str(error.value.__cause__) == "synthetic anchor preparation failure"
    assert len(completions) == 1
    assert completions[0].cancelled()
    assert calls == []

    with sqlite3.connect(database_path) as connection:
        state = connection.execute(
            """
            SELECT state
            FROM incident_occurrence_admissions
            WHERE occurrence_key = ?
            """,
            (candidate.occurrence_refs[0].occurrence_key,),
        ).fetchone()[0]
    assert state == "queued"

    replay_correlator = IncidentCorrelator()
    replay_candidate = replay_correlator.ingest(payload)
    replay_dispatcher = IncidentDispatcher(replay_correlator, database_path)
    replay = replay_dispatcher.submit(replay_candidate, handler)
    assert replay.state == "duplicate"
    assert calls == []


@pytest.mark.asyncio
async def test_dispatch_runs_real_incident_handler_with_blocked_approval_trace(
    monkeypatch,
    tmp_path,
):
    from unittest.mock import AsyncMock, MagicMock

    from agents import coordinator, verifier
    from agents.tools import comms

    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-placeholder-audit-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("ATLASOPS_AUTO_HF_INFERENCE", "0")
    monkeypatch.setenv("ATLASOPS_USE_HF_INFERENCE", "0")
    monkeypatch.setenv("ATLASOPS_LIVE_JUDGE", "0")
    monkeypatch.setenv("ATLASOPS_RECOMMENDER_ENABLED", "0")
    monkeypatch.setenv("ATLASOPS_REMEDIATION_BACKEND", "agent")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
    trajectory_dir = tmp_path / "trajectories"
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", trajectory_dir)

    triage = {
        "role": "triage",
        "trajectory": [{"role": "triage", "content": "synthetic triage trace"}],
        "final": {"severity": "P1", "title": "Synthetic frontend degradation"},
    }
    diagnosis = {
        "role": "diagnosis",
        "trajectory": [{"role": "diagnosis", "content": "synthetic diagnosis trace"}],
        "final": {"root_cause": "synthetic overload"},
    }
    call_order: list[str] = []
    agent_roles: list[str] = []
    real_call_agent = coordinator.call_agent

    async def fake_call_agent(role, _input_data):
        agent_roles.append(role)
        call_order.append(role)
        if role == "triage":
            return triage
        if role == "diagnosis":
            return diagnosis
        if role == "remediation":
            return await real_call_agent(role, _input_data)
        raise AssertionError(f"unexpected agent invocation: {role}")

    async def reject_approval(_incident_id, *, request_token):
        call_order.append("approval_decision")
        pending = next(
            request
            for request in coordinator.approval_gate.pending()
            if request["incident_id"] == _incident_id
        )
        decision = coordinator.approval_gate.callback(
            token=pending["token"],
            decision="rejected",
            approved_by="synthetic-reviewer",
            reason="synthetic safety rejection",
            action_digest=pending["action_digest"],
        )
        assert decision["ok"] is True
        return await real_wait_for_decision(_incident_id, request_token=request_token)

    async def settle_synthetic_environment(**kwargs):
        call_order.append("settle")
        assert kwargs["agent_claimed_resolved"] is False
        return {"status": "synthetic_settled", "observations": []}

    from agents.verifier import EnvironmentVerificationResult

    def verify_synthetic_environment(
        scenario_id,
        agent_claimed_resolved,
        alert,
        incident_context,
    ):
        call_order.append("verify")
        assert agent_claimed_resolved is False
        assert "remediation" in incident_context
        assert alert["commonLabels"]["alertname"] == "SyntheticAlert"
        return EnvironmentVerificationResult(
            scenario_id=scenario_id,
            agent_claimed_resolved=False,
            env_resolved=False,
            verification_status="failed",
            evidence=["synthetic environment remained unresolved"],
        )

    real_request = coordinator.approval_gate.request_action
    real_wait_for_decision = coordinator.approval_gate.wait_for_action_decision

    def track_approval_request(*args, **kwargs):
        call_order.append("approval_request")
        return real_request(*args, **kwargs)

    model_response = MagicMock()
    model_response.json.return_value = {"choices": [{
        "finish_reason": "tool_calls",
        "message": {"content": "", "tool_calls": [{
            "id": "selected-scale", "type": "function",
            "function": {
                "name": "kubectl_scale",
                "arguments": json.dumps({"deployment": "frontend", "replicas": 2}),
            },
        }]},
    }]}
    model = AsyncMock(return_value=model_response)
    mutation = MagicMock(side_effect=AssertionError("Rejected action must not execute"))
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "kubectl_scale", mutation)
    monkeypatch.setattr(coordinator, "post_with_retry", model)
    monkeypatch.setattr(coordinator.approval_gate, "request_action", track_approval_request)
    monkeypatch.setattr(coordinator, "call_agent", fake_call_agent)
    monkeypatch.setattr(coordinator.approval_gate, "wait_for_action_decision", reject_approval)
    monkeypatch.setattr(coordinator, "settle_environment", settle_synthetic_environment)
    monkeypatch.setattr(verifier, "verify_environment", verify_synthetic_environment)
    monkeypatch.setitem(
        coordinator.TOOL_REGISTRY,
        "slack_post_update",
        lambda **_kwargs: {"success": True, "synthetic": True},
    )
    monkeypatch.setattr(comms, "discord_scenario_run_ping", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(coordinator.circuit_breaker, "start_incident", lambda: None)
    monkeypatch.setattr(
        coordinator.circuit_breaker,
        "finish_incident",
        lambda _incident_id, **_kwargs: None,
    )

    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(
        incident_correlator,
        tmp_path / "admissions.sqlite3",
    )
    payload = _webhook_payload(
        "SyntheticAlert",
        "synthetic-fingerprint",
        "2026-10-15T10:00:00Z",
    )
    candidate = incident_correlator.ingest(payload)
    submission = dispatcher.submit(
        candidate,
        coordinator.handle_incident,
        anchor_builder=coordinator.build_incident_anchors,
    )
    result = await submission.wait()

    assert agent_roles == ["triage", "diagnosis", "remediation"]
    assert call_order == [
        "triage",
        "diagnosis",
        "remediation",
        "approval_request",
        "approval_decision",
        "verify",
    ]
    mutation.assert_not_called()
    assert model.await_count == 1
    assert result["remediation"]["final"]["status"] == "approval_rejected"
    assert result["remediation"]["final"]["executed_actions"] == []
    assert result["comms"]["final"]["status"] == "approval_rejected"
    assert result["env_resolved"] is False
    assert result["resolved"] is False
    assert result["triage"] == triage
    assert result["diagnosis"] == diagnosis

    persisted = json.loads(
        (trajectory_dir / f"{candidate.incident_id}.json").read_text(encoding="utf-8")
    )
    assert persisted == result
    assert persisted["alert"] == payload
    assert persisted["verification"]["verification_status"] == "failed"
    assert persisted["settling"] == {
        "settled": False, "observations": [], "skipped_reason": "no_authorized_remediation",
    }


@pytest.mark.asyncio
async def test_failed_occurrence_is_logged_observed_and_not_retried(caplog, tmp_path):
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(
        incident_correlator,
        tmp_path / "admissions.sqlite3",
    )
    payload = _webhook_payload(
        "HighCPU",
        "failed-fingerprint",
        "2026-10-15T10:00:00Z",
    )
    candidate = incident_correlator.ingest(payload)
    handler_finished = asyncio.Event()
    calls = 0

    async def failing_handler(_payload, *, incident_id):
        nonlocal calls
        calls += 1
        try:
            raise RuntimeError(f"synthetic failure for {incident_id}")
        finally:
            handler_finished.set()

    loop = asyncio.get_running_loop()
    unhandled_contexts = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled_contexts.append(context))
    try:
        with caplog.at_level("ERROR", logger="agents.incident_dispatch"):
            submission = dispatcher.submit(
                candidate,
                failing_handler,
            )
            completion_observed = asyncio.Event()
            submission.completion.add_done_callback(
                lambda _completion: completion_observed.set()
            )
            await handler_finished.wait()
            await completion_observed.wait()
            assert submission.completion.done()

            replay_correlator = IncidentCorrelator()
            replay = replay_correlator.ingest(payload)
            assert replay.occurrence_refs
            replay_dispatcher = IncidentDispatcher(
                replay_correlator,
                tmp_path / "admissions.sqlite3",
            )
            duplicate = replay_dispatcher.submit(replay, failing_handler)
            assert duplicate.state == "duplicate"
            assert duplicate.incident_id == candidate.incident_id
            await asyncio.sleep(0)
            del submission
            gc.collect()
            await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert calls == 1
    assert "attempted occurrences will not be replayed" in caplog.text
    assert not unhandled_contexts


@pytest.mark.parametrize("route", ["root", "coordinator"])
@pytest.mark.asyncio
async def test_resolved_notification_does_not_start_an_incident(monkeypatch, route, tmp_path):
    # Alertmanager (send_resolved: true) re-sends the same fingerprint/startsAt with
    # status "resolved" once the problem clears; that is not a new incident.
    import agents.correlator as correlator_module
    import app as app_module
    from agents import coordinator

    incident_correlator = IncidentCorrelator()
    incident_dispatcher = IncidentDispatcher(incident_correlator, tmp_path / "admissions.sqlite3")
    for module in (correlator_module, app_module, coordinator):
        monkeypatch.setattr(module, "correlator", incident_correlator)
    monkeypatch.setattr(app_module, "incident_dispatcher", incident_dispatcher)
    monkeypatch.setattr(coordinator, "incident_dispatcher", incident_dispatcher)
    calls: list[str] = []

    async def handler(_payload, *, incident_id, incident_anchors=None):
        calls.append(incident_id)
        return {"incident_id": incident_id}

    monkeypatch.setattr(coordinator, "handle_incident", handler)
    monkeypatch.setattr(app_module, "_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(coordinator, "_WEBHOOK_SECRET", "test-webhook-secret")
    application = app_module.app if route == "root" else coordinator.app
    resolved = _webhook_payload("HighCPU", "resolved-fingerprint", "2026-10-15T10:00:00Z")
    resolved["status"] = "resolved"
    resolved["alerts"][0]["status"] = "resolved"
    headers = {"Authorization": "Bearer test-webhook-secret"}

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
        response = await client.post("/webhook", json=resolved, headers=headers)

    assert response.status_code == 200
    assert response.json()["dispatched"] is False
    assert calls == []


def test_firing_alerts_only_keeps_firing_items_of_a_resolved_envelope():
    from agents.coordinator import firing_alerts_only

    payload = {
        "status": "resolved",
        "alerts": [
            {"status": "firing", "fingerprint": "a"},
            {"status": "resolved", "fingerprint": "b"},
            {"fingerprint": "c"},
        ],
    }
    assert [a["fingerprint"] for a in firing_alerts_only(payload)["alerts"]] == ["a"]
    assert payload["alerts"][1]["fingerprint"] == "b"
