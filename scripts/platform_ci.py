#!/usr/bin/env python3
"""Run the bounded, disposable AtlasOps platform check in GitHub-hosted Actions."""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REPOSITORY = "virajchoudhary/AtlasOps"
EXPECTED_REPO_URL = "https://github.com/virajchoudhary/AtlasOps.git"
APP_NAME = "atlasops-boutique"
APP_NAMESPACE = "argocd"
APP_PATH = "infra/local/boutique"
APP_WORKLOAD_NAMESPACE = "atlasops-boutique"
WORKLOAD_NAME = "frontend"
WORKLOAD_CONTAINER = "server"
ALERT_NAME = "AtlasOpsOnlineBoutiqueDeploymentUnavailable"
FIXTURE_MODEL = "fixture/no-model"
FIXTURE_SERVICE = "atlasops-ci-model-fixture"
FIXTURE_SECRET = "atlasops-ci-model-fixture"
FIXTURE_ACCOUNT = "atlasops-ci-model-fixture"
FIXTURE_CLASSIFICATION = "NON_EMPIRICAL_SOFTWARE_FIXTURE"
CERTIFICATION_STATUS = "NOT_CERTIFIED"
KIND_CLUSTER_LABEL = "io.x-k8s.kind.cluster"
KIND_NODE_ROLE_LABEL = "io.x-k8s.kind.role"
SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_RE = re.compile(r"(?:^|@|://)(sha256:[0-9a-f]{64})$")
IMAGE_ID_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?sha256:[0-9a-f]{64}$")
DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
SENSITIVE_KEY_RE = re.compile(
    r"(?:token|secret|password|api[_-]?key|authorization|credential|cookie)",
    re.IGNORECASE,
)
APPROVAL_TOKEN_RE = re.compile(r"\bapr-[A-Za-z0-9_-]{6,}\b")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[^,\s\"']+")
DEFAULT_TIMEOUT = 30
POLL_INTERVAL = 5
COORDINATOR_METRICS_TIMEOUT_SECONDS = 90
MAX_EVIDENCE_TEXT = 100_000
MAX_EXECUTION_EXPORT_BYTES = 8 * 1024 * 1024


class PlatformCIError(RuntimeError):
    """A bounded runner failure with a safe, user-facing explanation."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class RunIdentity:
    repository: str
    source_sha: str
    base_sha: str
    run_id: str
    attempt: str
    event_name: str
    source_ref: str
    source_branch: str


@dataclass(frozen=True)
class ClusterOwnership:
    cluster_name: str
    context_name: str
    control_plane_id: str
    cluster_label: str
    role_label: str
    api_server: str
    api_host_port: str
    kube_system_uid: str


@dataclass
class Forward:
    name: str
    port: int
    process: subprocess.Popen[str]
    log_path: Path
    log_stream: Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_sha(value: Any, description: str) -> str:
    if not isinstance(value, str) or not SOURCE_SHA_RE.fullmatch(value):
        raise PlatformCIError(f"{description} must be a lowercase 40-character commit SHA")
    return value


def _git(root: Path, args: list[str]) -> str:
    safe_env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT")
        if key in os.environ
    }
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_CEILING_DIRECTORIES",
    ):
        safe_env.pop(key, None)
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        env=safe_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise PlatformCIError(f"Git identity check failed ({result.returncode})")
    return result.stdout.strip()


def _event_repository(event: dict[str, Any]) -> str:
    repository = event.get("repository")
    full_name = repository.get("full_name") if isinstance(repository, dict) else None
    if not isinstance(full_name, str):
        raise PlatformCIError("GitHub event did not identify its repository")
    return full_name


def _remote_repository(url: str) -> str:
    if not isinstance(url, str) or not url:
        raise PlatformCIError("origin remote is missing")
    if url.startswith("git@github.com:"):
        path = url.removeprefix("git@github.com:")
    else:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise PlatformCIError("origin must be an unauthenticated HTTPS or SSH GitHub URL")
        path = parsed.path.lstrip("/")
    path = path.removesuffix(".git")
    if path != EXPECTED_REPOSITORY:
        raise PlatformCIError("origin does not identify the expected repository")
    return f"https://github.com/{path}.git"


def validate_run_identity(
    env: dict[str, str],
    repo_root: Path = REPO_ROOT,
    *,
    git: Callable[[Path, list[str]], str] = _git,
) -> RunIdentity:
    if env.get("GITHUB_ACTIONS", "").lower() != "true":
        raise PlatformCIError("platform-ci run is restricted to GitHub-hosted Actions")
    if env.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise PlatformCIError("platform-ci run requires a GitHub-hosted runner")
    if env.get("GITHUB_REPOSITORY") != EXPECTED_REPOSITORY:
        raise PlatformCIError("GitHub Actions repository identity is not allowed")

    run_id = env.get("GITHUB_RUN_ID", "")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "")
    if not run_id.isascii() or not run_id.isdigit() or not attempt.isascii() or not attempt.isdigit():
        raise PlatformCIError("GitHub Actions run identity is malformed")
    if int(run_id) <= 0 or int(attempt) <= 0:
        raise PlatformCIError("GitHub Actions run identity must be positive")

    event_path = env.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        raise PlatformCIError("GitHub event payload path is missing")
    try:
        event_file = Path(event_path)
        if not event_file.is_file() or event_file.stat().st_size > 2_000_000:
            raise PlatformCIError("GitHub event payload is unavailable or too large")
        event = json.loads(event_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlatformCIError("GitHub event payload could not be read") from exc
    if not isinstance(event, dict):
        raise PlatformCIError("GitHub event payload is malformed")
    if _event_repository(event) != EXPECTED_REPOSITORY:
        raise PlatformCIError("GitHub event repository does not match the checkout")

    event_name = env.get("GITHUB_EVENT_NAME", "")
    source_ref = ""
    source_branch = ""
    if event_name == "pull_request":
        pull_request = event.get("pull_request")
        if not isinstance(pull_request, dict):
            raise PlatformCIError("pull request event payload is malformed")
        head = pull_request.get("head")
        base = pull_request.get("base")
        head_repo = head.get("repo") if isinstance(head, dict) else None
        base_repo = base.get("repo") if isinstance(base, dict) else None
        if (
            not isinstance(head, dict)
            or not isinstance(head_repo, dict)
            or head_repo.get("full_name") != EXPECTED_REPOSITORY
            or not isinstance(base, dict)
            or not isinstance(base_repo, dict)
            or base_repo.get("full_name") != EXPECTED_REPOSITORY
            or base.get("ref") != "main"
            or pull_request.get("state", "open") != "open"
        ):
            raise PlatformCIError("foreign-fork or non-main pull request heads are rejected")
        source_sha = _safe_sha(head.get("sha"), "pull request head")
        source_branch = head.get("ref", "")
        if not isinstance(source_branch, str) or not source_branch:
            raise PlatformCIError("pull request source branch is invalid")
        try:
            git(repo_root, ["check-ref-format", f"refs/heads/{source_branch}"])
        except PlatformCIError as exc:
            raise PlatformCIError("pull request source branch is invalid") from exc
        source_ref = f"refs/remotes/origin/{source_branch}"
        base_sha = _safe_sha(base.get("sha"), "pull request base")
    elif event_name == "push":
        if event.get("ref") != "refs/heads/main":
            raise PlatformCIError("push-triggered platform-ci runs are restricted to main")
        source_sha = _safe_sha(event.get("after"), "main push")
        if env.get("GITHUB_SHA") != source_sha:
            raise PlatformCIError("main push SHA differs from the GitHub event payload")
        source_branch = "main"
        source_ref = "refs/remotes/origin/main"
        before = event.get("before")
        base_sha = before if isinstance(before, str) and SOURCE_SHA_RE.fullmatch(before) else ""
    else:
        raise PlatformCIError("only same-repository pull requests and main pushes are accepted")

    checkout_sha = _safe_sha(git(repo_root, ["rev-parse", "--verify", "HEAD"]), "checkout HEAD")
    if checkout_sha != source_sha:
        raise PlatformCIError("checkout HEAD is not the exact current PR-head/main SHA")
    remote = git(repo_root, ["remote", "get-url", "origin"])
    _remote_repository(remote)
    remote_sha = _safe_sha(
        git(repo_root, ["rev-parse", "--verify", f"{source_ref}^{{commit}}"]),
        "reachable source ref",
    )
    if remote_sha != source_sha:
        raise PlatformCIError("source SHA is not the current reachable same-repository ref")
    if git(repo_root, ["status", "--porcelain", "--untracked-files=all"]):
        raise PlatformCIError("platform-ci requires a clean source checkout")

    return RunIdentity(
        repository=EXPECTED_REPOSITORY,
        source_sha=source_sha,
        base_sha=base_sha,
        run_id=run_id,
        attempt=attempt,
        event_name=event_name,
        source_ref=source_ref,
        source_branch=source_branch,
    )


def validate_external_evidence_path(path: str | Path, repo_root: Path = REPO_ROOT) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raise PlatformCIError("--evidence-dir must be an absolute external path")
    if raw.is_symlink():
        raise PlatformCIError("--evidence-dir may not be a symlink")
    resolved = raw.resolve()
    root = repo_root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        pass
    else:
        raise PlatformCIError("--evidence-dir must be outside the source checkout")
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise PlatformCIError("--evidence-dir must be a new or empty directory")
    return resolved


def sanitize_text(value: str, secrets_to_redact: tuple[str, ...] | list[str] = ()) -> str:
    safe = value
    for secret in sorted((item for item in secrets_to_redact if item), key=len, reverse=True):
        safe = safe.replace(secret, "[REDACTED]")
    safe = BEARER_RE.sub("Bearer [REDACTED]", safe)
    safe = APPROVAL_TOKEN_RE.sub("[REDACTED_APPROVAL_TOKEN]", safe)
    return safe[:MAX_EVIDENCE_TEXT]


def sanitize_evidence(
    value: Any,
    secrets_to_redact: tuple[str, ...] | list[str] = (),
    *,
    key: str = "",
) -> Any:
    if key and SENSITIVE_KEY_RE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_evidence(
                item_value,
                secrets_to_redact,
                key=str(item_key),
            )
            for item_key, item_value in value.items()
            if str(item_key) not in {"scenario_id", "experiment_id", "gate_id"}
        }
    if isinstance(value, list):
        return [sanitize_evidence(item, secrets_to_redact) for item in value]
    if isinstance(value, tuple):
        return [sanitize_evidence(item, secrets_to_redact) for item in value]
    if isinstance(value, str):
        return sanitize_text(value, secrets_to_redact)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def write_json(path: Path, value: Any, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(serialized, encoding="utf-8")
    try:
        temporary.chmod(mode)
    except OSError:
        pass
    temporary.replace(path)


def _fixture_tool_names(payload: dict[str, Any]) -> set[str]:
    tools = payload.get("tools")
    names: set[str] = set()
    if not isinstance(tools, list):
        return names
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str):
            names.add(name)
    return names


def derive_fixture_role(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise PlatformCIError("fixture request has no message list")
    system_text = "\n".join(
        item.get("content", "")
        for item in messages
        if isinstance(item, dict)
        and item.get("role") == "system"
        and isinstance(item.get("content"), str)
    )
    prompt_markers = {
        "triage": "Triage Agent",
        "diagnosis": "Diagnosis Agent",
        "remediation": "Remediation Agent",
        "comms": "Comms Agent",
    }
    prompt_roles = [
        role for role, marker in prompt_markers.items()
        if re.search(
            rf"(?im)^(?:#+\s*|You are the (?:\*\*)?){re.escape(marker)}\b",
            system_text,
        )
    ]
    names = _fixture_tool_names(payload)
    if len(prompt_roles) != 1:
        raise PlatformCIError("fixture could not derive one role from the system prompt")
    role = prompt_roles[0]
    if role == "comms":
        if (
            "You cannot publish messages or postmortems." not in system_text
            or "The coordinator publishes from verified facts" not in system_text
            or names
        ):
            raise PlatformCIError(
                "operational Comms fixture requires its inference-only prompt and no publication tools"
            )
        return role
    schema_roles = []
    if "alertmanager_list_alerts" in names:
        schema_roles.append("triage")
    if "argocd_app_history" in names:
        schema_roles.append("diagnosis")
    if "argocd_rollback" in names:
        schema_roles.append("remediation")
    if schema_roles != prompt_roles:
        raise PlatformCIError("fixture could not derive one consistent role from prompt and tool schema")
    return role


def _message_json(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def history_id_from_messages(
    messages: list[dict[str, Any]],
    *,
    app: str = APP_NAME,
    source_sha: str,
) -> str:
    candidates: set[str] = set()
    handoff = _user_input(messages).get("diagnosis_observations")
    observations = list(handoff) if isinstance(handoff, list) else []
    observations.extend(
        {
            "tool": function["name"],
            "args": _message_json(function.get("arguments")),
            "output": output,
        }
        for function, output in _tool_results(messages).values()
        if function.get("name") == "argocd_app_history"
    )
    for item in observations:
        if not isinstance(item, dict):
            continue
        if item.get("tool") != "argocd_app_history":
            continue
        args = item.get("args")
        output = item.get("output")
        if (
            not isinstance(args, dict)
            or args.get("app") != app
            or not isinstance(output, dict)
            or output.get("success") is not True
            or not isinstance(output.get("history"), list)
        ):
            continue
        for entry in output["history"]:
            if not isinstance(entry, dict) or entry.get("revision") != source_sha:
                continue
            entry_id = entry.get("id")
            if type(entry_id) is int and entry_id >= 0:
                candidates.add(str(entry_id))
            elif (
                isinstance(entry_id, str)
                and entry_id.isascii()
                and entry_id.isdigit()
            ):
                candidates.add(entry_id)
    if len(candidates) != 1:
        raise PlatformCIError("fixture requires one numeric Argo history ID observed for the build SHA")
    return candidates.pop()


def _user_input(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        parsed = _message_json(message.get("content"))
        if isinstance(parsed, dict):
            return parsed
    return {}


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], Any]]:
    calls: dict[str, dict[str, Any]] = {}
    outputs: dict[str, Any] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                    continue
                function = call.get("function")
                if isinstance(function, dict) and isinstance(function.get("name"), str):
                    calls[call["id"]] = function
        elif message.get("role") == "tool" and isinstance(message.get("tool_call_id"), str):
            outputs[message["tool_call_id"]] = _message_json(message.get("content"))
    return {
        function["name"]: (function, outputs[call_id])
        for call_id, function in calls.items()
        if call_id in outputs
    }


def _tool_call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, separators=(",", ":"))},
    }


def _fixture_response(
    payload: dict[str, Any],
    role: str,
    *,
    source_sha: str,
) -> dict[str, Any]:
    messages = payload["messages"]
    user = _user_input(messages)
    results = _tool_results(messages)
    prior_tool_calls = sum(
        len(message.get("tool_calls") or [])
        for message in messages
        if isinstance(message, dict) and message.get("role") == "assistant"
    )
    call_id = f"fixture-{role}-{prior_tool_calls + 1}"
    message: dict[str, Any]
    finish_reason = "stop"

    if role == "triage":
        if "alertmanager_list_alerts" not in results:
            message = {
                "content": None,
                "tool_calls": [
                    _tool_call(
                        "alertmanager_list_alerts",
                        {"active_only": True},
                        call_id,
                    )
                ],
            }
            finish_reason = "tool_calls"
        else:
            incident_id = user.get("incident_id")
            if not isinstance(incident_id, str) or not incident_id:
                raise PlatformCIError("triage fixture requires the admitted incident identity")
            message = {
                "content": json.dumps(
                    {
                        "incident_id": incident_id,
                        "severity": "P1",
                        "title": "Frontend unavailable in the disposable platform fixture",
                        "blast_radius": {
                            "services": [WORKLOAD_NAME],
                            "namespaces": [APP_WORKLOAD_NAMESPACE],
                            "user_impact_pct": 0,
                            "revenue_path_affected": True,
                        },
                        "correlated_alerts": [ALERT_NAME],
                        "next_agent": "diagnosis",
                        "handoff_notes": "Synthetic CI fixture; use observed deployment and Argo evidence.",
                    }
                )
            }
    elif role == "diagnosis":
        if "argocd_app_history" not in results:
            message = {
                "content": None,
                "tool_calls": [
                    _tool_call(
                        "argocd_app_history",
                        {"app": APP_NAME},
                        call_id,
                    ),
                    _tool_call(
                        "kubectl_describe",
                        {
                            "resource": "deployment",
                            "name": WORKLOAD_NAME,
                            "namespace": APP_WORKLOAD_NAMESPACE,
                        },
                        f"{call_id}-describe",
                    ),
                    _tool_call(
                        "kubectl_get",
                        {
                            "resource": "pods",
                            "namespace": APP_WORKLOAD_NAMESPACE,
                            "output": "json",
                        },
                        f"{call_id}-pods",
                    ),
                ],
            }
            finish_reason = "tool_calls"
        else:
            observed_id = history_id_from_messages(
                messages,
                app=APP_NAME,
                source_sha=source_sha,
            )
            describe = results.get("kubectl_describe")
            if not describe or not isinstance(describe[1], dict) or describe[1].get("success") is not True:
                raise PlatformCIError("diagnosis fixture requires a successful live workload description")
            describe_arguments = json.loads(describe[0].get("arguments") or "{}")
            if describe_arguments != {
                "resource": "deployment",
                "name": WORKLOAD_NAME,
                "namespace": APP_WORKLOAD_NAMESPACE,
            }:
                raise PlatformCIError("diagnosis fixture description is not scoped to the frontend Deployment")
            stdout = str(describe[1].get("stdout") or "")
            deployment_image = re.search(
                r"(?m)^\s*Image:\s*(atlasops-ci-unavailable:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})\s*$",
                stdout,
            )
            if (
                not re.search(rf"(?m)^Name:\s+{re.escape(WORKLOAD_NAME)}\s*$", stdout)
                or not re.search(
                    rf"(?m)^Namespace:\s+{re.escape(APP_WORKLOAD_NAMESPACE)}\s*$",
                    stdout,
                )
                or deployment_image is None
            ):
                raise PlatformCIError("diagnosis fixture did not observe the unavailable frontend Deployment")

            pods = results.get("kubectl_get")
            if not pods or not isinstance(pods[1], dict) or pods[1].get("success") is not True:
                raise PlatformCIError("diagnosis fixture requires a successful workload-scoped Pod read")
            pods_arguments = json.loads(pods[0].get("arguments") or "{}")
            if (
                pods_arguments.get("resource") != "pods"
                or pods_arguments.get("namespace") != APP_WORKLOAD_NAMESPACE
                or pods_arguments.get("output") != "json"
            ):
                raise PlatformCIError("diagnosis fixture Pod read is not scoped to the frontend namespace")
            pod_document = pods[1].get("parsed")
            if not isinstance(pod_document, dict):
                try:
                    pod_document = json.loads(str(pods[1].get("stdout") or ""))
                except json.JSONDecodeError as exc:
                    raise PlatformCIError("diagnosis fixture Pod read is not valid JSON") from exc
            pod_items = pod_document.get("items") if isinstance(pod_document, dict) else None
            if not isinstance(pod_items, list):
                raise PlatformCIError("diagnosis fixture Pod read has no observed Pod items")
            pull_failure = None
            for pod in pod_items:
                if not isinstance(pod, dict):
                    continue
                pod_metadata = pod.get("metadata") or {}
                pod_labels = pod_metadata.get("labels") or {}
                if (
                    pod_metadata.get("namespace") != APP_WORKLOAD_NAMESPACE
                    or pod_labels.get("app") != WORKLOAD_NAME
                ):
                    continue
                pod_containers = (pod.get("spec") or {}).get("containers") or []
                server_containers = [
                    item
                    for item in pod_containers
                    if isinstance(item, dict)
                    and item.get("name") == WORKLOAD_CONTAINER
                    and item.get("image") == deployment_image.group(1)
                    and item.get("imagePullPolicy") == "Never"
                ]
                statuses = (pod.get("status") or {}).get("containerStatuses") or []
                for status in statuses:
                    waiting = (
                        status.get("state", {}).get("waiting", {})
                        if isinstance(status, dict)
                        else {}
                    )
                    reason = waiting.get("reason") if isinstance(waiting, dict) else None
                    if (
                        len(server_containers) == 1
                        and isinstance(status, dict)
                        and status.get("name") == WORKLOAD_CONTAINER
                        and status.get("image") == deployment_image.group(1)
                        and reason in {"ErrImageNeverPull", "ImagePullBackOff", "ErrImagePull"}
                    ):
                        pull_failure = reason
                        break
                if pull_failure is not None:
                    break
            if pull_failure is None:
                raise PlatformCIError(
                    "diagnosis fixture did not observe a frontend server Pod with imagePullPolicy "
                    "Never and an image-pull failure"
                )
            incident_id = user.get("incident_id", "")
            message = {
                "content": json.dumps(
                    {
                        "incident_id": incident_id,
                        "root_cause": (
                            f"The frontend server Pod is waiting with {pull_failure} for the observed "
                            f"image {deployment_image.group(1)} under imagePullPolicy Never; the running "
                            "Argo history observation identifies the build revision."
                        ),
                        "category": "deploy",
                        "confidence": 1.0,
                        "evidence": [
                            {
                                "tool": "argocd_app_history",
                                "finding": f"Observed source revision {source_sha} at numeric history ID {observed_id}.",
                            },
                            {
                                "tool": "kubectl_describe",
                                "finding": f"Observed the frontend Deployment image {deployment_image.group(1)}.",
                            },
                            {
                                "tool": "kubectl_get",
                                "finding": (
                                    "Observed the matching frontend server Pod with imagePullPolicy Never "
                                    f"waiting with {pull_failure}."
                                ),
                            },
                        ],
                        "blast_radius_update": "Only atlasops-boutique/frontend is affected.",
                        "next_agent": "remediation",
                        "recommended_fix": ["Reconcile image drift through the owned Argo Application."],
                    }
                )
            }
    elif role == "remediation":
        if "argocd_rollback" not in results:
            observed_id = history_id_from_messages(
                messages,
                app=APP_NAME,
                source_sha=source_sha,
            )
            message = {
                "content": None,
                "tool_calls": [
                    _tool_call(
                        "argocd_rollback",
                        {"app": APP_NAME, "revision": observed_id},
                        call_id,
                    )
                ],
            }
            finish_reason = "tool_calls"
        else:
            action_function, action_result = results["argocd_rollback"]
            action_arguments = json.loads(action_function.get("arguments") or "{}")
            if not isinstance(action_result, dict) or action_result.get("success") is not True:
                raise PlatformCIError("remediation fixture observed an unsuccessful Argo action")
            observed_id = history_id_from_messages(
                messages,
                app=APP_NAME,
                source_sha=source_sha,
            )
            if action_arguments != {"app": APP_NAME, "revision": observed_id}:
                raise PlatformCIError("remediation fixture action differs from observed Argo history")
            deployment_result = results.get("kubectl_get")
            deployment = None
            if deployment_result and isinstance(deployment_result[1], dict):
                stdout = deployment_result[1].get("stdout")
                if isinstance(stdout, str):
                    try:
                        deployment = json.loads(stdout)
                    except json.JSONDecodeError:
                        deployment = None
            ready = (
                isinstance(deployment, dict)
                and deployment.get("kind") == "Deployment"
                and deployment.get("metadata", {}).get("namespace") == APP_WORKLOAD_NAMESPACE
                and deployment.get("metadata", {}).get("name") == WORKLOAD_NAME
                and deployment.get("spec", {}).get("replicas") == 1
                and deployment.get("status", {}).get("availableReplicas", 0) >= 1
                and deployment.get("status", {}).get("observedGeneration", 0)
                >= deployment.get("metadata", {}).get("generation", 1)
            )
            if not ready and prior_tool_calls < 8:
                message = {
                    "content": None,
                    "tool_calls": [
                        _tool_call(
                            "kubectl_get",
                            {
                                "resource": f"deployment/{WORKLOAD_NAME}",
                                "namespace": APP_WORKLOAD_NAMESPACE,
                                "output": "json",
                            },
                            call_id,
                        )
                    ],
                }
                finish_reason = "tool_calls"
            else:
                outcome = "resolved" if ready else "unresolved"
                message = {
                    "content": json.dumps(
                        {
                            "incident_id": user.get("incident_id", ""),
                            "proposed_actions": [
                                {
                                    "tool": "argocd_rollback",
                                    "args": {"app": APP_NAME, "revision": observed_id},
                                }
                            ],
                            "executed_actions": [
                                {
                                    "step": 1,
                                    "tool": "argocd_rollback",
                                    "args": {"app": APP_NAME, "revision": observed_id},
                                    "result": "success",
                                }
                            ],
                            "outcome": outcome,
                            "time_to_resolve_seconds": 1,
                            "next_agent": "comms",
                            "handoff_notes": (
                                "The fixture action reconciled the same source revision."
                                if ready
                                else "The objective deployment read remained unavailable."
                            ),
                        }
                    )
                }
    else:
        if _fixture_tool_names(payload):
            raise PlatformCIError("operational Comms fixture must not expose publication tools")
        incident_id = user.get("incident_id")
        if not isinstance(incident_id, str) or not incident_id:
            raise PlatformCIError("Comms fixture requires the admitted incident identity")
        message = {
            "content": json.dumps(
                {
                    "incident_id": incident_id,
                    "summary": "Synthetic platform CI recovery evidence is ready for review.",
                }
            )
        }

    message["role"] = "assistant"
    return {
        "id": f"fixture-{role}-{prior_tool_calls}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": FIXTURE_MODEL,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def create_fixture_app(api_key: str, source_sha: str):
    from fastapi import FastAPI, HTTPException, Request

    _safe_sha(source_sha, "fixture source")
    if len(api_key) < 32:
        raise PlatformCIError("fixture API key is too short")
    app = FastAPI(title="AtlasOps CI fixture")
    app.state.fixture_requests = []

    def authorize(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        if scheme.casefold() != "bearer" or not hmac.compare_digest(supplied, api_key):
            raise HTTPException(status_code=401, detail="fixture authentication failed")

    @app.get("/healthz")
    async def health():
        return {"ok": True, "classification": FIXTURE_CLASSIFICATION}

    async def models(request: Request):
        authorize(request)
        return {
            "object": "list",
            "data": [{"id": FIXTURE_MODEL, "object": "model", "created": 0, "owned_by": "atlasops-ci"}],
        }

    models.__annotations__["request"] = Request
    app.add_api_route("/v1/models", models, methods=["GET"])

    async def completions(request: Request):
        authorize(request)
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid fixture request") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid fixture request")
        if payload.get("model") != FIXTURE_MODEL:
            raise HTTPException(status_code=400, detail="unexpected fixture model")
        try:
            role = derive_fixture_role(payload)
            response = _fixture_response(payload, role, source_sha=source_sha)
        except PlatformCIError as exc:
            raise HTTPException(status_code=400, detail=sanitize_text(str(exc))) from exc
        calls = response["choices"][0]["message"].get("tool_calls") or []
        summary = {
            "role": role,
            "model": payload.get("model"),
            "tool_names": sorted(_fixture_tool_names(payload)),
            "tool_calls_returned": [
                call["function"]["name"] for call in calls
            ],
            "selected_history_id": (
                json.loads(calls[0]["function"]["arguments"]).get("revision")
                if calls and calls[0]["function"]["name"] == "argocd_rollback"
                else None
            ),
            "request_sha256": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        app.state.fixture_requests.append(summary)
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")), flush=True)
        return response

    completions.__annotations__["request"] = Request
    app.add_api_route("/v1/chat/completions", completions, methods=["POST"])

    return app


def run_fixture_server(key_file: Path, source_sha: str) -> int:
    if not key_file.is_absolute() or not key_file.is_file():
        raise PlatformCIError("fixture key file must be an absolute readable file")
    api_key = key_file.read_text(encoding="utf-8").strip()
    app = create_fixture_app(api_key, source_sha)
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=True, log_level="info")
    return 0


def validate_pre_fault(
    application: dict[str, Any],
    deployment: dict[str, Any],
    prometheus_rule: dict[str, Any] | None,
    active_alerts: list[dict[str, Any]],
    *,
    source_sha: str,
) -> dict[str, Any]:
    _safe_sha(source_sha, "pre-fault source")
    metadata = application.get("metadata") if isinstance(application, dict) else None
    spec = application.get("spec") if isinstance(application, dict) else None
    status = application.get("status") if isinstance(application, dict) else None
    source = spec.get("source") if isinstance(spec, dict) else None
    sync = status.get("sync") if isinstance(status, dict) else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("name") != APP_NAME
        or metadata.get("namespace") != APP_NAMESPACE
        or not isinstance(source, dict)
        or source.get("repoURL") != EXPECTED_REPO_URL
        or source.get("path") != APP_PATH
        or source.get("targetRevision") != source_sha
        or spec.get("sources")
        or spec.get("operation")
        or not isinstance(sync, dict)
        or sync.get("status") != "Synced"
        or sync.get("revision") != source_sha
    ):
        raise PlatformCIError("pre-fault Argo Application is not synced to the exact owned source")
    operation_state = status.get("operationState")
    if isinstance(operation_state, dict) and operation_state.get("phase") in {
        "Running",
        "Terminating",
        "Error",
    }:
        raise PlatformCIError("pre-fault Argo Application has an active or failed operation")

    history = status.get("history")
    if not isinstance(history, list):
        raise PlatformCIError("pre-fault Argo history is unavailable")
    eligible = []
    for item in history:
        if not isinstance(item, dict) or item.get("revision") != source_sha:
            continue
        item_source = item.get("source")
        if (
            not isinstance(item_source, dict)
            or item_source.get("repoURL") != EXPECTED_REPO_URL
            or item_source.get("path") != APP_PATH
            or item.get("sources")
        ):
            continue
        item_id = item.get("id")
        if type(item_id) is int and item_id >= 0:
            eligible.append((str(item_id), item))
        elif isinstance(item_id, str) and item_id.isascii() and item_id.isdigit():
            eligible.append((item_id, item))
    if len(eligible) != 1:
        raise PlatformCIError("pre-fault source must have exactly one eligible numeric Argo history entry")

    if not isinstance(prometheus_rule, dict) or prometheus_rule.get("name") != ALERT_NAME:
        raise PlatformCIError("the actual Prometheus availability rule is not loaded")
    expression = prometheus_rule.get("query") or prometheus_rule.get("expr")
    labels = prometheus_rule.get("labels")
    if (
        not isinstance(expression, str)
        or not re.search(r'namespace\s*=\s*["\']atlasops-boutique["\']', expression)
        or not re.search(r"frontend", expression)
        or not isinstance(labels, dict)
        or labels.get("atlasops_route") != "coordinator"
    ):
        raise PlatformCIError(
            "loaded Prometheus rule does not select atlasops-boutique/frontend for coordinator delivery"
        )

    deployment_metadata = deployment.get("metadata") if isinstance(deployment, dict) else None
    deployment_api_version = deployment.get("apiVersion") if isinstance(deployment, dict) else None
    deployment_kind = deployment.get("kind") if isinstance(deployment, dict) else None
    deployment_annotations = (
        deployment_metadata.get("annotations")
        if isinstance(deployment_metadata, dict)
        else None
    )
    deployment_spec = deployment.get("spec") if isinstance(deployment, dict) else None
    template = deployment_spec.get("template") if isinstance(deployment_spec, dict) else None
    template_spec = template.get("spec") if isinstance(template, dict) else None
    containers = template_spec.get("containers") if isinstance(template_spec, dict) else None
    if (
        deployment_api_version != "apps/v1"
        or deployment_kind != "Deployment"
        or not isinstance(deployment_metadata, dict)
        or deployment_metadata.get("name") != WORKLOAD_NAME
        or deployment_metadata.get("namespace") != APP_WORKLOAD_NAMESPACE
        or not isinstance(deployment_spec, dict)
        or deployment_spec.get("replicas") != 1
        or not isinstance(deployment_annotations, dict)
        or deployment_annotations.get("argocd.argoproj.io/tracking-id")
        != f"{APP_NAME}:apps/Deployment:{APP_WORKLOAD_NAMESPACE}/{WORKLOAD_NAME}"
        or not isinstance(containers, list)
        or len([item for item in containers if isinstance(item, dict) and item.get("name") == WORKLOAD_CONTAINER]) != 1
    ):
        raise PlatformCIError("pre-fault frontend Deployment is not the expected Argo-owned single replica")
    for alert in active_alerts:
        labels = alert.get("labels") if isinstance(alert, dict) else None
        if (
            isinstance(labels, dict)
            and labels.get("alertname") == ALERT_NAME
            and labels.get("namespace") == APP_WORKLOAD_NAMESPACE
            and labels.get("deployment") == WORKLOAD_NAME
        ):
            raise PlatformCIError("frontend availability alert was already active before the CI fault")
    history_id, entry = eligible[0]
    return {
        "history_id": history_id,
        "history_revision": entry["revision"],
        "application_revision": sync["revision"],
        "deployment_uid": deployment_metadata.get("uid"),
        "deployment_generation": deployment_metadata.get("generation"),
        "desired_replicas": deployment_spec["replicas"],
    }


def current_owner_matches(expected: ClusterOwnership, observed: ClusterOwnership) -> bool:
    return expected == observed


def make_wrong_digest(digest: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PlatformCIError("pending action digest is malformed")
    replacement = "1" if digest[0] == "0" else "0"
    return replacement + digest[1:]


def validate_rejected_decision(
    status_code: int,
    response: Any,
    pending_before: dict[str, Any],
    pending_after: dict[str, Any] | None,
    *,
    workload_still_broken: bool,
    mutation_events_before: set[str],
    mutation_events_after: set[str],
) -> None:
    if (
        status_code != 400
        or not isinstance(response, dict)
        or response.get("error") != "action_digest_mismatch"
    ):
        raise PlatformCIError("wrong approval digest was not rejected by the operator API")
    if (
        not isinstance(pending_after, dict)
        or pending_after.get("token") != pending_before.get("token")
        or pending_after.get("action_digest") != pending_before.get("action_digest")
        or not workload_still_broken
        or mutation_events_before != mutation_events_after
    ):
        raise PlatformCIError("wrong digest changed pending state, workload state, or agent mutations")


def _check_by_name(verification: dict[str, Any], name: str) -> dict[str, Any]:
    checks = verification.get("checks")
    if not isinstance(checks, list):
        raise PlatformCIError("environment verifier did not return checks")
    matches = [check for check in checks if isinstance(check, dict) and check.get("name") == name]
    if len(matches) != 1:
        raise PlatformCIError(f"environment verifier must return one {name} check")
    return matches[0]


def validate_verification(
    verification: dict[str, Any],
    *,
    expected_uid: str,
    namespace: str = APP_WORKLOAD_NAMESPACE,
    deployment: str = WORKLOAD_NAME,
) -> dict[str, Any]:
    if verification.get("env_resolved") is not True or verification.get("verification_status") != "passed":
        raise PlatformCIError("authoritative environment verifier did not pass")
    deployment_check = _check_by_name(verification, "operational_deployment_current")
    observed = deployment_check.get("observed")
    if (
        deployment_check.get("passed") is not True
        or deployment_check.get("target") != f"{namespace}/{deployment}"
        or not isinstance(observed, dict)
        or observed.get("uid") != expected_uid
        or type(observed.get("generation")) is not int
        or type(observed.get("observed_generation")) is not int
        or observed["observed_generation"] < observed["generation"]
        or type(observed.get("desired_replicas")) is not int
        or observed["desired_replicas"] <= 0
        or type(observed.get("available_replicas")) is not int
        or observed["available_replicas"] < observed["desired_replicas"]
    ):
        raise PlatformCIError("authoritative verifier lacks a current UID/generation/replica proof")
    freshness = []
    for metric_name in (
        "kube_deployment_spec_replicas",
        "kube_deployment_status_replicas_available",
    ):
        check = _check_by_name(verification, f"operational_metric_{metric_name}_fresh")
        sample = check.get("observed")
        if (
            check.get("passed") is not True
            or check.get("target") != f"{namespace}/{deployment}"
            or not isinstance(sample, dict)
            or type(sample.get("value")) not in {int, float}
            or sample["value"] <= 0
            or type(sample.get("source_sample_age_at_observation_seconds")) not in {int, float}
            or sample["source_sample_age_at_observation_seconds"] < 0
            or sample["source_sample_age_at_observation_seconds"] > sample.get("max_sample_age_seconds", 0)
            or sample.get("query")
            != f'{metric_name}{{namespace="{namespace}",deployment="{deployment}"}}'
        ):
            raise PlatformCIError(f"authoritative verifier lacks a fresh scoped {metric_name} sample")
        freshness.append(sample)
    recovered = _check_by_name(verification, "operational_kube_state_metrics_recovered")
    alert = _check_by_name(verification, "operational_alert_cleared")
    recovered_observed = recovered.get("observed")
    if (
        recovered.get("passed") is not True
        or not isinstance(recovered_observed, dict)
        or recovered_observed.get("deployment_spec_replicas") != observed["desired_replicas"]
        or recovered_observed.get("deployment_status_replicas_available") < observed["desired_replicas"]
        or alert.get("passed") is not True
        or alert.get("target") != f"{namespace}/{deployment}"
    ):
        raise PlatformCIError("authoritative verifier did not prove replica recovery and alert clearance")
    return {
        "verification_timestamp": verification.get("verification_timestamp"),
        "deployment": observed,
        "fresh_metric_samples": freshness,
        "alert_clearance": alert,
    }


def _validate_operation_export(
    api_evidence: dict[str, Any],
    *,
    source_sha: str,
    audit_secret: str,
) -> dict[str, Any]:
    incident_id = api_evidence.get("incident_id")
    execution_id = api_evidence.get("execution_id")
    record = api_evidence.get("record")
    integrity = api_evidence.get("integrity")
    audit_integrity = api_evidence.get("audit_integrity")
    events = api_evidence.get("events")
    audit_rows = api_evidence.get("audit")
    if (
        not isinstance(incident_id, str)
        or not incident_id
        or not isinstance(execution_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", execution_id)
        or not isinstance(record, dict)
        or record.get("incident_id") != incident_id
        or record.get("execution_id") != execution_id
        or api_evidence.get("source_sha") != source_sha
        or record.get("source_sha") != source_sha
    ):
        raise PlatformCIError("operational evidence is not bound to this source, incident, and execution")
    if (
        not isinstance(audit_secret, str)
        or not audit_secret
        or not isinstance(audit_integrity, dict)
        or audit_integrity.get("ok") is not True
        or type(audit_integrity.get("entries")) is not int
        or audit_integrity["entries"] < 1
    ):
        raise PlatformCIError("operations audit integrity evidence is unavailable")
    if api_evidence.get("audit_scope") != "incident_records":
        raise PlatformCIError("operations evidence does not export audit_scope=incident_records")
    if not isinstance(integrity, dict) or integrity.get("ok") is not True:
        raise PlatformCIError("execution journal integrity evidence is unavailable")
    if not isinstance(events, list) or not events:
        raise PlatformCIError("execution evidence lacks its durable journal transitions")
    if not isinstance(audit_rows, list) or not audit_rows:
        raise PlatformCIError("operations evidence lacks same-incident audit rows")
    if audit_integrity["entries"] < len(audit_rows):
        raise PlatformCIError("incident audit export exceeds the verified global audit count")

    rows_by_hash: dict[str, dict[str, Any]] = {}
    audit_key = audit_secret.encode("utf-8")
    for row in audit_rows:
        if not isinstance(row, dict) or row.get("incident_id") != incident_id:
            raise PlatformCIError("audit export contains rows outside the same incident")
        entry_hash = row.get("entry_hash")
        signature = row.get("signature")
        previous_hash = row.get("prev_hash")
        if (
            not isinstance(entry_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry_hash)
            or entry_hash in rows_by_hash
            or not isinstance(signature, str)
            or not re.fullmatch(r"[0-9a-f]{64}", signature)
            or not isinstance(previous_hash, str)
            or (previous_hash and not re.fullmatch(r"[0-9a-f]{64}", previous_hash))
        ):
            raise PlatformCIError("same-incident audit rows contain invalid or duplicate signatures")
        signed_fields = {
            key: value
            for key, value in row.items()
            if key not in {"signature", "entry_hash"}
        }
        canonical = json.dumps(signed_fields, sort_keys=True)
        expected_signature = hmac.new(
            audit_key,
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        expected_hash = hashlib.sha256((canonical + signature).encode("utf-8")).hexdigest()
        if (
            not hmac.compare_digest(signature, expected_signature)
            or not hmac.compare_digest(entry_hash, expected_hash)
        ):
            raise PlatformCIError("incident audit row signature or entry hash is invalid")
        rows_by_hash[entry_hash] = row

    verified_references = []
    seen_references: set[str] = set()
    previous_hash = ""
    for event in events:
        event_record = event.get("record") if isinstance(event, dict) else None
        if (
            not isinstance(event, dict)
            or not isinstance(event_record, dict)
            or event.get("execution_id") != execution_id
            or event_record.get("incident_id") != incident_id
            or event_record.get("execution_id") != execution_id
            or event_record.get("source_sha") != source_sha
        ):
            raise PlatformCIError("execution journal transition is not bound to this source and identity")
        timestamp = event.get("ts")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not 0 <= timestamp < float("inf")
            or event.get("prev_hash") != previous_hash
        ):
            raise PlatformCIError("execution journal transition time or linkage is invalid")
        entry_hash = event.get("audit_entry_hash")
        if (
            not isinstance(entry_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry_hash)
            or entry_hash in seen_references
        ):
            raise PlatformCIError("execution-progress reference is invalid or duplicated")
        transition = {
            key: value
            for key, value in event.items()
            if key != "audit_entry_hash"
        }
        transition_audit_hash = hashlib.sha256(
            json.dumps(transition, sort_keys=True).encode("utf-8")
        ).hexdigest()
        row = rows_by_hash.get(entry_hash)
        if (
            row is None
            or row.get("agent_role") != "coordinator"
            or row.get("action_type") != "execution_progress"
            or row.get("tool_args_hash") != transition_audit_hash
        ):
            raise PlatformCIError(
                "execution-progress audit reference is missing or does not verify its signed transition"
            )
        try:
            serialized = json.dumps(
                event,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise PlatformCIError("execution journal transition is not canonical JSON") from exc
        previous_hash = hashlib.sha256((previous_hash + serialized).encode("utf-8")).hexdigest()
        seen_references.add(entry_hash)
        verified_references.append(
            {
                "audit_entry_hash": entry_hash,
                "transition_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            }
        )

    if (
        type(integrity.get("events")) is not int
        or integrity["events"] != len(events)
        or integrity.get("last_hash") != previous_hash
        or events[-1].get("record") != record
        or events[-1].get("phase") != api_evidence.get("phase")
        or events[-1].get("state") != api_evidence.get("state")
    ):
        raise PlatformCIError("execution journal snapshot differs from its signed transition chain")
    return {
        "incident_id": incident_id,
        "execution_id": execution_id,
        "record": record,
        "events": events,
        "audit_rows": audit_rows,
        "rows_by_hash": rows_by_hash,
        "audit_integrity": audit_integrity,
        "execution_integrity": integrity,
        "execution_progress_references": verified_references,
    }


def _prepared_action_digest(action: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            action,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _validate_approved_mutation_start(
    verified_export: dict[str, Any],
    *,
    source_sha: str,
    history_id: str,
    expected_operator: str,
) -> dict[str, Any]:
    events = verified_export["events"]
    action_prepared = [
        (index, event)
        for index, event in enumerate(events)
        if event.get("event") == "action_prepared"
    ]
    approval_decided = [
        (index, event)
        for index, event in enumerate(events)
        if event.get("event") == "approval_decided"
    ]
    mutation_started = [
        (index, event)
        for index, event in enumerate(events)
        if event.get("event") == "mutation_started"
    ]
    mutation_uncertain = [event for event in events if event.get("event") == "mutation_uncertain"]
    if (
        len(action_prepared) != 1
        or len(approval_decided) != 1
        or len(mutation_started) != 1
        or mutation_uncertain
    ):
        raise PlatformCIError(
            "execution journal must contain one prepared action, exact approval, and mutation start"
        )
    prepared_index, prepared_event = action_prepared[0]
    approved_index, approved_event = approval_decided[0]
    started_index, started_event = mutation_started[0]
    if not prepared_index < approved_index < started_index:
        raise PlatformCIError("durable mutation start did not follow the exact-action approval")
    if (
        prepared_event.get("phase") != "approval"
        or approved_event.get("phase") != "approval"
        or started_event.get("phase") != "applying"
        or started_event.get("state") != "running"
    ):
        raise PlatformCIError("durable approval and mutation dispatch phases are inconsistent")

    prepared_action = prepared_event.get("record", {}).get("prepared_action")
    if not isinstance(prepared_action, dict):
        raise PlatformCIError("durable action preparation event omitted its action")
    arguments = prepared_action.get("arguments")
    target = prepared_action.get("target")
    verification_target = prepared_action.get("verification_target")
    patch = prepared_action.get("patch")
    if (
        prepared_action.get("tool") != "argocd_rollback"
        or arguments != {"app": APP_NAME, "revision": history_id}
        or not isinstance(target, dict)
        or target.get("kind") != "Application"
        or target.get("namespace") != APP_NAMESPACE
        or target.get("name") != APP_NAME
        or not isinstance(target.get("uid"), str)
        or not target["uid"]
        or not isinstance(verification_target, dict)
        or verification_target.get("kind") != "Deployment"
        or verification_target.get("namespace") != APP_WORKLOAD_NAMESPACE
        or verification_target.get("name") != WORKLOAD_NAME
        or not isinstance(verification_target.get("uid"), str)
        or not verification_target["uid"]
        or not isinstance(patch, list)
    ):
        raise PlatformCIError("durable prepared action is outside the exact observed Argo target")
    replacement = [
        item.get("value", {}).get("targetRevision")
        for item in patch
        if isinstance(item, dict)
        and item.get("op") == "replace"
        and item.get("path") == "/spec/source"
        and isinstance(item.get("value"), dict)
    ]
    operation_revision = [
        item.get("value", {}).get("sync", {}).get("revision")
        for item in patch
        if isinstance(item, dict)
        and item.get("op") == "add"
        and item.get("path") == "/operation"
        and isinstance(item.get("value"), dict)
    ]
    if replacement != [source_sha] or operation_revision != [source_sha]:
        raise PlatformCIError("durable prepared action does not select the exact source revision")

    action_digest = _prepared_action_digest(prepared_action)
    approval = approved_event.get("record", {}).get("approval")
    started_record = started_event.get("record")
    started_approval = started_record.get("approval") if isinstance(started_record, dict) else None
    if (
        not isinstance(approval, dict)
        or approval.get("decision") != "approved"
        or approval.get("approved_by") != expected_operator
        or approval.get("action_digest") != action_digest
        or approval.get("action") != prepared_action
        or not isinstance(started_record, dict)
        or started_record.get("prepared_action") != prepared_action
        or not isinstance(started_approval, dict)
        or started_approval.get("decision") != "approved"
        or started_approval.get("approved_by") != expected_operator
        or started_approval.get("action_digest") != action_digest
        or started_approval.get("action") != prepared_action
        or started_approval.get("status") not in {None, "approved"}
    ):
        raise PlatformCIError("durable mutation start is not bound to the approved prepared action")

    decision_arguments_hash = hashlib.sha256(
        json.dumps(
            {"action_digest": action_digest, "decision": "approved"},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    operator_decisions = [
        row
        for row in verified_export["audit_rows"]
        if row.get("incident_id") == verified_export["incident_id"]
        and row.get("agent_role") == "operator"
        and row.get("action_type") == "operator_decision"
        and row.get("approved_by") == expected_operator
        and row.get("tool_args_hash") == decision_arguments_hash
        and row.get("policy_check") == "accepted"
        and row.get("result_summary") == "approved"
    ]
    if len(operator_decisions) != 1:
        raise PlatformCIError("incident audit lacks one authenticated exact-action operator decision")

    return {
        "action": prepared_action,
        "action_digest": action_digest,
        "execution_id": verified_export["execution_id"],
        "action_prepared_index": prepared_index,
        "approval_event": approved_event,
        "approval_index": approved_index,
        "approval_timestamp": approved_event.get("ts"),
        "mutation_started_event": started_event,
        "mutation_started_index": started_index,
        "mutation_started_timestamp": started_event.get("ts"),
        "operator_decision_audit_hash": operator_decisions[0].get("entry_hash"),
    }


def _validate_approved_mutation_return(
    verified: dict[str, Any], dispatch: dict[str, Any],
) -> dict[str, Any]:
    events = verified["events"]
    returned = [
        (index, event) for index, event in enumerate(events)
        if event.get("event") == "mutation_returned"
    ]
    if (
        len(returned) != 1
        or returned[0][0] <= dispatch["mutation_started_index"]
        or any(event.get("event") == "mutation_uncertain" for event in events)
    ):
        raise PlatformCIError("execution journal must contain one successful post-approval mutation return")
    returned_event = returned[0][1]
    returned_record = returned_event.get("record")
    started_record = dispatch["mutation_started_event"].get("record")
    action_result = returned_record.get("action_result") if isinstance(returned_record, dict) else None
    if (
        not isinstance(returned_record, dict)
        or not isinstance(started_record, dict)
        or returned_event.get("phase") != "verification"
        or returned_event.get("state") != "running"
        or returned_record.get("prepared_action") != dispatch["action"]
        or returned_record.get("approval") != started_record.get("approval")
        or not isinstance(action_result, dict)
        or action_result.get("success") is not True
        or action_result.get("atomic_preconditions") is not True
        or action_result.get("action_digest") != dispatch["action_digest"]
        or action_result.get("target") != dispatch["action"].get("target")
    ):
        raise PlatformCIError("durable mutation return does not prove the prepared action succeeded")
    return {
        **dispatch,
        "action_result": action_result,
        "mutation_returned_timestamp": returned_event.get("ts"),
    }


def validate_completion_evidence(
    api_evidence: dict[str, Any],
    *,
    source_sha: str,
    history_id: str,
    expected_operator: str,
    audit_secret: str,
) -> dict[str, Any]:
    verified = _validate_operation_export(
        api_evidence,
        source_sha=source_sha,
        audit_secret=audit_secret,
    )
    record = verified["record"]
    if (
        api_evidence.get("phase") != "finished"
        or api_evidence.get("state") != "completed"
        or api_evidence.get("certification_status") != CERTIFICATION_STATUS
        or record.get("env_resolved") is not True
        or record.get("resolved") is not True
        or record.get("certification_status") != CERTIFICATION_STATUS
    ):
        raise PlatformCIError("completed operations evidence is not verified and NOT_CERTIFIED")

    for role, required_key in (
        ("triage", "severity"),
        ("diagnosis", "root_cause"),
        ("remediation", "outcome"),
        ("comms", "summary_for_dashboard"),
    ):
        result = record.get(role)
        final = result.get("final") if isinstance(result, dict) else None
        if not isinstance(final, dict) or not final.get(required_key):
            raise PlatformCIError(f"completed operation is missing its {role} conclusion")
    if record["remediation"]["final"].get("outcome") != "resolved":
        raise PlatformCIError("remediation fixture did not report its observed successful action")
    settling = record.get("settling")
    if not isinstance(settling, dict) or settling.get("settled") is not True:
        raise PlatformCIError("completed operation did not preserve settled environment observations")

    dispatch = _validate_approved_mutation_start(
        verified,
        source_sha=source_sha,
        history_id=history_id,
        expected_operator=expected_operator,
    )
    dispatch = _validate_approved_mutation_return(verified, dispatch)

    verification = record.get("verification")
    remediation_final = record["remediation"]["final"]
    action_list = remediation_final.get("executed_actions")
    if not isinstance(action_list, list):
        action_list = remediation_final.get("actions_taken")
    if not isinstance(action_list, list):
        action_list = []
    mutations = [
        item for item in action_list
        if isinstance(item, dict)
        and item.get("tool") in {
            "argocd_rollback",
            "kubectl_rollout",
            "kubectl_scale",
            "chaos_stop_experiment",
            "alertmanager_silence",
        }
    ]
    if (
        len(mutations) != 1
        or mutations[0].get("tool") != "argocd_rollback"
        or mutations[0].get("args") != {"app": APP_NAME, "revision": history_id}
        or mutations[0].get("success") is not True
        or mutations[0].get("prepared_action") != dispatch["action"]
        or mutations[0].get("approval") != dispatch["mutation_started_event"]["record"].get("approval")
        or not isinstance(mutations[0].get("output"), dict)
        or mutations[0]["output"].get("success") is not True
        or mutations[0]["output"].get("action_digest") != dispatch["action_digest"]
        or mutations[0]["output"].get("target") != dispatch["action"].get("target")
    ):
        raise PlatformCIError(
            "completed evidence does not bind one successful Argo action to its durable approval pair"
        )
    approval = record.get("approval")
    if (
        not isinstance(approval, dict)
        or approval.get("decision") != "approved"
        or approval.get("approved_by") != expected_operator
        or approval.get("action_digest") != dispatch["action_digest"]
    ):
        raise PlatformCIError("completed evidence lacks the server-principal exact-action approval")
    if not isinstance(verification, dict):
        raise PlatformCIError("completed evidence lacks its objective environment verifier record")

    comms = record["comms"]
    comms_final = comms.get("final")
    comms_delivery = comms.get("delivery")
    comms_inference = comms.get("inference")
    inference_final = comms_inference.get("final") if isinstance(comms_inference, dict) else None
    if (
        not isinstance(inference_final, dict)
        or inference_final.get("incident_id") != verified["incident_id"]
        or not isinstance(inference_final.get("summary"), str)
        or not inference_final["summary"].strip()
        or any(key in inference_final for key in ("slack_posted", "postmortem_path", "resolved"))
        or not isinstance(comms_final, dict)
        or comms_final.get("status") != "verified_resolved"
        or comms_final.get("slack_posted") is not False
        or not isinstance(comms_delivery, dict)
        or comms_delivery.get("local_feed_written") is not True
        or comms_delivery.get("postmortem_written") is not True
        or comms_delivery.get("slack") != {"configured": False, "delivered": False}
        or comms_delivery.get("discord") != {"configured": False, "delivered": False}
    ):
        raise PlatformCIError(
            "operational Comms evidence must separate inference from local verifier-bound delivery"
        )
    comms_trajectory = comms.get("trajectory")
    if not isinstance(comms_trajectory, list):
        raise PlatformCIError("Comms did not preserve its tool trajectory")
    comms_tools = [
        item for item in comms_trajectory
        if isinstance(item, dict) and item.get("tool") in {"slack_post_update", "postmortem_draft"}
    ]
    slack_rows = [item for item in comms_tools if item.get("tool") == "slack_post_update"]
    postmortem_rows = [item for item in comms_tools if item.get("tool") == "postmortem_draft"]
    if (
        len(slack_rows) != 1
        or slack_rows[0].get("role") != "comms"
        or slack_rows[0].get("publication_source") != "coordinator_verifier_facts"
        or not isinstance(slack_rows[0].get("output"), dict)
        or slack_rows[0]["output"].get("mode") != "logged_locally"
        or slack_rows[0]["output"].get("success") is not True
        or slack_rows[0]["output"].get("errors")
        or len(postmortem_rows) != 1
        or postmortem_rows[0].get("role") != "comms"
        or postmortem_rows[0].get("publication_source") != "coordinator_verifier_facts"
        or not isinstance(postmortem_rows[0].get("output"), dict)
        or postmortem_rows[0]["output"].get("success") is not True
        or not (postmortem_rows[0]["output"].get("postmortem_path") or postmortem_rows[0]["output"].get("path"))
    ):
        raise PlatformCIError("Comms evidence is missing local-only logging or a saved postmortem")

    expected_tool_results = {
        "triage": {"alertmanager_list_alerts"},
        "diagnosis": {"argocd_app_history", "kubectl_describe"},
        "remediation": {"argocd_rollback"},
        "comms": {"slack_post_update", "postmortem_draft"},
    }
    tool_audit_evidence = [
        {
            "agent_role": row["agent_role"],
            "tool_name": row["tool_name"],
            "entry_hash": row["entry_hash"],
            "action_type": row["action_type"],
        }
        for row in verified["audit_rows"]
        if row.get("action_type") == "tool_result"
        and row.get("agent_role") in expected_tool_results
        and row.get("tool_name") in expected_tool_results[row["agent_role"]]
    ]
    observed_tool_results: dict[str, set[str]] = {}
    for item in tool_audit_evidence:
        observed_tool_results.setdefault(item["agent_role"], set()).add(item["tool_name"])
    if any(
        not required.issubset(observed_tool_results.get(role, set()))
        for role, required in expected_tool_results.items()
    ):
        raise PlatformCIError("signed incident audit lacks the required role and tool-result evidence")

    return {
        "execution_id": verified["execution_id"],
        "incident_id": verified["incident_id"],
        "audit_integrity": verified["audit_integrity"],
        "audit_scope": "incident_records",
        "execution_integrity": verified["execution_integrity"],
        "execution_progress_references": verified["execution_progress_references"],
        "incident_audit_rows": verified["audit_rows"],
        "tool_audit_evidence": tool_audit_evidence,
        "settling": settling,
        "verification": verification,
        "approval": approval,
        "roles": ["triage", "diagnosis", "remediation", "comms"],
        "agent_mutations": mutations,
        "approved_mutation": dispatch,
        "mutation_returned_timestamp": dispatch["mutation_returned_timestamp"],
        "comms_local_log": slack_rows[0]["output"],
        "postmortem": postmortem_rows[0]["output"],
    }


def _float_sample(value: Any) -> float:
    if isinstance(value, bool):
        raise PlatformCIError("telemetry value is not numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PlatformCIError("telemetry value is not numeric") from exc
    if not (result >= 0 and result < float("inf")):
        raise PlatformCIError("telemetry value is not finite and non-negative")
    return result


def _version_labels(
    metric_labels: dict[str, Any],
    target_info: list[dict[str, Any]],
    *,
    source_sha: str,
    service_name: str,
) -> bool:
    service_value = metric_labels.get("service_name", metric_labels.get("service.name"))
    version_value = metric_labels.get("service_version", metric_labels.get("service.version"))
    if service_value == service_name and version_value == source_sha:
        return True
    for item in target_info:
        labels = item.get("metric") if isinstance(item, dict) else None
        if not isinstance(labels, dict):
            continue
        if (
            labels.get("service_name", labels.get("service.name")) == service_name
            and labels.get("service_version", labels.get("service.version")) == source_sha
            and any(
                key in metric_labels and metric_labels.get(key) == labels.get(key)
                for key in ("instance", "job", "otel_scope_name")
            )
        ):
            return True
    return False


def validate_coordinator_telemetry(
    http_series: list[dict[str, Any]],
    incident_series: list[dict[str, Any]],
    target_info: list[dict[str, Any]],
    *,
    source_sha: str,
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    http_matches = []
    for sample in http_series:
        labels = sample.get("metric") if isinstance(sample, dict) else None
        point = sample.get("value") if isinstance(sample, dict) else None
        if not isinstance(labels, dict) or not isinstance(point, list) or len(point) != 2:
            continue
        if (
            labels.get("method") == "POST"
            and labels.get("route") == "/webhook"
            and labels.get("status") == "200"
            and _version_labels(
                labels,
                target_info,
                source_sha=source_sha,
                service_name="atlasops-coordinator",
            )
        ):
            timestamp = _float_sample(point[0])
            value = _float_sample(point[1])
            if abs(now - timestamp) <= 300 and value > 0:
                http_matches.append({"labels": labels, "timestamp": timestamp, "increase": value})
    incident_matches = []
    for sample in incident_series:
        labels = sample.get("metric") if isinstance(sample, dict) else None
        point = sample.get("value") if isinstance(sample, dict) else None
        if not isinstance(labels, dict) or not isinstance(point, list) or len(point) != 2:
            continue
        if (
            labels.get("outcome") == "resolved"
            and _version_labels(
                labels,
                target_info,
                source_sha=source_sha,
                service_name="atlasops-coordinator",
            )
        ):
            timestamp = _float_sample(point[0])
            value = _float_sample(point[1])
            if abs(now - timestamp) <= 300 and value == 1:
                incident_matches.append({"labels": labels, "timestamp": timestamp, "counter": value})
    if len(http_matches) != 1 or len(incident_matches) != 1:
        raise PlatformCIError(
            "an observed coordinator HTTP increase and one resolved-incident counter sample "
            "with the build service.version are required"
        )
    return {
        "http_metric": "atlasops_http_requests_total",
        "http_sample": http_matches[0],
        "incident_metric": "atlasops_incidents_total",
        "incident_sample": incident_matches[0],
        "service_name": "atlasops-coordinator",
        "service_version": source_sha,
    }


def _jaeger_tag_map(span: dict[str, Any]) -> dict[str, Any]:
    attributes = span.get("tags")
    if not isinstance(attributes, list):
        return {}
    return {
        item.get("key"): item.get("value")
        for item in attributes
        if isinstance(item, dict)
    }


def validate_coordinator_trace(
    traces_response: dict[str, Any],
    *,
    source_sha: str,
    incident_id: str,
    execution_id: str,
    tool_audit_evidence: list[dict[str, Any]],
    now_microseconds: int | None = None,
) -> dict[str, Any]:
    now_microseconds = int(time.time() * 1_000_000) if now_microseconds is None else now_microseconds
    traces = traces_response.get("data")
    if not isinstance(traces, list):
        raise PlatformCIError("Jaeger coordinator trace response is malformed")
    expected_role_tools = {
        "triage": {"alertmanager_list_alerts"},
        "diagnosis": {"argocd_app_history", "kubectl_describe"},
        "remediation": {"argocd_rollback"},
        "comms": {"slack_post_update", "postmortem_draft"},
    }
    expected_comms_spans = {"slack_post_update", "postmortem_draft"}
    tool_evidence = [
        item
        for item in tool_audit_evidence
        if isinstance(item, dict)
        and item.get("agent_role") in expected_role_tools
        and item.get("tool_name") in expected_role_tools[item["agent_role"]]
        and item.get("action_type") == "tool_result"
        and re.fullmatch(r"[0-9a-f]{64}", str(item.get("entry_hash") or ""))
    ]
    observed_tools: dict[str, set[str]] = {}
    for item in tool_evidence:
        observed_tools.setdefault(item["agent_role"], set()).add(item["tool_name"])
    if any(
        not required.issubset(observed_tools.get(role, set()))
        for role, required in expected_role_tools.items()
    ):
        raise PlatformCIError("signed incident audit lacks role-bound tool-result trace evidence")

    matching_traces = []
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        processes = trace.get("processes")
        spans = trace.get("spans")
        trace_id = trace.get("traceID")
        if not isinstance(processes, dict) or not isinstance(spans, list) or not isinstance(trace_id, str) or not trace_id:
            continue
        source_spans = []
        for span in spans:
            if not isinstance(span, dict) or span.get("processID") not in processes:
                continue
            process = processes[span["processID"]]
            process_tags = process.get("tags") if isinstance(process, dict) else None
            version = next(
                (
                    item.get("value")
                    for item in process_tags
                    if isinstance(item, dict) and item.get("key") == "service.version"
                ),
                None,
            ) if isinstance(process_tags, list) else None
            started = span.get("startTime")
            if (
                not isinstance(process, dict)
                or process.get("serviceName") != "atlasops-coordinator"
                or version != source_sha
                or type(started) is not int
                or not 0 <= now_microseconds - started <= 300_000_000
            ):
                continue
            source_spans.append((span, _jaeger_tag_map(span), started))
        incident_spans = [
            (span, tags, started)
            for span, tags, started in source_spans
            if span.get("operationName") == "incident"
            and tags.get("incident.id") == incident_id
            and tags.get("execution.id") == execution_id
        ]
        if len(incident_spans) != 1:
            continue
        role_spans = {}
        for role in expected_role_tools:
            matches = [
                (span, tags, started)
                for span, tags, started in source_spans
                if span.get("operationName") == f"agent.{role}"
                and tags.get("agent.role") == role
                and tags.get("incident.id") == incident_id
            ]
            if len(matches) != 1:
                role_spans = {}
                break
            span, _tags, started = matches[0]
            role_spans[role] = {
                "span_id": span.get("spanID"),
                "operation": span.get("operationName"),
                "start_time_microseconds": started,
            }
        if len(role_spans) != len(expected_role_tools):
            continue
        http_spans = [
            (span, tags, started)
            for span, tags, started in source_spans
            if span.get("operationName") == "http.request"
            and tags.get("http.route") == "/webhook"
            and tags.get("http.response.status_code") in {200, "200"}
        ]
        if len(http_spans) != 1:
            continue
        tool_spans = {}
        for tool_name in expected_comms_spans:
            matches = [
                (span, tags, started)
                for span, tags, started in source_spans
                if span.get("operationName") == f"tool.{tool_name}"
                and tags.get("agent.role") == "comms"
                and tags.get("tool.name") == tool_name
                and tags.get("incident.id") == incident_id
            ]
            if len(matches) != 1:
                tool_spans = {}
                break
            span, _tags, started = matches[0]
            tool_spans[tool_name] = {
                "span_id": span.get("spanID"),
                "operation": span.get("operationName"),
                "start_time_microseconds": started,
            }
        if len(tool_spans) != len(expected_comms_spans):
            continue

        incident_span, _incident_tags, incident_started = incident_spans[0]
        http_span, http_tags, http_started = http_spans[0]
        matching_traces.append(
            {
                "trace_id": trace_id,
                "incident_span": {
                    "span_id": incident_span.get("spanID"),
                    "operation": incident_span.get("operationName"),
                    "incident_id": incident_id,
                    "execution_id": execution_id,
                    "service_name": "atlasops-coordinator",
                    "service_version": source_sha,
                    "start_time_microseconds": incident_started,
                },
                "role_spans": role_spans,
                "tool_spans": tool_spans,
                "tool_audit_evidence": tool_evidence,
                "http_request": {
                    "trace_id": trace_id,
                    "span_id": http_span.get("spanID"),
                    "route": http_tags.get("http.route"),
                    "service_name": "atlasops-coordinator",
                    "service_version": source_sha,
                    "start_time_microseconds": http_started,
                },
            }
        )
    if len(matching_traces) == 1:
        return matching_traces[0]
    raise PlatformCIError(
        "Jaeger did not return one fresh source-bound incident trace with all role and Comms tool spans "
        "for the exact execution"
    )


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._form: dict[str, Any] | None = None
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and isinstance(values.get("href"), str):
            self.links.append(values["href"])
        if tag == "form":
            self._form = {
                "action": values.get("action", ""),
                "method": str(values.get("method", "GET")).upper(),
                "fields": {},
                "options": {},
            }
        elif self._form is not None and tag == "input":
            name = values.get("name")
            if isinstance(name, str) and name:
                self._form["fields"][name] = values.get("value", "")
        elif self._form is not None and tag == "textarea":
            name = values.get("name")
            if isinstance(name, str) and name:
                self._form["fields"].setdefault(name, "")
        elif self._form is not None and tag == "select":
            name = values.get("name")
            if isinstance(name, str) and name:
                self._form["options"][name] = []
                self._form["fields"].setdefault(name, "")
                self._select_name = name
        elif self._form is not None and tag == "option":
            name = getattr(self, "_select_name", None)
            if name:
                self._form["options"].setdefault(name, []).append(values.get("value", ""))
                if "selected" in values:
                    self._form["fields"][name] = values.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._select_name = None
        elif tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None


def _checkout_value(name: str, options: list[str]) -> str:
    normalized = name.casefold()
    if "email" in normalized:
        return "platform-ci@example.invalid"
    if "street" in normalized or "address" in normalized:
        return "1 CI Way"
    if "zip" in normalized or "postal" in normalized:
        return "94000"
    if "city" in normalized:
        return "Test"
    if "state" in normalized or "province" in normalized:
        return "CA"
    if "country" in normalized:
        return next((item for item in options if item in {"US", "USA"}), options[0] if options else "US")
    if "card" in normalized and "number" in normalized:
        return "4111111111111111"
    if "expiration" in normalized and "month" in normalized:
        return "12"
    if "expiration" in normalized and "year" in normalized:
        return "2030"
    if "cvv" in normalized or "cvc" in normalized:
        return "123"
    if "quantity" in normalized:
        return "1"
    return "ci"


def exercise_boutique(base_url: str, *, timeout: float = 10) -> dict[str, Any]:
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor())
    paths: list[str] = []

    def request(path: str, data: dict[str, str] | None = None) -> str:
        url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        if urlsplit(url).hostname not in {"127.0.0.1", "localhost"}:
            raise PlatformCIError("Boutique traffic must stay on the loopback-mapped frontend")
        body = urlencode(data).encode() if data is not None else None
        headers = {"User-Agent": "atlasops-platform-ci-fixture/1", "Accept": "text/html"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = Request(url, data=body, headers=headers)
        try:
            with opener.open(req, timeout=timeout) as response:
                if response.status != 200:
                    raise PlatformCIError(f"frontend returned HTTP {response.status} for {path}")
                paths.append(urlsplit(response.geturl()).path)
                return response.read(2_000_000).decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise PlatformCIError(f"frontend request failed for {path}: {type(exc).__name__}") from exc

    home = request("/")
    home_parser = _FormParser()
    home_parser.feed(home)
    product_links = [
        link
        for link in home_parser.links
        if re.search(r"/product/[A-Za-z0-9_-]+/?$", urlsplit(link).path)
    ]
    if not product_links:
        raise PlatformCIError("frontend home did not expose a product link")
    product_path = urlsplit(urljoin(base_url, product_links[0])).path
    product_page = request(product_path)
    product_parser = _FormParser()
    product_parser.feed(product_page)
    cart_form = next(
        (form for form in product_parser.forms if urlsplit(form["action"]).path.rstrip("/") == "/cart"),
        None,
    )
    if cart_form is None:
        raise PlatformCIError("frontend product page did not expose its cart form")
    cart_fields = dict(cart_form["fields"])
    cart_fields.setdefault("product_id", product_path.rstrip("/").split("/")[-1])
    cart_fields["quantity"] = cart_fields.get("quantity") or "1"
    request(cart_form["action"], cart_fields)

    cart_page = request("/cart")
    cart_parser = _FormParser()
    cart_parser.feed(cart_page)
    checkout_form = next(
        (
            form
            for form in cart_parser.forms
            if urlsplit(form["action"]).path.rstrip("/") == "/cart/checkout"
        ),
        None,
    )
    if checkout_form is None:
        raise PlatformCIError("frontend cart page did not expose its checkout form")
    checkout_fields = dict(checkout_form["fields"])
    for name, options in checkout_form["options"].items():
        if not checkout_fields.get(name):
            checkout_fields[name] = _checkout_value(name, options)
    for name in tuple(checkout_fields):
        if not checkout_fields[name]:
            checkout_fields[name] = _checkout_value(
                name,
                checkout_form["options"].get(name, []),
            )
    checkout_page = request(checkout_form["action"], checkout_fields)
    if not checkout_page.strip():
        raise PlatformCIError("frontend checkout returned an empty response")
    return {
        "request_paths": paths,
        "checkout_status": "completed",
        "synthetic_checkout": True,
    }


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 10,
    expect_json: bool = True,
    max_bytes: int = MAX_EVIDENCE_TEXT,
) -> tuple[int, Any]:
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_EXECUTION_EXPORT_BYTES:
        raise PlatformCIError("HTTP response limit must be positive and bounded")
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    request_headers = {"Accept": "application/json" if expect_json else "*/*"}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    request = Request(url, data=body, headers=request_headers, method=method)
    opener = build_opener(ProxyHandler({}), _NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            raw = response.read(max_bytes + 1)
    except HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read(max_bytes + 1)
        finally:
            exc.close()
    except (URLError, TimeoutError, OSError) as exc:
        raise PlatformCIError(f"HTTP request failed for {urlsplit(url).path}: {type(exc).__name__}") from exc
    if len(raw) > max_bytes:
        raise PlatformCIError("HTTP response exceeded the bounded evidence size")
    try:
        text = raw.decode("utf-8")
        parsed = json.loads(text) if expect_json else text
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlatformCIError(f"HTTP response was not bounded UTF-8 JSON: {urlsplit(url).path}") from exc
    return status, parsed


class Runner:
    def __init__(self, identity: RunIdentity, evidence_dir: Path, *, env: dict[str, str]):
        self.identity = identity
        self.evidence_dir = evidence_dir
        self.inherited_env = env
        self.stage = "initializing"
        self.private_temp: tempfile.TemporaryDirectory[str] | None = None
        self.private_dir: Path | None = None
        self.secret_dir: Path | None = None
        self.secret_values: list[str] = []
        self.child_env: dict[str, str] = {}
        self.cluster_name = f"atlasops-ci-{identity.run_id}-{identity.attempt}"
        if len(self.cluster_name) > 63 or not DNS_LABEL_RE.fullmatch(self.cluster_name):
            raise PlatformCIError("derived Kind cluster name is not a DNS label")
        self.context_name = f"kind-{self.cluster_name}"
        self.docker_context = f"atlasops-ci-{identity.run_id}-{identity.attempt}"
        self.kubeconfig = Path()
        self.owner: ClusterOwnership | None = None
        self.cluster_created_by_runner = False
        self.creation_attempted = False
        self.forwards: dict[str, Forward] = {}
        self.data: dict[str, Any] = {
            "schema_version": 1,
            "status": "running",
            "classification": FIXTURE_CLASSIFICATION,
            "certification_status": CERTIFICATION_STATUS,
            "run": asdict(identity),
            "build": {
                "source_sha": identity.source_sha,
                "base_sha": identity.base_sha,
                "image_reference": f"atlasops:{identity.source_sha}",
            },
            "cluster": {
                "name": self.cluster_name,
                "context": self.context_name,
                "created_by_runner": False,
                "ownership": None,
            },
            "observations": {},
            "stages": {},
            "cleanup": {"status": "not_started"},
        }
        self._flush()

    def _flush(self) -> None:
        safe = sanitize_evidence(self.data, self.secret_values)
        write_json(self.evidence_dir / "evidence.json", safe)

    def _record(self, key: str, value: Any) -> None:
        self.data[key] = value
        self._flush()

    def _append_jsonl(self, relative: str, value: Any) -> None:
        path = self.evidence_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        safe = sanitize_evidence(value, self.secret_values)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(safe, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _stage(self, name: str, status: str, **details: Any) -> None:
        record = self.data["stages"].setdefault(name, {})
        if status == "running":
            record.update(started_at=utc_now())
        else:
            record.update(status=status, completed_at=utc_now())
        if details:
            record.update(sanitize_evidence(details, self.secret_values))
        self.stage = name
        self._flush()

    def _ensure_private_area(self) -> None:
        self.private_temp = tempfile.TemporaryDirectory(prefix="atlasops-platform-ci-")
        self.private_dir = Path(self.private_temp.name).resolve()
        try:
            self.private_dir.chmod(0o700)
        except OSError:
            pass
        self.secret_dir = self.private_dir / "secrets"
        self.secret_dir.mkdir(mode=0o700)
        self.kubeconfig = self.private_dir / "ci.kubeconfig"
        docker_config = self.private_dir / "docker"
        helm_config = self.private_dir / "helm"
        home = self.private_dir / "home"
        for path in (docker_config, helm_config, home):
            path.mkdir(mode=0o700)
        path_value = self.inherited_env.get("PATH", "")
        self.child_env = {
            "PATH": path_value,
            "HOME": str(home),
            "TMPDIR": str(self.private_dir),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "DOCKER_CONFIG": str(docker_config),
            "HELM_CONFIG_HOME": str(helm_config),
            "HELM_CACHE_HOME": str(self.private_dir / "helm-cache"),
            "HELM_DATA_HOME": str(self.private_dir / "helm-data"),
            "KUBECONFIG": str(self.kubeconfig),
            "NO_PROXY": "127.0.0.1,localhost,.svc,.svc.cluster.local",
            "no_proxy": "127.0.0.1,localhost,.svc,.svc.cluster.local",
            "ATLASOPS_SECRET_DIR": str(self.secret_dir),
            "ATLASOPS_KIND_CLUSTER_NAME": self.cluster_name,
            "ATLASOPS_VLLM_BASE": f"http://{FIXTURE_SERVICE}.default.svc.cluster.local:8080/v1",
            "ATLASOPS_AGENT_MODEL": FIXTURE_MODEL,
            "ATLASOPS_OPERATOR_ID": "ci-synthetic-operator",
            "ATLASOPS_AUTO_HF_INFERENCE": "0",
            "ATLASOPS_USE_HF_INFERENCE": "0",
            "ATLASOPS_LIVE_JUDGE": "0",
            "PYTHON_BIN": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if "RUNNER_TEMP" in self.inherited_env:
            self.child_env["RUNNER_TEMP"] = self.inherited_env["RUNNER_TEMP"]
        self._record(
            "private_runtime",
            {
                "external_temp_root": str(self.private_dir),
                "generated_credentials_external": True,
                "kubeconfig_external": True,
                "docker_context_external": True,
            },
        )

    def _command(
        self,
        command: list[str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        check: bool = True,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
        label: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command_env = self.child_env if env is None else env
        try:
            result = subprocess.run(
                command,
                cwd=REPO_ROOT,
                env=command_env,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            self._append_jsonl(
                "logs/commands.jsonl",
                {
                    "stage": self.stage,
                    "label": label,
                    "command": command,
                    "returncode": result.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                },
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            self._append_jsonl(
                "logs/commands.jsonl",
                {
                    "stage": self.stage,
                    "label": label,
                    "command": command,
                    "timeout_seconds": timeout,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": True,
                },
            )
            raise PlatformCIError(f"{label or command[0]} timed out after {timeout:g} seconds") from exc
        if check and result.returncode:
            raise PlatformCIError(
                f"{label or command[0]} failed with exit code {result.returncode}"
            )
        return result

    def _http(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float = 10,
        label: str,
        expect_json: bool = True,
        max_bytes: int = MAX_EVIDENCE_TEXT,
    ) -> tuple[int, Any]:
        status, body = http_json(
            url,
            method=method,
            headers=headers,
            payload=payload,
            timeout=timeout,
            expect_json=expect_json,
            max_bytes=max_bytes,
        )
        self._append_jsonl(
            "logs/http-events.jsonl",
            {
                "stage": self.stage,
                "label": label,
                "method": method,
                "path": urlsplit(url).path,
                "status": status,
                "request": payload or {},
                "response": body,
            },
        )
        return status, body

    def _git_clean(self) -> None:
        if _git(REPO_ROOT, ["status", "--porcelain", "--untracked-files=all"]):
            raise PlatformCIError("source checkout changed during disposable platform setup")

    def _docker(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return self._command(
            ["docker", "--context", self.docker_context, *args],
            **kwargs,
        )

    def _kind(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return self._command(["kind", *args], **kwargs)

    def _kubectl(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return self._command(
            [
                "kubectl",
                f"--kubeconfig={self.kubeconfig}",
                f"--context={self.context_name}",
                *args,
            ],
            **kwargs,
        )

    def _kubectl_json(self, *args: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        result = self._kubectl(*args, "-o", "json", timeout=timeout)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise PlatformCIError("kubectl returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise PlatformCIError("kubectl JSON response is not an object")
        return payload

    def _prepare_docker_context(self) -> None:
        self._stage("docker_context", "running")
        socket_path = self.inherited_env.get(
            "ATLASOPS_PLATFORM_CI_DOCKER_SOCKET",
            "/var/run/docker.sock",
        )
        if socket_path != "/var/run/docker.sock":
            raise PlatformCIError("platform-ci accepts only the GitHub-hosted local Docker socket")
        if self.private_dir is None:
            raise PlatformCIError("private runtime paths were not initialized")
        context_env = {
            "PATH": self.child_env["PATH"],
            "HOME": self.child_env["HOME"],
            "DOCKER_CONFIG": self.child_env["DOCKER_CONFIG"],
            "LANG": self.child_env["LANG"],
            "LC_ALL": self.child_env["LC_ALL"],
        }
        self._command(
            [
                "docker",
                "context",
                "create",
                self.docker_context,
                "--docker",
                f"host=unix://{socket_path}",
                "--description",
                "Disposable AtlasOps GitHub Actions Kind runner",
            ],
            env=context_env,
            timeout=20,
            label="create explicit local Docker context",
        )
        host_result = self._command(
            [
                "docker",
                "context",
                "inspect",
                self.docker_context,
                "--format",
                "{{json .Endpoints.docker.Host}}",
            ],
            env=context_env,
            timeout=20,
            label="inspect explicit Docker endpoint",
        )
        try:
            endpoint = json.loads(host_result.stdout.strip())
        except json.JSONDecodeError as exc:
            raise PlatformCIError("Docker context did not return a valid endpoint") from exc
        if endpoint != f"unix://{socket_path}":
            raise PlatformCIError("Docker context is not bound to the local GitHub runner socket")
        self.child_env["DOCKER_HOST"] = endpoint
        self.child_env["DOCKER_CONTEXT"] = self.docker_context
        info = self._docker("info", "--format", "{{.OSType}}|{{.ServerVersion}}", timeout=20)
        os_type, _, server_version = info.stdout.strip().partition("|")
        if os_type != "linux" or not server_version:
            raise PlatformCIError("local Docker daemon identity is not a usable Linux builder")
        self.data["docker"] = {
            "context": self.docker_context,
            "endpoint": "unix:///var/run/docker.sock",
            "server_os": os_type,
            "server_version": server_version,
        }
        self._stage("docker_context", "completed")

    def _prepare_secrets(self) -> None:
        self._stage("runtime_secrets", "running")
        if self.secret_dir is None or self.private_dir is None:
            raise PlatformCIError("private secret directory was not initialized")
        api_key_file = self.private_dir / "fixture-api-key"
        api_key_file.write_text(__import__("secrets").token_urlsafe(48), encoding="utf-8")
        try:
            api_key_file.chmod(0o600)
        except OSError:
            pass
        secret_values = [api_key_file.read_text(encoding="utf-8").strip()]
        self.secret_values.extend(secret_values)
        self._command(
            [
                sys.executable,
                "scripts/generate_runtime_secrets.py",
                "--output-dir",
                str(self.secret_dir),
                "--llm-api-key-file",
                str(api_key_file),
            ],
            timeout=20,
            label="generate disposable runtime credentials outside the checkout",
        )
        for secret_path in self.secret_dir.glob("*.secret"):
            if secret_path.is_file():
                value = secret_path.read_text(encoding="utf-8").strip()
                if value:
                    self.secret_values.append(value)
        self.data["private_runtime"]["secret_files"] = len(list(self.secret_dir.glob("*.secret")))
        self._stage("runtime_secrets", "completed", generated_outside_checkout=True)

    def _render_manifests(self) -> None:
        self._stage("render_manifests", "running")
        output_dir = self.evidence_dir / "rendered-manifests"
        if output_dir.exists():
            raise PlatformCIError("rendered manifest evidence directory already exists")
        self._command(
            [
                sys.executable,
                "scripts/verify_platform_infrastructure.py",
                "render",
                "--source-sha",
                self.identity.source_sha,
                "--vllm-base",
                self.child_env["ATLASOPS_VLLM_BASE"],
                "--agent-model",
                FIXTURE_MODEL,
                "--operator-identity",
                self.child_env["ATLASOPS_OPERATOR_ID"],
                "--output-dir",
                str(output_dir),
            ],
            timeout=30,
            label="render source-bound platform manifests",
        )
        for path in output_dir.glob("*.yaml"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^kind:\s*(?:Secret|SecretList)\s*$", text):
                raise PlatformCIError("refusing to archive a Kubernetes Secret manifest")
        files = sorted(path.name for path in output_dir.iterdir() if path.is_file())
        if not files:
            raise PlatformCIError("platform renderer produced no manifest evidence")
        self._record("rendered_manifests", {"files": files, "secret_manifests_archived": False})
        self._stage("render_manifests", "completed")

    def _record_fixture_identity(self) -> None:
        if self.private_dir is None:
            raise PlatformCIError("private runtime directory is unavailable")
        llm_key = self.private_dir / "fixture-api-key"
        key = llm_key.read_text(encoding="utf-8").strip()
        self._record(
            "fixture",
            {
                "model": FIXTURE_MODEL,
                "provider_kind": "fixture/no-model",
                "auth_required": True,
                "service": FIXTURE_SERVICE,
                "service_type": "ClusterIP",
                "service_account_token_automount": False,
                "external_model_calls": False,
                "pretrained_inference": False,
            },
        )
        self._fixture_api_key = key

    def _cluster_list(self) -> set[str]:
        output = self._kind("get", "clusters", timeout=30, label="inspect Kind cluster inventory")
        return {line.strip() for line in output.stdout.splitlines() if line.strip()}

    def _load_kubeconfig_server(self) -> str:
        try:
            import yaml
        except ImportError as exc:
            raise PlatformCIError("PyYAML is required to verify the generated kubeconfig") from exc
        if not self.kubeconfig.is_file():
            raise PlatformCIError("Kind did not write the external kubeconfig")
        config = yaml.safe_load(self.kubeconfig.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or config.get("current-context") != self.context_name:
            raise PlatformCIError("external kubeconfig selected a different context")
        contexts = config.get("contexts")
        clusters = config.get("clusters")
        if not isinstance(contexts, list) or not isinstance(clusters, list):
            raise PlatformCIError("external kubeconfig is malformed")
        matching_context = [
            item for item in contexts
            if isinstance(item, dict) and item.get("name") == self.context_name
        ]
        if len(matching_context) != 1:
            raise PlatformCIError("generated kubeconfig does not contain the exact Kind context")
        cluster_ref = matching_context[0].get("context", {}).get("cluster")
        matching_cluster = [
            item for item in clusters
            if isinstance(item, dict) and item.get("name") == cluster_ref
        ]
        if len(matching_cluster) != 1:
            raise PlatformCIError("generated kubeconfig does not contain the exact cluster endpoint")
        server = matching_cluster[0].get("cluster", {}).get("server")
        if not isinstance(server, str):
            raise PlatformCIError("generated kubeconfig API server is missing")
        parsed = urlsplit(server)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise PlatformCIError("Kind API server must be a loopback HTTPS endpoint")
        return server.rstrip("/")

    @staticmethod
    def _active_bindings(value: Any, description: str) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(value, dict):
            raise PlatformCIError(f"{description} Docker port map is missing")
        active = {}
        for port, bindings in value.items():
            if bindings is None:
                continue
            if not isinstance(bindings, list):
                raise PlatformCIError(f"{description} Docker port map is malformed")
            if bindings:
                active[port] = bindings
        return active

    @staticmethod
    def _loopback_port(bindings: dict[str, list[dict[str, Any]]], port: str) -> str:
        values = bindings.get(port)
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
            raise PlatformCIError(f"Kind control plane must publish exactly one {port} binding")
        host_ip = values[0].get("HostIp")
        host_port = values[0].get("HostPort")
        if (
            host_ip != "127.0.0.1"
            or not isinstance(host_port, str)
            or not host_port.isdigit()
            or not 1 <= int(host_port) <= 65535
        ):
            raise PlatformCIError(f"Kind control-plane {port} mapping is not loopback-only")
        return host_port

    def _read_owner(self) -> ClusterOwnership:
        if self.cluster_name not in self._cluster_list():
            raise PlatformCIError("owned Kind cluster is not present")
        result = self._docker(
            "ps",
            "--all",
            "--quiet",
            "--no-trunc",
            f"--filter=label={KIND_CLUSTER_LABEL}={self.cluster_name}",
            timeout=30,
            label="read Kind owner container",
        )
        container_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(container_ids) != 1 or not re.fullmatch(r"[0-9a-f]{64}", container_ids[0]):
            raise PlatformCIError("Kind cluster does not have one verifiable control-plane container")
        inspected_result = self._docker(
            "inspect",
            container_ids[0],
            timeout=30,
            label="inspect exact Kind owner container",
        )
        try:
            inspected = json.loads(inspected_result.stdout)
        except json.JSONDecodeError as exc:
            raise PlatformCIError("Docker inspect returned malformed owner identity") from exc
        if not isinstance(inspected, list) or len(inspected) != 1:
            raise PlatformCIError("Docker inspect returned an ambiguous owner identity")
        node = inspected[0]
        config = node.get("Config")
        state = node.get("State")
        if not isinstance(config, dict) or not isinstance(state, dict):
            raise PlatformCIError("Kind control-plane identity is incomplete")
        labels = config.get("Labels")
        if (
            node.get("Id") != container_ids[0]
            or node.get("Name") != f"/{self.cluster_name}-control-plane"
            or state.get("Running") is not True
            or not isinstance(labels, dict)
            or labels.get(KIND_CLUSTER_LABEL) != self.cluster_name
            or labels.get(KIND_NODE_ROLE_LABEL) != "control-plane"
        ):
            raise PlatformCIError("Kind control-plane labels or container identity changed")
        host_config = node.get("HostConfig")
        network_settings = node.get("NetworkSettings")
        if not isinstance(host_config, dict) or not isinstance(network_settings, dict):
            raise PlatformCIError("Kind owner port bindings are incomplete")
        configured = self._active_bindings(host_config.get("PortBindings"), "configured")
        realized = self._active_bindings(network_settings.get("Ports"), "realized")
        expected_ports = {"6443/tcp", "30080/tcp"}
        if set(configured) != expected_ports or set(realized) != expected_ports:
            raise PlatformCIError("Kind owner port mappings differ from the reviewed local config")
        configured_api = self._loopback_port(configured, "6443/tcp")
        realized_api = self._loopback_port(realized, "6443/tcp")
        if configured_api != realized_api:
            raise PlatformCIError("Kind API host mapping was not realized")
        if (
            self._loopback_port(configured, "30080/tcp") != "8080"
            or self._loopback_port(realized, "30080/tcp") != "8080"
        ):
            raise PlatformCIError("Kind Boutique mapping does not bind only loopback port 8080")

        server = self._load_kubeconfig_server()
        server_port = str(urlsplit(server).port)
        if server_port != configured_api or server_port != realized_api:
            raise PlatformCIError(
                "Kind Docker API host port does not match the selected kubeconfig context server"
            )
        namespace = self._kubectl_json(
            "get",
            "namespace",
            "kube-system",
            timeout=30,
        )
        metadata = namespace.get("metadata")
        if (
            namespace.get("kind") != "Namespace"
            or not isinstance(metadata, dict)
            or metadata.get("name") != "kube-system"
            or not isinstance(metadata.get("uid"), str)
            or not metadata["uid"]
        ):
            raise PlatformCIError("Kubernetes did not return the exact kube-system UID")
        return ClusterOwnership(
            cluster_name=self.cluster_name,
            context_name=self.context_name,
            control_plane_id=container_ids[0],
            cluster_label=labels[KIND_CLUSTER_LABEL],
            role_label=labels[KIND_NODE_ROLE_LABEL],
            api_server=server,
            api_host_port=configured_api,
            kube_system_uid=metadata["uid"],
        )

    def _capture_owner(self) -> ClusterOwnership:
        partial: dict[str, Any] = {
            "cluster_name": self.cluster_name,
            "context_name": self.context_name,
            "complete": False,
        }
        try:
            if self.cluster_name not in self._cluster_list():
                raise PlatformCIError("new Kind cluster was not reported after creation")
            result = self._docker(
                "ps",
                "--all",
                "--quiet",
                "--no-trunc",
                f"--filter=label={KIND_CLUSTER_LABEL}={self.cluster_name}",
                timeout=30,
                label="record Kind owner container",
            )
            ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            partial["container_ids"] = ids
            self._record("cluster", {**self.data["cluster"], "partial_ownership": partial})
            if len(ids) != 1:
                raise PlatformCIError("new Kind cluster has no unique control-plane owner")
            inspected = json.loads(
                self._docker("inspect", ids[0], timeout=30, label="record Kind owner labels").stdout
            )[0]
            config = inspected.get("Config") or {}
            labels = config.get("Labels") or {}
            partial.update(
                control_plane_id=inspected.get("Id"),
                cluster_label=labels.get(KIND_CLUSTER_LABEL),
                role_label=labels.get(KIND_NODE_ROLE_LABEL),
            )
            self._record("cluster", {**self.data["cluster"], "partial_ownership": partial})
            owner = self._read_owner()
            partial.update(asdict(owner))
            partial["complete"] = True
            self.owner = owner
            self.data["cluster"].update(ownership=asdict(owner), partial_ownership=partial)
            self._flush()
            return owner
        except Exception:
            self.data["cluster"]["partial_ownership"] = sanitize_evidence(
                partial,
                self.secret_values,
            )
            self._flush()
            raise

    def _verify_owner_unchanged(self) -> None:
        if self.owner is None:
            raise PlatformCIError("full cluster ownership was never recorded")
        observed = self._read_owner()
        if not current_owner_matches(self.owner, observed):
            raise PlatformCIError("cluster ownership identity changed; no cleanup or fault is permitted")

    def _create_cluster(self) -> None:
        self._stage("cluster_create", "running")
        if self.private_dir is None:
            raise PlatformCIError("private runtime directory was not initialized")
        if self.kubeconfig.exists():
            raise PlatformCIError("external kubeconfig path already exists")
        existing = self._cluster_list()
        if self.cluster_name in existing:
            raise PlatformCIError("refusing to reuse a pre-existing Kind cluster name")
        self.creation_attempted = True
        created = self._kind(
            "create",
            "cluster",
            "--name",
            self.cluster_name,
            "--config",
            str(REPO_ROOT / "infra" / "local" / "kind-config.yaml"),
            "--wait",
            "120s",
            timeout=180,
            check=False,
            label="create unique disposable Kind cluster",
        )
        self.cluster_created_by_runner = created.returncode == 0
        self.data["cluster"]["created_by_runner"] = self.cluster_created_by_runner
        self.data["cluster"]["creation_returncode"] = created.returncode
        self._flush()
        if not self.cluster_created_by_runner:
            raise PlatformCIError("Kind cluster creation did not complete; cleanup will require full ownership proof")
        self._capture_owner()
        self._stage(
            "cluster_create",
            "completed",
            owner_recorded_immediately=True,
            control_plane_id=self.owner.control_plane_id if self.owner else None,
            context=self.context_name,
            kube_system_uid=self.owner.kube_system_uid if self.owner else None,
        )

    def _setup_platform(self) -> None:
        self._stage("platform_setup", "running")
        self._verify_owner_unchanged()
        try:
            self._command(
                ["bash", "infra/local/setup_local.sh", "--apply"],
                timeout=3600,
                label="run existing local platform setup",
            )
        except PlatformCIError:
            try:
                self._verify_owner_unchanged()
            except PlatformCIError as exc:
                self._record("setup_diagnostics", {"status": "ownership_refused", "message": str(exc)})
            else:
                for resources, columns in (
                    (
                        "pods",
                        "NAMESPACE:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase,"
                        "WAITING:.status.containerStatuses[*].state.waiting.reason,"
                        "RESTARTS:.status.containerStatuses[*].restartCount",
                    ),
                    (
                        "deployments,statefulsets,jobs",
                        "NAMESPACE:.metadata.namespace,NAME:.metadata.name,DESIRED:.spec.replicas,"
                        "READY:.status.readyReplicas,AVAILABLE:.status.availableReplicas,"
                        "ACTIVE:.status.active,FAILED:.status.failed",
                    ),
                    (
                        "events",
                        "NAMESPACE:.metadata.namespace,REASON:.reason,OBJECT:.involvedObject.name,"
                        "MESSAGE:.message",
                    ),
                ):
                    try:
                        self._kubectl(
                            "get", resources, "--all-namespaces", "-o", f"custom-columns={columns}",
                            timeout=20, check=False, label=f"setup failure status for {resources}",
                        )
                    except PlatformCIError:
                        continue
            raise
        self._git_clean()
        self._stage("platform_setup", "completed", reused_existing_setup_script=True)

    def _install_fixture(self) -> None:
        self._stage("fixture_install", "running")
        self._verify_owner_unchanged()
        key_path = self.private_dir / "fixture-api-key" if self.private_dir else None
        if key_path is None or not key_path.is_file():
            raise PlatformCIError("generated fixture key is unavailable")
        self._command(
            [
                "kubectl",
                f"--kubeconfig={self.kubeconfig}",
                f"--context={self.context_name}",
                "create",
                "secret",
                "generic",
                FIXTURE_SECRET,
                "-n",
                "default",
                f"--from-file=api-key={key_path}",
            ],
            timeout=30,
            label="create keyed fixture Secret without archiving its manifest",
        )
        image = f"atlasops:{self.identity.source_sha}"
        service_account = {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": FIXTURE_ACCOUNT, "namespace": "default"},
            "automountServiceAccountToken": False,
        }
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": FIXTURE_SERVICE,
                "namespace": "default",
                "labels": {"app.kubernetes.io/name": FIXTURE_SERVICE, "app.kubernetes.io/part-of": "atlasops"},
            },
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app.kubernetes.io/name": FIXTURE_SERVICE}},
                "template": {
                    "metadata": {
                        "labels": {
                            "app.kubernetes.io/name": FIXTURE_SERVICE,
                            "app.kubernetes.io/part-of": "atlasops",
                        }
                    },
                    "spec": {
                        "serviceAccountName": FIXTURE_ACCOUNT,
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 10001,
                            "runAsGroup": 10001,
                            "fsGroup": 10001,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": "fixture",
                                "image": image,
                                "imagePullPolicy": "Never",
                                "command": [
                                    "python",
                                    "-m",
                                    "scripts.platform_ci",
                                    "serve-fixture",
                                    "--key-file",
                                    "/run/atlasops-ci-fixture/api-key",
                                    "--source-sha",
                                    self.identity.source_sha,
                                ],
                                "ports": [{"name": "http", "containerPort": 8080, "protocol": "TCP"}],
                                "env": [
                                    {"name": "ATLASOPS_FIXTURE_CLASSIFICATION", "value": FIXTURE_CLASSIFICATION},
                                    {"name": "ATLASOPS_USE_HF_INFERENCE", "value": "0"},
                                ],
                                "volumeMounts": [
                                    {"name": "api-key", "mountPath": "/run/atlasops-ci-fixture", "readOnly": True},
                                    {"name": "tmp", "mountPath": "/tmp"},
                                ],
                                "resources": {
                                    "requests": {"cpu": "10m", "memory": "64Mi"},
                                    "limits": {"cpu": "200m", "memory": "256Mi"},
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                            }
                        ],
                        "volumes": [
                            {"name": "api-key", "secret": {"secretName": FIXTURE_SECRET, "defaultMode": 0o440}},
                            {"name": "tmp", "emptyDir": {}},
                        ],
                    },
                },
            },
        }
        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": FIXTURE_SERVICE,
                "namespace": "default",
                "labels": {"app.kubernetes.io/name": FIXTURE_SERVICE, "app.kubernetes.io/part-of": "atlasops"},
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {"app.kubernetes.io/name": FIXTURE_SERVICE},
                "ports": [{"name": "http", "port": 8080, "targetPort": "http", "protocol": "TCP"}],
            },
        }
        if (
            service_account["automountServiceAccountToken"] is not False
            or deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is not False
            or service["spec"]["type"] != "ClusterIP"
        ):
            raise PlatformCIError("fixture manifest violated its private service-account contract")
        rendered = "\n---\n".join(json.dumps(item, separators=(",", ":")) for item in (service_account, deployment, service))
        manifest_path = self.evidence_dir / "rendered-manifests" / "ci-model-fixture.json"
        manifest_path.write_text(rendered + "\n", encoding="utf-8")
        try:
            manifest_path.chmod(0o600)
        except OSError:
            pass
        self._kubectl("apply", "-f", "-", input_text=rendered, timeout=30)
        self._kubectl(
            "rollout",
            "status",
            f"deployment/{FIXTURE_SERVICE}",
            "-n",
            "default",
            "--timeout=180s",
            timeout=200,
        )
        deployment_state = self._kubectl_json(
            "get",
            "deployment",
            FIXTURE_SERVICE,
            "-n",
            "default",
        )
        pod_spec = deployment_state.get("spec", {}).get("template", {}).get("spec", {})
        if (
            pod_spec.get("automountServiceAccountToken") is not False
            or pod_spec.get("serviceAccountName") != FIXTURE_ACCOUNT
            or deployment_state.get("spec", {}).get("replicas") != 1
        ):
            raise PlatformCIError("live fixture Deployment does not satisfy the private fixture contract")
        self._record(
            "fixture",
            {
                **self.data.get("fixture", {}),
                "model": FIXTURE_MODEL,
                "provider_kind": "fixture/no-model",
                "auth_required": True,
                "service": FIXTURE_SERVICE,
                "service_type": "ClusterIP",
                "service_account_token_automount": False,
                "external_model_calls": False,
                "pretrained_inference": False,
                "deployment_image": image,
                "replicas": 1,
                "generated_key_archived": False,
            },
        )
        self._stage("fixture_install", "completed")

    def _start_forward(
        self, name: str, namespace: str, service: str, service_port: int, *, pod_port: int
    ) -> int:
        port = 0
        log_path = self.evidence_dir / "logs" / f"port-forward-{name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = log_path.open("w", encoding="utf-8")
        command = [
            "kubectl",
            f"--kubeconfig={self.kubeconfig}",
            f"--context={self.context_name}",
            "port-forward",
            "--address=127.0.0.1",
            "-n",
            namespace,
            f"service/{service}",
            f"{port}:{service_port}",
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=REPO_ROOT,
                env=self.child_env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
            )
        except Exception:
            stream.close()
            raise
        forward = Forward(name, port, process, log_path, stream)
        self.forwards[name] = forward
        self._append_jsonl(
            "logs/commands.jsonl",
            {
                "stage": self.stage,
                "label": f"loopback-only {name} port-forward",
                "command": command,
                "bound_address": "127.0.0.1",
                "service_port": service_port,
                "pod_port": pod_port,
            },
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise PlatformCIError(f"{name} port-forward exited before becoming ready")
            try:
                output = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                output = ""
            bound = re.search(
                r"(?m)^Forwarding from 127\.0\.0\.1:(\d+)\s+->\s+(\d+)\s*$",
                output,
            )
            if bound:
                port = int(bound.group(1))
                if not 1 <= port <= 65535 or int(bound.group(2)) != pod_port:
                    raise PlatformCIError(f"{name} port-forward reported an invalid bound port")
                forward.port = port
            if port:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        return port
                except OSError:
                    pass
            time.sleep(0.25)
        raise PlatformCIError(f"{name} port-forward did not bind within 60 seconds")

    def _start_forwards(self) -> None:
        self._stage("admin_forwards", "running")
        mapping = {
            "coordinator": ("default", "atlasops-coordinator-svc", 9099, 7860),
            "prometheus": ("monitoring", "prometheus-kube-prometheus-prometheus", 9090, 9090),
            "alertmanager": ("monitoring", "prometheus-kube-prometheus-alertmanager", 9093, 9093),
            "jaeger": ("jaeger", "jaeger", 16686, 16686),
        }
        ports = {
            name: self._start_forward(name, namespace, service, service_port, pod_port=pod_port)
            for name, (namespace, service, service_port, pod_port) in mapping.items()
        }
        self.data["admin_forwards"] = {
            name: {"bind": "127.0.0.1", "port": port}
            for name, port in ports.items()
        }
        self._stage("admin_forwards", "completed")
        self.ports = ports

    def _operations_headers(self) -> dict[str, str]:
        api_key_path = self.secret_dir / "atlasops-api-key.secret" if self.secret_dir else None
        if api_key_path is None or not api_key_path.is_file():
            raise PlatformCIError("generated operations API key is unavailable")
        api_key = api_key_path.read_text(encoding="utf-8").strip()
        if not api_key:
            raise PlatformCIError("generated operations API key is empty")
        return {"X-AtlasOps-Key": api_key}

    def _api_root(self) -> str:
        return f"http://127.0.0.1:{self.ports['coordinator']}"

    def _operations(self) -> dict[str, Any]:
        status, payload = self._http(
            self._api_root() + "/api/operations",
            headers=self._operations_headers(),
            timeout=15,
            label="authenticated GET /api/operations",
        )
        if status != 200 or not isinstance(payload, dict):
            raise PlatformCIError(f"authenticated operations route returned HTTP {status}")
        if (
            payload.get("source_sha") != self.identity.source_sha
            or payload.get("certification_status") != CERTIFICATION_STATUS
            or payload.get("operator_id") != "ci-synthetic-operator"
        ):
            raise PlatformCIError("operations route identity or NOT_CERTIFIED status is mismatched")
        return payload

    def _application(self) -> dict[str, Any]:
        return self._kubectl_json(
            "get",
            "application.argoproj.io",
            APP_NAME,
            "-n",
            APP_NAMESPACE,
        )

    def _deployment(self) -> dict[str, Any]:
        return self._kubectl_json(
            "get",
            "deployment",
            WORKLOAD_NAME,
            "-n",
            APP_WORKLOAD_NAMESPACE,
        )

    def _prometheus_query(self, query: str) -> list[dict[str, Any]]:
        status, body = self._http(
            f"http://127.0.0.1:{self.ports['prometheus']}/api/v1/query?{urlencode({'query': query})}",
            timeout=15,
            label="Prometheus instant query",
        )
        data = body.get("data") if isinstance(body, dict) else None
        result = data.get("result") if isinstance(data, dict) else None
        if status != 200 or body.get("status") != "success" or not isinstance(result, list):
            raise PlatformCIError("Prometheus query returned an invalid result")
        return result

    def _prometheus_rule(self) -> dict[str, Any]:
        query = urlencode({"type": "alert", "rule_name[]": ALERT_NAME})
        status, body = self._http(
            f"http://127.0.0.1:{self.ports['prometheus']}/api/v1/rules?{query}",
            timeout=15,
            label="read actual Prometheus alert rules",
        )
        data = body.get("data") if isinstance(body, dict) else None
        groups = data.get("groups") if isinstance(data, dict) else None
        if status != 200 or body.get("status") != "success" or not isinstance(groups, list):
            raise PlatformCIError("Prometheus did not return its loaded alert rules")
        matches = [
            rule
            for group in groups
            if isinstance(group, dict)
            for rule in group.get("rules", [])
            if isinstance(rule, dict) and rule.get("name") == ALERT_NAME
        ]
        if len(matches) != 1:
            raise PlatformCIError("Prometheus must load exactly one frontend availability rule")
        rule = matches[0]
        return {
            "name": rule.get("name"),
            "query": rule.get("query"),
            "labels": rule.get("labels"),
            "duration": rule.get("duration"),
            "state": rule.get("state"),
        }

    def _alertmanager_alerts(self) -> list[dict[str, Any]]:
        status, body = self._http(
            f"http://127.0.0.1:{self.ports['alertmanager']}/api/v2/alerts",
            timeout=15,
            label="read actual Alertmanager alerts",
        )
        if status != 200 or not isinstance(body, list):
            raise PlatformCIError("Alertmanager did not return its active alert inventory")
        return [item for item in body if isinstance(item, dict)]

    def _active_frontend_alert(self, alerts: list[dict[str, Any]]) -> dict[str, Any] | None:
        for alert in alerts:
            labels = alert.get("labels")
            status = alert.get("status")
            if (
                isinstance(labels, dict)
                and labels.get("alertname") == ALERT_NAME
                and labels.get("namespace") == APP_WORKLOAD_NAMESPACE
                and labels.get("deployment") == WORKLOAD_NAME
                and labels.get("atlasops_route") == "coordinator"
                and isinstance(status, dict)
                and status.get("state") in {"active", "firing"}
            ):
                return alert
        return None

    def _wait_tcp_service(self, name: str, url: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last_status = 0
        last_error = None
        while time.monotonic() < deadline:
            try:
                last_status, _body = self._http(
                    url, timeout=3, label=f"wait for {name}", expect_json=False
                )
                if last_status == 200:
                    return
            except PlatformCIError as exc:
                last_error = sanitize_text(str(exc), self.secret_values)
                self._append_jsonl(
                    "logs/http-events.jsonl",
                    {"stage": self.stage, "label": f"wait for {name}", "error": last_error},
                )
            time.sleep(1)
        raise PlatformCIError(
            f"{name} did not become ready; last HTTP status was {last_status}; "
            f"last error was {last_error or 'none'}"
        )

    def _verify_image_identity(self) -> dict[str, Any]:
        self._stage("image_identity", "running")
        source = self._kubectl_json(
            "get",
            "deployment",
            "atlasops-coordinator",
            "-n",
            "default",
        )
        metadata = source.get("metadata") or {}
        spec = source.get("spec") or {}
        template = spec.get("template") or {}
        labels = metadata.get("labels") or {}
        template_labels = template.get("metadata", {}).get("labels") or {}
        containers = template.get("spec", {}).get("containers") or []
        selected = [item for item in containers if isinstance(item, dict) and item.get("name") == "coordinator"]
        if (
            metadata.get("name") != "atlasops-coordinator"
            or metadata.get("namespace") != "default"
            or len(selected) != 1
            or selected[0].get("image") != f"atlasops:{self.identity.source_sha}"
            or labels.get("app.kubernetes.io/version") != self.identity.source_sha
            or template_labels.get("app.kubernetes.io/version") != self.identity.source_sha
        ):
            raise PlatformCIError("coordinator Deployment does not name the exact source image and labels")
        docker_info = json.loads(
            self._docker(
                "image",
                "inspect",
                f"atlasops:{self.identity.source_sha}",
                timeout=30,
                label="inspect built source image",
            ).stdout
        )
        if not isinstance(docker_info, list) or len(docker_info) != 1:
            raise PlatformCIError("Docker source image inspection is ambiguous")
        built = docker_info[0]
        config = built.get("Config") or {}
        labels = config.get("Labels") or {}
        rootfs = built.get("RootFS") or {}
        layers = rootfs.get("Layers")
        image_id = built.get("Id")
        if (
            not isinstance(image_id, str)
            or not IMAGE_ID_RE.fullmatch(image_id)
            or labels.get("org.opencontainers.image.revision") != self.identity.source_sha
            or not isinstance(layers, list)
            or not layers
            or any(not isinstance(item, str) or not IMAGE_ID_RE.fullmatch(item) for item in layers)
        ):
            raise PlatformCIError("Docker image config, rootfs, or OCI source label is not verifiable")
        pods = self._kubectl_json(
            "get",
            "pods",
            "-n",
            "default",
            "-l",
            "app.kubernetes.io/name=atlasops-coordinator",
        ).get("items")
        if not isinstance(pods, list):
            raise PlatformCIError("coordinator Pods are unavailable")
        running = []
        for pod in pods:
            if not isinstance(pod, dict):
                continue
            pod_status = pod.get("status") or {}
            pod_labels = pod.get("metadata", {}).get("labels") or {}
            if pod_labels.get("app.kubernetes.io/version") != self.identity.source_sha:
                continue
            statuses = pod_status.get("containerStatuses") or []
            for status in statuses:
                if (
                    isinstance(status, dict)
                    and status.get("name") == "coordinator"
                    and status.get("ready") is True
                    and status.get("image") in {
                        f"atlasops:{self.identity.source_sha}",
                        f"docker.io/library/atlasops:{self.identity.source_sha}",
                    }
                    and isinstance(status.get("imageID"), str)
                    and IMAGE_DIGEST_RE.search(status["imageID"])
                ):
                    running.append((pod, status))
        if len(running) != 1:
            raise PlatformCIError("no unique ready coordinator container matches the source image")
        pod, pod_status = running[0]
        if pod.get("spec", {}).get("containers", [{}])[0].get("image") != f"atlasops:{self.identity.source_sha}":
            raise PlatformCIError("running Pod spec does not match the built image reference")

        if self.owner is None:
            raise PlatformCIError("Kind ownership is unavailable for CRI image inspection")
        cri_raw = self._docker(
            "exec",
            self.owner.control_plane_id,
            "crictl",
            "inspecti",
            f"atlasops:{self.identity.source_sha}",
            timeout=30,
            label="inspect loaded CRI image config and rootfs",
        ).stdout
        try:
            cri = json.loads(cri_raw)
        except json.JSONDecodeError as exc:
            raise PlatformCIError("CRI image inspection returned malformed JSON") from exc
        status = cri.get("status") if isinstance(cri, dict) else None
        info = cri.get("info") if isinstance(cri, dict) else None
        image_spec = info.get("imageSpec") if isinstance(info, dict) else None
        runtime_config = image_spec.get("config") if isinstance(image_spec, dict) else None
        runtime_rootfs = image_spec.get("rootfs") if isinstance(image_spec, dict) else None
        runtime_layers = runtime_rootfs.get("diff_ids") if isinstance(runtime_rootfs, dict) else None
        runtime_labels = runtime_config.get("Labels") if isinstance(runtime_config, dict) else None
        if (
            not isinstance(status, dict)
            or not isinstance(status.get("id"), str)
            or not isinstance(runtime_config, dict)
            or not isinstance(runtime_labels, dict)
            or not isinstance(runtime_layers, list)
            or runtime_layers != layers
            or runtime_labels.get("org.opencontainers.image.revision") != self.identity.source_sha
        ):
            raise PlatformCIError("loaded CRI image does not bind to the Docker rootfs and source labels")
        for field in ("User", "WorkingDir", "Entrypoint", "Cmd", "Env", "Labels"):
            if field in config and runtime_config.get(field) != config.get(field):
                raise PlatformCIError(f"Docker and CRI image config differ at {field}")
        runtime_id = status["id"]
        runtime_digests = status.get("repoDigests") or []
        pod_image_digest = IMAGE_DIGEST_RE.search(pod_status["imageID"]).group(1)
        comparable_runtime_ids = {
            item.group(1)
            for raw in [runtime_id, *runtime_digests]
            if isinstance(raw, str)
            for item in [IMAGE_DIGEST_RE.search(raw)]
            if item
        }
        if comparable_runtime_ids and pod_image_digest not in comparable_runtime_ids:
            raise PlatformCIError("Pod runtime imageID is not represented by the inspected CRI image")
        identity = {
            "reference": f"atlasops:{self.identity.source_sha}",
            "docker_config_id": image_id,
            "docker_rootfs_layer_count": len(layers),
            "docker_rootfs_layers": layers,
            "docker_source_label": self.identity.source_sha,
            "cri_runtime_id": runtime_id,
            "cri_repo_digests": runtime_digests,
            "cri_rootfs_matches_docker": True,
            "cri_config_fields_match": ["User", "WorkingDir", "Entrypoint", "Cmd", "Env", "Labels"],
            "pod_image_id": pod_status["imageID"],
            "pod_image_digest_matches_cri_reference": (
                pod_image_digest in comparable_runtime_ids if comparable_runtime_ids else None
            ),
            "source_sha": self.identity.source_sha,
        }
        self._record("image_identity", identity)
        self._stage("image_identity", "completed")
        return identity

    def _verify_live(self, label: str) -> dict[str, Any]:
        evidence_file = self.evidence_dir / f"verify-live-{label}.json"
        self._command(
            [
                sys.executable,
                "scripts/verify_platform_infrastructure.py",
                "verify-live",
                "--source-sha",
                self.identity.source_sha,
                "--kube-context",
                self.context_name,
                "--kubeconfig",
                str(self.kubeconfig),
                "--operator-key-file",
                str(self.secret_dir / "atlasops-api-key.secret"),
                "--evidence-file",
                str(evidence_file),
            ],
            timeout=220,
            label=f"verify authenticated seven-service live signals ({label})",
        )
        evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
        if (
            evidence.get("classification") != FIXTURE_CLASSIFICATION
            or evidence.get("source_sha") != self.identity.source_sha
        ):
            raise PlatformCIError("live infrastructure verifier returned a mismatched evidence receipt")
        return evidence

    def _check_frontend_rule(self, *, before_fault: bool) -> dict[str, Any]:
        rule = self._prometheus_rule()
        alerts = self._alertmanager_alerts()
        if before_fault:
            state = validate_pre_fault(
                self._application(),
                self._deployment(),
                rule,
                alerts,
                source_sha=self.identity.source_sha,
            )
            self._record(
                "pre_fault_eligibility",
                {
                    **state,
                    "prometheus_rule": rule,
                    "active_matching_alerts": 0,
                },
            )
            return state
        return {"prometheus_rule": rule, "alerts": alerts}

    def _kubectl_alerts(self) -> list[dict[str, Any]]:
        return self._alertmanager_alerts()

    def _alert_rule_firing(self) -> dict[str, Any] | None:
        query = (
            f'ALERTS{{alertname="{ALERT_NAME}",namespace="{APP_WORKLOAD_NAMESPACE}",'
            f'deployment="{WORKLOAD_NAME}",alertstate="firing"}}'
        )
        results = self._prometheus_query(query)
        for item in results:
            labels = item.get("metric") if isinstance(item, dict) else None
            point = item.get("value") if isinstance(item, dict) else None
            if (
                isinstance(labels, dict)
                and labels.get("alertname") == ALERT_NAME
                and labels.get("namespace") == APP_WORKLOAD_NAMESPACE
                and labels.get("deployment") == WORKLOAD_NAME
                and labels.get("alertstate") == "firing"
                and labels.get("atlasops_route") == "coordinator"
                and isinstance(point, list)
                and len(point) == 2
                and _float_sample(point[1]) > 0
            ):
                return {"labels": labels, "sample": point}
        return None

    def _wait_for_pending_incident(self, fault_started_at: str) -> tuple[dict[str, Any], dict[str, Any]]:
        deadline = time.monotonic() + 420
        last_prometheus: Any = None
        last_alertmanager: Any = None
        last_error = ""
        while time.monotonic() < deadline:
            try:
                deployment = self._deployment()
                if deployment.get("spec", {}).get("replicas") != 1:
                    raise PlatformCIError("frontend desired replicas changed during fault")
                if deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("imagePullPolicy") != "Never":
                    raise PlatformCIError("frontend fault imagePullPolicy drifted before alert admission")
                last_prometheus = self._alert_rule_firing()
                alerts = self._kubectl_alerts()
                last_alertmanager = self._active_frontend_alert(alerts)
                if last_prometheus and last_alertmanager:
                    status = self._operations()
                    pending = status.get("pending")
                    if isinstance(pending, list):
                        candidates = [
                            item
                            for item in pending
                            if isinstance(item, dict)
                            and isinstance(item.get("action"), dict)
                            and item["action"].get("tool") == "argocd_rollback"
                        ]
                        if len(candidates) == 1:
                            pending_item = candidates[0]
                            application = self._application()
                            if (
                                not isinstance(application.get("spec"), dict)
                                or application["spec"].get("targetRevision")
                                or application["spec"].get("operation")
                            ):
                                pass
                            app_source = application.get("spec", {}).get("source", {})
                            if (
                                app_source.get("repoURL") == EXPECTED_REPO_URL
                                and app_source.get("path") == APP_PATH
                                and app_source.get("targetRevision") == self.identity.source_sha
                            ):
                                evidence = self._operation_evidence(pending_item["incident_id"])
                                self._require_zero_mutations_before_approval(evidence)
                                return pending_item, evidence
            except PlatformCIError as exc:
                last_error = sanitize_text(str(exc), self.secret_values)
            time.sleep(POLL_INTERVAL)
        raise PlatformCIError(
            "timed out waiting for Prometheus firing alert, Alertmanager delivery, and prepared approval; "
            f"last Prometheus={bool(last_prometheus)} last Alertmanager={bool(last_alertmanager)}"
            + (f"; last observation error={last_error}" if last_error else "")
        )

    def _operations_by_id(self, execution_id: str) -> dict[str, Any]:
        status, payload = self._http(
            f"{self._api_root()}/api/operations/{execution_id}/evidence",
            headers=self._operations_headers(),
            timeout=15,
            label="authenticated GET operational evidence",
            max_bytes=MAX_EXECUTION_EXPORT_BYTES,
        )
        if status != 200 or not isinstance(payload, dict):
            raise PlatformCIError(f"operational evidence route returned HTTP {status}")
        self._append_jsonl("api-evidence/operation-evidence.jsonl", payload)
        return payload

    def _operation_evidence(self, incident_id: str) -> dict[str, Any]:
        status = self._operations()
        executions = status.get("executions")
        if not isinstance(executions, list):
            raise PlatformCIError("operations route omitted execution inventory")
        matching = [
            item
            for item in executions
            if isinstance(item, dict) and item.get("incident_id") == incident_id
        ]
        if len(matching) != 1 or not isinstance(matching[0].get("execution_id"), str):
            raise PlatformCIError("pending incident has no unique durable execution")
        return self._operations_by_id(matching[0]["execution_id"])

    def _audit_secret(self) -> str:
        path = self.secret_dir / "atlasops-audit-secret.secret" if self.secret_dir else None
        if path is None or not path.is_file():
            raise PlatformCIError("generated audit signing key is unavailable")
        secret = path.read_text(encoding="utf-8").strip()
        if not secret:
            raise PlatformCIError("generated audit signing key is empty")
        return secret

    def _wait_for_approved_mutation_returned(self, pending: dict[str, Any]) -> dict[str, Any]:
        incident_id = pending.get("incident_id")
        if not isinstance(incident_id, str) or not incident_id:
            raise PlatformCIError("approved action has no incident identity")
        expected_action = pending.get("action")
        expected_digest = pending.get("action_digest")
        if not isinstance(expected_action, dict) or not isinstance(expected_digest, str):
            raise PlatformCIError("approved action omitted its prepared action or digest")

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            evidence = self._operation_evidence(incident_id)
            verified = _validate_operation_export(
                evidence,
                source_sha=self.identity.source_sha,
                audit_secret=self._audit_secret(),
            )
            if verified["incident_id"] != incident_id:
                raise PlatformCIError("operational evidence changed its incident identity")
            started = [
                event
                for event in verified["events"]
                if event.get("event") == "mutation_started"
            ]
            returned = [
                event for event in verified["events"]
                if event.get("event") == "mutation_returned"
            ]
            if started and returned:
                dispatch = _validate_approved_mutation_start(
                    verified,
                    source_sha=self.identity.source_sha,
                    history_id=str(expected_action.get("arguments", {}).get("revision", "")),
                    expected_operator="ci-synthetic-operator",
                )
                if (
                    dispatch["action"] != expected_action
                    or dispatch["action_digest"] != expected_digest
                ):
                    raise PlatformCIError("durable mutation start does not match the approved pending action")
                return _validate_approved_mutation_return(verified, dispatch)
            if evidence.get("state") != "running":
                raise PlatformCIError("operational execution ended before its approved mutation returned")
            time.sleep(POLL_INTERVAL)
        raise PlatformCIError("timed out waiting for the durable post-approval mutation return")

    @staticmethod
    def _mutation_events(evidence: dict[str, Any]) -> set[str]:
        events = evidence.get("events")
        if not isinstance(events, list):
            raise PlatformCIError("execution evidence omitted its durable event list")
        return {
            item.get("event")
            for item in events
            if isinstance(item, dict)
            and item.get("event") in {
                "mutation_started",
                "mutation_returned",
                "mutation_uncertain",
            }
        }

    def _require_zero_mutations_before_approval(self, evidence: dict[str, Any]) -> None:
        if (
            evidence.get("phase") != "approval"
            or evidence.get("state") != "running"
            or self._mutation_events(evidence)
        ):
            raise PlatformCIError("agent mutation started before exact-action approval")
        self._append_jsonl("api-evidence/pre-approval-evidence.jsonl", evidence)

    def _pending_action(self, pending: dict[str, Any], selected: dict[str, Any]) -> dict[str, Any]:
        action = pending.get("action")
        if not isinstance(action, dict):
            raise PlatformCIError("pending approval omitted its prepared action")
        arguments = action.get("arguments")
        target = action.get("target")
        verify_target = action.get("verification_target")
        patch = action.get("patch")
        if (
            action.get("tool") != "argocd_rollback"
            or arguments != {"app": APP_NAME, "revision": selected["history_id"]}
            or not isinstance(target, dict)
            or target.get("kind") != "Application"
            or target.get("namespace") != APP_NAMESPACE
            or target.get("name") != APP_NAME
            or target.get("uid") != self._application().get("metadata", {}).get("uid")
            or not isinstance(verify_target, dict)
            or verify_target.get("kind") != "Deployment"
            or verify_target.get("namespace") != APP_WORKLOAD_NAMESPACE
            or verify_target.get("name") != WORKLOAD_NAME
            or not isinstance(verify_target.get("uid"), str)
            or not isinstance(patch, list)
        ):
            raise PlatformCIError("prepared action does not bind the exact Argo Application and frontend")
        replacement = [
            item.get("value", {}).get("targetRevision")
            for item in patch
            if isinstance(item, dict)
            and item.get("op") == "replace"
            and item.get("path") == "/spec/source"
            and isinstance(item.get("value"), dict)
        ]
        operation = [
            item.get("value", {}).get("sync", {}).get("revision")
            for item in patch
            if isinstance(item, dict)
            and item.get("op") == "add"
            and item.get("path") == "/operation"
            and isinstance(item.get("value"), dict)
        ]
        if (
            replacement != [selected["history_revision"]]
            or operation != [selected["history_revision"]]
        ):
            raise PlatformCIError("prepared Argo action does not reconcile the selected committed revision")
        self._pending_token = pending.get("token")
        self._pending_digest = pending.get("action_digest")
        if not isinstance(self._pending_token, str) or not isinstance(self._pending_digest, str):
            raise PlatformCIError("pending exact-action credentials are malformed")
        self.secret_values.append(self._pending_token)
        return {
            "tool": action["tool"],
            "arguments": arguments,
            "target": target,
            "verification_target": verify_target,
            "action_digest": self._pending_digest,
            "token": self._pending_token,
        }

    def _decision(self, pending: dict[str, Any], digest: str, *, label: str) -> tuple[int, Any]:
        return self._http(
            f"{self._api_root()}/api/operations/decision",
            method="POST",
            headers=self._operations_headers(),
            payload={
                "token": pending["token"],
                "action_digest": digest,
                "decision": "approved",
                "reason": "Disposable synthetic operator decision.",
            },
            timeout=15,
            label=label,
        )

    def _assert_workload_broken(self) -> bool:
        deployment = self._deployment()
        spec = deployment.get("spec") or {}
        status = deployment.get("status") or {}
        desired = spec.get("replicas")
        available = status.get("availableReplicas", 0)
        if type(desired) is not int or desired != 1:
            raise PlatformCIError("fault changed the frontend desired replica count")
        return type(available) is int and available < desired

    def _prove_rejection_before_approval(
        self,
        pending: dict[str, Any],
        before: dict[str, Any],
    ) -> None:
        wrong = make_wrong_digest(self._pending_digest)
        status_code, response = self._decision(pending, wrong, label="reject incorrect approval digest")
        pending_after_list = self._operations().get("pending")
        pending_after = next(
            (
                item
                for item in pending_after_list
                if isinstance(item, dict) and item.get("incident_id") == pending.get("incident_id")
            ),
            None,
        ) if isinstance(pending_after_list, list) else None
        after = self._operation_evidence(pending["incident_id"])
        validate_rejected_decision(
            status_code,
            response,
            pending,
            pending_after,
            workload_still_broken=self._assert_workload_broken(),
            mutation_events_before=self._mutation_events(before),
            mutation_events_after=self._mutation_events(after),
        )
        if before.get("execution_id") != after.get("execution_id"):
            raise PlatformCIError("wrong digest changed the durable execution identity")
        self._append_jsonl(
            "api-evidence/wrong-digest-rejection.jsonl",
            {
                "status": status_code,
                "response": response,
                "pending_action_digest": pending.get("action_digest"),
                "workload_still_broken": True,
                "mutation_events_unchanged": True,
            },
        )

    def _approve_exact_action(self, pending: dict[str, Any]) -> None:
        status_code, response = self._decision(
            pending,
            self._pending_digest,
            label="approve exact prepared action as synthetic server principal",
        )
        if (
            status_code != 200
            or not isinstance(response, dict)
            or response.get("ok") is not True
            or response.get("decision") != "approved"
        ):
            raise PlatformCIError("operator API did not accept the exact action digest")
        self.data["approval_request_shape"] = {
            "route": "/api/operations/decision",
            "server_principal": "ci-synthetic-operator",
            "approved_by_body_field_sent": False,
            "wrong_digest_rejected_first": True,
            "exact_digest_accepted": True,
        }
        self._flush()

    def _wait_for_application_sync(
        self,
        selected_revision: str,
        dispatch: dict[str, Any],
        *,
        before: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        approval_timestamp = dispatch.get("approval_timestamp")
        mutation_started_timestamp = dispatch.get("mutation_started_timestamp")
        mutation_returned_timestamp = dispatch.get("mutation_returned_timestamp")
        if (
            isinstance(approval_timestamp, bool)
            or not isinstance(approval_timestamp, (int, float))
            or not 0 <= approval_timestamp < float("inf")
            or isinstance(mutation_started_timestamp, bool)
            or not isinstance(mutation_started_timestamp, (int, float))
            or not 0 <= mutation_started_timestamp < float("inf")
            or approval_timestamp > mutation_started_timestamp
            or isinstance(mutation_returned_timestamp, bool)
            or not isinstance(mutation_returned_timestamp, (int, float))
            or not mutation_started_timestamp <= mutation_returned_timestamp < float("inf")
        ):
            raise PlatformCIError("durable approval and mutation-start timestamps are missing or out of order")
        action = dispatch.get("action")
        target = action.get("target") if isinstance(action, dict) else None
        result = dispatch.get("action_result")
        receipt = result.get("mutation_receipt") if isinstance(result, dict) else None
        acknowledged = receipt.get("target") if isinstance(receipt, dict) else None
        previous_metadata = (before or {}).get("metadata") or {}
        previous_operation = ((before or {}).get("status") or {}).get("operationState") or {}
        if (
            not isinstance(target, dict)
            or not isinstance(result, dict)
            or result.get("success") is not True
            or result.get("atomic_preconditions") is not True
            or result.get("target") != target
            or result.get("action_digest") != dispatch.get("action_digest")
            or dispatch.get("action_digest") != _prepared_action_digest(action)
            or not isinstance(acknowledged, dict)
            or not isinstance(acknowledged.get("resource_version"), str)
            or not acknowledged["resource_version"]
            or {key: value for key, value in acknowledged.items() if key != "resource_version"}
            != {key: value for key, value in target.items() if key != "resource_version"}
            or acknowledged["resource_version"] == target.get("resource_version")
            or previous_metadata.get("uid") != target.get("uid")
            or previous_metadata.get("resourceVersion") != target.get("resource_version")
            or previous_metadata.get("name") != APP_NAME
            or previous_metadata.get("namespace") != APP_NAMESPACE
            or (before or {}).get("operation")
            or previous_operation.get("phase") in {"Running", "Pending", "Terminating"}
        ):
            raise PlatformCIError("Argo sync requires the prepared-target snapshot and atomic PATCH receipt")
        source = receipt.get("source")
        receipt_operation = receipt.get("operation")
        replacement = [
            item.get("value") for item in action.get("patch", [])
            if isinstance(item, dict) and item.get("op") == "replace" and item.get("path") == "/spec/source"
        ]
        operations = [
            item.get("value") for item in action.get("patch", [])
            if isinstance(item, dict) and item.get("op") == "add" and item.get("path") == "/operation"
        ]
        markers = receipt_operation.get("info") if isinstance(receipt_operation, dict) else None
        if (
            replacement != [source]
            or operations != [receipt_operation]
            or not isinstance(source, dict)
            or source.get("targetRevision") != selected_revision
            or not isinstance(receipt_operation, dict)
            or receipt_operation.get("initiatedBy") != {"username": "atlasops"}
            or receipt_operation.get("sync") != {"revision": selected_revision, "prune": False}
            or not isinstance(markers, list)
            or len(markers) != 1
            or not isinstance(markers[0], dict)
            or markers[0].get("name") != "atlasops-action-id"
            or not isinstance(markers[0].get("value"), str)
            or re.fullmatch(r"[0-9a-f]{64}", markers[0]["value"]) is None
            or previous_operation.get("operation", {}).get("info") == markers
        ):
            raise PlatformCIError("Argo PATCH receipt changed the exact approved source or operation")
        deadline = time.monotonic() + 300
        last = {}
        while time.monotonic() < deadline:
            app = self._application()
            status = app.get("status") or {}
            spec = app.get("spec") or {}
            operation_state = status.get("operationState") or {}
            operation = operation_state.get("operation") or {}
            initiated_by = operation.get("initiatedBy") or {}
            requested_sync = operation.get("sync") or {}
            sync = status.get("sync") or {}
            last = {
                "phase": operation_state.get("phase"),
                "sync_status": sync.get("status"),
                "sync_revision": sync.get("revision"),
                "target_revision": spec.get("source", {}).get("targetRevision"),
                "started_at": operation_state.get("startedAt"),
                "initiated_by": initiated_by.get("username"),
                "operation_revision": requested_sync.get("revision"),
                "operation_marker_matches": operation.get("info") == markers,
            }
            started_at = operation_state.get("startedAt")
            try:
                started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            except ValueError:
                started = None
            started_after_dispatch = False
            if started is not None and started.tzinfo is not None:
                started_epoch = started.timestamp()
                if started.microsecond:
                    started_after_dispatch = started_epoch >= mutation_started_timestamp
                else:
                    started_after_dispatch = started_epoch >= math.floor(mutation_started_timestamp)
            metadata = app.get("metadata") or {}
            if (
                operation_state.get("phase") == "Succeeded"
                and sync.get("status") == "Synced"
                and sync.get("revision") == selected_revision
                and spec.get("source") == source
                and initiated_by.get("username") == "atlasops"
                and requested_sync.get("revision") == selected_revision
                and requested_sync.get("prune", False) is False
                and operation.get("info") == markers
                and started_after_dispatch
                and metadata.get("uid") == target.get("uid")
                and isinstance(metadata.get("resourceVersion"), str)
                and bool(metadata["resourceVersion"])
                and metadata["resourceVersion"] != target.get("resource_version")
            ):
                self._append_jsonl("api-evidence/argo-application-after-sync.jsonl", app)
                self._record("argo_sync_correlation", {
                    "action_digest": dispatch["action_digest"],
                    "application_uid": target["uid"],
                    "operation_id": markers[0]["value"],
                    "receipt_resource_version": acknowledged["resource_version"],
                    "observed_resource_version": metadata["resourceVersion"],
                    "resource_versions_compared_as_opaque_identity": True,
                })
                return app
            if operation.get("info") == markers and operation_state.get("phase") in {"Error", "Failed"}:
                raise PlatformCIError("Argo Application sync ended in a failed phase")
            time.sleep(POLL_INTERVAL)
        raise PlatformCIError(f"Argo did not complete the selected revision sync: {last}")

    def _wait_for_execution(self, incident_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 480
        while time.monotonic() < deadline:
            status = self._operations()
            executions = status.get("executions")
            if not isinstance(executions, list):
                raise PlatformCIError("operations route omitted executions after approval")
            matches = [
                item
                for item in executions
                if isinstance(item, dict) and item.get("incident_id") == incident_id
            ]
            if len(matches) == 1 and matches[0].get("state") in {
                "failed",
                "uncertain",
                "interrupted",
                "cancelled",
            }:
                raise PlatformCIError("operational execution did not complete successfully")
            if len(matches) == 1 and matches[0].get("state") == "completed":
                return self._operations_by_id(matches[0]["execution_id"])
            time.sleep(POLL_INTERVAL)
        raise PlatformCIError("timed out waiting for the completed operational evidence endpoint")

    def _read_pod_logs(self, resource: str, namespace: str, filename: str) -> str:
        result = self._kubectl(
            "logs",
            "-n",
            namespace,
            resource,
            "--all-containers=false",
            "--tail=2000",
            timeout=30,
        )
        text = sanitize_text(result.stdout, self.secret_values)
        destination = self.evidence_dir / "logs" / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        return text

    def _verify_webhook_admission_log(self) -> str:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            logs = self._read_pod_logs(
                "deployment/atlasops-coordinator",
                "default",
                "coordinator-after-alert.log",
            )
            if re.search(r'"POST /webhook HTTP/1\.[01]" 200', logs):
                return logs
            time.sleep(POLL_INTERVAL)
        raise PlatformCIError("coordinator access log lacks a completed Alertmanager webhook response")

    def _fault_deployment(self) -> tuple[dict[str, Any], str, datetime]:
        self._stage("fault_injection", "running")
        self._verify_owner_unchanged()
        before = self._deployment()
        metadata = before.get("metadata") or {}
        spec = before.get("spec") or {}
        template = spec.get("template") or {}
        template_spec = template.get("spec") or {}
        containers = template_spec.get("containers")
        source_strategy = spec.get("strategy")
        if (
            metadata.get("name") != WORKLOAD_NAME
            or metadata.get("namespace") != APP_WORKLOAD_NAMESPACE
            or not isinstance(metadata.get("uid"), str)
            or not metadata.get("uid")
            or not isinstance(metadata.get("resourceVersion"), str)
            or not metadata.get("resourceVersion")
            or spec.get("replicas") != 1
            or not isinstance(source_strategy, dict)
            or source_strategy.get("type") != "RollingUpdate"
            or not isinstance(containers, list)
        ):
            raise PlatformCIError("frontend Deployment is not eligible for the bounded image fixture")
        matches = [
            (index, item)
            for index, item in enumerate(containers)
            if isinstance(item, dict) and item.get("name") == WORKLOAD_CONTAINER
        ]
        if len(matches) != 1:
            raise PlatformCIError("frontend server container identity is ambiguous")
        index, container = matches[0]
        invalid_image = (
            "atlasops-ci-unavailable:run-"
            f"{self.identity.run_id}-{self.identity.attempt}"
        )
        if len(invalid_image) > 128 or not re.fullmatch(
            r"[a-z0-9]+(?:[._-][a-z0-9]+)*:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}",
            invalid_image,
        ):
            raise PlatformCIError("derived unavailable image reference is invalid")
        image_probe = self._docker(
            "image",
            "inspect",
            invalid_image,
            check=False,
            timeout=15,
            label="confirm unavailable local fault image is absent",
        )
        if image_probe.returncode == 0:
            raise PlatformCIError("refusing to use a fault image that is already present locally")
        json_patch = [
            {"op": "test", "path": "/metadata/uid", "value": metadata.get("uid")},
            {"op": "test", "path": "/metadata/resourceVersion", "value": metadata.get("resourceVersion")},
            {"op": "test", "path": "/spec/replicas", "value": 1},
            {
                "op": "test",
                "path": "/metadata/labels",
                "value": metadata.get("labels", {}),
            },
            {
                "op": "test",
                "path": "/spec/template/metadata/labels",
                "value": template.get("metadata", {}).get("labels", {}),
            },
            {
                "op": "test",
                "path": "/spec/strategy",
                "value": copy.deepcopy(source_strategy),
            },
            {
                "op": "test",
                "path": f"/spec/template/spec/containers/{index}/name",
                "value": WORKLOAD_CONTAINER,
            },
            {
                "op": "test",
                "path": f"/spec/template/spec/containers/{index}/image",
                "value": container.get("image"),
            },
            {
                "op": "replace",
                "path": f"/spec/template/spec/containers/{index}/image",
                "value": invalid_image,
            },
        ]
        pull_policy_path = f"/spec/template/spec/containers/{index}/imagePullPolicy"
        json_patch.append(
            {
                "op": "replace" if "imagePullPolicy" in container else "add",
                "path": pull_policy_path,
                "value": "Never",
            }
        )
        json_patch.append(
            {
                "op": "replace",
                "path": "/spec/strategy",
                "value": {"type": "Recreate"},
            }
        )
        started = datetime.now(timezone.utc)
        self._kubectl(
            "patch",
            "deployment",
            WORKLOAD_NAME,
            "-n",
            APP_WORKLOAD_NAMESPACE,
            "--type=json",
            "-p",
            json.dumps(json_patch, separators=(",", ":")),
            timeout=30,
            label="apply one atomic frontend image/pullPolicy/strategy fixture fault",
        )
        after = self._deployment()
        expected_spec = copy.deepcopy(spec)
        expected_containers = expected_spec["template"]["spec"]["containers"]
        expected_containers[index]["image"] = invalid_image
        expected_containers[index]["imagePullPolicy"] = "Never"
        expected_spec["strategy"] = {"type": "Recreate"}
        if (
            after.get("spec") != expected_spec
            or after.get("metadata", {}).get("labels") != metadata.get("labels")
            or after.get("spec", {}).get("template", {}).get("metadata", {}).get("labels")
            != template.get("metadata", {}).get("labels")
            or after.get("spec", {}).get("replicas") != 1
        ):
            raise PlatformCIError(
                "fault changed fields other than frontend server image/pullPolicy/strategy"
            )
        self._record(
            "fault",
            {
                "target": f"{APP_WORKLOAD_NAMESPACE}/{WORKLOAD_NAME}",
                "container": WORKLOAD_CONTAINER,
                "original_image": container.get("image"),
                "original_image_pull_policy": container.get("imagePullPolicy"),
                "fault_image": invalid_image,
                "image_pull_policy": "Never",
                "original_strategy": copy.deepcopy(source_strategy),
                "temporary_strategy": {"type": "Recreate"},
                "desired_replicas": 1,
                "deployment_uid": metadata.get("uid"),
                "deployment_resource_version": metadata.get("resourceVersion"),
                "argocd_labels_preserved": True,
                "changed_fields": [
                    "/spec/template/spec/containers/server/image",
                    "/spec/template/spec/containers/server/imagePullPolicy",
                    "/spec/strategy",
                ],
            },
        )
        self._stage("fault_injection", "completed", fault_started_at=started.isoformat())
        return before, invalid_image, started

    def _verify_frontend_source_restored(self, before: dict[str, Any]) -> None:
        original_metadata = before.get("metadata") or {}
        original_spec = before.get("spec") or {}
        original_template = original_spec.get("template") or {}
        original_template_spec = original_template.get("spec") or {}
        original_containers = original_template_spec.get("containers") or []
        original_matches = [
            item
            for item in original_containers
            if isinstance(item, dict) and item.get("name") == WORKLOAD_CONTAINER
        ]
        if len(original_matches) != 1:
            raise PlatformCIError("source frontend server container identity is ambiguous")

        restored = self._deployment()
        restored_metadata = restored.get("metadata") or {}
        restored_spec = restored.get("spec") or {}
        restored_template = restored_spec.get("template") or {}
        restored_template_spec = restored_template.get("spec") or {}
        restored_containers = restored_template_spec.get("containers") or []
        restored_matches = [
            item
            for item in restored_containers
            if isinstance(item, dict) and item.get("name") == WORKLOAD_CONTAINER
        ]
        if (
            restored_metadata.get("name") != WORKLOAD_NAME
            or restored_metadata.get("namespace") != APP_WORKLOAD_NAMESPACE
            or restored_metadata.get("uid") != original_metadata.get("uid")
            or len(restored_matches) != 1
        ):
            raise PlatformCIError("Argo reconciliation changed the frontend Deployment identity")

        if (
            ("strategy" in restored_spec) != ("strategy" in original_spec)
            or restored_spec.get("strategy") != original_spec.get("strategy")
        ):
            raise PlatformCIError(
                "Argo reconciliation did not restore the original frontend Deployment strategy"
            )
        original_container = original_matches[0]
        restored_container = restored_matches[0]
        if (
            restored_container.get("image") != original_container.get("image")
            or ("image" in restored_container) != ("image" in original_container)
        ):
            raise PlatformCIError(
                "Argo reconciliation did not restore the original frontend server image"
            )
        if (
            restored_container.get("imagePullPolicy")
            != original_container.get("imagePullPolicy")
            or ("imagePullPolicy" in restored_container)
            != ("imagePullPolicy" in original_container)
        ):
            raise PlatformCIError(
                "Argo reconciliation did not restore the original frontend imagePullPolicy"
            )
        if (
            restored_metadata.get("labels") != original_metadata.get("labels")
            or restored_template.get("metadata", {}).get("labels")
            != original_template.get("metadata", {}).get("labels")
        ):
            raise PlatformCIError("Argo reconciliation did not restore frontend Deployment labels")
        self._record(
            "fault_reconciliation",
            {
                "target": f"{APP_WORKLOAD_NAMESPACE}/{WORKLOAD_NAME}",
                "deployment_uid": original_metadata.get("uid"),
                "source_strategy_restored": True,
                "source_image_restored": True,
                "source_image_pull_policy_restored": True,
                "source_strategy": copy.deepcopy(original_spec.get("strategy")),
                "source_strategy_present": "strategy" in original_spec,
            },
        )

    def _wait_for_broken_pod(self, invalid_image: str, deadline: float) -> None:
        while time.monotonic() < deadline:
            deployment = self._deployment()
            if deployment.get("spec", {}).get("replicas") != 1:
                raise PlatformCIError("frontend desired replicas changed during image fault")
            if deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("image") != invalid_image:
                raise PlatformCIError("frontend image fault was overwritten before alert admission")
            if self._assert_workload_broken():
                pods = self._kubectl_json(
                    "get",
                    "pods",
                    "-n",
                    APP_WORKLOAD_NAMESPACE,
                    "-l",
                    "app=frontend",
                ).get("items")
                waiting = []
                if isinstance(pods, list):
                    for pod in pods:
                        statuses = pod.get("status", {}).get("containerStatuses", []) if isinstance(pod, dict) else []
                        for status in statuses:
                            state = status.get("state", {}).get("waiting", {}) if isinstance(status, dict) else {}
                            if state.get("reason") in {"ErrImageNeverPull", "ImagePullBackOff", "ErrImagePull"}:
                                waiting.append(state.get("reason"))
                if waiting:
                    self.data["fault"]["observed_waiting_reasons"] = sorted(set(waiting))
                    self._flush()
                    return
            time.sleep(POLL_INTERVAL)
        raise PlatformCIError("frontend never reached the expected unavailable image state")

    def _fault_and_wait_for_admission(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        before, invalid_image, started = self._fault_deployment()
        self._wait_for_broken_pod(invalid_image, time.monotonic() + 180)
        pending, pre_approval_evidence = self._wait_for_pending_incident(started.isoformat())
        selected = self.data["pre_fault_eligibility"]
        action = self._pending_action(pending, selected)
        self._record(
            "alert_admission",
            {
                "prometheus": self._alert_rule_firing(),
                "alertmanager": self._active_frontend_alert(self._alertmanager_alerts()),
                "webhook": {
                    "route": "/webhook",
                    "http_status": None,
                    "response_status": "awaiting_execution_completion",
                    "execution_id": pre_approval_evidence.get("execution_id"),
                    "authenticated_admission": True,
                    "direct_coordinator_injection": False,
                },
                "pending_action": {
                    "tool": action["tool"],
                    "arguments": action["arguments"],
                    "target": action["target"],
                    "verification_target": action["verification_target"],
                    "action_digest": action["action_digest"],
                },
                "pre_approval_events": pre_approval_evidence.get("events"),
            },
        )
        return pending, pre_approval_evidence, action, before

    def _fixture_traffic(self) -> dict[str, Any]:
        return exercise_boutique("http://127.0.0.1:8080")

    def _collect_coordinator_telemetry(
        self,
        completion: dict[str, Any],
        proof: dict[str, Any],
    ) -> dict[str, Any]:
        deadline = time.monotonic() + COORDINATOR_METRICS_TIMEOUT_SECONDS
        metrics = None
        last_validation_error = None
        while time.monotonic() < deadline:
            http_series = self._prometheus_query(
                "increase(atlasops_http_requests_total[5m])"
            )
            incident_series = self._prometheus_query("atlasops_incidents_total")
            target_info = self._prometheus_query("target_info")
            try:
                metrics = validate_coordinator_telemetry(
                    http_series,
                    incident_series,
                    target_info,
                    source_sha=self.identity.source_sha,
                )
            except PlatformCIError as exc:
                last_validation_error = exc
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(POLL_INTERVAL, remaining))
            else:
                break
        if metrics is None:
            raise PlatformCIError(
                "fresh coordinator HTTP and resolved-incident metrics were not observed "
                f"within {COORDINATOR_METRICS_TIMEOUT_SECONDS} seconds"
            ) from last_validation_error
        status, traces = self._http(
            f"http://127.0.0.1:{self.ports['jaeger']}/api/traces?"
            + urlencode({"service": "atlasops-coordinator", "limit": "20"}),
            timeout=15,
            label="read coordinator HTTP and incident spans",
        )
        if status != 200 or not isinstance(traces, dict):
            raise PlatformCIError("Jaeger coordinator trace query failed")
        trace = validate_coordinator_trace(
            traces,
            source_sha=self.identity.source_sha,
            incident_id=completion["incident_id"],
            execution_id=completion["execution_id"],
            tool_audit_evidence=proof["tool_audit_evidence"],
        )
        result = {"metrics": metrics, "trace": trace}
        self._record("coordinator_telemetry", result)
        return result

    def _collect_fixture_logs(self) -> None:
        logs = self._read_pod_logs(
            f"deployment/{FIXTURE_SERVICE}",
            "default",
            "fixture-requests.log",
        )
        requests = []
        for line in logs.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("role") in {
                "triage",
                "diagnosis",
                "remediation",
                "comms",
            }:
                requests.append(row)
        served_roles = {row["role"] for row in requests}
        if served_roles != {"triage", "diagnosis", "remediation", "comms"}:
            raise PlatformCIError("fixture request logs do not prove all four roles ran")
        if not any(
            row.get("selected_history_id") == self.data["pre_fault_eligibility"]["history_id"]
            for row in requests
            if row.get("role") == "remediation"
        ):
            raise PlatformCIError("fixture request evidence did not select the observed Argo history ID")
        self._record(
            "fixture_requests",
            {
                "count": len(requests),
                "roles": sorted(served_roles),
                "requests": requests,
                "raw_authorization_headers_archived": False,
            },
        )

    def _save_postmortem(self, completion: dict[str, Any]) -> None:
        record = completion.get("record") or {}
        comms = record.get("comms") if isinstance(record, dict) else None
        trajectory = comms.get("trajectory") if isinstance(comms, dict) else None
        path = None
        if isinstance(trajectory, list):
            for entry in trajectory:
                if not isinstance(entry, dict) or entry.get("tool") != "postmortem_draft":
                    continue
                output = entry.get("output")
                if isinstance(output, dict) and output.get("success") is True:
                    path = output.get("postmortem_path") or output.get("path")
                    break
        if not isinstance(path, str) or not re.fullmatch(
            r"/var/lib/atlasops/postmortems/[A-Za-z0-9._-]+\.md",
            path,
        ):
            raise PlatformCIError("postmortem path is not a direct file in the runtime directory")
        content = self._kubectl(
            "exec",
            "-n",
            "default",
            "deployment/atlasops-coordinator",
            "-c",
            "coordinator",
            "--",
            "cat",
            path,
            timeout=30,
            label="read the saved local postmortem from the runtime volume",
        ).stdout
        content = sanitize_text(content, self.secret_values)
        if "## Summary" not in content or "## Resolution" not in content:
            raise PlatformCIError("saved postmortem is missing its expected sections")
        destination = self.evidence_dir / "postmortem" / Path(path).name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        self._record(
            "postmortem",
            {
                "runtime_path": path,
                "evidence_path": str(destination.relative_to(self.evidence_dir)),
                "saved": True,
                "external_delivery": False,
            },
        )
        comms_log = self._kubectl(
            "exec",
            "-n",
            "default",
            "deployment/atlasops-coordinator",
            "-c",
            "coordinator",
            "--",
            "cat",
            "/var/lib/atlasops/comms/slack_posts.jsonl",
            timeout=30,
            check=False,
            label="read local-only Comms log",
        )
        if comms_log.returncode:
            raise PlatformCIError("local Comms log was not available on the runtime volume")
        safe_lines = []
        for line in comms_log.stdout.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                safe_lines.append(sanitize_evidence(item, self.secret_values))
        if not safe_lines:
            raise PlatformCIError("no local Comms log entry was retained")
        write_json(self.evidence_dir / "comms" / "local-log.json", {"entries": safe_lines})
        self.data["postmortem"]["local_comms_entries"] = len(safe_lines)
        self._flush()

    def _workflow_logs_and_evidence(
        self,
        completion: dict[str, Any],
        *,
        selected_revision: str,
    ) -> None:
        logs = self._read_pod_logs(
            "deployment/atlasops-coordinator",
            "default",
            "coordinator-final.log",
        )
        if not re.search(r'"POST /webhook HTTP/1\.[01]" 200', logs):
            raise PlatformCIError("coordinator logs lost the admitted webhook receipt")
        write_json(self.evidence_dir / "api-evidence" / "completed-operation.json", completion)
        self._record(
            "selected_argo_revision",
            {
                "build_sha": self.identity.source_sha,
                "history_id": self.data["pre_fault_eligibility"]["history_id"],
                "revision": selected_revision,
                "same_source_reconciliation": selected_revision == self.identity.source_sha,
                "previous_release_rollback": False,
            },
        )
        self._save_postmortem(completion)
        self._collect_fixture_logs()

    def _execute(self) -> None:
        self._ensure_private_area()
        self._prepare_secrets()
        self._record_fixture_identity()
        self._prepare_docker_context()
        self._render_manifests()

        self._stage("cluster_create", "running")
        self._create_cluster()
        self._stage("platform_setup", "running")
        self._setup_platform()

        self._stage("fixture_install", "running")
        self._install_fixture()
        self._verify_image_identity()
        self._stage("admin_forwards", "running")
        self._start_forwards()
        self._wait_tcp_service(
            "coordinator",
            f"{self._api_root()}/health",
            120,
        )
        self._wait_tcp_service(
            "Prometheus",
            f"http://127.0.0.1:{self.ports['prometheus']}/-/ready",
            120,
        )
        self._wait_tcp_service(
            "Alertmanager",
            f"http://127.0.0.1:{self.ports['alertmanager']}/-/ready",
            120,
        )
        self._wait_tcp_service(
            "Jaeger",
            f"http://127.0.0.1:{self.ports['jaeger']}/api/services",
            120,
        )
        operations = self._operations()
        if operations.get("pending"):
            raise PlatformCIError("fresh disposable coordinator unexpectedly has a pending action")
        self._record(
            "baseline_operations",
            {
                "authenticated_status": True,
                "source_sha": operations.get("source_sha"),
                "certification_status": operations.get("certification_status"),
                "pending_count": 0,
            },
        )

        self._stage("pre_fault_signals", "running")
        first_traffic = self._fixture_traffic()
        pre_fault_live = self._verify_live("pre-fault")
        eligibility = self._check_frontend_rule(before_fault=True)
        self._record(
            "pre_fault_signals",
            {
                "frontend_traffic": first_traffic,
                "seven_service_verification": pre_fault_live,
                "eligible_application_revision": eligibility["application_revision"],
            },
        )
        self._stage("pre_fault_signals", "completed")
        self._verify_owner_unchanged()

        (
            pending,
            pre_approval_evidence,
            prepared_action,
            pre_fault_deployment,
        ) = self._fault_and_wait_for_admission()
        self._pending_digest = prepared_action["action_digest"]
        self._pending_token = prepared_action["token"]
        self._prove_rejection_before_approval(pending, pre_approval_evidence)
        before_approval = self._application()
        self._append_jsonl(
            "api-evidence/argo-application-before-approval.jsonl", before_approval
        )
        self._approve_exact_action(pending)
        dispatch = self._wait_for_approved_mutation_returned(pending)
        self._stage(
            "approval_and_recovery",
            "running",
            approval_timestamp_source="durable_execution_journal",
            approval_timestamp=dispatch["approval_timestamp"],
            mutation_started_timestamp=dispatch["mutation_started_timestamp"],
        )
        self._wait_for_application_sync(
            eligibility["history_revision"], dispatch, before=before_approval
        )
        self._verify_frontend_source_restored(pre_fault_deployment)
        completion = self._wait_for_execution(pending["incident_id"])
        proof = validate_completion_evidence(
            completion,
            source_sha=self.identity.source_sha,
            history_id=eligibility["history_id"],
            expected_operator="ci-synthetic-operator",
            audit_secret=self._audit_secret(),
        )
        verification = completion.get("record", {}).get("verification")
        validate_verification(
            verification,
            expected_uid=prepared_action["verification_target"]["uid"],
        )
        self._verify_webhook_admission_log()
        self._record("webhook_response", {
            "route": "/webhook",
            "http_status": 200,
            "execution_id": completion.get("execution_id"),
            "observed_after_execution_completion": True,
        })
        self._record("completion_proof", proof)
        self._stage("approval_and_recovery", "completed")

        second_traffic = self._fixture_traffic()
        post_fault_live = self._verify_live("post-recovery")
        telemetry = self._collect_coordinator_telemetry(completion, proof)
        self._record(
            "post_recovery_signals",
            {
                "frontend_traffic": second_traffic,
                "seven_service_verification": post_fault_live,
                "coordinator_telemetry": telemetry,
            },
        )
        self._workflow_logs_and_evidence(
            completion,
            selected_revision=eligibility["history_revision"],
        )
        self._record("status", "passed")

    def _stop_forwards(self) -> None:
        failed_names: list[str] = []
        for name, forward in list(self.forwards.items()):
            error_type: str | None = None
            try:
                if forward.process.poll() is None:
                    forward.process.terminate()
                    try:
                        forward.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        forward.process.kill()
                        forward.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as exc:
                error_type = type(exc).__name__
            try:
                forward.log_stream.close()
            except OSError as exc:
                error_type = error_type or type(exc).__name__
            if forward.log_path.is_file():
                try:
                    text = sanitize_text(forward.log_path.read_text(encoding="utf-8", errors="replace"), self.secret_values)
                    forward.log_path.write_text(text, encoding="utf-8")
                except OSError as exc:
                    error_type = error_type or type(exc).__name__
            try:
                terminated = forward.process.poll() is not None
            except OSError as exc:
                terminated = False
                error_type = error_type or type(exc).__name__
            record = {"name": name, "bind": "127.0.0.1", "terminated": terminated}
            if error_type:
                record["error_type"] = error_type
            try:
                self._append_jsonl("cleanup/port-forwards.jsonl", record)
            except OSError as exc:
                error_type = error_type or type(exc).__name__
            if error_type or not terminated:
                failed_names.append(name)
        self.forwards.clear()
        if failed_names:
            raise PlatformCIError(f"port-forward cleanup failed for {', '.join(failed_names)}")

    def _cleanup_cluster(self) -> None:
        cleanup = {
            "status": "not_attempted",
            "cluster_name": self.cluster_name,
            "created_by_runner": self.cluster_created_by_runner,
            "ownership_rechecked": False,
            "deleted": False,
            "verified_absent": False,
        }
        if not self.creation_attempted:
            cleanup["status"] = "not_created"
            self.data["cleanup"] = cleanup
            self._flush()
            return
        if not self.cluster_created_by_runner or self.owner is None:
            cleanup.update(
                status="ownership_unverified_no_delete",
                reason="creation did not complete with a full immediate ownership receipt",
            )
            self.data["cleanup"] = cleanup
            self._flush()
            return
        try:
            observed = self._read_owner()
            cleanup["ownership_rechecked"] = True
            cleanup["observed_ownership"] = asdict(observed)
            if not current_owner_matches(self.owner, observed):
                cleanup.update(
                    status="ownership_mismatch_no_delete",
                    reason="complete cluster identity no longer matches the creation receipt",
                )
                self.data["cleanup"] = cleanup
                self._flush()
                return
            self._kind(
                "delete",
                "cluster",
                "--name",
                self.cluster_name,
                timeout=180,
                label="delete only the verified runner-owned Kind cluster",
            )
            remaining = self._cluster_list()
            node_result = self._docker(
                "ps",
                "--all",
                "--quiet",
                "--no-trunc",
                f"--filter=label={KIND_CLUSTER_LABEL}={self.cluster_name}",
                check=False,
                timeout=30,
                label="verify runner-owned cluster deletion",
            )
            if node_result.returncode:
                raise PlatformCIError("cluster cleanup Docker absence verification failed")
            remaining_nodes = [line.strip() for line in node_result.stdout.splitlines() if line.strip()]
            if self.cluster_name in remaining or remaining_nodes:
                raise PlatformCIError("owned cluster remained after the deletion command")
            cleanup.update(
                status="verified_deleted",
                deleted=True,
                verified_absent=True,
            )
        except Exception as exc:
            cleanup.update(
                status="cleanup_unverified",
                error=sanitize_text(f"{type(exc).__name__}: {exc}", self.secret_values),
            )
            if self.data.get("status") == "passed":
                self.data["status"] = "failed"
                self.data["failure"] = {
                    "stage": "cleanup",
                    "error_type": type(exc).__name__,
                    "message": sanitize_text(str(exc), self.secret_values),
                }
        self.data["cleanup"] = sanitize_evidence(cleanup, self.secret_values)
        self._flush()

    def run(self) -> Path:
        failure: Exception | None = None
        try:
            self._execute()
        except Exception as exc:
            failure = exc
            self.data["status"] = "failed"
            self.data["failure"] = {
                "stage": self.stage,
                "error_type": type(exc).__name__,
                "message": sanitize_text(str(exc), self.secret_values),
            }
            self._flush()
        finally:
            def retain_cleanup_error(stage: str, exc: Exception) -> None:
                item = {
                    "stage": stage,
                    "error_type": type(exc).__name__,
                    "message": sanitize_text(str(exc), self.secret_values),
                }
                self.data.setdefault("cleanup_errors", []).append(item)
                if self.data.get("status") != "failed":
                    self.data["status"] = "failed"
                    self.data["failure"] = item

            try:
                try:
                    self._stop_forwards()
                except Exception as exc:
                    retain_cleanup_error("port_forward_cleanup", exc)
                    failure = failure or exc
                try:
                    self._cleanup_cluster()
                except Exception as exc:
                    retain_cleanup_error("cluster_cleanup", exc)
                    cleanup = self.data.get("cleanup")
                    if isinstance(cleanup, dict):
                        cleanup.update(status="cleanup_unverified", deleted=False, verified_absent=False)
                    else:
                        self.data["cleanup"] = {
                            "status": "cleanup_unverified",
                            "deleted": False,
                            "verified_absent": False,
                        }
                    failure = failure or exc
                if (
                    self.data.get("status") == "passed"
                    and self.data["cleanup"]["status"] != "verified_deleted"
                ):
                    exc = PlatformCIError("cluster cleanup is not verified")
                    retain_cleanup_error("cluster_cleanup", exc)
                    failure = failure or exc
                self._flush()
            finally:
                if self.private_temp is not None:
                    self.private_temp.cleanup()
        if failure is not None:
            raise PlatformCIError(sanitize_text(str(failure), self.secret_values)) from failure
        if self.data.get("status") != "passed":
            raise PlatformCIError("platform-ci runner did not complete with verified cleanup")
        return self.evidence_dir / "evidence.json"


def run_integration(
    evidence_path: str | Path,
    *,
    environ: dict[str, str] | None = None,
) -> Path:
    env = dict(os.environ if environ is None else environ)
    evidence_dir = validate_external_evidence_path(evidence_path)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": 1,
        "status": "failed",
        "classification": FIXTURE_CLASSIFICATION,
        "certification_status": CERTIFICATION_STATUS,
        "guard": "GitHub-hosted exact-source identity is checked before Docker or Kubernetes access",
    }
    try:
        identity = validate_run_identity(env)
    except Exception as exc:
        receipt["failure"] = {
            "stage": "identity_guard",
            "error_type": type(exc).__name__,
            "message": sanitize_text(str(exc)),
        }
        write_json(evidence_dir / "evidence.json", receipt)
        write_json(evidence_dir / "failure.json", receipt["failure"])
        raise PlatformCIError(sanitize_text(str(exc))) from exc
    runner = Runner(identity, evidence_dir, env=env)
    return runner.run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run one disposable GitHub Actions platform check")
    run.add_argument("--evidence-dir", required=True, type=Path)
    fixture = commands.add_parser("serve-fixture", help=argparse.SUPPRESS)
    fixture.add_argument("--key-file", required=True, type=Path)
    fixture.add_argument("--source-sha", required=True)
    return parser


def main(argv: list[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve-fixture":
        try:
            return run_fixture_server(args.key_file, args.source_sha)
        except PlatformCIError as exc:
            print(f"platform-ci fixture failed: {exc}", file=sys.stderr)
            return 2
    try:
        evidence = run_integration(args.evidence_dir, environ=environ)
    except PlatformCIError as exc:
        print(f"platform-ci failed: {sanitize_text(str(exc))}", file=sys.stderr)
        return 2
    print(f"platform-ci completed; sanitized evidence: {evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
