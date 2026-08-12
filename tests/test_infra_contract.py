"""Static Stage 1D-A infrastructure safety-contract regression tests.

These tests read tracked text only. They must never execute setup, teardown,
gcloud, kubectl, Helm, or any network operation.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


SETUP_ENTRY = read("infra/setup.sh")
SETUP_IMPL = read("infra/setup_impl.sh")
SETUP = SETUP_ENTRY + "\n" + SETUP_IMPL
TEARDOWN_ENTRY = read("infra/teardown.sh")
TEARDOWN_IMPL = read("infra/teardown_impl.sh")
TEARDOWN = TEARDOWN_ENTRY + "\n" + TEARDOWN_IMPL
SHELL = SETUP + "\n" + TEARDOWN


def shell_commands(source: str) -> list[str]:
    """Join shell continuation lines without depending on harmless spacing."""
    commands: list[str] = []
    current = ""
    for line in source.splitlines():
        stripped = line.strip()
        current = f"{current} {stripped}".strip() if current else stripped
        if current.endswith("\\"):
            current = current[:-1].rstrip()
        else:
            commands.append(current)
            current = ""
    if current:
        commands.append(current)
    return commands


def test_entry_points_delegate_only_to_reviewed_implementations() -> None:
    assert 'exec bash "${SCRIPT_DIR}/setup_impl.sh" "$@"' in SETUP_ENTRY
    assert 'exec bash "${SCRIPT_DIR}/teardown_impl.sh" "$@"' in TEARDOWN_ENTRY


def test_no_global_gcloud_project_mutation() -> None:
    assert not re.search(r"\bgcloud\s+config\s+set\s+project\b", SHELL)


def test_setup_requires_explicit_mode_and_cost_acknowledgement() -> None:
    assert "--check|--apply" in SETUP_IMPL
    assert "An explicit --check or --apply mode is required" in SETUP_IMPL
    assert "Unknown option" in SETUP_IMPL
    assert "ATLASOPS_COST_ACK" in SETUP_IMPL
    assert "I_UNDERSTAND_GCP_COSTS" in SETUP_IMPL
    main = re.search(r"main\(\) \{(?P<body>.*?)\n\}", SETUP_IMPL, re.DOTALL)
    assert main
    body = main.group("body")
    assert body.index("print_summary") < body.index("apply_foundation")
    assert re.search(r'if \[\[ "\$MODE" == --check \]\]; then.*?return', body, re.DOTALL)


def test_teardown_requires_explicit_mode_and_destructive_acknowledgement() -> None:
    assert "--check|--apply" in TEARDOWN_IMPL
    assert "An explicit --check or --apply mode is required" in TEARDOWN_IMPL
    assert "ATLASOPS_TEARDOWN_ACK" in TEARDOWN_IMPL
    assert "DELETE_ATLASOPS_DEVELOPMENT_RESOURCES" in TEARDOWN_IMPL
    main = re.search(r"main\(\) \{(?P<body>.*?)\n\}", TEARDOWN_IMPL, re.DOTALL)
    assert main
    body = main.group("body")
    assert body.index("print_summary") < body.index("gcloud container clusters delete")
    assert re.search(r'if \[\[ "\$MODE" == --check \]\]; then.*?return', body, re.DOTALL)


def test_linkerd_remote_installer_and_install_path_are_absent() -> None:
    assert "run.linkerd.io" not in SHELL
    assert not re.search(r"curl\b[^\n|]*\|\s*(?:ba)?sh\b", SHELL)
    assert not re.search(r"\blinkerd\s+(?:install|check)\b", SHELL)
    assert "LINKERD: SKIPPED / DEFERRED" in SETUP_IMPL


def test_cloud_sql_is_deferred_and_invalid_identifier_is_gone() -> None:
    assert "AtlasOps-cart-db" not in SHELL
    assert "gcloud sql" not in SHELL
    contract = read("docs/project/INFRASTRUCTURE_CONTRACT.md")
    assert "`atlasops-cart-db`" in contract


def test_premature_uppercase_kubernetes_identifiers_are_absent() -> None:
    for invalid in (
        "alertmanager-AtlasOps-config",
        "AtlasOps-coordinator-svc",
    ):
        assert invalid not in SHELL
        assert invalid not in read("infra/values/kube-prometheus-stack.yaml")
    assert not re.search(r"metadata:\s*\n\s+name:\s*[^\n]*[A-Z]", SHELL)


def test_base_prometheus_values_do_not_require_premature_routing() -> None:
    values = read("infra/values/kube-prometheus-stack.yaml")
    assert "configSecret:" not in values
    assert "webhook_configs:" not in values
    assert "additionalScrapeConfigs:" not in values
    assert "Stage 1D-B" in values


def test_every_installed_helm_chart_has_an_explicit_version() -> None:
    installs = [command for command in shell_commands(SETUP_IMPL) if "helm upgrade --install" in command]
    assert len(installs) == 3
    assert all(re.search(r"\s--version\s+\"?\$[A-Z_]+_CHART_VERSION\"?", command) for command in installs)
    assert 'PROMETHEUS_CHART_VERSION="88.3.0"' in SETUP_IMPL
    assert 'JAEGER_CHART_VERSION="4.12.0"' in SETUP_IMPL
    assert 'ARGOCD_CHART_VERSION="10.3.2"' in SETUP_IMPL
    assert 'CHAOS_MESH_CHART_VERSION="2.8.3"' in SETUP_IMPL
    assert not any("jaegertracing/jaeger" in command for command in installs)


def test_online_boutique_manifest_uses_immutable_commit() -> None:
    commit = re.search(r'BOUTIQUE_COMMIT="([0-9a-f]{40})"', SETUP_IMPL)
    assert commit
    assert 'BOUTIQUE_RELEASE="v0.10.0"' in SETUP_IMPL
    assert "raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/${BOUTIQUE_COMMIT}/" in SETUP_IMPL
    assert "refs/tags" not in SETUP_IMPL


def test_optional_components_default_disabled_and_apis_are_gated() -> None:
    flags = (
        "ATLASOPS_ENABLE_CLOUD_SQL",
        "ATLASOPS_ENABLE_PUBSUB",
        "ATLASOPS_ENABLE_ARTIFACT_REGISTRY",
        "ATLASOPS_ENABLE_CLOUD_BUILD",
    )
    for flag in flags:
        assert f'{flag}="${{{flag}:-false}}"' in SETUP_IMPL
        assert f'{flag}="${{{flag}:-false}}"' in TEARDOWN_IMPL
    assert "sqladmin.googleapis.com" not in SETUP_IMPL
    assert "artifactregistry.googleapis.com" not in SETUP_IMPL
    assert "cloudbuild.googleapis.com" not in SETUP_IMPL
    assert re.search(r'if \[\[ "\$ATLASOPS_ENABLE_PUBSUB" == true \]\]; then.*?pubsub\.googleapis\.com', SETUP_IMPL, re.DOTALL)


def test_pubsub_idempotency_does_not_hide_failures() -> None:
    assert "|| true" not in SHELL
    assert "2>/dev/null" not in SHELL
    assert "gcloud pubsub topics list" in SETUP_IMPL
    assert "gcloud pubsub subscriptions list" in SETUP_IMPL
    assert "actual_topic" in SETUP_IMPL


def test_project_admin_uis_are_cluster_ip() -> None:
    for path in (
        "infra/values/kube-prometheus-stack.yaml",
        "infra/values/jaeger.yaml",
        "infra/values/argocd.yaml",
        "infra/values/chaos-mesh.yaml",
    ):
        values = read(path)
        assert "type: LoadBalancer" not in values
        assert "type: ClusterIP" in values


def test_setup_and_teardown_share_zonal_location_contract() -> None:
    for source in (SETUP_IMPL, TEARDOWN_IMPL):
        assert 'DEFAULT_REGION="us-central1"' in source
        assert 'DEFAULT_CLUSTER="atlasops"' in source
        assert 'ZONE="${ATLASOPS_GKE_ZONE:-${REGION}-a}"' in source
        assert '--zone="$ZONE"' in source
        assert '--region="$REGION"' not in source.split("gcloud container clusters", maxsplit=1)[-1]


def test_existing_cluster_reuse_checks_material_contract() -> None:
    expected = (
        "location",
        "autopilot.enabled",
        "workloadIdentityConfig.workloadPool",
        "masterAuthorizedNetworksConfig.enabled",
        "nodePools.config.machineType",
        "nodePools.config.serviceAccount",
        "nodePools.autoscaling.enabled",
        "nodePools.autoscaling.minNodeCount",
        "nodePools.autoscaling.maxNodeCount",
        "masterAuthorizedNetworksConfig.cidrBlocks.cidrBlock",
        "resourceLabels",
    )
    for field in expected:
        assert field in SETUP_IMPL
    preflight = re.search(r"read_only_preflight\(\) \{(?P<body>.*?)\n\}", SETUP_IMPL, re.DOTALL)
    assert preflight
    assert "inspect_existing_cluster" in preflight.group("body")
    assert "GKE API is disabled" in preflight.group("body")


def test_topology_command_and_summary_agree() -> None:
    assert 'MACHINE_TYPE="e2-standard-4"' in SETUP_IMPL
    assert 'INITIAL_NODES="1"' in SETUP_IMPL
    assert 'MIN_NODES="1"' in SETUP_IMPL
    assert 'MAX_NODES="3"' in SETUP_IMPL
    for flag in ("--num-nodes", "--enable-autoscaling", "--min-nodes", "--max-nodes"):
        assert flag in SETUP_IMPL
    assert "1 initial node, autoscaling 1-3" in SETUP_IMPL


def test_stage_output_does_not_claim_full_atlasops_ready() -> None:
    assert "AtlasOps Infrastructure Ready" not in SHELL
    assert "FULL ATLASOPS READY" not in SHELL
    assert "FOUNDATION PROVISIONED" in SETUP_IMPL
    assert "FULL ATLASOPS NOT READY" in SETUP_IMPL


def test_static_status_documents_keep_live_state_unverified() -> None:
    contract = read("docs/project/INFRASTRUCTURE_CONTRACT.md")
    status = read("docs/project/IMPLEMENTATION_STATUS.md")
    assert "DO NOT RUN LIVE GKE UNTIL STAGE 1D-B IS COMPLETE" in contract
    assert "REPAIRED / STATICALLY VALIDATED" in status
    assert "Real GKE provisioning | UNVERIFIED" in status
    assert "Observability wiring | INCOMPLETE" in status
    assert "Environment verifier | NOT YET IMPLEMENTED" in status
