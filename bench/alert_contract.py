"""Predeclared alert-observation contract shared by benchmark runners."""

from __future__ import annotations

import time


class AlertObservationTimeout(RuntimeError):
    pass


class AlertObservationContaminated(RuntimeError):
    pass


_FINGERPRINT_LABELS = (
    "alertname", "service", "deployment", "pod", "namespace", "severity",
)


def _alert_fingerprint(alert: dict, labels: dict | None = None) -> tuple[str, ...]:
    merged = {**(labels or {}), **(alert.get("labels") or {})}
    values = []
    for key in _FINGERPRINT_LABELS:
        value = str(merged.get(key, "")).strip().lower()
        if value:
            values.append(f"{key}={value}")
    # Label maps are semantically unordered; stable fingerprints must be too.
    return tuple(sorted(values)) if values else ("alertname=unknown",)


def _alert_observation_contract(entry: dict) -> tuple[dict, list[tuple[str, ...]]]:
    template = entry.get("model_visible_alert") or {}
    common = template.get("commonLabels") or {}
    expected = sorted(
        _alert_fingerprint(item, common)
        for item in template.get("alerts") or []
        if isinstance(item, dict)
    )
    return template, expected


def _is_prefix_match(observed: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    return observed == expected or (
        len(observed) < len(expected) and expected[:len(observed)] == observed
    )


def select_expected_alerts(
    active_alerts: list[dict],
    expected: list[tuple[str, ...]],
    common_labels: dict | None = None,
) -> tuple[list[dict], list[tuple[str, ...]], list[tuple[str, ...]]]:
    """Select only predeclared observations; reject unrelated active alerts."""
    expected_set = {tuple(sorted(candidate)) for candidate in expected}
    remaining = sorted(expected_set)
    matched: list[dict] = []
    unexpected: list[tuple[str, ...]] = []
    for alert in active_alerts:
        fingerprint = _alert_fingerprint(alert, common_labels)
        if fingerprint in remaining:
            matched.append(alert)
            remaining.remove(fingerprint)
        elif any(
            _is_prefix_match(fingerprint, candidate)
            for candidate in expected_set
        ):
            # A missing optional label remains a valid match; retain it.
            candidate = min(
                (
                    item for item in expected_set
                    if _is_prefix_match(fingerprint, item)
                ),
                key=len,
            )
            matched.append(alert)
            if candidate in remaining:
                remaining.remove(candidate)
        else:
            unexpected.append(fingerprint)
    return matched, list(remaining), unexpected


def wait_for_alert(
    scenario_id: str,
    timeout_s: int = 300,
    *,
    load_catalog_entry=None,
    list_active_alerts=None,
    sleep=time.sleep,
) -> dict:
    """Wait for every predeclared alert and reject unrelated observations."""
    if list_active_alerts is None:
        from agents.tools.alertmanager import alertmanager_list_alerts

        list_active_alerts = alertmanager_list_alerts
    if load_catalog_entry is None:
        from bench.runner import load_catalog_entry

        load_catalog_entry = load_catalog_entry

    entry = load_catalog_entry(scenario_id)
    if entry is None:
        raise AlertObservationContaminated("catalogue entry unavailable")
    template, expected = _alert_observation_contract(entry)
    if not expected:
        raise AlertObservationContaminated("catalogue alert contract is empty")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = list_active_alerts(active_only=True)
        if not result.get("success"):
            sleep(20)
            continue
        matched, missing, unexpected = select_expected_alerts(
            result.get("alerts") or [],
            expected,
            common_labels=template.get("commonLabels") or {},
        )
        if unexpected:
            raise AlertObservationContaminated(
                "unrelated active alerts observed: "
                + "; ".join("/".join(item) for item in sorted(unexpected))
        )
        if matched and not missing:
            # The template's common labels are orchestration context, not fields
            # Alertmanager necessarily repeats on every individual alert.
            return {
                "commonLabels": dict(template.get("commonLabels") or {}),
                "alerts": matched,
            }
        sleep(20)
    raise AlertObservationTimeout(f"expected alerts did not fire within {timeout_s}s")
