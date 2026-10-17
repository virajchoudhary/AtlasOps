from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scripts.stage_a_telemetry import cpu_observation, readiness_decision

OBSERVED_AT = 1_000.0
TARGET_POD = "payments-abc123"


def _sample(
    timestamp: float | str = 990.0,
    value: float | str = "0.5",
    *,
    pod: str | None = TARGET_POD,
    container: str | None = "server",
    extra_labels: dict | None = None,
) -> dict:
    metric = {}
    if pod is not None:
        metric["pod"] = pod
    if container is not None:
        metric["container"] = container
    metric.update(extra_labels or {})
    return {"metric": metric, "value": [timestamp, value]}


def _query_result(samples: list, *, success: bool = True, result_type: str = "vector") -> dict:
    return {"success": success, "resultType": result_type, "result": samples}


def _targets(
    *,
    health: str = "up",
    last_error: str = "",
    last_scrape: str | None = None,
) -> dict:
    if last_scrape is None:
        last_scrape = datetime.fromtimestamp(OBSERVED_AT - 10, UTC).isoformat()
    return {
        "status": "success",
        "data": {
            "activeTargets": [
                {
                    "scrapeUrl": "http://node-1:10250/metrics/cadvisor",
                    "health": health,
                    "lastError": last_error,
                    "lastScrape": last_scrape,
                }
            ]
        },
    }


def _valid_results(
    *,
    raw_timestamp: float = 990.0,
    rate_timestamp: float = 995.0,
    source_timestamp: float = 990.0,
    source_eval_timestamp: float = 990.0,
    source_labels: dict | None = None,
) -> tuple[dict, dict, dict]:
    metric = {"pod": TARGET_POD, "container": "server"}
    metric.update(source_labels or {})
    return (
        _query_result([_sample(raw_timestamp, "125.0")]),
        _query_result([_sample(rate_timestamp, "0.8")]),
        _query_result(
            [
                {
                    "metric": metric,
                    "value": [source_eval_timestamp, str(source_timestamp)],
                }
            ]
        ),
    )


def test_cpu_observation_retains_provider_timestamps_and_computes_values():
    result = _query_result(
        [
            _sample(970.25, "0.5"),
            _sample("980.5", "1.25", pod="payments-def456"),
        ]
    )

    observation = cpu_observation(
        result,
        "sum by (pod, container) (rate(container_cpu_usage_seconds_total[30s]))",
        observed_at=OBSERVED_AT,
    )

    assert observation["raw_result"] is result
    assert observation["observed_at"] == OBSERVED_AT
    assert observation["query_success"] is True
    assert observation["fresh"] is True
    assert observation["series"] == [
        {
            "metric": {"pod": TARGET_POD, "container": "server"},
            "timestamp": 970.25,
            "source_timestamp": None,
            "value": 0.5,
        },
        {
            "metric": {"pod": "payments-def456", "container": "server"},
            "timestamp": 980.5,
            "source_timestamp": None,
            "value": 1.25,
        },
    ]
    assert observation["samples_cores"] == [0.5, 1.25]
    assert observation["max_cores"] == 1.25
    assert observation["failures"] == []


def test_cpu_observation_accepts_native_prometheus_envelope():
    result = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [_sample(OBSERVED_AT, "0.25")],
        },
    }

    observation = cpu_observation(result, "up", observed_at=OBSERVED_AT)

    assert observation["query_success"] is True
    assert observation["fresh"] is True
    assert observation["series"][0]["timestamp"] == OBSERVED_AT


def test_cpu_observation_uses_source_timestamp_for_metric_freshness():
    result = _query_result([_sample(OBSERVED_AT, "125.0")])
    source_timestamps = _query_result([_sample(OBSERVED_AT, str(OBSERVED_AT - 61))])

    observation = cpu_observation(
        result,
        "raw CPU",
        observed_at=OBSERVED_AT,
        source_timestamps_result=source_timestamps,
    )

    assert observation["series"][0]["timestamp"] == OBSERVED_AT
    assert observation["series"][0]["source_timestamp"] == OBSERVED_AT - 61
    assert observation["fresh"] is False
    assert any("source timestamp" in failure and "stale" in failure for failure in observation["failures"])


def test_cpu_observation_matches_source_series_without_metric_name_label():
    labels = {"pod": TARGET_POD, "container": "server", "__name__": "container_cpu_usage_seconds_total"}
    result = _query_result([{"metric": labels, "value": [OBSERVED_AT, "125.0"]}])
    source_timestamps = _query_result(
        [_sample(OBSERVED_AT, str(OBSERVED_AT - 10))]
    )

    observation = cpu_observation(
        result,
        "raw CPU",
        observed_at=OBSERVED_AT,
        target_pod=TARGET_POD,
        source_timestamps_result=source_timestamps,
    )

    assert observation["fresh"] is True
    assert observation["failures"] == []
    assert observation["series"][0] == {
        "metric": labels,
        "timestamp": OBSERVED_AT,
        "source_timestamp": OBSERVED_AT - 10,
        "value": 125.0,
    }


def test_cpu_observation_rejects_unmatched_source_timestamp_series():
    result = _query_result([_sample(OBSERVED_AT, "125.0", extra_labels={"id": "raw"})])
    source_timestamps = _query_result(
        [_sample(OBSERVED_AT, str(OBSERVED_AT - 10), extra_labels={"id": "other"})]
    )

    observation = cpu_observation(
        result,
        "raw CPU",
        observed_at=OBSERVED_AT,
        source_timestamps_result=source_timestamps,
    )

    assert observation["fresh"] is False
    assert any("no source timestamp matches" in failure for failure in observation["failures"])
    assert any("unmatched source timestamp series" in failure for failure in observation["failures"])


@pytest.mark.parametrize(
    ("result", "expected_failure"),
    [
        (_query_result([_sample()], success=False), "not successful"),
        (_query_result([_sample()], result_type="scalar"), "resultType"),
        (_query_result([]), "empty"),
        (_query_result([_sample(OBSERVED_AT - 61)]), "stale"),
        (_query_result([_sample(OBSERVED_AT + 5.01)]), "future"),
        (_query_result([_sample(OBSERVED_AT, "NaN")]), "finite"),
        (_query_result([_sample(OBSERVED_AT, "+Inf")]), "finite"),
        (_query_result([_sample(OBSERVED_AT, "-0.1")]), "non-negative"),
        (_query_result([_sample(-1, "0.5")]), "non-negative"),
        (_query_result([{"metric": {}, "value": [OBSERVED_AT]}]), "timestamp/value"),
    ],
)
def test_cpu_observation_rejects_invalid_or_unusable_vector_results(result, expected_failure):
    observation = cpu_observation(result, "up", observed_at=OBSERVED_AT)

    assert observation["failures"]
    assert any(expected_failure in failure for failure in observation["failures"])
    assert observation["fresh"] is False


def test_cpu_observation_checks_pod_and_container_labels_when_target_is_bound():
    result = _query_result([_sample(pod="other-pod", container="server")])

    observation = cpu_observation(
        result,
        "up",
        observed_at=OBSERVED_AT,
        target_pod=TARGET_POD,
    )

    assert observation["query_success"] is True
    assert observation["fresh"] is False
    assert any("pod" in failure and TARGET_POD in failure for failure in observation["failures"])


def test_unbound_cpu_observation_accepts_an_aggregate_without_labels():
    result = _query_result([_sample(pod=None, container=None, value="0.7")])

    observation = cpu_observation(result, "max(rate(...))", observed_at=OBSERVED_AT)

    assert observation["failures"] == []
    assert observation["max_cores"] == 0.7


def test_readiness_decision_accepts_fresh_bound_samples_and_keeps_timestamps():
    raw_result, rate_result, sample_timestamps_result = _valid_results()

    first_probe = readiness_decision(
        _targets(),
        raw_result,
        rate_result,
        observed_at=OBSERVED_AT,
        target_pod=TARGET_POD,
        sample_timestamps_result=sample_timestamps_result,
    )

    assert first_probe["ready"] is False
    assert first_probe["fresh"] is True

    next_raw, next_rate, next_sample_timestamps = _valid_results(
        raw_timestamp=1_020.0,
        rate_timestamp=1_025.0,
        source_timestamp=1_020.0,
        source_eval_timestamp=1_020.0,
    )
    decision = readiness_decision(
        _targets(last_scrape=datetime.fromtimestamp(1_020.0, UTC).isoformat()),
        next_raw,
        next_rate,
        observed_at=1_030.0,
        previous=first_probe,
        target_pod=TARGET_POD,
        sample_timestamps_result=next_sample_timestamps,
    )

    assert decision["ready"] is True
    assert decision["fresh"] is True
    assert decision["failures"] == []
    assert decision["raw_observation"]["series"][0]["timestamp"] == 1_020.0
    assert decision["raw_observation"]["series"][0]["source_timestamp"] == 1_020.0
    assert decision["rate_observation"]["series"][0]["timestamp"] == 1_025.0
    assert decision["last_sample_timestamps"]["raw"] == [
        {
            "metric": {"pod": TARGET_POD, "container": "server"},
            "timestamp": 1_020.0,
            "source_timestamp": 1_020.0,
        }
    ]


def test_readiness_decision_accepts_unlabelled_aggregate_only_when_unbound():
    raw_result = _query_result([_sample(990.0, "125.0", pod=None, container=None)])
    rate_result = _query_result([_sample(995.0, "0.8", pod=None, container=None)])
    sample_timestamps_result = _query_result(
        [_sample(990.0, "990.0", pod=None, container=None)]
    )

    decision = readiness_decision(
        _targets(),
        raw_result,
        rate_result,
        observed_at=OBSERVED_AT,
        sample_timestamps_result=sample_timestamps_result,
    )

    assert decision["ready"] is False
    assert decision["fresh"] is True
    assert not any("label" in failure for failure in decision["failures"])


def test_readiness_decision_requires_bound_rate_labels_for_a_target_pod():
    raw_result, _rate_result, sample_timestamps_result = _valid_results()
    unlabelled_rate = _query_result([_sample(995.0, "0.8", pod=None, container=None)])

    decision = readiness_decision(
        _targets(),
        raw_result,
        unlabelled_rate,
        observed_at=OBSERVED_AT,
        target_pod=TARGET_POD,
        sample_timestamps_result=sample_timestamps_result,
    )

    assert decision["ready"] is False
    assert any("pod" in failure for failure in decision["failures"])


@pytest.mark.parametrize(
    ("target_overrides", "expected_failure"),
    [
        ({"health": "down"}, "health"),
        ({"last_error": "connection refused"}, "lastError"),
        ({"last_scrape": "not-an-iso-timestamp"}, "lastScrape"),
        (
            {"last_scrape": datetime.fromtimestamp(OBSERVED_AT - 61, UTC).isoformat()},
            "stale",
        ),
        (
            {"last_scrape": datetime.fromtimestamp(OBSERVED_AT + 6, UTC).isoformat()},
            "future",
        ),
    ],
)
def test_readiness_decision_rejects_unhealthy_or_stale_cadvisor_targets(
    target_overrides, expected_failure
):
    raw_result, rate_result, sample_timestamps_result = _valid_results()

    decision = readiness_decision(
        _targets(**target_overrides),
        raw_result,
        rate_result,
        observed_at=OBSERVED_AT,
        target_pod=TARGET_POD,
        sample_timestamps_result=sample_timestamps_result,
    )

    assert decision["ready"] is False
    assert any(expected_failure in failure for failure in decision["failures"])


def test_readiness_decision_rejects_missing_cadvisor_targets():
    raw_result, rate_result, sample_timestamps_result = _valid_results()
    payload = {"status": "success", "data": {"activeTargets": []}}

    decision = readiness_decision(
        payload,
        raw_result,
        rate_result,
        observed_at=OBSERVED_AT,
        sample_timestamps_result=sample_timestamps_result,
    )

    assert decision["ready"] is False
    assert decision["fresh"] is False
    assert any("cAdvisor" in failure for failure in decision["failures"])


def test_readiness_decision_rejects_failed_targets_query_as_not_fresh():
    raw_result, rate_result, sample_timestamps_result = _valid_results()
    payload = _targets()
    payload["status"] = "error"

    decision = readiness_decision(
        payload,
        raw_result,
        rate_result,
        observed_at=OBSERVED_AT,
        sample_timestamps_result=sample_timestamps_result,
    )

    assert decision["ready"] is False
    assert decision["fresh"] is False
    assert any("targets query status" in failure for failure in decision["failures"])


def test_readiness_decision_rejects_repeated_raw_timestamp_for_same_series():
    raw_result, rate_result, sample_timestamps_result = _valid_results()
    previous = readiness_decision(
        _targets(),
        raw_result,
        rate_result,
        observed_at=OBSERVED_AT,
        target_pod=TARGET_POD,
        sample_timestamps_result=sample_timestamps_result,
    )

    repeated = readiness_decision(
        _targets(),
        raw_result,
        rate_result,
        observed_at=OBSERVED_AT,
        previous=previous,
        target_pod=TARGET_POD,
        sample_timestamps_result=sample_timestamps_result,
    )

    assert repeated["ready"] is False
    assert repeated["fresh"] is False
    assert any("did not advance" in failure for failure in repeated["failures"])


def test_readiness_decision_does_not_compare_different_raw_series_keys():
    raw_result, rate_result, sample_timestamps_result = _valid_results()
    previous = readiness_decision(
        _targets(),
        raw_result,
        rate_result,
        observed_at=OBSERVED_AT,
        target_pod=TARGET_POD,
        sample_timestamps_result=sample_timestamps_result,
    )
    changed_labels = _query_result(
        [_sample(990.0, "125.0", extra_labels={"id": "replacement-series"})]
    )
    changed_timestamp_labels = _query_result(
        [
            {
                "metric": {"pod": TARGET_POD, "container": "server", "id": "replacement-series"},
                "value": [990.0, "990.0"],
            }
        ]
    )

    decision = readiness_decision(
        _targets(),
        changed_labels,
        rate_result,
        observed_at=OBSERVED_AT,
        previous=previous,
        target_pod=TARGET_POD,
        sample_timestamps_result=changed_timestamp_labels,
    )

    assert decision["ready"] is False
    assert decision["fresh"] is False
    assert any(
        "no identical raw series" in failure
        for failure in decision["failures"]
    )
    assert not any(
        "raw source timestamp did not advance" in failure
        for failure in decision["failures"]
    )


def test_readiness_decision_fails_closed_without_source_timestamp_result():
    raw_result, rate_result, _sample_timestamps_result = _valid_results()

    decision = readiness_decision(
        _targets(),
        raw_result,
        rate_result,
        observed_at=OBSERVED_AT,
        target_pod=TARGET_POD,
    )

    assert decision["ready"] is False
    assert decision["fresh"] is False
    assert any("sample_timestamps_result is required" in failure for failure in decision["failures"])
