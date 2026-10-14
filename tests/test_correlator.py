"""Tests for incident correlator and dedup behavior."""

from agents.correlator import IncidentCorrelator


def _alert(alertname: str, namespace: str = "default", service: str = "frontend") -> dict:
    return {
        "commonLabels": {"alertname": alertname, "namespace": namespace, "service": service},
        "alerts": [{"labels": {"alertname": alertname, "service": service}}],
    }


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
    alert = _alert("HighCPU", service="frontend")
    alert["alerts"][0]["fingerprint"] = "alert-high-cpu"

    incident_id, is_new, should_dispatch = c.ingest(alert)
    assert is_new is True
    assert should_dispatch is True

    c.mark_processing(incident_id, True)
    _same_incident, _is_new, during_processing = c.ingest(alert)
    assert during_processing is False
    c.mark_processing(incident_id, False)

    replay_id, replay_is_new, replay_dispatch = c.ingest(alert)
    assert replay_id == incident_id
    assert replay_is_new is False
    assert replay_dispatch is False

    follow_up = _alert("HighLatency", service="frontend")
    follow_up["alerts"][0]["fingerprint"] = "alert-high-latency"
    follow_up_id, follow_up_is_new, follow_up_dispatch = c.ingest(follow_up)
    assert follow_up_id == incident_id
    assert follow_up_is_new is False
    assert follow_up_dispatch is True


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
