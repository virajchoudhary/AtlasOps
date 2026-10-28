from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from uvicorn.importer import import_from_string


def _proxy_module():
    return importlib.import_module("scripts.local_llm_proxy")


def test_default_app_imports_and_fails_closed_without_proxy_key(monkeypatch):
    monkeypatch.delenv("PROXY_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test")
    module = importlib.reload(_proxy_module())

    with TestClient(module.app) as client:
        response = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer "},
        )

    assert response.status_code == 503
    assert "PROXY_KEY" not in response.text


def test_proxy_module_alias_still_exports_app(monkeypatch):
    monkeypatch.delenv("PROXY_KEY", raising=False)
    module_path = Path(__file__).parents[1] / "scripts" / "local_llm_proxy.py"
    spec = importlib.util.spec_from_file_location("proxy", module_path)
    assert spec is not None
    assert spec.loader is not None

    previous_module = sys.modules.get("proxy")
    module = importlib.util.module_from_spec(spec)
    sys.modules["proxy"] = module
    try:
        spec.loader.exec_module(module)
        assert import_from_string("proxy:app") is module.app
    finally:
        if previous_module is None:
            sys.modules.pop("proxy", None)
        else:
            sys.modules["proxy"] = previous_module


@pytest.mark.parametrize("proxy_key", ["", " \t "])
def test_empty_or_whitespace_proxy_key_fails_closed(proxy_key):
    from scripts.local_llm_proxy import create_app

    forwarded = []
    transport = httpx.MockTransport(
        lambda request: (
            forwarded.append(request)
            or httpx.Response(200, json={"models": []}, request=request)
        )
    )
    app = create_app(
        proxy_key=proxy_key,
        upstream_url="http://ollama.test",
        upstream_transport=transport,
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer "},
        )

    assert response.status_code == 503
    assert forwarded == []


@pytest.mark.parametrize(
    ("method", "path", "request_body", "expected_status"),
    [
        ("GET", "/v1/models", b"", 200),
        ("POST", "/v1/chat/completions", b'{"model":"test"}', 201),
    ],
)
def test_authenticated_request_preserves_success_and_does_not_forward_key(
    method, path, request_body, expected_status
):
    from scripts.local_llm_proxy import create_app

    body = b'{"choices":[{"message":{"content":"ok"}}]}'
    forwarded = []

    def respond(request):
        forwarded.append(request)
        return httpx.Response(
            expected_status,
            content=body,
            headers={"content-type": "application/json"},
            request=request,
        )

    app = create_app(
        proxy_key="synthetic-client-key",
        upstream_url="http://ollama.test",
        upstream_transport=httpx.MockTransport(respond),
    )

    with TestClient(app) as client:
        response = client.request(
            method,
            path,
            content=request_body,
            headers={"Authorization": "Bearer synthetic-client-key"},
        )

    assert response.status_code == expected_status
    assert response.content == body
    assert response.headers["content-type"] == "application/json"
    assert len(forwarded) == 1
    assert forwarded[0].method == method
    assert forwarded[0].url.path == path
    assert forwarded[0].content == request_body
    assert "authorization" not in forwarded[0].headers


@pytest.mark.parametrize("authorization", [None, "Bearer wrong-key", "Bearer"])
def test_missing_or_wrong_authorization_never_reaches_upstream(authorization):
    from scripts.local_llm_proxy import create_app

    forwarded = []
    app = create_app(
        proxy_key="synthetic-client-key",
        upstream_url="http://ollama.test",
        upstream_transport=httpx.MockTransport(
            lambda request: forwarded.append(request) or httpx.Response(200, content=b"ok"),
        ),
    )
    headers = {} if authorization is None else {"Authorization": authorization}
    with TestClient(app) as client:
        response = client.get("/v1/models", headers=headers)
    assert response.status_code == 401
    assert response.content == b""
    assert forwarded == []


class _TrackingTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.closed = False

    async def handle_async_request(self, request):
        return httpx.Response(200, content=b"ok", request=request)

    async def aclose(self):
        self.closed = True


def test_lifespan_closes_upstream_transport():
    from scripts.local_llm_proxy import create_app

    transport = _TrackingTransport()
    app = create_app(
        proxy_key="synthetic-client-key",
        upstream_url="http://ollama.test",
        upstream_transport=transport,
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer synthetic-client-key"},
        )

    assert response.status_code == 200
    assert response.content == b"ok"
    assert transport.closed


@pytest.mark.parametrize("chunked", [False, True])
def test_oversized_request_is_rejected_before_upstream(chunked):
    from scripts.local_llm_proxy import create_app

    forwarded = []
    transport = httpx.MockTransport(
        lambda request: (
            forwarded.append(request)
            or httpx.Response(200, content=b"ok", request=request)
        )
    )
    app = create_app(
        proxy_key="synthetic-client-key",
        upstream_url="http://ollama.test",
        upstream_transport=transport,
    )
    body = b"x" * (8 * 1024 * 1024 + 1)
    content = (
        iter((body[:4 * 1024 * 1024], body[4 * 1024 * 1024:]))
        if chunked
        else body
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            content=content,
            headers={"Authorization": "Bearer synthetic-client-key"},
        )

    assert response.status_code == 413
    assert forwarded == []


@pytest.mark.parametrize(
    ("error_type", "expected_status"),
    [
        (httpx.ConnectError, 502),
        (httpx.ReadTimeout, 504),
    ],
)
def test_upstream_transport_errors_have_controlled_responses(
    error_type, expected_status, caplog
):
    from scripts.local_llm_proxy import create_app

    def fail(request):
        raise error_type("synthetic-upstream-secret", request=request)

    app = create_app(
        proxy_key="synthetic-client-key",
        upstream_url="http://ollama.test",
        upstream_transport=httpx.MockTransport(fail),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer synthetic-client-key"},
        )

    assert response.status_code == expected_status
    assert "synthetic-upstream-secret" not in response.text
    assert "synthetic-client-key" not in response.text
    assert "synthetic-upstream-secret" not in caplog.text
    assert "synthetic-client-key" not in caplog.text


def test_upstream_error_status_does_not_forward_raw_body():
    from scripts.local_llm_proxy import create_app

    raw_body = b"synthetic-upstream-secret synthetic-client-key"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, content=raw_body, request=request)
    )
    app = create_app(
        proxy_key="synthetic-client-key",
        upstream_url="http://ollama.test",
        upstream_transport=transport,
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer synthetic-client-key"},
        )

    assert response.status_code == 500
    assert "synthetic-upstream-secret" not in response.text
    assert "synthetic-client-key" not in response.text
