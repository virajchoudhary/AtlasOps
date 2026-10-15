from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


def _write_frontend_dist(root):
    assets = root / "assets"
    reference_assets = root / "reference" / "assets"
    assets.mkdir(parents=True)
    reference_assets.mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><div id="root"></div>'
        '<script type="module" src="./assets/app.js"></script>'
        '<link rel="stylesheet" href="./assets/app.css">',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("window.atlasops = true;", encoding="utf-8")
    (assets / "app.css").write_text("body { color: black; }", encoding="utf-8")
    (reference_assets / "mark.svg").write_text("<svg></svg>", encoding="utf-8")
    return root


def _client(application):
    return AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://atlasops.example",
    )


def _integrated_app(monkeypatch):
    monkeypatch.setenv("ATLASOPS_DEMO_LIVE_OBSERVATIONS", "0")
    from app import app

    return app


@pytest.mark.asyncio
async def test_built_frontend_serves_index_assets_and_nested_routes(tmp_path):
    from app import create_frontend_app

    async with _client(create_frontend_app(_write_frontend_dist(tmp_path))) as client:
        for path in (
            "/",
            "/reference",
            "/blog",
            "/blog/articles/the-content-architecture-four-months-in",
            "/roadmap",
            "/legal/privacy-policy",
        ):
            response = await client.get(path, headers={"accept": "text/html"})
            assert response.status_code == 200
            assert '<div id="root"></div>' in response.text
            assert 'src="/assets/app.js"' in response.text
            assert 'href="/assets/app.css"' in response.text

        assert (await client.get("/assets/app.js")).text == "window.atlasops = true;"
        assert (await client.get("/assets/app.css")).text == "body { color: black; }"
        assert (await client.get("/reference/assets/mark.svg")).text == "<svg></svg>"
        assert (
            await client.get(
                "/reference/not-a-route",
                headers={"accept": "text/html"},
            )
        ).status_code == 404
        for path in ("/../app.py", "/assets/%2e%2e/%2e%2e/app.py"):
            traversal = await client.get(path, headers={"accept": "text/html"})
            assert traversal.status_code == 404
            assert "AtlasOps HF Space entry point" not in traversal.text


@pytest.mark.asyncio
async def test_missing_build_and_traversal_fail_closed(tmp_path, monkeypatch):
    from app import create_frontend_app

    async with _client(create_frontend_app(tmp_path / "missing")) as client:
        response = await client.get(
            "/legal/privacy-policy",
            headers={"accept": "text/html"},
        )
        assert response.status_code == 503
        assert "frontend build" in response.json()["detail"].lower()

    async with _client(_integrated_app(monkeypatch)) as client:
        missing_api = await client.get(
            "/api/not-a-read-route",
            headers={"accept": "text/html"},
        )
        assert missing_api.status_code == 404
        assert missing_api.headers["content-type"].startswith("application/json")
        assert "<!doctype html>" not in missing_api.text.lower()


@pytest.mark.asyncio
async def test_integrated_app_reuses_read_projection_and_keeps_disabled_defaults(monkeypatch):
    from agents.approval import approval_gate

    async with _client(_integrated_app(monkeypatch)) as client:
        pending_before = approval_gate.pending()

        catalog = await client.get("/api/catalog")
        assert catalog.status_code == 200
        data = catalog.json()
        assert data["source"] == "Checked-in repository snapshot; not a live health check"
        assert data["current_results"]["base_vs_sft"]["paired_delta"] == -0.0094
        assert data["gates"][4]["status"] == "NOT_PASSED"
        assert data["product"]["serving_mode"] == "integrated-operational-app"
        assert data["product"]["operator_enabled"] is False
        boundary_labels = {row["label"] for row in data["product"]["boundaries"]}
        assert "PRESENTATION READS GET/HEAD ONLY" in boundary_labels
        assert "LOCALHOST ONLY" not in boundary_labels
        assert "NO KUBECTL" not in boundary_labels
        remediation = next(
            agent for agent in data["product"]["agents"] if agent["id"] == "remediation"
        )
        assert "No actions run in this demo" not in remediation["governance_boundary"]
        assert "exact-action P1 approval" in remediation["governance_boundary"]

        attempt = await client.get("/api/attempts/EXP-STAGE4-SF002-010.json")
        assert attempt.status_code == 200
        assert attempt.json()["name"] == "EXP-STAGE4-SF002-010.json"

        rehearsal = await client.get("/api/rehearsal")
        assert rehearsal.status_code == 200
        assert rehearsal.json()["classification"] == "SYNTHETIC / NON-LIVE / NON-EMPIRICAL"
        assert (await client.get("/api/live-status")).json()["enabled"] is False
        assert (await client.get("/api/incident-monitor")).json() == {"enabled": False}
        assert (await client.get("/api/operator")).json() == {"enabled": False}

        assert (await client.get("/api/scenarios")).status_code == 200
        assert approval_gate.pending() == pending_before


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/catalog"),
        ("put", "/api/attempts/EXP-STAGE4-SF002-010.json"),
        ("patch", "/api/rehearsal"),
        ("delete", "/api/live-status"),
        ("options", "/api/operator"),
    ],
)
@pytest.mark.asyncio
async def test_integrated_read_routes_reject_non_get_methods(monkeypatch, method, path):
    async with _client(_integrated_app(monkeypatch)) as client:
        response = await client.request(method.upper(), path)
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"
