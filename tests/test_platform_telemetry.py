from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agents import telemetry


@pytest.fixture
def collector(monkeypatch):
    receipts = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            receipts.append((
                self.path,
                dict(self.headers),
                self.rfile.read(int(self.headers["Content-Length"])),
            ))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for signal in ("", "_TRACES", "_METRICS"):
        for option in ("HEADERS", "CLIENT_KEY", "CLIENT_CERTIFICATE", "COMPRESSION"):
            monkeypatch.delenv(f"OTEL_EXPORTER_OTLP{signal}_{option}", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    try:
        yield receipts
    finally:
        telemetry.shutdown_telemetry()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_official_otlp_export_contains_real_spans_metrics_and_trace_identity(collector):
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    telemetry.configure_telemetry()
    with telemetry.trace_span("incident", {"incident.id": "inc-telemetry"}):
        identity = telemetry.current_trace_identity()
        assert identity is not None and len(identity["trace_id"]) == 32
        with telemetry.trace_span("agent.triage", {"agent.role": "triage"}):
            pass
    telemetry.record_incident("unresolved", 0.25)
    telemetry.shutdown_telemetry()

    spans = []
    metrics = []
    for path, headers, body in collector:
        assert "Authorization" not in headers
        if path == "/v1/traces":
            request = ExportTraceServiceRequest.FromString(body)
            spans.extend(
                span
                for resource in request.resource_spans
                for scope in resource.scope_spans
                for span in scope.spans
            )
            services = [
                attribute.value.string_value
                for resource in request.resource_spans
                for attribute in resource.resource.attributes
                if attribute.key == "service.name"
            ]
            assert services == ["atlasops-coordinator"]
        elif path == "/v1/metrics":
            request = ExportMetricsServiceRequest.FromString(body)
            metrics.extend(
                metric
                for resource in request.resource_metrics
                for scope in resource.scope_metrics
                for metric in scope.metrics
            )
    assert {span.name for span in spans} == {"incident", "agent.triage"}
    assert {span.trace_id.hex() for span in spans} == {identity["trace_id"]}
    parent = next(span for span in spans if span.name == "incident")
    child = next(span for span in spans if span.name == "agent.triage")
    assert child.parent_span_id == parent.span_id
    assert all(span.end_time_unix_nano >= span.start_time_unix_nano > 0 for span in spans)
    counter = next(metric for metric in metrics if metric.name == "atlasops_incidents")
    assert counter.sum.data_points[0].as_int == 1


def test_http_trace_uses_the_route_template_and_does_not_export_credentials(collector):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    telemetry.configure_telemetry()
    app = FastAPI()
    telemetry.install_http_telemetry(app)

    @app.get("/private/{value}")
    def private(value: str):
        return {"ok": bool(value)}

    with TestClient(app) as client:
        response = client.get("/private/fixture-sensitive-value", headers={
            "Authorization": "Bearer fixture-private-credential",
        })
        assert response.status_code == 200
    telemetry.shutdown_telemetry()
    bodies = [body for path, _headers, body in collector if path == "/v1/traces"]
    assert bodies
    assert all(
        b"fixture-sensitive-value" not in body and b"fixture-private-credential" not in body
        for body in bodies
    )
    spans = [
        span for body in bodies
        for resource in ExportTraceServiceRequest.FromString(body).resource_spans
        for scope in resource.scope_spans for span in scope.spans
    ]
    attrs = {attr.key: attr.value for attr in spans[0].attributes}
    assert attrs["http.route"].string_value == "/private/{value}"
    assert attrs["http.response.status_code"].int_value == 200


@pytest.mark.parametrize("endpoint", [
    "http://user:fixture-private@127.0.0.1:4318",
    "http://127.0.0.1:4318/?token=fixture-private",
    "http://127.0.0.1:4318/v1/traces",
])
def test_endpoint_configuration_does_not_accept_embedded_credentials(endpoint, monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    with pytest.raises(ValueError, match="collector origin"):
        telemetry.configure_telemetry()
    assert telemetry.telemetry_status()["enabled"] is False


def test_ambient_authentication_cannot_be_silently_exported(collector, monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "authorization=fixture-private")
    with pytest.raises(ValueError, match="ambient authentication"):
        telemetry.configure_telemetry()
    assert collector == []


def test_disabled_telemetry_does_not_start_an_exporter(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    telemetry.configure_telemetry()
    with telemetry.trace_span("incident") as span:
        assert span is None
        assert telemetry.current_trace_identity() is None
    assert telemetry.telemetry_status()["enabled"] is False


def test_failed_incident_root_span_records_error_without_exception_text(
    collector, monkeypatch, tmp_path,
):
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.proto.trace.v1.trace_pb2 import Status
    from agents import coordinator
    from agents.tools import comms

    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "0")
    monkeypatch.setattr(coordinator, "require_audit_log", Mock())
    monkeypatch.setattr(coordinator, "audit_log", Mock())
    monkeypatch.setattr(coordinator, "circuit_breaker", Mock())
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path / "trajectories")
    monkeypatch.setattr(
        coordinator, "call_agent",
        AsyncMock(side_effect=RuntimeError("token=fixture-private-exception")),
    )
    monkeypatch.setattr(comms, "discord_scenario_run_ping", Mock())
    telemetry.configure_telemetry()
    with pytest.raises(RuntimeError):
        asyncio.run(coordinator.handle_incident(
            {"commonLabels": {"alertname": "TraceFailure"}},
            incident_id="inc-trace-failure",
        ))
    telemetry.shutdown_telemetry()
    bodies = [body for path, _headers, body in collector if path == "/v1/traces"]
    spans = [
        span for body in bodies
        for resource in ExportTraceServiceRequest.FromString(body).resource_spans
        for scope in resource.scope_spans for span in scope.spans
    ]
    root = next(span for span in spans if span.name == "incident")
    assert root.status.code == Status.STATUS_CODE_ERROR
    assert all(b"fixture-private-exception" not in body for body in bodies)
