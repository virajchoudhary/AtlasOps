"""Chaos Mesh tool wrappers — safe, role-gated chaos remediation actions."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from agents.tools.kubectl import _G9_KUBECONFIG_CONTEXT, _run

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
_CHAOS_RESOURCE_PLURALS = {
    "podchaos": "podchaos",
    "stresschaos": "stresschaos",
    "networkchaos": "networkchaos",
    "dnschaos": "dnschaos",
    "iochaos": "iochaos",
    "timechaos": "timechaos",
}
_CHAOS_API_GROUP = "chaos-mesh.org"
_CHAOS_API_VERSION = "v1alpha1"
_KUBERNETES_REQUEST_TIMEOUT = 30
_STAGE_A_CHAOS_SCOPE: ContextVar[dict[str, Any] | None] = ContextVar(
    "stage_a_chaos_scope",
    default=None,
)


def _validated_chaos_target(
    kind: Any,
    name: Any,
    namespace: Any,
    kube_context: Any,
) -> tuple[str, str, str] | str:
    if not isinstance(kind, str) or kind.strip().casefold() not in ALLOWED_CHAOS_KINDS:
        return "invalid_chaos_kind"
    normalized_kind = kind.strip().casefold()
    if (
        not isinstance(name, str)
        or name != name.strip()
        or len(name) > 253
        or not _SAFE_NAME_RE.fullmatch(name)
    ):
        return "invalid_chaos_name"
    if not isinstance(namespace, str) or namespace not in ALLOWED_CHAOS_NAMESPACES:
        return "unauthorized_chaos_namespace"
    if kube_context is not None and (
        not isinstance(kube_context, str)
        or not kube_context
        or kube_context != kube_context.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in kube_context)
    ):
        return "invalid_kubernetes_context"
    return normalized_kind, name, namespace


def _valid_precondition(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _new_chaos_api_client(kube_context: str | None):
    from kubernetes import client, config

    if kube_context is None and os.getenv("KUBERNETES_SERVICE_HOST"):
        configuration = client.Configuration()
        config.load_incluster_config(client_configuration=configuration)
        api_client = client.ApiClient(configuration=configuration)
    else:
        api_client = config.new_client_from_config(context=kube_context)
    try:
        return client.CustomObjectsApi(api_client=api_client), api_client
    except Exception:
        _close_chaos_api_client(api_client)
        raise


def _close_chaos_api_client(api_client: Any) -> None:
    close = getattr(api_client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


@contextmanager
def _chaos_api_client(kube_context: str | None):
    api, api_client = _new_chaos_api_client(kube_context)
    try:
        yield api
    finally:
        _close_chaos_api_client(api_client)


def _kubernetes_error(error: Exception) -> str:
    status = getattr(error, "status", None)
    reason = getattr(error, "reason", None)
    if status is not None:
        suffix = f": {reason}" if isinstance(reason, str) and reason else ""
        return f"Kubernetes API request failed ({status}){suffix}"
    if isinstance(reason, str) and reason:
        return f"Kubernetes API request failed: {reason}"
    return f"Kubernetes API request failed ({type(error).__name__})"


def _delete_chaos_resource(
    api: Any,
    kind: str,
    name: str,
    namespace: str,
    *,
    uid: str,
    resource_version: str,
) -> Any:
    return api.delete_namespaced_custom_object(
        group=_CHAOS_API_GROUP,
        version=_CHAOS_API_VERSION,
        namespace=namespace,
        plural=_CHAOS_RESOURCE_PLURALS[kind],
        name=name,
        body={
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {
                "uid": uid,
                "resourceVersion": resource_version,
            },
        },
        _request_timeout=_KUBERNETES_REQUEST_TIMEOUT,
    )


def delete_chaos_with_preconditions(
    kind: str,
    name: str,
    namespace: str,
    *,
    uid: str,
    resource_version: str,
    kube_context: str | None = None,
) -> dict[str, Any]:
    """Delete one allowlisted Chaos resource with API-enforced identity pins."""
    target = _validated_chaos_target(kind, name, namespace, kube_context)
    if isinstance(target, str):
        return {"success": False, "error": target}
    if not _valid_precondition(uid) or not _valid_precondition(resource_version):
        return {
            "success": False,
            "error": "uid and resource_version are required deletion preconditions",
        }

    normalized_kind, clean_name, clean_namespace = target
    try:
        with _chaos_api_client(kube_context) as api:
            _delete_chaos_resource(
                api,
                normalized_kind,
                clean_name,
                clean_namespace,
                uid=uid,
                resource_version=resource_version,
            )
    except Exception as exc:
        return {
            "success": False,
            "error": _kubernetes_error(exc),
            "status_code": getattr(exc, "status", None),
        }

    return {
        "success": True,
        "action": "deleted_chaos_experiment",
        "kind": _CANONICAL_KINDS[normalized_kind],
        "name": clean_name,
        "namespace": clean_namespace,
        "uid": uid,
        "resource_version": resource_version,
    }


@contextmanager
def stage_a_chaos_scope(
    identity: dict[str, Any],
    before_delete: Callable[[], Any] | None = None,
) -> Iterator[None]:
    """Bind stop calls to one injected Chaos UID for the Stage A action window."""
    if not isinstance(identity, dict):
        raise ValueError("Stage A Chaos identity must be an object")
    kube_context = identity.get("kube_context")
    if not isinstance(kube_context, str) or not kube_context:
        raise ValueError("Stage A Chaos identity requires an explicit kube_context")
    target = _validated_chaos_target(
        identity.get("kind"),
        identity.get("name"),
        identity.get("namespace"),
        kube_context,
    )
    if isinstance(target, str):
        raise ValueError(f"Invalid Stage A Chaos identity: {target}")
    uid = identity.get("uid")
    if not _valid_precondition(uid):
        raise ValueError("Stage A Chaos identity requires a non-empty uid")
    if before_delete is not None and not callable(before_delete):
        raise ValueError("before_delete must be callable")

    normalized_kind, name, namespace = target
    binding = {
        "kind": normalized_kind,
        "name": name,
        "namespace": namespace,
        "uid": uid,
        "kube_context": kube_context,
        "before_delete": before_delete,
        "container_records": identity.get("container_records"),
    }
    token = _STAGE_A_CHAOS_SCOPE.set(binding)
    try:
        yield
    finally:
        _STAGE_A_CHAOS_SCOPE.reset(token)


def _chaos_activity_for_resource(resource: Any) -> str:
    if not isinstance(resource, dict):
        return "unknown"
    status = resource.get("status")
    if not isinstance(status, dict):
        return "unknown"
    conditions = status.get("conditions", [])
    experiment = status.get("experiment", {})
    if not isinstance(conditions, list) or not isinstance(experiment, dict):
        return "unknown"
    phase = experiment.get("phase")
    if phase is not None and not isinstance(phase, str):
        return "unknown"
    for condition in conditions:
        if (
            not isinstance(condition, dict)
            or not isinstance(condition.get("type"), str)
            or not isinstance(condition.get("status"), str)
        ):
            return "unknown"
    records = experiment.get("containerRecords")
    if records is not None and (
        not isinstance(records, list)
        or any(
            not isinstance(record, dict) or not isinstance(record.get("phase"), str)
            for record in records
        )
    ):
        return "unknown"
    try:
        return _chaos_activity_state(phase, conditions, experiment)
    except (AttributeError, KeyError, TypeError):
        return "unknown"


def _stop_scoped_chaos_experiment(
    scope: dict[str, Any],
    kind: str,
    name: str,
    namespace: str,
) -> dict[str, Any]:
    result = {
        "success": False,
        "action": "stopped_chaos_experiment",
        "kind": _CANONICAL_KINDS[kind],
        "name": name,
        "namespace": namespace,
    }
    if (kind, name, namespace) != (
        scope["kind"],
        scope["name"],
        scope["namespace"],
    ):
        return {**result, "error": "Stage A scope forbids deleting a different Chaos resource"}

    try:
        with _chaos_api_client(scope["kube_context"]) as api:
            resource = api.get_namespaced_custom_object(
                group=_CHAOS_API_GROUP,
                version=_CHAOS_API_VERSION,
                namespace=namespace,
                plural=_CHAOS_RESOURCE_PLURALS[kind],
                name=name,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT,
            )
            metadata = resource.get("metadata") if isinstance(resource, dict) else None
            if not isinstance(metadata, dict) or metadata.get("uid") != scope["uid"]:
                return {**result, "error": "Stage A Chaos UID does not match the bound fault"}
            resource_version = metadata.get("resourceVersion")
            if not _valid_precondition(resource_version):
                return {
                    **result,
                    "error": "Stage A Chaos object has no current resourceVersion",
                }
            if _chaos_activity_for_resource(resource) != "active":
                return {
                    **result,
                    "error": "Stage A Chaos object is not positively active/injected",
                }
            bound_records = scope.get("container_records")
            if bound_records is not None and (
                resource.get("status", {}).get("experiment", {}).get("containerRecords")
                != bound_records
            ):
                return {**result, "error": "Stage A injected container identity changed"}

            before_delete = scope["before_delete"]
            if before_delete is not None:
                before_delete()
            _delete_chaos_resource(
                api,
                kind,
                name,
                namespace,
                uid=scope["uid"],
                resource_version=resource_version,
            )
    except Exception as exc:
        return {**result, "error": _kubernetes_error(exc)}

    return {
        **result,
        "success": True,
        "uid": scope["uid"],
        "resource_version": resource_version,
    }


def _chaos_activity_state(
    phase: str | None,
    conditions: list[dict[str, Any]],
    experiment: dict[str, Any],
) -> str:
    """Classify only positively active Chaos resources as stoppable."""
    # Chaos Mesh 2.8.3 does not define this global phase field.
    if phase is not None:
        return "unknown"

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
    namespace_scope = "chaos-mesh" if os.getenv("ATLASOPS_OPERATIONAL_MODE") == "1" else None
    result = _run(
        [
            "kubectl",
            "get",
            ",".join(_CHAOS_RESOURCE_TYPES),
            *(["--namespace", namespace_scope] if namespace_scope else ["-A"]),
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
        if namespace_scope is not None and namespace.strip() != namespace_scope:
            return _invalid_chaos_observation(
                f"chaos observation item {index} is outside the selected namespace",
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
            "uid": metadata.get("uid"),
            "resource_version": metadata.get("resourceVersion"),
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

    observation = {
        "success": True,
        "observation_status": observation_status,
        "evidence_status": evidence_status,
        "active_experiments": active_experiments,
        "inventory": inventory,
        "inventory_count": len(inventory),
        "inventory_complete": True,
        "count": len(active_experiments),
    }
    if namespace_scope is not None:
        observation["namespace_scope"] = namespace_scope
    return observation


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

    scope = _STAGE_A_CHAOS_SCOPE.get()
    if scope is not None:
        return _stop_scoped_chaos_experiment(
            scope,
            normalized_kind,
            clean_name,
            clean_namespace,
        )

    result = {
        "success": False,
        "action": "stopped_chaos_experiment",
        "kind": _CANONICAL_KINDS[normalized_kind],
        "name": clean_name,
        "namespace": clean_namespace,
    }
    kube_context = _G9_KUBECONFIG_CONTEXT.get()
    if kube_context is None:
        kube_context = os.getenv("KUBECONFIG_CONTEXT", "").strip() or None
    try:
        with _chaos_api_client(kube_context) as api:
            resource = api.get_namespaced_custom_object(
                group=_CHAOS_API_GROUP,
                version=_CHAOS_API_VERSION,
                namespace=clean_namespace,
                plural=_CHAOS_RESOURCE_PLURALS[normalized_kind],
                name=clean_name,
                _request_timeout=_KUBERNETES_REQUEST_TIMEOUT,
            )
            metadata = resource.get("metadata") if isinstance(resource, dict) else None
            if (
                not isinstance(metadata, dict)
                or metadata.get("name") != clean_name
                or metadata.get("namespace") != clean_namespace
            ):
                return {**result, "error": "Chaos API resource identity does not match"}
            uid = metadata.get("uid")
            resource_version = metadata.get("resourceVersion")
            if not _valid_precondition(uid) or not _valid_precondition(resource_version):
                return {**result, "error": "Chaos resource requires UID and resourceVersion"}
            if _chaos_activity_for_resource(resource) != "active":
                return {**result, "error": "Chaos resource is not positively active/injected"}
            _delete_chaos_resource(
                api, normalized_kind, clean_name, clean_namespace,
                uid=uid, resource_version=resource_version,
            )
    except Exception as exc:
        return {**result, "error": _kubernetes_error(exc)}
    return {
        **result, "success": True, "uid": uid, "resource_version": resource_version,
    }
