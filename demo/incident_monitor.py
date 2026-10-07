"""Bounded read-only projection of one explicitly selected local runner capture."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path

from agents.tool_policy import AGENT_EXPOSED_TOOLS

EXPERIMENT = re.compile(r"EXP-STAGE4-SF002-\d{3}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/-]{1,160}\Z")
FAILURES = {
    "bridge_transport_failure", "admission_failure", "model_load_failure",
    "load_timeout", "rpc_timeout", "worker_process_exit", "engine_poisoned",
    "journal_failure", "checkpoint_integrity_failure", "runtime_device_mismatch",
}
PHASES = (
    ("telemetry", "Telemetry preflight", ">>> Phase 0: Telemetry Readiness"),
    ("baseline", "Healthy baseline", ">>> Phase 0b: Paymentservice Baseline"),
    ("injection", "Fault injection", ">>> Phase 2:"),
    ("observation", "Fault / degradation", ">>> Phase 3:"),
    ("agents", "Agent workflow", ">>> Phase 4:"),
    ("verification", "Objective verification", "STAGE 4 GOLDEN INCIDENT RESULT:"),
    ("cleanup", "Safety cleanup", ">>> Phase 6:"),
)


def _safe_file(root: Path, relative: str, limit: int) -> bytes | None:
    path = root / relative
    current = Path(root.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Capture path redirects")
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("Capture file is not bounded regular data")
    with path.open("rb") as stream:
        if not os.path.samestat(info, os.fstat(stream.fileno())):
            raise ValueError("Capture file changed identity")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Capture file exceeds bound")
    return raw


def _object(root: Path, relative: str, limit: int = 2 * 1024**2) -> tuple[dict, str | None]:
    raw = _safe_file(root, relative, limit)
    if raw is None:
        return {}, None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("Capture must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _identifier(value):
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def _boolean(value):
    return value if type(value) is bool else None


def _target(value):
    if value is not None and not isinstance(value, str):
        raise TypeError("Invalid recorded target")
    if value in {"paymentservice", "deployment/paymentservice", "sf-002-paymentservice-cpu"}:
        return value
    return "OTHER_TARGET" if value else None


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def _process_running(launch: dict) -> bool | None:
    """Windows PID and creation-time match; never trust a launch file alone."""
    pid = launch.get("pid")
    started = _timestamp(launch.get("started_at_utc"))
    if os.name != "nt" or type(pid) is not int or pid <= 0 or started is None:
        return None
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        return False if ctypes.get_last_error() == 87 else None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(item) for item in times]):
            return None
        created = ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) / 10**7 - 11644473600
        expected = datetime.fromisoformat(started).timestamp()
        if abs(created - expected) > 10:
            return False
        wait_status = kernel.WaitForSingleObject(handle, 0)
        return True if wait_status == 258 else False if wait_status == 0 else None
    finally:
        kernel.CloseHandle(handle)


def read_incident(
    root: Path, experiment: str, capture: str = "golden", *, execution_root: Path | None = None,
) -> dict:
    if capture not in {"golden", "golden-v3", "golden-v3b"} and not (
        capture == "operator" and execution_root is not None
    ):
        raise ValueError("Unsupported runner capture")
    if not root.is_absolute() or ".." in root.parts or not EXPERIMENT.fullmatch(experiment):
        raise ValueError("Explicit absolute capture root and SF002 identity required")
    if not root.is_dir():
        raise ValueError("Capture root is unavailable")
    result = {
        "enabled": True, "available": True, "experiment_id": experiment,
        "classification": "RECORDED GOVERNED RUN / NOT A SIMULATION",
        "observed_at": datetime.now(UTC).isoformat(),
        "status": "NOT_STARTED", "process_running": None, "reserved": False,
        "attempt_state": None, "failure": None, "gate_g4_pass": None,
        "env_resolved": None, "cleanup_verified_zero": None,
        "approval": None, "severity": None, "services": [], "actions": [],
        "proposal": None, "phases": [], "sources": [],
        "policy_block": None, "comms_status": None,
    }
    launch, _ = _object(root, f"{capture}/launch.json", 16384)
    exited, exit_digest = _object(root, f"{capture}/exit.json", 16384)
    if launch and launch.get("mode") != "golden":
        raise ValueError("Not a golden runner capture")
    result["started_at"] = _timestamp(launch.get("started_at_utc"))
    result["source_sha"] = (
        launch.get("source_sha") if re.fullmatch(r"[0-9a-f]{40}", str(launch.get("source_sha", ""))) else None
    )
    result["process_running"] = False if exited else _process_running(launch)
    if launch:
        result["status"] = "RUNNING" if result["process_running"] else "ACTIVITY_UNVERIFIED"
    if exited:
        code = exited.get("exit_code")
        if type(code) is not int:
            raise ValueError("Invalid process exit")
        result["status"] = "PROCESS_EXITED" if code == 0 else "PROCESS_FAILED"
        result["exit_code"] = code
        result["finished_at"] = _timestamp(exited.get("finished_at_utc"))
        result["sources"].append({"name": "Process exit", "sha256": exit_digest})

    execution = "execution-v3b" if capture == "golden-v3b" else "execution"
    evidence_root = execution_root if execution_root is not None else root / execution
    if not evidence_root.is_absolute() or ".." in evidence_root.parts:
        raise ValueError("Explicit absolute execution root required")
    base = "artifacts/evidence/stage4/"
    primary, primary_digest = _object(evidence_root, base + experiment + ".json")
    interrupted, _ = _object(evidence_root, base + experiment + ".interruption.json")
    marker, _ = _object(evidence_root, base + ".attempts/" + experiment + ".attempt.json")
    cleanup, cleanup_digest = _object(evidence_root, base + experiment + ".cleanup.json")
    for record in (primary, interrupted, marker, cleanup):
        if record and record.get("experiment_id") != experiment:
            raise ValueError("Capture experiment identity mismatch")
        if record.get("scenario_id") not in (None, "single_fault/sf-002"):
            raise ValueError("Capture scenario identity mismatch")
    result["reserved"] = bool(marker)
    state = marker.get("state")
    if marker and state not in {"RESERVED", "CONSUMED", "COMPLETED"}:
        raise ValueError("Invalid attempt accounting state")
    result["attempt_state"] = state
    phases = primary.get("phases") or {}
    if not isinstance(phases, dict):
        raise TypeError("Invalid recorded phases")
    execution = phases.get("coordinator_execution") or {}
    verification = phases.get("verification") or {}
    if not isinstance(execution, dict) or not isinstance(verification, dict):
        raise TypeError("Invalid recorded workflow")
    triage = execution.get("triage") or {}
    approval = execution.get("approval") or {}
    comms = execution.get("comms") or {}
    if isinstance(comms, dict):
        status = comms.get("status")
        if status in {
            "target_mismatch", "approved", "rejected", "timeout", "missing",
            "blocked", "policy_invalid_action", "policy_approval_required",
            "policy_missing_evidence", "policy_policy_block", "policy_tool_unavailable",
            "policy_already_resolved",
        }:
            result["comms_status"] = status
    if isinstance(triage, dict):
        severity = triage.get("severity")
        result["severity"] = severity if severity in {"P0", "P1", "P2", "P3"} else None
        services = triage.get("affected_services") or []
        if not isinstance(services, list):
            raise TypeError("Invalid recorded service list")
        result["services"] = [
            _target(item) for item in services[:20]
            if isinstance(item, str)
        ]
    if isinstance(approval, dict):
        decision = approval.get("decision") or approval.get("status")
        result["approval"] = decision if decision in {"pending", "approved", "rejected", "timeout", "missing"} else None
    actions = execution.get("executed_tool_actions") or []
    if not isinstance(actions, list):
        raise TypeError("Invalid recorded action list")
    for action in actions[:30]:
        if not isinstance(action, dict):
            continue
        args, output = action.get("args") or {}, action.get("output") or {}
        if not isinstance(args, dict) or not isinstance(output, dict):
            continue
        result["actions"].append({
            "tool": action.get("tool") if action.get("tool") in AGENT_EXPOSED_TOOLS else None,
            "target": _target(args.get("name") or args.get("resource") or args.get("app")),
            "namespace": args.get("namespace") if args.get("namespace") in {"default", "chaos-mesh"} else None,
            "success": _boolean(output.get("success")),
        })
    proposal = execution.get("model_proposed_action") or {}
    terminal = proposal.get("terminal_block") if isinstance(proposal, dict) else None
    if isinstance(terminal, dict):
        category = terminal.get("category")
        if category in {
            "invalid_action", "approval_required", "missing_evidence",
            "policy_block", "tool_unavailable", "already_resolved",
        }:
            result["policy_block"] = category
    proposed = proposal.get("proposed_actions") if isinstance(proposal, dict) else None
    if isinstance(proposed, list) and proposed and isinstance(proposed[0], dict):
        action = proposed[0]
        args = action.get("arguments") or action.get("args") or {}
        if isinstance(args, dict):
            result["proposal"] = {
                "tool": action.get("tool") if action.get("tool") in AGENT_EXPOSED_TOOLS else None,
                "target": _target(args.get("name") or args.get("resource")),
                "namespace": args.get("namespace") if args.get("namespace") in {"default", "chaos-mesh"} else None,
            }
    result["gate_g4_pass"] = _boolean(primary.get("gate_g4_pass"))
    result["env_resolved"] = _boolean(verification.get("env_resolved"))
    result["cleanup_verified_zero"] = _boolean(cleanup.get("verified_zero_chaos"))
    if primary:
        result["status"] = (
            "RECORDED_PASS" if result["gate_g4_pass"] is True
            else "RECORDED_NEGATIVE" if result["gate_g4_pass"] is False
            else "UNSCORED / INCONCLUSIVE"
        )
        result["sources"].append({"name": "Primary incident record", "sha256": primary_digest})
    elif interrupted:
        result["status"] = "INTERRUPTED / INCONCLUSIVE"
    if cleanup:
        result["sources"].append({"name": "Cleanup sidecar", "sha256": cleanup_digest})
    qualification, _ = _object(root, "startup-evidence/g4-inference-v1.qualification.json") if capture == "golden" else ({}, None)
    q = qualification.get("qualification") or {}
    if isinstance(q, dict) and q.get("status") == "NOT_QUALIFIED":
        failure = q.get("failure_category")
        result["failure"] = failure if failure in FAILURES else "qualification_failure"
        if exited and not marker and not primary:
            result["status"] = "STARTUP_FAILED / NOT_RESERVED"
    log = _safe_file(root, f"{capture}/stdout.log", 1024**2)
    text = log.decode("utf-8", errors="replace") if log else ""
    result["phases"] = [
        {"id": key, "label": label, "observed": token in text}
        for key, label, token in PHASES
    ]
    return result
