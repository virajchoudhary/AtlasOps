"""Static/mocked Stage 1D-B runtime and observability contract tests.

No test in this module may contact Kubernetes, Helm, cloud services, model
endpoints, Alertmanager, Prometheus, Jaeger, or Argo CD.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock

import yaml
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


SETUP = read("infra/setup_impl.sh")
TEMPLATE = read("infra/kubernetes/coordinator.yaml.tmpl")
PROM_VALUES = read("infra/values/kube-prometheus-stack.yaml")
PROM_RULES = read("infra/kubernetes/atlasops-prometheus-rules.yaml")
MAKEFILE = read("Makefile")
DOCS = "\n".join(
    read(path)
    for path in (
        "DEPLOYMENT.md",
        "docs/project/INFRASTRUCTURE_CONTRACT.md",
        "docs/project/IMPLEMENTATION_STATUS.md",
    )
)


def rendered_coordinator_documents() -> list[dict]:
    rendered = (
        TEMPLATE.replace(
            "__ATLASOPS_COORDINATOR_IMAGE__",
            "registry.example/atlasops/coordinator@sha256:" + "a" * 64,
        )
        .replace("__ATLASOPS_BACKEND__", "vllm")
        .replace("__ATLASOPS_VLLM_BASE__", "http://model.default.svc.cluster.local:8000/v1")
        .replace("__ATLASOPS_AGENT_MODEL__", "example/model")
        .replace("__ATLASOPS_GCP_PROJECT__", "atlasops-project")
    )
    assert "__ATLASOPS_" not in rendered
    return [doc for doc in yaml.safe_load_all(rendered) if doc]


def document(kind: str, name: str) -> dict:
    for item in rendered_coordinator_documents():
        if item["kind"] == kind and item["metadata"]["name"] == name:
            return item
    raise AssertionError(f"missing {kind}/{name}")


def test_dedicated_coordinator_container_contract() -> None:
    dockerfile = read("Dockerfile.coordinator")
    assert 'CMD ["python", "-m", "agents.coordinator"]' in dockerfile
    assert "EXPOSE 9099" in dockerfile
    assert "/healthz" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "KUBECTL_VERSION=v1.31.10" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert "app.py" not in dockerfile
    assert "7860" not in dockerfile


def test_coordinator_service_and_deployment_are_private_and_canonical() -> None:
    deployment = document("Deployment", "atlasops-coordinator")
    service = document("Service", "atlasops-coordinator-svc")
    assert deployment["metadata"]["namespace"] == "default"
    assert service["metadata"]["namespace"] == "default"
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 9099, "targetPort": "http", "protocol": "TCP"}
    ]
    assert "LoadBalancer" not in TEMPLATE
    assert "NodePort" not in TEMPLATE


def test_coordinator_pod_security_resources_and_probes() -> None:
    deployment = document("Deployment", "atlasops-coordinator")
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    assert pod_spec["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "privileged": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert container["readinessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert container["livenessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert container["resources"]["requests"] == {"cpu": "100m", "memory": "256Mi"}
    assert container["resources"]["limits"] == {"cpu": "1", "memory": "1Gi"}
    assert "hostPath" not in TEMPLATE
    assert "hostPID" not in TEMPLATE


def test_runtime_configuration_uses_secret_references_without_secret_values() -> None:
    docs = rendered_coordinator_documents()
    assert all(item["kind"] != "Secret" for item in docs)
    deployment = document("Deployment", "atlasops-coordinator")
    env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    refs = {
        item["name"]: item["valueFrom"]["secretKeyRef"]
        for item in env
    }
    assert refs["ATLASOPS_AUDIT_SECRET"]["key"] == "atlasops-audit-secret"
    assert refs["ALERTMANAGER_WEBHOOK_SECRET"]["key"] == "alertmanager-webhook-secret"
    assert refs["ATLASOPS_API_KEY"]["key"] == "atlasops-api-key"
    assert refs["LLM_API_KEY"]["optional"] is True
    assert refs["ARGOCD_USER"]["key"] == "argocd-user"
    assert refs["ARGOCD_USER"].get("optional") is not True
    assert refs["ARGOCD_PASS"]["key"] == "argocd-pass"
    assert refs["ARGOCD_PASS"].get("optional") is not True
    assert "stringData:" not in TEMPLATE
    config = document("ConfigMap", "atlasops-coordinator-config")["data"]
    assert config["ATLASOPS_RUNTIME_DATA_DIR"].startswith("/var/lib/atlasops/")
    assert config["POSTMORTEM_DIR"].startswith("/var/lib/atlasops/")
    assert config["TRAJECTORIES_DIR"].startswith("/var/lib/atlasops/")
    assert config["JAEGER_URL"] == "http://jaeger.jaeger.svc.cluster.local:16686"
    assert config["ARGOCD_URL"] == "http://argocd-server.argocd.svc.cluster.local:80"
    assert config["ARGOCD_VERIFY_TLS"] == "false"


def test_rbac_is_bounded_and_never_grants_secret_or_cluster_admin_access() -> None:
    docs = rendered_coordinator_documents()
    roles = [item for item in docs if item["kind"] in {"Role", "ClusterRole"}]
    assert roles
    for role in roles:
        for rule in role["rules"]:
            assert "*" not in rule.get("resources", [])
            assert "*" not in rule.get("verbs", [])
            assert "secrets" not in rule.get("resources", [])
    assert "cluster-admin" not in TEMPLATE
    remediation = document("Role", "atlasops-coordinator-remediation")
    assert remediation["metadata"]["namespace"] == "default"
    assert remediation["rules"][0]["resources"] == ["deployments", "deployments/scale"]


def test_immutable_operator_image_is_required_and_rendered_before_apply() -> None:
    assert "ATLASOPS_COORDINATOR_IMAGE" in SETUP
    assert "@sha256:[a-f0-9]{64}" in SETUP
    assert "ATLASOPS_COORDINATOR_IMAGE must be" in SETUP
    assert "render_coordinator_manifest" in SETUP
    assert "Coordinator manifest rendering left unresolved placeholders" in SETUP
    assert SETUP.index("render_coordinator_manifest") < SETUP.index(
        'kubectl_target apply -f "$RENDERED_COORDINATOR_MANIFEST"'
    )
    combined = TEMPLATE + SETUP + DOCS
    assert "ghcr.io/harikishanth/atlasops:latest" not in combined
    assert not re.search(r"image:\s*[^\n]*:latest(?:\s|$)", TEMPLATE)


def test_runtime_secrets_fail_before_boutique_or_runtime_mutation() -> None:
    foundation = re.search(
        r"apply_foundation\(\) \{(?P<body>.*?)\n\}",
        SETUP,
        re.MULTILINE | re.DOTALL,
    )
    assert foundation
    body = foundation.group("body")
    assert body.index("initialize_cluster_access") < body.index("validate_runtime_secret_contract")
    assert body.index("validate_runtime_secret_contract") < body.index(
        'kubectl_target apply -f "$BOUTIQUE_MANIFEST"'
    )


def test_alertmanager_route_and_secret_file_match_coordinator_exactly() -> None:
    assert "http://atlasops-coordinator-svc.default.svc.cluster.local:9099/webhook" in PROM_VALUES
    assert "atlasops_route=\"coordinator\"" in PROM_VALUES
    assert "credentials_file:" in PROM_VALUES
    assert "/etc/alertmanager/secrets/atlasops-alertmanager-webhook/alertmanager-webhook-secret" in PROM_VALUES
    assert "credentials:" not in PROM_VALUES
    assert "AtlasOps-coordinator" not in PROM_VALUES


def test_prometheus_rule_uses_only_kube_state_metrics_availability() -> None:
    rules = list(yaml.safe_load_all(PROM_RULES))
    assert len(rules) == 1
    rule = rules[0]
    assert rule["kind"] == "PrometheusRule"
    alert = rule["spec"]["groups"][0]["rules"][0]
    assert alert["alert"] == "AtlasOpsOnlineBoutiqueDeploymentUnavailable"
    assert "kube_deployment_status_replicas_available" in alert["expr"]
    assert "kube_deployment_spec_replicas" in alert["expr"]
    assert alert["labels"]["atlasops_route"] == "coordinator"
    forbidden = ("http_request", "5xx", "latency", "histogram_quantile")
    assert not any(value in alert["expr"].lower() for value in forbidden)
    assert "additionalScrapeConfigs" not in PROM_VALUES


def test_jaeger_and_argocd_states_are_fail_closed_and_honest() -> None:
    jaeger_vals = yaml.safe_load(read("infra/values/jaeger.yaml"))
    assert jaeger_vals["jaeger"]["service"]["type"] == "ClusterIP"
    assert "JAEGER: in-cluster Query backend" in SETUP
    assert 'ATLASOPS_ENABLE_ARGOCD="${ATLASOPS_ENABLE_ARGOCD:-true}"' in SETUP
    assert "kind: Application" not in "\n".join(
        read(path) for path in ("infra/values/argocd.yaml", "infra/setup_impl.sh")
    )
    assert "application error-rate or latency metrics" in DOCS
    assert "no Application" in DOCS


def test_mutating_make_targets_never_use_implicit_current_context() -> None:
    assert "KUBE_CONTEXT ?= gke_$(PROJECT)_$(ZONE)_$(CLUSTER)" in MAKEFILE
    for target in ("chaos", "chaos-reset", "replay-%"):
        match = re.search(
            rf"^{re.escape(target)}:.*?(?=^\S|\Z)",
            MAKEFILE,
            re.MULTILINE | re.DOTALL,
        )
        assert match, target
        body = match.group(0)
        assert "require-kube-context" in body
        assert '--context="$(KUBE_CONTEXT)"' in body
    assert "gcloud config set" not in SETUP
    assert "RUNTIME_KUBECONFIG=\"$(mktemp)\"" in SETUP
    assert 'kubectl --context="$KUBE_CONTEXT"' in SETUP
    assert 'helm --kube-context "$KUBE_CONTEXT"' in SETUP


def test_healthz_is_side_effect_free_without_runtime_secrets(monkeypatch) -> None:
    monkeypatch.delenv("ATLASOPS_AUDIT_SECRET", raising=False)
    import agents.coordinator as coordinator

    client = TestClient(coordinator.app)
    assert client.get("/healthz").json() == {"status": "ok"}


def test_coordinator_webhook_requires_and_accepts_bearer_secret(monkeypatch) -> None:
    import agents.coordinator as coordinator

    monkeypatch.setattr(coordinator, "_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setattr(coordinator.correlator, "ingest", lambda payload: ("inc-test", False, False))
    monkeypatch.setattr(coordinator, "handle_incident", AsyncMock())
    client = TestClient(coordinator.app)
    payload = {"alerts": [], "commonLabels": {}, "status": "firing"}
    assert client.post("/webhook", json=payload).status_code == 401
    response = client.post(
        "/webhook",
        json=payload,
        headers={"Authorization": "Bearer test-webhook-secret"},
    )
    assert response.status_code == 200
    assert response.json()["dispatched"] is False
    coordinator.handle_incident.assert_not_awaited()


def test_dedicated_runtime_approval_endpoints_require_api_key(monkeypatch) -> None:
    import agents.coordinator as coordinator

    monkeypatch.setattr(coordinator, "_RUNTIME_API_KEY", "test-operator-key")
    request = coordinator.approval_gate.request("inc-runtime-approval", "P1", "review")
    client = TestClient(coordinator.app)
    payload = {
        "token": request.token,
        "decision": "approved",
        "approved_by": "test-operator",
    }
    assert client.get("/approval/pending").status_code == 401
    assert client.post("/approve", json=payload).status_code == 401
    assert request.decision == "pending"

    headers = {"X-AtlasOps-Key": "test-operator-key"}
    response = client.post(
        "/approve",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    pending = client.get("/approval/pending", headers=headers).json()["pending"]
    assert any(
        item["incident_id"] == "inc-runtime-approval" and item["decision"] == "approved"
        for item in pending
    )
    decision = asyncio.run(coordinator.approval_gate.wait_for_decision("inc-runtime-approval"))
    assert decision["status"] == "approved"


def test_comms_runtime_paths_can_live_on_writable_volume(monkeypatch, tmp_path) -> None:
    import agents.tools.comms as comms

    runtime = tmp_path / "runtime"
    monkeypatch.setattr(comms, "_LOG_PATH", runtime / "comms" / "slack_posts.jsonl")
    monkeypatch.setattr(comms, "POSTMORTEM_DIR", runtime / "postmortems")
    monkeypatch.setattr(comms, "SLACK_WEBHOOK", "")
    monkeypatch.setattr(comms, "DISCORD_WEBHOOK", "")
    assert comms.slack_post_update("#incidents", "P2", "Test", "Summary")["success"]
    result = comms.postmortem_draft({"title": "Runtime test"})
    assert Path(result["path"]).is_file()
    assert (runtime / "comms" / "slack_posts.jsonl").is_file()


def test_argo_credential_validation_and_transport_contract_in_setup() -> None:
    assert 'for key in argocd-user argocd-pass; do' in SETUP
    assert 'missing required Argo CD credential key' in SETUP
    assert 'argocd-initial-admin-secret' not in SETUP
    assert 'argocd-initial-admin-secret' not in TEMPLATE
    assert 'argocd-initial-admin-secret' not in read("infra/values/argocd.yaml")
    # Verify setup summary states failure if Argo disabled
    assert "DEVIATION: canonical Gate G3 cannot PASS without Argo CD" in SETUP
