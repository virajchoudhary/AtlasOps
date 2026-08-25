"""Explicit in-memory schemas for the remediation recommender."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_PLACEHOLDER_RE = re.compile(
    r"\{\{([a-z][a-z0-9_]*)(?:[|:](int|str)(?::([A-Za-z0-9_.-]+))?)?\}\}"
)
VALID_SPLITS = frozenset({"train", "calibration", "test", "future_final_test"})
SPLIT_FIT_ELIGIBILITY = {
    "train": True,
    "calibration": True,
    "test": False,
    "future_final_test": False,
}
VALID_RISKS = frozenset({"low", "medium", "high"})
VALID_OBSERVATION_TYPES = frozenset({
    "synthetic_label",
    "selected_success",
    "selected_partial",
    "selected_failure",
    "not_selected",
    "unknown_counterfactual",
    "human_override",
    "unsafe_filtered",
    "policy_rejected",
})


class SchemaError(ValueError):
    """Raised when an RS record fails a structural or semantic invariant."""


@dataclass(frozen=True)
class Runbook:
    action_id: str
    name: str
    tool_name: str
    parameter_template: dict[str, Any]
    applicable_fault_types: tuple[str, ...]
    prerequisites: tuple[str, ...]
    risk: str
    mutating: bool
    description: str
    stage: str = "remediation"
    tags: tuple[str, ...] = ()
    version: int = 1
    service_constraints: tuple[str, ...] = ("*",)
    safety_constraints: tuple[str, ...] = ()
    verification_action_ids: tuple[str, ...] = ()
    deprecated: bool = False
    provenance: str = "registered-tool-policy"

    def __post_init__(self) -> None:
        if not _IDENTIFIER_RE.fullmatch(self.action_id):
            raise SchemaError(f"invalid action_id: {self.action_id!r}")
        if not _IDENTIFIER_RE.fullmatch(self.tool_name):
            raise SchemaError(f"invalid tool_name on {self.action_id}: {self.tool_name!r}")
        if self.risk not in VALID_RISKS:
            raise SchemaError(f"invalid risk on {self.action_id}: {self.risk!r}")
        if not isinstance(self.mutating, bool):
            raise SchemaError(f"mutating must be boolean on {self.action_id}")
        if self.stage not in {"diagnostic", "verification", "remediation"}:
            raise SchemaError(f"invalid stage on {self.action_id}: {self.stage!r}")
        if self.stage != "remediation" and self.mutating:
            raise SchemaError(f"non-remediation runbook cannot mutate: {self.action_id}")
        for attr in (
            "applicable_fault_types", "prerequisites", "tags",
            "service_constraints", "safety_constraints", "verification_action_ids",
        ):
            values = getattr(self, attr)
            if isinstance(values, str) or not all(isinstance(v, str) and v for v in values):
                raise SchemaError(f"{attr} must be non-empty strings on {self.action_id}")
        if isinstance(self.version, bool) or self.version < 1:
            raise SchemaError(f"version must be positive on {self.action_id}")
        if self.deprecated not in {True, False}:
            raise SchemaError(f"deprecated must be boolean on {self.action_id}")
        if not self.provenance.strip():
            raise SchemaError(f"provenance cannot be blank on {self.action_id}")
        if not isinstance(self.parameter_template, dict):
            raise SchemaError(f"parameter_template must be an object on {self.action_id}")
        if not self.name.strip() or not self.description.strip():
            raise SchemaError(f"name/description cannot be blank on {self.action_id}")
        _validate_json_values(self.parameter_template, f"{self.action_id}.parameter_template")
        placeholders = {name for name, _kind in _iter_placeholders(self.parameter_template)}
        declared_names = {prerequisite.removesuffix(":int") for prerequisite in self.prerequisites}
        undeclared = placeholders.difference(declared_names)
        if undeclared:
            raise SchemaError(
                f"{self.action_id} has undeclared template inputs: {sorted(undeclared)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextFeatures:
    incident_key: str
    service: str
    namespace: str
    fault_types: tuple[str, ...]
    symptoms: tuple[str, ...]
    severity: str
    diagnosis_text: str
    deployment_recently_changed: bool
    active_chaos_experiment: bool
    mutation_budget_remaining: int
    approval_granted: bool = False
    numeric_features: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.incident_key, str) or len(self.incident_key) > 128:
            raise SchemaError("incident_key must be a string of at most 128 characters")
        for attr in ("service", "namespace", "severity"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise SchemaError(f"{attr} must be a non-empty string")
        for attr in ("fault_types", "symptoms"):
            values = getattr(self, attr)
            if isinstance(values, str) or not all(isinstance(v, str) and v for v in values):
                raise SchemaError(f"{attr} must be non-empty strings")
        if not isinstance(self.diagnosis_text, str):
            raise SchemaError("diagnosis_text must be text")
        for attr in ("deployment_recently_changed", "active_chaos_experiment", "approval_granted"):
            if not isinstance(getattr(self, attr), bool):
                raise SchemaError(f"{attr} must be boolean")
        if isinstance(self.mutation_budget_remaining, bool) or self.mutation_budget_remaining < 0:
            raise SchemaError("mutation_budget_remaining must be a non-negative integer")
        if not isinstance(self.numeric_features, dict):
            raise SchemaError("numeric_features must be an object")
        for key, value in self.numeric_features.items():
            if not _IDENTIFIER_RE.fullmatch(key):
                raise SchemaError(f"invalid numeric feature name: {key!r}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise SchemaError(f"numeric feature {key} must be finite")


@dataclass(frozen=True)
class InteractionRow:
    incident_key: str
    action_id: str
    service: str
    fault_types: tuple[str, ...]
    outcome: str
    relevance: float
    selected: bool
    split: str
    eligible_for_fit: bool
    source_run: str = ""
    recorded_at_unix: float | None = None
    observation_type: str = "synthetic_label"
    rank: int | None = None
    policy_choice: str = ""
    approval_result: str = ""
    executor_outcome: str = ""
    verifier_outcome: str = ""
    counterfactual_status: str = ""
    context_hash: str = ""
    family_id: str = ""
    episode_id: str = ""
    schema_version: str = "rs-interaction-v1"
    provenance: str = ""

    def __post_init__(self) -> None:
        for attr in ("incident_key", "action_id", "service"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value:
                raise SchemaError(f"{attr} must be a non-empty string")
        if isinstance(self.fault_types, str) or not all(
            isinstance(item, str) and item for item in self.fault_types
        ):
            raise SchemaError("fault_types must be non-empty strings")
        if self.outcome not in {
            "success", "partial", "failure", "rejected", "not_selected",
            "unknown", "human_override", "unsafe_filtered", "policy_rejected",
        }:
            raise SchemaError(f"invalid outcome: {self.outcome!r}")
        if self.observation_type not in VALID_OBSERVATION_TYPES:
            raise SchemaError(f"invalid observation_type: {self.observation_type!r}")
        if self.schema_version != "rs-interaction-v1":
            raise SchemaError("unsupported interaction schema_version")
        if isinstance(self.relevance, bool) or not isinstance(self.relevance, (int, float)):
            raise SchemaError("relevance must be numeric")
        relevance = float(self.relevance)
        if not math.isfinite(relevance) or not 0.0 <= relevance <= 1.0:
            raise SchemaError("relevance must be finite in [0, 1]")
        if self.outcome == "not_selected" and self.selected:
            raise SchemaError("selected/outcome combination is inconsistent")
        non_observed = {"not_selected", "unknown_counterfactual", "unsafe_filtered", "policy_rejected"}
        if self.observation_type in non_observed:
            if self.selected:
                raise SchemaError(f"{self.observation_type} rows cannot be selected")
            if relevance != 0.0:
                raise SchemaError(f"{self.observation_type} rows cannot claim positive utility")
        if self.observation_type in {"selected_success", "selected_partial", "selected_failure"}:
            expected_outcome = self.observation_type.removeprefix("selected_")
            if not self.selected or self.outcome != expected_outcome:
                raise SchemaError(f"{self.observation_type} conflicts with selected/outcome")
        if self.rank is not None:
            if isinstance(self.rank, bool) or self.rank < 1:
                raise SchemaError("rank must be a positive integer when present")
        if self.split not in VALID_SPLITS:
            raise SchemaError(f"unknown split: {self.split!r}")
        expected_fit = SPLIT_FIT_ELIGIBILITY[self.split]
        if self.eligible_for_fit is not expected_fit:
            raise SchemaError(
                f"eligible_for_fit={self.eligible_for_fit} conflicts with split={self.split}"
            )
        if self.recorded_at_unix is not None:
            timestamp = float(self.recorded_at_unix)
            if not math.isfinite(timestamp) or timestamp < 0:
                raise SchemaError("recorded_at_unix must be finite and non-negative")


def validate_catalogue(catalogue: list[Runbook], registered_tools: frozenset[str]) -> None:
    ids = [runbook.action_id for runbook in catalogue]
    if len(ids) != len(set(ids)):
        raise SchemaError("duplicate action_id in runbook catalogue")
    unknown_tools = sorted({item.tool_name for item in catalogue}.difference(registered_tools))
    if unknown_tools:
        raise SchemaError(f"catalogue names unregistered tools: {unknown_tools}")
    if not 20 <= len(catalogue) <= 40:
        raise SchemaError(f"catalogue size must be between 20 and 40, got {len(catalogue)}")


def validate_interactions(rows: list[InteractionRow]) -> None:
    seen: set[tuple[str, str]] = set()
    by_incident: dict[str, set[str]] = {}
    by_family: dict[str, set[str]] = {}
    by_episode: dict[str, set[str]] = {}
    for row in rows:
        pair = (row.incident_key, row.action_id)
        if pair in seen:
            raise SchemaError(f"duplicate interaction for incident/action: {pair}")
        seen.add(pair)
        by_incident.setdefault(row.incident_key, set()).add(row.split)
        if row.family_id:
            by_family.setdefault(row.family_id, set()).add(row.split)
        if row.episode_id:
            by_episode.setdefault(row.episode_id, set()).add(row.split)
        if row.eligible_for_fit and row.split not in {"train", "calibration"}:
            raise SchemaError(f"illegal fit row outside train/calibration: {row.incident_key}")
    mixed = {key: splits for key, splits in by_incident.items() if len(splits) != 1}
    if mixed:
        raise SchemaError(f"incident appears in multiple splits: {sorted(mixed)[:3]}")
    mixed_families = {key: splits for key, splits in by_family.items() if len(splits) != 1}
    if mixed_families:
        raise SchemaError(f"incident family crosses splits: {sorted(mixed_families)[:3]}")
    mixed_episodes = {key: splits for key, splits in by_episode.items() if len(splits) != 1}
    if mixed_episodes:
        raise SchemaError(f"source episode crosses splits: {sorted(mixed_episodes)[:3]}")


def validate_split_boundaries(rows: list[InteractionRow]) -> None:
    """Require disjoint incidents and monotonic time across fit/holdout rows."""
    validate_interactions(rows)
    timestamps_by_split: dict[str, list[float]] = {split: [] for split in VALID_SPLITS}
    for row in rows:
        if row.recorded_at_unix is not None:
            timestamps_by_split[row.split].append(float(row.recorded_at_unix))
    ordered_splits = ["train", "calibration", "test", "future_final_test"]
    present = [(split, timestamps_by_split[split]) for split in ordered_splits if timestamps_by_split[split]]
    for earlier, later in zip(present, present[1:]):
        if max(earlier[1]) >= min(later[1]):
            raise SchemaError(
                f"split boundary overlap between {earlier[0]} and {later[0]}"
            )


def iter_placeholder_names_and_kinds(value: Any) -> list[tuple[str, str | None]]:
    if isinstance(value, str):
        return [(match.group(1), match.group(2)) for match in _PLACEHOLDER_RE.finditer(value)]
    if isinstance(value, dict):
        return [part for child in value.values() for part in _iter_placeholders(child)]
    if isinstance(value, (list, tuple)):
        return [part for child in value for part in _iter_placeholders(child)]
    return []


# Backward-compatible local name for schema internals.
_iter_placeholders = iter_placeholder_names_and_kinds


def _validate_json_values(value: Any, location: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{location} must be JSON-compatible") from exc


def dataclass_fields(model: type[Any]) -> tuple[str, ...]:
    return tuple(item.name for item in fields(model))
