import httpx
from fastapi.testclient import TestClient

from demo import live_status
from demo.read_api import create_app


def test_live_observations_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ATLASOPS_DEMO_LIVE_OBSERVATIONS", raising=False)
    monkeypatch.setattr("demo.read_api.observe_services", lambda: (_ for _ in ()).throw(AssertionError()))
    client = TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1))
    assert client.get("/api/live-status").json()["enabled"] is False


def test_enabled_route_still_rejects_writes_and_external_peers(monkeypatch):
    monkeypatch.setenv("ATLASOPS_DEMO_LIVE_OBSERVATIONS", "1")
    monkeypatch.setattr("demo.read_api.observe_services", lambda: {"enabled": True, "services": []})
    client = TestClient(create_app(), base_url="http://localhost", client=("127.0.0.1", 1))
    assert client.get("/api/live-status").json()["enabled"]
    assert client.post("/api/live-status").status_code == 405
    assert client.get("/api/live-status", headers={"host": "evil.example"}).status_code == 403


def test_probes_use_fixed_loopback_gets_without_redirects_or_proxy_env(monkeypatch):
    requests = []
    responses = {
        17880: b"<title>Online Boutique</title>",
        19099: b'{"status":"ok"}',
        19090: b"Prometheus Server is Ready.",
        11434: b'{"models":[{"name":"fixture","secret":"must-not-be-exposed"}]}',
    }

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=responses[request.url.port])

    original_client = httpx.Client

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(live_status.httpx, "Client", client)
    report = live_status.observe_services()
    assert all(row["available"] for row in report["services"])
    assert report["model_inference"] is False
    assert report["incident_execution"] is False
    assert "must-not-be-exposed" not in str(report)
    assert all(request.method == "GET" and request.url.host == "127.0.0.1" for request in requests)


def test_failed_observations_remain_unavailable(monkeypatch):
    original_client = httpx.Client
    monkeypatch.setattr(live_status.httpx, "Client", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    ))
    assert not any(row["available"] for row in live_status.observe_services()["services"])
