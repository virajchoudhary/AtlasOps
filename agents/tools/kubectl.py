"""kubectl tool wrappers — real subprocess calls to kubectl."""

import json
import os
import re
import subprocess
from typing import Any


METRICS_API_UNAVAILABLE_SIGNATURE = "metrics api not available"
METRICS_API_UNAVAILABLE_ERROR_CLASS = "metrics_api_unavailable"
METRICS_API_UNAVAILABLE_ERROR = (
    "metrics-server Metrics API is not available in this cluster"
)

VALID_ROLLOUT_ACTIONS = ("undo", "status", "history")
VALID_ROLLOUT_KINDS = {
    "deployment": "deployment",
    "deployments": "deployment",
    "deploy": "deployment",
    "statefulset": "statefulset",
    "statefulsets": "statefulset",
    "sts": "statefulset",
    "daemonset": "daemonset",
    "daemonsets": "daemonset",
    "ds": "daemonset",
}
_K8S_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

# Model-supplied strings reach kubectl as argv elements. There is no shell, so
# shell metacharacters are inert, but a value beginning with '-' is still parsed
# by kubectl as a flag (`--raw=...`, `--kubeconfig=...`, `--as=...`), which would
# let a tool argument reach resources and identities the role ACL never granted.
# Constrain each argument to the grammar its position actually accepts.
_RESOURCE_RE = re.compile(
    r"^[a-z0-9][a-z0-9.-]*(,[a-z0-9][a-z0-9.-]*)*(/[a-z0-9][a-z0-9.-]*)?$"
)
# Object names are DNS *subdomains*: dots are legal and common. Custom resource
# definitions are named `stresschaos.chaos-mesh.org`, so a DNS-1123 *label*
# pattern rejects the very names cluster inspection surfaces.
_K8S_OBJECT_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
# `kubectl logs` accepts a qualified workload reference as well as a pod name.
_K8S_LOG_TARGET_RE = re.compile(
    r"^([a-z]+/)?[a-z0-9]([-a-z0-9.]*[a-z0-9])?$"
)
_ALLOWED_OUTPUT_FORMATS = frozenset({"json", "yaml", "wide", "name"})


def _validate_resource(resource: str) -> str | None:
    if not isinstance(resource, str) or not _RESOURCE_RE.match(resource.strip()):
        return (
            f"Invalid resource {resource!r}. Use lower-case resource types, "
            "optionally comma-separated or as type/name."
        )
    return None


def _validate_namespace(namespace: str, *, allow_all: bool = True) -> str | None:
    text = str(namespace).strip()
    if allow_all and text == "-A":
        return None
    if not _K8S_NAME_RE.match(text):
        return f"Invalid namespace {namespace!r}."
    return None


def canonicalize_rollout_resource(resource: str) -> tuple[str | None, str | None]:
    """Validate and canonicalize a rollout resource reference.

    Accepts:
      - deployment/name, deploy/name, statefulset/name, sts/name, daemonset/name, ds/name
      - bare name: canonicalized to deployment/<name>
    Rejects:
      - malformed strings, flags, shell metacharacters, unsupported resource kinds, empty names
    Returns (canonical_resource, error_message).
    """
    if not isinstance(resource, str) or not resource.strip():
        return None, "Resource must be a non-empty string"
    resource = resource.strip()
    if resource.startswith("-") or " " in resource or "\t" in resource or "\n" in resource:
        return None, f"Invalid resource format or flag injection: {resource!r}"

    if "/" in resource:
        parts = resource.split("/", 1)
        kind_raw = parts[0].strip().lower()
        name = parts[1].strip()
        if kind_raw not in VALID_ROLLOUT_KINDS:
            return (
                None,
                f"Unsupported rollout resource kind '{parts[0]}'. Allowed kinds: deployment, statefulset, daemonset.",
            )
        kind = VALID_ROLLOUT_KINDS[kind_raw]
    else:
        kind = "deployment"
        name = resource

    if not _K8S_NAME_RE.match(name):
        return (
            None,
            f"Invalid Kubernetes resource name: {name!r}. Must consist of lower-case alphanumeric characters or '-', and must start and end with an alphanumeric character.",
        )

    return f"{kind}/{name}", None


def _run(cmd: list[str], timeout: int = 30) -> dict[str, Any]:
    ctx = os.getenv("KUBECONFIG_CONTEXT", "").strip()
    if ctx and len(cmd) > 1 and cmd[0] == "kubectl" and "--context" not in cmd:
        cmd = [cmd[0], "--context", ctx] + cmd[1:]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}


def kubectl_get(resource: str, namespace: str = "-A", output: str = "json") -> dict[str, Any]:
    """Get Kubernetes resources. resource examples: 'pods', 'deployments', 'services'."""
    error = _validate_resource(resource)
    if error:
        return {"error": error, "success": False}
    error = _validate_namespace(namespace)
    if error:
        return {"error": error, "success": False}
    if output not in _ALLOWED_OUTPUT_FORMATS:
        return {
            "error": f"Invalid output format {output!r}. Allowed: {sorted(_ALLOWED_OUTPUT_FORMATS)}.",
            "success": False,
        }
    cmd = ["kubectl", "get", resource.strip(), "-o", output]
    if str(namespace).strip() == "-A":
        cmd.append("-A")
    else:
        cmd.extend(["-n", str(namespace).strip()])
    result = _run(cmd)
    if result.get("success") and output == "json":
        try:
            result["parsed"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return result


def kubectl_describe(resource: str, name: str, namespace: str = "default") -> dict[str, Any]:
    """Describe a specific Kubernetes resource."""
    error = _validate_resource(resource) or _validate_namespace(namespace, allow_all=False)
    if error:
        return {"error": error, "success": False}
    if not _K8S_OBJECT_NAME_RE.match(str(name).strip()):
        return {"error": f"Invalid resource name {name!r}.", "success": False}
    return _run(
        ["kubectl", "describe", resource.strip(), str(name).strip(), "-n", str(namespace).strip()]
    )


def kubectl_logs(pod: str, namespace: str = "default", tail: int = 200,
                 container: str = "") -> dict[str, Any]:
    """Get pod logs."""
    error = _validate_namespace(namespace, allow_all=False)
    if error:
        return {"error": error, "success": False}
    if not _K8S_LOG_TARGET_RE.match(str(pod).strip()):
        return {
            "error": f"Invalid log target {pod!r}. Use a pod name or deployment/<name>.",
            "success": False,
        }
    try:
        tail_lines = int(tail)
    except (TypeError, ValueError):
        return {"error": f"Invalid tail value {tail!r}.", "success": False}
    if not 1 <= tail_lines <= 5000:
        return {"error": "tail must be between 1 and 5000", "success": False}
    cmd = [
        "kubectl", "logs", str(pod).strip(), "-n", str(namespace).strip(),
        f"--tail={tail_lines}",
    ]
    if container:
        if not _K8S_OBJECT_NAME_RE.match(str(container).strip()):
            return {"error": f"Invalid container name {container!r}.", "success": False}
        cmd.extend(["-c", str(container).strip()])
    return _run(cmd, timeout=20)


def _classify_metrics_api_result(result: dict[str, Any]) -> dict[str, Any]:
    """Deterministically classify a failed `kubectl top` call.

    The Metrics API (metrics-server) is an optional cluster dependency. When it
    is absent, `kubectl top` fails with a stable stderr signature; surface that
    as a machine-differentiable ``metrics_api_unavailable`` class instead of raw
    stderr so agents and evidence can distinguish "tool dependency missing"
    from transient command failures.
    """
    err = str((result or {}).get("stderr") or "").casefold()
    if METRICS_API_UNAVAILABLE_SIGNATURE in err:
        return {
            **result,
            "success": False,
            "error_class": METRICS_API_UNAVAILABLE_ERROR_CLASS,
            "error": METRICS_API_UNAVAILABLE_ERROR,
        }
    return result


def response_contract_profile() -> dict[str, Any]:
    """Declare the model-visible Metrics API dependency contract."""
    return {
        "version": "g4-kubectl-top-response-v1",
        "tools": ["kubectl_top_nodes", "kubectl_top_pods"],
        "unavailable_signature": METRICS_API_UNAVAILABLE_SIGNATURE,
        "signature_matching": "casefold-substring",
        "unavailable_result": {
            "success": False,
            "error": METRICS_API_UNAVAILABLE_ERROR,
            "error_class": METRICS_API_UNAVAILABLE_ERROR_CLASS,
        },
        "preserves_raw_stderr": True,
        "unrelated_failures_passthrough": True,
    }


def kubectl_top_pods(namespace: str = "-A") -> dict[str, Any]:
    """Get CPU/memory usage for pods."""
    cmd = ["kubectl", "top", "pods"]
    if namespace == "-A":
        cmd.append("-A")
    else:
        cmd.extend(["-n", namespace])
    return _classify_metrics_api_result(_run(cmd))


def kubectl_top_nodes() -> dict[str, Any]:
    """Get CPU/memory usage for nodes."""
    return _classify_metrics_api_result(_run(["kubectl", "top", "nodes"]))


def kubectl_rollout(action: str, resource: str, namespace: str = "default") -> dict[str, Any]:
    """Rollout operations: undo, status, history. action in {undo, status, history}.

    Resource can be deployment/name, statefulset/name, daemonset/name, or a bare name (canonicalized to deployment/name).
    """
    if action not in VALID_ROLLOUT_ACTIONS:
        return {
            "error": f"Invalid rollout action: {action}. Must be one of {list(VALID_ROLLOUT_ACTIONS)}",
            "success": False,
        }

    canonical_res, err = canonicalize_rollout_resource(resource)
    if err is not None:
        return {"error": err, "success": False}

    if not isinstance(namespace, str) or not _K8S_NAME_RE.match(namespace.strip()):
        return {"error": f"Invalid namespace: {namespace!r}", "success": False}

    return _run(["kubectl", "rollout", action, canonical_res, "-n", namespace.strip()], timeout=60)


def kubectl_scale(deployment: str, replicas: int, namespace: str = "default") -> dict[str, Any]:
    """Scale a deployment to the given replica count."""
    try:
        replica_count = int(replicas)
    except (TypeError, ValueError):
        return {"error": f"Invalid replicas value {replicas!r}.", "success": False}
    if not (0 <= replica_count <= 20):
        return {"error": "replicas must be 0–20", "success": False}
    if not _K8S_NAME_RE.match(str(deployment).strip()):
        return {"error": f"Invalid deployment name {deployment!r}.", "success": False}
    error = _validate_namespace(namespace, allow_all=False)
    if error:
        return {"error": error, "success": False}
    return _run(
        ["kubectl", "scale", "deployment", str(deployment).strip(),
         f"--replicas={replica_count}", "-n", str(namespace).strip()],
        timeout=30,
    )


def kubectl_exec(pod: str, command: list[str], namespace: str = "default") -> dict[str, Any]:
    """Execute a read-only command inside a pod. Allowlisted commands only."""
    allowlist = {"ls", "cat", "env", "ps", "df", "free", "netstat", "ss", "curl", "wget",
                 "nslookup", "dig", "ping", "hostname", "id", "whoami", "date", "uptime"}
    if not command or command[0] not in allowlist:
        return {
            "error": f"Command '{command[0] if command else ''}' not in allowlist",
            "success": False,
        }
    return _run(
        ["kubectl", "exec", pod, "-n", namespace, "--"] + command,
        timeout=15,
    )
