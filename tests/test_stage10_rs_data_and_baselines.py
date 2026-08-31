"""Tests for Stage 10: Build RS Data and Baselines (Gate G10).

Validates:
1. SRE Runbook Catalog schema and non-emptiness.
2. Incident-Runbook interaction dataset generation and split partition integrity.
3. Mathematical correctness of IR/RecSys metrics (Hit@K, MRR@K, NDCG@K, Precision@K, Recall@K).
4. Reference recommenders (Random, Popularity, BM25 Content) behavior and ranking.
5. Baseline benchmark evaluation harness and evidence file generation.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import pytest

from recommender.baselines import (
    BM25ContentRecommender,
    PopularityRecommender,
    RandomRecommender,
)
from recommender.dataset import (
    IncidentInteraction,
    build_incident_interactions,
    load_interactions,
)
from recommender.evaluate import evaluate_recommender, run_full_baseline_benchmark
from recommender.metrics import (
    hit_at_k,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from recommender.runbook_catalog import RUNBOOK_CATALOG, get_all_runbooks, get_runbook


class TestStage10RSDataAndBaselines:
    def test_runbook_catalog_integrity(self):
        runbooks = get_all_runbooks()
        assert len(runbooks) == 12
        assert len(RUNBOOK_CATALOG) == 12

        for rb in runbooks:
            assert rb.runbook_id.startswith("RB-")
            assert len(rb.title) > 0
            assert len(rb.category) > 0
            assert len(rb.target_symptoms) > 0
            assert len(rb.failure_patterns) > 0
            assert len(rb.actions) > 0
            assert len(rb.suggested_tools) > 0
            assert len(rb.description) > 0

        assert get_runbook("RB-POD-OOM") is not None
        assert get_runbook("RB-NON-EXISTENT") is None

    def test_build_and_load_interactions(self, tmp_path):
        out_file = tmp_path / "interactions.jsonl"
        path, manifest = build_incident_interactions(output_path=out_file)

        assert path.exists()
        assert manifest["total_interactions"] == 28
        assert manifest["split_distribution"]["train"] == 16
        assert manifest["split_distribution"]["val"] == 6
        assert manifest["split_distribution"]["test"] == 6

        loaded = load_interactions(path=path)
        assert len(loaded) == 28
        assert all(isinstance(i, IncidentInteraction) for i in loaded)

    def test_metrics_mathematical_precision(self):
        # Case 1: Ground truth at rank 1
        recs = ["RB-POD-OOM", "RB-NET-LOSS", "RB-DISK-FILL"]
        gt = "RB-POD-OOM"

        assert hit_at_k(recs, gt, 1) == 1.0
        assert hit_at_k(recs, gt, 3) == 1.0
        assert mrr_at_k(recs, gt, 1) == 1.0
        assert mrr_at_k(recs, gt, 3) == 1.0
        assert ndcg_at_k(recs, gt, 1) == 1.0
        assert ndcg_at_k(recs, gt, 3) == 1.0
        assert precision_at_k(recs, gt, 1) == 1.0
        assert precision_at_k(recs, gt, 3) == 1.0 / 3.0
        assert recall_at_k(recs, gt, 3) == 1.0

        # Case 2: Ground truth at rank 2
        gt2 = "RB-NET-LOSS"
        assert hit_at_k(recs, gt2, 1) == 0.0
        assert hit_at_k(recs, gt2, 2) == 1.0
        assert mrr_at_k(recs, gt2, 1) == 0.0
        assert mrr_at_k(recs, gt2, 2) == 0.5
        assert ndcg_at_k(recs, gt2, 1) == 0.0
        # DCG = 1/log2(3), IDCG = 1/log2(2) = 1.0 => NDCG = 1/log2(3) = 0.6309
        assert abs(ndcg_at_k(recs, gt2, 2) - (1.0 / math.log2(3))) < 1e-4

        # Case 3: Ground truth absent in top-K
        gt3 = "RB-CPU-THROTTLE"
        assert hit_at_k(recs, gt3, 3) == 0.0
        assert mrr_at_k(recs, gt3, 3) == 0.0
        assert ndcg_at_k(recs, gt3, 3) == 0.0

    def test_random_recommender(self, tmp_path):
        interactions = load_interactions()
        rec = RandomRecommender(seed=123)
        rec.fit(interactions)

        results = rec.recommend(interactions[0], k=3)
        assert len(results) == 3
        ids = [r[0] for r in results]
        assert len(set(ids)) == 3  # Unique recommendations
        assert all(rb_id in RUNBOOK_CATALOG for rb_id in ids)

    def test_popularity_recommender(self):
        interactions = load_interactions()
        train_data = [i for i in interactions if i.split == "train"]

        rec = PopularityRecommender()
        rec.fit(train_data)

        results = rec.recommend(train_data[0], k=5)
        assert len(results) == 5
        scores = [s for _, s in results]
        # Verify scores are non-increasing (sorted descending)
        assert scores == sorted(scores, reverse=True)

    def test_bm25_content_recommender(self):
        interactions = load_interactions()
        train_data = [i for i in interactions if i.split == "train"]

        rec = BM25ContentRecommender()
        rec.fit(train_data)

        query = {
            "alertname": "KubeMemoryOvercommit",
            "affected_services": ["frontend"],
            "symptoms_text": "OOMKilled container memory limit exceeded 137",
        }
        recs = rec.recommend(query, k=3)
        assert len(recs) == 3
        top_rb = recs[0][0]
        assert top_rb == "RB-POD-OOM", f"Expected RB-POD-OOM for OOM symptoms, got {top_rb}"

    def test_full_baseline_benchmark_execution(self, tmp_path):
        out_json = tmp_path / "baseline_eval.json"
        results = run_full_baseline_benchmark(output_path=out_json)

        assert out_json.exists()
        assert "models" in results
        assert "RandomRecommender" in results["models"]
        assert "PopularityRecommender" in results["models"]
        assert "BM25ContentRecommender" in results["models"]

        # Verify BM25 test metrics are populated and valid
        bm25_test = results["models"]["BM25ContentRecommender"]["test"]
        assert bm25_test["hit@3"] > 0.50
        assert bm25_test["mrr@3"] > 0.50
        assert bm25_test["ndcg@3"] > 0.50
