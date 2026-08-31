"""AtlasOps Hybrid Runbook Recommender (Gate G11).

Blends:
1. Lexical Content Matching (BM25 token similarity)
2. Collaborative Graph Affinities (Service-to-runbook co-occurrence and alert transition graph)
3. Tier-Weighted Prior Distribution

Provides top-K scored runbook recommendations with action sequences and suggested tools
to guide the Remediation Agent during incident resolution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from recommender.baselines import BaseRecommender, BM25ContentRecommender
from recommender.dataset import IncidentInteraction
from recommender.runbook_catalog import RUNBOOK_CATALOG, Runbook, get_runbook

log = logging.getLogger("hybrid_recommender")


@dataclass
class RunbookRecommendation:
    runbook_id: str
    title: str
    category: str
    score: float
    suggested_tools: list[str]
    actions: list[str]
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CollaborativeGraphRecommender(BaseRecommender):
    """Learns service co-occurrence and alert transition affinities to runbooks."""

    def __init__(self):
        self.service_runbook_matrix: dict[str, dict[str, int]] = {}
        self.alert_runbook_matrix: dict[str, dict[str, int]] = {}
        self.tier_runbook_matrix: dict[str, dict[str, int]] = {}

    def fit(self, interactions: list[IncidentInteraction]) -> CollaborativeGraphRecommender:
        self.service_runbook_matrix = {}
        self.alert_runbook_matrix = {}
        self.tier_runbook_matrix = {}

        for item in interactions:
            rb_id = item.relevant_runbook_id
            alert = item.alertname
            tier = item.tier

            # Alert mapping
            if alert not in self.alert_runbook_matrix:
                self.alert_runbook_matrix[alert] = {}
            self.alert_runbook_matrix[alert][rb_id] = self.alert_runbook_matrix[alert].get(rb_id, 0) + 1

            # Tier mapping
            if tier not in self.tier_runbook_matrix:
                self.tier_runbook_matrix[tier] = {}
            self.tier_runbook_matrix[tier][rb_id] = self.tier_runbook_matrix[tier].get(rb_id, 0) + 1

            # Service mappings
            for svc in item.affected_services:
                if svc not in self.service_runbook_matrix:
                    self.service_runbook_matrix[svc] = {}
                self.service_runbook_matrix[svc][rb_id] = self.service_runbook_matrix[svc].get(rb_id, 0) + 1

        return self

    def score_item(self, alertname: str, services: list[str], tier: str, rb_id: str) -> float:
        """Compute collaborative transition graph affinity score in [0, 1]."""
        score = 0.0
        components = 0

        # Alert transition affinity
        if alertname in self.alert_runbook_matrix:
            counts = self.alert_runbook_matrix[alertname]
            total = sum(counts.values())
            if total > 0:
                score += counts.get(rb_id, 0) / total
                components += 1

        # Service co-occurrence affinity
        for svc in services:
            if svc in self.service_runbook_matrix:
                counts = self.service_runbook_matrix[svc]
                total = sum(counts.values())
                if total > 0:
                    score += counts.get(rb_id, 0) / total
                    components += 1

        # Tier pattern affinity
        if tier in self.tier_runbook_matrix:
            counts = self.tier_runbook_matrix[tier]
            total = sum(counts.values())
            if total > 0:
                score += 0.5 * (counts.get(rb_id, 0) / total)
                components += 0.5

        return (score / components) if components > 0 else 0.0

    def recommend(
        self,
        query: dict[str, Any] | IncidentInteraction,
        k: int = 3,
    ) -> list[tuple[str, float]]:
        if isinstance(query, IncidentInteraction):
            alert = query.alertname
            services = query.affected_services
            tier = query.tier
        else:
            alert = query.get("alertname", "")
            svcs = query.get("affected_services", [])
            services = svcs if isinstance(svcs, list) else [str(svcs)]
            tier = query.get("tier", "single_fault")

        scored = []
        for rb_id in RUNBOOK_CATALOG.keys():
            s = self.score_item(alert, services, tier, rb_id)
            scored.append((rb_id, round(s, 4)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]


class HybridRecommender(BaseRecommender):
    """Tri-signal Hybrid Runbook Recommender.

    Combines:
    - S_content: Lexical BM25 similarity (weight alpha)
    - S_collab: Collaborative graph affinity (weight beta)
    - S_prior: Global occurrence prior (weight gamma)
    """

    def __init__(self, alpha: float = 0.50, beta: float = 0.35, gamma: float = 0.15):
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma

        self.bm25 = BM25ContentRecommender()
        self.collab = CollaborativeGraphRecommender()
        self.priors: dict[str, float] = {}
        self.fitted = False

    def fit(self, interactions: list[IncidentInteraction]) -> HybridRecommender:
        self.bm25.fit(interactions)
        self.collab.fit(interactions)

        # Compute prior distribution
        counts = {rb_id: 0 for rb_id in RUNBOOK_CATALOG.keys()}
        for item in interactions:
            counts[item.relevant_runbook_id] = counts.get(item.relevant_runbook_id, 0) + 1
        total = sum(counts.values()) or 1
        self.priors = {rb_id: count / total for rb_id, count in counts.items()}

        self.fitted = True
        return self

    def recommend(
        self,
        query: dict[str, Any] | IncidentInteraction,
        k: int = 3,
    ) -> list[tuple[str, float]]:
        if not self.fitted:
            raise RuntimeError("HybridRecommender must be fitted before generating recommendations.")

        # 1. Content score
        bm25_recs = dict(self.bm25.recommend(query, k=len(RUNBOOK_CATALOG)))
        # Normalize BM25 scores to [0, 1]
        max_bm25 = max(bm25_recs.values()) if bm25_recs and max(bm25_recs.values()) > 0 else 1.0

        # 2. Collaborative score
        collab_recs = dict(self.collab.recommend(query, k=len(RUNBOOK_CATALOG)))

        # 3. Hybrid fusion
        final_scores: list[tuple[str, float]] = []
        for rb_id in RUNBOOK_CATALOG.keys():
            s_content = (bm25_recs.get(rb_id, 0.0) / max_bm25) if max_bm25 > 0 else 0.0
            s_collab = collab_recs.get(rb_id, 0.0)
            s_prior = self.priors.get(rb_id, 0.0)

            score = (
                self.alpha * s_content +
                self.beta * s_collab +
                self.gamma * s_prior
            )
            final_scores.append((rb_id, round(score, 4)))

        final_scores.sort(key=lambda x: x[1], reverse=True)
        return final_scores[:k]

    def recommend_runbooks(
        self,
        query: dict[str, Any] | IncidentInteraction,
        k: int = 3,
    ) -> list[RunbookRecommendation]:
        """Generate structured RunbookRecommendation objects for the multi-agent system."""
        top_items = self.recommend(query, k=k)
        recommendations = []

        for rb_id, score in top_items:
            rb = get_runbook(rb_id)
            if not rb:
                continue

            explanation = (
                f"Recommended '{rb.title}' with confidence {score:.2f} based on "
                f"symptom overlap with {rb.category} patterns and historical recovery success."
            )

            rec = RunbookRecommendation(
                runbook_id=rb.runbook_id,
                title=rb.title,
                category=rb.category,
                score=score,
                suggested_tools=list(rb.suggested_tools),
                actions=list(rb.actions),
                explanation=explanation,
            )
            recommendations.append(rec)

        return recommendations

    def save_checkpoint(self, path: Path | str) -> None:
        """Save hybrid recommender weights and state to JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "model_type": "AtlasOpsHybridRunbookRecommender",
            "hyperparameters": {
                "alpha": self.alpha,
                "beta": self.beta,
                "gamma": self.gamma,
            },
            "priors": self.priors,
            "collab_service_matrix": self.collab.service_runbook_matrix,
            "collab_alert_matrix": self.collab.alert_runbook_matrix,
            "collab_tier_matrix": self.collab.tier_runbook_matrix,
        }
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.info("Saved Hybrid Recommender checkpoint to %s", p)

    @classmethod
    def load_checkpoint(cls, path: Path | str) -> HybridRecommender:
        """Load trained hybrid recommender from JSON checkpoint."""
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        hp = data.get("hyperparameters", {})
        model = cls(alpha=hp.get("alpha", 0.50), beta=hp.get("beta", 0.35), gamma=hp.get("gamma", 0.15))
        model.priors = data.get("priors", {})
        model.collab.service_runbook_matrix = data.get("collab_service_matrix", {})
        model.collab.alert_runbook_matrix = data.get("collab_alert_matrix", {})
        model.collab.tier_runbook_matrix = data.get("collab_tier_matrix", {})
        model.bm25.fit()
        model.collab.fitted = True
        model.fitted = True
        return model
