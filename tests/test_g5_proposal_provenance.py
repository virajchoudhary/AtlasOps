from bench import scenario_contract as contract


def test_only_derived_artifacts_may_differ_before_proposal_regeneration(monkeypatch):
    class Completed:
        returncode = 0
        stdout = (
            " M bench/g5/exposure_ledger.json\n"
            " M bench/g5/scenario_catalog.json\n"
            " M bench/g5/split.proposed.json\n"
            "?? bench/g5/split.frozen.json\n"
            " M bench/runner.py\n"
        )

    monkeypatch.setattr(contract.subprocess, "run", lambda *args, **kwargs: Completed)

    assert contract._unexpected_dirty_proposal_sources() == ["bench/runner.py"]


def test_plan_rejects_dirty_non_derived_sources(tmp_path, monkeypatch):
    class Completed:
        returncode = 0
        stdout = " M bench/runner.py\n"

    monkeypatch.setattr(
        contract.subprocess,
        "run",
        lambda *args, **kwargs: Completed,
    )
    args = type("Args", (), {
        "catalog": tmp_path / "catalog.json",
        "output": tmp_path / "split.json",
        "seed": "unit",
        "train_fraction": 0.6,
        "validation_fraction": 0.2,
        "repo_sha": "",
    })()

    import pytest

    with pytest.raises(ValueError, match="repo_sha would be ambiguous"):
        contract._write_plan(args)
