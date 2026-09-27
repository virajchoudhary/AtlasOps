"""Tests for approval gate behavior."""

import asyncio

import pytest


def test_approval_policy_mapping():
    from agents.approval import approval_mode_for_severity

    assert approval_mode_for_severity("P0") == "manual"
    assert approval_mode_for_severity("P1") == "approve"
    assert approval_mode_for_severity("P2") == "auto"
    assert approval_mode_for_severity("P3") == "auto"
    assert approval_mode_for_severity("UNKNOWN") == "approve"


def test_approval_gate_approve_roundtrip():
    from agents.approval import ApprovalGate

    gate = ApprovalGate(timeout_seconds=2)
    req = gate.request("inc-1", "P1", "rollback checkoutservice")
    cb = gate.callback(req.token, "approved", approved_by="hari")
    assert cb["ok"] is True
    result = asyncio.run(gate.wait_for_decision("inc-1"))
    assert result["status"] == "approved"
    assert result["approved_by"] == "hari"


def test_approval_gate_timeout():
    from agents.approval import ApprovalGate

    gate = ApprovalGate(timeout_seconds=1)
    gate.request("inc-timeout", "P1", "scale service")
    result = asyncio.run(gate.wait_for_decision("inc-timeout"))
    assert result["status"] == "timeout"


def test_approval_callback_rejects_unknown_token():
    from agents.approval import ApprovalGate

    gate = ApprovalGate(timeout_seconds=1)
    result = gate.callback("missing-token", "approved")
    assert result["ok"] is False


@pytest.mark.parametrize("decision", ["approved", "rejected"])
@pytest.mark.parametrize("approved_by", ["", " ", "\t\n"])
def test_approval_callback_requires_nonblank_operator_identity(decision, approved_by):
    from agents.approval import ApprovalGate

    gate = ApprovalGate(timeout_seconds=1)
    req = gate.request("inc-no-operator", "P1", "review")

    result = gate.callback(req.token, decision, approved_by=approved_by)

    assert result == {"ok": False, "error": "approved_by is required"}
    assert req.decision == "pending"
    assert req.event.is_set() is False


@pytest.mark.parametrize("approved_by", ["", " \t"])
def test_action_approval_requires_a_named_operator(approved_by):
    from agents.approval import ApprovalGate

    gate = ApprovalGate(timeout_seconds=2)
    request = gate.request_action(
        incident_id="inc-named-operator",
        severity="P1",
        action={"tool": "kubectl_scale", "arguments": {"deployment": "paymentservice"}},
    )

    result = gate.callback(request.token, "approved", approved_by=approved_by)

    assert result == {"ok": False, "error": "approved_by is required"}
    assert gate.pending()[0]["decision"] == "pending"


@pytest.mark.parametrize(
    ("operator_decision", "expected_status"),
    [("approved", "approved"), ("rejected", "rejected")],
)
def test_exact_action_approval_is_shown_and_requires_authenticated_callback(
    monkeypatch, operator_decision, expected_status
):
    from fastapi.testclient import TestClient

    import agents.coordinator as coordinator
    from agents.approval import ApprovalGate

    gate = ApprovalGate(timeout_seconds=2)
    monkeypatch.setattr(coordinator, "approval_gate", gate)
    monkeypatch.setattr(coordinator, "_RUNTIME_API_KEY", "test-operator-key")
    action = {
        "tool": "kubectl_scale",
        "arguments": {
            "deployment": "paymentservice",
            "replicas": 2,
            "namespace": "default",
        },
    }
    request = gate.request_action(
        incident_id="inc-action-approval",
        severity="P1",
        action=action,
    )
    client = TestClient(coordinator.app)

    unauthorized_pending = client.get("/approval/pending")
    assert unauthorized_pending.status_code == 401
    pending_response = client.get(
        "/approval/pending",
        headers={"X-AtlasOps-Key": "test-operator-key"},
    )
    assert pending_response.status_code == 200
    pending = next(
        entry
        for entry in pending_response.json()["pending"]
        if entry["incident_id"] == "inc-action-approval"
    )
    assert pending["action"] == action
    assert pending["action_digest"] == gate.action_digest(action)
    assert request.action_summary in pending["summary"]

    callback = {
        "token": request.token,
        "decision": operator_decision,
        "approved_by": "test-operator",
    }
    assert client.post("/approve", json=callback).status_code == 401
    assert client.post(
        "/approve",
        json={"token": request.token, "decision": operator_decision},
        headers={"X-AtlasOps-Key": "test-operator-key"},
    ).status_code == 422
    assert client.post(
        "/approve",
        json={**callback, "approved_by": " \t"},
        headers={"X-AtlasOps-Key": "test-operator-key"},
    ).status_code == 400
    assert gate.pending()[0]["decision"] == "pending"
    authorized_response = client.post(
        "/approve",
        json=callback,
        headers={"X-AtlasOps-Key": "test-operator-key"},
    )
    assert authorized_response.status_code == 200

    result, permit = asyncio.run(
        gate.wait_for_action_decision(
            "inc-action-approval",
            request_token=request.token,
        )
    )
    assert result["status"] == expected_status
    assert (permit is not None) is (operator_decision == "approved")
    if permit is not None:
        assert gate.consume_action_permit(
            permit,
            incident_id="inc-action-approval",
            action_digest=gate.action_digest(action),
        )
        assert not gate.consume_action_permit(
            permit,
            incident_id="inc-action-approval",
            action_digest=gate.action_digest(action),
        )


def test_exact_action_approval_timeout_does_not_issue_a_permit():
    from agents.approval import ApprovalGate

    gate = ApprovalGate(timeout_seconds=0.001)
    request = gate.request_action(
        incident_id="inc-action-timeout",
        severity="P1",
        action={
            "tool": "kubectl_scale",
            "arguments": {"deployment": "paymentservice", "replicas": 2},
        },
    )

    result, permit = asyncio.run(
        gate.wait_for_action_decision(
            "inc-action-timeout",
            request_token=request.token,
        )
    )

    assert result["status"] == "timeout"
    assert permit is None
    assert gate.callback(request.token, "approved") == {
        "ok": False,
        "error": "unknown_token",
    }


def test_action_approval_permit_mismatch_burns_permit_and_expiry_fails_closed(
    monkeypatch,
):
    import agents.approval as approval_module
    from agents.approval import ApprovalGate

    clock = [100.0]
    monkeypatch.setattr(approval_module.time, "time", lambda: clock[0])
    gate = ApprovalGate(timeout_seconds=2)
    action = {
        "tool": "kubectl_scale",
        "arguments": {"deployment": "paymentservice", "replicas": 2},
    }
    request = gate.request_action(
        incident_id="inc-action-mismatch",
        severity="P1",
        action=action,
    )
    gate.callback(request.token, "approved", approved_by="test-operator")
    _result, permit = asyncio.run(
        gate.wait_for_action_decision(
            "inc-action-mismatch",
            request_token=request.token,
        )
    )
    assert permit is not None
    changed_action_digest = gate.action_digest(
        {
            "tool": "kubectl_scale",
            "arguments": {"deployment": "paymentservice", "replicas": 0},
        }
    )
    assert not gate.consume_action_permit(
        permit,
        incident_id="inc-action-mismatch",
        action_digest=changed_action_digest,
    )
    assert not gate.consume_action_permit(
        permit,
        incident_id="inc-action-mismatch",
        action_digest=gate.action_digest(action),
    )

    second_request = gate.request_action(
        incident_id="inc-action-stale",
        severity="P1",
        action=action,
    )
    gate.callback(second_request.token, "approved", approved_by="test-operator")
    _result, stale_permit = asyncio.run(
        gate.wait_for_action_decision(
            "inc-action-stale",
            request_token=second_request.token,
        )
    )
    assert stale_permit is not None
    clock[0] = 103.0
    assert not gate.consume_action_permit(
        stale_permit,
        incident_id="inc-action-stale",
        action_digest=gate.action_digest(action),
    )
