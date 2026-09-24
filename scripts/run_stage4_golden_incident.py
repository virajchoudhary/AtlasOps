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
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from config.g4_protocol import (
    G4_PROTOCOL_MARKER,
    build_runtime_protocol_profile,
    file_sha256,
    inspect_metrics_server_deployment,
    metrics_server_declaration,
    protocol_fingerprint,
    validate_runtime_protocol_profile,
)
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


_EXPERIMENT_ID_RE = re.compile(r"^EXP-STAGE4-[A-Za-z0-9][A-Za-z0-9_-]{0,112}$")


def _validated_experiment_id(experiment_id: str) -> str:
    if not isinstance(experiment_id, str) or not _EXPERIMENT_ID_RE.fullmatch(experiment_id):
        raise ValueError("Stage 4 experiment ID must be a single safe EXP-STAGE4-* name")
    return experiment_id


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
EXPERIMENT_ID = _validated_experiment_id(
    os.environ.get("STAGE4_EXPERIMENT_ID", "EXP-STAGE4-SF002-004")
)
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
ATTEMPT_STATES = {
    ATTEMPT_STATE_RESERVED,
    ATTEMPT_STATE_CONSUMED,
    ATTEMPT_STATE_COMPLETED,
}
G4_PLATFORM_HARDENING_MARKER = G4_PROTOCOL_MARKER
MAX_ATTEMPTS_PER_PROTOCOL_MARKER = 2
ATTEMPT_BUDGET_LOCK_FILENAME = ".reservation.lock"
CHAOS_RESOURCE_KINDS = "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos"
POISONED_ENVIRONMENT_FILENAME = ".poisoned-environment.json"
MAX_POSTFLIGHT_CLEANUP_ATTEMPTS = 3
POSTFLIGHT_CLEANUP_RETRY_INTERVAL_SECONDS = 2
BLOCKED_ACTION_MARKERS = (
    "blocked_by_policy",
    "blocked_by_circuit_breaker",
    "dedup_blocked",
    "cap_blocked",
    "invalid_arguments",
)


def run_kubectl(args: list[str], timeout: int = 20) -> dict[str, Any]:
    cmd = ["kubectl", "--context", KIND_CONTEXT] + args
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return {
            "success": res.returncode == 0,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
            "returncode": res.returncode,
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc), "returncode": -1}


def _query_ollama_model_identity(selected_model: str) -> dict[str, str]:
    """Read the exact local model identity without exposing credentials."""
    import requests

    base_url = os.getenv("VLLM_BASE", "http://localhost:11434/v1").strip().rstrip("/")
    base_url = base_url.removesuffix("/v1")
    try:
        response = requests.get(
            f"{base_url}/api/tags",
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"Unable to verify Stage 4 model identity for {selected_model}: {type(exc).__name__}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Unable to verify Stage 4 model identity for {selected_model}: response from /api/tags is not a JSON object"
        )
    models = payload.get("models")
    if not isinstance(models, list):
        raise RuntimeError(
            f"Unable to verify Stage 4 model identity for {selected_model}: 'models' in /api/tags is not a list"
        )

    matching = [
        m
        for m in models
        if isinstance(m, dict)
        and (m.get("name") == selected_model or m.get("model") == selected_model)
    ]
    if len(matching) == 0:
        raise RuntimeError(
            f"Stage 4 model {selected_model} is not installed in local Ollama"
        )
    if len(matching) > 1:
        raise RuntimeError(
            f"Ambiguous model resolution for {selected_model} in local Ollama"
        )

    raw_digest = matching[0].get("digest")
    if not isinstance(raw_digest, str) or not raw_digest.strip():
        raise RuntimeError(f"Stage 4 model identity for {selected_model} has no digest")
    normalized_digest = raw_digest.strip().lower().removeprefix("sha256:")
    if len(normalized_digest) != 64 or any(
        c not in "0123456789abcdef" for c in normalized_digest
    ):
        raise RuntimeError(
            f"Stage 4 model identity for {selected_model} has invalid SHA-256 digest"
        )
    return {
        "provider": "ollama-local",
        "name": selected_model,
        "digest": normalized_digest,
    }


def _probe_metrics_server_contract() -> dict[str, Any]:
    observation = inspect_metrics_server_deployment(run_kubectl)
    if observation.get("state") == "present":
        return {
            **metrics_server_declaration(),
            "live_config_sha256": observation["live_config_sha256"],
        }
    return observation



def _observe_protocol_profile(selected_model: str) -> dict[str, Any]:
    observed = build_runtime_protocol_profile(
        selected_model=selected_model,
        model_digest=_query_ollama_model_identity(selected_model)["digest"],
        metrics_observation=_probe_metrics_server_contract(),
    )
    return validate_runtime_protocol_profile(observed)


def _experiment_evidence_dir(experiment_id: str, root: str | None = None) -> str:
    if experiment_id:
        _validated_experiment_id(experiment_id)
    base = root or REPO_ROOT
    return os.path.join(base, "artifacts", "evidence", "stage4")


def _attempt_marker_path(experiment_id: str, root: str | None = None) -> str:
    safe_id = experiment_id.replace(os.sep, "_").replace("/", "_")
    return os.path.join(
        _experiment_evidence_dir(experiment_id, root),
        ".attempts",
        f"{safe_id}.attempt.json",
    )


def _poisoned_environment_path(root: str | None = None) -> str:
    return os.path.join(
        _experiment_evidence_dir("", root), POISONED_ENVIRONMENT_FILENAME
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


def _claimed_attempts_for_protocol_fingerprint(
    protocol_fingerprint_value: str, attempt_root: str | None = None
) -> int:
    attempts_dir = os.path.join(
        _experiment_evidence_dir("", attempt_root), ".attempts"
    )
    if not os.path.isdir(attempts_dir):
        return 0

    claimed_attempts = 0
    for name in os.listdir(attempts_dir):
        if not name.endswith(".attempt.json"):
            continue
        path = os.path.join(attempts_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as stream:
                attempt = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Stage 4 attempt accounting record is invalid: {path}"
            ) from exc
        if not isinstance(attempt, dict):
            raise TypeError(f"Stage 4 attempt accounting record is invalid: {path}")
        if attempt.get("protocol_fingerprint") != protocol_fingerprint_value:
            continue
        if attempt.get("state") not in ATTEMPT_STATES:
            raise RuntimeError(
                f"Stage 4 attempt has an invalid state for accounting: {path}"
            )
        # An unused reservation occupies a slot until explicitly released.
        claimed_attempts += 1
    return claimed_attempts


@contextmanager
def _reservation_budget_lock(attempt_root: str | None = None):
    attempts_dir = os.path.join(
        _experiment_evidence_dir("", attempt_root), ".attempts"
    )
    os.makedirs(attempts_dir, exist_ok=True)
    lock_path = os.path.join(attempts_dir, ATTEMPT_BUDGET_LOCK_FILENAME)
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(
            "Stage 4 reservation budget is locked by another process; "
            f"refusing ambiguous accounting: {lock_path}"
        ) from exc
    try:
        yield
    finally:
        os.close(lock_descriptor)
        os.unlink(lock_path)


def reserve_experiment_attempt(
    experiment_id: str,
    *,
    selected_model: str,
    main_sha: str,
    attempt_root: str | None = None,
) -> dict[str, Any]:
    """Atomically reserve an experiment after exact protocol qualification."""
    evidence_dir = _experiment_evidence_dir(experiment_id)
    primary_path = os.path.join(evidence_dir, f"{experiment_id}.json")
    if os.path.exists(primary_path):
        raise RuntimeError(f"Stage 4 evidence already exists: {primary_path}")

    poisoned_path = _poisoned_environment_path(attempt_root)
    if os.path.exists(poisoned_path):
        raise RuntimeError(
            "Stage 4 environment is poisoned because zero-Chaos postflight was "
            f"not proven; operator verification is required before another run: {poisoned_path}"
        )

    marker_path = _attempt_marker_path(experiment_id, attempt_root)
    profile = _observe_protocol_profile(selected_model)
    profile_fingerprint = protocol_fingerprint(profile)
    with _reservation_budget_lock(attempt_root):
        if os.path.exists(poisoned_path):
            raise RuntimeError(
                "Stage 4 environment is poisoned because zero-Chaos postflight was "
                f"not proven; operator verification is required before another run: {poisoned_path}"
            )
        spent_attempts = _claimed_attempts_for_protocol_fingerprint(
            profile_fingerprint, attempt_root
        )
        if spent_attempts >= MAX_ATTEMPTS_PER_PROTOCOL_MARKER:
            raise RuntimeError(
                "protocol attempt limit reached for "
                f"profile {profile_fingerprint}: "
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
            "reserved_at": datetime.now(UTC).isoformat(),
            "reservation_token": uuid.uuid4().hex,
            "protocol_marker": G4_PLATFORM_HARDENING_MARKER,
            "protocol_profile": profile,
            "protocol_fingerprint": profile_fingerprint,
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
        timestamp_field: datetime.now(UTC).isoformat(),
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

    started_at = datetime.now(UTC).isoformat()
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
    """Build pending model-identity fields shared by output and evidence.

    The runner overwrites ``protocol_profile`` with the reservation's exact
    validated profile before fault injection and primary evidence persistence.
    """
    return {
        "model": SELECTED_STAGE4_AGENT_MODEL,
        "inference_provider": "ollama-local",
        "protocol_marker": G4_PLATFORM_HARDENING_MARKER,
        "protocol_profile": {
            "protocol_fingerprint": None,
            "validation_state": "PENDING_RESERVATION",
        },
        "trigger_type": "manual coordinator trigger over a real independently observed cluster fault",
    }


def _prometheus_http_get(path: str) -> tuple[int | None, str]:
    """Read-only GET against the configured Prometheus endpoint."""
    base = os.environ.get("PROMETHEUS_URL", "http://localhost:19090").rstrip("/")
    try:
        import requests

        resp = requests.get(f"{base}{path}", timeout=10)
        return resp.status_code, resp.text
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _endpoint_ready(
    http_get_fn: Callable[[str], tuple[int | None, str]] = _prometheus_http_get,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for path in ("/-/healthy", "/-/ready"):
        try:
            status, _body = http_get_fn(path)
        except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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


def _status_text(value: Any) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def _p1_approval_satisfied(severity: Any, approval: Any) -> bool:
    """Require the coordinator's explicit approval record for this P1 run."""
    if _status_text(severity) != "p1" or not isinstance(approval, dict):
        return False
    if _status_text(approval.get("mode")) != "approve":
        return False
    if _status_text(approval.get("severity")) != "p1":
        return False
    approved_by = approval.get("approved_by")
    if not isinstance(approved_by, str) or not approved_by.strip():
        return False

    decision = _status_text(approval.get("decision"))
    status = _status_text(approval.get("status"))
    if decision and decision != "approved":
        return False
    if status and status != "approved":
        return False
    return decision == "approved" or status == "approved"


def _diagnosis_is_evidence_backed(incident_result: dict[str, Any]) -> bool:
    diagnosis = incident_result.get("diagnosis")
    final = diagnosis.get("final") if isinstance(diagnosis, dict) else None
    if not isinstance(final, dict):
        return False
    root_cause = final.get("root_cause")
    if not isinstance(root_cause, str) or not root_cause.strip():
        return False
    cause_text = root_cause.casefold()
    compact_cause = cause_text.replace(" ", "").replace("-", "").replace("_", "")
    identifies_target = TARGET_SERVICE.casefold() in compact_cause
    identifies_fault = any(
        phrase in cause_text
        for phrase in ("cpu", "stresschaos", "stress chaos", "resource pressure")
    )

    anchors = incident_result.get("incident_anchors")
    anchors = anchors if isinstance(anchors, dict) else {}
    target_consistency = incident_result.get("target_consistency")
    target_consistency = target_consistency if isinstance(target_consistency, dict) else {}
    target_is_anchored = (
        _status_text(anchors.get("primary_service")) == TARGET_SERVICE.casefold()
        and _status_text(target_consistency.get("primary_target"))
        == TARGET_SERVICE.casefold()
        and target_consistency.get("requires_review") is False
        and target_consistency.get("status")
        in {"primary_target_preserved", "mismatch_observed"}
    )

    try:
        from agents.grounding import validate_evidence_grounding

        computed_grounding = validate_evidence_grounding(diagnosis)
    except Exception:  # noqa: BLE001
        return False
    persisted_grounding = incident_result.get("grounding_validation")
    persisted_grounding = (
        persisted_grounding.get("diagnosis")
        if isinstance(persisted_grounding, dict)
        else None
    )
    grounded_citations = (
        isinstance(persisted_grounding, dict)
        and persisted_grounding == computed_grounding
        and computed_grounding.get("grounded") is True
        and computed_grounding.get("citation_count", 0) > 0
        and not computed_grounding.get("violations")
    )
    return identifies_target and identifies_fault and target_is_anchored and grounded_citations


def _settling_report_satisfied(incident_result: dict[str, Any]) -> bool:
    report = incident_result.get("settling")
    if not isinstance(report, dict) or report.get("settled") is not True:
        return False
    if not all(
        isinstance(report.get(field), str) and report[field].strip()
        for field in ("started_at", "completed_at")
    ):
        return False
    timeout = report.get("timeout_seconds")
    poll_interval = report.get("poll_interval_seconds")
    duration = report.get("duration_seconds")
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value) and value > 0
        for value in (timeout, poll_interval)
    ):
        return False
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
        return False
    observations = report.get("observations")
    if not isinstance(observations, list) or not observations:
        return False
    last = observations[-1]
    return (
        isinstance(last, dict)
        and last.get("env_resolved") is True
        and _status_text(last.get("verification_status")) == "passed"
        and last.get("failed_checks") == []
    )


def _status_claim_matches(text: str, *, env_resolved: bool) -> bool:
    normalized = text.casefold()
    unresolved_terms = ("unresolved", "unverified", "not resolved", "not verified", "blocked")
    if env_resolved:
        return (
            ("resolved" in normalized or "verified" in normalized)
            and not any(term in normalized for term in unresolved_terms)
        )
    return any(term in normalized for term in unresolved_terms)


def _postmortem_path_is_owned(path: str) -> bool:
    try:
        candidate = Path(path).resolve()
        root = Path(os.getenv("POSTMORTEM_DIR", os.path.join(REPO_ROOT, "artifacts", "postmortems"))).resolve()
        return candidate != root and root in candidate.parents
    except (OSError, RuntimeError):
        return False


def _comms_evidence_satisfied(incident_result: dict[str, Any], *, env_resolved: bool) -> bool:
    comms = incident_result.get("comms")
    final = comms.get("final") if isinstance(comms, dict) else None
    if not isinstance(final, dict):
        return False
    incident_id = str(incident_result.get("incident_id") or "")
    summary = final.get("summary")
    postmortem_path = final.get("postmortem_path")
    if (
        not incident_id
        or final.get("incident_id") != incident_id
        or not isinstance(summary, str)
        or not summary.strip()
        or not _status_claim_matches(summary, env_resolved=env_resolved)
        or not isinstance(postmortem_path, str)
        or not _postmortem_path_is_owned(postmortem_path)
    ):
        return False

    actual_postmortem_path = None
    trajectory = comms.get("trajectory")
    if isinstance(trajectory, list):
        for step in trajectory:
            if not isinstance(step, dict) or step.get("tool") != "postmortem_draft":
                continue
            output = step.get("output")
            if not isinstance(output, dict) or output.get("success") is not True:
                continue
            actual_postmortem_path = output.get("postmortem_path") or output.get("path")
            if actual_postmortem_path:
                break
    if not isinstance(actual_postmortem_path, str):
        return False
    try:
        if Path(actual_postmortem_path).resolve() != Path(postmortem_path).resolve():
            return False
        content = Path(actual_postmortem_path).read_text(encoding="utf-8").casefold()
    except (OSError, RuntimeError):
        return False
    diagnosis = incident_result.get("diagnosis")
    final_diagnosis = diagnosis.get("final") if isinstance(diagnosis, dict) else None
    root_cause = final_diagnosis.get("root_cause") if isinstance(final_diagnosis, dict) else None
    return (
        bool(content.strip())
        and isinstance(root_cause, str)
        and root_cause.strip().casefold() in content
        and _status_claim_matches(content, env_resolved=env_resolved)
    )


def _remediation_tool_outcomes(remediation_trajectory: Any) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    if not isinstance(remediation_trajectory, list):
        return outcomes
    for step in remediation_trajectory:
        if not isinstance(step, dict):
            continue
        nested_outcomes = step.get("tool_outcomes")
        if isinstance(nested_outcomes, list):
            outcomes.extend(
                {"turn": step.get("turn"), **outcome}
                for outcome in nested_outcomes
                if isinstance(outcome, dict)
            )
        if "tool" in step:
            outcomes.append(dict(step))
    return outcomes


def evaluate_causal_g4_predicate(
    baseline_healthy: bool,
    injection_success: bool,
    fault_observed: bool,
    incident_result: dict[str, Any],
    harness_repaired_pre_verification: bool,
    *,
    degradation_proven: bool = True,
    settling_completed: bool | None = None,
    primary_evidence_persisted: bool = False,
) -> dict[str, Any]:
    """Strictly evaluate the 15 causal requirements for Gate G4 PASS."""
    from agents.tool_policy import CLUSTER_MUTATING_TOOLS

    triage_result = incident_result.get("triage")
    diagnosis_result = incident_result.get("diagnosis")
    remediation_result = incident_result.get("remediation")
    triage_final = triage_result.get("final", {}) if isinstance(triage_result, dict) else {}
    diagnosis_final = (
        diagnosis_result.get("final", {}) if isinstance(diagnosis_result, dict) else {}
    )
    remediation_traj = (
        remediation_result.get("trajectory", [])
        if isinstance(remediation_result, dict)
        else []
    )
    remediation_traj = remediation_traj if isinstance(remediation_traj, list) else []

    # 1. Baseline healthy
    c1 = baseline_healthy is True

    # 2. Injection success
    c2 = injection_success is True

    # 3. Fault observed and independently measured before trigger.
    c3 = fault_observed is True and degradation_proven is True

    # 4. Trigger delivered and handled
    incident_id = incident_result.get("incident_id")
    c4 = bool(incident_id and incident_id != "unknown")

    # 5. Triage valid schema, with one of the declared severity values.
    severity = _status_text(triage_final.get("severity")) if isinstance(triage_final, dict) else ""
    c5 = severity in {"p0", "p1", "p2", "p3"}

    # 6. Diagnosis valid schema
    c6 = bool(
        isinstance(diagnosis_final, dict)
        and isinstance(diagnosis_final.get("root_cause"), str)
        and diagnosis_final["root_cause"].strip()
    )

    # 7. Diagnosis matches the anchored target/fault and cites grounded observations.
    c7 = _diagnosis_is_evidence_backed(incident_result)

    # 8. The SF002 incident is P1 and has an explicit coordinator approval record.
    approval = incident_result.get("approval")
    c8 = _p1_approval_satisfied(severity, approval)

    # 9. Exactly one unblocked cluster mutation was executed.
    remediation_outcomes = _remediation_tool_outcomes(remediation_traj)
    executed_tool_calls: list[dict[str, Any]] = []
    for step in remediation_traj:
        if not isinstance(step, dict) or step.get("tool") not in CLUSTER_MUTATING_TOOLS:
            continue
        if any(step.get(marker) for marker in BLOCKED_ACTION_MARKERS):
            continue
        executed_tool_calls.append(step)
    c9 = len(executed_tool_calls) == 1 and c8

    # 10. Actual tool result reports success
    c10 = False
    executed_target_matched = False
    if c9:
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
            namespace_match = str(tool_args.get("namespace", "")).strip() == TARGET_CHAOS_NAMESPACE
            executed_target_matched = kind_match and name_match and namespace_match

    c11 = executed_target_matched

    # 12. No harness repair before verifier
    c12 = harness_repaired_pre_verification is False

    # 13. Bounded settling observations and the authoritative verifier agree.
    verifier_result = incident_result.get("verification", {})
    env_resolved = (
        isinstance(verifier_result, dict)
        and verifier_result.get("env_resolved") is True
        and incident_result.get("env_resolved") is True
    )
    settling_satisfied = _settling_report_satisfied(incident_result)
    if settling_completed is False:
        settling_satisfied = False
    c13 = env_resolved is True and settling_satisfied

    # 14. Comms and its postmortem contain the incident and verified outcome.
    c14 = _comms_evidence_satisfied(incident_result, env_resolved=env_resolved)

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
        "tool_outcomes": remediation_outcomes,
        "settling_satisfied": settling_satisfied,
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


def _chaos_state_observation(result: dict[str, Any]) -> dict[str, Any]:
    """Distinguish a verified empty Chaos list from failed or malformed reads."""
    stdout = result.get("stdout", "") if isinstance(result, dict) else ""
    count = _active_chaos_count(stdout) if isinstance(stdout, str) else -1
    return {
        "count": count,
        "verified_zero": (
            isinstance(result, dict)
            and result.get("success") is True
            and count == 0
        ),
    }


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
    try:
        expected_record = json.loads(json.dumps(incident_result, sort_keys=True))
    except (TypeError, ValueError):
        return False
    return persisted == expected_record


def _persist_stage4_primary_evidence(evidence: dict[str, Any]) -> str:
    evidence_dir = _experiment_evidence_dir(evidence["experiment_id"])
    path = os.path.join(evidence_dir, f"{evidence['experiment_id']}.json")
    if os.path.exists(path):
        raise RuntimeError(f"Refusing to overwrite Stage 4 evidence: {path}")
    _write_json_atomic(path, evidence)
    return path


def _current_main_sha() -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        raise RuntimeError("Unable to verify a clean experiment source tree for Stage 4")
    if status.stdout.strip():
        raise RuntimeError(
            "Stage 4 requires a clean experiment source tree; commit or preserve local changes "
            "before reserving an attempt"
        )
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


def _persist_stage4_preflight_evidence(
    evidence: dict[str, Any],
    chaos_precheck: dict[str, Any],
    *,
    root: str | None = None,
) -> str:
    """Freeze the successful telemetry/workload/zero-Chaos preflight before T0."""
    phases = evidence.get("phases")
    phases = phases if isinstance(phases, dict) else {}
    telemetry = phases.get("telemetry_readiness")
    baseline = phases.get("baseline")
    zero_chaos = _chaos_state_observation(chaos_precheck)
    source_identity = evidence.get("source_identity")
    if (
        not isinstance(telemetry, dict)
        or telemetry.get("ready") is not True
        or not isinstance(baseline, dict)
        or baseline.get("baseline_healthy") is not True
        or not zero_chaos["verified_zero"]
        or not isinstance(source_identity, dict)
        or source_identity.get("working_tree_clean") is not True
        or not source_identity.get("git_commit")
        or not source_identity.get("protocol_fingerprint")
    ):
        raise RuntimeError("Refusing to persist incomplete or unverified Stage 4 preflight")

    experiment_id = str(evidence.get("experiment_id") or "")
    if not experiment_id:
        raise RuntimeError("Stage 4 preflight is missing an experiment ID")
    path = os.path.join(
        _experiment_evidence_dir(experiment_id, root),
        f"{experiment_id}.preflight.json",
    )
    if os.path.exists(path):
        raise RuntimeError(f"Refusing to overwrite Stage 4 preflight evidence: {path}")
    record = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "scenario_id": evidence.get("scenario_id"),
        "persisted_at": datetime.now(UTC).isoformat(),
        "source_identity": source_identity,
        "protocol_marker": evidence.get("protocol_marker"),
        "protocol_profile": evidence.get("protocol_profile"),
        "telemetry_readiness": telemetry,
        "baseline": baseline,
        "zero_chaos_preflight": {
            **zero_chaos,
            "result": chaos_precheck,
        },
    }
    _write_json_atomic(path, record)
    evidence["preflight_evidence"] = {
        "path": os.path.relpath(path, root or REPO_ROOT).replace(os.sep, "/"),
        "sha256": file_sha256(Path(path)),
        "persisted_before_injection": True,
    }
    return path


def reconcile_stage4_postflight_cleanup(
    experiment_id: str,
    *,
    root: str | None = None,
    max_attempts: int = MAX_POSTFLIGHT_CLEANUP_ATTEMPTS,
    timing: str = "after_verdict_persisted",
    observed_leftover_chaos_sha256: str | None = None,
) -> dict[str, Any]:
    """Boundedly remove the injected Chaos object and verify cluster-wide zero."""
    if not 1 <= max_attempts <= MAX_POSTFLIGHT_CLEANUP_ATTEMPTS:
        raise ValueError(
            f"max_attempts must be between 1 and {MAX_POSTFLIGHT_CLEANUP_ATTEMPTS}"
        )
    evidence_dir = _experiment_evidence_dir(experiment_id, root)
    cleanup_path = os.path.join(evidence_dir, f"{experiment_id}.cleanup.json")
    if os.path.exists(cleanup_path):
        raise RuntimeError(f"Refusing to overwrite Stage 4 cleanup evidence: {cleanup_path}")

    attempts: list[dict[str, Any]] = []
    verified_zero = False
    for attempt_number in range(1, max_attempts + 1):
        started_at = datetime.now(UTC).isoformat()
        try:
            delete_result = run_kubectl(
                [
                    "delete",
                    TARGET_CHAOS_KIND.lower(),
                    TARGET_CHAOS_NAME,
                    "-n",
                    TARGET_CHAOS_NAMESPACE,
                    "--ignore-not-found=true",
                ]
            )
        except Exception as exc:  # noqa: BLE001
            delete_result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        time.sleep(POSTFLIGHT_CLEANUP_RETRY_INTERVAL_SECONDS)
        try:
            observation_result = run_kubectl(
                ["get", CHAOS_RESOURCE_KINDS, "-A", "-o", "json"]
            )
        except Exception as exc:  # noqa: BLE001
            observation_result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        observation = _chaos_state_observation(observation_result)
        attempts.append(
            {
                "attempt": attempt_number,
                "started_at": started_at,
                "completed_at": datetime.now(UTC).isoformat(),
                "delete_result": delete_result,
                "postflight_result": observation_result,
                "active_chaos_count": observation["count"],
                "verified_zero_chaos": observation["verified_zero"],
            }
        )
        if observation["verified_zero"]:
            verified_zero = True
            break

    cleanup_record: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "timing": timing,
        "affects_env_resolved": False,
        "verdict_preserved": True,
        "max_attempts": max_attempts,
        "attempts": attempts,
        "result": attempts[-1]["delete_result"] if attempts else None,
        "observed_leftover_chaos_sha256": observed_leftover_chaos_sha256,
        "verified_zero_chaos": verified_zero,
        "poisoned_environment": not verified_zero,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if not verified_zero:
        poison_path = _poisoned_environment_path(root)
        poison_record = {
            "schema_version": 1,
            "experiment_id": experiment_id,
            "marked_at": datetime.now(UTC).isoformat(),
            "reason": "zero-Chaos postflight could not be verified after bounded cleanup",
            "cleanup_record": os.path.relpath(cleanup_path, root or REPO_ROOT).replace(os.sep, "/"),
            "attempts": attempts,
        }
        if not os.path.exists(poison_path):
            _write_json_atomic(poison_path, poison_record)
        cleanup_record["poisoned_environment_record"] = os.path.relpath(
            poison_path, root or REPO_ROOT
        ).replace(os.sep, "/")
    _write_json_atomic(cleanup_path, cleanup_record)
    return {
        **cleanup_record,
        "cleanup_record_path": os.path.relpath(cleanup_path, root or REPO_ROOT).replace(os.sep, "/"),
    }


def _handle_post_t0_interruption(
    *,
    reservation: dict[str, Any],
    evidence: dict[str, Any],
    exc: Exception,
    fault_observable: bool,
    evidence_dir: str,
) -> None:
    """Safely persist forensic interruption evidence and perform cleanup after T0 crash."""
    experiment_id = reservation.get("experiment_id") or EXPERIMENT_ID
    attempt_file = _attempt_marker_path(experiment_id, REPO_ROOT)
    attempt_data = _read_json_file(attempt_file) or {}
    attempt_state_observed = attempt_data.get("state", ATTEMPT_STATE_CONSUMED)
    attempt_sha = file_sha256(Path(attempt_file)) if os.path.exists(attempt_file) else None

    # Check if a durable primary evidence file already exists on disk
    primary_file = os.path.join(evidence_dir, f"{experiment_id}.json")
    primary_record: dict[str, Any] | None = None
    has_durable_primary = False
    if os.path.exists(primary_file):
        try:
            with open(primary_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and "gate_g4_pass" in loaded:
                primary_record = loaded
                has_durable_primary = True
        except Exception:  # noqa: BLE001
            has_durable_primary = False

    # Check for active leftover chaos in cluster
    leftover_chaos_file = os.path.join(evidence_dir, f"{experiment_id}.leftover-chaos.yaml")
    leftover_sha = None
    chaos_get = run_kubectl(["get", TARGET_CHAOS_KIND.lower(), TARGET_CHAOS_NAME, "-n", TARGET_CHAOS_NAMESPACE, "-o", "yaml"])
    if chaos_get.get("success") and chaos_get.get("stdout"):
        stdout_text = str(chaos_get["stdout"])
        if "apiVersion" in stdout_text and TARGET_CHAOS_NAME in stdout_text:
            if not os.path.exists(leftover_chaos_file):
                with open(leftover_chaos_file, "w", encoding="utf-8") as f:
                    f.write(stdout_text)
            leftover_sha = file_sha256(Path(leftover_chaos_file))

    # Build evidence hashes
    evidence_hashes: dict[str, Any] = {}
    if attempt_sha:
        evidence_hashes["attempt_json"] = {
            "path": f"artifacts/evidence/stage4/.attempts/{experiment_id}.attempt.json",
            "sha256": attempt_sha,
        }
    if leftover_sha:
        evidence_hashes["leftover_chaos_yaml"] = {
            "path": f"artifacts/evidence/stage4/{experiment_id}.leftover-chaos.yaml",
            "sha256": leftover_sha,
        }
    if has_durable_primary:
        evidence_hashes["primary_evidence_json"] = {
            "path": f"artifacts/evidence/stage4/{experiment_id}.json",
            "sha256": file_sha256(Path(primary_file)),
        }

    # Determine phase-aware classification and scientific status
    if has_durable_primary and primary_record is not None:
        classification = "POST_VERDICT_OPERATIONAL_INTERRUPTION"
        scientific_status = "VERDICT_ALREADY_FROZEN"
        gate_g4_pass = primary_record.get("gate_g4_pass")
        env_resolved = primary_record.get("env_resolved")
        if env_resolved is None:
            env_resolved = primary_record.get("phases", {}).get("verification", {}).get("env_resolved")
        verifier_completed = bool(primary_record.get("phases", {}).get("verification"))
        model_capability_failure = primary_record.get("model_capability_failure", None)
        root_cause_summary = (
            f"Primary verdict is frozen and authoritative (gate_g4_pass={gate_g4_pass}); "
            f"post-verdict operational exception occurred: {type(exc).__name__}: {exc!s}"
        )
        extra_fields: dict[str, Any] = {
            "primary_verdict_authoritative": True,
            "primary_verdict_persisted": True,
            "attempt_state_observed": attempt_state_observed,
        }
    else:
        in_memory_phases = evidence.get("phases") if isinstance(evidence, dict) else {}
        in_memory_verifier = (in_memory_phases or {}).get("verification")
        verifier_completed = bool(
            in_memory_verifier and in_memory_verifier.get("verification_report")
        )
        is_timeout = (
            isinstance(exc, (httpx.TimeoutException, TimeoutError))
            or "ReadTimeout" in type(exc).__name__
            or "Timeout" in type(exc).__name__
        )
        classification = (
            "MODEL_TIMEOUT_INTERRUPTION"
            if is_timeout
            else "POST_T0_EXECUTION_INTERRUPTION"
        )
        scientific_status = "INCONCLUSIVE"
        gate_g4_pass = None
        env_resolved = None
        model_capability_failure = False
        root_cause_summary = f"Unhandled {type(exc).__name__} after T0 fault injection: {exc!s}"
        extra_fields = {
            "primary_verdict_authoritative": False,
            "primary_verdict_persisted": False,
            "attempt_state_observed": attempt_state_observed,
        }
        if verifier_completed and in_memory_verifier:
            extra_fields["in_memory_verifier_summary"] = {
                "env_resolved": in_memory_verifier.get("env_resolved"),
                "agent_claimed_resolved": in_memory_verifier.get("agent_claimed_resolved"),
            }

    interruption_record = {
        "classification": classification,
        "scientific_status": scientific_status,
        "attempt_state": attempt_state_observed,
        "gate_g4_pass": gate_g4_pass,
        "env_resolved": env_resolved,
        "verifier_completed": verifier_completed,
        "model_capability_failure": model_capability_failure,
        "experiment_id": experiment_id,
        "main_sha": reservation.get("main_sha", ""),
        "protocol_fingerprint": reservation.get("protocol_fingerprint", ""),
        "protocol_marker": reservation.get("protocol_marker", ""),
        "reservation_timestamp": reservation.get("reserved_at", ""),
        "consumed_timestamp": reservation.get("consumed_at", ""),
        "interruption_timestamp": datetime.now(UTC).isoformat(),
        "observed_exception_class": type(exc).__name__,
        "observed_exception_message": str(exc),
        "t0_crossed": True,
        "fault_observed_in_cluster": fault_observable,
        "evidence_hashes": evidence_hashes,
        "root_cause_summary": root_cause_summary,
        **extra_fields,
    }
    interruption_file = os.path.join(evidence_dir, f"{experiment_id}.interruption.json")
    if not os.path.exists(interruption_file):
        _write_json_atomic(interruption_file, interruption_record)
        print(f"  Persisted interruption record (sidecar): {interruption_file}")

    cleanup_timing = (
        "after_post_verdict_interruption"
        if has_durable_primary
        else "after_post_t0_interruption"
    )
    cleanup_record = reconcile_stage4_postflight_cleanup(
        experiment_id,
        root=REPO_ROOT,
        timing=cleanup_timing,
        observed_leftover_chaos_sha256=leftover_sha,
    )
    print(f"  Persisted safety cleanup record (sidecar): {cleanup_record['cleanup_record_path']}")


async def main() -> dict[str, Any]:
    fault_crossed = False
    fault_observable = False
    reservation: dict[str, Any] | None = None
    evidence: dict[str, Any] = {}
    evidence_dir = _experiment_evidence_dir(EXPERIMENT_ID)
    print("=" * 80)
    print(f" ATLASOPS STAGE 4 GOLDEN INCIDENT VALIDATION ({EXPERIMENT_ID}) ")
    print(f" Scenario: {SCENARIO_ID} | Model: {SELECTED_STAGE4_AGENT_MODEL} (Ollama Local) ")
    print("=" * 80)

    # Ensure context is kind-atlasops-local
    subprocess.run(  # noqa: ASYNC221
        ["kubectl", "config", "use-context", KIND_CONTEXT],
        capture_output=True,
        check=False,
    )

    os.environ["KUBECONFIG_CONTEXT"] = KIND_CONTEXT
    os.environ["BACKEND"] = "vllm"
    os.environ["VLLM_BASE"] = "http://localhost:11434/v1"
    os.environ["AGENT_MODEL"] = SELECTED_STAGE4_AGENT_MODEL
    os.environ["PROMETHEUS_URL"] = "http://localhost:19090"
    os.environ["ALERTMANAGER_URL"] = "http://localhost:19093"
    os.environ["JAEGER_URL"] = "http://localhost:16686"
    os.environ["ARGOCD_URL"] = "http://localhost:18080"
    os.environ["ARGOCD_VERIFY_TLS"] = "false"

    start_time = datetime.now(UTC).isoformat()
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
        p = subprocess.Popen(  # noqa: ASYNC220
            ["kubectl", "--context", KIND_CONTEXT, "port-forward", f"svc/{svc}", f"{lp}:{rp}", "-n", ns],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pf_procs.append(p)
    time.sleep(3)  # noqa: ASYNC251

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
        evidence["completed_at"] = datetime.now(UTC).isoformat()
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
            "timestamp": datetime.now(UTC).isoformat(),
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
            evidence["completed_at"] = datetime.now(UTC).isoformat()
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
            "timestamp": datetime.now(UTC).isoformat(),
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
            evidence["completed_at"] = datetime.now(UTC).isoformat()
            return evidence

        main_sha = _current_main_sha()
        reservation = reserve_experiment_attempt(
            EXPERIMENT_ID,
            selected_model=SELECTED_STAGE4_AGENT_MODEL,
            main_sha=main_sha,
        )
        evidence["model"] = reservation["protocol_profile"]["model"]["name"]
        evidence["protocol_profile"] = reservation["protocol_profile"]
        evidence["source_identity"] = {
            "git_commit": reservation["main_sha"],
            "working_tree_clean": True,
            "protocol_fingerprint": reservation["protocol_fingerprint"],
        }

        # Require an independently verified empty Chaos list. Never erase
        # unrelated active experiments as part of preflight.
        chaos_precheck = run_kubectl(
            ["get", CHAOS_RESOURCE_KINDS, "-A", "-o", "json"]
        )
        chaos_observation = _chaos_state_observation(chaos_precheck)
        evidence["phases"]["pre_fault_chaos_check"] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "active_chaos_count": chaos_observation["count"],
            "verified_zero": chaos_observation["verified_zero"],
            "result": chaos_precheck,
        }
        if not chaos_observation["verified_zero"]:
            print(
                "  Zero active Chaos resources were not verified "
                f"(observed_count={chaos_observation['count']}); refusing to inject."
            )
            return abort_before_fault("pre_fault_chaos_not_zero")
        preflight_path = _persist_stage4_preflight_evidence(evidence, chaos_precheck)
        print(f"  Persisted clean preflight before injection: {preflight_path}")

        # Phase 2: Inject Fault
        print(f"\n>>> Phase 2: Injecting Fault ({SCENARIO_ID})...")
        manifest_path = os.path.join(REPO_ROOT, "bench", "chaos_manifests", "single_fault", "sf-002.yaml")
        # Treat an apply timeout/error as potentially side-effecting until
        # postflight proves the target resource is absent.
        fault_crossed = True
        inject_res = run_kubectl(["apply", "-f", manifest_path])
        injection_success = inject_res.get("success") is True
        evidence["phases"]["injection"] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "manifest": manifest_path,
            "result": inject_res,
        }
        if not injection_success:
            consume_experiment_attempt(reservation)
            evidence["attempt_state"] = ATTEMPT_STATE_CONSUMED
            evidence["outcome"] = "INVALID"
            evidence["failure_phase"] = "fault_application"
            evidence["completed_at"] = datetime.now(UTC).isoformat()
            evidence["gate_g4_pass"] = False
            primary_path = _persist_stage4_primary_evidence(evidence)
            complete_experiment_attempt(reservation)
            cleanup_record = reconcile_stage4_postflight_cleanup(
                EXPERIMENT_ID,
                timing="after_failed_injection_verdict",
            )
            evidence["postflight_cleanup"] = cleanup_record
            print(
                "  Fault application was unsuccessful or ambiguous; preserved the "
                f"negative record ({primary_path}) and verified cleanup="
                f"{cleanup_record['verified_zero_chaos']}"
            )
            return evidence
        consume_experiment_attempt(reservation)
        evidence["attempt_state"] = ATTEMPT_STATE_CONSUMED
        print(f"  Chaos Mesh injection: {inject_res.get('stdout')}")

        # Phase 3: Observable Fault Verification
        print("\n>>> Phase 3: Verifying Observable Fault in Cluster...")
        time.sleep(4)  # noqa: ASYNC251
        chaos_check = run_kubectl(["get", TARGET_CHAOS_KIND.lower(), "-n", TARGET_CHAOS_NAMESPACE, TARGET_CHAOS_NAME, "-o", "json"])
        fault_observable = chaos_check.get("success") is True
        evidence["phases"]["observable_fault"] = {
            "timestamp": datetime.now(UTC).isoformat(),
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
            "timestamp": datetime.now(UTC).isoformat(),
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
            evidence["completed_at"] = datetime.now(UTC).isoformat()
            primary_path = _persist_stage4_primary_evidence(evidence)
            complete_experiment_attempt(reservation)
            cleanup_record = reconcile_stage4_postflight_cleanup(
                EXPERIMENT_ID,
                timing="after_failure_verdict_persisted",
            )
            evidence["postflight_cleanup"] = cleanup_record
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
                    "startsAt": datetime.now(UTC).isoformat(),
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
                **{
                    marker: step.get(marker, False)
                    for marker in BLOCKED_ACTION_MARKERS
                    if marker in step
                },
            }
            for step in rem_traj
            if isinstance(step, dict) and "tool" in step
        ]

        evidence["phases"]["coordinator_execution"] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "incident_id": incident_result.get("incident_id"),
            "triage": triage_res.get("final"),
            "diagnosis": diagnosis_res.get("final"),
            "approval": incident_result.get("approval"),
            "grounding_validation": incident_result.get("grounding_validation", {}),
            "model_proposed_action": remediation_res.get("final"),
            "executed_tool_actions": executed_tools,
            "remediation_trajectory": rem_traj,
            "target_consistency": incident_result.get("target_consistency"),
            "incident_anchors": incident_result.get("incident_anchors"),
            "environment_observation": incident_result.get("environment_observation"),
            "settling": incident_result.get("settling"),
            "comms": comms_res.get("final"),
        }

        evidence["phases"]["verification"] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "verification_report": verifier_res,
            "env_resolved": incident_result.get("env_resolved", False),
            "agent_claimed_resolved": incident_result.get("agent_claimed_resolved", False),
        }
        evidence["phases"]["settling"] = incident_result.get("settling")

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
            primary_evidence_persisted=primary_evidence_persisted,
        )
        evidence["phases"]["coordinator_execution"]["remediation_tool_outcomes"] = (
            eval_result["tool_outcomes"]
        )

        gate_g4_pass = eval_result["gate_g4_pass"]
        env_resolved = eval_result["env_resolved"]
        duration_s = round(time.time() - t0, 2)

        evidence["duration_seconds"] = duration_s
        evidence["completed_at"] = datetime.now(UTC).isoformat()
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
            raise RuntimeError(
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
        cleanup_record = reconcile_stage4_postflight_cleanup(EXPERIMENT_ID)
        evidence["postflight_cleanup"] = cleanup_record
        print(
            "  Verified zero-Chaos postflight: "
            f"{cleanup_record['verified_zero_chaos']} "
            f"(cleanup record: {cleanup_record['cleanup_record_path']})"
        )

        return evidence

    except Exception as exc:
        if reservation is not None and not fault_crossed:
            release_experiment_reservation(reservation)
        elif reservation is not None and fault_crossed:
            _handle_post_t0_interruption(
                reservation=reservation,
                evidence=evidence,
                exc=exc,
                fault_observable=fault_observable,
                evidence_dir=evidence_dir,
            )
        raise
    finally:
        for p in pf_procs:
            p.terminate()
            p.wait()


if __name__ == "__main__":
    rep = asyncio.run(main())
    if (
        not rep.get("gate_g4_pass")
        or not isinstance(rep.get("postflight_cleanup"), dict)
        or rep["postflight_cleanup"].get("verified_zero_chaos") is not True
    ):
        sys.exit(1)
