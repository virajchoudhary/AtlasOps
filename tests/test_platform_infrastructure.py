"""Model-free contracts for the integrated local AI-SRE platform."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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


def test_both_images_select_verified_kubectl_checksum_for_target_architecture() -> None:
    expected = {
        "amd64": "f7e806b676bea3b4995e9c236445a5f24ae61ed3d5245c39d7b816d209b06a78",
        "arm64": "73dcb8c9031d9533c0b8b1f12ffaddf0a5e8c559fbce3397693399212ec75ed9",
    }
    for path in ("Dockerfile", "Dockerfile.coordinator"):
        content = read(path)
        assert "ARG TARGETARCH" in content
        assert "KUBECTL_VERSION=v1.31.10" in content
        assert f"ARG KUBECTL_SHA256={expected['amd64']}" in content
        assert f"ARG KUBECTL_SHA256_ARM64={expected['arm64']}" in content
        assert "linux/${TARGETARCH}/kubectl" in content
        assert "sha256sum -c -" in content


def test_root_image_is_non_root_and_labels_the_source_revision() -> None:
    dockerfile = read("Dockerfile")
    assert "USER 10001:10001" in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile
    assert "ARG ATLASOPS_SOURCE_SHA" in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
    assert '--workers", "1"' in dockerfile


def test_kind_api_and_host_mapping_bind_to_loopback() -> None:
    config = yaml.safe_load((INFRA_LOCAL / "kind-config.yaml").read_text(encoding="utf-8"))
    networking = config["networking"]
    assert networking["apiServerAddress"] == "127.0.0.1"
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


def test_coordinator_mutation_roles_are_scoped_to_supported_namespaces_and_targets() -> None:
    items = documents(INFRA_LOCAL / "coordinator-local.yaml")
    readonly = find_document(items, "ClusterRole", "atlasops-coordinator-readonly")
    assert all(
        rule.get("apiGroups") != ["chaos-mesh.org"] for rule in readonly["rules"]
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

    remediation_role = find_document(
        items, "Role", "atlasops-coordinator-remediation"
    )
    assert remediation_role["metadata"]["namespace"] == "atlasops-boutique"
    assert remediation_role["rules"] == [
        {
            "apiGroups": ["apps"],
            "resources": ["deployments", "deployments/scale"],
            "resourceNames": list(BOUTIQUE_DEPLOYMENT_NAMES),
            "verbs": ["get", "patch", "update"],
        }
    ]
    remediation_binding = find_document(
        items, "RoleBinding", "atlasops-coordinator-remediation"
    )
    assert remediation_binding["metadata"]["namespace"] == "atlasops-boutique"
    assert remediation_binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "atlasops-coordinator",
            "namespace": "default",
        }
    ]


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


def test_platform_kind_workflow_is_disposable_private_and_non_empirical() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "platform-kind.yml"
    if not workflow_path.is_file():
        pytest.skip("Combined disposable Kind CI is owned by the parent lane.")
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    job = workflow["jobs"]["platform-kind"]
    steps = job["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert job["timeout-minutes"] >= 45
    assert workflow["permissions"] == {"contents": "read"}
    assert "atlasops-platform-ci" in commands
    assert "kind delete cluster --name atlasops-platform-ci" in commands
    assert "ATLASOPS_NON_EMPIRICAL" in commands
    assert "/api/operations" in commands
    assert "atlasops_boutique_calls_total" in commands
    assert "/api/traces" in commands
    assert "HF_TOKEN" not in workflow.get("env", {})
    assert steps[-1]["if"] == "always()"
