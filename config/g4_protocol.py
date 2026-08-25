"""Explicitly declared protocol contract for the hardened G4 experiment.

The approved profile is intentionally static. Runtime observations are built
into the same canonical shape and must match it exactly before Stage 4 can
reserve an attempt. A changed prompt, tool schema, model digest, or Metrics API
therefore requires a reviewed declaration change, not merely a new fingerprint.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from agents.tool_policy import (
    AGENT_EXPOSED_TOOLS,
    ADMIN_OR_UNEXPOSED_TOOLS,
    CLUSTER_MUTATING_TOOLS,
    EXTERNAL_COMMUNICATION_TOOLS,
    FILESYSTEM_WRITING_TOOLS,
    HIGH_RISK_UNEXPOSED_TOOLS,
    ROLE_ALLOWED_TOOLS,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DIAGNOSIS_PROMPT_PATH = REPO_ROOT / "agents" / "prompts" / "diagnosis.md"
SF002_MANIFEST_PATH = REPO_ROOT / "bench" / "chaos_manifests" / "single_fault" / "sf-002.yaml"

G4_PROTOCOL_MARKER = "G4-PLATFORM-HARDENING-2026-08-25"
G4_PROTOCOL_PROFILE_VERSION = "g4-hardening-profile-v2"
APPROVED_G4_MODEL = "qwen2.5:3b-instruct"
APPROVED_G4_MODEL_DIGEST = "357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b"
APPROVED_DIAGNOSIS_PROMPT_SHA256 = "c9af943dff7b9b7b0d39a299a202a6c51bc0ce65c9f8f11680172f58cd457c1b"
APPROVED_TOOL_CONTRACT_SHA256 = "52ecf641cb915a3660af1fdf50c4065e388194eaedf0b788a6b0d7480378ecc6"
EXPECTED_METRICS_API_STATE = "required-present"

METRICS_SERVER_CONTEXT = "kind-atlasops-local"
METRICS_SERVER_NAMESPACE = "kube-system"
METRICS_SERVER_NAME = "metrics-server"
METRICS_SERVER_CONTAINER = "metrics-server"
METRICS_SERVER_IMAGE = "registry.k8s.io/metrics-server/metrics-server:v0.7.2"
METRICS_SERVER_SOURCE_COMMIT = "096960107da4a1b2e2ec83b2ac3424248cfc0ad5"
METRICS_SERVER_VERSION = "v0.7.2"
REQUIRED_METRICS_SERVER_ARGS = (
    "--cert-dir=/tmp",
    "--secure-port=10250",
    "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
    "--kubelet-use-node-status-port",
    "--metric-resolution=15s",
    "--kubelet-insecure-tls",
)


def _canonical_hash(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """Hash canonical LF-normalized bytes so checkout style cannot split profiles."""
    content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(content).hexdigest()


def _tool_contract_components() -> dict[str, Any]:
    from agents.coordinator import _TOOL_DESCRIPTIONS, _TOOL_PARAMETER_SCHEMAS
    from agents.tools import REGISTERED_TOOLS

    return {
        "registered_tools": sorted(REGISTERED_TOOLS),
        "role_allowed_tools": {
            role: sorted(tools) for role, tools in ROLE_ALLOWED_TOOLS.items()
        },
        "agent_exposed_tools": sorted(AGENT_EXPOSED_TOOLS),
        "admin_or_unexposed_tools": sorted(ADMIN_OR_UNEXPOSED_TOOLS),
        "high_risk_unexposed_tools": sorted(HIGH_RISK_UNEXPOSED_TOOLS),
        "cluster_mutating_tools": sorted(CLUSTER_MUTATING_TOOLS),
        "external_communication_tools": sorted(EXTERNAL_COMMUNICATION_TOOLS),
        "filesystem_writing_tools": sorted(FILESYSTEM_WRITING_TOOLS),
        "side_effecting_tools": sorted(
            HIGH_RISK_UNEXPOSED_TOOLS
            | CLUSTER_MUTATING_TOOLS
            | EXTERNAL_COMMUNICATION_TOOLS
            | FILESYSTEM_WRITING_TOOLS
        ),
        "tool_parameter_schemas": _TOOL_PARAMETER_SCHEMAS,
        "tool_descriptions": _TOOL_DESCRIPTIONS,
    }


def tool_contract_profile() -> dict[str, Any]:
    components = _tool_contract_components()
    digest = _canonical_hash(components)
    return {"version": f"g4-role-tool-contract-{digest[:12]}", "sha256": digest}


def diagnosis_prompt_profile() -> dict[str, Any]:
    return {
        "path": "agents/prompts/diagnosis.md",
        "version": G4_PROTOCOL_PROFILE_VERSION,
        "sha256": file_sha256(DIAGNOSIS_PROMPT_PATH),
    }


def _f1_contract() -> dict[str, Any]:
    return {
        "version": "pipeline-v1.1-sf002-f1-envelope-2026-08-24",
        "query": 'max(rate(container_cpu_usage_seconds_total{namespace="default",pod=~"paymentservice-.*",container="server"}[2m]))',
        "raw_query": 'container_cpu_usage_seconds_total{namespace="default",pod=~"paymentservice-.*",container="server"}',
        "min_absolute_increase_cores": 0.15,
        "min_ratio": 2.0,
        "observation_timeout_seconds": 150,
        "poll_interval_seconds": 3,
        "telemetry_scrape_interval_seconds": 30,
        "telemetry_required_stable_probes": 2,
        "telemetry_readiness_timeout_seconds": 120,
    }


def _scenario_fault_contract() -> dict[str, Any]:
    return {
        "scenario_id": "single_fault/sf-002",
        "tier": "single_fault",
        "target_service": "paymentservice",
        "target_namespace": "default",
        "chaos_kind": "StressChaos",
        "chaos_name": "sf-002-paymentservice-cpu",
        "chaos_namespace": "chaos-mesh",
        "mode": "one",
        "cpu_workers": 4,
        "cpu_load": 90,
        "duration": "10m",
        "manifest_sha256": file_sha256(SF002_MANIFEST_PATH),
    }


def metrics_server_declaration() -> dict[str, Any]:
    return {
        "required_state": EXPECTED_METRICS_API_STATE,
        "context": METRICS_SERVER_CONTEXT,
        "source_commit": METRICS_SERVER_SOURCE_COMMIT,
        "version": METRICS_SERVER_VERSION,
        "namespace": METRICS_SERVER_NAMESPACE,
        "name": METRICS_SERVER_NAME,
        "container": METRICS_SERVER_CONTAINER,
        "image": METRICS_SERVER_IMAGE,
    }


def _expected_live_metrics_config() -> dict[str, Any]:
    return {
        "namespace": METRICS_SERVER_NAMESPACE,
        "name": METRICS_SERVER_NAME,
        "service_account": "metrics-server",
        "priority_class": "system-cluster-critical",
        "container": METRICS_SERVER_CONTAINER,
        "image": METRICS_SERVER_IMAGE,
        "args": list(REQUIRED_METRICS_SERVER_ARGS),
        "container_port": 10250,
        "port_name": "https",
        "port_protocol": "TCP",
        "cpu_request": "100m",
        "memory_request": "200Mi",
    }


def expected_live_metrics_config_fingerprint() -> str:
    return _canonical_hash(_expected_live_metrics_config())


def build_runtime_protocol_profile(
    *,
    selected_model: str,
    model_digest: str,
    metrics_observation: dict[str, Any],
) -> dict[str, Any]:
    """Build the observed profile in the exact declared canonical shape."""
    normalized_digest = str(model_digest).strip().lower().removeprefix("sha256:")
    if len(normalized_digest) != 64 or any(c not in "0123456789abcdef" for c in normalized_digest):
        raise RuntimeError("Stage 4 model identity has no valid SHA-256 digest")
    return {
        "protocol_marker": G4_PROTOCOL_MARKER,
        "profile_version": G4_PROTOCOL_PROFILE_VERSION,
        "model": {
            "provider": "ollama-local",
            "name": selected_model,
            "digest": normalized_digest,
        },
        "diagnosis_prompt": diagnosis_prompt_profile(),
        "role_tool_contract": tool_contract_profile(),
        "f1_contract": _f1_contract(),
        "scenario_fault_contract": _scenario_fault_contract(),
        "metrics_api": metrics_observation,
    }


APPROVED_G4_PROTOCOL_PROFILE: dict[str, Any] = {
    "protocol_marker": G4_PROTOCOL_MARKER,
    "profile_version": G4_PROTOCOL_PROFILE_VERSION,
    "model": {
        "provider": "ollama-local",
        "name": APPROVED_G4_MODEL,
        "digest": APPROVED_G4_MODEL_DIGEST,
    },
    "diagnosis_prompt": {
        "path": "agents/prompts/diagnosis.md",
        "version": G4_PROTOCOL_PROFILE_VERSION,
        "sha256": APPROVED_DIAGNOSIS_PROMPT_SHA256,
    },
    "role_tool_contract": {
        "version": "g4-role-tool-contract-52ecf641cb91",
        "sha256": APPROVED_TOOL_CONTRACT_SHA256,
    },
    "f1_contract": _f1_contract(),
    "scenario_fault_contract": _scenario_fault_contract(),
    "metrics_api": {
        **metrics_server_declaration(),
        "live_config_sha256": expected_live_metrics_config_fingerprint(),
    },
}


def protocol_fingerprint(profile: dict[str, Any]) -> str:
    return _canonical_hash(profile)


def inspect_metrics_server_deployment(
    kubectl_fn: Callable[[list[str]], dict[str, Any]],
) -> dict[str, Any]:
    """Read only the pinned Deployment and classify its immutable identity."""
    result = kubectl_fn(
        [
            "get", "deployment", METRICS_SERVER_NAME,
            "-n", METRICS_SERVER_NAMESPACE, "-o", "json",
        ]
    )
    if result.get("success") is not True:
        stderr = str(result.get("stderr") or result.get("error") or "")
        if 'deployments.apps "metrics-server" not found' in stderr:
            return {"state": "missing"}
        raise RuntimeError("Unable to determine Metrics API deployment state fail-closed")

    try:
        payload = json.loads(result.get("stdout") or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Metrics API Deployment payload is not valid JSON") from exc
    metadata = payload.get("metadata") or {}
    spec = payload.get("spec") or {}
    template_spec = (spec.get("template") or {}).get("spec") or {}
    containers = template_spec.get("containers") or []
    matching = [c for c in containers if isinstance(c, dict) and c.get("name") == METRICS_SERVER_CONTAINER]
    if metadata.get("name") != METRICS_SERVER_NAME or metadata.get("namespace") != METRICS_SERVER_NAMESPACE:
        raise RuntimeError("Metrics API Deployment namespace/name does not match the approved profile")
    if len(containers) != 1 or len(matching) != 1:
        raise RuntimeError("Metrics API Deployment container topology does not match the approved profile")

    container = matching[0]
    args = [str(item) for item in container.get("args") or []]
    ports = container.get("ports") or []
    requests = ((container.get("resources") or {}).get("requests") or {})
    live_config = {
        "namespace": metadata.get("namespace"),
        "name": metadata.get("name"),
        "service_account": template_spec.get("serviceAccountName"),
        "priority_class": template_spec.get("priorityClassName"),
        "container": container.get("name"),
        "image": container.get("image"),
        "args": args,
        "container_port": ports[0].get("containerPort") if len(ports) == 1 else None,
        "port_name": ports[0].get("name") if len(ports) == 1 else None,
        "port_protocol": ports[0].get("protocol") if len(ports) == 1 else None,
        "cpu_request": requests.get("cpu"),
        "memory_request": requests.get("memory"),
    }
    mismatches = []
    if live_config["service_account"] != "metrics-server":
        mismatches.append("service_account")
    if live_config["priority_class"] != "system-cluster-critical":
        mismatches.append("priority_class")
    if live_config["image"] != METRICS_SERVER_IMAGE:
        mismatches.append("image")
    if args != list(REQUIRED_METRICS_SERVER_ARGS):
        mismatches.append("args")
    if live_config["container_port"] != 10250 or live_config["port_name"] != "https" or live_config["port_protocol"] != "TCP":
        mismatches.append("ports")
    if live_config["cpu_request"] != "100m" or live_config["memory_request"] != "200Mi":
        mismatches.append("resource_requests")
    if live_config != _expected_live_metrics_config() or mismatches:
        raise RuntimeError(
            "Metrics API Deployment provenance mismatch: " + ",".join(mismatches)
        )
    return {
        "state": "present",
        "live_config_sha256": _canonical_hash(live_config),
        **{key: value for key, value in live_config.items() if key != "args"},
        "args": args,
        "declared_source_commit": METRICS_SERVER_SOURCE_COMMIT,
        "declared_version": METRICS_SERVER_VERSION,
    }


def validate_runtime_protocol_profile(observed: dict[str, Any]) -> dict[str, Any]:
    if observed != APPROVED_G4_PROTOCOL_PROFILE:
        observed_fingerprint = protocol_fingerprint(observed)
        approved_fingerprint = protocol_fingerprint(APPROVED_G4_PROTOCOL_PROFILE)
        raise RuntimeError(
            "Stage 4 runtime does not match the explicitly approved protocol profile "
            f"(observed={observed_fingerprint}, approved={approved_fingerprint})"
        )
    return observed
