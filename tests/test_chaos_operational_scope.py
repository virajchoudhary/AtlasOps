from __future__ import annotations

import json

from agents.tools import chaos


def install_inventory(monkeypatch, items):
    commands = []

    def observe(command, **_kwargs):
        commands.append(command)
        return {"success": True, "stdout": json.dumps({"items": items})}

    monkeypatch.setattr(chaos, "_run", observe)
    return commands


def test_operational_chaos_inventory_uses_the_cleanup_namespace(monkeypatch):
    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    commands = install_inventory(monkeypatch, [])

    result = chaos.chaos_list_experiments()

    assert commands == [[
        "kubectl", "get",
        "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
        "--namespace", "chaos-mesh", "-o", "json",
    ]]
    assert result["success"] is True
    assert result["inventory_complete"] is True
    assert result["namespace_scope"] == "chaos-mesh"


def test_operational_chaos_inventory_rejects_an_out_of_scope_resource(monkeypatch):
    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    install_inventory(monkeypatch, [{
        "kind": "PodChaos",
        "metadata": {"name": "unexpected", "namespace": "another-namespace"},
        "status": {},
    }])

    result = chaos.chaos_list_experiments()

    assert result["success"] is False
    assert result["inventory_complete"] is False
    assert result["observation_status"] == "invalid_response"


def test_non_operational_chaos_inventory_retains_the_existing_all_namespace_query(monkeypatch):
    monkeypatch.delenv("ATLASOPS_OPERATIONAL_MODE", raising=False)
    commands = install_inventory(monkeypatch, [])

    result = chaos.chaos_list_experiments()

    assert commands[0] == [
        "kubectl", "get",
        "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
        "-A", "-o", "json",
    ]
    assert result["success"] is True
    assert "namespace_scope" not in result
