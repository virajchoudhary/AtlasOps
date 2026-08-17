"""Chaos Mesh tool wrappers — safe, role-gated chaos remediation actions."""

from __future__ import annotations

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

_SAFE_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


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

    clean_namespace = str(namespace or "chaos-mesh").strip()
    if not clean_namespace or not _SAFE_NAME_RE.match(clean_namespace):
        return {
            "success": False,
            "error": f"Invalid namespace '{namespace}'. Must be a valid DNS-1123 namespace.",
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
