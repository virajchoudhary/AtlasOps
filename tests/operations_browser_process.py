"""Serve the real Operations handlers with disposable non-live browser state."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

TEST_KEY = "test-placeholder-operations-browser-key"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7873)
    args = parser.parse_args()
    source_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True,
    ).strip()
    with tempfile.TemporaryDirectory(prefix="atlasops-operations-browser-") as temporary:
        root = Path(temporary)
        os.environ.update({
            "ATLASOPS_API_KEY": TEST_KEY,
            "ATLASOPS_AUDIT_SECRET": "test-placeholder-operations-browser-audit",
            "ATLASOPS_AUDIT_LOG": str(root / "audit.jsonl"),
            "ATLASOPS_RUNTIME_DATA_DIR": str(root),
            "ATLASOPS_OPERATIONAL_MODE": "1",
            "ATLASOPS_OPERATOR_ID": "browser-fixture-operator",
            "ATLASOPS_SOURCE_SHA": source_sha,
            "ATLASOPS_WORKLOAD_NAMESPACE": "atlasops-boutique",
            "ATLASOPS_AUTO_HF_INFERENCE": "0",
            "ATLASOPS_USE_HF_INFERENCE": "0",
            "BACKEND": "vllm",
            "LLM_API_KEY": "",
            "VLLM_BASE": "http://127.0.0.1:9/v1",
            "SLACK_WEBHOOK_URL": "",
            "DISCORD_WEBHOOK_URL": "",
            "ALERTMANAGER_WEBHOOK_SECRET": "",
            "POSTMORTEM_DIR": str(root / "postmortems"),
            "TRAJECTORIES_DIR": str(root / "trajectories"),
        })
        for name in tuple(os.environ):
            if name.startswith("OTEL_"):
                os.environ.pop(name)

        import uvicorn

        import app as application
        from agents import coordinator
        from agents.approval import approval_gate
        from agents.execution_journal import get_execution_journal
        from agents.operations_api import operations_lifespan

        def blocked_tool(*_args, **_kwargs):
            raise AssertionError("Browser fixtures cannot execute infrastructure tools")

        async def blocked_completion(*_args, **_kwargs):
            raise AssertionError("Browser fixtures cannot invoke a model")

        coordinator.post_with_retry = blocked_completion
        for name in tuple(coordinator.TOOL_REGISTRY):
            coordinator.TOOL_REGISTRY[name] = blocked_tool
        application.subprocess.run = blocked_tool

        target = {
            "server": "https://kubernetes.default.svc",
            "context": "in-cluster",
            "api_version": "argoproj.io/v1alpha1",
            "kind": "Application",
            "namespace": "argocd",
            "name": "atlasops-boutique",
            "uid": "browser-fixture-application",
            "resource_version": "42",
        }
        workload = {
            **target,
            "api_version": "apps/v1",
            "kind": "Deployment",
            "namespace": "atlasops-boutique",
            "name": "frontend",
            "uid": "browser-fixture-frontend",
        }
        action = {
            "tool": "argocd_rollback",
            "arguments": {"app": "atlasops-boutique", "revision": "0"},
            "target": target,
            "verification_target": workload,
            "patch": [
                {"op": "test", "path": "/metadata/uid", "value": target["uid"]},
                {"op": "test", "path": "/metadata/resourceVersion", "value": "42"},
                {"op": "add", "path": "/operation", "value": {
                    "sync": {"revision": source_sha, "prune": False},
                }},
            ],
        }

        @asynccontextmanager
        async def fixture_lifespan(instance):
            async with operations_lifespan(instance):
                journal = get_execution_journal()
                journal.begin(
                    "fixture-pending", "a" * 32, alert={}, anchors=None,
                    evidence_class="NON_EMPIRICAL_SOFTWARE_FIXTURE",
                )
                pending = approval_gate.request_action(
                    incident_id="fixture-pending", severity="P1", action=action,
                )
                journal.note(
                    "a" * 32, phase="approval", event="approval_pending",
                    patch={"prepared_action": action, "action_digest": pending.action_digest},
                )
                for incident_id, execution_id, decision in (
                    ("fixture-rejected", "b" * 32, "rejected"),
                    ("fixture-timeout", "c" * 32, "timeout"),
                ):
                    journal.begin(
                        incident_id, execution_id, alert={}, anchors=None,
                        evidence_class="NON_EMPIRICAL_SOFTWARE_FIXTURE",
                    )
                    journal.note(
                        execution_id, phase="finished", state="completed",
                        event=f"approval_{decision}", patch={
                            "approval": {"decision": decision},
                            "remediation": {"status": f"approval_{decision}"},
                            "verification": {"verification_status": "inconclusive"},
                            "resolved": False, "env_resolved": False,
                        },
                    )

                async def wait_for_fixture_decision():
                    decision, _permit = await approval_gate.wait_for_action_decision(
                        "fixture-pending", request_token=pending.token,
                    )
                    journal.note(
                        "a" * 32, phase="finished", state="completed",
                        event="fixture_decision_recorded",
                        patch={
                            "approval": {"decision": decision["status"]},
                            "resolved": False, "env_resolved": False,
                        },
                    )

                waiter = asyncio.create_task(wait_for_fixture_decision())
                try:
                    await asyncio.sleep(0)
                    yield
                finally:
                    if not waiter.done():
                        waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)

        application.app.router.lifespan_context = fixture_lifespan
        uvicorn.run(application.app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
