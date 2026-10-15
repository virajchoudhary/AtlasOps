"""AtlasOps HF Space entry point.

Serves the custom ops console UI at / and wires the coordinator API.
This is what HF Spaces runs via the Dockerfile.
"""

import hmac
import json
import logging
import os
import subprocess
from collections import deque
from pathlib import Path
from types import MappingProxyType

from config.hf_space_env import apply_hf_space_inference_defaults

apply_hf_space_inference_defaults()

from fastapi import FastAPI, HTTPException, Query, Request, Security
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.routing import APIRoute
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

log = logging.getLogger("atlasops")

# ── Auth ──────────────────────────────────────────────────────────────────────
# Mutating and approval endpoints fail closed without an operator API key.
_API_KEY = os.getenv("ATLASOPS_API_KEY", "")
_api_key_header = APIKeyHeader(name="X-AtlasOps-Key", auto_error=False)

# HMAC secret for validating Alertmanager webhook payloads.
# Set ALERTMANAGER_WEBHOOK_SECRET and configure Alertmanager to send:
#   Authorization: Bearer <secret>
_WEBHOOK_SECRET = os.getenv("ALERTMANAGER_WEBHOOK_SECRET", "")

if not _API_KEY:
    log.warning("ATLASOPS_API_KEY not set — mutating and approval endpoints are disabled")
if not _WEBHOOK_SECRET:
    log.warning("ALERTMANAGER_WEBHOOK_SECRET not set — webhook is disabled")


def _require_api_key(key: str | None = Security(_api_key_header)) -> None:
    """Require configured operator auth before any protected action."""
    if not _API_KEY:
        raise HTTPException(status_code=503, detail="ATLASOPS_API_KEY is required for this endpoint")
    if not key or not hmac.compare_digest(key, _API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-AtlasOps-Key header")


def _verify_webhook_signature(body: bytes, authorization: str | None) -> None:
    """Validate Alertmanager webhook Bearer token before dispatch."""
    if not _WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="ALERTMANAGER_WEBHOOK_SECRET is required")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header on webhook")
    token = authorization.removeprefix("Bearer ").strip()
    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(token.encode(), _WEBHOOK_SECRET.encode()):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

# Import coordinator internals
import agents.coordinator as coordinator_module
from agents import incident_history
from agents.approval import approval_gate
from agents.audit import audit_log
from agents.circuit_breaker import circuit_breaker
from agents.coordinator import (
    app as coordinator_app,
)
from agents.coordinator import (
    build_incident_anchors,
    parse_alert_webhook_payload,
    read_alert_webhook_body,
)
from agents.correlator import correlator
from agents.incident_dispatch import incident_dispatcher
from agents.prometheus_metrics import build_dashboard_metrics_payload
from agents.stream import get_history, subscribe
from demo import read_api as presentation_read_api
from ui_read_model import attempt_detail, catalog

app = FastAPI(title="AtlasOps", docs_url="/api/docs")

_PRESENTATION_READ_PATHS = (
    "/api/catalog",
    "/api/attempts/{name}",
    "/api/rehearsal",
    "/api/live-status",
    "/api/incident-monitor",
    "/api/operator",
)
_FALLBACK_FRONTEND_PATHS = (
    "/",
    "/reference",
    "/blog",
    "/blog/articles",
    "/blog/the-content-architecture-four-months-in",
    "/blog/serve-content-to-ai-agents-llms-txt-markdown-sanity",
    "/blog/stale-sanity-content-nextjs-caching-revalidation",
    "/blog/articles/the-content-architecture-four-months-in",
    "/blog/articles/serve-content-to-ai-agents-llms-txt-markdown-sanity",
    "/blog/articles/stale-sanity-content-nextjs-caching-revalidation",
    "/roadmap",
    "/legal/privacy-policy",
    "/legal/terms-of-service",
)
_FRONTEND_PATHS = tuple(
    getattr(presentation_read_api, "FRONTEND_PATHS", _FALLBACK_FRONTEND_PATHS)
)
_APP_PRESENTATION_BOUNDARIES = (
    {
        "id": "read-api",
        "label": "PRESENTATION READS GET/HEAD ONLY",
        "state": "ENFORCED",
    },
    {
        "id": "approval-auth",
        "label": "APPROVAL ENDPOINTS REQUIRE OPERATOR API KEY",
        "state": "ENFORCED",
    },
    {
        "id": "webhook-auth",
        "label": "ALERT WEBHOOK REQUIRES BEARER SECRET",
        "state": "ENFORCED",
    },
    {
        "id": "retired-fault-routes",
        "label": "WEB INJECTION AND RESET ARE RETIRED",
        "state": "ENFORCED",
    },
)
FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"


def _presentation_read_handlers():
    projection_app = presentation_read_api.create_app()
    handlers = {
        route.path: route.endpoint
        for route in projection_app.routes
        if isinstance(route, APIRoute)
    }
    missing = set(_PRESENTATION_READ_PATHS) - handlers.keys()
    if missing:
        raise RuntimeError(f"Presentation read API routes are unavailable: {sorted(missing)}")
    return handlers


_PRESENTATION_READ_HANDLERS = MappingProxyType(_presentation_read_handlers())


def _is_presentation_read_path(path: str) -> bool:
    return (
        path
        in {
            "/api/catalog",
            "/api/rehearsal",
            "/api/live-status",
            "/api/incident-monitor",
            "/api/operator",
        }
        or path == "/api/attempts"
        or path.startswith("/api/attempts/")
    )


@app.middleware("http")
async def presentation_read_boundary(request: Request, call_next):
    if _is_presentation_read_path(request.url.path):
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse(
                {"detail": "Read-only presentation; no execution endpoints"},
                status_code=405,
                headers={"Allow": "GET, HEAD"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    return await call_next(request)


def _register_frontend_routes(application: FastAPI, dist: Path) -> None:
    asset_dir = dist / "assets"
    reference_asset_dir = dist / "reference" / "assets"
    if asset_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=str(asset_dir)), name="frontend-assets")
    if reference_asset_dir.is_dir():
        application.mount(
            "/reference/assets",
            StaticFiles(directory=str(reference_asset_dir)),
            name="reference-assets",
        )

    def index_response():
        index_path = dist / "index.html"
        if not index_path.is_file():
            raise HTTPException(
                status_code=503,
                detail="React frontend build missing. Run npm ci and npm run build in frontend/.",
            )
        try:
            html = index_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail="React frontend build is unavailable.",
            ) from exc
        html = html.replace('src="./assets/', 'src="/assets/')
        html = html.replace('href="./assets/', 'href="/assets/')
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    def index():
        return index_response()

    for frontend_path in _FRONTEND_PATHS:
        application.add_api_route(
            frontend_path,
            index,
            methods=["GET", "HEAD"],
            include_in_schema=False,
            name=f"frontend_{frontend_path.strip('/').replace('/', '_') or 'root'}",
        )


def create_frontend_app(frontend_dist: Path | None = None) -> FastAPI:
    """Build a small HTTP app for exercising the built frontend routes."""
    frontend = FastAPI(
        title="AtlasOps frontend",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    dist = frontend_dist if frontend_dist is not None else FRONTEND_DIST
    _register_frontend_routes(frontend, dist)
    return frontend


def read_integrated_catalog():
    payload = _PRESENTATION_READ_HANDLERS["/api/catalog"]()
    product = payload["product"]
    product["serving_mode"] = "integrated-operational-app"
    product["operator_enabled"] = False
    product["boundaries"] = [dict(item) for item in _APP_PRESENTATION_BOUNDARIES]
    for agent in product["agents"]:
        if agent["id"] == "remediation":
            agent["governance_boundary"] = (
                "Role ACL and fail-closed exact-action P1 approval through the operator API."
            )
    return payload


for _path in _PRESENTATION_READ_PATHS:
    _endpoint = (
        read_integrated_catalog
        if _path == "/api/catalog"
        else _PRESENTATION_READ_HANDLERS[_path]
    )
    app.add_api_route(
        _path,
        _endpoint,
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )

# Mount coordinator routes
app.mount("/api", coordinator_app)

# Serve static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


class InjectRequest(BaseModel):
    scenario_id: str = Field(min_length=1)
    name: str | None = None


class ResetRequest(BaseModel):
    scenario_id: str = Field(min_length=1)


class RuntimeConfigResponse(BaseModel):
    coordinator_url: str
    grafana_url: str
    argocd_url: str
    boutique_url: str


class ApprovalCallbackRequest(BaseModel):
    token: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    approved_by: str = ""
    reason: str = ""


@app.post("/inject", dependencies=[Security(_require_api_key)])
async def inject_chaos(_body: InjectRequest):
    """Retire the web shortcut until it can meet the Stage 4 preflight contract."""
    raise HTTPException(
        status_code=503,
        detail="Web injection is retired; use the governed Stage 4 harness after preflight",
    )


@app.post("/reset", dependencies=[Security(_require_api_key)])
async def reset_chaos(_body: ResetRequest):
    """Never claim cleanup from an unverified web command."""
    raise HTTPException(
        status_code=503,
        detail="Web cleanup is retired; use governed scoped cleanup and environment verification",
    )


@app.get("/stream")
async def stream():
    return StreamingResponse(
        subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/thoughts")
async def thoughts():
    return JSONResponse({"thoughts": get_history()})


@app.get("/metrics")
async def proxy_metrics():
    """Server-side Prometheus proxy — avoids browser CORS on direct GKE IP access."""
    return JSONResponse(await build_dashboard_metrics_payload())


@app.get("/bench/results/comparison_table.md")
def comparison_table_markdown():
    """UI fetches this path; serve repo file or a tiny placeholder."""
    p = Path(__file__).resolve().parent / "bench" / "results" / "comparison_table.md"
    if not p.is_file():
        return PlainTextResponse(
            "| Scenario | Outcome | Notes |\n|---|---|---|\n"
            "| *unavailable* | — | No reviewed comparison artifact is available. Local fixture metrics are NON_EMPIRICAL. |\n",
            media_type="text/markdown; charset=utf-8",
        )
    return PlainTextResponse(p.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


@coordinator_app.get("/scenarios")
@app.get("/api/scenarios")
async def get_scenarios_list():
    from config.scenario_catalog import SCENARIO_CATALOG
    scenarios = [
        {
            "scenario_id": s.scenario_id,
            "tier": s.tier,
            "expected_alert": s.expected_alert,
            "target_services": list(s.target_services),
            "description": s.description,
        }
        for s in SCENARIO_CATALOG.values()
    ]
    return JSONResponse({"scenarios": scenarios})


class RecommenderQueryRequest(BaseModel):
    alert_name: str
    service: str
    symptoms: str = ""
    top_k: int = 3


@coordinator_app.post("/recommender/recommend")
@app.post("/api/recommender/recommend")
def query_recommender_endpoint(body: RecommenderQueryRequest):
    from recommender.dataset import load_interactions
    from recommender.hybrid import HybridRecommender
    ckpt_path = Path("artifacts/models/hybrid_recommender.json")
    if ckpt_path.exists():
        recommender = HybridRecommender.load_checkpoint(ckpt_path)
    else:
        recommender = HybridRecommender().fit(load_interactions())

    query = {
        "alertname": body.alert_name,
        "affected_services": [body.service],
        "symptoms_text": body.symptoms,
        "tier": "single_fault",
    }
    recs = recommender.recommend_runbooks(query, k=body.top_k)
    return JSONResponse({
        "query": {"alert_name": body.alert_name, "service": body.service, "symptoms": body.symptoms},
        "recommendations": [
            {
                "runbook_id": r.runbook_id,
                "title": r.title,
                "category": r.category,
                "score": round(r.score, 4),
                "explanation": r.explanation,
                "suggested_tools": r.suggested_tools,
                "action_sequence": r.actions,
            }
            for r in recs
        ],
    })


@coordinator_app.get("/ablation-matrix")
@app.get("/api/ablation-matrix")
def get_ablation_matrix_endpoint():
    p = Path("artifacts/evidence/stage13/ablation_benchmark_results.json")
    if p.exists():
        payload = json.loads(p.read_text(encoding="utf-8"))
        payload["evidence_classification"] = "historical_non_empirical"
        payload["empirical"] = False
        payload["evidence_note"] = (
            "Predetermined five-arm profiles retained for historical traceability; "
            "not measured results or the required three-arm comparison."
        )
        return JSONResponse(payload)
    return JSONResponse({"error": "Ablation results not found"}, 404)


@app.get("/health")
async def health():
    from agents.coordinator import _live_judge_requested

    agent_base = os.getenv("VLLM_BASE", "").rstrip("/")
    ju = os.getenv("JUDGE_URL", "").rstrip("/")
    return JSONResponse({
        "status": "ok",
        "model": os.getenv("AGENT_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
        "agent_base": agent_base,
        "backend": os.getenv("BACKEND", "vllm"),
        "judge_model": os.getenv("JUDGE_MODEL", ""),
        "judge_base": ju,
        "hf_inference_pack": os.getenv("ATLASOPS_USE_HF_INFERENCE", ""),
        "live_judge": _live_judge_requested(),
        "discord_webhook_configured": bool(os.getenv("DISCORD_WEBHOOK_URL", "").strip()),
        "slack_webhook_configured": bool(os.getenv("SLACK_WEBHOOK_URL", "").strip()),
    })


@app.get("/ui/catalog")
def ui_catalog():
    """Checked-in governance and historical evidence, never inferred live state."""
    try:
        return JSONResponse(catalog())
    except (OSError, ValueError):
        log.exception("UI catalog unavailable")
        raise HTTPException(status_code=503, detail="Repository status unavailable")


@app.get("/ui/attempts/{name}")
def ui_attempt(name: str):
    """Allowlisted, bounded projection of one preserved attempt."""
    try:
        return JSONResponse(attempt_detail(name))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Preserved attempt unavailable")


@app.get("/config")
async def runtime_config():
    """Expose runtime URLs so UI doesn't rely on hardcoded IPs."""
    # Empty → browser keeps `window.location.origin` (required for HF Spaces).
    coordinator_url = (os.getenv("COORDINATOR_URL") or "").strip()
    grafana_url = os.getenv("GRAFANA_URL", "")
    argocd_url = os.getenv("ARGOCD_URL", "")
    boutique_url = os.getenv("BOUTIQUE_URL", "")
    return JSONResponse(
        RuntimeConfigResponse(
            coordinator_url=coordinator_url,
            grafana_url=grafana_url,
            argocd_url=argocd_url,
            boutique_url=boutique_url,
        ).model_dump()
    )


@app.post("/approval/callback", dependencies=[Security(_require_api_key)])
async def approval_callback(payload: ApprovalCallbackRequest):
    result = approval_gate.callback(
        token=payload.token,
        decision=payload.decision,
        approved_by=payload.approved_by,
        reason=payload.reason,
    )
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@app.post("/approve", dependencies=[Security(_require_api_key)])
async def approve_public(payload: ApprovalCallbackRequest):
    """Operator approval endpoint; a token alone is not authorization."""
    result = approval_gate.callback(
        token=payload.token,
        decision=payload.decision,
        approved_by=payload.approved_by or "human-operator",
        reason=payload.reason,
    )
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@app.get("/approval/pending", dependencies=[Security(_require_api_key)])
async def approval_pending():
    return JSONResponse({"pending": approval_gate.pending()})


@app.get("/circuit-breaker/status")
async def circuit_breaker_status():
    return JSONResponse(circuit_breaker.status())


@app.post("/circuit-breaker/reset", dependencies=[Security(_require_api_key)])
async def circuit_breaker_reset():
    return JSONResponse(circuit_breaker.reset())


@app.get("/incidents/active")
async def incidents_active():
    return JSONResponse({"incidents": correlator.get_active()})


@app.get("/incidents/history", dependencies=[Security(_require_api_key)])
def incidents_history(
    limit: int = Query(default=50, ge=1, le=incident_history.MAX_HISTORY_LIMIT),
    offset: int = Query(default=0, ge=0, le=incident_history.MAX_HISTORY_OFFSET),
):
    try:
        return incident_history.list_incident_history(
            limit=limit,
            offset=offset,
            directory=coordinator_module.TRAJECTORIES_DIR,
        )
    except incident_history.HistoryUnavailable:
        raise HTTPException(status_code=503, detail="Incident history is unavailable") from None


@app.get("/incidents/{incident_id}", dependencies=[Security(_require_api_key)])
def incident_history_detail(incident_id: str, execution_id: str | None = None):
    try:
        return incident_history.read_incident_history(
            incident_id,
            execution_id=execution_id,
            directory=coordinator_module.TRAJECTORIES_DIR,
        )
    except incident_history.RecordNotFound:
        raise HTTPException(status_code=404, detail="Incident record not found") from None
    except incident_history.HistoryUnavailable:
        raise HTTPException(status_code=503, detail="Incident history is unavailable") from None


@app.get("/audit/log")
def audit_log_entries(limit: int = 100, offset: int = 0):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return JSONResponse({"entries": audit_log.tail(limit=limit, offset=offset)})


@app.get("/audit/verify")
def audit_verify():
    return JSONResponse(audit_log.verify_integrity())


_TOPOLOGY_SERVICES = [
    "frontend", "cartservice", "checkoutservice", "paymentservice",
    "currencyservice", "shippingservice", "emailservice",
    "recommendationservice", "productcatalogservice", "adservice", "redis-cart",
]


@app.get("/cluster/health")
def cluster_health():
    """Query pods in FastAPI's worker pool so kubectl cannot block the event loop."""
    env = os.environ.copy()
    env["USE_GKE_GCLOUD_AUTH_PLUGIN"] = "True"
    try:
        r = subprocess.run(
            ["kubectl", "get", "pods", "-n", "default", "-o", "json"],
            capture_output=True, text=True, env=env, timeout=8, check=False,
        )
        if r.returncode != 0:
            return JSONResponse({"ok": False, "services": {}})

        items = json.loads(r.stdout).get("items", [])
        services: dict = {}

        for pod in items:
            meta = pod.get("metadata", {})
            app_label = meta.get("labels", {}).get("app", "")
            if app_label not in _TOPOLOGY_SERVICES:
                continue

            status = pod.get("status", {})
            phase = status.get("phase", "Unknown")
            cs = status.get("containerStatuses", [])
            restarts = sum(c.get("restartCount", 0) for c in cs)
            ready_count = sum(1 for c in cs if c.get("ready", False))
            total = len(cs)

            if phase == "Running" and ready_count == total and total > 0:
                health = "healthy"
            elif phase in ("Pending", "Terminating"):
                health = "degraded"
            else:
                health = "down"

            if app_label in services:
                prev = services[app_label]
                # worst-case wins
                if "down" in (health, prev["status"]):
                    health = "down"
                elif "degraded" in (health, prev["status"]):
                    health = "degraded"
                services[app_label]["restarts"] = max(prev["restarts"], restarts)
                services[app_label]["status"] = health
            else:
                services[app_label] = {
                    "status": health,
                    "restarts": restarts,
                    "ready": f"{ready_count}/{total}",
                    "phase": phase,
                }

        for svc in _TOPOLOGY_SERVICES:
            if svc not in services:
                services[svc] = {"status": "unknown", "restarts": 0, "ready": "0/0", "phase": "?"}

        total_healthy = sum(1 for s in services.values() if s["status"] == "healthy")
        return JSONResponse({"ok": True, "services": services, "healthy": total_healthy, "total": len(_TOPOLOGY_SERVICES)})
    except Exception as e:  # noqa: BLE001 - malformed pod observations remain unavailable, never healthy
        return JSONResponse({"ok": False, "error": str(e), "services": {}})


@app.post("/webhook")
async def webhook_proxy(request: Request):
    """Top-level Alertmanager webhook — validates signature then forwards to coordinator.

    Alertmanager config:
      receivers:
        - name: atlasops
          webhook_configs:
            - url: 'http://<host>:7860/webhook'
              http_config:
                authorization:
                  type: Bearer
                  credentials: <ALERTMANAGER_WEBHOOK_SECRET>
    """
    body = await read_alert_webhook_body(request)
    _verify_webhook_signature(body, request.headers.get("Authorization"))
    payload = parse_alert_webhook_payload(body)
    if not payload["alerts"]:
        return JSONResponse(
            {
                "ok": True,
                "incident_id": None,
                "dispatched": False,
                "queued": False,
                "dispatch_state": "noop",
            }
        )
    candidate_batch = correlator.ingest(payload)
    submission = incident_dispatcher.submit(
        candidate_batch,
        handler=_dispatch_incident,
        anchor_builder=build_incident_anchors,
    )
    return JSONResponse(
        {
            "ok": True,
            "incident_id": submission.incident_id,
            "dispatched": submission.state == "started",
            "queued": submission.state == "queued",
            "dispatch_state": submission.state,
        }
    )


async def _dispatch_incident(
    payload: dict,
    incident_id: str,
    *,
    incident_anchors: dict | None = None,
):
    from agents.coordinator import handle_incident
    return await handle_incident(
        payload,
        incident_id=incident_id,
        incident_anchors=incident_anchors,
    )


@app.get("/slack/feed")
def slack_feed():
    """Return last 30 comms posts from the local log (powers the UI feed)."""
    log_path = Path("data/slack_posts.jsonl")
    if not log_path.exists():
        return JSONResponse({"posts": []})
    posts = deque(maxlen=30)
    with log_path.open(encoding="utf-8") as file:
        for line in file:
            try:
                posts.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return JSONResponse({"posts": list(posts)})


_register_frontend_routes(app, FRONTEND_DIST)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
