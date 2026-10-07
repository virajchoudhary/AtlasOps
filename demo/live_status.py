"""Opt-in localhost service observations, never incident or inference execution."""

from datetime import UTC, datetime

import httpx

SERVICES = (
    ("boutique", "Online Boutique", "http://127.0.0.1:17880/", "html"),
    ("coordinator", "Coordinator process", "http://127.0.0.1:19099/healthz", "json"),
    ("prometheus", "Prometheus", "http://127.0.0.1:19090/-/ready", "text"),
    ("inference", "Cached model server", "http://127.0.0.1:11434/api/tags", "models"),
)


def observe_services() -> dict:
    rows = []
    with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
        for service_id, label, url, kind in SERVICES:
            row = {"id": service_id, "label": label, "available": False, "detail": "Unavailable"}
            try:
                with client.stream("GET", url) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 131072:
                            raise ValueError("Response exceeds observation limit")
                    row["http_status"] = response.status_code
                    if response.status_code != 200:
                        row["detail"] = f"HTTP {response.status_code}"
                    elif kind == "html":
                        row["available"] = b"Online Boutique" in raw
                        row["detail"] = "Application HTML received" if row["available"] else "Unexpected HTML"
                    elif kind in {"json", "models"}:
                        import json

                        payload = json.loads(raw)
                        if kind == "json":
                            row["available"] = payload.get("status") == "ok"
                            row["detail"] = "Process health only; model execution not tested"
                        else:
                            models = payload.get("models")
                            row["available"] = isinstance(models, list)
                            row["detail"] = (
                                f"{len(models)} cached model entries; no model loaded or tokens generated"
                                if row["available"] else "Invalid model inventory"
                            )
                    else:
                        row["available"] = b"Ready" in raw or b"ready" in raw
                        row["detail"] = "Readiness response received" if row["available"] else "Unexpected response"
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                row["detail"] = "Service unavailable or response invalid"
            rows.append(row)
    return {
        "enabled": True,
        "classification": "LIVE SERVICE OBSERVATIONS / NOT INCIDENT-RESOLUTION EVIDENCE",
        "observed_at": datetime.now(UTC).isoformat(),
        "services": rows,
        "incident_execution": False,
        "model_inference": False,
    }
