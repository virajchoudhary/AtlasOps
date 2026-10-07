"""Prepared declaration only; never activates or executes a new experiment."""

import copy

import pytest

from config import g4_demo_candidate as candidate
from config import g4_protocol as active


def test_candidate_pins_current_source_without_activating_or_claiming_acceptance():
    before = copy.deepcopy(active.APPROVED_G4_PROTOCOL_PROFILE)
    report = candidate.inspect_candidate()
    assert report["status"] == "PREPARED"
    assert report["source_mismatches"] == []
    assert report["historical_profile_unchanged"]
    assert report["active"] is report["runtime_qualified"] is False
    assert report["launch_authorized_by_this_report"] is report["empirical_pass"] is False
    assert active.APPROVED_G4_PROTOCOL_PROFILE == before
    assert active.protocol_fingerprint(before) == candidate.HISTORICAL_PROFILE_SHA256


def test_model_fault_verifier_and_old_budget_contracts_are_preserved():
    profile = candidate.candidate_profile()
    old = active.APPROVED_G4_V38_SOURCE_GUARD_PROFILE
    for key in (
        "model", "integrated_inference", "f1_contract", "scenario_fault_contract",
        "approval_channel", "pre_t0_safety", "settling_deadline_policy", "role_tool_contract",
    ):
        assert profile[key] == old[key]
    assert profile["website_demo"]["maximum_website_launches"] == 1
    assert profile["protocol_marker"] != old["protocol_marker"]
    assert profile["diagnosis_prompt"]["sha256"] != old["diagnosis_prompt"]["sha256"]
    assert profile["causal_evidence_policy"]["source_sha256"]["bench/integrated_inference.py"] != (
        old["causal_evidence_policy"]["source_sha256"]["bench/integrated_inference.py"]
    )


def test_candidate_is_independent_and_missing_source_fails_closed(tmp_path):
    first = candidate.candidate_profile()
    first["model"]["name"] = "changed"
    assert candidate.candidate_profile()["model"]["name"] != "changed"
    report = candidate.inspect_candidate(tmp_path)
    assert report["status"] == "SOURCE_MISMATCH"
    assert report["source_mismatches"]
    assert report["active"] is False


def test_candidate_observation_builder_matches_only_its_exact_declared_runtime():
    expected = candidate.candidate_profile()
    observed = candidate.observe_candidate_profile(expected["model"], expected["metrics_api"])
    assert candidate.validate_candidate_profile(observed) == expected
    changed = copy.deepcopy(observed)
    changed["role_tool_contract"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="prepared website protocol"):
        candidate.validate_candidate_profile(changed)
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        active.validate_runtime_protocol_profile(observed)


@pytest.mark.parametrize("path", [
    "agents/tool_policy.py", "agents/tools/__init__.py",
    "bench/chaos_manifests/single_fault/sf-002.yaml",
])
def test_candidate_integrity_checks_inherited_acl_and_fault_inputs(monkeypatch, path):
    actual = candidate.file_sha256
    monkeypatch.setattr(candidate, "file_sha256", lambda file: (
        "0" * 64 if file == candidate.REPO_ROOT / path else actual(file)
    ))
    report = candidate.inspect_candidate()
    assert report["status"] == "SOURCE_MISMATCH"
    assert path in report["source_mismatches"]
