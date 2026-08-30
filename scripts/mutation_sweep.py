"""Mutation sweep: revert each repair in an isolated copy, confirm a test fails.

A surviving mutation means the fix is unprotected — the suite would stay green if
someone reverted it, so the repair would silently disappear on a later refactor.
This harness caught three such fixes in the G4 observability work, and two
"protective" tests that passed with their own fix reverted. The second is worth
knowing about: a test asserted `'"settling"' in <source of handle_incident>`,
and the *comment explaining why the key mattered* quoted
`incident["settling"]["settled"]` — so the substring matched even after the key
was deleted. A source-text assertion can be satisfied by prose about the thing
it is meant to check. Prefer driving the function and inspecting what it
returns; where that is impractical, assert on parsed structure (regex the keys)
rather than raw text.

Run a control first. A test that fails in an *unmutated* copy is an environment
result, not a catch, and will otherwise masquerade as protecting every fix:

    python scripts/mutation_sweep.py --control

Then the sweep:

    python scripts/mutation_sweep.py

Never modifies the working tree; every run happens in a temporary copy.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "bin" / "python"
if not PY.exists():  # fall back to the interpreter running this script
    PY = Path(sys.executable)

# Tests that fail in an unmutated exported copy (no .git). Verified by a control
# run; discounted so they cannot masquerade as catching a mutation.
ENV_FRAGILE: set[str] = set()

MUTATIONS = [
    (
        "PYTHON_BIN -> ignore an activated virtualenv",
        "infra/setup_impl.sh",
        """elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python3" ]]; then
  readonly PYTHON_BIN="${VIRTUAL_ENV}/bin/python3"
elif command -v python3 >/dev/null 2>&1; then""",
        """elif command -v python3 >/dev/null 2>&1; then""",
    ),
    (
        "judge fallback -> pre-fix 0.5 scores",
        "agents/judge.py",
        '''_FALLBACK = {
    "correctness": 0.0, "efficiency": 0.0, "reasoning": 0.0,
    "red_herring_handling": 0.0, "overall": 0.0, "critique": "judge_fallback",
    "judge_available": False,
}''',
        '''_FALLBACK = {
    "correctness": 0.5, "efficiency": 0.5, "reasoning": 0.5,
    "red_herring_handling": 0.5, "overall": 0.5, "critique": "judge_fallback",
}''',
    ),
    (
        "error signature -> collapse all kubectl failures",
        "agents/coordinator.py",
        '''    error = tool_output.get("error")
    if error:
        return str(error)[:120]
    stderr = str(tool_output.get("stderr") or "").strip()
    if stderr:
        return f"stderr:{stderr[:120]}"
    return f"returncode:{tool_output.get('returncode', 'unknown')}"''',
        '''    return str(tool_output.get("error", "unknown_error"))[:80]''',
    ),
    (
        "remove the blast-radius refund",
        "agents/coordinator.py",
        "circuit_breaker.release_cluster_mutation_reservation()",
        "pass",
    ),
    (
        "remove chaos_list_experiments from the remediation ACL",
        "agents/tool_policy.py",
        '        "chaos_list_experiments",\n        "chaos_stop_experiment",',
        '        "chaos_stop_experiment",',
    ),
    (
        "StepRewardTracker._MUTATING -> drift back to local copy",
        "config/runtime.py",
        "    _MUTATING = CLUSTER_MUTATING_TOOLS",
        '    _MUTATING = frozenset({"argocd_rollback", "kubectl_rollout", "kubectl_scale", "alertmanager_silence"})',
    ),
    (
        "GRPO rollout -> score the agent self-claim again",
        "training/grpo.py",
        '"resolved": bool(verification.get("env_resolved", False)),',
        '"resolved": remediation.get("outcome") == "resolved",',
    ),
    (
        "GRPO rollout -> drop dense step rewards",
        "training/grpo.py",
        "            **role_step_rewards,\n",
        "",
    ),
    (
        "benchmark -> leak scenario_id into the model prompt again",
        "bench/runner.py",
        "        incident = await handle_incident(alert, scenario_id=scenario_id)",
        '        alert["scenario_id"] = scenario_id\n        incident = await handle_incident(alert)',
    ),
    (
        "benchmark -> average ungraded episodes into the judge mean",
        "bench/runner.py",
        '"avg_reward": round(sum(judge_scores) / len(judge_scores), 3) if judge_scores else None,',
        '"avg_reward": round(sum(judge_scores) / max(len(judge_scores), 1), 3),',
    ),
    (
        "projection -> unbounded item count (truncated JSON)",
        "agents/coordinator.py",
        "        if len(json.dumps(compacted)) <= _MODEL_TOOL_RESULT_CHAR_CAP:\n            return compacted",
        "        return compacted",
    ),
    (
        "kubectl_logs -> reject qualified deployment/<name>",
        "agents/tools/kubectl.py",
        "    if not _K8S_LOG_TARGET_RE.match(str(pod).strip()):",
        "    if not _K8S_NAME_RE.match(str(pod).strip()):",
    ),
    (
        "kubectl_describe -> reject dotted CRD object names",
        "agents/tools/kubectl.py",
        "    if not _K8S_OBJECT_NAME_RE.match(str(name).strip()):",
        "    if not _K8S_NAME_RE.match(str(name).strip()):",
    ),
    (
        "release gate -> make missing run output a critical blocker again",
        "scripts/release_gate.py",
        '                "Benchmark output sanity",\n                "WARN",',
        '                "Benchmark output sanity",\n                "FAIL",',
    ),
    (
        "protocol -> stop pinning the remediation prompt",
        "config/g4_protocol.py",
        '        "remediation_prompt": {**remediation_prompt_profile(), "version": version},\n',
        "",
    ),
    (
        "protocol -> stop declaring the safety envelope",
        "config/g4_protocol.py",
        '        "safety_envelope": safety_envelope_profile(),\n',
        "",
    ),
    (
        "circuit breaker -> let investigation spend the whole budget",
        "agents/circuit_breaker.py",
        '        if role and role != "remediation":',
        "        if False:",
    ),
    (
        "coordinator -> stop persisting the settling report",
        "agents/coordinator.py",
        '            "settling": settling_report,\n            "agent_claimed_resolved": agent_claimed_resolved,',
        '            "agent_claimed_resolved": agent_claimed_resolved,',
    ),
    (
        "agents -> unbounded completions again",
        "agents/coordinator.py",
        '                    "max_tokens": LLM_MAX_COMPLETION_TOKENS,\n                    "tools"',
        '                    "tools"',
    ),
    (
        "stage4 -> label every run with the module-default marker",
        "scripts/run_stage4_golden_incident.py",
        '"protocol_marker": _marker_for_selected_model(),',
        '"protocol_marker": G4_PLATFORM_HARDENING_MARKER,',
    ),
    (
        "stage4 -> stop recording the settling report in evidence",
        "scripts/run_stage4_golden_incident.py",
        '        evidence["phases"]["settling"] = incident_result.get("settling", {})\n\n',
        "",
    ),
    (
        "GRPO -> allow uncoupled training without acknowledgement",
        "training/grpo.py",
        'if os.getenv(self.COUPLING_ACK_ENV, "").strip().lower() not in ("1", "true", "yes"):',
        "if False:",
    ),
]


def run(tmp: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", "tests/", "-q",
         "--ignore=tests/test_bootstrap_lifecycle.py", "-p", "no:cacheprovider"],
        cwd=tmp, capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout[-4000:]


def control() -> int:
    """Run the suite in an unmutated copy. Anything failing here is env-fragile."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "repo"
        shutil.copytree(
            REPO, tmp,
            ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc", "scratch"),
            symlinks=True,
        )
        code, tail = run(tmp)
    if code == 0:
        print("control: clean — no environment-fragile tests")
        return 0
    failing = [ln.split(" ")[1] for ln in tail.splitlines() if ln.startswith(("FAILED", "ERROR"))]
    print("control: these tests fail without mutation and must be discounted:")
    for name in failing:
        print(f"  {name}")
    print("\nAdd their node names to ENV_FRAGILE, or repair them.")
    return 1


def main() -> int:
    if "--control" in sys.argv:
        return control()
    survivors: list[str] = []
    skipped: list[str] = []
    for i, (name, rel, original, mutated) in enumerate(MUTATIONS, 1):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "repo"
            shutil.copytree(
                REPO, tmp,
                ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc", "scratch"),
                symlinks=True,
            )
            target = tmp / rel
            text = target.read_text(encoding="utf-8")
            if original not in text:
                skipped.append(name)
                print(f"[{i:>2}] SKIPPED (anchor not found in {rel})  {name}")
                print("            This mutation never ran — the fix is UNTESTED, not protected.")
                continue
            target.write_text(text.replace(original, mutated), encoding="utf-8")

            code, tail = run(tmp)
            if code == 0:
                survivors.append(name)
                print(f"[{i:>2}] SURVIVED  {name}  <-- FIX IS UNPROTECTED")
                continue
            failing = [ln.split(" ")[1] for ln in tail.splitlines() if ln.startswith("FAILED")]
            errored = [ln.split(" ")[1] for ln in tail.splitlines() if ln.startswith("ERROR")]
            # A test that fails in an unmutated copy is an environment result, not
            # a catch. Discount it so attribution stays honest.
            failing = [f for f in failing if f.split("::")[-1] not in ENV_FRAGILE]
            if failing:
                shown = "; ".join(f.split("::")[-1] for f in failing[:3])
                print(f"[{i:>2}] caught by {len(failing)} test(s)  {name}\n            {shown}")
            elif errored:
                print(f"[{i:>2}] caught by import-time guard  {name}\n            {errored[0]}")
            else:
                survivors.append(name)
                print(f"[{i:>2}] SURVIVED  {name}  <-- only env-fragile tests failed")

    caught = len(MUTATIONS) - len(survivors) - len(skipped)
    print(f"\n{caught}/{len(MUTATIONS)} mutations caught, "
          f"{len(survivors)} survived, {len(skipped)} skipped")
    if survivors:
        print("\nUNPROTECTED FIXES (a test should have failed and none did):")
        for name in survivors:
            print(f"  - {name}")
    if skipped:
        print("\nUNTESTED MUTATIONS (anchor drifted; update scripts/mutation_sweep.py):")
        for name in skipped:
            print(f"  - {name}")
    return 1 if (survivors or skipped) else 0


if __name__ == "__main__":
    sys.exit(main())
