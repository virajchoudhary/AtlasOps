"""Offline ranking metrics. All functions are deterministic and side-effect free."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from agents.rs.schemas import InteractionRow, SchemaError


def hit_rate_at_k(relevance: Mapping[str, float], ranking: Sequence[str], k: int) -> int:
    _validate_ranking(relevance, ranking, k)
    return int(any(float(relevance.get(action_id, 0.0)) > 0.0 for action_id in ranking[:k]))


def mrr_at_k(relevance: Mapping[str, float], ranking: Sequence[str], k: int) -> float:
    _validate_ranking(relevance, ranking, k)
    for position, action_id in enumerate(ranking[:k], start=1):
        if float(relevance.get(action_id, 0.0)) > 0.0:
            return 1.0 / position
    return 0.0


def ndcg_at_k(relevance: Mapping[str, float], ranking: Sequence[str], k: int) -> float:
    _validate_ranking(relevance, ranking, k)
    dcg = sum(
        float(relevance.get(action_id, 0.0)) / math.log2(position + 1)
        for position, action_id in enumerate(ranking[:k], start=1)
    )
    ideal_relevances = sorted((float(value) for value in relevance.values()), reverse=True)[:k]
    idcg = sum(value / math.log2(position + 1) for position, value in enumerate(ideal_relevances, start=1))
    return dcg / idcg if idcg > 0.0 else 0.0


def coverage_at_k(rankings: Sequence[Sequence[str]], catalogue_size: int, k: int) -> float:
    if catalogue_size <= 0:
        raise SchemaError("catalogue_size must be positive")
    if k <= 0:
        raise SchemaError("k must be positive")
    recommended: set[str] = set()
    for ranking in rankings:
        if len(ranking) < min(k, 1):
            raise SchemaError("ranking is empty")
        recommended.update(ranking[:k])
    return len(recommended) / catalogue_size


def evaluate_rankings(
    rows: list[InteractionRow],
    rankings_by_incident: Mapping[str, Sequence[str]],
    k: int,
) -> dict[str, float]:
    """Evaluate held-out rows using one ranking per incident key."""
    relevance_by_incident: dict[str, dict[str, float]] = {}
    for row in rows:
        if row.split in {"train", "calibration"}:
            raise SchemaError(f"evaluation received fit split {row.split}")
        relevance_by_incident.setdefault(row.incident_key, {})[row.action_id] = float(row.relevance)
    missing = sorted(set(relevance_by_incident).difference(rankings_by_incident))
    if missing:
        raise SchemaError(f"missing evaluation ranking for incidents: {missing[:3]}")
    hits = mrrs = ndcgs = 0.0
    for incident_key, relevance in relevance_by_incident.items():
        ranking = rankings_by_incident[incident_key]
        hits += hit_rate_at_k(relevance, ranking, k)
        mrrs += mrr_at_k(relevance, ranking, k)
        ndcgs += ndcg_at_k(relevance, ranking, k)
    count = len(relevance_by_incident)
    return {
        f"hit_rate@{k}": hits / count,
        f"mrr@{k}": mrrs / count,
        f"ndcg@{k}": ndcgs / count,
        "evaluated_incidents": float(count),
    }


def mutating_action_exposure_at_k(packets: list[dict[str, Any]], k: int) -> float:
    """Fraction of returned Top-K slots occupied by side-effecting candidates."""
    if k <= 0:
        raise SchemaError("k must be positive")
    slots = mutating = 0
    for packet in packets:
        candidates = packet.get("candidates", [])
        if not isinstance(candidates, list):
            raise SchemaError("packet candidates must be a list")
        selected_candidates = candidates[:k]
        slots += len(selected_candidates)
        mutating += sum(bool(item.get("mutating")) for item in selected_candidates)
    return mutating / slots if slots else 0.0


def unsafe_recommendation_rate(packets: list[dict[str, Any]]) -> float:
    """Count packet-contract violations that could expose an unsafe execution path.

    Pre-approval mutation ranking is intentional and is not unsafe by itself.
    A candidate is unsafe only when its execution metadata contradicts the
    approval/budget gates or names a tool outside the packet's declared pool.
    """
    candidates = []
    for packet in packets:
        if not isinstance(packet.get("candidates"), list):
            raise SchemaError("packet candidates must be a list")
        available_tools = packet.get("available_tools")
        for item in packet["candidates"]:
            if not isinstance(item, dict):
                raise SchemaError("packet candidates must be dictionaries")
            candidates.append((item, available_tools))
    if not candidates:
        return 0.0
    unsafe = 0
    for item, available_tools in candidates:
        mutating = bool(item.get("mutating"))
        blockers = set(item.get("downstream_execution_blockers", ()))
        eligible = bool(item.get("execution_eligible_after_downstream_gates"))
        if (
            bool(item.get("approval_required_before_execution")) != mutating
            or (mutating and "approval_pending" not in blockers)
            or ("mutation_budget_exhausted" in blockers and eligible)
            or (isinstance(available_tools, (set, frozenset, list)) and item.get("tool_name") not in available_tools)
        ):
            unsafe += 1
    return unsafe / len(candidates)


def _validate_ranking(
    relevance: Mapping[str, float],
    ranking: Sequence[str],
    k: int,
) -> None:
    if k <= 0:
        raise SchemaError("k must be positive")
    if len(set(ranking)) != len(ranking):
        raise SchemaError("ranking contains duplicate actions")
    for value in relevance.values():
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise SchemaError("relevance values must be finite in [0, 1]")
