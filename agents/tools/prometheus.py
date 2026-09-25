"""Prometheus tool wrappers — HTTP API queries to Prometheus."""

import os
import time
from typing import Any

import requests

PROMETHEUS_URL = os.getenv(
    "PROMETHEUS_URL",
    "http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090",
)


def _promql_evidence_semantics(result: list[Any]) -> dict[str, Any]:
    """Describe whether a successful PromQL query produced observable data.

    HTTP/API success and metric evidence are separate facts.  An empty vector
    is a valid Prometheus answer, but it must not be presented to an agent as
    positive evidence.
    """
    has_data = bool(result)
    return {
        "has_data": has_data,
        "series_present": has_data,
        "no_series": not has_data,
        "evidence_status": "series_present" if has_data else "no_series",
    }


def promql_query(query: str, time_unix: float | None = None) -> dict[str, Any]:
    """Execute an instant PromQL query."""
    params = {"query": query}
    if time_unix is not None:
        params["time"] = str(time_unix)
    try:
        r = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            return {
                "success": False,
                "error": data.get("error", "unknown"),
                "error_class": "promql_query_error",
                "evidence_status": "query_error",
                "has_data": False,
                "series_present": False,
                "no_series": False,
                "raw": data,
            }
        result = data["data"]["result"]
        return {
            "success": True,
            "result": result,
            "resultType": data["data"]["resultType"],
            **_promql_evidence_semantics(result),
        }
    except requests.RequestException as e:
        return {
            "success": False,
            "error": str(e),
            "error_class": "promql_transport_error",
            "evidence_status": "transport_error",
            "has_data": False,
            "series_present": False,
            "no_series": False,
        }


def promql_query_range(query: str, start: float | None = None, end: float | None = None,
                        step: str = "30s") -> dict[str, Any]:
    """Execute a PromQL range query (last 15 min by default)."""
    end = end or time.time()
    start = start or (end - 900)
    params = {"query": query, "start": str(start), "end": str(end), "step": step}
    try:
        r = requests.get(f"{PROMETHEUS_URL}/api/v1/query_range", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            return {
                "success": False,
                "error": data.get("error", "unknown"),
                "error_class": "promql_query_range_error",
                "evidence_status": "query_error",
                "has_data": False,
                "series_present": False,
                "no_series": False,
            }
        result = data["data"]["result"]
        return {
            "success": True,
            "result": result,
            **_promql_evidence_semantics(result),
        }
    except requests.RequestException as e:
        return {
            "success": False,
            "error": str(e),
            "error_class": "promql_transport_error",
            "evidence_status": "transport_error",
            "has_data": False,
            "series_present": False,
            "no_series": False,
        }
