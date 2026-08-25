"""Generic, safety-gated remediation recommendation infrastructure.

This package deliberately has no imports from :mod:`agents.tools`: it may name
and validate supported actions, but execution remains the responsibility of the
approval gate, remediation policy, and coordinator.
"""

from agents.rs.catalogue import RUNBOOK_CATALOGUE
from agents.rs.integration import RecommendationPacketBuilder
from agents.rs.metrics import coverage_at_k, hit_rate_at_k, mrr_at_k, ndcg_at_k
from agents.rs.recommender import HybridRecommender

__all__ = [
    "RUNBOOK_CATALOGUE",
    "RecommendationPacketBuilder",
    "HybridRecommender",
    "coverage_at_k",
    "hit_rate_at_k",
    "mrr_at_k",
    "ndcg_at_k",
]
