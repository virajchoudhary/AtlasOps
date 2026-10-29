"""Isolated synthetic coordinator process for the Stage 4 approval HTTP tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from agents.circuit_breaker import CircuitBreaker
from scripts.run_stage4_golden_incident import stage4_approval_server


def publish_json(path: Path, payload: dict) -> None:
    """Expose complete subprocess handoff records, never a partial JSON write."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream)
    os.replace(temporary, path)


async def main(output_dir: Path) -> None:
    from agents import coordinator, verifier

    coordinator.TRAJECTORIES_DIR = output_dir / "trajectories"
    coordinator.require_audit_log = MagicMock()
    coordinator.audit_log = MagicMock()
    coordinator.thought_emit = MagicMock()
    coordinator.circuit_breaker = CircuitBreaker()
    mutation = MagicMock(return_value={"success": True})
    coordinator.TOOL_REGISTRY = {
        "slack_post_update": MagicMock(return_value={"success": True}),
        "kubectl_scale": mutation,
    }
    coordinator.settle_environment = AsyncMock(return_value={})
    verifier.verify_environment = MagicMock(return_value=verifier.EnvironmentVerificationResult(
        scenario_id="", agent_claimed_resolved=False,
        env_resolved=False, verification_status="synthetic",
    ))
    from agents.tools import comms
    comms.discord_scenario_run_ping = MagicMock()

    roles: list[str] = []
    original_agent = coordinator.call_agent

    async def completion(_role, request):
        if request.get("tools") and not any(
            message.get("role") == "tool" for message in request["messages"]
        ):
            message = {
                "content": "",
                "tool_calls": [{
                    "id": "synthetic-exact-action",
                    "type": "function",
                    "function": {
                        "name": "kubectl_scale",
                        "arguments": json.dumps({
                            "deployment": "frontend", "namespace": "default",
                            "replicas": 2,
                        }),
                    },
                }],
            }
            finish_reason = "tool_calls"
        else:
            message = {"content": '{"outcome":"unresolved"}'}
            finish_reason = "stop"
        return {
            "model": coordinator.MODEL_NAME,
            "choices": [{"message": message, "finish_reason": finish_reason}],
        }

    async def agent(role, payload):
        roles.append(role)
        if role == "remediation":
            return await original_agent(
                role, payload, max_turns=2, completion_provider=completion,
            )
        if role == "triage":
            final = {"severity": "P1", "title": "Synthetic approval test"}
        elif role == "diagnosis":
            final = {"root_cause": "synthetic test"}
        else:
            final = {"status": "synthetic_no_mutation"}
        return {"role": role, "trajectory": [], "final": final}

    coordinator.call_agent = agent
    coordinator.approval_gate.timeout_seconds = float(os.environ["TEST_APPROVAL_TIMEOUT"])
    async with stage4_approval_server(os.environ["ATLASOPS_API_KEY"]) as base_url:
        publish_json(output_dir / "ready.json", {"base_url": base_url})
        incident = await coordinator.handle_incident(
            {"commonLabels": {"alertname": "SyntheticApproval", "severity": "critical"}},
            incident_id="inc-stage4-process-test",
        )
        publish_json(
            output_dir / "result.json",
            {
                "approval": incident["approval"],
                "remediation": incident["remediation"]["final"],
                "roles": roles,
                "mutations": mutation.call_count,
            },
        )


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
