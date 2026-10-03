"""NON_EMPIRICAL check of the real provider/coordinator composition."""

import json
from unittest.mock import AsyncMock

import pytest

import agents.coordinator as coordinator
from tests.test_integrated_completion_provider import (
    _inconclusive_verification,
    _no_settle,
    safe_runtime as safe_runtime,
)
from tests.test_integrated_inference import rig as rig


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["base", "sft"])
async def test_paired_provider_accepts_all_four_coordinator_roles(
    arm, rig, safe_runtime, monkeypatch
):
    monkeypatch.setattr(coordinator, "settle_environment", _no_settle)
    monkeypatch.setattr(
        "agents.verifier.verify_environment",
        lambda **_kwargs: _inconclusive_verification(),
    )
    monkeypatch.setattr(coordinator, "post_with_retry", AsyncMock())
    calls = []
    active_role = None
    responses = {
        "triage": {"severity": "P3", "affected_services": ["frontend"], "title": "Fixture"},
        "diagnosis": {"root_cause": "unknown", "confidence": 0.1},
        "remediation": {"outcome": "unresolved", "proposed_actions": []},
        "comms": {"slack_posted": False, "summary": "Unresolved fixture"},
    }

    def decode(_token_ids, **_kwargs):
        return json.dumps(responses[active_role])

    monkeypatch.setattr(rig.tokenizer, "decode", decode)
    provider = rig.engine.provider(arm)

    async def complete(role, request):
        nonlocal active_role
        active_role = role
        calls.append(role)
        return await provider(role, request)

    result = await coordinator.handle_incident(
        {
            "commonLabels": {"alertname": "Fixture", "service": "frontend"},
            "alerts": [{"labels": {"service": "frontend"}}],
        },
        incident_id=f"inc-provider-bridge-{arm}",
        scenario_id="single_fault/provider-fixture",
        completion_provider=complete,
    )
    await rig.engine.close()
    assert set(calls) == {"triage", "diagnosis", "remediation", "comms"}
    assert result["evidence_class"] == "NON_EMPIRICAL"
    assert result["resolved"] is False
    coordinator.post_with_retry.assert_not_awaited()
    assert rig.model.calls and all(
        enabled is (arm == "sft") for enabled in rig.model.calls
    )
