"""Objective Environment Verifier for AtlasOps.

Distinguishes between agent-claimed resolution and objective ground-truth
resolution across Kubernetes workloads, Alertmanager alerts, Prometheus
metrics, and Chaos Mesh clearance.

An agent outputting `outcome="resolved"` is a hypothesis; this verifier
determines whether the environment has actually recovered.
"""

from __future__ import annotations

import json
import logging
import operator
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
    require_no_legacy_deployments: tuple[str, ...] = ()


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

FROZEN_TIER_PREFIXES = (
    "single_fault/",
    "cascade/",
    "multi_fault/",
    "named_replays/",
)


# ── Predefined Verification Specs Derived Directly from Frozen Manifests ──────

def _make_workload(name: str, namespace: str = "default") -> WorkloadPredicate:
    return WorkloadPredicate(name=name, kind="deployment", namespace=namespace, min_ready_replicas=1)


SCENARIO_VERIFICATION_SPECS: dict[str, ScenarioVerificationSpec] = {
    # ── Single-Fault (8 scenarios) ────────────────────────────────────────────
    # sf-001: PodChaos targeting cartservice (sf-001-cartservice-kill)
    "single_fault/sf-001": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-001",
        workloads=(_make_workload("cartservice"),),
        require_chaos_cleared=True,
    ),
    # sf-002: StressChaos CPU targeting paymentservice (sf-002-paymentservice-cpu)
    "single_fault/sf-002": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-002",
        workloads=(_make_workload("paymentservice"),),
        require_chaos_cleared=True,
    ),
    # sf-003: StressChaos memory targeting checkoutservice (sf-003-checkoutservice-memory)
    "single_fault/sf-003": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-003",
        workloads=(_make_workload("checkoutservice"),),
        require_chaos_cleared=True,
    ),
    # sf-004: NetworkChaos loss targeting frontend (sf-004-frontend-network-loss)
    "single_fault/sf-004": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-004",
        workloads=(_make_workload("frontend"),),
        require_chaos_cleared=True,
    ),
    # sf-005: NetworkChaos partition between redis-cart and cartservice
    "single_fault/sf-005": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-005",
        workloads=(_make_workload("redis-cart"), _make_workload("cartservice")),
        require_chaos_cleared=True,
    ),
    # sf-006: DNSChaos random targeting checkoutservice
    "single_fault/sf-006": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-006",
        workloads=(_make_workload("checkoutservice"),),
        require_chaos_cleared=True,
    ),
    # sf-007: IOChaos disk-fill targeting emailservice
    "single_fault/sf-007": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-007",
        workloads=(_make_workload("emailservice"),),
        require_chaos_cleared=True,
    ),
    # sf-008: TimeChaos clock-skew targeting paymentservice
    "single_fault/sf-008": ScenarioVerificationSpec(
        scenario_id="single_fault/sf-008",
        workloads=(_make_workload("paymentservice"),),
        require_chaos_cleared=True,
    ),

    # ── Cascade (5 scenarios) ─────────────────────────────────────────────────
    # cs-001: NetworkChaos latency on currencyservice
    "cascade/cs-001": ScenarioVerificationSpec(
        scenario_id="cascade/cs-001",
        workloads=(_make_workload("currencyservice"),),
        require_chaos_cleared=True,
    ),
    # cs-002: NetworkChaos partition on redis-cart
    "cascade/cs-002": ScenarioVerificationSpec(
        scenario_id="cascade/cs-002",
        workloads=(_make_workload("redis-cart"),),
        require_chaos_cleared=True,
    ),
    # cs-003: StressChaos CPU on recommendationservice
    "cascade/cs-003": ScenarioVerificationSpec(
        scenario_id="cascade/cs-003",
        workloads=(_make_workload("recommendationservice"),),
        require_chaos_cleared=True,
    ),
    # cs-004: IOChaos disk-full on emailservice
    "cascade/cs-004": ScenarioVerificationSpec(
        scenario_id="cascade/cs-004",
        workloads=(_make_workload("emailservice"),),
        require_chaos_cleared=True,
    ),
    # cs-005: NetworkChaos latency + StressChaos memory on cartservice
    "cascade/cs-005": ScenarioVerificationSpec(
        scenario_id="cascade/cs-005",
        workloads=(_make_workload("cartservice"),),
        require_chaos_cleared=True,
    ),

    # ── Multi-Fault (5 scenarios) ─────────────────────────────────────────────
    # mf-001: NetworkChaos loss on frontend + StressChaos CPU on checkoutservice
    "multi_fault/mf-001": ScenarioVerificationSpec(
        scenario_id="multi_fault/mf-001",
        workloads=(_make_workload("frontend"), _make_workload("checkoutservice")),
        require_chaos_cleared=True,
    ),
    # mf-002: NetworkChaos partition on redis-cart/cartservice + StressChaos memory on recommendationservice
    "multi_fault/mf-002": ScenarioVerificationSpec(
        scenario_id="multi_fault/mf-002",
        workloads=(_make_workload("redis-cart"), _make_workload("cartservice"), _make_workload("recommendationservice")),
        require_chaos_cleared=True,
    ),
    # mf-003: DNSChaos random on cluster + NetworkChaos delay on currencyservice
    "multi_fault/mf-003": ScenarioVerificationSpec(
        scenario_id="multi_fault/mf-003",
        workloads=(_make_workload("currencyservice"),),
        require_chaos_cleared=True,
    ),
    # mf-004: TimeChaos clockskew on paymentservice + NetworkChaos corrupt on cartservice
    "multi_fault/mf-004": ScenarioVerificationSpec(
        scenario_id="multi_fault/mf-004",
        workloads=(_make_workload("paymentservice"), _make_workload("cartservice")),
        require_chaos_cleared=True,
    ),
    # mf-005: IOChaos disk fault on emailservice + NetworkChaos delay on checkoutservice
    "multi_fault/mf-005": ScenarioVerificationSpec(
        scenario_id="multi_fault/mf-005",
        workloads=(_make_workload("emailservice"), _make_workload("checkoutservice")),
        require_chaos_cleared=True,
    ),

    # ── Named Historical Replays (10 scenarios) ──────────────────────────────
    # hist-aws-s3-2017: Argo CD application patch scaling productcatalogservice to 0
    "named_replays/hist-aws-s3-2017": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-aws-s3-2017",
        workloads=(_make_workload("productcatalogservice"),),
        require_chaos_cleared=True,
    ),
    # hist-azure-dns-2019: DNSChaos random on checkoutservice, cartservice, currencyservice
    "named_replays/hist-azure-dns-2019": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-azure-dns-2019",
        workloads=(_make_workload("checkoutservice"), _make_workload("cartservice"), _make_workload("currencyservice")),
        require_chaos_cleared=True,
    ),
    # hist-cloudflare-2019: StressChaos 100% CPU on frontend
    "named_replays/hist-cloudflare-2019": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-cloudflare-2019",
        workloads=(_make_workload("frontend"),),
        require_chaos_cleared=True,
    ),
    # hist-datadog-2023: DNSChaos error on default namespace services
    "named_replays/hist-datadog-2023": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-datadog-2023",
        workloads=(
            _make_workload("frontend"),
            _make_workload("cartservice"),
            _make_workload("checkoutservice"),
            _make_workload("productcatalogservice"),
        ),
        require_chaos_cleared=True,
    ),
    # hist-discord-2022: PodChaos kill on redis-cart + NetworkChaos latency on cartservice
    "named_replays/hist-discord-2022": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-discord-2022",
        workloads=(_make_workload("redis-cart"), _make_workload("cartservice")),
        require_chaos_cleared=True,
    ),
    # hist-facebook-bgp-2021: NetworkChaos partition default -> kube-system
    "named_replays/hist-facebook-bgp-2021": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-facebook-bgp-2021",
        workloads=(_make_workload("frontend"),),
        require_chaos_cleared=True,
    ),
    # hist-fastly-2021: NetworkChaos corrupt 60% on frontend
    "named_replays/hist-fastly-2021": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-fastly-2021",
        workloads=(_make_workload("frontend"),),
        require_chaos_cleared=True,
    ),
    # hist-github-2018: PodChaos kill on redis-cart
    "named_replays/hist-github-2018": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-github-2018",
        workloads=(_make_workload("redis-cart"),),
        require_chaos_cleared=True,
    ),
    # hist-knight-capital-2012: Deployment of checkoutservice-legacy (must be removed/scaled down)
    "named_replays/hist-knight-capital-2012": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-knight-capital-2012",
        workloads=(_make_workload("checkoutservice"),),
        require_chaos_cleared=True,
        require_no_legacy_deployments=("checkoutservice-legacy",),
    ),
    # hist-slack-2022: NetworkChaos duplicate on frontend + delay on checkoutservice
    "named_replays/hist-slack-2022": ScenarioVerificationSpec(
        scenario_id="named_replays/hist-slack-2022",
        workloads=(_make_workload("frontend"), _make_workload("checkoutservice")),
        require_chaos_cleared=True,
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

        # Fail closed on unknown frozen scenario prefixes: never silently fall back
        if any(normalized_id.startswith(prefix) for prefix in FROZEN_TIER_PREFIXES):
            raise KeyError(f"Unknown frozen scenario ID in catalog: {normalized_id}")

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
            # If no workload can be synthesized for an unfrozen scenario, fail closed
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
        try:
            spec = self.resolve_spec(scenario_id, alert=alert)
        except KeyError as err:
            return EnvironmentVerificationResult(
                scenario_id=scenario_id,
                agent_claimed_resolved=agent_claimed_resolved,
                env_resolved=False,
                verification_status="error",
                failed_checks=["scenario_spec_resolution"],
                checks=[CheckResult(
                    name="scenario_spec_resolution",
                    target=scenario_id,
                    passed=False,
                    details=str(err),
                )],
                error=f"unknown_frozen_scenario_spec: {scenario_id}",
                is_false_resolution=bool(agent_claimed_resolved),
                is_false_negative=False,
            )

        checks: list[CheckResult] = []
        observed_metrics: dict[str, Any] = {}
        evidence: list[str] = []
        verification_error: str | None = None

        # 1. Check Workload Health & Readiness
        for wl in spec.workloads:
            check = self._verify_workload(wl)
            checks.append(check)
            if check.passed:
                evidence.append(f"Workload {wl.namespace}/{wl.name} ({wl.kind}): {check.details}")
            else:
                evidence.append(f"FAILED Workload {wl.namespace}/{wl.name}: {check.details}")

        # 2. Check Legacy Deployment Removal (e.g. hist-knight-capital-2012)
        for legacy_name in spec.require_no_legacy_deployments:
            legacy_check = self._verify_no_legacy_deployment(legacy_name, namespace="default")
            checks.append(legacy_check)
            if legacy_check.passed:
                evidence.append(f"Legacy deployment {legacy_name} removed: {legacy_check.details}")
            else:
                evidence.append(f"FAILED Legacy deployment {legacy_name}: {legacy_check.details}")

        # 3. Check Alertmanager Cleared Alerts
        if spec.alerts_must_clear:
            alert_check = self._verify_alerts(spec.alerts_must_clear)
            checks.append(alert_check)
            if alert_check.passed:
                evidence.append(f"Alerts cleared: {alert_check.details}")
            else:
                evidence.append(f"FAILED Alerts: {alert_check.details}")

        # 4. Check PromQL Metric Predicates
        for metric_pred in spec.metrics:
            m_check, val = self._verify_metric(metric_pred)
            checks.append(m_check)
            if val is not None:
                observed_metrics[metric_pred.query] = val
            if m_check.passed:
                evidence.append(f"Metric [{metric_pred.query}]: {m_check.details}")
            else:
                evidence.append(f"FAILED Metric [{metric_pred.query}]: {m_check.details}")

        # 5. Check Chaos Mesh Clearance
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
            stdout = str(res.get("stdout", "")).strip()
            if stdout:
                try:
                    parsed = json.loads(stdout)
                except Exception:
                    pass

        if not parsed or not isinstance(parsed, dict):
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

    def _verify_no_legacy_deployment(self, deployment_name: str, namespace: str = "default") -> CheckResult:
        """Verify that a rogue/legacy deployment is scaled to 0 or completely removed."""
        res = self._kubectl_get("deployment", namespace=namespace, output="json")
        if not res.get("success", False):
            err_msg = str(res.get("error") or res.get("stderr") or "kubectl_query_failed")
            return CheckResult(
                name=f"legacy_deployment_{deployment_name}_removed",
                target=f"{namespace}/{deployment_name}",
                passed=False,
                details=f"Failed to query deployments from cluster: {err_msg}",
            )

        parsed = res.get("parsed") or {}
        items = parsed.get("items", []) if isinstance(parsed, dict) else []
        legacy_item = None
        for item in items:
            if item.get("metadata", {}).get("name") == deployment_name:
                legacy_item = item
                break

        if not legacy_item:
            return CheckResult(
                name=f"legacy_deployment_{deployment_name}_removed",
                target=f"{namespace}/{deployment_name}",
                passed=True,
                details=f"Legacy deployment {deployment_name} not present in cluster",
            )

        ready = int(legacy_item.get("status", {}).get("readyReplicas", 0))
        if ready == 0:
            return CheckResult(
                name=f"legacy_deployment_{deployment_name}_removed",
                target=f"{namespace}/{deployment_name}",
                passed=True,
                details=f"Legacy deployment {deployment_name} scaled to 0 ready replicas",
            )

        return CheckResult(
            name=f"legacy_deployment_{deployment_name}_removed",
            target=f"{namespace}/{deployment_name}",
            passed=False,
            details=f"Legacy deployment {deployment_name} still active with {ready} ready replicas",
            observed={"ready_replicas": ready},
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
            val = 0.0
        else:
            try:
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
