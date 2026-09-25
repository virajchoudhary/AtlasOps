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
            "error": result.get("error") or result.get("stderr") or "chaos_observation_failed",
        }

    try:
        payload = json.loads(str(result.get("stdout") or "{}"))
    except json.JSONDecodeError:
        return {
            "success": False,
            "observation_status": "invalid_response",
            "evidence_status": "environment_observation_error",
            "active_experiments": [],
            "error": "chaos observation returned invalid JSON",
        }

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return {
            "success": False,
            "observation_status": "invalid_response",
            "evidence_status": "environment_observation_error",
            "active_experiments": [],
            "error": "chaos observation requires an items list",
        }
    active_experiments: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") or {}
        kind = str(item.get("kind") or "").strip()
        if kind.casefold() not in ALLOWED_CHAOS_KINDS:
            continue
        item_status = item.get("status") or {}
        conditions = item_status.get("conditions") or []
        experiment_status = item_status.get("experiment") or {}
        phase = experiment_status.get("phase")
        active_experiments.append(
            {
                "kind": _CANONICAL_KINDS.get(kind.casefold(), kind),
                "name": str(metadata.get("name") or ""),
                "namespace": str(metadata.get("namespace") or ""),
                "status": {
                    "phase": phase,
                    "conditions": conditions,
                },
            }
        )

    return {
        "success": True,
        "observation_status": "observed",
        "evidence_status": "active_experiments" if active_experiments else "no_active_experiments",
        "active_experiments": active_experiments,
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
