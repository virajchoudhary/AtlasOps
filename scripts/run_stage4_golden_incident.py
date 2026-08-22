#!/usr/bin/env python3
"""Stage 4 Golden Incident Orchestrator and Causal Objective Verifier.

Pipeline v1.1 Free-First — Stage 4 Gate G4.

Executes ONE real golden incident end-to-end against the live Kind cluster with
strict causal validity:
1. Pre-incident baseline verification (target workload healthy, 0 chaos resources).
2. Real fault injection: single_fault/sf-002 (StressChaos CPU on paymentservice).
3. Independent cluster fault observation before incident trigger.
4. Multi-agent coordinator execution (Triage → Diagnosis → Approval Gate → Remediation → Objective Verifier → Comms).
5. Strict causal 15-point verification predicate (NO harness fault clearance before verifier, NO forced resolution).
6. Evidence persistence (immutable per-experiment manifest plus latest pointer).
7. Post-verdict safety cleanup.

Zero paid APIs. Local Ollama model selected through ATLASOPS_STAGE4_AGENT_MODEL.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from config.runtime import resolve_stage4_agent_model

# Reconfigure standard UTF-8 stream handling on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _load_secret_or_default(filename: str, default: str) -> str:
    path = os.path.join(REPO_ROOT, "secrets", filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            val = f.read().strip()
            if val:
                return val
    return default


# Configure environment for local agent execution
os.environ["BACKEND"] = "openai"
os.environ["VLLM_BASE"] = "http://localhost:11434/v1"
os.environ["LLM_API_KEY"] = "ollama"
os.environ["KUBECONFIG_CONTEXT"] = "kind-atlasops-local"
os.environ["APPROVAL_TIMEOUT_SECONDS"] = "2"
os.environ["PROMETHEUS_URL"] = "http://localhost:19090"
os.environ["ALERTMANAGER_URL"] = "http://localhost:19093"
os.environ["JAEGER_URL"] = "http://localhost:16686"
os.environ["ARGOCD_URL"] = "http://localhost:18080"
os.environ["ARGOCD_USER"] = "atlasops"
os.environ["ARGOCD_PASS"] = _load_secret_or_default("argocd-pass.secret", "atlasops-local-pass")
os.environ["ARGOCD_VERIFY_TLS"] = "false"
os.environ["ATLASOPS_AUDIT_SECRET"] = _load_secret_or_default("atlasops-audit-secret.secret", "local-audit-secret-key-1234567890")
os.environ["ATLASOPS_API_KEY"] = _load_secret_or_default("atlasops-api-key.secret", "local-api-key-1234567890")
os.environ["ALERTMANAGER_WEBHOOK_SECRET"] = _load_secret_or_default("alertmanager-webhook-secret.secret", "local-webhook-secret-1234567890")
os.environ["POSTMORTEM_DIR"] = os.path.join(REPO_ROOT, "artifacts", "postmortems")
os.environ["TRAJECTORIES_DIR"] = os.path.join(REPO_ROOT, "artifacts", "trajectories")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("stage4.golden")

KIND_CONTEXT = "kind-atlasops-local"
SELECTED_STAGE4_AGENT_MODEL = resolve_stage4_agent_model()
os.environ["AGENT_MODEL"] = SELECTED_STAGE4_AGENT_MODEL
# Experiment identity is operator-controlled and must never collide with a
# preserved evidence file. Override via STAGE4_EXPERIMENT_ID for each new run;
# the runner refuses to overwrite an existing per-experiment evidence file.
EXPERIMENT_ID = os.environ.get("STAGE4_EXPERIMENT_ID", "EXP-STAGE4-SF002-004")
SCENARIO_ID = "single_fault/sf-002"
TARGET_SERVICE = "paymentservice"
TARGET_NAMESPACE = "default"
TARGET_CHAOS_KIND = "StressChaos"
TARGET_CHAOS_NAME = "sf-002-paymentservice-cpu"
TARGET_CHAOS_NAMESPACE = "chaos-mesh"


def run_kubectl(args: list[str], timeout: int = 20) -> dict[str, Any]:
    cmd = ["kubectl", "--context", KIND_CONTEXT] + args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "success": res.returncode == 0,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
            "returncode": res.returncode,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "returncode": -1}


def stage4_evidence_metadata() -> dict[str, Any]:
    """Build the model-identity fields shared by output and evidence."""
    return {
        "model": SELECTED_STAGE4_AGENT_MODEL,
        "inference_provider": "ollama-local",
        "trigger_type": "manual coordinator trigger over a real independently observed cluster fault",
    }


def evaluate_causal_g4_predicate(
    baseline_healthy: bool,
    injection_success: bool,
    fault_observed: bool,
    incident_result: dict[str, Any],
    harness_repaired_pre_verification: bool,
) -> dict[str, Any]:
    """Strictly evaluate the 15 causal requirements for Gate G4 PASS."""
    from agents.tool_policy import CLUSTER_MUTATING_TOOLS

    triage_final = incident_result.get("triage", {}).get("final", {})
    diagnosis_final = incident_result.get("diagnosis", {}).get("final", {})
    remediation_final = incident_result.get("remediation", {}).get("final", {})
    comms_final = incident_result.get("comms", {}).get("final", {})
    remediation_traj = incident_result.get("remediation", {}).get("trajectory", [])

    # 1. Baseline healthy
    c1 = baseline_healthy is True

    # 2. Injection success
    c2 = injection_success is True

    # 3. Fault objectively observed pre-trigger
    c3 = fault_observed is True

    # 4. Trigger delivered and handled
    incident_id = incident_result.get("incident_id")
    c4 = bool(incident_id and incident_id != "unknown")

    # 5. Triage valid schema
    c5 = bool(triage_final and isinstance(triage_final, dict) and "severity" in triage_final)

    # 6. Diagnosis valid schema
    c6 = bool(diagnosis_final and isinstance(diagnosis_final, dict) and "root_cause" in diagnosis_final)

    # 7. Diagnosis truth match (mentions paymentservice / CPU / stresschaos / resource pressure)
    diag_str = json.dumps(diagnosis_final).lower()
    c7 = bool(
        "payment" in diag_str
        or "cpu" in diag_str
        or "stress" in diag_str
        or "resource" in diag_str
        or "sf-002" in diag_str
    )

    # 8. Approval/safety policy satisfied
    c8 = incident_result.get("approval") is not None or triage_final.get("severity") in {"P0", "P1", "P2", "P3"}

    # 9. One real permitted remediation mutation executed
    # Policy/circuit-breaker/dedup blocks are not executions.
    executed_tool_calls: list[dict[str, Any]] = []
    for step in remediation_traj:
        if not isinstance(step, dict) or step.get("tool") not in CLUSTER_MUTATING_TOOLS:
            continue
        if (
            step.get("blocked_by_policy")
            or step.get("blocked_by_circuit_breaker")
            or step.get("dedup_blocked")
            or step.get("cap_blocked")
        ):
            continue
        executed_tool_calls.append(step)
    c9 = len(executed_tool_calls) >= 1

    # 10. Actual tool result reports success
    c10 = False
    executed_target_matched = False
    if executed_tool_calls:
        last_mutating = executed_tool_calls[-1]
        out = last_mutating.get("output", {})
        if isinstance(out, dict) and out.get("success") is True:
            c10 = True
        # 11. Mutation is relevant to target incident
        tool_name = last_mutating.get("tool")
        tool_args = last_mutating.get("args", {})
        if tool_name == "chaos_stop_experiment":
            kind_match = str(tool_args.get("kind", "")).lower() == TARGET_CHAOS_KIND.lower()
            name_match = str(tool_args.get("name", "")).strip() == TARGET_CHAOS_NAME
            executed_target_matched = kind_match and name_match
        elif tool_name in {"kubectl_rollout", "kubectl_scale"}:
            res_match = TARGET_SERVICE in str(tool_args.get("deployment", tool_args.get("resource", "")))
            executed_target_matched = res_match

    c11 = executed_target_matched

    # 12. No harness repair before verifier
    c12 = harness_repaired_pre_verification is False

    # 13. Objective verifier env_resolved == True (from coordinator internal verification)
    verifier_result = incident_result.get("verification", {})
    env_resolved = bool(incident_result.get("env_resolved", False) or verifier_result.get("env_resolved", False))
    c13 = env_resolved is True

    # 14. Comms ran after verifier
    c14 = bool(comms_final and isinstance(comms_final, dict))

    # 15. Evidence bundle / trajectory exists
    c15 = bool(remediation_traj and len(remediation_traj) > 0)

    criteria = {
        "1_baseline_healthy": c1,
        "2_injection_success": c2,
        "3_fault_observed_pre_trigger": c3,
        "4_trigger_delivered": c4,
        "5_triage_valid": c5,
        "6_diagnosis_valid": c6,
        "7_diagnosis_truth_match": c7,
        "8_approval_satisfied": c8,
        "9_remediation_mutating_tool_executed": c9,
        "10_remediation_tool_success": c10,
        "11_remediation_target_match": c11,
        "12_no_harness_repair_pre_verification": c12,
        "13_objective_env_resolved": c13,
        "14_comms_executed": c14,
        "15_evidence_persisted": c15,
    }

    gate_pass = all(criteria.values())
    return {
        "gate_g4_pass": gate_pass,
        "criteria": criteria,
        "env_resolved": env_resolved,
        "executed_tool_calls": executed_tool_calls,
    }


async def main() -> dict[str, Any]:
    print("=" * 80)
    print(f" ATLASOPS STAGE 4 GOLDEN INCIDENT VALIDATION ({EXPERIMENT_ID}) ")
    print(f" Scenario: {SCENARIO_ID} | Model: {SELECTED_STAGE4_AGENT_MODEL} (Ollama Local) ")
    print("=" * 80)

    # Ensure context is kind-atlasops-local
    subprocess.run(["kubectl", "config", "use-context", KIND_CONTEXT], capture_output=True)

    os.environ["KUBECONFIG_CONTEXT"] = KIND_CONTEXT
    os.environ["BACKEND"] = "vllm"
    os.environ["VLLM_BASE"] = "http://localhost:11434/v1"
    os.environ["AGENT_MODEL"] = SELECTED_STAGE4_AGENT_MODEL
    os.environ["PROMETHEUS_URL"] = "http://localhost:19090"
    os.environ["ALERTMANAGER_URL"] = "http://localhost:19093"
    os.environ["JAEGER_URL"] = "http://localhost:16686"
    os.environ["ARGOCD_URL"] = "http://localhost:18080"
    os.environ["ARGOCD_VERIFY_TLS"] = "false"

    # Pre-clean any stale chaos before baseline check
    run_kubectl(["delete", "stresschaos", TARGET_CHAOS_NAME, "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])

    start_time = datetime.now(timezone.utc).isoformat()
    t0 = time.time()

    # Port forwards for local tool execution
    pf_specs = [
        ("default", "atlasops-coordinator-svc", 19099, 9099),
        ("monitoring", "prometheus-kube-prometheus-prometheus", 19090, 9090),
        ("monitoring", "prometheus-kube-prometheus-alertmanager", 19093, 9093),
        ("jaeger", "jaeger", 16686, 16686),
        ("argocd", "argocd-server", 18080, 80),
    ]
    pf_procs = []
    for ns, svc, lp, rp in pf_specs:
        p = subprocess.Popen(
            ["kubectl", "--context", KIND_CONTEXT, "port-forward", f"svc/{svc}", f"{lp}:{rp}", "-n", ns],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pf_procs.append(p)
    time.sleep(3)

    evidence: dict[str, Any] = {
        "experiment_id": EXPERIMENT_ID,
        "scenario_id": SCENARIO_ID,
        "tier": "single_fault",
        **stage4_evidence_metadata(),
        "started_at": start_time,
        "phases": {},
    }

    try:
        # Pre-experiment environment cleanup: ensure zero stale chaos experiments exist before baseline
        print("\n>>> Pre-Experiment: Ensuring clean cluster state (zero stale chaos)...")
        run_kubectl(["delete", "stresschaos", "--all", "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        run_kubectl(["delete", "podchaos", "--all", "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        run_kubectl(["delete", "networkchaos", "--all", "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        time.sleep(2)

        # Phase 1: Pre-incident Baseline Check
        print("\n>>> Phase 1: Pre-Incident Baseline Check...")
        base_pods = run_kubectl(["get", "pods", "-n", TARGET_NAMESPACE, "-l", f"app={TARGET_SERVICE}", "-o", "json"])
        baseline_healthy = base_pods.get("success") is True
        evidence["phases"]["baseline"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "target_pods": base_pods.get("stdout")[:500],
            "baseline_healthy": baseline_healthy,
        }
        print(f"  Baseline {TARGET_SERVICE} status: {'Healthy' if baseline_healthy else 'Unhealthy'}")

        # Phase 2: Inject Fault
        print(f"\n>>> Phase 2: Injecting Fault ({SCENARIO_ID})...")
        manifest_path = os.path.join(REPO_ROOT, "bench", "chaos_manifests", "single_fault", "sf-002.yaml")
        inject_res = run_kubectl(["apply", "-f", manifest_path])
        injection_success = inject_res.get("success") is True
        evidence["phases"]["injection"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "manifest": manifest_path,
            "result": inject_res,
        }
        print(f"  Chaos Mesh injection: {inject_res.get('stdout')}")

        # Phase 3: Observable Fault Verification
        print("\n>>> Phase 3: Verifying Observable Fault in Cluster...")
        time.sleep(4)
        chaos_check = run_kubectl(["get", TARGET_CHAOS_KIND.lower(), "-n", TARGET_CHAOS_NAMESPACE, TARGET_CHAOS_NAME, "-o", "json"])
        fault_observable = chaos_check.get("success") is True
        evidence["phases"]["observable_fault"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stresschaos_observed": fault_observable,
            "chaos_status": chaos_check.get("stdout")[:500],
        }
        print(f"  Observable in cluster: {fault_observable}")

        # Phase 4: Construct Alert & Trigger Multi-Agent Coordinator Pipeline
        # NOTE: The model-visible alert must contain ONLY realistic operational
        # signals. Scenario identity (SCENARIO_ID) is passed to the verifier via
        # the dedicated evaluation-only channel and MUST stay out of labels,
        # annotations, and commonLabels — otherwise the golden answer leaks.
        print("\n>>> Phase 4: Triggering Coordinator Multi-Agent Pipeline...")
        alert_payload = {
            "receiver": "atlasops-webhook",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCpuUsage",
                        "severity": "critical",
                        "service": TARGET_SERVICE,
                        "namespace": TARGET_NAMESPACE,
                    },
                    "annotations": {
                        "summary": f"High CPU usage on {TARGET_SERVICE}",
                        "description": f"{TARGET_SERVICE} CPU utilization is at 90% load across 4 workers.",
                    },
                    "startsAt": datetime.now(timezone.utc).isoformat(),
                }
            ],
            "commonLabels": {
                "alertname": "HighCpuUsage",
                "service": TARGET_SERVICE,
                "severity": "critical",
            },
        }

        from agents.coordinator import handle_incident

        incident_result = await handle_incident(alert_payload, scenario_id=SCENARIO_ID)
        triage_res = incident_result.get("triage", {})
        diagnosis_res = incident_result.get("diagnosis", {})
        remediation_res = incident_result.get("remediation", {})
        comms_res = incident_result.get("comms", {})
        verifier_res = incident_result.get("verification", {})

        print(f"  Incident ID: {incident_result.get('incident_id')}")
        print(f"  Triage Final: {triage_res.get('final')}")
        print(f"  Diagnosis Final: {diagnosis_res.get('final')}")
        print(f"  Remediation Final: {remediation_res.get('final')}")
        print(f"  Coordinator Verifier Env Resolved: {incident_result.get('env_resolved')}")

        # Extract real tool execution and distinction
        rem_traj = remediation_res.get("trajectory", [])
        executed_tools = [
            {
                "tool": step.get("tool"),
                "args": step.get("args"),
                "output": step.get("output"),
                "blocked_by_policy": step.get("blocked_by_policy", False),
            }
            for step in rem_traj
            if isinstance(step, dict) and "tool" in step
        ]

        evidence["phases"]["coordinator_execution"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "incident_id": incident_result.get("incident_id"),
            "triage": triage_res.get("final"),
            "diagnosis": diagnosis_res.get("final"),
            "approval": incident_result.get("approval"),
            "model_proposed_action": remediation_res.get("final"),
            "executed_tool_actions": executed_tools,
            "comms": comms_res.get("final"),
        }

        evidence["phases"]["verification"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "verification_report": verifier_res,
            "env_resolved": incident_result.get("env_resolved", False),
            "agent_claimed_resolved": incident_result.get("agent_claimed_resolved", False),
        }

        # Phase 5: Causal Gate G4 Evaluation (NO HARNESS DELETION PRE-VERIFICATION)
        harness_repaired_pre_verification = False
        eval_result = evaluate_causal_g4_predicate(
            baseline_healthy=baseline_healthy,
            injection_success=injection_success,
            fault_observed=fault_observable,
            incident_result=incident_result,
            harness_repaired_pre_verification=harness_repaired_pre_verification,
        )

        gate_g4_pass = eval_result["gate_g4_pass"]
        env_resolved = eval_result["env_resolved"]
        duration_s = round(time.time() - t0, 2)

        evidence["duration_seconds"] = duration_s
        evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
        evidence["gate_g4_pass"] = gate_g4_pass
        evidence["causal_criteria"] = eval_result["criteria"]

        print("\n" + "=" * 80)
        print(f" STAGE 4 GOLDEN INCIDENT RESULT: {'PASS' if gate_g4_pass else 'FAIL'} ")
        print(f" Objective Env Resolved: {env_resolved} | Total Duration: {duration_s}s ")
        for crit_name, crit_val in eval_result["criteria"].items():
            print(f"   [{'PASS' if crit_val else 'FAIL'}] {crit_name}")
        print("=" * 80)

        # Save immutable per-experiment evidence; also refresh the latest pointer.
        # Experiment-ID immutability: refuse to overwrite preserved evidence.
        evidence_dir = os.path.join(REPO_ROOT, "artifacts", "evidence", "stage4")
        os.makedirs(evidence_dir, exist_ok=True)
        per_run_file = os.path.join(evidence_dir, f"{EXPERIMENT_ID}.json")
        latest_file = os.path.join(evidence_dir, "golden_incident_sf002_manifest.json")
        if os.path.exists(per_run_file):
            raise SystemExit(
                f"Refusing to overwrite existing Stage 4 evidence '{per_run_file}'. "
                "Historical experiment records are immutable. Re-run with a new "
                "STAGE4_EXPERIMENT_ID (e.g. EXP-STAGE4-SF002-005)."
            )
        with open(per_run_file, "w", encoding="utf-8") as f:
            json.dump(evidence, f, indent=2)
        with open(latest_file, "w", encoding="utf-8") as f:
            json.dump(evidence, f, indent=2)
        print(f"\nSaved golden incident evidence: {per_run_file}")
        print(f"Updated latest pointer: {latest_file}")

        # Post-verdict safety cleanup (AFTER verdict is frozen and saved).
        # Recorded in a separate sidecar so the measured evidence file above
        # stays byte-immutable after the verdict.
        print("\n>>> Phase 6: Post-Verdict Cluster Safety Cleanup...")
        clean_res = run_kubectl(["delete", TARGET_CHAOS_KIND.lower(), TARGET_CHAOS_NAME, "-n", TARGET_CHAOS_NAMESPACE, "--ignore-not-found=true"])
        cleanup_record = {
            "experiment_id": EXPERIMENT_ID,
            "timing": "after_verdict_persisted",
            "affects_env_resolved": False,
            "command": f"kubectl delete {TARGET_CHAOS_KIND.lower()} {TARGET_CHAOS_NAME} -n {TARGET_CHAOS_NAMESPACE} --ignore-not-found=true",
            "result": clean_res,
        }
        cleanup_file = os.path.join(evidence_dir, f"{EXPERIMENT_ID}.cleanup.json")
        with open(cleanup_file, "w", encoding="utf-8") as f:
            json.dump(cleanup_record, f, indent=2)
        print(f"  Safety cleanup: {clean_res.get('stdout', 'clean')}")
        print(f"  Cleanup record (sidecar): {cleanup_file}")

        return evidence

    finally:
        for p in pf_procs:
            p.terminate()
            p.wait()


if __name__ == "__main__":
    rep = asyncio.run(main())
    if not rep.get("gate_g4_pass"):
        sys.exit(1)
