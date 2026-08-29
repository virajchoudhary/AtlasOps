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
    cmd = ["kubectl", "get", resource, "-o", output]
    if namespace == "-A":
        cmd.append("-A")
    else:
        cmd.extend(["-n", namespace])
    result = _run(cmd)
    if result.get("success") and output == "json":
        try:
            result["parsed"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            pass
    return result


def kubectl_describe(resource: str, name: str, namespace: str = "default") -> dict[str, Any]:
    """Describe a specific Kubernetes resource."""
    return _run(["kubectl", "describe", resource, name, "-n", namespace])


def kubectl_logs(pod: str, namespace: str = "default", tail: int = 200,
                 container: str = "") -> dict[str, Any]:
    """Get pod logs."""
    cmd = ["kubectl", "logs", pod, "-n", namespace, f"--tail={tail}"]
    if container:
        cmd.extend(["-c", container])
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
    if not (0 <= replicas <= 20):
        return {"error": "replicas must be 0–20", "success": False}
    return _run(
        ["kubectl", "scale", "deployment", deployment,
         f"--replicas={replicas}", "-n", namespace],
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
