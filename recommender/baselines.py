"""AtlasOps Recommender Systems Baselines (Gate G10).

Implements reference baseline recommenders:
1. RandomRecommender: Random selection baseline.
2. PopularityRecommender: Global occurrence frequency baseline.
3. BM25ContentRecommender: Lexical BM25/TF-IDF content-based similarity matching.
"""

from __future__ import annotations

import math
import random
import re
from abc import ABC, abstractmethod
from typing import Any, Sequence

from recommender.dataset import IncidentInteraction
from recommender.runbook_catalog import RUNBOOK_CATALOG, Runbook, get_all_runbooks


def _tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric words, splitting CamelCase and delimiters."""
    split_camel = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    split_num = re.sub(r"([a-zA-Z])([0-9])", r"\1 \2", split_camel)
    raw_tokens = re.findall(r"\b[a-zA-Z0-9_-]+\b", split_num.lower())
    tokens = []
    for t in raw_tokens:
        sub = re.split(r"[-_]+", t)
        tokens.extend([s for s in sub if s])
    return tokens


class BaseRecommender(ABC):
    """Abstract base class for AtlasOps runbook recommenders."""

    @abstractmethod
    def fit(self, interactions: list[IncidentInteraction]) -> BaseRecommender:
        """Fit recommender parameters on training interaction history."""
        pass

    @abstractmethod
    def recommend(
        self,
        query: dict[str, Any] | IncidentInteraction,
        k: int = 3,
    ) -> list[tuple[str, float]]:
        """Return top-K recommendations as a list of (runbook_id, score) tuples."""
        pass


class RandomRecommender(BaseRecommender):
    """Uniform random runbook recommender."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)
        self.runbook_ids: list[str] = []

    def fit(self, interactions: list[IncidentInteraction]) -> RandomRecommender:
        self.runbook_ids = list(RUNBOOK_CATALOG.keys())
        return self

    def recommend(
        self,
        query: dict[str, Any] | IncidentInteraction,
        k: int = 3,
    ) -> list[tuple[str, float]]:
        if not self.runbook_ids:
            self.runbook_ids = list(RUNBOOK_CATALOG.keys())
        sampled = self.rng.sample(self.runbook_ids, min(k, len(self.runbook_ids)))
        return [(rb_id, 1.0 / (idx + 1)) for idx, rb_id in enumerate(sampled)]


class PopularityRecommender(BaseRecommender):
    """Global frequency / popularity-based runbook recommender."""

    def __init__(self):
        self.runbook_scores: list[tuple[str, float]] = []

    def fit(self, interactions: list[IncidentInteraction]) -> PopularityRecommender:
        counts: dict[str, int] = {rb_id: 0 for rb_id in RUNBOOK_CATALOG.keys()}
        for item in interactions:
            rb_id = item.relevant_runbook_id
            counts[rb_id] = counts.get(rb_id, 0) + 1

        total = sum(counts.values()) or 1
        sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        self.runbook_scores = [(rb_id, count / total) for rb_id, count in sorted_items]
        return self

    def recommend(
        self,
        query: dict[str, Any] | IncidentInteraction,
        k: int = 3,
    ) -> list[tuple[str, float]]:
        return self.runbook_scores[:k]


class BM25ContentRecommender(BaseRecommender):
    """BM25 / TF-IDF Lexical Content-Based Recommender.

    Scores runbooks based on token overlaps between incident query text
    (alertname, services, symptoms) and runbook corpus descriptions, patterns, and tags.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_tokens: dict[str, list[str]] = {}
        self.doc_lens: dict[str, int] = {}
        self.avg_doc_len: float = 1.0
        self.idf: dict[str, float] = {}
        self.fitted = False

    def _build_runbook_doc(self, rb: Runbook) -> str:
        """Compose indexed document string for a runbook."""
        parts = [
            rb.runbook_id,
            rb.title,
            rb.category,
            " ".join(rb.target_symptoms),
            " ".join(rb.failure_patterns),
            " ".join(rb.actions),
            " ".join(rb.suggested_tools),
            rb.description,
        ]
        return " ".join(parts)

    def fit(self, interactions: list[IncidentInteraction] | None = None) -> BM25ContentRecommender:
        # Index all runbooks in catalog
        self.doc_tokens = {}
        self.doc_lens = {}

        for rb_id, rb in RUNBOOK_CATALOG.items():
            text = self._build_runbook_doc(rb)
            tokens = _tokenize(text)
            self.doc_tokens[rb_id] = tokens
            self.doc_lens[rb_id] = len(tokens)

        n_docs = len(self.doc_tokens)
        self.avg_doc_len = sum(self.doc_lens.values()) / max(n_docs, 1)

        # Compute IDF for all tokens in corpus
        df: dict[str, int] = {}
        for tokens in self.doc_tokens.values():
            unique_terms = set(tokens)
            for t in unique_terms:
                df[t] = df.get(t, 0) + 1

        self.idf = {}
        for term, doc_freq in df.items():
            # Standard Robertson-Spärck Jones BM25 IDF
            self.idf[term] = math.log((n_docs - doc_freq + 0.5) / (doc_freq + 0.5) + 1.0)

        self.fitted = True
        return self

    def _query_to_text(self, query: dict[str, Any] | IncidentInteraction) -> str:
        if isinstance(query, IncidentInteraction):
            return f"{query.alertname} {' '.join(query.affected_services)} {query.symptoms_text} {query.tier}"
        elif isinstance(query, dict):
            parts = []
            if "alertname" in query:
                parts.append(str(query["alertname"]))
            if "affected_services" in query:
                svcs = query["affected_services"]
                parts.append(" ".join(svcs) if isinstance(svcs, list) else str(svcs))
            if "symptoms_text" in query:
                parts.append(str(query["symptoms_text"]))
            if "expected_root_cause" in query:
                parts.append(str(query["expected_root_cause"]))
            return " ".join(parts)
        return str(query)

    def score_runbook(self, query_tokens: list[str], rb_id: str) -> float:
        """Compute BM25 score of a single runbook for query tokens."""
        tokens = self.doc_tokens.get(rb_id, [])
        doc_len = self.doc_lens.get(rb_id, 1)

        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1

        score = 0.0
        for q in query_tokens:
            if q in tf and q in self.idf:
                freq = tf[q]
                numerator = freq * (self.k1 + 1.0)
                denominator = freq + self.k1 * (1.0 - self.b + self.b * (doc_len / self.avg_doc_len))
                score += self.idf[q] * (numerator / denominator)
        return score

    def recommend(
        self,
        query: dict[str, Any] | IncidentInteraction,
        k: int = 3,
    ) -> list[tuple[str, float]]:
        if not self.fitted:
            self.fit()

        query_text = self._query_to_text(query)
        q_tokens = _tokenize(query_text)

        scored: list[tuple[str, float]] = []
        for rb_id in RUNBOOK_CATALOG.keys():
            s = self.score_runbook(q_tokens, rb_id)
            scored.append((rb_id, round(s, 4)))

        # Sort descending by BM25 score
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
