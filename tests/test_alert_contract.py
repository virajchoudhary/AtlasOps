import pytest

from bench import alert_contract


def test_alert_fingerprint_is_label_order_insensitive():
    first = alert_contract._alert_fingerprint({
        "labels": {
            "alertname": "PodCrashLooping",
            "pod": "cartservice-xxx",
            "severity": "warning",
        }
    })
    second = alert_contract._alert_fingerprint({
        "labels": {
            "severity": "warning",
            "pod": "cartservice-xxx",
            "alertname": "PodCrashLooping",
        }
    })

    assert first == second
    assert first == (
        "alertname=podcrashlooping",
        "pod=cartservice-xxx",
        "severity=warning",
    )


def test_wait_for_alert_requires_complete_predeclared_set():
    entry = {
        "model_visible_alert": {
            "commonLabels": {"namespace": "default", "severity": "critical"},
            "alerts": [
                {"labels": {"alertname": "one", "service": "frontend"}},
                {"labels": {"alertname": "two", "service": "cart"}},
            ],
        }
    }
    results = [
        {
            "success": True,
            "alerts": [
                {"labels": {"service": "frontend", "alertname": "one"}},
            ],
        },
        {
            "success": True,
            "alerts": [
                {"labels": {"service": "cart", "alertname": "two"}},
                {"labels": {"service": "frontend", "alertname": "one"}},
            ],
        },
    ]
    sleeps: list[float] = []

    observed = alert_contract.wait_for_alert(
        "single_fault/unit",
        load_catalog_entry=lambda _scenario_id: entry,
        list_active_alerts=lambda **_kwargs: results.pop(0),
        sleep=sleeps.append,
    )

    assert len(observed["alerts"]) == 2
    assert sleeps == [20]


def test_wait_for_alert_rejects_unrelated_observation():
    entry = {
        "model_visible_alert": {
            "commonLabels": {},
            "alerts": [{"labels": {"alertname": "expected"}}],
        }
    }

    with pytest.raises(
        alert_contract.AlertObservationContaminated,
        match="unrelated active alerts",
    ):
        alert_contract.wait_for_alert(
            "single_fault/unit",
            load_catalog_entry=lambda _scenario_id: entry,
            list_active_alerts=lambda **_kwargs: {
                "success": True,
                "alerts": [{"labels": {"alertname": "unrelated"}}],
            },
            sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("sleep used")),
        )
