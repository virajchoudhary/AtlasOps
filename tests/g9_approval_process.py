"""Synthetic G9 approval subprocess used by process-boundary tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from agents.approval import ApprovalGate
from agents.approval_http import loopback_approval_server
from training import grpo


class FakeEnvironment:
    def __init__(self, *, _action_approval_gate: ApprovalGate, **_kwargs):
        self.gate = _action_approval_gate

    async def step(
        self, completion: str, *, state: dict,
        _action_approval_permit=None, **_kwargs,
    ) -> dict:
        allowed = self.gate.consume_action_permit(
            _action_approval_permit,
            incident_id=state["incident_id"],
            action_digest=self.gate.action_digest(json.loads(completion)),
        )
        return {
            "status": "ok" if allowed else "blocked",
            "env_resolved": False,
            "allowed": allowed,
        }


def publish_json(path: Path, payload: dict) -> None:
    """Expose complete subprocess handoff records, never a partial JSON write."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream)
    os.replace(temporary, path)


async def main(output: Path) -> None:
    gate = ApprovalGate(timeout_seconds=10)
    key = os.environ["ATLASOPS_API_KEY"]
    action = {
        "tool": "kubectl_scale",
        "arguments": {"deployment": "paymentservice", "namespace": "default", "replicas": 2},
        "agent_claimed_resolved": False,
    }
    grpo.DirectPolicyEnvironment = FakeEnvironment
    reward = grpo.OnlineRewardFunction(
        ["single_fault"], execute_live_chaos=True, kube_context="kind-atlasops-test"
    )
    try:
        async with loopback_approval_server(gate, key) as base_url:
            publish_json(output / "ready.json", {"base_url": base_url})
            result = await reward._run_one_rollout(
                json.dumps(action),
                "single_fault/sf-002",
                "single_fault",
                {"commonLabels": {"severity": "critical"}, "alerts": []},
                approval_gate=gate,
            )
            publish_json(output / "result.json", {
                "decision": result["approval"]["decision"],
                "allowed": result["allowed"],
            })
    finally:
        reward._loop.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
