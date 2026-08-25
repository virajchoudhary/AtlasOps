"""Deterministic, fail-closed RS artifact and future-corpus contracts."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from agents.rs.recommender import (
    CollaborativeSVDBaseline,
    ContentBasedBaseline,
    HybridRecommender,
    PopularitySuccessBaseline,
)
from agents.rs.schemas import InteractionRow, Runbook, SchemaError

ARTIFACT_FORMAT = "atlasops-rs-artifact-v1"
MODEL_CONTRACT_VERSION = "rs-model-v1"
CORPUS_MANIFEST_VERSION = "rs-corpus-manifest-v1"
_HASH_LENGTH = 64


@dataclass(frozen=True)
class CorpusManifest:
    schema_version: str
    synthetic: bool
    g5_binding_status: str
    extraction_version: str
    source_splits: tuple[str, ...]
    source_run_ids: tuple[str, ...]
    row_count: int
    incident_count: int
    action_count: int
    service_coverage: tuple[str, ...]
    fault_type_coverage: tuple[str, ...]
    first_recorded_at_unix: float | None
    last_recorded_at_unix: float | None
    corpus_hash: str
    g5_split_hash: str | None


def stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.blake2b(encoded, digest_size=32).hexdigest()


def catalogue_fingerprint(catalogue: list[Runbook]) -> str:
    return stable_hash([item.to_dict() for item in catalogue])


def interaction_corpus_fingerprint(rows: list[InteractionRow]) -> str:
    ordered = sorted(rows, key=lambda row: (
        row.incident_key, row.action_id, row.split, row.recorded_at_unix or 0.0
    ))
    return stable_hash([asdict(row) for row in ordered])


def serialize_hybrid_model(
    model: HybridRecommender,
    catalogue: list[Runbook],
    *,
    code_git_sha: str,
    fitted_at_unix: float,
) -> dict[str, Any]:
    """Return a portable JSON artifact; this function performs no file I/O."""
    if not model.is_fitted:
        raise SchemaError("cannot serialize an unfitted recommender")
    collaborative = model.collaborative_model
    success = model.success_model
    if not isinstance(collaborative, CollaborativeSVDBaseline):
        raise SchemaError("serialization requires CollaborativeSVDBaseline")
    if not isinstance(success, PopularitySuccessBaseline):
        raise SchemaError("serialization requires PopularitySuccessBaseline")
    training_rows = [
        row for row in model.fitted_split_rows if row.eligible_for_fit
    ]
    payload: dict[str, Any] = {
        "contract_version": MODEL_CONTRACT_VERSION,
        "catalogue_hash": catalogue_fingerprint(catalogue),
        "training_split_hash": interaction_corpus_fingerprint(training_rows),
        "interaction_corpus_hash": interaction_corpus_fingerprint(list(model.fitted_split_rows)),
        "weights": dict(model.weights),
        "seed": collaborative.seed,
        "fitted_at_unix": fitted_at_unix,
        "code_git_sha": code_git_sha,
        "algorithm_parameters": {
            "collaborative": {
                "latent_dimensions": collaborative.latent_dimensions,
                "iterations": collaborative.iterations,
            },
            "success": {
                "alpha": success.alpha,
                "prior_success": success.prior_success,
                "min_actions_for_rate": success.min_actions_for_rate,
            },
        },
        "state": {
            "incident_index": collaborative._incident_index,
            "action_index": collaborative._action_index,
            "matrix": collaborative._matrix,
            "action_factors": collaborative._action_factors,
            "singular_values": list(collaborative.singular_values),
            "success_stats": {key: list(value) for key, value in success._stats.items()},
        },
    }
    return {
        "format": ARTIFACT_FORMAT,
        "payload": payload,
        "payload_hash": stable_hash(payload),
    }


def deserialize_hybrid_model(
    envelope: Mapping[str, Any],
    catalogue: list[Runbook],
) -> HybridRecommender:
    if envelope.get("format") != ARTIFACT_FORMAT:
        raise SchemaError("unknown RS artifact format")
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or stable_hash(payload) != envelope.get("payload_hash"):
        raise SchemaError("RS artifact integrity check failed")
    if payload.get("contract_version") != MODEL_CONTRACT_VERSION:
        raise SchemaError("unsupported RS model contract")
    if payload.get("catalogue_hash") != catalogue_fingerprint(catalogue):
        raise SchemaError("RS artifact catalogue hash does not match loaded catalogue")
    for field in ("training_split_hash", "interaction_corpus_hash", "code_git_sha"):
        if not _is_hash_or_revision(str(payload.get(field, ""))):
            raise SchemaError(f"invalid artifact provenance field: {field}")
    timestamp = payload.get("fitted_at_unix")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(float(timestamp)):
        raise SchemaError("invalid fitted_at_unix")
    weights = payload.get("weights")
    parameters = payload.get("algorithm_parameters")
    state = payload.get("state")
    if not isinstance(weights, dict) or not isinstance(parameters, dict) or not isinstance(state, dict):
        raise SchemaError("malformed RS artifact payload")
    collaborative_params = parameters.get("collaborative")
    success_params = parameters.get("success")
    if not isinstance(collaborative_params, dict) or not isinstance(success_params, dict):
        raise SchemaError("malformed algorithm parameters")
    try:
        collaborative = CollaborativeSVDBaseline(
            latent_dimensions=int(collaborative_params["latent_dimensions"]),
            seed=int(payload["seed"]),
            iterations=int(collaborative_params["iterations"]),
        )
        success = PopularitySuccessBaseline(
            alpha=float(success_params["alpha"]),
            prior_success=float(success_params["prior_success"]),
            min_actions_for_rate=int(success_params["min_actions_for_rate"]),
        )
        model = HybridRecommender(
            content_model=ContentBasedBaseline(),
            collaborative_model=collaborative,
            success_model=success,
            weights={key: float(value) for key, value in weights.items()},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaError("invalid RS algorithm parameters") from exc
    _validate_and_restore_state(collaborative, success, state)
    model._fit_rows = []
    model._has_been_fit = True
    return model


def build_corpus_manifest(
    rows: list[InteractionRow],
    *,
    synthetic: bool = True,
    g5_split_hash: str | None = None,
) -> CorpusManifest:
    if synthetic and any(row.observation_type != "synthetic_label" for row in rows):
        raise SchemaError("manifest marked synthetic contains non-synthetic observations")
    if g5_split_hash is None:
        binding_status = "pending_g5"
    elif not _is_hash(g5_split_hash):
        raise SchemaError("G5 split hash must be a 64-character digest")
    else:
        binding_status = "bound_to_g5"
    timestamps = [float(row.recorded_at_unix) for row in rows if row.recorded_at_unix is not None]
    return CorpusManifest(
        schema_version=CORPUS_MANIFEST_VERSION,
        synthetic=synthetic,
        g5_binding_status=binding_status,
        extraction_version="not_extracted_until_g5_authorizes",
        source_splits=tuple(sorted({row.split for row in rows})),
        source_run_ids=tuple(sorted({row.source_run for row in rows if row.source_run})),
        row_count=len(rows),
        incident_count=len({row.incident_key for row in rows}),
        action_count=len({row.action_id for row in rows}),
        service_coverage=tuple(sorted({row.service for row in rows})),
        fault_type_coverage=tuple(sorted({
            fault for row in rows for fault in row.fault_types
        })),
        first_recorded_at_unix=min(timestamps) if timestamps else None,
        last_recorded_at_unix=max(timestamps) if timestamps else None,
        corpus_hash=interaction_corpus_fingerprint(rows),
        g5_split_hash=g5_split_hash,
    )


def _validate_and_restore_state(
    collaborative: CollaborativeSVDBaseline,
    success: PopularitySuccessBaseline,
    state: Mapping[str, Any],
) -> None:
    incident_index = state.get("incident_index")
    action_index = state.get("action_index")
    matrix = state.get("matrix")
    action_factors = state.get("action_factors")
    singular_values = state.get("singular_values")
    success_stats = state.get("success_stats")
    if not all(isinstance(item, dict) for item in (incident_index, action_index, success_stats)):
        raise SchemaError("malformed RS model indexes")
    if not all(isinstance(item, list) for item in (matrix, action_factors, singular_values)):
        raise SchemaError("malformed RS model matrices")
    if list(incident_index.values()) != list(range(len(incident_index))):
        raise SchemaError("incident index must be contiguous")
    if list(action_index.values()) != list(range(len(action_index))):
        raise SchemaError("action index must be contiguous")
    if len(matrix) != len(incident_index) or any(len(row) != len(action_index) for row in matrix):
        raise SchemaError("collaborative matrix dimensions do not match indexes")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0
        for row in matrix for value in row
    ):
        raise SchemaError("collaborative matrix values must be finite relevance values")
    rank = len(singular_values)
    if rank > min(len(incident_index), len(action_index)):
        raise SchemaError("singular-value rank exceeds matrix dimensions")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(float(value)) or float(value) < 0.0
        for value in singular_values
    ):
        raise SchemaError("singular values must be finite and non-negative")
    if len(action_factors) != rank or any(len(row) != len(action_index) for row in action_factors):
        raise SchemaError("action-factor dimensions do not match model state")
    if any(
        not math.isfinite(float(value))
        for row in action_factors for value in row
    ):
        raise SchemaError("action factors must be finite")
    restored_stats: dict[str, tuple[int, float]] = {}
    for action_id, raw_stats in success_stats.items():
        if action_id not in action_index or not isinstance(raw_stats, list) or len(raw_stats) != 2:
            raise SchemaError("invalid success statistics")
        count, average = raw_stats
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise SchemaError("invalid success count")
        if isinstance(average, bool) or not isinstance(average, (int, float)) or not math.isfinite(float(average)) or not 0.0 <= float(average) <= 1.0:
            raise SchemaError("invalid success average")
        restored_stats[action_id] = (count, float(average))
    collaborative._incident_index = {str(key): int(value) for key, value in incident_index.items()}
    collaborative._action_index = {str(key): int(value) for key, value in action_index.items()}
    collaborative._matrix = [[float(value) for value in row] for row in matrix]
    collaborative._action_factors = [[float(value) for value in row] for row in action_factors]
    collaborative._last_right_factors = collaborative._action_factors
    collaborative._singular_values = [float(value) for value in singular_values]
    collaborative._last_singular_values = list(collaborative._singular_values)
    success._stats = restored_stats


def _is_hash(value: str) -> bool:
    return len(value) == _HASH_LENGTH and all(char in "0123456789abcdef" for char in value)


def _is_hash_or_revision(value: str) -> bool:
    return bool(value) and (len(value) == 40 and all(char in "0123456789abcdef" for char in value.lower()) or _is_hash(value))
