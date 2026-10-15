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
    result = c.ingest(_alert("HighCPU"))
    assert result.incident_id.startswith("inc-")
    assert result.is_new_incident is True
    assert result.occurrence_refs


def test_ingest_correlates_similar_alerts():
    c = IncidentCorrelator()
    first = c.ingest(_alert("HighCPU", service="frontend"))
    second = c.ingest(_alert("HighLatency", service="frontend"))
    assert first.incident_id == second.incident_id
    assert second.is_new_incident is False
    assert second.occurrence_refs


def test_correlator_returns_candidates_without_claiming_dispatch():
    c = IncidentCorrelator()
    first = c.ingest(_alert("HighCPU", service="frontend"))
    c.mark_processing(first.incident_id, True)
    follow_up = _alert("High5xx", service="frontend")

    result = c.ingest(follow_up)
    assert result.incident_id == first.incident_id
    assert result.is_new_incident is False
    assert result.occurrence_refs

    replay = c.ingest(follow_up)
    assert replay.incident_id == first.incident_id
    assert replay.is_new_incident is False
    assert replay.occurrence_refs == result.occurrence_refs
    assert c.get_active()[0]["alert_count"] == 2


def test_replayed_alert_keeps_candidate_refs_after_processing_completes():
    c = IncidentCorrelator()
    alert = _alertmanager_alert(fingerprint="alert-high-cpu")

    first = c.ingest(alert)
    incident_id = first.incident_id
    assert first.is_new_incident is True
    assert first.occurrence_refs

    c.mark_processing(incident_id, True)
    replay = _alertmanager_alert(
        fingerprint="alert-high-cpu",
        ends_at="2026-10-15T10:05:00Z",
        annotations={"summary": "still high"},
    )
    during_processing = c.ingest(replay)
    assert during_processing.incident_id == incident_id
    assert during_processing.occurrence_refs == first.occurrence_refs
    c.mark_processing(incident_id, False)

    replay_result = c.ingest(replay)
    assert replay_result.incident_id == incident_id
    assert replay_result.is_new_incident is False
    assert replay_result.occurrence_refs == first.occurrence_refs

    follow_up = _alertmanager_alert(
        alertname="HighLatency",
        fingerprint="alert-high-latency",
        starts_at="2026-10-15T10:01:00Z",
    )
    follow_up_result = c.ingest(follow_up)
    assert follow_up_result.incident_id == incident_id
    assert follow_up_result.is_new_incident is False
    assert follow_up_result.occurrence_refs


def test_same_fingerprint_with_new_starts_at_is_a_new_occurrence():
    c = IncidentCorrelator()
    first = _alertmanager_alert(fingerprint="alert-high-cpu")
    first_result = c.ingest(first)
    incident_id = first_result.incident_id
    assert first_result.occurrence_refs
    c.mark_processing(incident_id, False)

    next_occurrence = _alertmanager_alert(
        fingerprint="alert-high-cpu",
        starts_at="2026-10-15T10:01:00Z",
    )
    next_result = c.ingest(next_occurrence)
    assert next_result.incident_id == incident_id
    assert next_result.is_new_incident is False
    assert next_result.occurrence_refs


def test_status_transition_is_distinct_from_firing_occurrence():
    c = IncidentCorrelator()
    firing = _alertmanager_alert(fingerprint="alert-high-cpu")
    first = c.ingest(firing)
    incident_id = first.incident_id
    c.mark_processing(incident_id, False)

    resolved = _alertmanager_alert(
        fingerprint="alert-high-cpu",
        status="resolved",
        ends_at="2026-10-15T10:05:00Z",
    )
    resolved["status"] = "firing"
    result = c.ingest(resolved)
    assert result.incident_id == incident_id
    assert result.is_new_incident is False
    assert result.occurrence_refs


def test_missing_fingerprint_fallback_ignores_annotation_and_ends_at_churn():
    c = IncidentCorrelator()
    first = _alertmanager_alert(fingerprint=None)
    first_result = c.ingest(first)
    incident_id = first_result.incident_id
    assert first_result.occurrence_refs
    c.mark_processing(incident_id, False)

    replay = _alertmanager_alert(
        fingerprint=None,
        ends_at="2026-10-15T10:05:00Z",
        annotations={"summary": "updated summary"},
    )
    replay_result = c.ingest(replay)
    assert replay_result.incident_id == incident_id
    assert replay_result.is_new_incident is False
    assert replay_result.occurrence_refs == first_result.occurrence_refs

    next_occurrence = _alertmanager_alert(
        fingerprint=None,
        starts_at="2026-10-15T10:01:00Z",
    )
    next_result = c.ingest(next_occurrence)
    assert next_result.incident_id == incident_id
    assert next_result.is_new_incident is False
    assert next_result.occurrence_refs


def test_missing_starts_at_uses_stable_fallback():
    c = IncidentCorrelator()
    first = _alertmanager_alert(fingerprint=None, starts_at=None)
    first_result = c.ingest(first)
    incident_id = first_result.incident_id
    assert first_result.occurrence_refs
    c.mark_processing(incident_id, False)

    replay = _alertmanager_alert(
        fingerprint=None,
        starts_at=None,
        ends_at="2026-10-15T10:05:00Z",
        annotations={"summary": "updated summary"},
    )
    replay_result = c.ingest(replay)
    assert replay_result.incident_id == incident_id
    assert replay_result.is_new_incident is False
    assert replay_result.occurrence_refs == first_result.occurrence_refs


def test_concurrent_same_episode_correlates_once():
    c = IncidentCorrelator()
    alert = _alertmanager_alert(fingerprint="alert-high-cpu")
    workers = 24
    barrier = Barrier(workers)

    def ingest_same_episode(_):
        barrier.wait()
        return c.ingest(alert)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(ingest_same_episode, range(workers)))

    assert len({result.incident_id for result in results}) == 1
    assert sum(result.is_new_incident for result in results) == 1
    assert all(result.occurrence_refs == results[0].occurrence_refs for result in results)


def test_concurrent_related_occurrences_share_one_incident():
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

    assert len({result.incident_id for result in results}) == 1
    assert sum(result.is_new_incident for result in results) == 1
    assert sum(len(result.occurrence_refs) for result in results) == workers


def test_different_namespace_does_not_correlate():
    c = IncidentCorrelator()
    first = c.ingest(_alert("HighCPU", namespace="prod-a"))
    second = c.ingest(_alert("HighCPU", namespace="prod-b"))
    assert first.incident_id != second.incident_id
    assert second.is_new_incident is True


def test_ui_inject_correlation_id_always_dispatches():
    """Each demo inject is independent; same alertname/service must not dedupe."""
    c = IncidentCorrelator()
    base = {
        "commonLabels": {"alertname": "HighCPU", "namespace": "default", "service": "frontend"},
        "alerts": [{"labels": {"alertname": "HighCPU", "service": "frontend"}}],
    }
    a1 = {**base, "correlation_id": "inj-1-aaa", "scenario_id": "single_fault/sf-006"}
    a2 = {**base, "correlation_id": "inj-2-bbb", "scenario_id": "single_fault/sf-002"}
    result1 = c.ingest(a1)
    c.mark_processing(result1.incident_id, True)
    result2 = c.ingest(a2)
    assert result1.incident_id != result2.incident_id
    assert result1.is_new_incident is True and result2.is_new_incident is True
    assert result1.occurrence_refs and result2.occurrence_refs
