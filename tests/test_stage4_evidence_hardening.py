"""Deterministic regression tests for Stage 4 causal/evidence hardening."""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scripts.run_stage4_golden_incident as runner
from config.g4_protocol import (
    APPROVED_G4_MODEL,
    APPROVED_G4_PROTOCOL_PROFILE,
    REQUIRED_METRICS_SERVER_ARGS,
    protocol_fingerprint,
)
from scripts.run_stage4_golden_incident import (
    ATTEMPT_STATE_COMPLETED,
    ATTEMPT_STATE_CONSUMED,
    ATTEMPT_STATE_RESERVED,
    DEGRADATION_QUERY,
    G4_PLATFORM_HARDENING_MARKER,
    MAX_ATTEMPTS_PER_PROTOCOL_MARKER,
    MAX_POSTFLIGHT_CLEANUP_ATTEMPTS,
    _attempt_marker_path,
    _chaos_state_observation,
    _paymentservice_baseline_check,
    _paymentservice_baseline_healthy,
    _persist_stage4_preflight_evidence,
    _poisoned_environment_path,
    _primary_incident_evidence_persisted,
    collect_sf002_cpu_telemetry,
    complete_experiment_attempt,
    consume_experiment_attempt,
    evaluate_causal_g4_predicate,
    reconcile_stage4_postflight_cleanup,
    release_experiment_reservation,
    reserve_experiment_attempt,
    sf002_degradation_decision,
    stage4_evidence_metadata,
)


def _attempt_root() -> str:
    root = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scratch"
        / "stage4-attempt-tests"
        / uuid.uuid4().hex
    )
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


@pytest.mark.parametrize(
    "experiment_id",
    ["../outside", "EXP-STAGE4-../../outside", "EXP-STAGE4-..\\outside", "EXP-STAGE4-A/B"],
)
def test_experiment_id_cannot_escape_evidence_root(tmp_path, experiment_id):
    with pytest.raises(ValueError, match="single safe"):
        runner._experiment_evidence_dir(experiment_id, root=str(tmp_path))
    with pytest.raises(ValueError, match="single safe"):
        runner._attempt_marker_path(experiment_id, root=str(tmp_path))
    assert not (tmp_path / "artifacts").exists()


@pytest.fixture(autouse=True)
def isolated_protocol_runtime(monkeypatch, tmp_path):
    postmortem_dir = tmp_path / "postmortems"
    postmortem_dir.mkdir()
    monkeypatch.setenv("POSTMORTEM_DIR", str(postmortem_dir))
    monkeypatch.setattr(
        runner,
        "_query_ollama_model_identity",
        lambda selected_model: {
            "provider": "ollama-local",
            "name": selected_model,
            "digest": APPROVED_G4_PROTOCOL_PROFILE["model"]["digest"],
        },
    )
    monkeypatch.setattr(
        runner,
        "_probe_metrics_server_contract",
        lambda: APPROVED_G4_PROTOCOL_PROFILE["metrics_api"],
    )


def test_stage4_metadata_persists_protocol_marker():
    metadata = stage4_evidence_metadata()
    assert metadata["protocol_marker"] == G4_PLATFORM_HARDENING_MARKER


def test_reservation_records_protocol_marker_and_spent_limit_is_two():
    root = _attempt_root()
    reservation = reserve_experiment_attempt(
        "EXP-STAGE4-MARKER",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    assert reservation["protocol_profile"] == APPROVED_G4_PROTOCOL_PROFILE
    assert reservation["protocol_fingerprint"] == protocol_fingerprint(
        APPROVED_G4_PROTOCOL_PROFILE
    )
    assert MAX_ATTEMPTS_PER_PROTOCOL_MARKER == 2


def test_default_and_arbitrary_models_cannot_consume_approved_protocol_budget():
    root = _attempt_root()
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        reserve_experiment_attempt(
            "EXP-STAGE4-DEFAULT-MODEL",
            selected_model="qwen2.5:1.5b",
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not list(pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts").glob("*.json"))

    with pytest.raises(RuntimeError, match="approved protocol profile"):
        reserve_experiment_attempt(
            "EXP-STAGE4-ARBITRARY-MODEL",
            selected_model="qwen2.5:3b-instruct",
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not list(pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts").glob("*.json"))


def test_metrics_api_absence_cannot_reserve_under_required_present_profile(monkeypatch):
    monkeypatch.setattr(
        runner,
        "_probe_metrics_server_contract",
        lambda: {"state": "missing"},
    )
    root = _attempt_root()
    with pytest.raises(RuntimeError, match="approved protocol profile"):
        reserve_experiment_attempt(
            "EXP-STAGE4-METRICS-MISSING",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not list(pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts").glob("*.json"))


def test_metrics_api_drift_cannot_masquerade_under_same_profile(monkeypatch):
    drifted_payload = {
        "metadata": {"name": "metrics-server", "namespace": "kube-system"},
        "spec": {"template": {"spec": {
            "serviceAccountName": "metrics-server",
            "priorityClassName": "system-cluster-critical",
            "containers": [{
                "name": "metrics-server",
                "image": "registry.example.invalid/metrics-server:v0.7.2",
                "args": list(REQUIRED_METRICS_SERVER_ARGS),
                "ports": [{"containerPort": 10250, "name": "https", "protocol": "TCP"}],
                "resources": {"requests": {"cpu": "100m", "memory": "200Mi"}},
            }],
        }}},
    }
    monkeypatch.setattr(
        runner,
        "_probe_metrics_server_contract",
        lambda: __import__("config.g4_protocol", fromlist=["inspect_metrics_server_deployment"]).inspect_metrics_server_deployment(
            lambda _args: {"success": True, "stdout": json.dumps(drifted_payload)}
        ),
    )
    root = _attempt_root()
    with pytest.raises(RuntimeError, match="Metrics API Deployment provenance mismatch"):
        reserve_experiment_attempt(
            "EXP-STAGE4-METRICS-DRIFT",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not list(pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts").glob("*.json"))


def test_prompt_or_tool_contract_drift_fails_closed_before_attempt_budget(
    monkeypatch,
):
    root = _attempt_root()
    original_builder = runner.build_runtime_protocol_profile

    # Keep the live model/Metrics probes valid so this test isolates declared
    # contract drift from unrelated runtime drift.

    for field in ("diagnosis_prompt", "role_tool_contract"):
        def build_with_drift(_field=field, **kwargs):
            profile = original_builder(**kwargs)
            profile[_field] = {**profile[_field], "sha256": "0" * 64}
            return profile

        monkeypatch.setattr(runner, "build_runtime_protocol_profile", build_with_drift)
        with pytest.raises(RuntimeError, match="approved protocol profile"):
            reserve_experiment_attempt(
                f"EXP-STAGE4-{field.upper()}-DRIFT",
                selected_model=APPROVED_G4_MODEL,
                main_sha="test-sha",
                attempt_root=root,
            )
    assert not list(pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts").glob("*.json"))


def test_historical_unmarked_attempts_are_not_retroactively_counted():
    root = _attempt_root()
    attempts_dir = pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts")
    attempts_dir.mkdir(parents=True)
    legacy = {
        "experiment_id": "EXP-STAGE4-HISTORICAL",
        "state": "COMPLETED",
        "protocol_marker": G4_PLATFORM_HARDENING_MARKER,
    }
    (attempts_dir / "EXP-STAGE4-HISTORICAL.attempt.json").write_text(
        json.dumps(legacy), encoding="utf-8"
    )

    reservation = reserve_experiment_attempt(
        "EXP-STAGE4-AFTER-HISTORY",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    assert reservation["protocol_fingerprint"] == protocol_fingerprint(
        APPROVED_G4_PROTOCOL_PROFILE
    )


def test_third_spent_attempt_for_same_protocol_marker_fails_closed():
    root = _attempt_root()
    for experiment_id in ("EXP-STAGE4-MARKER-A", "EXP-STAGE4-MARKER-B"):
        reservation = reserve_experiment_attempt(
            experiment_id,
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )
        consume_experiment_attempt(reservation, attempt_root=root)

    with pytest.raises(RuntimeError, match="protocol attempt limit reached"):
        reserve_experiment_attempt(
            "EXP-STAGE4-MARKER-C",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )


def test_corrupt_attempt_accounting_fails_closed():
    root = _attempt_root()
    attempts_dir = pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts")
    attempts_dir.mkdir(parents=True)
    (attempts_dir / "corrupt.attempt.json").write_text("{", encoding="utf-8")

    with pytest.raises(RuntimeError, match="attempt accounting record is invalid"):
        reserve_experiment_attempt(
            "EXP-STAGE4-CORRUPT-ACCOUNTING",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not list(attempts_dir.glob("EXP-STAGE4-CORRUPT-ACCOUNTING.*"))
    assert not (attempts_dir / runner.ATTEMPT_BUDGET_LOCK_FILENAME).exists()


def test_stale_reservation_lock_fails_closed_without_creating_attempt():
    root = _attempt_root()
    attempts_dir = pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts")
    lock_path = attempts_dir / runner.ATTEMPT_BUDGET_LOCK_FILENAME
    attempts_dir.mkdir(parents=True)
    lock_path.write_text("stale", encoding="utf-8")

    with pytest.raises(RuntimeError, match="reservation budget is locked"):
        reserve_experiment_attempt(
            "EXP-STAGE4-STALE-LOCK",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )
    assert not any(attempts_dir.glob("EXP-STAGE4-STALE-LOCK.*"))
    assert lock_path.exists()
    lock_path.unlink()


def test_reserved_attempts_occupy_budget_slots_and_release_frees_a_slot():
    root = _attempt_root()
    spent = reserve_experiment_attempt(
        "EXP-STAGE4-SLOT-A",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    consume_experiment_attempt(spent, attempt_root=root)

    reserved = reserve_experiment_attempt(
        "EXP-STAGE4-SLOT-B",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    with pytest.raises(RuntimeError, match="protocol attempt limit reached"):
        reserve_experiment_attempt(
            "EXP-STAGE4-SLOT-C",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )

    assert release_experiment_reservation(reserved, attempt_root=root) is True
    replacement = reserve_experiment_attempt(
        "EXP-STAGE4-SLOT-D",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    assert replacement["state"] == ATTEMPT_STATE_RESERVED


def test_concurrent_reservations_cannot_exceed_the_last_budget_slot():
    root = _attempt_root()
    first = reserve_experiment_attempt(
        "EXP-STAGE4-RACE-SPENT",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    consume_experiment_attempt(first, attempt_root=root)

    def reserve_slot(experiment_id):
        try:
            return reserve_experiment_attempt(
                experiment_id,
                selected_model=APPROVED_G4_MODEL,
                main_sha="test-sha",
                attempt_root=root,
            )
        except RuntimeError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                reserve_slot,
                ("EXP-STAGE4-RACE-A", "EXP-STAGE4-RACE-B"),
            )
        )

    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, RuntimeError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert any(
        marker in str(failures[0])
        for marker in (
            "protocol attempt limit reached",
            "reservation budget is locked",
        )
    )
    attempts_dir = pathlib.Path(root, "artifacts", "evidence", "stage4", ".attempts")
    assert len(list(attempts_dir.glob("*.attempt.json"))) == 2
    assert not (attempts_dir / runner.ATTEMPT_BUDGET_LOCK_FILENAME).exists()


def _valid_incident() -> dict:
    incident_id = "inc-hardening"
    root_cause = "paymentservice CPU pressure caused by StressChaos"
    postmortem_path = pathlib.Path(os.environ["POSTMORTEM_DIR"]) / f"{incident_id}.md"
    postmortem_path.write_text(
        f"Incident: {incident_id}\nRoot cause: {root_cause}\n"
        "Resolution: Resolved and verified by the objective environment check.\n",
        encoding="utf-8",
    )
    diagnosis = {
        "final": {
            "root_cause": root_cause,
            "evidence": [
                {
                    "tool": "promql_query",
                    "query": "paymentservice_cpu_usage",
                    "finding": "Observed elevated paymentservice CPU usage.",
                }
            ],
        },
        "trajectory": [
            {
                "tool": "promql_query",
                "args": {"query": "paymentservice_cpu_usage"},
                "output": {"success": True, "result": [{"value": "0.6"}]},
            }
        ],
    }
    from agents.grounding import validate_evidence_grounding

    approval = {
        "mode": "approve",
        "severity": "P1",
        "decision": "approved",
        "approved_by": "test-operator",
    }
    settling = {
        "started_at": "2026-09-24T00:00:00+00:00",
        "completed_at": "2026-09-24T00:00:01+00:00",
        "duration_seconds": 1.0,
        "timeout_seconds": 30,
        "poll_interval_seconds": 2,
        "settled": True,
        "observations": [
            {
                "timestamp": "2026-09-24T00:00:01+00:00",
                "elapsed_seconds": 1.0,
                "env_resolved": True,
                "verification_status": "passed",
                "failed_checks": [],
            }
        ],
    }
    return {
        "incident_id": incident_id,
        "incident_anchors": {"primary_service": "paymentservice"},
        "target_consistency": {
            "primary_target": "paymentservice",
            "status": "primary_target_preserved",
            "requires_review": False,
        },
        "triage": {"final": {"severity": "P1"}},
        "diagnosis": diagnosis,
        "grounding_validation": {
            "diagnosis": validate_evidence_grounding(diagnosis),
        },
        "approval": approval,
        "remediation": {
            "trajectory": [
                {
                    "tool": "chaos_stop_experiment",
                    "args": {
                        "kind": "StressChaos",
                        "name": "sf-002-paymentservice-cpu",
                        "namespace": "chaos-mesh",
                    },
                    "output": {"success": True},
                }
            ],
            "final": {"outcome": "resolved"},
        },
        "verification": {
            "env_resolved": True,
            "verification_status": "passed",
            "failed_checks": [],
        },
        "settling": settling,
        "env_resolved": True,
        "comms": {
            "final": {
                "incident_id": incident_id,
                "summary": "paymentservice was resolved and verified by the objective check.",
                "postmortem_path": str(postmortem_path),
            },
            "trajectory": [
                {
                    "tool": "postmortem_draft",
                    "output": {
                        "success": True,
                        "path": str(postmortem_path),
                        "postmortem_path": str(postmortem_path),
                    },
                }
            ],
        },
    }


@pytest.mark.parametrize(
    "approval",
    [None, {}, {"decision": "timeout"}, {"decision": "rejected"},
     {"decision": "unknown"}, {"decision": None}, {"decision": True},
     {"decision": "APPROVED"}, "approved"],
)
def test_p1_gate_requires_explicit_approval(approval):
    """Synthetic predicate coverage only; this is not empirical G4 evidence."""
    incident = _valid_incident()
    incident["approval"] = approval
    result = runner.evaluate_causal_g4_predicate(
        True, True, True, incident, False, primary_evidence_persisted=True,
    )
    assert result["criteria"]["8_approval_satisfied"] is False
    assert result["gate_g4_pass"] is False


def test_p1_gate_accepts_explicit_approval():
    result = runner.evaluate_causal_g4_predicate(
        True, True, True, _valid_incident(), False,
        primary_evidence_persisted=True,
    )
    assert result["criteria"]["8_approval_satisfied"] is True


def test_p1_gate_accepts_normalized_approval_status():
    incident = _valid_incident()
    incident["approval"] = {
        "mode": "approve",
        "severity": "p1",
        "status": "APPROVED",
        "approved_by": "test-operator",
    }
    result = runner.evaluate_causal_g4_predicate(
        True, True, True, incident, False,
        primary_evidence_persisted=True,
    )
    assert result["criteria"]["8_approval_satisfied"] is True


@pytest.mark.parametrize(
    "severity,approval",
    [
        ("P2", {"mode": "auto", "severity": "P2", "decision": "approved"}),
        ("P1", {"mode": "auto", "severity": "P1", "decision": "approved"}),
        ("P1", {"mode": "approve", "severity": "P2", "decision": "approved"}),
        ("P1", {"mode": "approve", "severity": "P1", "decision": "rejected", "status": "approved"}),
        ("P1", {"mode": "approve", "severity": "P1", "decision": "approved"}),
        ("P1", {"mode": "approve", "severity": "P1", "decision": "approved", "approved_by": "  "}),
        ("P1", "approved"),
    ],
)
def test_p1_gate_rejects_non_p1_or_inconsistent_approval(severity, approval):
    incident = _valid_incident()
    incident["triage"]["final"]["severity"] = severity
    incident["approval"] = approval
    result = evaluate_causal_g4_predicate(
        True, True, True, incident, False, primary_evidence_persisted=True
    )
    assert result["criteria"]["8_approval_satisfied"] is False
    assert result["gate_g4_pass"] is False


def test_diagnosis_requires_anchored_target_and_grounded_observation():
    incident = _valid_incident()
    incident["diagnosis"]["final"]["root_cause"] = "CPU pressure caused elevated latency"
    result = evaluate_causal_g4_predicate(
        True, True, True, incident, False, primary_evidence_persisted=True
    )
    assert result["criteria"]["7_diagnosis_truth_match"] is False

    incident = _valid_incident()
    incident["diagnosis"]["final"]["evidence"][0]["query"] = "query-that-was-not-run"
    result = evaluate_causal_g4_predicate(
        True, True, True, incident, False, primary_evidence_persisted=True
    )
    assert result["criteria"]["7_diagnosis_truth_match"] is False


def test_action_predicate_requires_one_successful_exact_chaos_stop():
    incident = _valid_incident()
    incident["remediation"]["trajectory"].append(
        dict(incident["remediation"]["trajectory"][0])
    )
    result = evaluate_causal_g4_predicate(
        True, True, True, incident, False, primary_evidence_persisted=True
    )
    assert result["criteria"]["9_remediation_mutating_tool_executed"] is False
    assert len(result["executed_tool_calls"]) == 2

    incident = _valid_incident()
    incident["remediation"]["trajectory"][0]["args"]["namespace"] = "other"
    result = evaluate_causal_g4_predicate(
        True, True, True, incident, False, primary_evidence_persisted=True
    )
    assert result["criteria"]["11_remediation_target_match"] is False


def test_blocked_action_outcomes_are_preserved_but_not_counted_as_execution():
    incident = _valid_incident()
    incident["remediation"]["trajectory"] = [
        {
            "tool": "chaos_stop_experiment",
            "args": {"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh"},
            "output": {"success": False, "error": "approval required"},
            "blocked_by_policy": True,
        }
    ]
    result = evaluate_causal_g4_predicate(
        True, True, True, incident, False, primary_evidence_persisted=True
    )
    assert result["criteria"]["9_remediation_mutating_tool_executed"] is False
    assert result["executed_tool_calls"] == []
    assert result["tool_outcomes"][0]["blocked_by_policy"] is True


def test_settling_requires_persisted_bounded_observations():
    incident = _valid_incident()
    incident["settling"]["observations"] = []
    result = evaluate_causal_g4_predicate(
        True,
        True,
        True,
        incident,
        False,
        settling_completed=True,
        primary_evidence_persisted=True,
    )
    assert result["settling_satisfied"] is False
    assert result["criteria"]["13_objective_env_resolved"] is False


def test_comms_requires_real_postmortem_tool_result_and_verified_status():
    incident = _valid_incident()
    incident["comms"]["trajectory"] = []
    result = evaluate_causal_g4_predicate(
        True, True, True, incident, False, primary_evidence_persisted=True
    )
    assert result["criteria"]["14_comms_executed"] is False

    incident = _valid_incident()
    incident["comms"]["final"]["summary"] = "The incident remains unresolved."
    result = evaluate_causal_g4_predicate(
        True, True, True, incident, False, primary_evidence_persisted=True
    )
    assert result["criteria"]["14_comms_executed"] is False


def test_primary_incident_evidence_requires_exact_record_equality(monkeypatch, tmp_path):
    incident = _valid_incident()
    trajectory_dir = tmp_path / "trajectories"
    trajectory_dir.mkdir()
    monkeypatch.setenv("TRAJECTORIES_DIR", str(trajectory_dir))
    record_path = trajectory_dir / f"{incident['incident_id']}.json"
    record_path.write_text(json.dumps(incident), encoding="utf-8")
    assert _primary_incident_evidence_persisted(incident) is True

    tampered = json.loads(record_path.read_text(encoding="utf-8"))
    tampered["remediation"]["trajectory"][0]["output"]["success"] = False
    record_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert _primary_incident_evidence_persisted(incident) is False


def _preflight_record() -> dict:
    return {
        "experiment_id": "EXP-STAGE4-PREFLIGHT-TEST",
        "scenario_id": "single_fault/sf-002",
        "protocol_marker": G4_PLATFORM_HARDENING_MARKER,
        "protocol_profile": APPROVED_G4_PROTOCOL_PROFILE,
        "source_identity": {
            "git_commit": "a" * 40,
            "working_tree_clean": True,
            "protocol_fingerprint": "b" * 64,
        },
        "phases": {
            "telemetry_readiness": {"ready": True},
            "baseline": {"baseline_healthy": True},
        },
    }


def test_clean_preflight_must_be_verified_and_persisted_immutably(tmp_path):
    evidence = _preflight_record()
    path = pathlib.Path(
        _persist_stage4_preflight_evidence(
            evidence,
            {"success": True, "stdout": json.dumps({"items": []})},
            root=str(tmp_path),
        )
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["zero_chaos_preflight"]["verified_zero"] is True
    assert evidence["preflight_evidence"]["persisted_before_injection"] is True
    with pytest.raises(RuntimeError, match="overwrite Stage 4 preflight"):
        _persist_stage4_preflight_evidence(
            evidence,
            {"success": True, "stdout": json.dumps({"items": []})},
            root=str(tmp_path),
        )


def test_preflight_does_not_persist_when_zero_chaos_query_failed(tmp_path):
    evidence = _preflight_record()
    with pytest.raises(RuntimeError, match="incomplete or unverified"):
        _persist_stage4_preflight_evidence(
            evidence,
            {"success": False, "stdout": json.dumps({"items": []})},
            root=str(tmp_path),
        )
    assert not list((tmp_path / "artifacts" / "evidence" / "stage4").glob("*.preflight.json"))


def test_failed_chaos_list_query_is_not_an_empty_preflight():
    observation = _chaos_state_observation(
        {"success": False, "stdout": json.dumps({"items": []})}
    )
    assert observation == {"count": 0, "verified_zero": False}


def test_postflight_cleanup_persists_verified_zero_without_poison(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    def kubectl(args):
        if args[0] == "delete":
            return {"success": True, "stdout": "deleted", "returncode": 0}
        return {"success": True, "stdout": json.dumps({"items": []}), "returncode": 0}

    monkeypatch.setattr(runner, "run_kubectl", kubectl)
    cleanup = reconcile_stage4_postflight_cleanup(
        "EXP-STAGE4-CLEAN-POSTFLIGHT", root=str(tmp_path)
    )
    assert cleanup["verified_zero_chaos"] is True
    assert cleanup["poisoned_environment"] is False
    assert cleanup["verdict_preserved"] is True
    assert not pathlib.Path(_poisoned_environment_path(str(tmp_path))).exists()


def test_postflight_cleanup_retries_then_poisons_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    chaos_json = json.dumps({"items": [{"metadata": {"name": "leftover"}}]})
    calls = []

    def kubectl(args):
        calls.append(args)
        if args[0] == "delete":
            return {"success": True, "stdout": "deleted", "returncode": 0}
        return {"success": True, "stdout": chaos_json, "returncode": 0}

    monkeypatch.setattr(runner, "run_kubectl", kubectl)
    cleanup = reconcile_stage4_postflight_cleanup(
        "EXP-STAGE4-POISON-TEST", root=str(tmp_path)
    )
    assert cleanup["verified_zero_chaos"] is False
    assert cleanup["poisoned_environment"] is True
    assert len(cleanup["attempts"]) == MAX_POSTFLIGHT_CLEANUP_ATTEMPTS
    assert len(calls) == 2 * MAX_POSTFLIGHT_CLEANUP_ATTEMPTS
    assert pathlib.Path(_poisoned_environment_path(str(tmp_path))).exists()
    with pytest.raises(RuntimeError, match="environment is poisoned"):
        reserve_experiment_attempt(
            "EXP-STAGE4-AFTER-POISON",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=str(tmp_path),
        )


def test_postflight_cleanup_requires_successful_zero_chaos_observation(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        runner,
        "run_kubectl",
        lambda args: (
            {"success": True, "stdout": "deleted"}
            if args[0] == "delete"
            else {"success": False, "stdout": json.dumps({"items": []})}
        ),
    )
    cleanup = reconcile_stage4_postflight_cleanup(
        "EXP-STAGE4-FAILED-POSTFLIGHT", root=str(tmp_path)
    )
    assert cleanup["verified_zero_chaos"] is False
    assert pathlib.Path(_poisoned_environment_path(str(tmp_path))).exists()


def test_postflight_cleanup_refuses_existing_evidence_before_mutation(monkeypatch, tmp_path):
    cleanup_path = (
        tmp_path
        / "artifacts"
        / "evidence"
        / "stage4"
        / "EXP-STAGE4-IMMUTABLE-CLEANUP.cleanup.json"
    )
    cleanup_path.parent.mkdir(parents=True)
    original = b"{\"preserved\": true}\n"
    cleanup_path.write_bytes(original)
    kubectl_calls = []
    monkeypatch.setattr(runner, "run_kubectl", lambda args: kubectl_calls.append(args))

    with pytest.raises(RuntimeError, match="overwrite Stage 4 cleanup evidence"):
        reconcile_stage4_postflight_cleanup(
            "EXP-STAGE4-IMMUTABLE-CLEANUP", root=str(tmp_path)
        )

    assert kubectl_calls == []
    assert cleanup_path.read_bytes() == original


def _model_response(content="", tool_calls=None, finish_reason="stop"):
    response = MagicMock()
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    response.json.return_value = {
        "choices": [{"message": message, "finish_reason": finish_reason}]
    }
    return response


def _native_call(name, args, id):
    return {
        "id": id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def test_sf002_degradation_requires_absolute_and_relative_increase():
    passed = sf002_degradation_decision(
        {"max_cores": 0.10},
        {"max_cores": 1.00},
    )
    failed_absolute = sf002_degradation_decision(
        {"max_cores": 0.10},
        {"max_cores": 0.24},
    )
    failed_ratio = sf002_degradation_decision(
        {"max_cores": 0.80},
        {"max_cores": 1.00},
    )
    missing = sf002_degradation_decision({"max_cores": None}, {"max_cores": 1.0})
    assert passed["passed"] is True
    # Amended envelope contract (2026-08-24): absolute requirement is >= 0.15
    # cores; a +0.14-core increase is below it even though the relative ratio
    # would be large.
    assert failed_absolute["passed"] is False
    assert failed_ratio["passed"] is False
    assert missing["measured"] is False


def test_sf002_telemetry_uses_targeted_cadvisor_metric():
    response = {
        "success": True,
        "result": [{"value": [1, "0.75"]}, {"value": [2, "bad"]}],
    }
    with patch("agents.tools.prometheus.promql_query", return_value=response) as query:
        telemetry = collect_sf002_cpu_telemetry(time_unix=123.0)
    query.assert_called_once_with(DEGRADATION_QUERY, time_unix=123.0)
    assert telemetry["query_success"] is True
    assert telemetry["samples_cores"] == [0.75]
    assert telemetry["max_cores"] == 0.75


def _deployment_item(status: dict) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "paymentservice", "namespace": "default", "labels": {"app": "paymentservice"}},
        "spec": {"replicas": 1, "selector": {"matchLabels": {"app": "paymentservice"}}},
        "status": status,
    }


def _pod_item(phase: str = "Running", containers_ready: bool = True) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "paymentservice-754c98b599-rjvxx", "namespace": "default", "labels": {"app": "paymentservice"}},
        "status": {
            "phase": phase,
            "containerStatuses": [{"name": "paymentservice", "ready": containers_ready, "started": True}],
        },
    }


def test_baseline_health_requires_ready_desired_replicas():
    healthy = json.dumps({
        "items": [{"status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1}}]
    })
    not_ready = json.dumps({
        "items": [{"status": {"replicas": 1, "readyReplicas": 0, "availableReplicas": 0}}]
    })
    zero_desired = json.dumps({
        "items": [{"status": {"replicas": 0, "readyReplicas": 0, "availableReplicas": 0}}]
    })
    missing_replica_state = json.dumps({"items": [{"metadata": {"name": "paymentservice"}}]})
    ready_without_availability = json.dumps({
        "items": [{"status": {"replicas": 1, "readyReplicas": 1}}],
    })
    empty = json.dumps({"items": []})
    assert _paymentservice_baseline_healthy(healthy) is True
    assert _paymentservice_baseline_healthy(not_ready) is False
    assert _paymentservice_baseline_healthy(zero_desired) is False
    assert _paymentservice_baseline_healthy(missing_replica_state) is False
    # Availability is informational in the merged contract (mirrors the
    # Deployment readiness gate in agents/verifier.py): readiness is gated,
    # availability is not.
    assert _paymentservice_baseline_healthy(ready_without_availability) is True
    assert _paymentservice_baseline_healthy(empty) is False
    assert _paymentservice_baseline_healthy("not-json") is False


def test_stage4_preflight_supplies_deployment_schema_to_baseline_helper():
    deployment_payload = json.dumps({"items": [_deployment_item({"replicas": 1, "readyReplicas": 1, "availableReplicas": 1})]})
    with patch(
        "scripts.run_stage4_golden_incident.run_kubectl",
        return_value={"success": True, "stdout": deployment_payload, "returncode": 0},
    ) as kubectl:
        healthy, workloads = _paymentservice_baseline_check()
    kubectl.assert_called_once_with(
        ["get", "deployments", "-n", "default", "-l", "app=paymentservice", "-o", "json"]
    )
    assert healthy is True
    assert json.loads(workloads["stdout"])["items"][0]["kind"] == "Deployment"


def test_stage4_preflight_does_not_rely_on_pod_shaped_input():
    pod_payload = json.dumps({"items": [_pod_item()]})
    with patch(
        "scripts.run_stage4_golden_incident.run_kubectl",
        return_value={"success": True, "stdout": pod_payload, "returncode": 0},
    ):
        healthy, workloads = _paymentservice_baseline_check()
    assert healthy is False
    assert json.loads(workloads["stdout"])["items"][0]["kind"] == "Pod"
    # Directly: Pod JSON never satisfies the Deployment-schema helper even for
    # fully Running/ready pods (no replicas/readyReplicas fields -> fail closed).
    assert _paymentservice_baseline_healthy(pod_payload) is False


def test_stage4_preflight_fails_closed_when_deployment_query_fails():
    deployment_payload = json.dumps({"items": [_deployment_item({"replicas": 1, "readyReplicas": 1, "availableReplicas": 1})]})
    with patch(
        "scripts.run_stage4_golden_incident.run_kubectl",
        return_value={"success": False, "stderr": "connection refused", "returncode": 1, "stdout": deployment_payload},
    ):
        healthy, _workloads = _paymentservice_baseline_check()
    assert healthy is False


def test_attempt_lifecycle_blocks_duplicate_and_crashed_attempts():
    root = _attempt_root()
    reservation = reserve_experiment_attempt(
        "EXP-STAGE4-TEST",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    marker = _attempt_marker_path("EXP-STAGE4-TEST", root)
    assert pathlib.Path(marker).exists()
    with pytest.raises(RuntimeError, match="already exists"):
        reserve_experiment_attempt(
            "EXP-STAGE4-TEST",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )

    consume_experiment_attempt(reservation, attempt_root=root)
    consumed = json.loads(pathlib.Path(marker).read_text(encoding="utf-8"))
    assert consumed["state"] == ATTEMPT_STATE_CONSUMED
    with pytest.raises(RuntimeError, match="already exists"):
        reserve_experiment_attempt(
            "EXP-STAGE4-TEST",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )

    complete_experiment_attempt(reservation, attempt_root=root)
    completed = json.loads(pathlib.Path(marker).read_text(encoding="utf-8"))
    assert completed["state"] == ATTEMPT_STATE_COMPLETED
    with pytest.raises(RuntimeError, match="already exists"):
        reserve_experiment_attempt(
            "EXP-STAGE4-TEST",
            selected_model=APPROVED_G4_MODEL,
            main_sha="test-sha",
            attempt_root=root,
        )


def test_unused_reservation_is_released_after_pre_fault_abort():
    root = _attempt_root()
    reservation = reserve_experiment_attempt(
        "EXP-STAGE4-ABORT",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    assert release_experiment_reservation(reservation, attempt_root=root) is True
    assert pathlib.Path(_attempt_marker_path("EXP-STAGE4-ABORT", root)).exists() is False

    replacement = reserve_experiment_attempt(
        "EXP-STAGE4-AFTER-RELEASE",
        selected_model=APPROVED_G4_MODEL,
        main_sha="test-sha",
        attempt_root=root,
    )
    assert replacement["state"] == ATTEMPT_STATE_RESERVED


def test_causal_predicate_requires_degradation_settling_and_primary_evidence():
    incident = _valid_incident()
    failed = evaluate_causal_g4_predicate(
        baseline_healthy=True,
        injection_success=True,
        fault_observed=True,
        incident_result=incident,
        harness_repaired_pre_verification=False,
        degradation_proven=False,
        settling_completed=True,
        primary_evidence_persisted=True,
    )
    unsettled = evaluate_causal_g4_predicate(
        baseline_healthy=True,
        injection_success=True,
        fault_observed=True,
        incident_result=incident,
        harness_repaired_pre_verification=False,
        degradation_proven=True,
        settling_completed=False,
        primary_evidence_persisted=True,
    )
    unpersisted = evaluate_causal_g4_predicate(
        baseline_healthy=True,
        injection_success=True,
        fault_observed=True,
        incident_result=incident,
        harness_repaired_pre_verification=False,
        degradation_proven=True,
        settling_completed=True,
        primary_evidence_persisted=False,
    )
    assert failed["criteria"]["3_fault_observed_pre_trigger"] is False
    assert unsettled["criteria"]["13_objective_env_resolved"] is False
    assert unpersisted["criteria"]["15_evidence_persisted"] is False


def test_every_remediation_model_response_is_persisted_before_branching():
    prose = MagicMock()
    prose.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "I will investigate."}, "finish_reason": "stop"}]
    }
    action = MagicMock()
    action.json.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-hardening",
                    "type": "function",
                    "function": {
                        "name": "chaos_stop_experiment",
                        "arguments": json.dumps({
                            "kind": "StressChaos",
                            "name": "sf-002-paymentservice-cpu",
                            "namespace": "chaos-mesh",
                        }),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }]
    }
    conclusion = MagicMock()
    conclusion.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": '{"outcome":"unresolved"}'}, "finish_reason": "stop"}]
    }
    with patch("agents.coordinator.post_with_retry", side_effect=[prose, action, conclusion]):  # noqa: SIM117
        with patch("agents.coordinator.require_audit_log"):
            with patch(
                "agents.tools.chaos._run",
                return_value={"success": True, "stdout": "deleted", "returncode": 0},
            ):
                result = asyncio.run(
                    __import__("agents.coordinator", fromlist=["call_agent"]).call_agent(
                        "remediation",
                        {"incident_id": "inc-turns"},
                    )
                )
    records = [step for step in result["trajectory"] if step.get("kind") == "model_turn"]
    assert [record["turn"] for record in records] == [0, 1, 2]
    assert all(
        isinstance(record["executed_tool_calls"], list)
        and all(isinstance(name, str) for name in record["executed_tool_calls"])
        for record in records
    )
    assert records[0]["assistant_text"] == "I will investigate."
    assert records[0]["retry"]["reason"] == "remediation_no_tool_call_retry"
    assert records[1]["native_tool_calls"][0]["name"] == "chaos_stop_experiment"
    assert records[1]["validation_state"] == "validated"
    assert records[1]["executed_tool_calls"] == ["chaos_stop_experiment"]
    assert records[2]["conclusion_present"] is True


def test_forced_conclusion_model_response_is_persisted():
    initial_prose = MagicMock()
    initial_prose.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "I need help concluding."}, "finish_reason": "stop"}]
    }
    retry_prose = MagicMock()
    retry_prose.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "Still no structured call."}, "finish_reason": "stop"}]
    }
    forced = MagicMock()
    forced.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": '{"outcome":"unresolved"}'},
            "finish_reason": "stop",
        }]
    }
    with patch(
        "agents.coordinator.post_with_retry",
        side_effect=[initial_prose, retry_prose, forced],
    ), patch("agents.coordinator.require_audit_log"):
        result = asyncio.run(__import__("agents.coordinator", fromlist=["call_agent"]).call_agent(
            "remediation",
            {"incident_id": "inc-forced"},
        ))
    records = [step for step in result["trajectory"] if step.get("kind") == "model_turn"]
    assert [record["turn"] for record in records] == [0, 1, 1]
    assert records[2]["turn_kind"] == "forced_conclusion"
    assert records[2]["finish_reason"] == "stop"
    assert records[2]["validation_state"] == "final_conclusion"


def test_max_turn_forced_conclusion_has_no_attributed_execution():
    responses = [
        _model_response(
            "",
            tool_calls=[_native_call("promql_query", {"query": f"q{i}"}, f"c{i}")],
            finish_reason="tool_calls",
        )
        for i in range(10)
    ]
    responses.append(_model_response('{"outcome":"unresolved"}'))
    with patch("agents.coordinator.post_with_retry", side_effect=responses) as post:  # noqa: SIM117
        with patch("agents.coordinator.require_audit_log"):
            with patch(
                "agents.tools.prometheus.promql_query",
                return_value={"success": True, "result": []},
            ):
                result = asyncio.run(__import__("agents.coordinator", fromlist=["call_agent"]).call_agent(
                    "remediation",
                    {"incident_id": "inc-max-turn-forced"},
                ))

    assert post.call_count == 11
    records = [entry for entry in result["trajectory"] if entry.get("kind") == "model_turn"]
    ordinary_records = records[:-1]
    forced_record = records[-1]
    assert [record["turn"] for record in ordinary_records] == list(range(10))
    assert forced_record["turn"] == 10
    assert forced_record["turn_kind"] == "forced_conclusion"
    assert forced_record["validation_state"] == "final_conclusion"
    assert forced_record["executed_tool_calls"] == []
    assert all(
        isinstance(record["executed_tool_calls"], list)
        and all(isinstance(name, str) for name in record["executed_tool_calls"])
        for record in records
    )


def test_settling_is_bounded_and_preserves_verifier_call_contract():
    responses = [
        SimpleNamespace(env_resolved=False, verification_status="failed", failed_checks=["chaos_mesh_cleared"]),
        SimpleNamespace(env_resolved=True, verification_status="passed", failed_checks=[]),
    ]

    def verify(**kwargs):
        assert kwargs["scenario_id"] == "single_fault/sf-002"
        assert kwargs["agent_claimed_resolved"] is True
        return responses.pop(0)

    with patch("agents.verifier.verify_environment", side_effect=verify):  # noqa: SIM117
        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            from agents.coordinator import settle_environment

            report = asyncio.run(settle_environment(
                scenario_id="single_fault/sf-002",
                agent_claimed_resolved=True,
                alert={"commonLabels": {}},
                incident_context={},
            ))
    assert report["settled"] is True
    assert len(report["observations"]) == 2
    assert report["timeout_seconds"] == 30
    sleep.assert_called_once_with(2)
