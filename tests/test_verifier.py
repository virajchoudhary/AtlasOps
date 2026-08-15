"""Comprehensive tests for the Objective Environment Verifier and Scenario Contract.

Guarantees:
1. Exact (bijective) agreement between SCENARIO_VERIFICATION_SPECS and the actual Chaos Mesh manifests.
2. Exact tier and namespace verification against version-controlled manifest metadata and selectors.
3. Strict fail-closed behavior for unknown frozen scenarios and unresolved dynamic scenarios.
4. 100% offline mocked unit execution with zero network, cloud, or cluster dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from agents.verifier import (
    CheckResult,
    EnvironmentVerificationResult,
    EnvironmentVerifier,
    MetricPredicate,
    ScenarioVerificationSpec,
    WorkloadPredicate,
    SCENARIO_VERIFICATION_SPECS,
    FROZEN_TIER_PREFIXES,
    verify_environment,
)
from config.runtime import FROZEN_SCENARIOS

MANIFESTS_DIR = Path(__file__).parent.parent / "bench" / "chaos_manifests"


# ── Mock Helpers ──────────────────────────────────────────────────────────────

def _mock_kubectl_success(deployments: list[dict]):
    def _kubectl_get(resource: str, namespace: str = "-A", output: str = "json"):
        resource_lower = resource.lower()
        if any(ck in resource_lower for ck in ("podchaos", "networkchaos", "stresschaos", "dnschaos", "iochaos", "timechaos")):
            return {
                "success": True,
                "stdout": json.dumps({"items": []}),
                "parsed": {"items": []},
                "returncode": 0,
            }
        return {
            "success": True,
            "stdout": json.dumps({"items": deployments}),
            "parsed": {"items": deployments},
            "returncode": 0,
        }
    return _kubectl_get


def _mock_kubectl_failure(error_msg: str = "cluster unreachable"):
    def _kubectl_get(resource: str, namespace: str = "-A", output: str = "json"):
        return {
            "success": False,
            "error": error_msg,
            "stderr": error_msg,
            "returncode": 1,
        }
    return _kubectl_get


def _mock_alertmanager_success(alerts: list[dict]):
    def _alert_list(active_only: bool = True):
        return {
            "success": True,
            "count": len(alerts),
            "alerts": alerts,
        }
    return _alert_list


def _mock_alertmanager_failure(error_msg: str = "alertmanager connection refused"):
    def _alert_list(active_only: bool = True):
        return {
            "success": False,
            "error": error_msg,
        }
    return _alert_list


def _mock_promql_success(result_value: float = 0.0):
    def _promql_query(query: str, time_unix=None):
        return {
            "success": True,
            "result": [{"metric": {"__name__": "metric"}, "value": [1786542619, str(result_value)]}],
            "resultType": "vector",
        }
    return _promql_query


def _mock_promql_failure(error_msg: str = "prometheus timeout"):
    def _promql_query(query: str, time_unix=None):
        return {
            "success": False,
            "error": error_msg,
        }
    return _promql_query


# ── Manifest Parsing Helpers ──────────────────────────────────────────────────

def _extract_manifest_targeted_workloads(manifest_path: Path) -> set[str]:
    """Parse YAML docs in manifest and extract directly targeted workload names from label selectors and patches."""
    content = manifest_path.read_text(encoding="utf-8")
    targets = set()
    for doc in yaml.safe_load_all(content):
        if not doc or not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        spec = doc.get("spec", {})

        # Standard Chaos Mesh selector labelSelectors
        selector = spec.get("selector", {})
        label_selectors = selector.get("labelSelectors", {})
        if "app" in label_selectors:
            targets.add(label_selectors["app"])

        # Chaos Mesh target selector (e.g. partition destination)
        target_spec = spec.get("target", {})
        target_selector = target_spec.get("selector", {})
        target_label_selectors = target_selector.get("labelSelectors", {})
        if "app" in target_label_selectors:
            targets.add(target_label_selectors["app"])

        # Argo CD application patch
        if kind == "Application":
            patches = spec.get("source", {}).get("kustomize", {}).get("patches", [])
            for patch in patches:
                target_obj = patch.get("target", {})
                if target_obj.get("name"):
                    targets.add(target_obj["name"])

    return targets


def _extract_manifest_tier(manifest_path: Path) -> str | None:
    """Extract metadata.labels.tier from the first document in the manifest."""
    content = manifest_path.read_text(encoding="utf-8")
    for doc in yaml.safe_load_all(content):
        if doc and isinstance(doc, dict):
            tier = doc.get("metadata", {}).get("labels", {}).get("tier")
            if tier:
                return str(tier)
    return None


def _extract_manifest_selector_namespaces(manifest_path: Path) -> set[str]:
    """Extract spec.selector.namespaces across all documents in the manifest."""
    content = manifest_path.read_text(encoding="utf-8")
    namespaces = set()
    for doc in yaml.safe_load_all(content):
        if not doc or not isinstance(doc, dict):
            continue
        spec = doc.get("spec", {})
        sel_ns = spec.get("selector", {}).get("namespaces", [])
        if isinstance(sel_ns, list):
            namespaces.update(sel_ns)
    return namespaces


# ── Explicit Reviewed Exceptions for Genuine Non-Standard Manifests ───────────

REVIEWED_MANIFEST_TARGET_EXCEPTIONS: dict[str, set[str]] = {
    # hist-azure-dns-2019 targets checkoutservice, cartservice, and currencyservice via
    # DNS patterns across default namespace rather than individual pod labelSelectors.
    "named_replays/hist-azure-dns-2019": {"checkoutservice", "cartservice", "currencyservice"},
    # hist-datadog-2023 applies wildcard DNSChaos (*.default.svc.cluster.local.)
    # across the entire namespace; objective recovery verifies the 4 core customer-facing services.
    "named_replays/hist-datadog-2023": {"frontend", "cartservice", "checkoutservice", "productcatalogservice"},
    # hist-facebook-bgp-2021 partitions default namespace from kube-system;
    # objective recovery verifies ingress edge availability on frontend.
    "named_replays/hist-facebook-bgp-2021": {"frontend"},
    # hist-knight-capital-2012 deploys rogue checkoutservice-legacy;
    # objective recovery verifies standard checkoutservice deployment while checking
    # that checkoutservice-legacy is scaled down / removed.
    "named_replays/hist-knight-capital-2012": {"checkoutservice"},
    # mf-003 combines cluster-wide DNSChaos with targeted currencyservice network delay;
    # objective recovery verifies currencyservice.
    "multi_fault/mf-003": {"currencyservice"},
}


# ── Scenario Catalogue Contract Tests (All 28 Frozen Scenarios) ───────────────

class TestScenarioCatalogueContract:
    def test_verifier_spec_count_equals_frozen_catalogue_count(self):
        assert len(SCENARIO_VERIFICATION_SPECS) == 28
        assert len(FROZEN_SCENARIOS) == 28
        assert set(SCENARIO_VERIFICATION_SPECS.keys()) == set(FROZEN_SCENARIOS)

    def test_no_unknown_or_missing_frozen_ids(self):
        for scenario_id in FROZEN_SCENARIOS:
            assert scenario_id in SCENARIO_VERIFICATION_SPECS, f"Missing spec for frozen scenario: {scenario_id}"
            spec = SCENARIO_VERIFICATION_SPECS[scenario_id]
            assert spec.scenario_id == scenario_id
            assert len(spec.workloads) >= 1, f"Scenario {scenario_id} must specify at least one target workload"

    def test_dynamic_adversarial_scenarios_not_in_frozen_spec_catalogue(self):
        for spec_id in SCENARIO_VERIFICATION_SPECS:
            assert not spec_id.startswith("adversarial/"), f"Adversarial scenario {spec_id} must not be in frozen catalogue"

    @pytest.mark.parametrize("scenario_id", FROZEN_SCENARIOS)
    def test_spec_workloads_match_actual_manifest_targets_exactly(self, scenario_id: str):
        """Require exact equality (spec_workloads == manifest_targets).

        Detects both missing workload targets and extra unrelated bogus targets.
        """
        manifest_path = MANIFESTS_DIR / f"{scenario_id}.yaml"
        assert manifest_path.exists(), f"Manifest file missing for {scenario_id}: {manifest_path}"

        spec = SCENARIO_VERIFICATION_SPECS[scenario_id]
        spec_workloads = {w.name for w in spec.workloads}

        if scenario_id in REVIEWED_MANIFEST_TARGET_EXCEPTIONS:
            expected_targets = REVIEWED_MANIFEST_TARGET_EXCEPTIONS[scenario_id]
            assert spec_workloads == expected_targets, (
                f"Reviewed exception mismatch for {scenario_id}: spec={spec_workloads} vs expected={expected_targets}"
            )
            if scenario_id == "named_replays/hist-knight-capital-2012":
                assert "checkoutservice-legacy" in spec.require_no_legacy_deployments
        else:
            manifest_targets = _extract_manifest_targeted_workloads(manifest_path)
            assert spec_workloads == manifest_targets, (
                f"Exact target mismatch for {scenario_id}: spec={spec_workloads} vs manifest={manifest_targets}"
            )

    def test_extra_bogus_workload_target_fails_contract(self):
        """Regression test: proving that an extra bogus target fails the exact equality contract."""
        manifest_path = MANIFESTS_DIR / "single_fault/sf-001.yaml"
        manifest_targets = _extract_manifest_targeted_workloads(manifest_path)
        assert manifest_targets == {"cartservice"}

        # Simulate a corrupted spec with an extra unrelated service
        bogus_spec_workloads = {"cartservice", "adservice"}
        assert bogus_spec_workloads != manifest_targets, "Exact equality must catch extra bogus workloads"

    @pytest.mark.parametrize("scenario_id", FROZEN_SCENARIOS)
    def test_spec_tier_agrees_with_manifest_metadata(self, scenario_id: str):
        """Enforce tier agreement between canonical scenario ID and metadata.labels.tier."""
        manifest_path = MANIFESTS_DIR / f"{scenario_id}.yaml"
        expected_tier = scenario_id.split("/")[0]
        manifest_tier = _extract_manifest_tier(manifest_path)
        assert manifest_tier is not None, f"Manifest {scenario_id} lacks metadata.labels.tier"
        assert manifest_tier == expected_tier, (
            f"Tier mismatch for {scenario_id}: canonical tier '{expected_tier}' != manifest tier '{manifest_tier}'"
        )

    @pytest.mark.parametrize("scenario_id", FROZEN_SCENARIOS)
    def test_spec_workload_namespaces_agree_with_manifest_selectors(self, scenario_id: str):
        """Enforce that workload target namespaces match selector.namespaces from manifests."""
        manifest_path = MANIFESTS_DIR / f"{scenario_id}.yaml"
        spec = SCENARIO_VERIFICATION_SPECS[scenario_id]

        sel_namespaces = _extract_manifest_selector_namespaces(manifest_path)
        for wl in spec.workloads:
            # If selector namespaces are specified in manifest, workload namespace must match
            if sel_namespaces:
                assert wl.namespace in sel_namespaces, (
                    f"Namespace mismatch for {scenario_id} workload {wl.name}: '{wl.namespace}' not in {sel_namespaces}"
                )
            else:
                # Default for ArgoCD Application or raw Deployments
                assert wl.namespace == "default"

    def test_cross_namespace_network_chaos_target_not_confused_with_workload(self):
        """Verify that NetworkChaos destination namespace (e.g. kube-system in hist-facebook-bgp-2021)

        is not confused with the data plane workload namespace (default).
        """
        manifest_path = MANIFESTS_DIR / "named_replays/hist-facebook-bgp-2021.yaml"
        content = manifest_path.read_text(encoding="utf-8")
        doc = yaml.safe_load(content)

        source_ns = doc["spec"]["selector"]["namespaces"]
        target_ns = doc["spec"]["target"]["selector"]["namespaces"]

        assert source_ns == ["default"]
        assert target_ns == ["kube-system"]

        # Spec workload must be the source data-plane application (frontend in default)
        spec = SCENARIO_VERIFICATION_SPECS["named_replays/hist-facebook-bgp-2021"]
        for wl in spec.workloads:
            assert wl.namespace == "default"
            assert wl.namespace != "kube-system"

    def test_unknown_frozen_scenario_fails_closed(self):
        verifier = EnvironmentVerifier()
        # Passing an invalid ID with a frozen tier prefix must NOT fall back to frontend
        result = verifier.verify("single_fault/sf-999-nonexistent", agent_claimed_resolved=True)
        assert result.env_resolved is False
        assert result.verification_status == "error"
        assert "unknown_frozen_scenario_spec" in str(result.error)
        assert result.is_false_resolution is True  # Agent claimed victory on invalid scenario


# ── Dynamic Scenario Handling Tests ───────────────────────────────────────────

class TestDynamicScenarioHandling:
    def test_dynamic_scenario_valid_service_target(self):
        """Dynamic scenario with valid service label synthesizes workload and resolves."""
        alert_payload = {
            "commonLabels": {
                "alertname": "AdversarialPaymentDrop",
                "service": "paymentservice",
                "namespace": "default",
            },
            "alerts": [
                {"labels": {"alertname": "AdversarialPaymentDrop", "service": "paymentservice", "namespace": "default"}}
            ],
        }
        deployment_item = {
            "metadata": {"name": "paymentservice", "namespace": "default"},
            "status": {"readyReplicas": 1, "replicas": 1},
        }
        verifier = EnvironmentVerifier(
            kubectl_getter=_mock_kubectl_success([deployment_item]),
            alert_lister=_mock_alertmanager_success([]),
            promql_querier=_mock_promql_success(0.0),
        )

        result = verifier.verify(
            scenario_id="adversarial/adv-synthetic-042",
            agent_claimed_resolved=True,
            alert=alert_payload,
        )

        assert result.env_resolved is True
        assert result.verification_status == "passed"
        assert any("paymentservice" in c.target for c in result.checks)

    def test_dynamic_scenario_no_target_labels_fails_closed(self):
        """Dynamic scenario without service/deployment/app labels fails closed (no frontend fallback)."""
        alert_payload = {
            "commonLabels": {
                "alertname": "GenericAlertWithNoTargetService",
            },
            "alerts": [
                {"labels": {"alertname": "GenericAlertWithNoTargetService"}}
            ],
        }
        verifier = EnvironmentVerifier()
        result = verifier.verify(
            scenario_id="adversarial/adv-no-labels",
            agent_claimed_resolved=True,
            alert=alert_payload,
        )

        assert result.env_resolved is False
        assert result.verification_status == "error"
        assert result.error == "dynamic_scenario_target_unresolved"
        assert "dynamic_scenario_target_resolution" in result.failed_checks
        assert result.is_false_resolution is True

    def test_dynamic_scenario_empty_or_none_alert_fails_closed(self):
        """Dynamic scenario with None or empty alert dictionary fails closed."""
        verifier = EnvironmentVerifier()
        result_none = verifier.verify(
            scenario_id="adversarial/adv-none-alert",
            agent_claimed_resolved=True,
            alert=None,
        )
        assert result_none.env_resolved is False
        assert result_none.verification_status == "error"
        assert result_none.error == "dynamic_scenario_target_unresolved"
        assert result_none.is_false_resolution is True

        result_empty = verifier.verify(
            scenario_id="adversarial/adv-empty-alert",
            agent_claimed_resolved=False,
            alert={},
        )
        assert result_empty.env_resolved is False
        assert result_empty.verification_status == "error"
        assert result_empty.error == "dynamic_scenario_target_unresolved"
        assert result_empty.is_false_negative is False


# ── Verifier Engine Behavioral Tests ──────────────────────────────────────────

class TestEnvironmentVerifierBehavior:
    def test_resolved_environment_all_checks_pass(self):
        """Case 1: Ground-truth healthy cluster with target workload ready."""
        deployment_item = {
            "metadata": {"name": "cartservice", "namespace": "default"},
            "spec": {"replicas": 1},
            "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
        }
        verifier = EnvironmentVerifier(
            kubectl_getter=_mock_kubectl_success([deployment_item]),
            alert_lister=_mock_alertmanager_success([]),
            promql_querier=_mock_promql_success(0.0),
        )

        result = verifier.verify(
            scenario_id="single_fault/sf-001",
            agent_claimed_resolved=True,
        )

        assert result.env_resolved is True
        assert result.agent_claimed_resolved is True
        assert result.verification_status == "passed"
        assert result.is_false_resolution is False
        assert result.is_false_negative is False
        assert len(result.failed_checks) == 0

    def test_unresolved_environment_workload_failing(self):
        """Case 2: Workload has zero ready replicas."""
        deployment_item = {
            "metadata": {"name": "cartservice", "namespace": "default"},
            "spec": {"replicas": 1},
            "status": {"replicas": 1, "readyReplicas": 0, "availableReplicas": 0},
        }
        verifier = EnvironmentVerifier(
            kubectl_getter=_mock_kubectl_success([deployment_item]),
            alert_lister=_mock_alertmanager_success([]),
            promql_querier=_mock_promql_success(0.0),
        )

        result = verifier.verify(
            scenario_id="single_fault/sf-001",
            agent_claimed_resolved=False,
        )

        assert result.env_resolved is False
        assert result.agent_claimed_resolved is False
        assert result.verification_status == "failed"
        assert "workload_cartservice_ready" in result.failed_checks

    def test_false_resolution_detection(self):
        """Case 3: Agent falsely claims resolved, but target workload is unready."""
        deployment_item = {
            "metadata": {"name": "paymentservice", "namespace": "default"},
            "spec": {"replicas": 1},
            "status": {"replicas": 1, "readyReplicas": 0},
        }
        verifier = EnvironmentVerifier(
            kubectl_getter=_mock_kubectl_success([deployment_item]),
            alert_lister=_mock_alertmanager_success([]),
            promql_querier=_mock_promql_success(0.0),
        )

        result = verifier.verify(
            scenario_id="single_fault/sf-002",
            agent_claimed_resolved=True,
        )

        assert result.env_resolved is False
        assert result.agent_claimed_resolved is True
        assert result.is_false_resolution is True
        assert result.is_false_negative is False
        assert result.verification_status == "failed"

    def test_false_negative_detection(self):
        """Case 4: Agent claimed unresolved, but environment is actually healthy."""
        deployment_item = {
            "metadata": {"name": "checkoutservice", "namespace": "default"},
            "spec": {"replicas": 1},
            "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
        }
        verifier = EnvironmentVerifier(
            kubectl_getter=_mock_kubectl_success([deployment_item]),
            alert_lister=_mock_alertmanager_success([]),
            promql_querier=_mock_promql_success(0.0),
        )

        result = verifier.verify(
            scenario_id="single_fault/sf-003",
            agent_claimed_resolved=False,
        )

        assert result.env_resolved is True
        assert result.agent_claimed_resolved is False
        assert result.is_false_negative is True
        assert result.is_false_resolution is False
        assert result.verification_status == "passed"

    def test_composite_multi_workload_verification(self):
        """Case 5: Multi-workload scenario verifying all affected services."""
        deployments = [
            {"metadata": {"name": "redis-cart", "namespace": "default"}, "status": {"readyReplicas": 1, "replicas": 1}},
            {"metadata": {"name": "cartservice", "namespace": "default"}, "status": {"readyReplicas": 1, "replicas": 1}},
            {"metadata": {"name": "recommendationservice", "namespace": "default"}, "status": {"readyReplicas": 1, "replicas": 1}},
        ]
        verifier = EnvironmentVerifier(
            kubectl_getter=_mock_kubectl_success(deployments),
            alert_lister=_mock_alertmanager_success([]),
            promql_querier=_mock_promql_success(0.0),
        )

        result = verifier.verify(
            scenario_id="multi_fault/mf-002",
            agent_claimed_resolved=True,
        )

        assert result.env_resolved is True
        assert result.verification_status == "passed"
        workload_checks = [c for c in result.checks if c.name.startswith("workload_")]
        assert len(workload_checks) == 3

    def test_knight_capital_legacy_deployment_check(self):
        """Case 6: Knight Capital named replay requires checkoutservice ready AND legacy deployment removed."""
        deployments_with_active_legacy = [
            {"metadata": {"name": "checkoutservice", "namespace": "default"}, "status": {"readyReplicas": 1, "replicas": 1}},
            {"metadata": {"name": "checkoutservice-legacy", "namespace": "default"}, "status": {"readyReplicas": 2, "replicas": 2}},
        ]
        verifier = EnvironmentVerifier(
            kubectl_getter=_mock_kubectl_success(deployments_with_active_legacy),
            alert_lister=_mock_alertmanager_success([]),
            promql_querier=_mock_promql_success(0.0),
        )

        result = verifier.verify("named_replays/hist-knight-capital-2012", agent_claimed_resolved=True)
        assert result.env_resolved is False
        assert "legacy_deployment_checkoutservice-legacy_removed" in result.failed_checks

        # Now test when legacy deployment is removed/scaled to 0
        deployments_repaired = [
            {"metadata": {"name": "checkoutservice", "namespace": "default"}, "status": {"readyReplicas": 1, "replicas": 1}},
        ]
        verifier_repaired = EnvironmentVerifier(
            kubectl_getter=_mock_kubectl_success(deployments_repaired),
            alert_lister=_mock_alertmanager_success([]),
            promql_querier=_mock_promql_success(0.0),
        )
        result_repaired = verifier_repaired.verify("named_replays/hist-knight-capital-2012", agent_claimed_resolved=True)
        assert result_repaired.env_resolved is True

    def test_chaos_mesh_clearance_matching_and_diagnostics(self):
        """Case 7: Chaos clearance diagnostics matching active scenario labels/names."""
        # When an active chaos experiment matches the scenario
        active_chaos = [{
            "kind": "PodChaos",
            "metadata": {"name": "sf-001-cartservice-kill", "namespace": "chaos-mesh", "labels": {"scenario": "sf-001"}},
        }]
        def _mock_kubectl_active_chaos(resource: str, namespace: str = "-A", output: str = "json"):
            if "chaos" in resource.lower():
                return {"success": True, "parsed": {"items": active_chaos}}
            return {"success": True, "parsed": {"items": [{"metadata": {"name": "cartservice"}, "status": {"readyReplicas": 1, "replicas": 1}}]}}

        verifier = EnvironmentVerifier(
            kubectl_getter=_mock_kubectl_active_chaos,
            alert_lister=_mock_alertmanager_success([]),
            promql_querier=_mock_promql_success(0.0),
        )

        result = verifier.verify("single_fault/sf-001", agent_claimed_resolved=True)
        assert result.env_resolved is False
        assert "chaos_mesh_cleared" in result.failed_checks
        chaos_check = next(c for c in result.checks if c.name == "chaos_mesh_cleared")
        assert "sf-001-cartservice-kill" in chaos_check.details

    def test_missing_backend_inconclusive_handling(self):
        """Case 8: All telemetry backends fail closed (inconclusive status)."""
        verifier = EnvironmentVerifier(
            kubectl_getter=_mock_kubectl_failure("cluster unreachable"),
            alert_lister=_mock_alertmanager_failure("alertmanager down"),
            promql_querier=_mock_promql_failure("prometheus down"),
        )

        result = verifier.verify(
            scenario_id="single_fault/sf-001",
            agent_claimed_resolved=True,
        )

        assert result.env_resolved is False
        assert result.verification_status == "inconclusive"
        assert result.error == "environment_telemetry_unreachable"

    def test_metric_predicate_comparison_operators_and_semantics(self):
        """Case 9: PromQL metric evaluation semantics across comparison operators."""
        # Test less-than operator
        verifier_lt = EnvironmentVerifier(promql_querier=_mock_promql_success(0.02))
        check_lt, val_lt = verifier_lt._verify_metric(MetricPredicate(query="test_metric", operator="<", threshold=0.05))
        assert check_lt.passed is True
        assert val_lt == 0.02

        # Test threshold breach
        check_breach, _ = verifier_lt._verify_metric(MetricPredicate(query="test_metric", operator="<", threshold=0.01))
        assert check_breach.passed is False

        # Test greater-than operator
        check_gt, _ = verifier_lt._verify_metric(MetricPredicate(query="test_metric", operator=">=", threshold=0.02))
        assert check_gt.passed is True

    def test_deterministic_serialization_to_dict(self):
        """Case 10: Serialization contract produces typed JSON-compatible dictionary."""
        res = EnvironmentVerificationResult(
            scenario_id="single_fault/sf-001",
            agent_claimed_resolved=True,
            env_resolved=True,
            verification_status="passed",
            checks=[CheckResult(name="workload_cartservice_ready", target="default/cartservice", passed=True, details="ok")],
            failed_checks=[],
            observed_metrics={"ready": 1},
            evidence=["Workload default/cartservice ready"],
        )

        d = res.to_dict()
        assert d["scenario_id"] == "single_fault/sf-001"
        assert d["agent_claimed_resolved"] is True
        assert d["env_resolved"] is True
        assert d["verification_status"] == "passed"
        assert d["is_false_resolution"] is False
        assert len(d["checks"]) == 1

        encoded = json.dumps(d)
        assert isinstance(encoded, str)
        decoded = json.loads(encoded)
        assert decoded["scenario_id"] == "single_fault/sf-001"
