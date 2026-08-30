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
        "chaos_list_experiments",
        "chaos_stop_experiment",
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
    assert len(REGISTERED_TOOLS) == 24
    assert len(AGENT_EXPOSED_TOOLS) == 19


def test_every_acl_tool_is_registered_and_role_names_are_deterministic():
    from agents.tool_policy import ROLE_ALLOWED_TOOLS, ROLE_TOOL_COUNTS
    from agents.tools import REGISTERED_TOOLS

    assert set(ROLE_ALLOWED_TOOLS) == {"triage", "diagnosis", "remediation", "comms"}
    assert ROLE_TOOL_COUNTS == {"triage": 4, "diagnosis": 10, "remediation": 10, "comms": 2}
    assert {role: set(tools) for role, tools in ROLE_ALLOWED_TOOLS.items()} == EXPECTED_ROLE_TOOLS
    assert all(tools <= REGISTERED_TOOLS for tools in ROLE_ALLOWED_TOOLS.values())


def test_chaos_discovery_is_not_exposed_to_investigative_roles():
    """Chaos-named wrappers must never reach triage or diagnosis.

    Every frozen scenario's success predicate is chaos clearance, so a
    chaos-named tool offered to an investigative role hands those roles the
    benchmark's answer and makes measured root-cause accuracy meaningless.
    Diagnosis must reach an injected fault through generic `kubectl_get`
    resource-type discovery instead. Remediation may hold both wrappers: by
    then diagnosis has already committed to a root cause.
    """
    from agents.tool_policy import ROLE_ALLOWED_TOOLS

    for role in ("triage", "diagnosis", "comms"):
        assert not any(name.startswith("chaos_") for name in ROLE_ALLOWED_TOOLS[role])
    assert "chaos_list_experiments" in ROLE_ALLOWED_TOOLS["remediation"]
    assert "chaos_stop_experiment" in ROLE_ALLOWED_TOOLS["remediation"]


def test_chaos_discovery_is_read_only():
    """Listing experiments must not consume the cluster-mutation quota."""
    from agents.tool_policy import CLUSTER_MUTATING_TOOLS, SIDE_EFFECTING_TOOLS

    assert "chaos_list_experiments" not in CLUSTER_MUTATING_TOOLS
    assert "chaos_list_experiments" not in SIDE_EFFECTING_TOOLS


def test_unexposed_tools_are_explicit_and_not_role_reachable():
    from agents.tool_policy import (
        ADMIN_OR_UNEXPOSED_TOOLS,
        AGENT_EXPOSED_TOOLS,
        HIGH_RISK_UNEXPOSED_TOOLS,
    )
    from agents.tools import REGISTERED_TOOLS

    assert ADMIN_OR_UNEXPOSED_TOOLS == {
        "argocd_app_get",
        "cloud_monitoring_query",
        "gcloud_logs_read",
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
        "chaos_stop_experiment",
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


def test_kubectl_get_contract_discovers_custom_resource_types():
    from agents.coordinator import _tool_schema

    schema = _tool_schema("kubectl_get")
    assert schema["function"]["description"] == (
        "Get a Kubernetes built-in or custom resource type. "
        "Use customresourcedefinitions to discover installed custom resource types."
    )
    assert schema["function"]["parameters"]["required"] == ["resource"]


def test_policy_blocks_unexposed_high_risk_tool():
    from agents.coordinator import _check_tool_policy

    for role in EXPECTED_ROLE_TOOLS:
        error = _check_tool_policy(role, "kubectl_exec", {}, {})
        assert error == f"tool `kubectl_exec` not allowed for role `{role}`"


def test_documented_tool_counts_match_the_code():
    """Six documents previously claimed four different, all-wrong tool counts.

    Judges count these. Pin the published numbers to the registry so they cannot
    drift again.
    """
    import re
    from pathlib import Path

    from agents.tool_policy import ADMIN_OR_UNEXPOSED_TOOLS, AGENT_EXPOSED_TOOLS
    from agents.tools import REGISTERED_TOOLS

    registered = len(REGISTERED_TOOLS)
    exposed = len(AGENT_EXPOSED_TOOLS)
    unexposed = len(ADMIN_OR_UNEXPOSED_TOOLS)
    root = Path(__file__).resolve().parents[1]

    # Any "<N> registered"/"registers <N>" claim must equal the real registry size.
    # Count matches too: a regex that silently matches nothing would let this
    # test pass while the documents carried the wrong numbers.
    claims_checked = 0
    for name in ("README.md", "ARCHITECTURE.md", "JUDGES_START_HERE.md", "CLAUDE.md"):
        text = (root / name).read_text(encoding="utf-8")
        for claimed in re.findall(r"(\d+)\s+registered", text):
            claims_checked += 1
            assert int(claimed) == registered, f"{name} claims {claimed} registered tools"
        for claimed in re.findall(r"registers\s+\*?\*?(\d+)", text):
            claims_checked += 1
            assert int(claimed) == registered, f"{name} claims registering {claimed} tools"
        for claimed in re.findall(r"(\d+)\s+agent-exposed", text):
            claims_checked += 1
            assert int(claimed) == exposed, f"{name} claims {claimed} agent-exposed tools"
    assert claims_checked >= 6, f"only {claims_checked} documented counts were checked"

    status = (root / "docs" / "project" / "IMPLEMENTATION_STATUS.md").read_text(encoding="utf-8")
    assert f"{registered} wrappers are registered" in status
    assert f"{exposed} are exposed" in status
    assert f"{unexposed} are intentionally unexposed" in status
