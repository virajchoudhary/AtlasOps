"""Standard Evaluation Metrics for Recommender Systems.

Implements information retrieval and ranking metrics:
- Hit@K (Hit Rate at K)
- MRR@K (Mean Reciprocal Rank at K)
- NDCG@K (Normalized Discounted Cumulative Gain at K)
- Precision@K & Recall@K
"""

from __future__ import annotations

import math
from typing import Sequence


def _normalize_ground_truth(target: str | Sequence[str]) -> set[str]:
    if isinstance(target, str):
        return {target}
    return set(target)


def hit_at_k(recommended: list[str], ground_truth: str | Sequence[str], k: int) -> float:
    """Compute Hit@K: returns 1.0 if any ground-truth item appears in the top-K recommendations."""
    gt_set = _normalize_ground_truth(ground_truth)
    top_k = recommended[:k]
    return 1.0 if any(item in gt_set for item in top_k) else 0.0


def mrr_at_k(recommended: list[str], ground_truth: str | Sequence[str], k: int) -> float:
    """Compute Mean Reciprocal Rank at K: 1 / rank of first relevant item in top-K (1-indexed)."""
    gt_set = _normalize_ground_truth(ground_truth)
    for rank_idx, item in enumerate(recommended[:k], start=1):
        if item in gt_set:
            return 1.0 / rank_idx
    return 0.0


def ndcg_at_k(recommended: list[str], ground_truth: str | Sequence[str], k: int) -> float:
    """Compute Normalized Discounted Cumulative Gain at K with binary relevance."""
    gt_set = _normalize_ground_truth(ground_truth)
    top_k = recommended[:k]

    dcg = 0.0
    for idx, item in enumerate(top_k, start=1):
        if item in gt_set:
            # Standard binary DCG formula: 1 / log2(rank + 1)
            dcg += 1.0 / math.log2(idx + 1)

    # Ideal DCG: all ground-truth items ranked at top (up to K)
    n_relevant = min(len(gt_set), k)
    if n_relevant == 0:
        return 0.0

    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, n_relevant + 1))
    return dcg / idcg if idcg > 0 else 0.0


def precision_at_k(recommended: list[str], ground_truth: str | Sequence[str], k: int) -> float:
    """Compute Precision@K: fraction of top-K recommendations that are relevant."""
    if k <= 0:
        return 0.0
    gt_set = _normalize_ground_truth(ground_truth)
    top_k = recommended[:k]
    hits = sum(1 for item in top_k if item in gt_set)
    return hits / k


def recall_at_k(recommended: list[str], ground_truth: str | Sequence[str], k: int) -> float:
    """Compute Recall@K: fraction of relevant items captured in top-K recommendations."""
    gt_set = _normalize_ground_truth(ground_truth)
    if not gt_set:
        return 0.0
    top_k = recommended[:k]
    hits = sum(1 for item in top_k if item in gt_set)
    return hits / len(gt_set)


def evaluate_recommendations(
    recommended: list[str],
    ground_truth: str | Sequence[str],
    k_list: tuple[int, ...] = (1, 3, 5),
) -> dict[str, float]:
    """Compute full metric suite across given K thresholds."""
    metrics: dict[str, float] = {}
    for k in k_list:
        metrics[f"hit@{k}"] = hit_at_k(recommended, ground_truth, k)
        metrics[f"mrr@{k}"] = mrr_at_k(recommended, ground_truth, k)
        metrics[f"ndcg@{k}"] = ndcg_at_k(recommended, ground_truth, k)
        metrics[f"precision@{k}"] = precision_at_k(recommended, ground_truth, k)
        metrics[f"recall@{k}"] = recall_at_k(recommended, ground_truth, k)
    return metrics
