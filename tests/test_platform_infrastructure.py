from __future__ import annotations

import io
import json
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from scripts import verify_platform_infrastructure as platform_verify


@contextmanager
def _http_server(handler):
    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, headers, body = handler(self)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


ROOT = Path(__file__).resolve().parents[1]
INFRA_LOCAL = ROOT / "infra" / "local"
SOURCE_SHA = "fafe1eb7815e215ea4da15222ed9d89483a2fdba"
UPSTREAM_BOUTIQUE_SHA = "98e60f5ee0b643cc00bceb71e6efb89617740432"
UPSTREAM_RAW_REPO = "https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo"
FORK_REPO = "https://github.com/virajchoudhary/AtlasOps.git"
BOUTIQUE_DEPLOYMENT_NAMES = (
    "adservice",
    "cartservice",
    "checkoutservice",
    "currencyservice",
    "emailservice",
    "frontend",
    "loadgenerator",
    "paymentservice",
    "productcatalogservice",
    "recommendationservice",
    "redis-cart",
    "shippingservice",
)
TRACED_BOUTIQUE_SERVICES = (
    "frontend",
    "checkoutservice",
    "currencyservice",
    "productcatalogservice",
    "paymentservice",
    "emailservice",
    "recommendationservice",
)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def documents(path: Path) -> list[dict]:
    assert path.is_file(), f"missing manifest: {path.relative_to(ROOT)}"
    return [item for item in yaml.safe_load_all(path.read_text(encoding="utf-8")) if item]


def find_document(items: list[dict], kind: str, name: str) -> dict:
    for item in items:
        if item.get("kind") == kind and item.get("metadata", {}).get("name") == name:
            return item
    raise AssertionError(f"missing {kind}/{name}")


def run_renderer(output_dir: Path, source_sha: str = SOURCE_SHA) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "verify_platform_infrastructure.py"),
            "render",
            "--source-sha",
            source_sha,
            "--vllm-base",
            "http://127.0.0.1:1/v1",
            "--agent-model",
            "fixture/no-model",
            "--operator-identity",
            "infra-test-operator",
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("registry_prefix", ["", "docker.io/library/"])
def test_live_image_verifier_accepts_the_actual_rendered_coordinator_container(
    tmp_path, registry_prefix,
) -> None:
    rendered = tmp_path / "rendered"
    result = run_renderer(rendered)
    assert result.returncode == 0, result.stderr
    deployment = find_document(
        documents(rendered / "coordinator-local.yaml"),
        "Deployment", "atlasops-coordinator",
    )
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    image_id = f"containerd://sha256:{'a' * 64}"
    statuses = [{
        "name": container["name"],
        "image": registry_prefix + container["image"],
        "imageID": image_id,
        "ready": True,
    }]
    image, observed_id = platform_verify._named_container(
        pod_spec, statuses, SOURCE_SHA, owner="rendered coordinator Pod",
    )
    assert image == f"atlasops:{SOURCE_SHA}"
    assert observed_id == image_id


def test_integrated_image_uses_verified_kubectl_1358_checksums() -> None:
    expected = {
        "amd64": "874d5e72dbb819f43cff16bcd1e4f8bac5b7f2361fe1e55049b0a6c676fb0cbf",
        "arm64": "cc749967b62f4422260bc9c0aa7a7c55f45175ae38cb8d95767b5d2b7e04c1fd",
    }
    content = read("Dockerfile")
    assert "ARG TARGETARCH" in content
    assert "KUBECTL_VERSION=v1.35.8" in content
    assert f"ARG KUBECTL_SHA256={expected['amd64']}" in content
    assert f"ARG KUBECTL_SHA256_ARM64={expected['arm64']}" in content
    assert "linux/${TARGETARCH}/kubectl" in content
    assert "sha256sum -c -" in content


def test_root_image_is_non_root_and_labels_the_source_revision() -> None:
    dockerfile = read("Dockerfile")
    assert "USER 10001:10001" in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile
    assert "ARG ATLASOPS_SOURCE_SHA" in dockerfile
    assert "ATLASOPS_SOURCE_SHA=${ATLASOPS_SOURCE_SHA}" in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
    assert '--workers", "1"' in dockerfile


def test_kind_api_and_host_mapping_bind_to_loopback() -> None:
    config = yaml.safe_load((INFRA_LOCAL / "kind-config.yaml").read_text(encoding="utf-8"))
    networking = config["networking"]
    assert networking["apiServerAddress"] == "127.0.0.1"
    assert config["nodes"][0]["image"] == (
        "kindest/node:v1.35.8@sha256:"
        "07b2536e30b803ed61d1677a79df6115f798ce64c80f9e22f6ed45afd09323c0"
    )
    mappings = config["nodes"][0]["extraPortMappings"]
    assert mappings
    assert all(item["listenAddress"] == "127.0.0.1" for item in mappings)
    assert {item["containerPort"] for item in mappings} == {30080}
    setup = read("infra/local/setup_local.sh")
    port_forward_commands = [
        line
        for line in setup.splitlines()
        if "kubectl --context=$KUBE_CONTEXT port-forward " in line
    ]
    assert port_forward_commands
    assert all("--address=127.0.0.1" in line for line in port_forward_commands)


def test_integrated_coordinator_uses_current_sha_image_and_persistent_single_writer_volume() -> None:
    items = documents(INFRA_LOCAL / "coordinator-local.yaml")
    deployment = find_document(items, "Deployment", "atlasops-coordinator")
    service = find_document(items, "Service", "atlasops-coordinator-svc")
    pvc = find_document(items, "PersistentVolumeClaim", "atlasops-runtime-data")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "atlasops:__ATLASOPS_SOURCE_SHA__"
    assert container["imagePullPolicy"] == "Never"
    assert container["ports"] == [{"name": "http", "containerPort": 7860, "protocol": "TCP"}]
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert deployment["spec"]["template"]["spec"]["securityContext"]["runAsUser"] == 10001
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    runtime_mount = next(
        item for item in container["volumeMounts"] if item["mountPath"] == "/var/lib/atlasops"
    )
    assert runtime_mount["name"] == "runtime-data"
    runtime_volume = next(
        item
        for item in deployment["spec"]["template"]["spec"]["volumes"]
        if item["name"] == "runtime-data"
    )
    assert runtime_volume["persistentVolumeClaim"]["claimName"] == "atlasops-runtime-data"
    assert pvc["spec"]["storageClassName"] == "local-path"
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"][0]["port"] == 9099
    assert service["spec"]["ports"][0]["targetPort"] == "http"
    assert {
        probe["httpGet"]["path"]
        for probe in (
            container["startupProbe"],
            container["readinessProbe"],
            container["livenessProbe"],
        )
    } == {"/health"}


def test_setup_always_builds_integrated_source_image_and_does_not_apply_boutique_workloads() -> None:
    content = read("infra/local/setup_local.sh")
    dockerfile = read("Dockerfile")
    assert "docker build \\" in content
    assert '--file "${REPO_ROOT}/Dockerfile"' in content
    assert "org.opencontainers.image.revision" in dockerfile
    assert "kind load docker-image" in content
    assert "docker image inspect" not in content
    assert "g3-local" not in content
    assert "kubectl_local apply -f \"$BOUTIQUE_MANIFEST\"" not in content
    assert 'check-application --source-sha "$SOURCE_SHA"' in content
    assert "ATLASOPS_VLLM_BASE" in content
    assert "ATLASOPS_AGENT_MODEL" in content
    assert "ATLASOPS_OPERATOR_ID" in content
    assert "ATLASOPS_OPERATOR_IDENTITY" not in content
    ensure_cluster = content.split("ensure_kind_cluster() {", 1)[1].split(
        "# Namespace management", 1
    )[0]
    assert "inspect-kind-cluster" in ensure_cluster
    main_body = content.split("main() {", 1)[1]
    assert main_body.index("ensure_kind_cluster") < main_body.index("ensure_namespaces")
    assert main_body.index("ensure_kind_cluster") < main_body.index("apply_runtime_secrets")


def test_setup_waits_for_current_policy_status_before_binding_and_permissions() -> None:
    content = read("infra/local/setup_local.sh")
    install_policy = content.index(
        'kubectl_local apply -f "$ADMISSION_MANIFEST" '
        '--selector=atlasops.local/admission-stage=policy'
    )
    read_generation = content.index(
        "policy_generation=\"$(kubectl_local get validatingadmissionpolicy "
        "atlasops-coordinator-rollback-only"
    )
    wait_observed_generation = content.index(
        "kubectl_local wait --for=jsonpath='{.status.observedGeneration}'="
        '"$policy_generation" \\\n    validatingadmissionpolicy/'
        "atlasops-coordinator-rollback-only"
    )
    verify_type_check = content.index(
        '"$PYTHON_BIN" "$PLATFORM_RENDERER" check-admission-policy'
    )
    report_current_warnings = content.index(
        "typeChecking.expressionWarnings is empty for the current policy generation."
    )
    install_policy_binding = content.index(
        'kubectl_local apply -f "$ADMISSION_MANIFEST" '
        '--selector=atlasops.local/admission-stage=binding'
    )
    install_application_permissions = content.index(
        'kubectl_local apply -f "$PLATFORM_MANIFEST"'
    )
    assert (
        install_policy
        < read_generation
        < wait_observed_generation
        < verify_type_check
        < report_current_warnings
        < install_policy_binding
        < install_application_permissions
    )
    assert "condition=Accepted" not in content


def test_setup_retires_legacy_direct_deployment_mutation_permissions() -> None:
    content = read("infra/local/setup_local.sh")
    function_name = "retire_legacy_direct_deployment_mutation_rbac"
    function_body = content.split(f"{function_name}() {{", 1)[1].split("}", 1)[0]
    delete_binding = (
        "kubectl_local delete rolebinding atlasops-coordinator-remediation"
    )
    delete_role = "kubectl_local delete role atlasops-coordinator-remediation"
    assert delete_binding in function_body
    assert delete_role in function_body
    assert function_body.index(delete_binding) < function_body.index(delete_role)
    assert '--namespace "$BOUTIQUE_NAMESPACE"' in function_body
    assert "--ignore-not-found=true" in function_body

    main_body = content.split("main() {", 1)[1]
    assert (
        main_body.index("ensure_kind_cluster")
        < main_body.index("ensure_namespaces")
        < main_body.index(function_name)
        < main_body.index("apply_runtime_secrets")
    )


def test_runtime_config_keeps_model_and_operator_settings_explicit() -> None:
    items = documents(INFRA_LOCAL / "coordinator-local.yaml")
    config = find_document(items, "ConfigMap", "atlasops-coordinator-config")["data"]
    assert config["ATLASOPS_OPERATIONAL_MODE"] == "1"
    assert config["ATLASOPS_OPERATOR_ID"] == "__ATLASOPS_OPERATOR_ID__"
    assert config["ATLASOPS_WORKLOAD_NAMESPACE"] == "atlasops-boutique"
    assert config["VLLM_BASE"] == "__ATLASOPS_VLLM_BASE__"
    assert config["AGENT_MODEL"] == "__ATLASOPS_AGENT_MODEL__"
    assert config["ATLASOPS_AUTO_HF_INFERENCE"] == "0"
    assert config["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        "http://atlasops-otel-collector.monitoring.svc.cluster.local:4318"
    )
    assert config["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert config["OTEL_SERVICE_NAME"] == "atlasops-coordinator"
    assert config["ATLASOPS_ARGOCD_APP"] == "atlasops-boutique"
    assert config["ATLASOPS_ARGOCD_NAMESPACE"] == "argocd"
    assert config["ATLASOPS_RUNTIME_DATA_DIR"].startswith("/var/lib/atlasops/")
    assert config["POSTMORTEM_DIR"].startswith("/var/lib/atlasops/")
    assert config["TRAJECTORIES_DIR"].startswith("/var/lib/atlasops/")


def test_boutique_overlay_uses_exact_upstream_commit_and_source_supported_tracing() -> None:
    kustomization_path = INFRA_LOCAL / "boutique" / "kustomization.yaml"
    overlay = yaml.safe_load(kustomization_path.read_text(encoding="utf-8"))
    source_url = next(item for item in overlay["resources"] if "microservices-demo" in item)
    assert source_url == (
        f"{UPSTREAM_RAW_REPO}/{UPSTREAM_BOUTIQUE_SHA}/release/kubernetes-manifests.yaml"
    )
    patches = overlay["patches"]
    traced_services = {
        patch["target"]["name"]
        for patch in patches
        if "ENABLE_TRACING" in patch["patch"]
    }
    assert traced_services == {
        "frontend",
        "checkoutservice",
        "currencyservice",
        "productcatalogservice",
        "paymentservice",
        "emailservice",
        "recommendationservice",
    }
    assert all(
        "ENABLE_TRACING" not in next(
            patch["patch"] for patch in patches if patch["target"]["name"] == service
        )
        for service in ("shippingservice", "adservice")
    )
    assert "frontend-external" in " ".join(
        patch["patch"] for patch in patches
    )
    deployment_patches = [
        patch for patch in patches if patch["target"]["kind"] == "Deployment"
    ]
    probe_services = {
        patch["target"]["name"]
        for patch in deployment_patches
        if "startupProbe:" in patch["patch"]
    }
    assert probe_services == {
        "adservice",
        "cartservice",
        "checkoutservice",
        "currencyservice",
        "emailservice",
        "frontend",
        "paymentservice",
        "productcatalogservice",
        "recommendationservice",
        "redis-cart",
        "shippingservice",
    }
    assert all(
        "timeoutSeconds: 5" in patch["patch"]
        for patch in deployment_patches
        if patch["target"]["name"] in probe_services
    )
    assert any("USERS" in patch["patch"] and '"1"' in patch["patch"] for patch in patches)
    assert all(
        patch["patch"].count("replicas: 1") == 1 for patch in deployment_patches
    )


def test_argocd_project_application_and_coordinator_role_are_exactly_scoped() -> None:
    platform_items = documents(INFRA_LOCAL / "argocd-platform.yaml")
    app_items = documents(INFRA_LOCAL / "argocd-application.yaml")
    project = find_document(platform_items, "AppProject", "atlasops-boutique")
    role = find_document(
        platform_items, "Role", "atlasops-coordinator-argocd-application"
    )
    application = find_document(app_items, "Application", "atlasops-boutique")
    spec = application["spec"]
    assert application["metadata"]["namespace"] == "argocd"
    assert spec["project"] == "atlasops-boutique"
    assert spec["source"] == {
        "repoURL": FORK_REPO,
        "targetRevision": "__ATLASOPS_SOURCE_SHA__",
        "path": "infra/local/boutique",
    }
    assert spec["destination"] == {
        "server": "https://kubernetes.default.svc",
        "namespace": "atlasops-boutique",
    }
    assert spec["syncPolicy"]["automated"]["prune"] is False
    assert spec["syncPolicy"]["automated"]["selfHeal"] is False
    project_spec = project["spec"]
    assert project_spec["sourceRepos"] == [FORK_REPO]
    assert project_spec["destinations"] == [
        {"server": "https://kubernetes.default.svc", "namespace": "atlasops-boutique"}
    ]
    assert project_spec["clusterResourceWhitelist"] == []
    assert {
        (item["group"], item["kind"])
        for item in project_spec["namespaceResourceWhitelist"]
    } == {("apps", "Deployment"), ("", "Service"), ("", "ServiceAccount")}
    assert role["metadata"]["namespace"] == "argocd"
    assert role["rules"] == [
        {
            "apiGroups": ["argoproj.io"],
            "resources": ["applications"],
            "resourceNames": ["atlasops-boutique"],
            "verbs": ["get", "patch"],
        }
    ]
    binding = find_document(
        platform_items, "RoleBinding", "atlasops-coordinator-argocd-application"
    )
    assert binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "atlasops-coordinator",
            "namespace": "default",
        }
    ]


def test_operational_admission_is_fail_closed_and_limits_the_coordinator_to_rollback() -> None:
    items = documents(INFRA_LOCAL / "operational-admission.yaml")
    policy = find_document(
        items,
        "ValidatingAdmissionPolicy",
        "atlasops-coordinator-rollback-only",
    )
    binding = find_document(
        items,
        "ValidatingAdmissionPolicyBinding",
        "atlasops-coordinator-rollback-only",
    )

    assert policy["spec"]["failurePolicy"] == "Fail"
    assert policy["spec"]["matchConstraints"]["resourceRules"] == [
        {
            "apiGroups": ["argoproj.io"],
            "apiVersions": ["v1alpha1"],
            "operations": ["UPDATE"],
            "resources": ["applications"],
            "scope": "Namespaced",
        }
    ]
    assert policy["spec"]["matchConditions"] == [
        {
            "name": "atlasops-coordinator",
            "expression": (
                "request.userInfo.username == "
                "'system:serviceaccount:default:atlasops-coordinator'"
            ),
        }
    ]
    expression = policy["spec"]["validations"][0]["expression"]
    for required_fragment in (
        "object.spec.source.targetRevision",
        "oldObject.status.history",
        "oldObject.operation",
        "object.operation.initiatedBy.username",
        "object.operation.sync.prune",
        "object.metadata",
        "object.status",
    ):
        assert required_fragment in expression
    assert binding["spec"]["policyName"] == policy["metadata"]["name"]
    assert binding["spec"]["validationActions"] == ["Deny"]


def test_coordinator_workload_inspection_remains_read_only_and_chaos_cleanup_scoped() -> None:
    items = documents(INFRA_LOCAL / "coordinator-local.yaml")
    readonly_roles = [
        item
        for item in items
        if item["kind"] == "Role"
        and item["metadata"]["name"].startswith("atlasops-coordinator-readonly-")
    ]
    assert {role["metadata"]["namespace"] for role in readonly_roles} == {
        "default",
        "atlasops-boutique",
    }
    for role in readonly_roles:
        rules = role["rules"]
        assert all("nodes" not in rule["resources"] for rule in rules)
        assert all("secrets" not in rule["resources"] for rule in rules)
        assert all(
            rule["apiGroups"] != ["metrics.k8s.io"]
            or rule["resources"] == ["pods"]
            for rule in rules
        )
        binding = find_document(
            items, "RoleBinding", role["metadata"]["name"]
        )
        assert binding["metadata"]["namespace"] == role["metadata"]["namespace"]
        assert binding["subjects"] == [
            {
                "kind": "ServiceAccount",
                "name": "atlasops-coordinator",
                "namespace": "default",
            }
        ]
    assert not any(
        item["kind"] == "ClusterRole"
        and item["metadata"]["name"] == "atlasops-coordinator-readonly"
        for item in items
    )
    assert not any(
        item["kind"] in {"Role", "RoleBinding"}
        and item["metadata"]["name"] == "atlasops-coordinator-remediation"
        for item in items
    )
    write_verbs = {"create", "delete", "deletecollection", "patch", "update"}
    for item in items:
        if item["kind"] not in {"Role", "ClusterRole"}:
            continue
        for rule in item.get("rules", []):
            resources = set(rule.get("resources", []))
            if resources & {"deployments", "deployments/scale", "replicasets"}:
                assert not write_verbs.intersection(rule.get("verbs", []))
    for role in readonly_roles:
        workload_rules = [
            rule
            for rule in role["rules"]
            if set(rule.get("resources", [])) & {"deployments", "replicasets"}
        ]
        assert workload_rules
        assert all(
            {"get", "list", "watch"} <= set(rule["verbs"])
            for rule in workload_rules
        )

    chaos_role = find_document(items, "Role", "atlasops-coordinator-chaos")
    assert chaos_role["metadata"]["namespace"] == "chaos-mesh"
    assert chaos_role["rules"] == [
        {
            "apiGroups": ["chaos-mesh.org"],
            "resources": [
                "podchaos",
                "stresschaos",
                "networkchaos",
                "dnschaos",
                "iochaos",
                "timechaos",
            ],
            "verbs": ["get", "list", "delete"],
        }
    ]
    chaos_binding = find_document(items, "RoleBinding", "atlasops-coordinator-chaos")
    assert chaos_binding["metadata"]["namespace"] == "chaos-mesh"
    assert chaos_binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "atlasops-coordinator",
            "namespace": "default",
        }
    ]

def test_static_validation_checks_kubernetes_pins_and_admission_policy() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "verify_platform_infrastructure.py"),
            "validate-static",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Kubernetes version pins, and coordinator admission policy verified" in (
        result.stdout
    )
    assert "No cluster or model was contacted." in result.stdout


def test_alertmanager_delivery_uses_the_coordinator_webhook_secret() -> None:
    coordinator_items = documents(INFRA_LOCAL / "coordinator-local.yaml")
    container = find_document(
        coordinator_items, "Deployment", "atlasops-coordinator"
    )["spec"]["template"]["spec"]["containers"][0]
    secret_refs = {
        item["name"]: item["valueFrom"]["secretKeyRef"]
        for item in container["env"]
        if "valueFrom" in item
    }
    assert secret_refs["ATLASOPS_API_KEY"] == {
        "name": "atlasops-coordinator-secrets",
        "key": "atlasops-api-key",
    }
    assert secret_refs["ALERTMANAGER_WEBHOOK_SECRET"] == {
        "name": "atlasops-coordinator-secrets",
        "key": "alertmanager-webhook-secret",
    }

    values = yaml.safe_load(
        (INFRA_LOCAL / "values" / "prometheus-local.yaml").read_text(
            encoding="utf-8"
        )
    )
    alertmanager = values["alertmanager"]["config"]
    route = alertmanager["route"]["routes"][0]
    assert alertmanager["route"]["group_by"] == [
        "alertname",
        "namespace",
        "deployment",
    ]
    assert route["receiver"] == "atlasops-coordinator"
    assert route["matchers"] == ['atlasops_route="coordinator"']
    webhook = next(
        item
        for item in alertmanager["receivers"]
        if item["name"] == "atlasops-coordinator"
    )["webhook_configs"][0]
    assert webhook["url"].endswith("/webhook")
    assert webhook["send_resolved"] is True
    assert webhook["http_config"]["authorization"] == {
        "type": "Bearer",
        "credentials_file": (
            "/etc/alertmanager/secrets/atlasops-alertmanager-webhook/"
            "alertmanager-webhook-secret"
        ),
    }


def test_collector_exports_declared_span_metrics_and_has_a_private_scrape_service() -> None:
    items = documents(INFRA_LOCAL / "otel-collector.yaml")
    config_map = find_document(items, "ConfigMap", "atlasops-otel-collector-config")
    deployment = find_document(items, "Deployment", "atlasops-otel-collector")
    service = find_document(items, "Service", "atlasops-otel-collector")
    monitor = find_document(items, "ServiceMonitor", "atlasops-otel-collector")
    policy = find_document(
        items, "NetworkPolicy", "atlasops-otel-collector-ingress"
    )
    collector = yaml.safe_load(config_map["data"]["config.yaml"])
    receiver_protocols = collector["receivers"]["otlp"]["protocols"]
    assert receiver_protocols["grpc"]["endpoint"] == "0.0.0.0:4317"
    assert receiver_protocols["http"]["endpoint"] == "0.0.0.0:4318"
    spanmetrics = collector["connectors"]["spanmetrics"]
    assert spanmetrics["namespace"] == "atlasops_boutique"
    assert spanmetrics["histogram"]["unit"] == "ms"
    assert spanmetrics["exclude_dimensions"] == ["span.name"]
    assert collector["exporters"]["otlp/jaeger"]["endpoint"] == (
        "jaeger.jaeger.svc.cluster.local:4317"
    )
    assert collector["service"]["pipelines"]["traces"]["exporters"] == [
        "otlp/jaeger",
        "spanmetrics",
    ]
    assert collector["service"]["pipelines"]["metrics/spanmetrics"]["receivers"] == [
        "spanmetrics"
    ]
    assert collector["service"]["pipelines"]["metrics/coordinator"] == {
        "receivers": ["otlp"],
        "processors": ["memory_limiter", "batch"],
        "exporters": ["prometheus"],
    }
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "otel/opentelemetry-collector-contrib:0.98.0"
    assert service["spec"]["type"] == "ClusterIP"
    assert {port["port"] for port in service["spec"]["ports"]} == {4317, 4318, 8889}
    assert {port["containerPort"] for port in container["ports"]} == {
        4317,
        4318,
        8889,
    }
    assert monitor["spec"]["endpoints"][0]["port"] == "metrics"
    assert monitor["spec"]["selector"]["matchLabels"] == service["metadata"]["labels"]
    assert "calls_total" not in config_map["data"]["config.yaml"]
    policy_spec = policy["spec"]
    assert policy["metadata"]["namespace"] == "monitoring"
    assert policy_spec["podSelector"]["matchLabels"] == service["spec"]["selector"]
    assert policy_spec["policyTypes"] == ["Ingress"]
    assert [
        (
            rule["from"][0].get("namespaceSelector", {}).get("matchLabels", {}).get(
                "kubernetes.io/metadata.name"
            ),
            rule["from"][0].get("podSelector", {}).get("matchLabels"),
            rule["ports"][0]["port"],
        )
        for rule in policy_spec["ingress"]
    ] == [
        ("default", {"app.kubernetes.io/name": "atlasops-coordinator"}, 4318),
        ("atlasops-boutique", None, 4317),
        ("monitoring", None, 8889),
    ]


def test_grafana_uses_new_secret_and_trace_derived_dashboard_queries() -> None:
    values = yaml.safe_load((INFRA_LOCAL / "values" / "prometheus-local.yaml").read_text())
    assert values["grafana"]["enabled"] is True
    assert values["grafana"]["admin"]["existingSecret"] == "atlasops-grafana-admin"
    assert values["grafana"]["admin"]["userKey"] == "admin-user"
    assert values["grafana"]["admin"]["passwordKey"] == "admin-password"
    assert values["grafana"]["service"]["type"] == "ClusterIP"
    dashboard = find_document(
        documents(INFRA_LOCAL / "grafana-dashboard.yaml"), "ConfigMap", "atlasops-boutique-dashboard"
    )
    model = json.loads(dashboard["data"]["atlasops-boutique.json"])
    expressions = [
        target["expr"]
        for panel in model["panels"]
        for target in panel.get("targets", [])
    ]
    joined = "\n".join(expressions)
    assert "atlasops_boutique_calls_total" in joined
    assert "atlasops_boutique_duration_milliseconds_bucket" in joined
    assert "service_name" in joined
    assert 'span_kind="SPAN_KIND_SERVER"' in joined
    assert "STATUS_CODE_ERROR" in joined
    assert "kube_" not in joined


def test_jaeger_chart_values_match_the_pinned_v2_chart_and_keep_service_private() -> None:
    values = yaml.safe_load((INFRA_LOCAL / "values" / "jaeger-local.yaml").read_text())
    assert values["jaeger"]["service"]["type"] == "ClusterIP"
    assert values["jaeger"]["replicas"] == 1
    assert values["userconfig"]["extensions"]["jaeger_storage"]["backends"]["primary"][
        "memory"
    ]["max_traces"] == 5000
    assert values["userconfig"]["extensions"]["jaeger_query"]["storage"] == {
        "traces": "primary",
        "traces_archive": "primary",
    }
    assert values["userconfig"]["exporters"]["jaeger_storage_exporter"][
        "trace_storage"
    ] == "primary"
    assert "allInOne" not in values
    setup = read("infra/local/setup_local.sh")
    assert "svc/jaeger 16686:16686" in setup
    assert "svc/jaeger-query" not in setup


def test_grafana_secret_generation_preserves_existing_new_secret_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "secrets"
    command = [
        sys.executable,
        "-B",
        str(ROOT / "scripts" / "generate_runtime_secrets.py"),
        "--output-dir",
        str(output_dir),
    ]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    user_file = output_dir / "grafana-admin-user.secret"
    password_file = output_dir / "grafana-admin-password.secret"
    assert user_file.read_text(encoding="utf-8").strip() == "admin"
    first_password = password_file.read_text(encoding="utf-8").strip()
    assert len(first_password) >= 24
    assert first_password not in first.stdout
    second = subprocess.run(command, check=True, capture_output=True, text=True)
    assert user_file.read_text(encoding="utf-8").strip() == "admin"
    assert password_file.read_text(encoding="utf-8").strip() == first_password
    assert first_password not in second.stdout


def test_renderer_is_deterministic_and_rejects_an_invalid_source_revision(tmp_path: Path) -> None:
    first = run_renderer(tmp_path / "first")
    assert first.returncode == 0, first.stderr
    second = run_renderer(tmp_path / "second")
    assert second.returncode == 0, second.stderr
    for name in ("coordinator-local.yaml", "argocd-application.yaml"):
        left = (tmp_path / "first" / name).read_bytes()
        right = (tmp_path / "second" / name).read_bytes()
        assert left == right
        assert SOURCE_SHA.encode() in left
    rendered = documents(tmp_path / "first" / "coordinator-local.yaml")
    config = find_document(rendered, "ConfigMap", "atlasops-coordinator-config")["data"]
    assert config["VLLM_BASE"] == "http://127.0.0.1:1/v1"
    assert config["AGENT_MODEL"] == "fixture/no-model"
    assert config["ATLASOPS_OPERATOR_ID"] == "infra-test-operator"
    assert config["ATLASOPS_ARGOCD_APP"] == "atlasops-boutique"
    rendered_rules = documents(tmp_path / "first" / "atlasops-prometheus-rules.yaml")
    rule = find_document(
        rendered_rules, "PrometheusRule", "atlasops-online-boutique"
    )["spec"]["groups"][0]["rules"][0]
    assert rule["expr"].count('namespace="atlasops-boutique"') == 2
    assert 'namespace="default"' not in rule["expr"]
    rejected = run_renderer(tmp_path / "invalid", "not-a-commit")
    assert rejected.returncode != 0
    assert "40-character commit SHA" in rejected.stderr


def test_existing_argocd_application_must_match_the_scoped_owner_contract() -> None:
    app = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": "atlasops-boutique", "namespace": "argocd"},
        "spec": {
            "project": "atlasops-boutique",
            "source": {
                "repoURL": FORK_REPO,
                "targetRevision": SOURCE_SHA,
                "path": "infra/local/boutique",
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": "atlasops-boutique",
            },
            "syncPolicy": {
                "automated": {
                    "enabled": True,
                    "prune": False,
                    "selfHeal": False,
                    "allowEmpty": False,
                },
                "syncOptions": [],
            },
        },
    }
    good = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "verify_platform_infrastructure.py"),
            "check-application",
            "--source-sha",
            SOURCE_SHA,
        ],
        input=json.dumps(app),
        check=False,
        capture_output=True,
        text=True,
    )
    assert good.returncode == 0, good.stderr
    multi_source = json.loads(json.dumps(app))
    multi_source["spec"]["sources"] = [
        {
            "repoURL": FORK_REPO,
            "targetRevision": "main",
            "path": "infra/local/boutique",
        }
    ]
    multi_source_result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "verify_platform_infrastructure.py"),
            "check-application",
            "--source-sha",
            SOURCE_SHA,
        ],
        input=json.dumps(multi_source),
        check=False,
        capture_output=True,
        text=True,
    )
    assert multi_source_result.returncode != 0
    assert "spec.sources" in multi_source_result.stderr

    app["spec"]["source"]["path"] = "infra/local"
    bad = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "verify_platform_infrastructure.py"),
            "check-application",
            "--source-sha",
            SOURCE_SHA,
        ],
        input=json.dumps(app),
        check=False,
        capture_output=True,
        text=True,
    )
    assert bad.returncode != 0
    assert "refusing to adopt" in bad.stderr


def _admission_policy_status(
    *,
    generation: int = 8,
    observed_generation: int | None = None,
    warnings=(),
) -> dict:
    if observed_generation is None:
        observed_generation = generation
    return {
        "metadata": {"generation": generation},
        "status": {
            "observedGeneration": observed_generation,
            "typeChecking": {"expressionWarnings": list(warnings)},
        },
    }


def test_admission_policy_status_accepts_current_generation_without_accepted_condition() -> None:
    policy = _admission_policy_status()

    assert platform_verify.validate_admission_policy_status(policy) == 8
    assert "conditions" not in policy["status"]

    del policy["status"]["typeChecking"]["expressionWarnings"]
    assert platform_verify.validate_admission_policy_status(policy) == 8


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ({"metadata": {"generation": 8}}, "status"),
        (
            {
                "metadata": {"generation": 8},
                "status": {"typeChecking": {"expressionWarnings": []}},
            },
            "observedGeneration",
        ),
        (
            _admission_policy_status(generation=8, observed_generation=7),
            "observedGeneration",
        ),
        (
            {"metadata": {"generation": 8}, "status": {"observedGeneration": 8}},
            "typeChecking",
        ),
        (
            {
                "metadata": {"generation": 8},
                "status": {
                    "observedGeneration": 8,
                    "typeChecking": {"expressionWarnings": None},
                },
            },
            "expressionWarnings",
        ),
        (
            _admission_policy_status(
                warnings=({"field": "spec.validations[0].expression"},)
            ),
            "expression warnings",
        ),
    ],
)
def test_admission_policy_status_rejects_missing_stale_or_warned_status(
    policy: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        platform_verify.validate_admission_policy_status(policy)


def test_check_admission_policy_cli_reads_json_from_stdin() -> None:
    command = [
        sys.executable,
        "-B",
        str(ROOT / "scripts" / "verify_platform_infrastructure.py"),
        "check-admission-policy",
    ]
    current = subprocess.run(
        command,
        input=json.dumps(_admission_policy_status(generation=12)),
        check=False,
        capture_output=True,
        text=True,
    )
    assert current.returncode == 0, current.stderr
    assert "observedGeneration=12" in current.stdout

    stale = _admission_policy_status(generation=12, observed_generation=11)
    rejected = subprocess.run(
        command,
        input=json.dumps(stale),
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "observedGeneration" in rejected.stderr


def test_platform_integration_workflow_is_disposable_private_and_non_empirical() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "platform-integration.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    job = workflow["jobs"]["platform-integration"]
    steps = job["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    runner = (ROOT / "scripts" / "platform_ci.py").read_text(encoding="utf-8")
    live_verifier = (ROOT / "scripts" / "verify_platform_infrastructure.py").read_text(
        encoding="utf-8"
    )
    assert job["timeout-minutes"] >= 45
    assert workflow["permissions"] == {"contents": "read"}
    assert "python -m scripts.platform_ci run" in commands
    assert '--evidence-dir "$RUNNER_TEMP/platform-ci-' in commands
    assert "NON_EMPIRICAL_SOFTWARE_FIXTURE" in commands
    assert 'status="verified_deleted"' in runner
    assert "/api/operations" in runner
    assert "scripts/verify_platform_infrastructure.py" in runner
    assert '"verify-live"' in runner
    assert "atlasops_boutique_calls_total" in live_verifier
    assert "/api/traces" in runner
    assert "HF_TOKEN" not in workflow.get("env", {})
    assert steps[-1]["if"] == "always()"


def _kind_container(cluster_name: str = "atlasops-local") -> dict:
    ports = {
        "6443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "52345"}],
        "30080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}],
    }
    return {
        "Name": f"/{cluster_name}-control-plane",
        "State": {"Running": True},
        "Config": {
            "Image": platform_verify.KIND_NODE_IMAGE,
            "Labels": {
                "io.x-k8s.kind.cluster": cluster_name,
                "io.x-k8s.kind.role": "control-plane",
            }
        },
        "HostConfig": {"PortBindings": ports},
        "NetworkSettings": {"Ports": ports},
    }


def _install_kind_subprocess_doubles(
    monkeypatch,
    *,
    cluster_names: tuple[str, ...] = ("atlasops-local",),
    containers: list[dict] | None = None,
    default_items: list[dict] | None = None,
    kubectl_returncode: int = 0,
    kube_server: str = "https://127.0.0.1:52345",
    kubernetes_version: str = platform_verify.KUBECTL_VERSION,
):
    inspected = containers if containers is not None else [_kind_container()]
    items = default_items if default_items is not None else []
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        command = list(command)
        commands.append(command)
        if command == ["kind", "get", "clusters"]:
            return subprocess.CompletedProcess(
                command, 0, "\n".join(cluster_names), ""
            )
        if command[:3] == ["docker", "ps", "--all"]:
            ids = [f"container-{index}" for index in range(len(inspected))]
            return subprocess.CompletedProcess(command, 0, "\n".join(ids), "")
        if command[:2] == ["docker", "inspect"]:
            return subprocess.CompletedProcess(
                command, 0, json.dumps(inspected), ""
            )
        if command and command[0] == "kubectl" and "config" in command:
            return subprocess.CompletedProcess(command, 0, kube_server, "")
        if command and command[0] == "kubectl" and "version" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"serverVersion": {"gitVersion": kubernetes_version}}),
                "",
            )
        if command and command[0] == "kubectl":
            return subprocess.CompletedProcess(
                command,
                kubectl_returncode,
                json.dumps({"items": items}),
                "fixture failure" if kubectl_returncode else "",
            )
        raise AssertionError(f"unexpected subprocess command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return commands


def test_prometheus_selects_the_private_otel_service_monitor() -> None:
    values = yaml.safe_load(
        (INFRA_LOCAL / "values" / "prometheus-local.yaml").read_text(encoding="utf-8")
    )
    spec = values["prometheus"]["prometheusSpec"]
    assert spec.get("serviceMonitorSelectorNilUsesHelmValues") is False
    assert spec.get("podMonitorSelectorNilUsesHelmValues") is False
    assert "serviceMonitorSelectorNilUsesHelmValue" not in spec
    assert "podMonitorSelectorNilUsesHelmValue" not in spec


@pytest.mark.parametrize(
    "server",
    [
        "https://another-cluster.invalid:52345",
        "https://127.0.0.1:52346",
        "http://127.0.0.1:52345",
        "",
    ],
)
def test_kind_reuse_rejects_a_context_not_bound_to_the_inspected_node(
    monkeypatch, server,
) -> None:
    commands = _install_kind_subprocess_doubles(monkeypatch, kube_server=server)
    with pytest.raises(RuntimeError, match="context server"):
        platform_verify.verify_existing_kind_cluster(
            "atlasops-local", "kind-atlasops-local"
        )
    assert not any(command[2:3] == ["get"] for command in commands if command[0] == "kubectl")


def test_kind_cluster_name_is_validated_before_any_inspection(monkeypatch) -> None:
    commands = _install_kind_subprocess_doubles(monkeypatch)
    for name in ("AtlasOps", "../atlasops", "atlasops;docker", "a" * 64):
        with pytest.raises(ValueError, match="Kind cluster name"):
            platform_verify.verify_existing_kind_cluster(name, f"kind-{name}")
    assert commands == []


def test_kind_reuse_refuses_an_unpinned_node_image(monkeypatch) -> None:
    container = _kind_container()
    container["Config"]["Image"] = "kindest/node:v1.34.0"
    commands = _install_kind_subprocess_doubles(monkeypatch, containers=[container])

    with pytest.raises(RuntimeError, match="node image"):
        platform_verify.verify_existing_kind_cluster(
            "atlasops-local", "kind-atlasops-local"
        )
    assert not any(command[0] == "kubectl" for command in commands)


@pytest.mark.parametrize("version", ["v1.35.7", "", "v1.34.0"])
def test_kind_reuse_refuses_an_unpinned_kubernetes_server(monkeypatch, version) -> None:
    commands = _install_kind_subprocess_doubles(
        monkeypatch, kubernetes_version=version
    )

    with pytest.raises(RuntimeError, match="Kubernetes server version"):
        platform_verify.verify_existing_kind_cluster(
            "atlasops-local", "kind-atlasops-local"
        )
    assert not any("get" in command for command in commands if command[0] == "kubectl")


def test_kind_reuse_checks_owner_mappings_and_default_namespace_before_returning(
    monkeypatch,
) -> None:
    commands = _install_kind_subprocess_doubles(monkeypatch)

    assert (
        platform_verify.verify_existing_kind_cluster(
            "atlasops-local", "kind-atlasops-local"
        )
        is True
    )
    assert commands[0] == ["kind", "get", "clusters"]
    assert commands[1] == [
        "docker",
        "ps",
        "--all",
        "--quiet",
        "--filter=label=io.x-k8s.kind.cluster=atlasops-local",
    ]
    assert commands[2] == ["docker", "inspect", "container-0"]
    assert commands[3] == [
        "kubectl", "--context=kind-atlasops-local", "config", "view", "--minify",
        "--output=jsonpath={.clusters[0].cluster.server}",
    ]
    assert commands[4] == [
        "kubectl", "--context=kind-atlasops-local", "version", "--output=json",
    ]
    kubectl = commands[5]
    assert kubectl[:3] == [
        "kubectl",
        "--context=kind-atlasops-local",
        "get",
    ]
    assert "-n" in kubectl and kubectl[kubectl.index("-n") + 1] == "default"
    assert kubectl[-2:] == ["-o", "json"]
    assert "secrets" in kubectl[3]


@pytest.mark.parametrize(
    "incompatibility",
    [
        "public_api",
        "wrong_boutique_port",
        "extra_host_port",
        "wrong_cluster_label",
        "wrong_node_role",
        "stopped_control_plane",
        "unrealized_boutique_port",
        "extra_node",
    ],
)
def test_kind_reuse_refuses_incompatible_or_unverifiable_nodes(
    monkeypatch, incompatibility
) -> None:
    container = _kind_container()
    if incompatibility == "public_api":
        container["HostConfig"]["PortBindings"]["6443/tcp"][0]["HostIp"] = "0.0.0.0"
        container["NetworkSettings"]["Ports"]["6443/tcp"][0]["HostIp"] = "0.0.0.0"
    elif incompatibility == "wrong_boutique_port":
        container["HostConfig"]["PortBindings"]["30080/tcp"][0]["HostPort"] = "8081"
        container["NetworkSettings"]["Ports"]["30080/tcp"][0]["HostPort"] = "8081"
    elif incompatibility == "extra_host_port":
        extra = [{"HostIp": "127.0.0.1", "HostPort": "8081"}]
        container["HostConfig"]["PortBindings"]["80/tcp"] = extra
        container["NetworkSettings"]["Ports"]["80/tcp"] = extra
    elif incompatibility == "wrong_cluster_label":
        container["Config"]["Labels"]["io.x-k8s.kind.cluster"] = "another-cluster"
    elif incompatibility == "wrong_node_role":
        container["Config"]["Labels"]["io.x-k8s.kind.role"] = "worker"
    elif incompatibility == "stopped_control_plane":
        container["State"]["Running"] = False
    elif incompatibility == "unrealized_boutique_port":
        container["NetworkSettings"]["Ports"]["30080/tcp"] = None
    elif incompatibility == "extra_node":
        containers = [container, _kind_container()]
        _install_kind_subprocess_doubles(monkeypatch, containers=containers)
        with pytest.raises(RuntimeError):
            platform_verify.verify_existing_kind_cluster(
                "atlasops-local", "kind-atlasops-local"
            )
        return

    _install_kind_subprocess_doubles(monkeypatch, containers=[container])
    with pytest.raises(RuntimeError):
        platform_verify.verify_existing_kind_cluster(
            "atlasops-local", "kind-atlasops-local"
        )


def test_kind_reuse_is_absent_without_running_docker_or_kubectl(monkeypatch) -> None:
    commands = _install_kind_subprocess_doubles(monkeypatch, cluster_names=())
    assert (
        platform_verify.verify_existing_kind_cluster(
            "atlasops-local", "kind-atlasops-local"
        )
        is False
    )
    assert commands == [["kind", "get", "clusters"]]


def test_kind_reuse_refuses_unverifiable_cluster_and_wrong_context(monkeypatch) -> None:
    _install_kind_subprocess_doubles(monkeypatch, kubectl_returncode=1)
    with pytest.raises(RuntimeError, match="default namespace"):
        platform_verify.verify_existing_kind_cluster(
            "atlasops-local", "kind-atlasops-local"
        )

    _install_kind_subprocess_doubles(monkeypatch)
    with pytest.raises(ValueError, match="Kind context"):
        platform_verify.verify_existing_kind_cluster(
            "atlasops-local", "kind-other"
        )


def test_default_namespace_boutique_ownership_blocks_reuse_without_leaking_secret_data(
    monkeypatch,
) -> None:
    legacy = {
        "kind": "Secret",
        "metadata": {
            "name": "frontend-credentials",
            "namespace": "default",
            "labels": {"app": "frontend"},
        },
        "data": {"token": "fixture-secret-value"},
    }
    commands = _install_kind_subprocess_doubles(
        monkeypatch, default_items=[legacy]
    )

    with pytest.raises(RuntimeError, match="default namespace") as error:
        platform_verify.verify_default_namespace_has_no_boutique_resources(
            "kind-atlasops-local"
        )

    assert "fixture-secret-value" not in str(error.value)
    assert "kubectl" in commands[-1][0]
    assert commands[-1][1] == "--context=kind-atlasops-local"


def test_kubectl_json_uses_the_explicit_kubeconfig_and_context(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    kubeconfig = tmp_path / "fixture.kubeconfig"
    kubeconfig.write_text("fixture only\n", encoding="utf-8")
    target = platform_verify.KubeTarget(kubeconfig, "kind-atlasops-local")

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"metadata": {"name": "atlasops-boutique"}}), ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = platform_verify._kubectl_json(
        target,
        "argocd",
        "application",
        "atlasops-boutique",
    )

    assert result["metadata"]["name"] == "atlasops-boutique"
    assert commands == [
        [
            "kubectl",
            f"--kubeconfig={kubeconfig}",
            "--context=kind-atlasops-local",
            "-n",
            "argocd",
            "get",
            "application",
            "atlasops-boutique",
            "-o",
            "json",
        ]
    ]


def test_verify_live_cli_is_context_bound_and_rejects_endpoint_urls(tmp_path, capsys) -> None:
    base_args = [
        "verify-live",
        "--source-sha",
        SOURCE_SHA,
        "--kubeconfig",
        str(tmp_path / "kubeconfig.fixture"),
        "--kube-context",
        "kind-atlasops-local",
        "--operator-key-file",
        str(tmp_path / "operator-key.fixture"),
        "--evidence-file",
        str(tmp_path / "evidence.json"),
    ]

    parsed = platform_verify.build_parser().parse_args(base_args)
    assert parsed.kubeconfig == Path(tmp_path / "kubeconfig.fixture")
    assert parsed.kube_context == "kind-atlasops-local"

    for endpoint_flag in ("--api-url", "--prometheus-url", "--jaeger-url"):
        with pytest.raises(SystemExit) as exit_info:
            platform_verify.build_parser().parse_args(
                [*base_args, endpoint_flag, "http://127.0.0.1:1"]
            )
        assert exit_info.value.code == 2
        assert endpoint_flag in capsys.readouterr().err


def _valid_operations_status(source_sha: str) -> dict:
    return {
        "enabled": True,
        "source_sha": source_sha,
        "certification_status": "NOT_CERTIFIED",
        "operator_id": "fixture-operator",
        "approval_policy": "exact-action-all-mutations",
        "executions": [],
        "pending": [],
        "circuit_breaker": {"tripped": False},
    }


def _valid_coordinator_deployment(source_sha: str) -> dict:
    return {
        "metadata": {
            "name": "atlasops-coordinator",
            "namespace": "default",
            "uid": "deployment-uid",
            "generation": 3,
            "labels": {
                "app.kubernetes.io/name": "atlasops-coordinator",
                "app.kubernetes.io/version": source_sha,
            },
        },
        "spec": {
            "replicas": 1,
            "selector": {
                "matchLabels": {
                    "app.kubernetes.io/name": "atlasops-coordinator"
                }
            },
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/name": "atlasops-coordinator",
                        "app.kubernetes.io/version": source_sha,
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "coordinator",
                            "image": f"atlasops:{source_sha}",
                        }
                    ]
                },
            },
        },
        "status": {"observedGeneration": 3, "readyReplicas": 1},
    }


def _valid_coordinator_replicaset(source_sha: str) -> dict:
    return {
        "metadata": {
            "name": "atlasops-coordinator-abc123",
            "namespace": "default",
            "uid": "replicaset-uid",
            "generation": 1,
            "labels": {
                "app.kubernetes.io/name": "atlasops-coordinator",
                "app.kubernetes.io/version": source_sha,
                "pod-template-hash": "abc123",
            },
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": "atlasops-coordinator",
                    "uid": "deployment-uid",
                    "controller": True,
                }
            ],
        },
        "spec": {
            "replicas": 1,
            "selector": {
                "matchLabels": {
                    "app.kubernetes.io/name": "atlasops-coordinator",
                    "pod-template-hash": "abc123",
                }
            },
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/name": "atlasops-coordinator",
                        "app.kubernetes.io/version": source_sha,
                        "pod-template-hash": "abc123",
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "coordinator",
                            "image": f"atlasops:{source_sha}",
                        }
                    ]
                },
            },
        },
        "status": {"observedGeneration": 1, "replicas": 1, "readyReplicas": 1},
    }


def _valid_coordinator_pod(source_sha: str) -> dict:
    return {
        "metadata": {
            "name": "atlasops-coordinator-abc123",
            "namespace": "default",
            "uid": "pod-uid",
            "labels": {
                "app.kubernetes.io/name": "atlasops-coordinator",
                "app.kubernetes.io/version": source_sha,
                "pod-template-hash": "abc123",
            },
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "ReplicaSet",
                    "name": "atlasops-coordinator-abc123",
                    "uid": "replicaset-uid",
                    "controller": True,
                }
            ],
        },
        "spec": {
            "containers": [
                {
                    "name": "coordinator",
                    "image": f"atlasops:{source_sha}",
                }
            ]
        },
        "status": {
            "phase": "Running",
            "podIP": "10.0.0.3",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [
                {
                    "name": "coordinator",
                    "ready": True,
                    "image": f"atlasops:{source_sha}",
                    "imageID": "containerd://sha256:" + "f" * 64,
                }
            ],
        },
    }


def _valid_argocd_application(source_sha: str) -> dict:
    app = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": "atlasops-boutique", "namespace": "argocd"},
        "spec": {
            "project": "atlasops-boutique",
            "source": {
                "repoURL": FORK_REPO,
                "targetRevision": source_sha,
                "path": "infra/local/boutique",
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": "atlasops-boutique",
            },
            "syncPolicy": {
                "automated": {
                    "enabled": True,
                    "prune": False,
                    "selfHeal": False,
                    "allowEmpty": False,
                },
                "syncOptions": [],
            },
        },
        "status": {"sync": {"status": "Synced", "revision": source_sha}},
    }
    return app


def _metric_rows(
    now: float,
    *,
    value: str = "2",
    sample_timestamp: float | None = None,
    services: tuple[str, ...] = TRACED_BOUTIQUE_SERVICES,
) -> list[dict]:
    timestamp = now if sample_timestamp is None else sample_timestamp
    return [
        {
            "metric": {
                "service_name": service,
                "span_kind": "SPAN_KIND_SERVER",
            },
            "value": [str(timestamp), value],
        }
        for service in services
    ]


def _valid_trace(service: str, now: float) -> dict:
    trace_id = "a" * 32
    return {
        "traceID": trace_id,
        "spans": [
            {
                "traceID": trace_id,
                "spanID": "b" * 16,
                "operationName": "GET /",
                "startTime": int(now * 1_000_000),
                "duration": 1000,
                "processID": "process-1",
            }
        ],
        "processes": {"process-1": {"serviceName": service}},
    }


def _install_verify_live_doubles(
    monkeypatch,
    tmp_path: Path,
    *,
    source_sha: str = SOURCE_SHA,
    operations_sha: str | None = None,
    deployment: dict | None = None,
    pod: dict | None = None,
    application: dict | None = None,
    calls: list[dict] | None = None,
    durations: list[dict] | None = None,
    trace_factory=None,
):
    now = 1_760_000_000.0
    operations = _valid_operations_status(
        source_sha if operations_sha is None else operations_sha
    )
    deployment = deployment or _valid_coordinator_deployment(source_sha)
    replicaset = _valid_coordinator_replicaset(source_sha)
    pod = pod or _valid_coordinator_pod(source_sha)
    application = application or _valid_argocd_application(source_sha)
    calls = calls if calls is not None else _metric_rows(now)
    durations = durations if durations is not None else _metric_rows(now)
    trace_factory = trace_factory or (lambda service: _valid_trace(service, now))
    operator_key_file = tmp_path / "operator-key.fixture"
    operator_key_file.write_text("fixture-only-key\n", encoding="utf-8")
    kubeconfig = tmp_path / "fixture.kubeconfig"
    kubeconfig.write_text("fixture only\n", encoding="utf-8")
    target = platform_verify.KubeTarget(kubeconfig, "kind-atlasops-local")
    ports = {"coordinator": 40100, "prometheus": 40101, "jaeger": 40102}
    loopback_requests: list[tuple[int, str, dict[str, str] | None]] = []
    kubectl_reads: list[tuple[object, str | None, str, str | None]] = []
    forward_starts: list[
        tuple[object, tuple[platform_verify.ServiceEndpoint, ...]]
    ] = []
    legacy_requests: list[tuple[str, dict | None]] = []
    prometheus_queries: list[str] = []

    route_responses: dict[tuple[str, str, str], dict] = {}
    for route in platform_verify.SERVICE_ROUTES:
        if route.name == "coordinator":
            route_selector = {"app.kubernetes.io/name": "atlasops-coordinator"}
            endpoint_pod = pod
            endpoint_ip = pod["status"]["podIP"]
        else:
            route_selector = {"app.kubernetes.io/name": route.name}
            endpoint_pod = {
                "metadata": {
                    "name": f"{route.name}-0",
                    "namespace": route.namespace,
                    "uid": f"{route.name}-pod-uid",
                    "labels": route_selector,
                },
                "spec": {},
                "status": {
                    "phase": "Running",
                    "podIP": f"10.0.0.{8 if route.name == 'prometheus' else 9}",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            }
            endpoint_ip = endpoint_pod["status"]["podIP"]
        service = {
            "metadata": {"name": route.service_name, "namespace": route.namespace},
            "spec": {
                "type": "ClusterIP",
                "selector": route_selector,
                "ports": [
                    {
                        "name": "http",
                        "port": route.service_port,
                        "targetPort": route.target_port,
                        "protocol": "TCP",
                    }
                ],
            },
        }
        endpoint_name = endpoint_pod["metadata"]["name"]
        endpoints = {
            "metadata": {"name": route.service_name, "namespace": route.namespace},
            "subsets": [
                {
                    "addresses": [
                        {
                            "ip": endpoint_ip,
                            "targetRef": {
                                "kind": "Pod",
                                "name": endpoint_name,
                                "namespace": route.namespace,
                                "uid": endpoint_pod["metadata"]["uid"],
                            },
                        }
                    ],
                    "ports": [
                        {
                            "name": "http",
                            "port": route.pod_port,
                            "protocol": "TCP",
                        }
                    ],
                }
            ],
        }
        route_responses[(route.namespace, "service", route.service_name)] = service
        route_responses[(route.namespace, "endpoints", route.service_name)] = endpoints
        route_responses[(route.namespace, "pod", endpoint_name)] = endpoint_pod

    def fake_loopback_json(local_port, path, *, headers=None, timeout):
        loopback_requests.append((local_port, path, headers))
        if local_port == ports["coordinator"]:
            if path == "/health":
                return 200, {"status": "ok"}
            if path == "/api/operations":
                if not headers:
                    return 401, None
                return 200, operations
        if local_port == ports["prometheus"] and path.startswith("/api/v1/query?"):
            query = parse_qs(urlsplit(path).query).get("query", [""])[0]
            prometheus_queries.append(query)
            samples = durations if "duration_milliseconds" in query else calls
            return 200, {"status": "success", "data": {"result": samples}}
        if local_port == ports["jaeger"]:
            if path == "/api/services":
                return 200, {"data": list(TRACED_BOUTIQUE_SERVICES)}
            if path.startswith("/api/traces?"):
                service = parse_qs(urlsplit(path).query).get("service", [""])[0]
                return 200, {"data": [trace_factory(service)]}
        raise AssertionError(
            f"unexpected loopback request to port {local_port}: {path}"
        )

    def fake_owned_forwards(forward_target, endpoints):
        @contextmanager
        def started_forwards():
            forward_starts.append((forward_target, tuple(endpoints)))
            yield ports

        return started_forwards()

    def fake_http_json(url, *, headers=None, timeout):
        legacy_requests.append((url, headers))
        raise AssertionError(f"live verifier used a non-owned HTTP URL: {url}")

    def fake_kubectl_json(read_target, namespace, resource, name=None, selector=None):
        kubectl_reads.append((read_target, namespace, resource, name))
        if (namespace, resource, name) == (None, "namespace", "kube-system"):
            return {
                "metadata": {"name": "kube-system", "uid": "cluster-uid-fixture"}
            }
        if (namespace, resource, name) == (
            "default",
            "deployment",
            "atlasops-coordinator",
        ):
            return deployment
        if (namespace, resource, name) == ("default", "replicasets", None):
            return {"items": [replicaset]}
        if (namespace, resource, name) == ("default", "pods", None):
            return {"items": [pod]}
        if (namespace, resource, name) == (
            "argocd",
            "application",
            "atlasops-boutique",
        ):
            return application
        response = route_responses.get((namespace, resource, name))
        if response is not None:
            return response
        raise AssertionError(f"unexpected kubectl read: {namespace}/{resource}/{name}")

    ticks = iter((0.0, 0.5, 2.0))
    monkeypatch.setattr(platform_verify, "_http_json", fake_http_json, raising=False)
    monkeypatch.setattr(platform_verify, "_loopback_json", fake_loopback_json)
    monkeypatch.setattr(platform_verify, "_owned_forwards", fake_owned_forwards)
    monkeypatch.setattr(
        platform_verify, "_kubectl_json", fake_kubectl_json
    )
    monkeypatch.setattr(platform_verify.time, "time", lambda: now)
    monkeypatch.setattr(
        platform_verify.time, "monotonic", lambda: next(ticks, 2.0)
    )
    monkeypatch.setattr(platform_verify.time, "sleep", lambda _seconds: None)
    args = SimpleNamespace(
        source_sha=source_sha,
        kubeconfig=kubeconfig,
        kube_context="kind-atlasops-local",
        operator_key_file=operator_key_file,
        request_timeout=1.0,
        signal_wait_seconds=1,
        poll_seconds=0,
        evidence_file=tmp_path / "verify-live.json",
    )
    return (
        args,
        loopback_requests,
        legacy_requests,
        kubectl_reads,
        prometheus_queries,
        forward_starts,
        target,
        now,
    )


def test_runtime_identity_binds_deployment_replicaset_pod_and_cluster_uid(
    monkeypatch, tmp_path: Path
) -> None:
    kubeconfig = tmp_path / "fixture.kubeconfig"
    kubeconfig.write_text("fixture only\n", encoding="utf-8")
    target = platform_verify.KubeTarget(kubeconfig, "kind-atlasops-local")
    deployment = _valid_coordinator_deployment(SOURCE_SHA)
    replicaset = _valid_coordinator_replicaset(SOURCE_SHA)
    pod = _valid_coordinator_pod(SOURCE_SHA)
    application = _valid_argocd_application(SOURCE_SHA)
    responses = {
        (None, "namespace", "kube-system"): {
            "metadata": {"name": "kube-system", "uid": "cluster-uid-fixture"}
        },
        ("default", "deployment", "atlasops-coordinator"): deployment,
        ("default", "replicasets", None): {"items": [replicaset]},
        ("default", "pods", None): {"items": [pod]},
        ("argocd", "application", "atlasops-boutique"): application,
    }
    reads: list[tuple[object, str | None, str, str | None]] = []

    def fake_kubectl_json(read_target, namespace, resource, name=None, selector=None):
        reads.append((read_target, namespace, resource, name))
        return responses[(namespace, resource, name)]

    monkeypatch.setattr(platform_verify, "_kubectl_json", fake_kubectl_json)

    identity = platform_verify._verify_runtime_source_identity(SOURCE_SHA, target)

    assert identity["kube_system_uid"] == "cluster-uid-fixture"
    assert identity["coordinator_replicaset"] == "default/atlasops-coordinator-abc123"
    assert identity["coordinator_pod"] == "atlasops-coordinator-abc123"
    assert identity["coordinator_pod_image_id"] == "containerd://sha256:" + "f" * 64
    assert {read[0] for read in reads} == {target}
    assert {read[1:3] for read in reads} == {
        (None, "namespace"),
        ("default", "deployment"),
        ("default", "replicasets"),
        ("default", "pods"),
        ("argocd", "application"),
    }


@pytest.mark.parametrize(
    "route_name,pod_name,pod_uid,pod_ip,selector_value",
    [
        (
            "coordinator",
            "atlasops-coordinator-abc123",
            "coordinator-pod-uid",
            "10.0.0.3",
            "atlasops-coordinator",
        ),
        ("prometheus", "prometheus-0", "prometheus-pod-uid", "10.0.0.8", "prometheus"),
    ],
)
def test_service_discovery_resolves_a_ready_pod_through_the_explicit_target(
    monkeypatch,
    tmp_path: Path,
    route_name: str,
    pod_name: str,
    pod_uid: str,
    pod_ip: str,
    selector_value: str,
) -> None:
    kubeconfig = tmp_path / "fixture.kubeconfig"
    kubeconfig.write_text("fixture only\n", encoding="utf-8")
    target = platform_verify.KubeTarget(kubeconfig, "kind-atlasops-local")
    route = next(
        candidate
        for candidate in platform_verify.SERVICE_ROUTES
        if candidate.name == route_name
    )
    service = {
        "metadata": {"name": route.service_name, "namespace": route.namespace},
        "spec": {
            "type": "ClusterIP",
            "selector": {"app.kubernetes.io/name": selector_value},
            "ports": [
                {
                    "name": "http",
                    "port": route.service_port,
                    "targetPort": route.target_port,
                    "protocol": "TCP",
                }
            ],
        },
    }
    endpoints = {
        "metadata": {"name": route.service_name, "namespace": route.namespace},
        "subsets": [
            {
                "addresses": [
                    {
                        "ip": pod_ip,
                        "targetRef": {
                            "kind": "Pod",
                            "name": pod_name,
                            "namespace": route.namespace,
                            "uid": pod_uid,
                        },
                    }
                ],
                "ports": [
                    {
                        "name": "http",
                        "port": route.pod_port,
                        "protocol": "TCP",
                    }
                ],
            }
        ],
    }
    pod = {
        "metadata": {
            "name": pod_name,
            "namespace": route.namespace,
            "uid": pod_uid,
            "labels": {"app.kubernetes.io/name": selector_value},
        },
        "status": {
            "phase": "Running",
            "podIP": pod_ip,
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }
    responses = {
        (route.namespace, "service", route.service_name): service,
        (route.namespace, "endpoints", route.service_name): endpoints,
        (route.namespace, "pod", pod_name): pod,
    }
    reads: list[tuple[object, str | None, str, str | None]] = []

    def fake_kubectl_json(read_target, namespace, resource, name=None, selector=None):
        reads.append((read_target, namespace, resource, name))
        return responses[(namespace, resource, name)]

    monkeypatch.setattr(platform_verify, "_kubectl_json", fake_kubectl_json)

    endpoint = platform_verify._resolve_service_endpoint(target, route)

    assert endpoint.pod_name == pod_name
    assert endpoint.pod_uid == pod_uid
    assert endpoint.pod_port == route.pod_port
    if route.name == "coordinator":
        assert (
            route.service_port,
            route.target_port,
            endpoint.pod_port,
        ) == (9099, "http", 7860)
    assert {read[0] for read in reads} == {target}
    assert {read[1:3] for read in reads} == {
        (route.namespace, "service"),
        (route.namespace, "endpoints"),
        (route.namespace, "pod"),
    }


def _forward_test_endpoints() -> tuple[object, ...]:
    return (
        platform_verify.ServiceEndpoint(
            "coordinator",
            "default",
            "atlasops-coordinator-svc",
            "atlasops-coordinator-abc123",
            "coordinator-uid",
            "10.0.0.3",
            7860,
        ),
        platform_verify.ServiceEndpoint(
            "prometheus",
            "monitoring",
            "prometheus-kube-prometheus-prometheus",
            "prometheus-0",
            "prometheus-uid",
            "10.0.0.8",
            9090,
        ),
        platform_verify.ServiceEndpoint(
            "jaeger",
            "jaeger",
            "jaeger",
            "jaeger-0",
            "jaeger-uid",
            "10.0.0.9",
            16686,
        ),
    )


class _ForwardProcess:
    def __init__(self, output: str, *, fail_post_kill_wait: bool = False) -> None:
        self.stdout = io.StringIO(output)
        self.returncode: int | None = None
        self.fail_post_kill_wait = fail_post_kill_wait
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        if not self.fail_post_kill_wait:
            self.returncode = -9

    def wait(self, timeout: float) -> int:
        self.wait_calls += 1
        if self.fail_post_kill_wait and self.kill_calls and self.wait_calls > 1:
            raise OSError("fixture post-kill wait failure")
        if self.fail_post_kill_wait and self.kill_calls == 0:
            raise subprocess.TimeoutExpired("kubectl", timeout)
        self.returncode = 0
        return self.returncode or 0


def test_owned_forwards_use_explicit_target_and_child_reported_ports(
    monkeypatch, tmp_path: Path
) -> None:
    kubeconfig = tmp_path / "fixture.kubeconfig"
    kubeconfig.write_text("fixture only\n", encoding="utf-8")
    target = platform_verify.KubeTarget(kubeconfig, "kind-atlasops-local")
    endpoints = _forward_test_endpoints()
    commands: list[list[str]] = []
    processes: list[_ForwardProcess] = []

    def fake_popen(command, **kwargs):
        command = list(command)
        commands.append(command)
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.STDOUT
        assert kwargs["text"] is True
        endpoint = endpoints[len(processes)]
        process = _ForwardProcess(
            f"Forwarding from 127.0.0.1:{42000 + len(processes)} -> "
            f"{endpoint.pod_port}\n"
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with platform_verify._owned_forwards(
        target, endpoints, startup_timeout=1
    ) as ports:
        assert ports == {
            "coordinator": 42000,
            "prometheus": 42001,
            "jaeger": 42002,
        }

    assert commands == [
        [
            "kubectl",
            f"--kubeconfig={kubeconfig}",
            "--context=kind-atlasops-local",
            "port-forward",
            "--address=127.0.0.1",
            "--namespace",
            endpoint.namespace,
            f"pod/{endpoint.pod_name}",
            f":{endpoint.pod_port}",
        ]
        for endpoint in endpoints
    ]
    assert all(process.terminate_calls == 1 for process in processes)
    assert all(process.wait_calls == 1 for process in processes)
    assert all(process.kill_calls == 0 for process in processes)


def test_owned_forwards_reject_an_unrelated_listening_port_as_readiness(
    monkeypatch, tmp_path: Path
) -> None:
    kubeconfig = tmp_path / "fixture.kubeconfig"
    kubeconfig.write_text("fixture only\n", encoding="utf-8")
    target = platform_verify.KubeTarget(kubeconfig, "kind-atlasops-local")
    endpoints = _forward_test_endpoints()
    processes: list[_ForwardProcess] = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as unrelated_listener:
        unrelated_listener.bind(("127.0.0.1", 0))
        unrelated_listener.listen()

        def fake_popen(_command, **_kwargs):
            process = _ForwardProcess("")
            processes.append(process)
            return process

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        with pytest.raises(RuntimeError, match="did not report its loopback forward"):
            with platform_verify._owned_forwards(
                target, endpoints, startup_timeout=0.1
            ):
                pytest.fail("an unrelated listener must not count as forward readiness")

    assert len(processes) == 1
    assert processes[0].terminate_calls == 1
    assert processes[0].wait_calls == 1
    assert processes[0].kill_calls == 0


def test_owned_forwards_clean_every_registered_child_after_partial_start(
    monkeypatch, tmp_path: Path
) -> None:
    kubeconfig = tmp_path / "fixture.kubeconfig"
    kubeconfig.write_text("fixture only\n", encoding="utf-8")
    target = platform_verify.KubeTarget(kubeconfig, "kind-atlasops-local")
    endpoints = _forward_test_endpoints()
    processes: list[_ForwardProcess] = []
    starts = 0

    def fake_popen(command, **_kwargs):
        nonlocal starts
        starts += 1
        if starts == 3:
            raise OSError("fixture third forward could not start")
        endpoint = endpoints[starts - 1]
        process = _ForwardProcess(
            f"Forwarding from 127.0.0.1:{43000 + starts} -> "
            f"{endpoint.pod_port}\n"
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(OSError, match="third forward"):
        with platform_verify._owned_forwards(
            target, endpoints, startup_timeout=1
        ):
            pytest.fail("startup should stop when the third child cannot start")

    assert len(processes) == 2
    assert all(process.terminate_calls == 1 for process in processes)
    assert all(process.wait_calls == 1 for process in processes)


def test_owned_forwards_attempt_every_cleanup_after_post_kill_wait_failure(
    monkeypatch, tmp_path: Path
) -> None:
    kubeconfig = tmp_path / "fixture.kubeconfig"
    kubeconfig.write_text("fixture only\n", encoding="utf-8")
    target = platform_verify.KubeTarget(kubeconfig, "kind-atlasops-local")
    endpoints = _forward_test_endpoints()
    processes: list[_ForwardProcess] = []

    def fake_popen(command, **_kwargs):
        endpoint = endpoints[len(processes)]
        process = _ForwardProcess(
            f"Forwarding from 127.0.0.1:{44000 + len(processes)} -> "
            f"{endpoint.pod_port}\n",
            fail_post_kill_wait=len(processes) == 0,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="cleanup"):
        with platform_verify._owned_forwards(
            target, endpoints, startup_timeout=1
        ):
            raise ValueError("fixture body failure")

    assert len(processes) == 3
    assert all(process.terminate_calls == 1 for process in processes)
    assert processes[0].kill_calls == 1
    assert [process.wait_calls for process in processes] == [2, 1, 1]


def test_loopback_json_does_not_use_proxy_or_follow_redirects(
    monkeypatch,
) -> None:
    proxy_hits: list[str] = []
    forwarded_requests: list[tuple[str, str | None]] = []

    def proxy_handler(request):
        proxy_hits.append(request.path)
        return 200, {"Content-Type": "application/json"}, b'{"proxy": true}'

    with _http_server(proxy_handler) as proxy_port:

        def redirect_handler(request):
            forwarded_requests.append(
                (request.path, request.headers.get("X-AtlasOps-Key"))
            )
            return (
                302,
                {"Location": f"http://127.0.0.1:{proxy_port}/redirect-target"},
                b"",
            )

        with _http_server(redirect_handler) as local_port:
            proxy_url = f"http://127.0.0.1:{proxy_port}"
            monkeypatch.setenv("HTTP_PROXY", proxy_url)
            monkeypatch.setenv("http_proxy", proxy_url)
            monkeypatch.setenv("ALL_PROXY", proxy_url)
            monkeypatch.setenv("all_proxy", proxy_url)
            monkeypatch.setenv("NO_PROXY", "")
            monkeypatch.setenv("no_proxy", "")

            with pytest.raises(RuntimeError, match="redirect"):
                platform_verify._loopback_json(
                    local_port,
                    "/api/operations",
                    headers={"X-AtlasOps-Key": "fixture-only-secret"},
                    timeout=1,
                )

    assert forwarded_requests == [
        ("/api/operations", "fixture-only-secret")
    ]
    assert proxy_hits == []


def test_verify_live_source_mismatch_precedes_key_read_and_forward_start(
    monkeypatch, tmp_path: Path
) -> None:
    deployment = _valid_coordinator_deployment(SOURCE_SHA)
    deployment["metadata"]["labels"]["app.kubernetes.io/version"] = "0" * 40
    (
        args,
        requests,
        legacy_requests,
        _reads,
        _queries,
        forward_starts,
        _target,
        _now,
    ) = _install_verify_live_doubles(
        monkeypatch,
        tmp_path,
        deployment=deployment,
    )
    key_reads: list[Path] = []
    original_read_text = Path.read_text

    def tracked_read_text(path, *args, **kwargs):
        if path == args_value.operator_key_file:
            key_reads.append(path)
        return original_read_text(path, *args, **kwargs)

    args_value = args
    monkeypatch.setattr(Path, "read_text", tracked_read_text)

    with pytest.raises(RuntimeError, match="coordinator deployment source"):
        platform_verify.verify_live(args)

    assert key_reads == []
    assert forward_starts == []
    assert requests == []
    assert legacy_requests == []


def test_verify_live_refuses_redirect_before_operator_key_read(
    monkeypatch, tmp_path: Path
) -> None:
    args, _requests, _legacy_requests, _reads, _queries, forward_starts, _target, _now = (
        _install_verify_live_doubles(monkeypatch, tmp_path)
    )
    key_reads: list[Path] = []
    requests: list[tuple[str, dict[str, str] | None]] = []
    original_read_text = Path.read_text

    def tracked_read_text(path, *args, **kwargs):
        if path == args_value.operator_key_file:
            key_reads.append(path)
        return original_read_text(path, *args, **kwargs)

    def redirect_before_auth(_local_port, path, *, headers=None, timeout):
        requests.append((path, headers))
        if path == "/health":
            return 200, {"status": "ok"}
        if path == "/api/operations" and not headers:
            raise RuntimeError("loopback endpoint returned a redirect")
        raise AssertionError(f"unexpected request before authentication: {path}")

    args_value = args
    monkeypatch.setattr(Path, "read_text", tracked_read_text)
    monkeypatch.setattr(platform_verify, "_loopback_json", redirect_before_auth)

    with pytest.raises(RuntimeError, match="redirect"):
        platform_verify.verify_live(args)

    assert requests == [("/health", None), ("/api/operations", None)]
    assert key_reads == []
    assert len(forward_starts) == 1
    assert not args.evidence_file.exists()


def test_verify_live_binds_authenticated_api_kubernetes_and_sampled_traces_to_source(
    monkeypatch, tmp_path: Path
) -> None:
    (
        args,
        requests,
        legacy_requests,
        kubectl_reads,
        queries,
        forward_starts,
        target,
        _now,
    ) = _install_verify_live_doubles(monkeypatch, tmp_path)

    assert platform_verify.verify_live(args) == 0

    evidence = json.loads(args.evidence_file.read_text(encoding="utf-8"))
    assert evidence["classification"] == "NON_EMPIRICAL_SOFTWARE_FIXTURE"
    assert evidence["source_sha"] == SOURCE_SHA
    identity = evidence["observations"]["runtime_identity"]
    assert identity["kube_context"] == "kind-atlasops-local"
    assert identity["kube_system_uid"] == "cluster-uid-fixture"
    assert identity["coordinator_replicaset"] == "default/atlasops-coordinator-abc123"
    assert identity["coordinator_pod_uid"] == "pod-uid"
    assert identity["coordinator_image"] == f"atlasops:{SOURCE_SHA}"
    assert identity["argocd_observed_revision"] == SOURCE_SHA
    assert evidence["observations"]["coordinator_health"] == {
        "http_status": 200,
        "status": "ok",
    }
    sampled = evidence["observations"]["telemetry"]["sampled_services"]
    assert sampled == list(TRACED_BOUTIQUE_SERVICES)
    assert evidence["observations"]["telemetry"]["scope"] == (
        "sampled recent traces and span-derived metrics; not an all-traffic measure"
    )
    assert requests[:3] == [
        (40100, "/health", None),
        (40100, "/api/operations", None),
        (
            40100,
            "/api/operations",
            {
                "X-AtlasOps-Key": "fixture-only-key",
                "Accept": "application/json",
            },
        ),
    ]
    assert legacy_requests == []
    assert len(queries) == 2
    assert {
        port for port, path, _headers in requests if path.startswith("/api/v1/query?")
    } == {40101}
    assert {
        port for port, path, _headers in requests if path == "/api/services"
    } == {40102}
    assert all(
        port == 40102
        for port, path, _headers in requests
        if path.startswith("/api/traces?")
    )
    assert kubectl_reads
    assert {read[0] for read in kubectl_reads} == {target}
    assert len(forward_starts) == 1
    assert forward_starts[0][0] == target
    assert [endpoint.name for endpoint in forward_starts[0][1]] == [
        "coordinator",
        "prometheus",
        "jaeger",
    ]
    assert [
        (endpoint.name, endpoint.pod_port)
        for endpoint in forward_starts[0][1]
    ] == [("coordinator", 7860), ("prometheus", 9090), ("jaeger", 16686)]
    assert platform_verify.SERVICE_ROUTES[0].service_port == 9099
    assert platform_verify.SERVICE_ROUTES[0].target_port == "http"
    assert any("increase(" in query for query in queries)


@pytest.mark.parametrize(
    "identity_failure",
    [
        "operations_source",
        "deployment_label",
        "deployment_image",
        "pod_label",
        "pod_image",
        "pod_image_id",
        "argo_spec",
        "argo_sync_state",
        "argo_observed_revision",
    ],
)
def test_verify_live_refuses_every_source_identity_mismatch_without_receipt(
    monkeypatch, tmp_path: Path, identity_failure: str
) -> None:
    deployment = _valid_coordinator_deployment(SOURCE_SHA)
    pod = _valid_coordinator_pod(SOURCE_SHA)
    application = _valid_argocd_application(SOURCE_SHA)
    operations_sha = SOURCE_SHA
    if identity_failure == "operations_source":
        operations_sha = "0" * 40
    elif identity_failure == "deployment_label":
        deployment["metadata"]["labels"]["app.kubernetes.io/version"] = "0" * 40
    elif identity_failure == "deployment_image":
        deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "atlasops:other"
    elif identity_failure == "pod_label":
        pod["metadata"]["labels"]["app.kubernetes.io/version"] = "0" * 40
    elif identity_failure == "pod_image":
        pod["status"]["containerStatuses"][0]["image"] = "atlasops:other"
    elif identity_failure == "pod_image_id":
        pod["status"]["containerStatuses"][0]["imageID"] = "placeholder"
    elif identity_failure == "argo_spec":
        application["spec"]["source"]["targetRevision"] = "0" * 40
    elif identity_failure == "argo_sync_state":
        application["status"]["sync"]["status"] = "OutOfSync"
    elif identity_failure == "argo_observed_revision":
        application["status"]["sync"]["revision"] = "0" * 40

    args, _requests, _legacy_requests, _reads, queries, forward_starts, _target, _now = (
        _install_verify_live_doubles(
            monkeypatch,
            tmp_path,
            operations_sha=operations_sha,
            deployment=deployment,
            pod=pod,
            application=application,
        )
    )

    with pytest.raises(RuntimeError, match="source|image|revision|Synced|Argo|label"):
        platform_verify.verify_live(args)
    assert queries == []
    if identity_failure == "operations_source":
        assert len(forward_starts) == 1
        assert [endpoint.name for endpoint in forward_starts[0][1]] == [
            "coordinator",
            "prometheus",
            "jaeger",
        ]
    else:
        assert forward_starts == []
    assert not args.evidence_file.exists()


@pytest.mark.parametrize(
    "signal_failure",
    [
        "empty_calls",
        "zero_calls",
        "stale_metrics",
        "missing_traced_service",
        "empty_duration",
        "malformed_metric",
        "bad_trace_id",
        "empty_trace",
        "stale_trace",
        "wrong_trace_service",
    ],
)
def test_verify_live_rejects_empty_malformed_stale_or_unattributed_signals(
    monkeypatch, tmp_path: Path, signal_failure: str
) -> None:
    now = 1_760_000_000.0
    calls = _metric_rows(now)
    durations = _metric_rows(now)
    trace_factory = lambda service: _valid_trace(service, now)
    if signal_failure == "empty_calls":
        calls = []
    elif signal_failure == "zero_calls":
        calls = _metric_rows(now, value="0")
    elif signal_failure == "stale_metrics":
        calls = _metric_rows(now, sample_timestamp=now - 301)
    elif signal_failure == "missing_traced_service":
        calls = _metric_rows(now, services=TRACED_BOUTIQUE_SERVICES[:-1])
    elif signal_failure == "empty_duration":
        durations = []
    elif signal_failure == "malformed_metric":
        calls = [{"metric": ["not", "a", "map"], "value": [str(now), "1"]}]
    elif signal_failure == "bad_trace_id":
        trace_factory = lambda _service: {
            "traceID": "placeholder",
            "spans": [],
            "processes": {},
        }
    elif signal_failure == "empty_trace":
        trace_factory = lambda _service: {
            "traceID": "a" * 32,
            "spans": [],
            "processes": {},
        }
    elif signal_failure == "stale_trace":
        trace_factory = lambda service: _valid_trace(service, now - 301)
    elif signal_failure == "wrong_trace_service":
        trace_factory = lambda _service: _valid_trace("placeholder", now)

    args, _requests, _legacy_requests, _reads, _queries, _forwards, _target, _current = (
        _install_verify_live_doubles(
            monkeypatch,
            tmp_path,
            calls=calls,
            durations=durations,
            trace_factory=trace_factory,
        )
    )

    with pytest.raises(RuntimeError, match="fresh seven-service telemetry"):
        platform_verify.verify_live(args)
    assert not args.evidence_file.exists()
