"""AtlasOps Recommender Benchmark Evaluation Harness (Gate G10).

Evaluates baseline runbook recommenders on historical interaction splits,
computing Hit@K, MRR@K, NDCG@K, Precision@K, and Recall@K, and persisting benchmark evidence.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from recommender.baselines import (
    BaseRecommender,
    BM25ContentRecommender,
    PopularityRecommender,
    RandomRecommender,
)
from recommender.dataset import IncidentInteraction, load_interactions
from recommender.metrics import evaluate_recommendations

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rs_eval")

EVIDENCE_DIR = Path("artifacts/evidence/stage10")


def evaluate_recommender(
    recommender: BaseRecommender,
    interactions: list[IncidentInteraction],
    k_list: tuple[int, ...] = (1, 3, 5),
) -> dict[str, float]:
    """Evaluate a single recommender over a set of interactions."""
    if not interactions:
        return {}

    metric_sums: dict[str, float] = {}

    for item in interactions:
        top_items = recommender.recommend(item, k=max(k_list))
        recommended_ids = [rb_id for rb_id, _ in top_items]
        row_metrics = evaluate_recommendations(recommended_ids, item.relevant_runbook_id, k_list=k_list)

        for k, v in row_metrics.items():
            metric_sums[k] = metric_sums.get(k, 0.0) + v

    n = len(interactions)
    return {k: round(v / n, 4) for k, v in metric_sums.items()}


def run_full_baseline_benchmark(
    interactions: list[IncidentInteraction] | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Execute complete benchmarking across Random, Popularity, and BM25 recommenders."""
    corpus = interactions or load_interactions()
    train_data = [i for i in corpus if i.split == "train"]
    val_data = [i for i in corpus if i.split == "val"]
    test_data = [i for i in corpus if i.split == "test"]

    log.info("Benchmarking RS Baselines on %d interactions (Train: %d, Val: %d, Test: %d)",
             len(corpus), len(train_data), len(val_data), len(test_data))

    recommenders: dict[str, BaseRecommender] = {
        "RandomRecommender": RandomRecommender(seed=42),
        "PopularityRecommender": PopularityRecommender(),
        "BM25ContentRecommender": BM25ContentRecommender(),
    }

    # Fit all models on train partition
    for name, model in recommenders.items():
        model.fit(train_data)

    results: dict[str, Any] = {
        "dataset_split_counts": {
            "train": len(train_data),
            "val": len(val_data),
            "test": len(test_data),
            "total": len(corpus),
        },
        "models": {},
    }

    for name, model in recommenders.items():
        val_metrics = evaluate_recommender(model, val_data)
        test_metrics = evaluate_recommender(model, test_data)
        train_metrics = evaluate_recommender(model, train_data)

        results["models"][name] = {
            "val": val_metrics,
            "test": test_metrics,
            "train": train_metrics,
        }
        log.info("[%s] Val Hit@1: %.3f, Hit@3: %.3f, MRR@3: %.3f, NDCG@3: %.3f",
                 name, val_metrics.get("hit@1", 0.0), val_metrics.get("hit@3", 0.0),
                 val_metrics.get("mrr@3", 0.0), val_metrics.get("ndcg@3", 0.0))

    out_file = output_path or (EVIDENCE_DIR / "rs_baseline_eval.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    log.info("Saved RS baseline benchmark results to %s", out_file)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps RS Baseline Benchmark")
    parser.add_argument("--output", default="artifacts/evidence/stage10/rs_baseline_eval.json", help="Output path")
    args = parser.parse_args()
    run_full_baseline_benchmark(output_path=Path(args.output))


if __name__ == "__main__":
    main()
