"""Chaos Mesh tool wrappers — safe, role-gated chaos remediation actions."""

from __future__ import annotations

import json
import re
from typing import Any

from agents.tools.kubectl import _run

ALLOWED_CHAOS_KINDS = frozenset({
    "podchaos",
    "stresschaos",
    "networkchaos",
    "dnschaos",
    "iochaos",
    "timechaos",
})

# Canonical display casing
_CANONICAL_KINDS = {
    "podchaos": "PodChaos",
    "stresschaos": "StressChaos",
    "networkchaos": "NetworkChaos",
    "dnschaos": "DNSChaos",
    "iochaos": "IOChaos",
    "timechaos": "TimeChaos",
}

ALLOWED_CHAOS_NAMESPACES = frozenset({
    "chaos-mesh",
})
# No environment-variable or caller override. Additional namespaces require an
# explicit reviewed configuration change to this allowlist.

_SAFE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_CHAOS_RESOURCE_TYPES = (
    "podchaos",
    "networkchaos",
    "stresschaos",
    "dnschaos",
    "iochaos",
    "timechaos",
)


def _chaos_activity_state(
    phase: str | None,
    conditions: list[dict[str, Any]],
    experiment: dict[str, Any],
) -> str:
    """Classify only positively active Chaos resources as stoppable."""
    condition_states: dict[str, str] = {}
    for condition in conditions:
        condition_type = condition["type"].strip().casefold()
        condition_status = condition["status"].strip().casefold()
        previous_status = condition_states.get(condition_type)
        if previous_status is not None and previous_status != condition_status:
            condition_status = "unknown"
        condition_states[condition_type] = condition_status

    paused = condition_states.get("paused")
    recovered = condition_states.get("allrecovered")

    if phase is not None:
        normalized_phase = phase.strip().casefold()
        if normalized_phase == "running":
            # Chaos Mesh 2.8.3 has no global phase; conflicting provider
            # conditions cannot safely authorize a stop or prove a clean state.
            if paused in {"true", "unknown"} or recovered in {"true", "unknown"}:
                return "unknown"
            return "active"
        if normalized_phase == "finished":
            if recovered in {"false", "unknown"}:
                return "unknown"
            return "inactive"
        if normalized_phase == "paused":
            if paused in {"false", "unknown"}:
                return "unknown"
            return "inactive"
        return "unknown"

    if paused == "true":
        return "inactive"
    if paused != "false":
        return "unknown"

    records = experiment.get("containerRecords")
    if recovered == "true":
        if not isinstance(records, list):
            return "unknown"
        record_phases = [record["phase"].strip().casefold() for record in records]
        if any(
            record_phase not in {"injected", "not injected"}
            for record_phase in record_phases
        ):
            return "unknown"
        return "unknown" if "injected" in record_phases else "inactive"
    if recovered != "false" or not isinstance(records, list) or not records:
        return "unknown"

    record_phases = [record["phase"].strip().casefold() for record in records]
    if any(record_phase not in {"injected", "not injected"} for record_phase in record_phases):
        return "unknown"
    if "injected" in record_phases:
        return "active"
    return "unknown"


def _invalid_chaos_observation(
    error: str,
    inventory: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    parsed_inventory = inventory or []
    return {
        "success": False,
        "observation_status": "invalid_response",
        "evidence_status": "environment_observation_error",
        "active_experiments": [],
        "inventory": parsed_inventory,
        "inventory_complete": False,
        "error": error,
    }


def chaos_list_experiments() -> dict[str, Any]:
    """Observe active supported Chaos Mesh resources without mutating state."""
    result = _run(
        [
            "kubectl",
            "get",
            ",".join(_CHAOS_RESOURCE_TYPES),
            "-A",
            "-o",
            "json",
        ],
        timeout=30,
    )
    if not result.get("success"):
        return {
            "success": False,
            "observation_status": "unavailable",
            "evidence_status": "environment_unavailable",
            "active_experiments": [],
            "inventory": [],
            "inventory_complete": False,
            "error": result.get("error") or result.get("stderr") or "chaos_observation_failed",
        }

    try:
        payload = json.loads(str(result.get("stdout") or "{}"))
    except json.JSONDecodeError:
        return _invalid_chaos_observation("chaos observation returned invalid JSON")

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return _invalid_chaos_observation("chaos observation requires an items list")

    inventory: list[dict[str, Any]] = []
    active_experiments: list[dict[str, Any]] = []
    has_unclassified = False
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            return _invalid_chaos_observation(
                f"chaos observation item {index} must be an object",
                inventory,
            )
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            return _invalid_chaos_observation(
                f"chaos observation item {index} requires object metadata",
                inventory,
            )
        name = metadata.get("name")
        namespace = metadata.get("namespace")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(namespace, str)
            or not namespace.strip()
        ):
            return _invalid_chaos_observation(
                f"chaos observation item {index} requires a name and namespace",
                inventory,
            )

        kind_value = item.get("kind")
        if not isinstance(kind_value, str) or not kind_value.strip():
            return _invalid_chaos_observation(
                f"chaos observation item {index} requires a kind",
                inventory,
            )
        kind = kind_value.strip()
        if kind.casefold() not in ALLOWED_CHAOS_KINDS:
            return _invalid_chaos_observation(
                f"chaos observation item {index} has an unsupported kind",
                inventory,
            )

        item_status = item.get("status")
        if item_status is None:
            item_status = {}
        if not isinstance(item_status, dict):
            return _invalid_chaos_observation(
                f"chaos observation item {index} has invalid status",
                inventory,
            )
        conditions = item_status.get("conditions")
        if conditions is None:
            conditions = []
        if not isinstance(conditions, list):
            return _invalid_chaos_observation(
                f"chaos observation item {index} has invalid conditions",
                inventory,
            )
        for condition in conditions:
            if (
                not isinstance(condition, dict)
                or not isinstance(condition.get("type"), str)
                or not condition["type"].strip()
                or not isinstance(condition.get("status"), str)
                or condition["status"].strip().casefold()
                not in {"true", "false", "unknown"}
            ):
                return _invalid_chaos_observation(
                    f"chaos observation item {index} has malformed conditions",
                    inventory,
                )

        experiment_status = item_status.get("experiment")
        if experiment_status is None:
            experiment_status = {}
        if not isinstance(experiment_status, dict):
            return _invalid_chaos_observation(
                f"chaos observation item {index} has invalid experiment status",
                inventory,
            )
        phase = experiment_status.get("phase")
        if phase is not None and not isinstance(phase, str):
            return _invalid_chaos_observation(
                f"chaos observation item {index} has invalid experiment phase",
                inventory,
            )
        desired_phase = experiment_status.get("desiredPhase")
        if desired_phase is not None and not isinstance(desired_phase, str):
            return _invalid_chaos_observation(
                f"chaos observation item {index} has invalid desired phase",
                inventory,
            )
        records = experiment_status.get("containerRecords")
        if records is not None and not isinstance(records, list):
            return _invalid_chaos_observation(
                f"chaos observation item {index} has invalid container records",
                inventory,
            )
        for record in records or []:
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("phase"), str)
                or not record["phase"].strip()
            ):
                return _invalid_chaos_observation(
                    f"chaos observation item {index} has malformed container records",
                    inventory,
                )

        status_summary: dict[str, Any] = {
            "phase": phase,
            "conditions": conditions,
        }
        if desired_phase is not None:
            status_summary["desired_phase"] = desired_phase
        if records is not None:
            status_summary["container_records"] = records
        observed = {
            "kind": _CANONICAL_KINDS[kind.casefold()],
            "name": name.strip(),
            "namespace": namespace.strip(),
            "status": status_summary,
        }
        inventory.append(observed)
        activity_state = _chaos_activity_state(phase, conditions, experiment_status)
        if activity_state == "active":
            active_experiments.append(observed)
        elif activity_state == "unknown":
            has_unclassified = True

    if has_unclassified:
        observation_status = "unclassified"
        evidence_status = "active_state_unknown"
    elif not items:
        observation_status = "observed_empty"
        evidence_status = "no_active_experiments"
    elif active_experiments:
        observation_status = "observed"
        evidence_status = "active_experiments"
    else:
        observation_status = "observed"
        evidence_status = "no_active_experiments"

    return {
        "success": True,
        "observation_status": observation_status,
        "evidence_status": evidence_status,
        "active_experiments": active_experiments,
        "inventory": inventory,
        "inventory_count": len(inventory),
        "inventory_complete": True,
        "count": len(active_experiments),
    }


def chaos_stop_experiment(
    kind: str,
    name: str,
    namespace: str = "chaos-mesh",
) -> dict[str, Any]:
    """Safely terminate and delete an active Chaos Mesh experiment.

    Gated exclusively to the Remediation role. Only allowlisted Chaos Mesh
    CRD kinds in safe namespaces may be deleted. Generic kubectl delete or
    wildcards are strictly rejected.
    """
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in ALLOWED_CHAOS_KINDS:
        return {
            "success": False,
            "error": (
                f"Invalid chaos kind '{kind}'. Allowed kinds: "
                f"{sorted(_CANONICAL_KINDS.values())}"
            ),
        }

    clean_name = str(name or "").strip()
    if not clean_name or not _SAFE_NAME_RE.match(clean_name):
        return {
            "success": False,
            "error": f"Invalid chaos resource name '{name}'. Must be a valid DNS-1123 resource name without wildcards or special characters.",
        }

    clean_namespace = str(namespace or "chaos-mesh").strip().lower()
    if clean_namespace not in ALLOWED_CHAOS_NAMESPACES:
        return {
            "success": False,
            "error": (
                f"Unauthorized chaos namespace '{namespace}'. Allowed chaos namespaces: "
                f"{sorted(ALLOWED_CHAOS_NAMESPACES)}"
            ),
        }

    cmd = [
        "kubectl",
        "delete",
        normalized_kind,
        clean_name,
        "-n",
        clean_namespace,
        "--ignore-not-found=false",
    ]
    res = _run(cmd, timeout=30)
    if res.get("success"):
        return {
            "success": True,
            "action": "stopped_chaos_experiment",
            "kind": _CANONICAL_KINDS[normalized_kind],
            "name": clean_name,
            "namespace": clean_namespace,
            "stdout": res.get("stdout", "").strip(),
        }
    return {
        "success": False,
        "action": "stopped_chaos_experiment",
        "kind": _CANONICAL_KINDS[normalized_kind],
        "name": clean_name,
        "namespace": clean_namespace,
        "error": res.get("stderr", res.get("error", "Failed to delete chaos resource")).strip(),
    }
