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
import math
import operator
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

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

OPERATIONAL_AVAILABILITY_ALERT = "AtlasOpsOnlineBoutiqueDeploymentUnavailable"
OPERATIONAL_SAMPLE_MAX_AGE_SECONDS = 60
OPERATIONAL_WORKLOAD_NAMESPACE_ENV = "ATLASOPS_WORKLOAD_NAMESPACE"
_OPERATIONAL_TARGET_LABELS = ("deployment", "service", "app")
_KUBERNETES_NAMESPACE_PATTERN = re.compile(
    r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?"
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


def _parse_kubernetes_items(
    response: Any,
    *,
    expected_api_version: str | None = None,
    expected_list_kind: str | None = None,
    expected_item_kind: str | None = None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Parse a successful Kubernetes list response without inferring absence."""
    if not isinstance(response, dict) or response.get("success") is not True:
        return None, "Kubernetes query did not return success"

    parsed = response.get("parsed")
    if not parsed:
        stdout = response.get("stdout")
        if not isinstance(stdout, str) or not stdout.strip():
            return None, "Kubernetes response did not contain parseable JSON"
        try:
            parsed = json.loads(stdout)
        except (TypeError, ValueError):
            return None, "Kubernetes response did not contain parseable JSON"

    if not isinstance(parsed, dict):
        return None, "Kubernetes response JSON was not an object"

    if expected_api_version is not None and parsed.get("apiVersion") != expected_api_version:
        return None, "Kubernetes response apiVersion was missing or unexpected"

    is_list = "items" in parsed
    if is_list:
        if expected_list_kind is not None and parsed.get("kind") != expected_list_kind:
            return None, "Kubernetes response kind was missing or unexpected"
        items = parsed["items"]
    elif isinstance(parsed.get("metadata"), dict):
        if expected_item_kind is not None and parsed.get("kind") != expected_item_kind:
            return None, "Kubernetes object kind was missing or unexpected"
        items = [parsed]
    else:
        return None, "Kubernetes response did not contain a resource list"

    if not isinstance(items, list):
        return None, "Kubernetes resource list items were malformed"

    for index, item in enumerate(items):
        metadata = item.get("metadata") if isinstance(item, dict) else None
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if not isinstance(name, str) or not name or name != name.strip():
            return None, f"Kubernetes resource list item {index} had no valid metadata.name"
        if expected_api_version is not None and item.get("apiVersion") != expected_api_version:
            return None, f"Kubernetes resource list item {index} had an unexpected apiVersion"
        if expected_item_kind is not None and item.get("kind") != expected_item_kind:
            return None, f"Kubernetes resource list item {index} had an unexpected kind"

    return items, None


def _configured_operational_namespace() -> tuple[str | None, str | None]:
    """Read the operational workload namespace without accepting unsafe labels."""
    namespace = os.getenv(OPERATIONAL_WORKLOAD_NAMESPACE_ENV, "default")
    if _KUBERNETES_NAMESPACE_PATTERN.fullmatch(namespace) is None:
        return (
            None,
            f"{OPERATIONAL_WORKLOAD_NAMESPACE_ENV} is missing or not a valid Kubernetes namespace",
        )
    return namespace, None


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


@dataclass(frozen=True)
class _OperationalTarget:
    deployment: str
    namespace: str
    uid: str | None = None
    server: str | None = None
    context: str | None = None
    api_version: str | None = None
    kind: str | None = None
    resource_version: str | None = None


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

    @staticmethod
    def _has_operational_availability_alert(alert: dict[str, Any] | None) -> bool:
        if not isinstance(alert, dict):
            return False

        common_labels = alert.get("commonLabels")
        if (
            isinstance(common_labels, dict)
            and common_labels.get("alertname") == OPERATIONAL_AVAILABILITY_ALERT
        ):
            return True

        alert_items = alert.get("alerts")
        if not isinstance(alert_items, list):
            return False
        return any(
            isinstance(item, dict)
            and isinstance(item.get("labels"), dict)
            and item["labels"].get("alertname") == OPERATIONAL_AVAILABILITY_ALERT
            for item in alert_items
        )

    @staticmethod
    def _resolve_operational_target(
        alert: dict[str, Any],
        workload_namespace: str,
    ) -> tuple[_OperationalTarget | None, str | None]:
        common_labels = alert.get("commonLabels", {})
        if not isinstance(common_labels, dict):
            return None, "commonLabels must be an object"

        alert_items = alert.get("alerts", [])
        if not isinstance(alert_items, list):
            return None, "alerts must be a list"
        if not alert_items:
            return None, "alerts must contain at least one alert"

        individual_labels = []
        for index, item in enumerate(alert_items):
            labels = item.get("labels") if isinstance(item, dict) else None
            if not isinstance(labels, dict):
                return None, f"alerts[{index}].labels must be an object"
            if not labels:
                return None, f"alerts[{index}].labels must not be empty"
            individual_labels.append(labels)

        label_sets = [common_labels, *individual_labels]
        explicit_alert_names: list[str] = []
        for labels in label_sets:
            if "alertname" not in labels:
                continue
            value = labels["alertname"]
            if not isinstance(value, str) or not value or value != value.strip():
                return None, "alertname label is malformed"
            explicit_alert_names.append(value)

        if (
            not explicit_alert_names
            or any(name != OPERATIONAL_AVAILABILITY_ALERT for name in explicit_alert_names)
            or (
                "alertname" not in common_labels
                and any("alertname" not in labels for labels in individual_labels)
            )
        ):
            return None, "alertname labels do not identify one operational availability alert"

        def read_label(labels: dict[str, Any], name: str) -> tuple[str | None, str | None]:
            if name not in labels:
                return None, None
            value = labels[name]
            if not isinstance(value, str) or not value or value != value.strip():
                return None, f"{name} label is malformed"
            return value, None

        targets: list[str | None] = []
        namespaces: list[str | None] = []
        for labels in label_sets:
            source_targets = []
            for key in _OPERATIONAL_TARGET_LABELS:
                value, error = read_label(labels, key)
                if error:
                    return None, error
                if value is not None:
                    if value not in ONLINE_BOUTIQUE_SERVICES:
                        return None, f"unknown Online Boutique deployment label: {value}"
                    source_targets.append(value)
            if len(set(source_targets)) > 1:
                return None, "deployment, service, and app labels conflict"
            targets.append(source_targets[0] if source_targets else None)

            namespace, error = read_label(labels, "namespace")
            if error:
                return None, error
            namespaces.append(namespace)

        explicit_targets = {value for value in targets if value is not None}
        if len(explicit_targets) != 1:
            return None, "deployment target is missing or ambiguous"
        deployment = next(iter(explicit_targets))
        if targets[0] is None and any(value is None for value in targets[1:]):
            return None, "each individual alert must resolve to the deployment target"

        explicit_namespaces = {value for value in namespaces if value is not None}
        if len(explicit_namespaces) != 1:
            return None, "namespace is missing or ambiguous"
        namespace = next(iter(explicit_namespaces))
        if namespace != workload_namespace:
            return (
                None,
                (
                    "operational availability alert namespace must match the configured "
                    f"workload namespace {workload_namespace}, got {namespace}"
                ),
            )
        if namespaces[0] is None and any(value is None for value in namespaces[1:]):
            return None, (
                "each individual alert must resolve to the configured workload namespace"
            )

        return _OperationalTarget(deployment=deployment, namespace=namespace), None

    @staticmethod
    def _resolve_operational_context_target(
        incident_context: dict[str, Any],
        workload_namespace: str,
    ) -> tuple[_OperationalTarget | None, str | None]:
        action = incident_context.get("action")
        if action is None:
            remediation = incident_context.get("remediation")
            final = remediation.get("final") if isinstance(remediation, dict) else None
            executed_actions = (
                final.get("executed_actions")
                if isinstance(final, dict)
                else None
            )
            if isinstance(executed_actions, list):
                action = next(
                    (
                        candidate
                        for candidate in reversed(executed_actions)
                        if isinstance(candidate, dict) and "prepared_action" in candidate
                    ),
                    None,
                )

        if not isinstance(action, dict):
            return None, "operational context must include the executed prepared action"

        prepared_action = action.get("prepared_action")
        if not isinstance(prepared_action, dict):
            return None, "operational action has no prepared action"
        if (
            action.get("tool") != prepared_action.get("tool")
            or action.get("args") != prepared_action.get("arguments")
        ):
            return None, "operational action conflicts with its prepared action"

        has_verification_target = "verification_target" in prepared_action
        mutation_target = prepared_action.get("target")
        if has_verification_target:
            selected = prepared_action["verification_target"]
            if not isinstance(mutation_target, dict):
                return None, "prepared action mutation target is missing or malformed"
        else:
            selected = mutation_target
        if not isinstance(selected, dict):
            return None, "prepared action has no verification Deployment target"

        required_fields = (
            "server",
            "context",
            "api_version",
            "kind",
            "namespace",
            "name",
            "uid",
            "resource_version",
        )
        values = {}
        for key in required_fields:
            value = selected.get(key)
            if not isinstance(value, str) or not value or value != value.strip():
                return None, f"prepared action target {key} is missing or malformed"
            values[key] = value

        if has_verification_target:
            for key in ("server", "context"):
                mutation_value = mutation_target.get(key)
                if (
                    not isinstance(mutation_value, str)
                    or not mutation_value
                    or mutation_value != mutation_value.strip()
                ):
                    return None, f"prepared action mutation target {key} is missing or malformed"
                if mutation_value != values[key]:
                    return (
                        None,
                        (
                            "prepared verification target does not match the mutation "
                            f"target {key}"
                        ),
                    )

        if values["api_version"] != "apps/v1" or values["kind"] != "Deployment":
            return None, "prepared verification target is not an apps/v1 Deployment"
        if values["namespace"] != workload_namespace:
            return None, "prepared verification target namespace does not match the workload namespace"
        if values["name"] not in ONLINE_BOUTIQUE_SERVICES:
            return None, "prepared verification target is not an Online Boutique Deployment"

        return (
            _OperationalTarget(
                namespace=values["namespace"],
                deployment=values["name"],
                uid=values["uid"],
                server=values["server"],
                context=values["context"],
                api_version=values["api_version"],
                kind=values["kind"],
                resource_version=values["resource_version"],
            ),
            None,
        )

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
            # If no workload can be synthesized for an unfrozen scenario, fail closed:
            # do not invent frontend or guess a service.
            raise ValueError(
                f"dynamic_scenario_target_unresolved: unable to synthesize target workload from alert context for scenario '{normalized_id}'"
            )

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
        if (
            isinstance(scenario_id, str)
            and not scenario_id.strip()
            and self._has_operational_availability_alert(alert)
        ):
            return self._verify_operational_availability(
                alert=alert,
                agent_claimed_resolved=agent_claimed_resolved,
                incident_context=incident_context,
            )

        if (
            isinstance(scenario_id, str)
            and not scenario_id.strip()
            and isinstance(incident_context, dict)
            and incident_context.get("operational_mode") is True
        ):
            check = CheckResult(
                name="operational_policy_resolution",
                target="alert",
                passed=False,
                details="No supported operational verification policy matched the alert",
            )
            return EnvironmentVerificationResult(
                scenario_id="",
                agent_claimed_resolved=agent_claimed_resolved,
                env_resolved=False,
                verification_status="error",
                checks=[check],
                failed_checks=[check.name],
                evidence=[f"FAILED Operational policy: {check.details}"],
                is_false_resolution=bool(agent_claimed_resolved),
                error="unknown_operational_policy",
            )

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
        except ValueError as err:
            return EnvironmentVerificationResult(
                scenario_id=scenario_id,
                agent_claimed_resolved=agent_claimed_resolved,
                env_resolved=False,
                verification_status="error",
                failed_checks=["dynamic_scenario_target_resolution"],
                checks=[CheckResult(
                    name="dynamic_scenario_target_resolution",
                    target=scenario_id,
                    passed=False,
                    details=str(err),
                )],
                error="dynamic_scenario_target_unresolved",
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

    def _verify_operational_availability(
        self,
        alert: dict[str, Any] | None,
        agent_claimed_resolved: bool,
        incident_context: dict[str, Any] | None,
    ) -> EnvironmentVerificationResult:
        workload_namespace, namespace_error = _configured_operational_namespace()
        if workload_namespace is None:
            target, target_error = None, namespace_error
        else:
            target, target_error = self._resolve_operational_target(
                alert or {},
                workload_namespace,
            )
        if (
            target_error is None
            and target is not None
            and workload_namespace is not None
            and isinstance(incident_context, dict)
            and incident_context.get("operational_mode") is True
        ):
            bound_target, binding_error = self._resolve_operational_context_target(
                incident_context,
                workload_namespace,
            )
            if binding_error:
                target_error = binding_error
            elif bound_target is not None and (
                bound_target.namespace != target.namespace
                or bound_target.deployment != target.deployment
            ):
                target_error = "selected action target conflicts with alert labels"
            else:
                target = bound_target

        if target_error or target is None:
            details = target_error or "operational target could not be resolved"
            check = CheckResult(
                name="operational_target_resolution",
                target="alert",
                passed=False,
                details=details,
            )
            return EnvironmentVerificationResult(
                scenario_id="",
                agent_claimed_resolved=agent_claimed_resolved,
                env_resolved=False,
                verification_status="error",
                checks=[check],
                failed_checks=[check.name],
                evidence=[f"FAILED Operational target: {details}"],
                is_false_resolution=bool(agent_claimed_resolved),
                error="operational_target_unresolved",
            )

        deployment_check, api_desired, deployment_state = (
            self._verify_operational_deployment(target)
        )
        desired_check, desired = self._verify_operational_metric(
            "kube_deployment_spec_replicas",
            target,
        )
        available_check, available = self._verify_operational_metric(
            "kube_deployment_status_replicas_available",
            target,
        )

        if desired is None or available is None or api_desired is None:
            metric_state = None
            metric_details = "Fresh desired and availability samples were not both established"
        elif desired != api_desired:
            metric_state = None
            metric_details = (
                f"Prometheus desired replicas {desired:g} do not match the current "
                f"Deployment spec {api_desired}"
            )
        elif desired <= 0:
            metric_state = False
            metric_details = f"Prometheus desired replicas must be greater than zero, got {desired:g}"
        elif available < desired:
            metric_state = False
            metric_details = (
                f"Prometheus available replicas {available:g} are below desired replicas {desired:g}"
            )
        else:
            metric_state = True
            metric_details = (
                f"Prometheus reports {available:g} available of {desired:g} desired replicas"
            )

        metrics_check = CheckResult(
            name="operational_kube_state_metrics_recovered",
            target=f"{target.namespace}/{target.deployment}",
            passed=metric_state is True,
            details=metric_details,
            observed={
                "deployment_spec_replicas": desired,
                "deployment_status_replicas_available": available,
                "api_spec_replicas": api_desired,
            },
        )
        alert_check, alert_state = self._verify_operational_alert_cleared(target)

        checks = [
            deployment_check,
            desired_check,
            available_check,
            metrics_check,
            alert_check,
        ]
        states = (deployment_state, metric_state, alert_state)
        if all(state is True for state in states):
            verification_status = "passed"
            verification_error = None
            env_resolved = True
        elif any(state is False for state in states):
            verification_status = "failed"
            verification_error = None
            env_resolved = False
        else:
            verification_status = "inconclusive"
            verification_error = "operational_telemetry_inconclusive"
            env_resolved = False

        evidence = [
            (
                f"{'Operational check' if check.passed else 'FAILED operational check'} "
                f"{check.name}: {check.details}"
            )
            for check in checks
        ]
        observed_metrics = {}
        if desired is not None:
            observed_metrics["kube_deployment_spec_replicas"] = desired
        if available is not None:
            observed_metrics["kube_deployment_status_replicas_available"] = available
        failed_checks = [check.name for check in checks if not check.passed and check.required]

        return EnvironmentVerificationResult(
            scenario_id="",
            agent_claimed_resolved=agent_claimed_resolved,
            env_resolved=env_resolved,
            verification_status=verification_status,
            checks=checks,
            failed_checks=failed_checks,
            observed_metrics=observed_metrics,
            evidence=evidence,
            verification_timestamp=time.time(),
            is_false_resolution=bool(agent_claimed_resolved and not env_resolved),
            is_false_negative=bool(not agent_claimed_resolved and env_resolved),
            error=verification_error,
        )

    def _verify_operational_deployment(
        self,
        target: _OperationalTarget,
    ) -> tuple[CheckResult, int | None, bool | None]:
        name = "operational_deployment_current"
        resource_target = f"{target.namespace}/{target.deployment}"
        try:
            response = self._kubectl_get(
                f"deployment/{target.deployment}",
                namespace=target.namespace,
                output="json",
            )
        # Tool adapters can raise client-specific transport errors; none proves recovery.
        except Exception as error:  # noqa: BLE001
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details=f"Failed to query current Deployment status: {error}",
                ),
                None,
                None,
            )

        if not isinstance(response, dict) or response.get("success") is not True:
            error = (
                response.get("error") or response.get("stderr") or "kubectl_query_failed"
                if isinstance(response, dict)
                else "kubectl_query_failed"
            )
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details=f"Failed to query current Deployment status: {error}",
                ),
                None,
                None,
            )

        items, parse_error = _parse_kubernetes_items(
            response,
            expected_api_version="apps/v1",
            expected_list_kind="DeploymentList",
            expected_item_kind="Deployment",
        )
        if parse_error:
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details=f"Could not establish current Deployment status: {parse_error}",
                ),
                None,
                None,
            )

        for index, item in enumerate(items):
            metadata = item.get("metadata")
            namespace = metadata.get("namespace") if isinstance(metadata, dict) else None
            if (
                not isinstance(namespace, str)
                or not namespace
                or namespace != namespace.strip()
            ):
                return (
                    CheckResult(
                        name=name,
                        target=resource_target,
                        passed=False,
                        details=(
                            f"Deployment response item {index} did not identify its namespace"
                        ),
                    ),
                    None,
                    None,
                )

        same_name = [
            item for item in items
            if item.get("metadata", {}).get("name") == target.deployment
        ]

        matches = [
            item for item in same_name
            if item.get("metadata", {}).get("namespace") == target.namespace
        ]
        if not matches:
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details=f"Deployment {resource_target} was not present",
                ),
                None,
                False,
            )
        if len(matches) != 1:
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details="Deployment response contained multiple exact target objects",
                ),
                None,
                None,
            )

        deployment = matches[0]
        metadata = deployment.get("metadata")
        spec = deployment.get("spec")
        status = deployment.get("status")
        if (
            deployment.get("kind") != "Deployment"
            or not isinstance(metadata, dict)
            or not isinstance(spec, dict)
            or not isinstance(status, dict)
        ):
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details="Exact target object was not a complete Deployment status",
                ),
                None,
                None,
            )
        current_uid = metadata.get("uid")
        current_resource_version = metadata.get("resourceVersion")
        if (
            not isinstance(current_uid, str)
            or not current_uid
            or current_uid != current_uid.strip()
            or not isinstance(current_resource_version, str)
            or not current_resource_version
            or current_resource_version != current_resource_version.strip()
        ):
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details="Current Deployment UID or resourceVersion was missing or malformed",
                ),
                None,
                None,
            )
        if target.uid is not None and current_uid != target.uid:
            desired = spec.get("replicas")
            desired_replicas = (
                desired if type(desired) is int and desired >= 0 else None
            )
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details="Current Deployment UID does not match the prepared action target",
                    observed={
                        "prepared_target_uid": target.uid,
                        "prepared_target_resource_version": target.resource_version,
                        "current_deployment_uid": current_uid,
                        "current_resource_version": current_resource_version,
                    },
                ),
                desired_replicas,
                None,
            )

        generation = metadata.get("generation")
        observed_generation = status.get("observedGeneration")
        desired = spec.get("replicas")
        ready = status.get("readyReplicas")
        available = status.get("availableReplicas")
        numeric_values = (generation, observed_generation, desired, ready, available)
        if any(type(value) is not int or value < 0 for value in numeric_values):
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details="Deployment generation or replica counts were missing or malformed",
                ),
                None,
                None,
            )
        if observed_generation < generation:
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details=(
                        f"Deployment status is stale: observedGeneration "
                        f"{observed_generation} is below generation {generation}"
                    ),
                    observed={
                        "generation": generation,
                        "observed_generation": observed_generation,
                    },
                ),
                desired,
                None,
            )

        observed = {
            "api_version": deployment["apiVersion"],
            "kind": deployment["kind"],
            "name": metadata["name"],
            "namespace": metadata["namespace"],
            "uid": current_uid,
            "resource_version": current_resource_version,
            "prepared_target_uid": target.uid,
            "prepared_target_resource_version": target.resource_version,
            "generation": generation,
            "observed_generation": observed_generation,
            "desired_replicas": desired,
            "ready_replicas": ready,
            "available_replicas": available,
        }
        if desired == 0:
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details="Deployment desired replicas must be greater than zero",
                    observed=observed,
                ),
                desired,
                False,
            )
        if ready < desired or available < desired:
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details=(
                        f"Deployment is not fully ready: ready {ready}/{desired}, "
                        f"available {available}/{desired}"
                    ),
                    observed=observed,
                ),
                desired,
                False,
            )

        return (
            CheckResult(
                name=name,
                target=resource_target,
                passed=True,
                details=(
                    f"Current Deployment generation {observed_generation} has "
                    f"{ready}/{desired} ready and {available}/{desired} available replicas"
                ),
                observed=observed,
            ),
            desired,
            True,
        )

    @staticmethod
    def _operational_metric_selector(metric_name: str, target: _OperationalTarget) -> str:
        return (
            f'{metric_name}{{namespace="{target.namespace}",'
            f'deployment="{target.deployment}"}}'
        )

    @classmethod
    def _operational_metric_query(
        cls,
        metric_name: str,
        target: _OperationalTarget,
    ) -> str:
        metric = cls._operational_metric_selector(metric_name, target)
        sample_age = f"time() - timestamp({metric})"
        return (
            "max by (namespace, deployment) ("
            f"{metric} and ({sample_age} >= 0) "
            f"and ({sample_age} <= {OPERATIONAL_SAMPLE_MAX_AGE_SECONDS})"
            ")"
        )

    @classmethod
    def _operational_sample_age_query(
        cls,
        metric_name: str,
        target: _OperationalTarget,
    ) -> str:
        metric = cls._operational_metric_selector(metric_name, target)
        sample_age = f"time() - timestamp({metric})"
        return (
            "max by (namespace, deployment) ("
            f"{sample_age} and ({sample_age} >= 0) "
            f"and ({sample_age} <= {OPERATIONAL_SAMPLE_MAX_AGE_SECONDS})"
            ")"
        )

    def _verify_operational_metric(
        self,
        metric_name: str,
        target: _OperationalTarget,
    ) -> tuple[CheckResult, float | None]:
        name = f"operational_metric_{metric_name}_fresh"
        resource_target = f"{target.namespace}/{target.deployment}"
        query = self._operational_metric_query(metric_name, target)
        sample_age_query = self._operational_sample_age_query(metric_name, target)
        query_time = time.time()

        def inconclusive(details: str) -> tuple[CheckResult, None]:
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details=details,
                    observed={
                        "query": query,
                        "sample_age_query": sample_age_query,
                    },
                ),
                None,
            )

        try:
            response = self._promql_query(query, time_unix=query_time)
            age_response = self._promql_query(
                sample_age_query,
                time_unix=query_time,
            )
        # Any query-side exception is missing evidence, regardless of adapter type.
        except Exception as error:  # noqa: BLE001
            return inconclusive(f"Prometheus query failed: {error}")

        def read_series_sample(
            response: Any,
            query_name: str,
        ) -> tuple[float | None, float | None, str | None]:
            if not isinstance(response, dict) or response.get("success") is not True:
                error = (
                    response.get("error") or "promql_query_failed"
                    if isinstance(response, dict)
                    else "promql_query_failed"
                )
                return None, None, f"Prometheus {query_name} query failed: {error}"
            if response.get("resultType") != "vector":
                return None, None, f"Prometheus {query_name} query returned a non-vector response"

            result = response.get("result")
            if not isinstance(result, list) or len(result) != 1:
                return (
                    None,
                    None,
                    f"Prometheus {query_name} query did not return exactly one target series",
                )

            series = result[0]
            labels = series.get("metric") if isinstance(series, dict) else None
            sample = series.get("value") if isinstance(series, dict) else None
            if (
                not isinstance(labels, dict)
                or set(labels) != {"namespace", "deployment"}
                or labels.get("namespace") != target.namespace
                or labels.get("deployment") != target.deployment
            ):
                return (
                    None,
                    None,
                    f"Prometheus {query_name} series did not match the exact deployment labels",
                )
            if (
                not isinstance(sample, (list, tuple))
                or len(sample) != 2
                or isinstance(sample[0], bool)
                or isinstance(sample[1], bool)
            ):
                return None, None, f"Prometheus {query_name} sample was malformed"
            try:
                evaluation_timestamp = float(sample[0])
                sample_value = float(sample[1])
            except (TypeError, ValueError):
                return None, None, f"Prometheus {query_name} sample was not numeric"
            if not math.isfinite(evaluation_timestamp) or not math.isfinite(sample_value):
                return None, None, f"Prometheus {query_name} sample was nonfinite"
            return evaluation_timestamp, sample_value, None

        evaluation_timestamp, value, sample_error = read_series_sample(
            response,
            "replica-count",
        )
        if sample_error:
            return inconclusive(sample_error)
        age_evaluation_timestamp, source_sample_age, age_error = read_series_sample(
            age_response,
            "source-age",
        )
        if age_error:
            return inconclusive(age_error)

        observed_at = time.time()
        if (
            evaluation_timestamp is None
            or age_evaluation_timestamp is None
            or value is None
            or source_sample_age is None
        ):
            return inconclusive("Prometheus did not establish both metric value and source age")
        if abs(evaluation_timestamp - age_evaluation_timestamp) > 0.001:
            return inconclusive("Prometheus value and source-age queries used different evaluation times")
        if value < 0 or not value.is_integer():
            return inconclusive("Prometheus series value was not a non-negative integer replica count")
        if source_sample_age < 0 or source_sample_age > OPERATIONAL_SAMPLE_MAX_AGE_SECONDS:
            return inconclusive("Prometheus source sample age was outside the permitted window")

        response_age = observed_at - age_evaluation_timestamp
        sample_age_at_observation = source_sample_age + response_age
        if (
            response_age < 0
            or response_age > OPERATIONAL_SAMPLE_MAX_AGE_SECONDS
            or sample_age_at_observation < 0
            or sample_age_at_observation > OPERATIONAL_SAMPLE_MAX_AGE_SECONDS
        ):
            return inconclusive(
                "Prometheus observation or source sample was stale or from the future"
            )
        source_sample_timestamp = age_evaluation_timestamp - source_sample_age

        return (
            CheckResult(
                name=name,
                target=resource_target,
                passed=True,
                details=(
                    f"One replica-count series had source age "
                    f"{sample_age_at_observation:.3f}s at observation; maximum "
                    f"{OPERATIONAL_SAMPLE_MAX_AGE_SECONDS}s"
                ),
                observed={
                    "value": value,
                    "evaluation_timestamp": evaluation_timestamp,
                    "source_sample_timestamp": source_sample_timestamp,
                    "source_sample_age_at_evaluation_seconds": source_sample_age,
                    "source_sample_age_at_observation_seconds": sample_age_at_observation,
                    "observed_at": observed_at,
                    "max_sample_age_seconds": OPERATIONAL_SAMPLE_MAX_AGE_SECONDS,
                    "query": query,
                    "sample_age_query": sample_age_query,
                },
            ),
            value,
        )

    def _verify_operational_alert_cleared(
        self,
        target: _OperationalTarget,
    ) -> tuple[CheckResult, bool | None]:
        name = "operational_alert_cleared"
        resource_target = f"{target.namespace}/{target.deployment}"

        def inconclusive(details: str) -> tuple[CheckResult, None]:
            return (
                CheckResult(
                    name=name,
                    target=resource_target,
                    passed=False,
                    details=details,
                ),
                None,
            )

        try:
            response = self._alert_list(active_only=True)
        # Alert-list failures cannot establish that the target alert cleared.
        except Exception as error:  # noqa: BLE001
            return inconclusive(f"Failed to query Alertmanager: {error}")
        if not isinstance(response, dict) or response.get("success") is not True:
            error = (
                response.get("error") or "alertmanager_query_failed"
                if isinstance(response, dict)
                else "alertmanager_query_failed"
            )
            return inconclusive(f"Failed to query Alertmanager: {error}")

        active_alerts = response.get("alerts")
        if not isinstance(active_alerts, list):
            return inconclusive("Alertmanager did not return a valid active alert list")

        unknown_scope = False
        same_name_other_targets = []
        for active_alert in active_alerts:
            if not isinstance(active_alert, dict):
                unknown_scope = True
                continue
            alertname = active_alert.get("alertname")
            if (
                not isinstance(alertname, str)
                or not alertname
                or alertname != alertname.strip()
            ):
                unknown_scope = True
                continue
            if alertname != OPERATIONAL_AVAILABILITY_ALERT:
                continue

            namespace = active_alert.get("namespace")
            deployment = active_alert.get("deployment")
            if (
                not isinstance(namespace, str)
                or not namespace
                or namespace != namespace.strip()
                or not isinstance(deployment, str)
                or not deployment
                or deployment != deployment.strip()
                or namespace != target.namespace
                or deployment not in ONLINE_BOUTIQUE_SERVICES
            ):
                unknown_scope = True
                continue
            if namespace == target.namespace and deployment == target.deployment:
                return (
                    CheckResult(
                        name=name,
                        target=resource_target,
                        passed=False,
                        details=(
                            "Matching operational availability alert is still present "
                            "in the active Alertmanager response"
                        ),
                        observed={
                            "alertname": alertname,
                            "namespace": namespace,
                            "deployment": deployment,
                        },
                    ),
                    False,
                )
            same_name_other_targets.append(f"{namespace}/{deployment}")

        if unknown_scope:
            return inconclusive(
                "Alertmanager returned an alert that could not be scoped to a deployment"
            )

        return (
            CheckResult(
                name=name,
                target=resource_target,
                passed=True,
                details="No matching operational availability alert is active for the target",
                observed={
                    "cleared_alertname": OPERATIONAL_AVAILABILITY_ALERT,
                    "ignored_other_targets": same_name_other_targets,
                },
            ),
            True,
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

        if not isinstance(res, dict) or res.get("success") is not True:
            err_msg = (
                str(res.get("error") or res.get("stderr") or "kubectl_query_failed")
                if isinstance(res, dict)
                else "kubectl_query_failed"
            )
            return CheckResult(
                name=f"workload_{wl.name}_ready",
                target=f"{wl.namespace}/{wl.name}",
                passed=False,
                details=f"Failed to query workload from cluster: {err_msg}",
            )

        items, parse_error = _parse_kubernetes_items(res)
        if parse_error:
            return CheckResult(
                name=f"workload_{wl.name}_ready",
                target=f"{wl.namespace}/{wl.name}",
                passed=False,
                details=f"Could not parse workload status JSON from cluster: {parse_error}",
            )

        target_item = None
        for item in items:
            metadata = item.get("metadata", {})
            if metadata.get("name") == wl.name:
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
        spec = target_item.get("spec", {})
        if not isinstance(status, dict) or not isinstance(spec, dict):
            return CheckResult(
                name=f"workload_{wl.name}_ready",
                target=f"{wl.namespace}/{wl.name}",
                passed=False,
                details="Workload status or spec JSON was malformed",
            )
        if resource_kind in ("deployment", "deployments"):
            try:
                desired = int(status.get("replicas", 0) or spec.get("replicas", 1))
                ready = int(status.get("readyReplicas", 0))
                available = int(status.get("availableReplicas", 0))
            except (TypeError, ValueError):
                return CheckResult(
                    name=f"workload_{wl.name}_ready",
                    target=f"{wl.namespace}/{wl.name}",
                    passed=False,
                    details="Workload replica counts were malformed",
                )
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
        if not isinstance(res, dict) or res.get("success") is not True:
            err_msg = (
                str(res.get("error") or res.get("stderr") or "kubectl_query_failed")
                if isinstance(res, dict)
                else "kubectl_query_failed"
            )
            return CheckResult(
                name=f"legacy_deployment_{deployment_name}_removed",
                target=f"{namespace}/{deployment_name}",
                passed=False,
                details=f"Failed to query deployments from cluster: {err_msg}",
            )

        items, parse_error = _parse_kubernetes_items(res)
        if parse_error:
            return CheckResult(
                name=f"legacy_deployment_{deployment_name}_removed",
                target=f"{namespace}/{deployment_name}",
                passed=False,
                details=f"Could not establish legacy deployment absence: {parse_error}",
            )
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

        status = legacy_item.get("status")
        if not isinstance(status, dict):
            return CheckResult(
                name=f"legacy_deployment_{deployment_name}_removed",
                target=f"{namespace}/{deployment_name}",
                passed=False,
                details="Legacy deployment status JSON was malformed",
            )
        try:
            ready = int(status.get("readyReplicas", 0))
        except (TypeError, ValueError):
            return CheckResult(
                name=f"legacy_deployment_{deployment_name}_removed",
                target=f"{namespace}/{deployment_name}",
                passed=False,
                details="Legacy deployment ready replica count was malformed",
            )
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
        if not isinstance(res, dict) or res.get("success") is not True:
            err_msg = (
                str(res.get("error") or "promql_query_failed")
                if isinstance(res, dict)
                else "promql_query_failed"
            )
            return CheckResult(
                name=f"metric_{metric.query[:30]}",
                target="prometheus",
                passed=False,
                details=f"PromQL query failed: {err_msg}",
            ), None

        result_data = res.get("result")
        if not isinstance(result_data, list) or not result_data:
            return CheckResult(
                name=f"metric_{metric.query[:30]}",
                target="prometheus",
                passed=False,
                details="PromQL returned no scalar samples",
            ), None

        first_item = result_data[0]
        if isinstance(first_item, dict):
            sample = first_item.get("value")
            if not isinstance(sample, (list, tuple)) or len(sample) != 2:
                return CheckResult(
                    name=f"metric_{metric.query[:30]}",
                    target="prometheus",
                    passed=False,
                    details="PromQL returned a malformed sample value",
                ), None
            raw_value = sample[1]
        elif isinstance(first_item, (int, float)) and not isinstance(first_item, bool):
            raw_value = first_item
        else:
            return CheckResult(
                name=f"metric_{metric.query[:30]}",
                target="prometheus",
                passed=False,
                details="PromQL returned a malformed sample",
            ), None

        try:
            val = float(raw_value)
        except (ValueError, TypeError):
            return CheckResult(
                name=f"metric_{metric.query[:30]}",
                target="prometheus",
                passed=False,
                details="PromQL sample value was not numeric",
            ), None
        if not math.isfinite(val):
            return CheckResult(
                name=f"metric_{metric.query[:30]}",
                target="prometheus",
                passed=False,
                details="PromQL sample value was nonfinite",
            ), None

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
        """Verify that Chaos Mesh resources applied for the scenario are removed/inactive.

        AtlasOps Benchmark Execution Invariant:
        In the canonical benchmark evaluation pipeline (`bench/runner.py`), scenarios
        are executed strictly serially on a dedicated cluster and reset between episodes
        (`reset_cluster()`). Therefore, under this serialized single-scenario model,
        zero active Chaos Mesh experiment resources must remain in the cluster after
        recovery.

        This check verifies cluster-wide CRD clearance while also inspecting scenario-specific
        labels and resource names for detailed diagnostic reporting.
        """
        res = self._kubectl_get("podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos", namespace="-A", output="json")
        if not isinstance(res, dict) or res.get("success") is not True:
            err_msg = (
                str(res.get("error") or res.get("stderr") or "chaos_query_failed")
                if isinstance(res, dict)
                else "chaos_query_failed"
            )
            return CheckResult(
                name="chaos_mesh_cleared",
                target="chaos-mesh",
                passed=False,
                details=f"Failed to query Chaos Mesh resources: {err_msg}",
            )

        items, parse_error = _parse_kubernetes_items(res)
        if parse_error:
            return CheckResult(
                name="chaos_mesh_cleared",
                target="chaos-mesh",
                passed=False,
                details=f"Could not establish Chaos Mesh resource absence: {parse_error}",
            )
        chaos_kinds = {"podchaos", "networkchaos", "stresschaos", "dnschaos", "iochaos", "timechaos"}
        if any(str(item.get("kind", "")).lower() not in chaos_kinds for item in items):
            return CheckResult(
                name="chaos_mesh_cleared",
                target="chaos-mesh",
                passed=False,
                details="Chaos Mesh resource list contained an unknown or missing kind",
            )
        active_chaos = items
        if not active_chaos:
            return CheckResult(
                name="chaos_mesh_cleared",
                target="chaos-mesh",
                passed=True,
                details="Zero active Chaos Mesh experiment resources present in cluster",
            )

        scenario_short = scenario_id.split("/")[-1]
        matching_chaos = [
            f"{item.get('kind', 'Chaos')}/{item.get('metadata', {}).get('name', 'unknown')}"
            for item in active_chaos
            if (
                isinstance(item.get("metadata", {}).get("labels"), dict)
                and item.get("metadata", {}).get("labels", {}).get("scenario") == scenario_short
            )
            or str(item.get("metadata", {}).get("name", "")).startswith(scenario_short)
        ]
        active_chaos_names = [
            f"{item.get('kind', 'Chaos')}/{item.get('metadata', {}).get('name', 'unknown')}"
            for item in active_chaos
        ]

        if matching_chaos:
            details = f"Active scenario chaos experiments remain: {', '.join(matching_chaos)}"
        else:
            details = f"Active chaos experiment resources remain in cluster: {', '.join(active_chaos_names[:5])}"

        return CheckResult(
            name="chaos_mesh_cleared",
            target="chaos-mesh",
            passed=False,
            details=details,
            observed={
                "active_chaos_count": len(active_chaos),
                "scenario_matching_count": len(matching_chaos),
                "active_experiments": active_chaos_names,
                "matching_experiments": matching_chaos,
            },
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
    result = environment_verifier.verify(
        scenario_id=scenario_id,
        agent_claimed_resolved=agent_claimed_resolved,
        alert=alert,
        incident_context=incident_context,
    )
    from agents.stage_a import bind_cpu_identity, current_run, target_identity

    run = current_run()
    if run is not None and scenario_id == "single_fault/sf-002":
        passed = False
        details = "Post-action CPU recovery not established"
        try:
            run.require_recovery_window()
            pods = environment_verifier._kubectl_get("pods", namespace="default", output="json")
            if pods.get("success") is not True:
                raise ValueError("Stage A target identity read failed")
            payload = pods.get("parsed") or json.loads(pods.get("stdout", ""))
            selected = {
                "items": [
                    pod for pod in payload.get("items", [])
                    if pod.get("metadata", {}).get("labels", {}).get("app") == "paymentservice"
                ],
            }
            if target_identity(selected) != run.target:
                raise ValueError("Stage A target pod or container identity changed")
            from agents.tools.prometheus import promql_query
            from scripts.stage_a_telemetry import cpu_observation

            query = (
                'container_cpu_usage_seconds_total{namespace="default",'
                f'pod="{run.target["pod"]}",container="server"' + "}"
            )
            sample_at = time.time()
            raw = promql_query(query, time_unix=sample_at)
            source_times = promql_query(f"timestamp({query})", time_unix=sample_at)
            observation = bind_cpu_identity(cpu_observation(
                raw, query, observed_at=time.time(), target_pod=run.target["pod"],
                source_timestamps_result=source_times,
            ), run.target)
            passed = run.recovery_check(observation)
            run.require_recovery_window()
            details = (
                "Two fresh post-remediation CPU counter samples meet the recovery threshold"
                if passed else "Post-remediation CPU counter samples do not establish recovery"
            )
        except (RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            passed = False
            details = f"Stage A CPU verification failed: {type(exc).__name__}"
        result.checks.append(CheckResult(
            name="stage_a_post_action_cpu", target=run.target["pod"],
            passed=passed, details=details, observed=run.cpu_recovery,
        ))
        result.verification_timestamp = time.time()
        if not passed:
            result.env_resolved = False
            result.verification_status = "failed"
            result.failed_checks.append("stage_a_post_action_cpu")
            result.is_false_resolution = bool(agent_claimed_resolved)
            result.is_false_negative = False
    return result
