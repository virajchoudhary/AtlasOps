"""Adversarial contracts for Stage 9 prompt/scenario/action coupling."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import coordinator
from config.runtime import CurriculumManager
from training.grpo import (
    InfrastructureInvalid,
    PolicyExecutionInvalid,
    OnlineRewardFunction,
    apply_fault,
    cleanup_scenario_faults,
    compute_reward,
    compute_reward_breakdown,
    cluster_healthy,
    detect_active_chaos_resources,
    fault_established,
    observe_cluster_fingerprint,
    resolve_environment_identity,
    validate_resume_identity,
    validate_sft_checkpoint,
    validate_reward_pairing,
    verify_kubernetes_environment,
)
from training.stage9_contract import (
    ScenarioFaultPlan,
    build_remediation_policy_prompt,
    build_remediation_training_row,
    build_scenario_fault_plan,
    build_trl_training_record,
    load_remediation_training_rows,
    validate_remediation_training_row,
)


ENVIRONMENT = {"provider": "local-kind", "kubernetes_context": "kind-atlasops-local"}


def _observation(scenario_id: str = "single_fault/sf-900") -> dict:
    incident_suffix = hashlib.sha256(scenario_id.encode("utf-8")).hexdigest()[:8]
    return {
        "alert": {"commonLabels": {"alertname": "CPUSaturation"}, "alerts": []},
        "approval_mode": "auto",
        "diagnosis": {"root_cause": {"category": "resource"}},
        "incident_id": f"inc-synthetic-{incident_suffix}",
        "triage": {"severity": "P2"},
    }


def _row(scenario_id: str = "single_fault/sf-900", replay_id: str = "replay-1") -> dict:
    return build_remediation_training_row(
        observation=_observation(scenario_id),
        scenario_id=scenario_id,
        tier="single_fault",
        replay_id=replay_id,
        environment_identity=ENVIRONMENT,
        split_source="synthetic-training-only",
        split_hash="a" * 64,
        split_eligibility="training",
    )


def _policy_completion() -> str:
    return json.dumps({
        "name": "chaos_stop_experiment",
        "arguments": {
            "kind": "StressChaos",
            "name": "sf-002-paymentservice-cpu",
            "namespace": "chaos-mesh",
        },
    })


def test_remediation_row_is_rebuildable_and_hides_scenario_identity() -> None:
    row = _row()
    normalized = validate_remediation_training_row(row)

    assert normalized["role"] == "remediation"
    assert normalized["hidden_metadata"]["scenario_id"] == "single_fault/sf-900"
    assert "single_fault/sf-900" not in normalized["model_visible_prompt"]
    assert "sf-900" not in normalized["model_visible_prompt"]
    assert '"scenario_id"' not in normalized["model_visible_prompt"]


@pytest.mark.parametrize("role", ["triage", "diagnosis", "comms", "unknown"])
def test_mixed_or_unknown_role_dataset_fails_closed(role: str, tmp_path: Path) -> None:
    row = _row()
    row["role"] = role
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="mixed_or_unknown_role"):
        load_remediation_training_rows(path)


def test_hidden_identity_injection_into_prompt_is_rejected() -> None:
    observation = _observation()
    observation["triage"]["scenario_id"] = "single_fault/sf-900"

    with pytest.raises(ValueError, match="hidden_scenario_leak"):
        build_remediation_training_row(
            observation=observation,
            scenario_id="single_fault/sf-900",
            tier="single_fault",
            replay_id="replay-1",
            environment_identity=ENVIRONMENT,
            split_source="synthetic",
            split_hash="a" * 64,
            split_eligibility="training",
        )


def test_other_agent_dense_rewards_cannot_boost_failed_policy() -> None:
    episode = {
        "agent_claimed_resolved": False,
        "env_resolved": False,
        "outcome": "unresolved",
        "remediation": {
            "final": {
                "mode": "policy_rollout",
                "policy_completion_valid": False,
                "executed_actions": [],
            }
        },
        "triage": {
            "step_reward_summary": {
                "dense_reward_total": 1000,
                "success_count": 100,
            }
        },
        "diagnosis": {
            "step_reward_summary": {
                "dense_reward_total": 1000,
                "success_count": 100,
            }
        },
        "comms": {
            "step_reward_summary": {
                "dense_reward_total": 1000,
                "postmortem_path": "/tmp/postmortem.md",
            }
        },
    }
    breakdown = compute_reward_breakdown(episode)

    assert breakdown["components"]["dense_policy_action"] == 0.0
    assert breakdown["total"] == pytest.approx(-0.25)


def test_invalid_policy_completion_receives_no_progress_credit() -> None:
    episode = {
        "env_resolved": False,
        "outcome": "unresolved",
        "triage": {"step_reward_summary": {"dense_reward_total": 99}},
        "diagnosis": {"step_reward_summary": {"dense_reward_total": 99}},
        "remediation": {
            "final": {
                "mode": "policy_rollout",
                "policy_completion_valid": False,
                "executed_actions": [],
            }
        },
    }

    breakdown = compute_reward_breakdown(episode)
    assert breakdown["components"]["resolution"] == 0.0
    assert breakdown["components"]["partial_progress"] == 0.0
    assert breakdown["components"]["dense_policy_action"] == 0.0


def test_policy_owned_nonmutating_success_gets_no_progress_credit() -> None:
    action = {
        "args": {"query": "up"},
        "output": {"success": True},
        "success": True,
        "tool": "kubectl_get",
    }
    episode = {
        "env_resolved": False,
        "outcome": "unresolved",
        "remediation": {
            "final": {
                "executed_actions": [action],
                "mode": "policy_rollout",
                "policy_action_admitted": True,
                "policy_action_identity_match": True,
                "policy_completion_valid": True,
            }
        },
    }
    breakdown = compute_reward_breakdown(episode)

    assert breakdown["components"]["dense_policy_action"] == pytest.approx(0.0)
    assert breakdown["total"] == pytest.approx(0.0)


def test_env_truth_without_policy_owned_mutation_cannot_resolve() -> None:
    episode = {
        "agent_claimed_resolved": False,
        "env_resolved": True,
        "outcome": "resolved",
        "triage": {"step_reward_summary": {"dense_reward_total": 50}},
        "remediation": {
            "final": {
                "executed_actions": [],
                "mode": "manual",
            }
        },
    }

    assert compute_reward(episode) == pytest.approx(0.0)


def test_false_resolution_penalizes_unverified_claim() -> None:
    action = {
        "args": {},
        "success": True,
        "tool": "chaos_stop_experiment",
    }
    episode = {
        "agent_claimed_resolved": True,
        "env_resolved": False,
        "outcome": "resolved",
        "remediation": {
            "final": {
                "executed_actions": [action],
                "mode": "policy_rollout",
                "policy_action_admitted": True,
                "policy_action_identity_match": True,
                "policy_completion_valid": True,
            }
        },
    }
    breakdown = compute_reward_breakdown(episode)

    assert breakdown["penalties"]["false_resolution"] == pytest.approx(0.25)
    assert breakdown["total"] == pytest.approx(-0.25)


def test_reward_pairing_uses_hidden_row_metadata() -> None:
    first = _row("single_fault/sf-901", "replay-a")
    second = _row("multi_fault/mf-902", "replay-b")
    prompts = [first["model_visible_prompt"], second["model_visible_prompt"]]
    metadata = {
        key: [first[key], second[key]]
        for key in ("prompt_sha256", "provenance_hash", "role", "row_id", "stage9_group_id")
    }
    rows_by_group = {
        first["stage9_group_id"]: first,
        second["stage9_group_id"]: second,
    }

    paired = validate_reward_pairing(
        prompts,
        ["{}", "{}"],
        metadata,
        rows_by_group,
        ENVIRONMENT,
    )
    assert [row["stage9_group_id"] for row in paired] == [
        first["stage9_group_id"],
        second["stage9_group_id"],
    ]


def test_policy_parser_accepts_only_canonical_single_action_json() -> None:
    parsed = coordinator._parse_policy_remediation_completion(
        '{"arguments":{"query":"up"},"name":"promql_query"}'
    )
    assert parsed["policy_parse_error"] is None


@pytest.mark.parametrize("completion", [
    '<tool_call>{"name":"promql_query","arguments":{}}</tool_call>',
    '{"name":"promql_query","arguments":"{\\"query\\":\\"up\\"}"}',
    '{"name":"promql_query","arguments":{},"rationale":"because"}',
    "promql_query",
])
def test_provider_artifacts_and_noncanonical_shapes_are_invalid(completion: str) -> None:
    parsed = coordinator._parse_policy_remediation_completion(completion)

    assert parsed["tool_calls"] == []
    assert parsed["policy_parse_error"]


def test_same_tool_with_different_arguments_has_different_identity() -> None:
    first = coordinator._canonical_action_identity("promql_query", {"query": "up"})
    second = coordinator._canonical_action_identity("promql_query", {"query": "down"})

    assert first["sha256"] != second["sha256"]


def test_disallowed_known_tool_is_admitted_never_and_not_executed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    executed = []

    def reject_executor(**_args):
        executed.append(_args)
        return {"success": True}

    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "kubectl_top_pods", reject_executor)
    result = asyncio.run(coordinator.call_agent(
        "remediation",
        {"incident_id": "inc-acl"},
        policy_completion='{"name":"kubectl_top_pods","arguments":{}}',
    ))

    assert executed == []
    assert result["final"]["policy_denial_reason"].startswith("policy_blocked:")
    breakdown = compute_reward_breakdown({"remediation": {"final": result["final"]}})
    assert breakdown["penalties"]["unadmitted_policy_action"] == pytest.approx(0.20)


@pytest.mark.parametrize(("completion", "reason"), [
    ('{"name":"kubectl_get","arguments":{}}', "missing_required_arguments"),
    ('{"name":"kubectl_get","arguments":{"resource":"pods","extra":1}}', "unexpected_arguments"),
    ('{"name":"kubectl_scale","arguments":{"deployment":"app","replicas":"three"}}', "invalid_argument_type"),
])
def test_schema_drift_cannot_reach_executor(
    completion: str,
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))

    def reject_executor(**_args):
        raise AssertionError("schema-invalid policy action must not execute")

    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "kubectl_get", reject_executor)
    result = asyncio.run(coordinator.call_agent(
        "remediation",
        {"incident_id": "inc-schema"},
        policy_completion=completion,
    ))

    assert result["final"]["policy_completion_error"].startswith(reason)
    assert result["trajectory"][0]["validation_state"] == "invalid_policy_completion"


def test_swapped_scenario_metadata_fails_closed() -> None:
    first = _row("single_fault/sf-901", "replay-a")
    second = _row("multi_fault/mf-902", "replay-b")
    prompts = [first["model_visible_prompt"], second["model_visible_prompt"]]
    metadata = {
        key: [first[key], first[key]]
        for key in ("prompt_sha256", "provenance_hash", "role", "row_id", "stage9_group_id")
    }

    with pytest.raises(RuntimeError, match="unknown_stage9_group|prompt_metadata_mismatch"):
        validate_reward_pairing(
            prompts,
            ["{}", "{}"],
            metadata,
            {first["stage9_group_id"]: first, second["stage9_group_id"]: second},
            ENVIRONMENT,
        )


def test_row_environment_mismatch_fails_closed() -> None:
    row = _row()
    metadata = {
        key: [row[key]]
        for key in ("prompt_sha256", "provenance_hash", "role", "row_id", "stage9_group_id")
    }

    with pytest.raises(RuntimeError, match="environment_identity_mismatch"):
        validate_reward_pairing(
            [row["model_visible_prompt"]],
            ["{}"],
            metadata,
            {row["stage9_group_id"]: row},
            {"provider": "local-kind", "kubernetes_context": "other-context"},
        )


def test_environment_requires_canonical_explicit_local_context() -> None:
    assert resolve_environment_identity(
        "local-kind", "kind-atlasops-local"
    ).kubernetes_context == "kind-atlasops-local"
    with pytest.raises(ValueError, match="stage9_environment_unapproved"):
        resolve_environment_identity("gke", "gke_project")
    with pytest.raises(ValueError, match="stage9_context_unapproved"):
        resolve_environment_identity("local-kind", "ambient-context")


def test_kubectl_always_carries_declared_context_and_never_gcloud_auth() -> None:
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="kind-atlasops-local\n", stderr="")

    original = subprocess.run
    subprocess.run = fake_run
    try:
        result = verify_kubernetes_environment(identity)
    finally:
        subprocess.run = original

    assert result["status"] == "CONTEXT_VERIFIED"
    assert commands[0][0][:3] == ["kubectl", "--context", "kind-atlasops-local"]
    assert all("USE_GKE_GCLOUD_AUTH_PLUGIN" not in kwargs.get("env", {}) for _, kwargs in commands)


def test_unlisted_kubectl_context_fails_closed() -> None:
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")

    def fake_run(_identity, _args, **_kwargs):
        return {"success": True, "stdout": "other-context\n"}

    original = verify_kubernetes_environment.__globals__["_run_kubectl"]
    verify_kubernetes_environment.__globals__["_run_kubectl"] = fake_run
    try:
        with pytest.raises(InfrastructureInvalid, match="kubernetes_context_mismatch"):
            verify_kubernetes_environment(identity)
    finally:
        verify_kubernetes_environment.__globals__["_run_kubectl"] = original


def test_cluster_health_requires_all_minimum_deployments_ready() -> None:
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    payload = {
        "items": [
            {
                "spec": {"replicas": 1},
                "status": {"readyReplicas": 1},
            }
            for _ in range(12)
        ]
    }

    def fake_run(_identity, args, **_kwargs):
        assert args[:2] == ["get", "deployments"]
        return {"success": True, "stdout": json.dumps(payload)}

    original = cluster_healthy.__globals__["_run_kubectl"]
    cluster_healthy.__globals__["_run_kubectl"] = lambda *_args, **_kwargs: fake_run(*_args, **_kwargs)
    try:
        passed, details = cluster_healthy(identity)
    finally:
        cluster_healthy.__globals__["_run_kubectl"] = original

    assert passed is True
    assert details["ready_deployments"] == 12


def test_fault_requires_exact_labelled_resource() -> None:
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")

    def fail():
        raise AssertionError("invalid fault setup must not proceed")

    original = fault_established.__globals__["_run_kubectl"]
    fault_established.__globals__["_run_kubectl"] = lambda *_, **__: {
        "success": True,
        "stdout": json.dumps({"items": []}),
    }
    try:
        with pytest.raises(InfrastructureInvalid, match="fault_not_objectively_established"):
            fault_established(identity, "single_fault/sf-002")
    finally:
        fault_established.__globals__["_run_kubectl"] = original


def test_paired_rollout_keeps_scenario_out_of_model_visible_alert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    seen = {}

    async def fake_handle_incident(
        alert,
        *,
        scenario_id,
        remediation_policy_completion,
        remediation_observation,
    ):
        seen.update({
            "alert": alert,
            "completion": remediation_policy_completion,
            "scenario_id": scenario_id,
            "observation": remediation_observation,
        })
        return {
            "agent_claimed_resolved": False,
            "comms": {},
            "diagnosis": {},
            "env_resolved": False,
            "remediation": {"final": {}},
            "triage": {},
            "verification": {"env_resolved": False},
        }

    monkeypatch.setattr("agents.coordinator.handle_incident", fake_handle_incident)
    monkeypatch.setattr(
        "training.grpo.verify_kubernetes_environment",
        lambda _identity: {"status": "CONTEXT_VERIFIED"},
    )
    monkeypatch.setattr(
        "training.grpo.cluster_healthy",
        lambda _identity, **_kwargs: (True, {"ready_deployments": 12}),
    )
    monkeypatch.setattr(
        "training.grpo.detect_active_chaos_resources",
        lambda _identity: [],
    )
    monkeypatch.setattr(
        "training.grpo.build_scenario_fault_plan",
        lambda scenario_id, **_kwargs: ScenarioFaultPlan(
            scenario_id=scenario_id,
            tier="single_fault",
            manifest_path="bench/chaos_manifests/single_fault/sf-002.yaml",
            manifest_sha256="a" * 64,
            resources=(),
        ),
    )
    monkeypatch.setattr(
        "training.grpo.apply_fault",
        lambda _identity, scenario_id, **_kwargs: {"phase": "FAULT_APPLIED", "status": "FAULT_APPLIED"},
    )
    monkeypatch.setattr(
        "training.grpo.fault_established",
        lambda _identity, scenario_id, **_kwargs: {"phase": "FAULT_ESTABLISHED", "status": "FAULT_ESTABLISHED"},
    )
    monkeypatch.setattr(
        "training.grpo.cleanup_scenario_faults",
        lambda _identity, _plan: {"phase": "RESET", "status": "RESET"},
    )
    monkeypatch.setattr(
        "training.grpo.reset_faults",
        lambda _identity: {"phase": "RESET", "status": "FAULTS_RESET"},
    )
    reward_fn = OnlineRewardFunction(
        [_row()],
        identity,
        curriculum_seed=7,
        dataset_sha256="b" * 64,
        fault_settle_seconds=0,
        reset_settle_seconds=0,
    )

    result = asyncio.run(reward_fn._score_paired_rollout(_row(), '{"name":"promql_query","arguments":{"query":"up"}}'))

    assert seen["scenario_id"] == "single_fault/sf-900"
    assert "scenario_id" not in seen["alert"]
    assert seen["observation"] == _row()["observation"]
    assert result["lifecycle"][0]["phase"] == "CONTEXT_VERIFIED"
    assert result["lifecycle"][1]["phase"] == "PRE_ROLLOUT_HEALTHY"
    assert result["lifecycle"][-1]["phase"] == "POST_RESET_HEALTHY"


def test_stage9_rollout_replays_exact_row_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    observed = []

    def fake_tool(**args):
        observed.append(args)
        return {"success": True}

    verification = SimpleNamespace(
        env_resolved=True,
        verification_status="passed",
        failed_checks=[],
        to_dict=lambda: {"env_resolved": True},
    )

    async def fake_settle(**kwargs):
        return {"scenario_id": kwargs["scenario_id"]}

    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "chaos_stop_experiment", fake_tool)
    monkeypatch.setattr(coordinator, "settle_environment", fake_settle)
    monkeypatch.setattr("agents.verifier.verify_environment", lambda **kwargs: verification)
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path)

    observation = _observation()
    incident = asyncio.run(coordinator.handle_incident(
        observation["alert"],
        incident_id="inc-stage9-replay",
        scenario_id="single_fault/sf-900",
        remediation_policy_completion=_policy_completion(),
        remediation_observation=observation,
    ))

    assert observed == [{
        "kind": "StressChaos",
        "name": "sf-002-paymentservice-cpu",
        "namespace": "chaos-mesh",
    }]
    assert incident["triage"]["source"] == "stage9_training_row"
    assert incident["diagnosis"]["source"] == "stage9_training_row"
    assert incident["triage"]["final"] is observation["triage"]
    assert incident["diagnosis"]["final"] is observation["diagnosis"]
    assert "scenario_id" not in incident["alert"]
    assert incident["comms"]["final"]["skipped"] == "stage9_reward_rollout"


def test_stage9_observation_cannot_bypass_approval_policy() -> None:
    observation = _observation()
    observation["approval_mode"] = "auto"
    observation["triage"]["severity"] = "P0"

    with pytest.raises(ValueError, match="stage9_approval_gate_mismatch"):
        coordinator._stage9_remediation_observation(observation)


def test_interactive_approval_rows_fail_closed_before_waiting() -> None:
    observation = _observation()
    observation["triage"]["severity"] = "P1"
    observation["approval_mode"] = "approve"

    with pytest.raises(ValueError, match="stage9_interactive_approval_required"):
        coordinator._stage9_remediation_observation(observation)


def test_stage9_observation_rejects_hidden_identity_key() -> None:
    observation = _observation()
    observation["alert"]["scenario_id"] = "single_fault/sf-900"

    with pytest.raises(ValueError, match="hidden_scenario_leak"):
        coordinator._stage9_remediation_observation(observation)


def test_manual_gate_prevents_stage9_policy_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))

    def reject_tool(**_args):
        raise AssertionError("manual gate must block optimized execution")

    verification = SimpleNamespace(
        env_resolved=False,
        verification_status="failed",
        failed_checks=["manual"],
        to_dict=lambda: {"env_resolved": False},
    )

    async def fake_settle(**_kwargs):
        return {}

    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "chaos_stop_experiment", reject_tool)
    monkeypatch.setattr(coordinator, "settle_environment", fake_settle)
    monkeypatch.setattr("agents.verifier.verify_environment", lambda **_kwargs: verification)
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path)
    observation = _observation()
    observation["triage"]["severity"] = "P0"
    observation["approval_mode"] = "manual"

    incident = asyncio.run(coordinator.handle_incident(
        observation["alert"],
        incident_id="inc-manual-stage9",
        scenario_id="single_fault/sf-900",
        remediation_policy_completion=_policy_completion(),
        remediation_observation=observation,
    ))

    assert incident["remediation"]["final"]["mode"] == "manual"
    assert incident["remediation"]["trajectory"] == []


def test_pre_rollout_invalidity_never_applies_fault_or_scores_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")

    async def reject_handle(*_args, **_kwargs):
        raise AssertionError("invalid setup must not reach policy execution")

    monkeypatch.setattr("agents.coordinator.handle_incident", reject_handle)
    monkeypatch.setattr(
        "training.grpo.verify_kubernetes_environment",
        lambda _identity: {"status": "CONTEXT_VERIFIED"},
    )
    monkeypatch.setattr(
        "training.grpo.cluster_healthy",
        lambda _identity, **_kwargs: (False, {"ready_deployments": 3}),
    )
    monkeypatch.setattr(
        "training.grpo.apply_fault",
        lambda _identity, _scenario_id: pytest.fail("fault applied after failed health gate"),
    )
    row = _row("single_fault/sf-002")
    reward_fn = OnlineRewardFunction(
        [row],
        identity,
        curriculum_seed=1,
        dataset_sha256="b" * 64,
    )

    with pytest.raises(InfrastructureInvalid, match="pre_rollout_unhealthy"):
        asyncio.run(reward_fn._score_paired_rollout(row, "{}"))


def test_fault_establishment_failure_always_resets_before_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    calls = []

    async def reject_handle(*_args, **_kwargs):
        raise AssertionError("unestablished fault must not reach policy execution")

    monkeypatch.setattr("agents.coordinator.handle_incident", reject_handle)
    monkeypatch.setattr(
        "training.grpo.verify_kubernetes_environment",
        lambda _identity: {"status": "CONTEXT_VERIFIED"},
    )
    monkeypatch.setattr(
        "training.grpo.cluster_healthy",
        lambda _identity, **_kwargs: (True, {}),
    )
    monkeypatch.setattr(
        "training.grpo.detect_active_chaos_resources",
        lambda _identity: [],
    )
    monkeypatch.setattr(
        "training.grpo.build_scenario_fault_plan",
        lambda scenario_id, **_kwargs: ScenarioFaultPlan(
            scenario_id=scenario_id,
            tier="single_fault",
            manifest_path="bench/chaos_manifests/single_fault/sf-002.yaml",
            manifest_sha256="a" * 64,
            resources=(),
        ),
    )
    monkeypatch.setattr(
        "training.grpo.apply_fault",
        lambda _identity, scenario_id, **_kwargs: calls.append(("apply", scenario_id)) or {},
    )
    def fail_established(_identity, scenario_id, **_kwargs):
        calls.append(("establish_failed", scenario_id))
        raise InfrastructureInvalid("fault_not_objectively_established")
    monkeypatch.setattr("training.grpo.fault_established", fail_established)
    monkeypatch.setattr(
        "training.grpo.cleanup_scenario_faults",
        lambda _identity, _plan: calls.append(("reset", "")) or {"phase": "RESET", "status": "RESET"},
    )
    monkeypatch.setattr(
        "training.grpo.reset_faults",
        lambda _identity: calls.append(("reset", "")) or {"phase": "RESET", "status": "FAULTS_RESET"},
    )
    reward_fn = OnlineRewardFunction(
        [_row()],
        identity,
        curriculum_seed=1,
        dataset_sha256="b" * 64,
        fault_settle_seconds=0,
        reset_settle_seconds=0,
    )

    with pytest.raises(InfrastructureInvalid, match="fault_not_objectively_established"):
        asyncio.run(reward_fn._score_paired_rollout(_row(), "{}"))
    assert [call[0] for call in calls] == ["apply", "establish_failed", "reset"]


def test_reset_failure_is_infrastructure_invalid_even_when_handler_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")

    async def fake_handle_incident(*_args, **_kwargs):
        return {
            "agent_claimed_resolved": False,
            "comms": {}, "diagnosis": {}, "env_resolved": False,
            "remediation": {"final": {}}, "triage": {},
            "verification": {"env_resolved": False},
        }

    monkeypatch.setattr("agents.coordinator.handle_incident", fake_handle_incident)
    monkeypatch.setattr(
        "training.grpo.verify_kubernetes_environment",
        lambda _identity: {"status": "CONTEXT_VERIFIED"},
    )
    monkeypatch.setattr(
        "training.grpo.cluster_healthy",
        lambda _identity, **_kwargs: (True, {}),
    )
    monkeypatch.setattr(
        "training.grpo.detect_active_chaos_resources",
        lambda _identity: [],
    )
    monkeypatch.setattr(
        "training.grpo.build_scenario_fault_plan",
        lambda scenario_id, **_kwargs: ScenarioFaultPlan(
            scenario_id=scenario_id,
            tier="single_fault",
            manifest_path="bench/chaos_manifests/single_fault/sf-002.yaml",
            manifest_sha256="a" * 64,
            resources=(),
        ),
    )
    monkeypatch.setattr("training.grpo.apply_fault", lambda *_, **__: {})
    monkeypatch.setattr("training.grpo.fault_established", lambda *_, **__: {})
    def fail_cleanup(_identity, _plan=None):
        raise InfrastructureInvalid("fault_reset_failed")
    monkeypatch.setattr("training.grpo.cleanup_scenario_faults", fail_cleanup)
    monkeypatch.setattr("training.grpo.reset_faults", fail_cleanup)
    reward_fn = OnlineRewardFunction(
        [_row()],
        identity,
        curriculum_seed=1,
        dataset_sha256="b" * 64,
        fault_settle_seconds=0,
        reset_settle_seconds=0,
    )

    with pytest.raises(InfrastructureInvalid, match="post_reset_unhealthy|fault_reset_failed"):
        asyncio.run(reward_fn._score_paired_rollout(_row(), "{}"))


def test_harness_error_is_not_scored_as_policy_reward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-secret")
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")

    async def fail_handle_incident(*_args, **_kwargs):
        raise RuntimeError("coordinator unavailable")

    monkeypatch.setattr("agents.coordinator.handle_incident", fail_handle_incident)
    monkeypatch.setattr(
        "training.grpo.verify_kubernetes_environment",
        lambda _identity: {"status": "CONTEXT_VERIFIED"},
    )
    monkeypatch.setattr(
        "training.grpo.cluster_healthy",
        lambda _identity, **_kwargs: (True, {}),
    )
    monkeypatch.setattr(
        "training.grpo.detect_active_chaos_resources",
        lambda _identity: [],
    )
    monkeypatch.setattr(
        "training.grpo.build_scenario_fault_plan",
        lambda scenario_id, **_kwargs: ScenarioFaultPlan(
            scenario_id=scenario_id,
            tier="single_fault",
            manifest_path="bench/chaos_manifests/single_fault/sf-002.yaml",
            manifest_sha256="a" * 64,
            resources=(),
        ),
    )
    monkeypatch.setattr("training.grpo.apply_fault", lambda *_, **__: {})
    monkeypatch.setattr("training.grpo.fault_established", lambda *_, **__: {})
    monkeypatch.setattr("training.grpo.cleanup_scenario_faults", lambda *_, **__: {})
    monkeypatch.setattr("training.grpo.reset_faults", lambda *_, **__: {})
    reward_fn = OnlineRewardFunction(
        [_row()],
        identity,
        curriculum_seed=1,
        dataset_sha256="b" * 64,
        fault_settle_seconds=0,
        reset_settle_seconds=0,
    )

    with pytest.raises(PolicyExecutionInvalid, match="rollout_harness_error"):
        asyncio.run(reward_fn._score_paired_rollout(_row(), "{}"))


def test_apply_fault_rejects_path_traversal() -> None:
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")

    with pytest.raises(InfrastructureInvalid, match="fault_scenario_identity_invalid"):
        apply_fault(identity, "../single_fault/sf-900")


def test_resume_identity_rejects_incompatible_contract_seed_model_or_split() -> None:
    previous = {field: field for field in (
        "code_commit", "contracts", "curriculum_seed", "dataset",
        "dependency_versions", "environment_identity", "hyperparameters",
        "environment_observed", "model_path", "sft_manifest",
    )}
    validate_resume_identity(previous, dict(previous))

    for field in previous:
        current = dict(previous)
        current[field] = f"changed-{field}"
        with pytest.raises(RuntimeError, match="incompatible_resume_identity"):
            validate_resume_identity(previous, current)


def test_reward_callback_scores_each_completion_with_its_paired_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _row("single_fault/sf-911", "replay-a")
    second = _row("multi_fault/mf-912", "replay-b")
    episodes_path = tmp_path / "episodes.jsonl"
    reward_fn = OnlineRewardFunction(
        [first, second],
        resolve_environment_identity("local-kind", "kind-atlasops-local"),
        curriculum_seed=5,
        dataset_sha256="d" * 64,
        episodes_path=episodes_path,
    )
    seen = []

    async def fake_score(row, completion):
        seen.append((row["stage9_group_id"], completion))
        return {
            "episode": {
                "env_resolved": False,
                "outcome": "unresolved",
                "remediation": {
                    "final": {
                        "executed_actions": [{
                            "args": {},
                            "success": True,
                            "tool": "chaos_stop_experiment",
                        }],
                        "mode": "policy_rollout",
                        "policy_action_admitted": True,
                        "policy_action_identity_match": True,
                        "policy_completion_valid": True,
                    }
                },
            },
            "lifecycle": [{"phase": "MOCK_LIFECYCLE"}],
            "model_visible_alert": {},
        }

    monkeypatch.setattr(
        reward_fn,
        "_score_paired_rollout",
        fake_score,
    )
    rewards = reward_fn(
        completion_ids=[[1], [2]],
        prompts=[first["model_visible_prompt"], second["model_visible_prompt"]],
        completions=[
            '{"name":"promql_query","arguments":{"query":"a"}}',
            '{"name":"promql_query","arguments":{"query":"b"}}',
        ],
        prompt_sha256=[first["prompt_sha256"], second["prompt_sha256"]],
        provenance_hash=[first["provenance_hash"], second["provenance_hash"]],
        role=[first["role"], second["role"]],
        row_id=[first["row_id"], second["row_id"]],
        stage9_group_id=[first["stage9_group_id"], second["stage9_group_id"]],
    )

    assert rewards == pytest.approx([0.0, 0.0])
    assert [group for group, _ in seen] == [
        first["stage9_group_id"],
        second["stage9_group_id"],
    ]
    records = [json.loads(line) for line in episodes_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert [record["stage9_group_id"] for record in records] == [
        first["stage9_group_id"],
        second["stage9_group_id"],
    ]
    assert all(record["status"] == "COMPLETED" for record in records)


def test_infrastructure_invalidation_is_persisted_before_batch_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row()
    episodes_path = tmp_path / "episodes.jsonl"
    reward_fn = OnlineRewardFunction(
        [row],
        resolve_environment_identity("local-kind", "kind-atlasops-local"),
        curriculum_seed=2,
        dataset_sha256="e" * 64,
        episodes_path=episodes_path,
    )

    async def fail_rollout(_row, _completion):
        raise InfrastructureInvalid("pre_rollout_unhealthy")

    monkeypatch.setattr(
        reward_fn,
        "_score_paired_rollout",
        fail_rollout,
    )

    with pytest.raises(InfrastructureInvalid, match="pre_rollout_unhealthy"):
        reward_fn(
            completions=['{"name":"promql_query","arguments":{}}'],
            prompts=[row["model_visible_prompt"]],
            prompt_sha256=[row["prompt_sha256"]],
            provenance_hash=[row["provenance_hash"]],
            role=[row["role"]],
            row_id=[row["row_id"]],
            stage9_group_id=[row["stage9_group_id"]],
        )

    records = [json.loads(line) for line in episodes_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["status"] == "INFRASTRUCTURE_INVALID"
    assert records[0]["reward"] is None
    assert records[0]["invalidation_reason"] == "pre_rollout_unhealthy"


def test_existing_episode_log_validation_rejects_corrupt_resume_evidence(
    tmp_path: Path,
) -> None:
    row = _row()
    episodes_path = tmp_path / "episodes.jsonl"
    reward_fn = OnlineRewardFunction(
        [row],
        resolve_environment_identity("local-kind", "kind-atlasops-local"),
        curriculum_seed=3,
        dataset_sha256="f" * 64,
        episodes_path=episodes_path,
    )
    valid = {
        "event_id": "event-1",
        "hidden_metadata": row["hidden_metadata"],
        "policy_completion_sha256": "a" * 64,
        "prompt_sha256": row["prompt_sha256"],
        "reward": {"total": 0.0},
        "row_id": row["row_id"],
        "stage9_group_id": row["stage9_group_id"],
        "status": "COMPLETED",
    }
    episodes_path.write_text(json.dumps(valid) + "\n", encoding="utf-8")
    assert reward_fn.validate_existing_episode_log() == 1

    invalid_single_cases = [
        "{",
        json.dumps({**valid, "status": "UNKNOWN"}),
        json.dumps({**valid, "reward": None}),
    ]
    for case in invalid_single_cases:
        episodes_path.write_text(case + "\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="episode_evidence|completed_episode"):
            reward_fn.validate_existing_episode_log()

    episodes_path.write_text(
        json.dumps(valid) + "\n" + json.dumps(valid) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="episode_evidence_event_id_invalid"):
        reward_fn.validate_existing_episode_log()


def test_scientific_tool_context_pins_cluster_and_blocks_webhooks() -> None:
    from training.grpo import _approved_tool_context

    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    os.environ["KUBECONFIG_CONTEXT"] = "ambient-forbidden"
    os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.invalid/webhook"
    os.environ["SLACK_WEBHOOK_URL"] = "https://slack.invalid/webhook"
    try:
        with _approved_tool_context(identity):
            assert os.environ["KUBECONFIG_CONTEXT"] == "kind-atlasops-local"
            assert "DISCORD_WEBHOOK_URL" not in os.environ
            assert "SLACK_WEBHOOK_URL" not in os.environ
        assert os.environ["KUBECONFIG_CONTEXT"] == "ambient-forbidden"
        assert os.environ["DISCORD_WEBHOOK_URL"] == "https://discord.invalid/webhook"
        assert os.environ["SLACK_WEBHOOK_URL"] == "https://slack.invalid/webhook"
    finally:
        os.environ.pop("KUBECONFIG_CONTEXT", None)
        os.environ.pop("DISCORD_WEBHOOK_URL", None)
        os.environ.pop("SLACK_WEBHOOK_URL", None)


def test_corrupt_curriculum_state_fails_closed() -> None:
    manager = CurriculumManager(seed=3)
    state = manager.export_state()
    restored = CurriculumManager(seed=3)
    restored.restore_state(state)
    tampered = dict(state)
    tampered["episode_count"] = 41

    with pytest.raises(ValueError, match="curriculum_state_corrupt"):
        CurriculumManager(seed=3).restore_state(tampered)


def _write_sft_checkpoint(root: Path, *, adapter: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "config.json": '{"model_type":"qwen2"}',
        "generation_config.json": "{}",
        "tokenizer_config.json": "{}",
        "tokenizer.json": "{}",
        "model.safetensors": "weights",
    }
    if adapter:
        files["adapter_config.json"] = "{}"
    hashes = {}
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
        hashes[name] = hashlib.sha256(content.encode()).hexdigest()
    manifest = {
        "base_model": {
            "architecture": "qwen2",
            "name": "Qwen/Qwen2.5-7B-Instruct",
            "revision": "a" * 40,
        },
        "checkpoint_kind": "merged_decoder",
        "file_hashes": hashes,
        "g8_evaluation": {
            "code_commit": "b" * 40,
            "metrics": {"resolution_rate": 1.0},
            "passed": True,
            "run_id": "EXP-G8-SYNTHETIC",
        },
        "lora": {
            "bias": "none",
            "lora_alpha": 32,
            "lora_dropout": 0.05,
            "r": 16,
            "target_modules": [
                "q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj",
            ],
        },
        "schema_version": 1,
        "sft_corpus": {"sha256": "c" * 64},
        "stage": "G8",
        "tokenizer": {"name": "Qwen/Qwen2.5-7B-Instruct", "revision": "c" * 40},
    }
    # Unknown sidecar fields must never be copied into a Stage 9 run manifest.
    manifest["operator_credential"] = "must-not-propagate"
    (root / "checkpoint_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_merged_g8_checkpoint_manifest_is_required(tmp_path: Path) -> None:
    checkpoint = _write_sft_checkpoint(tmp_path / "merged")
    manifest = validate_sft_checkpoint(checkpoint)

    assert manifest["checkpoint_kind"] == "merged_decoder"
    assert "operator_credential" not in manifest
    assert manifest["_raw_manifest_sha256"]


def test_single_weight_hash_is_required(tmp_path: Path) -> None:
    checkpoint = _write_sft_checkpoint(tmp_path / "unhashed-weight")
    manifest = json.loads((checkpoint / "checkpoint_manifest.json").read_text(encoding="utf-8"))
    manifest["file_hashes"].pop("model.safetensors")
    (checkpoint / "checkpoint_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sft_file_hash_mismatch:model.safetensors"):
        validate_sft_checkpoint(checkpoint)


def test_weight_index_shard_completeness_is_enforced(tmp_path: Path) -> None:
    checkpoint = _write_sft_checkpoint(tmp_path / "sharded")
    (checkpoint / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"layer": "missing.safetensors"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sft_weight_shards_missing"):
        validate_sft_checkpoint(checkpoint)


def test_adapter_only_checkpoint_fails_closed(tmp_path: Path) -> None:
    checkpoint = _write_sft_checkpoint(tmp_path / "adapter", adapter=True)

    with pytest.raises(ValueError, match="adapter_only_checkpoint_rejected"):
        validate_sft_checkpoint(checkpoint)


# ── Final Causal-Runtime Regression Tests ─────────────────────────────────────

def test_trl_training_record_conversion_contract() -> None:
    """Requirement 1: build_trl_training_record converts row to exact TRL dataset record."""
    row = _row("single_fault/sf-900", "replay-trl")
    record = build_trl_training_record(row)

    assert "prompt" in record
    assert record["prompt"] == row["model_visible_prompt"]
    assert record["prompt_sha256"] == row["prompt_sha256"]
    assert record["prompt_sha256"] == hashlib.sha256(record["prompt"].encode("utf-8")).hexdigest()
    assert record["provenance_hash"] == row["provenance_hash"]
    assert record["role"] == "remediation"
    assert record["row_id"] == row["row_id"]
    assert record["stage9_group_id"] == row["stage9_group_id"]
    assert record["hidden_metadata"] == row["hidden_metadata"]

    # Reward pairing succeeds with exact record
    prompts = [record["prompt"]]
    metadata = {
        "hidden_metadata": [record["hidden_metadata"]],
        "prompt_sha256": [record["prompt_sha256"]],
        "provenance_hash": [record["provenance_hash"]],
        "role": [record["role"]],
        "row_id": [record["row_id"]],
        "stage9_group_id": [record["stage9_group_id"]],
    }
    rows_by_group = {row["stage9_group_id"]: row}
    paired = validate_reward_pairing(
        prompts,
        ['{"name":"promql_query","arguments":{}}'],
        metadata,
        rows_by_group,
        ENVIRONMENT,
    )
    assert paired == [row]

    # Tampering with prompt invalidates pairing immediately
    with pytest.raises(RuntimeError, match="prompt_metadata_mismatch"):
        validate_reward_pairing(
            ["tampered prompt"],
            ['{"name":"promql_query","arguments":{}}'],
            metadata,
            rows_by_group,
            ENVIRONMENT,
        )


def test_ordered_fault_lifecycle_event_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 2: FAULT_APPLIED < FAULT_ESTABLISHED < POLICY_EXECUTION < VERIFIER_COMPLETED < RESET < POST_RESET_HEALTHY."""
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    row = _row("single_fault/sf-002", "replay-order")
    event_sequence: list[str] = []

    async def mock_handle_incident(*_args, **_kwargs):
        event_sequence.append("HANDLE_INCIDENT_CALLED")
        return {
            "agent_claimed_resolved": True,
            "comms": {},
            "diagnosis": {},
            "env_resolved": True,
            "remediation": {
                "final": {
                    "executed_actions": [{
                        "args": {"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh"},
                        "output": {"success": True},
                        "success": True,
                        "tool": "chaos_stop_experiment",
                    }],
                    "mode": "policy_rollout",
                    "policy_action_admitted": True,
                    "policy_action_identity_match": True,
                    "policy_completion_valid": True,
                }
            },
            "triage": {},
            "verification": {"env_resolved": True},
        }

    monkeypatch.setattr("agents.coordinator.handle_incident", mock_handle_incident)
    monkeypatch.setattr(
        "training.grpo.verify_kubernetes_environment",
        lambda _identity: {"fingerprint": {}, "status": "CONTEXT_VERIFIED"},
    )
    monkeypatch.setattr(
        "training.grpo.cluster_healthy",
        lambda _identity, **_kwargs: (True, {"ready_deployments": 12}),
    )
    monkeypatch.setattr(
        "training.grpo.detect_active_chaos_resources",
        lambda _identity: [],
    )
    monkeypatch.setattr(
        "training.grpo.apply_fault",
        lambda _identity, scenario_id, **_kwargs: {"manifest": "manifest.yaml", "phase": "FAULT_APPLIED", "status": "FAULT_APPLIED"},
    )
    monkeypatch.setattr(
        "training.grpo.fault_established",
        lambda _identity, scenario_id, **_kwargs: {"effect_status": "FAULT_EFFECT_CONFIRMED", "injection_status": "INJECTION_RESOURCE_PRESENT", "phase": "FAULT_ESTABLISHED", "status": "FAULT_ESTABLISHED"},
    )
    monkeypatch.setattr(
        "training.grpo.cleanup_scenario_faults",
        lambda _identity, _plan: {"cleaned_resources": [], "phase": "RESET", "status": "RESET"},
    )

    reward_fn = OnlineRewardFunction(
        [row],
        identity,
        curriculum_seed=42,
        dataset_sha256="c" * 64,
        fault_settle_seconds=0,
        reset_settle_seconds=0,
    )

    result = asyncio.run(reward_fn._score_paired_rollout(row, _policy_completion()))
    lifecycle = result["lifecycle"]
    phases = [entry["phase"] for entry in lifecycle]

    idx_applied = phases.index("FAULT_APPLIED")
    idx_established = phases.index("FAULT_ESTABLISHED")
    idx_policy = phases.index("POLICY_EXECUTION")
    idx_verifier = phases.index("VERIFIER_COMPLETED")
    idx_reset = phases.index("RESET")
    idx_post_healthy = phases.index("POST_RESET_HEALTHY")

    assert idx_applied < idx_established < idx_policy < idx_verifier < idx_reset < idx_post_healthy
    assert result["episode"]["env_resolved"] is True


def test_manifest_derived_fault_plan_fixtures() -> None:
    """Requirement 3: Deterministic fault plans for single_fault/sf-002 and multi_fault/mf-001."""
    plan_sf002 = build_scenario_fault_plan("single_fault/sf-002")
    assert plan_sf002.scenario_id == "single_fault/sf-002"
    assert plan_sf002.tier == "single_fault"
    assert len(plan_sf002.resources) == 1
    r_sf = plan_sf002.resources[0]
    assert r_sf.kind == "StressChaos"
    assert r_sf.name == "sf-002-paymentservice-cpu"
    assert r_sf.namespace == "chaos-mesh"
    assert r_sf.scenario_label == "sf-002"

    plan_mf001 = build_scenario_fault_plan("multi_fault/mf-001")
    assert plan_mf001.scenario_id == "multi_fault/mf-001"
    assert plan_mf001.tier == "multi_fault"
    assert len(plan_mf001.resources) == 2

    resource_names = {(r.kind, r.name, r.scenario_label) for r in plan_mf001.resources}
    assert ("NetworkChaos", "mf-001-frontend-loss", "mf-001") in resource_names
    assert ("StressChaos", "mf-001-checkout-cpu", "mf-001") in resource_names

    # Non-chaos mutating manifest (e.g. ArgoCD Application / Deployment) fails closed
    with pytest.raises(ValueError, match="unsupported_scenario_document_for_stage9"):
        build_scenario_fault_plan("named_replays/hist-aws-s3-2017")


def test_fault_establishment_requires_all_multi_fault_resources() -> None:
    """Requirement 3 & 5: Multi-fault verification fails if any expected resource is absent."""
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    plan_mf001 = build_scenario_fault_plan("multi_fault/mf-001")

    # Mock cluster returning only 1 of 2 resources
    mock_active = [
        {
            "kind": "NetworkChaos",
            "labels": {"scenario": "mf-001"},
            "name": "mf-001-frontend-loss",
            "namespace": "chaos-mesh",
        }
    ]

    original_detect = fault_established.__globals__["detect_active_chaos_resources"]
    fault_established.__globals__["detect_active_chaos_resources"] = lambda _id: mock_active
    try:
        with pytest.raises(InfrastructureInvalid, match="fault_not_objectively_established.*StressChaos/mf-001-checkout-cpu"):
            fault_established(identity, "multi_fault/mf-001", fault_plan=plan_mf001)

        # Both present succeeds
        mock_active.append({
            "kind": "StressChaos",
            "labels": {"scenario": "mf-001"},
            "name": "mf-001-checkout-cpu",
            "namespace": "chaos-mesh",
        })
        res = fault_established(identity, "multi_fault/mf-001", fault_plan=plan_mf001)
        assert res["status"] == "FAULT_ESTABLISHED"
        assert res["matching_resources"] == 2
        assert res["injection_status"] == "INJECTION_RESOURCE_PRESENT"
    finally:
        fault_established.__globals__["detect_active_chaos_resources"] = original_detect


def test_scoped_cleanup_deletes_only_current_rollout_faults() -> None:
    """Requirement 4: Cleanup deletes ONLY scenario resources and verifies they are gone."""
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    plan = build_scenario_fault_plan("single_fault/sf-002")
    deleted_commands = []

    # Mock active state: has sf-002 and an unrelated experiment
    active_cluster = [
        {"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh"},
        {"kind": "PodChaos", "name": "unrelated-experiment", "namespace": "chaos-mesh"},
    ]

    def mock_run_kubectl(_id, args, **_kwargs):
        deleted_commands.append(args)
        if args[0] == "delete" and args[2] == "sf-002-paymentservice-cpu":
            active_cluster.remove(active_cluster[0])
        return {"returncode": 0, "stderr": "", "stdout": "deleted", "success": True}

    original_run = cleanup_scenario_faults.__globals__["_run_kubectl"]
    original_detect = cleanup_scenario_faults.__globals__["detect_active_chaos_resources"]
    cleanup_scenario_faults.__globals__["_run_kubectl"] = mock_run_kubectl
    cleanup_scenario_faults.__globals__["detect_active_chaos_resources"] = lambda _id: list(active_cluster)
    try:
        result = cleanup_scenario_faults(identity, plan)
        assert result["status"] == "RESET"
        assert len(result["cleaned_resources"]) == 1
        assert result["cleaned_resources"][0]["name"] == "sf-002-paymentservice-cpu"
        # Unrelated experiment is untouched
        assert len(active_cluster) == 1
        assert active_cluster[0]["name"] == "unrelated-experiment"
    finally:
        cleanup_scenario_faults.__globals__["_run_kubectl"] = original_run
        cleanup_scenario_faults.__globals__["detect_active_chaos_resources"] = original_detect


def test_preexisting_chaos_resources_fail_closed_before_rollout() -> None:
    """Requirement 4: Detect unexpected active Chaos before rollout and fail closed."""
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    row = _row("single_fault/sf-002", "replay-pre")

    reward_fn = OnlineRewardFunction(
        [row],
        identity,
        curriculum_seed=1,
        dataset_sha256="a" * 64,
        fault_settle_seconds=0,
        reset_settle_seconds=0,
    )

    original_detect = detect_active_chaos_resources
    try:
        OnlineRewardFunction._score_paired_rollout.__globals__["detect_active_chaos_resources"] = (
            lambda _id: [{"kind": "PodChaos", "name": "stray-chaos", "namespace": "chaos-mesh"}]
        )
        OnlineRewardFunction._score_paired_rollout.__globals__["verify_kubernetes_environment"] = (
            lambda _id: {"status": "CONTEXT_VERIFIED"}
        )
        OnlineRewardFunction._score_paired_rollout.__globals__["cluster_healthy"] = (
            lambda _id, **_kwargs: (True, {"ready_deployments": 12})
        )

        with pytest.raises(InfrastructureInvalid, match="preexisting_chaos_resources_detected"):
            asyncio.run(reward_fn._score_paired_rollout(row, _policy_completion()))
    finally:
        OnlineRewardFunction._score_paired_rollout.__globals__["detect_active_chaos_resources"] = original_detect


def test_distinguish_injection_present_from_fault_effect_confirmed() -> None:
    """Requirement 5: Distinguish INJECTION_RESOURCE_PRESENT from FAULT_EFFECT_CONFIRMED."""
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    plan = build_scenario_fault_plan("single_fault/sf-002")

    mock_active = [{
        "kind": "StressChaos",
        "labels": {"scenario": "sf-002"},
        "name": "sf-002-paymentservice-cpu",
        "namespace": "chaos-mesh",
    }]

    original_detect = fault_established.__globals__["detect_active_chaos_resources"]
    fault_established.__globals__["detect_active_chaos_resources"] = lambda _id: mock_active
    try:
        # 1. Without predicate: returns INJECTION_RESOURCE_PRESENT
        res = fault_established(identity, "single_fault/sf-002", fault_plan=plan)
        assert res["injection_status"] == "INJECTION_RESOURCE_PRESENT"
        assert res["effect_status"] == "INJECTION_RESOURCE_PRESENT_ONLY"

        # 2. With passing predicate: returns FAULT_EFFECT_CONFIRMED
        def passing_pred(_id, _sc):
            return True, {"cpu_percent": 92.5}

        res_passing = fault_established(
            identity,
            "single_fault/sf-002",
            fault_plan=plan,
            fault_effect_predicate=passing_pred,
        )
        assert res_passing["effect_status"] == "FAULT_EFFECT_CONFIRMED"

        # 3. With failing predicate: raises InfrastructureInvalid
        def failing_pred(_id, _sc):
            return False, {"cpu_percent": 5.0}

        with pytest.raises(InfrastructureInvalid, match="fault_effect_not_confirmed"):
            fault_established(
                identity,
                "single_fault/sf-002",
                fault_plan=plan,
                fault_effect_predicate=failing_pred,
            )

        # 4. With require_effect_confirmation=True but missing predicate: fails closed
        with pytest.raises(InfrastructureInvalid, match="fault_effect_predicate_missing"):
            fault_established(
                identity,
                "single_fault/sf-002",
                fault_plan=plan,
                require_effect_confirmation=True,
            )
    finally:
        fault_established.__globals__["detect_active_chaos_resources"] = original_detect


def test_hidden_identity_leak_rejection_across_entire_model_visible_payload() -> None:
    """Requirement 6: Complete final model-visible payload rejects hidden keys and scenario aliases."""
    scenario_id = "single_fault/sf-002"

    # 1. Leak into diagnosis.root_cause
    obs1 = _observation(scenario_id)
    obs1["diagnosis"]["root_cause"] = "Fault in sf-002 paymentservice"
    with pytest.raises(ValueError, match="hidden_scenario_value_leak"):
        build_remediation_policy_prompt(obs1, scenario_id=scenario_id)

    # 2. Leak into diagnosis evidence
    obs2 = _observation(scenario_id)
    obs2["diagnosis"]["evidence"] = ["Checked single_fault/sf-002 logs"]
    with pytest.raises(ValueError, match="hidden_scenario_value_leak"):
        build_remediation_policy_prompt(obs2, scenario_id=scenario_id)

    # 3. Leak into incident_id
    obs3 = _observation(scenario_id)
    obs3["incident_id"] = "inc-sf-002-alert"
    with pytest.raises(ValueError, match="hidden_scenario_value_leak"):
        build_remediation_policy_prompt(obs3, scenario_id=scenario_id)

    # 4. Leak into nested recommended actions
    obs4 = _observation(scenario_id)
    obs4["recommendations"] = [{"name": "chaos_stop", "target": "sf-002"}]
    with pytest.raises(ValueError, match="hidden_scenario_value_leak"):
        build_remediation_policy_prompt(obs4, scenario_id=scenario_id)

    # 5. Hidden orchestration keys rejected
    for key in ("scenario_id", "replay_id", "split_hash", "split_eligibility"):
        obs_key = _observation(scenario_id)
        obs_key["triage"][key] = "leak"
        with pytest.raises(ValueError, match="hidden_scenario_leak"):
            build_remediation_policy_prompt(obs_key, scenario_id=scenario_id)

    # 6. Legitimate words in prose do not fail
    obs_valid = _observation(scenario_id)
    obs_valid["diagnosis"]["root_cause"] = "High CPU saturation in service container"
    prompt = build_remediation_policy_prompt(obs_valid, scenario_id=scenario_id)
    assert "High CPU saturation" in prompt


def test_zero_mutation_success_dense_credit_and_safe_reward_attribution() -> None:
    """Requirement 7: No reward for ungrounded mutation success, partial outcome, or read-only action."""
    # 1. Successful unrelated mutation + env unresolved => 0.0 reward
    unrelated_mutation = {
        "args": {"kind": "StressChaos", "name": "unrelated", "namespace": "chaos-mesh"},
        "output": {"success": True},
        "success": True,
        "tool": "chaos_stop_experiment",
    }
    ep1 = {
        "env_resolved": False,
        "outcome": "unresolved",
        "remediation": {
            "final": {
                "executed_actions": [unrelated_mutation],
                "mode": "policy_rollout",
                "policy_action_admitted": True,
                "policy_action_identity_match": True,
                "policy_completion_valid": True,
            }
        },
    }
    b1 = compute_reward_breakdown(ep1)
    assert b1["components"]["dense_policy_action"] == 0.0
    assert b1["components"]["resolution"] == 0.0
    assert b1["total"] == 0.0

    # 2. Model prose outcome="partial" + env unresolved => 0.0 reward (no positive partial reward)
    ep2 = {**ep1, "outcome": "partial"}
    b2 = compute_reward_breakdown(ep2)
    assert b2["components"]["partial_progress"] == 0.0
    assert b2["total"] == 0.0

    # 3. Successful read-only action => 0.0 reward
    readonly_action = {
        "args": {"query": "rate(http_requests_total[1m])"},
        "output": {"success": True},
        "success": True,
        "tool": "promql_query",
    }
    ep3 = {
        "env_resolved": False,
        "outcome": "unresolved",
        "remediation": {
            "final": {
                "executed_actions": [readonly_action],
                "mode": "policy_rollout",
                "policy_action_admitted": True,
                "policy_action_identity_match": True,
                "policy_completion_valid": True,
            }
        },
    }
    assert compute_reward(ep3) == 0.0

    # 4. Verified recovery attributable to exact policy mutation => 0.85 resolution reward
    ep4 = {
        "agent_claimed_resolved": True,
        "env_resolved": True,
        "outcome": "resolved",
        "remediation": {
            "final": {
                "executed_actions": [unrelated_mutation],
                "mode": "policy_rollout",
                "policy_action_admitted": True,
                "policy_action_identity_match": True,
                "policy_completion_valid": True,
            }
        },
    }
    b4 = compute_reward_breakdown(ep4)
    assert b4["components"]["resolution"] == 0.85
    assert b4["total"] == 0.85


def test_observed_cluster_fingerprint_and_resume_identity_rejection() -> None:
    """Requirement 8: Fingerprint cluster instance UID and reject resume on different cluster."""
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")

    mock_outputs = {
        "get namespace kube-system": json.dumps({"metadata": {"uid": "uid-cluster-alpha-12345"}}),
        "version": json.dumps({"serverVersion": {"gitVersion": "v1.31.2"}}),
        "config view": json.dumps({"clusters": [{"cluster": {"server": "https://127.0.0.1:51234"}}]}),
    }

    def mock_run(_id, args, **_kwargs):
        for pattern, out in mock_outputs.items():
            if all(p in args for p in pattern.split()):
                return {"returncode": 0, "stderr": "", "stdout": out, "success": True}
        return {"returncode": 0, "stderr": "", "stdout": "kind-atlasops-local", "success": True}

    original_run = observe_cluster_fingerprint.__globals__["_run_kubectl"]
    observe_cluster_fingerprint.__globals__["_run_kubectl"] = mock_run
    try:
        fingerprint = observe_cluster_fingerprint(identity)
        assert fingerprint["cluster_uid"] == "uid-cluster-alpha-12345"
        assert fingerprint["server_version"] == "v1.31.2"
        assert fingerprint["api_server_endpoint"] == "https://127.0.0.1:51234"

        # Resume validation on same cluster succeeds
        manifest_prev = {
            "code_commit": "a" * 40,
            "contracts": {},
            "curriculum_seed": 42,
            "dataset": {},
            "dependency_versions": {},
            "environment_identity": identity.to_dict(),
            "environment_observed": {
                "fingerprint": fingerprint,
                "identity": identity.to_dict(),
                "status": "CONTEXT_VERIFIED",
            },
            "hyperparameters": {},
            "model_path": "model",
            "sft_manifest": {},
        }
        manifest_curr = dict(manifest_prev)
        validate_resume_identity(manifest_prev, manifest_curr)

        # Resume on recreated cluster with different UID fails closed
        diff_fingerprint = dict(fingerprint, cluster_uid="uid-cluster-beta-recreated")
        manifest_diff = dict(manifest_prev, environment_observed={
            "fingerprint": diff_fingerprint,
            "identity": identity.to_dict(),
            "status": "CONTEXT_VERIFIED",
        })
        with pytest.raises(RuntimeError, match=r"incompatible_resume_identity:\['environment_observed'\]"):
            validate_resume_identity(manifest_prev, manifest_diff)
    finally:
        observe_cluster_fingerprint.__globals__["_run_kubectl"] = original_run


def test_pure_preflight_assembly_boundary(tmp_path: Path) -> None:
    """Requirement 9: Assemble full chain offline without model or k8s."""
    row = _row("single_fault/sf-002", "replay-preflight")

    # 1. TRL record conversion
    trl_record = build_trl_training_record(row)
    assert trl_record["prompt"] == row["model_visible_prompt"]

    # 2. Reward pairing validation
    paired = validate_reward_pairing(
        [trl_record["prompt"]],
        ['{"name":"promql_query","arguments":{}}'],
        {
            "hidden_metadata": [trl_record["hidden_metadata"]],
            "prompt_sha256": [trl_record["prompt_sha256"]],
            "provenance_hash": [trl_record["provenance_hash"]],
            "role": [trl_record["role"]],
            "row_id": [trl_record["row_id"]],
            "stage9_group_id": [trl_record["stage9_group_id"]],
        },
        {row["stage9_group_id"]: row},
        ENVIRONMENT,
    )
    assert len(paired) == 1

    # 3. Reward function setup
    identity = resolve_environment_identity("local-kind", "kind-atlasops-local")
    reward_fn = OnlineRewardFunction(
        [row],
        identity,
        curriculum_seed=42,
        dataset_sha256="a" * 64,
        episodes_path=tmp_path / "episodes.jsonl",
    )
    assert len(reward_fn.rows) == 1

    # 4. Scenario fault plan construction
    fault_plan = build_scenario_fault_plan("single_fault/sf-002")
    assert len(fault_plan.resources) == 1
    assert fault_plan.resources[0].name == "sf-002-paymentservice-cpu"

    # 5. Training rows loading & validation
    dataset_file = tmp_path / "train.jsonl"
    dataset_file.write_text(json.dumps(row) + "\n", encoding="utf-8")
    loaded_rows = load_remediation_training_rows(dataset_file)
    assert len(loaded_rows) == 1
