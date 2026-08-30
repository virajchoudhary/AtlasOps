"""Remediation model-behaviour probe for the G4 v4 tool contract.

WHAT THIS IS NOT
----------------
This is **not** Gate G4 evidence and must never be recorded as such. Tool
*results* are simulated in-process. There is no cluster, no Chaos Mesh, no
Alertmanager, and the objective environment verifier never runs. Nothing here
can close a gate.

WHAT IT IS FOR
--------------
It isolates one question the live run answers only slowly and expensively:

    Given the v4 tool contract, does the real model discover the chaos
    experiment's exact name and stop it — or does it loop on argocd_rollback
    the way EXP-STAGE4-SF002-008 did?

Everything except the model is held fixed, so a difference in the tool-call
sequence is attributable to the model and the prompt/tool contract rather than
to cluster timing, alert delivery, or telemetry readiness.

Two modes:

  (default)    an active StressChaos targets paymentservice, and Argo CD owns
               zero Applications — exactly the run-008 environment.
  --no-chaos   negative control: no experiment exists and a rollback would
               genuinely succeed. Guards against the opposite failure, where the
               agent becomes chaos-fixated and stops investigating.

Usage:
    ollama serve &
    ollama pull qwen2.5:7b-instruct
    .venv/bin/python scripts/probe_remediation_behaviour.py
    .venv/bin/python scripts/probe_remediation_behaviour.py --no-chaos

Exit code 0 means the probe's expectation held.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The fault recorded in artifacts/evidence/stage4/EXP-STAGE4-SF002-008.json.
LIVE_EXPERIMENT = {
    "kind": "StressChaos",
    "name": "sf-002-paymentservice-cpu",
    "namespace": "chaos-mesh",
    "created": "2026-08-25T03:44:42Z",
    "action": "",
    "duration": "10m",
    "target": {"namespaces": ["default"], "app": "paymentservice", "mode": "one"},
    "desired_phase": "Run",
    "stressors": {"cpu": {"workers": 4, "load": 90}},
}

BASELINE_CORES = 0.0008402604427100003
FAULTED_CORES = 0.1597448920662832


class SimulatedCluster:
    """In-process stand-in for the tool results, not for the cluster itself."""

    def __init__(self, *, chaos_active: bool, argocd_has_apps: bool) -> None:
        self.chaos_active = chaos_active
        self.argocd_has_apps = argocd_has_apps
        self.replicas = 1

    def chaos_list_experiments(self, namespace: str = "-A") -> dict:
        experiments = [LIVE_EXPERIMENT] if self.chaos_active else []
        return {
            "success": True,
            "count": len(experiments),
            "experiments": experiments,
            "truncated": False,
        }

    def chaos_stop_experiment(self, kind: str, name: str, namespace: str = "chaos-mesh") -> dict:
        if not self.chaos_active or name != LIVE_EXPERIMENT["name"]:
            return {"success": False, "error": f'chaos resource "{name}" not found'}
        self.chaos_active = False
        return {
            "success": True,
            "action": "stopped_chaos_experiment",
            "kind": kind,
            "name": name,
            "namespace": namespace,
            "stdout": f'stresschaos.chaos-mesh.org "{name}" deleted',
        }

    def argocd_rollback(self, app: str, revision: str) -> dict:
        if self.argocd_has_apps and str(revision).isdigit():
            return {"success": True, "message": f"Rollback of {app} to revision {revision} initiated."}
        # Canonical environment: Argo CD owns zero Applications.
        return {
            "success": False,
            "error": "argocd_request_error: request failed",
            "error_class": "request_failed",
        }

    def promql_query(self, query: str) -> dict:
        cores = FAULTED_CORES if self.chaos_active else BASELINE_CORES
        return {"success": True, "result": [{"metric": {}, "value": [1787630003, str(cores)]}]}

    def kubectl_get(self, resource: str, namespace: str = "-A", output: str = "json") -> dict:
        return {
            "success": True,
            "parsed": {
                "items": [
                    {
                        "kind": "Deployment",
                        "metadata": {"name": "paymentservice", "namespace": "default"},
                        "status": {
                            "replicas": self.replicas,
                            "readyReplicas": self.replicas,
                            "availableReplicas": self.replicas,
                        },
                    }
                ]
            },
        }

    def kubectl_scale(self, deployment: str, replicas: int, namespace: str = "default") -> dict:
        self.replicas = int(replicas)
        return {"success": True, "stdout": f"deployment.apps/{deployment} scaled"}

    def registry(self) -> dict:
        return {
            "chaos_list_experiments": self.chaos_list_experiments,
            "chaos_stop_experiment": self.chaos_stop_experiment,
            "argocd_rollback": self.argocd_rollback,
            "promql_query": self.promql_query,
            "kubectl_get": self.kubectl_get,
            "kubectl_scale": self.kubectl_scale,
            "kubectl_describe": lambda **_: {"success": True, "stdout": "Events: <none>"},
            "kubectl_rollout": lambda **_: {"success": True, "stdout": "rolled back"},
            "alertmanager_silence": lambda **_: {"success": True, "silence_id": "probe"},
            "slack_post_update": lambda **_: {"success": True, "mode": "local"},
        }


def remediation_input() -> dict:
    """Triage/diagnosis exactly as a scenario-neutral Diagnosis role produces them.

    The diagnosis category is deliberately *not* fault_injection: Diagnosis has no
    chaos-aware tool by design, so it reports resource saturation. Remediation has
    to reach the right branch anyway.
    """
    return {
        "incident_id": "probe-sf002",
        "triage": {
            "incident_id": "probe-sf002",
            "severity": "P1",
            "title": "High CPU usage on paymentservice",
            "affected_services": ["paymentservice"],
            "blast_radius": {
                "services": ["paymentservice"],
                "namespaces": ["default"],
                "revenue_path_affected": True,
                "user_impact_pct": 100,
            },
        },
        "diagnosis": {
            "root_cause": {
                "category": "resource",
                "specific": (
                    "paymentservice CPU is saturated at roughly 190x its baseline. "
                    "No recent deployment was found in Argo CD history."
                ),
                "evidence": [
                    {"tool": "promql_query", "finding": f"CPU {FAULTED_CORES:.4f} vs {BASELINE_CORES:.4f} baseline"},
                    {"tool": "argocd_list_apps", "finding": "no applications and no recent deployments"},
                ],
            },
            "confidence": 0.6,
            "recommended_fix": [{"action": "investigate_resource_saturation", "target": "paymentservice"}],
        },
        "approval_mode": "auto",
        "approval": {"status": "approved", "approved_by": "probe"},
    }


async def run_probe(no_chaos: bool) -> int:
    from agents.circuit_breaker import circuit_breaker
    from agents.coordinator import call_agent

    cluster = SimulatedCluster(chaos_active=not no_chaos, argocd_has_apps=no_chaos)
    circuit_breaker.reset()

    with patch.dict("agents.coordinator.TOOL_REGISTRY", cluster.registry()):
        with patch("agents.coordinator.thought_emit", MagicMock()):
            result = await call_agent("remediation", remediation_input(), max_turns=10)

    steps = [s for s in result["trajectory"] if s.get("tool")]
    print("\n=== TOOL CALL SEQUENCE ===")
    for i, step in enumerate(steps, 1):
        blocked = " [BLOCKED]" if step.get("repeated_failure_blocked") else ""
        args = json.dumps(step.get("args", {}))
        print(f"{i:>2}. {step['tool']:<24}{blocked} {args[:68]}")

    rollbacks = sum(1 for s in steps if s.get("tool") == "argocd_rollback")
    checked = any(s.get("tool") == "chaos_list_experiments" for s in steps)
    print(f"\nrepeated-failure blocks : {sum(1 for s in steps if s.get('repeated_failure_blocked'))}")
    print(f"argocd_rollback attempts: {rollbacks}   (EXP-STAGE4-SF002-008 made 9)")
    print(f"checked for chaos       : {checked}")

    print("\n=== VERDICT ===")
    if no_chaos:
        moved_on = [
            s["tool"] for s in steps
            if s.get("tool") in ("kubectl_scale", "kubectl_rollout", "argocd_rollback")
            and s.get("output", {}).get("success")
        ]
        print("negative control — no experiment exists")
        print(f"  checked, then moved to another branch: {moved_on or 'NO'}")
        ok = checked and bool(moved_on)
    else:
        stopped = any(
            s.get("tool") == "chaos_stop_experiment" and s.get("output", {}).get("success")
            for s in steps
        )
        print(f"  reached goal state (experiment cleared): {stopped}")
        ok = stopped
    print(f"  PROBE {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-chaos", action="store_true",
                        help="negative control: no experiment, rollback would succeed")
    parser.add_argument("--base", default=os.getenv("VLLM_BASE", "http://localhost:11434/v1"))
    parser.add_argument("--model", default=os.getenv("AGENT_MODEL", "qwen2.5:7b-instruct"))
    args = parser.parse_args()

    os.environ["BACKEND"] = "openai"
    os.environ["VLLM_BASE"] = args.base
    os.environ["AGENT_MODEL"] = args.model
    os.environ.setdefault("ATLASOPS_AUDIT_SECRET", "probe-only-secret-not-for-evidence")
    os.environ.setdefault("ATLASOPS_AUDIT_LOG", "/tmp/atlasops-probe-audit.jsonl")

    print(f"probe: model={args.model} base={args.base} "
          f"mode={'no-chaos' if args.no_chaos else 'active-chaos'}")
    print("NOTE: simulated tool results — not Gate G4 evidence.")
    return asyncio.run(run_probe(args.no_chaos))


if __name__ == "__main__":
    raise SystemExit(main())
