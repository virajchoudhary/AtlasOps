"""Fail-closed contracts for the explicitly declared G4 protocol profile."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

import agents.tools.argocd as argocd
import agents.tools.kubectl as kubectl

import config.g4_protocol as protocol
import scripts.run_stage4_golden_incident as runner
from config.g4_protocol import (
    APPROVED_DIAGNOSIS_PROMPT_SHA256,
    APPROVED_G4_MODEL,
    APPROVED_G4_MODEL_DIGEST,
    APPROVED_G4_PROTOCOL_PROFILE,
    APPROVED_G4_V2_MODEL,
    APPROVED_G4_V2_MODEL_DIGEST,
    APPROVED_G4_V2_PROTOCOL_PROFILE,
    APPROVED_G4_V2_TOOL_CONTRACT_SHA256,
    APPROVED_G4_V3_MODEL,
    APPROVED_G4_V3_MODEL_DIGEST,
    APPROVED_G4_V3_PROTOCOL_PROFILE,
    APPROVED_G4_V3_TOOL_CONTRACT_SHA256,
    APPROVED_G4_V31_MODEL,
    APPROVED_G4_V31_MODEL_DIGEST,
    APPROVED_G4_V31_PROTOCOL_PROFILE,
    APPROVED_G4_V31_TOOL_CONTRACT_SHA256,
    APPROVED_G4_V32_PROTOCOL_PROFILE,
    APPROVED_G4_V33_MODEL,
    APPROVED_G4_V33_MODEL_DIGEST,
    APPROVED_G4_V33_PROTOCOL_PROFILE,
    APPROVED_G4_V33_TOOL_CONTRACT_SHA256,
    APPROVED_G4_V34_PROTOCOL_PROFILE,
    APPROVED_G4_V35_AGENT_PROMPT_SHA256,
    APPROVED_G4_V35_CAUSAL_SOURCE_SHA256,
    APPROVED_G4_V35_PROTOCOL_PROFILE,
    APPROVED_G4_V36_CAUSAL_SOURCE_SHA256,
    APPROVED_G4_V36_PROTOCOL_PROFILE,
    APPROVED_G4_V36_SETTLING_DEADLINE,
    APPROVED_G4_V37_CAUSAL_SOURCE_SHA256,
    APPROVED_G4_V37_PROTOCOL_PROFILE,
    APPROVED_G4_V38_DIAGNOSTICS_PROFILE,
    APPROVED_G4_V38_LIFECYCLE_PROFILE,
    APPROVED_G4_V38_LIFECYCLE_SOURCE_SHA256,
    APPROVED_G4_V38_MODEL,
    APPROVED_G4_V38_PROTOCOL_PROFILE,
    APPROVED_TOOL_CONTRACT_SHA256,
    G4_V2_PROTOCOL_MARKER,
    G4_V3_PROTOCOL_MARKER,
    G4_V31_PROTOCOL_MARKER,
    G4_V33_PROTOCOL_MARKER,
    G4_V34_PROTOCOL_MARKER,
    G4_V35_PROTOCOL_MARKER,
    G4_V36_PROTOCOL_MARKER,
    G4_V37_PROTOCOL_MARKER,
    G4_V38_DIAGNOSTICS_PROFILE_VERSION,
    G4_V38_DIAGNOSTICS_PROTOCOL_MARKER,
    G4_V38_LIFECYCLE_PROFILE_VERSION,
    G4_V38_LIFECYCLE_PROTOCOL_MARKER,
    G4_V38_PROTOCOL_MARKER,
    build_runtime_protocol_profile,
    diagnosis_prompt_profile,
    expected_live_metrics_config_fingerprint,
    inspect_metrics_server_deployment,
    protocol_fingerprint,
    tool_contract_profile,
)


def test_active_v38_lifecycle_profile_pins_model_approval_causal_and_settling_contract():
    assert APPROVED_G4_PROTOCOL_PROFILE["model"] == APPROVED_G4_V38_MODEL
    assert APPROVED_G4_PROTOCOL_PROFILE["protocol_marker"] == (
        G4_V38_LIFECYCLE_PROTOCOL_MARKER
    )
    assert APPROVED_G4_PROTOCOL_PROFILE["profile_version"] == (
        G4_V38_LIFECYCLE_PROFILE_VERSION
    )
    assert APPROVED_G4_PROTOCOL_PROFILE["role_tool_contract"]["sha256"] == APPROVED_G4_V33_TOOL_CONTRACT_SHA256
    assert APPROVED_G4_PROTOCOL_PROFILE["llm_transport"] == {
        "request_timeout_seconds": 600,
        "max_attempts": 2,
        "base_backoff_seconds": 1.5,
    }
    assert APPROVED_G4_PROTOCOL_PROFILE["approval_channel"] == {
        "transport": "host-loopback-same-process",
        "timeout_seconds": 300,
        "authentication": "X-AtlasOps-Key",
        "restart": "fail-closed-memory-only",
    }
    assert APPROVED_G4_PROTOCOL_PROFILE["agent_prompt_sha256"] == (
        APPROVED_G4_V35_AGENT_PROMPT_SHA256
    )
    assert APPROVED_G4_PROTOCOL_PROFILE["causal_evidence_policy"]["source_sha256"] == (
        APPROVED_G4_V38_LIFECYCLE_SOURCE_SHA256
    )
    assert APPROVED_G4_PROTOCOL_PROFILE["settling_deadline_policy"] == (
        APPROVED_G4_V36_SETTLING_DEADLINE
    )
    assert APPROVED_G4_V38_LIFECYCLE_PROFILE == APPROVED_G4_PROTOCOL_PROFILE
    assert protocol_fingerprint(APPROVED_G4_V37_PROTOCOL_PROFILE) == (
        "ea72468a1237baf4c423552deb0911cd58ed3ba217b8074a36c1a94498223414"
    )
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        protocol.validate_runtime_protocol_profile(APPROVED_G4_V37_PROTOCOL_PROFILE)


def test_historical_v38_profile_fingerprint_remains_immutable_and_is_rejected_as_active():
    assert APPROVED_G4_V38_PROTOCOL_PROFILE["protocol_marker"] == G4_V38_PROTOCOL_MARKER
    assert protocol_fingerprint(APPROVED_G4_V38_PROTOCOL_PROFILE) == (
        "8f21f2b77981e040e63f0d12e78ecf336353aaf10a4fc109b16feaaef898c236"
    )
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        protocol.validate_runtime_protocol_profile(APPROVED_G4_V38_PROTOCOL_PROFILE)


def test_diagnostics_revision_preserves_v38_scientific_model_and_decoding_contracts():
    original = APPROVED_G4_V38_PROTOCOL_PROFILE
    revised = APPROVED_G4_V38_DIAGNOSTICS_PROFILE

    assert revised["model"] == original["model"]
    assert revised["integrated_inference"] == original["integrated_inference"]
    assert revised["f1_contract"] == original["f1_contract"]
    assert revised["scenario_fault_contract"] == original["scenario_fault_contract"]

    normalized = copy.deepcopy(revised)
    normalized["protocol_marker"] = original["protocol_marker"]
    normalized["profile_version"] = original["profile_version"]
    normalized["diagnosis_prompt"]["version"] = original["diagnosis_prompt"]["version"]
    normalized["causal_evidence_policy"]["source_sha256"] = (
        original["causal_evidence_policy"]["source_sha256"]
    )
    assert normalized == original

    original_sources = original["causal_evidence_policy"]["source_sha256"]
    revised_sources = revised["causal_evidence_policy"]["source_sha256"]
    assert set(revised_sources) - set(original_sources) == {
        "bench/integrated_inference.py",
        "scripts/qualify_loopback_inference.py",
    }
    assert revised_sources["bench/integrated_inference_remote.py"] != (
        original_sources["bench/integrated_inference_remote.py"]
    )
    for path, digest in original_sources.items():
        if path not in {
            "bench/integrated_inference_remote.py",
            "scripts/qualify_integrated_inference.py",
        }:
            assert revised_sources[path] == digest


def test_lifecycle_revision_changes_only_version_labels_and_bridge_source_pin():
    original = APPROVED_G4_V38_DIAGNOSTICS_PROFILE
    assert protocol_fingerprint(original) == (
        "7f6131b953e0195b7681336f84e3d6b8c5ed592bc41303c523e4260a94918805"
    )
    revised = copy.deepcopy(APPROVED_G4_V38_LIFECYCLE_PROFILE)
    revised["protocol_marker"] = original["protocol_marker"]
    revised["profile_version"] = original["profile_version"]
    revised["diagnosis_prompt"]["version"] = original["diagnosis_prompt"]["version"]
    revised["causal_evidence_policy"]["source_sha256"]["bench/integrated_inference_remote.py"] = (
        original["causal_evidence_policy"]["source_sha256"]["bench/integrated_inference_remote.py"]
    )
    assert revised == original
    assert original["protocol_marker"] == G4_V38_DIAGNOSTICS_PROTOCOL_MARKER
    assert original["profile_version"] == G4_V38_DIAGNOSTICS_PROFILE_VERSION
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        protocol.validate_runtime_protocol_profile(original)


def test_historical_v36_profile_remains_exact_and_is_rejected_as_active():
    assert APPROVED_G4_V36_PROTOCOL_PROFILE["protocol_marker"] == G4_V36_PROTOCOL_MARKER
    assert APPROVED_G4_V36_CAUSAL_SOURCE_SHA256["agents/coordinator.py"] == (
        "5d7be471592526fbf519fba571c2a1d2ccfd1d976f027736d00f2b3b39aadd0c"
    )
    assert APPROVED_G4_V37_CAUSAL_SOURCE_SHA256.keys() == (
        APPROVED_G4_V36_CAUSAL_SOURCE_SHA256.keys()
    )
    assert {
        path: digest
        for path, digest in APPROVED_G4_V37_CAUSAL_SOURCE_SHA256.items()
        if path != "agents/coordinator.py"
    } == {
        path: digest
        for path, digest in APPROVED_G4_V36_CAUSAL_SOURCE_SHA256.items()
        if path != "agents/coordinator.py"
    }
    assert protocol_fingerprint(APPROVED_G4_V36_PROTOCOL_PROFILE) == (
        "eedcc9e09d30700c7d53d206398bf32ff8d7dee10ce4c90a96d6c60686a9b97a"
    )
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        protocol.validate_runtime_protocol_profile(APPROVED_G4_V36_PROTOCOL_PROFILE)


def test_historical_v35_profile_remains_exact_and_immutable():
    assert APPROVED_G4_V35_PROTOCOL_PROFILE["protocol_marker"] == G4_V35_PROTOCOL_MARKER
    assert APPROVED_G4_V35_PROTOCOL_PROFILE["causal_evidence_policy"]["source_sha256"] == (
        APPROVED_G4_V35_CAUSAL_SOURCE_SHA256
    )
    assert "settling_deadline_policy" not in APPROVED_G4_V35_PROTOCOL_PROFILE
    assert protocol_fingerprint(APPROVED_G4_V35_PROTOCOL_PROFILE) == (
        "01467df37336b2cd3fb9c93148c782a1539c358f09d6f7e68c29ab7ca6fe7cd9"
    )


def test_historical_v34_profile_remains_exact_and_immutable():
    assert APPROVED_G4_V34_PROTOCOL_PROFILE["protocol_marker"] == G4_V34_PROTOCOL_MARKER
    assert "causal_evidence_policy" not in APPROVED_G4_V34_PROTOCOL_PROFILE
    assert protocol_fingerprint(APPROVED_G4_V34_PROTOCOL_PROFILE) == (
        "885349a5083509d43ef5366d6157367e33a986fb0022c4c71eb8c4fa02ea10a3"
    )


def test_historical_v33_profile_remains_exact_and_immutable():
    assert APPROVED_G4_V33_PROTOCOL_PROFILE["protocol_marker"] == G4_V33_PROTOCOL_MARKER
    assert "approval_channel" not in APPROVED_G4_V33_PROTOCOL_PROFILE
    assert protocol_fingerprint(APPROVED_G4_V33_PROTOCOL_PROFILE) == "1d9694f79bcba99ac9c8a0b67e66d0e6b66165aa88b47bb311986362ed7943fe"


def test_historical_v31_profile_remains_exact_and_immutable():
    assert APPROVED_G4_V31_PROTOCOL_PROFILE["model"] == {
        "provider": "ollama-local",
        "name": APPROVED_G4_V31_MODEL,
        "digest": APPROVED_G4_V31_MODEL_DIGEST,
    }
    assert APPROVED_G4_V31_PROTOCOL_PROFILE["protocol_marker"] == G4_V31_PROTOCOL_MARKER
    assert APPROVED_G4_V31_PROTOCOL_PROFILE["role_tool_contract"]["sha256"] == APPROVED_G4_V31_TOOL_CONTRACT_SHA256
    assert protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE) == "94758f5b7a24242f8fbb00f89b5cd0d5aec23d95dc02f6f0202442791726b561"


def test_historical_v3_profile_remains_exact_and_immutable():
    assert APPROVED_G4_V3_PROTOCOL_PROFILE["model"] == {
        "provider": "ollama-local",
        "name": APPROVED_G4_V3_MODEL,
        "digest": APPROVED_G4_V3_MODEL_DIGEST,
    }
    assert APPROVED_G4_V3_PROTOCOL_PROFILE["protocol_marker"] == G4_V3_PROTOCOL_MARKER
    assert APPROVED_G4_V3_PROTOCOL_PROFILE["role_tool_contract"]["sha256"] == APPROVED_G4_V3_TOOL_CONTRACT_SHA256
    assert protocol_fingerprint(APPROVED_G4_V3_PROTOCOL_PROFILE) == "02ff4b95df55f3031d4e06d161f8b80393a6a508064c9b6172ffc4a205a210e0"


def test_historical_v2_profile_remains_exact_and_immutable():
    assert APPROVED_G4_V2_PROTOCOL_PROFILE["model"] == {
        "provider": "ollama-local",
        "name": APPROVED_G4_V2_MODEL,
        "digest": APPROVED_G4_V2_MODEL_DIGEST,
    }
    assert APPROVED_G4_V2_PROTOCOL_PROFILE["protocol_marker"] == G4_V2_PROTOCOL_MARKER
    assert APPROVED_G4_V2_PROTOCOL_PROFILE["role_tool_contract"]["sha256"] == APPROVED_G4_V2_TOOL_CONTRACT_SHA256
    assert protocol_fingerprint(APPROVED_G4_V2_PROTOCOL_PROFILE) == "f4ddad6d4a0c26f6c0b124693d9cfa59aad33a3acc795068a3d6d604382672d3"


def test_v32_fingerprint_differs_from_v31_v3_and_v2():
    v2_fp = protocol_fingerprint(APPROVED_G4_V2_PROTOCOL_PROFILE)
    v3_fp = protocol_fingerprint(APPROVED_G4_V3_PROTOCOL_PROFILE)
    v31_fp = protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE)
    v32_fp = protocol_fingerprint(APPROVED_G4_V32_PROTOCOL_PROFILE)
    assert v32_fp != v31_fp
    assert v32_fp != v3_fp
    assert v32_fp != v2_fp
    assert v2_fp == "f4ddad6d4a0c26f6c0b124693d9cfa59aad33a3acc795068a3d6d604382672d3"
    assert v3_fp == "02ff4b95df55f3031d4e06d161f8b80393a6a508064c9b6172ffc4a205a210e0"
    assert v31_fp == "94758f5b7a24242f8fbb00f89b5cd0d5aec23d95dc02f6f0202442791726b561"


def test_v32_accounting_sees_zero_claimed_attempts_with_synthetic_historical_attempts(tmp_path):
    v2_fp = protocol_fingerprint(APPROVED_G4_V2_PROTOCOL_PROFILE)
    v3_fp = protocol_fingerprint(APPROVED_G4_V3_PROTOCOL_PROFILE)
    v31_fp = protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE)
    v32_fp = protocol_fingerprint(APPROVED_G4_V32_PROTOCOL_PROFILE)

    attempts_dir = tmp_path / "artifacts" / "evidence" / "stage4" / ".attempts"
    attempts_dir.mkdir(parents=True)

    record_001 = {
        "experiment_id": "EXP-STAGE4-SYNTH-001",
        "state": runner.ATTEMPT_STATE_CONSUMED,
        "protocol_marker": G4_V2_PROTOCOL_MARKER,
        "protocol_fingerprint": v2_fp,
    }
    record_002 = {
        "experiment_id": "EXP-STAGE4-SYNTH-002",
        "state": runner.ATTEMPT_STATE_COMPLETED,
        "protocol_marker": G4_V2_PROTOCOL_MARKER,
        "protocol_fingerprint": v2_fp,
    }
    record_003 = {
        "experiment_id": "EXP-STAGE4-SYNTH-003",
        "state": runner.ATTEMPT_STATE_CONSUMED,
        "protocol_marker": G4_V3_PROTOCOL_MARKER,
        "protocol_fingerprint": v3_fp,
    }
    record_004 = {
        "experiment_id": "EXP-STAGE4-SYNTH-004",
        "state": runner.ATTEMPT_STATE_CONSUMED,
        "protocol_marker": G4_V31_PROTOCOL_MARKER,
        "protocol_fingerprint": v31_fp,
    }
    (attempts_dir / "EXP-STAGE4-SYNTH-001.attempt.json").write_text(
        json.dumps(record_001), encoding="utf-8"
    )
    (attempts_dir / "EXP-STAGE4-SYNTH-002.attempt.json").write_text(
        json.dumps(record_002), encoding="utf-8"
    )
    (attempts_dir / "EXP-STAGE4-SYNTH-003.attempt.json").write_text(
        json.dumps(record_003), encoding="utf-8"
    )
    (attempts_dir / "EXP-STAGE4-SYNTH-004.attempt.json").write_text(
        json.dumps(record_004), encoding="utf-8"
    )

    assert runner._claimed_attempts_for_protocol_fingerprint(v2_fp, attempt_root=str(tmp_path)) == 2
    assert runner._claimed_attempts_for_protocol_fingerprint(v3_fp, attempt_root=str(tmp_path)) == 1
    assert runner._claimed_attempts_for_protocol_fingerprint(v31_fp, attempt_root=str(tmp_path)) == 1
    assert runner._claimed_attempts_for_protocol_fingerprint(v32_fp, attempt_root=str(tmp_path)) == 0


def test_declared_prompt_and_tool_hashes_match_current_contract():
    assert diagnosis_prompt_profile()["sha256"] == APPROVED_DIAGNOSIS_PROMPT_SHA256
    assert tool_contract_profile()["sha256"] == APPROVED_TOOL_CONTRACT_SHA256


def test_changed_response_taxonomies_change_the_tool_contract():
    base = tool_contract_profile()
    argocd_actual = argocd.response_contract_profile()
    kubectl_actual = kubectl.response_contract_profile()

    with patch.object(
        argocd,
        "response_contract_profile",
        return_value={**argocd_actual, "version": "drift"},
    ):
        argocd_drift = tool_contract_profile()
    with patch.object(
        kubectl,
        "response_contract_profile",
        return_value={**kubectl_actual, "version": "drift"},
    ):
        kubectl_drift = tool_contract_profile()

    assert argocd_drift["sha256"] != base["sha256"]
    assert kubectl_drift["sha256"] != base["sha256"]


def test_declared_argocd_response_contract_matches_runtime_taxonomy():
    profile = argocd.response_contract_profile()
    timeout = argocd._classify_api_failure(requests.exceptions.Timeout())
    assert timeout == {"success": False, **profile["transport_errors"]["timeout"]}

    connection = argocd._classify_api_failure(requests.exceptions.ConnectionError())
    assert connection == {
        "success": False,
        **profile["transport_errors"]["connection_failed"],
    }

    for status, error_class in profile["http_status_classes"].items():
        response = Mock()
        response.status_code = int(status)
        failure = requests.HTTPError("hidden body")
        failure.response = response
        result = argocd._classify_api_failure(failure)
        assert result == {
            "success": False,
            "error": f"argocd_{error_class} (HTTP {status})",
            "error_class": error_class,
            "status_code": int(status),
        }


def test_declared_kubectl_top_response_contract_matches_runtime_taxonomy():
    profile = kubectl.response_contract_profile()
    failing = {
        "success": True,
        "stderr": "Error: Metrics API Not Available",
        "returncode": 1,
    }
    result = kubectl._classify_metrics_api_result(failing)
    assert result["success"] is False
    assert result["stderr"] == failing["stderr"]
    assert result["error"] == profile["unavailable_result"]["error"]
    assert result["error_class"] == profile["unavailable_result"]["error_class"]


def test_file_hashes_are_independent_of_windows_or_posix_newlines(tmp_path):
    text = b"model-visible protocol contract\nline two"
    posix_copy = tmp_path / "posix.txt"
    windows_copy = tmp_path / "windows.txt"
    posix_copy.write_bytes(text)
    windows_copy.write_bytes(text.replace(b"\n", b"\r\n"))

    expected = protocol.file_sha256(posix_copy)
    assert protocol.file_sha256(windows_copy) == expected

    changed = tmp_path / "changed.txt"
    changed.write_bytes(text + b"\nchanged")
    assert protocol.file_sha256(changed) != expected


def test_fingerprint_is_deterministic_and_covers_all_components():
    first = protocol_fingerprint(APPROVED_G4_PROTOCOL_PROFILE)
    second = protocol_fingerprint(APPROVED_G4_PROTOCOL_PROFILE)
    assert first == second
    assert len(first) == 64
    for component in (
        "model",
        "approval_channel",
        "pre_t0_safety",
        "agent_prompt_sha256",
        "causal_evidence_policy",
        "diagnosis_prompt",
        "role_tool_contract",
        "f1_contract",
        "scenario_fault_contract",
        "metrics_api",
    ):
        assert component in APPROVED_G4_PROTOCOL_PROFILE
    assert APPROVED_G4_PROTOCOL_PROFILE["pre_t0_safety"] == {
        "zero_chaos_before_reservation": True,
        "zero_chaos_rechecked_before_apply": True,
        "attempt_consumption": "durable-before-apply",
        "interrupted_pre_apply_consumed": "preserve-and-record-prefault",
        "kubectl_context": "explicit-per-command-no-global-switch",
    }


def test_v34_attempt_transition_terms_are_required_by_fingerprint(
    frozen_g4_v38_diagnostics_source_hashes,
):
    observed = _approved_observation()
    observed["pre_t0_safety"].pop("attempt_consumption")

    assert protocol_fingerprint(observed) != protocol_fingerprint(
        APPROVED_G4_PROTOCOL_PROFILE
    )
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        protocol.validate_runtime_protocol_profile(observed)


@pytest.mark.parametrize(
    "changed_name",
    [
        "grounding.py",
        "chaos.py",
        "coordinator.py",
        "run_stage4_golden_incident.py",
        "remediation.md",
    ],
)
def test_v35_rejects_causal_source_or_prompt_drift(
    monkeypatch, changed_name, frozen_g4_v38_diagnostics_source_hashes
):
    original_hash = protocol.file_sha256
    monkeypatch.setattr(
        protocol,
        "file_sha256",
        lambda path: "0" * 64 if path.name == changed_name else original_hash(path),
    )
    observed = _approved_observation()

    assert observed != APPROVED_G4_PROTOCOL_PROFILE
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        protocol.validate_runtime_protocol_profile(observed)


@pytest.fixture
def frozen_g4_v38_diagnostics_source_hashes(monkeypatch):
    original_file_sha256 = protocol.file_sha256

    def file_sha256_with_frozen_sources(path):
        try:
            relative_path = Path(path).resolve().relative_to(protocol.REPO_ROOT).as_posix()
        except ValueError:
            return original_file_sha256(path)
        if relative_path in APPROVED_G4_V38_LIFECYCLE_SOURCE_SHA256:
            return APPROVED_G4_V38_LIFECYCLE_SOURCE_SHA256[relative_path]
        return original_file_sha256(path)

    monkeypatch.setattr(protocol, "file_sha256", file_sha256_with_frozen_sources)


def _approved_observation():
    return protocol.build_integrated_protocol_profile(
        model_identity=dict(APPROVED_G4_V38_MODEL),
        metrics_observation=APPROVED_G4_PROTOCOL_PROFILE["metrics_api"],
    )


def test_current_coordinator_source_matches_v37_binding():
    coordinator_path = protocol.REPO_ROOT / "agents" / "coordinator.py"
    current_source_hash = protocol.file_sha256(coordinator_path)
    approved_source_hash = APPROVED_G4_V37_CAUSAL_SOURCE_SHA256[
        "agents/coordinator.py"
    ]
    assert current_source_hash == approved_source_hash

    observed = _approved_observation()
    assert (
        observed["causal_evidence_policy"]["source_sha256"]["agents/coordinator.py"]
        == approved_source_hash
    )
    assert protocol.validate_runtime_protocol_profile(observed) == APPROVED_G4_PROTOCOL_PROFILE


def test_runtime_builder_reproduces_explicitly_approved_profile(
    frozen_g4_v38_diagnostics_source_hashes,
):
    assert _approved_observation() == APPROVED_G4_PROTOCOL_PROFILE
    assert protocol.validate_runtime_protocol_profile(_approved_observation())


def test_stage4_approval_timeout_drift_fails_protocol_qualification(
    monkeypatch, frozen_g4_v38_diagnostics_source_hashes
):
    from agents.approval import approval_gate

    monkeypatch.setattr(approval_gate, "timeout_seconds", 2)
    observed = _approved_observation()
    assert observed["approval_channel"]["timeout_seconds"] == 2
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        protocol.validate_runtime_protocol_profile(observed)


def test_invalid_model_digest_is_rejected_before_profile_comparison():
    with pytest.raises(RuntimeError, match="valid SHA-256 digest"):
        build_runtime_protocol_profile(
            selected_model=APPROVED_G4_MODEL,
            model_digest="not-a-digest",
            metrics_observation=APPROVED_G4_PROTOCOL_PROFILE["metrics_api"],
        )


def _deployment_result(image: str = protocol.METRICS_SERVER_IMAGE):
    payload = {
        "metadata": {"name": "metrics-server", "namespace": "kube-system"},
        "spec": {"template": {"spec": {
            "serviceAccountName": "metrics-server",
            "priorityClassName": "system-cluster-critical",
            "containers": [{
                "name": "metrics-server",
                "image": image,
                "args": list(protocol.REQUIRED_METRICS_SERVER_ARGS),
                "ports": [{"containerPort": 10250, "name": "https", "protocol": "TCP"}],
                "resources": {"requests": {"cpu": "100m", "memory": "200Mi"}},
            }],
        }}},
    }
    return {"success": True, "stdout": json.dumps(payload)}


def test_pinned_metrics_server_deployment_matches_expected_fingerprint():
    observation = inspect_metrics_server_deployment(lambda _args: _deployment_result())
    assert observation["live_config_sha256"] == expected_live_metrics_config_fingerprint()


@pytest.mark.parametrize("image", ["registry.example.invalid/metrics-server:v0.7.2"])
def test_metrics_server_image_drift_is_rejected_fail_closed(image):
    with pytest.raises(RuntimeError, match="provenance mismatch: image"):
        inspect_metrics_server_deployment(lambda _args: _deployment_result(image=image))


def test_metrics_server_missing_state_cannot_match_required_present_profile(
    frozen_g4_v38_diagnostics_source_hashes,
):
    observed = protocol.build_integrated_protocol_profile(
        model_identity=dict(APPROVED_G4_V38_MODEL),
        metrics_observation={"state": "missing"},
    )
    assert observed != APPROVED_G4_PROTOCOL_PROFILE
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        protocol.validate_runtime_protocol_profile(observed)


def test_reservation_uses_live_identity_and_does_not_write_marker_on_mismatch(
    frozen_g4_v38_diagnostics_source_hashes,
):
    root = __import__("pathlib").Path(__file__).parent / "scratch" / "never-used-profile"
    with patch.object(
        runner, "_current_main_sha", return_value="test-sha"
    ), patch.object(
        runner, "_QUALIFIED_MODEL_IDENTITY", {}
    ) as model_query, patch.object(
        runner, "_probe_metrics_server_contract"
    ) as metrics_probe:
        model_query.update({**APPROVED_G4_V38_MODEL, "revision": "0" * 40})
        metrics_probe.return_value = APPROVED_G4_PROTOCOL_PROFILE["metrics_api"]
        with pytest.raises(RuntimeError, match="approved protocol profile"):
            runner.reserve_experiment_attempt(
                "EXP-STAGE4-PROFILE-NEVER",
                selected_model=APPROVED_G4_MODEL,
                main_sha="test-sha",
                attempt_root=str(root),
            )
    metrics_probe.assert_called_once()
    assert not (root / "artifacts" / "evidence" / "stage4" / ".attempts").exists()


def test_coordinator_source_hash_drift_is_rejected_before_reservation(
    monkeypatch, frozen_g4_v38_diagnostics_source_hashes, tmp_path
):
    original_hash = protocol.file_sha256
    monkeypatch.setattr(
        protocol,
        "file_sha256",
        lambda path: "0" * 64
        if Path(path).name == "coordinator.py"
        else original_hash(path),
    )
    with patch.object(runner, "_current_main_sha", return_value="test-sha"), patch.object(
        runner,
        "_QUALIFIED_MODEL_IDENTITY",
        dict(APPROVED_G4_V38_MODEL),
    ), patch.object(
        runner,
        "_probe_metrics_server_contract",
        return_value=APPROVED_G4_PROTOCOL_PROFILE["metrics_api"],
    ):
        with pytest.raises(RuntimeError, match="approved protocol profile"):
            runner.reserve_experiment_attempt(
                "EXP-STAGE4-COORDINATOR-DRIFT-NEVER",
                selected_model=APPROVED_G4_MODEL,
                main_sha="test-sha",
                attempt_root=str(tmp_path),
            )
    assert not (
        tmp_path / "artifacts" / "evidence" / "stage4" / ".attempts"
    ).exists()


def test_ollama_identity_exact_match_returns_normalized_digest():
    mock_resp = Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "models": [
            {"name": "qwen2.5:1.5b", "digest": "sha256:" + "a" * 64},
            {
                "name": APPROVED_G4_MODEL,
                "digest": "sha256:" + APPROVED_G4_MODEL_DIGEST.upper(),
            },
        ]
    }
    with patch("requests.get", return_value=mock_resp) as mock_get:
        identity = runner._query_ollama_model_identity(APPROVED_G4_MODEL)
    mock_get.assert_called_once_with("http://localhost:11434/api/tags", timeout=10)
    assert identity == {
        "provider": "ollama-local",
        "name": APPROVED_G4_MODEL,
        "digest": APPROVED_G4_MODEL_DIGEST.lower(),
    }


def test_ollama_identity_missing_model_fails_closed():
    mock_resp = Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "models": [
            {"name": "other-model:latest", "digest": "b" * 64},
        ]
    }
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="not installed in local Ollama"):
            runner._query_ollama_model_identity(APPROVED_G4_MODEL)


def test_ollama_identity_similarly_named_model_does_not_match():
    mock_resp = Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "models": [
            {"name": "qwen2.5:7b", "digest": "c" * 64},
            {"name": "qwen2.5:7b-instruct-v2", "digest": "d" * 64},
            {"name": "qwen2.5:14b-instruct", "digest": "e" * 64},
        ]
    }
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="not installed in local Ollama"):
            runner._query_ollama_model_identity(APPROVED_G4_MODEL)


@pytest.mark.parametrize(
    "invalid_digest",
    [
        "",
        "not-a-sha256",
        "12345",
        "g" * 64,
        "0" * 63,
        "0" * 65,
    ],
)
def test_ollama_identity_malformed_digest_fails_closed(invalid_digest):
    mock_resp = Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "models": [
            {"name": APPROVED_G4_MODEL, "digest": invalid_digest},
        ]
    }
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="(has no digest|has invalid SHA-256 digest)"):
            runner._query_ollama_model_identity(APPROVED_G4_MODEL)


@pytest.mark.parametrize(
    "bad_payload",
    [
        None,
        [],
        "not-json",
        {"models": "not-a-list"},
        {"not_models": []},
    ],
)
def test_ollama_identity_invalid_payload_shape_fails_closed(bad_payload):
    mock_resp = Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = bad_payload
    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="Unable to verify Stage 4 model identity"):
            runner._query_ollama_model_identity(APPROVED_G4_MODEL)


def test_ollama_identity_transport_failure_fails_closed():
    with patch("requests.get", side_effect=requests.ConnectionError("offline")):
        with pytest.raises(RuntimeError, match="Unable to verify Stage 4 model identity"):
            runner._query_ollama_model_identity(APPROVED_G4_MODEL)


def test_observe_protocol_profile_requires_qualified_integrated_identity(
    frozen_g4_v38_diagnostics_source_hashes, monkeypatch,
):
    monkeypatch.setattr(runner, "_QUALIFIED_MODEL_IDENTITY", dict(APPROVED_G4_V38_MODEL))
    with patch.object(
        runner,
        "_probe_metrics_server_contract",
        return_value=APPROVED_G4_PROTOCOL_PROFILE["metrics_api"],
    ):
        observed = runner._observe_protocol_profile(APPROVED_G4_MODEL)
    assert observed == APPROVED_G4_PROTOCOL_PROFILE
