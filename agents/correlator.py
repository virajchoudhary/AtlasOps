"""Incident correlation and deduplication for alert storms."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class CorrelatedIncident:
    incident_id: str
    created_at: float
    namespace: str
    services: set[str] = field(default_factory=set)
    alert_names: set[str] = field(default_factory=set)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    dispatch_claims: set[str] = field(default_factory=set)
    active_processing: bool = False
    last_alert_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "created_at": self.created_at,
            "namespace": self.namespace,
            "services": sorted(self.services),
            "alert_names": sorted(self.alert_names),
            "alert_count": len(self.alerts),
            "active_processing": self.active_processing,
            "last_alert_at": self.last_alert_at,
        }


class IncidentCorrelator:
    CORRELATION_WINDOW_S = 300

    def __init__(self):
        self._lock = RLock()
        self._incidents: dict[str, CorrelatedIncident] = {}

    def ingest(self, alert: dict[str, Any]) -> tuple[str, bool, bool]:
        """Return (incident_id, is_new_incident, should_dispatch_chain)."""
        with self._lock:
            return self._ingest_locked(alert)

    def _ingest_locked(self, alert: dict[str, Any]) -> tuple[str, bool, bool]:
        now = time.time()
        self._expire_old(now)
        alert_keys = self._alert_keys(alert)

        # UI chaos inject carries `correlation_id=inj-...` (see app._handle_after_delay).
        # Do not correlate with overlapping Alertmanager fingerprints or in-flight webhook
        # incidents — otherwise the second scenario is silently skipped while the coordinator
        # is still busy, and every poll uses `alerts[0]` so fingerprints look identical.
        if isinstance(alert, dict):
            cid = str(alert.get("correlation_id") or "").strip()
            if cid.startswith("inj-"):
                incident_id = f"inc-{int(now)}-{uuid.uuid4().hex[:6]}"
                namespace = self._extract_namespace(alert)
                services = self._extract_services(alert)
                alert_names = self._extract_alert_names(alert)
                inc = CorrelatedIncident(
                    incident_id=incident_id,
                    created_at=now,
                    namespace=namespace,
                    services=set(services),
                    alert_names=set(alert_names),
                    alerts=[alert],
                    dispatch_claims=set(alert_keys),
                    active_processing=False,
                    last_alert_at=now,
                )
                self._incidents[incident_id] = inc
                return incident_id, True, True

        namespace = self._extract_namespace(alert)
        services = self._extract_services(alert)
        alert_names = self._extract_alert_names(alert)

        for inc in self._incidents.values():
            if self._should_correlate(now, namespace, services, alert_names, inc):
                new_alert_keys = alert_keys - inc.dispatch_claims
                if not new_alert_keys:
                    inc.last_alert_at = now
                    return inc.incident_id, False, False
                inc.alerts.append(alert)
                inc.alert_names.update(alert_names)
                inc.services.update(services)
                inc.last_alert_at = now
                should_dispatch = not inc.active_processing
                if should_dispatch:
                    inc.dispatch_claims.update(new_alert_keys)
                return inc.incident_id, False, should_dispatch

        incident_id = f"inc-{int(now)}-{uuid.uuid4().hex[:6]}"
        inc = CorrelatedIncident(
            incident_id=incident_id,
            created_at=now,
            namespace=namespace,
            services=set(services),
            alert_names=set(alert_names),
            alerts=[alert],
            dispatch_claims=set(alert_keys),
            active_processing=False,
            last_alert_at=now,
        )
        self._incidents[incident_id] = inc
        return incident_id, True, True

    def mark_processing(self, incident_id: str, active: bool) -> None:
        with self._lock:
            inc = self._incidents.get(incident_id)
            if inc:
                inc.active_processing = active

    def get_active(self) -> list[dict[str, Any]]:
        with self._lock:
            now = time.time()
            self._expire_old(now)
            return [inc.to_dict() for inc in self._incidents.values()]

    def reset(self) -> None:
        """Clear all tracked incidents (used by POST /reset)."""
        with self._lock:
            self._incidents.clear()

    @staticmethod
    def _payload_key(value: Any) -> str:
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"payload:{digest}"

    @classmethod
    def _alert_keys(cls, alert: dict[str, Any]) -> set[str]:
        alerts = alert.get("alerts") if isinstance(alert, dict) else None
        if isinstance(alerts, list):
            keys: set[str] = set()
            for item in alerts:
                if not isinstance(item, dict):
                    continue
                fingerprint = item.get("fingerprint")
                if isinstance(fingerprint, str) and fingerprint.strip():
                    keys.add(f"fingerprint:{fingerprint.strip()}")
                else:
                    keys.add(cls._payload_key(item))
            if keys:
                return keys
        return {cls._payload_key(alert)}

    def _should_correlate(
        self,
        now: float,
        namespace: str,
        services: set[str],
        alert_names: set[str],
        incident: CorrelatedIncident,
    ) -> bool:
        if now - incident.created_at > self.CORRELATION_WINDOW_S:
            return False
        if namespace and incident.namespace and namespace != incident.namespace:
            return False
        if services and incident.services and services.isdisjoint(incident.services):
            return False
        if alert_names and incident.alert_names and alert_names.intersection(incident.alert_names):
            return True
        return bool(services and incident.services)

    def _expire_old(self, now: float) -> None:
        stale = [
            inc_id
            for inc_id, inc in self._incidents.items()
            if (now - inc.last_alert_at > self.CORRELATION_WINDOW_S and not inc.active_processing)
        ]
        for inc_id in stale:
            self._incidents.pop(inc_id, None)

    @staticmethod
    def _extract_namespace(alert: dict[str, Any]) -> str:
        labels = alert.get("commonLabels", {}) if isinstance(alert, dict) else {}
        return str(labels.get("namespace", "")).strip()

    @staticmethod
    def _extract_services(alert: dict[str, Any]) -> set[str]:
        labels = alert.get("commonLabels", {}) if isinstance(alert, dict) else {}
        services = {
            str(labels.get("service", "")).strip(),
            str(labels.get("app", "")).strip(),
            str(labels.get("deployment", "")).strip(),
        }
        services.update(
            str(a.get("labels", {}).get("service", "")).strip()
            for a in alert.get("alerts", [])
            if isinstance(a, dict)
        )
        return {s for s in services if s}

    @staticmethod
    def _extract_alert_names(alert: dict[str, Any]) -> set[str]:
        names = set()
        labels = alert.get("commonLabels", {}) if isinstance(alert, dict) else {}
        common = str(labels.get("alertname", "")).strip()
        if common:
            names.add(common)
        for a in alert.get("alerts", []):
            if isinstance(a, dict):
                name = str(a.get("labels", {}).get("alertname", "")).strip()
                if name:
                    names.add(name)
        return names


correlator = IncidentCorrelator()
