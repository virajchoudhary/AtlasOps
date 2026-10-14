"""Tests for incident correlator and dedup behavior."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from agents.correlator import IncidentCorrelator


def _alert(alertname: str, namespace: str = "default", service: str = "frontend") -> dict:
    return {
        "commonLabels": {"alertname": alertname, "namespace": namespace, "service": service},
        "alerts": [{"labels": {"alertname": alertname, "service": service}}],
    }


def _alertmanager_alert(
    alertname: str = "HighCPU",
    namespace: str = "default",
    service: str = "frontend",
    fingerprint: str | None = None,
    status: str | None = "firing",
    starts_at: str | None = "2026-10-15T10:00:00Z",
    ends_at: str | None = "0001-01-01T00:00:00Z",
    annotations: dict | None = None,
) -> dict:
    labels = {"alertname": alertname, "namespace": namespace, "service": service}
    item = {
        "labels": labels,
        "annotations": annotations if annotations is not None else {"summary": f"{alertname} detected"},
    }
    if fingerprint is not None:
        item["fingerprint"] = fingerprint
    if status is not None:
        item["status"] = status
    if starts_at is not None:
        item["startsAt"] = starts_at
    if ends_at is not None:
        item["endsAt"] = ends_at
    webhook = {"commonLabels": labels, "alerts": [item]}
    if status is not None:
        webhook["status"] = status
    return webhook


def test_ingest_creates_new_incident():
    c = IncidentCorrelator()
    incident_id, is_new, should_dispatch = c.ingest(_alert("HighCPU"))
    assert incident_id.startswith("inc-")
    assert is_new is True
    assert should_dispatch is True


def test_ingest_correlates_similar_alerts():
    c = IncidentCorrelator()
    inc1, _, _ = c.ingest(_alert("HighCPU", service="frontend"))
    inc2, is_new, _ = c.ingest(_alert("HighLatency", service="frontend"))
    assert inc1 == inc2
    assert is_new is False


def test_dedup_skips_dispatch_while_processing():
    c = IncidentCorrelator()
    inc, _, _ = c.ingest(_alert("HighCPU", service="frontend"))
    c.mark_processing(inc, True)
    _inc2, _is_new, should_dispatch = c.ingest(_alert("High5xx", service="frontend"))
    assert should_dispatch is False


def test_replayed_alert_is_not_redispatched_after_processing_completes():
    c = IncidentCorrelator()
    alert = _alertmanager_alert(fingerprint="alert-high-cpu")

    incident_id, is_new, should_dispatch = c.ingest(alert)
    assert is_new is True
    assert should_dispatch is True

    c.mark_processing(incident_id, True)
    replay = _alertmanager_alert(
        fingerprint="alert-high-cpu",
        ends_at="2026-10-15T10:05:00Z",
        annotations={"summary": "still high"},
    )
    _same_incident, _is_new, during_processing = c.ingest(replay)
    assert during_processing is False
    c.mark_processing(incident_id, False)

    replay_id, replay_is_new, replay_dispatch = c.ingest(replay)
    assert replay_id == incident_id
    assert replay_is_new is False
    assert replay_dispatch is False

    follow_up = _alertmanager_alert(
        alertname="HighLatency",
        fingerprint="alert-high-latency",
        starts_at="2026-10-15T10:01:00Z",
    )
    follow_up_id, follow_up_is_new, follow_up_dispatch = c.ingest(follow_up)
    assert follow_up_id == incident_id
    assert follow_up_is_new is False
    assert follow_up_dispatch is True


def test_same_fingerprint_with_new_starts_at_is_a_new_occurrence():
    c = IncidentCorrelator()
    first = _alertmanager_alert(fingerprint="alert-high-cpu")
    incident_id, _, first_dispatch = c.ingest(first)
    assert first_dispatch is True
    c.mark_processing(incident_id, False)

    next_occurrence = _alertmanager_alert(
        fingerprint="alert-high-cpu",
        starts_at="2026-10-15T10:01:00Z",
    )
    next_id, is_new, should_dispatch = c.ingest(next_occurrence)
    assert next_id == incident_id
    assert is_new is False
    assert should_dispatch is True


def test_status_transition_is_distinct_from_firing_occurrence():
    c = IncidentCorrelator()
    firing = _alertmanager_alert(fingerprint="alert-high-cpu")
    incident_id, _, _ = c.ingest(firing)
    c.mark_processing(incident_id, False)

    resolved = _alertmanager_alert(
        fingerprint="alert-high-cpu",
        status="resolved",
        ends_at="2026-10-15T10:05:00Z",
    )
    resolved["status"] = "firing"
    resolved_id, is_new, should_dispatch = c.ingest(resolved)
    assert resolved_id == incident_id
    assert is_new is False
    assert should_dispatch is True


def test_missing_fingerprint_fallback_ignores_annotation_and_ends_at_churn():
    c = IncidentCorrelator()
    first = _alertmanager_alert(fingerprint=None)
    incident_id, _, first_dispatch = c.ingest(first)
    assert first_dispatch is True
    c.mark_processing(incident_id, False)

    replay = _alertmanager_alert(
        fingerprint=None,
        ends_at="2026-10-15T10:05:00Z",
        annotations={"summary": "updated summary"},
    )
    replay_id, is_new, should_dispatch = c.ingest(replay)
    assert replay_id == incident_id
    assert is_new is False
    assert should_dispatch is False

    next_occurrence = _alertmanager_alert(
        fingerprint=None,
        starts_at="2026-10-15T10:01:00Z",
    )
    next_id, next_is_new, next_dispatch = c.ingest(next_occurrence)
    assert next_id == incident_id
    assert next_is_new is False
    assert next_dispatch is True


def test_missing_starts_at_uses_stable_fallback():
    c = IncidentCorrelator()
    first = _alertmanager_alert(fingerprint=None, starts_at=None)
    incident_id, _, first_dispatch = c.ingest(first)
    assert first_dispatch is True
    c.mark_processing(incident_id, False)

    replay = _alertmanager_alert(
        fingerprint=None,
        starts_at=None,
        ends_at="2026-10-15T10:05:00Z",
        annotations={"summary": "updated summary"},
    )
    replay_id, is_new, should_dispatch = c.ingest(replay)
    assert replay_id == incident_id
    assert is_new is False
    assert should_dispatch is False


def test_concurrent_same_episode_ingest_dispatches_once():
    c = IncidentCorrelator()
    alert = _alertmanager_alert(fingerprint="alert-high-cpu")
    workers = 24
    barrier = Barrier(workers)

    def ingest_same_episode(_):
        barrier.wait()
        return c.ingest(alert)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(ingest_same_episode, range(workers)))

    assert len({result[0] for result in results}) == 1
    assert sum(result[1] for result in results) == 1
    assert sum(result[2] for result in results) == 1


def test_concurrent_distinct_related_alerts_reserve_one_dispatch():
    c = IncidentCorrelator()
    workers = 24
    alerts = [
        _alertmanager_alert(
            alertname=f"HighCPU{i}",
            fingerprint=f"alert-high-cpu-{i}",
            starts_at=f"2026-10-15T10:{i:02d}:00Z",
        )
        for i in range(workers)
    ]
    barrier = Barrier(workers)

    def ingest_related(alert):
        barrier.wait()
        return c.ingest(alert)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(ingest_related, alerts))

    assert len({result[0] for result in results}) == 1
    assert sum(result[1] for result in results) == 1
    assert sum(result[2] for result in results) == 1


def test_different_namespace_does_not_correlate():
    c = IncidentCorrelator()
    inc1, _, _ = c.ingest(_alert("HighCPU", namespace="prod-a"))
    inc2, is_new, _ = c.ingest(_alert("HighCPU", namespace="prod-b"))
    assert inc1 != inc2
    assert is_new is True


def test_ui_inject_correlation_id_always_dispatches():
    """Each demo inject is independent; same alertname/service must not dedupe."""
    c = IncidentCorrelator()
    base = {
        "commonLabels": {"alertname": "HighCPU", "namespace": "default", "service": "frontend"},
        "alerts": [{"labels": {"alertname": "HighCPU", "service": "frontend"}}],
    }
    a1 = {**base, "correlation_id": "inj-1-aaa", "scenario_id": "single_fault/sf-006"}
    a2 = {**base, "correlation_id": "inj-2-bbb", "scenario_id": "single_fault/sf-002"}
    inc1, new1, d1 = c.ingest(a1)
    c.mark_processing(inc1, True)
    inc2, new2, d2 = c.ingest(a2)
    assert inc1 != inc2
    assert new1 is True and new2 is True
    assert d1 is True and d2 is True
