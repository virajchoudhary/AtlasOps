"""Tests for Stage 11: Train Hybrid Recommender (Gate G11).

Validates:
1. Collaborative Graph Recommender service/alert transition matrix learning.
2. Tri-signal Hybrid Recommender (Content + Collab + Prior) scoring and ranking.
3. Structured RunbookRecommendation payload (actions, suggested tools, explanations).
4. Checkpoint persistence and loading fidelity.
5. Valid metrics and direct model comparisons on the held-out synthetic test partition.
6. End-to-end training and evidence generation pipeline.
"""

from __future__ import annotations

import json
import math

from recommender.dataset import build_incident_interactions, load_interactions
from recommender.evaluate import evaluate_recommender
from recommender.hybrid import (
    CollaborativeGraphRecommender,
    HybridRecommender,
    RunbookRecommendation,
)
from recommender.train_hybrid import train_and_evaluate_hybrid


def _load_synthetic_interactions(tmp_path):
    path = tmp_path / "rs_incident_interactions.jsonl"
    build_incident_interactions(output_path=path)
    return load_interactions(path=path)


class TestStage11HybridRecommender:
    def test_collaborative_graph_scoring(self, tmp_path):
        interactions = _load_synthetic_interactions(tmp_path)
        train_data = [i for i in interactions if i.split == "train"]

        collab = CollaborativeGraphRecommender()
        collab.fit(train_data)

        recs = collab.recommend(train_data[0], k=3)
        assert len(recs) == 3
        # Top score should be positive
        assert recs[0][1] >= 0.0

    def test_hybrid_recommender_fit_and_recommend(self, tmp_path):
        interactions = _load_synthetic_interactions(tmp_path)
        train_data = [i for i in interactions if i.split == "train"]

        hybrid = HybridRecommender(alpha=0.50, beta=0.35, gamma=0.15)
        hybrid.fit(train_data)

        query = {
            "alertname": "KubeMemoryOvercommit",
            "affected_services": ["frontend"],
            "symptoms_text": "OOMKilled pod memory limit exceeded",
        }
        recs = hybrid.recommend(query, k=3)
        assert len(recs) == 3
        top_rb, score = recs[0]
        assert top_rb == "RB-POD-OOM"
        assert 0.0 <= score <= 1.0

    def test_structured_runbook_recommendation_payload(self, tmp_path):
        interactions = _load_synthetic_interactions(tmp_path)
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
        interactions = _load_synthetic_interactions(tmp_path)
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

    def test_test_split_metrics_are_valid_and_comparison_is_reported(self, tmp_path):
        interactions = _load_synthetic_interactions(tmp_path)
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

        metrics_by_model = {
            "RandomRecommender": m_rand,
            "PopularityRecommender": m_pop,
            "BM25ContentRecommender": m_bm25,
            "HybridRecommender": m_hybrid,
        }
        metric_names = {"hit@1", "hit@3", "hit@5", "mrr@3", "ndcg@3"}
        for metrics in metrics_by_model.values():
            assert metric_names <= metrics.keys()
            assert all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in metrics.values())

        comparison = {
            metric: {
                "winner": max(metrics_by_model, key=lambda name: metrics_by_model[name][metric]),
                "scores": {name: metrics[metric] for name, metrics in metrics_by_model.items()},
            }
            for metric in ("hit@3", "mrr@3", "ndcg@3")
        }
        comparison_path = tmp_path / "scenario-derived-test-comparison.json"
        comparison_path.write_text(
            json.dumps(
                {
                    "data_origin": "scenario_derived_synthetic_benchmark",
                    "historical_user_feedback": False,
                    "test_count": len(test_data),
                    "metrics": comparison,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        saved_comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        assert saved_comparison["data_origin"] == "scenario_derived_synthetic_benchmark"
        assert saved_comparison["historical_user_feedback"] is False
        assert saved_comparison["test_count"] == len(test_data)
        assert all(result["winner"] in metrics_by_model for result in comparison.values())

    def test_training_pipeline_generates_model_and_evidence(self, tmp_path):
        interactions = _load_synthetic_interactions(tmp_path)
        model_path = tmp_path / "model.json"
        evidence_path = tmp_path / "evidence.json"

        model, evidence = train_and_evaluate_hybrid(
            interactions=interactions,
            output_model_path=model_path,
            output_evidence_path=evidence_path,
        )

        assert model.fitted is True
        assert model_path.exists()
        assert evidence_path.exists()
        assert evidence["model_name"] == "HybridRecommender"
        assert "baseline_comparison_test" in evidence
        assert evidence["dataset_split_counts"] == {
            "train": 12,
            "val": 5,
            "test": 4,
            "total": 21,
        }
        assert evidence["baseline_comparison_test"]["HybridRecommender"] == evidence["evaluation"]["test"]
        for metrics in evidence["baseline_comparison_test"].values():
            assert all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in metrics.values())
