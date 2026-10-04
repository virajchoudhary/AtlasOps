"""Deterministic tests for G4 v3.1 transport timing and post-T0 exception safety."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agents.approval import approval_gate
import agents.coordinator as coordinator
from agents._http_retry import post_with_retry
import config.g4_protocol as protocol
from config.g4_protocol import (
    APPROVED_G4_MODEL,
    APPROVED_G4_PROTOCOL_PROFILE,
    APPROVED_G4_V31_PROTOCOL_PROFILE,
    APPROVED_G4_V32_PROTOCOL_PROFILE,
    APPROVED_G4_V32_TOOL_CONTRACT_SHA256,
    APPROVED_G4_V38_DIAGNOSTICS_SOURCE_SHA256,
    protocol_fingerprint,
)
import scripts.run_stage4_golden_incident as runner


@pytest.fixture(autouse=True)
def isolated_protocol_runtime(monkeypatch):
    original_file_sha256 = protocol.file_sha256

    def file_sha256_with_frozen_sources(path):
        try:
            relative_path = Path(path).resolve().relative_to(protocol.REPO_ROOT).as_posix()
        except ValueError:
            return original_file_sha256(path)
        if relative_path in APPROVED_G4_V38_DIAGNOSTICS_SOURCE_SHA256:
            return APPROVED_G4_V38_DIAGNOSTICS_SOURCE_SHA256[relative_path]
        return original_file_sha256(path)

    # Reservation tests model the declared diagnostics profile, not the source tree.
    monkeypatch.setattr(protocol, "file_sha256", file_sha256_with_frozen_sources)
    monkeypatch.setattr(
        runner,
        "_QUALIFIED_MODEL_IDENTITY",
        dict(APPROVED_G4_PROTOCOL_PROFILE["model"]),
    )
    monkeypatch.setattr(
        runner,
        "_probe_metrics_server_contract",
        lambda: APPROVED_G4_PROTOCOL_PROFILE["metrics_api"],
    )
    monkeypatch.setattr(
        runner,
        "_current_main_sha",
        lambda expected_sha=None: expected_sha,
    )


def test_coordinator_declares_v32_transport_constants():
    assert coordinator.LLM_REQUEST_TIMEOUT_SECONDS == 600.0
    assert coordinator.LLM_MAX_ATTEMPTS == 2
    assert coordinator.LLM_BASE_BACKOFF_SECONDS == 1.5


def test_v32_profile_declares_llm_transport_matching_coordinator():
    transport = protocol.llm_transport_profile()
    assert transport == {
        "request_timeout_seconds": 600,
        "max_attempts": 2,
        "base_backoff_seconds": 1.5,
    }
    assert APPROVED_G4_V32_PROTOCOL_PROFILE["llm_transport"] == transport
    assert APPROVED_G4_PROTOCOL_PROFILE["llm_transport"] == transport


def test_historical_v31_profile_preserves_300s_transport():
    assert APPROVED_G4_V31_PROTOCOL_PROFILE["llm_transport"] == {
        "request_timeout_seconds": 300,
        "max_attempts": 2,
        "base_backoff_seconds": 1.5,
    }


def test_v32_preserves_7b_model_and_tool_contract():
    assert APPROVED_G4_V32_PROTOCOL_PROFILE["model"]["name"] == "qwen2.5:7b-instruct"
    assert APPROVED_G4_V32_PROTOCOL_PROFILE["model"]["digest"] == "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
    assert APPROVED_G4_V32_PROTOCOL_PROFILE["role_tool_contract"]["sha256"] == APPROVED_G4_V32_TOOL_CONTRACT_SHA256
    assert APPROVED_G4_V32_PROTOCOL_PROFILE["role_tool_contract"]["sha256"] == APPROVED_G4_V31_PROTOCOL_PROFILE["role_tool_contract"]["sha256"]


def test_v31_fingerprint_immutable_and_differs_from_v32():
    v31_fp = protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE)
    v32_fp = protocol_fingerprint(APPROVED_G4_V32_PROTOCOL_PROFILE)
    assert v31_fp == "94758f5b7a24242f8fbb00f89b5cd0d5aec23d95dc02f6f0202442791726b561"
    assert v32_fp != v31_fp


@pytest.mark.asyncio
async def test_post_with_retry_respects_max_attempts_two():
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ReadTimeout("timed out after 300s")

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(httpx.ReadTimeout):
            await post_with_retry(
                mock_client,
                "http://localhost:11434/v1/chat/completions",
                {"messages": []},
                context="test/turn-0",
                max_attempts=2,
                base_backoff=1.5,
            )
    assert mock_client.post.call_count == 2
    mock_sleep.assert_awaited_once_with(1.5)


@pytest.mark.asyncio
async def test_call_agent_passes_transport_parameters(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "synthetic-transport-audit-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": '{"severity": "P1", "service": "paymentservice"}', "tool_calls": []}}]
    }

    with patch("agents.coordinator.post_with_retry", new_callable=AsyncMock) as mock_pwr:
        mock_pwr.return_value = mock_response
        result = await coordinator.call_agent("triage", {"incident_id": "test-inc", "alert": {}})

        assert mock_pwr.call_count == 1
        _, kwargs = mock_pwr.call_args
        assert kwargs.get("max_attempts") == 2
        assert kwargs.get("base_backoff") == 1.5
        assert result.get("final", {}).get("severity") == "P1"


# Case A: 011-style early post-T0 ReadTimeout (no primary verdict, verifier not completed)
def test_post_t0_interruption_case_a_early_timeout_inconclusive(tmp_path):
    evidence_dir = tmp_path / "artifacts" / "evidence" / "stage4"
    attempts_dir = evidence_dir / ".attempts"
    attempts_dir.mkdir(parents=True)

    experiment_id = "EXP-STAGE4-TEST-CASE-A"
    attempt_file = attempts_dir / f"{experiment_id}.attempt.json"
    reservation = {
        "experiment_id": experiment_id,
        "state": runner.ATTEMPT_STATE_CONSUMED,
        "reservation_token": "token-123",
        "reserved_at": "2026-08-29T10:00:00+00:00",
        "consumed_at": "2026-08-29T10:00:05+00:00",
        "protocol_marker": protocol.G4_V31_PROTOCOL_MARKER,
        "protocol_fingerprint": protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE),
        "main_sha": "abc1234",
    }
    attempt_file.write_text(json.dumps(reservation), encoding="utf-8")

    evidence = {"experiment_id": experiment_id, "attempt_state": runner.ATTEMPT_STATE_CONSUMED}
    timeout_exc = httpx.ReadTimeout("ReadTimeout during triage turn-0")

    fake_chaos_yaml = "apiVersion: chaos-mesh.org/v1alpha1\nkind: StressChaos\nmetadata:\n  name: sf-002-paymentservice-cpu\n"

    def mock_run_kubectl(cmd):
        if cmd[0] == "get":
            if len(cmd) > 1 and cmd[1] == runner.CHAOS_RESOURCE_KINDS:
                return {"success": True, "stdout": json.dumps({"items": []}), "returncode": 0}
            return {"success": True, "stdout": fake_chaos_yaml, "returncode": 0}
        elif cmd[0] == "delete":
            return {"success": True, "stdout": "deleted", "returncode": 0}
        return {"success": True}

    with patch.object(runner, "run_kubectl", side_effect=mock_run_kubectl), patch.object(
        runner, "REPO_ROOT", str(tmp_path)
    ):
        runner._handle_post_t0_interruption(
            reservation=reservation,
            evidence=evidence,
            exc=timeout_exc,
            fault_observable=True,
            evidence_dir=str(evidence_dir),
        )

    # Interruption sidecar assertions
    intr_file = evidence_dir / f"{experiment_id}.interruption.json"
    assert intr_file.exists()
    intr = json.loads(intr_file.read_text(encoding="utf-8"))

    assert intr["classification"] == "MODEL_TIMEOUT_INTERRUPTION"
    assert intr["scientific_status"] == "INCONCLUSIVE"
    assert intr["attempt_state"] == runner.ATTEMPT_STATE_CONSUMED
    assert intr["gate_g4_pass"] is None
    assert intr["env_resolved"] is None
    assert intr["verifier_completed"] is False
    assert intr["model_capability_failure"] is False
    assert intr["primary_verdict_authoritative"] is False
    assert intr["t0_crossed"] is True
    assert intr["fault_observed_in_cluster"] is True
    assert "attempt_json" in intr["evidence_hashes"]
    assert "leftover_chaos_yaml" in intr["evidence_hashes"]

    # Leftover chaos YAML export assertions
    chaos_file = evidence_dir / f"{experiment_id}.leftover-chaos.yaml"
    assert chaos_file.exists()
    assert "sf-002-paymentservice-cpu" in chaos_file.read_text(encoding="utf-8")

    # Cleanup sidecar assertions
    clean_file = evidence_dir / f"{experiment_id}.cleanup.json"
    assert clean_file.exists()
    clean = json.loads(clean_file.read_text(encoding="utf-8"))
    assert clean["timing"] == "after_post_t0_interruption"
    assert clean["affects_env_resolved"] is False
    assert clean["result"]["success"] is True
    assert clean["verified_zero_chaos"] is True

    # Attempt file remains in CONSUMED state (never released or completed)
    current_attempt = json.loads(attempt_file.read_text(encoding="utf-8"))
    assert current_attempt["state"] == runner.ATTEMPT_STATE_CONSUMED


# Case B: In-memory verifier completed, but primary verdict not persisted
def test_post_t0_interruption_case_b_in_memory_verifier_completed_no_primary(tmp_path):
    evidence_dir = tmp_path / "artifacts" / "evidence" / "stage4"
    attempts_dir = evidence_dir / ".attempts"
    attempts_dir.mkdir(parents=True)

    experiment_id = "EXP-STAGE4-TEST-CASE-B"
    attempt_file = attempts_dir / f"{experiment_id}.attempt.json"
    reservation = {
        "experiment_id": experiment_id,
        "state": runner.ATTEMPT_STATE_CONSUMED,
        "reservation_token": "token-case-b",
        "reserved_at": "2026-08-29T10:00:00+00:00",
        "consumed_at": "2026-08-29T10:00:05+00:00",
        "protocol_marker": protocol.G4_V31_PROTOCOL_MARKER,
        "protocol_fingerprint": protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE),
        "main_sha": "abc1234",
    }
    attempt_file.write_text(json.dumps(reservation), encoding="utf-8")

    evidence = {
        "experiment_id": experiment_id,
        "phases": {
            "verification": {
                "verification_report": {"status": "success"},
                "env_resolved": True,
                "agent_claimed_resolved": True,
            }
        },
    }
    disk_exc = OSError("Disk full before primary evidence was written")

    def mock_run_kubectl(cmd):
        return {"success": True, "stdout": "", "returncode": 0}

    with patch.object(runner, "run_kubectl", side_effect=mock_run_kubectl), patch.object(
        runner, "REPO_ROOT", str(tmp_path)
    ):
        runner._handle_post_t0_interruption(
            reservation=reservation,
            evidence=evidence,
            exc=disk_exc,
            fault_observable=True,
            evidence_dir=str(evidence_dir),
        )

    intr_file = evidence_dir / f"{experiment_id}.interruption.json"
    assert intr_file.exists()
    intr = json.loads(intr_file.read_text(encoding="utf-8"))

    assert intr["classification"] == "POST_T0_EXECUTION_INTERRUPTION"
    assert intr["scientific_status"] == "INCONCLUSIVE"
    assert intr["verifier_completed"] is True
    assert intr["gate_g4_pass"] is None
    assert intr["env_resolved"] is None
    assert intr["model_capability_failure"] is False
    assert intr["primary_verdict_authoritative"] is False
    assert intr["in_memory_verifier_summary"]["env_resolved"] is True
    assert intr["in_memory_verifier_summary"]["agent_claimed_resolved"] is True


# Case C: Primary PASS verdict already persisted on disk
def test_post_t0_interruption_case_c_primary_pass_verdict_already_persisted(tmp_path):
    evidence_dir = tmp_path / "artifacts" / "evidence" / "stage4"
    attempts_dir = evidence_dir / ".attempts"
    attempts_dir.mkdir(parents=True)

    experiment_id = "EXP-STAGE4-TEST-CASE-C"
    attempt_file = attempts_dir / f"{experiment_id}.attempt.json"
    reservation = {
        "experiment_id": experiment_id,
        "state": runner.ATTEMPT_STATE_CONSUMED,
        "reservation_token": "token-case-c",
        "reserved_at": "2026-08-29T10:00:00+00:00",
        "consumed_at": "2026-08-29T10:00:05+00:00",
        "protocol_marker": protocol.G4_V31_PROTOCOL_MARKER,
        "protocol_fingerprint": protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE),
        "main_sha": "abc1234",
    }
    attempt_file.write_text(json.dumps(reservation), encoding="utf-8")

    # Persist authoritative primary evidence
    primary_file = evidence_dir / f"{experiment_id}.json"
    primary_data = {
        "experiment_id": experiment_id,
        "gate_g4_pass": True,
        "env_resolved": True,
        "completed_at": "2026-08-29T10:05:00+00:00",
        "causal_criteria": {"objective_env_resolved": True, "f1_score_passing": True},
        "phases": {
            "verification": {
                "verification_report": {"status": "success"},
                "env_resolved": True,
            }
        },
    }
    primary_file.write_text(json.dumps(primary_data, indent=2), encoding="utf-8")
    primary_sha_before = runner.file_sha256(primary_file)

    bookkeeping_exc = RuntimeError("Failure during complete_experiment_attempt accounting lock")

    def mock_run_kubectl(cmd):
        return {"success": True, "stdout": "", "returncode": 0}

    with patch.object(runner, "run_kubectl", side_effect=mock_run_kubectl), patch.object(
        runner, "REPO_ROOT", str(tmp_path)
    ):
        runner._handle_post_t0_interruption(
            reservation=reservation,
            evidence=primary_data,
            exc=bookkeeping_exc,
            fault_observable=True,
            evidence_dir=str(evidence_dir),
        )

    # Primary evidence must remain 100% byte-for-byte immutable
    primary_sha_after = runner.file_sha256(primary_file)
    assert primary_sha_before == primary_sha_after

    # Interruption record must preserve PASS and NOT declare INCONCLUSIVE
    intr_file = evidence_dir / f"{experiment_id}.interruption.json"
    assert intr_file.exists()
    intr = json.loads(intr_file.read_text(encoding="utf-8"))

    assert intr["classification"] == "POST_VERDICT_OPERATIONAL_INTERRUPTION"
    assert intr["scientific_status"] == "VERDICT_ALREADY_FROZEN"
    assert intr["gate_g4_pass"] is True
    assert intr["env_resolved"] is True
    assert intr["verifier_completed"] is True
    assert intr["model_capability_failure"] is None
    assert intr["primary_verdict_authoritative"] is True
    assert intr["primary_verdict_persisted"] is True
    assert intr["evidence_hashes"]["primary_evidence_json"]["sha256"] == primary_sha_before

    # Cleanup timing indicates post-verdict
    clean_file = evidence_dir / f"{experiment_id}.cleanup.json"
    assert clean_file.exists()
    clean = json.loads(clean_file.read_text(encoding="utf-8"))
    assert clean["timing"] == "after_post_verdict_interruption"


# Case D: Primary FAIL verdict already persisted on disk without explicit model attribution
def test_post_t0_interruption_case_d_primary_fail_verdict_already_persisted(tmp_path):
    evidence_dir = tmp_path / "artifacts" / "evidence" / "stage4"
    attempts_dir = evidence_dir / ".attempts"
    attempts_dir.mkdir(parents=True)

    experiment_id = "EXP-STAGE4-TEST-CASE-D"
    attempt_file = attempts_dir / f"{experiment_id}.attempt.json"
    reservation = {
        "experiment_id": experiment_id,
        "state": runner.ATTEMPT_STATE_CONSUMED,
        "reservation_token": "token-case-d",
        "reserved_at": "2026-08-29T10:00:00+00:00",
        "consumed_at": "2026-08-29T10:00:05+00:00",
        "protocol_marker": protocol.G4_V31_PROTOCOL_MARKER,
        "protocol_fingerprint": protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE),
        "main_sha": "abc1234",
    }
    attempt_file.write_text(json.dumps(reservation), encoding="utf-8")

    # Persist authoritative primary FAIL evidence (without explicit model attribution)
    primary_file = evidence_dir / f"{experiment_id}.json"
    primary_data = {
        "experiment_id": experiment_id,
        "gate_g4_pass": False,
        "env_resolved": False,
        "completed_at": "2026-08-29T10:05:00+00:00",
        "causal_criteria": {"objective_env_resolved": False, "f1_score_passing": False},
        "phases": {
            "verification": {
                "verification_report": {"status": "failed"},
                "env_resolved": False,
            }
        },
    }
    primary_file.write_text(json.dumps(primary_data, indent=2), encoding="utf-8")
    primary_sha_before = runner.file_sha256(primary_file)

    cleanup_exc = RuntimeError("Failure during cluster kubectl delete")

    def mock_run_kubectl(cmd):
        return {"success": True, "stdout": "", "returncode": 0}

    with patch.object(runner, "run_kubectl", side_effect=mock_run_kubectl), patch.object(
        runner, "REPO_ROOT", str(tmp_path)
    ):
        runner._handle_post_t0_interruption(
            reservation=reservation,
            evidence=primary_data,
            exc=cleanup_exc,
            fault_observable=True,
            evidence_dir=str(evidence_dir),
        )

    # Primary evidence must remain 100% byte-for-byte immutable
    primary_sha_after = runner.file_sha256(primary_file)
    assert primary_sha_before == primary_sha_after

    # Interruption record must preserve FAIL and NOT declare INCONCLUSIVE or guess model failure
    intr_file = evidence_dir / f"{experiment_id}.interruption.json"
    assert intr_file.exists()
    intr = json.loads(intr_file.read_text(encoding="utf-8"))

    assert intr["classification"] == "POST_VERDICT_OPERATIONAL_INTERRUPTION"
    assert intr["scientific_status"] == "VERDICT_ALREADY_FROZEN"
    assert intr["gate_g4_pass"] is False
    assert intr["env_resolved"] is False
    assert intr["verifier_completed"] is True
    # Critical regression test: Failed G4 does NOT automatically mean model capability failure
    assert intr["model_capability_failure"] is None
    assert intr["primary_verdict_authoritative"] is True


# Case D2: Primary verdict with explicit adjudicated model_capability_failure preserved
def test_post_t0_interruption_case_d2_explicit_model_capability_failure_preserved(tmp_path):
    evidence_dir = tmp_path / "artifacts" / "evidence" / "stage4"
    attempts_dir = evidence_dir / ".attempts"
    attempts_dir.mkdir(parents=True)

    experiment_id = "EXP-STAGE4-TEST-CASE-D2"
    attempt_file = attempts_dir / f"{experiment_id}.attempt.json"
    reservation = {
        "experiment_id": experiment_id,
        "state": runner.ATTEMPT_STATE_CONSUMED,
        "reservation_token": "token-case-d2",
        "reserved_at": "2026-08-29T10:00:00+00:00",
        "consumed_at": "2026-08-29T10:00:05+00:00",
        "protocol_marker": protocol.G4_V31_PROTOCOL_MARKER,
        "protocol_fingerprint": protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE),
        "main_sha": "abc1234",
    }
    attempt_file.write_text(json.dumps(reservation), encoding="utf-8")

    # Persist authoritative primary evidence with explicit model_capability_failure
    primary_file = evidence_dir / f"{experiment_id}.json"
    primary_data = {
        "experiment_id": experiment_id,
        "gate_g4_pass": False,
        "env_resolved": False,
        "model_capability_failure": True,
        "completed_at": "2026-08-29T10:05:00+00:00",
        "causal_criteria": {"objective_env_resolved": False, "f1_score_passing": False},
        "phases": {
            "verification": {
                "verification_report": {"status": "failed"},
                "env_resolved": False,
            }
        },
    }
    primary_file.write_text(json.dumps(primary_data, indent=2), encoding="utf-8")
    primary_sha_before = runner.file_sha256(primary_file)

    cleanup_exc = RuntimeError("Failure during cluster kubectl delete")

    def mock_run_kubectl(cmd):
        return {"success": True, "stdout": "", "returncode": 0}

    with patch.object(runner, "run_kubectl", side_effect=mock_run_kubectl), patch.object(
        runner, "REPO_ROOT", str(tmp_path)
    ):
        runner._handle_post_t0_interruption(
            reservation=reservation,
            evidence=primary_data,
            exc=cleanup_exc,
            fault_observable=True,
            evidence_dir=str(evidence_dir),
        )

    # Primary evidence must remain 100% byte-for-byte immutable
    primary_sha_after = runner.file_sha256(primary_file)
    assert primary_sha_before == primary_sha_after

    intr_file = evidence_dir / f"{experiment_id}.interruption.json"
    assert intr_file.exists()
    intr = json.loads(intr_file.read_text(encoding="utf-8"))

    assert intr["classification"] == "POST_VERDICT_OPERATIONAL_INTERRUPTION"
    assert intr["scientific_status"] == "VERDICT_ALREADY_FROZEN"
    assert intr["gate_g4_pass"] is False
    assert intr["model_capability_failure"] is True
    assert intr["primary_verdict_authoritative"] is True


# Case E: Cleanup occurs only after sidecar persistence
def test_post_t0_interruption_case_e_cleanup_order(tmp_path):
    evidence_dir = tmp_path / "artifacts" / "evidence" / "stage4"
    attempts_dir = evidence_dir / ".attempts"
    attempts_dir.mkdir(parents=True)

    experiment_id = "EXP-STAGE4-TEST-CASE-E"
    attempt_file = attempts_dir / f"{experiment_id}.attempt.json"
    reservation = {
        "experiment_id": experiment_id,
        "state": runner.ATTEMPT_STATE_CONSUMED,
        "reservation_token": "token-case-e",
        "reserved_at": "2026-08-29T10:00:00+00:00",
        "consumed_at": "2026-08-29T10:00:05+00:00",
        "protocol_marker": protocol.G4_V31_PROTOCOL_MARKER,
        "protocol_fingerprint": protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE),
        "main_sha": "abc1234",
    }
    attempt_file.write_text(json.dumps(reservation), encoding="utf-8")

    evidence = {"experiment_id": experiment_id}
    intr_file = evidence_dir / f"{experiment_id}.interruption.json"

    def mock_run_kubectl(cmd):
        if cmd[0] == "delete":
            # Assert interruption file exists before kubectl delete executes
            assert intr_file.exists(), "Interruption sidecar must be persisted before cleanup executes"
            return {"success": True, "stdout": "deleted", "returncode": 0}
        return {"success": True, "stdout": "", "returncode": 0}

    with patch.object(runner, "run_kubectl", side_effect=mock_run_kubectl), patch.object(
        runner, "REPO_ROOT", str(tmp_path)
    ):
        runner._handle_post_t0_interruption(
            reservation=reservation,
            evidence=evidence,
            exc=RuntimeError("Generic runtime failure"),
            fault_observable=True,
            evidence_dir=str(evidence_dir),
        )

    assert (evidence_dir / f"{experiment_id}.cleanup.json").exists()


# Case F: Pre-T0 exception releases reservation
def test_pre_t0_exception_releases_reservation(tmp_path):
    attempts_dir = tmp_path / "artifacts" / "evidence" / "stage4" / ".attempts"
    attempts_dir.mkdir(parents=True)

    experiment_id = "EXP-STAGE4-PRE-T0"
    reservation = runner.reserve_experiment_attempt(
        experiment_id,
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=str(tmp_path),
    )
    marker_file = attempts_dir / f"{experiment_id}.attempt.json"
    assert marker_file.exists()

    released = runner.release_experiment_reservation(reservation, attempt_root=str(tmp_path))
    assert released is True
    assert not marker_file.exists()


# Case G: No automatic next attempt is created on interruption
def test_interruption_does_not_create_automatic_next_attempt(tmp_path):
    evidence_dir = tmp_path / "artifacts" / "evidence" / "stage4"
    attempts_dir = evidence_dir / ".attempts"
    attempts_dir.mkdir(parents=True)

    exp11 = "EXP-STAGE4-SF002-011"
    v31_fp = protocol_fingerprint(APPROVED_G4_V31_PROTOCOL_PROFILE)
    reservation = {
        "experiment_id": exp11,
        "state": runner.ATTEMPT_STATE_CONSUMED,
        "reservation_token": "token-11",
        "reserved_at": "2026-08-29T10:00:00+00:00",
        "consumed_at": "2026-08-29T10:00:05+00:00",
        "protocol_marker": protocol.G4_V31_PROTOCOL_MARKER,
        "protocol_fingerprint": v31_fp,
        "main_sha": "abc1234",
    }
    (attempts_dir / f"{exp11}.attempt.json").write_text(json.dumps(reservation), encoding="utf-8")

    # Claimed attempts for v3.1 is 1
    assert runner._claimed_attempts_for_protocol_fingerprint(v31_fp, attempt_root=str(tmp_path)) == 1

    # Verify no 012 attempt exists
    assert not (attempts_dir / "EXP-STAGE4-SF002-012.attempt.json").exists()
    assert len(list(attempts_dir.glob("*012*"))) == 0


def _install_mocked_host_runner_boundaries(
    monkeypatch,
    tmp_path: Path,
    experiment_id: str,
    run_kubectl,
) -> dict[str, object]:
    main_sha = "a" * 40
    port_forward = object()
    stopped_port_forwards = []

    @asynccontextmanager
    async def mocked_approval_server(_api_key: str):
        yield "http://127.0.0.1:18099"

    monkeypatch.setattr(runner, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(runner, "EXPERIMENT_ID", runner.EXPERIMENT_ID)
    monkeypatch.setattr(approval_gate, "timeout_seconds", approval_gate.timeout_seconds)
    monkeypatch.setattr(runner, "load_stage4_secrets", lambda: {"ATLASOPS_API_KEY": "synthetic"})
    monkeypatch.setattr(runner, "stage4_approval_server", mocked_approval_server)
    monkeypatch.setattr(
        runner, "_current_main_sha", lambda expected_sha=None: expected_sha or main_sha
    )
    monkeypatch.setattr(runner, "_configure_stage4_runtime", lambda: None)
    monkeypatch.setattr(runner, "_start_port_forwards", lambda *_args, **_kwargs: [port_forward])
    monkeypatch.setattr(
        runner,
        "_stop_port_forwards",
        lambda processes: stopped_port_forwards.append(processes),
    )
    monkeypatch.setattr(
        runner,
        "wait_for_telemetry_readiness",
        lambda: (True, {"attempts": 2, "stable_probes": 2}),
    )
    monkeypatch.setattr(
        runner,
        "wait_for_baseline_readiness",
        lambda: (True, {"stable_probes": 2}, True, {"stdout": "{}"}),
    )
    monkeypatch.setattr(runner, "collect_sf002_cpu_telemetry", lambda: {"max_cores": 0.1})
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner, "run_kubectl", run_kubectl)
    monkeypatch.setenv("STAGE4_EXPERIMENT_ID", experiment_id)

    return {
        "evidence_dir": tmp_path / "artifacts" / "evidence" / "stage4",
        "port_forward": port_forward,
        "stopped_port_forwards": stopped_port_forwards,
    }


@pytest.mark.parametrize(
    "interruption_type",
    [KeyboardInterrupt, asyncio.CancelledError],
    ids=["keyboard-interrupt", "task-cancelled"],
)
@pytest.mark.parametrize("cleanup_verified", [True, False], ids=["clean", "poisoned"])
def test_host_runner_records_post_t0_baseexception_and_reraises(
    monkeypatch,
    tmp_path,
    interruption_type,
    cleanup_verified,
):
    experiment_id = "EXP-STAGE4-TEST-POST-T0-INTERRUPTION"
    interruption = interruption_type("synthetic post-T0 interruption")
    cleanup_started = False
    chaos_list_reads = 0

    def mocked_run_kubectl(command):
        nonlocal cleanup_started, chaos_list_reads
        if command[0] == "apply":
            raise interruption
        if command[0] == "delete":
            cleanup_started = True
            return {"success": cleanup_verified, "stdout": "deleted" if cleanup_verified else ""}
        if command[0] == "get" and command[1] == runner.CHAOS_RESOURCE_KINDS:
            chaos_list_reads += 1
            if chaos_list_reads <= 3:
                items = []
            elif cleanup_verified or not cleanup_started:
                items = []
            else:
                items = [
                    {
                        "kind": runner.TARGET_CHAOS_KIND,
                        "metadata": {
                            "name": runner.TARGET_CHAOS_NAME,
                            "namespace": runner.TARGET_CHAOS_NAMESPACE,
                        },
                    }
                ]
            return {"success": True, "stdout": json.dumps({"items": items})}
        if command[0] == "get":
            return {
                "success": True,
                "stdout": (
                    "apiVersion: chaos-mesh.org/v1alpha1\n"
                    f"kind: {runner.TARGET_CHAOS_KIND}\n"
                    "metadata:\n"
                    f"  name: {runner.TARGET_CHAOS_NAME}\n"
                ),
            }
        return {"success": True, "stdout": ""}

    boundaries = _install_mocked_host_runner_boundaries(
        monkeypatch,
        tmp_path,
        experiment_id,
        mocked_run_kubectl,
    )
    original_public_base = runner.os.environ.get("ATLASOPS_PUBLIC_BASE_URL")

    with pytest.raises(interruption_type) as raised:
        asyncio.run(runner._main_with_qualified_inference())

    assert raised.value is interruption
    assert boundaries["stopped_port_forwards"] == [[boundaries["port_forward"]]]
    assert runner.os.environ.get("ATLASOPS_PUBLIC_BASE_URL") == original_public_base

    evidence_dir = boundaries["evidence_dir"]
    interruption_path = evidence_dir / f"{experiment_id}.interruption.json"
    interruption_record = json.loads(interruption_path.read_text(encoding="utf-8"))
    attempt_path = evidence_dir / ".attempts" / f"{experiment_id}.attempt.json"
    attempt_record = json.loads(attempt_path.read_text(encoding="utf-8"))
    assert interruption_record["observed_exception_class"] == interruption_type.__name__
    assert interruption_record["scientific_status"] == "INCONCLUSIVE"
    assert interruption_record["classification"] == "POST_T0_EXECUTION_INTERRUPTION"
    assert interruption_record["t0_crossed"] is True
    assert interruption_record["gate_g4_pass"] is None
    assert interruption_record["env_resolved"] is None
    assert attempt_record["state"] == runner.ATTEMPT_STATE_CONSUMED
    assert interruption_record["attempt_state"] == attempt_record["state"]
    assert interruption_record["consumed_timestamp"] == attempt_record["consumed_at"]
    assert interruption_record["evidence_hashes"]["attempt_json"]["sha256"] == runner.file_sha256(
        attempt_path
    )
    assert not (evidence_dir / f"{experiment_id}.json").exists()

    cleanup_record = json.loads(
        (evidence_dir / f"{experiment_id}.cleanup.json").read_text(encoding="utf-8")
    )
    assert cleanup_record["timing"] == "after_post_t0_interruption"
    assert cleanup_record["verified_zero_chaos"] is cleanup_verified
    assert cleanup_record["poisoned_environment"] is not cleanup_verified
    poison_path = evidence_dir / runner.POISONED_ENVIRONMENT_FILENAME
    assert poison_path.exists() is not cleanup_verified
    assert (evidence_dir / f"{experiment_id}.leftover-chaos.yaml").exists()


@pytest.mark.parametrize(
    "interruption_type",
    [KeyboardInterrupt, asyncio.CancelledError],
    ids=["keyboard-interrupt", "task-cancelled"],
)
def test_host_runner_releases_reservation_on_pre_t0_baseexception(
    monkeypatch,
    tmp_path,
    interruption_type,
):
    experiment_id = "EXP-STAGE4-TEST-PRE-T0-INTERRUPTION"
    interruption = interruption_type("synthetic pre-T0 interruption")
    chaos_list_reads = 0

    def mocked_run_kubectl(command):
        nonlocal chaos_list_reads
        if command[0] == "get" and command[1] == runner.CHAOS_RESOURCE_KINDS:
            chaos_list_reads += 1
            if chaos_list_reads == 2:
                raise interruption
            return {"success": True, "stdout": json.dumps({"items": []})}
        return {"success": True, "stdout": ""}

    boundaries = _install_mocked_host_runner_boundaries(
        monkeypatch,
        tmp_path,
        experiment_id,
        mocked_run_kubectl,
    )
    attempt_path = boundaries["evidence_dir"] / ".attempts" / f"{experiment_id}.attempt.json"

    with pytest.raises(interruption_type) as raised:
        asyncio.run(runner._main_with_qualified_inference())

    assert raised.value is interruption
    assert boundaries["stopped_port_forwards"] == [[boundaries["port_forward"]]]
    assert not attempt_path.exists()
    assert not (boundaries["evidence_dir"] / f"{experiment_id}.interruption.json").exists()
    assert not (boundaries["evidence_dir"] / f"{experiment_id}.cleanup.json").exists()


@pytest.mark.parametrize(
    ("interruption_type", "commit_transition"),
    [
        pytest.param(KeyboardInterrupt, False, id="before-transition-commit"),
        pytest.param(asyncio.CancelledError, True, id="after-transition-commit"),
    ],
)
def test_host_runner_transition_interruption_preserves_pre_t0_truth(
    monkeypatch,
    tmp_path,
    interruption_type,
    commit_transition,
):
    experiment_id = "EXP-STAGE4-TEST-CONSUMPTION-TRANSITION"
    interruption = interruption_type("synthetic consumption-transition interruption")
    commands = []

    def mocked_run_kubectl(command):
        commands.append(list(command))
        if command[0] == "apply":
            pytest.fail("fault apply must not begin before consumption completes")
        if command[0] == "get" and command[1] == runner.CHAOS_RESOURCE_KINDS:
            return {"success": True, "stdout": json.dumps({"items": []})}
        return {"success": True, "stdout": ""}

    boundaries = _install_mocked_host_runner_boundaries(
        monkeypatch,
        tmp_path,
        experiment_id,
        mocked_run_kubectl,
    )
    attempt_path = boundaries["evidence_dir"] / ".attempts" / f"{experiment_id}.attempt.json"
    original_write_json_atomic = runner._write_json_atomic

    def interrupted_transition_write(path, data):
        if Path(path) == attempt_path and data.get("state") == runner.ATTEMPT_STATE_CONSUMED:
            if commit_transition:
                original_write_json_atomic(path, data)
            raise interruption
        original_write_json_atomic(path, data)

    monkeypatch.setattr(runner, "_write_json_atomic", interrupted_transition_write)

    with pytest.raises(interruption_type) as raised:
        asyncio.run(runner._main_with_qualified_inference())

    assert raised.value is interruption
    assert not any(command[0] == "apply" for command in commands)
    assert boundaries["stopped_port_forwards"] == [[boundaries["port_forward"]]]
    assert not (boundaries["evidence_dir"] / f"{experiment_id}.interruption.json").exists()
    assert not (boundaries["evidence_dir"] / f"{experiment_id}.cleanup.json").exists()

    prefault_path = attempt_path.with_name(f"{experiment_id}.prefault.json")
    if commit_transition:
        attempt_record = json.loads(attempt_path.read_text(encoding="utf-8"))
        prefault_record = json.loads(prefault_path.read_text(encoding="utf-8"))
        assert attempt_record["state"] == runner.ATTEMPT_STATE_CONSUMED
        assert prefault_record["attempt_state"] == runner.ATTEMPT_STATE_CONSUMED
        assert prefault_record["failure_phase"] == "attempt_consumption"
        assert prefault_record["t0_crossed"] is False
        assert not prefault_record["reservation_released"]
    else:
        assert not attempt_path.exists()
        assert not prefault_path.exists()
