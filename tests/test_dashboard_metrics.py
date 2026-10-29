import time

import httpx
import pytest

from agents.prometheus_metrics import build_dashboard_metrics_payload


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://fixture-prometheus.invalid")
    monkeypatch.setenv("ALERTMANAGER_URL", "http://fixture-alertmanager.invalid")
    requests = []
    reply = {"status": "success", "data": {"resultType": "vector", "result": []}}

    def handle(request):
        requests.append(request)
        if request.url.path == "/api/v2/alerts":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=reply)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs),
    )
    return requests, reply


@pytest.mark.asyncio
async def test_missing_application_samples_are_not_zero(transport):
    requests, _reply = transport
    result = await build_dashboard_metrics_payload()
    assert result["rps"] is None
    assert result["error_rate"] is None
    assert result["cpu"] is None
    assert result["alerts"] == 0
    queries = [str(request.url.params.get("query", "")) for request in requests]
    assert not any("apiserver_request_total" in query or "vector(0)" in query for query in queries)
    assert any("atlasops_boutique_calls_total" in query for query in queries)


@pytest.mark.parametrize("value,timestamp", [
    ("NaN", 0), ("Inf", 0), ("-1", 0), ("2", -300), ("2", 30),
])
@pytest.mark.asyncio
async def test_invalid_or_stale_samples_remain_unavailable(transport, value, timestamp):
    _requests, reply = transport
    reply["data"]["result"] = [{"metric": {}, "value": [time.time() + timestamp, value]}]
    result = await build_dashboard_metrics_payload()
    assert result["rps"] is None
    assert result["error_rate"] is None
    assert result["cpu"] is None


@pytest.mark.asyncio
async def test_observed_zero_is_distinct_from_missing_data(transport):
    _requests, reply = transport
    reply["data"]["result"] = [{"metric": {}, "value": [time.time(), "0"]}]
    result = await build_dashboard_metrics_payload()
    assert result["rps"] == 0
    assert result["error_rate"] == 0
    assert result["cpu"] == 0
    assert result["attribution"]["rps"] == "sampled_otlp_server_spans"


@pytest.mark.asyncio
async def test_error_api_response_cannot_be_interpreted_as_a_metric(transport):
    _requests, reply = transport
    reply["status"] = "error"
    reply["data"]["result"] = [{"metric": {}, "value": [time.time(), "1"]}]
    result = await build_dashboard_metrics_payload()
    assert result["rps"] is None
    assert result["error_rate"] is None
    assert result["cpu"] is None
