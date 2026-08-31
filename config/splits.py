"""AtlasOps Benchmark Split Helpers and Curricula (Gate G5).

Provides lightweight accessors, sampling seeds, and iteration utilities for
the frozen benchmark partitions defined in config.scenario_catalog.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from config.scenario_catalog import (
    LEADERBOARD_SEED,
    LEADERBOARD_SPLIT,
    SCENARIO_CATALOG,
    SPLIT_PARTITIONS,
    TEST_SEED,
    TEST_SPLIT,
    TRAIN_SEED,
    TRAIN_SPLIT,
    VAL_SEED,
    VAL_SPLIT,
    ScenarioMetadata,
    get_scenario,
    verify_split_disjointness,
)

__all__ = [
    "TRAIN_SEED",
    "VAL_SEED",
    "TEST_SEED",
    "LEADERBOARD_SEED",
    "TRAIN_SPLIT",
    "VAL_SPLIT",
    "TEST_SPLIT",
    "LEADERBOARD_SPLIT",
    "SPLIT_PARTITIONS",
    "get_split",
    "get_split_scenarios",
    "verify_split_disjointness",
]


def get_split(name: str) -> tuple[str, ...]:
    """Retrieve scenario IDs for a named split ('train', 'val', 'test', 'leaderboard')."""
    normalized = name.strip().lower()
    if normalized not in SPLIT_PARTITIONS:
        raise KeyError(f"Unknown split name: {name!r}. Supported splits: {sorted(SPLIT_PARTITIONS.keys())}")
    return SPLIT_PARTITIONS[normalized]


def get_split_scenarios(name: str) -> list[ScenarioMetadata]:
    """Retrieve full ScenarioMetadata records for a named split."""
    ids = get_split(name)
    return [get_scenario(sid) for sid in ids]
