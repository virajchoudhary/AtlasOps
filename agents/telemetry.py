from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlsplit


class _RuntimeTelemetry:
    def __init__(self, endpoint: str):
        import requests
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource({
            "service.name": "atlasops-coordinator",
            "service.version": os.getenv("ATLASOPS_SOURCE_SHA", "unknown"),
        })
        self.traces = TracerProvider(resource=resource)
        trace_session, metric_session = requests.Session(), requests.Session()
        trace_session.trust_env = metric_session.trust_env = False
        headers = {"Content-Type": "application/x-protobuf"}
        self.traces.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=endpoint + "/v1/traces", headers=headers,
                timeout=2, session=trace_session,
            ),
            max_queue_size=128, max_export_batch_size=128, schedule_delay_millis=500,
        ))
        self.tracer = self.traces.get_tracer("atlasops.operational")
        self.metrics = MeterProvider(
            resource=resource,
            metric_readers=[PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=endpoint + "/v1/metrics", headers=headers,
                    timeout=2, session=metric_session,
                ),
                export_interval_millis=5000, export_timeout_millis=2000,
            )],
        )
        meter = self.metrics.get_meter("atlasops.operational")
        self.incidents = meter.create_counter("atlasops_incidents", unit="1")
        self.incident_duration = meter.create_histogram("atlasops_incident_duration", unit="s")
        self.http_requests = meter.create_counter("atlasops_http_requests", unit="1")
        self.http_duration = meter.create_histogram("atlasops_http_duration", unit="s")

    def close(self) -> None:
        self.traces.force_flush(timeout_millis=2000)
        self.metrics.force_flush(timeout_millis=2000)
        self.traces.shutdown()
        self.metrics.shutdown(timeout_millis=2000)


_runtime: _RuntimeTelemetry | None = None


def configure_telemetry() -> None:
    global _runtime
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip().rstrip("/")
    if not endpoint:
        return
    if any(
        os.getenv(f"OTEL_EXPORTER_OTLP{signal}_{option}")
        for signal in ("", "_TRACES", "_METRICS")
        for option in ("HEADERS", "CLIENT_KEY", "CLIENT_CERTIFICATE")
    ):
        raise ValueError("Operational OTLP cannot use ambient authentication or client credentials")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or parsed.path
    ):
        raise ValueError("OTLP requires an explicit collector origin without credentials or a path")
    if _runtime is not None:
        raise RuntimeError("Operational telemetry already has a runtime owner")
    _runtime = _RuntimeTelemetry(endpoint)


def shutdown_telemetry() -> None:
    global _runtime
    runtime, _runtime = _runtime, None
    if runtime is not None:
        runtime.close()


@contextmanager
def trace_span(name: str, attributes: dict[str, str] | None = None) -> Iterator[Any]:
    if _runtime is None:
        yield None
        return
    from opentelemetry.trace import Status, StatusCode

    with _runtime.tracer.start_as_current_span(
        name, attributes=attributes or {}, record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except BaseException as error:
            span.set_attribute("error.type", type(error).__name__)
            span.set_status(Status(StatusCode.ERROR))
            raise


def current_trace_identity() -> dict[str, str] | None:
    if _runtime is None:
        return None
    from opentelemetry.trace import get_current_span

    context = get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}


def record_incident(outcome: str, duration_seconds: float) -> None:
    if _runtime is not None:
        attributes = {"outcome": outcome}
        _runtime.incidents.add(1, attributes)
        _runtime.incident_duration.record(duration_seconds, attributes)


def record_http(method: str, route: str, status: int, duration_seconds: float) -> None:
    if _runtime is not None:
        attributes = {"method": method, "route": route, "status": str(status)}
        _runtime.http_requests.add(1, attributes)
        _runtime.http_duration.record(duration_seconds, attributes)


def telemetry_status() -> dict[str, Any]:
    return {
        "enabled": _runtime is not None,
        "service": "atlasops-coordinator",
        "protocol": "otlp/http",
        "evidence_scope": "observed_requests_and_incident_execution",
    }


def install_http_telemetry(application: Any) -> None:
    @application.middleware("http")
    async def observe_request(request: Any, call_next: Any):
        started = time.monotonic()
        status = 500
        with trace_span("http.request") as span:
            try:
                response = await call_next(request)
                status = response.status_code
                return response
            finally:
                route = getattr(request.scope.get("route"), "path", "unmatched")
                record_http(request.method, route, status, time.monotonic() - started)
                if span is not None:
                    span.set_attribute("http.request.method", request.method)
                    span.set_attribute("http.route", route)
                    span.set_attribute("http.response.status_code", status)
