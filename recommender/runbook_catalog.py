"""AtlasOps SRE Runbook Catalog (Recommender Systems Workstream).

Defines structured SRE Runbooks with failure signatures, target symptoms,
recommended action sequences, and relevance tags for incident remediation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Runbook:
    runbook_id: str
    title: str
    category: str
    target_symptoms: list[str]
    failure_patterns: list[str]
    actions: list[str]
    suggested_tools: list[str]
    description: str


RUNBOOK_CATALOG: dict[str, Runbook] = {
    "RB-POD-OOM": Runbook(
        runbook_id="RB-POD-OOM",
        title="Pod Out-Of-Memory (OOM) Remediation",
        category="resource_exhaustion",
        target_symptoms=["OOMKilled", "memory limit exceeded", "memory pressure", "container exit 137"],
        failure_patterns=["memory", "oom", "ram", "leak", "high memory usage"],
        actions=["Inspect memory limits", "Delete crashing pod to trigger clean restart", "Adjust container memory limits"],
        suggested_tools=["kubectl_describe", "promql_query", "k8s_delete_pod", "k8s_scale_deployment"],
        description="Remediate pods killed due to memory limit exhaustion. Verifies metrics and triggers pod restart with resource adjustment.",
    ),
    "RB-POD-CRASH": Runbook(
        runbook_id="RB-POD-CRASH",
        title="Pod CrashLoopBackOff & Fault Remediation",
        category="workload_failure",
        target_symptoms=["CrashLoopBackOff", "exit code 1", "unhandled exception", "application panic"],
        failure_patterns=["crash", "panic", "exception", "backoff", "deadlock"],
        actions=["Check container logs", "Describe pod events", "Rollback to previous deployment revision"],
        suggested_tools=["kubectl_logs", "kubectl_describe", "argocd_rollback", "k8s_delete_pod"],
        description="Remediate failing or crash-looping workload pods via log inspection and revision rollback.",
    ),
    "RB-CPU-THROTTLE": Runbook(
        runbook_id="RB-CPU-THROTTLE",
        title="CPU Quota Saturation & Throttling Mitigation",
        category="resource_exhaustion",
        target_symptoms=["CPUThrottlingHigh", "cfs quota exceeded", "slow response latency", "high cpu usage"],
        failure_patterns=["cpu", "throttle", "load", "saturation", "cpu burn"],
        actions=["Query CPU usage metrics", "Scale deployment replicas horizontally", "Increase CPU limits"],
        suggested_tools=["promql_query", "k8s_scale_deployment", "kubectl_describe"],
        description="Mitigate CPU starvation and CFS quota throttling by scaling replicas and increasing limits.",
    ),
    "RB-NET-LOSS": Runbook(
        runbook_id="RB-NET-LOSS",
        title="Network Packet Loss & Drop Remediation",
        category="network_failure",
        target_symptoms=["packet drop", "connection timeout", "network unreachable", "high packet loss"],
        failure_patterns=["network loss", "packet loss", "drop", "netloss", "disconnect"],
        actions=["Inspect network telemetry", "Restart CNI network daemon pod", "Clear stale network rules"],
        suggested_tools=["promql_query", "k8s_delete_pod", "environment_verify"],
        description="Diagnose and clear network packet loss and transport faults across service mesh interfaces.",
    ),
    "RB-NET-DELAY": Runbook(
        runbook_id="RB-NET-DELAY",
        title="Network Latency & Jitter Remediation",
        category="network_failure",
        target_symptoms=["high latency", "network delay", "rtt spike", "timeout"],
        failure_patterns=["latency", "delay", "jitter", "slow network", "lag"],
        actions=["Query round-trip time metrics", "Check cross-AZ egress rules", "Restart affected pod"],
        suggested_tools=["promql_query", "kubectl_describe", "k8s_delete_pod"],
        description="Isolate and remediate artificial network latency injections and routing delays.",
    ),
    "RB-NET-CORRUPT": Runbook(
        runbook_id="RB-NET-CORRUPT",
        title="Network Packet Corruption Remediation",
        category="network_failure",
        target_symptoms=["TCP checksum error", "corrupted packet", "handshake failure", "bad response"],
        failure_patterns=["corrupt", "checksum", "bad packet", "protocol error"],
        actions=["Verify network interface integrity", "Recreate pod network namespace", "Restart service container"],
        suggested_tools=["promql_query", "kubectl_describe", "k8s_delete_pod"],
        description="Remediate network transmission corruption and checksum validation errors.",
    ),
    "RB-DISK-FILL": Runbook(
        runbook_id="RB-DISK-FILL",
        title="Ephemeral Disk Pressure Remediation",
        category="storage_failure",
        target_symptoms=["DiskPressure", "no space left on device", "ephemeral storage exceeded", "evicted"],
        failure_patterns=["disk", "storage", "full", "space", "fill", "write failed"],
        actions=["Identify large unrotated logs", "Purge temp cache directories", "Restart worker container"],
        suggested_tools=["kubectl_describe", "promql_query", "k8s_delete_pod"],
        description="Clear disk pressure by rotating application logs and freeing ephemeral storage volumes.",
    ),
    "RB-IO-DELAY": Runbook(
        runbook_id="RB-IO-DELAY",
        title="Disk I/O Latency & Saturation Mitigation",
        category="storage_failure",
        target_symptoms=["high i/o wait", "slow disk write", "read timeout", "queue saturation"],
        failure_patterns=["io", "iowait", "disk delay", "storage latency"],
        actions=["Query storage metrics", "Throttle non-critical background jobs", "Recreate pod volume attachment"],
        suggested_tools=["promql_query", "kubectl_describe", "k8s_delete_pod"],
        description="Mitigate disk I/O bottlenecks and volume latency spikes.",
    ),
    "RB-DNS-FAIL": Runbook(
        runbook_id="RB-DNS-FAIL",
        title="CoreDNS Name Resolution Failure Remediation",
        category="service_discovery",
        target_symptoms=["NXDOMAIN", "dns lookup timeout", "cannot resolve host", "resolv.conf error"],
        failure_patterns=["dns", "resolve", "domain", "lookup", "coredns"],
        actions=["Query DNS query failure rate", "Restart CoreDNS deployment pods", "Verify kube-dns service"],
        suggested_tools=["promql_query", "kubectl_describe", "k8s_delete_pod"],
        description="Restore cluster DNS resolution by restarting CoreDNS instances and verifying resolv.conf.",
    ),
    "RB-HTTP-5XX": Runbook(
        runbook_id="RB-HTTP-5XX",
        title="Upstream HTTP 5xx Outage & Gateway Error Remediation",
        category="application_outage",
        target_symptoms=["500 Internal Server Error", "502 Bad Gateway", "503 Service Unavailable", "high error rate"],
        failure_patterns=["500", "502", "503", "http 5xx", "upstream error", "server error"],
        actions=["Query HTTP error rate by service", "Synchronize GitOps application state", "Rollback bad release"],
        suggested_tools=["promql_query", "argocd_sync", "argocd_rollback", "k8s_delete_pod"],
        description="Remediate web application 5xx outages via telemetry correlation, GitOps sync, or rollback.",
    ),
    "RB-CASCADE-HEAL": Runbook(
        runbook_id="RB-CASCADE-HEAL",
        title="Cascading Failure & Dependency Chain Recovery",
        category="cascade_failure",
        target_symptoms=["cascading outage", "dependency failure", "thread pool exhaustion", "chain reaction"],
        failure_patterns=["cascade", "dependency", "downstream", "upstream", "chain", "circuit breaker"],
        actions=["Identify root failing service in call graph", "Restart root service", "Sequentially clear downstream queues"],
        suggested_tools=["promql_query", "kubectl_describe", "k8s_delete_pod", "environment_verify"],
        description="Break cascading dependency deadlocks by isolating and remediating the root-cause service first.",
    ),
    "RB-SCALE-OUT": Runbook(
        runbook_id="RB-SCALE-OUT",
        title="Traffic Surge & Queue Backlog Autoscaling",
        category="capacity_management",
        target_symptoms=["queue backlog high", "request queue saturation", "slow consumer", "hpa maxed"],
        failure_patterns=["surge", "traffic", "backlog", "queue", "capacity", "scale"],
        actions=["Check queue depth metrics", "Scale deployment replicas", "Tune HPA target threshold"],
        suggested_tools=["promql_query", "k8s_scale_deployment", "kubectl_describe"],
        description="Scale out backend worker instances to drain sudden request surges and queue backlogs.",
    ),
}


def get_all_runbooks() -> list[Runbook]:
    """Return all available runbooks in the catalog."""
    return list(RUNBOOK_CATALOG.values())


def get_runbook(runbook_id: str) -> Runbook | None:
    """Retrieve a runbook by ID."""
    return RUNBOOK_CATALOG.get(runbook_id)
