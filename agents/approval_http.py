"""Authenticated, same-process loopback transport for an ApprovalGate."""

from __future__ import annotations

import asyncio
import hmac
import socket
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ValidationError

from agents.approval import ApprovalGate


OPERATOR_APPROVAL_TIMEOUT_SECONDS = 300


class ApprovalCallback(BaseModel):
    token: str
    decision: str
    approved_by: str
    reason: str = ""


def approval_app(gate: ApprovalGate, api_key: str) -> FastAPI:
    if not isinstance(api_key, str) or not api_key.strip():
        raise RuntimeError("An explicit operator API key is required")
    header = APIKeyHeader(name="X-AtlasOps-Key", auto_error=False)

    def require_operator(key: str | None = Security(header)) -> None:
        if not key or not hmac.compare_digest(key.encode(), api_key.encode()):
            raise HTTPException(status_code=401, detail="Invalid or missing X-AtlasOps-Key")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/approval/pending", dependencies=[Security(require_operator)])
    async def pending():
        return JSONResponse({"pending": gate.pending()})

    @app.post("/approve", dependencies=[Security(require_operator)])
    async def approve(request: Request):
        try:
            payload = ApprovalCallback.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            raise HTTPException(status_code=422, detail="Invalid approval callback") from exc
        result = gate.callback(
            payload.token, payload.decision, payload.approved_by, payload.reason
        )
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    return app


@asynccontextmanager
async def loopback_approval_server(gate: ApprovalGate, api_key: str):
    """Start on an ephemeral loopback port in the gate waiter's event loop."""
    app = approval_app(gate, api_key)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="warning", access_log=False)
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("Operator approval listener stopped before startup")
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await task
    finally:
        listener.close()
