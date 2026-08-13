"""Canonical AtlasOps tool exposure and side-effect policy.

Registration means a wrapper exists in :mod:`agents.tools`; it does not grant
an autonomous agent permission to call that wrapper. Role ACLs are the only
agent-exposure boundary. Side-effect categories are separate so the cluster
mutation circuit-breaker quota does not accidentally count communications or
local postmortem writes as infrastructure remediation actions.
"""

from __future__ import annotations


ROLE_ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "triage": frozenset({
        "kubectl_get",
        "kubectl_top_pods",
        "alertmanager_list_alerts",
        "promql_query",
    }),
    "diagnosis": frozenset({
        "promql_query",
        "promql_query_range",
        "jaeger_search",
        "jaeger_get_trace",
        "kubectl_logs",
        "kubectl_describe",
        "kubectl_get",
        "kubectl_top_pods",
        "argocd_list_apps",
        "argocd_app_history",
        "gcloud_logs_read",
        "cloud_monitoring_query",
    }),
    "remediation": frozenset({
        "argocd_rollback",
        "kubectl_rollout",
        "kubectl_scale",
        "alertmanager_silence",
        "promql_query",
        "kubectl_get",
        "kubectl_describe",
        "slack_post_update",
    }),
    "comms": frozenset({"slack_post_update", "postmortem_draft"}),
}

AGENT_EXPOSED_TOOLS = frozenset().union(*ROLE_ALLOWED_TOOLS.values())

# Registered for explicit administrative/library use, but never offered to an
# autonomous role. kubectl_exec remains high-risk even with its command allowlist
# because an allowed executable such as curl or wget can still have side effects.
ADMIN_OR_UNEXPOSED_TOOLS = frozenset({
    "argocd_app_get",
    "kubectl_exec",
    "kubectl_top_nodes",
})
HIGH_RISK_UNEXPOSED_TOOLS = frozenset({"kubectl_exec"})

# These four operations consume the circuit breaker's cluster-remediation quota.
CLUSTER_MUTATING_TOOLS = frozenset({
    "alertmanager_silence",
    "argocd_rollback",
    "kubectl_rollout",
    "kubectl_scale",
})

# These effects are real but do not consume the cluster-remediation quota.
EXTERNAL_COMMUNICATION_TOOLS = frozenset({"slack_post_update"})
FILESYSTEM_WRITING_TOOLS = frozenset({"postmortem_draft", "slack_post_update"})

SIDE_EFFECTING_TOOLS = frozenset().union(
    CLUSTER_MUTATING_TOOLS,
    EXTERNAL_COMMUNICATION_TOOLS,
    FILESYSTEM_WRITING_TOOLS,
    HIGH_RISK_UNEXPOSED_TOOLS,
)

ROLE_TOOL_COUNTS = {
    role: len(tool_names) for role, tool_names in ROLE_ALLOWED_TOOLS.items()
}
