"""Chaos Mesh tool wrappers — safe, role-gated chaos observation and remediation."""

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

# Canonical comma-joined CRD list for cluster-wide chaos queries.
CHAOS_RESOURCE_TYPES = ",".join(sorted(ALLOWED_CHAOS_KINDS))

_MAX_LISTED_EXPERIMENTS = 25


def _summarise_selector(spec: dict[str, Any]) -> dict[str, Any]:
    selector = spec.get("selector") or {}
    label_selectors = selector.get("labelSelectors") or {}
    return {
        "namespaces": selector.get("namespaces") or [],
        "app": label_selectors.get("app") or label_selectors.get("app.kubernetes.io/name") or "",
        "mode": spec.get("mode", ""),
    }


def _summarise_experiment(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    status = item.get("status") or {}
    summary = {
        "kind": item.get("kind", ""),
        "name": metadata.get("name", ""),
        "namespace": metadata.get("namespace", ""),
        "created": metadata.get("creationTimestamp", ""),
        "action": spec.get("action", ""),
        "duration": spec.get("duration", ""),
        "target": _summarise_selector(spec),
        "desired_phase": (status.get("experiment") or {}).get("desiredPhase", ""),
    }
    stressors = spec.get("stressors")
    if isinstance(stressors, dict):
        summary["stressors"] = stressors
    return summary


def chaos_list_experiments(namespace: str = "-A") -> dict[str, Any]:
    """List active Chaos Mesh experiments so an agent can observe injected faults.

    Read-only. This is the discovery counterpart to
    :func:`chaos_stop_experiment`, which needs the exact resource ``name``.
    Without this wrapper an agent can never learn that name, so an injected
    fault is undiagnosable and the environment verifier's chaos-clearance
    predicate is unreachable.
    """
    cmd = ["kubectl", "get", CHAOS_RESOURCE_TYPES, "-o", "json"]
    if str(namespace).strip() == "-A":
        cmd.append("-A")
    else:
        clean_namespace = str(namespace).strip()
        if not _SAFE_NAME_RE.match(clean_namespace):
            return {"success": False, "error": f"Invalid namespace '{namespace}'."}
        cmd.extend(["-n", clean_namespace])

    res = _run(cmd, timeout=30)
    if not res.get("success"):
        return {
            "success": False,
            "error": res.get("stderr", res.get("error", "chaos_query_failed")).strip(),
        }

    try:
        parsed = json.loads(res.get("stdout") or "{}")
    except json.JSONDecodeError as exc:
        return {"success": False, "error": f"Could not parse chaos resource JSON: {exc}"}

    items = parsed.get("items", []) if isinstance(parsed, dict) else []
    experiments = [_summarise_experiment(item) for item in items]
    return {
        "success": True,
        "count": len(experiments),
        "experiments": experiments[:_MAX_LISTED_EXPERIMENTS],
        "truncated": len(experiments) > _MAX_LISTED_EXPERIMENTS,
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
