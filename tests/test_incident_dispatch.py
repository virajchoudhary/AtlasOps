import asyncio
import gc
import inspect
import json

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

    submission = dispatcher.submit(
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
async def test_cancelled_admission_does_not_lose_claimed_follow_up_occurrence():
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(incident_correlator)
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

    first_submission = dispatcher.submit(
        incident_id=first_correlation.incident_id,
        occurrence_keys=first_correlation.new_occurrence_keys,
        payload=first_payload,
        handler=handler,
    )
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
        submission = dispatcher.submit(
            incident_id=correlation.incident_id,
            occurrence_keys=correlation.new_occurrence_keys,
            payload=follow_up_payload,
            handler=handler,
        )
        if inspect.isawaitable(submission):
            submission = await submission
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
    assert not replay.new_occurrence_keys
    assert calls == ["fingerprint-a", "fingerprint-b"]
    assert incident_correlator.get_active()[0]["active_processing"] is False


@pytest.mark.asyncio
async def test_cancelled_worker_settles_all_claimed_completions_and_releases_queue():
    incident_correlator = IncidentCorrelator()
    dispatcher = IncidentDispatcher(incident_correlator)
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

    first_submission = dispatcher.submit(
        incident_id=first_correlation.incident_id,
        occurrence_keys=first_correlation.new_occurrence_keys,
        payload=first_payload,
        handler=handler,
    )
    await first_started.wait()

    queued_payload = _webhook_payload(
        "High5xx",
        "fingerprint-b",
        "2026-10-15T10:01:00Z",
    )
    queued_correlation = incident_correlator.ingest(queued_payload)
    queued_submission = dispatcher.submit(
        incident_id=queued_correlation.incident_id,
        occurrence_keys=queued_correlation.new_occurrence_keys,
        payload=queued_payload,
        handler=handler,
    )
    worker = dispatcher._queues[first_correlation.incident_id].worker

    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    assert first_submission.completion.cancelled()
    assert queued_submission.completion.cancelled()
    assert first_correlation.incident_id not in dispatcher._queues
    assert incident_correlator.get_active()[0]["active_processing"] is False
    assert not incident_correlator.ingest(queued_payload).new_occurrence_keys
    assert calls == ["fingerprint-a"]

    next_payload = _webhook_payload(
        "HighLatency",
        "fingerprint-c",
        "2026-10-15T10:02:00Z",
    )
    next_correlation = incident_correlator.ingest(next_payload)
    next_submission = dispatcher.submit(
        incident_id=next_correlation.incident_id,
        occurrence_keys=next_correlation.new_occurrence_keys,
        payload=next_payload,
        handler=handler,
    )
    assert await next_submission.wait() == {
        "incident_id": first_correlation.incident_id,
        "fingerprint": "fingerprint-c",
    }
    assert calls == ["fingerprint-a", "fingerprint-c"]
    assert incident_correlator.get_active()[0]["active_processing"] is False


@pytest.mark.asyncio
async def test_dispatch_runs_real_incident_handler_with_blocked_approval_trace(
    monkeypatch,
    tmp_path,
):
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

    async def fake_call_agent(role, _input_data):
        agent_roles.append(role)
        call_order.append(role)
        if role == "triage":
            return triage
        if role == "diagnosis":
            return diagnosis
        raise AssertionError(f"unexpected agent invocation: {role}")

    async def reject_approval(_incident_id):
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
        )
        assert decision["ok"] is True
        return await real_wait_for_decision(_incident_id)

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

    real_request = coordinator.approval_gate.request
    real_wait_for_decision = coordinator.approval_gate.wait_for_decision

    def track_approval_request(*args, **kwargs):
        call_order.append("approval_request")
        return real_request(*args, **kwargs)

    monkeypatch.setattr(coordinator.approval_gate, "request", track_approval_request)
    monkeypatch.setattr(coordinator, "call_agent", fake_call_agent)
    monkeypatch.setattr(coordinator.approval_gate, "wait_for_decision", reject_approval)
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
    dispatcher = IncidentDispatcher(incident_correlator)
    payload = _webhook_payload(
        "SyntheticAlert",
        "synthetic-fingerprint",
        "2026-10-15T10:00:00Z",
    )
    correlation = incident_correlator.ingest(payload)
    submission = dispatcher.submit(
        incident_id=correlation.incident_id,
        occurrence_keys=correlation.new_occurrence_keys,
        payload=payload,
        handler=coordinator.handle_incident,
    )
    result = await submission.wait()

    assert agent_roles == ["triage", "diagnosis"]
    assert call_order == [
        "triage",
        "diagnosis",
        "approval_request",
        "approval_decision",
        "settle",
        "verify",
    ]
    assert result["remediation"]["final"]["status"] == "approval_rejected"
    assert result["remediation"]["final"]["executed_actions"] == []
    assert result["comms"]["final"]["status"] == "approval_rejected"
    assert result["env_resolved"] is False
    assert result["resolved"] is False
    assert result["triage"] == triage
    assert result["diagnosis"] == diagnosis

    persisted = json.loads(
        (trajectory_dir / f"{correlation.incident_id}.json").read_text(encoding="utf-8")
    )
    assert persisted == result
    assert persisted["alert"] == payload
    assert persisted["verification"]["verification_status"] == "failed"
    assert persisted["settling"] == {"status": "synthetic_settled", "observations": []}


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
            submission = dispatcher.submit(
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
            duplicate = dispatcher.submit(
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
