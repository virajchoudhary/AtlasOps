"""Runbook recommender demo — the Recommender Systems workstream, end to end.

> [!IMPORTANT]
> Runs on the **synthetic fixture** in `agents/rs/synthetic.py`. These are not
> G10/G11 results and no real incident history exists yet. The numbers printed
> here characterise the implementation on toy data; they are not a benchmark.

`agents.rs` ranks remediation runbooks for a diagnosed incident. It sits between
Diagnosis and Remediation in the target pipeline and is deliberately inert: it
imports no tool module, opens no socket, and cannot execute anything. A packet is
a *proposal* — every side-effecting candidate carries the gates that still have to
clear before anything runs.

Usage:
    .venv/bin/python scripts/demo_runbook_recommender.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.rs import RUNBOOK_CATALOGUE, RecommendationPacketBuilder  # noqa: E402
from agents.rs.metrics import (  # noqa: E402
    coverage_at_k,
    mutating_action_exposure_at_k,
    unsafe_recommendation_rate,
)
from agents.rs.recommender import (  # noqa: E402
    CollaborativeSVDBaseline,
    ContentBasedBaseline,
    HybridRecommender,
    PopularitySuccessBaseline,
)
from agents.rs.features import ContextFeatures  # noqa: E402
from agents.rs.synthetic import build_synthetic_fixture  # noqa: E402

RULE = "─" * 78


def _hybrid() -> HybridRecommender:
    return HybridRecommender(
        content_model=ContentBasedBaseline(),
        collaborative_model=CollaborativeSVDBaseline(latent_dimensions=2, iterations=20),
        success_model=PopularitySuccessBaseline(),
    )


def _print_packet(title: str, packet: dict, limit: int = 5) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")
    print(f"next stage: {packet.get('next_stage')}")
    for i, candidate in enumerate(packet.get("candidates", [])[:limit], 1):
        flag = "MUTATING" if candidate.get("mutating") else "read-only"
        print(f"\n{i}. {candidate.get('action_id')}  [{flag}]  score={candidate.get('score', 0):.4f}")
        print(f"   tool     : {candidate.get('tool_name')}")
        blockers = candidate.get("downstream_execution_blockers") or []
        print(f"   blockers : {', '.join(blockers) if blockers else 'none'}")
        print(f"   eligible after downstream gates: "
              f"{candidate.get('execution_eligible_after_downstream_gates')}")


def main() -> int:
    print("AtlasOps — Runbook Recommender (G10/G11 preparation)")
    print("SYNTHETIC FIXTURE ONLY — not a benchmark, not a gate result.\n")

    fixture = build_synthetic_fixture()
    rows = [row for row in fixture["rows"] if getattr(row, "eligible_for_fit", False)]
    print(f"catalogue      : {len(RUNBOOK_CATALOGUE)} runbooks")
    print(f"training rows  : {len(rows)} (split-safe; test rows excluded from fit)")

    builder = RecommendationPacketBuilder(RUNBOOK_CATALOGUE, _hybrid(), k=5)
    builder.recommender.fit(rows)

    base = fixture["contexts"]["cpu"]

    def variant(**overrides) -> ContextFeatures:
        return ContextFeatures(**{**base.__dict__, **overrides})

    # 1. CPU saturation with a fault-injection experiment observed in context.
    observed = builder.recommend_packet(variant(active_chaos_experiment=True))
    _print_packet("CPU saturation, fault-injection experiment observed", observed)

    # 2. Same incident, but the mutation budget is spent — blockers stack.
    exhausted = builder.recommend_packet(
        variant(active_chaos_experiment=True, mutation_budget_remaining=0)
    )
    _print_packet("Same incident, cluster-mutation budget exhausted", exhausted)

    # 3. The safety property worth demonstrating: a caller cannot talk the
    #    recommender into marking a mutation executable.
    claims_approval = builder.recommend_packet(
        variant(active_chaos_experiment=True, approval_granted=True)
    )
    still_blocked = [
        c for c in claims_approval["candidates"]
        if c["mutating"] and not c["execution_eligible_after_downstream_gates"]
    ]
    print(f"\n{RULE}\nFail-closed authority boundary\n{RULE}")
    print("Context asserts approval_granted=True. Mutating candidates still blocked:")
    for candidate in still_blocked:
        print(f"  - {candidate['action_id']:<28} blockers: "
              f"{', '.join(candidate['downstream_execution_blockers'])}")
    print(
        "\napproval_granted is recorded in the context hash so identical situations\n"
        "hash identically, but it never clears a blocker. Approval is a token-based\n"
        "decision owned by the coordinator's gate; the recommender cannot grant it,\n"
        "so it never claims to."
    )

    # 4. Safety metrics over the packets.
    packets = [observed, exhausted, claims_approval]
    rankings = [[c["action_id"] for c in p.get("candidates", [])] for p in packets]
    print(f"\n{RULE}\nSafety and coverage (synthetic)\n{RULE}")
    print(f"coverage@5                  : {coverage_at_k(rankings, len(RUNBOOK_CATALOGUE), 5):.3f}")
    print(f"mutating action exposure@5  : {mutating_action_exposure_at_k(packets, 5):.3f}")
    print(f"unsafe recommendation rate  : {unsafe_recommendation_rate(packets):.3f}")
    print(
        "\nNon-zero mutating exposure is expected and safe: the packet ranks mutations\n"
        "so a human can see and choose among them, while every one stays\n"
        "execution-ineligible until approval and the runtime tool policy clear it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
