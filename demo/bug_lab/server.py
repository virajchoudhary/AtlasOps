"""FastAPI app for the bug lab: the control room at ``/`` and the store at ``/shop/``."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from demo.bug_lab.engine import ApprovalError, BugLab, LabBusy, LabOptions
from demo.bug_lab.llm import RULES, Backend, detect_backend
from demo.bug_lab.workspace import Workspace

STATIC = Path(__file__).resolve().parent / "static"
_MEDIA = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
          ".js": "text/javascript; charset=utf-8", ".json": "application/json",
          ".svg": "image/svg+xml"}
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    ),
}
_NO_STORE = {"Cache-Control": "no-store"}
MAX_TELEMETRY_BYTES = 64 * 1024
_sleep = asyncio.sleep  # the slow-API bug's delay; tests shorten it


@dataclass
class LabConfig:
    workdir: Path
    agents: str = "auto"
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str = ""
    llm_timeout: float = 120.0
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]")
    options: LabOptions = field(default_factory=LabOptions)


def require_lab_header(request: Request) -> None:
    """Require an intentional same-origin write; the fixed header is not authentication."""
    if request.headers.get("x-bug-lab") != "1":
        raise HTTPException(status_code=403, detail="Missing X-Bug-Lab header")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Foreign Origin is not permitted")


async def _json_body(request: Request, limit: int = 8192) -> dict[str, Any]:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise HTTPException(status_code=413, detail="Request body too large")
        raw.extend(chunk)
    try:
        body = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Body must be JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return body


def create_app(config: LabConfig, backend: Backend | None = None) -> FastAPI:
    config.workdir.mkdir(parents=True, exist_ok=True)
    workspace = Workspace(config.workdir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if backend is None:
            lab.backend = await detect_backend(config.agents, config.llm_base_url,
                                               config.llm_model, config.llm_api_key,
                                               timeout=config.llm_timeout)
        await lab.start()
        try:
            yield
        finally:
            await lab.stop()

    app = FastAPI(title="AtlasOps Bug Lab", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(config.allowed_hosts),
                       www_redirect=False)

    def shop_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
                                 timeout=15.0)

    lab = BugLab(workspace, backend or RULES, shop_client, config.options,
                 log_path=config.workdir / "incidents.jsonl")
    app.state.lab = lab

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    def static_file(name: str) -> FileResponse:
        path = STATIC / name
        return FileResponse(path, media_type=_MEDIA[path.suffix], headers=_NO_STORE)

    @app.get("/", include_in_schema=False)
    async def control_room() -> FileResponse:
        return static_file("index.html")

    @app.get("/lab/static/{name}", include_in_schema=False)
    async def lab_static(name: str) -> FileResponse:
        if name not in {"lab.css", "lab.js"}:
            raise HTTPException(status_code=404)
        return static_file(name)

    @app.get("/lab/monitor.js", include_in_schema=False)
    async def monitor_script() -> FileResponse:
        return static_file("monitor.js")

    @app.get("/shop", include_in_schema=False)
    async def shop_root() -> RedirectResponse:
        return RedirectResponse("/shop/")

    @app.get("/shop/api/products")
    async def products() -> Response:
        try:
            delay_ms = float(json.loads(workspace.read("config.json")).get("api_delay_ms", 0))
        except (ValueError, AttributeError, TypeError):
            delay_ms = 0.0
        if delay_ms > 0:
            await _sleep(min(delay_ms, 10_000) / 1000)
        try:
            data = json.loads(workspace.read("products.json"))
        except ValueError as exc:
            return JSONResponse({"error": f"Invalid product data: {exc}"}, status_code=500, headers=_NO_STORE)
        return JSONResponse(data, headers=_NO_STORE)

    @app.get("/shop/{path:path}", include_in_schema=False)
    async def shop_file(path: str) -> Response:
        resolved = workspace.public_path(path)
        if resolved is None:
            raise HTTPException(status_code=404)
        return Response(resolved.read_bytes(),
                        media_type=_MEDIA.get(resolved.suffix, "application/octet-stream"),
                        headers=_NO_STORE)

    @app.get("/lab/api/state")
    async def state() -> JSONResponse:
        return JSONResponse(lab.snapshot(), headers=_NO_STORE)

    @app.get("/lab/api/evidence")
    async def evidence() -> Response:
        raw = lab.log_path.read_bytes() if lab.log_path and lab.log_path.is_file() else b""
        return Response(raw, media_type="application/x-ndjson", headers={
            **_NO_STORE, "Content-Disposition": 'attachment; filename="atlasops-incidents.jsonl"',
        })

    @app.get("/lab/api/site-version")
    async def site_version() -> JSONResponse:
        return JSONResponse({"version": workspace.version()}, headers=_NO_STORE)

    @app.post("/lab/api/inject", dependencies=[Depends(require_lab_header)])
    async def inject(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            record = lab.inject(str(body.get("bug", "")))
        except LabBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"injected": record["id"]}, status_code=202)

    @app.post("/lab/api/reset", dependencies=[Depends(require_lab_header)])
    async def reset() -> JSONResponse:
        await lab.reset()
        return JSONResponse({"reset": True})

    @app.post("/lab/api/approval", dependencies=[Depends(require_lab_header)])
    async def approval(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            lab.decide(str(body.get("incident_id", "")), str(body.get("digest", "")),
                       str(body.get("decision", "")))
        except ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"recorded": True})

    @app.post("/lab/api/settings", dependencies=[Depends(require_lab_header)])
    async def settings(request: Request) -> JSONResponse:
        body = await _json_body(request)
        if "require_approval_all" in body:
            lab.require_approval_all = bool(body["require_approval_all"])
        return JSONResponse({"require_approval_all": lab.require_approval_all})

    @app.post("/lab/api/telemetry", dependencies=[Depends(require_lab_header)])
    async def telemetry(request: Request) -> JSONResponse:
        body = await _json_body(request, MAX_TELEMETRY_BYTES)
        try:
            lab.record_browser_report(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"recorded": True})

    return app
