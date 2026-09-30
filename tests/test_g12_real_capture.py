"""Non-live contracts for the governed Stage 12 evidence wrapper."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from agents.policy_remediation import run_policy_remediation
from scripts import run_g12_integrated_episode as capture
from training.grpo_environment import DirectPolicyEnvironment

LIVE_EXECUTION = {
    "execute_live_chaos": True,
    "kube_context": capture.METRICS_SERVER_CONTEXT,
}
EXPERIMENT_ID = "EXP-STAGE4-SF002-999"
INCIDENT_ID = "inc-test-999"
SOURCE_SHA = "a" * 40
INCIDENT_ALERT = {"commonLabels": {"service": "paymentservice"}}
INCIDENT_ANCHORS = {
    "alert_name": "",
    "primary_service": "paymentservice",
    "namespace": "default",
    "labels": {"service": "paymentservice"},
    "original_description": "",
}
_NO_CLEANUP_SIDECAR = object()


def _complete_cleanup_record(*, verified_zero_chaos=True):
    items = [] if verified_zero_chaos else [{"metadata": {"name": "fixture-leftover"}}]
    return {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "timing": "after_verdict_persisted",
        "affects_env_resolved": False,
        "verdict_preserved": True,
        "max_attempts": 1,
        "attempts": [{
            "attempt": 1,
            "started_at": "2026-09-28T12:00:00+00:00",
            "completed_at": "2026-09-28T12:00:01+00:00",
            "delete_result": {"success": True},
            "postflight_result": {
                "success": True,
                "stdout": json.dumps({"items": items}),
            },
            "active_chaos_count": len(items),
            "verified_zero_chaos": verified_zero_chaos,
        }],
        "result": {"success": True},
        "observed_leftover_chaos_sha256": None,
        "verified_zero_chaos": verified_zero_chaos,
        "poisoned_environment": not verified_zero_chaos,
        "timestamp": "2026-09-28T12:00:01+00:00",
    }


def _complete_executed_negative_step():
    action = {
        "tool": "chaos_stop_experiment",
        "arguments": {
            "kind": "StressChaos",
            "name": "fixture-experiment",
            "namespace": "chaos-mesh",
        },
        "agent_claimed_resolved": True,
    }
    raw_policy_output = json.dumps(action)
    tool_result = {"success": False, "error": "experiment remained active"}
    verification = {
        "verification_status": "failed",
        "env_resolved": False,
        "failed_checks": ["chaos_stopped"],
        "checks": [{
            "name": "chaos_stopped",
            "target": "StressChaos",
            "passed": False,
            "required": True,
        }],
    }
    state = {
        "incident_id": INCIDENT_ID,
        "alert": json.loads(json.dumps(INCIDENT_ALERT)),
        "incident_anchors": json.loads(json.dumps(INCIDENT_ANCHORS)),
        "env_resolved": False,
    }
    next_state = {
        **state,
        "previous_policy_action": action,
        "previous_tool_result": tool_result,
        "verification": verification,
        "env_resolved": False,
    }
    return {
        "index": 0,
        "started_at": "2026-09-28T12:00:00+00:00",
        "completed_at": "2026-09-28T12:00:01+00:00",
        "state": state,
        "environment_status": "ok",
        "pre_action_observation": {
            "tool": "chaos_list_experiments",
            "success": True,
            "observation_status": "observed",
            "history": None,
            "active_experiments": [{
                "kind": "StressChaos",
                "name": "fixture-experiment",
                "namespace": "chaos-mesh",
            }],
        },
        "raw_policy_output": raw_policy_output,
        "parsed_action": action,
        "executed_actions": [{
            "tool": action["tool"],
            "arguments": action["arguments"],
            "result": tool_result,
        }],
        "verification": verification,
        "settling": {
            "started_at": "2026-09-28T12:00:00+00:00",
            "completed_at": "2026-09-28T12:00:01+00:00",
            "duration_seconds": 1.0,
            "timeout_seconds": 30.0,
            "poll_interval_seconds": 1.0,
            "settled": False,
            "observations": [{
                "timestamp": "2026-09-28T12:00:01+00:00",
                "elapsed_seconds": 1.0,
                "env_resolved": False,
                "verification_status": "failed",
                "failed_checks": ["chaos_stopped"],
            }],
        },
        "env_resolved": False,
        "terminal_block": None,
        "next_state": next_state,
    }


def _complete_blocked_step():
    step = _complete_executed_negative_step()
    step["environment_status"] = "blocked"
    step["pre_action_observation"] = None
    step["executed_actions"] = []
    step["verification"] = None
    step["settling"] = None
    step["terminal_block"] = {
        "category": "approval_required",
        "reason": "Mutation requires the severity-specific approval decision",
    }
    step["next_state"].update({
        "previous_tool_result": None,
        "verification": None,
    })
    return step


def _complete_unscorable_step():
    step = _complete_executed_negative_step()
    step["environment_status"] = "unscorable"
    verification = {
        "verification_status": "inconclusive",
        "env_resolved": False,
        "failed_checks": ["telemetry"],
        "checks": [{
            "name": "telemetry",
            "target": "prometheus",
            "passed": False,
            "required": True,
        }],
    }
    step["verification"] = verification
    step["settling"] = {
        "status": "unscorable",
        "settled": False,
        "stable": False,
        "stable_observations": 0,
        "observation_count": 1,
        "verification_status": "inconclusive",
        "last_verification": verification,
        "observations": [],
        "failure": "post_action_verification_nonconclusive",
    }
    step["next_state"]["verification"] = verification
    return step


def _complete_resolved_step():
    step = _complete_executed_negative_step()
    verification = {
        "verification_status": "passed",
        "env_resolved": True,
        "failed_checks": [],
        "checks": [{
            "name": "chaos_stopped",
            "target": "StressChaos",
            "passed": True,
            "required": True,
        }],
    }
    step["verification"] = verification
    step["settling"].update({
        "status": "settled",
        "settled": True,
        "observations": [{
            **step["settling"]["observations"][0],
            "env_resolved": True,
            "verification_status": "passed",
            "failed_checks": [],
        }],
    })
    step["env_resolved"] = True
    step["next_state"].update({
        "verification": verification,
        "env_resolved": True,
    })
    return step


def _complete_followup_step(first, *, blocked=False):
    second = _complete_blocked_step() if blocked else _complete_executed_negative_step()
    second["index"] = 1
    second["started_at"] = "2026-09-28T12:00:02+00:00"
    second["completed_at"] = "2026-09-28T12:00:03+00:00"
    second["parsed_action"]["arguments"]["name"] = "fixture-experiment-second"
    second["raw_policy_output"] = json.dumps(second["parsed_action"])
    if second["executed_actions"]:
        second["executed_actions"][0]["arguments"] = json.loads(
            json.dumps(second["parsed_action"]["arguments"])
        )
        second["pre_action_observation"]["active_experiments"][0]["name"] = (
            "fixture-experiment-second"
        )
    second["state"] = json.loads(json.dumps(first["next_state"]))
    second["next_state"] = {
        **second["state"],
        "previous_policy_action": second["parsed_action"],
        "previous_tool_result": (
            second["executed_actions"][0]["result"]
            if second["executed_actions"]
            else None
        ),
        "verification": second["verification"],
        "env_resolved": second["env_resolved"],
    }
    return second


def _complete_error_step():
    step = _complete_unscorable_step()
    step["verification"] = None
    step["settling"].update({
        "status": "error",
        "verification_status": "error",
        "last_verification": None,
        "failure": "post_action_verification_error:RuntimeError",
    })
    step["next_state"]["verification"] = None
    return step


def _complete_remediation(policy_steps):
    last = policy_steps[-1]
    status = (
        "blocked"
        if last["terminal_block"] is not None
        else "resolved"
        if last["env_resolved"]
        else "unresolved"
    )
    executed_actions = [
        action
        for step in policy_steps
        for action in step["executed_actions"]
    ]
    return {
        "policy_steps": policy_steps,
        "final": {
            "incident_id": INCIDENT_ID,
            "status": status,
            "outcome": status,
            "executed_actions": executed_actions,
            "verification": last["verification"],
            "env_resolved": last["env_resolved"],
            "terminal_block": last["terminal_block"],
            "policy_backend": "checkpoint",
            "generation_seed": 17,
        },
    }


def _complete_two_step_chain():
    first = _complete_executed_negative_step()
    return [first, _complete_followup_step(first)]


def _collect_fixture(
    tmp_path,
    remediation,
    *,
    seed: int = 17,
    include_recommender: bool = True,
    cleanup_sidecar=_NO_CLEANUP_SIDECAR,
    prefault_sidecar: bytes | None = None,
    primary_incident_anchors=INCIDENT_ANCHORS,
):
    root = tmp_path / "repo"
    evidence_dir = root / "artifacts/evidence/stage4"
    trajectory_dir = root / "artifacts/trajectories"
    evidence_dir.mkdir(parents=True)
    trajectory_dir.mkdir(parents=True)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    primary = {
        "experiment_id": EXPERIMENT_ID,
        "gate_g4_pass": False,
        "duration_seconds": 31.5,
        "source_identity": {"git_commit": SOURCE_SHA},
        "preflight_evidence": {"persisted_before_injection": True},
        "protocol_profile": {"model": {"name": "fixture"}},
        "phases": {
            "coordinator_execution": {
                "incident_id": INCIDENT_ID,
                "incident_anchors": primary_incident_anchors,
            }
        },
    }
    incident = {
        "incident_id": INCIDENT_ID,
        "alert": INCIDENT_ALERT,
        "incident_anchors": INCIDENT_ANCHORS,
        "triage": {"final": {"severity": "P1"}},
        "diagnosis": {"final": {"root_cause": "observed pressure"}},
        "approval": {"decision": "approved"},
        "remediation": remediation,
        "settling": {"settled": False},
        "verification": {"env_resolved": False},
        "comms": {"final": {"summary": "Unresolved"}},
    }
    if include_recommender:
        incident["recommender"] = {
            "status": "executed",
            "recommended_runbooks": [{"runbook_id": "RB-1"}],
        }
    (evidence_dir / f"{EXPERIMENT_ID}.json").write_text(
        json.dumps(primary), encoding="utf-8"
    )
    if cleanup_sidecar is not None:
        sidecar = (
            _complete_cleanup_record()
            if cleanup_sidecar is _NO_CLEANUP_SIDECAR
            else cleanup_sidecar
        )
        cleanup_text = sidecar if isinstance(sidecar, str) else json.dumps(sidecar)
        (evidence_dir / f"{EXPERIMENT_ID}.cleanup.json").write_text(
            cleanup_text, encoding="utf-8"
        )
    if prefault_sidecar is not None:
        attempts_dir = evidence_dir / ".attempts"
        attempts_dir.mkdir()
        (attempts_dir / f"{EXPERIMENT_ID}.prefault.json").write_bytes(
            prefault_sidecar
        )
    (trajectory_dir / f"{INCIDENT_ID}.json").write_text(
        json.dumps(incident), encoding="utf-8"
    )
    manifest = capture.collect_bundle(
        root=root,
        bundle=bundle,
        experiment_id=EXPERIMENT_ID,
        source_sha=SOURCE_SHA,
        checkpoint={"checkpoint_sha256": "b" * 64},
        seed=seed,
        process_exit_code=1,
        checkpoint_postflight_verified=True,
    )
    return manifest, bundle


def test_capture_accepts_missing_recommender_without_certifying_or_scoring(tmp_path):
    manifest, bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([_complete_executed_negative_step()]),
        include_recommender=False,
    )

    trajectory_asset = manifest["assets"][f"trajectory-{INCIDENT_ID}.json"]
    preserved = (bundle / trajectory_asset["path"]).read_bytes()

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["gate_certification"] == "NOT_CERTIFIED"
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["reward"] is None
    assert manifest["time_to_resolve_s"] is None
    assert manifest["record_fields_present"]["recommender"] is False
    assert "recommender" not in json.loads(preserved)
    assert trajectory_asset["sha256"] == hashlib.sha256(preserved).hexdigest()
    assert trajectory_asset["size_bytes"] == len(preserved)


def test_cli_requires_explicit_live_execution_before_any_work(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_g12_integrated_episode",
            "--checkpoint", str(tmp_path / "missing"),
            "--experiment-id", "EXP-STAGE4-SF002-999",
            "--bundle-dir", str(tmp_path / "bundle"),
            "--seed", "17",
        ],
    )
    monkeypatch.setattr(
        capture,
        "run_live_episode",
        lambda **_kwargs: pytest.fail("live harness invoked without opt-in"),
    )
    with pytest.raises(SystemExit) as exc:
        capture.main()
    assert exc.value.code == 2
    assert not (tmp_path / "bundle").exists()


def test_cli_requires_named_stage4_context_before_any_work(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_g12_integrated_episode",
            "--checkpoint", str(tmp_path / "missing"),
            "--experiment-id", "EXP-STAGE4-SF002-999",
            "--bundle-dir", str(tmp_path / "bundle"),
            "--seed", "17",
            "--execute-live-chaos",
        ],
    )
    monkeypatch.setattr(
        capture,
        "run_live_episode",
        lambda **_kwargs: pytest.fail("live harness invoked without context"),
    )
    with pytest.raises(SystemExit) as exc:
        capture.main()
    assert exc.value.code == 2
    assert not (tmp_path / "bundle").exists()


def test_direct_live_wrapper_requires_opt_in_and_context(monkeypatch, tmp_path):
    monkeypatch.setattr(
        capture,
        "_source_sha",
        lambda _root: pytest.fail("source probed before live authorization"),
    )
    kwargs = {
        "checkpoint": tmp_path / "checkpoint",
        "experiment_id": "EXP-STAGE4-SF002-999",
        "bundle": tmp_path / "bundle",
        "seed": 17,
    }
    with pytest.raises(PermissionError, match="--execute-live-chaos"):
        capture.run_live_episode(**kwargs)
    with pytest.raises(ValueError, match="--kube-context"):
        capture.run_live_episode(**kwargs, execute_live_chaos=True)
    assert not kwargs["bundle"].exists()


def test_negative_integrated_attempt_is_archived_without_a_gate_claim(tmp_path):
    step = _complete_executed_negative_step()
    manifest, bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"
    assert manifest["checkpoint_postflight_verified"] is True
    assert manifest["recorded_g4_verdict"] is False
    assert manifest["recorded_env_resolved"] is False
    assert manifest["time_to_resolve_s"] is None and manifest["reward"] is None
    assert manifest["policy_step_count"] == 1
    assert len(manifest["assets"]) == 3
    for asset in manifest["assets"].values():
        assert asset["sha256"] == hashlib.sha256((bundle / asset["path"]).read_bytes()).hexdigest()
    assert json.loads((bundle / "g12_capture_manifest.json").read_text()) == manifest


@pytest.mark.parametrize(
    ("mutation", "observed_seed", "problem"),
    [
        ("mismatch", 18, "policy_generation_seed_mismatch"),
        ("missing", None, "policy_generation_seed_missing"),
        ("boolean", True, "policy_generation_seed_malformed"),
        ("string", "17", "policy_generation_seed_malformed"),
        ("float", 17.0, "policy_generation_seed_malformed"),
        ("negative", -1, "policy_generation_seed_malformed"),
    ],
)
def test_untrusted_generation_seed_makes_capture_incomplete_without_rewriting_evidence(
    tmp_path, mutation, observed_seed, problem
):
    remediation = _complete_remediation([_complete_executed_negative_step()])
    final = remediation["final"]
    if mutation == "missing":
        final.pop("generation_seed")
    else:
        final["generation_seed"] = observed_seed

    manifest, bundle = _collect_fixture(tmp_path, remediation)

    trajectory_name = f"trajectory-{INCIDENT_ID}.json"
    trajectory_asset = manifest["assets"][trajectory_name]
    source = (
        tmp_path
        / "repo"
        / "artifacts"
        / "trajectories"
        / f"{INCIDENT_ID}.json"
    )
    copied = bundle / trajectory_asset["path"]
    raw = copied.read_bytes()
    assert raw == source.read_bytes()
    assert trajectory_asset["sha256"] == hashlib.sha256(raw).hexdigest()
    assert trajectory_asset["size_bytes"] == len(raw)
    assert manifest["status"] == "INCOMPLETE"
    assert problem in manifest["problems"]
    assert manifest["policy_seed"] == 17
    assert manifest["recorded_g4_verdict"] is False
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"
    assert manifest["reward"] is None
    assert manifest["time_to_resolve_s"] is None


def test_matching_generation_seed_remains_reviewable_without_a_gate_claim(tmp_path):
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([_complete_executed_negative_step()]),
    )

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["policy_seed"] == 17
    assert manifest["recorded_g4_verdict"] is False
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"
    assert manifest["reward"] is None
    assert manifest["time_to_resolve_s"] is None


@pytest.mark.parametrize(
    "requested_seed", [True, "17", 17.0, -1, float("nan"), float("inf")]
)
def test_malformed_requested_seed_cannot_make_capture_reviewable(
    tmp_path, requested_seed
):
    manifest, bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([_complete_executed_negative_step()]),
        seed=requested_seed,
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_seed_request_malformed" in manifest["problems"]
    assert manifest["policy_seed"] is None
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["reward"] is None
    assert "NaN" not in (bundle / "g12_capture_manifest.json").read_text(encoding="utf-8")
    assert "Infinity" not in (bundle / "g12_capture_manifest.json").read_text(encoding="utf-8")


def test_guarded_pre_action_observation_survives_capture_without_gate_claim(tmp_path):
    step = _complete_executed_negative_step()
    expected_observation = json.loads(json.dumps(step["pre_action_observation"]))
    manifest, bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )
    trajectory = json.loads(
        (bundle / f"trajectory-{INCIDENT_ID}.json").read_text(encoding="utf-8")
    )
    persisted_step = trajectory["remediation"]["policy_steps"][0]

    assert persisted_step["pre_action_observation"] == expected_observation
    assert persisted_step["verification"]["verification_status"] == "failed"
    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["recorded_env_resolved"] is False
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"
    assert manifest["time_to_resolve_s"] is None
    assert manifest["reward"] is None


def test_parsed_action_only_policy_step_is_incomplete(tmp_path):
    manifest, _bundle = _collect_fixture(
        tmp_path,
        {
            "final": {"policy_backend": "checkpoint", "status": "unresolved"},
            "policy_steps": [{
                "parsed_action": {"tool": "chaos_stop_experiment"},
            }],
        },
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_started_at_missing" in manifest["problems"]
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"


@pytest.mark.parametrize(
    ("remediation", "problem"),
    [
        (
            {"final": {"policy_backend": "checkpoint", "status": "unresolved"}},
            "policy_steps_missing",
        ),
        (
            {
                "final": {"policy_backend": "checkpoint", "status": "unresolved"},
                "policy_steps": None,
            },
            "policy_steps_malformed",
        ),
        (
            {
                "final": {"policy_backend": "checkpoint", "status": "unresolved"},
                "policy_steps": [],
            },
            "policy_steps_empty",
        ),
        (
            {
                "final": {"policy_backend": "checkpoint", "status": "unresolved"},
                "policy_steps": [None],
            },
            "policy_step_0_malformed",
        ),
    ],
)
def test_missing_or_malformed_policy_steps_are_incomplete(
    tmp_path, remediation, problem
):
    manifest, _bundle = _collect_fixture(tmp_path, remediation)

    assert manifest["status"] == "INCOMPLETE"
    assert problem in manifest["problems"]


@pytest.mark.parametrize(
    "field",
    [
        "index",
        "started_at",
        "completed_at",
        "environment_status",
        "pre_action_observation",
        "raw_policy_output",
        "parsed_action",
        "executed_actions",
        "verification",
        "settling",
        "terminal_block",
        "state",
        "next_state",
    ],
)
def test_executed_policy_step_requires_complete_capture_fields(tmp_path, field):
    step = _complete_executed_negative_step()
    remediation = _complete_remediation([step])
    del step[field]
    manifest, _bundle = _collect_fixture(tmp_path, remediation)

    assert manifest["status"] == "INCOMPLETE"
    assert f"policy_step_0_{field}_missing" in manifest["problems"]


def test_complete_blocked_policy_step_is_captured_for_review(tmp_path):
    step = _complete_blocked_step()
    assert step["pre_action_observation"] is None
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["policy_step_count"] == 1
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"


def test_failed_runtime_support_read_is_preserved_but_capture_is_incomplete(tmp_path):
    executed = []

    class Policy:
        def generate(self, state, *, seed, generation_config):
            return _complete_executed_negative_step()["raw_policy_output"]

    environment = DirectPolicyEnvironment(
        tool_registry={
            "chaos_stop_experiment": lambda **kwargs: executed.append(kwargs),
            "chaos_list_experiments": lambda: {
                "success": True,
                "observation_status": "unavailable",
                "active_experiments": [{
                    "kind": "StressChaos",
                    "name": "fixture-experiment",
                    "namespace": "chaos-mesh",
                }],
                "access_token": "reader-secret-must-not-be-persisted",
            },
        },
        policy_check=lambda role, tool, arguments, state: None,
        execute_live_chaos=True,
        kube_context=LIVE_EXECUTION["kube_context"],
    )
    remediation = asyncio.run(
        run_policy_remediation(
            policy=Policy(),
            state={
                "incident_id": INCIDENT_ID,
                "alert": INCIDENT_ALERT,
                "incident_anchors": INCIDENT_ANCHORS,
                "triage": {"severity": "P2"},
            },
            scenario_id="single_fault/sf-002",
            environment=environment,
            seed=7,
            generation_config={"do_sample": False},
        )
    )

    assert executed == []
    step = remediation["policy_steps"][0]
    assert step["terminal_block"]["category"] == "missing_evidence"
    assert step["pre_action_observation"]["observation_status"] == "unavailable"
    manifest, bundle = _collect_fixture(tmp_path, remediation, seed=7)
    copied = json.loads(
        (bundle / f"trajectory-{INCIDENT_ID}.json").read_text(encoding="utf-8")
    )
    assert copied["remediation"]["policy_steps"][0]["pre_action_observation"] == (
        step["pre_action_observation"]
    )
    assert "reader-secret-must-not-be-persisted" not in json.dumps(copied)
    assert manifest["status"] == "INCOMPLETE"
    assert set(manifest["problems"]) == {
        "policy_step_0_pre_action_observation_unverified",
        "checkpoint_policy_execution_unverified",
    }
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"
    assert manifest["time_to_resolve_s"] is None
    assert manifest["reward"] is None


def test_complete_unscorable_policy_step_is_captured_for_review(tmp_path):
    step = _complete_unscorable_step()
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["policy_step_count"] == 1
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"


@pytest.mark.parametrize(
    ("observation", "problem"),
    [
        (None, "policy_step_0_pre_action_observation_missing"),
        ("not-an-object", "policy_step_0_pre_action_observation_malformed"),
        (
            {
                "tool": "argocd_app_history",
                "success": True,
                "observation_status": "observed",
                "history": None,
                "active_experiments": None,
            },
            "policy_step_0_pre_action_observation_mismatch",
        ),
        (
            {
                "tool": "chaos_list_experiments",
                "success": False,
                "observation_status": "observed",
                "history": None,
                "active_experiments": [{
                    "kind": "StressChaos",
                    "name": "fixture-experiment",
                    "namespace": "chaos-mesh",
                }],
            },
            "policy_step_0_pre_action_observation_mismatch",
        ),
        (
            {
                "tool": "chaos_list_experiments",
                "success": True,
                "observation_status": "unavailable",
                "history": None,
                "active_experiments": [{
                    "kind": "StressChaos",
                    "name": "fixture-experiment",
                    "namespace": "chaos-mesh",
                }],
            },
            "policy_step_0_pre_action_observation_mismatch",
        ),
        (
            {
                "tool": "chaos_list_experiments",
                "success": True,
                "observation_status": "observed",
                "history": None,
                "active_experiments": [{
                    "kind": "StressChaos",
                    "name": "different-experiment",
                    "namespace": "chaos-mesh",
                }],
            },
            "policy_step_0_pre_action_observation_mismatch",
        ),
        (
            {
                "tool": "chaos_list_experiments",
                "success": True,
                "observation_status": "observed",
                "history": None,
                "active_experiments": [{
                    "kind": "StressChaos",
                    "name": "fixture-experiment",
                    "namespace": "chaos-mesh",
                }],
                "access_token": "must-not-be-captured",
            },
            "policy_step_0_pre_action_observation_malformed",
        ),
    ],
)
def test_guarded_pre_action_observation_tampering_is_incomplete(
    tmp_path, observation, problem
):
    step = _complete_executed_negative_step()
    step["pre_action_observation"] = observation
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert problem in manifest["problems"]
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"


def test_unguarded_action_cannot_claim_pre_action_observation(tmp_path):
    step = _complete_executed_negative_step()
    action = {
        "tool": "kubectl_scale",
        "arguments": {
            "deployment": "paymentservice",
            "replicas": 2,
            "namespace": "default",
        },
        "agent_claimed_resolved": True,
    }
    step["parsed_action"] = action
    step["raw_policy_output"] = json.dumps(action)
    step["executed_actions"][0]["tool"] = action["tool"]
    step["executed_actions"][0]["arguments"] = action["arguments"]
    step["next_state"]["previous_policy_action"] = action
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_pre_action_observation_unexpected" in manifest["problems"]


@pytest.mark.parametrize(
    ("observed_status", "expected_status", "expected_problem"),
    [
        (None, "CAPTURED_FOR_REVIEW", None),
        ("observed", "CAPTURED_FOR_REVIEW", None),
        ("unavailable", "INCOMPLETE", "policy_step_0_pre_action_observation_mismatch"),
        ([], "INCOMPLETE", "policy_step_0_pre_action_observation_malformed"),
        ({}, "INCOMPLETE", "policy_step_0_pre_action_observation_malformed"),
    ],
)
def test_rollback_history_observation_matches_guarded_action(
    tmp_path, observed_status, expected_status, expected_problem
):
    step = _complete_executed_negative_step()
    action = {
        "tool": "argocd_rollback",
        "arguments": {"app": "paymentservice", "revision": "42"},
        "agent_claimed_resolved": False,
    }
    step["parsed_action"] = action
    step["raw_policy_output"] = json.dumps(action)
    step["executed_actions"][0]["tool"] = action["tool"]
    step["executed_actions"][0]["arguments"] = action["arguments"]
    step["next_state"]["previous_policy_action"] = action
    step["pre_action_observation"] = {
        "tool": "argocd_app_history",
        "success": True,
        "observation_status": observed_status,
        "history": [{"id": 42}],
        "active_experiments": None,
    }
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == expected_status
    if expected_problem:
        assert expected_problem in manifest["problems"]
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"


def test_missing_environment_status_makes_capture_incomplete(tmp_path):
    step = _complete_executed_negative_step()
    del step["environment_status"]
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_environment_status_missing" in manifest["problems"]


@pytest.mark.parametrize(
    ("step_kind", "claimed_status"),
    [
        ("executed", "blocked"),
        ("executed", "unscorable"),
        ("blocked", "ok"),
        ("unscorable", "ok"),
    ],
)
def test_environment_status_must_match_action_and_settlement(
    tmp_path, step_kind, claimed_status
):
    factories = {
        "executed": _complete_executed_negative_step,
        "blocked": _complete_blocked_step,
        "unscorable": _complete_unscorable_step,
    }
    step = factories[step_kind]()
    step["environment_status"] = claimed_status
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_environment_status_mismatch" in manifest["problems"]


@pytest.mark.parametrize("claimed_status", [[], {}, True, "error", None])
def test_malformed_environment_status_is_preserved_as_incomplete(
    tmp_path, claimed_status
):
    step = _complete_executed_negative_step()
    step["environment_status"] = claimed_status
    manifest, bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_environment_status_invalid" in manifest["problems"]
    assert (bundle / "g12_capture_manifest.json").is_file()


def test_unscorable_policy_step_with_explicit_settlement_failure_is_captured(
    tmp_path,
):
    step = _complete_unscorable_step()
    step["verification"] = None
    step["settling"]["status"] = "error"
    step["settling"]["last_verification"] = None
    step["settling"]["failure"] = "post_action_verifier_error:RuntimeError"
    step["next_state"]["verification"] = None
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"


def test_invalid_action_block_is_captured_without_execution(tmp_path):
    step = _complete_blocked_step()
    step["raw_policy_output"] = "not-json"
    step["parsed_action"] = None
    step["terminal_block"]["category"] = "invalid_action"
    step["terminal_block"]["reason"] = "Policy completion must be one JSON object"
    step["next_state"]["previous_policy_action"] = None
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"


@pytest.mark.parametrize("claim", [None, "false", 0])
def test_invalid_resolution_claim_block_is_preserved_by_capture(tmp_path, claim):
    step = _complete_blocked_step()
    payload = json.loads(step["raw_policy_output"])
    payload["agent_claimed_resolved"] = claim
    step["raw_policy_output"] = json.dumps(payload)
    step["parsed_action"] = None
    step["terminal_block"]["category"] = "invalid_action"
    step["terminal_block"]["reason"] = "Policy action agent_claimed_resolved must be a JSON boolean"
    step["next_state"]["previous_policy_action"] = None
    manifest, _bundle = _collect_fixture(tmp_path, _complete_remediation([step]))

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["empirical_claim_allowed"] is False


def test_duplicate_policy_key_cannot_be_accepted_as_executed_capture(tmp_path):
    step = _complete_executed_negative_step()
    step["raw_policy_output"] = step["raw_policy_output"].replace(
        '"tool":', '"tool": "other_action", "tool":', 1,
    )
    manifest, _bundle = _collect_fixture(tmp_path, _complete_remediation([step]))

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_raw_parsed_action_mismatch" in manifest["problems"]


@pytest.mark.parametrize("invalid_json", ["duplicate", "nonfinite", "deep"])
def test_strict_json_invalid_action_block_retains_raw_capture(tmp_path, invalid_json):
    step = _complete_blocked_step()
    if invalid_json == "duplicate":
        raw = step["raw_policy_output"].replace('"tool":', '"tool": "other", "tool":', 1)
    elif invalid_json == "nonfinite":
        raw = step["raw_policy_output"][:-1] + ', "metadata": NaN}'
    else:
        raw = (
            step["raw_policy_output"][:-1] + ', "metadata": '
            + "[" * 1100 + "null" + "]" * 1100 + "}"
        )
    step["raw_policy_output"] = raw
    step["parsed_action"] = None
    step["terminal_block"]["category"] = "invalid_action"
    step["terminal_block"]["reason"] = "Policy completion is not valid finite unambiguous JSON"
    step["next_state"]["previous_policy_action"] = None
    manifest, bundle = _collect_fixture(tmp_path, _complete_remediation([step]))

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["empirical_claim_allowed"] is False
    archived = json.loads((bundle / f"trajectory-{INCIDENT_ID}.json").read_text())
    assert archived["remediation"]["policy_steps"][0]["raw_policy_output"] == raw


def test_tool_unavailable_block_can_retain_parseable_raw_action_without_parsed_action(
    tmp_path,
):
    step = _complete_blocked_step()
    step["parsed_action"] = None
    step["terminal_block"]["category"] = "tool_unavailable"
    step["terminal_block"]["reason"] = "Unknown tool: chaos_stop_experiment"
    step["next_state"]["previous_policy_action"] = None
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"


def test_executed_action_requires_a_tool_result(tmp_path):
    step = _complete_executed_negative_step()
    remediation = _complete_remediation([step])
    del step["executed_actions"][0]["result"]
    manifest, _bundle = _collect_fixture(tmp_path, remediation)

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_executed_action_malformed" in manifest["problems"]


def test_policy_step_requires_matching_next_state_feedback_and_index(tmp_path):
    step = _complete_executed_negative_step()
    remediation = _complete_remediation([step])
    step["next_state"] = {"incident_id": INCIDENT_ID}
    step["index"] = True
    manifest, _bundle = _collect_fixture(tmp_path, remediation)

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_index_invalid" in manifest["problems"]
    assert "policy_step_0_next_state_feedback_mismatch" in manifest["problems"]


def test_blocked_step_cannot_claim_an_action_was_executed(tmp_path):
    step = _complete_blocked_step()
    remediation = _complete_remediation([step])
    step["executed_actions"] = [{
        "tool": step["parsed_action"]["tool"],
        "arguments": step["parsed_action"]["arguments"],
        "result": {"success": False},
    }]
    manifest, _bundle = _collect_fixture(tmp_path, remediation)

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_blocked_action_was_executed" in manifest["problems"]


def test_policy_step_timestamps_must_be_ordered_and_timezone_aware(tmp_path):
    step = _complete_executed_negative_step()
    remediation = _complete_remediation([step])
    step["started_at"] = "2026-09-28T12:00:02"
    step["completed_at"] = "2026-09-28T12:00:01+00:00"
    manifest, _bundle = _collect_fixture(tmp_path, remediation)

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_started_at_invalid" in manifest["problems"]


def test_policy_steps_must_chain_state_and_not_overlap(tmp_path):
    steps = _complete_two_step_chain()
    steps[1]["state"] = {**steps[1]["state"], "unexpected_state": True}
    steps[1]["started_at"] = "2026-09-28T12:00:00.500000+00:00"
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation(steps),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_1_state_chain_mismatch" in manifest["problems"]
    assert "policy_step_1_starts_before_previous_completed" in manifest["problems"]


def test_policy_cannot_act_from_already_resolved_input_state(tmp_path):
    step = _complete_executed_negative_step()
    step["state"]["env_resolved"] = True
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_state_already_resolved" in manifest["problems"]


def test_multi_step_unresolved_policy_trajectory_is_reviewable(tmp_path):
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation(_complete_two_step_chain()),
    )

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["policy_step_count"] == 2


@pytest.mark.parametrize(
    "terminal_reason", ["resolved", "blocked", "unscorable", "error"]
)
def test_policy_steps_cannot_continue_after_terminal_result(
    tmp_path, terminal_reason
):
    if terminal_reason == "resolved":
        first = _complete_resolved_step()
    elif terminal_reason == "blocked":
        first = _complete_blocked_step()
    elif terminal_reason == "unscorable":
        first = _complete_unscorable_step()
    else:
        first = _complete_error_step()
    second = _complete_followup_step(
        first,
        blocked=terminal_reason == "resolved",
    )
    if terminal_reason == "resolved":
        second["terminal_block"] = {
            "category": "already_resolved",
            "reason": "Environment was already verified resolved",
        }

    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([first, second]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_1_after_terminal_step" in manifest["problems"]


def test_final_remediation_status_must_match_last_policy_step(tmp_path):
    step = _complete_executed_negative_step()
    remediation = _complete_remediation([step])
    remediation["final"]["status"] = "resolved"
    remediation["final"]["outcome"] = "resolved"
    manifest, _bundle = _collect_fixture(tmp_path, remediation)

    assert manifest["status"] == "INCOMPLETE"
    assert "remediation_final_status_mismatch" in manifest["problems"]


@pytest.mark.parametrize(
    "mutation",
    [
        "failed_but_resolved",
        "passed_but_unresolved",
        "failed_without_failed_required_check",
        "passed_with_failed_required_check",
        "empty_checks",
        "failed_checks_disagree",
    ],
)
def test_verifier_status_resolution_and_checks_must_be_consistent(
    tmp_path, mutation
):
    step = _complete_executed_negative_step()
    verification = step["verification"]
    if mutation == "failed_but_resolved":
        verification["env_resolved"] = True
        step["next_state"]["verification"] = verification
    elif mutation == "passed_but_unresolved":
        verification["verification_status"] = "passed"
    elif mutation == "failed_without_failed_required_check":
        verification["checks"][0]["passed"] = True
        verification["failed_checks"] = []
    elif mutation == "passed_with_failed_required_check":
        verification["verification_status"] = "passed"
        verification["env_resolved"] = True
        verification["failed_checks"] = ["chaos_stopped"]
        step["env_resolved"] = True
        step["next_state"]["env_resolved"] = True
        step["next_state"]["verification"] = verification
    elif mutation == "empty_checks":
        verification["checks"] = []
    elif mutation == "failed_checks_disagree":
        verification["failed_checks"] = []

    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert any(problem.startswith("policy_step_0_verification") for problem in manifest["problems"])


def test_nested_settlement_verifier_must_match_step_verifier(tmp_path):
    step = _complete_executed_negative_step()
    nested = json.loads(json.dumps(step["verification"]))
    nested["verification_status"] = "passed"
    nested["env_resolved"] = True
    nested["failed_checks"] = []
    nested["checks"][0]["passed"] = True
    step["settling"]["verification"] = nested
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_settlement_verification_mismatch" in manifest["problems"]


def test_settlement_observation_must_have_time_and_verifier_status(tmp_path):
    step = _complete_executed_negative_step()
    step["settling"]["observations"] = [{}]
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert any(
        problem.startswith("policy_step_0_settling_observation_0_")
        for problem in manifest["problems"]
    )


@pytest.mark.parametrize("category", ["unrecognized_block", ["approval_required"]])
def test_unsupported_block_category_is_incomplete_without_raising(tmp_path, category):
    step = _complete_blocked_step()
    step["terminal_block"]["category"] = category
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_terminal_block_category_invalid" in manifest["problems"]


def test_missing_cleanup_sidecar_marks_capture_incomplete_and_preserves_bundle(tmp_path):
    step = _complete_executed_negative_step()
    manifest, bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
        cleanup_sidecar=None,
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "cleanup_evidence_missing" in manifest["problems"]
    assert (bundle / f"{EXPERIMENT_ID}.json").is_file()
    assert (bundle / f"trajectory-{INCIDENT_ID}.json").is_file()
    assert (bundle / "g12_capture_manifest.json").is_file()


@pytest.mark.parametrize(
    ("sidecar", "problem"),
    [
        ("not-json", "cleanup_evidence_unreadable"),
        (
            {
                "schema_version": 1,
                "experiment_id": "EXP-STAGE4-SF002-other",
                "verified_zero_chaos": True,
            },
            "cleanup_evidence_malformed",
        ),
    ],
)
def test_invalid_cleanup_sidecar_marks_incomplete_and_preserves_raw_bytes(
    tmp_path, sidecar, problem
):
    step = _complete_executed_negative_step()
    manifest, bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
        cleanup_sidecar=sidecar,
    )

    assert manifest["status"] == "INCOMPLETE"
    assert any(item.startswith(problem) for item in manifest["problems"])
    cleanup_name = f"{EXPERIMENT_ID}.cleanup.json"
    cleanup_asset = manifest["assets"][cleanup_name]
    raw = (bundle / cleanup_asset["path"]).read_bytes()
    assert cleanup_asset["sha256"] == hashlib.sha256(raw).hexdigest()


def test_complete_negative_cleanup_failure_remains_reviewable(tmp_path):
    step = _complete_executed_negative_step()
    cleanup = _complete_cleanup_record(verified_zero_chaos=False)
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([step]),
        cleanup_sidecar=cleanup,
    )

    attempt = _complete_cleanup_record(verified_zero_chaos=False)["attempts"][0]
    assert len(json.loads(attempt["postflight_result"]["stdout"])["items"]) == 1
    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"


def test_cleanup_raw_postflight_items_must_match_recorded_count(tmp_path):
    cleanup = _complete_cleanup_record()
    cleanup["attempts"][0]["postflight_result"]["stdout"] = json.dumps({
        "items": [{"metadata": {"name": "unrecorded-leftover"}}]
    })
    manifest, _bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([_complete_executed_negative_step()]),
        cleanup_sidecar=cleanup,
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "cleanup_evidence_malformed" in manifest["problems"]


@pytest.mark.parametrize("field", ["alert", "incident_anchors"])
def test_policy_states_must_match_authoritative_incident_source(tmp_path, field):
    step = _complete_executed_negative_step()
    if field == "alert":
        untrusted = {"commonLabels": {"service": "unrelated-service"}}
        problem = "policy_step_0_state_alert_source_mismatch"
    else:
        untrusted = {**INCIDENT_ANCHORS, "primary_service": "unrelated-service"}
        problem = "policy_step_0_state_anchors_source_mismatch"
    step["state"][field] = untrusted
    step["next_state"][field] = json.loads(json.dumps(untrusted))
    manifest, _bundle = _collect_fixture(
        tmp_path, _complete_remediation([step])
    )

    assert manifest["status"] == "INCOMPLETE"
    assert problem in manifest["problems"]


@pytest.mark.parametrize("mutation", ["omit", "reorder", "change_result"])
def test_final_executed_actions_must_match_ordered_policy_step_results(
    tmp_path, mutation
):
    remediation = _complete_remediation(_complete_two_step_chain())
    final_actions = json.loads(json.dumps(remediation["final"]["executed_actions"]))
    if mutation == "omit":
        final_actions.pop()
    elif mutation == "reorder":
        final_actions.reverse()
    else:
        final_actions[0]["result"]["success"] = True
    remediation["final"]["executed_actions"] = final_actions

    manifest, _bundle = _collect_fixture(tmp_path, remediation)

    assert manifest["status"] == "INCOMPLETE"
    assert "remediation_final_executed_actions_mismatch" in manifest["problems"]


def test_failure_bundle_preserves_and_hashes_prefault_evidence(tmp_path):
    prefault_raw = json.dumps({
        "experiment_id": EXPERIMENT_ID,
        "preflight_status": "failed",
    }).encode("utf-8")
    manifest, bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([_complete_executed_negative_step()]),
        prefault_sidecar=prefault_raw,
    )

    name = f"{EXPERIMENT_ID}.prefault.json"
    asset = manifest["assets"][name]
    copied = (bundle / asset["path"]).read_bytes()
    assert copied == prefault_raw
    assert asset["sha256"] == hashlib.sha256(prefault_raw).hexdigest()
    assert asset["size_bytes"] == len(prefault_raw)


def test_primary_coordinator_anchors_must_match_trajectory_and_source(tmp_path):
    primary_anchors = {**INCIDENT_ANCHORS, "primary_service": "wrong-service"}
    manifest, bundle = _collect_fixture(
        tmp_path,
        _complete_remediation([_complete_executed_negative_step()]),
        primary_incident_anchors=primary_anchors,
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "primary_incident_anchors_mismatch" in manifest["problems"]
    primary_name = f"{EXPERIMENT_ID}.json"
    primary_asset = manifest["assets"][primary_name]
    primary_raw = (bundle / primary_asset["path"]).read_bytes()
    assert json.loads(primary_raw)["phases"]["coordinator_execution"][
        "incident_anchors"
    ] == primary_anchors
    assert primary_asset["sha256"] == hashlib.sha256(primary_raw).hexdigest()


def test_unscorable_step_may_preserve_passed_last_verifier_without_resolution(
    tmp_path,
):
    step = _complete_unscorable_step()
    verification = {
        "verification_status": "passed",
        "env_resolved": True,
        "failed_checks": [],
        "checks": [{
            "name": "chaos_stopped",
            "target": "StressChaos",
            "passed": True,
            "required": True,
        }],
    }
    step["verification"] = verification
    step["settling"].update({
        "status": "timeout",
        "verification_status": "passed",
        "last_verification": verification,
        "failure": "post_action_settle_timeout",
    })
    step["next_state"]["verification"] = verification
    remediation = _complete_remediation([step])
    manifest, _bundle = _collect_fixture(tmp_path, remediation)

    assert remediation["final"]["status"] == "unresolved"
    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["recorded_env_resolved"] is False
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"


def test_timed_out_observation_may_omit_resolution_and_failed_checks(tmp_path):
    step = _complete_unscorable_step()
    verification = {
        "verification_status": "passed",
        "env_resolved": True,
        "failed_checks": [],
        "checks": [{
            "name": "chaos_stopped",
            "target": "StressChaos",
            "passed": True,
            "required": True,
        }],
    }
    step["verification"] = verification
    step["settling"].update({
        "status": "timeout",
        "verification_status": "passed",
        "last_verification": verification,
        "failure": "post_action_settle_timeout",
        "observations": [{
            "elapsed_s": 1.0,
            "verification_status": "passed",
            "timed_out": True,
        }],
    })
    step["next_state"]["verification"] = verification
    manifest, _bundle = _collect_fixture(
        tmp_path, _complete_remediation([step])
    )

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["recorded_env_resolved"] is False


def test_normal_observation_requires_failed_checks(tmp_path):
    step = _complete_executed_negative_step()
    del step["settling"]["observations"][0]["failed_checks"]
    manifest, _bundle = _collect_fixture(
        tmp_path, _complete_remediation([step])
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_settling_observation_0_failed_checks_malformed" in manifest["problems"]


@pytest.mark.parametrize("anchor", ["incident_id", "alert", "incident_anchors"])
def test_policy_state_and_next_state_require_incident_anchors(tmp_path, anchor):
    step = _complete_executed_negative_step()
    step["state"].pop(anchor)
    step["next_state"].pop(anchor)
    manifest, _bundle = _collect_fixture(
        tmp_path, _complete_remediation([step])
    )

    assert manifest["status"] == "INCOMPLETE"
    assert "policy_step_0_state_anchors_missing" in manifest["problems"]
    assert "policy_step_0_next_state_anchors_missing" in manifest["problems"]


def test_missing_primary_is_preserved_as_incomplete(tmp_path):
    root = tmp_path / "repo"
    (root / "artifacts/evidence/stage4").mkdir(parents=True)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = capture.collect_bundle(
        root=root,
        bundle=bundle,
        experiment_id="EXP-STAGE4-SF002-999",
        source_sha="a" * 40,
        checkpoint={},
        seed=17,
        process_exit_code=1,
    )
    assert manifest["status"] == "INCOMPLETE"
    assert "primary_evidence_missing" in manifest["problems"]
    assert manifest["assets"] == {}


def test_live_wrapper_uses_governed_harness_and_records_failure(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "artifacts/evidence/stage4").mkdir(parents=True)
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(capture, "_source_sha", lambda _root: "a" * 40)
    monkeypatch.setattr(capture, "_checkpoint_record", lambda _checkpoint: {"verified": True})
    checkpoint = tmp_path / "checkpoint"
    bundle = tmp_path / "bundle"

    def fake_run(argv, *, cwd, env, check):
        assert argv == [sys.executable, "-m", "scripts.run_stage4_golden_incident"]
        assert cwd == root
        assert check is False
        assert env["ATLASOPS_REMEDIATION_BACKEND"] == "rl_policy"
        assert env["ATLASOPS_RL_POLICY_CHECKPOINT"] == str(checkpoint.resolve())
        assert env["ATLASOPS_RL_POLICY_EXECUTE_ACTIONS"] == "1"
        assert env["KUBECONFIG_CONTEXT"] == capture.METRICS_SERVER_CONTEXT
        assert env["STAGE4_EXPERIMENT_ID"] == "EXP-STAGE4-SF002-999"
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    manifest = capture.run_live_episode(
        checkpoint=checkpoint,
        experiment_id="EXP-STAGE4-SF002-999",
        bundle=bundle,
        seed=17,
        **LIVE_EXECUTION,
    )
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["process_exit_code"] == 1
    assert (bundle / "g12_capture_manifest.json").is_file()


def test_live_wrapper_preserves_launch_failure(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "artifacts/evidence/stage4").mkdir(parents=True)
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(capture, "_source_sha", lambda _root: "a" * 40)
    monkeypatch.setattr(capture, "_checkpoint_record", lambda _checkpoint: {"verified": True})
    monkeypatch.setattr(
        capture.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable"))
    )
    bundle = tmp_path / "bundle"
    manifest = capture.run_live_episode(
        checkpoint=tmp_path / "checkpoint",
        experiment_id="EXP-STAGE4-SF002-999",
        bundle=bundle,
        seed=17,
        **LIVE_EXECUTION,
    )
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["process_exit_code"] == 127
    assert manifest["launch_error"] == "harness_launch_failed:OSError"
    assert (bundle / "g12_capture_manifest.json").is_file()


def test_live_wrapper_flags_checkpoint_change_after_run(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "artifacts/evidence/stage4").mkdir(parents=True)
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(capture, "_source_sha", lambda _root: "a" * 40)
    inventories = iter(({"tree_sha256": "a" * 64}, {"tree_sha256": "b" * 64}))
    monkeypatch.setattr(capture, "_checkpoint_record", lambda _checkpoint: next(inventories))
    monkeypatch.setattr(
        capture.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1)
    )
    manifest = capture.run_live_episode(
        checkpoint=tmp_path / "checkpoint",
        experiment_id="EXP-STAGE4-SF002-999",
        bundle=tmp_path / "bundle",
        seed=17,
        **LIVE_EXECUTION,
    )
    assert manifest["checkpoint_postflight_verified"] is False
    assert "checkpoint_changed_during_run" in manifest["problems"]
    assert manifest["status"] == "INCOMPLETE"


def test_live_wrapper_rejects_unsafe_paths_before_execution(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(
        capture.subprocess, "run", lambda *args, **kwargs: pytest.fail("external command executed")
    )
    with pytest.raises(ValueError, match="Experiment ID"):
        capture.run_live_episode(
            checkpoint=tmp_path / "checkpoint",
            experiment_id="../../elsewhere",
            bundle=tmp_path / "bundle",
            seed=17,
            **LIVE_EXECUTION,
        )
    with pytest.raises(ValueError, match="outside"):
        capture.run_live_episode(
            checkpoint=tmp_path / "checkpoint",
            experiment_id="EXP-STAGE4-SF002-999",
            bundle=root / "bundle",
            seed=17,
            **LIVE_EXECUTION,
        )


def test_live_wrapper_rejects_existing_attempt_sidecar(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    evidence_dir = root / "artifacts/evidence/stage4"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "EXP-STAGE4-SF002-999.interruption.json").write_text(
        "{}", encoding="utf-8"
    )
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(capture, "_source_sha", lambda _root: "a" * 40)
    monkeypatch.setattr(capture, "_checkpoint_record", lambda _checkpoint: {"verified": True})
    monkeypatch.setattr(
        capture.subprocess, "run", lambda *args, **kwargs: pytest.fail("external command executed")
    )
    bundle = tmp_path / "bundle"
    with pytest.raises(FileExistsError, match="artifact already exists"):
        capture.run_live_episode(
            checkpoint=tmp_path / "checkpoint",
            experiment_id="EXP-STAGE4-SF002-999",
            bundle=bundle,
            seed=17,
            **LIVE_EXECUTION,
        )
    assert not bundle.exists()


def test_checkpoint_policy_requires_explicit_context_before_loading(monkeypatch, tmp_path):
    from agents import coordinator
    from bench import grpo_eval

    checkpoint = str(tmp_path / "checkpoint")
    calls = []

    def load(path, **kwargs):
        calls.append((path, kwargs))
        return object()

    monkeypatch.setattr(grpo_eval.LocalGRPOPolicy, "from_checkpoint", load)
    monkeypatch.delenv("ATLASOPS_RL_POLICY_EXECUTE_ACTIONS", raising=False)
    monkeypatch.delenv("KUBECONFIG_CONTEXT", raising=False)
    with pytest.raises(RuntimeError, match="live policy opt-in"):
        coordinator._load_rl_policy_checkpoint(checkpoint)
    monkeypatch.setenv("ATLASOPS_RL_POLICY_EXECUTE_ACTIONS", "1")
    with pytest.raises(RuntimeError, match="KUBECONFIG_CONTEXT"):
        coordinator._load_rl_policy_checkpoint(checkpoint)
    assert calls == []

    monkeypatch.setenv("KUBECONFIG_CONTEXT", capture.METRICS_SERVER_CONTEXT)
    coordinator._load_rl_policy_checkpoint(checkpoint)
    assert calls == [
        (
            tmp_path / "checkpoint",
            {"execute_actions": True, "kube_context": capture.METRICS_SERVER_CONTEXT},
        )
    ]
