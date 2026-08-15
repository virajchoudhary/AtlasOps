"""Static invariants for tool registration, role ACLs, and side effects."""

from __future__ import annotations


EXPECTED_ROLE_TOOLS = {
    "triage": {
        "alertmanager_list_alerts",
        "kubectl_get",
        "kubectl_top_pods",
        "promql_query",
    },
    "diagnosis": {
        "argocd_app_history",
        "argocd_list_apps",
        "cloud_monitoring_query",
        "gcloud_logs_read",
        "jaeger_get_trace",
        "jaeger_search",
        "kubectl_describe",
        "kubectl_get",
        "kubectl_logs",
        "kubectl_top_pods",
        "promql_query",
        "promql_query_range",
    },
    "remediation": {
        "alertmanager_silence",
        "argocd_rollback",
        "kubectl_describe",
        "kubectl_get",
        "kubectl_rollout",
        "kubectl_scale",
        "promql_query",
        "slack_post_update",
    },
    "comms": {"postmortem_draft", "slack_post_update"},
}


def test_registry_and_agent_exposure_counts_are_exact():
    from agents.tool_policy import AGENT_EXPOSED_TOOLS
    from agents.tools import REGISTERED_TOOLS, TOOL_REGISTRY

    assert REGISTERED_TOOLS == frozenset(TOOL_REGISTRY)
    assert len(REGISTERED_TOOLS) == 22
    assert len(AGENT_EXPOSED_TOOLS) == 19


def test_every_acl_tool_is_registered_and_role_names_are_deterministic():
    from agents.tool_policy import ROLE_ALLOWED_TOOLS, ROLE_TOOL_COUNTS
    from agents.tools import REGISTERED_TOOLS

    assert set(ROLE_ALLOWED_TOOLS) == {"triage", "diagnosis", "remediation", "comms"}
    assert ROLE_TOOL_COUNTS == {"triage": 4, "diagnosis": 12, "remediation": 8, "comms": 2}
    assert {role: set(tools) for role, tools in ROLE_ALLOWED_TOOLS.items()} == EXPECTED_ROLE_TOOLS
    assert all(tools <= REGISTERED_TOOLS for tools in ROLE_ALLOWED_TOOLS.values())


def test_unexposed_tools_are_explicit_and_not_role_reachable():
    from agents.tool_policy import (
        ADMIN_OR_UNEXPOSED_TOOLS,
        AGENT_EXPOSED_TOOLS,
        HIGH_RISK_UNEXPOSED_TOOLS,
    )
    from agents.tools import REGISTERED_TOOLS

    assert ADMIN_OR_UNEXPOSED_TOOLS == {
        "argocd_app_get",
        "kubectl_exec",
        "kubectl_top_nodes",
    }
    assert REGISTERED_TOOLS - AGENT_EXPOSED_TOOLS == ADMIN_OR_UNEXPOSED_TOOLS
    assert HIGH_RISK_UNEXPOSED_TOOLS == {"kubectl_exec"}
    assert HIGH_RISK_UNEXPOSED_TOOLS.isdisjoint(AGENT_EXPOSED_TOOLS)


def test_side_effect_categories_are_explicit_and_complete():
    from agents.tool_policy import (
        CLUSTER_MUTATING_TOOLS,
        EXTERNAL_COMMUNICATION_TOOLS,
        FILESYSTEM_WRITING_TOOLS,
        HIGH_RISK_UNEXPOSED_TOOLS,
        SIDE_EFFECTING_TOOLS,
    )
    from agents.tools import REGISTERED_TOOLS

    assert CLUSTER_MUTATING_TOOLS == {
        "alertmanager_silence",
        "argocd_rollback",
        "kubectl_rollout",
        "kubectl_scale",
    }
    assert EXTERNAL_COMMUNICATION_TOOLS == {"slack_post_update"}
    assert FILESYSTEM_WRITING_TOOLS == {"postmortem_draft", "slack_post_update"}
    assert SIDE_EFFECTING_TOOLS == (
        CLUSTER_MUTATING_TOOLS
        | EXTERNAL_COMMUNICATION_TOOLS
        | FILESYSTEM_WRITING_TOOLS
        | HIGH_RISK_UNEXPOSED_TOOLS
    )
    assert SIDE_EFFECTING_TOOLS <= REGISTERED_TOOLS


def test_only_remediation_role_can_reach_cluster_mutating_tools():
    from agents.tool_policy import CLUSTER_MUTATING_TOOLS, ROLE_ALLOWED_TOOLS

    assert CLUSTER_MUTATING_TOOLS <= ROLE_ALLOWED_TOOLS["remediation"]
    for role in ("triage", "diagnosis", "comms"):
        assert CLUSTER_MUTATING_TOOLS.isdisjoint(ROLE_ALLOWED_TOOLS[role])


def test_agent_schema_map_exactly_matches_exposed_tools():
    from agents.coordinator import _TOOL_PARAMETER_SCHEMAS, _tool_schemas_for_role
    from agents.tool_policy import AGENT_EXPOSED_TOOLS, ROLE_ALLOWED_TOOLS

    assert frozenset(_TOOL_PARAMETER_SCHEMAS) == AGENT_EXPOSED_TOOLS
    for role, allowed in ROLE_ALLOWED_TOOLS.items():
        schemas = _tool_schemas_for_role(role)
        schema_names = [schema["function"]["name"] for schema in schemas]
        assert schema_names == sorted(allowed)
        assert all(name in AGENT_EXPOSED_TOOLS for name in schema_names)


def test_policy_blocks_unexposed_high_risk_tool():
    from agents.coordinator import _check_tool_policy

    for role in EXPECTED_ROLE_TOOLS:
        error = _check_tool_policy(role, "kubectl_exec", {}, {})
        assert error == f"tool `kubectl_exec` not allowed for role `{role}`"
