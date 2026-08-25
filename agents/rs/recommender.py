"""Split-safe popularity, success, content, collaborative/SVD, and hybrid models."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from agents.rs.features import build_content_query, content_vector, cosine_similarity
from agents.rs.schemas import InteractionRow, Runbook, SchemaError


class BaseRecommender:
    """Minimal interface shared by all explicit baselines."""

    def fit(self, rows: list[InteractionRow]) -> None:
        raise NotImplementedError

    def score(self, query: dict[str, float], candidates: list[Runbook]) -> dict[str, float]:
        raise NotImplementedError

    def _validate_fit_rows(self, rows: list[InteractionRow]) -> None:
        illegal = [row.incident_key for row in rows if not row.eligible_for_fit]
        if illegal:
            raise SchemaError(
                f"fit received ineligible split rows: {sorted(set(illegal))[:3]}"
            )


@dataclass
class PopularitySuccessBaseline(BaseRecommender):
    alpha: float = 0.7
    prior_success: float = 0.5
    min_actions_for_rate: int = 3

    def __post_init__(self) -> None:
        if not 0.0 <= self.alpha <= 1.0:
            raise SchemaError("alpha must be in [0, 1]")
        if not 0.0 <= self.prior_success <= 1.0:
            raise SchemaError("prior_success must be in [0, 1]")
        if self.min_actions_for_rate < 1:
            raise SchemaError("min_actions_for_rate must be positive")
        self._stats: dict[str, tuple[int, float]] = {}

    def fit(self, rows: list[InteractionRow]) -> None:
        self._validate_fit_rows(rows)
        grouped: dict[str, list[InteractionRow]] = {}
        for row in rows:
            grouped.setdefault(row.action_id, []).append(row)
        self._stats = {
            action_id: (
                len(items),
                sum(float(item.relevance) for item in items) / len(items),
            )
            for action_id, items in grouped.items()
        }

    def score(self, query: dict[str, float], candidates: list[Runbook]) -> dict[str, float]:
        del query
        scores: dict[str, float] = {}
        for candidate in candidates:
            count, average_relevance = self._stats.get(candidate.action_id, (0, self.prior_success))
            shrunk_success = (
                (count * average_relevance + self.min_actions_for_rate * self.prior_success)
                / (count + self.min_actions_for_rate)
            )
            popularity = min(count / 10.0, 1.0)
            scores[candidate.action_id] = self.alpha * shrunk_success + (1.0 - self.alpha) * popularity
        return scores


@dataclass
class ContentBasedBaseline(BaseRecommender):
    def __post_init__(self) -> None:
        self._vectors: dict[str, dict[str, float]] = {}

    def fit(self, rows: list[InteractionRow]) -> None:
        # Content matching itself needs no labels; fit validates that this call
        # was explicitly made with a legal split rather than final-test rows.
        self._validate_fit_rows(rows)

    def score(self, query: dict[str, float], candidates: list[Runbook]) -> dict[str, float]:
        scores: dict[str, float] = {}
        for candidate in candidates:
            vector = content_vector(candidate)
            overlap = cosine_similarity(query, vector)
            exact_service_bonus = 1.0 if query.get("service") == 1.0 else 0.0
            scores[candidate.action_id] = max(overlap + exact_service_bonus, 0.0)
        return scores


class CollaborativeSVDBaseline(BaseRecommender):
    """Small dense truncated SVD over an incident-by-action relevance matrix.

    The implementation uses seeded power iterations and deterministic Gram-Schmidt
    orthogonalization. It is intentionally compact and dependency-free; G5 will
    replace synthetic matrices only after legal source splits are assigned.

    Cold-start limitation: an unseen incident is scored from the training
    population mean projection, so it receives no incident-specific
    personalization; an action absent from training scores zero.
    """

    def __init__(self, latent_dimensions: int = 4, seed: int = 1729, iterations: int = 30) -> None:
        if latent_dimensions < 1 or iterations < 1:
            raise SchemaError("latent dimensions and iterations must be positive")
        self.latent_dimensions = latent_dimensions
        self.seed = seed
        self.iterations = iterations
        self._incident_index: dict[str, int] = {}
        self._action_index: dict[str, int] = {}
        self._matrix: list[list[float]] = []
        self._incident_factors: list[list[float]] = []
        self._action_factors: list[list[float]] = []
        self._last_right_factors: list[list[float]] = []

    def fit(self, rows: list[InteractionRow]) -> None:
        self._validate_fit_rows(rows)
        incidents = sorted({row.incident_key for row in rows})
        actions = sorted({row.action_id for row in rows})
        if not incidents or not actions:
            self._incident_index, self._action_index = {}, {}
            self._matrix = []
            return
        self._incident_index = {key: idx for idx, key in enumerate(incidents)}
        self._action_index = {key: idx for idx, key in enumerate(actions)}
        matrix = [[self.prior() for _ in actions] for _ in incidents]
        for row in rows:
            matrix[self._incident_index[row.incident_key]][self._action_index[row.action_id]] = float(row.relevance)
        factors = self._truncated_svd(matrix, min(self.latent_dimensions, min(len(incidents), len(actions))))
        self._matrix = matrix
        self._incident_factors = factors[0]
        self._action_factors = self._last_right_factors

    def prior(self) -> float:
        return 0.0

    def score(self, query: dict[str, float], candidates: list[Runbook]) -> dict[str, float]:
        if not self._action_factors:
            return {candidate.action_id: 0.0 for candidate in candidates}
        mean_row = [
            sum(row[action_idx] for row in self._matrix) / len(self._matrix)
            for action_idx in range(len(self._action_index))
        ]
        projected_query = self._project_mean_row(mean_row)
        scores: dict[str, float] = {}
        for candidate in candidates:
            action_idx = self._action_index.get(candidate.action_id)
            if action_idx is None:
                scores[candidate.action_id] = 0.0
            else:
                scores[candidate.action_id] = max(0.0, self._dot(projected_query, [
                    factor[action_idx] for factor in self._action_factors
                ]))
        return scores

    def _project_mean_row(self, mean_row: list[float]) -> list[float]:
        # V columns are unit vectors, so projecting the training population mean
        # gives a stable cold-start point for unseen incidents.
        dimensions = len(self._action_factors)
        return [
            sum(mean_row[col] * self._action_factors[dim][col] for col in range(len(mean_row)))
            for dim in range(dimensions)
        ]

    def _truncated_svd(self, matrix: list[list[float]], rank: int) -> tuple[list[list[float]], list[list[float]]]:
        columns = self._transpose(matrix)
        gram = [[self._dot(columns[i], columns[j]) for j in range(len(columns))] for i in range(len(columns))]
        rng = random.Random(self.seed)
        size = len(gram)
        vectors: list[list[float]] = []
        values: list[float] = []
        for component in range(rank):
            start = [rng.uniform(-1.0, 1.0) for _ in range(size)]
            vector = self._normalize(start)
            value = 0.0
            for _ in range(self.iterations):
                working = list(vector)
                for previous in vectors:
                    coefficient = self._dot(previous, working)
                    working = [working[idx] - coefficient * previous[idx] for idx in range(size)]
                vector = self._normalize([sum(gram[row][col] * working[col] for col in range(size)) for row in range(size)])
                new_value = math.sqrt(max(0.0, self._dot(vector, [sum(gram[row][col] * vector[col] for col in range(size)) for row in range(size)])))
                if abs(new_value - value) < 1e-12:
                    value = new_value
                    break
                value = new_value
            vectors.append(vector)
            values.append(value)
        left = []
        for row in matrix:
            left.append([
                sum(row[col] * vectors[dim][col] for col in range(len(row))) / max(values[dim], 1e-12)
                for dim in range(rank)
            ])
        right = self._transpose(vectors)
        # SVD scoring uses V-transpose (rank x actions).
        self._last_right_factors = vectors
        return left, right

    @staticmethod
    def _dot(left: list[float], right: list[float]) -> float:
        return sum(a * b for a, b in zip(left, right))

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm < 1e-15:
            result = [0.0] * len(vector)
            result[0] = 1.0
            return result
        return [value / norm for value in vector]

    @staticmethod
    def _transpose(matrix: list[list[float]]) -> list[list[float]]:
        return [list(row) for row in zip(*matrix)] if matrix else []


class HybridRecommender(BaseRecommender):
    """Fixed-weight late fusion of four normalized utility signals."""

    def __init__(
        self,
        content_model: ContentBasedBaseline,
        collaborative_model: CollaborativeSVDBaseline,
        success_model: PopularitySuccessBaseline,
        weights: dict[str, float] | None = None,
    ) -> None:
        defaults = {"content": 0.45, "collaborative": 0.20, "success": 0.25, "risk": -0.10}
        self.weights = defaults if weights is None else dict(weights)
        required = set(defaults)
        if set(self.weights) != required:
            raise SchemaError(f"hybrid weights must have exactly keys {sorted(required)}")
        if any(not math.isfinite(value) for value in self.weights.values()):
            raise SchemaError("hybrid weights must be finite")
        self.content_model = content_model
        self.collaborative_model = collaborative_model
        self.success_model = success_model
        self._fit_rows: list[InteractionRow] = []

    def fit(self, rows: list[InteractionRow]) -> None:
        self._validate_fit_rows(rows)
        self.content_model.fit(rows)
        self.collaborative_model.fit(rows)
        self.success_model.fit(rows)
        self._fit_rows = list(rows)

    @property
    def fitted_split_rows(self) -> tuple[InteractionRow, ...]:
        return tuple(self._fit_rows)

    def score(
        self,
        query: dict[str, float],
        candidates: list[Runbook],
        *,
        risk_penalty_by_action: dict[str, float] | None = None,
    ) -> dict[str, float]:
        components = {
            "content": self.content_model.score(query, candidates),
            "collaborative": self.collaborative_model.score(query, candidates),
            "success": self.success_model.score(query, candidates),
        }
        penalties = risk_penalty_by_action or {}
        scores: dict[str, float] = {}
        for candidate in candidates:
            raw = sum(self.weights[name] * components[name][candidate.action_id] for name in components)
            raw += self.weights["risk"] * penalties.get(candidate.action_id, default_risk_penalty(candidate))
            scores[candidate.action_id] = raw
        return scores


def rank_candidates(scores: dict[str, float], k: int) -> list[tuple[str, float]]:
    if k <= 0:
        raise SchemaError("k must be positive")
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:k]


def default_risk_penalty(runbook: Runbook) -> float:
    penalty = {"low": 0.0, "medium": 0.35, "high": 0.75}[runbook.risk]
    return penalty + (0.25 if runbook.mutating else 0.0)


# Retain a stable recommender-module export for callers.
__all__ = ["build_content_query"]
