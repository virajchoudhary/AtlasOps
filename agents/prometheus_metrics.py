"""Shared Prometheus dashboard metrics JSON for FastAPI handlers.

Used by the root Space app (`GET /metrics`) and the coordinator sub-app mounted
at `/api` (`GET /api/metrics` → coordinator route `/metrics`).
"""

from __future__ import annotations

import os
import math
import time
from typing import Any


def _sample_total(payload: Any, now: float) -> float | None:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "vector":
        return None
    samples = data.get("result")
    if not isinstance(samples, list) or not samples:
        return None
    total = 0.0
    for sample in samples:
        value = sample.get("value") if isinstance(sample, dict) else None
        if not isinstance(value, list) or len(value) != 2 or any(isinstance(item, bool) for item in value):
            return None
        try:
            timestamp, number = float(value[0]), float(value[1])
        except (ValueError, TypeError):
            return None
        if (
            not math.isfinite(timestamp) or not math.isfinite(number)
            or number < 0 or not -5 <= now - timestamp <= 60
        ):
            return None
        total += number
    return round(total, 4) if math.isfinite(total) else None


async def build_dashboard_metrics_payload() -> dict[str, Any]:
    import httpx as _httpx

    prom = (os.getenv("PROMETHEUS_URL") or "").strip()
    alert_url = (os.getenv("ALERTMANAGER_URL") or "").strip()
    results: dict[str, Any] = {
        "error_rate": None, "cpu": None, "rps": None, "alerts": None,
        "attribution": {
            "error_rate": "sampled_otlp_server_span_error_fraction",
            "rps": "sampled_otlp_server_spans",
            "cpu": "workload_container_cpu_cores",
            "alerts": "alertmanager_active_records",
        },
        "observed_at": {},
    }
    if not prom:
        return results
    from agents.verifier import ONLINE_BOUTIQUE_SERVICES

    services = "|".join(sorted(ONLINE_BOUTIQUE_SERVICES))
    spans = (
        'atlasops_boutique_calls_total{span_kind="SPAN_KIND_SERVER",'
        f'service_name=~"{services}"'
    )
    rate = f"sum(rate({spans}}}[2m]))"
    queries = {
        "error_rate": f'1 - sum(rate({spans},status_code!="STATUS_CODE_ERROR"}}[2m])) / {rate}',
        "cpu": (
            "sum(rate(container_cpu_usage_seconds_total{"
            f'namespace="{os.getenv("ATLASOPS_WORKLOAD_NAMESPACE", "default")}",'
            'container!="",container!="POD"}[2m]))'
        ),
        "rps": rate,
    }
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            for key, query in queries.items():
                try:
                    r = await client.get(f"{prom}/api/v1/query", params={"query": query})
                    if r.status_code == 200:
                        observed_at = time.time()
                        value = _sample_total(r.json(), observed_at)
                        if value is not None and (key != "error_rate" or value <= 1):
                            results[key] = value
                            results["observed_at"][key] = observed_at
                except Exception:
                    continue
            if alert_url:
                try:
                    ar = await client.get(f"{alert_url}/api/v2/alerts?active=true")
                    if ar.status_code == 200:
                        alerts = ar.json()
                        if isinstance(alerts, list) and all(isinstance(item, dict) for item in alerts):
                            results["alerts"] = len(alerts)
                            results["observed_at"]["alerts"] = time.time()
                except Exception:
                    pass
    except Exception:
        pass
    return results
