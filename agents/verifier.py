"""Objective Environment Verifier for AtlasOps.

Distinguishes between agent-claimed resolution and objective ground-truth
resolution across Kubernetes workloads, Alertmanager alerts, Prometheus
metrics, and chaos experiment clearance.

An agent outputting `outcome="resolved"` is a hypothesis; this verifier
determines whether the environment has actually recovered.
"""

from __future__ import annotations

import json
import logging
import operator
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from agents.tools.alertmanager import alertmanager_list_alerts
from agents.tools.kubectl import kubectl_get
from agents.tools.prometheus import promql_query

log = logging.getLogger("atlasops.verifier")


# ── Comparison Operators ──────────────────────────────────────────────────────

_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


# ── Verification Specification Data Structures ───────────────────────────────

@dataclass(frozen=True)
class WorkloadPredicate:
    """Predicate requiring a Kubernetes workload to be ready."""
    name: str
    kind: str = "deployment"
    namespace: str = "default"
    min_ready_replicas: int = 1
    min_ready_fraction: float = 1.0


@dataclass(frozen=True)
class MetricPredicate:
    """Predicate requiring a PromQL query to satisfy a numeric threshold."""
    query: str
    operator: str
    threshold: float
    description: str = ""


@dataclass(frozen=True)
class ScenarioVerificationSpec:
    """Declarative objective verification predicates for an incident scenario."""
    scenario_id: str
    workloads: tuple[WorkloadPredicate, ...] = ()
    alerts_must_clear: tuple[str, ...] = ()
    metrics: tuple[MetricPredicate, ...] = ()
    require_chaos_cleared: bool = True


# ── Standard Online Boutique Services Catalog ────────────────────────────────

ONLINE_BOUTIQUE_SERVICES = frozenset({
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
})


# ── Predefined Verification Specs for Frozen Scenario Catalogue ──────────────

def _make_workload(name: str, namespace: str = "default") -> WorkloadPredicate:
    return WorkloadPredicate(name=name, kind="deployment", namespace=namespace, min_ready_replicas=1)


SCENARIO_VERIFICATION_SPECS: dict[str, ScenarioVerificationSpec] = {
    # Single-fault scenarios
    "single_fault/sf-001": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-001",
        workloads=(_make_workload("frontend"),),
        alerts_must_clear=("FrontendCrashLooping", "High5xxRate", "sf-001-pod-failure"),
        metrics=(MetricPredicate(
            query='sum(rate(http_requests_total{status=~"5.."}[2m])) or vector(0)',
            operator="<=",
            threshold=0.05,
            description="5xx error rate below 5%",
        ),),
    ),
    "single_fault/sf-002": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-002",
        workloads=(_make_workload("productcatalogservice"),),
        alerts_must_clear=("ProductCatalogLatencyHigh", "sf-002-network-delay"),
    ),
    "single_fault/sf-003": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-003",
        workloads=(_make_workload("recommendationservice"),),
        alerts_must_clear=("HighCpuSaturation", "sf-003-cpu-stress"),
    ),
    "single_fault/sf-004": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-004",
        workloads=(_make_workload("cartservice"),),
        alerts_must_clear=("HighMemoryUsage", "sf-004-memory-stress"),
    ),
    "single_fault/sf-005": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-005",
        workloads=(_make_workload("paymentservice"),),
        alerts_must_clear=("PaymentServiceDNSError", "sf-005-dns-chaos"),
    ),
    "single_fault/sf-006": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-006",
        workloads=(_make_workload("shippingservice"),),
        alerts_must_clear=("ShippingIODelay", "sf-006-io-delay"),
    ),
    "single_fault/sf-007": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-007",
        workloads=(_make_workload("currencyservice"),),
        alerts_must_clear=("ClockSkewAlert", "sf-007-time-skew"),
    ),
    "single_fault/sf-008": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-008",
        workloads=(_make_workload("checkoutservice"),),
        alerts_must_clear=("CheckoutPodFailure", "sf-008-pod-failure"),
    ),

    # Cascade scenarios
    "cascade/cs-001": ScenarioVerificationSpec(
        scenario_id="cascade/cs-001",
        workloads=(_make_workload("redis-cart"), _make_workload("cartservice"), _make_workload("frontend")),
        alerts_must_clear=("RedisCartUnavailable", "CartServiceDown", "Frontend5xxSpike"),
    ),
    "cascade/cs-002": ScenarioVerificationSpec(
        scenario_id="cascade/cs-002",
        workloads=(_make_workload("paymentservice"), _make_workload("checkoutservice")),
        alerts_must_clear=("PaymentNetworkLoss", "CheckoutFailureCascade"),
    ),
    "cascade/cs-003": ScenarioVerificationSpec(
        scenario_id="cascade/cs-003",
        workloads=(_make_workload("shippingservice"), _make_workload("checkoutservice")),
        alerts_must_clear=("ShippingLatencyHigh", "CheckoutTimeoutCascade"),
    ),
    "cascade/cs-004": ScenarioVerificationSpec(
        scenario_id="cascade/cs-004",
        workloads=(_make_workload("productcatalogservice"), _make_workload("frontend")),
        alerts_must_clear=("ProductCatalogCPUSaturation", "FrontendSlowdown"),
    ),
    "cascade/cs-005": ScenarioVerificationSpec(
        scenario_id="cascade/cs-005",
        workloads=(_make_workload("adservice"), _make_workload("frontend")),
        alerts_must_clear=("AdServiceCrashLoop", "FrontendAdTimeout"),
    ),

    # Multi-fault scenarios
    "multi_fault/mf-001": ScenarioVerificationSpec(
        scenario_id="multi_fault/mf-001",
        workloads=(_make_workload("frontend"), _make_workload("checkoutservice")),
        alerts_must_clear=("FrontendCPUStress", "CheckoutPacketLoss"),
    ),
    "multi_fault/mf-002": ScenarioVerificationSpec(
        scenario_id="multi_fault/mf-002",
        workloads=(_make_workload("cartservice"), _make_workload("currencyservice")),
        alerts_must_clear=("CartMemorySaturation", "CurrencyDNSDelay"),
    ),
    "multi_fault/mf-003": ScenarioVerificationSpec(
        scenario_id="multi_fault/mf-003",
        workloads=(_make_workload("recommendationservice"), _make_workload("productcatalogservice")),
        alerts_must_clear=("RecommendationPodKill", "ProductCatalogLatency"),
    ),
    "multi_fault/mf-004": ScenarioVerificationSpec(
        scenario_id="multi_fault/mf-004",
        workloads=(_make_workload("shippingservice"), _make_workload("emailservice")),
        alerts_must_clear=("ShippingIODelay", "EmailServiceCrash"),
    ),
    "multi_fault/mf-005": ScenarioVerificationSpec(
        scenario_id="multi_fault/mf-005",
        workloads=(_make_workload("paymentservice"), _make_workload("redis-cart")),
        alerts_must_clear=("PaymentNetworkCorruption", "RedisMemoryStress"),
    ),

    # Named historical replays
    "named_replays/hist-cloudflare-2019": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-cloudflare-2019",
        workloads=(_make_workload("frontend"),),
        alerts_must_clear=("FrontendHighCPUSaturation", "GlobalWAFCPULoop"),
    ),
    "named_replays/hist-aws-s3-2017": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-aws-s3-2017",
        workloads=(_make_workload("productcatalogservice"),),
        alerts_must_clear=("SubsystemUnreachable", "StorageIndexTimeout"),
    ),
    "named_replays/hist-github-2018": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-github-2018",
        workloads=(_make_workload("redis-cart"),),
        alerts_must_clear=("DatabaseFailoverLoop", "ReplicaDesyncAlert"),
    ),
    "named_replays/hist-datadog-2023": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-datadog-2023",
        workloads=(_make_workload("cartservice"), _make_workload("frontend")),
        alerts_must_clear=("NetworkPartitionAlert", "StateSyncTimeout"),
    ),
    "named_replays/hist-discord-2022": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-discord-2022",
        workloads=(_make_workload("checkoutservice"),),
        alerts_must_clear=("QueueSaturation", "MessageTimeoutCascade"),
    ),
    "named_replays/hist-fastly-2021": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-fastly-2021",
        workloads=(_make_workload("frontend"),),
        alerts_must_clear=("VCLConfigurationError", "Edge503Spike"),
    ),
    "named_replays/hist-facebook-bgp-2021": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-facebook-bgp-2021",
        workloads=(_make_workload("frontend"),),
        alerts_must_clear=("DNSResolutionFailure", "BackboneUnreachable"),
    ),
    "named_replays/hist-slack-2022": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-slack-2022",
        workloads=(_make_workload("cartservice"),),
        alerts_must_clear=("DatabaseThreadStarvation", "ConnectionPoolExhausted"),
    ),
    "named_replays/hist-azure-dns-2019": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-azure-dns-2019",
        workloads=(_make_workload("currencyservice"),),
        alerts_must_clear=("DNSLookupsFailing", "NameResolutionTimeout"),
    ),
    "named_replays/hist-knight-capital-2012": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-knight-capital-2012",
        workloads=(_make_workload("checkoutservice"),),
        alerts_must_clear=("BadDeploymentRollbackRequired", "OrderExecutionLoop"),
    ),
}


# ── Output Data Structures ───────────────────────────────────────────────────

@dataclass
class CheckResult:
    """Individual verification check result."""
    name: str
    target: str
    passed: bool
    required: bool = True
    details: str = ""
    observed: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EnvironmentVerificationResult:
    """Complete objective environment verification result.

    Preserves both `agent_claimed_resolved` and `env_resolved`.
    """
    scenario_id: str
    agent_claimed_resolved: bool
    env_resolved: bool
    verification_status: str  # "passed" | "failed" | "inconclusive" | "error"
    checks: list[CheckResult] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    observed_metrics: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    verification_timestamp: float = field(default_factory=time.time)
    is_false_resolution: bool = False
    is_false_negative: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "agent_claimed_resolved": self.agent_claimed_resolved,
            "env_resolved": self.env_resolved,
            "verification_status": self.verification_status,
            "is_false_resolution": self.is_false_resolution,
            "is_false_negative": self.is_false_negative,
            "failed_checks": list(self.failed_checks),
            "checks": [c.to_dict() for c in self.checks],
            "observed_metrics": dict(self.observed_metrics),
            "evidence": list(self.evidence),
            "verification_timestamp": self.verification_timestamp,
            "error": self.error,
        }


# ── Environment Verifier Engine ───────────────────────────────────────────────

class EnvironmentVerifier:
    """Deterministic, objective verifier for incident remediation."""

    def __init__(
        self,
        kubectl_getter: Callable[..., dict[str, Any]] | None = None,
        alert_lister: Callable[..., dict[str, Any]] | None = None,
        promql_querier: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._kubectl_get = kubectl_getter or kubectl_get
        self._alert_list = alert_lister or alertmanager_list_alerts
        self._promql_query = promql_querier or promql_query

    def resolve_spec(
        self,
        scenario_id: str,
        alert: dict[str, Any] | None = None,
    ) -> ScenarioVerificationSpec:
        """Resolve scenario verification spec or synthesize one from alert context."""
        normalized_id = scenario_id.replace("\\", "/").strip()
        if normalized_id in SCENARIO_VERIFICATION_SPECS:
            return SCENARIO_VERIFICATION_SPECS[normalized_id]

        # Dynamic / adversarial scenario synthesis
        workloads = []
        alerts_must_clear = []
        if alert and isinstance(alert, dict):
            common_labels = alert.get("commonLabels", {}) or {}
            target_svc = str(
                common_labels.get("service")
                or common_labels.get("deployment")
                or common_labels.get("app")
                or ""
            ).strip()
            ns = str(common_labels.get("namespace") or "default").strip()
            if target_svc:
                workloads.append(_make_workload(target_svc, namespace=ns))

            alertname = str(common_labels.get("alertname") or "").strip()
            if alertname:
                alerts_must_clear.append(alertname)

            for item in alert.get("alerts", []):
                if isinstance(item, dict):
                    lbls = item.get("labels", {}) or {}
                    s = str(lbls.get("service") or lbls.get("deployment") or lbls.get("app") or "").strip()
                    if s and not any(w.name == s for w in workloads):
                        workloads.append(_make_workload(s, namespace=str(lbls.get("namespace") or ns).strip()))
                    an = str(lbls.get("alertname") or "").strip()
                    if an and an not in alerts_must_clear:
                        alerts_must_clear.append(an)

        if not workloads:
            # Default fallback: check frontend service health in default namespace
            workloads.append(_make_workload("frontend", namespace="default"))

        return ScenarioVerificationSpec(
            scenario_id=normalized_id,
            workloads=tuple(workloads),
            alerts_must_clear=tuple(alerts_must_clear),
            require_chaos_cleared=True,
        )

    def verify(
        self,
        scenario_id: str,
        agent_claimed_resolved: bool,
        alert: dict[str, Any] | None = None,
        incident_context: dict[str, Any] | None = None,
    ) -> EnvironmentVerificationResult:
        """Execute objective verification against the environment."""
        spec = self.resolve_spec(scenario_id, alert=alert)
        checks: list[CheckResult] = []
        observed_metrics: dict[str, Any] = {}
        evidence: list[str] = []
        verification_error: str | None = None
        has_communication_error = False

        # 1. Check Workload Health & Readiness
        for wl in spec.workloads:
            check = self._verify_workload(wl)
            checks.append(check)
            if check.passed:
                evidence.append(f"Workload {wl.namespace}/{wl.name} ({wl.kind}): {check.details}")
            else:
                evidence.append(f"FAILED Workload {wl.namespace}/{wl.name}: {check.details}")
                if "error" in check.details.lower() or "unreachable" in check.details.lower():
                    has_communication_error = True

        # 2. Check Alertmanager Cleared Alerts
        alert_check = self._verify_alerts(spec.alerts_must_clear)
        checks.append(alert_check)
        if alert_check.passed:
            evidence.append(f"Alerts cleared: {alert_check.details}")
        else:
            evidence.append(f"FAILED Alerts: {alert_check.details}")
            if "error" in alert_check.details.lower() or "unreachable" in alert_check.details.lower():
                has_communication_error = True

        # 3. Check PromQL Metric Predicates
        for metric_pred in spec.metrics:
            m_check, val = self._verify_metric(metric_pred)
            checks.append(m_check)
            if val is not None:
                observed_metrics[metric_pred.query] = val
            if m_check.passed:
                evidence.append(f"Metric [{metric_pred.query}]: {m_check.details}")
            else:
                evidence.append(f"FAILED Metric [{metric_pred.query}]: {m_check.details}")
                if "error" in m_check.details.lower() or "unreachable" in m_check.details.lower():
                    has_communication_error = True

        # 4. Check Chaos Mesh Clearance
        if spec.require_chaos_cleared:
            chaos_check = self._verify_chaos_clearance(spec.scenario_id)
            checks.append(chaos_check)
            if chaos_check.passed:
                evidence.append(f"Chaos clearance: {chaos_check.details}")
            else:
                evidence.append(f"FAILED Chaos clearance: {chaos_check.details}")

        # Derive Summary
        failed_checks = [c.name for c in checks if not c.passed and c.required]
        all_passed = len(failed_checks) == 0 and len(checks) > 0

        # Check if all checks failed due to transport / connection errors
        is_all_unreachable = len(checks) > 0 and all(
            not c.passed and any(
                term in c.details.lower()
                for term in ("error", "unreachable", "down", "refused", "timeout", "failed to query")
            )
            for c in checks
        )

        if all_passed:
            verification_status = "passed"
            env_resolved = True
        elif is_all_unreachable:
            verification_status = "inconclusive"
            env_resolved = False
            verification_error = "environment_telemetry_unreachable"
        else:
            verification_status = "failed"
            env_resolved = False

        is_false_resolution = bool(agent_claimed_resolved and not env_resolved)
        is_false_negative = bool(not agent_claimed_resolved and env_resolved)

        return EnvironmentVerificationResult(
            scenario_id=spec.scenario_id,
            agent_claimed_resolved=agent_claimed_resolved,
            env_resolved=env_resolved,
            verification_status=verification_status,
            checks=checks,
            failed_checks=failed_checks,
            observed_metrics=observed_metrics,
            evidence=evidence,
            verification_timestamp=time.time(),
            is_false_resolution=is_false_resolution,
            is_false_negative=is_false_negative,
            error=verification_error,
        )

    def _verify_workload(self, wl: WorkloadPredicate) -> CheckResult:
        """Verify that a Kubernetes deployment/workload is in a ready state."""
        resource_kind = wl.kind.lower()
        if resource_kind in ("deployment", "deployments"):
            res = self._kubectl_get("deployment", namespace=wl.namespace, output="json")
        elif resource_kind in ("pod", "pods"):
            res = self._kubectl_get("pods", namespace=wl.namespace, output="json")
        else:
            res = self._kubectl_get(resource_kind, namespace=wl.namespace, output="json")

        if not res.get("success", False):
            err_msg = str(res.get("error") or res.get("stderr") or "kubectl_query_failed")
            return CheckResult(
                name=f"workload_{wl.name}_ready",
                target=f"{wl.namespace}/{wl.name}",
                passed=False,
                details=f"Failed to query workload from cluster: {err_msg}",
            )

        parsed = res.get("parsed")
        if not parsed:
            # Parse from stdout if parsed dict not present
            stdout = str(res.get("stdout", "")).strip()
            if stdout:
                try:
                    parsed = json.loads(stdout)
                except Exception:
                    pass

        if not parsed or not isinstance(parsed, dict):
            # Non-json or empty response
            stdout_text = str(res.get("stdout", ""))
            if wl.name in stdout_text and ("1/1" in stdout_text or "Running" in stdout_text):
                return CheckResult(
                    name=f"workload_{wl.name}_ready",
                    target=f"{wl.namespace}/{wl.name}",
                    passed=True,
                    details="Workload appears running in text output",
                )
            return CheckResult(
                name=f"workload_{wl.name}_ready",
                target=f"{wl.namespace}/{wl.name}",
                passed=False,
                details="Could not parse workload status JSON from cluster",
            )

        items = parsed.get("items", []) if "items" in parsed else [parsed]
        target_item = None
        for item in items:
            metadata = item.get("metadata", {})
            if metadata.get("name") == wl.name:
                target_item = item
                break

        if not target_item:
            # Check if any item contains the name prefix (for pods)
            for item in items:
                metadata = item.get("metadata", {})
                if str(metadata.get("name", "")).startswith(wl.name):
                    target_item = item
                    break

        if not target_item:
            return CheckResult(
                name=f"workload_{wl.name}_ready",
                target=f"{wl.namespace}/{wl.name}",
                passed=False,
                details=f"Workload {wl.name} not found in namespace {wl.namespace}",
            )

        # Evaluate deployment readyReplicas
        status = target_item.get("status", {})
        if resource_kind in ("deployment", "deployments"):
            desired = int(status.get("replicas", 0) or target_item.get("spec", {}).get("replicas", 1))
            ready = int(status.get("readyReplicas", 0))
            available = int(status.get("availableReplicas", 0))
            if ready >= wl.min_ready_replicas and (desired == 0 or ready / desired >= wl.min_ready_fraction):
                return CheckResult(
                    name=f"workload_{wl.name}_ready",
                    target=f"{wl.namespace}/{wl.name}",
                    passed=True,
                    details=f"Ready replicas: {ready}/{desired} (available: {available})",
                    observed={"ready_replicas": ready, "desired_replicas": desired},
                )
            return CheckResult(
                name=f"workload_{wl.name}_ready",
                target=f"{wl.namespace}/{wl.name}",
                passed=False,
                details=f"Insufficient ready replicas: {ready}/{desired} (min required: {wl.min_ready_replicas})",
                observed={"ready_replicas": ready, "desired_replicas": desired},
            )

        # Evaluate pod status
        phase = str(status.get("phase", ""))
        container_statuses = status.get("containerStatuses", [])
        all_containers_ready = bool(
            container_statuses and all(cs.get("ready", False) for cs in container_statuses)
        )
        if phase == "Running" and all_containers_ready:
            return CheckResult(
                name=f"workload_{wl.name}_ready",
                target=f"{wl.namespace}/{wl.name}",
                passed=True,
                details="Pod phase Running with all containers ready",
                observed={"phase": phase, "containers_ready": True},
            )
        return CheckResult(
            name=f"workload_{wl.name}_ready",
            target=f"{wl.namespace}/{wl.name}",
            passed=False,
            details=f"Pod not ready (phase: {phase}, containers_ready: {all_containers_ready})",
            observed={"phase": phase, "containers_ready": all_containers_ready},
        )

    def _verify_alerts(self, alerts_must_clear: tuple[str, ...]) -> CheckResult:
        """Verify that target alerts are no longer actively firing in Alertmanager."""
        if not alerts_must_clear:
            return CheckResult(
                name="alerts_cleared",
                target="alertmanager",
                passed=True,
                details="No specific alerts required to clear",
            )

        res = self._alert_list(active_only=True)
        if not res.get("success", False):
            err_msg = str(res.get("error") or "alertmanager_query_failed")
            return CheckResult(
                name="alerts_cleared",
                target="alertmanager",
                passed=False,
                details=f"Failed to query Alertmanager: {err_msg}",
            )

        active_alerts = res.get("alerts", [])
        active_names = {
            str(a.get("alertname") or "").strip()
            for a in active_alerts
            if a.get("status") in (None, "active", "firing")
        }

        firing_conflicts = [name for name in alerts_must_clear if name in active_names]
        if firing_conflicts:
            return CheckResult(
                name="alerts_cleared",
                target="alertmanager",
                passed=False,
                details=f"Alerts still actively firing: {', '.join(firing_conflicts)}",
                observed={"firing_alerts": list(firing_conflicts), "total_active": len(active_alerts)},
            )

        return CheckResult(
            name="alerts_cleared",
            target="alertmanager",
            passed=True,
            details=f"All {len(alerts_must_clear)} target alert(s) cleared",
            observed={"cleared_alerts": list(alerts_must_clear), "total_active": len(active_alerts)},
        )

    def _verify_metric(self, metric: MetricPredicate) -> tuple[CheckResult, float | None]:
        """Verify that a PromQL metric satisfies its health condition."""
        cmp_fn = _OPERATORS.get(metric.operator)
        if not cmp_fn:
            return CheckResult(
                name=f"metric_{metric.query[:30]}",
                target="prometheus",
                passed=False,
                details=f"Unknown operator: {metric.operator}",
            ), None

        res = self._promql_query(metric.query)
        if not res.get("success", False):
            err_msg = str(res.get("error") or "promql_query_failed")
            return CheckResult(
                name=f"metric_{metric.query[:30]}",
                target="prometheus",
                passed=False,
                details=f"PromQL query failed: {err_msg}",
            ), None

        result_data = res.get("result", [])
        if not result_data:
            # Query returned empty vector — if checking error rate < threshold, 0 is often passed
            val = 0.0
        else:
            try:
                # Prometheus vector format: [{'metric': {...}, 'value': [1786542619, '0.02']}]
                first_item = result_data[0]
                if isinstance(first_item, dict) and "value" in first_item:
                    val = float(first_item["value"][1])
                elif isinstance(first_item, (int, float)):
                    val = float(first_item)
                else:
                    val = float(first_item.get("value", 0.0))
            except (ValueError, TypeError, IndexError):
                val = 0.0

        passed = cmp_fn(val, metric.threshold)
        desc = metric.description or f"{metric.query} {metric.operator} {metric.threshold}"
        if passed:
            details = f"Passed: observed {val:.4f} {metric.operator} {metric.threshold} ({desc})"
        else:
            details = f"Threshold breached: observed {val:.4f} not {metric.operator} {metric.threshold} ({desc})"

        return CheckResult(
            name=f"metric_{metric.query[:30]}",
            target="prometheus",
            passed=passed,
            details=details,
            observed=val,
        ), val

    def _verify_chaos_clearance(self, scenario_id: str) -> CheckResult:
        """Verify that Chaos Mesh resources applied for the scenario are removed/inactive."""
        res = self._kubectl_get("podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos", namespace="-A", output="json")
        if not res.get("success", False):
            # If kubectl query fails with error, report communication error
            err_msg = str(res.get("error") or res.get("stderr") or "chaos_query_failed")
            return CheckResult(
                name="chaos_mesh_cleared",
                target="chaos-mesh",
                passed=False,
                details=f"Failed to query Chaos Mesh resources: {err_msg}",
            )

        parsed = res.get("parsed") or {}
        items = parsed.get("items", []) if isinstance(parsed, dict) else []
        chaos_kinds = {"podchaos", "networkchaos", "stresschaos", "dnschaos", "iochaos", "timechaos"}
        active_chaos = [
            item for item in items
            if str(item.get("kind", "")).lower() in chaos_kinds
        ]
        if not active_chaos:
            return CheckResult(
                name="chaos_mesh_cleared",
                target="chaos-mesh",
                passed=True,
                details="Zero active Chaos Mesh experiment resources present in cluster",
            )

        # Check if active experiments remain
        active_chaos_names = [
            f"{item.get('kind', 'Chaos')}/{item.get('metadata', {}).get('name', 'unknown')}"
            for item in active_chaos
        ]
        return CheckResult(
            name="chaos_mesh_cleared",
            target="chaos-mesh",
            passed=False,
            details=f"Active chaos experiment resources remain: {', '.join(active_chaos_names[:5])}",
            observed={"active_chaos_count": len(active_chaos), "active_experiments": active_chaos_names},
        )


# ── Global Singleton Instance & Convenience Entry Point ───────────────────────

environment_verifier = EnvironmentVerifier()


def verify_environment(
    scenario_id: str,
    agent_claimed_resolved: bool,
    alert: dict[str, Any] | None = None,
    incident_context: dict[str, Any] | None = None,
) -> EnvironmentVerificationResult:
    """Convenience functional interface to verify environment recovery."""
    return environment_verifier.verify(
        scenario_id=scenario_id,
        agent_claimed_resolved=agent_claimed_resolved,
        alert=alert,
        incident_context=incident_context,
    )
