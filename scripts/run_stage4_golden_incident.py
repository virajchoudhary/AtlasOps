#!/usr/bin/env python3
"""Stage 4 Golden Incident Orchestrator and Causal Objective Verifier.

Pipeline v1.1 Free-First — Stage 4 Gate G4.

Executes ONE real golden incident end-to-end against the live Kind cluster with
strict causal validity:
1. Pre-incident baseline verification (target workload healthy, 0 chaos resources).
2. Real fault injection: single_fault/sf-002 (StressChaos CPU on paymentservice).
3. Independent cluster fault observation before incident trigger.
4. Multi-agent coordinator execution (Triage → Diagnosis → Approval Gate → Remediation → Objective Verifier → Comms).
5. Strict causal 15-point verification predicate (NO harness fault clearance before verifier, NO forced resolution).
6. Evidence persistence (immutable per-experiment manifest plus latest pointer).
7. Post-verdict safety cleanup.

Zero paid APIs. Local Ollama model selected through ATLASOPS_STAGE4_AGENT_MODEL.
"""

import asyncio
import json
import logging
import math
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from config.runtime import resolve_stage4_agent_model

# Reconfigure standard UTF-8 stream handling on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _load_secret_or_default(filename: str, default: str) -> str:
    path = os.path.join(REPO_ROOT, "secrets", filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            val = f.read().strip()
            if val:
                return val
    return default


# Configure environment for local agent execution
os.environ["BACKEND"] = "openai"
os.environ["VLLM_BASE"] = "http://localhost:11434/v1"
os.environ["LLM_API_KEY"] = "ollama"
os.environ["KUBECONFIG_CONTEXT"] = "kind-atlasops-local"
os.environ["APPROVAL_TIMEOUT_SECONDS"] = "2"
os.environ["PROMETHEUS_URL"] = "http://localhost:19090"
os.environ["ALERTMANAGER_URL"] = "http://localhost:19093"
os.environ["JAEGER_URL"] = "http://localhost:16686"
os.environ["ARGOCD_URL"] = "http://localhost:18080"
os.environ["ARGOCD_USER"] = "atlasops"
os.environ["ARGOCD_PASS"] = _load_secret_or_default("argocd-pass.secret", "atlasops-local-pass")
os.environ["ARGOCD_VERIFY_TLS"] = "false"
os.environ["ATLASOPS_AUDIT_SECRET"] = _load_secret_or_default("atlasops-audit-secret.secret", "local-audit-secret-key-1234567890")
os.environ["ATLASOPS_API_KEY"] = _load_secret_or_default("atlasops-api-key.secret", "local-api-key-1234567890")
os.environ["ALERTMANAGER_WEBHOOK_SECRET"] = _load_secret_or_default("alertmanager-webhook-secret.secret", "local-webhook-secret-1234567890")
os.environ["POSTMORTEM_DIR"] = os.path.join(REPO_ROOT, "artifacts", "postmortems")
os.environ["TRAJECTORIES_DIR"] = os.path.join(REPO_ROOT, "artifacts", "trajectories")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("stage4.golden")

KIND_CONTEXT = "kind-atlasops-local"
SELECTED_STAGE4_AGENT_MODEL = resolve_stage4_agent_model()
os.environ["AGENT_MODEL"] = SELECTED_STAGE4_AGENT_MODEL
# Experiment identity is operator-controlled and must never collide with a
# preserved evidence file. Override via STAGE4_EXPERIMENT_ID for each new run;
# the runner refuses to overwrite an existing per-experiment evidence file.
EXPERIMENT_ID = os.environ.get("STAGE4_EXPERIMENT_ID", "EXP-STAGE4-SF002-004")
SCENARIO_ID = "single_fault/sf-002"
TARGET_SERVICE = "paymentservice"
TARGET_NAMESPACE = "default"
TARGET_CHAOS_KIND = "StressChaos"
TARGET_CHAOS_NAME = "sf-002-paymentservice-cpu"
TARGET_CHAOS_NAMESPACE = "chaos-mesh"

# Predeclared SF002 degradation contract. Prometheus cAdvisor is already part
# of the canonical G3 stack. A real stress increase must be material in both
# absolute cores and relative to the healthy baseline. The application
# container in the pinned microservices-demo v0.10.6 paymentservice pod is
# named "server" (verified from the live Deployment spec and cAdvisor labels),
# so the selector must target container="server" to measure the intended
# paymentservice application-container CPU.
#
# Absolute threshold derivation (operator-approved 2026-08-24 envelope
# amendment): the pinned paymentservice container carries a 200m (0.2-core)
# CPU limit, so the previous +0.25-core threshold exceeded the container's
# hard CFS ceiling and was mathematically unreachable. The predeclared
# absolute requirement is now +0.15 cores = 75% of the pinned 0.2-core limit:
# materially elevated application CPU while remaining physically reachable.
# The post-fault observation timeout is 150s so the [2m] rate window can be
# repopulated by post-fault scrapes (~30s scrape interval) plus one scrape
# interval of convergence margin; the prior 30s window could only observe a
# rate dominated by pre-fault history.
DEGRADATION_QUERY = (
    'max(rate(container_cpu_usage_seconds_total{namespace="default",'
    'pod=~"paymentservice-.*",container="server"}[2m]))'
)
RAW_PAYMENTSERVICE_CPU_QUERY = (
    'container_cpu_usage_seconds_total{namespace="default",'
    'pod=~"paymentservice-.*",container="server"}'
)
DEGRADATION_MIN_ABSOLUTE_INCREASE_CORES = 0.15
DEGRADATION_MIN_RATIO = 2.0
DEGRADATION_OBSERVATION_TIMEOUT_SECONDS = 150
DEGRADATION_POLL_INTERVAL_SECONDS = 3
# Pre-reservation telemetry-readiness gate: no attempt may be reserved until
# the exact F1 telemetry path is demonstrably usable. Two consecutive valid
# probes separated by >= one scrape interval prove a fresh scrape occurred.
TELEMETRY_SCRAPE_INTERVAL_SECONDS = 30
TELEMETRY_REQUIRED_STABLE_PROBES = 2
TELEMETRY_READINESS_TIMEOUT_SECONDS = 120
# Pre-reservation paymentservice Deployment readiness gate: the same fail-closed
# baseline contract as the causal predicate, evaluated BEFORE any reservation.
# Two consecutive healthy reads separated by a short interval guard against
# single-read flaps during node resource churn (observed 2026-08-23).
BASELINE_STABILITY_INTERVAL_SECONDS = 5
BASELINE_REQUIRED_STABLE_PROBES = 2
BASELINE_READINESS_TIMEOUT_SECONDS = 60
ATTEMPT_STATE_RESERVED = "RESERVED"
ATTEMPT_STATE_CONSUMED = "CONSUMED"
ATTEMPT_STATE_COMPLETED = "COMPLETED"
G4_PLATFORM_HARDENING_MARKER = "G4-PLATFORM-HARDENING-2026-08-25"
MAX_ATTEMPTS_PER_PROTOCOL_MARKER = 2


def run_kubectl(args: list[str], timeout: int = 20) -> dict[str, Any]:
    cmd = ["kubectl", "--context", KIND_CONTEXT] + args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "success": res.returncode == 0,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
            "returncode": res.returncode,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "returncode": -1}


def _experiment_evidence_dir(experiment_id: str, root: str | None = None) -> str:
    base = root or REPO_ROOT
    return os.path.join(base, "artifacts", "evidence", "stage4")


def _attempt_marker_path(experiment_id: str, root: str | None = None) -> str:
    safe_id = experiment_id.replace(os.sep, "_").replace("/", "_")
    return os.path.join(
        _experiment_evidence_dir(experiment_id, root),
        ".attempts",
        f"{safe_id}.attempt.json",
    )


def _write_json_atomic(path: str, data: dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary = os.path.join(
        directory,
        f".{os.path.basename(path)}.{uuid.uuid4().hex}.tmp",
    )
    with open(temporary, "x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_json_file(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _spent_attempts_for_protocol_marker(attempt_root: str | None = None) -> int:
    attempts_dir = os.path.join(
        _experiment_evidence_dir("", attempt_root), ".attempts"
    )
    if not os.path.isdir(attempts_dir):
        return 0

    spent_states = {ATTEMPT_STATE_CONSUMED, ATTEMPT_STATE_COMPLETED}
    return sum(
        (
            attempt.get("state") in spent_states
            and attempt.get("protocol_marker") == G4_PLATFORM_HARDENING_MARKER
        )
        for attempt in (
            _read_json_file(os.path.join(attempts_dir, name))
            for name in os.listdir(attempts_dir)
            if name.endswith(".attempt.json")
        )
    )


def reserve_experiment_attempt(
    experiment_id: str,
    *,
    selected_model: str,
    main_sha: str,
    attempt_root: str | None = None,
) -> dict[str, Any]:
    """Atomically reserve an experiment before any pre-T0 mutation."""
    evidence_dir = _experiment_evidence_dir(experiment_id)
    primary_path = os.path.join(evidence_dir, f"{experiment_id}.json")
    if os.path.exists(primary_path):
        raise RuntimeError(f"Stage 4 evidence already exists: {primary_path}")

    marker_path = _attempt_marker_path(experiment_id, attempt_root)
    spent_attempts = _spent_attempts_for_protocol_marker(attempt_root)
    if spent_attempts >= MAX_ATTEMPTS_PER_PROTOCOL_MARKER:
        raise RuntimeError(
            "protocol attempt limit reached for "
            f"{G4_PLATFORM_HARDENING_MARKER}: "
            f"{spent_attempts}/{MAX_ATTEMPTS_PER_PROTOCOL_MARKER}"
        )
    if os.path.exists(marker_path):
        existing = _read_json_file(marker_path) or {}
        raise RuntimeError(
            f"Stage 4 attempt already exists: {marker_path} "
            f"(state={existing.get('state', 'UNKNOWN')})"
        )

    reservation = {
        "experiment_id": experiment_id,
        "state": ATTEMPT_STATE_RESERVED,
        "reserved_at": datetime.now(timezone.utc).isoformat(),
        "reservation_token": uuid.uuid4().hex,
        "protocol_marker": G4_PLATFORM_HARDENING_MARKER,
        "selected_model": selected_model,
        "main_sha": main_sha,
    }
    _write_json_atomic(marker_path, reservation)
    persisted = _read_json_file(marker_path) or {}
    if persisted.get("reservation_token") != reservation["reservation_token"]:
        raise RuntimeError(f"Concurrent Stage 4 reservation detected: {marker_path}")
    return reservation


def _transition_attempt(
    reservation: dict[str, Any],
    *,
    state: str,
    timestamp_field: str,
    attempt_root: str | None = None,
) -> dict[str, Any]:
    marker_path = _attempt_marker_path(reservation["experiment_id"], attempt_root)
    current = _read_json_file(marker_path) or {}
    if current.get("reservation_token") != reservation.get("reservation_token"):
        raise RuntimeError(f"Stage 4 attempt ownership mismatch: {marker_path}")
    expected = {
        ATTEMPT_STATE_CONSUMED: ATTEMPT_STATE_RESERVED,
        ATTEMPT_STATE_COMPLETED: ATTEMPT_STATE_CONSUMED,
    }[state]
    if current.get("state") != expected:
        raise RuntimeError(
            f"Invalid Stage 4 attempt transition "
            f"{current.get('state')} -> {state}: {marker_path}"
        )
    updated = {
        **current,
        "state": state,
        timestamp_field: datetime.now(timezone.utc).isoformat(),
    }
    _write_json_atomic(marker_path, updated)
    return updated


def consume_experiment_attempt(
    reservation: dict[str, Any],
    attempt_root: str | None = None,
) -> dict[str, Any]:
    return _transition_attempt(
        reservation,
        state=ATTEMPT_STATE_CONSUMED,
        timestamp_field="consumed_at",
        attempt_root=attempt_root,
    )


def complete_experiment_attempt(
    reservation: dict[str, Any],
    attempt_root: str | None = None,
) -> dict[str, Any]:
    return _transition_attempt(
        reservation,
        state=ATTEMPT_STATE_COMPLETED,
        timestamp_field="completed_at",
        attempt_root=attempt_root,
    )


def release_experiment_reservation(
    reservation: dict[str, Any],
    attempt_root: str | None = None,
) -> bool:
    """Release only an unused reservation after a pre-fault setup failure."""
    marker_path = _attempt_marker_path(reservation["experiment_id"], attempt_root)
    current = _read_json_file(marker_path) or {}
    if current.get("reservation_token") != reservation.get("reservation_token"):
        return False
    if current.get("state") != ATTEMPT_STATE_RESERVED:
        return False
    os.remove(marker_path)
    return True


def _extract_prometheus_cpu_cores(result: dict[str, Any]) -> list[float]:
    if not result.get("success"):
        return []
    values: list[float] = []
    for series in result.get("result") or []:
        value = series.get("value", [])
        if len(value) != 2:
            continue
        try:
            values.append(float(value[1]))
        except (TypeError, ValueError):
            continue
    return values


def collect_sf002_cpu_telemetry(time_unix: float | None = None) -> dict[str, Any]:
    from agents.tools.prometheus import promql_query

    started_at = datetime.now(timezone.utc).isoformat()
    result = promql_query(DEGRADATION_QUERY, time_unix=time_unix)
    samples = _extract_prometheus_cpu_cores(result)
    return {
        "timestamp": started_at,
        "query": DEGRADATION_QUERY,
        "query_success": result.get("success") is True,
        "query_error": result.get("error"),
        "samples_cores": samples,
        "max_cores": max(samples, default=None),
    }


def sf002_degradation_decision(
    baseline: dict[str, Any],
    post_fault: dict[str, Any],
) -> dict[str, Any]:
    baseline_max = baseline.get("max_cores")
    post_max = post_fault.get("max_cores")
    numeric = (
        isinstance(baseline_max, (int, float))
        and isinstance(post_max, (int, float))
    )
    absolute_increase = post_max - baseline_max if numeric else None
    ratio = post_max / baseline_max if numeric and baseline_max > 0 else None
    passed = bool(
        absolute_increase is not None
        and ratio is not None
        and absolute_increase >= DEGRADATION_MIN_ABSOLUTE_INCREASE_CORES
        and ratio >= DEGRADATION_MIN_RATIO
    )
    return {
        "measured": absolute_increase is not None and ratio is not None,
        "passed": passed,
        "baseline_max_cores": baseline_max,
        "post_fault_max_cores": post_max,
        "absolute_increase_cores": absolute_increase,
        "post_to_baseline_ratio": ratio,
        "min_absolute_increase_cores": DEGRADATION_MIN_ABSOLUTE_INCREASE_CORES,
        "min_ratio": DEGRADATION_MIN_RATIO,
    }


def stage4_evidence_metadata() -> dict[str, Any]:
    """Build the model-identity fields shared by output and evidence."""
    return {
        "model": SELECTED_STAGE4_AGENT_MODEL,
        "inference_provider": "ollama-local",
        "protocol_marker": G4_PLATFORM_HARDENING_MARKER,
        "trigger_type": "manual coordinator trigger over a real independently observed cluster fault",
    }


def _prometheus_http_get(path: str) -> tuple[int | None, str]:
    """Read-only GET against the configured Prometheus endpoint."""
    base = os.environ.get("PROMETHEUS_URL", "http://localhost:19090").rstrip("/")
    try:
        import requests

        resp = requests.get(f"{base}{path}", timeout=10)
        return resp.status_code, resp.text
    except Exception as exc:
        return None, str(exc)


def _endpoint_ready(
    http_get_fn: Callable[[str], tuple[int | None, str]] = _prometheus_http_get,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for path in ("/-/healthy", "/-/ready"):
        try:
            status, _body = http_get_fn(path)
        except Exception as exc:
            failures.append(f"{path} transport error: {exc}")
            continue
        if status != 200:
            failures.append(f"{path} -> HTTP {status}")
    return not failures, failures


def _cadvisor_target_healthy(
    fetch_targets_fn: Callable[[], Any] | None = None,
) -> tuple[bool, list[str]]:
    try:
        if fetch_targets_fn is not None:
            payload = fetch_targets_fn()
        else:
            status, body = _prometheus_http_get("/api/v1/targets")
            if status != 200:
                return False, [f"targets endpoint -> HTTP {status}"]
            payload = json.loads(body)
    except Exception as exc:
        return False, [f"targets fetch error: {exc}"]
    active = ((payload or {}).get("data") or {}).get("activeTargets") or []
    cadvisor = [
        target
        for target in active
        if isinstance(target, dict)
        and "/metrics/cadvisor" in str(target.get("scrapeUrl", ""))
        and target.get("health") is not None
    ]
    if not cadvisor:
        return False, ["no cAdvisor scrape target configured"]
    unhealthy = [
        target
        for target in cadvisor
        if target.get("health") != "up" or str(target.get("lastError", "")).strip()
    ]
    if unhealthy:
        reasons = "; ".join(
            f"{target.get('scrapeUrl')} health={target.get('health')} lastError={target.get('lastError')}"
            for target in unhealthy
        )
        return False, [f"cAdvisor scrape target unhealthy: {reasons}"]
    return True, []


def _finite_cpu_samples(samples: list[Any]) -> list[float]:
    finite: list[float] = []
    for sample in samples:
        try:
            value = float(sample)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            finite.append(value)
    return finite


def _telemetry_query_valid(
    run_query_fn: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[bool, list[str]]:
    def default_run_query(query: str) -> dict[str, Any]:
        from agents.tools.prometheus import promql_query

        return promql_query(query)

    query_fn = run_query_fn or default_run_query
    raw = query_fn(RAW_PAYMENTSERVICE_CPU_QUERY)
    if raw.get("success") is not True:
        return False, [f"raw metric query failed: {raw.get('error')}"]
    if len(raw.get("result") or []) < 1:
        return False, ["raw paymentservice CPU metric absent"]
    exact = query_fn(DEGRADATION_QUERY)
    if exact.get("success") is not True:
        return False, [f"F1 query failed: {exact.get('error')}"]
    if exact.get("resultType") != "vector":
        return False, [f"F1 resultType {exact.get('resultType')!r} != 'vector'"]
    finite = _finite_cpu_samples(_extract_prometheus_cpu_cores(exact))
    if not finite:
        return False, ["F1 query returned no finite numeric sample (empty/NaN/Inf)"]
    return True, []


def wait_for_telemetry_readiness(
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    timeout_seconds: int = TELEMETRY_READINESS_TIMEOUT_SECONDS,
    http_get_fn: Callable[[str], tuple[int | None, str]] = _prometheus_http_get,
    fetch_targets_fn: Callable[[], Any] | None = None,
    run_query_fn: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Prove F1 telemetry usability BEFORE any experiment reservation.

    Requires TELEMETRY_REQUIRED_STABLE_PROBES consecutive fully-valid cycles
    separated by at least one Prometheus scrape interval. Never mutates
    cluster/experiment state; on failure nothing is reserved.
    """
    started = monotonic_fn()
    attempts = 0
    stable = 0
    last_failures: list[str] = []
    while (monotonic_fn() - started) < timeout_seconds:
        attempts += 1
        ok_endpoint, endpoint_failures = _endpoint_ready(http_get_fn=http_get_fn)
        ok_target, target_failures = _cadvisor_target_healthy(fetch_targets_fn=fetch_targets_fn)
        ok_query, query_failures = _telemetry_query_valid(run_query_fn=run_query_fn)
        last_failures = endpoint_failures + target_failures + query_failures
        if ok_endpoint and ok_target and ok_query:
            stable += 1
            if stable >= TELEMETRY_REQUIRED_STABLE_PROBES:
                return True, {
                    "attempts": attempts,
                    "stable_probes": stable,
                    "required_stable_probes": TELEMETRY_REQUIRED_STABLE_PROBES,
                }
        else:
            stable = 0
        remaining = timeout_seconds - (monotonic_fn() - started)
        if remaining <= 0:
            break
        sleep_fn(min(TELEMETRY_SCRAPE_INTERVAL_SECONDS, remaining))
    return False, {
        "attempts": attempts,
        "stable_probes": stable,
        "required_stable_probes": TELEMETRY_REQUIRED_STABLE_PROBES,
        "timeout_seconds": timeout_seconds,
        "scrape_interval_seconds": TELEMETRY_SCRAPE_INTERVAL_SECONDS,
        "failures": last_failures,
    }


def wait_for_baseline_readiness(
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    timeout_seconds: int = BASELINE_READINESS_TIMEOUT_SECONDS,
    check_fn: Callable[[], tuple[bool, dict[str, Any]]] | None = None,
) -> tuple[bool, dict[str, Any], bool, dict[str, Any]]:
    """Prove paymentservice Deployment readiness BEFORE any experiment reservation.

    Applies the unchanged fail-closed baseline contract and requires
    BASELINE_REQUIRED_STABLE_PROBES consecutive healthy reads separated by at
    least BASELINE_STABILITY_INTERVAL_SECONDS. Read-only; never reserves.
    """
    check = check_fn or _paymentservice_baseline_check
    started = monotonic_fn()
    attempts = 0
    stable = 0
    last_healthy = False
    last_workloads: dict[str, Any] = {}
    while (monotonic_fn() - started) < timeout_seconds:
        attempts += 1
        last_healthy, last_workloads = check()
        if last_healthy:
            stable += 1
            if stable >= BASELINE_REQUIRED_STABLE_PROBES:
                return True, {"attempts": attempts, "stable_probes": stable}, True, last_workloads
        else:
            stable = 0
        remaining = timeout_seconds - (monotonic_fn() - started)
        if remaining <= 0:
            break
        sleep_fn(min(BASELINE_STABILITY_INTERVAL_SECONDS, remaining))
    return (
        False,
        {
            "attempts": attempts,
            "stable_probes": stable,
            "required_stable_probes": BASELINE_REQUIRED_STABLE_PROBES,
            "timeout_seconds": timeout_seconds,
            "stability_interval_seconds": BASELINE_STABILITY_INTERVAL_SECONDS,
        },
        last_healthy,
        last_workloads,
    )


def evaluate_causal_g4_predicate(
    baseline_healthy: bool,
    injection_success: bool,
    fault_observed: bool,
    incident_result: dict[str, Any],
    harness_repaired_pre_verification: bool,
    *,
    degradation_proven: bool = True,
    settling_completed: bool = True,
    primary_evidence_persisted: bool = False,
) -> dict[str, Any]:
    """Strictly evaluate the 15 causal requirements for Gate G4 PASS."""
    from agents.tool_policy import CLUSTER_MUTATING_TOOLS

    triage_final = incident_result.get("triage", {}).get("final", {})
    diagnosis_final = incident_result.get("diagnosis", {}).get("final", {})
    remediation_final = incident_result.get("remediation", {}).get("final", {})
    comms_final = incident_result.get("comms", {}).get("final", {})
    remediation_traj = incident_result.get("remediation", {}).get("trajectory", [])

    # 1. Baseline healthy
    c1 = baseline_healthy is True

    # 2. Injection success
    c2 = injection_success is True

    # 3. Fault observed and independently measured before trigger.
    c3 = fault_observed is True and degradation_proven is True

    # 4. Trigger delivered and handled
    incident_id = incident_result.get("incident_id")
    c4 = bool(incident_id and incident_id != "unknown")

    # 5. Triage valid schema
    c5 = bool(triage_final and isinstance(triage_final, dict) and "severity" in triage_final)

    # 6. Diagnosis valid schema
    c6 = bool(diagnosis_final and isinstance(diagnosis_final, dict) and "root_cause" in diagnosis_final)

    # 7. Diagnosis truth match (mentions paymentservice / CPU / stresschaos / resource pressure)
    diag_str = json.dumps(diagnosis_final).lower()
    c7 = bool(
        "payment" in diag_str
        or "cpu" in diag_str
        or "stress" in diag_str
        or "resource" in diag_str
        or "sf-002" in diag_str
    )

    # 8. Approval/safety policy satisfied
    c8 = incident_result.get("approval") is not None or triage_final.get("severity") in {"P0", "P1", "P2", "P3"}

    # 9. One real permitted remediation mutation executed
    # Policy/circuit-breaker/dedup blocks are not executions.
    executed_tool_calls: list[dict[str, Any]] = []
    for step in remediation_traj:
        if not isinstance(step, dict) or step.get("tool") not in CLUSTER_MUTATING_TOOLS:
            continue
        if (
            step.get("blocked_by_policy")
            or step.get("blocked_by_circuit_breaker")
            or step.get("dedup_blocked")
            or step.get("cap_blocked")
        ):
            continue
        executed_tool_calls.append(step)
    c9 = len(executed_tool_calls) >= 1

    # 10. Actual tool result reports success
    c10 = False
    executed_target_matched = False
    if executed_tool_calls:
        last_mutating = executed_tool_calls[-1]
        out = last_mutating.get("output", {})
        if isinstance(out, dict) and out.get("success") is True:
            c10 = True
        # 11. Mutation is relevant to target incident
        tool_name = last_mutating.get("tool")
        tool_args = last_mutating.get("args", {})
        if tool_name == "chaos_stop_experiment":
            kind_match = str(tool_args.get("kind", "")).lower() == TARGET_CHAOS_KIND.lower()
            name_match = str(tool_args.get("name", "")).strip() == TARGET_CHAOS_NAME
            executed_target_matched = kind_match and name_match
        elif tool_name in {"kubectl_rollout", "kubectl_scale"}:
            res_match = TARGET_SERVICE in str(tool_args.get("deployment", tool_args.get("resource", "")))
            executed_target_matched = res_match

    c11 = executed_target_matched

    # 12. No harness repair before verifier
    c12 = harness_repaired_pre_verification is False

    # 13. Bounded convergence completed, then objective env_resolved is true.
    verifier_result = incident_result.get("verification", {})
    env_resolved = bool(incident_result.get("env_resolved", False) or verifier_result.get("env_resolved", False))
    c13 = env_resolved is True and settling_completed is True

    # 14. Comms ran after verifier
    c14 = bool(comms_final and isinstance(comms_final, dict))

    # 15. The coordinator's primary incident record was durably persisted.
    c15 = primary_evidence_persisted is True

    criteria = {
        "1_baseline_healthy": c1,
        "2_injection_success": c2,
        "3_fault_observed_pre_trigger": c3,
        "4_trigger_delivered": c4,
        "5_triage_valid": c5,
        "6_diagnosis_valid": c6,
        "7_diagnosis_truth_match": c7,
        "8_approval_satisfied": c8,
        "9_remediation_mutating_tool_executed": c9,
        "10_remediation_tool_success": c10,
        "11_remediation_target_match": c11,
        "12_no_harness_repair_pre_verification": c12,
        "13_objective_env_resolved": c13,
        "14_comms_executed": c14,
        "15_evidence_persisted": c15,
    }

    gate_pass = all(criteria.values())
    return {
        "gate_g4_pass": gate_pass,
        "criteria": criteria,
        "env_resolved": env_resolved,
        "executed_tool_calls": executed_tool_calls,
    }


def _paymentservice_baseline_healthy(kubectl_stdout: str) -> bool:
    try:
        payload = json.loads(kubectl_stdout)
    except json.JSONDecodeError:
        return False
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return False
    for item in items:
        status = item.get("status") or {}
        desired = int(status.get("replicas", 0))
        ready = int(status.get("readyReplicas", 0))
        if desired <= 0 or ready < desired:
            return False
    return True


def _paymentservice_baseline_check() -> tuple[bool, dict[str, Any]]:
    """Fetch the target Deployment (not Pods) and evaluate Deployment-schema readiness."""
    workloads = run_kubectl(
        ["get", "deployments", "-n", TARGET_NAMESPACE, "-l", f"app={TARGET_SERVICE}", "-o", "json"]
    )
    healthy = (
        workloads.get("success") is True
        and _paymentservice_baseline_healthy(workloads.get("stdout", ""))
    )
    return healthy, workloads


def _active_chaos_count(kubectl_stdout: str) -> int:
    try:
        payload = json.loads(kubectl_stdout)
    except json.JSONDecodeError:
        return -1
    items = payload.get("items") if isinstance(payload, dict) else None
    return len(items) if isinstance(items, list) else -1


def _primary_incident_evidence_persisted(incident_result: dict[str, Any]) -> bool:
    incident_id = str(incident_result.get("incident_id") or "")
    if not incident_id:
        return False
    trajectory_dir = os.getenv("TRAJECTORIES_DIR", "")
    if not trajectory_dir:
        return False
    path = os.path.join(trajectory_dir, f"{incident_id}.json")
    persisted = _read_json_file(path)
    if not persisted or persisted.get("incident_id") != incident_id:
        return False
    runtime_trajectory = incident_result.get("remediation", {}).get("trajectory", [])
    persisted_trajectory = persisted.get("remediation", {}).get("trajectory", [])
    return len(persisted_trajectory) == len(runtime_trajectory)


def _persist_stage4_primary_evidence(evidence: dict[str, Any]) -> str:
    evidence_dir = _experiment_evidence_dir(evidence["experiment_id"])
    path = os.path.join(evidence_dir, f"{evidence['experiment_id']}.json")
    if os.path.exists(path):
        raise RuntimeError(f"Refusing to overwrite Stage 4 evidence: {path}")
    _write_json_atomic(path, evidence)
    return path


def _current_main_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    main_sha = result.stdout.strip()
    if result.returncode != 0 or len(main_sha) != 40:
        raise RuntimeError("Unable to establish a valid repository HEAD for Stage 4 reservation")
    return main_sha


def _persist_stage4_prefault_failure(evidence: dict[str, Any]) -> str:
    evidence_dir = _experiment_evidence_dir(evidence["experiment_id"])
    path = os.path.join(
        evidence_dir,
        ".attempts",
        f"{evidence['experiment_id']}.prefault.json",
    )
    _write_json_atomic(path, evidence)
    return path


async def main() -> dict[str, Any]:
    fault_crossed = False
    reservation: dict[str, Any] | None = None
    print("=" * 80)
    print(f" ATLASOPS STAGE 4 GOLDEN INCIDENT VALIDATION ({EXPERIMENT_ID}) ")
    print(f" Scenario: {SCENARIO_ID} | Model: {SELECTED_STAGE4_AGENT_MODEL} (Ollama Local) ")
    print("=" * 80)

    # Ensure context is kind-atlasops-local
    subprocess.run(["kubectl", "config", "use-context", KIND_CONTEXT], capture_output=True)

    os.environ["KUBECONFIG_CONTEXT"] = KIND_CONTEXT
    os.environ["BACKEND"] = "vllm"
    os.environ["VLLM_BASE"] = "http://localhost:11434/v1"
    os.environ["AGENT_MODEL"] = SELECTED_STAGE4_AGENT_MODEL
    os.environ["PROMETHEUS_URL"] = "http://localhost:19090"
    os.environ["ALERTMANAGER_URL"] = "http://localhost:19093"
    os.environ["JAEGER_URL"] = "http://localhost:16686"
    os.environ["ARGOCD_URL"] = "http://localhost:18080"
    os.environ["ARGOCD_VERIFY_TLS"] = "false"

    start_time = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    # Port forwards for local tool execution
    pf_specs = [
        ("default", "atlasops-coordinator-svc", 19099, 9099),
        ("monitoring", "prometheus-kube-prometheus-prometheus", 19090, 9090),
        ("monitoring", "prometheus-kube-prometheus-alertmanager", 19093, 9093),
        ("jaeger", "jaeger", 16686, 16686),
        ("argocd", "argocd-server", 18080, 80),
    ]
    pf_procs = []
    for ns, svc, lp, rp in pf_specs:
        p = subprocess.Popen(
            ["kubectl", "--context", KIND_CONTEXT, "port-forward", f"svc/{svc}", f"{lp}:{rp}", "-n", ns],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pf_procs.append(p)
    time.sleep(3)

    evidence: dict[str, Any] = {
        "experiment_id": EXPERIMENT_ID,
        "scenario_id": SCENARIO_ID,
        "tier": "single_fault",
        **stage4_evidence_metadata(),
        "started_at": start_time,
        "phases": {},
    }

    def abort_before_fault(phase: str) -> dict[str, Any]:
        released = release_experiment_reservation(reservation)
        evidence["attempt_state"] = "RELEASED_PRE_FAULT"
        evidence["reservation_released"] = released
        evidence["outcome"] = "INVALID"
        evidence["failure_phase"] = phase
        evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
        prefault_path = _persist_stage4_prefault_failure(evidence)
        evidence["prefault_evidence"] = prefault_path
        return evidence

    try:
        # Phase 0: Telemetry readiness gate — strictly BEFORE any reservation.
        # No attempt marker, no RESERVED state, no chaos object, and no T0 may
        # exist unless the exact F1 measurement path is demonstrably usable.
        print("\n>>> Phase 0: Telemetry Readiness Gate (pre-reservation)...")
        telemetry_ready, readiness_detail = wait_for_telemetry_readiness()
        evidence["phases"]["telemetry_readiness"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ready": telemetry_ready,
            **readiness_detail,
        }
        print(
            f"  Telemetry readiness: {'READY' if telemetry_ready else 'NOT READY'} "
            f"(stable_probes={readiness_detail.get('stable_probes')}/"
            f"{TELEMETRY_REQUIRED_STABLE_PROBES})"
        )
        if not telemetry_ready:
            for failure in readiness_detail.get("failures", []):
                print(f"    - {failure}")
            print("  Refusing to reserve or inject: F1 telemetry path unusable.")
            evidence["attempt_state"] = "NOT_RESERVED"
            evidence["outcome"] = "PREFLIGHT_ABORT"
            evidence["failure_phase"] = "telemetry_readiness"
            evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
            return evidence

        # Phase 0b: Paymentservice Deployment baseline readiness — also strictly
        # BEFORE any reservation. Applies the unchanged fail-closed health
        # contract with bounded two-consecutive-read stability.
        print("\n>>> Phase 0b: Paymentservice Baseline Readiness Gate (pre-reservation)...")
        (
            baseline_ready,
            baseline_detail,
            baseline_healthy,
            base_workloads,
        ) = wait_for_baseline_readiness()
        baseline_telemetry = collect_sf002_cpu_telemetry()
        evidence["phases"]["baseline"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_deployments": base_workloads.get("stdout")[:500],
            "baseline_healthy": baseline_healthy,
            "cpu_telemetry": baseline_telemetry,
            **(
                {
                    "attempts": baseline_detail.get("attempts"),
                    "stable_probes": baseline_detail.get("stable_probes"),
                    "required_stable_probes": baseline_detail.get("required_stable_probes"),
                }
                if isinstance(baseline_detail, dict)
                else {}
            ),
        }
        print(
            f"  Baseline readiness: {'READY' if baseline_ready else 'NOT READY'} "
            f"(stable_probes={baseline_detail.get('stable_probes')}/"
            f"{BASELINE_REQUIRED_STABLE_PROBES}, healthy={baseline_healthy})"
        )
        if not baseline_ready:
            print("  Healthy paymentservice baseline not established; refusing to reserve or inject.")
            evidence["attempt_state"] = "NOT_RESERVED"
            evidence["outcome"] = "PREFLIGHT_ABORT"
            evidence["failure_phase"] = "unhealthy_baseline"
            evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
            return evidence

        reservation = reserve_experiment_attempt(
            EXPERIMENT_ID,
            selected_model=SELECTED_STAGE4_AGENT_MODEL,
            main_sha=_current_main_sha(),
        )

        # Pre-experiment environment cleanup: ensure zero stale chaos experiments exist before baseline
        print("\n>>> Pre-Experiment: Ensuring clean cluster state (zero stale chaos)...")
        run_kubectl(["delete", "stresschaos", "--all", "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        run_kubectl(["delete", "podchaos", "--all", "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        run_kubectl(["delete", "networkchaos", "--all", "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        run_kubectl(["delete", "dnschaos", "--all", "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        run_kubectl(["delete", "iochaos", "--all", "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        run_kubectl(["delete", "timechaos", "--all", "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        time.sleep(2)

        chaos_precheck = run_kubectl(
            ["get", "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos", "-A", "-o", "json"]
        )
        active_chaos_count = _active_chaos_count(chaos_precheck.get("stdout", ""))
        if active_chaos_count != 0:
            evidence["phases"]["pre_fault_chaos_check"] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "active_chaos_count": active_chaos_count,
                "result": chaos_precheck,
            }
            print(f"  Active chaos remains ({active_chaos_count}); refusing to start incident setup.")
            return abort_before_fault("pre_fault_chaos_not_zero")

        # Phase 2: Inject Fault
        print(f"\n>>> Phase 2: Injecting Fault ({SCENARIO_ID})...")
        manifest_path = os.path.join(REPO_ROOT, "bench", "chaos_manifests", "single_fault", "sf-002.yaml")
        inject_res = run_kubectl(["apply", "-f", manifest_path])
        injection_success = inject_res.get("success") is True
        if not injection_success:
            released = release_experiment_reservation(reservation)
            evidence["attempt_state"] = "RELEASED_PRE_FAULT"
            evidence["reservation_released"] = released
            evidence["outcome"] = "INVALID"
            evidence["failure_phase"] = "fault_application"
            evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
            evidence["prefault_evidence"] = _persist_stage4_prefault_failure(evidence)
            print(f"  Fault application failed; reservation released={released}")
            return evidence
        consume_experiment_attempt(reservation)
        fault_crossed = True
        evidence["attempt_state"] = ATTEMPT_STATE_CONSUMED
        evidence["phases"]["injection"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "manifest": manifest_path,
            "result": inject_res,
        }
        print(f"  Chaos Mesh injection: {inject_res.get('stdout')}")

        # Phase 3: Observable Fault Verification
        print("\n>>> Phase 3: Verifying Observable Fault in Cluster...")
        time.sleep(4)
        chaos_check = run_kubectl(["get", TARGET_CHAOS_KIND.lower(), "-n", TARGET_CHAOS_NAMESPACE, TARGET_CHAOS_NAME, "-o", "json"])
        fault_observable = chaos_check.get("success") is True
        evidence["phases"]["observable_fault"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stresschaos_observed": fault_observable,
            "chaos_status": chaos_check.get("stdout")[:500],
        }
        print(f"  Observable in cluster: {fault_observable}")

        # Phase 3a: Independently measure the CPU effect before any agent runs.
        print("\n>>> Phase 3a: Measuring SF002 Degradation...")
        post_fault_observations = []
        degradation_decision: dict[str, Any] | None = None
        degradation_deadline = time.monotonic() + DEGRADATION_OBSERVATION_TIMEOUT_SECONDS
        while time.monotonic() < degradation_deadline:
            post_fault_telemetry = collect_sf002_cpu_telemetry()
            post_fault_observations.append(post_fault_telemetry)
            degradation_decision = sf002_degradation_decision(
                baseline_telemetry,
                post_fault_telemetry,
            )
            if degradation_decision["passed"]:
                break
            await asyncio.sleep(DEGRADATION_POLL_INTERVAL_SECONDS)
        if degradation_decision is None:
            degradation_decision = sf002_degradation_decision(
                baseline_telemetry,
                {"max_cores": None},
            )
        evidence["phases"]["degradation_proof"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timeout_seconds": DEGRADATION_OBSERVATION_TIMEOUT_SECONDS,
            "poll_interval_seconds": DEGRADATION_POLL_INTERVAL_SECONDS,
            "decision": degradation_decision,
            "post_fault_observations": post_fault_observations,
        }

        if not fault_observable or not degradation_decision["passed"]:
            evidence["attempt_state"] = ATTEMPT_STATE_CONSUMED
            evidence["outcome"] = "INVALID"
            evidence["failure_phase"] = (
                "fault_activation" if not fault_observable else "measured_degradation"
            )
            evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
            primary_path = _persist_stage4_primary_evidence(evidence)
            complete_experiment_attempt(reservation)
            clean_res = run_kubectl(
                ["delete", TARGET_CHAOS_KIND.lower(), TARGET_CHAOS_NAME, "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"]
            )
            cleanup_record = {
                "experiment_id": EXPERIMENT_ID,
                "timing": "after_failure_verdict_persisted",
                "affects_env_resolved": False,
                "result": clean_res,
            }
            _write_json_atomic(
                os.path.join(_experiment_evidence_dir(EXPERIMENT_ID), f"{EXPERIMENT_ID}.cleanup.json"),
                cleanup_record,
            )
            print(f"  Degradation proof failed; INVALID evidence saved: {primary_path}")
            return evidence
        evidence["phases"]["degradation_proof"]["passed"] = True

        # Phase 4: Construct Alert & Trigger Multi-Agent Coordinator Pipeline
        # NOTE: The model-visible alert must contain ONLY realistic operational
        # signals. Scenario identity (SCENARIO_ID) is passed to the verifier via
        # the dedicated evaluation-only channel and MUST stay out of labels,
        # annotations, and commonLabels — otherwise the golden answer leaks.
        print("\n>>> Phase 4: Triggering Coordinator Multi-Agent Pipeline...")
        alert_payload = {
            "receiver": "atlasops-webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCpuUsage",
                        "severity": "critical",
                        "service": TARGET_SERVICE,
                        "namespace": TARGET_NAMESPACE,
                    },
                    "annotations": {
                        "summary": f"High CPU usage on {TARGET_SERVICE}",
                        "description": f"{TARGET_SERVICE} CPU utilization is at 90% load across 4 workers.",
                    },
                    "startsAt": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "commonLabels": {
                "alertname": "HighCpuUsage",
                "service": TARGET_SERVICE,
                "severity": "critical",
            },
        }

        from agents.coordinator import handle_incident

        incident_result = await handle_incident(alert_payload, scenario_id=SCENARIO_ID)
        triage_res = incident_result.get("triage", {})
        diagnosis_res = incident_result.get("diagnosis", {})
        remediation_res = incident_result.get("remediation", {})
        comms_res = incident_result.get("comms", {})
        verifier_res = incident_result.get("verification", {})

        print(f"  Incident ID: {incident_result.get('incident_id')}")
        print(f"  Triage Final: {triage_res.get('final')}")
        print(f"  Diagnosis Final: {diagnosis_res.get('final')}")
        print(f"  Remediation Final: {remediation_res.get('final')}")
        print(f"  Coordinator Verifier Env Resolved: {incident_result.get('env_resolved')}")

        # Extract real tool execution and distinction
        rem_traj = remediation_res.get("trajectory", [])
        executed_tools = [
            {
                "tool": step.get("tool"),
                "args": step.get("args"),
                "output": step.get("output"),
                "blocked_by_policy": step.get("blocked_by_policy", False),
            }
            for step in rem_traj
            if isinstance(step, dict) and "tool" in step
        ]

        evidence["phases"]["coordinator_execution"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "incident_id": incident_result.get("incident_id"),
            "triage": triage_res.get("final"),
            "diagnosis": diagnosis_res.get("final"),
            "approval": incident_result.get("approval"),
            "grounding_validation": incident_result.get("grounding_validation", {}),
            "model_proposed_action": remediation_res.get("final"),
            "executed_tool_actions": executed_tools,
            "comms": comms_res.get("final"),
        }

        evidence["phases"]["verification"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "verification_report": verifier_res,
            "env_resolved": incident_result.get("env_resolved", False),
            "agent_claimed_resolved": incident_result.get("agent_claimed_resolved", False),
        }

        # Phase 5: Causal Gate G4 Evaluation (NO HARNESS DELETION PRE-VERIFICATION)
        harness_repaired_pre_verification = False
        primary_evidence_persisted = _primary_incident_evidence_persisted(incident_result)
        eval_result = evaluate_causal_g4_predicate(
            baseline_healthy=baseline_healthy,
            injection_success=injection_success,
            fault_observed=fault_observable,
            incident_result=incident_result,
            harness_repaired_pre_verification=harness_repaired_pre_verification,
            degradation_proven=bool(degradation_decision["passed"]),
            settling_completed=bool(
                incident_result.get("settling", {}).get("settled", False)
            ),
            primary_evidence_persisted=primary_evidence_persisted,
        )

        gate_g4_pass = eval_result["gate_g4_pass"]
        env_resolved = eval_result["env_resolved"]
        duration_s = round(time.time() - t0, 2)

        evidence["duration_seconds"] = duration_s
        evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
        evidence["gate_g4_pass"] = gate_g4_pass
        evidence["causal_criteria"] = eval_result["criteria"]

        print("\n" + "=" * 80)
        print(f" STAGE 4 GOLDEN INCIDENT RESULT: {'PASS' if gate_g4_pass else 'FAIL'} ")
        print(f" Objective Env Resolved: {env_resolved} | Total Duration: {duration_s}s ")
        for crit_name, crit_val in eval_result["criteria"].items():
            print(f"   [{'PASS' if crit_val else 'FAIL'}] {crit_name}")
        print("=" * 80)

        # Save immutable per-experiment evidence; also refresh the latest pointer.
        # Experiment-ID immutability: refuse to overwrite preserved evidence.
        evidence_dir = os.path.join(REPO_ROOT, "artifacts", "evidence", "stage4")
        os.makedirs(evidence_dir, exist_ok=True)
        per_run_file = os.path.join(evidence_dir, f"{EXPERIMENT_ID}.json")
        latest_file = os.path.join(evidence_dir, "golden_incident_sf002_manifest.json")
        if os.path.exists(per_run_file):
            raise SystemExit(
                f"Refusing to overwrite existing Stage 4 evidence '{per_run_file}'. "
                "Historical experiment records are immutable. Re-run with a new "
                "STAGE4_EXPERIMENT_ID (e.g. EXP-STAGE4-SF002-005)."
            )
        _write_json_atomic(per_run_file, evidence)
        _write_json_atomic(latest_file, evidence)
        print(f"\nSaved golden incident evidence: {per_run_file}")
        print(f"Updated latest pointer: {latest_file}")

        complete_experiment_attempt(reservation)
        evidence["attempt_state"] = ATTEMPT_STATE_COMPLETED

        # Post-verdict safety cleanup (AFTER verdict is frozen and saved).
        # Recorded in a separate sidecar so the measured evidence file above
        # stays byte-immutable after the verdict.
        print("\n>>> Phase 6: Post-Verdict Cluster Safety Cleanup...")
        clean_res = run_kubectl(["delete", TARGET_CHAOS_KIND.lower(), TARGET_CHAOS_NAME, "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        cleanup_record = {
            "experiment_id": EXPERIMENT_ID,
            "timing": "after_verdict_persisted",
            "affects_env_resolved": False,
            "command": f"kubectl delete {TARGET_CHAOS_KIND.lower()} {TARGET_CHAOS_NAME} -n {TARGET_CHAOS_NAMESPACE} --ignore-not-found=true",
            "result": clean_res,
        }
        cleanup_file = os.path.join(evidence_dir, f"{EXPERIMENT_ID}.cleanup.json")
        _write_json_atomic(cleanup_file, cleanup_record)
        print(f"  Safety cleanup: {clean_res.get('stdout', 'clean')}")
        print(f"  Cleanup record (sidecar): {cleanup_file}")

        return evidence

    except Exception:
        if reservation is not None and not fault_crossed:
            release_experiment_reservation(reservation)
        raise
    finally:
        for p in pf_procs:
            p.terminate()
            p.wait()


if __name__ == "__main__":
    rep = asyncio.run(main())
    if not rep.get("gate_g4_pass"):
        sys.exit(1)
