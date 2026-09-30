"""Build a deterministic, scenario-derived SFT review candidate.

This builder creates simulated wire-format examples only. It never executes
tools, loads a model, starts training, or writes output unless the CLI is run
with an explicit destination.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.approval import approval_mode_for_severity
from agents.coordinator import build_incident_anchors
from config.scenario_catalog import SCENARIO_CATALOG, TRAIN_SPLIT
from training.sft_candidate import (
    CORPUS_VERSION,
    SCHEMA_VERSION,
    candidate_manifest,
    validate_source_revision,
)
from training.sft_provenance import (
    has_redirecting_path_component,
    source_provenance,
)
from training.sft_rendering import SFT_EXAMPLE_FORMAT, SFT_ROLES

CANONICAL_RUNTIME_PROMPT_PLACEHOLDER = "__ATLASOPS_CANONICAL_RUNTIME_PROMPT__"
OUTPUT_CORPUS_NAME = "sft_corpus_train.jsonl"
OUTPUT_MANIFEST_NAME = "sft_corpus_manifest.json"
_CHAOS_KINDS = frozenset(
    {"PodChaos", "StressChaos", "NetworkChaos", "DNSChaos", "IOChaos", "TimeChaos"}
)
_TRIAGE_TOOL_PLANS: dict[int, tuple[str, ...]] = {
    0: ("kubectl_get", "alertmanager_list_alerts"),
    1: ("kubectl_top_pods", "promql_query"),
    2: ("promql_query", "kubectl_top_pods", "alertmanager_list_alerts"),
    3: ("promql_query", "alertmanager_list_alerts"),
    4: ("alertmanager_list_alerts", "kubectl_get", "promql_query"),
    5: ("promql_query", "kubectl_get", "alertmanager_list_alerts"),
    6: ("alertmanager_list_alerts", "promql_query", "kubectl_get"),
    7: ("kubectl_top_pods", "alertmanager_list_alerts", "promql_query"),
    8: ("alertmanager_list_alerts", "kubectl_top_pods", "promql_query", "kubectl_get"),
    9: ("alertmanager_list_alerts",),
    10: ("alertmanager_list_alerts", "promql_query", "kubectl_get"),
    11: ("alertmanager_list_alerts", "kubectl_top_pods", "promql_query"),
    12: ("kubectl_get", "alertmanager_list_alerts", "promql_query"),
    13: ("kubectl_get", "kubectl_top_pods", "alertmanager_list_alerts"),
    14: ("alertmanager_list_alerts", "promql_query", "kubectl_get"),
    15: ("alertmanager_list_alerts", "kubectl_top_pods", "kubectl_get", "promql_query"),
}


@dataclass(frozen=True)
class CaseRecipe:
    key: str
    severity: str
    approval_status: str
    outcome: str
    evidence_profile: str
    verifier_resolved: bool | None = None
    comms_delivery: str = "delivered"


# Indices address TRAIN_SPLIT only. The single extra CPU case is a controlled
# contrast: same scenario and proposed action, distinct simulated evidence and
# tool/verifier result.
_CASE_RECIPES: dict[int, tuple[CaseRecipe, ...]] = {
    0: (
        CaseRecipe(
            "approved-p1-recovery",
            "P1",
            "approved",
            "successful",
            "pod-availability",
            verifier_resolved=True,
        ),
    ),
    1: (
        CaseRecipe(
            "cpu-action-denied",
            "P2",
            "auto",
            "failed",
            "cpu-persistent",
            comms_delivery="external-failure",
        ),
        CaseRecipe(
            "cpu-verified-recovery",
            "P2",
            "auto",
            "successful",
            "cpu-easing",
            verifier_resolved=True,
        ),
    ),
    2: (
        CaseRecipe(
            "memory-pressure-unresolved",
            "P2",
            "auto",
            "unresolved",
            "memory-persistent",
            verifier_resolved=False,
        ),
    ),
    3: (
        CaseRecipe(
            "network-evidence-inconclusive",
            "P2",
            "auto",
            "inconclusive",
            "no-series",
        ),
    ),
    4: (
        CaseRecipe("p1-rejected", "P1", "rejected", "blocked", "network-denied"),
    ),
    5: (
        CaseRecipe("p1-timeout", "P1", "timeout", "blocked", "network-timeout"),
    ),
    6: (
        CaseRecipe("p1-approval-missing", "P1", "missing", "blocked", "resource-missing"),
    ),
    7: (
        CaseRecipe("p1-approval-malformed", "P1", "malformed", "blocked", "dual-fault"),
    ),
    8: (
        CaseRecipe("p0-manual-only", "P0", "manual", "blocked", "multi-target-manual"),
    ),
    9: (
        CaseRecipe("malformed-observation", "P2", "auto", "malformed", "malformed"),
    ),
    10: (
        CaseRecipe(
            "verified-network-recovery",
            "P2",
            "auto",
            "successful",
            "network-recovered",
            verifier_resolved=True,
        ),
    ),
    11: (
        CaseRecipe(
            "approved-proxy-recovery",
            "P1",
            "approved",
            "successful",
            "cpu-persistent",
            verifier_resolved=True,
        ),
    ),
    12: (
        CaseRecipe(
            "history-backed-capacity-recovery",
            "P1",
            "approved",
            "successful",
            "capacity-recovered",
            verifier_resolved=True,
        ),
    ),
    13: (
        CaseRecipe(
            "verified-dns-recovery",
            "P2",
            "auto",
            "successful",
            "dns-recovered",
            verifier_resolved=True,
        ),
    ),
    14: (
        CaseRecipe(
            "verified-multifault-recovery",
            "P2",
            "auto",
            "successful",
            "multifault-recovered",
            verifier_resolved=True,
        ),
    ),
    15: (
        CaseRecipe(
            "verified-database-recovery",
            "P2",
            "auto",
            "successful",
            "database-recovered",
            verifier_resolved=True,
        ),
    ),
}

CONSTRUCTION_CONFIG: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "corpus_version": CORPUS_VERSION,
    "train_only": True,
    "scenario_metadata_fields_used": [
        "tier",
        "target_services",
        "chaos_kinds",
    ],
    "scenario_metadata_fields_excluded": [
        "expected_alert",
        "expected_root_cause",
        "verification_workloads",
        "manifest_sha256",
    ],
    "recipe_indices_address_train_split_only": True,
    "case_groups": 17,
    "roles_per_case": list(SFT_ROLES),
    "tool_observations_are_simulated": True,
    "tool_execution": False,
    "model_loading": False,
    "training": False,
    "outcome_semantics": "current_role_stage_disposition",
    "triage_tool_plans": {
        str(index): list(plan) for index, plan in sorted(_TRIAGE_TOOL_PLANS.items())
    },
}

_PROFILE_ERROR_ADJUSTMENT = {
    "cpu-persistent": 0.08,
    "cpu-easing": 0.025,
    "memory-persistent": 0.075,
    "network-denied": 0.05,
    "network-timeout": 0.04,
    "resource-missing": 0.04,
    "dual-fault": 0.07,
    "multi-target-manual": 0.09,
    "network-recovered": 0.025,
    "capacity-recovered": 0.03,
    "pod-recovered": 0.035,
    "dns-recovered": 0.03,
    "multifault-recovered": 0.07,
    "database-recovered": 0.04,
    "pod-availability": 0.05,
    "malformed": 0.06,
}
_PROFILE_WINDOW = {
    "cpu-persistent": "15m",
    "cpu-easing": "5m",
    "memory-persistent": "15m",
    "network-denied": "10m",
    "network-timeout": "10m",
    "resource-missing": "10m",
    "dual-fault": "15m",
    "multi-target-manual": "15m",
    "network-recovered": "5m",
    "capacity-recovered": "5m",
    "pod-recovered": "5m",
    "dns-recovered": "5m",
    "multifault-recovered": "10m",
    "database-recovered": "10m",
    "pod-availability": "5m",
    "no-series": "15m",
    "malformed": "10m",
}


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _case_id(scenario_id: str, recipe: CaseRecipe) -> str:
    value = f"{SCHEMA_VERSION}:{scenario_id}:{recipe.key}".encode("utf-8")
    return f"candidate-{hashlib.sha256(value).hexdigest()[:16]}"


def _incident_id(case_id: str) -> str:
    digest = hashlib.sha256(case_id.encode("ascii")).hexdigest()[:12]
    return f"inc-candidate-{digest}"


def _slug(value: str) -> str:
    pieces = [character.lower() if character.isalnum() else "-" for character in value]
    slug = "".join(pieces).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:48].strip("-") or "service"


def _signal_kind(chaos_kinds: tuple[str, ...], index: int) -> str:
    if not chaos_kinds:
        return "Application"
    return chaos_kinds[min(index, len(chaos_kinds) - 1)]


def _signal_family(meta: Any, recipe: CaseRecipe) -> str:
    if len(meta.chaos_kinds) > 1:
        return "multi_fault"
    kind = _signal_kind(meta.chaos_kinds, 0)
    if kind == "StressChaos":
        return (
            "memory_pressure"
            if recipe.evidence_profile == "memory-persistent"
            else "cpu_pressure"
        )
    if kind == "NetworkChaos":
        return {
            "network-denied": "network_packet_loss",
            "network-timeout": "network_latency",
        }.get(recipe.evidence_profile, "network_errors")
    return {
        "PodChaos": "pod_availability",
        "DNSChaos": "dns_dependency",
        "IOChaos": "storage_pressure",
        "TimeChaos": "clock_skew",
        "Application": "capacity_change",
    }.get(kind, "service_health")


def _alert_name(signal_family: str) -> str:
    return {
        "multi_fault": "MultiSignalServiceDegradation",
        "memory_pressure": "ContainerMemoryPressureHigh",
        "cpu_pressure": "ContainerCpuPressureHigh",
        "network_packet_loss": "ServicePacketLossHigh",
        "network_latency": "ServiceLatencyHigh",
        "network_errors": "ServiceNetworkErrors",
        "pod_availability": "ServiceAvailabilityDegraded",
        "dns_dependency": "ServiceResolutionErrors",
        "storage_pressure": "ServiceStoragePressure",
        "clock_skew": "ServiceClockSkew",
        "capacity_change": "ServiceCapacityReduced",
    }.get(signal_family, "ServiceHealthSignal")


def _metrics(meta: Any, recipe: CaseRecipe) -> list[dict[str, Any]]:
    if recipe.evidence_profile == "no-series":
        return []

    tier_offset = {
        "single_fault": 0.0,
        "cascade": 0.018,
        "multi_fault": 0.032,
        "named_replays": 0.024,
    }.get(meta.tier, 0.0)
    profile_offset = _PROFILE_ERROR_ADJUSTMENT.get(recipe.evidence_profile, 0.03)
    window = _PROFILE_WINDOW.get(recipe.evidence_profile, "10m")
    rows: list[dict[str, Any]] = []
    for index, service in enumerate(meta.target_services):
        kind = _signal_kind(meta.chaos_kinds, index)
        if recipe.severity == "P0":
            error_ratio = 0.98
        elif recipe.severity == "P1":
            error_ratio = round(min(0.35, 0.08 + tier_offset + index * 0.015), 3)
        else:
            error_ratio = round(min(0.045, 0.015 + tier_offset * 0.25 + index * 0.004), 3)
        row: dict[str, Any] = {
            "service": service,
            "window": window,
            "error_ratio": error_ratio,
            "latency_slo_utilization_pct": min(
                99,
                88 + int(tier_offset * 150) + index * 3,
            ),
            "request_rate_per_second": (
                0 if recipe.severity == "P0" else 120 + index * 35
            ),
        }
        if kind == "PodChaos":
            ready = (
                0 if recipe.severity == "P0"
                else 1 if recipe.severity == "P1"
                else 3
            )
            row.update(
                {
                    "ready_replicas": max(0, ready - index),
                    "desired_replicas": 3,
                    "restart_count_in_window": 2 + index,
                }
            )
        elif kind == "StressChaos":
            pressure = (
                100 if recipe.severity == "P0"
                else min(99, 90 + int(profile_offset * 50) + index * 2)
                if recipe.severity == "P1"
                else min(94, 82 + int(profile_offset * 70) + index * 2)
            )
            row.update(
                {
                    "cpu_utilization_pct": pressure,
                    "memory_utilization_pct": min(97, pressure - 7 + index),
                    "container_throttled_seconds": 44 + index * 17,
                }
            )
        elif kind == "NetworkChaos":
            row.update(
                {
                    "packet_loss_pct": (
                        100 if recipe.severity == "P0"
                        else min(95, 62 + index * 5)
                        if recipe.severity == "P1"
                        else min(35, 12 + int(profile_offset * 50) + index * 3)
                    ),
                    "upstream_retry_ratio": (
                        0.99 if recipe.severity == "P0"
                        else round(min(0.8, 0.32 + index * 0.06), 3)
                        if recipe.severity == "P1"
                        else round(min(0.25, 0.08 + index * 0.03), 3)
                    ),
                }
            )
        elif kind == "DNSChaos":
            row.update(
                {
                    "lookup_failure_ratio": (
                        0.98 if recipe.severity == "P0"
                        else round(min(0.7, 0.16 + index * 0.05), 3)
                        if recipe.severity == "P1"
                        else round(min(0.04, 0.015 + index * 0.005), 3)
                    ),
                    "servfail_per_minute": 18 + index * 11,
                }
            )
        elif kind == "IOChaos":
            row.update(
                {
                    "volume_io_wait_pct": (
                        100 if recipe.severity == "P0"
                        else min(95, 75 + index * 4)
                        if recipe.severity == "P1"
                        else min(65, 48 + int(profile_offset * 70) + index * 4)
                    ),
                    "write_latency_ms": 175 + index * 40,
                }
            )
        elif kind == "TimeChaos":
            row.update(
                {
                    "clock_skew_seconds": 42 + index * 9,
                    "authentication_reject_ratio": (
                        0.98 if recipe.severity == "P0"
                        else 0.12 if recipe.severity == "P1"
                        else 0.02
                    ),
                }
            )
        elif kind == "Application":
            ready = 0 if recipe.severity in {"P0", "P1"} else 3
            row.update(
                {
                    "ready_replicas": ready,
                    "desired_replicas": 3,
                    "pending_capacity_requests": 5 + index,
                }
            )
        rows.append(row)
    return rows


def _simulated_alert(
    meta: Any,
    recipe: CaseRecipe,
    incident_id: str,
    metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    primary = meta.target_services[0]
    signal_family = _signal_family(meta, recipe)
    peak_ratio = max((item["error_ratio"] for item in metrics), default=None)
    labels = {
        "alertname": _alert_name(signal_family),
        "service": primary,
        "namespace": "default",
        "severity": "critical" if recipe.severity in {"P0", "P1"} else "warning",
    }
    user_impact = (
        100.0
        if recipe.severity == "P0"
        else min(95.0, 12.0 + max(0, len(meta.target_services) - 1) * 4.0)
        if recipe.severity == "P1"
        else 0.0
    )
    if peak_ratio is None:
        description = (
            "The synthetic query returned no error series for this window. The signal is "
            "unavailable and must not be interpreted as zero errors or healthy state."
        )
    elif recipe.severity == "P0":
        description = (
            "Synthetic observations report a total outage across the critical request path, "
            f"including {peak_ratio:.1%} failed requests. This is explicitly simulated."
        )
    elif recipe.severity == "P1":
        description = (
            f"Scenario-derived synthetic capture reports a {peak_ratio:.1%} error ratio, "
            "above the P1 major-degradation threshold; the alert does not assign a cause."
        )
    else:
        slo_use = metrics[0]["latency_slo_utilization_pct"]
        description = (
            f"Scenario-derived synthetic capture reports a {peak_ratio:.1%} error ratio below "
            f"the customer-impact threshold while latency consumes {slo_use}% of its SLO "
            "budget. The alert does not assign a cause."
        )
    return {
        "commonLabels": labels,
        "alerts": [
            {
                "labels": labels,
                "annotations": {
                    "summary": f"Simulated service health signal for {primary}",
                    "description": description,
                    "signal_family": signal_family,
                    "affected_services": list(meta.target_services),
                    "reported_error_ratio": peak_ratio,
                    "user_impact_pct": user_impact,
                    "revenue_path_affected": recipe.severity in {"P0", "P1"},
                    "measurement_window": _PROFILE_WINDOW.get(
                        recipe.evidence_profile, "10m"
                    ),
                    "trend": {
                        "cpu-persistent": "rising",
                        "cpu-easing": "easing",
                        "network-timeout": "latency-tail",
                        "network-denied": "packet-loss",
                    }.get(recipe.evidence_profile, "current"),
                },
                "status": "firing",
                "incident_id": incident_id,
            }
        ],
    }


def _triage_summary(
    alert: dict[str, Any],
    correlated_alert_names: list[str],
    handoff_notes: str,
) -> dict[str, Any]:
    labels = alert["commonLabels"]
    annotations = alert["alerts"][0]["annotations"]
    services = list(annotations.get("affected_services") or [labels["service"]])
    impact = float(annotations.get("user_impact_pct") or 0.0)
    ratio = annotations.get("reported_error_ratio")
    if impact >= 50:
        severity = "P0"
    elif labels.get("severity") == "critical" or (
        isinstance(ratio, (int, float)) and ratio > 0.05
    ):
        severity = "P1"
    else:
        severity = "P2"
    return {
        "incident_id": alert["alerts"][0]["incident_id"],
        "severity": severity,
        "title": f"{labels['alertname']} on {', '.join(services)}",
        "blast_radius": {
            "services": services,
            "namespaces": [labels.get("namespace", "default")],
            "user_impact_pct": impact,
            "revenue_path_affected": annotations["revenue_path_affected"],
        },
        "correlated_alerts": correlated_alert_names,
        "next_agent": "diagnosis",
        "handoff_notes": handoff_notes,
    }


def _chaos_resources(meta: Any) -> list[dict[str, str]]:
    resources: list[dict[str, str]] = []
    for index, service in enumerate(meta.target_services):
        kind = _signal_kind(meta.chaos_kinds, index)
        if kind not in _CHAOS_KINDS:
            continue
        resources.append(
            {
                "kind": kind,
                "name": f"{_slug(kind)}-{_slug(service)}-exercise",
                "namespace": "chaos-mesh",
                "target_service": service,
                "status": "Running",
            }
        )
    return resources


def _tool_call_id(case_id: str, role: str, index: int, tool_name: str) -> str:
    value = f"{case_id}:{role}:{index}:{tool_name}".encode("utf-8")
    return "call-" + hashlib.sha256(value).hexdigest()[:12]


def _tool_observation(
    messages: list[dict[str, Any]],
    *,
    case_id: str,
    role: str,
    index: int,
    tool_name: str,
    arguments: dict[str, Any],
    output: dict[str, Any],
    reasoning: str,
    call_id: str | None = None,
) -> str:
    call_id = call_id or _tool_call_id(case_id, role, index, tool_name)
    messages.append(
        {
            "role": "assistant",
            "content": reasoning,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": _json_text(arguments),
                    },
                }
            ],
        }
    )
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "content": _json_text({"simulation": True, **output}),
        }
    )
    return call_id


def _base_messages(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "role": "system",
            "content": CANONICAL_RUNTIME_PROMPT_PLACEHOLDER,
        },
        {"role": "user", "content": _json_text(context)},
    ]


def _final_message(messages: list[dict[str, Any]], final: dict[str, Any]) -> None:
    messages.append({"role": "assistant", "content": _json_text(final)})


def _tool_observations_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        for call in message.get("tool_calls") or []:
            response = messages[index + 1]
            observations.append(
                {
                    "tool": call["function"]["name"],
                    "args": json.loads(call["function"]["arguments"]),
                    "output": json.loads(response["content"]),
                }
            )
    return observations


def _safety(recipe: CaseRecipe, incident_id: str) -> dict[str, Any]:
    approval: dict[str, Any] = {
        "status": recipe.approval_status,
        "incident_id": incident_id,
    }
    if recipe.approval_status == "approved":
        approval["approved_by"] = "Simulated Incident Commander"
    return {
        "severity": recipe.severity,
        "approval_mode": approval_mode_for_severity(recipe.severity),
        "approval": approval,
    }


def _runtime_outcome(outcome: str) -> str:
    return {
        "successful": "resolved",
        "failed": "unresolved",
        "unresolved": "unresolved",
        "blocked": "escalated",
        "inconclusive": "escalated",
        "malformed": "escalated",
    }[outcome]


def _make_row(
    *,
    scenario_id: str,
    meta: Any,
    recipe: CaseRecipe,
    case_id: str,
    role: str,
    outcome: str,
    safety: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    row = {
        "format": SFT_EXAMPLE_FORMAT,
        "schema_version": SCHEMA_VERSION,
        "corpus_version": CORPUS_VERSION,
        "scenario_id": scenario_id,
        "role": role,
        "tier": meta.tier,
        "case_id": case_id,
        "outcome": outcome,
        "safety": safety,
        "simulation": True,
        "messages": messages,
    }
    if role in {"remediation", "comms"} and (
        recipe.severity == "P0"
        or (recipe.severity == "P1" and recipe.approval_status != "approved")
    ):
        if role == "remediation" or (
            role == "comms"
            and recipe.severity == "P1"
            and recipe.approval_status != "approved"
        ):
            row["execution_stage"] = "host_gate_not_invoked"
    if role == "remediation" and outcome == "successful":
        row["supervision_source"] = "synthetic_controller_resolution"
    return row


def _triage_query(alert: dict[str, Any]) -> str:
    primary = alert["commonLabels"]["service"]
    signal_family = alert["alerts"][0]["annotations"]["signal_family"]
    if signal_family == "cpu_pressure":
        return (
            "sum by (pod) (rate(container_cpu_usage_seconds_total"
            f'{{namespace="default",pod=~"{primary}-.*"}}[5m]))'
        )
    if signal_family == "memory_pressure":
        return (
            "max by (pod) (container_memory_working_set_bytes"
            f'{{namespace="default",pod=~"{primary}-.*"}})'
        )
    if signal_family == "dns_dependency":
        return (
            "sum by (service) (rate(coredns_dns_responses_total"
            f'{{service="{primary}",rcode="SERVFAIL"}}[5m]))'
        )
    if signal_family == "capacity_change":
        return f'kube_deployment_status_replicas_available{{deployment="{primary}"}}'
    if signal_family == "network_latency":
        return (
            "histogram_quantile(0.95,rate(http_request_duration_seconds_bucket"
            f'{{service="{primary}"}}[5m]))'
        )
    if signal_family == "network_packet_loss":
        return f'network_packet_loss_ratio{{service="{primary}"}}'
    if signal_family == "pod_availability":
        return f'kube_deployment_status_replicas_available{{deployment="{primary}"}}'
    return (
        "sum by (service) (rate(http_requests_total"
        f'{{service="{primary}",status=~"5.."}}[5m]))'
    )


def _triage_tool_payload(
    meta: Any,
    recipe: CaseRecipe,
    alert: dict[str, Any],
    metrics: list[dict[str, Any]],
    tool_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    annotations = alert["alerts"][0]["annotations"]
    if tool_name == "alertmanager_list_alerts":
        related = [
            {
                "labels": {
                    "alertname": f"RelatedServiceSignal{index}",
                    "service": service,
                    "namespace": "default",
                    "severity": alert["commonLabels"]["severity"],
                },
                "annotations": {
                    "summary": f"Simulated correlated signal for {service}"
                },
                "status": "firing",
            }
            for index, service in enumerate(annotations["affected_services"][1:], start=1)
        ]
        observed_alerts = [*alert["alerts"], *related]
        return (
            {"active_only": True},
            {"success": True, "count": len(observed_alerts), "alerts": observed_alerts},
        )
    if tool_name == "kubectl_get":
        items = [
            {
                "name": item["service"],
                "namespace": "default",
                "ready_replicas": item.get("ready_replicas", 2),
                "desired_replicas": item.get("desired_replicas", 3),
            }
            for item in metrics
        ]
        return (
            {"resource": "deployments", "namespace": "default", "output": "json"},
            {"success": True, "items": items},
        )
    if tool_name == "kubectl_top_pods":
        pods = [
            {
                "service": item["service"],
                "pod": f"{_slug(item['service'])}-simulated",
                "namespace": "default",
                "cpu_utilization_pct": item.get("cpu_utilization_pct", 34),
                "memory_utilization_pct": item.get("memory_utilization_pct", 46),
            }
            for item in metrics
        ]
        return (
            {"namespace": "default"},
            {"success": True, "pods": pods},
        )
    query = _triage_query(alert)
    if annotations["reported_error_ratio"] is None:
        output = {"success": True, "evidence_status": "no_series", "series": []}
    else:
        output = {
            "success": True,
            "evidence_status": "observed",
            "signal_family": annotations["signal_family"],
            "series": metrics,
        }
    return {"query": query}, output


def _triage_reasoning(
    tool_name: str,
    alert: dict[str, Any],
    prior_outputs: list[dict[str, Any]],
    *,
    malformed: bool,
) -> str:
    labels = alert["commonLabels"]
    annotations = alert["alerts"][0]["annotations"]
    primary = labels["service"]
    family = annotations["signal_family"]
    if not prior_outputs:
        if malformed:
            return (
                f"The incoming telemetry observation for {primary} is flagged malformed. "
                "I will confirm the firing alert without using the truncated payload."
            )
        return (
            f"The incoming {labels['alertname']} alert anchors {primary} and reports "
            f"{annotations['description']} I will gather the first independent observation."
        )
    prior = prior_outputs[-1]
    output = prior["output"]
    if tool_name == "kubectl_top_pods":
        series = output.get("series") or []
        ratio = next(
            (item.get("error_ratio") for item in series if item.get("service") == primary),
            None,
        )
        qualifier = (
            "no series was returned, so I will not infer health"
            if output.get("evidence_status") == "no_series"
            else f"the observed service error ratio is {ratio:.1%}"
            if isinstance(ratio, (int, float))
            else f"the previous {prior['tool']} response is available"
        )
        return (
            f"The previous observation says {qualifier}. I will compare pod-level CPU and memory "
            f"for {primary} without treating utilization as customer impact."
        )
    if tool_name == "promql_query":
        pods = output.get("pods") or []
        pod = next((item for item in pods if item.get("service") == primary), {})
        if family == "memory_pressure":
            note = f"the prior pod sample shows memory at {pod.get('memory_utilization_pct', 'unavailable')}%"
        elif family == "cpu_pressure":
            note = f"the prior pod sample shows CPU at {pod.get('cpu_utilization_pct', 'unavailable')}%"
        else:
            note = f"the prior observation covers {len(pods)} pod(s)"
        return (
            f"{note}. I will query the signal family named in the incoming alert for {primary} "
            "to compare resource pressure with user-facing symptoms."
        )
    if tool_name == "kubectl_get":
        items = output.get("items") or []
        alerts = output.get("alerts") or []
        if alerts:
            note = f"the alert inventory returned {len(alerts)} firing signal(s)"
        else:
            note = f"the prior metric observation returned {len(items)} deployment row(s)"
        return (
            f"{note}. I will inspect deployment availability for the alert-provided service scope "
            "before recommending any action."
        )
    alerts = output.get("alerts") or []
    rows = output.get("items") or output.get("series") or []
    return (
        f"The preceding {prior['tool']} observation contains {len(alerts) or len(rows)} relevant "
        f"record(s). I will check for correlated alerts around {primary}; correlation alone will "
        "not establish cause."
    )


def _triage_handoff(
    alert: dict[str, Any],
    prior_outputs: list[dict[str, Any]],
    *,
    malformed: bool,
) -> str:
    annotations = alert["alerts"][0]["annotations"]
    primary = alert["commonLabels"]["service"]
    family = annotations["signal_family"]
    series = [
        item
        for observation in prior_outputs
        for item in observation["output"].get("series", [])
    ]
    pods = [
        item
        for observation in prior_outputs
        for item in observation["output"].get("pods", [])
    ]
    items = [
        item
        for observation in prior_outputs
        for item in observation["output"].get("items", [])
    ]
    signal = next(
        (item for item in series + pods + items if item.get("service", item.get("name")) == primary),
        {},
    )
    if malformed:
        return (
            f"The incoming telemetry payload is malformed; retain {primary} and the alert's "
            "severity, but do not use that payload to infer cause or resolution."
        )
    if any(item["output"].get("evidence_status") == "no_series" for item in prior_outputs):
        return (
            f"No series was returned for {primary}; absence is not evidence of zero errors. "
            "Preserve the firing alert and request fresh metric and workload observations."
        )
    if annotations["user_impact_pct"] >= 50:
        return (
            f"The simulated capture indicates a total outage across "
            f"{', '.join(annotations['affected_services'])} "
            "and a broken critical path. Preserve P0 and pass the full scope to diagnosis."
        )
    if family == "memory_pressure":
        return (
            f"{primary} is under memory pressure at {signal.get('memory_utilization_pct', 0)}% "
            f"while modeled user impact remains {annotations['user_impact_pct']:.1f}%. "
            "Keep memory pressure distinct from confirmed customer impact."
        )
    if family == "cpu_pressure":
        return (
            f"{primary} CPU is {signal.get('cpu_utilization_pct', 0)}%; modeled 5xx impact is "
            f"{annotations['reported_error_ratio']:.1%}. Use the observed traffic threshold, "
            "not saturation alone, to preserve severity."
        )
    if family == "dns_dependency":
        return (
            f"The alert scope is {', '.join(annotations['affected_services'])} and the symptom class is resolver "
            "failure. Ask diagnosis to check DNS evidence and dependent workloads separately."
        )
    if family in {"network_packet_loss", "network_latency", "network_errors"}:
        return (
            f"{primary} has a {family.replace('_', ' ')} signal with modeled error ratio "
            f"{annotations['reported_error_ratio']:.1%}. Preserve upstream context before action."
        )
    if family == "pod_availability":
        return (
            f"Readiness on {primary} is {signal.get('ready_replicas', 0)}/"
            f"{signal.get('desired_replicas', 3)} in the synthetic snapshot. Diagnosis should "
            "correlate restarts and impact before selecting a remediation."
        )
    if family == "capacity_change":
        return (
            f"{primary} has {signal.get('ready_replicas', 0)} available replicas. Preserve the "
            "capacity signal and ask diagnosis to inspect change history before rollback."
        )
    return (
        f"The alert spans {', '.join(annotations['affected_services'])}. Keep {primary} as the "
        "original target and hand off the observed evidence without collapsing scope."
    )


def _triage_row(
    *,
    train_index: int,
    scenario_id: str,
    meta: Any,
    recipe: CaseRecipe,
    case_id: str,
    safety: dict[str, Any],
    alert: dict[str, Any],
    metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "simulation": True,
        "incident_id": alert["alerts"][0]["incident_id"],
        "alert": alert,
    }
    if recipe.evidence_profile == "malformed":
        context["incoming_observation"] = {
            "source": "telemetry_collector",
            "raw_payload": '{"series":[{"service":"truncated',
            "parse_status": "malformed",
        }
    messages = _base_messages(context)
    plan = _TRIAGE_TOOL_PLANS[train_index]
    if train_index == 1 and recipe.evidence_profile == "cpu-easing":
        plan = ("promql_query", "kubectl_top_pods")
    correlated_alert_names = [alert["commonLabels"]["alertname"]]
    prior_outputs: list[dict[str, Any]] = []
    malformed = "incoming_observation" in context
    for call_index, tool_name in enumerate(plan):
        arguments, output = _triage_tool_payload(
            meta, recipe, alert, metrics, tool_name
        )
        reasoning = _triage_reasoning(
            tool_name,
            alert,
            prior_outputs,
            malformed=malformed,
        )
        if tool_name == "alertmanager_list_alerts":
            correlated_alert_names = [
                item["labels"]["alertname"] for item in output["alerts"]
            ]
        _tool_observation(
            messages,
            case_id=case_id,
            role="triage",
            index=call_index,
            tool_name=tool_name,
            arguments=arguments,
            output=output,
            reasoning=reasoning,
        )
        prior_outputs.append({"tool": tool_name, "output": output})
    final = _triage_summary(
        alert,
        correlated_alert_names,
        _triage_handoff(alert, prior_outputs, malformed=malformed),
    )
    final["correlated_alerts"] = correlated_alert_names
    final["outcome"] = "malformed" if malformed else "inconclusive"
    _final_message(messages, final)
    return _make_row(
        scenario_id=scenario_id,
        meta=meta,
        recipe=recipe,
        case_id=case_id,
        role="triage",
        outcome=final["outcome"],
        safety=safety,
        messages=messages,
    )


def _diagnosis_observations(
    *,
    case_id: str,
    meta: Any,
    recipe: CaseRecipe,
    alert: dict[str, Any],
    metrics: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    service_pattern = "|".join(meta.target_services)
    window = _PROFILE_WINDOW.get(recipe.evidence_profile, "10m")
    query = (
        "sum by (service) (rate(http_requests_total{service=~"
        f'"{service_pattern}",status=~"5.."}}[{window}]))'
    )
    query_output: dict[str, Any]
    if recipe.evidence_profile == "no-series":
        query_output = {
            "success": True,
            "evidence_status": "no_series",
            "series": [],
        }
    elif recipe.evidence_profile == "malformed":
        query_output = {
            "success": True,
            "evidence_status": "recheck_after_malformed_input",
            "series": [],
        }
    else:
        query_output = {
            "success": True,
            "evidence_status": "observed",
            "window": window,
            "series": metrics,
        }
    results: list[dict[str, Any]] = [
        {
            "tool": "promql_query",
            "args": {"query": query},
            "output": {"simulation": True, **query_output},
        }
    ]
    results.append(
        {
            "tool": "kubectl_get",
            "args": {"resource": "deployments", "namespace": "default", "output": "json"},
            "output": {
                "simulation": True,
                "success": True,
                "evidence_status": (
                    "observed_empty" if recipe.evidence_profile == "malformed" else "observed"
                ),
                "items": [
                    {
                        "name": item["service"],
                        "namespace": "default",
                        "ready_replicas": item.get("ready_replicas", 2),
                        "desired_replicas": item.get("desired_replicas", 3),
                    }
                    for item in metrics
                ],
            },
        }
    )

    resources = _chaos_resources(meta)
    if recipe.evidence_profile in {"no-series", "malformed"}:
        chaos_output = {
            "simulation": True,
            "success": False,
            "observation_status": "unavailable",
            "evidence_status": "observation_unavailable",
            "active_experiments": [],
            "count": 0,
        }
    else:
        chaos_output = {
            "simulation": True,
            "success": True,
            "observation_status": "observed",
            "evidence_status": "active_experiments" if resources else "no_active_experiments",
            "active_experiments": resources,
            "count": len(resources),
        }
    results.append(
        {
            "tool": "chaos_list_experiments",
            "args": {},
            "output": chaos_output,
        }
    )
    if meta.tier in {"cascade", "multi_fault"}:
        primary = meta.target_services[0]
        results.append(
            {
                "tool": "jaeger_search",
                "args": {
                    "service": primary,
                    "lookback": window,
                    "limit": 5,
                    "min_duration": "250ms",
                },
                "output": {
                    "simulation": True,
                    "success": recipe.evidence_profile != "no-series",
                    "evidence_status": (
                        "unavailable" if recipe.evidence_profile == "no-series" else "observed"
                    ),
                    "traces": (
                        []
                        if recipe.evidence_profile == "no-series"
                        else [
                            {
                                "trace_id": f"trace-sim-{_slug(primary)}-1",
                                "service": primary,
                                "duration_ms": 680 + len(meta.target_services) * 115,
                                "error": True,
                                "longest_span_service": meta.target_services[-1],
                                "span_kind": "client",
                            }
                        ]
                    ),
                },
            }
        )
    if "DNSChaos" in meta.chaos_kinds:
        primary = meta.target_services[0]
        results.append(
            {
                "tool": "kubectl_logs",
                "args": {
                    "pod": f"{_slug(primary)}-7c49f8d5",
                    "namespace": "default",
                    "tail": 120,
                },
                "output": {
                    "simulation": True,
                    "success": True,
                    "lines": [
                        "upstream lookup retry exhausted for the configured service name",
                        "resolver returned SERVFAIL; application request was not retried",
                    ],
                },
            }
        )
    if recipe.key == "history-backed-capacity-recovery":
        primary = meta.target_services[0]
        results.append(
            {
                "tool": "argocd_app_history",
                "args": {"app": primary},
                "output": {
                    "simulation": True,
                    "success": True,
                    "history": [
                        {
                            "id": 42,
                            "revision": "42",
                            "summary": "deployment capacity reduced to zero",
                        },
                        {
                            "id": 41,
                            "revision": "41",
                            "summary": "previous capacity-bearing deployment",
                        },
                    ],
                },
            }
        )
    return results, resources


def _diagnosis_row(
    *,
    scenario_id: str,
    meta: Any,
    recipe: CaseRecipe,
    case_id: str,
    safety: dict[str, Any],
    alert: dict[str, Any],
    metrics: list[dict[str, Any]],
    triage: dict[str, Any],
    triage_observations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    diagnosis_observations, resources = _diagnosis_observations(
        case_id=case_id,
        meta=meta,
        recipe=recipe,
        alert=alert,
        metrics=metrics,
    )
    context: dict[str, Any] = {
        "simulation": True,
        "incident_id": alert["alerts"][0]["incident_id"],
        "alert": alert,
        "incident_anchors": build_incident_anchors(alert),
        "triage": triage,
        "triage_observations": triage_observations,
    }
    if recipe.evidence_profile == "malformed":
        context["incoming_observation"] = {
            "source": "telemetry_collector",
            "raw_payload": '{"series":[{"service":"truncated',
            "parse_status": "malformed",
        }
    messages = _base_messages(context)
    primary = meta.target_services[0]
    query_record = diagnosis_observations[0]
    _tool_observation(
        messages,
        case_id=case_id,
        role="diagnosis",
        index=0,
        tool_name="promql_query",
        arguments=query_record["args"],
        output=query_record["output"],
        reasoning=(
            f"The handoff preserves {primary} as the alert-anchored service. I will compare "
            f"the {_PROFILE_WINDOW.get(recipe.evidence_profile, '10m')} error series across "
            f"{', '.join(meta.target_services)}; absence of a series will remain missing evidence."
        ),
    )
    workload_record = diagnosis_observations[1]
    _tool_observation(
        messages,
        case_id=case_id,
        role="diagnosis",
        index=1,
        tool_name="kubectl_get",
        arguments=workload_record["args"],
        output=(
            workload_record["output"]
            if isinstance(workload_record["output"], dict)
            else workload_record["output"]
        ),
        reasoning=(
            f"The metric response alone cannot establish the failing component. I am checking "
            f"deployment readiness for the alert scope, especially {primary}, before recommending "
            "any change."
        ),
    )
    for call_index, record in enumerate(diagnosis_observations[2:], start=2):
        tool_name = record["tool"]
        if tool_name == "chaos_list_experiments":
            reasoning = (
                "I am checking the read-only experiment inventory. I will report only the "
                "resource identities returned by this simulated observation."
            )
        elif tool_name == "jaeger_search":
            reasoning = (
                "The service-level metrics do not establish where a request stalls. I am "
                "checking sampled spans for a specific slow dependency boundary."
            )
        elif tool_name == "kubectl_logs":
            reasoning = (
                "The DNS failure ratio is not enough to identify the failing hop. I am "
                "checking workload logs for the resolver response and retry behavior."
            )
        else:
            reasoning = (
                "A capacity-changing rollback is only justified if the application history "
                "contains a concrete prior revision and a relevant change."
            )
        _tool_observation(
            messages,
            case_id=case_id,
            role="diagnosis",
            index=call_index,
            tool_name=tool_name,
            arguments=record["args"],
            output=record["output"],
            reasoning=reasoning,
        )

    if recipe.evidence_profile == "malformed":
        category = "unknown"
        specific = (
            f"The incoming workload observation for {primary} is truncated and cannot be "
            "parsed as an object. A separate simulated query did not repair that source record, "
            "so the cause remains unknown and no remediation should be inferred."
        )
        diagnosis_outcome = "malformed"
        recommendations: list[dict[str, Any]] = []
    elif recipe.evidence_profile == "no-series":
        category = "unknown"
        specific = (
            f"No PromQL series was returned for {primary}, and the experiment inventory was "
            "unavailable. Neither absence proves health nor supports a causal attribution; "
            "collect fresh metrics and resource observations before proposing a mutation."
        )
        diagnosis_outcome = "inconclusive"
        recommendations = []
    elif recipe.key == "history-backed-capacity-recovery":
        category = "config"
        specific = (
            f"The simulated deployment snapshot shows no ready replicas for {meta.target_services[0]}; "
            "Argo CD history contains revision 42 with capacity reduced to zero and revision 41 "
            "with prior capacity. The history supports a specific rollback candidate, contingent "
            "on the P1 approval gate and post-action verification."
        )
        diagnosis_outcome = "unresolved"
        recommendations = [
            {
                "action": "argocd_rollback",
                "target": meta.target_services[0],
                "revision": "41",
                "evidence": "matching application history includes the prior capacity-bearing revision",
            }
        ]
    else:
        kind = _signal_kind(meta.chaos_kinds, 0)
        primary_series = next(
            (item for item in metrics if item["service"] == primary),
            {},
        )
        if recipe.severity == "P0":
            impact_note = (
                "The synthetic request sample shows a total outage, with 100% estimated user "
                "impact on the critical request path."
            )
        elif recipe.severity == "P1":
            impact_note = (
                f"The synthetic error ratio is {primary_series.get('error_ratio', 0):.1%}, "
                "above the P1 major-degradation threshold."
            )
        else:
            impact_note = (
                f"The synthetic error ratio is {primary_series.get('error_ratio', 0):.1%} with "
                "no current user impact; latency or resource headroom remains an SLO risk."
            )
        category = {
            "PodChaos": "resource",
            "StressChaos": "resource",
            "NetworkChaos": "network",
            "DNSChaos": "dependency",
            "IOChaos": "resource",
            "TimeChaos": "config",
            "Application": "resource",
        }.get(kind, "unknown")
        if resources:
            resource = resources[0]
            if any(item["tool"] == "jaeger_search" for item in diagnosis_observations):
                trace = next(
                    item for item in diagnosis_observations
                    if item["tool"] == "jaeger_search"
                )["output"]["traces"][0]
                detail = (
                    f"Jaeger returned a simulated {trace['duration_ms']}ms trace whose longest "
                    f"client span is {trace['longest_span_service']}."
                )
            elif any(item["tool"] == "kubectl_logs" for item in diagnosis_observations):
                log_output = next(
                    item for item in diagnosis_observations
                    if item["tool"] == "kubectl_logs"
                )["output"]
                detail = (
                    f"The simulated workload log records {log_output['lines'][-1]}."
                )
            else:
                detail = (
                    f"The read-only experiment inventory independently contains an active "
                    f"{resource['kind']} resource scoped to that workload."
                )
            specific = (
                f"{impact_note} Readiness and metric observations keep {primary} as the "
                f"alert-anchored target. {detail} The matching experiment is a supported "
                "candidate cause, not proof of recovery."
            )
            recommendations = [
                {
                    "action": "stop_observed_experiment",
                    "target": primary,
                    "evidence": "matching active experiment plus alert-anchored workload impact",
                }
            ]
        else:
            specific = (
                f"{impact_note} The simulated readiness and resource signals identify degraded "
                f"capacity for {primary}; no matching active experiment was observed. The "
                "evidence supports a bounded capacity review, not a guessed rollback."
            )
            recommendations = [
                {
                    "action": "review_capacity",
                    "target": primary,
                    "evidence": "observed readiness deficit without deployment history",
                }
            ]
        diagnosis_outcome = "unresolved"

    diagnosis = {
        "incident_id": alert["alerts"][0]["incident_id"],
        "root_cause": {
            "category": category,
            "specific": specific,
            "evidence": [
                {
                    "tool": "promql_query",
                    "query": query_record["args"]["query"],
                    "finding": (
                        "no_series; no metric claim made"
                        if recipe.evidence_profile == "no-series"
                        else (
                            "malformed incoming payload quarantined; safe query returned no series"
                            if recipe.evidence_profile == "malformed"
                            else (
                                f"simulated {len(metrics)} service series; "
                                f"primary={primary}, window="
                                f"{_PROFILE_WINDOW.get(recipe.evidence_profile, '10m')}"
                            )
                        )
                    ),
                },
                {
                    "tool": "kubectl_get",
                    "resource": "deployments",
                    "finding": (
                        "incoming observation was malformed; safe deployment recheck returned empty"
                        if recipe.evidence_profile == "malformed"
                        else f"readiness snapshot covers {len(metrics)} alert-scoped service(s)"
                    ),
                },
            ],
        },
        "blast_radius_update": (
            f"Keep the primary alert anchor at {primary}; the simulated inventory covers "
            f"{', '.join(meta.target_services)} in default."
        ),
        "next_agent": "remediation",
        "recommended_actions": recommendations,
        "outcome": diagnosis_outcome,
    }
    diagnosis["root_cause"]["evidence"].extend(
        {
            "tool": observation["tool"],
            "finding": (
                "matching simulated active resource inventory"
                if observation["tool"] == "chaos_list_experiments"
                and (observation["output"].get("active_experiments") or [])
                else (
                    "deployment history contains a relevant prior revision"
                    if observation["tool"] == "argocd_app_history"
                    else (
                (
                    f"sampled trace shows {observation['output']['traces'][0]['duration_ms']}ms "
                    f"with longest span at {observation['output']['traces'][0]['longest_span_service']}"
                )
                        if observation["tool"] == "jaeger_search"
                        else (
                    f"workload logs contain {observation['output']['lines'][-1]}"
                            if observation["tool"] == "kubectl_logs"
                            else "observation unavailable or empty"
                        )
                    )
                )
            ),
        }
        for observation in diagnosis_observations[2:]
    )
    _final_message(messages, diagnosis)
    row = _make_row(
        scenario_id=scenario_id,
        meta=meta,
        recipe=recipe,
        case_id=case_id,
        role="diagnosis",
        outcome=diagnosis_outcome,
        safety=safety,
        messages=messages,
    )
    return row, diagnosis, diagnosis_observations


def _remediation_action(
    meta: Any,
    recipe: CaseRecipe,
    resources: list[dict[str, str]],
) -> tuple[str, dict[str, Any]]:
    if recipe.key == "history-backed-capacity-recovery":
        return (
            "argocd_rollback",
            {
                "app": meta.target_services[0],
                "revision": "41",
            },
        )
    if resources:
        resource = resources[0]
        return (
            "chaos_stop_experiment",
            {
                "kind": resource["kind"],
                "name": resource["name"],
                "namespace": resource["namespace"],
            },
        )
    return (
        "kubectl_scale",
        {
            "deployment": meta.target_services[0],
            "replicas": 3,
            "namespace": "default",
        },
    )


def _remediation_row(
    *,
    scenario_id: str,
    meta: Any,
    recipe: CaseRecipe,
    case_id: str,
    safety: dict[str, Any],
    alert: dict[str, Any],
    triage: dict[str, Any],
    diagnosis: dict[str, Any],
    diagnosis_observations: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    resources: list[dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    context: dict[str, Any] = {
        "simulation": True,
        "incident_id": alert["alerts"][0]["incident_id"],
        "alert": alert,
        "incident_anchors": build_incident_anchors(alert),
        "triage": triage,
        "diagnosis": diagnosis,
        "triage_observations": [
            {
                "tool": "alertmanager_list_alerts",
                "args": {"active_only": True},
                "output": {
                    "simulation": True,
                    "success": True,
                    "alerts": alert["alerts"],
                },
            }
        ],
        "diagnosis_observations": diagnosis_observations,
        "approval_mode": safety["approval_mode"],
        "approval": safety["approval"],
    }
    if recipe.evidence_profile == "malformed":
        context["incoming_observation"] = {
            "source": "telemetry_collector",
            "raw_payload": '{"series":[{"service":"truncated',
            "parse_status": "malformed",
        }
    messages = _base_messages(context)
    primary = meta.target_services[0]
    blocked = recipe.severity == "P0" or (
        recipe.severity == "P1" and recipe.approval_status != "approved"
    )
    if blocked:
        final = {
            "incident_id": alert["alerts"][0]["incident_id"],
            "proposed_actions": diagnosis.get("recommended_actions", []),
            "executed_actions": [],
            "outcome": _runtime_outcome("blocked"),
            "time_to_resolve_seconds": None,
            "next_agent": "comms",
            "handoff_notes": (
                "The host approval gate prevented the remediation model/tool stage from being "
                f"invoked for {primary}; no remediation call was made."
            ),
        }
    elif recipe.outcome in {"inconclusive", "malformed"}:
        query = (
            "sum by (service) (rate(http_requests_total{service="
            f'"{primary}",status=~"5.."}}[5m]))'
        )
        output = (
            {
                "success": True,
                "evidence_status": "no_series",
                "series": [],
            }
            if recipe.outcome == "inconclusive"
            else {
                "success": True,
                "evidence_status": "malformed_prior_observation",
                "series": [],
            }
        )
        _tool_observation(
            messages,
            case_id=case_id,
            role="remediation",
            index=0,
            tool_name="promql_query",
            arguments={"query": query},
            output=output,
            reasoning=(
                f"I will use only a read-only check for {primary}. The incoming evidence is "
                "inconclusive or malformed, so I will not issue a mutating action."
            ),
        )
        final = {
            "incident_id": alert["alerts"][0]["incident_id"],
            "proposed_actions": [],
            "executed_actions": [],
            "outcome": _runtime_outcome(recipe.outcome),
            "time_to_resolve_seconds": None,
            "next_agent": "comms",
            "handoff_notes": (
                "No mutation was selected: the available input does not meet the evidence "
                "threshold for an action. The case is escalated without a resolution claim."
            ),
        }
    else:
        tool_name, arguments = _remediation_action(meta, recipe, resources)
        action_summary = (
            f"{tool_name} for {primary}; simulated approval mode "
            f"{safety['approval_mode']} with decision {safety['approval']['status']}."
        )
        action_call_id = _tool_call_id(case_id, "remediation", 0, tool_name)
        verifier_observation = {
            "env_resolved": recipe.verifier_resolved is True,
            "verification_status": (
                "verified" if recipe.verifier_resolved is True else "not_resolved"
            ),
            "action_tool": tool_name,
            "action_args": arguments,
            "tool_call_id": action_call_id,
            "observation_source": "host_verifier_simulation",
            "simulation": True,
        }
        action_output = (
            {
                "success": False,
                "error_class": "authorization_failed",
                "error": "simulated API permission denied for the scoped action",
                "action": tool_name,
            }
            if recipe.outcome == "failed"
            else {
                "success": True,
                "action": tool_name,
                "result": "simulated_action_accepted",
                "target": primary,
            }
        )
        action_output["verifier_observation"] = verifier_observation
        _tool_observation(
            messages,
            case_id=case_id,
            role="remediation",
            index=0,
            tool_name=tool_name,
            arguments=arguments,
            output=action_output,
            call_id=action_call_id,
            reasoning=(
                f"The diagnosis identifies {primary} as the anchored target and supplies "
                f"{len(diagnosis_observations)} structured observations. {action_summary} "
                "I will make one scoped action and inspect its paired tool and verifier result."
            ),
        )
        action_record = {
            "step": 1,
            "tool": tool_name,
            "args": arguments,
            "result": "failed" if recipe.outcome == "failed" else "success",
            "verifier_observation": verifier_observation,
        }
        if recipe.outcome == "failed":
            action_record["error_class"] = action_output["error_class"]
        executed_actions = [action_record]
        if verifier_observation["env_resolved"]:
            final = {
                "incident_id": alert["alerts"][0]["incident_id"],
                "status": "resolved",
                "outcome": "resolved",
                "proposed_actions": [{"tool": tool_name, "args": arguments}],
                "executed_actions": executed_actions,
                "actions_taken": executed_actions,
                "verified_by": "environment_verifier",
                "verification": verifier_observation,
                "time_to_resolve_seconds": None,
                "next_agent": "comms",
                "handoff_notes": (
                    "The paired simulated environment verifier returned env_resolved=true. "
                    "This controller-generated conclusion is not empirical evidence."
                ),
            }
        else:
            verification_query = (
                "sum by (service) (rate(http_requests_total{service="
                f'"{primary}",status=~"5.."}}[5m]))'
            )
            verification_series = [
                {
                    **metrics[0],
                    "evidence_status": (
                        "action_failed" if recipe.outcome == "failed" else "still_degraded"
                    ),
                }
            ] if metrics else []
            verification_note = (
                "the action was denied and the symptom remains unverified"
                if recipe.outcome == "failed"
                else "the symptom or workload condition remains unresolved"
            )
            _tool_observation(
                messages,
                case_id=case_id,
                role="remediation",
                index=1,
                tool_name="promql_query",
                arguments={"query": verification_query},
                output={
                    "success": True,
                    "evidence_status": "observed",
                    "series": verification_series,
                    "verification_note": verification_note,
                },
                reasoning=(
                    "The paired host verifier did not confirm resolution. I am checking the "
                    f"alert-anchored service over a fresh 5m window; {verification_note}."
                ),
            )
            final = {
                "incident_id": alert["alerts"][0]["incident_id"],
                "proposed_actions": [{"tool": tool_name, "args": arguments}],
                "executed_actions": executed_actions,
                "outcome": _runtime_outcome(recipe.outcome),
                "time_to_resolve_seconds": None,
                "next_agent": "comms",
                "verification_status": "not_resolved",
                "verification": verifier_observation,
                "handoff_notes": (
                    "The paired action verifier returned env_resolved=false; the follow-up "
                    "read-only check is preserved and the incident remains open."
                ),
            }
    _final_message(messages, final)
    row = _make_row(
        scenario_id=scenario_id,
        meta=meta,
        recipe=recipe,
        case_id=case_id,
        role="remediation",
        outcome=recipe.outcome,
        safety=safety,
        messages=messages,
    )
    return row, final


def _comms_row(
    *,
    scenario_id: str,
    meta: Any,
    recipe: CaseRecipe,
    case_id: str,
    safety: dict[str, Any],
    alert: dict[str, Any],
    triage: dict[str, Any],
    diagnosis: dict[str, Any],
    remediation: dict[str, Any],
) -> dict[str, Any]:
    incident_id = alert["alerts"][0]["incident_id"]
    resolved = recipe.outcome == "successful"
    host_gate_blocked = (
        recipe.severity == "P1" and recipe.approval_status != "approved"
    )
    title = triage["title"]
    if recipe.severity == "P0":
        impact = (
            "Synthetic total outage on the critical request path; the modeled user impact is "
            "100%, and automated remediation remained manual-only."
        )
    elif recipe.severity == "P1":
        impact = (
            f"Synthetic major degradation affecting {triage['blast_radius']['user_impact_pct']:.1f}% "
            "of the modeled user path."
        )
    else:
        impact = (
            "Synthetic SLO risk with no current modeled customer impact; no outage percentage "
            "is inferred."
        )
    summary = (
        f"Simulation only: {title}. "
        + (
            "The injected verifier reports recovery; this is not empirical incident evidence."
            if resolved
            else f"The case remains {recipe.outcome}; no resolution claim is made."
        )
    )
    if recipe.severity == "P0" or (
        recipe.severity == "P1" and recipe.approval_status != "approved"
    ):
        remediation_event = (
            "Host approval gate blocked the remediation model/tool stage; no remediation call was made."
        )
    elif recipe.outcome == "failed":
        remediation_event = "The simulated action call failed with an authorization error."
    elif recipe.outcome in {"successful", "unresolved"}:
        remediation_event = "One simulated scoped remediation action returned success."
    else:
        remediation_event = "No mutation was selected because evidence was malformed or insufficient."
    if resolved:
        verifier_event = "Coordinator-injected simulated verifier reports env_resolved=true."
    elif recipe.outcome in {"failed", "unresolved"}:
        verifier_event = "Coordinator-injected simulated verifier reports env_resolved=false."
    else:
        verifier_event = "Environment verifier was not run for this blocked or evidence-deficient case."
    action_items = [
        {
            "action": (
                f"Compare the next live error-rate and readiness observations for "
                f"{meta.target_services[0]} before changing incident status."
            ),
            "owner": "on-call SRE",
            "priority": recipe.severity,
            "due": "next review",
        },
        {
            "action": (
                "Keep automated remediation blocked and route any exact action through the "
                "required manual/operator approval path."
                if recipe.severity == "P0"
                or (recipe.severity == "P1" and recipe.approval_status != "approved")
                else (
                    "Reconcile the failed simulated authorization before retrying the same scoped action."
                    if recipe.outcome == "failed"
                    else (
                        "Re-run environment verification and preserve the unresolved result."
                        if recipe.outcome == "unresolved"
                        else (
                            "Repair the malformed source observation and collect a fresh structured capture."
                            if recipe.outcome == "malformed"
                            else (
                                "Collect fresh metrics and experiment inventory before selecting a mutation."
                                if recipe.outcome == "inconclusive"
                                else "Confirm the host verifier record before closing the incident."
                            )
                        )
                    )
                )
            ),
            "owner": "incident commander",
            "priority": recipe.severity,
            "due": "next review",
        },
    ]
    timeline = [
        {"time": "step 1 (simulated)", "event": f"Synthetic alert received for {meta.target_services[0]}."},
        {"time": "step 2 (simulated)", "event": "Triage preserved the alert target and reported modeled impact."},
        {"time": "step 3 (simulated)", "event": "Diagnosis reviewed metric, workload, and relevant dependency evidence."},
        {
            "time": "step 4 (simulated)",
            "event": (
                f"Approval mode {safety['approval_mode']} recorded decision "
                f"{safety['approval']['status']}."
            ),
        },
        {"time": "step 5 (simulated)", "event": remediation_event},
        {"time": "step 6 (simulated)", "event": verifier_event},
    ]
    incident = {
        "simulation": True,
        "incident_id": incident_id,
        "title": title,
        "severity": recipe.severity,
        "duration": "not measured in this simulation",
        "authors": ["AtlasOps synthetic candidate"],
        "triage": triage,
        "diagnosis": diagnosis,
        "remediation": remediation,
        "approval": safety["approval"],
        "env_resolved": resolved,
        "summary": summary,
        "impact": impact,
        "root_cause": diagnosis["root_cause"],
        "detection": (
            "Scenario-derived synthetic alert and tool observations; no production capture was used."
        ),
        "resolution": (
            "Simulated verification reports recovery."
            if resolved
            else "Resolution is not verified in this simulated case."
        ),
        "timeline": timeline,
        "went_well": [
            f"The alert-anchored primary service {meta.target_services[0]} remained explicit."
        ],
        "went_wrong": (
            ["External webhook delivery was not confirmed."]
            if recipe.comms_delivery == "external-failure"
            else (
                ["The case has no verified recovery."]
                if not resolved
                else ["No empirical environment evidence exists in this synthetic example."]
            )
        ),
        "action_items": action_items,
    }
    if recipe.evidence_profile == "malformed":
        incident["incoming_observation"] = {
            "source": "telemetry_collector",
            "raw_payload": '{"series":[{"service":"truncated',
            "parse_status": "malformed",
        }
    context = {
        "simulation": True,
        "incident_id": incident_id,
        "incident": incident,
        "triage": triage,
        "diagnosis": diagnosis,
        "remediation": remediation,
        "approval_mode": safety["approval_mode"],
        "approval": safety["approval"],
    }
    if remediation.get("verification"):
        context["environment_observation"] = remediation["verification"]
    if host_gate_blocked:
        context["coordination_status"] = "host_gate_blocked_before_comms"
    messages = _base_messages(context)
    if host_gate_blocked:
        final = {
            "incident_id": incident_id,
            "outcome": "blocked",
            "slack_posted": False,
            "external_delivery_confirmed": False,
            "postmortem_status": "not_invoked_by_host_gate",
            "postmortem_path": "not_written_host_gate",
            "summary_for_dashboard": (
                f"Comms was not invoked because the P1 {safety['approval']['status']} decision "
                "left the host approval gate closed. No external update or postmortem was created."
            ),
            "lessons_learned": [
                "Preserve the distinct P1 approval result and route follow-up through the operator workflow."
            ],
        }
        _final_message(messages, final)
        return _make_row(
            scenario_id=scenario_id,
            meta=meta,
            recipe=recipe,
            case_id=case_id,
            role="comms",
            outcome=recipe.outcome,
            safety=safety,
            messages=messages,
        )
    delivery_failed = recipe.comms_delivery == "external-failure"
    _tool_observation(
        messages,
        case_id=case_id,
        role="comms",
        index=0,
        tool_name="slack_post_update",
        arguments={
            "channel": "incident-response",
            "severity": recipe.severity,
            "title": title,
            "summary": summary,
            "action_items": [item["action"] for item in action_items],
        },
        output=(
            {
                "success": True,
                "mode": "logged_locally",
                "errors": ["simulated Slack webhook timeout; external delivery not confirmed"],
            }
            if delivery_failed
            else {
                "success": True,
                "mode": "logged_locally+slack",
                "errors": [],
            }
        ),
        reasoning=(
            f"I will communicate the current {recipe.outcome} status for {meta.target_services[0]} "
            "without implying that synthetic observations are production evidence."
        ),
    )
    _tool_observation(
        messages,
        case_id=case_id,
        role="comms",
        index=1,
        tool_name="postmortem_draft",
        arguments={"incident": incident},
        output={
            "success": True,
            "simulation": True,
            "written": False,
            "path": "not_written_simulation_only",
        },
        reasoning=(
            "The update result is recorded, including any unconfirmed external delivery. I will "
            "draft a postmortem from the supplied triage, diagnosis, remediation, and verification "
            "state without adding facts."
        ),
    )
    final = {
        "incident_id": incident_id,
        "outcome": recipe.outcome,
        "slack_posted": True,
        "external_delivery_confirmed": not delivery_failed,
        "postmortem_status": "drafted_in_simulation",
        "postmortem_path": "not_written_simulation_only",
        "summary_for_dashboard": summary,
        "lessons_learned": [
            "Keep synthetic tool observations distinct from verified environment evidence.",
            (
                "Retry external communication through the approved channel."
                if delivery_failed
                else "Keep the incident open unless the verifier confirms recovery."
            ),
        ],
    }
    _final_message(messages, final)
    return _make_row(
        scenario_id=scenario_id,
        meta=meta,
        recipe=recipe,
        case_id=case_id,
        role="comms",
        outcome=recipe.outcome,
        safety=safety,
        messages=messages,
    )


def build_candidate_rows() -> list[dict[str, Any]]:
    """Return deterministic role examples for frozen Train scenarios only."""
    if set(_CASE_RECIPES) != set(range(len(TRAIN_SPLIT))):
        raise ValueError("candidate recipes must address exactly the frozen Train indices")

    rows: list[dict[str, Any]] = []
    for index, scenario_id in enumerate(TRAIN_SPLIT):
        meta = SCENARIO_CATALOG[scenario_id]
        for recipe in _CASE_RECIPES[index]:
            case_id = _case_id(scenario_id, recipe)
            incident_id = _incident_id(case_id)
            safety = _safety(recipe, incident_id)
            metrics = _metrics(meta, recipe)
            alert = _simulated_alert(meta, recipe, incident_id, metrics)
            triage_row = _triage_row(
                train_index=index,
                scenario_id=scenario_id,
                meta=meta,
                recipe=recipe,
                case_id=case_id,
                safety=safety,
                alert=alert,
                metrics=metrics,
            )
            triage = json.loads(triage_row["messages"][-1]["content"])
            triage_observations = _tool_observations_from_messages(
                triage_row["messages"]
            )
            diagnosis_row, diagnosis, diagnosis_observations = _diagnosis_row(
                scenario_id=scenario_id,
                meta=meta,
                recipe=recipe,
                case_id=case_id,
                safety=safety,
                alert=alert,
                metrics=metrics,
                triage=triage,
                triage_observations=triage_observations,
            )
            resources = _chaos_resources(meta)
            remediation_row, remediation = _remediation_row(
                scenario_id=scenario_id,
                meta=meta,
                recipe=recipe,
                case_id=case_id,
                safety=safety,
                alert=alert,
                triage=triage,
                diagnosis=diagnosis,
                diagnosis_observations=diagnosis_observations,
                metrics=metrics,
                resources=resources,
            )
            comms_row = _comms_row(
                scenario_id=scenario_id,
                meta=meta,
                recipe=recipe,
                case_id=case_id,
                safety=safety,
                alert=alert,
                triage=triage,
                diagnosis=diagnosis,
                remediation=remediation,
            )
            rows.extend((triage_row, diagnosis_row, remediation_row, comms_row))
    return rows


def serialize_candidate_rows(rows: list[dict[str, Any]]) -> bytes:
    """Serialize rows as canonical-order JSONL without changing their values."""
    return b"".join(
        (
            json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )


def write_candidate_output(
    output_dir: Path,
    *,
    source_sha: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Write into a new, redirect-free directory without overwriting existing files."""
    destination = Path(output_dir).expanduser().absolute()
    if has_redirecting_path_component(destination):
        raise ValueError(f"candidate output path is redirected: {destination}")
    if destination.exists():
        raise FileExistsError(f"candidate output directory already exists: {destination}")
    rows = build_candidate_rows()
    raw = serialize_candidate_rows(rows)
    pinned_source_sha = source_sha or source_provenance()["git_sha"]
    manifest = candidate_manifest(rows, raw, pinned_source_sha)
    validate_source_revision(manifest)
    destination.mkdir(parents=True, exist_ok=False)
    if has_redirecting_path_component(destination):
        raise ValueError(f"candidate output path changed to a redirect: {destination}")
    corpus_path = destination / OUTPUT_CORPUS_NAME
    manifest_path = destination / OUTPUT_MANIFEST_NAME
    with corpus_path.open("xb") as stream:
        stream.write(raw)
    with manifest_path.open("xb") as stream:
        stream.write(
            (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n")
            .encode("utf-8")
        )
    return corpus_path, manifest_path, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a simulated Train-only AtlasOps SFT review candidate."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new candidate directory; existing paths and redirects are refused",
    )
    args = parser.parse_args(argv)
    corpus_path, manifest_path, manifest = write_candidate_output(args.output_dir)
    print(
        json.dumps(
            {
                "corpus": str(corpus_path),
                "manifest": str(manifest_path),
                "total_examples": manifest["total_examples"],
                "total_scenarios": manifest["total_scenarios"],
                "outcome_distribution": manifest["outcome_distribution"],
                "technical_admissibility": manifest["technical_admissibility"],
                "d3_approval": manifest["d3_approval"],
                "synthetic": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
