"""Tests for Stage 5: Freeze Scenario Truth and Benchmark Splits (Gate G5).

Validates:
1. All 28 static chaos manifests exist on disk and match frozen cryptographic SHA-256 digests.
2. Benchmark splits (Train, Val, Test) are strictly pairwise disjoint and cover all 28 scenarios.
3. Objective verifier specifications cover 100% of scenarios in the catalog.
4. Target microservices belong to the standard Online Boutique catalog or documented exceptions.
5. Every tier is represented in Train, Val, and Test splits.
6. Helper functions and split accessors function correctly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import pytest

from agents.verifier import ONLINE_BOUTIQUE_SERVICES, SCENARIO_VERIFICATION_SPECS
from config.scenario_catalog import (
    FROZEN_STATIC_SCENARIO_COUNT,
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
    get_scenario,
    verify_catalog_manifest_hashes,
    verify_split_disjointness,
)
from config.splits import get_split, get_split_scenarios


class TestStage5ScenarioTruthAndSplits:
    def test_catalog_contains_exactly_28_scenarios(self):
        assert len(SCENARIO_CATALOG) == 28
        assert FROZEN_STATIC_SCENARIO_COUNT == 28

    def test_all_28_manifests_exist_and_hashes_match(self):
        repo_root = Path(__file__).resolve().parents[1]
        result = verify_catalog_manifest_hashes(repo_root)
        assert result["missing_files"] == [], f"Missing files: {result['missing_files']}"
        assert result["mismatches"] == {}, f"Hash mismatches: {result['mismatches']}"
        assert result["is_valid"] is True
        assert result["total_verified"] == 28

    def test_split_disjointness_and_exhaustiveness(self):
        result = verify_split_disjointness()
        assert result["is_valid"] is True
        assert result["is_disjoint"] is True
        assert result["is_exhaustive"] is True
        assert result["train_count"] == 16
        assert result["val_count"] == 6
        assert result["test_count"] == 6
        assert result["total_static_count"] == 28
        assert result["train_val_overlap"] == []
        assert result["train_test_overlap"] == []
        assert result["val_test_overlap"] == []
        assert result["missing_scenarios"] == []
        assert result["extra_scenarios"] == []

    def test_verifier_coverage_for_all_scenarios(self):
        missing_verifier_specs = []
        for sid, meta in SCENARIO_CATALOG.items():
            if sid not in SCENARIO_VERIFICATION_SPECS:
                missing_verifier_specs.append(sid)
            else:
                spec = SCENARIO_VERIFICATION_SPECS[sid]
                assert spec.scenario_id == sid
                assert spec.require_chaos_cleared == meta.require_chaos_cleared
        assert missing_verifier_specs == [], f"Scenarios missing verifier spec: {missing_verifier_specs}"

    def test_service_catalog_compliance(self):
        allowed_services = set(ONLINE_BOUTIQUE_SERVICES).union({"checkoutservice-legacy"})
        for sid, meta in SCENARIO_CATALOG.items():
            for svc in meta.target_services:
                assert svc in allowed_services, f"Scenario {sid} references uncatalogued service: {svc}"
            for wl in meta.verification_workloads:
                assert wl in allowed_services, f"Scenario {sid} verification references uncatalogued workload: {wl}"

    def test_tier_distribution_across_splits(self):
        tiers = {"single_fault", "cascade", "multi_fault", "named_replays"}
        
        train_tiers = {SCENARIO_CATALOG[sid].tier for sid in TRAIN_SPLIT}
        val_tiers = {SCENARIO_CATALOG[sid].tier for sid in VAL_SPLIT}
        test_tiers = {SCENARIO_CATALOG[sid].tier for sid in TEST_SPLIT}

        assert train_tiers == tiers, f"Train split missing tiers: {tiers - train_tiers}"
        assert val_tiers == tiers, f"Val split missing tiers: {tiers - val_tiers}"
        assert test_tiers == tiers, f"Test split missing tiers: {tiers - test_tiers}"

    def test_get_split_and_helpers(self):
        train_ids = get_split("train")
        assert train_ids == TRAIN_SPLIT

        val_ids = get_split("val")
        assert val_ids == VAL_SPLIT

        test_ids = get_split("test")
        assert test_ids == TEST_SPLIT

        lb_ids = get_split("leaderboard")
        assert lb_ids == LEADERBOARD_SPLIT

        train_objs = get_split_scenarios("train")
        assert len(train_objs) == 16
        assert all(isinstance(obj.scenario_id, str) for obj in train_objs)

        with pytest.raises(KeyError):
            get_split("invalid_split_name")

    def test_get_scenario_and_error_handling(self):
        sf1 = get_scenario("single_fault/sf-001")
        assert sf1.scenario_id == "single_fault/sf-001"
        assert sf1.tier == "single_fault"
        assert sf1.chaos_kinds == ("PodChaos",)

        with pytest.raises(KeyError):
            get_scenario("non_existent_scenario")

    def test_seeds_are_deterministic_integers(self):
        assert isinstance(TRAIN_SEED, int)
        assert isinstance(VAL_SEED, int)
        assert isinstance(TEST_SEED, int)
        assert isinstance(LEADERBOARD_SEED, int)
        assert len({TRAIN_SEED, VAL_SEED, TEST_SEED, LEADERBOARD_SEED}) == 4
