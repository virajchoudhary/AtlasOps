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


async def main(output_dir: Path) -> None:
    import agents.coordinator as coordinator
    import agents.verifier as verifier

    coordinator.TRAJECTORIES_DIR = output_dir / "trajectories"
    coordinator.require_audit_log = MagicMock()
    coordinator.audit_log = MagicMock()
    coordinator.thought_emit = MagicMock()
    coordinator.circuit_breaker = CircuitBreaker()
    coordinator.TOOL_REGISTRY = {"slack_post_update": MagicMock(return_value={"success": True})}
    coordinator.settle_environment = AsyncMock(return_value={})
    verifier.verify_environment = MagicMock(return_value=verifier.EnvironmentVerificationResult(
        scenario_id="", agent_claimed_resolved=False,
        env_resolved=False, verification_status="synthetic",
    ))
    from agents.tools import comms
    comms.discord_scenario_run_ping = MagicMock()

    roles: list[str] = []

    async def agent(role, _payload):
        roles.append(role)
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
        (output_dir / "ready.json").write_text(json.dumps({"base_url": base_url}), encoding="utf-8")
        incident = await coordinator.handle_incident(
            {"commonLabels": {"alertname": "SyntheticApproval", "severity": "critical"}},
            incident_id="inc-stage4-process-test",
        )
        (output_dir / "result.json").write_text(
            json.dumps({"approval": incident["approval"], "remediation": incident["remediation"]["final"], "roles": roles}),
            encoding="utf-8",
        )


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
