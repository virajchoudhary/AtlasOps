import json

import pytest

from bench import scenario_contract as contract
from eval import _evaluation_population


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
        assert entry["fault_signature"]
        assert entry["fault_signatures"]
        assert entry["scenario_family_id"]
        assert entry["alert_semantic_hash"]
        assert entry["manifest_semantic_hash"]
        assert entry["target_signature"]
        assert entry["success_predicates"]["scenario_id"] == entry["scenario_id"]
        assert entry["faults"], entry["scenario_id"]
        assert "scenario_id" not in entry["model_visible_alert"]


def test_contract_hashes_are_portable_across_line_endings(tmp_path):
    line_ending = tmp_path / "lf.txt"
    carriage = tmp_path / "crlf.txt"
    bom = tmp_path / "bom.txt"
    line_ending.write_bytes(b"one\ntwo\n")
    carriage.write_bytes(b"one\r\ntwo\r\n")
    bom.write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")

    expected = contract.portable_sha256_file(line_ending)
    assert contract.portable_sha256_file(carriage) == expected
    assert contract.portable_sha256_file(bom) == expected
    assert contract.sha256_file(line_ending) != contract.sha256_file(carriage)


def test_family_signatures_group_same_causal_fault():
    catalog = contract.build_catalog()
    relations = contract.scenario_relationships(catalog)

    assert len(relations) == 17
    reasons = {relation["reason"] for relation in relations}
    assert "single_target" in reasons
    redis_relation = next(
        relation
        for relation in relations
        if {"cascade/cs-002", "single_fault/sf-005"}.issubset(set(relation["scenario_ids"]))
    )
    assert redis_relation["reason"] == "fault_signature"
    assert "multi_fault/mf-002" in redis_relation["scenario_ids"]


def test_catalog_does_not_confuse_cascade_effects_with_red_herrings():
    catalog = contract.build_catalog()
    entries = contract.catalog_entries(catalog)
    cascade = entries["cascade/cs-001"]
    single = entries["single_fault/sf-001"]

    assert cascade["injected_fault_services"] == ["currencyservice"]
    assert {"checkoutservice", "frontend"}.issubset(
        set(cascade["non_target_alert_services"])
    )
    assert cascade["reviewed_red_herring_services"] == []
    assert cascade["red_herring_review_status"] == "NOT_REVIEWED"
    assert single["red_herring_review_status"] == "NO_CANDIDATES"


def test_exposure_ledger_is_deterministic_and_fails_closed():
    catalog = contract.build_catalog()
    first = contract.build_exposure_ledger()
    second = contract.build_exposure_ledger()

    assert first == second
    assert contract.canonical_json(first) == contract.canonical_json(second)
    assert first["schema_version"] == contract.EXPOSURE_SCHEMA_VERSION
    assert len(first["surfaces"]) > 20
    assert "agents/coordinator.py" in {
        surface["path"] for surface in first["surfaces"]
    }

    exposed = contract.development_exposed_ids(catalog, first)
    assert len(exposed) == 28
    assert "single_fault/sf-002" in exposed
    assert first["summary"]["eligible_final_test_candidates"] == []

    contract.validate_exposure_ledger(first, catalog)
    tampered = dict(first)
    tampered["summary"] = dict(first["summary"])
    tampered["summary"]["development_exposed_scenario_ids"] = sorted(exposed - {"single_fault/sf-001"})
    with pytest.raises(ValueError, match="hash mismatch"):
        contract.validate_exposure_ledger(tampered, catalog)


def test_exposure_ledger_excludes_derived_artifacts_and_detects_drift():
    ledger = contract.build_exposure_ledger()
    paths = {surface["path"] for surface in ledger["surfaces"]}

    assert not (paths & contract._DERIVED_CONTRACT_ARTIFACT_PATHS)
    assert any(exclusion["paths"] for exclusion in ledger["exclusions"])
    contract.validate_exposure_ledger(ledger, contract.build_catalog(), require_reproducible=True)

    stale = dict(ledger)
    stale.pop("ledger_sha256")
    stale["exclusions"] = [dict(ledger["exclusions"][0])]
    stale["exclusions"][0]["reason"] = "stale reason"
    stale["ledger_sha256"] = contract.sha256_object(stale)
    catalog = contract.build_catalog()
    with pytest.raises(ValueError, match="exposure ledger drift"):
        contract.validate_exposure_ledger(
            stale,
            catalog,
            require_reproducible=True,
        )


def test_proposed_split_is_reproducible_and_blocked_before_freeze(tmp_path):
    catalog = contract.build_catalog()
    ledger = contract.load_exposure_ledger()
    first = contract.build_proposed_split(catalog, seed="test-seed", exposure_ledger=ledger)
    second = contract.build_proposed_split(catalog, seed="test-seed", exposure_ledger=ledger)
    different = contract.build_proposed_split(catalog, seed="other-seed", exposure_ledger=ledger)

    assert first == second
    assert first != different
    assert first["status"] == "PROPOSED_BLOCKED_NO_FINAL_TEST"
    assert first["exposure_ledger_sha256"] == ledger["ledger_sha256"]
    assert first["family_relation_count"] == 17
    assert any(
        blocker["code"] == "FAMILY_RELATIONS_CROSS_ASSIGNED_SPLITS"
        for blocker in first["blockers"]
    )
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

    contract.validate_split(
        first,
        catalog,
        require_ready=False,
        exposure_ledger=contract.load_exposure_ledger(),
    )
    catalog_path = tmp_path / "catalog.json"
    candidate_path = tmp_path / "candidate.json"
    frozen_path = tmp_path / "frozen.json"
    contract.write_json_atomically(catalog_path, catalog)
    contract.write_json_atomically(candidate_path, first)

    with pytest.raises(ValueError, match="not PROPOSED_READY"):
        contract.freeze_split(
            candidate_path,
            frozen_path,
            authorization={
                "authorized_at": "2026-08-26T00:00:00Z",
                "authorized_by": "operator",
                "authorization_ref": "blocked",
                "g4_passed": True,
            },
            catalog_path=catalog_path,
        )
    assert not frozen_path.exists()


def test_freeze_requires_clean_final_test_and_is_atomic(tmp_path, monkeypatch):
    def entry(scenario_id: str, signature: str):
        return {
            "alert_semantic_hash": f"alert-{scenario_id}",
            "fault_signatures": [signature],
            "red_herring_review_status": "NO_CANDIDATES",
            "scenario_id": scenario_id,
            "source_incident_id": None,
        }

    catalog = {
        "catalog_sha256": "test-digest",
        "entries": [
            entry("train-a", "signature-train"),
            entry("heldout-b", "signature-heldout"),
            entry("safe-c", "signature-safe"),
            entry("related-d", "signature-train"),
            entry("cascade-e", "signature-cascade"),
            entry("multi-f", "signature-multi"),
            entry("named-g", "signature-named"),
        ],
    }
    candidate = {
        "activation": {"active": False, "authorized_at": None, "frozen": False},
        "blockers": [],
        "catalog_sha256": "test-digest",
        "exposure_ledger_sha256": "ledger-digest",
        "ready_for_freeze": True,
        "contract_provenance": {
            "algorithm_version": contract.SPLIT_ALGORITHM_VERSION,
            "generator_version": contract.SPLIT_GENERATOR_VERSION,
            "repo_sha": "0123456789abcdef0123456789abcdef01234567",
        },
        "coverage": {
            "final_test_by_tier": {
                "cascade": 1,
                "multi_fault": 1,
                "named_replays": 1,
                "single_fault": 1,
            }
        },
        "gate_prerequisites": {
            "G4": "OPEN",
            "explicit_freeze_authorization": False,
        },
        "schema_version": contract.SPLIT_SCHEMA_VERSION,
        "seed": "unit",
        "split_fractions": {"train": 0.5, "validation": 0.0, "final_test": 0.5},
        "splits": {
            "train": ["train-a", "related-d"],
            "validation": ["heldout-b"],
            "final_test": ["safe-c", "cascade-e", "multi-f", "named-g"],
            "ineligible_final_test_development_exposed": ["train-a"],
        },
        "status": "PROPOSED_READY",
        "usage_policy": {},
    }
    synthetic_ledger = {
        "schema_version": contract.EXPOSURE_SCHEMA_VERSION,
        "summary": {
            "development_exposed_scenario_ids": ["train-a"],
            "eligible_final_test_candidates": [
                "cascade-e", "heldout-b", "multi-f", "named-g", "related-d", "safe-c",
            ],
        },
    }
    unsigned = {key: value for key, value in synthetic_ledger.items()}
    synthetic_ledger["ledger_sha256"] = contract.sha256_object(unsigned)
    candidate["exposure_ledger_sha256"] = synthetic_ledger["ledger_sha256"]
    monkeypatch.setattr(
        contract,
        "load_exposure_ledger",
        lambda repo_root=contract.REPO_ROOT, verify=True: synthetic_ledger,
    )
    monkeypatch.setattr(contract, "repository_head", lambda repo_root: "head-sha")
    monkeypatch.setattr(
        contract,
        "_unexpected_dirty_proposal_sources",
        lambda repo_root=contract.REPO_ROOT: [],
    )
    monkeypatch.setattr(
        contract,
        "_non_derived_source_drift_since",
        lambda repo_root, source_sha, head_sha: [],
    )
    catalog_path = tmp_path / "catalog.json"
    candidate_path = tmp_path / "candidate.json"
    frozen_path = tmp_path / "split.frozen.json"
    contract.write_json_atomically(catalog_path, catalog)
    contract.write_json_atomically(candidate_path, candidate)

    frozen = contract.freeze_split(
        candidate_path,
        frozen_path,
        authorization={
            "authorized_at": "2026-08-26T00:00:00Z",
            "authorized_by": "operator",
            "authorization_ref": "G5-FREEZE-1",
            "g4_passed": True,
        },
        catalog_path=catalog_path,
        expected_repo_sha="head-sha",
    )
    assert frozen["status"] == "FROZEN"
    assert frozen["activation"]["active"] is True
    assert json.loads(frozen_path.read_text(encoding="utf-8")) == frozen

    leaked = dict(candidate)
    leaked["exposure_ledger_sha256"] = candidate["exposure_ledger_sha256"]
    leaked["splits"] = dict(candidate["splits"])
    leaked["splits"]["train"] = ["heldout-b", "related-d"]
    leaked["splits"]["validation"] = ["safe-c", "cascade-e", "multi-f", "named-g"]
    leaked["splits"]["final_test"] = ["train-a"]
    leaked["splits"]["ineligible_final_test_development_exposed"] = ["train-a"]
    leaked_path = tmp_path / "leaked.json"
    conflict_path = tmp_path / "conflict.json"
    contract.write_json_atomically(leaked_path, leaked)
    with pytest.raises(ValueError, match="final-test leakage"):
        contract.freeze_split(
            leaked_path,
            conflict_path,
            authorization={
                "authorized_at": "2026-08-26T00:00:00Z",
                "authorized_by": "operator",
                "authorization_ref": "G5-FREEZE-1",
                "g4_passed": True,
            },
            catalog_path=catalog_path,
            expected_repo_sha="head-sha",
        )
    assert not conflict_path.exists()
    related = dict(candidate)
    related["splits"] = dict(candidate["splits"])
    related["splits"]["train"] = ["train-a"]
    related["splits"]["validation"] = ["heldout-b", "safe-c"]
    related["splits"]["final_test"] = ["related-d", "cascade-e", "multi-f", "named-g"]
    related["splits"]["ineligible_final_test_development_exposed"] = ["train-a"]
    related_path = tmp_path / "related.json"
    related_conflict_path = tmp_path / "related-frozen.json"
    contract.write_json_atomically(related_path, related)
    with pytest.raises(ValueError, match="family leakage"):
        contract.freeze_split(
            related_path,
            related_conflict_path,
            authorization={
                "authorized_at": "2026-08-26T00:00:00Z",
                "authorized_by": "operator",
                "authorization_ref": "G5-FREEZE-1",
                "g4_passed": True,
            },
            catalog_path=catalog_path,
            expected_repo_sha="head-sha",
        )
    assert not related_conflict_path.exists()
    with pytest.raises(FileExistsError):
        contract.freeze_split(
            candidate_path,
            frozen_path,
            authorization={
                "authorized_at": "2026-08-26T00:00:01Z",
                "authorized_by": "operator",
                "authorization_ref": "G5-FREEZE-1",
                "g4_passed": True,
            },
            catalog_path=catalog_path,
            expected_repo_sha="head-sha",
        )


def test_runtime_role_gate_refuses_missing_active_split(tmp_path):
    with pytest.raises(RuntimeError, match="G5_SPLIT_NOT_ACTIVE"):
        contract.allowed_scenario_ids("sft", repo_root=tmp_path)


def test_development_consumer_policy_and_stage4_special():
    assert len(contract.development_scenario_ids("evaluation_subset")) == 11
    assert len(contract.development_scenario_ids("leaderboard_subset")) == 7
    assert contract.assert_consumer_may_use_scenario(
        "stage4_special", "single_fault/sf-002"
    ) is None
    with pytest.raises(ValueError, match="Stage4 special"):
        contract.assert_consumer_may_use_scenario("stage4_special", "cascade/cs-001")


def test_demo_consumer_cannot_reach_declared_final_test(monkeypatch):
    monkeypatch.setattr(
        contract,
        "load_active_split",
        lambda repo_root=contract.REPO_ROOT: {
            "splits": {"final_test": ["single_fault/sf-001"]}
        },
    )
    with pytest.raises(ValueError, match="final-test scenario"):
        contract.assert_consumer_may_use_scenario(
            "demo_development", "single_fault/sf-001"
        )


def test_demo_consumer_rejects_unknown_scenario_before_freeze():
    with pytest.raises(ValueError, match="outside the frozen scenario catalogue"):
        contract.assert_consumer_may_use_scenario(
            "demo_development", "single_fault/not-in-catalogue"
        )


def test_evaluation_prefers_active_validation_population(monkeypatch):
    monkeypatch.setattr(
        "eval.allowed_scenario_ids",
        lambda role: ("multi_fault/mf-001", "single_fault/sf-001")
        if role == "validation"
        else (),
    )
    monkeypatch.setattr(
        "eval.development_scenario_ids",
        lambda consumer: (_ for _ in ()).throw(AssertionError("fallback used")),
    )

    scenarios, consumer = _evaluation_population(["single_fault"])

    assert scenarios == ["single_fault/sf-001"]
    assert consumer == "validation"


def test_evaluation_falls_back_only_before_split_activation(monkeypatch):
    def refuse_active_split(role):
        raise RuntimeError("G5_SPLIT_NOT_ACTIVE: no frozen split")

    monkeypatch.setattr("eval.allowed_scenario_ids", refuse_active_split)
    monkeypatch.setattr(
        "eval.development_scenario_ids",
        lambda consumer: (
            "cascade/cs-001",
            "single_fault/sf-001",
        ),
    )

    scenarios, consumer = _evaluation_population(["cascade"])

    assert scenarios == ["cascade/cs-001"]
    assert consumer == "evaluation_subset"
