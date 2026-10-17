"""Pure validation helpers for Stage A Prometheus telemetry observations."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

_MAX_FUTURE_SKEW_SECONDS = 5.0
_DEFAULT_MAX_AGE_SECONDS = 60.0
_MIN_PROBE_SEPARATION_SECONDS = 30.0


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _query_label(query: str) -> str:
    return query.strip() if isinstance(query, str) and query.strip() else "Prometheus query"


def _vector_series(
    result: Any,
    query: str,
) -> tuple[bool, list[dict[str, Any]], list[str]]:
    label = _query_label(query)
    failures: list[str] = []
    if not isinstance(result, Mapping):
        return False, [], [f"{label}: response is not an object"]

    if "success" in result:
        query_success = result.get("success") is True
    else:
        query_success = result.get("status") == "success"
    if not query_success:
        detail = result.get("error")
        suffix = f": {detail}" if detail else ""
        failures.append(f"{label}: query was not successful{suffix}")

    data = result.get("data")
    if isinstance(data, Mapping):
        result_type = result.get("resultType", data.get("resultType"))
        samples = result.get("result", data.get("result"))
    else:
        result_type = result.get("resultType")
        samples = result.get("result")

    if result_type != "vector":
        failures.append(f"{label}: resultType {result_type!r} != 'vector'")
    if not isinstance(samples, list):
        failures.append(f"{label}: vector result is not a list")
        return query_success, [], failures
    if not samples:
        failures.append(f"{label}: vector is empty")
        return query_success, [], failures

    series: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            failures.append(f"{label}: sample[{index}] is not an object")
            series.append({"metric": {}, "timestamp": None, "value": None})
            continue

        metric = sample.get("metric")
        if not isinstance(metric, Mapping):
            failures.append(f"{label}: sample[{index}] metric labels are not an object")
            metric = {}
        else:
            metric = dict(metric)

        value_pair = sample.get("value")
        if not isinstance(value_pair, (list, tuple)) or len(value_pair) != 2:
            failures.append(f"{label}: sample[{index}] must have a two-item [timestamp/value] pair")
            series.append({"metric": metric, "timestamp": None, "value": None})
            continue

        timestamp = _finite_number(value_pair[0])
        value = _finite_number(value_pair[1])
        if timestamp is None:
            failures.append(f"{label}: sample[{index}] timestamp must be finite and numeric")
        elif timestamp < 0:
            failures.append(f"{label}: sample[{index}] timestamp must be non-negative")
        if value is None:
            failures.append(f"{label}: sample[{index}] value must be finite and numeric")
        elif value < 0:
            failures.append(f"{label}: sample[{index}] value must be non-negative")
        series.append({"metric": metric, "timestamp": timestamp, "value": value})
    return query_success, series, failures


def _timestamp_failure(
    timestamp: float | None,
    observed_at: float | None,
    max_age_seconds: float | None,
    *,
    name: str,
) -> str | None:
    if timestamp is None:
        return f"{name} is missing or non-finite"
    if timestamp < 0:
        return f"{name} must be non-negative"
    if observed_at is None or max_age_seconds is None:
        return f"{name} freshness cannot be evaluated"

    age = observed_at - timestamp
    if age > max_age_seconds:
        return f"{name} is stale by {age:g}s (maximum age {max_age_seconds:g}s)"
    if age < -_MAX_FUTURE_SKEW_SECONDS:
        return f"{name} is future-dated by {-age:g}s (maximum skew {_MAX_FUTURE_SKEW_SECONDS:g}s)"
    return None


def _label_failure(
    metric: Mapping[str, Any],
    *,
    target_pod: str | None,
    container_name: str,
) -> str | None:
    if target_pod is None:
        return None
    if metric.get("pod") != target_pod:
        return f"pod label {metric.get('pod')!r} does not match target pod {target_pod!r}"
    if metric.get("container") != container_name:
        return (
            f"container label {metric.get('container')!r} "
            f"does not match target container {container_name!r}"
        )
    return None


def _series_key(metric: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (str(key), str(value))
            for key, value in metric.items()
            if key != "__name__"
        )
    )


def _parse_iso_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    timestamp = parsed.timestamp()
    return timestamp if math.isfinite(timestamp) else None


def cpu_observation(
    result: dict,
    query: str,
    *,
    observed_at: float,
    target_pod: str | None = None,
    container_name: str = "server",
    max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS,
    source_timestamps_result: dict | None = None,
) -> dict:
    """Validate a CPU vector while retaining evaluation and source timestamps."""
    label = _query_label(query)
    observed = _finite_number(observed_at)
    max_age = _finite_number(max_age_seconds)
    failures: list[str] = []
    if observed is None or observed < 0:
        failures.append(f"{label}: observed_at must be a finite non-negative timestamp")
    if max_age is None or max_age < 0:
        failures.append(f"{label}: max_age_seconds must be finite and non-negative")

    query_success, series, vector_failures = _vector_series(result, label)
    failures.extend(vector_failures)
    timestamp_fresh = bool(series)
    samples_cores: list[float] = []
    for item in series:
        item["source_timestamp"] = None

    if target_pod is not None and (
        not isinstance(target_pod, str) or not target_pod.strip()
    ):
        failures.append(f"{label}: target_pod must be a non-empty string when bound")
    if target_pod is not None and (
        not isinstance(container_name, str) or not container_name.strip()
    ):
        failures.append(f"{label}: container_name must be a non-empty string when bound")

    for index, item in enumerate(series):
        metric = item["metric"]
        sample_timestamp = item["timestamp"]
        value = item["value"]
        timestamp_error = _timestamp_failure(
            sample_timestamp,
            observed,
            max_age,
            name=f"{label}: sample[{index}] evaluation timestamp",
        )
        if timestamp_error:
            failures.append(timestamp_error)
            timestamp_fresh = False

        label_error = _label_failure(
            metric,
            target_pod=target_pod,
            container_name=container_name,
        )
        if label_error:
            failures.append(f"{label}: sample[{index}] {label_error}")
        if value is not None and value >= 0:
            samples_cores.append(value)

    source_fresh = True
    if source_timestamps_result is not None:
        source_query = f"timestamp({label})"
        source_query_success, source_series, source_failures = _vector_series(
            source_timestamps_result,
            source_query,
        )
        failures.extend(source_failures)
        source_fresh = bool(source_series) and source_query_success
        source_by_key: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}

        for index, item in enumerate(source_series):
            metric = item["metric"]
            key = _series_key(metric)
            if key in source_by_key:
                failures.append(
                    f"{source_query}: duplicate source timestamp series for labels {metric!r}"
                )
                source_fresh = False
            source_by_key[key] = item

            label_error = _label_failure(
                metric,
                target_pod=target_pod,
                container_name=container_name,
            )
            if label_error:
                failures.append(f"{source_query}: sample[{index}] {label_error}")
                source_fresh = False

            evaluation_error = _timestamp_failure(
                item["timestamp"],
                observed,
                max_age,
                name=f"{source_query}: sample[{index}] evaluation timestamp",
            )
            if evaluation_error:
                failures.append(evaluation_error)
                source_fresh = False

            source_error = _timestamp_failure(
                item["value"],
                observed,
                max_age,
                name=f"{source_query}: sample[{index}] source timestamp",
            )
            if source_error:
                failures.append(source_error)
                source_fresh = False

        primary_keys: set[tuple[tuple[str, str], ...]] = set()
        for index, item in enumerate(series):
            key = _series_key(item["metric"])
            primary_keys.add(key)
            source_item = source_by_key.get(key)
            item["source_timestamp"] = source_item["value"] if source_item else None
            if source_item is None:
                failures.append(
                    f"{source_query}: no source timestamp matches sample[{index}] labels "
                    f"{item['metric']!r}"
                )
                source_fresh = False
        if set(source_by_key) - primary_keys:
            unmatched = [
                item["metric"]
                for key, item in source_by_key.items()
                if key not in primary_keys
            ]
            failures.append(f"{source_query}: unmatched source timestamp series: {unmatched!r}")
            source_fresh = False

    if not query_success:
        timestamp_fresh = False
    fresh = (
        timestamp_fresh
        and source_fresh
        and observed is not None
        and max_age is not None
        and not failures
    )
    return {
        "raw_result": result,
        "observed_at": observed_at,
        "series": series,
        "samples_cores": samples_cores,
        "max_cores": max(samples_cores) if samples_cores else None,
        "query_success": query_success,
        "fresh": fresh,
        "failures": failures,
    }


def readiness_decision(
    targets_payload: dict,
    raw_result: dict,
    rate_result: dict,
    *,
    observed_at: float,
    previous: dict | None = None,
    target_pod: str | None = None,
    sample_timestamps_result: dict | None = None,
) -> dict:
    """Decide readiness from cAdvisor and timestamp-validated CPU observations."""
    observed = _finite_number(observed_at)
    failures: list[str] = []
    targets: list[dict[str, Any]] = []
    targets_query_success = True
    if observed is None or observed < 0:
        failures.append("observed_at must be a finite non-negative timestamp")
        targets_query_success = False

    if not isinstance(targets_payload, Mapping):
        failures.append("targets payload is not an object")
        active_targets: list[Any] = []
        targets_query_success = False
    else:
        if "status" in targets_payload and targets_payload.get("status") != "success":
            failures.append(f"targets query status {targets_payload.get('status')!r} != 'success'")
            targets_query_success = False
        if "success" in targets_payload and targets_payload.get("success") is not True:
            failures.append("targets query was not successful")
            targets_query_success = False
        data = targets_payload.get("data")
        if isinstance(data, Mapping):
            active_targets = data.get("activeTargets", [])
        else:
            active_targets = targets_payload.get("activeTargets", [])
        if not isinstance(active_targets, list):
            failures.append("targets activeTargets is not a list")
            active_targets = []
            targets_query_success = False

    cadvisor_targets = [
        target
        for target in active_targets
        if isinstance(target, Mapping)
        and "/metrics/cadvisor" in str(target.get("scrapeUrl", ""))
    ]
    if not cadvisor_targets:
        failures.append("no cAdvisor scrape target")
    target_timestamps_fresh = targets_query_success and bool(cadvisor_targets)

    for index, target in enumerate(cadvisor_targets):
        scrape_url = target.get("scrapeUrl")
        health = target.get("health")
        last_error = target.get("lastError")
        last_scrape = target.get("lastScrape")
        scrape_timestamp = _parse_iso_timestamp(last_scrape)
        target_fresh = True
        if health != "up":
            failures.append(f"cAdvisor target[{index}] health={health!r}, expected 'up'")
        if not isinstance(last_error, str) or last_error.strip():
            failures.append(f"cAdvisor target[{index}] lastError is not empty: {last_error!r}")
        if scrape_timestamp is None:
            failures.append(f"cAdvisor target[{index}] lastScrape is not a timezone-aware ISO timestamp")
            target_fresh = False
        else:
            scrape_error = _timestamp_failure(
                scrape_timestamp,
                observed,
                _DEFAULT_MAX_AGE_SECONDS,
                name=f"cAdvisor target[{index}] lastScrape",
            )
            if scrape_error:
                failures.append(scrape_error)
                target_fresh = False
        targets.append(
            {
                "scrapeUrl": scrape_url,
                "health": health,
                "lastError": last_error,
                "lastScrape": last_scrape,
                "last_scrape_timestamp": scrape_timestamp,
                "fresh": target_fresh,
            }
        )
        target_timestamps_fresh = target_timestamps_fresh and target_fresh

    raw_observation = cpu_observation(
        raw_result,
        "raw CPU result",
        observed_at=observed_at,
        target_pod=target_pod,
        source_timestamps_result=sample_timestamps_result,
    )
    if sample_timestamps_result is None:
        missing_timestamps = (
            "sample_timestamps_result is required to verify raw metric source freshness"
        )
        raw_observation["fresh"] = False
        raw_observation["failures"].append(missing_timestamps)
    rate_observation = cpu_observation(
        rate_result,
        "CPU rate result",
        observed_at=observed_at,
        target_pod=target_pod,
    )
    failures.extend(raw_observation["failures"])
    failures.extend(rate_observation["failures"])

    previous_series: dict[tuple[tuple[str, str], ...], float] = {}
    previous_observed_at: float | None = None
    previous_valid = False
    if isinstance(previous, Mapping):
        previous_observed_at = _finite_number(previous.get("observed_at"))
        previous_failures = previous.get("failures")
        previous_valid = (
            previous.get("fresh") is True
            and isinstance(previous_failures, list)
            and not previous_failures
        )
        previous_raw = previous.get("raw_observation")
        if not isinstance(previous_raw, Mapping):
            previous_raw = previous
        old_series = previous_raw.get("series", [])
        if isinstance(old_series, list):
            for item in old_series:
                if not isinstance(item, Mapping) or not isinstance(item.get("metric"), Mapping):
                    continue
                old_timestamp = _finite_number(item.get("source_timestamp"))
                if old_timestamp is not None and old_timestamp >= 0:
                    previous_series[_series_key(item["metric"])] = old_timestamp
    elif previous is not None:
        failures.append("previous observation is not an object")

    advancing = True
    comparable_series = 0
    for item in raw_observation["series"]:
        source_timestamp = _finite_number(item.get("source_timestamp"))
        if source_timestamp is None:
            continue
        old_timestamp = previous_series.get(_series_key(item["metric"]))
        if old_timestamp is None:
            continue
        comparable_series += 1
        if source_timestamp <= old_timestamp:
            failures.append(
                "raw source timestamp did not advance for identical series labels "
                f"{item['metric']!r}: {source_timestamp:g} <= {old_timestamp:g}"
            )
            advancing = False

    history_ready = previous_valid and comparable_series > 0
    if previous is not None:
        if not previous_valid:
            failures.append("previous observation was not a fresh valid probe")
            history_ready = False
        if comparable_series == 0:
            failures.append("no identical raw series is available to verify source timestamp advancement")
            history_ready = False
        if previous_observed_at is None:
            failures.append("previous observation timestamp is unavailable")
            history_ready = False
        elif observed is None or observed - previous_observed_at < _MIN_PROBE_SEPARATION_SECONDS:
            failures.append(
                "readiness observations must be separated by at least "
                f"{_MIN_PROBE_SEPARATION_SECONDS:g}s"
            )
            history_ready = False

    fresh = (
        raw_observation["fresh"]
        and rate_observation["fresh"]
        and target_timestamps_fresh
        and advancing
        and not failures
    )
    ready = (
        raw_observation["query_success"]
        and rate_observation["query_success"]
        and fresh
        and history_ready
        and not failures
    )
    last_sample_timestamps = {
        "raw": [
            {
                "metric": item["metric"],
                "timestamp": item["timestamp"],
                "source_timestamp": item.get("source_timestamp"),
            }
            for item in raw_observation["series"]
        ],
        "rate": [
            {"metric": item["metric"], "timestamp": item["timestamp"]}
            for item in rate_observation["series"]
        ],
    }
    return {
        "observed_at": observed_at,
        "ready": ready,
        "fresh": fresh,
        "failures": failures,
        "raw_observation": raw_observation,
        "rate_observation": rate_observation,
        "targets": targets,
        "last_sample_timestamps": last_sample_timestamps,
    }
