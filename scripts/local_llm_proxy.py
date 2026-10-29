"""Key-checking proxy in front of a local Ollama, for the bug lab.

Ollama has no authentication of its own, so the lab's BUG_LAB_LLM_API_KEY would
be decorative without something in front of it. This is the same shape as the
proxy in notebooks/buglab_kaggle_qwen_serving.ipynb, so the container talks to
an identical interface whether the model runs on this Mac or on a Kaggle GPU.

Binds to loopback. Under Colima, host.docker.internal from a container maps to
the Mac's loopback, so the container reaches this without anything listening on
the LAN. The key is read from the environment and never written to disk.

  export PROXY_KEY="$(grep '^BUG_LAB_LLM_API_KEY=' ~/atlasops-local/buglab.env | cut -d= -f2-)"
  python -m uvicorn scripts.local_llm_proxy:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

DEFAULT_UPSTREAM = "http://127.0.0.1:11434"
UPSTREAM_TIMEOUT_SECONDS = 600
MAX_REQUEST_BODY_BYTES = 8 * 1024 * 1024


def create_app(
    *,
    proxy_key: str | None = None,
    upstream_url: str | None = None,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    key = os.environ.get("PROXY_KEY") if proxy_key is None else proxy_key
    base_url = (
        os.environ.get("OLLAMA_BASE_URL", DEFAULT_UPSTREAM)
        if upstream_url is None
        else upstream_url
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=UPSTREAM_TIMEOUT_SECONDS,
            transport=upstream_transport,
        ) as client:
            application.state.upstream = client
            yield

    app = FastAPI(lifespan=lifespan)

    @app.api_route("/v1/{path:path}", methods=["GET", "POST"])
    async def forward(path: str, request: Request) -> Response:
        if key is None or not key.strip():
            return JSONResponse(
                {"detail": "Proxy is not configured"},
                status_code=503,
            )

        sent = request.headers.get("authorization", "")
        if not hmac.compare_digest(sent.encode(), f"Bearer {key}".encode()):
            return Response(status_code=401)

        content_length = request.headers.get("content-length")
        if (
            content_length is not None
            and content_length.isdecimal()
            and int(content_length) > MAX_REQUEST_BODY_BYTES
        ):
            return Response(status_code=413)

        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REQUEST_BODY_BYTES:
                return Response(status_code=413)
            body.extend(chunk)

        try:
            reply = await request.app.state.upstream.request(
                request.method,
                f"/v1/{path}",
                content=bytes(body),
                headers={"content-type": "application/json"},
            )
        except httpx.TimeoutException:
            return JSONResponse(
                {"detail": "Upstream request timed out"},
                status_code=504,
            )
        except httpx.RequestError:
            return JSONResponse(
                {"detail": "Upstream is unavailable"},
                status_code=502,
            )

        if reply.is_error:
            return JSONResponse(
                {"detail": "Upstream request failed"},
                status_code=reply.status_code,
            )

        return Response(
            reply.content,
            status_code=reply.status_code,
            media_type=reply.headers.get("content-type"),
        )

    return app


app = create_app()
