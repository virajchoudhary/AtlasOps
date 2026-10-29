import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.parametrize("namespace", [None, "-A", "unrelated-namespace"])
def test_operational_agent_reads_use_only_the_configured_rbac_scope(
    monkeypatch, namespace,
):
    import agents.coordinator as coordinator

    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    monkeypatch.setenv("ATLASOPS_WORKLOAD_NAMESPACE", "atlasops-boutique")
    monkeypatch.setattr(coordinator, "require_audit_log", MagicMock())
    monkeypatch.setattr(coordinator, "audit_log", MagicMock())
    monkeypatch.setattr(coordinator, "thought_emit", MagicMock())
    monkeypatch.setattr(coordinator, "circuit_breaker", MagicMock())
    tool = MagicMock(return_value={"success": True, "parsed": {"items": []}})
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "kubectl_get", tool)
    arguments = {"resource": "pods"}
    if namespace is not None:
        arguments["namespace"] = namespace
    responses = []
    for message in (
        {
            "content": "",
            "tool_calls": [{
                "id": "scoped-read", "type": "function",
                "function": {"name": "kubectl_get", "arguments": json.dumps(arguments)},
            }],
        },
        {"content": '{"severity":"P1","title":"Scoped read fixture"}'},
    ):
        response = MagicMock()
        response.json.return_value = {
            "model": coordinator.MODEL_NAME, "choices": [{"message": message}],
        }
        responses.append(response)
    monkeypatch.setattr(coordinator, "post_with_retry", AsyncMock(side_effect=responses))
    result = asyncio.run(coordinator.call_agent(
        "triage", {"incident_id": "inc-read-scope"}, max_turns=2,
    ))
    observation = next(
        row for row in result["trajectory"] if row.get("tool") == "kubectl_get"
    )
    if namespace is None:
        tool.assert_called_once_with(resource="pods", namespace="atlasops-boutique")
        assert observation["args"]["namespace"] == "atlasops-boutique"
        assert observation["output"]["success"] is True
    else:
        tool.assert_not_called()
        assert observation["blocked_by_policy"] is True
        assert observation["output"]["success"] is False


def test_cluster_health_observes_the_selected_workload_namespace_and_context(monkeypatch):
    import app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ATLASOPS_WORKLOAD_NAMESPACE", "atlasops-boutique")
    monkeypatch.setenv("KUBECONFIG_CONTEXT", "fixture-kind")
    run = MagicMock(return_value=MagicMock(
        returncode=0, stdout='{"items":[]}', stderr="",
    ))
    monkeypatch.setattr(app.subprocess, "run", run)
    response = TestClient(app.app).get("/cluster/health")
    assert response.status_code == 200 and response.json()["ok"] is True
    command = run.call_args.args[0]
    assert command[command.index("-n") + 1] == "atlasops-boutique"
    assert command[command.index("--context") + 1] == "fixture-kind"
