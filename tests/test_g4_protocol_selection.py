"""Prospective protocol wiring without source approval, inference or cluster access."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from config import g4_demo_candidate as candidate
from config import g4_protocol as historical
from config import g4_protocol_selection as selection


@pytest.fixture
def website_claim(monkeypatch, tmp_path):
    from config import g4_launch_authority as authority

    root = tmp_path / "authority"
    root.mkdir()
    monkeypatch.setattr(authority, "LOCAL_OPERATOR_AUTHORITY", root)
    def claim():
        fingerprint = historical.protocol_fingerprint(candidate.candidate_profile())
        channel = str(tmp_path / "channel.json")
        monkeypatch.setenv("ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE", channel)
        monkeypatch.setenv("ATLASOPS_STAGE4_LAUNCH_TOKEN", "test-launch-token")
        monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", "a" * 40)
        monkeypatch.setenv("STAGE4_EXPERIMENT_ID", "EXP-STAGE4-SF002-019")
        (root / f"{fingerprint}.json").write_text(json.dumps({
            "experiment_id": "EXP-STAGE4-SF002-019", "source_sha": "a" * 40,
            "protocol_fingerprint": fingerprint, "channel_path": channel,
            "launch_token_sha256": hashlib.sha256(b"test-launch-token").hexdigest(),
        }))
        return fingerprint
    return claim


def test_default_selection_preserves_historical_profile(monkeypatch):
    monkeypatch.delenv("ATLASOPS_STAGE4_PROTOCOL", raising=False)
    before = copy.deepcopy(historical.APPROVED_G4_PROTOCOL_PROFILE)
    assert selection.selected_profile() == before
    assert selection.declared_profile() == before


@pytest.mark.parametrize("value", ["unknown", "", "website", "../candidate"])
def test_unknown_selection_fails_closed(monkeypatch, value):
    monkeypatch.setenv("ATLASOPS_STAGE4_PROTOCOL", value)
    with pytest.raises(ValueError, match="Unknown"):
        selection.selected_profile()


def test_candidate_requires_exact_fingerprint_operator_channel_and_pinned_source(monkeypatch, website_claim):
    monkeypatch.setenv("ATLASOPS_STAGE4_PROTOCOL", selection.WEBSITE_CANDIDATE)
    monkeypatch.delenv("STAGE4_APPROVED_PROTOCOL_SHA256", raising=False)
    with pytest.raises(RuntimeError, match="fingerprint"):
        selection.selected_profile()
    expected = candidate.candidate_profile()
    monkeypatch.setenv("STAGE4_APPROVED_PROTOCOL_SHA256", historical.protocol_fingerprint(expected))
    monkeypatch.delenv("ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE", raising=False)
    with pytest.raises(RuntimeError, match="operator channel"):
        selection.selected_profile()
    monkeypatch.setenv("ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE", "channel")
    monkeypatch.setattr(candidate, "inspect_candidate", lambda: {"status": "SOURCE_MISMATCH"})
    with pytest.raises(RuntimeError, match="source"):
        selection.selected_profile()
    monkeypatch.setattr(candidate, "inspect_candidate", lambda: {"status": "PREPARED"})
    website_claim()
    assert selection.selected_profile() == expected
    assert historical.APPROVED_G4_PROTOCOL_PROFILE != expected


@pytest.mark.asyncio
async def test_candidate_without_operator_channel_refuses_before_engine_setup(monkeypatch):
    import scripts.run_stage4_golden_incident as runner

    monkeypatch.setenv("ATLASOPS_STAGE4_PROTOCOL", selection.WEBSITE_CANDIDATE)
    monkeypatch.setenv(
        "STAGE4_APPROVED_PROTOCOL_SHA256",
        historical.protocol_fingerprint(candidate.candidate_profile()),
    )
    monkeypatch.delenv("ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE", raising=False)
    monkeypatch.setattr(
        "scripts.qualify_integrated_inference.engine_from_environment",
        lambda: pytest.fail("model engine constructed before operator channel check"),
    )
    with pytest.raises(RuntimeError, match="operator channel"):
        await runner.main()


def test_selected_candidate_builder_and_runner_use_same_exact_profile(monkeypatch, website_claim):
    import scripts.run_stage4_golden_incident as runner

    expected = candidate.candidate_profile()
    monkeypatch.setenv("ATLASOPS_STAGE4_PROTOCOL", selection.WEBSITE_CANDIDATE)
    monkeypatch.setenv("STAGE4_APPROVED_PROTOCOL_SHA256", historical.protocol_fingerprint(expected))
    monkeypatch.setenv("ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE", "channel")
    website_claim()
    assert selection.observe_selected_profile(expected["model"], expected["metrics_api"]) == expected
    monkeypatch.setattr(runner, "_QUALIFIED_MODEL_IDENTITY", expected["model"])
    monkeypatch.setattr(runner, "_probe_metrics_server_contract", lambda: expected["metrics_api"])
    assert runner._observe_protocol_profile(expected["model"]["name"]) == expected
    assert runner.stage4_evidence_metadata()["protocol_marker"] == candidate.CANDIDATE_MARKER
    changed = copy.deepcopy(expected["model"])
    changed["arm"] = "sft"
    with pytest.raises(ValueError, match="prepared website protocol"):
        selection.observe_selected_profile(changed, expected["metrics_api"])


def test_candidate_marker_and_profile_survive_run_owned_preflight(monkeypatch, tmp_path, website_claim):
    import json

    import scripts.run_stage4_golden_incident as runner

    expected = candidate.candidate_profile()
    experiment = "EXP-STAGE4-SF002-019"
    source = "a" * 40
    monkeypatch.setenv("ATLASOPS_STAGE4_PROTOCOL", selection.WEBSITE_CANDIDATE)
    monkeypatch.setenv("STAGE4_APPROVED_PROTOCOL_SHA256", historical.protocol_fingerprint(expected))
    monkeypatch.setenv("ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE", "channel")
    monkeypatch.setenv("STAGE4_APPROVED_MAIN_SHA", source)
    website_claim()
    monkeypatch.setattr(runner, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(runner, "EXPERIMENT_ID", experiment)
    monkeypatch.setattr(runner, "_RUN_OWNED_PREFLIGHT_BINDING", None)
    chaos = {"success": True, "stdout": '{"items":[]}'}
    evidence = {
        "experiment_id": experiment, "scenario_id": runner.SCENARIO_ID,
        "protocol_marker": expected["protocol_marker"], "protocol_profile": expected,
        "source_identity": {
            "git_commit": source, "working_tree_clean": True,
            "protocol_fingerprint": historical.protocol_fingerprint(expected),
        },
        "phases": {
            "telemetry_readiness": {"ready": True},
            "baseline": {"baseline_healthy": True},
            "pre_reservation_chaos_check": {"verified_zero": True, "result": chaos},
        },
    }
    path = runner._persist_stage4_preflight_evidence(evidence, chaos)
    record = json.loads(Path(path).read_text())
    assert record["protocol_marker"] == candidate.CANDIDATE_MARKER
    binding = runner._RUN_OWNED_PREFLIGHT_BINDING
    assert binding["profile_sha256"] == historical.protocol_fingerprint(expected)
    runner._validate_run_owned_preflight(binding, binding["relative_path"], expected_sha=source)
    binding["protocol_marker"] = historical.G4_PROTOCOL_MARKER
    with pytest.raises(RuntimeError, match="identity"):
        runner._validate_run_owned_preflight(binding, binding["relative_path"], expected_sha=source)


def test_direct_candidate_invocation_without_claim_fails_and_claim_consumes_once(monkeypatch, website_claim):
    from config import g4_launch_authority as authority

    fingerprint = historical.protocol_fingerprint(candidate.candidate_profile())
    monkeypatch.setenv("ATLASOPS_STAGE4_PROTOCOL", selection.WEBSITE_CANDIDATE)
    monkeypatch.setenv("STAGE4_APPROVED_PROTOCOL_SHA256", fingerprint)
    monkeypatch.setenv("ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE", str(Path.cwd() / "arbitrary.json"))
    with pytest.raises(RuntimeError, match="authority claim"):
        selection.selected_profile()
    website_claim()
    authority.consume_candidate_launch(fingerprint)
    with pytest.raises(FileExistsError):
        authority.consume_candidate_launch(fingerprint)
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", "EXP-STAGE4-SF002-020")
    with pytest.raises(RuntimeError, match="authority claim"):
        selection.selected_profile()


def test_candidate_attempt_budget_is_one_without_changing_historical_limit(monkeypatch, tmp_path):
    import scripts.run_stage4_golden_incident as runner

    profile = candidate.candidate_profile()
    monkeypatch.setenv("ATLASOPS_STAGE4_PROTOCOL", selection.WEBSITE_CANDIDATE)
    monkeypatch.setattr(runner, "_current_main_sha", lambda expected_sha=None: expected_sha)
    monkeypatch.setattr(runner, "_observe_protocol_profile", lambda model: profile)
    monkeypatch.setattr(runner, "_claimed_attempts_for_protocol_fingerprint", lambda *a: 1)
    with pytest.raises(RuntimeError, match="1/1"):
        runner.reserve_experiment_attempt(
            "EXP-STAGE4-SF002-019", selected_model=profile["model"]["name"],
            main_sha="a" * 40, attempt_root=str(tmp_path),
        )
    assert runner.MAX_ATTEMPTS_PER_PROTOCOL_MARKER == 2
    assert not (tmp_path / "artifacts/evidence/stage4/.attempts/EXP-STAGE4-SF002-019.attempt.json").exists()
