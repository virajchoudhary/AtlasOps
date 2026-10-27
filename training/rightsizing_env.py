"""Right-sizing capacity environment for a small-model, CPU-scale GRPO experiment.

This generalises the controlled-G9 ``CapacityEnvironment``: every episode has its
own replica count, demand and per-replica capacity, and the verifier adds one
required check, ``right_sized``, so only the smallest replica count that covers
demand resolves the incident. Actions pass the same parser, schema validation,
AtlasOps tool policy and verifier-based reward (``score_direct_action_step``)
as the controlled-G9 environment. Invalid or blocked actions score -1.

The environment is synthetic. No Kubernetes, Chaos Mesh or live service is
involved, and results from it are not G9 evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from dataclasses import asdict, dataclass
from typing import Any

from agents.coordinator import _TOOL_PARAMETER_SCHEMAS, _check_tool_policy
from training.grpo_environment import parse_policy_action
from training.grpo_reward import score_direct_action_step
from training.sft_candidate import _validate_schema

EXPERIMENT = "rightsizing-grpo-cpu-v1"
SEVERITY = "P2"
MAX_REPLICAS = 20  # the AtlasOps tool policy bound for kubectl_scale

SYSTEM_PROMPT = (
    "You are the Remediation agent in AtlasOps. paymentservice in namespace default "
    "is saturated: its ready replicas cannot serve the current demand. Each ready "
    "replica serves capacity_units_per_replica demand units. Scale paymentservice to "
    "the smallest number of replicas whose total capacity covers demand_units; more "
    "replicas than that waste money and count as a failed fix.\n"
    "Reply with only one JSON object and no other text, in exactly this form:\n"
    '{"tool": "kubectl_scale", "arguments": {"deployment": "paymentservice", '
    '"namespace": "default", "replicas": <integer>}, "agent_claimed_resolved": false}'
)


# The reasoned format allows one short arithmetic line before the action. Only the
# action on the final line is scored; the reasoning itself earns nothing.
SYSTEM_PROMPT_REASONED = SYSTEM_PROMPT.split("Reply with only")[0] + (
    "Answer in two lines. Line 1 shows the arithmetic; line 2 is the action as one JSON "
    "object, in exactly this form:\n"
    '{"tool": "kubectl_scale", "arguments": {"deployment": "paymentservice", '
    '"namespace": "default", "replicas": <integer>}, "agent_claimed_resolved": false}'
)
MAX_REASONED_LINES = 4
# "reasoned_strict" scores the two lines the reasoned prompt asks for and nothing else: an
# arithmetic line in the demonstrated form, then the action. Under "reasoned" a bare action
# also counts, and a GRPO policy learned to drop the arithmetic.
ACTION_FORMATS = ("direct", "reasoned", "reasoned_strict")
ARITHMETIC_LINE = re.compile(r"^\s*\d+\s*/\s*\d+\s*=\s*\d+(\.\d+)?\s*,\s*rounded up\s*=\s*\d+\s*$")
# "repository" is score_direct_action_step, as in controlled G9. "graded" is an
# experiment-only training reward; evaluations always use the repository reward.
REWARD_MODES = ("repository", "graded")


@dataclass(frozen=True)
class CapacityState:
    replicas: int
    demand: int
    capacity_per_replica: int

    @property
    def needed(self) -> int:
        return math.ceil(self.demand / self.capacity_per_replica)

    def observation(self) -> dict[str, Any]:
        return {
            "incident_id": f"{EXPERIMENT}-r{self.replicas}-d{self.demand}-c{self.capacity_per_replica}",
            "alert": {
                "labels": {"alertname": "PodCPUSaturation", "namespace": "default"},
                "status": "firing",
            },
            "triage": {"severity": SEVERITY},
            "observations": {
                "deployment": "paymentservice",
                "namespace": "default",
                "ready_replicas": self.replicas,
                "demand_units": self.demand,
                "capacity_units_per_replica": self.capacity_per_replica,
            },
        }


def all_states(max_needed: int = 12, max_capacity: int = 5) -> list[CapacityState]:
    """Every saturated state whose fix fits within the tool policy's replica bound."""
    states = []
    for capacity in range(1, max_capacity + 1):
        for needed in range(2, min(max_needed, MAX_REPLICAS) + 1):
            for demand in range((needed - 1) * capacity + 1, needed * capacity + 1):
                for replicas in range(1, needed):
                    states.append(CapacityState(replicas, demand, capacity))
    return states


# v2 difficulty bands, measured -- not assumed -- from the 256 states the 18-demonstration
# 7B warm start was scored on in the v1 GPU experiment (dev, confirmation and held-out rows,
# all committed under artifacts/evidence/rl-gpu-7b-v1/):
#
#     inexact division, capacity 4   28/57 = 0.49      <- the only band near a coin flip
#     inexact division, capacity 3   31/37 = 0.84
#     inexact division, capacity 5   52/52 = 1.00
#     exact division, any capacity   0.82 .. 1.00
#
# The v1 task was too easy because capacities 1, 2 and 5 divide in the head and exact
# divisions need no rounding at all. Difficulty lives in inexact division by a capacity
# that is not 1, 2, 5 or 10. v2 keeps only that band, and extends capacity past 5, which
# v1 never generated and where the rate is therefore predicted rather than measured.
EASY_DIVISORS = frozenset({1, 2, 5, 10})


def is_hard(state: CapacityState) -> bool:
    """A state in the band where the v1 warm start was near chance."""
    return (state.demand % state.capacity_per_replica != 0
            and state.capacity_per_replica not in EASY_DIVISORS)


def all_states_v2(max_needed: int = 20, capacities: tuple[int, ...] = (3, 4, 6, 7, 8, 9),
                  hard_only: bool = True) -> list[CapacityState]:
    """The v2 state space: wider capacities, deeper quotients, hard band only by default.

    ``max_needed`` is bounded by the tool policy's replica cap, so the fix always stays
    inside what ``kubectl_scale`` will accept.
    """
    states = []
    for capacity in capacities:
        for needed in range(2, min(max_needed, MAX_REPLICAS) + 1):
            for demand in range((needed - 1) * capacity + 1, needed * capacity + 1):
                for replicas in range(1, needed):
                    state = CapacityState(replicas, demand, capacity)
                    if not hard_only or is_hard(state):
                        states.append(state)
    return states


def split_states(seed: int = 2026, held_out: int = 64) -> tuple[list[CapacityState], list[CapacityState]]:
    """Disjoint Train and held-out states, fixed by ``seed``."""
    states = all_states()
    random.Random(seed).shuffle(states)
    return states[held_out:], states[:held_out]


def states_sha256(states: list[CapacityState]) -> str:
    payload = json.dumps([asdict(state) for state in states], sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_state_sets(path) -> dict[str, list[CapacityState]]:
    """Named state sets from a JSON file written by scripts/make_rightsizing_eval_sets.py."""
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    sets = {name: [CapacityState(**state) for state in states] for name, states in data["sets"].items()}
    for name, states in sets.items():
        if states_sha256(states) != data["sha256"][name]:
            raise ValueError(f"State set {name} does not match its recorded SHA-256")
    return sets


def prompt_messages(state: CapacityState, action_format: str = "direct") -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT if action_format == "direct" else SYSTEM_PROMPT_REASONED},
        {"role": "user", "content": json.dumps(state.observation(), sort_keys=True)},
    ]


def expert_completion(state: CapacityState, action_format: str = "direct") -> str:
    """The correct action for ``state``; used only as a Train-split SFT demonstration."""
    action = json.dumps({"tool": "kubectl_scale",
                         "arguments": {"deployment": "paymentservice", "namespace": "default",
                                       "replicas": state.needed},
                         "agent_claimed_resolved": False})
    if action_format == "direct":
        return action
    ratio = state.demand / state.capacity_per_replica
    return f"{state.demand} / {state.capacity_per_replica} = {ratio:.2f}, rounded up = {state.needed}\n{action}"


def final_action(completion: str, action_format: str = "direct") -> str:
    """The text that is scored: the whole completion, or the reasoned formats' last line."""
    if action_format == "direct":
        return completion
    lines = [line for line in completion.strip().splitlines() if line.strip()]
    if action_format == "reasoned_strict":
        return lines[1] if len(lines) == 2 and ARITHMETIC_LINE.match(lines[0]) else ""
    return lines[-1] if 0 < len(lines) <= MAX_REASONED_LINES else ""


def verify(state: CapacityState, replicas: int) -> dict[str, Any]:
    checks = [
        {"name": "capacity_covers_demand", "required": True,
         "passed": replicas * state.capacity_per_replica >= state.demand,
         "observed": {"capacity": replicas * state.capacity_per_replica, "demand": state.demand}},
        {"name": "ready_capacity_positive", "required": True, "passed": replicas > 0,
         "observed": replicas},
        {"name": "right_sized", "required": True, "passed": replicas <= state.needed,
         "observed": {"replicas": replicas, "needed": state.needed}},
    ]
    resolved = all(check["passed"] for check in checks)
    return {"verification_status": "passed" if resolved else "failed",
            "env_resolved": resolved, "checks": checks}


def graded_reward(state: CapacityState, replicas: int) -> float:
    """Near-miss credit that ranks covering demand above falling short; not the G9 reward.

    Resolved scores 1.0. An over-provisioned count earns 0.5 x needed / replicas (one extra
    replica earns 0.33-0.46); a count that leaves demand uncovered earns 0.25 x replicas /
    needed (one short earns 0.13-0.23), so one too many always beats one too few.
    """
    if verify(state, replicas)["env_resolved"]:
        return 1.0
    if replicas * state.capacity_per_replica >= state.demand:
        return round(0.5 * state.needed / replicas, 6)
    return round(0.25 * max(replicas, 0) / state.needed, 6)


def score(state: CapacityState, completion: str, reward_mode: str = "repository") -> dict[str, Any]:
    """Validate one policy completion like the controlled-G9 environment, then verify it."""
    if reward_mode not in REWARD_MODES:
        raise ValueError(f"Unknown reward mode {reward_mode}")
    observation = state.observation()
    category = None
    action = None
    try:
        action = parse_policy_action(completion)
        if set(json.loads(completion)) != {"tool", "arguments", "agent_claimed_resolved"}:
            raise ValueError("Extra policy fields are not admitted")
    except (TypeError, ValueError):
        category = "invalid_action"
    if category is None:
        tool, arguments = action["tool"], action["arguments"]
        try:
            _validate_schema(arguments, _TOOL_PARAMETER_SCHEMAS.get(tool, {}))
            if _check_tool_policy("remediation", tool, arguments, observation):
                category = "policy_block"
        except (ValueError, TypeError):
            category = "invalid_action"
        if category is None and tool not in {"kubectl_scale", "kubectl_get"}:
            category = "tool_unavailable"
        if category is None and (
            arguments.get("namespace", "default") != "default"
            or (tool == "kubectl_scale" and arguments["deployment"] != "paymentservice")
        ):
            category = "target_mismatch"
    if category is not None:
        return {"reward": -1.0, "repository_reward": -1.0, "admissible": False, "resolved": False,
                "category": category, "replicas": None, "needed": state.needed}
    replicas = action["arguments"]["replicas"] if action["tool"] == "kubectl_scale" else state.replicas
    verification = verify(state, replicas)
    decomposition = score_direct_action_step(verification, agent_claimed_resolved=False)
    reward = float(decomposition["total"]) if reward_mode == "repository" else graded_reward(state, replicas)
    return {"reward": reward, "repository_reward": float(decomposition["total"]), "admissible": True,
            "resolved": verification["env_resolved"], "category": None,
            "replicas": replicas, "needed": state.needed, "decomposition": decomposition}
