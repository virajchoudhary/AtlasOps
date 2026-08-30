"""Static Stage 1D-A/B infrastructure safety-contract regression tests.

These tests read tracked text only. They must never execute setup, teardown,
gcloud, kubectl, Helm, or any network operation.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


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
MAKEFILE = read("Makefile")


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


def shell_function_body(source: str, name: str) -> str:
    match = re.search(
        rf"^{re.escape(name)}\(\) \{{\n(?P<body>.*?)^\}}",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"shell function {name!r} was not found"
    return match.group("body")


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


def test_prometheus_values_use_authenticated_coordinator_routing() -> None:
    values = read("infra/values/kube-prometheus-stack.yaml")
    assert "configSecret:" not in values
    assert "additionalScrapeConfigs:" not in values
    assert "atlasops-coordinator-svc.default.svc.cluster.local:9099/webhook" in values
    assert "credentials_file:" in values
    assert "atlasops-alertmanager-webhook" in values
    assert "credentials:" not in values


def test_every_installed_helm_chart_has_an_explicit_version() -> None:
    installs = [command for command in shell_commands(SETUP_IMPL) if "helm_target upgrade --install" in command]
    assert len(installs) == 4
    assert all(re.search(r"\s--version\s+\"?\$[A-Z_]+_CHART_VERSION\"?", command) for command in installs)
    assert 'PROMETHEUS_CHART_VERSION="88.3.0"' in SETUP_IMPL
    assert 'JAEGER_CHART_VERSION="4.12.0"' in SETUP_IMPL
    assert 'ARGOCD_CHART_VERSION="10.3.2"' in SETUP_IMPL
    assert 'CHAOS_MESH_CHART_VERSION="2.8.3"' in SETUP_IMPL
    assert any("jaegertracing/jaeger" in command for command in installs)


def test_online_boutique_manifest_uses_immutable_commit() -> None:
    commit = re.search(r'BOUTIQUE_COMMIT="([0-9a-f]{40})"', SETUP_IMPL)
    assert commit
    assert 'BOUTIQUE_RELEASE="v0.10.0"' in SETUP_IMPL
    assert "raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/${BOUTIQUE_COMMIT}/" in SETUP_IMPL
    assert "refs/tags" not in SETUP_IMPL


def test_make_lifecycle_targets_require_an_explicit_project() -> None:
    active_entry_points = MAKEFILE + "\n" + SHELL
    assert "PROJECT ?= cloudsre-v3-amd" not in active_entry_points
    assert "cloudsre-v3-amd" not in active_entry_points
    assert re.search(r'^require-project:\s*$', MAKEFILE, re.MULTILINE)
    assert 'ERROR: PROJECT is required. Pass PROJECT=<gcp-project-id>.' in MAKEFILE
    assert '$(strip $(PROJECT))' in MAKEFILE
    for target in ("infra-check", "teardown-check", "up", "down"):
        assert re.search(rf"^{re.escape(target)}:\s+require-project\s*$", MAKEFILE, re.MULTILINE)
    assert re.search(r"^status:\s+require-kube-context\s*$", MAKEFILE, re.MULTILINE)


def test_online_boutique_waits_for_every_pinned_deployment() -> None:
    expected = (
        "currencyservice",
        "loadgenerator",
        "productcatalogservice",
        "checkoutservice",
        "shippingservice",
        "cartservice",
        "redis-cart",
        "emailservice",
        "paymentservice",
        "frontend",
        "recommendationservice",
        "adservice",
    )
    declaration = re.search(
        r"readonly BOUTIQUE_DEPLOYMENTS=\((?P<deployments>.*?)\n\)",
        SETUP_IMPL,
        re.DOTALL,
    )
    assert declaration
    declared = tuple(re.findall(r'^\s+"([a-z0-9-]+)"\s*$', declaration.group("deployments"), re.MULTILINE))
    assert declared == expected
    foundation = shell_function_body(SETUP_IMPL, "apply_foundation")
    assert 'for deployment in "${BOUTIQUE_DEPLOYMENTS[@]}"; do' in foundation
    rollout = 'kubectl_target rollout status "deployment/$deployment" --namespace=default --timeout="$BOUTIQUE_ROLLOUT_TIMEOUT"'
    assert rollout in foundation
    assert "kubectl wait --for=condition=ready pod -l app=frontend" not in foundation
    assert "|| true" not in foundation
    assert foundation.index(rollout) < foundation.index("helm_target upgrade --install prometheus")


def test_foundation_success_requires_boutique_and_subsequent_core_setup() -> None:
    assert "set -euo pipefail" in SETUP_IMPL
    foundation = shell_function_body(SETUP_IMPL, "apply_foundation")
    rollout = 'kubectl_target rollout status "deployment/$deployment"'
    prom = "helm_target upgrade --install prometheus"
    jaeger = "helm_target upgrade --install jaeger"
    argo = "helm_target upgrade --install argocd"
    chaos = "helm_target upgrade --install chaos-mesh"
    assert foundation.index(rollout) < foundation.index(prom)
    assert foundation.index(prom) < foundation.index(jaeger)
    assert foundation.index(jaeger) < foundation.index(argo)
    assert foundation.index(argo) < foundation.index(chaos)
    main = shell_function_body(SETUP_IMPL, "main")
    assert main.index("apply_foundation") < main.index("CORE RUNTIME WIRED")


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
    assert 'ATLASOPS_ENABLE_ARGOCD="${ATLASOPS_ENABLE_ARGOCD:-true}"' in SETUP_IMPL
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
    assert "CORE RUNTIME WIRED" in SETUP_IMPL
    assert "LIVE VALIDATION STILL REQUIRED" in SETUP_IMPL


def test_status_documents_do_not_overclaim_unverified_state() -> None:
    """Live status may only be claimed where live evidence exists.

    This originally asserted that everything stayed LIVE UNVERIFIED, which was
    correct while nothing had run. Gate G4 passed on a local Kind cluster
    (`EXP-STAGE4-SF002-010`), so the claims that changed are exactly those the
    run covers; the ones it does not cover must stay unverified.
    """
    contract = read("docs/project/INFRASTRUCTURE_CONTRACT.md")
    status = read("docs/project/IMPLEMENTATION_STATUS.md")

    # The GKE path was never exercised and must not inherit the local result.
    assert "STATICALLY WIRED / LIVE UNVERIFIED" in contract
    assert "Real GKE provisioning | UNVERIFIED" in status

    # Live claims must cite the run that justifies them.
    assert "Gate G4 golden incident | **PASS**" in status
    assert "EXP-STAGE4-SF002-010" in status

    # Trace ingestion was never proven, only Jaeger's API reachability.
    assert "TRACE INGESTION UNVERIFIED" in status

    # Benchmark results remain unreproduced regardless of the gate result.
    assert "Published benchmark/result claims | UNVERIFIED BY OUR TEAM" in status


def test_jaeger_and_argocd_helm_values_render_statically() -> None:
    jaeger_raw = read("infra/values/jaeger.yaml")
    jaeger_val = yaml.safe_load(jaeger_raw)
    assert isinstance(jaeger_val, dict)
    assert jaeger_val["jaeger"]["service"]["type"] == "ClusterIP"
    assert jaeger_val["jaeger"]["resources"]["requests"]["cpu"] == "100m"
    assert jaeger_val["jaeger"]["resources"]["requests"]["memory"] == "256Mi"
    assert jaeger_val["jaeger"]["resources"]["limits"]["cpu"] == "500m"
    assert jaeger_val["jaeger"]["resources"]["limits"]["memory"] == "512Mi"

    argocd_raw = read("infra/values/argocd.yaml")
    argocd_val = yaml.safe_load(argocd_raw)
    assert isinstance(argocd_val, dict)
    assert argocd_val["server"]["service"]["type"] == "ClusterIP"
    assert argocd_val["server"]["extraArgs"] == ["--insecure"]
    assert argocd_val["configs"]["params"]["server.insecure"] is True
    assert argocd_val["configs"]["cm"]["accounts.atlasops"] == "login"
    assert "role:atlasops-readonly" in argocd_val["configs"]["rbac"]["policy.csv"]
    assert argocd_val["notifications"]["enabled"] is False
    assert argocd_val["controller"]["resources"]["requests"]["cpu"] == "250m"
    assert argocd_val["redis"]["resources"]["requests"]["cpu"] == "100m"


def test_g3_acceptance_plan_truth_and_scenarios() -> None:
    from config.runtime import FROZEN_SCENARIOS
    plan = read("docs/project/G3_ACCEPTANCE_PLAN.md")
    assert "SF-001" not in plan
    assert "HIST-001" not in plan
    assert "28" in plan
    for scenario_id in FROZEN_SCENARIOS:
        assert scenario_id in plan


def test_stage_3_operator_guide_and_secret_helper() -> None:
    guide = read("docs/project/STAGE_3_OPERATOR_GUIDE.md")
    assert "Gate 3A" in guide
    assert "Gate 3K" in guide
    assert "gcloud auth login" in guide
    assert "scripts/generate_runtime_secrets.py" in guide
    assert "artifactregistry.googleapis.com" in guide
    assert "docker.pkg.dev" in guide
    assert "LoadBalancer" in guide
    assert "ClusterIP" in guide

    gitignore = read(".gitignore")
    assert "*.secret" in gitignore
    assert "secrets/" in gitignore


def test_activated_virtualenv_outranks_path_ordering_for_python_bin():
    """An activated venv must win over whatever `python3` PATH resolves to.

    A Homebrew python3 ahead of the venv on PATH selected an interpreter without
    bcrypt, so the Argo CD credential preflight aborted a valid --apply run ten
    minutes into provisioning. The operator activating a venv is an explicit
    interpreter choice and must outrank PATH order.
    """
    import pathlib
    import re

    source = pathlib.Path("infra/setup_impl.sh").read_text(encoding="utf-8")
    block = source.split("if [[ -n \"${PYTHON_BIN:-}\" ]]", 1)[1].split("usage()", 1)[0]
    # The VIRTUAL_ENV branch must be evaluated before the bare `python3` lookup.
    venv_at = block.find("VIRTUAL_ENV")
    path_lookup_at = block.find("command -v python3")
    assert venv_at != -1, "setup_impl.sh ignores an activated virtualenv"
    assert venv_at < path_lookup_at, "PATH lookup shadows the activated virtualenv"
    assert re.search(r'-x "\$\{VIRTUAL_ENV\}/bin/python3"', block), (
        "the venv interpreter must be probed for executability before selection"
    )
