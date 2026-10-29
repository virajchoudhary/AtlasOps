import json
from copy import deepcopy
from unittest.mock import MagicMock

import pytest
import requests


WEBHOOKS = {
    "slack": "https://hooks.slack.com/services/T00000000/B00000000/fixture-slack-token",
    "discord": "https://discord.com/api/webhooks/123456789/fixture-discord-token",
}


@pytest.mark.parametrize("provider", ["slack", "discord"])
def test_failed_webhooks_never_return_or_log_credential_urls(
    monkeypatch, tmp_path, caplog, provider,
):
    from agents.tools import comms

    monkeypatch.setattr(comms, "_LOG_PATH", tmp_path / "slack.jsonl")
    monkeypatch.setattr(comms, "SLACK_WEBHOOK", WEBHOOKS["slack"] if provider == "slack" else "")
    monkeypatch.setattr(comms, "DISCORD_WEBHOOK", WEBHOOKS["discord"] if provider == "discord" else "")
    monkeypatch.setattr(comms.time, "sleep", lambda _seconds: None)
    post = MagicMock(side_effect=requests.HTTPError(
        f"502 upstream unavailable for url: {WEBHOOKS[provider]}",
    ))
    monkeypatch.setattr(comms.requests, "post", post)

    result = comms.slack_post_update(
        "incident-response", "P1", "Unresolved incident",
        "The environment remains unresolved.",
    )

    assert post.called
    assert result["success"] is True
    assert result["mode"] == "logged_locally"
    assert result["errors"]
    assert "fixture-" not in json.dumps(result)
    assert "fixture-" not in caplog.text
    assert "webhook" not in json.dumps(result).lower()
    assert "environment remains unresolved" in comms._LOG_PATH.read_text()


@pytest.mark.parametrize("url", WEBHOOKS.values())
def test_webhook_credentials_are_removed_from_model_and_persisted_evidence(
    monkeypatch, tmp_path, url,
):
    import agents.coordinator as coordinator
    from agents.evidence_redaction import redact_persisted_evidence

    observation = {
        "success": False,
        "error": f"Delivery failed for {url}",
        "env_resolved": False,
        "verification_status": "failed",
        "failed_checks": ["available_replicas"],
    }
    original = deepcopy(observation)
    for safe in (
        redact_persisted_evidence(observation),
        coordinator._model_safe_tool_output(observation),
    ):
        assert "fixture-" not in json.dumps(safe)
        assert "[REDACTED]" in safe["error"]
        assert safe["env_resolved"] is False
        assert safe["failed_checks"] == ["available_replicas"]
    assert observation == original

    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path)
    coordinator._persist_incident_record({
        "incident_id": "inc-webhook-redaction",
        "execution_id": "b" * 32,
        "resolved": False,
        "env_resolved": False,
        "verification": observation,
    })
    saved = json.loads((tmp_path / "inc-webhook-redaction.json").read_text())
    assert "fixture-" not in json.dumps(saved)
    assert saved["resolved"] is False
    assert saved["verification"]["failed_checks"] == ["available_replicas"]


def test_slack_feed_and_delivery_payload_remove_embedded_webhook_credentials(
    monkeypatch, tmp_path,
):
    from agents.tools import comms

    monkeypatch.setattr(comms, "_LOG_PATH", tmp_path / "slack.jsonl")
    monkeypatch.setattr(comms, "SLACK_WEBHOOK", "https://fixture.invalid/no-real-webhook")
    monkeypatch.setattr(comms, "DISCORD_WEBHOOK", "")
    post = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(comms.requests, "post", post)
    result = comms.slack_post_update(
        "incident-response", "P1", "Unresolved incident",
        f"External delivery failed for {WEBHOOKS['discord']}.",
    )

    assert result["success"] is True
    payload = post.call_args.kwargs["json"]
    assert "fixture-discord-token" not in json.dumps(payload)
    assert json.loads(comms._LOG_PATH.read_text()) == payload
    assert "[REDACTED]" in payload["attachments"][0]["text"]
