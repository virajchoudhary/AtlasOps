import json

import pytest

from bench import scenario_contract as contract


def test_catalog_is_complete_and_deterministic():
    first = contract.build_catalog()
    second = contract.build_catalog()

    assert first["scenario_count"] == 28
    assert len(first["entries"]) == 28
    assert {entry["scenario_id"] for entry in first["entries"]} == {
        entry["scenario_id"] for entry in second["entries"]
    }
    assert contract.canonical_json(first) == contract.canonical_json(second)

    for entry in first["entries"]:
        assert entry["manifest_sha256"]
        assert entry["success_predicates"]["scenario_id"] == entry["scenario_id"]
        assert entry["faults"], entry["scenario_id"]
        assert "scenario_id" not in entry["model_visible_alert"]


def test_exposure_ledger_fails_closed_for_current_sft_defaults():
    catalog = contract.build_catalog()
    ledger = contract.development_exposure_ledger()
    exposed = contract.development_exposed_ids(catalog, ledger)

    assert len(exposed) == 28
    assert "single_fault/sf-002" in exposed
    assert len(ledger["categories"]["sft_generation_defaults"]["scenario_ids"]) == 28


def test_proposed_split_is_reproducible_and_blocked_before_freeze(tmp_path):
    catalog = contract.build_catalog()
    first = contract.build_proposed_split(catalog, seed="test-seed")
    second = contract.build_proposed_split(catalog, seed="test-seed")
    different = contract.build_proposed_split(catalog, seed="other-seed")

    assert first == second
    assert first != different
    assert first["status"] == "PROPOSED_BLOCKED_NO_FINAL_TEST"
    assert first["activation"] == {"active": False, "authorized_at": None, "frozen": False}
    assert first["splits"]["final_test"] == []
    assert first["ready_for_freeze"] is False
    assigned = (
        first["splits"]["train"]
        + first["splits"]["validation"]
        + first["splits"]["final_test"]
    )
    assert len(assigned) == 28
    assert len(set(assigned)) == 28
    assert first["splits"]["ineligible_final_test_development_exposed"] == sorted(
        contract.development_exposed_ids(catalog)
    )

    contract.validate_split(first, catalog, require_ready=False)
    catalog_path = tmp_path / "catalog.json"
    candidate_path = tmp_path / "candidate.json"
    frozen_path = tmp_path / "frozen.json"
    contract.write_json_atomically(catalog_path, catalog)
    contract.write_json_atomically(candidate_path, first)

    with pytest.raises(ValueError, match="not PROPOSED_READY"):
        contract.freeze_split(
            candidate_path,
            frozen_path,
            authorized_at="2026-08-26T00:00:00Z",
            catalog_path=catalog_path,
        )
    assert not frozen_path.exists()


def test_freeze_requires_clean_final_test_and_is_atomic(tmp_path, monkeypatch):
    catalog = {
        "catalog_sha256": "test-digest",
        "entries": [{"scenario_id": scenario_id} for scenario_id in ("train-a", "heldout-b")],
    }
    candidate = {
        "activation": {"active": False, "authorized_at": None, "frozen": False},
        "blockers": [],
        "catalog_sha256": "test-digest",
        "ready_for_freeze": True,
        "schema_version": contract.SPLIT_SCHEMA_VERSION,
        "seed": "unit",
        "split_fractions": {"train": 0.5, "validation": 0.0, "final_test": 0.5},
        "splits": {
            "train": ["train-a"],
            "validation": [],
            "final_test": ["heldout-b"],
            "ineligible_final_test_development_exposed": ["train-a"],
        },
        "status": "PROPOSED_READY",
        "usage_policy": {},
    }
    monkeypatch.setattr(contract, "development_exposed_ids", lambda _catalog: {"train-a"})
    catalog_path = tmp_path / "catalog.json"
    candidate_path = tmp_path / "candidate.json"
    frozen_path = tmp_path / "split.frozen.json"
    contract.write_json_atomically(catalog_path, catalog)
    contract.write_json_atomically(candidate_path, candidate)

    frozen = contract.freeze_split(
        candidate_path,
        frozen_path,
        authorized_at="2026-08-26T00:00:00Z",
        catalog_path=catalog_path,
    )
    assert frozen["status"] == "FROZEN"
    assert frozen["activation"]["active"] is True
    assert json.loads(frozen_path.read_text(encoding="utf-8")) == frozen

    leaked = dict(candidate)
    leaked["splits"] = dict(candidate["splits"])
    leaked["splits"]["train"] = ["heldout-b"]
    leaked["splits"]["final_test"] = ["train-a"]
    leaked["splits"]["ineligible_final_test_development_exposed"] = ["train-a"]
    leaked_path = tmp_path / "leaked.json"
    conflict_path = tmp_path / "conflict.json"
    contract.write_json_atomically(leaked_path, leaked)
    with pytest.raises(ValueError, match="final-test leakage"):
        contract.freeze_split(
            leaked_path,
            conflict_path,
            authorized_at="2026-08-26T00:00:00Z",
            catalog_path=catalog_path,
        )
    assert not conflict_path.exists()
    with pytest.raises(FileExistsError):
        contract.freeze_split(
            candidate_path,
            frozen_path,
            authorized_at="2026-08-26T00:00:01Z",
            catalog_path=catalog_path,
        )


def test_runtime_role_gate_refuses_missing_active_split(tmp_path):
    with pytest.raises(RuntimeError, match="G5_SPLIT_NOT_ACTIVE"):
        contract.allowed_scenario_ids("sft", repo_root=tmp_path)
