"""AtlasOps Hybrid Recommender Training & Evaluation Pipeline (Gate G11).

Trains the tri-signal Hybrid Runbook Recommender on the training split,
evaluates across Validation and Held-Out Test splits, saves model checkpoint,
and persists evaluation evidence.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from recommender.baselines import (
    BM25ContentRecommender,
    PopularityRecommender,
    RandomRecommender,
)
from recommender.dataset import IncidentInteraction, load_interactions
from recommender.evaluate import evaluate_recommender
from recommender.hybrid import HybridRecommender

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("train_hybrid")

MODELS_DIR = Path("artifacts/models")
EVIDENCE_DIR = Path("artifacts/evidence/stage11")


def train_and_evaluate_hybrid(
    interactions: list[IncidentInteraction] | None = None,
    output_model_path: Path | None = None,
    output_evidence_path: Path | None = None,
    alpha: float = 0.50,
    beta: float = 0.35,
    gamma: float = 0.15,
) -> tuple[HybridRecommender, dict[str, Any]]:
    """Train HybridRecommender on train partition and evaluate across all splits."""
    corpus = interactions or load_interactions()
    train_data = [i for i in corpus if i.split == "train"]
    val_data = [i for i in corpus if i.split == "val"]
    test_data = [i for i in corpus if i.split == "test"]

    log.info("Training Hybrid Recommender (alpha=%.2f, beta=%.2f, gamma=%.2f) on %d examples",
             alpha, beta, gamma, len(train_data))

    model = HybridRecommender(alpha=alpha, beta=beta, gamma=gamma)
    model.fit(train_data)

    # Save Checkpoint
    ckpt_path = output_model_path or (MODELS_DIR / "hybrid_recommender.json")
    model.save_checkpoint(ckpt_path)

    # Evaluate across partitions
    train_metrics = evaluate_recommender(model, train_data)
    val_metrics = evaluate_recommender(model, val_data)
    test_metrics = evaluate_recommender(model, test_data)

    # Compare with Baselines on Test Partition
    bm25 = BM25ContentRecommender().fit(train_data)
    pop = PopularityRecommender().fit(train_data)
    rand = RandomRecommender(seed=42).fit(train_data)

    test_bm25 = evaluate_recommender(bm25, test_data)
    test_pop = evaluate_recommender(pop, test_data)
    test_rand = evaluate_recommender(rand, test_data)

    evidence: dict[str, Any] = {
        "model_name": "HybridRecommender",
        "hyperparameters": {
            "alpha_content": alpha,
            "beta_collab": beta,
            "gamma_prior": gamma,
        },
        "dataset_split_counts": {
            "train": len(train_data),
            "val": len(val_data),
            "test": len(test_data),
            "total": len(corpus),
        },
        "evaluation": {
            "val": val_metrics,
            "test": test_metrics,
            "train": train_metrics,
        },
        "baseline_comparison_test": {
            "RandomRecommender": test_rand,
            "PopularityRecommender": test_pop,
            "BM25ContentRecommender": test_bm25,
            "HybridRecommender": test_metrics,
        },
        "checkpoint_path": str(ckpt_path.as_posix()),
    }

    ev_path = output_evidence_path or (EVIDENCE_DIR / "rs_hybrid_eval.json")
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    ev_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    log.info("Hybrid Evaluation — Test Hit@1: %.3f, Test Hit@3: %.3f, Test MRR@3: %.3f, Test NDCG@3: %.3f",
             test_metrics.get("hit@1", 0.0), test_metrics.get("hit@3", 0.0),
             test_metrics.get("mrr@3", 0.0), test_metrics.get("ndcg@3", 0.0))

    return model, evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps Hybrid Recommender Trainer")
    parser.add_argument("--model-out", default="artifacts/models/hybrid_recommender.json", help="Model checkpoint path")
    parser.add_argument("--evidence-out", default="artifacts/evidence/stage11/rs_hybrid_eval.json", help="Evidence output path")
    parser.add_argument("--alpha", type=float, default=0.50, help="Content weight")
    parser.add_argument("--beta", type=float, default=0.35, help="Collab weight")
    parser.add_argument("--gamma", type=float, default=0.15, help="Prior weight")
    args = parser.parse_args()

    train_and_evaluate_hybrid(
        output_model_path=Path(args.model_out),
        output_evidence_path=Path(args.evidence_out),
        alpha=args.alpha,
        beta=args.beta,
        gamma=args.gamma,
    )


if __name__ == "__main__":
    main()
