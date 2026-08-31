"""Tests for Stage 11: Train Hybrid Recommender (Gate G11).

Validates:
1. Collaborative Graph Recommender service/alert transition matrix learning.
2. Tri-signal Hybrid Recommender (Content + Collab + Prior) scoring and ranking.
3. Structured RunbookRecommendation payload (actions, suggested tools, explanations).
4. Checkpoint persistence and loading fidelity.
5. Statistical superiority of Hybrid Recommender over Stage 10 baselines on held-out test split.
6. End-to-end training and evidence generation pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from recommender.dataset import IncidentInteraction, load_interactions
from recommender.evaluate import evaluate_recommender
from recommender.hybrid import (
    CollaborativeGraphRecommender,
    HybridRecommender,
    RunbookRecommendation,
)
from recommender.train_hybrid import train_and_evaluate_hybrid


class TestStage11HybridRecommender:
    def test_collaborative_graph_scoring(self):
        interactions = load_interactions()
        train_data = [i for i in interactions if i.split == "train"]

        collab = CollaborativeGraphRecommender()
        collab.fit(train_data)

        recs = collab.recommend(train_data[0], k=3)
        assert len(recs) == 3
        # Top score should be positive
        assert recs[0][1] >= 0.0

    def test_hybrid_recommender_fit_and_recommend(self):
        interactions = load_interactions()
        train_data = [i for i in interactions if i.split == "train"]

        hybrid = HybridRecommender(alpha=0.50, beta=0.35, gamma=0.15)
        hybrid.fit(train_data)

        query = {
            "alertname": "KubeMemoryOvercommit",
            "affected_services": ["frontend"],
            "tier": "single_fault",
            "symptoms_text": "OOMKilled pod memory limit exceeded",
        }
        recs = hybrid.recommend(query, k=3)
        assert len(recs) == 3
        top_rb, score = recs[0]
        assert top_rb == "RB-POD-OOM"
        assert 0.0 <= score <= 1.0

    def test_structured_runbook_recommendation_payload(self):
        interactions = load_interactions()
        train_data = [i for i in interactions if i.split == "train"]

        hybrid = HybridRecommender()
        hybrid.fit(train_data)

        recs = hybrid.recommend_runbooks(train_data[0], k=3)
        assert len(recs) == 3
        for rec in recs:
            assert isinstance(rec, RunbookRecommendation)
            assert rec.runbook_id.startswith("RB-")
            assert len(rec.title) > 0
            assert len(rec.category) > 0
            assert len(rec.suggested_tools) > 0
            assert len(rec.actions) > 0
            assert "Recommended" in rec.explanation
            # Verify dictionary serialization
            d = rec.to_dict()
            assert d["runbook_id"] == rec.runbook_id

    def test_hybrid_checkpoint_save_and_load(self, tmp_path):
        interactions = load_interactions()
        train_data = [i for i in interactions if i.split == "train"]

        model = HybridRecommender(alpha=0.6, beta=0.3, gamma=0.1)
        model.fit(train_data)

        ckpt_file = tmp_path / "hybrid_recommender.json"
        model.save_checkpoint(ckpt_file)
        assert ckpt_file.exists()

        loaded_model = HybridRecommender.load_checkpoint(ckpt_file)
        assert loaded_model.alpha == 0.6
        assert loaded_model.beta == 0.3
        assert loaded_model.gamma == 0.1
        assert loaded_model.fitted is True

        # Verify predictions match identically
        orig_recs = model.recommend(train_data[0], k=3)
        loaded_recs = loaded_model.recommend(train_data[0], k=3)
        assert orig_recs == loaded_recs

    def test_hybrid_outperforms_all_baselines_on_test_split(self):
        interactions = load_interactions()
        train_data = [i for i in interactions if i.split == "train"]
        test_data = [i for i in interactions if i.split == "test"]

        from recommender.baselines import (
            BM25ContentRecommender,
            PopularityRecommender,
            RandomRecommender,
        )

        rand = RandomRecommender(seed=42).fit(train_data)
        pop = PopularityRecommender().fit(train_data)
        bm25 = BM25ContentRecommender().fit(train_data)
        hybrid = HybridRecommender(alpha=0.50, beta=0.35, gamma=0.15).fit(train_data)

        m_rand = evaluate_recommender(rand, test_data)
        m_pop = evaluate_recommender(pop, test_data)
        m_bm25 = evaluate_recommender(bm25, test_data)
        m_hybrid = evaluate_recommender(hybrid, test_data)

        # Scientific Delta Verification:
        # Hybrid Hit@3 achieves 100% on held-out test split
        assert m_hybrid["hit@3"] == 1.0
        # Hybrid Hit@3 >= BM25 Hit@3 >= Popularity Hit@3 > Random Hit@3
        assert m_hybrid["hit@3"] >= m_bm25["hit@3"] >= m_pop["hit@3"] > m_rand["hit@3"]
        # Hybrid MRR@3 > BM25 MRR@3 > Popularity MRR@3 > Random MRR@3
        assert m_hybrid["mrr@3"] > m_bm25["mrr@3"] > m_pop["mrr@3"] > m_rand["mrr@3"]
        # Hybrid NDCG@3 > BM25 NDCG@3 > Popularity NDCG@3 > Random NDCG@3
        assert m_hybrid["ndcg@3"] > m_bm25["ndcg@3"] > m_pop["ndcg@3"] > m_rand["ndcg@3"]

    def test_training_pipeline_generates_model_and_evidence(self, tmp_path):
        model_path = tmp_path / "model.json"
        evidence_path = tmp_path / "evidence.json"

        model, evidence = train_and_evaluate_hybrid(
            output_model_path=model_path,
            output_evidence_path=evidence_path,
        )

        assert model_path.exists()
        assert evidence_path.exists()
        assert evidence["model_name"] == "HybridRecommender"
        assert evidence["evaluation"]["test"]["hit@3"] == 1.0
        assert "baseline_comparison_test" in evidence
