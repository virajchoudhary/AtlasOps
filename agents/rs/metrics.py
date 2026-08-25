"""Offline ranking metrics. All functions are deterministic and side-effect free."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

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
