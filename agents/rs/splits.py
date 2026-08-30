"""Canonical G5 binding adapter; RS does not define a competing split system."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from agents.rs.persistence import build_corpus_manifest
from agents.rs.schemas import (
    InteractionRow,
    SchemaError,
    SPLIT_FIT_ELIGIBILITY,
    validate_interactions,
    validate_split_boundaries,
)

SPLIT_BINDING_VERSION = "g5-rs-binding-v1"


@dataclass(frozen=True)
class G5SplitBinding:
    split_hash: str
    incident_splits: Mapping[str, str]
    family_by_incident: Mapping[str, str]
    extraction_version: str = "unbound"

    def __post_init__(self) -> None:
        if len(self.split_hash) != 64 or any(char not in "0123456789abcdef" for char in self.split_hash):
            raise SchemaError("G5 split_hash must be a 64-character digest")
        if self.extraction_version == "unbound":
            raise SchemaError("refusing an empty G5 extraction version")
        valid_splits = set(SPLIT_FIT_ELIGIBILITY)
        for incident, split in self.incident_splits.items():
            if not incident or split not in valid_splits:
                raise SchemaError(f"invalid G5 incident split assignment: {incident}")
        for incident, family in self.family_by_incident.items():
            if not incident or not family:
                raise SchemaError(f"invalid G5 family assignment: {incident}")


def bind_rows_to_g5(
    rows: list[InteractionRow],
    binding: G5SplitBinding,
) -> list[InteractionRow]:
    """Replace provisional splits with canonical G5 assignments, fail-closed."""
    bound: list[InteractionRow] = []
    for row in rows:
        if row.incident_key not in binding.incident_splits:
            raise SchemaError(
                f"incident missing from canonical G5 assignment: {row.incident_key}"
            )
        split = binding.incident_splits[row.incident_key]
        family = binding.family_by_incident.get(row.incident_key, f"incident:{row.incident_key}")
        bound.append(replace(
            row,
            split=split,
            eligible_for_fit=SPLIT_FIT_ELIGIBILITY[split],
            family_id=family,
        ))
    validate_interactions(bound)
    validate_split_boundaries(bound)
    manifest = build_corpus_manifest(
        bound,
        synthetic=all(row.observation_type == "synthetic_label" for row in bound),
        g5_split_hash=binding.split_hash,
    )
    if manifest.g5_binding_status != "bound_to_g5":
        raise SchemaError("G5 binding did not produce a bound corpus manifest")
    return bound
