"""Adversarial contracts for Stage 9 prompt/scenario/action coupling."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import coordinator
from config.runtime import CurriculumManager
from training.grpo import (
    EnvironmentIdentity,
    InfrastructureInvalid,
    OnlineRewardFunction,
    compute_reward,
    compute_reward_breakdown,
    cluster_healthy,
    fault_established,
    resolve_environment_identity,
    validate_sft_checkpoint,
    validate_reward_pairing,
    verify_kubernetes_environment,
)
from training.stage9_contract import (
    build_remediation_training_row,
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


def test_policy_owned_nonmutating_success_gets_bounded_dense_credit() -> None:
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

    assert breakdown["components"]["dense_policy_action"] == pytest.approx(0.04)
    assert breakdown["total"] == pytest.approx(0.04)


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
    assert breakdown["total"] == pytest.approx(-0.17)


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

    paired = validate_reward_pairing(prompts, ["{}", "{}"], metadata, rows_by_group)
    assert [row["stage9_group_id"] for row in paired] == [
        first["stage9_group_id"],
        second["stage9_group_id"],
    ]


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
    fault_established.__globals__["_run_kubectl"] = lambda *_: {
        "success": True,
        "stdout": json.dumps({"items": []}),
    }
    try:
        with pytest.raises(InfrastructureInvalid, match="fault_not_objectively_established"):
            fault_established(identity, "single_fault/sf-903")
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

    async def fake_handle_incident(alert, *, scenario_id, remediation_policy_completion):
        seen.update({
            "alert": alert,
            "completion": remediation_policy_completion,
            "scenario_id": scenario_id,
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
        "training.grpo.apply_fault",
        lambda _identity, scenario_id: {"status": "FAULT_APPLIED"},
    )
    monkeypatch.setattr(
        "training.grpo.fault_established",
        lambda _identity, scenario_id: {"status": "FAULT_ESTABLISHED"},
    )
    monkeypatch.setattr(
        "training.grpo.reset_faults",
        lambda _identity: {"status": "FAULTS_RESET"},
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
    assert result["lifecycle"][0]["phase"] == "CONTEXT_VERIFIED"
    assert result["lifecycle"][1]["phase"] == "PRE_ROLLOUT_HEALTHY"
    assert result["lifecycle"][-1]["phase"] == "POST_RESET_HEALTHY"


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
        "model.safetensors": "weights",
    }
    if adapter:
        files["adapter_config.json"] = "{}"
    hashes = {}
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
        hashes[name] = hashlib.sha256(content.encode()).hexdigest()
    manifest = {
        "checkpoint_kind": "merged_decoder",
        "file_hashes": hashes,
        "g8_evaluation": {"passed": True},
        "schema_version": 1,
        "sft_corpus": {"sha256": "c" * 64},
        "stage": "G8",
    }
    (root / "checkpoint_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_merged_g8_checkpoint_manifest_is_required(tmp_path: Path) -> None:
    checkpoint = _write_sft_checkpoint(tmp_path / "merged")
    manifest = validate_sft_checkpoint(checkpoint)

    assert manifest["checkpoint_kind"] == "merged_decoder"


def test_adapter_only_checkpoint_fails_closed(tmp_path: Path) -> None:
    checkpoint = _write_sft_checkpoint(tmp_path / "adapter", adapter=True)

    with pytest.raises(ValueError, match="adapter_only_checkpoint_rejected"):
        validate_sft_checkpoint(checkpoint)
