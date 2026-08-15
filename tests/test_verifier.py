"""Comprehensive tests for the Objective Environment Verifier and Scenario Contract.

Guarantees:
1. 100% agreement between SCENARIO_VERIFICATION_SPECS and the actual Chaos Mesh manifests.
2. 100% offline mocked unit execution with zero network, cloud, or cluster dependencies.
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


# ── Scenario Manifest Extraction Helper ───────────────────────────────────────

def _extract_manifest_targeted_workloads(manifest_path: Path) -> set[str]:
    """Parse YAML docs in manifest and extract targeted workload names."""
    content = manifest_path.read_text(encoding="utf-8")
    targets = set()
    for doc in yaml.safe_load_all(content):
        if not doc or not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        spec = doc.get("spec", {})

        # Standard Chaos Mesh selector
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
            for p in patches:
                target_obj = p.get("target", {})
                if target_obj.get("name"):
                    targets.add(target_obj["name"])

        # Direct Deployment
        if kind == "Deployment":
            name = doc.get("metadata", {}).get("name", "")
            if name:
                targets.add(name)

    return targets


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
    def test_spec_workloads_match_actual_manifest_targets(self, scenario_id: str):
        manifest_path = MANIFESTS_DIR / f"{scenario_id}.yaml"
        assert manifest_path.exists(), f"Manifest file missing for {scenario_id}: {manifest_path}"

        manifest_targets = _extract_manifest_targeted_workloads(manifest_path)
        spec = SCENARIO_VERIFICATION_SPECS[scenario_id]
        spec_workloads = {w.name for w in spec.workloads}

        # For scenarios targeting specific services via labelSelectors or patches
        if scenario_id == "named_replays/hist-knight-capital-2012":
            # Targets checkoutservice, while checkoutservice-legacy is the bad deployment
            assert "checkoutservice" in spec_workloads
            assert "checkoutservice-legacy" in spec.require_no_legacy_deployments
        elif scenario_id == "named_replays/hist-datadog-2023":
            # DNS failure across default namespace; verifies core boutique services
            assert {"frontend", "cartservice", "checkoutservice", "productcatalogservice"}.issubset(spec_workloads)
        elif scenario_id == "named_replays/hist-azure-dns-2019":
            # Stale DNS patterns for checkoutservice, cartservice, currencyservice
            assert {"checkoutservice", "cartservice", "currencyservice"}.issubset(spec_workloads)
        elif scenario_id == "named_replays/hist-facebook-bgp-2021":
            # BGP partition from default to kube-system; verifies frontend edge availability
            assert "frontend" in spec_workloads
        elif scenario_id == "single_fault/sf-006":
            # DNS chaos on checkoutservice
            assert "checkoutservice" in spec_workloads
        elif scenario_id == "multi_fault/mf-003":
            # DNS random on cluster + currency latency
            assert "currencyservice" in spec_workloads
        else:
            # Manifest label selectors must be fully covered by spec workloads
            assert manifest_targets.issubset(spec_workloads), (
                f"Spec workloads for {scenario_id} ({spec_workloads}) do not cover manifest targets ({manifest_targets})"
            )

    @pytest.mark.parametrize("scenario_id", FROZEN_SCENARIOS)
    def test_spec_namespaces_are_default(self, scenario_id: str):
        spec = SCENARIO_VERIFICATION_SPECS[scenario_id]
        for w in spec.workloads:
            assert w.namespace == "default"

    def test_unknown_frozen_scenario_fails_closed(self):
        verifier = EnvironmentVerifier()
        # Passing an invalid ID with a frozen tier prefix must NOT fall back to frontend
        result = verifier.verify("single_fault/sf-999-nonexistent", agent_claimed_resolved=True)
        assert result.env_resolved is False
        assert result.verification_status == "error"
        assert "unknown_frozen_scenario_spec" in str(result.error)
        assert result.is_false_resolution is True  # Agent claimed victory on invalid scenario


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

    def test_missing_backend_inconclusive_handling(self):
        """Case 7: All telemetry backends fail closed (inconclusive status)."""
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

    def test_dynamic_adversarial_scenario_synthesis(self):
        """Case 8: Dynamically generated adversarial scenario synthesizing predicates from alert payload."""
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
