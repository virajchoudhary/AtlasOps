"""Generic, safety-gated remediation recommendation infrastructure.

This package deliberately has no imports from :mod:`agents.tools`: it may name
and validate supported actions, but execution remains the responsibility of the
approval gate, remediation policy, and coordinator.
"""

from agents.rs.catalogue import RUNBOOK_CATALOGUE
from agents.rs.integration import RecommendationPacketBuilder
from agents.rs.metrics import (
    coverage_at_k,
    hit_rate_at_k,
    mrr_at_k,
    mutating_action_exposure_at_k,
    ndcg_at_k,
    unsafe_recommendation_rate,
)
from agents.rs.ontology import derive_parameter_requirements, validate_parameter_contract
from agents.rs.recommender import HybridRecommender
from agents.rs.persistence import (
    build_corpus_manifest,
    deserialize_hybrid_model,
    serialize_hybrid_model,
)
from agents.rs.splits import G5SplitBinding, bind_rows_to_g5
from agents.rs.synthetic import build_synthetic_fixture

__all__ = [
    "RUNBOOK_CATALOGUE",
    "RecommendationPacketBuilder",
    "HybridRecommender",
    "derive_parameter_requirements",
    "validate_parameter_contract",
    "serialize_hybrid_model",
    "deserialize_hybrid_model",
    "build_corpus_manifest",
    "G5SplitBinding",
    "bind_rows_to_g5",
    "build_synthetic_fixture",
    "mutating_action_exposure_at_k",
    "unsafe_recommendation_rate",
    "coverage_at_k",
    "hit_rate_at_k",
    "mrr_at_k",
    "ndcg_at_k",
]
