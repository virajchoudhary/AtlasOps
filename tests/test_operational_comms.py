import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from tests.test_operational_completion_contract import native_runtime as native_runtime


MODEL_CLAIM = "MODEL CLAIM: the outage is resolved and every action succeeded."


def completion(message):
    return httpx.Response(
        200,
        json={"model": "fixture/expected-model", "choices": [{"message": message}]},
        request=httpx.Request("POST", "https://fixture.invalid/v1/chat/completions"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["remediation", "comms"])
async def test_operational_agents_cannot_publish_model_authored_outcomes(
    native_runtime, monkeypatch, role,
):
    coordinator = native_runtime
    notification = MagicMock(return_value={"success": True, "mode": "logged_locally"})
    postmortem = MagicMock(return_value={"success": True, "path": "model-claim.md"})
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "slack_post_update", notification)
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "postmortem_draft", postmortem)
    first = completion({
        "content": None,
        "tool_calls": [
            {
                "id": "untrusted-publication", "type": "function",
                "function": {"name": "slack_post_update", "arguments": json.dumps({
                    "channel": "incident-response", "severity": "P1",
                    "title": "Resolved", "summary": MODEL_CLAIM,
                })},
            },
            {
                "id": "untrusted-postmortem", "type": "function",
                "function": {"name": "postmortem_draft", "arguments": json.dumps({
                    "incident": {"summary": MODEL_CLAIM, "resolution": MODEL_CLAIM},
                })},
            },
        ],
    })
    final = completion({"content": json.dumps({
        "outcome": "unresolved", "slack_posted": True, "summary": MODEL_CLAIM,
    })})
    post = AsyncMock(side_effect=[first, final, final])
    monkeypatch.setattr(coordinator, "post_with_retry", post)
    result = await coordinator.call_agent(
        role, {
            "incident_id": "inc-untrusted-comms",
            "verification": {"verification_status": "failed", "env_resolved": False},
            "env_resolved": False,
        }, max_turns=1,
    )

    notification.assert_not_called()
    postmortem.assert_not_called()
    offered = [
        tool["function"]["name"]
        for tool in post.call_args_list[0].args[2].get("tools", [])
    ]
    assert "slack_post_update" not in offered
    assert "postmortem_draft" not in offered
    outputs = [row for row in result["trajectory"] if row.get("tool")]
    assert len(outputs) == 2
    assert all(row["blocked_by_policy"] for row in outputs)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,env_resolved", [
    ("failed", False), ("inconclusive", False), ("error", False),
    ("failed", True),
])
async def test_operational_publication_uses_verifier_facts_not_generated_summary(
    native_runtime, monkeypatch, status, env_resolved,
):
    from agents.approval import ApprovalGate
    from tests.test_approval_target_binding import ACTION

    coordinator = native_runtime
    order = []
    posted = []
    drafted = []
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://fixture.invalid/no-real-webhook")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
    monkeypatch.setattr(coordinator, "settle_environment", AsyncMock(return_value={"settled": False}))
    gate = ApprovalGate()
    request_action = gate.request_action

    def approve_request(*args, **kwargs):
        request = request_action(*args, **kwargs)
        assert gate.callback(
            request.token, "approved", approved_by="test-operator",
            action_digest=request.action_digest,
        )["ok"]
        return request

    monkeypatch.setattr(gate, "request_action", approve_request)
    monkeypatch.setattr(coordinator, "approval_gate", gate)

    verification = {
        "verification_status": status,
        "env_resolved": env_resolved,
        "failed_checks": ["available_replicas"],
    }

    def verify(**_kwargs):
        order.append("verification")
        return SimpleNamespace(
            env_resolved=env_resolved, verification_status=status,
            to_dict=lambda: verification,
        )

    monkeypatch.setattr("agents.verifier.verify_environment", verify)

    def notify(**arguments):
        if arguments["title"].startswith("Exact action approval required"):
            return {"success": True, "mode": "logged_locally"}
        assert "verification" in order
        order.append("notification")
        posted.append(arguments)
        return {"success": True, "mode": "logged_locally", "errors": ["slack: HTTPError"]}

    def draft(**arguments):
        assert "verification" in order
        order.append("postmortem")
        drafted.append(arguments)
        return {"success": True, "postmortem_path": "fixture-postmortem.md"}

    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "slack_post_update", notify)
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "postmortem_draft", draft)

    async def role_agent(role, _input):
        if role == "remediation":
            decision, permit = await coordinator._ACTIVE_ACTION_APPROVER.get()(deepcopy(ACTION))
            assert decision["status"] == "approved" and permit is not None
        final = {
            "triage": {"severity": "P2", "title": MODEL_CLAIM},
            "diagnosis": {"root_cause": "Unknown"},
            "remediation": {
                "status": "unresolved", "outcome": "unresolved", "executed_actions": [],
            },
            "comms": {
                "slack_posted": True, "postmortem_path": "invented.md",
                "summary": MODEL_CLAIM, "summary_for_dashboard": MODEL_CLAIM,
            },
        }[role]
        return {"role": role, "trajectory": [], "final": final}

    monkeypatch.setattr(coordinator, "call_agent", AsyncMock(side_effect=role_agent))
    result = await coordinator.handle_incident(
        {"commonLabels": {"alertname": "FixtureAvailability"}},
        incident_id=f"inc-canonical-{status}-{env_resolved}",
    )

    assert len(posted) == len(drafted) == 1
    assert MODEL_CLAIM not in json.dumps(posted)
    assert MODEL_CLAIM not in json.dumps(drafted)
    assert MODEL_CLAIM not in json.dumps(result["comms"]["final"])
    assert result["comms"]["final"]["slack_posted"] is False
    assert result["comms"]["final"]["postmortem_path"] == "fixture-postmortem.md"
    assert result["comms"]["inference"]["final"]["summary"] == MODEL_CLAIM
    assert result["comms"]["final"]["status"] == f"verification_{status}"
    assert drafted[0]["incident"]["verification"] == verification
    assert drafted[0]["incident"]["duration"] == "Not measured"
    assert drafted[0]["incident"]["impact"] == "Not quantified"
    assert result["resolved"] is False
    assert result["verification"] == verification
    assert result["comms"]["delivery"]["slack"]["delivered"] is False
    assert result["comms"]["delivery"]["local_feed_written"] is True


@pytest.mark.parametrize("decision,verification_status,expected_status", [
    ("approved", "passed", "verified_resolved"),
    ("rejected", "passed", "verification_unconfirmed"),
    ("approved", "inconclusive", "verification_inconclusive"),
])
def test_canonical_publisher_writes_real_unmeasured_artifacts(
    native_runtime, monkeypatch, tmp_path, decision, verification_status, expected_status,
):
    from pathlib import Path

    from agents.tools import comms

    monkeypatch.setattr(comms, "SLACK_WEBHOOK", "")
    monkeypatch.setattr(comms, "DISCORD_WEBHOOK", "")
    monkeypatch.setattr(comms, "_LOG_PATH", tmp_path / "feed.jsonl")
    monkeypatch.setattr(comms, "POSTMORTEM_DIR", tmp_path / "postmortems")
    monkeypatch.setitem(native_runtime.TOOL_REGISTRY, "slack_post_update", comms.slack_post_update)
    monkeypatch.setitem(native_runtime.TOOL_REGISTRY, "postmortem_draft", comms.postmortem_draft)
    verification = {
        "env_resolved": True,
        "verification_status": verification_status,
        "failed_checks": [],
    }
    result = comms.publish_operational_outcome(comms.OperationalCommsFacts(
        incident_id="inc-real-publication", severity="P1",
        verification=verification, resolved=True, approval_decision=decision,
    ))

    assert result["final"]["status"] == expected_status
    assert result["final"]["slack_posted"] is False
    assert result["delivery"]["local_feed_written"] is True
    assert result["delivery"]["postmortem_written"] is True
    document = Path(result["final"]["postmortem_path"]).read_text()
    assert "Not measured" in document
    assert "Not quantified" in document
    assert "< 5 min" not in document
    assert "Remediation applied" not in document
    assert "2026-06-01" not in document
    feed = json.loads(comms._LOG_PATH.read_text())
    assert feed["attachments"][0]["text"] == result["final"]["summary"]
    assert [row["tool"] for row in result["trajectory"]] == [
        "slack_post_update", "postmortem_draft",
    ]
