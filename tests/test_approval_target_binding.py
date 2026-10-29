"""Exact action receipts bind the prepared resource and mutation."""

from copy import deepcopy

import pytest

from agents.approval import ApprovalGate


ACTION = {
    "tool": "kubectl_scale",
    "arguments": {"deployment": "frontend", "namespace": "default", "replicas": 2},
    "target": {
        "server": "https://kubernetes.default.svc",
        "context": "in-cluster",
        "api_version": "apps/v1",
        "kind": "Deployment",
        "namespace": "default",
        "name": "frontend",
        "uid": "frontend-uid",
        "resource_version": "42",
    },
    "patch": [
        {"op": "test", "path": "/metadata/uid", "value": "frontend-uid"},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "42"},
        {"op": "replace", "path": "/spec/replicas", "value": 2},
    ],
}


@pytest.mark.parametrize(
    "field,value",
    [
        ("server", "https://another-cluster.invalid"),
        ("context", "another-context"),
        ("namespace", "another-namespace"),
        ("name", "cartservice"),
        ("uid", "replacement-uid"),
        ("resource_version", "43"),
    ],
)
def test_resource_identity_changes_the_approved_digest(field, value):
    changed = deepcopy(ACTION)
    changed["target"][field] = value
    assert ApprovalGate.action_digest(ACTION) != ApprovalGate.action_digest(changed)


def test_prepared_patch_changes_the_approved_digest():
    changed = deepcopy(ACTION)
    changed["patch"][-1]["value"] = 3
    assert ApprovalGate.action_digest(ACTION) != ApprovalGate.action_digest(changed)


def test_pending_receipt_preserves_the_complete_prepared_action():
    gate = ApprovalGate()
    action = deepcopy(ACTION)
    request = gate.request_action(
        incident_id="inc-target", severity="P1", action=action
    )
    assert request.to_dict()["action"] == ACTION
    action["target"]["uid"] = "changed-after-request"
    assert request.to_dict()["action"] == ACTION


def test_expired_exact_request_cannot_be_approved_before_a_waiter(monkeypatch):
    import agents.approval as module

    clock = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    gate = ApprovalGate(timeout_seconds=5)
    request = gate.request_action(
        incident_id="inc-expired", severity="P1", action=ACTION
    )
    clock[0] += 6
    result = gate.callback(request.token, "approved", approved_by="operator")
    assert result["ok"] is False
    assert request.decision == "timeout"


@pytest.mark.asyncio
async def test_timely_rejection_survives_a_waiter_starting_after_expiry(monkeypatch):
    import agents.approval as module

    clock = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    gate = ApprovalGate(timeout_seconds=5)
    request = gate.request_action(
        incident_id="inc-rejected", severity="P1", action=ACTION
    )
    clock[0] = 1004.0
    accepted = gate.callback(
        request.token, "rejected", approved_by="operator",
        action_digest=request.action_digest, reason="Do not apply",
    )
    assert accepted["ok"] is True
    clock[0] = 1006.0
    decision, permit = await gate.wait_for_action_decision(
        request.incident_id, request_token=request.token
    )
    assert decision["status"] == "rejected"
    assert decision["reason"] == "Do not apply"
    assert permit is None
    assert gate.pending() == []


@pytest.mark.asyncio
async def test_timely_approval_cannot_execute_after_request_expiry(monkeypatch):
    import agents.approval as module

    clock = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    gate = ApprovalGate(timeout_seconds=5)
    request = gate.request_action(
        incident_id="inc-approved", severity="P1", action=ACTION
    )
    clock[0] = 1004.0
    accepted = gate.callback(
        request.token, "approved", approved_by="operator",
        action_digest=request.action_digest,
    )
    assert accepted["ok"] is True
    clock[0] = 1006.0
    _, permit = await gate.wait_for_action_decision(
        request.incident_id, request_token=request.token
    )
    assert gate.consume_action_permit(
        permit, incident_id=request.incident_id, action_digest=request.action_digest
    ) is False
