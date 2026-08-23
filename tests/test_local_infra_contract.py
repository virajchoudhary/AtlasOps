"""AtlasOps Stage 3 local Kind infrastructure contract tests.

Pipeline v1.1 Free-First — validates that the local deployment path:
- Has zero gcloud dependency
- Uses deterministic Kind cluster name and context
- Deploys from Kind-loaded images (no registry)
- Requires no cloud LoadBalancer for core acceptance
- Targets an explicit kubectl context
- Provides idempotent teardown
- Keeps secrets uncommitted
- Preserves the GKE path as optional
- Maps v1.1 governance status correctly
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INFRA_LOCAL = REPO_ROOT / "infra" / "local"
INFRA_GKE = REPO_ROOT / "infra"

# =============================================================================
# Constants under test
# =============================================================================
KIND_CLUSTER_NAME = "atlasops-local"
KUBE_CONTEXT = f"kind-{KIND_CLUSTER_NAME}"


class TestLocalSetupNoGcloudDependency:
    """Verify the local path has ZERO gcloud dependency."""

    def test_setup_local_has_no_gcloud_invocation(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        # Check for actual gcloud command invocations (line starts with gcloud or pipes to it)
        gcloud_invocation = re.compile(r'^\s*gcloud\s|[|&]\s*gcloud\s')
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue  # skip comments
            assert not gcloud_invocation.search(line), (
                f"Local setup must not invoke gcloud (line {i}): {stripped}"
            )

    def test_setup_local_has_no_gcp_project_requirement(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        assert "PROJECT_ID" not in content or "GCP_PROJECT" not in content.split("ConfigMap")[0], \
            "Local setup must not require a GCP PROJECT_ID argument"

    def test_teardown_local_has_no_gcloud_command(self) -> None:
        content = (INFRA_LOCAL / "teardown_local.sh").read_text(encoding="utf-8")
        assert "gcloud " not in content, "Local teardown must not invoke gcloud"

    def test_local_values_have_no_gcloud_references(self) -> None:
        values_dir = INFRA_LOCAL / "values"
        for yaml_file in values_dir.glob("*.yaml"):
            content = yaml_file.read_text(encoding="utf-8")
            assert "gcloud" not in content, f"{yaml_file.name} must not reference gcloud"
            assert "googleapis.com" not in content, f"{yaml_file.name} must not reference googleapis"


class TestKindClusterNameDeterministic:
    """Verify Kind cluster name and context are deterministic."""

    def test_setup_uses_atlasops_local_cluster(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        assert KIND_CLUSTER_NAME in content

    def test_setup_uses_kind_context(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        # Accept either literal or shell variable interpolation
        assert KUBE_CONTEXT in content or \
               'KUBE_CONTEXT="kind-${KIND_CLUSTER_NAME}"' in content or \
               'kind-${KIND_CLUSTER_NAME}' in content

    def test_teardown_uses_same_cluster_name(self) -> None:
        content = (INFRA_LOCAL / "teardown_local.sh").read_text(encoding="utf-8")
        assert KIND_CLUSTER_NAME in content

    def test_kind_config_exists(self) -> None:
        assert (INFRA_LOCAL / "kind-config.yaml").is_file()


class TestLocalCoordinatorKindLoadable:
    """Verify coordinator uses Kind-loadable image, not a registry."""

    def test_coordinator_manifest_uses_local_image(self) -> None:
        content = (INFRA_LOCAL / "coordinator-local.yaml").read_text(encoding="utf-8")
        assert "atlasops-coordinator:g3-local" in content

    def test_coordinator_manifest_image_pull_never(self) -> None:
        content = (INFRA_LOCAL / "coordinator-local.yaml").read_text(encoding="utf-8")
        assert "imagePullPolicy: Never" in content or "imagePullPolicy: IfNotPresent" in content

    def test_setup_uses_kind_load(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        assert "kind load docker-image" in content


class TestNoRegistryRequired:
    """Verify no registry push/pull is needed."""

    def test_setup_no_docker_push(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        assert "docker push" not in content

    def test_setup_no_artifact_registry(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        assert "Artifact Registry" not in content
        assert "ARTIFACT_REGISTRY" not in content


class TestNoCloudLoadBalancer:
    """Verify no cloud LoadBalancer is required for core acceptance."""

    def test_coordinator_uses_cluster_ip(self) -> None:
        content = (INFRA_LOCAL / "coordinator-local.yaml").read_text(encoding="utf-8")
        assert "type: ClusterIP" in content

    def test_local_values_no_load_balancer(self) -> None:
        values_dir = INFRA_LOCAL / "values"
        for yaml_file in values_dir.glob("*.yaml"):
            content = yaml_file.read_text(encoding="utf-8")
            assert "LoadBalancer" not in content, \
                f"{yaml_file.name} must not use LoadBalancer type"


class TestExplicitContextTargeting:
    """Verify all local commands target the exact Kind context."""

    def test_setup_defines_kube_context(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        assert f'KUBE_CONTEXT="kind-{KIND_CLUSTER_NAME}"' in content or \
               f"KUBE_CONTEXT=\"kind-${{KIND_CLUSTER_NAME}}\"" in content

    def test_setup_uses_context_in_kubectl(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        assert '--context="$KUBE_CONTEXT"' in content or \
               "--context=$KUBE_CONTEXT" in content

    def test_setup_uses_context_in_helm(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        assert '--kube-context' in content


class TestTeardownTargetsExactCluster:
    """Verify teardown only targets the exact local Kind cluster."""

    def test_teardown_deletes_exact_cluster(self) -> None:
        content = (INFRA_LOCAL / "teardown_local.sh").read_text(encoding="utf-8")
        assert f'kind delete cluster --name "{KIND_CLUSTER_NAME}"' in content or \
               f"kind delete cluster --name \"$KIND_CLUSTER_NAME\"" in content

    def test_teardown_no_gke_commands(self) -> None:
        content = (INFRA_LOCAL / "teardown_local.sh").read_text(encoding="utf-8")
        # Check non-comment lines for GKE references
        for line in content.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            assert "gke" not in stripped.lower(), (
                f"Local teardown must not reference GKE outside comments: {stripped}"
            )


class TestLocalSecretsUncommitted:
    """Verify local secrets remain gitignored and uncommitted."""

    def test_secrets_dir_gitignored(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        # Check that secrets/ or *.secret patterns exist
        assert "secrets/" in gitignore or "*.secret" in gitignore

    def test_secret_files_pattern_gitignored(self) -> None:
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "*.secret" in gitignore


class TestGKEPathPreserved:
    """Verify the existing GKE/cloud path remains as optional portability code."""

    def test_gke_setup_impl_exists(self) -> None:
        assert (INFRA_GKE / "setup_impl.sh").is_file(), \
            "GKE setup_impl.sh must be preserved"

    def test_gke_teardown_impl_exists(self) -> None:
        assert (INFRA_GKE / "teardown_impl.sh").is_file(), \
            "GKE teardown_impl.sh must be preserved"

    def test_gke_coordinator_template_exists(self) -> None:
        assert (INFRA_GKE / "kubernetes" / "coordinator.yaml.tmpl").is_file(), \
            "GKE coordinator template must be preserved"

    def test_gke_values_preserved(self) -> None:
        gke_values = INFRA_GKE / "values"
        assert (gke_values / "argocd.yaml").is_file()
        assert (gke_values / "kube-prometheus-stack.yaml").is_file()
        assert (gke_values / "jaeger.yaml").is_file()
        assert (gke_values / "chaos-mesh.yaml").is_file()


class TestPipelineV11Governance:
    """Verify v1.1 status mapping is correctly represented."""

    def test_local_setup_mentions_pipeline_v11(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        assert "v1.1" in content or "Free-First" in content or "Pipeline" in content

    def test_local_setup_states_zero_cost(self) -> None:
        content = (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")
        assert "$0" in content or "zero" in content.lower()

    def test_coordinator_manifest_no_gcp_secrets(self) -> None:
        content = (INFRA_LOCAL / "coordinator-local.yaml").read_text(encoding="utf-8")
        assert "google-credentials" not in content.lower()
        assert "gcp-key" not in content.lower()


class TestLocalInfraStructure:
    """Verify the local infra directory structure is complete."""

    def test_kind_config_exists(self) -> None:
        assert (INFRA_LOCAL / "kind-config.yaml").is_file()

    def test_setup_local_exists(self) -> None:
        assert (INFRA_LOCAL / "setup_local.sh").is_file()

    def test_teardown_local_exists(self) -> None:
        assert (INFRA_LOCAL / "teardown_local.sh").is_file()

    def test_coordinator_local_exists(self) -> None:
        assert (INFRA_LOCAL / "coordinator-local.yaml").is_file()

    def test_prometheus_values_exist(self) -> None:
        assert (INFRA_LOCAL / "values" / "prometheus-local.yaml").is_file()

    def test_jaeger_values_exist(self) -> None:
        assert (INFRA_LOCAL / "values" / "jaeger-local.yaml").is_file()

    def test_argocd_values_exist(self) -> None:
        assert (INFRA_LOCAL / "values" / "argocd-local.yaml").is_file()

    def test_chaos_mesh_values_exist(self) -> None:
        assert (INFRA_LOCAL / "values" / "chaos-mesh-local.yaml").is_file()


class TestCoordinatorManifestSecurity:
    """Verify coordinator local manifest maintains security contract."""

    def test_runs_as_non_root(self) -> None:
        content = (INFRA_LOCAL / "coordinator-local.yaml").read_text(encoding="utf-8")
        assert "runAsNonRoot: true" in content

    def test_runs_as_uid_10001(self) -> None:
        content = (INFRA_LOCAL / "coordinator-local.yaml").read_text(encoding="utf-8")
        assert "runAsUser: 10001" in content

    def test_read_only_root_filesystem(self) -> None:
        content = (INFRA_LOCAL / "coordinator-local.yaml").read_text(encoding="utf-8")
        assert "readOnlyRootFilesystem: true" in content

    def test_drops_all_capabilities(self) -> None:
        content = (INFRA_LOCAL / "coordinator-local.yaml").read_text(encoding="utf-8")
        assert 'drop: ["ALL"]' in content or "drop:\n" in content

    def test_port_9099(self) -> None:
        content = (INFRA_LOCAL / "coordinator-local.yaml").read_text(encoding="utf-8")
        assert "containerPort: 9099" in content


# =============================================================================
# Jaeger trace ingestion contract (Stage 3 item B)
#
# Root cause of historically empty traces: the pinned Online Boutique v0.10.0
# release manifests ship with tracing disabled (no ENABLE_TRACING), and no OTLP
# endpoint was referenced anywhere in the deployment path. Chart 4.x renders
# the Jaeger v2 all-in-one whose default config already listens on OTLP
# 4317/4318, so only producer-side changes were needed.
#
# These checks are static/offline only (yaml.safe_load + text assertions).
# =============================================================================

JAEGER_LOCAL_VALUES = INFRA_LOCAL / "values" / "jaeger-local.yaml"
OTLP_GRPC_ENDPOINT = "jaeger.jaeger.svc.cluster.local:4317"

# Services verified against pinned boutique sources at commit 98e60f5e to honor
# ENABLE_TRACING/COLLECTOR_SERVICE_ADDR with real OTLP export.
INSTRUMENTED_BOUTIQUE_SERVICES = {
    "frontend",
    "checkoutservice",
    "currencyservice",
    "emailservice",
}

# Legacy chart-3.x keys that chart 4.12.0 silently ignores; their presence in
# values files would be misleading dead configuration.
LEGACY_JAEGER_KEYS = (
    "provisionDataStore",
    "allInOne",
)


class TestJaegerLocalValuesSchema:
    """Local Jaeger values must contain only chart-4.x-rendered keys."""

    def _load(self) -> dict:
        import yaml

        return yaml.safe_load(JAEGER_LOCAL_VALUES.read_text(encoding="utf-8"))

    def test_values_parse_as_yaml_mapping(self) -> None:
        data = self._load()
        assert isinstance(data, dict)

    def test_no_legacy_chart3_keys(self) -> None:
        content = JAEGER_LOCAL_VALUES.read_text(encoding="utf-8")
        for key in LEGACY_JAEGER_KEYS:
            # allow mentions inside comments explaining the schema history
            code_lines = [
                line for line in content.splitlines()
                if not line.lstrip().startswith("#")
            ]
            joined = "\n".join(code_lines)
            assert f"{key}:" not in joined, f"legacy key '{key}' must not be set"
            assert f"{key}.enabled" not in joined

    def test_resources_render_under_jaeger_component(self) -> None:
        data = self._load()
        assert "jaeger" in data, "chart 4.x renders resources under 'jaeger:'"
        resources = data["jaeger"]["resources"]
        assert resources["requests"]["memory"] == "64Mi"
        assert resources["limits"]["cpu"] == "200m"

    def test_documents_otlp_ingestion_endpoint(self) -> None:
        content = JAEGER_LOCAL_VALUES.read_text(encoding="utf-8")
        assert "4317" in content


class TestBoutiqueTraceExportPatching:
    """setup_local.sh must enable trace export on exactly the right services."""

    def _script(self) -> str:
        return (INFRA_LOCAL / "setup_local.sh").read_text(encoding="utf-8")

    @staticmethod
    def _normalized(script: str) -> str:
        """Strip whitespace and shell-escaped quotes for stable assertions."""
        return script.replace(" ", "").replace('\\"', '"')

    def test_trace_target_list_declares_only_instrumented_services(self) -> None:
        script = self._script()
        block = re.search(
            r"BOUTIQUE_TRACE_TARGETS=\(\n((?:\s+\"[a-z]+\"\n)+)\)", script
        )
        assert block, "BOUTIQUE_TRACE_TARGETS array missing"
        targets = set(re.findall(r'"([a-z]+)"', block.group(1)))
        assert targets == INSTRUMENTED_BOUTIQUE_SERVICES

    def test_patch_sets_enable_tracing_and_collector_addr(self) -> None:
        normalized = self._normalized(self._script())
        assert '{"name":"ENABLE_TRACING","value":"1"}' in normalized
        assert '{"name":"COLLECTOR_SERVICE_ADDR"' in normalized
        assert OTLP_GRPC_ENDPOINT in normalized

    def test_patch_uses_strategic_merge_on_server_container(self) -> None:
        normalized = self._normalized(self._script())
        assert '--type=strategic' in normalized
        assert '{"name":"server","env":[' in normalized

    def test_trace_targets_are_subset_of_boutique_deployments(self) -> None:
        script = self._script()
        all_deployments = set(
            re.findall(r'^\s+"([a-z]+)"$', script.split("BOUTIQUE_DEPLOYMENTS=(")[1].split(")")[0], re.M)
        )
        targets_block = re.search(
            r"BOUTIQUE_TRACE_TARGETS=\(\n((?:\s+\"[a-z]+\"\n)+)\)", script
        ).group(1)
        targets = set(re.findall(r'"([a-z]+)"', targets_block))
        assert targets <= all_deployments

    def test_trace_export_enabled_after_jaeger_deploy(self) -> None:
        """Pods should restart into an already-live OTLP receiver."""
        main_block = self._script().split("main() {")[-1]
        jaeger_pos = main_block.find("deploy_jaeger")
        patch_pos = main_block.find("enable_boutique_trace_export")
        assert jaeger_pos != -1 and patch_pos != -1
        assert patch_pos > jaeger_pos

    def test_jaeger_service_name_matches_chart4x(self) -> None:
        """Chart 4.x renders Service 'jaeger'; legacy 'jaeger-query' is gone."""
        script = self._script()
        assert "svc/jaeger-query" not in script
        assert "svc/jaeger 16686:16686" in script
