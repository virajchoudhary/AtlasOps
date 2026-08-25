"""Generate SFT trajectories for the active G5 train split on a real GKE cluster.

For each scenario:
  1. Apply Chaos Mesh manifest
  2. Wait for Alertmanager to fire
  3. Run the 4-agent chain (optionally with a strong teacher LLM for high-quality demos)
  4. Score with the judge
  5. Record (state, action, output, reward) tuples
  6. Reset cluster

Output: data/sft_corpus.jsonl plus an immutable generation manifest
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import asyncio
import json
import logging
import platform
import subprocess
import sys
import time
from pathlib import Path

from agents.coordinator import handle_incident
from agents.judge import judge_trajectory
from bench.scenario_contract import (
    allowed_scenario_ids,
    assert_consumer_may_use_scenario,
    sha256_file,
    sha256_object,
    write_json_atomically,
)
from config.runtime import evaluate_reward_contract


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("trajectories")


def list_scenarios(manifests_root: Path) -> list[Path]:
    return sorted(p for p in manifests_root.rglob("*.yaml") if p.is_file())


def select_training_manifests(manifests_root: Path) -> tuple[list[Path], list[str]]:
    """Restrict SFT trajectory generation to the active frozen train population."""
    allowed = set(allowed_scenario_ids("sft"))
    selected: list[Path] = []
    blocked: list[str] = []
    for path in list_scenarios(manifests_root):
        scenario_id = f"{path.parent.name}/{path.stem}"
        if scenario_id in allowed:
            selected.append(path)
        else:
            blocked.append(scenario_id)
    if not selected:
        raise RuntimeError(
            "SFT_GENERATION_BLOCKED: active frozen split contains no train scenarios"
        )
    return sorted(selected), sorted(blocked)


def git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or None


def write_generation_manifest(
    output: Path,
    *,
    args: argparse.Namespace,
    scenario_ids: list[str],
    blocked_scenario_ids: list[str],
    written: int,
) -> None:
    config = {
        "arguments": vars(args),
        "blocked_scenario_ids": blocked_scenario_ids,
        "git_commit": git_commit(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "scenario_ids": scenario_ids,
    }
    manifest = {
        "config_sha256": sha256_object(config),
        "config_provenance": config,
        "corpus_path": str(output),
        "corpus_sha256": sha256_file(output),
        "example_count": written,
        "schema_version": "atlasops.g5.sft-generation-manifest/v1",
    }
    write_json_atomically(output.with_suffix(output.suffix + ".manifest.json"), manifest)


def apply_chaos(manifest: Path) -> bool:
    log.info("applying %s", manifest)
    r = subprocess.run(["kubectl", "apply", "-f", str(manifest)], capture_output=True, text=True)
    if r.returncode != 0:
        log.error("kubectl apply failed: %s", r.stderr)
        return False
    return True


def wait_for_alert(timeout_s: int = 300) -> dict | None:
    """Poll Alertmanager until at least one alert is firing."""
    from agents.tools.alertmanager import alertmanager_list_alerts
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = alertmanager_list_alerts(active_only=True)
        if result.get("success") and result.get("count", 0) > 0:
            return {
                "commonLabels": {"alertname": result["alerts"][0]["alertname"]},
                "alerts": result["alerts"],
            }
        time.sleep(15)
    log.warning("no alert fired within %ds", timeout_s)
    return None


def reset_cluster() -> None:
    log.info("resetting cluster (deleting all chaos)")
    subprocess.run(
        ["kubectl", "delete", "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
         "--all", "-A"],
        capture_output=True,
    )
    time.sleep(60)  # wait for pods to recover


SFT_EXAMPLE_FORMAT = "openai-tool-messages-v1"


def _tool_call_arguments(entry: dict) -> str:
    """Return the wire-format arguments JSON string for one recorded tool step.

    Dict args are serialized deterministically. Pre-stringified args must parse
    to a JSON object — malformed strings are rejected instead of entering the
    corpus, because the runtime parser treats them as invalid tool calls and
    training on them would teach malformed emission.
    """
    args = entry.get("args")
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"trajectory entry turn={entry.get('turn')!r} tool={entry['tool']!r}: "
                f"stringified arguments are not valid JSON: {args[:120]!r}"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"trajectory entry turn={entry.get('turn')!r} tool={entry['tool']!r}: "
                f"arguments must be a JSON object, got {type(parsed).__name__}"
            )
        return args
    return json.dumps(args or {}, sort_keys=True)


def _tool_call_messages(entry: dict) -> list[dict]:
    """Serialize one recorded trajectory step into provider-native message shapes.

    Tool steps become an assistant ``tool_calls`` message (arguments encoded as a
    JSON string, matching the OpenAI-compatible wire format the runtime parses at
    inference) followed by the recorded ``role: "tool"`` observation. Text-only
    steps (conclusions/prose) become plain assistant content. Nothing is
    fabricated: only actions and observations actually recorded in the
    trajectory are emitted.
    """
    if "tool" not in entry:
        return [{"role": "assistant", "content": entry.get("content", "")}]
    arguments = _tool_call_arguments(entry)
    assistant_msg = {
        "role": "assistant",
        "content": entry.get("content") or "",
        "tool_calls": [{
            "id": f"call_{entry.get('turn', 0)}_{entry['tool']}",
            "type": "function",
            "function": {"name": entry["tool"], "arguments": arguments},
        }],
    }
    tool_msg = {
        "role": "tool",
        "tool_call_id": assistant_msg["tool_calls"][0]["id"],
        "tool_name": entry["tool"],
        "content": json.dumps(entry.get("output"), sort_keys=True),
    }
    return [assistant_msg, tool_msg]


def trajectory_to_sft_examples(
    scenario_id: str, tier: str, incident: dict, judge_score: dict, reward_contract: dict
) -> list[dict]:
    """Convert one full incident chain into SFT examples in native tool-message format.

    One example per agent role preserves the full multi-turn loop — every
    executable tool call in its native ``tool_calls`` structure plus the exact
    environment observation returned for it — so training teaches the SAME
    structured tool-call representation the runtime adapter parses, instead of
    flattening calls to prose (which trained prose-instead-of-native-call
    behaviour). Provenance (scenario, tier, judge, reward contract) is retained;
    no outcome labels are invented.
    """
    examples = []
    for role in ("triage", "diagnosis", "remediation", "comms"):
        agent_data = incident.get(role, {})
        trajectory = [e for e in agent_data.get("trajectory", []) if isinstance(e, dict)]
        if not trajectory:
            continue
        messages: list[dict] = [
            {"role": "system", "content": f"You are the {role} agent."},
        ]
        user_context = agent_data.get("input")
        if user_context is not None:
            messages.append({"role": "user", "content": json.dumps(user_context, sort_keys=True)})
        for entry in trajectory:
            # Observability-only forensic records are not teacher turns: they
            # must never become (empty) assistant training content.
            if entry.get("kind") == "model_turn":
                continue
            messages.extend(_tool_call_messages(entry))
        final = agent_data.get("final")
        if isinstance(final, dict):
            # The recorded structured conclusion is a legitimate assistant turn.
            messages.append({"role": "assistant", "content": json.dumps(final, sort_keys=True)})
        # Derived from the built structure, never stored metadata, so the count
        # cannot silently disagree with the serialized messages.
        n_tool_turns = sum(1 for m in messages if m.get("tool_calls"))
        examples.append({
            "format": SFT_EXAMPLE_FORMAT,
            "scenario_id": scenario_id,
            "role": role,
            "messages": messages,
            "n_tool_turns": n_tool_turns,
            "tier": tier,
            "reward": reward_contract.get("total", judge_score.get("overall", 0.0)),
            "reward_contract": reward_contract,
            "judge": judge_score,
        })
    return examples


async def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifests", default="bench/chaos_manifests")
    parser.add_argument("--output", default="data/sft_corpus.jsonl")
    parser.add_argument("--max-scenarios", type=int, default=0, help="0 = all")
    parser.add_argument("--repeats", type=int, default=10, help="repeats per scenario for variance")
    parser.add_argument("--overwrite", action="store_true", help="explicitly replace prior corpus")
    args = parser.parse_args()

    scenarios, blocked = select_training_manifests(Path(args.manifests))
    scenario_ids = [f"{path.parent.name}/{path.stem}" for path in scenarios]
    if args.max_scenarios:
        scenarios = scenarios[: args.max_scenarios]
        scenario_ids = scenario_ids[: args.max_scenarios]
    log.info("found %d scenarios; %d repeats each = %d trajectories",
             len(scenarios), args.repeats, len(scenarios) * args.repeats)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and not args.overwrite:
        raise RuntimeError(f"SFT corpus already exists; pass --overwrite explicitly: {output}")
    written = 0
    with output.open("w", encoding="utf-8") as f:
        for manifest in scenarios:
            tier = manifest.parent.name
            scenario_id = f"{tier}/{manifest.stem}"
            for repeat in range(args.repeats):
                if not apply_chaos(manifest):
                    continue
                assert_consumer_may_use_scenario("sft", scenario_id)
                alert = wait_for_alert()
                if not alert:
                    reset_cluster()
                    continue
                t0 = time.time()
                incident = await handle_incident(alert, scenario_id=scenario_id)
                judge_score = await judge_trajectory(incident)
                remediation = incident.get("remediation", {}).get("final", {})
                episode = {
                    "scenario_id": scenario_id,
                    "tier": tier,
                    "resolved": remediation.get("outcome") == "resolved",
                    "outcome": remediation.get("outcome", "unknown"),
                    "time_to_resolve_s": float(
                        remediation.get("time_to_resolve_seconds", round(time.time() - t0))
                    ),
                    "total_turns": sum(
                        len(incident.get(r, {}).get("trajectory", []))
                        for r in ("triage", "diagnosis", "remediation", "comms")
                    ),
                    "postmortem_path": incident.get("comms", {}).get("final", {}).get("postmortem_path"),
                    "judge": judge_score,
                }
                # Use the same shared reward contract used by benchmark + GRPO.
                reward_contract = evaluate_reward_contract(episode)
                examples = trajectory_to_sft_examples(
                    scenario_id, tier, incident, judge_score, reward_contract
                )
                for ex in examples:
                    f.write(json.dumps(ex) + "\n")
                    written += 1
                f.flush()
                log.info(
                    "[%s repeat=%d] judge=%.2f contract=%.2f written=%d",
                    scenario_id,
                    repeat,
                    judge_score.get("overall", 0),
                    reward_contract.get("total", 0),
                    written,
                )
                reset_cluster()

    write_generation_manifest(
        output,
        args=args,
        scenario_ids=scenario_ids,
        blocked_scenario_ids=blocked,
        written=written,
    )
    log.info("done. %d examples in %s", written, output)


if __name__ == "__main__":
    asyncio.run(run())
