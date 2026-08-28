"""Fail-closed contracts for the explicitly declared G4 protocol profile."""

from __future__ import annotations

import json
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
    APPROVED_TOOL_CONTRACT_SHA256,
    build_runtime_protocol_profile,
    diagnosis_prompt_profile,
    expected_live_metrics_config_fingerprint,
    inspect_metrics_server_deployment,
    protocol_fingerprint,
    tool_contract_profile,
)


def test_approved_profile_pins_exact_model_and_digest():
    assert APPROVED_G4_PROTOCOL_PROFILE["model"] == {
        "provider": "ollama-local",
        "name": APPROVED_G4_MODEL,
        "digest": APPROVED_G4_MODEL_DIGEST,
    }


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
        "diagnosis_prompt",
        "role_tool_contract",
        "f1_contract",
        "scenario_fault_contract",
        "metrics_api",
    ):
        assert component in APPROVED_G4_PROTOCOL_PROFILE


def _approved_observation():
    return build_runtime_protocol_profile(
        selected_model=APPROVED_G4_MODEL,
        model_digest=APPROVED_G4_MODEL_DIGEST,
        metrics_observation=APPROVED_G4_PROTOCOL_PROFILE["metrics_api"],
    )


def test_runtime_builder_reproduces_explicitly_approved_profile():
    assert _approved_observation() == APPROVED_G4_PROTOCOL_PROFILE
    assert protocol.validate_runtime_protocol_profile(_approved_observation())


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


def test_metrics_server_missing_state_cannot_match_required_present_profile():
    observed = build_runtime_protocol_profile(
        selected_model=APPROVED_G4_MODEL,
        model_digest=APPROVED_G4_MODEL_DIGEST,
        metrics_observation={"state": "missing"},
    )
    assert observed != APPROVED_G4_PROTOCOL_PROFILE
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        protocol.validate_runtime_protocol_profile(observed)


def test_reservation_uses_live_identity_and_does_not_write_marker_on_mismatch():
    root = __import__("pathlib").Path(__file__).parent / "scratch" / "never-used-profile"
    with patch.object(runner, "_query_ollama_model_identity") as model_query, patch.object(
        runner, "_probe_metrics_server_contract"
    ) as metrics_probe:
        model_query.return_value = {
            "provider": "ollama-local",
            "name": APPROVED_G4_MODEL,
            "digest": "0" * 64,
        }
        metrics_probe.return_value = APPROVED_G4_PROTOCOL_PROFILE["metrics_api"]
        with pytest.raises(RuntimeError, match="approved protocol profile"):
            runner.reserve_experiment_attempt(
                "EXP-STAGE4-PROFILE-NEVER",
                selected_model=APPROVED_G4_MODEL,
                main_sha="test-sha",
                attempt_root=str(root),
            )
    model_query.assert_called_once_with(APPROVED_G4_MODEL)
    metrics_probe.assert_called_once()
    assert not (root / "artifacts" / "evidence" / "stage4" / ".attempts").exists()


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
            {"name": "qwen2.5:3b", "digest": "c" * 64},
            {"name": "qwen2.5:3b-instruct-v2", "digest": "d" * 64},
            {"name": "qwen2.5:7b-instruct", "digest": "e" * 64},
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


def test_observe_protocol_profile_with_corrected_ollama_identity():
    mock_resp = Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "models": [
            {
                "name": APPROVED_G4_MODEL,
                "digest": APPROVED_G4_MODEL_DIGEST,
            },
        ]
    }
    with patch("requests.get", return_value=mock_resp), patch.object(
        runner,
        "_probe_metrics_server_contract",
        return_value=APPROVED_G4_PROTOCOL_PROFILE["metrics_api"],
    ):
        observed = runner._observe_protocol_profile(APPROVED_G4_MODEL)
    assert observed == APPROVED_G4_PROTOCOL_PROFILE
