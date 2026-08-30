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
# The Remediation prompt selects which tool is called against the cluster, so it
# determines the outcome at least as directly as the Diagnosis prompt does.
# Profiles v2-v3.1 hashed only Diagnosis, leaving remediation-side behaviour able
# to change between attempts without invalidating the declared protocol. v4
# closes that gap.
REMEDIATION_PROMPT_PATH = REPO_ROOT / "agents" / "prompts" / "remediation.md"
SF002_MANIFEST_PATH = REPO_ROOT / "bench" / "chaos_manifests" / "single_fault" / "sf-002.yaml"

G4_V2_PROTOCOL_MARKER = "G4-PLATFORM-HARDENING-2026-08-25"
G4_V2_PROTOCOL_PROFILE_VERSION = "g4-hardening-profile-v2"
APPROVED_G4_V2_MODEL = "qwen2.5:3b-instruct"
APPROVED_G4_V2_MODEL_DIGEST = "357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b"
APPROVED_G4_V2_DIAGNOSIS_PROMPT_SHA256 = "c9af943dff7b9b7b0d39a299a202a6c51bc0ce65c9f8f11680172f58cd457c1b"
APPROVED_G4_V2_TOOL_CONTRACT_SHA256 = "47b309395c327dd3e7ca93d3e5f2c44c93ba35cc7f58bd5ad34c9bc0f6061926"

G4_V3_PROTOCOL_MARKER = "G4-RECOVERY-V3-2026-08-29"
G4_V3_PROTOCOL_PROFILE_VERSION = "g4-recovery-profile-v3"
APPROVED_G4_V3_MODEL = "qwen2.5:7b-instruct"
APPROVED_G4_V3_MODEL_DIGEST = "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
APPROVED_G4_V3_DIAGNOSIS_PROMPT_SHA256 = "c9af943dff7b9b7b0d39a299a202a6c51bc0ce65c9f8f11680172f58cd457c1b"
APPROVED_G4_V3_TOOL_CONTRACT_SHA256 = "cb824284bd9d9eaf5ddf1d57f2ea9f031a2d2863c194dfe94a85b2b31c915ae3"

G4_V31_PROTOCOL_MARKER = "G4-RECOVERY-V3.1-2026-08-29"
G4_V31_PROTOCOL_PROFILE_VERSION = "g4-recovery-profile-v3.1"
APPROVED_G4_V31_MODEL = "qwen2.5:7b-instruct"
APPROVED_G4_V31_MODEL_DIGEST = "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
APPROVED_G4_V31_DIAGNOSIS_PROMPT_SHA256 = "c9af943dff7b9b7b0d39a299a202a6c51bc0ce65c9f8f11680172f58cd457c1b"
APPROVED_G4_V31_TOOL_CONTRACT_SHA256 = "cb824284bd9d9eaf5ddf1d57f2ea9f031a2d2863c194dfe94a85b2b31c915ae3"
APPROVED_G4_V31_LLM_TRANSPORT = {
    "request_timeout_seconds": 300,
    "max_attempts": 2,
    "base_backoff_seconds": 1.5,
}

# ── v4: chaos observability repair ────────────────────────────────────────────
# Runs 001-008 all failed against a goal state no agent could observe. Every
# frozen scenario's success predicate is Chaos Mesh clearance, and
# chaos_stop_experiment requires an exact resource name that no wrapper in the
# v3.1 contract could report. v4 adds the read-only chaos_list_experiments
# wrapper to the remediation role only, leaving the Diagnosis prompt and its
# scenario-neutral discovery contract byte-identical to v2/v3/v3.1 — measured
# root-cause accuracy stays comparable across every protocol version.
G4_V4_PROTOCOL_MARKER = "G4-OBSERVABILITY-V4-2026-08-30"
G4_V4_PROTOCOL_PROFILE_VERSION = "g4-observability-profile-v4"
APPROVED_G4_V4_MODEL = "qwen2.5:7b-instruct"
APPROVED_G4_V4_MODEL_DIGEST = "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
APPROVED_G4_V4_DIAGNOSIS_PROMPT_SHA256 = (
    "c9af943dff7b9b7b0d39a299a202a6c51bc0ce65c9f8f11680172f58cd457c1b"
)
APPROVED_G4_V4_TOOL_CONTRACT_SHA256 = (
    "30fa4cb43a47c40d26b9ff6f83f712563f936cbd8eb0c5c798bad4f5f5e4da4a"
)
# v4 is the first profile to pin the Remediation prompt. Profiles v2-v3.1 did
# not, so remediation-side behaviour could change between attempts without
# invalidating the declared protocol.
APPROVED_G4_V4_REMEDIATION_PROMPT_SHA256 = (
    "313c5d753c48628b268d7ffcdbf1c90046acd731e67763f39ab8504e455b8a21"
)
APPROVED_G4_V4_LLM_TRANSPORT = dict(APPROVED_G4_V31_LLM_TRANSPORT)

# v4-3b: the same v4 contract pinned to the 3B model.
#
# Runs 005-008 all used qwen2.5:3b-instruct. Re-running v4 on that identical
# model binary (digest 357c53fb659c…, byte-identical to the one v2 declared)
# makes the tool contract the ONLY difference from run 008, so a different
# outcome is attributable to the repair rather than to model capacity. It is
# also the configuration the platform-hardening review recommended for a
# ~16 GB host, where a 7B model alongside the full Kind stack risks swapping.
G4_V4_3B_PROTOCOL_MARKER = "G4-OBSERVABILITY-V4-3B-2026-08-30"
G4_V4_3B_PROTOCOL_PROFILE_VERSION = "g4-observability-profile-v4-3b"
APPROVED_G4_V4_3B_MODEL = "qwen2.5:3b-instruct"
APPROVED_G4_V4_3B_MODEL_DIGEST = APPROVED_G4_V2_MODEL_DIGEST

# ── v5: bounded agent completions ─────────────────────────────────────────────
# Agent turns carried no max_tokens, only the judge did. In run 010 the triage
# agent generated 11,000+ tokens in a single turn without emitting a stop token,
# hit the 300s request timeout, and retried for another 300s — 600 seconds spent
# on one turn of an incident that completes in 267s when the model terminates
# normally. v5 bounds every agent completion and declares the bound, so runs
# before and after are separately budgeted rather than silently comparable.
G4_V5_PROTOCOL_MARKER = "G4-BOUNDED-COMPLETION-V5-2026-08-30"
G4_V5_PROTOCOL_PROFILE_VERSION = "g4-bounded-completion-profile-v5"
G4_V5_3B_PROTOCOL_MARKER = "G4-BOUNDED-COMPLETION-V5-3B-2026-08-30"
G4_V5_3B_PROTOCOL_PROFILE_VERSION = "g4-bounded-completion-profile-v5-3b"
APPROVED_G4_V5_LLM_TRANSPORT = {
    "request_timeout_seconds": 300,
    "max_attempts": 2,
    "base_backoff_seconds": 1.5,
    "max_completion_tokens": 1024,
}

# ── v6: reserved remediation budget ───────────────────────────────────────────
# v5 bounded completions, which stopped runaway generations but made the model
# take more, shorter turns. Against Ollama's 4096-token sliding context the
# investigating agents lost track and looped, spending all 50 per-incident tool
# calls before Remediation ran; run 012 then had every call refused, including
# read-only chaos discovery, and escalated against a fault it could have fixed.
# v6 reserves part of the budget for the only role that can resolve, and — since
# a limit that decides a run's outcome is part of its protocol — declares the
# whole safety envelope.
G4_V6_PROTOCOL_MARKER = "G4-RESERVED-REMEDIATION-V6-2026-08-30"
G4_V6_PROTOCOL_PROFILE_VERSION = "g4-reserved-remediation-profile-v6"
G4_V6_3B_PROTOCOL_MARKER = "G4-RESERVED-REMEDIATION-V6-3B-2026-08-30"
G4_V6_3B_PROTOCOL_PROFILE_VERSION = "g4-reserved-remediation-profile-v6-3b"
APPROVED_G4_V6_SAFETY_ENVELOPE = {
    "max_tool_calls_per_incident": 50,
    "reserved_remediation_tool_calls": 12,
    "max_cluster_mutating_actions_per_hour": 10,
}

# Active approved protocol declaration (defaults to prospective v6 profile)
G4_PROTOCOL_MARKER = G4_V6_PROTOCOL_MARKER
G4_PROTOCOL_PROFILE_VERSION = G4_V6_PROTOCOL_PROFILE_VERSION
APPROVED_G4_MODEL = APPROVED_G4_V4_MODEL
APPROVED_G4_MODEL_DIGEST = APPROVED_G4_V4_MODEL_DIGEST
APPROVED_DIAGNOSIS_PROMPT_SHA256 = APPROVED_G4_V4_DIAGNOSIS_PROMPT_SHA256
APPROVED_TOOL_CONTRACT_SHA256 = APPROVED_G4_V4_TOOL_CONTRACT_SHA256
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
    from agents.tools.argocd import response_contract_profile as argocd_response_contract
    from agents.tools import REGISTERED_TOOLS
    from agents.tools.kubectl import response_contract_profile as kubectl_response_contract

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
        "response_contract_profiles": {
            "argocd": argocd_response_contract(),
            "kubectl_top": kubectl_response_contract(),
        },
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


def remediation_prompt_profile() -> dict[str, Any]:
    return {
        "path": "agents/prompts/remediation.md",
        "version": G4_PROTOCOL_PROFILE_VERSION,
        "sha256": file_sha256(REMEDIATION_PROMPT_PATH),
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


def safety_envelope_profile() -> dict[str, Any]:
    """Circuit-breaker limits, which materially shape what an agent can do.

    Gate G4 run 012 failed because triage and diagnosis consumed the whole
    per-incident tool-call budget and remediation was refused every call. A
    limit that can decide the outcome of a run belongs in the declared protocol,
    not in undeclared runtime configuration.
    """
    from agents.circuit_breaker import circuit_breaker

    return {
        "max_tool_calls_per_incident": int(circuit_breaker.max_tool_calls_per_incident),
        "reserved_remediation_tool_calls": int(circuit_breaker.reserved_remediation_tool_calls),
        "max_cluster_mutating_actions_per_hour": int(
            circuit_breaker.max_cluster_mutating_actions_per_hour
        ),
    }


def llm_transport_profile() -> dict[str, Any]:
    from agents.coordinator import (
        LLM_BASE_BACKOFF_SECONDS,
        LLM_MAX_ATTEMPTS,
        LLM_MAX_COMPLETION_TOKENS,
        LLM_REQUEST_TIMEOUT_SECONDS,
    )

    return {
        "request_timeout_seconds": int(LLM_REQUEST_TIMEOUT_SECONDS),
        "max_attempts": int(LLM_MAX_ATTEMPTS),
        "base_backoff_seconds": float(LLM_BASE_BACKOFF_SECONDS),
        "max_completion_tokens": int(LLM_MAX_COMPLETION_TOKENS),
    }


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
    # Marker and version come from the declaration for this model, so the two
    # qualified models carry distinct protocol identities and separate attempt
    # budgets. An unqualified model raises here rather than borrowing an
    # identity it was never approved under.
    declared = approved_profile_for_model(selected_model)
    marker = declared["protocol_marker"]
    version = declared["profile_version"]
    return {
        "protocol_marker": marker,
        "profile_version": version,
        "model": {
            "provider": "ollama-local",
            "name": selected_model,
            "digest": normalized_digest,
        },
        "diagnosis_prompt": {**diagnosis_prompt_profile(), "version": version},
        "remediation_prompt": {**remediation_prompt_profile(), "version": version},
        "role_tool_contract": tool_contract_profile(),
        "llm_transport": llm_transport_profile(),
        "safety_envelope": safety_envelope_profile(),
        "f1_contract": _f1_contract(),
        "scenario_fault_contract": _scenario_fault_contract(),
        "metrics_api": metrics_observation,
    }


APPROVED_G4_V2_PROTOCOL_PROFILE: dict[str, Any] = {
    "protocol_marker": G4_V2_PROTOCOL_MARKER,
    "profile_version": G4_V2_PROTOCOL_PROFILE_VERSION,
    "model": {
        "provider": "ollama-local",
        "name": APPROVED_G4_V2_MODEL,
        "digest": APPROVED_G4_V2_MODEL_DIGEST,
    },
    "diagnosis_prompt": {
        "path": "agents/prompts/diagnosis.md",
        "version": G4_V2_PROTOCOL_PROFILE_VERSION,
        "sha256": APPROVED_G4_V2_DIAGNOSIS_PROMPT_SHA256,
    },
    "role_tool_contract": {
        "version": "g4-role-tool-contract-47b309395c32",
        "sha256": APPROVED_G4_V2_TOOL_CONTRACT_SHA256,
    },
    "f1_contract": _f1_contract(),
    "scenario_fault_contract": _scenario_fault_contract(),
    "metrics_api": {
        **metrics_server_declaration(),
        "live_config_sha256": expected_live_metrics_config_fingerprint(),
    },
}

APPROVED_G4_V3_PROTOCOL_PROFILE: dict[str, Any] = {
    "protocol_marker": G4_V3_PROTOCOL_MARKER,
    "profile_version": G4_V3_PROTOCOL_PROFILE_VERSION,
    "model": {
        "provider": "ollama-local",
        "name": APPROVED_G4_V3_MODEL,
        "digest": APPROVED_G4_V3_MODEL_DIGEST,
    },
    "diagnosis_prompt": {
        "path": "agents/prompts/diagnosis.md",
        "version": G4_V3_PROTOCOL_PROFILE_VERSION,
        "sha256": APPROVED_G4_V3_DIAGNOSIS_PROMPT_SHA256,
    },
    "role_tool_contract": {
        "version": "g4-role-tool-contract-cb824284bd9d",
        "sha256": APPROVED_G4_V3_TOOL_CONTRACT_SHA256,
    },
    "f1_contract": _f1_contract(),
    "scenario_fault_contract": _scenario_fault_contract(),
    "metrics_api": {
        **metrics_server_declaration(),
        "live_config_sha256": expected_live_metrics_config_fingerprint(),
    },
}

APPROVED_G4_V31_PROTOCOL_PROFILE: dict[str, Any] = {
    "protocol_marker": G4_V31_PROTOCOL_MARKER,
    "profile_version": G4_V31_PROTOCOL_PROFILE_VERSION,
    "model": {
        "provider": "ollama-local",
        "name": APPROVED_G4_V31_MODEL,
        "digest": APPROVED_G4_V31_MODEL_DIGEST,
    },
    "diagnosis_prompt": {
        "path": "agents/prompts/diagnosis.md",
        "version": G4_V31_PROTOCOL_PROFILE_VERSION,
        "sha256": APPROVED_G4_V31_DIAGNOSIS_PROMPT_SHA256,
    },
    "role_tool_contract": {
        "version": "g4-role-tool-contract-cb824284bd9d",
        "sha256": APPROVED_G4_V31_TOOL_CONTRACT_SHA256,
    },
    "llm_transport": {
        "request_timeout_seconds": 300,
        "max_attempts": 2,
        "base_backoff_seconds": 1.5,
    },
    "f1_contract": _f1_contract(),
    "scenario_fault_contract": _scenario_fault_contract(),
    "metrics_api": {
        **metrics_server_declaration(),
        "live_config_sha256": expected_live_metrics_config_fingerprint(),
    },
}

APPROVED_G4_V4_PROTOCOL_PROFILE: dict[str, Any] = {
    "protocol_marker": G4_V4_PROTOCOL_MARKER,
    "profile_version": G4_V4_PROTOCOL_PROFILE_VERSION,
    "model": {
        "provider": "ollama-local",
        "name": APPROVED_G4_V4_MODEL,
        "digest": APPROVED_G4_V4_MODEL_DIGEST,
    },
    "diagnosis_prompt": {
        "path": "agents/prompts/diagnosis.md",
        "version": G4_V4_PROTOCOL_PROFILE_VERSION,
        "sha256": APPROVED_G4_V4_DIAGNOSIS_PROMPT_SHA256,
    },
    "remediation_prompt": {
        "path": "agents/prompts/remediation.md",
        "version": G4_V4_PROTOCOL_PROFILE_VERSION,
        "sha256": APPROVED_G4_V4_REMEDIATION_PROMPT_SHA256,
    },
    "role_tool_contract": {
        "version": "g4-role-tool-contract-30fa4cb43a47",
        "sha256": APPROVED_G4_V4_TOOL_CONTRACT_SHA256,
    },
    "llm_transport": dict(APPROVED_G4_V4_LLM_TRANSPORT),
    "f1_contract": _f1_contract(),
    "scenario_fault_contract": _scenario_fault_contract(),
    "metrics_api": {
        **metrics_server_declaration(),
        "live_config_sha256": expected_live_metrics_config_fingerprint(),
    },
}

APPROVED_G4_V4_3B_PROTOCOL_PROFILE: dict[str, Any] = {
    **APPROVED_G4_V4_PROTOCOL_PROFILE,
    "protocol_marker": G4_V4_3B_PROTOCOL_MARKER,
    "profile_version": G4_V4_3B_PROTOCOL_PROFILE_VERSION,
    "model": {
        "provider": "ollama-local",
        "name": APPROVED_G4_V4_3B_MODEL,
        "digest": APPROVED_G4_V4_3B_MODEL_DIGEST,
    },
    "diagnosis_prompt": {
        **APPROVED_G4_V4_PROTOCOL_PROFILE["diagnosis_prompt"],
        "version": G4_V4_3B_PROTOCOL_PROFILE_VERSION,
    },
    "remediation_prompt": {
        **APPROVED_G4_V4_PROTOCOL_PROFILE["remediation_prompt"],
        "version": G4_V4_3B_PROTOCOL_PROFILE_VERSION,
    },
}

def _with_protocol_identity(base: dict[str, Any], marker: str, version: str,
                            transport: dict[str, Any]) -> dict[str, Any]:
    """Derive a profile that differs only in identity and transport."""
    return {
        **base,
        "protocol_marker": marker,
        "profile_version": version,
        "diagnosis_prompt": {**base["diagnosis_prompt"], "version": version},
        "remediation_prompt": {**base["remediation_prompt"], "version": version},
        "llm_transport": dict(transport),
    }


APPROVED_G4_V5_PROTOCOL_PROFILE: dict[str, Any] = _with_protocol_identity(
    APPROVED_G4_V4_PROTOCOL_PROFILE,
    G4_V5_PROTOCOL_MARKER,
    G4_V5_PROTOCOL_PROFILE_VERSION,
    APPROVED_G4_V5_LLM_TRANSPORT,
)

APPROVED_G4_V5_3B_PROTOCOL_PROFILE: dict[str, Any] = _with_protocol_identity(
    APPROVED_G4_V4_3B_PROTOCOL_PROFILE,
    G4_V5_3B_PROTOCOL_MARKER,
    G4_V5_3B_PROTOCOL_PROFILE_VERSION,
    APPROVED_G4_V5_LLM_TRANSPORT,
)

APPROVED_G4_V6_PROTOCOL_PROFILE: dict[str, Any] = {
    **_with_protocol_identity(
        APPROVED_G4_V5_PROTOCOL_PROFILE,
        G4_V6_PROTOCOL_MARKER,
        G4_V6_PROTOCOL_PROFILE_VERSION,
        APPROVED_G4_V5_LLM_TRANSPORT,
    ),
    "safety_envelope": dict(APPROVED_G4_V6_SAFETY_ENVELOPE),
}

APPROVED_G4_V6_3B_PROTOCOL_PROFILE: dict[str, Any] = {
    **_with_protocol_identity(
        APPROVED_G4_V5_3B_PROTOCOL_PROFILE,
        G4_V6_3B_PROTOCOL_MARKER,
        G4_V6_3B_PROTOCOL_PROFILE_VERSION,
        APPROVED_G4_V5_LLM_TRANSPORT,
    ),
    "safety_envelope": dict(APPROVED_G4_V6_SAFETY_ENVELOPE),
}

APPROVED_G4_PROTOCOL_PROFILE: dict[str, Any] = APPROVED_G4_V6_PROTOCOL_PROFILE

# Both qualified models are explicitly declared. Selecting one is a declaration
# lookup, never a relaxation: a model outside this mapping still fails closed.
APPROVED_G4_PROTOCOL_PROFILES_BY_MODEL: dict[str, dict[str, Any]] = {
    APPROVED_G4_V4_MODEL: APPROVED_G4_V6_PROTOCOL_PROFILE,
    APPROVED_G4_V4_3B_MODEL: APPROVED_G4_V6_3B_PROTOCOL_PROFILE,
}


def approved_profile_for_model(selected_model: str) -> dict[str, Any]:
    """Return the declared profile for a qualified model, or fail closed."""
    profile = APPROVED_G4_PROTOCOL_PROFILES_BY_MODEL.get(str(selected_model).strip())
    if profile is None:
        raise RuntimeError(
            f"Stage 4 model '{selected_model}' has no explicitly approved protocol profile "
            f"(qualified: {sorted(APPROVED_G4_PROTOCOL_PROFILES_BY_MODEL)})"
        )
    return profile


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
    # The declaration is selected by the observed model, then compared in full.
    # An unqualified model raises rather than matching anything.
    approved = approved_profile_for_model((observed.get("model") or {}).get("name", ""))
    if observed != approved:
        observed_fingerprint = protocol_fingerprint(observed)
        approved_fingerprint = protocol_fingerprint(approved)
        raise RuntimeError(
            "Stage 4 runtime does not match the explicitly approved protocol profile "
            f"(observed={observed_fingerprint}, approved={approved_fingerprint})"
        )
    return observed
