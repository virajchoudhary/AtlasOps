from __future__ import annotations

import inspect
import json
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from agents.approval import ApprovalGate
from agents.tools import TOOL_REGISTRY
from agents.tools.chaos import (
    ALLOWED_CHAOS_KINDS,
    _CANONICAL_KINDS,
    _CHAOS_RESOURCE_PLURALS,
    _chaos_activity_for_resource,
    _close_chaos_api_client,
    _new_chaos_api_client,
)
from agents.tools.kubectl import _G9_KUBECONFIG_CONTEXT
from agents.verifier import ONLINE_BOUTIQUE_SERVICES, OPERATIONAL_AVAILABILITY_ALERT

_NAME = re.compile(r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?")
_ATOMIC_TOOLS = frozenset({
    "kubectl_scale", "kubectl_rollout", "chaos_stop_experiment", "argocd_rollback",
})
_RESOURCE_PATHS = {
    "Deployment": "/apis/apps/v1/namespaces/{namespace}/deployments/{name}",
    "Application": "/apis/argoproj.io/v1alpha1/namespaces/{namespace}/applications/{name}",
    **{
        display: f"/apis/chaos-mesh.org/v1alpha1/namespaces/{{namespace}}/{plural}/{{name}}"
        for key, display in _CANONICAL_KINDS.items()
        for plural in [_CHAOS_RESOURCE_PLURALS[key]]
    },
}


class ActionPreparationError(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    server: str
    context: str
    api_version: str
    kind: str
    namespace: str
    name: str
    uid: str
    resource_version: str

    @property
    def path(self) -> str:
        return _RESOURCE_PATHS[self.kind].format(
            namespace=quote(self.namespace, safe=""), name=quote(self.name, safe=""),
        )


@dataclass(frozen=True, slots=True)
class PreparedAction:
    tool: str
    arguments_json: str
    target: ResourceIdentity
    patch_json: str
    verification_target: ResourceIdentity | None = None

    @property
    def approval_action(self) -> dict[str, Any]:
        record = {
            "tool": self.tool,
            "arguments": json.loads(self.arguments_json),
            "target": asdict(self.target),
            "patch": json.loads(self.patch_json),
        }
        if self.verification_target is not None:
            record["verification_target"] = asdict(self.verification_target)
        return record

    @property
    def digest(self) -> str:
        return ApprovalGate.action_digest(self.approval_action)


def operational_mode() -> bool:
    return os.getenv("ATLASOPS_OPERATIONAL_MODE") == "1"


def _context() -> str | None:
    context = _G9_KUBECONFIG_CONTEXT.get()
    if context is None:
        context = os.getenv("KUBECONFIG_CONTEXT", "").strip() or None
    if context is None and not os.getenv("KUBERNETES_SERVICE_HOST"):
        raise ActionPreparationError("Operational actions require an explicit Kubernetes context")
    return context


@contextmanager
def _client(context: str | None):
    _api, client = _new_chaos_api_client(context)
    try:
        client.configuration.retries = 0
        yield client
    finally:
        _close_chaos_api_client(client)


def _request(client: Any, path: str, method: str = "GET", *, body: Any = None) -> Any:
    headers = {"Accept": "application/json"}
    if method == "PATCH":
        headers["Content-Type"] = "application/json-patch+json"
    elif method == "DELETE":
        headers["Content-Type"] = "application/json"
    return client.call_api(
        path, method, header_params=headers, body=body,
        auth_settings=["BearerToken"], response_type="object",
        _return_http_data_only=True, _request_timeout=30,
    )


def _identity(
    client: Any, context: str | None, resource: Any,
    *, kind: str, namespace: str, name: str, api_version: str,
) -> ResourceIdentity:
    metadata = resource.get("metadata") if isinstance(resource, dict) else None
    if (
        not isinstance(metadata, dict)
        or resource.get("kind") != kind or resource.get("apiVersion") != api_version
        or metadata.get("name") != name or metadata.get("namespace") != namespace
        or any(
            not isinstance(metadata.get(key), str) or not metadata[key].strip()
            for key in ("uid", "resourceVersion")
        )
    ):
        raise ActionPreparationError("Kubernetes response has no matching pinned resource identity")
    server = client.configuration.host.rstrip("/")
    parsed = urlsplit(server)
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment
    ):
        raise ActionPreparationError("Kubernetes server identity is invalid")
    return ResourceIdentity(
        server, context or "in-cluster", api_version, kind, namespace, name,
        metadata["uid"], metadata["resourceVersion"],
    )


def _tests(identity: ResourceIdentity) -> list[dict[str, Any]]:
    return [
        {"op": "test", "path": "/metadata/uid", "value": identity.uid},
        {"op": "test", "path": "/metadata/resourceVersion", "value": identity.resource_version},
    ]


def _name(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 253 or _NAME.fullmatch(value) is None:
        raise ActionPreparationError("Invalid Kubernetes resource name")
    return value


def _workload_target(arguments: dict[str, Any]) -> tuple[str, str]:
    namespace = arguments.get("namespace", "default")
    expected_namespace = os.getenv("ATLASOPS_WORKLOAD_NAMESPACE", "default")
    name = arguments.get("deployment", arguments.get("resource", ""))
    if "/" in str(name):
        kind, name = str(name).split("/", 1)
        if kind not in {"deployment", "deployments", "deploy"}:
            raise ActionPreparationError("Only Deployment mutations are supported operationally")
    name = _name(name)
    if namespace != expected_namespace or name not in ONLINE_BOUTIQUE_SERVICES:
        raise ActionPreparationError("Action target is outside the Online Boutique workload scope")
    return _name(namespace), name


def _alert_workload(alert: Any) -> tuple[str, str]:
    if not isinstance(alert, dict):
        raise ActionPreparationError("An incident alert is required to bind the workload")
    common = alert.get("commonLabels", {})
    items = alert.get("alerts")
    if not isinstance(common, dict) or not isinstance(items, list) or not items:
        raise ActionPreparationError("Incident workload labels are missing")
    labels = [common]
    for item in items:
        selected = item.get("labels") if isinstance(item, dict) else None
        if not isinstance(selected, dict):
            raise ActionPreparationError("Incident workload labels are malformed")
        labels.append(selected)
    alert_names = {
        selected["alertname"] for selected in labels if "alertname" in selected
    }
    if (
        alert_names != {OPERATIONAL_AVAILABILITY_ALERT}
        or any("alertname" not in common and "alertname" not in selected for selected in labels[1:])
    ):
        raise ActionPreparationError("Incident has no supported operational recovery policy")
    names, namespaces = set(), set()
    for index, selected in enumerate(labels):
        for key in ("deployment", "service", "app"):
            if key in selected:
                names.add(_name(selected[key]))
        if "namespace" in selected:
            namespaces.add(_name(selected["namespace"]))
        if index and (
            not any(key in common or key in selected for key in ("deployment", "service", "app"))
            or "namespace" not in common and "namespace" not in selected
        ):
            raise ActionPreparationError("Each incident alert must identify its workload")
    if len(names) != 1 or len(namespaces) != 1:
        raise ActionPreparationError("Incident workload labels conflict")
    return _workload_target({
        "deployment": next(iter(names)), "namespace": next(iter(namespaces)),
    })


def _managed_by_argo(resource: dict[str, Any]) -> bool:
    metadata = resource.get("metadata") or {}
    return bool(
        (metadata.get("labels") or {}).get("argocd.argoproj.io/instance")
        or (metadata.get("annotations") or {}).get("argocd.argoproj.io/tracking-id")
    )


def _rollback_template(client: Any, deployment: dict[str, Any], namespace: str) -> dict[str, Any]:
    replicasets = _request(client, f"/apis/apps/v1/namespaces/{quote(namespace)}/replicasets")
    candidates = []
    try:
        current_revision = int(
            deployment["metadata"]["annotations"]["deployment.kubernetes.io/revision"]
        )
        for item in replicasets["items"]:
            metadata = item["metadata"]
            owners = metadata.get("ownerReferences") or []
            revision = int((metadata.get("annotations") or {}).get("deployment.kubernetes.io/revision", "0"))
            if (
                any(owner.get("uid") == deployment["metadata"]["uid"] and owner.get("controller") is True for owner in owners)
                and 0 < revision < current_revision
            ):
                candidates.append((revision, metadata["uid"], item["spec"]["template"]))
    except (KeyError, TypeError, ValueError):
        raise ActionPreparationError("Deployment rollback history is invalid") from None
    if not candidates:
        raise ActionPreparationError("No prior owned ReplicaSet template is available")
    candidates.sort(key=lambda entry: entry[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        raise ActionPreparationError("Deployment rollback history is ambiguous")
    template = json.loads(_json(candidates[0][2]))
    template.get("metadata", {}).get("labels", {}).pop("pod-template-hash", None)
    return template


def _argo_patch(resource: dict[str, Any], arguments: dict[str, Any]) -> list[dict[str, Any]]:
    revision = str(arguments.get("revision", ""))
    if not revision.isascii() or not revision.isdigit():
        raise ActionPreparationError("Argo rollback requires an observed numeric history ID")
    spec = resource.get("spec") or {}
    source = spec.get("source")
    operation_state = (resource.get("status") or {}).get("operationState") or {}
    if (
        not isinstance(source, dict) or spec.get("sources") or resource.get("operation")
        or operation_state.get("phase") in {"Running", "Pending", "Terminating"}
    ):
        raise ActionPreparationError("Argo requires one idle, single-source Application")
    history = (resource.get("status") or {}).get("history") or []
    matches = [entry for entry in history if str(entry.get("id")) == revision]
    if len(matches) != 1:
        raise ActionPreparationError("Argo history ID is absent or ambiguous")
    target = matches[0]
    target_revision = target.get("revision")
    historical_source = target.get("source") or {}
    if (
        not isinstance(target_revision, str) or not re.fullmatch(r"[0-9a-f]{40}", target_revision)
        or any(historical_source.get(key) != source.get(key) for key in ("repoURL", "path"))
        or target.get("sources")
    ):
        raise ActionPreparationError("Argo rollback source identity is not the owned Git source")
    replacement = {**source, "targetRevision": target_revision}
    return [
        {"op": "test", "path": "/spec/source", "value": source},
        {"op": "replace", "path": "/spec/source", "value": replacement},
        {"op": "add", "path": "/operation", "value": {
            "initiatedBy": {"username": "atlasops"},
            "sync": {"revision": target_revision, "prune": False},
        }},
    ]


def prepare_action(
    tool: str, arguments: dict[str, Any], *, alert: dict[str, Any] | None = None,
) -> PreparedAction:
    if tool not in _ATOMIC_TOOLS:
        raise ActionPreparationError("No atomic operational adapter exists for this mutation")
    try:
        inspect.signature(TOOL_REGISTRY[tool]).bind(**arguments)
    except TypeError:
        raise ActionPreparationError("Tool arguments do not match the operational action contract") from None
    context = _context()
    workload = _alert_workload(alert) if alert is not None else None
    verification_target = None
    if tool in {"chaos_stop_experiment", "argocd_rollback"} and workload is None:
        raise ActionPreparationError("Incident workload is required for this mutation")
    with _client(context) as client:
        if tool in {"kubectl_scale", "kubectl_rollout"}:
            if tool == "kubectl_rollout" and arguments.get("action") != "undo":
                raise ActionPreparationError("Read-only rollout does not require a prepared mutation")
            namespace, name = _workload_target(arguments)
            if workload is not None and workload != (namespace, name):
                raise ActionPreparationError("Action target conflicts with the incident workload")
            path = _RESOURCE_PATHS["Deployment"].format(namespace=namespace, name=name)
            resource = _request(client, path)
            identity = _identity(
                client, context, resource, kind="Deployment", namespace=namespace,
                name=name, api_version="apps/v1",
            )
            if _managed_by_argo(resource):
                raise ActionPreparationError("Argo-managed workloads require an Application action")
            if tool == "kubectl_scale":
                replicas = arguments.get("replicas")
                if type(replicas) is not int or not 1 <= replicas <= 20:
                    raise ActionPreparationError("Operational remediation replicas must be 1-20")
                patch = [{"op": "replace", "path": "/spec/replicas", "value": replicas}]
            else:
                patch = [{"op": "replace", "path": "/spec/template", "value": _rollback_template(client, resource, namespace)}]
            patch = _tests(identity) + patch
        elif tool == "chaos_stop_experiment":
            kind = str(arguments.get("kind", "")).casefold()
            if kind not in ALLOWED_CHAOS_KINDS or arguments.get("namespace", "chaos-mesh") != "chaos-mesh":
                raise ActionPreparationError("Chaos target is outside the cleanup allowlist")
            name = _name(arguments.get("name"))
            kind = _CANONICAL_KINDS[kind]
            path = _RESOURCE_PATHS[kind].format(namespace="chaos-mesh", name=name)
            resource = _request(client, path)
            identity = _identity(
                client, context, resource, kind=kind, namespace="chaos-mesh",
                name=name, api_version="chaos-mesh.org/v1alpha1",
            )
            if _chaos_activity_for_resource(resource) != "active":
                raise ActionPreparationError("Chaos resource is not positively active/injected")
            patch = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {
                "uid": identity.uid, "resourceVersion": identity.resource_version,
            }}
        elif tool == "argocd_rollback":
            name = _name(arguments.get("app"))
            namespace = _name(os.getenv("ATLASOPS_ARGOCD_NAMESPACE", "argocd"))
            if name != os.getenv("ATLASOPS_ARGOCD_APP", "atlasops-boutique"):
                raise ActionPreparationError("Argo action is outside the owned Application")
            resource = _request(
                client, _RESOURCE_PATHS["Application"].format(namespace=namespace, name=name),
            )
            identity = _identity(
                client, context, resource, kind="Application", namespace=namespace,
                name=name, api_version="argoproj.io/v1alpha1",
            )
            spec = resource.get("spec") or {}
            destination = spec.get("destination") or {}
            source = spec.get("source") or {}
            if (
                spec.get("project") != "atlasops-boutique"
                or destination.get("server") != "https://kubernetes.default.svc"
                or destination.get("namespace") != workload[0]
                or source.get("repoURL") != "https://github.com/virajchoudhary/AtlasOps.git"
                or source.get("path") != "infra/local/boutique"
            ):
                raise ActionPreparationError("Argo target is not the owned Application and workload")
            patch = _tests(identity) + _argo_patch(resource, arguments)
            operation_id = ApprovalGate.action_digest({
                "tool": tool, "arguments": arguments,
                "target": asdict(identity), "patch": patch,
            })
            patch[-1]["value"]["info"] = [{
                "name": "atlasops-action-id", "value": operation_id,
            }]
        else:
            raise ActionPreparationError("No atomic operational adapter exists for this mutation")
        if identity.kind != "Deployment":
            namespace, name = workload
            resource = _request(
                client, _RESOURCE_PATHS["Deployment"].format(namespace=namespace, name=name),
            )
            verification_target = _identity(
                client, context, resource, kind="Deployment", namespace=namespace,
                name=name, api_version="apps/v1",
            )
    return PreparedAction(tool, _json(arguments), identity, _json(patch), verification_target)


def execute_prepared_action(action: PreparedAction) -> dict[str, Any]:
    context = None if action.target.context == "in-cluster" else action.target.context
    receipt = None
    try:
        with _client(context) as client:
            if client.configuration.host.rstrip("/") != action.target.server:
                return {"success": False, "error_class": "target_mismatch", "error": "Kubernetes server changed after preparation"}
            method = "DELETE" if action.tool == "chaos_stop_experiment" else "PATCH"
            patch = json.loads(action.patch_json)
            acknowledged = _request(client, action.target.path, method, body=patch)
            if action.tool == "argocd_rollback":
                identity = _identity(
                    client, context, acknowledged, kind=action.target.kind,
                    namespace=action.target.namespace, name=action.target.name,
                    api_version=action.target.api_version,
                )
                source = next(
                    item["value"] for item in patch
                    if item["op"] == "replace" and item["path"] == "/spec/source"
                )
                operation = next(
                    item["value"] for item in patch
                    if item["op"] == "add" and item["path"] == "/operation"
                )
                if (
                    identity.uid != action.target.uid
                    or identity.resource_version == action.target.resource_version
                    or acknowledged.get("spec", {}).get("source") != source
                    or acknowledged.get("operation") != operation
                ):
                    raise ActionPreparationError("Argo acknowledgement does not match the prepared action")
                receipt = {
                    "target": asdict(identity), "source": source, "operation": operation,
                }
    except Exception as error:
        status = getattr(error, "status", None)
        return {
            "success": False,
            "error_class": "target_conflict" if status in {404, 409, 422} else "mutation_uncertain",
            "status_code": status,
            "error": f"Atomic Kubernetes action failed ({status or type(error).__name__})",
        }
    result = {
        "success": True, "action_digest": action.digest,
        "target": asdict(action.target), "atomic_preconditions": True,
    }
    if receipt is not None:
        result["mutation_receipt"] = receipt
    return result
