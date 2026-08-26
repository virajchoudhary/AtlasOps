from pathlib import Path

import pytest

from bench import scenario_contract as contract


def _git_result(returncode: int = 0, stdout: str = ""):
    return type("Completed", (), {
        "returncode": returncode,
        "stdout": stdout,
    })()


def test_source_drift_excludes_derived_contract_artifacts(monkeypatch):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[:2] == ["git", "merge-base"]:
            return _git_result(0)
        if command[:2] == ["git", "diff"]:
            return _git_result(
                0,
                "bench/runner.py\n"
                "bench/g5/exposure_ledger.json\n"
                "bench/g5/scenario_catalog.json\n",
            )
        raise AssertionError(command)

    monkeypatch.setattr(contract.subprocess, "run", fake_run)

    drifted = contract._non_derived_source_drift_since(
        Path("repo"), "source-sha", "head-sha"
    )

    assert drifted == ["bench/runner.py"]
    assert any("merge-base" in command for command in [commands[0]])


def _candidate(source_sha: str) -> dict:
    return {
        "activation": {"active": False, "authorized_at": None, "frozen": False},
        "blockers": [],
        "catalog_sha256": "catalog-digest",
        "contract_provenance": {
            "algorithm_version": contract.SPLIT_ALGORITHM_VERSION,
            "generator_version": contract.SPLIT_GENERATOR_VERSION,
            "repo_sha": source_sha,
        },
        "coverage": {},
        "exposure_ledger_sha256": "ledger-digest",
        "gate_prerequisites": {
            "G4": "OPEN",
            "explicit_freeze_authorization": False,
        },
        "ready_for_freeze": True,
        "schema_version": contract.SPLIT_SCHEMA_VERSION,
        "seed": "unit",
        "splits": {"train": [], "validation": [], "final_test": []},
        "status": "PROPOSED_READY",
        "usage_policy": {},
    }


def _write_candidate(tmp_path: Path, value: dict) -> tuple[Path, Path, Path]:
    catalog_path = tmp_path / "catalog.json"
    candidate_path = tmp_path / "candidate.json"
    frozen_path = tmp_path / "frozen.json"
    contract.write_json_atomically(catalog_path, {"entries": []})
    contract.write_json_atomically(candidate_path, value)
    return catalog_path, candidate_path, frozen_path


def _prepare_freeze_mocks(monkeypatch):
    monkeypatch.setattr(contract, "validate_split", lambda *args, **kwargs: None)
    monkeypatch.setattr(contract, "load_exposure_ledger", lambda repo_root=None: {})
    monkeypatch.setattr(contract, "repository_head", lambda repo_root: "head-sha")
    monkeypatch.setattr(
        contract,
        "_unexpected_dirty_proposal_sources",
        lambda repo_root=contract.REPO_ROOT: [],
    )


def authorization():
    return {
        "authorized_at": "2030-01-01T00:00:00Z",
        "authorized_by": "operator",
        "authorization_ref": "unit",
        "g4_passed": True,
    }


def test_freeze_rejects_nonderived_source_drift(tmp_path, monkeypatch):
    _prepare_freeze_mocks(monkeypatch)
    monkeypatch.setattr(
        contract,
        "_non_derived_source_drift_since",
        lambda repo_root, source_sha, head_sha: ["bench/runner.py"],
    )
    catalog_path, candidate_path, frozen_path = _write_candidate(
        tmp_path,
        _candidate("source-sha"),
    )

    with pytest.raises(RuntimeError, match="split drift detected"):
        contract.freeze_split(
            candidate_path,
            frozen_path,
            authorization=authorization(),
            catalog_path=catalog_path,
        )

    assert not frozen_path.exists()


def test_freeze_allows_artifact_only_history_after_clean_source(tmp_path, monkeypatch):
    _prepare_freeze_mocks(monkeypatch)
    monkeypatch.setattr(
        contract,
        "_non_derived_source_drift_since",
        lambda repo_root, source_sha, head_sha: [],
    )
    catalog_path, candidate_path, frozen_path = _write_candidate(
        tmp_path,
        _candidate("source-sha"),
    )

    frozen = contract.freeze_split(
        candidate_path,
        frozen_path,
        authorization=authorization(),
        catalog_path=catalog_path,
    )

    assert frozen["status"] == "FROZEN"
    assert frozen_path.exists()
