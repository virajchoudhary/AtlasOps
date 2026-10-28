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

import httpx
from fastapi import FastAPI, Request, Response

KEY = os.environ["PROXY_KEY"]
UPSTREAM = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

upstream = httpx.AsyncClient(base_url=UPSTREAM, timeout=600)
app = FastAPI()


@app.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def forward(path: str, request: Request) -> Response:
    sent = request.headers.get("authorization", "")
    # compare_digest keeps the check constant-time.
    if not hmac.compare_digest(sent.encode(), f"Bearer {KEY}".encode()):
        return Response(status_code=401)
    reply = await upstream.request(
        request.method, f"/v1/{path}", content=await request.body(),
        headers={"content-type": "application/json"},
    )
    return Response(reply.content, status_code=reply.status_code,
                    media_type=reply.headers.get("content-type"))
