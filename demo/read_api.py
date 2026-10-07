"""Local presentation API. No operational runtime or tool implementation imports."""

from __future__ import annotations

import ast
import ipaddress
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from agents.tool_policy import ADMIN_OR_UNEXPOSED_TOOLS, ROLE_ALLOWED_TOOLS
from config.scenario_catalog import SCENARIO_CATALOG
from demo.incident_monitor import read_incident
from demo.live_status import observe_services
from demo.rehearsal import rehearsal_catalog
from ui_read_model import ROOT, attempt_detail, catalog


def is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _registered_tool_names() -> set[str]:
    # Inspect the registry syntax without importing executable tool wrappers.
    tree = ast.parse((ROOT / "agents/tools/__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TOOL_REGISTRY"
            for target in node.targets
        ) and isinstance(node.value, ast.Dict):
            return {
                key.value for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    raise ValueError("Tool registry is unavailable")


def product() -> dict[str, Any]:
    roles = {
        "triage": (
            "Triage", "Alert classification", "Establish severity and affected services.",
            "Incident alert", "Structured severity and service scope",
            "Read-only observations within the triage role ACL.",
        ),
        "diagnosis": (
            "Diagnosis", "Grounded investigation", "Connect telemetry to a proposed root cause.",
            "Scoped alert and observations", "Evidence-grounded diagnostic proposal",
            "Observation tools only; diagnosis does not authorize a change.",
        ),
        "remediation": (
            "Remediation", "Governed action", "Propose and submit policy-allowed recovery actions.",
            "Diagnosis and approval decision", "Audited action results for verification",
            "Role ACL and fail-closed P1 approval. No actions run in this demo.",
        ),
        "comms": (
            "Comms", "Incident communication", "Record updates, outcomes, and postmortem drafts.",
            "Audited actions and verifier outcome", "Incident update and postmortem",
            "Communication scope only; model claims cannot establish resolution.",
        ),
    }
    tools = _registered_tool_names()
    agents = []
    for role, values in roles.items():
        name, responsibility, purpose, input_text, output, boundary = values
        allowed = ROLE_ALLOWED_TOOLS[role]
        agents.append({
            "id": role, "name": name, "role": responsibility, "purpose": purpose,
            "input": input_text, "output": output, "governance_boundary": boundary,
            "tool_categories": sorted({tool.split("_")[0] for tool in allowed}),
            "allowed_tools": sorted(allowed), "tool_count": len(allowed),
        })
    return {
        "name": "AtlasOps",
        "subtitle": "Governed Multi-Agent SRE Intelligence",
        "description": "Specialized agents reason about incidents; governance constrains actions "
                       "and objective verification determines the outcome.",
        "agent_count": len(agents),
        "tool_count": len(tools),
        "agent_exposed_tool_count": len(set().union(*ROLE_ALLOWED_TOOLS.values())),
        "unexposed_tool_count": len(ADMIN_OR_UNEXPOSED_TOOLS),
        "scenario_count": len(SCENARIO_CATALOG),
        "certification": "NOT_CERTIFIED",
        "presentation_status": "READY_FOR_REVIEW",
        "model_method": "QLoRA SFT",
        "agents": agents,
        "controls": [
            {"id": "acl", "name": "Role ACL",
             "purpose": "Wrapper registration is not permission. Each role has an explicit tool scope."},
            {"id": "approval", "name": "P1 Approval",
             "purpose": "Only explicit approval permits P1 remediation. Timeout and rejection block."},
            {"id": "verifier", "name": "Objective Verifier",
             "purpose": "Environment checks determine resolution, independently of agent claims."},
            {"id": "audit", "name": "Audit / Evidence",
             "purpose": "Preserve outcomes, failures, and provenance. Negative evidence remains visible."},
        ],
        "boundaries": [
            {"id": key, "label": label, "state": "ENFORCED"}
            for key, label in (
                ("localhost", "LOCALHOST ONLY"), ("readonly", "READ ONLY"),
                ("kubectl", "NO KUBECTL"), ("inference", "NO MODEL INFERENCE"),
                ("faults", "NO FAULT INJECTION"), ("approval", "NO APPROVAL MUTATION"),
                ("remediation", "NO REMEDIATION"), ("cleanup", "NO CLEANUP"),
                ("share", "NO PUBLIC SHARE"),
            )
        ],
    }


def evidence_browser(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Classify allowlisted pointers; never open historical metric archives."""
    kinds = {
        "SFT v17 training and preservation record": "provenance",
        "SFT v17 independent reload": "provenance",
        "Matched Base-vs-SFT Validation summary": "empirical",
        "G4 final chronology reference": "disposition",
        "Controlled G9 final negative report": "empirical",
        "Final aligned G9 diagnostic": "empirical",
        "Final aligned diagnostic local verification": "provenance",
        "Final aligned diagnostic independent review": "provenance",
        "G4 attempt 015 integrity index": "disposition",
    }
    results = snapshot["current_results"]
    negative_titles = set()
    comparison = results["base_vs_sft"]
    if comparison["paired_delta"] is not None and comparison["paired_delta"] < 0:
        negative_titles.add("Matched Base-vs-SFT Validation summary")
    if results["g9_pilot"]["available"] and results["g9_pilot"]["acceptable_checkpoint"] is False:
        negative_titles.add("Controlled G9 final negative report")
    aligned = results["g9_aligned_diagnostic"]
    if aligned["available"] and aligned["admissible_count"] == 0:
        negative_titles.add("Final aligned G9 diagnostic")
    rows = [
        {
            **ref, "id": ref["path"], "scope": "current",
            "kind": kinds[ref["title"]], "negative": ref["title"] in negative_titles,
            "availability": "present" if ref["available"] else "missing",
            "details": "Canonical repository reference; not a live health observation.",
        }
        for ref in snapshot["evidence"]
    ]
    for reference in snapshot["historical_archive"]:
        locations = reference["path"].split(" and ")
        present = all((ROOT / location).exists() for location in locations)
        rows.append({
            **reference, "id": reference["path"], "scope": "historical",
            "kind": "non-empirical", "negative": False,
            "availability": "present" if present else "missing",
            "available": present, "sha256": None, "area": "Archive",
            "details": reference["details"] + " Pointer only; contents are not opened or hashed.",
        })
    for attempt in results["g4_attempts"]:
        rows.append({
            "id": f"external-g4-{attempt['attempt']}",
            "title": f"G4 attempt {attempt['attempt']} raw operational record",
            "path": None, "classification": attempt["state"],
            "scope": "current", "kind": "disposition",
            "negative": attempt["state"] == "COMPLETED NEGATIVE",
            "availability": (
                "external"
                if attempt["attempt"] in {"015", "017"} and attempt["state"] != "UNAVAILABLE"
                else "missing"
            ),
            "available": False, "sha256": None, "area": "G4",
            "details": attempt["source_note"],
        })
    return rows


def create_app(
    frontend_dist: Path | None = None,
    *,
    incident_capture_root: Path | None = None,
    incident_id: str = "EXP-STAGE4-SF002-018",
    incident_capture: str = "golden",
    operator=None,
) -> FastAPI:
    """Build a separate application, never mount the operational app."""
    application = FastAPI(
        title="AtlasOps read-only research demo",
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    dist = frontend_dist if frontend_dist is not None else ROOT / "frontend/dist"

    @application.middleware("http")
    async def presentation_boundary(request: Request, call_next):
        peer = request.client.host if request.client else ""
        if not is_loopback_host(peer) or not is_loopback_host(request.url.hostname or ""):
            return JSONResponse({"detail": "Loopback host required"}, status_code=403)
        operator_command = (
            operator is not None and request.method == "POST"
            and request.url.path in {"/api/operator/start", "/api/operator/decision"}
        )
        if request.url.path.startswith("/api/operator") and operator is not None:
            origin = request.headers.get("origin")
            if (
                (origin is not None and origin != str(request.base_url).rstrip("/"))
                or request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}
                or (operator_command and origin is None)
            ):
                return JSONResponse({"detail": "Same-origin operator access required"}, status_code=403)
        if request.method not in {"GET", "HEAD"} and not operator_command:
            return JSONResponse(
                {"detail": "Read-only presentation; no execution endpoints"},
                status_code=405, headers={"Allow": "GET, HEAD"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @application.get("/health")
    def health():
        return {
            "status": "ok", "mode": "operator" if operator is not None else "read-only",
            "frontend_built": (dist / "index.html").is_file(),
        }

    @application.get("/api/catalog")
    def read_catalog():
        snapshot = catalog()
        snapshot["gates"] = [
            {**gate, "name": gate["name"].replace("GAI + RL", "agent system")}
            for gate in snapshot["gates"]
        ]
        metadata = product()
        if operator is not None:
            metadata["boundaries"] = [
                item for item in metadata["boundaries"]
                if item["id"] in {"localhost", "share"}
            ]
            metadata["operator_enabled"] = True
            for agent in metadata["agents"]:
                if agent["id"] == "remediation":
                    agent["governance_boundary"] = (
                        "Role ACL and fail-closed exact-action P1 approval through the owned runner."
                    )
        return {**snapshot, "product": metadata, "evidence_browser": evidence_browser(snapshot)}

    @application.get("/api/attempts/{name}")
    def read_attempt(name: str):
        try:
            return attempt_detail(name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Attempt not found") from exc

    @application.get("/api/rehearsal")
    def read_rehearsal():
        try:
            return rehearsal_catalog()
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="Rehearsal fixtures unavailable") from exc

    @application.get("/api/live-status")
    def read_live_status():
        if os.environ.get("ATLASOPS_DEMO_LIVE_OBSERVATIONS") != "1":
            return {"enabled": False, "services": [], "observed_at": None}
        return observe_services()

    @application.get("/api/incident-monitor")
    def incident_monitor():
        if incident_capture_root is None:
            return {"enabled": False}
        try:
            return read_incident(incident_capture_root, incident_id, incident_capture)
        except (OSError, ValueError, TypeError, KeyError):
            return JSONResponse(
                {"enabled": True, "available": False, "status": "CAPTURE_UNAVAILABLE"},
                status_code=503,
            )

    @application.get("/api/operator")
    def operator_status():
        if operator is None:
            return {"enabled": False}
        try:
            return operator.snapshot()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            return JSONResponse({"detail": "Operator status unavailable"}, status_code=503)

    if operator is not None:
        from pydantic import BaseModel, ConfigDict, Field

        class Decision(BaseModel):
            model_config = ConfigDict(extra="forbid")
            token: str = Field(min_length=1, max_length=120)
            action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
            decision: str = Field(pattern=r"^(approved|rejected)$")

        async def command(request: Request):
            try:
                csrf = request.headers.get("X-AtlasOps-Operator", "")
                operator.authenticate(csrf)
                if request.url.path.endswith("/start"):
                    if await request.body() not in {b"", b"{}"}:
                        raise ValueError("Launch accepts no browser-supplied configuration")
                    return await run_in_threadpool(operator.start, csrf)
                if len(await request.body()) > 2048:
                    raise ValueError("Decision exceeds bound")
                decision = Decision.model_validate(await request.json())
                return await run_in_threadpool(
                    operator.decide, csrf, decision.token, decision.action_digest, decision.decision,
                )
            except PermissionError:
                return JSONResponse({"detail": "Invalid operator session"}, status_code=403)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                return JSONResponse(
                    {"detail": "Governed operation blocked; refresh status before proceeding"},
                    status_code=409,
                )
            except httpx.HTTPError:
                # Never return credential-bearing transport errors to the browser.
                return JSONResponse({"detail": "Operator transport unavailable"}, status_code=503)

        application.add_api_route("/api/operator/start", command, methods=["POST"])
        application.add_api_route("/api/operator/decision", command, methods=["POST"])

    if (dist / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @application.get("/")
    def index():
        if not (dist / "index.html").is_file():
            raise HTTPException(
                status_code=503,
                detail="Frontend not built. Run npm ci and npm run build in frontend/.",
            )
        return FileResponse(dist / "index.html")

    return application
