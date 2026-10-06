"""Presentation-only contracts, separate from the operational application."""

import sys

import pytest
from fastapi.testclient import TestClient

from demo.read_api import create_app, product


@pytest.fixture
def client():
    return TestClient(create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def test_canonical_product_counts_without_importing_tool_wrappers():
    snapshot = product()
    assert (snapshot["agent_count"], snapshot["tool_count"], snapshot["scenario_count"]) == (4, 24, 28)
    assert snapshot["agent_exposed_tool_count"] == 19
    assert snapshot["certification"] == "NOT_CERTIFIED"
    assert snapshot["presentation_status"] == "READY_FOR_REVIEW"


def test_exact_current_results_and_g4_chronology(client):
    response = client.get("/api/catalog")
    assert response.status_code == 200
    data = response.json()
    assert "workstreams" not in data["product"]
    assert all("GAI + RL" not in gate["name"] for gate in data["gates"])
    validation = data["current_results"]["base_vs_sft"]
    assert (validation["base_f1"], validation["sft_f1"], validation["paired_delta"]) == (
        0.16875, 0.15935, -0.0094,
    )
    assert validation["resolution_rate"] is None
    diagnostic = data["current_results"]["g9_aligned_diagnostic"]
    assert data["current_results"]["g9_pilot"]["reward_driven_advantage_groups"] == 0
    assert (diagnostic["admissible_count"], diagnostic["sample_count"]) == (0, 8)
    assert diagnostic["optimizer_steps"] == 0
    assert diagnostic["tensor_hash_count"] == 392
    assert diagnostic["tensor_hashes_unchanged"] is True
    assert [row["attempt"] for row in data["current_results"]["g4_attempts"]] == ["015", "016", "017"]
    assert data["gates"][4]["status"] == "NOT_PASSED"
    assert all("mock_archive" not in row["path"] for row in data["evidence"])
    assert "NON-EMPIRICAL" in data["historical_archive"][0]["classification"]
    browser = data["evidence_browser"]
    empirical = [row for row in browser if row["kind"] == "empirical"]
    assert len(empirical) == 3
    assert all(row["scope"] == "current" and row["negative"] for row in empirical)
    external = [row for row in browser if row["availability"] == "external"]
    assert len(external) == 2
    assert all(row["path"] is None and row["sha256"] is None for row in external)
    historical = [row for row in browser if row["scope"] == "historical"]
    assert len(historical) == 3
    assert all(row["kind"] == "non-empirical" and row["sha256"] is None for row in historical)


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "options"])
@pytest.mark.parametrize("path", [
    "/api/catalog", "/inject", "/reset", "/webhook", "/approve",
    "/api/recommender/recommend",
])
def test_all_mutations_are_rejected(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 405


@pytest.mark.parametrize("path", [
    "/inject", "/reset", "/config", "/webhook", "/approve", "/api/recommender/recommend",
    "/openapi.json", "/docs", "/artifacts/evidence/stage7/free-t4-v17/RESULT.json",
    "/api/attempts/EXP-STAGE4-SF002-017.json",
    "/api/attempts/EXP-STAGE4-SF002-010.cleanup.json",
])
def test_no_execution_or_raw_file_routes(client, path):
    assert client.get(path).status_code == 404


def test_unknown_hosts_and_security_headers(client):
    assert client.get("/health", headers={"host": "evil.example"}).status_code == 403
    response = client.get("/health")
    assert response.json()["mode"] == "read-only"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_external_peer_cannot_bypass_with_localhost_header():
    client = TestClient(
        create_app(), base_url="http://localhost", client=("198.51.100.20", 50000),
    )
    assert client.get("/api/catalog").status_code == 403


def test_missing_build_fails_with_actionable_message(tmp_path):
    client = TestClient(
        create_app(tmp_path), base_url="http://localhost", client=("127.0.0.1", 50000),
    )
    assert client.get("/").status_code == 503
    assert "npm run build" in client.get("/").json()["detail"]


def test_fresh_import_never_imports_execution_modules():
    import subprocess

    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; from demo.read_api import create_app; "
         "from fastapi.testclient import TestClient; "
         "c=TestClient(create_app(),base_url='http://localhost',client=('127.0.0.1',50000)); "
         "assert c.get('/api/catalog').status_code==200; "
         "assert not any(n in sys.modules for n in "
         "('app','dashboard','agents.coordinator','agents.tools','torch','transformers','kubernetes')); "
         "print('NO_EXECUTION_IMPORTS')"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "NO_EXECUTION_IMPORTS" in result.stdout


def test_built_assets_and_index_are_served_without_execution(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<title>AtlasOps</title>", encoding="utf-8")
    (tmp_path / "assets/test.js").write_text("export {};", encoding="utf-8")
    client = TestClient(
        create_app(tmp_path), base_url="http://localhost", client=("127.0.0.1", 50000),
    )
    assert client.get("/").text == "<title>AtlasOps</title>"
    assert client.get("/assets/test.js").status_code == 200
    assert client.get("/health").json()["frontend_built"] is True
