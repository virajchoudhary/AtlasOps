import asyncio
import gc

import pytest
from httpx import ASGITransport, AsyncClient

from agents.correlator import IncidentCorrelator
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


@pytest.mark.parametrize("route", ["root", "coordinator"])
@pytest.mark.asyncio
async def test_webhook_queues_each_new_related_occurrence_once(monkeypatch, route):
    import agents.correlator as correlator_module
    import app as app_module
    from agents import coordinator

    incident_correlator = IncidentCorrelator()
    incident_dispatcher = IncidentDispatcher(incident_correlator)
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

    async def controlled_handler(payload, *, incident_id):
        nonlocal active_handlers, maximum_active_handlers
        fingerprint = payload["alerts"][0]["fingerprint"]
        active_handlers += 1
        maximum_active_handlers = max(maximum_active_handlers, active_handlers)
        handled_fingerprints.append(fingerprint)
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

    if route == "coordinator":
        assert responses[0].json()["dispatched"] is True
        assert responses[1].json()["dispatched"] is True
        assert responses[2].json()["dispatched"] is False


@pytest.mark.asyncio
async def test_cancelled_request_wait_does_not_cancel_owned_worker():
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(incident_correlator)
    payload = {"commonLabels": {"service": "frontend"}}
    correlation = incident_correlator.ingest(payload)
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()

    async def controlled_handler(_payload, *, incident_id):
        handler_started.set()
        await release_handler.wait()
        return {"incident_id": incident_id}

    submission = await dispatcher.submit(
        incident_id=correlation.incident_id,
        occurrence_keys=correlation.new_occurrence_keys,
        payload=payload,
        handler=controlled_handler,
    )
    request_wait = asyncio.create_task(submission.wait())
    await handler_started.wait()

    request_wait.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_wait
    assert not submission.completion.cancelled()

    release_handler.set()
    assert await submission.wait() == {"incident_id": correlation.incident_id}


@pytest.mark.asyncio
async def test_failed_occurrence_is_logged_observed_and_not_retried(caplog):
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(incident_correlator)
    payload = _webhook_payload(
        "HighCPU",
        "failed-fingerprint",
        "2026-10-15T10:00:00Z",
    )
    correlation = incident_correlator.ingest(payload)
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
            submission = await dispatcher.submit(
                incident_id=correlation.incident_id,
                occurrence_keys=correlation.new_occurrence_keys,
                payload=payload,
                handler=failing_handler,
            )
            completion_observed = asyncio.Event()
            submission.completion.add_done_callback(
                lambda _completion: completion_observed.set()
            )
            await handler_finished.wait()
            await completion_observed.wait()
            assert submission.completion.done()

            replay = incident_correlator.ingest(payload)
            assert not replay.new_occurrence_keys
            duplicate = await dispatcher.submit(
                incident_id=replay.incident_id,
                occurrence_keys=replay.new_occurrence_keys,
                payload=payload,
                handler=failing_handler,
            )
            assert duplicate.state == "duplicate"
            await asyncio.sleep(0)
            del submission
            gc.collect()
            await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert calls == 1
    assert "claimed occurrences will not be retried" in caplog.text
    assert not unhandled_contexts
