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

import pathlib

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


class TestArm64DeviationsAreDocumented:
    """A first --apply on arm64 fails without deviations; they must be recorded.

    The pinned Online Boutique manifest is tuned for amd64 GKE. On arm64
    cartservice is OOMKilled at its 128Mi ceiling and then restarted by a
    one-second gRPC probe. Both are properties of the upstream manifest and the
    host architecture, not of AtlasOps — but a reproduction that silently needs
    undocumented kubectl patches is not a reproduction.
    """

    DOC = pathlib.Path(__file__).resolve().parents[1] / "docs" / "project" / "LOCAL_ARM64_DEVIATIONS.md"

    def test_deviation_record_exists(self) -> None:
        assert self.DOC.exists(), "arm64 deviations must be recorded, not tribal knowledge"

    def test_record_names_the_symptom_and_the_cause(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        assert "OOMKilled" in text
        assert "cartservice" in text
        assert "128Mi" in text, "the ceiling that fails must be stated"

    def test_deviation_is_applied_by_the_script_not_by_hand(self) -> None:
        """`kubectl apply` reverts it every run, so it must live in the script."""
        setup = (
            pathlib.Path(__file__).resolve().parents[1]
            / "infra" / "local" / "setup_local.sh"
        ).read_text(encoding="utf-8")
        assert "apply_arm64_boutique_deviation" in setup
        # Gated on the node's real architecture, never applied blindly.
        assert "nodeInfo.architecture" in setup
        assert 'node_arch" != "arm64"' in setup
        # Applied after the manifest, before the rollout wait.
        body = setup.split("deploy_online_boutique() {")[1].split("\n}")[0]
        assert body.index("apply -f") < body.index("apply_arm64_boutique_deviation")
        assert body.index("apply_arm64_boutique_deviation") < body.index("rollout status")

    def test_record_states_exactly_what_is_changed(self) -> None:
        """The doc must name the before/after values, not just gesture at them."""
        text = self.DOC.read_text(encoding="utf-8")
        assert "apply_arm64_boutique_deviation" in text
        for service in ("cartservice", "currencyservice"):
            assert service in text
        assert "128Mi → 384Mi" in text, "the ceiling change must be explicit"

    def test_record_states_the_effect_on_measurement(self) -> None:
        """A resource ceiling change alters memory-pressure scenarios."""
        text = self.DOC.read_text(encoding="utf-8")
        assert "not directly comparable" in text
        assert "hist-discord-2022" in text or "cs-005" in text

    def test_pinned_boutique_commit_is_unchanged(self) -> None:
        """Deviations are applied post-hoc; the frozen manifest stays frozen."""
        setup = (
            pathlib.Path(__file__).resolve().parents[1]
            / "infra" / "local" / "setup_local.sh"
        ).read_text(encoding="utf-8")
        assert "98e60f5ee0b643cc00bceb71e6efb89617740432" in setup


class TestPrometheusOperatorTlsContract:
    """Disabling admissionWebhooks alone leaves the operator unschedulable.

    kube-prometheus-stack provisions the operator's TLS material through the
    admission-webhook Job. Turning the webhooks off without also turning off
    `prometheusOperator.tls` removes the Job but keeps the Deployment's mount of
    secret `prometheus-kube-prometheus-admission`, so the pod sits in
    ContainerCreating with FailedMount until the Helm --wait times out. The
    failure surfaces ten minutes later as an opaque install timeout.
    """

    VALUES = (
        pathlib.Path(__file__).resolve().parents[1]
        / "infra" / "local" / "values" / "prometheus-local.yaml"
    )

    def _operator_block(self) -> dict:
        import yaml

        parsed = yaml.safe_load(self.VALUES.read_text(encoding="utf-8"))
        return parsed.get("prometheusOperator") or {}

    def test_admission_webhooks_are_disabled(self) -> None:
        assert self._operator_block().get("admissionWebhooks", {}).get("enabled") is False

    def test_operator_tls_is_disabled_alongside_them(self) -> None:
        operator = self._operator_block()
        assert operator.get("tls", {}).get("enabled") is False, (
            "prometheusOperator.tls must be disabled whenever admissionWebhooks is; "
            "otherwise the operator mounts a secret nothing creates"
        )

    def test_the_pairing_is_explained_in_the_values_file(self) -> None:
        """The next person to touch this must know why both flags are here."""
        text = self.VALUES.read_text(encoding="utf-8")
        assert "prometheus-kube-prometheus-admission" in text


class TestPreflightCatchesEnvironmentFaultsEarly:
    """Provisioning must fail in seconds on a bad interpreter, not ten minutes in.

    The Argo CD step derives a bcrypt hash. When PYTHON_BIN resolved to an
    interpreter without bcrypt, the run died only after the Kind cluster, all 12
    Boutique deployments, Prometheus and Jaeger already existed — leaving a
    half-built cluster and an error that named a Python module rather than the
    thing the operator had to change.
    """

    SETUP = (
        pathlib.Path(__file__).resolve().parents[1]
        / "infra" / "local" / "setup_local.sh"
    )

    def test_bcrypt_is_checked_before_any_cluster_mutation(self) -> None:
        text = self.SETUP.read_text(encoding="utf-8")
        assert "import bcrypt" in text, "preflight must probe bcrypt"

        # The probe must sit in validate_prerequisites, which runs before the
        # first mutating step.
        preflight = text.split("validate_prerequisites()")[1].split("\nvalidate_local_secrets()")[0]
        assert "import bcrypt" in preflight

    def test_bcrypt_failure_names_the_remedy(self) -> None:
        text = self.SETUP.read_text(encoding="utf-8")
        assert "PYTHON_BIN=" in text, "the error must show how to fix it"

    def test_daemon_check_does_not_assume_docker_desktop(self) -> None:
        """A headless daemon is a first-class option, not a workaround."""
        text = self.SETUP.read_text(encoding="utf-8")
        assert "colima" in text.lower()
        assert "Start Docker Desktop first." not in text

    def test_script_remains_syntactically_valid(self) -> None:
        import shutil
        import subprocess

        bash = shutil.which("bash")
        assert bash is not None
        result = subprocess.run([bash, "-n", str(self.SETUP)], capture_output=True, check=False)
        assert result.returncode == 0, result.stderr.decode()
