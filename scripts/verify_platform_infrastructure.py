#!/usr/bin/env python3
"""Render and verify the bounded local AI-SRE platform configuration."""

from __future__ import annotations

import argparse
import http.client
import json
import math
import queue
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
INFRA_LOCAL = REPO_ROOT / "infra" / "local"
APPLICATION_NAME = "atlasops-boutique"
APPLICATION_NAMESPACE = "argocd"
APPLICATION_PROJECT = "atlasops-boutique"
APPLICATION_REPOSITORY = "https://github.com/virajchoudhary/AtlasOps.git"
APPLICATION_PATH = "infra/local/boutique"
APPLICATION_DESTINATION = "https://kubernetes.default.svc"
BOUTIQUE_NAMESPACE = "atlasops-boutique"
KIND_NODE_IMAGE = (
    "kindest/node:v1.35.8@sha256:"
    "07b2536e30b803ed61d1677a79df6115f798ce64c80f9e22f6ed45afd09323c0"
)
KUBECTL_VERSION = "v1.35.8"
KUBECTL_SHA256 = "874d5e72dbb819f43cff16bcd1e4f8bac5b7f2361fe1e55049b0a6c676fb0cbf"
KUBECTL_SHA256_ARM64 = "cc749967b62f4422260bc9c0aa7a7c55f45175ae38cb8d95767b5d2b7e04c1fd"
COORDINATOR_SERVICE_ACCOUNT = (
    "system:serviceaccount:default:atlasops-coordinator"
)
ADMISSION_POLICY_NAME = "atlasops-coordinator-rollback-only"
SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
OPERATOR_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_.@+-]{1,128}$")
KIND_CLUSTER_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
IMAGE_ID_PATTERN = re.compile(
    r"^(?:[A-Za-z][A-Za-z0-9+.-]*://)?(?:[^@\s]+@)?sha256:[0-9a-f]{64}$"
)
KIND_CLUSTER_LABEL = "io.x-k8s.kind.cluster"
KIND_NODE_ROLE_LABEL = "io.x-k8s.kind.role"
TRACED_BOUTIQUE_SERVICES = (
    "frontend",
    "checkoutservice",
    "currencyservice",
    "productcatalogservice",
    "paymentservice",
    "emailservice",
    "recommendationservice",
)
ONLINE_BOUTIQUE_RESOURCE_NAMES = (
    "adservice",
    "cartservice",
    "checkoutservice",
    "currencyservice",
    "emailservice",
    "frontend",
    "frontend-external",
    "loadgenerator",
    "paymentservice",
    "productcatalogservice",
    "recommendationservice",
    "redis-cart",
    "shippingservice",
)
SIGNAL_WINDOW_SECONDS = 300
FORWARD_START_TIMEOUT_SECONDS = 15
FORWARD_STOP_TIMEOUT_SECONDS = 5
FORWARD_READY_PATTERN = re.compile(
    r"^Forwarding from 127\.0\.0\.1:(\d+) -> (\d+)$"
)


@dataclass(frozen=True)
class ServiceRoute:
    name: str
    namespace: str
    service_name: str
    service_port: int
    target_port: int | str
    pod_port: int


@dataclass(frozen=True)
class ServiceEndpoint:
    name: str
    namespace: str
    service_name: str
    pod_name: str
    pod_uid: str
    pod_ip: str
    pod_port: int


@dataclass(frozen=True)
class KubeTarget:
    kubeconfig: Path
    kube_context: str


SERVICE_ROUTES = (
    ServiceRoute(
        "coordinator",
        "default",
        "atlasops-coordinator-svc",
        9099,
        "http",
        7860,
    ),
    ServiceRoute(
        "prometheus",
        "monitoring",
        "prometheus-kube-prometheus-prometheus",
        9090,
        9090,
        9090,
    ),
    ServiceRoute(
        "jaeger",
        "jaeger",
        "jaeger",
        16686,
        16686,
        16686,
    ),
)


def load_documents(path: Path) -> list[dict[str, Any]]:
    return [document for document in yaml.safe_load_all(path.read_text(encoding="utf-8")) if document]


def find_document(
    documents: list[dict[str, Any]], kind: str, name: str
) -> dict[str, Any]:
    for document in documents:
        metadata = document.get("metadata", {})
        if document.get("kind") == kind and metadata.get("name") == name:
            return document
    raise ValueError(f"missing {kind}/{name}")


def validate_source_sha(source_sha: str) -> None:
    if not SOURCE_SHA_PATTERN.fullmatch(source_sha):
        raise ValueError("source revision must be a 40-character commit SHA")


def validate_kind_cluster_name(cluster_name: str) -> None:
    if not isinstance(cluster_name, str) or not KIND_CLUSTER_NAME_PATTERN.fullmatch(
        cluster_name
    ):
        raise ValueError(
            "Kind cluster name must be a lowercase DNS label of at most 63 characters"
        )


def _validate_kube_context(kube_context: str) -> None:
    if (
        not isinstance(kube_context, str)
        or not kube_context
        or kube_context != kube_context.strip()
        or len(kube_context) > 253
        or any(ord(char) < 33 or ord(char) == 127 for char in kube_context)
    ):
        raise ValueError("Kubernetes context must be a non-empty name without whitespace")


def _validate_kube_target(target: KubeTarget) -> None:
    if not isinstance(target, KubeTarget):
        raise TypeError("live Kubernetes operations require an explicit target")
    if not isinstance(target.kubeconfig, Path) or not target.kubeconfig.is_file():
        raise ValueError("explicit kubeconfig file is missing")
    _validate_kube_context(target.kube_context)


def _run_local_command(
    command: list[str], *, description: str, timeout: float = 30
) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{description} could not be verified") from exc
    if result.returncode != 0:
        raise RuntimeError(f"{description} failed with exit code {result.returncode}")
    if not isinstance(result.stdout, str):
        raise TypeError(f"{description} returned no readable output")
    return result.stdout


def _active_port_bindings(value: Any, description: str) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        raise TypeError(f"{description} port bindings are missing or malformed")
    active: dict[str, list[dict[str, Any]]] = {}
    for port, bindings in value.items():
        if bindings is None:
            continue
        if not isinstance(bindings, list):
            raise TypeError(f"{description} port bindings are malformed")
        if bindings:
            active[port] = bindings
    return active


def _loopback_binding(
    bindings: dict[str, list[dict[str, Any]]],
    port: str,
    *,
    expected_host_port: str | None = None,
    description: str,
) -> tuple[str, str]:
    values = bindings.get(port)
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise RuntimeError(f"{description} must publish exactly one {port} binding")
    host_ip = values[0].get("HostIp")
    host_port = values[0].get("HostPort")
    if (
        host_ip != "127.0.0.1"
        or not isinstance(host_port, str)
        or not host_port.isdigit()
        or not 1 <= int(host_port) <= 65535
        or expected_host_port is not None
        and host_port != expected_host_port
    ):
        raise RuntimeError(
            f"{description} {port} binding does not match the loopback-only contract"
        )
    return host_ip, host_port


def _is_online_boutique_resource(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        raise TypeError("Kubernetes returned a resource without metadata")
    name = metadata.get("name")
    labels = metadata.get("labels", {})
    if not isinstance(name, str) or not isinstance(labels, dict):
        raise TypeError("Kubernetes returned malformed resource metadata")

    normalized_name = name.casefold()
    known_names = {value.casefold() for value in ONLINE_BOUTIQUE_RESOURCE_NAMES}
    if normalized_name in known_names or any(
        normalized_name.startswith(f"{service}-")
        for service in known_names
        if service != "frontend-external"
    ):
        return True

    boutique_label_values = {
        "onlineboutique",
        "online-boutique",
        "boutique",
        *known_names,
    }
    return any(
        isinstance(value, str) and value.casefold() in boutique_label_values
        for value in labels.values()
    )


def verify_default_namespace_has_no_boutique_resources(kube_context: str) -> None:
    _validate_kube_context(kube_context)
    resources = (
        "deployments,replicasets,pods,services,endpoints,configmaps,"
        "serviceaccounts,secrets"
    )
    output = _run_local_command(
        [
            "kubectl",
            f"--context={kube_context}",
            "get",
            resources,
            "-n",
            "default",
            "-o",
            "json",
        ],
        description="default namespace ownership check",
        timeout=15,
    )
    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "default namespace ownership check did not return valid JSON"
        ) from exc
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise TypeError("default namespace ownership check returned malformed JSON")

    matches: list[str] = []
    for item in result["items"]:
        if not isinstance(item, dict):
            raise TypeError(
                "default namespace ownership check returned malformed items"
            )
        if _is_online_boutique_resource(item):
            metadata = item["metadata"]
            matches.append(f"{item.get('kind', 'resource')}/{metadata['name']}")
    if matches:
        raise RuntimeError(
            "legacy Online Boutique resources exist in the default namespace; "
            "refusing to reuse this cluster without manual migration: "
            + ", ".join(matches[:20])
        )


def verify_existing_kind_cluster(cluster_name: str, kube_context: str) -> bool:
    validate_kind_cluster_name(cluster_name)
    expected_context = f"kind-{cluster_name}"
    if kube_context != expected_context:
        raise ValueError(
            f"Kind context must be exactly {expected_context!r} for cluster {cluster_name!r}"
        )
    cluster_output = _run_local_command(
        ["kind", "get", "clusters"], description="Kind cluster inventory"
    )
    if cluster_name not in {line.strip() for line in cluster_output.splitlines()}:
        return False

    docker_ids = _run_local_command(
        [
            "docker",
            "ps",
            "--all",
            "--quiet",
            f"--filter=label={KIND_CLUSTER_LABEL}={cluster_name}",
        ],
        description="Kind node ownership inventory",
    )
    container_ids = [line.strip() for line in docker_ids.splitlines() if line.strip()]
    if len(container_ids) != 1:
        raise RuntimeError(
            "existing Kind cluster must have exactly one verifiable control-plane node"
        )
    inspect_output = _run_local_command(
        ["docker", "inspect", *container_ids],
        description="Kind node identity and host mapping inspection",
    )
    try:
        inspected = json.loads(inspect_output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Docker inspect did not return valid JSON") from exc
    if not isinstance(inspected, list) or len(inspected) != 1 or not isinstance(
        inspected[0], dict
    ):
        raise RuntimeError("Docker inspect returned an unverifiable Kind node")

    node = inspected[0]
    config = node.get("Config")
    state = node.get("State")
    if not isinstance(config, dict) or not isinstance(state, dict):
        raise TypeError("existing Kind node identity is missing or malformed")
    labels = config.get("Labels")
    if (
        node.get("Name") != f"/{cluster_name}-control-plane"
        or not isinstance(labels, dict)
        or labels.get(KIND_CLUSTER_LABEL) != cluster_name
        or labels.get(KIND_NODE_ROLE_LABEL) != "control-plane"
        or state.get("Running") is not True
    ):
        raise RuntimeError(
            "existing Kind node identity is missing, stopped, or owned by another cluster"
        )
    if config.get("Image") != KIND_NODE_IMAGE:
        raise RuntimeError("existing Kind node image does not match the reviewed immutable pin")

    host_config = node.get("HostConfig")
    network_settings = node.get("NetworkSettings")
    if not isinstance(host_config, dict) or not isinstance(network_settings, dict):
        raise TypeError("existing Kind node host mappings are missing or malformed")
    expected_ports = {"6443/tcp", "30080/tcp"}
    host_bindings = _active_port_bindings(
        host_config.get("PortBindings"),
        "Kind node configured",
    )
    realized_bindings = _active_port_bindings(
        network_settings.get("Ports"),
        "Kind node realized",
    )
    if set(host_bindings) != expected_ports or set(realized_bindings) != expected_ports:
        raise RuntimeError(
            "existing Kind node host ports differ from the reviewed private mapping"
        )
    for port, expected_host_port in (("6443/tcp", None), ("30080/tcp", "8080")):
        configured = _loopback_binding(
            host_bindings,
            port,
            expected_host_port=expected_host_port,
            description="Kind node configured",
        )
        realized = _loopback_binding(
            realized_bindings,
            port,
            expected_host_port=expected_host_port,
            description="Kind node realized",
        )
        if configured != realized:
            raise RuntimeError(f"Kind node {port} host mapping is not realized as configured")

    host_ip, host_port = _loopback_binding(
        realized_bindings, "6443/tcp", description="Kind node realized",
    )
    configured_server = _run_local_command(
        [
            "kubectl", f"--context={kube_context}", "config", "view", "--minify",
            "--output=jsonpath={.clusters[0].cluster.server}",
        ],
        description="Kind context server inspection", timeout=15,
    ).strip()
    if configured_server != f"https://{host_ip}:{host_port}":
        raise RuntimeError(
            "Kubernetes context server does not match the inspected Kind node"
        )
    version_output = _run_local_command(
        ["kubectl", f"--context={kube_context}", "version", "--output=json"],
        description="Kubernetes server version inspection",
        timeout=15,
    )
    try:
        version = json.loads(version_output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Kubernetes server version inspection returned invalid JSON") from exc
    if (
        not isinstance(version, dict)
        or not isinstance(version.get("serverVersion"), dict)
        or version["serverVersion"].get("gitVersion") != KUBECTL_VERSION
    ):
        raise RuntimeError(f"Kubernetes server version must be exactly {KUBECTL_VERSION}")
    verify_default_namespace_has_no_boutique_resources(kube_context)
    return True


def inspect_kind_cluster(args: argparse.Namespace) -> int:
    if verify_existing_kind_cluster(args.cluster_name, args.kube_context):
        print(
            f"Verified existing Kind cluster {args.cluster_name!r}, its immutable "
            "loopback mappings, ownership, and default namespace."
        )
    else:
        print(f"Kind cluster {args.cluster_name!r} does not exist.")
        return 3
    return 0


def _replace_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_placeholders(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if isinstance(value, str):
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
        return value
    return value


def _write_documents(path: Path, documents: list[dict[str, Any]]) -> None:
    rendered = yaml.safe_dump_all(
        documents,
        explicit_start=True,
        sort_keys=False,
        default_flow_style=False,
    )
    path.write_bytes(rendered.encode("utf-8"))


def render_prometheus_rules(output_dir: Path) -> Path:
    source = REPO_ROOT / "infra" / "kubernetes" / "atlasops-prometheus-rules.yaml"
    documents = load_documents(source)
    rule_document = find_document(
        documents, "PrometheusRule", "atlasops-online-boutique"
    )
    replaced_selectors = 0
    for group in rule_document.get("spec", {}).get("groups", []):
        for rule in group.get("rules", []):
            expression = rule.get("expr")
            if not isinstance(expression, str):
                continue
            selector = 'namespace="default"'
            replaced_selectors += expression.count(selector)
            rule["expr"] = expression.replace(
                selector, f'namespace="{BOUTIQUE_NAMESPACE}"'
            )
    if replaced_selectors != 2:
        raise ValueError(
            "the Online Boutique availability rule must select both replica "
            "metrics in the local workload namespace"
        )

    path = output_dir / "atlasops-prometheus-rules.yaml"
    _write_documents(path, documents)
    return path


def render_platform(args: argparse.Namespace) -> int:
    validate_source_sha(args.source_sha)
    parsed_url = urlsplit(args.vllm_base)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("VLLM_BASE must be an explicit HTTP or HTTPS URL")
    if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
        raise ValueError("VLLM_BASE must not embed credentials, a query, or a fragment")
    if not args.agent_model.strip() or any(char.isspace() for char in args.agent_model):
        raise ValueError("AGENT_MODEL must be a non-empty model identifier without spaces")
    if not OPERATOR_IDENTITY_PATTERN.fullmatch(args.operator_identity):
        raise ValueError("operator identity must use letters, digits, dot, underscore, plus, or hyphen")

    replacements = {
        "__ATLASOPS_SOURCE_SHA__": args.source_sha,
        "__ATLASOPS_VLLM_BASE__": args.vllm_base.rstrip("/"),
        "__ATLASOPS_AGENT_MODEL__": args.agent_model,
        "__ATLASOPS_OPERATOR_ID__": args.operator_identity,
    }
    coordinator = _replace_placeholders(
        load_documents(INFRA_LOCAL / "coordinator-local.yaml"), replacements
    )
    application = _replace_placeholders(
        load_documents(INFRA_LOCAL / "argocd-application.yaml"), replacements
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    coordinator_path = args.output_dir / "coordinator-local.yaml"
    application_path = args.output_dir / "argocd-application.yaml"
    _write_documents(coordinator_path, coordinator)
    _write_documents(application_path, application)
    render_prometheus_rules(args.output_dir)
    print(
        "Rendered coordinator, Argo CD Application, and namespace-scoped "
        f"Prometheus rules for source {args.source_sha}."
    )
    return 0


def _application_mismatches(
    application: dict[str, Any], source_sha: str | None
) -> list[str]:
    if source_sha:
        validate_source_sha(source_sha)
    metadata = application.get("metadata", {})
    spec = application.get("spec", {})
    source = spec.get("source", {})
    destination = spec.get("destination", {})
    sync_policy = spec.get("syncPolicy", {})
    automated = sync_policy.get("automated", {})
    expected = {
        "metadata.name": (metadata.get("name"), APPLICATION_NAME),
        "metadata.namespace": (metadata.get("namespace"), APPLICATION_NAMESPACE),
        "spec.project": (spec.get("project"), APPLICATION_PROJECT),
        "spec.source.repoURL": (source.get("repoURL"), APPLICATION_REPOSITORY),
        "spec.source.path": (source.get("path"), APPLICATION_PATH),
        "spec.destination.server": (
            destination.get("server"),
            APPLICATION_DESTINATION,
        ),
        "spec.destination.namespace": (
            destination.get("namespace"),
            APPLICATION_NAME,
        ),
        "spec.syncPolicy.automated.enabled": (automated.get("enabled"), True),
        "spec.syncPolicy.automated.prune": (automated.get("prune"), False),
        "spec.syncPolicy.automated.selfHeal": (automated.get("selfHeal"), False),
        "spec.syncPolicy.automated.allowEmpty": (automated.get("allowEmpty"), False),
        "spec.syncPolicy.syncOptions": (sync_policy.get("syncOptions", []), []),
    }
    mismatches = [
        field
        for field, (actual, expected_value) in expected.items()
        if actual != expected_value
    ]
    if spec.get("sources"):
        mismatches.append("spec.sources")
    revision = source.get("targetRevision")
    if (
        source_sha is not None
        and revision != source_sha
        or source_sha is None
        and (
            not isinstance(revision, str)
            or not SOURCE_SHA_PATTERN.fullmatch(revision)
        )
    ):
        mismatches.append("spec.source.targetRevision")
    return mismatches


def check_application(args: argparse.Namespace) -> int:
    try:
        application = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise ValueError("kubectl did not return a valid Application JSON object") from exc
    if not isinstance(application, dict):
        raise TypeError("kubectl did not return an Application JSON object")
    mismatches = _application_mismatches(application, args.source_sha)
    if mismatches:
        raise ValueError(
            "refusing to adopt incompatible Argo CD Application "
            f"{APPLICATION_NAMESPACE}/{APPLICATION_NAME}; mismatched fields: "
            + ", ".join(mismatches)
        )
    print(f"Existing Application {APPLICATION_NAMESPACE}/{APPLICATION_NAME} matches its owner contract.")
    return 0


def validate_admission_policy_status(policy: Any) -> int:
    if not isinstance(policy, dict):
        raise ValueError("ValidatingAdmissionPolicy JSON is not an object")
    metadata = policy.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("ValidatingAdmissionPolicy metadata is missing or malformed")
    generation = metadata.get("generation")
    if type(generation) is not int or generation < 1:
        raise ValueError("ValidatingAdmissionPolicy metadata.generation is invalid")

    status = policy.get("status")
    if not isinstance(status, dict):
        raise ValueError("ValidatingAdmissionPolicy status is missing or malformed")
    observed_generation = status.get("observedGeneration")
    if type(observed_generation) is not int or observed_generation != generation:
        raise ValueError(
            "ValidatingAdmissionPolicy status.observedGeneration is missing or stale"
        )

    type_checking = status.get("typeChecking")
    if not isinstance(type_checking, dict):
        raise ValueError(
            "ValidatingAdmissionPolicy status.typeChecking is missing or malformed"
        )
    expression_warnings = type_checking.get("expressionWarnings", [])
    if not isinstance(expression_warnings, list):
        raise ValueError(
            "ValidatingAdmissionPolicy typeChecking.expressionWarnings is malformed"
        )
    if expression_warnings:
        raise ValueError(
            "ValidatingAdmissionPolicy type-check status contains expression warnings"
        )
    return generation


def check_admission_policy(_args: argparse.Namespace) -> int:
    try:
        policy = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "kubectl did not return a valid ValidatingAdmissionPolicy JSON object"
        ) from exc
    generation = validate_admission_policy_status(policy)
    print(
        "ValidatingAdmissionPolicy type-check status is current at "
        f"observedGeneration={generation} with no expression warnings."
    )
    return 0


def _loopback_json(
    local_port: int,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> tuple[int, dict[str, Any] | None]:
    parsed_path = urlsplit(path)
    if (
        type(local_port) is not int
        or not 1 <= local_port <= 65535
        or not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or parsed_path.scheme
        or parsed_path.netloc
        or "\r" in path
        or "\n" in path
    ):
        raise ValueError("live HTTP requests require a relative loopback path")
    connection = http.client.HTTPConnection("127.0.0.1", local_port, timeout=timeout)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise RuntimeError("loopback endpoint returned a redirect; refusing to follow it")
        body = response.read()
        try:
            payload = json.loads(body) if body else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        return response.status, payload
    except (http.client.HTTPException, OSError, TimeoutError) as exc:
        raise RuntimeError(
            f"loopback request failed ({type(exc).__name__})"
        ) from exc
    finally:
        connection.close()


def _prometheus_result(
    local_port: int, query: str, timeout: float
) -> list[dict[str, Any]]:
    query_path = "/api/v1/query?" + urlencode({"query": query})
    status, response = _loopback_json(local_port, query_path, timeout=timeout)
    if status != 200 or not response or response.get("status") != "success":
        raise RuntimeError("Prometheus did not return a successful query response")
    result = response.get("data", {}).get("result")
    if not isinstance(result, list):
        raise TypeError("Prometheus query response did not contain a result list")
    return result


def _kubectl_json(
    target: KubeTarget,
    namespace: str | None,
    resource: str,
    name: str | None = None,
    selector: str | None = None,
) -> dict[str, Any]:
    _validate_kube_target(target)
    command = [
        "kubectl",
        f"--kubeconfig={target.kubeconfig}",
        f"--context={target.kube_context}",
    ]
    if namespace:
        command.extend(["-n", namespace])
    command.extend(["get", resource])
    if name:
        command.append(name)
    if selector:
        command.extend(["-l", selector])
    command.extend(["-o", "json"])
    output = _run_local_command(
        command,
        description=f"Kubernetes {namespace}/{resource} source identity read",
        timeout=15,
    )
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Kubernetes {namespace}/{resource} read did not return valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise TypeError(
            f"Kubernetes {namespace}/{resource} read returned malformed JSON"
        )
    return value


def _resolve_service_endpoint(
    target: KubeTarget, route: ServiceRoute
) -> ServiceEndpoint:
    service = _kubectl_json(
        target, route.namespace, "service", route.service_name
    )
    service_metadata = service.get("metadata", {})
    service_spec = service.get("spec", {})
    if (
        not isinstance(service_metadata, dict)
        or not isinstance(service_spec, dict)
        or service_metadata.get("name") != route.service_name
        or service_metadata.get("namespace") != route.namespace
        or service_spec.get("type", "ClusterIP") != "ClusterIP"
    ):
        raise RuntimeError(f"{route.name} Service identity does not match its fixed route")
    selector = service_spec.get("selector")
    service_ports = service_spec.get("ports")
    if not isinstance(selector, dict) or not selector or not isinstance(service_ports, list):
        raise TypeError(f"{route.name} Service selector or ports are malformed")
    matching_service_ports = [
        item
        for item in service_ports
        if isinstance(item, dict) and item.get("port") == route.service_port
    ]
    if len(matching_service_ports) != 1:
        raise RuntimeError(f"{route.name} Service port is missing or ambiguous")
    service_port = matching_service_ports[0]
    port_name = service_port.get("name")
    if (
        service_port.get("protocol", "TCP") != "TCP"
        or service_port.get("targetPort") != route.target_port
        or not isinstance(port_name, str)
        or not port_name
    ):
        raise RuntimeError(f"{route.name} Service port does not match its fixed target")

    endpoints = _kubectl_json(
        target, route.namespace, "endpoints", route.service_name
    )
    endpoints_metadata = endpoints.get("metadata", {})
    subsets = endpoints.get("subsets")
    if (
        not isinstance(endpoints_metadata, dict)
        or endpoints_metadata.get("name") != route.service_name
        or endpoints_metadata.get("namespace") != route.namespace
        or not isinstance(subsets, list)
    ):
        raise TypeError(f"{route.name} Endpoints identity is missing or malformed")

    ready_targets: list[tuple[str, str, str]] = []
    for subset in subsets:
        if not isinstance(subset, dict):
            raise TypeError(f"{route.name} Endpoints subset is malformed")
        subset_ports = subset.get("ports", [])
        addresses = subset.get("addresses", [])
        if not isinstance(subset_ports, list) or not isinstance(addresses, list):
            raise TypeError(f"{route.name} Endpoints subset is malformed")
        matching_endpoint_ports = [
            item
            for item in subset_ports
            if isinstance(item, dict)
            and item.get("name") == port_name
            and item.get("protocol", "TCP") == "TCP"
            and item.get("port") == route.pod_port
        ]
        if len(matching_endpoint_ports) > 1:
            raise RuntimeError(f"{route.name} Endpoints port is ambiguous")
        if not matching_endpoint_ports:
            continue
        for address in addresses:
            if not isinstance(address, dict):
                raise TypeError(f"{route.name} ready Endpoint address is malformed")
            target_ref = address.get("targetRef")
            if not isinstance(target_ref, dict):
                raise RuntimeError(f"{route.name} ready Endpoint has no Pod reference")
            pod_name = target_ref.get("name")
            pod_namespace = target_ref.get("namespace")
            pod_uid = target_ref.get("uid")
            pod_ip = address.get("ip")
            if (
                target_ref.get("kind") != "Pod"
                or pod_namespace != route.namespace
                or not isinstance(pod_name, str)
                or not pod_name
                or not isinstance(pod_uid, str)
                or not pod_uid
                or not isinstance(pod_ip, str)
                or not pod_ip
            ):
                raise RuntimeError(f"{route.name} ready Endpoint is not a verifiable Pod")
            ready_targets.append((pod_name, pod_uid, pod_ip))
    if len(ready_targets) != 1:
        raise RuntimeError(f"{route.name} Service must have exactly one ready Pod endpoint")
    pod_name, endpoint_uid, endpoint_ip = ready_targets[0]

    pod = _kubectl_json(target, route.namespace, "pod", pod_name)
    pod_metadata = pod.get("metadata", {})
    pod_spec = pod.get("spec", {})
    pod_status = pod.get("status", {})
    if (
        not isinstance(pod_metadata, dict)
        or not isinstance(pod_spec, dict)
        or not isinstance(pod_status, dict)
    ):
        raise TypeError(f"{route.name} endpoint Pod is malformed")
    labels = pod_metadata.get("labels", {})
    conditions = pod_status.get("conditions")
    ready = isinstance(conditions, list) and any(
        isinstance(condition, dict)
        and condition.get("type") == "Ready"
        and condition.get("status") == "True"
        for condition in conditions
    )
    if (
        pod_metadata.get("name") != pod_name
        or pod_metadata.get("namespace") != route.namespace
        or pod_metadata.get("uid") != endpoint_uid
        or not isinstance(labels, dict)
        or any(labels.get(key) != value for key, value in selector.items())
        or pod_status.get("phase") != "Running"
        or pod_status.get("podIP") != endpoint_ip
        or not ready
    ):
        raise RuntimeError(f"{route.name} endpoint Pod is not the ready selected Pod")
    return ServiceEndpoint(
        name=route.name,
        namespace=route.namespace,
        service_name=route.service_name,
        pod_name=pod_name,
        pod_uid=endpoint_uid,
        pod_ip=endpoint_ip,
        pod_port=route.pod_port,
    )


def _drain_forward_output(
    stream: Any, output: queue.Queue[str | None]
) -> None:
    try:
        for line in stream:
            output.put(line.rstrip("\r\n"))
    except BaseException:
        pass
    finally:
        output.put(None)


def _forward_local_port(
    process: subprocess.Popen[str], *, remote_port: int, timeout: float
) -> int:
    if process.stdout is None:
        raise RuntimeError("kubectl port-forward has no readiness output")
    output: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(
        target=_drain_forward_output,
        args=(process.stdout, output),
        daemon=True,
    )
    reader.start()
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RuntimeError("kubectl port-forward exited before reporting readiness")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("kubectl port-forward did not report its loopback forward")
        try:
            line = output.get(timeout=min(remaining, 0.1))
        except queue.Empty:
            continue
        if line is None:
            raise RuntimeError("kubectl port-forward did not report its loopback forward")
        match = FORWARD_READY_PATTERN.fullmatch(line)
        if match is not None:
            local_port, observed_remote_port = (int(value) for value in match.groups())
            if observed_remote_port != remote_port:
                raise RuntimeError("kubectl port-forward reported an unexpected target port")
            if not 1 <= local_port <= 65535:
                raise RuntimeError("kubectl port-forward reported an invalid local port")
            if process.poll() is not None:
                raise RuntimeError("kubectl port-forward exited before becoming ready")
            return local_port
        if line.startswith("Forwarding from "):
            raise RuntimeError("kubectl port-forward reported a non-loopback forward")


def _stop_owned_forwards(
    processes: list[subprocess.Popen[str]],
) -> list[str]:
    failures: list[str] = []
    for process in processes:
        try:
            running = process.poll() is None
        except BaseException:
            running = True
        if running:
            try:
                process.terminate()
            except BaseException:
                pass
        try:
            process.wait(timeout=FORWARD_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except BaseException:
                failures.append("kill")
            try:
                process.wait(timeout=FORWARD_STOP_TIMEOUT_SECONDS)
            except BaseException:
                failures.append("post-kill wait")
        except BaseException:
            failures.append("wait")
        stream = process.stdout
        if stream is not None:
            try:
                stream.close()
            except BaseException:
                failures.append("stdout close")
    return failures


@contextmanager
def _owned_forwards(
    target: KubeTarget,
    endpoints: tuple[ServiceEndpoint, ...] | list[ServiceEndpoint],
    *,
    startup_timeout: float = FORWARD_START_TIMEOUT_SECONDS,
):
    _validate_kube_target(target)
    if not endpoints:
        raise ValueError("live verification requires owned service forwards")
    processes: list[subprocess.Popen[str]] = []
    ports: dict[str, int] = {}
    try:
        for endpoint in endpoints:
            command = [
                "kubectl",
                f"--kubeconfig={target.kubeconfig}",
                f"--context={target.kube_context}",
                "port-forward",
                "--address=127.0.0.1",
                "--namespace",
                endpoint.namespace,
                f"pod/{endpoint.pod_name}",
                f":{endpoint.pod_port}",
            ]
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            processes.append(process)
            local_port = _forward_local_port(
                process,
                remote_port=endpoint.pod_port,
                timeout=startup_timeout,
            )
            if local_port in ports.values() or endpoint.name in ports:
                raise RuntimeError("owned kubectl forwards reported duplicate identities")
            ports[endpoint.name] = local_port
        yield ports
    except BaseException as exc:
        failures = _stop_owned_forwards(processes)
        if failures:
            raise RuntimeError(
                "owned kubectl port-forward cleanup could not be verified"
            ) from exc
        raise
    else:
        failures = _stop_owned_forwards(processes)
        if failures:
            raise RuntimeError(
                "owned kubectl port-forward cleanup could not be verified"
            )


def _named_container(
    pod_spec: dict[str, Any], container_statuses: Any, source_sha: str, *, owner: str
) -> tuple[str, str]:
    containers = pod_spec.get("containers")
    if not isinstance(containers, list):
        raise TypeError(f"{owner} containers are missing or malformed")
    matching = [
        container
        for container in containers
        if isinstance(container, dict)
        and container.get("name") == "coordinator"
    ]
    if len(matching) != 1:
        raise RuntimeError(f"{owner} does not have exactly one coordinator container")
    image = matching[0].get("image")
    expected_image = f"atlasops:{source_sha}"
    if image != expected_image:
        raise RuntimeError(f"{owner} image does not match requested source SHA")
    if container_statuses is None:
        return expected_image, ""

    if not isinstance(container_statuses, list):
        raise TypeError(f"{owner} container status is missing or malformed")
    statuses = [
        status
        for status in container_statuses
        if isinstance(status, dict)
        and status.get("name") == "coordinator"
    ]
    if len(statuses) != 1:
        raise RuntimeError(f"{owner} has no unique coordinator container status")
    status = statuses[0]
    image_id = status.get("imageID")
    if (
        status.get("image") not in {expected_image, f"docker.io/library/{expected_image}"}
        or status.get("ready") is not True
        or not isinstance(image_id, str)
        or not IMAGE_ID_PATTERN.fullmatch(image_id)
    ):
        raise RuntimeError(
            f"{owner} running image identity does not match the requested source"
        )
    return expected_image, image_id


def _verify_runtime_source_identity(
    source_sha: str, target: KubeTarget
) -> dict[str, str]:
    _validate_kube_target(target)
    kube_system = _kubectl_json(target, None, "namespace", "kube-system")
    cluster_metadata = kube_system.get("metadata", {})
    if (
        not isinstance(cluster_metadata, dict)
        or cluster_metadata.get("name") != "kube-system"
        or not isinstance(cluster_metadata.get("uid"), str)
        or not cluster_metadata["uid"].strip()
    ):
        raise RuntimeError("Kubernetes target has no verifiable kube-system UID")
    kube_system_uid = cluster_metadata["uid"]

    deployment = _kubectl_json(
        target,
        "default",
        "deployment",
        "atlasops-coordinator",
    )
    metadata = deployment.get("metadata", {})
    spec = deployment.get("spec", {})
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise TypeError("coordinator deployment source identity is malformed")
    template = spec.get("template", {})
    selector_config = spec.get("selector", {})
    if not isinstance(template, dict) or not isinstance(selector_config, dict):
        raise TypeError("coordinator deployment source identity is malformed")
    template_metadata = template.get("metadata", {})
    template_spec = template.get("spec", {})
    status = deployment.get("status", {})
    if (
        not isinstance(template_metadata, dict)
        or not isinstance(template_spec, dict)
        or not isinstance(status, dict)
    ):
        raise TypeError("coordinator deployment source identity is malformed")
    labels = metadata.get("labels", {})
    template_labels = template_metadata.get("labels", {})
    generation = metadata.get("generation")
    observed_generation = status.get("observedGeneration")
    ready_replicas = status.get("readyReplicas")
    selector = selector_config.get("matchLabels", {})
    deployment_uid = metadata.get("uid")
    if (
        metadata.get("name") != "atlasops-coordinator"
        or metadata.get("namespace") != "default"
        or not isinstance(deployment_uid, str)
        or not deployment_uid
        or not isinstance(labels, dict)
        or labels.get("app.kubernetes.io/version") != source_sha
        or not isinstance(template_labels, dict)
        or template_labels.get("app.kubernetes.io/version") != source_sha
        or not isinstance(selector, dict)
        or selector.get("app.kubernetes.io/name") != "atlasops-coordinator"
        or spec.get("replicas") != 1
        or type(generation) is not int
        or type(observed_generation) is not int
        or observed_generation < generation
        or type(ready_replicas) is not int
        or ready_replicas != 1
    ):
        raise RuntimeError("coordinator deployment source labels or readiness mismatch")

    deployment_image, _ = _named_container(
        template_spec, None, source_sha, owner="coordinator deployment"
    )
    replicaset_list = _kubectl_json(
        target,
        "default",
        "replicasets",
        selector="app.kubernetes.io/name=atlasops-coordinator",
    )
    replicasets = replicaset_list.get("items")
    if not isinstance(replicasets, list):
        raise TypeError("coordinator ReplicaSet identity is missing or malformed")
    current_replicasets: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for replicaset in replicasets:
        if not isinstance(replicaset, dict):
            raise TypeError("coordinator ReplicaSet identity is malformed")
        rs_metadata = replicaset.get("metadata", {})
        rs_spec = replicaset.get("spec", {})
        rs_status = replicaset.get("status", {})
        if not isinstance(rs_metadata, dict) or not isinstance(rs_spec, dict) or not isinstance(
            rs_status, dict
        ):
            raise TypeError("coordinator ReplicaSet identity is malformed")
        owner_references = rs_metadata.get("ownerReferences", [])
        if not isinstance(owner_references, list):
            raise TypeError("coordinator ReplicaSet ownership is malformed")
        owned_by_deployment = any(
            isinstance(owner, dict)
            and owner.get("kind") == "Deployment"
            and owner.get("name") == "atlasops-coordinator"
            and owner.get("uid") == deployment_uid
            and owner.get("controller") is True
            for owner in owner_references
        )
        rs_labels = rs_metadata.get("labels", {})
        rs_generation = rs_metadata.get("generation")
        rs_observed_generation = rs_status.get("observedGeneration")
        template = rs_spec.get("template", {})
        template_metadata = template.get("metadata", {}) if isinstance(template, dict) else {}
        template_spec = template.get("spec", {}) if isinstance(template, dict) else {}
        template_labels = (
            template_metadata.get("labels", {})
            if isinstance(template_metadata, dict)
            else None
        )
        rs_selector = rs_spec.get("selector", {})
        rs_match_labels = (
            rs_selector.get("matchLabels", {}) if isinstance(rs_selector, dict) else None
        )
        if (
            not owned_by_deployment
            or rs_metadata.get("namespace") != "default"
            or not isinstance(rs_metadata.get("name"), str)
            or not rs_metadata["name"]
            or not isinstance(rs_metadata.get("uid"), str)
            or not rs_metadata["uid"]
            or not isinstance(rs_labels, dict)
            or rs_labels.get("app.kubernetes.io/name") != "atlasops-coordinator"
            or rs_labels.get("app.kubernetes.io/version") != source_sha
            or not isinstance(template_metadata, dict)
            or not isinstance(template_spec, dict)
            or not isinstance(template_labels, dict)
            or template_labels.get("app.kubernetes.io/version") != source_sha
            or not isinstance(rs_match_labels, dict)
            or not rs_match_labels
            or type(rs_generation) is not int
            or type(rs_observed_generation) is not int
            or rs_observed_generation < rs_generation
            or rs_spec.get("replicas") != 1
            or rs_status.get("replicas") != 1
            or rs_status.get("readyReplicas") != 1
        ):
            continue
        _named_container(
            template_spec, None, source_sha, owner="coordinator ReplicaSet"
        )
        current_replicasets.append((rs_metadata, rs_spec, rs_status))
    if len(current_replicasets) != 1:
        raise RuntimeError("coordinator has no unique source-matching ready ReplicaSet")
    replicaset_metadata, replicaset_spec, _replicaset_status = current_replicasets[0]
    replicaset_uid = replicaset_metadata["uid"]
    replicaset_name = replicaset_metadata["name"]
    replicaset_selector = replicaset_spec["selector"]["matchLabels"]

    pod_list = _kubectl_json(
        target,
        "default",
        "pods",
        selector="app.kubernetes.io/name=atlasops-coordinator",
    )
    pods = pod_list.get("items")
    if not isinstance(pods, list) or len(pods) != 1 or not isinstance(pods[0], dict):
        raise RuntimeError("coordinator Pod identity is missing or ambiguous")
    pod = pods[0]
    pod_metadata = pod.get("metadata", {})
    if not isinstance(pod_metadata, dict):
        raise TypeError("coordinator Pod identity is malformed")
    pod_labels = pod_metadata.get("labels", {})
    pod_status = pod.get("status", {})
    if (
        not isinstance(pod_labels, dict)
        or not isinstance(pod_status, dict)
    ):
        raise TypeError("coordinator Pod identity is malformed")
    conditions = pod_status.get("conditions")
    if not isinstance(conditions, list):
        raise TypeError("coordinator Pod readiness is missing")
    pod_ready = any(
        isinstance(condition, dict)
        and condition.get("type") == "Ready"
        and condition.get("status") == "True"
        for condition in conditions
    )
    if (
        pod_metadata.get("namespace") != "default"
        or not isinstance(pod_metadata.get("name"), str)
        or not pod_metadata["name"]
        or not isinstance(pod_metadata.get("uid"), str)
        or not pod_metadata["uid"]
        or any(pod_labels.get(key) != value for key, value in selector.items())
        or any(
            pod_labels.get(key) != value
            for key, value in replicaset_selector.items()
        )
        or pod_labels.get("app.kubernetes.io/version") != source_sha
        or pod_status.get("phase") != "Running"
        or not pod_ready
    ):
        raise RuntimeError("coordinator Pod source labels or readiness mismatch")
    pod_owner_references = pod_metadata.get("ownerReferences", [])
    if not isinstance(pod_owner_references, list) or not any(
        isinstance(owner, dict)
        and owner.get("kind") == "ReplicaSet"
        and owner.get("name") == replicaset_name
        and owner.get("uid") == replicaset_uid
        and owner.get("controller") is True
        for owner in pod_owner_references
    ):
        raise RuntimeError("coordinator Pod is not owned by the verified ReplicaSet")
    pod_spec = pod.get("spec", {})
    if not isinstance(pod_spec, dict):
        raise TypeError("coordinator Pod spec is malformed")
    pod_image, pod_image_id = _named_container(
        pod_spec,
        pod_status.get("containerStatuses"),
        source_sha,
        owner="coordinator Pod",
    )

    application = _kubectl_json(
        target,
        APPLICATION_NAMESPACE,
        "application",
        APPLICATION_NAME,
    )
    try:
        mismatches = _application_mismatches(application, source_sha)
    except (AttributeError, TypeError) as exc:
        raise RuntimeError("Argo Application source object is malformed") from exc
    if mismatches:
        raise RuntimeError(
            "Argo Application source mismatch: " + ", ".join(mismatches)
        )
    application_status = application.get("status", {})
    if not isinstance(application_status, dict):
        raise TypeError("Argo Application observed sync state is malformed")
    sync = application_status.get("sync", {})
    if not isinstance(sync, dict):
        raise TypeError("Argo Application observed sync state is malformed")
    if sync.get("status") != "Synced":
        raise RuntimeError("Argo Application is not Synced for the requested source SHA")
    if sync.get("revision") != source_sha:
        raise RuntimeError(
            "Argo Application observed revision does not match requested source SHA"
        )

    return {
        "kube_context": target.kube_context,
        "kube_system_uid": kube_system_uid,
        "coordinator_deployment": "default/atlasops-coordinator",
        "coordinator_replicaset": f"default/{replicaset_name}",
        "coordinator_pod": pod_metadata.get("name", ""),
        "coordinator_pod_uid": pod_metadata["uid"],
        "coordinator_image": deployment_image,
        "coordinator_pod_image": pod_image,
        "coordinator_pod_image_id": pod_image_id,
        "argocd_application": f"{APPLICATION_NAMESPACE}/{APPLICATION_NAME}",
        "argocd_observed_revision": sync["revision"],
    }


def _numeric_sample(value: Any, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"{description} is not numeric")
    try:
        result = float(value)
    except ValueError as exc:
        raise RuntimeError(f"{description} is not numeric") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{description} is not finite")
    return result


def _validate_metric_increases(
    results: Any, *, metric_name: str, now: float
) -> dict[str, float]:
    if not isinstance(results, list) or not results:
        raise RuntimeError(f"{metric_name} returned no fresh seven-service samples")
    samples: dict[str, float] = {}
    expected = set(TRACED_BOUTIQUE_SERVICES)
    for index, result in enumerate(results):
        if not isinstance(result, dict) or not isinstance(result.get("metric"), dict):
            raise TypeError(f"{metric_name} sample {index} has malformed labels")
        labels = result["metric"]
        service = labels.get("service_name")
        span_kind = labels.get("span_kind")
        value = result.get("value")
        if (
            not isinstance(service, str)
            or not isinstance(span_kind, str)
            or not isinstance(value, list)
            or len(value) != 2
        ):
            raise TypeError(f"{metric_name} sample {index} is malformed")
        if service not in expected:
            continue
        if span_kind != "SPAN_KIND_SERVER":
            raise RuntimeError(f"{metric_name} sample has an unexpected span kind")
        sample_timestamp = _numeric_sample(value[0], f"{metric_name} timestamp")
        sample_value = _numeric_sample(value[1], f"{metric_name} increase")
        age = now - sample_timestamp
        if age < -30 or age > SIGNAL_WINDOW_SECONDS:
            raise RuntimeError(f"{metric_name} sample is stale")
        if sample_value <= 0:
            raise RuntimeError(f"{metric_name} sample is empty")
        if service in samples:
            raise RuntimeError(f"{metric_name} returned duplicate service samples")
        samples[service] = sample_value

    missing = expected.difference(samples)
    if missing:
        raise RuntimeError(
            f"{metric_name} is missing fresh server spans for "
            + ", ".join(sorted(missing))
        )
    return {service: samples[service] for service in TRACED_BOUTIQUE_SERVICES}


def _validate_service_trace(response: Any, *, service: str, now: float) -> dict[str, Any]:
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise TypeError(f"Jaeger returned malformed traces for {service}")
    traces = response["data"]
    if not traces or not isinstance(traces[0], dict):
        raise RuntimeError(f"Jaeger returned no recent trace for {service}")
    trace = traces[0]
    trace_id = trace.get("traceID")
    spans = trace.get("spans")
    processes = trace.get("processes")
    if (
        not isinstance(trace_id, str)
        or not TRACE_ID_PATTERN.fullmatch(trace_id)
        or not isinstance(spans, list)
        or not spans
        or not isinstance(processes, dict)
    ):
        raise RuntimeError(f"Jaeger returned a malformed trace for {service}")

    now_microseconds = now * 1_000_000
    service_span_count = 0
    latest_service_span = 0
    for index, span in enumerate(spans):
        if not isinstance(span, dict):
            raise TypeError(f"Jaeger trace for {service} has malformed spans")
        span_id = span.get("spanID")
        operation = span.get("operationName")
        start_time = span.get("startTime")
        duration = span.get("duration")
        process_id = span.get("processID")
        process = processes.get(process_id)
        service_name = process.get("serviceName") if isinstance(process, dict) else None
        if (
            span.get("traceID") != trace_id
            or not isinstance(span_id, str)
            or not SPAN_ID_PATTERN.fullmatch(span_id)
            or not isinstance(operation, str)
            or not operation.strip()
            or type(start_time) is not int
            or start_time <= 0
            or type(duration) is not int
            or duration <= 0
            or not isinstance(service_name, str)
            or not service_name
        ):
            raise RuntimeError(f"Jaeger trace for {service} has malformed span {index}")
        age = now_microseconds - start_time
        if age < -30_000_000 or age > SIGNAL_WINDOW_SECONDS * 1_000_000:
            raise RuntimeError(f"Jaeger trace for {service} is stale")
        if service_name == service:
            service_span_count += 1
            latest_service_span = max(latest_service_span, start_time)
    if service_span_count == 0:
        raise RuntimeError(f"Jaeger trace is not attributed to service {service}")
    return {
        "trace_id": trace_id,
        "span_count": len(spans),
        "service_span_count": service_span_count,
        "latest_service_span_start_time_unix_microseconds": latest_service_span,
    }


def verify_live(args: argparse.Namespace) -> int:
    validate_source_sha(args.source_sha)
    _validate_kube_context(args.kube_context)
    if (
        not math.isfinite(args.request_timeout)
        or args.request_timeout <= 0
        or args.signal_wait_seconds < 1
        or args.poll_seconds < 0
    ):
        raise ValueError("live verification timeouts must be positive and finite")
    target = KubeTarget(args.kubeconfig, args.kube_context)
    runtime_identity = _verify_runtime_source_identity(args.source_sha, target)
    endpoints = tuple(
        _resolve_service_endpoint(target, route) for route in SERVICE_ROUTES
    )
    coordinator_endpoint = next(
        endpoint for endpoint in endpoints if endpoint.name == "coordinator"
    )
    if (
        coordinator_endpoint.pod_name != runtime_identity["coordinator_pod"]
        or coordinator_endpoint.pod_uid != runtime_identity["coordinator_pod_uid"]
    ):
        raise RuntimeError(
            "coordinator Service endpoint is not the source-verified Pod"
        )

    calls_query = (
        'sum by (service_name, span_kind) '
        '(increase(atlasops_boutique_calls_total'
        '{span_kind="SPAN_KIND_SERVER"}[5m]))'
    )
    duration_query = (
        'sum by (service_name, span_kind) '
        '(increase(atlasops_boutique_duration_milliseconds_bucket'
        '{span_kind="SPAN_KIND_SERVER",le="+Inf"}[5m]))'
    )
    calls_by_service: dict[str, float] = {}
    duration_by_service: dict[str, float] = {}
    traces_by_service: dict[str, dict[str, Any]] = {}
    last_error = "no recent seven-service telemetry has been observed"
    with _owned_forwards(target, endpoints) as ports:
        health_status, health_response = _loopback_json(
            ports["coordinator"], "/health", timeout=args.request_timeout
        )
        if (
            health_status != 200
            or not isinstance(health_response, dict)
            or health_response.get("status") != "ok"
        ):
            raise RuntimeError("GET /health did not report a healthy coordinator")

        unauthenticated_status, _ = _loopback_json(
            ports["coordinator"],
            "/api/operations",
            timeout=args.request_timeout,
        )
        if unauthenticated_status not in {401, 403}:
            raise RuntimeError(
                "unauthenticated GET /api/operations must fail closed with 401 or 403"
            )

        operator_key = args.operator_key_file.read_text(encoding="utf-8").strip()
        if not operator_key:
            raise RuntimeError("generated CI operator key file is empty")
        authenticated_status, operations_status = _loopback_json(
            ports["coordinator"],
            "/api/operations",
            headers={
                "X-AtlasOps-Key": operator_key,
                "Accept": "application/json",
            },
            timeout=args.request_timeout,
        )
        if authenticated_status != 200:
            raise RuntimeError(
                "authenticated GET /api/operations returned HTTP "
                f"{authenticated_status}"
            )
        if (
            not isinstance(operations_status, dict)
            or operations_status.get("source_sha") != args.source_sha
        ):
            raise RuntimeError(
                "authenticated operations source SHA does not match the requested source"
            )

        deadline = time.monotonic() + args.signal_wait_seconds
        while time.monotonic() < deadline:
            try:
                now = time.time()
                calls_by_service = _validate_metric_increases(
                    _prometheus_result(
                        ports["prometheus"], calls_query, args.request_timeout
                    ),
                    metric_name="span call counter",
                    now=now,
                )
                duration_by_service = _validate_metric_increases(
                    _prometheus_result(
                        ports["prometheus"], duration_query, args.request_timeout
                    ),
                    metric_name="span duration histogram",
                    now=now,
                )
                status, services_response = _loopback_json(
                    ports["jaeger"],
                    "/api/services",
                    timeout=args.request_timeout,
                )
                if status != 200 or not isinstance(services_response, dict):
                    raise RuntimeError("Jaeger service query failed")
                services = services_response.get("data")
                if not isinstance(services, list) or any(
                    not isinstance(service, str) for service in services
                ):
                    raise RuntimeError("Jaeger returned a malformed service inventory")
                missing_services = set(TRACED_BOUTIQUE_SERVICES).difference(services)
                if missing_services:
                    raise RuntimeError(
                        "Jaeger service inventory is missing "
                        + ", ".join(sorted(missing_services))
                    )
                traces_by_service = {}
                for service in TRACED_BOUTIQUE_SERVICES:
                    trace_path = "/api/traces?" + urlencode(
                        {"service": service, "limit": "1"}
                    )
                    status, trace_response = _loopback_json(
                        ports["jaeger"],
                        trace_path,
                        timeout=args.request_timeout,
                    )
                    if status != 200:
                        raise RuntimeError(f"Jaeger trace query failed for {service}")
                    traces_by_service[service] = _validate_service_trace(
                        trace_response,
                        service=service,
                        now=now,
                    )
                break
            except (RuntimeError, TypeError, ValueError) as exc:
                last_error = str(exc)
            time.sleep(args.poll_seconds)
        else:
            raise RuntimeError(
                f"timed out verifying fresh seven-service telemetry: {last_error}"
            )

        evidence = {
            "classification": "NON_EMPIRICAL_SOFTWARE_FIXTURE",
            "source_sha": args.source_sha,
            "observations": {
                "runtime_identity": runtime_identity,
                "coordinator_health": {
                    "http_status": health_status,
                    "status": health_response["status"],
                },
                "operator_route": {
                    "unauthenticated_status": unauthenticated_status,
                    "authenticated_status": authenticated_status,
                },
                "telemetry": {
                    "calls_metric": "atlasops_boutique_calls_total",
                    "duration_histogram": (
                        "atlasops_boutique_duration_milliseconds_bucket"
                    ),
                    "span_kind": "SPAN_KIND_SERVER",
                    "window_seconds": SIGNAL_WINDOW_SECONDS,
                    "sampled_services": list(TRACED_BOUTIQUE_SERVICES),
                    "sampled_trace_count": len(traces_by_service),
                    "sampled_traces": traces_by_service,
                    "calls_increase_by_service": calls_by_service,
                    "duration_bucket_increase_by_service": duration_by_service,
                    "scope": (
                        "sampled recent traces and span-derived metrics; "
                        "not an all-traffic measure"
                    ),
                },
            },
        }

    if args.evidence_file:
        args.evidence_file.parent.mkdir(parents=True, exist_ok=True)
        args.evidence_file.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print("Authenticated GET /api/operations passed; no decision was submitted.")
    print(
        "Observed fresh span-derived metrics and sampled recent traces for "
        f"{len(TRACED_BOUTIQUE_SERVICES)} instrumented Boutique services in "
        f"context {args.kube_context}."
    )
    print("Evidence classification: NON_EMPIRICAL_SOFTWARE_FIXTURE.")
    return 0


def validate_local_runtime_pins() -> None:
    try:
        kind_config = yaml.safe_load(
            (INFRA_LOCAL / "kind-config.yaml").read_text(encoding="utf-8")
        )
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("Kind cluster configuration is missing or invalid") from exc
    nodes = kind_config.get("nodes") if isinstance(kind_config, dict) else None
    if not isinstance(nodes, list) or len(nodes) != 1:
        raise ValueError("local Kind configuration must have one control-plane node")
    node = nodes[0]
    if (
        not isinstance(node, dict)
        or node.get("role") != "control-plane"
        or node.get("image") != KIND_NODE_IMAGE
    ):
        raise ValueError(f"local Kind control-plane image must be {KIND_NODE_IMAGE}")

    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    expected_dockerfile_pins = (
        f"ARG KUBECTL_VERSION={KUBECTL_VERSION}",
        f"ARG KUBECTL_SHA256={KUBECTL_SHA256}",
        f"ARG KUBECTL_SHA256_ARM64={KUBECTL_SHA256_ARM64}",
    )
    if any(pin not in dockerfile.splitlines() for pin in expected_dockerfile_pins):
        raise ValueError(
            "integrated Dockerfile kubectl version and architecture checksums "
            "must match the local Kubernetes server version"
        )


def validate_local_admission_contract() -> None:
    coordinator_documents = load_documents(INFRA_LOCAL / "coordinator-local.yaml")
    mutating_verbs = {"create", "delete", "deletecollection", "patch", "update"}
    protected_resources = {"*", "deployments", "deployments/scale"}
    for document in coordinator_documents:
        if document.get("kind") not in {"Role", "ClusterRole"}:
            continue
        for rule in document.get("rules", []):
            if protected_resources.intersection(rule.get("resources", [])) and (
                mutating_verbs.intersection(rule.get("verbs", []))
            ):
                raise ValueError(
                    "coordinator must not have direct local Deployment mutation permission"
                )

    platform_documents = load_documents(INFRA_LOCAL / "argocd-platform.yaml")
    application_role = find_document(
        platform_documents, "Role", "atlasops-coordinator-argocd-application"
    )
    expected_role_rules = [
        {
            "apiGroups": ["argoproj.io"],
            "resources": ["applications"],
            "resourceNames": ["atlasops-boutique"],
            "verbs": ["get", "patch"],
        }
    ]
    if application_role.get("rules") != expected_role_rules:
        raise ValueError("coordinator Argo RBAC must remain limited to the named Application")
    role_binding = find_document(
        platform_documents,
        "RoleBinding",
        "atlasops-coordinator-argocd-application",
    )
    expected_subjects = [
        {
            "kind": "ServiceAccount",
            "name": "atlasops-coordinator",
            "namespace": "default",
        }
    ]
    if role_binding.get("subjects") != expected_subjects:
        raise ValueError("coordinator Argo RoleBinding has an unexpected subject")

    admission_documents = load_documents(INFRA_LOCAL / "operational-admission.yaml")
    policies = [
        item
        for item in admission_documents
        if item.get("kind") == "ValidatingAdmissionPolicy"
    ]
    bindings = [
        item
        for item in admission_documents
        if item.get("kind") == "ValidatingAdmissionPolicyBinding"
    ]
    if len(policies) != 1 or len(bindings) != 1:
        raise ValueError("local coordinator admission requires exactly one policy and binding")

    policy = policies[0]
    policy_metadata = policy.get("metadata", {})
    policy_spec = policy.get("spec", {})
    expected_rules = [
        {
            "apiGroups": ["argoproj.io"],
            "apiVersions": ["v1alpha1"],
            "operations": ["UPDATE"],
            "resources": ["applications"],
            "scope": "Namespaced",
        }
    ]
    expected_match_conditions = [
        {
            "name": "atlasops-coordinator",
            "expression": (
                "request.userInfo.username == "
                f"'{COORDINATOR_SERVICE_ACCOUNT}'"
            ),
        }
    ]
    if (
        policy_metadata.get("name") != ADMISSION_POLICY_NAME
        or policy_metadata.get("labels", {}).get("atlasops.local/admission-stage")
        != "policy"
        or policy_spec.get("failurePolicy") != "Fail"
        or policy_spec.get("matchConstraints", {}).get("resourceRules")
        != expected_rules
        or policy_spec.get("matchConditions") != expected_match_conditions
    ):
        raise ValueError(
            "coordinator ValidatingAdmissionPolicy must be Fail-closed and "
            "match only the named Application update path"
        )
    validations = policy_spec.get("validations")
    required_expression_fragments = (
        "object.spec.source.targetRevision.matches('^[0-9a-f]{40}$')",
        "oldObject.status.history.exists",
        "object.metadata.labels == oldObject.metadata.labels",
        "object.metadata.annotations == oldObject.metadata.annotations",
        "object.status == oldObject.status",
        "object.operation.initiatedBy.username == 'atlasops'",
        "object.operation.sync.prune == false",
    )
    if (
        not isinstance(validations, list)
        or len(validations) != 1
        or not isinstance(validations[0].get("expression"), str)
        or any(
            fragment not in validations[0]["expression"]
            for fragment in required_expression_fragments
        )
    ):
        raise ValueError(
            "coordinator admission policy must preserve status and metadata and "
            "allow only a recorded non-pruning rollback"
        )

    binding = bindings[0]
    binding_metadata = binding.get("metadata", {})
    binding_spec = binding.get("spec", {})
    if (
        binding_metadata.get("name") != ADMISSION_POLICY_NAME
        or binding_metadata.get("labels", {}).get("atlasops.local/admission-stage")
        != "binding"
        or binding_spec.get("policyName") != ADMISSION_POLICY_NAME
        or binding_spec.get("validationActions") != ["Deny"]
    ):
        raise ValueError("coordinator policy binding must enforce Deny")

    setup = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
    policy_install = setup.index(
        'kubectl_local apply -f "$ADMISSION_MANIFEST" '
        "--selector=atlasops.local/admission-stage=policy"
    )
    read_policy_generation = setup.index(
        "policy_generation=\"$(kubectl_local get validatingadmissionpolicy "
        f"{ADMISSION_POLICY_NAME}"
    )
    validate_policy_generation = setup.index(
        '[[ "$policy_generation" =~ ^[1-9][0-9]*$ ]]'
    )
    wait_for_observed_generation = setup.index(
        "kubectl_local wait --for=jsonpath='{.status.observedGeneration}'="
        '"$policy_generation" \\\n    validatingadmissionpolicy/'
        f"{ADMISSION_POLICY_NAME}"
    )
    check_type_status = setup.index(
        "printf '%s\\n' \"$policy_status\" | "
        '"$PYTHON_BIN" "$PLATFORM_RENDERER" check-admission-policy'
    )
    report_type_status = setup.index(
        "typeChecking.expressionWarnings is empty for the current policy generation."
    )
    binding_install = setup.index(
        'kubectl_local apply -f "$ADMISSION_MANIFEST" '
        "--selector=atlasops.local/admission-stage=binding"
    )
    platform_apply = setup.index('kubectl_local apply -f "$PLATFORM_MANIFEST"')
    if not (
        policy_install
        < read_policy_generation
        < validate_policy_generation
        < wait_for_observed_generation
        < check_type_status
        < report_type_status
        < binding_install
        < platform_apply
        or "condition=Accepted" in setup
    ):
        raise ValueError(
            "setup must wait for and type-check the current admission policy generation before binding "
            "it or granting Application patch permission"
        )

    main_body = setup.split("main() {", 1)[1]
    try:
        delete_legacy_binding = setup.index(
            "kubectl_local delete rolebinding atlasops-coordinator-remediation"
        )
        delete_legacy_role = setup.index(
            "kubectl_local delete role atlasops-coordinator-remediation"
        )
        ensure_namespaces = main_body.index("ensure_namespaces")
        retire_legacy_permissions = main_body.index(
            "retire_legacy_direct_deployment_mutation_rbac"
        )
        apply_runtime_secrets = main_body.index("apply_runtime_secrets")
    except ValueError as exc:
        raise ValueError(
            "setup must retire the legacy direct Deployment mutation RoleBinding "
            "and Role"
        ) from exc
    if (
        delete_legacy_binding >= delete_legacy_role
        or ensure_namespaces >= retire_legacy_permissions
        or retire_legacy_permissions >= apply_runtime_secrets
        or '--namespace "$BOUTIQUE_NAMESPACE"' not in setup
        or "--ignore-not-found=true" not in setup
    ):
        raise ValueError(
            "setup must retire legacy Deployment mutation permissions in the "
            "Boutique namespace before continuing"
        )


def validate_static() -> int:
    required_files = [
        INFRA_LOCAL / "coordinator-local.yaml",
        INFRA_LOCAL / "argocd-platform.yaml",
        INFRA_LOCAL / "argocd-application.yaml",
        INFRA_LOCAL / "operational-admission.yaml",
        INFRA_LOCAL / "otel-collector.yaml",
        INFRA_LOCAL / "grafana-dashboard.yaml",
        INFRA_LOCAL / "values" / "prometheus-local.yaml",
        INFRA_LOCAL / "boutique" / "kustomization.yaml",
        REPO_ROOT / "infra" / "kubernetes" / "atlasops-prometheus-rules.yaml",
    ]
    for path in required_files:
        if not path.is_file():
            raise ValueError(f"missing platform manifest: {path.relative_to(REPO_ROOT)}")
    for path in required_files:
        if path.suffix in {".yaml", ".yml"} and path.name != "kustomization.yaml":
            load_documents(path)
    yaml.safe_load((INFRA_LOCAL / "boutique" / "kustomization.yaml").read_text(encoding="utf-8"))
    prometheus_values = load_documents(INFRA_LOCAL / "values" / "prometheus-local.yaml")[0]
    operator = prometheus_values["prometheusOperator"]
    if (
        operator.get("admissionWebhooks", {}).get("enabled", True) is False
        and operator.get("tls", {}).get("enabled", True) is not False
    ):
        raise ValueError(
            "disabled Prometheus admission webhooks require prometheusOperator.tls.enabled=false "
            "because the chart does not generate an admission certificate"
        )
    validate_local_runtime_pins()
    validate_local_admission_contract()
    print(
        "Local platform YAML, Kubernetes version pins, and coordinator admission "
        "policy verified. No cluster or model was contacted."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    render = commands.add_parser("render", help="render source-bound local manifests")
    render.add_argument("--source-sha", required=True)
    render.add_argument("--vllm-base", required=True)
    render.add_argument("--agent-model", required=True)
    render.add_argument("--operator-identity", required=True)
    render.add_argument("--output-dir", type=Path, required=True)
    render.set_defaults(function=render_platform)

    check = commands.add_parser(
        "check-application",
        help="check an existing Application from kubectl JSON without changing it",
    )
    check.add_argument(
        "--source-sha",
        help="require this source revision; omit to validate an existing owned revision",
    )
    check.set_defaults(function=check_application)

    admission_check = commands.add_parser(
        "check-admission-policy",
        help="check a ValidatingAdmissionPolicy type-check status from stdin",
    )
    admission_check.set_defaults(function=check_admission_policy)

    live = commands.add_parser(
        "verify-live",
        help="verify the bounded authenticated route and trace-derived CI signals",
    )
    live.add_argument("--source-sha", required=True)
    live.add_argument("--kubeconfig", type=Path, required=True)
    live.add_argument("--kube-context", required=True)
    live.add_argument("--operator-key-file", type=Path, required=True)
    live.add_argument("--request-timeout", type=float, default=5.0)
    live.add_argument("--signal-wait-seconds", type=int, default=300)
    live.add_argument("--poll-seconds", type=int, default=5)
    live.add_argument("--evidence-file", type=Path)
    live.set_defaults(function=verify_live)

    kind_check = commands.add_parser(
        "inspect-kind-cluster",
        help="inspect existing Kind ownership, immutable host mappings, and default namespace",
    )
    kind_check.add_argument("--cluster-name", required=True)
    kind_check.add_argument("--kube-context", required=True)
    kind_check.set_defaults(function=inspect_kind_cluster)

    static = commands.add_parser(
        "validate-static", help="parse local manifests without contacting a cluster"
    )
    static.set_defaults(function=lambda _args: validate_static())
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.function(args)
    except (OSError, TypeError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
