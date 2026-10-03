"""NON_EMPIRICAL composition tests; every tool/model observation is a fixture."""

import json
from unittest.mock import AsyncMock

import pytest

from agents import coordinator
from agents.approval import ApprovalGate
from bench import integrated_validation_episode as episode
from tests.test_integrated_completion_provider import safe_runtime as safe_runtime
from tests.test_integrated_inference import rig as rig


def test_validation_membership_uses_existing_accessor():
    ids = episode._validation_ids()
    assert ids == [
        "single_fault/sf-006", "single_fault/sf-007", "cascade/cs-004",
        "multi_fault/mf-004", "named_replays/hist-fastly-2021",
        "named_replays/hist-facebook-bgp-2021",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("arm", ["base", "sft"])
@pytest.mark.parametrize("decision", ["approved", "rejected", "timeout"])
async def test_paired_exact_action_chain_uses_real_policy_gate_and_tool_loop(
    arm, decision, rig, safe_runtime, monkeypatch, tmp_path
):
    monkeypatch.setattr(episode, "_validation_ids", lambda: ["single_fault/fixture"])
    monkeypatch.setenv("KUBECONFIG_CONTEXT", episode.CONTEXT)
    monkeypatch.setenv("ATLASOPS_RL_POLICY_EXECUTE_ACTIONS", "1")
    monkeypatch.setattr(coordinator, "post_with_retry", AsyncMock())
    observed = []
    active = {
        "kind": "StressChaos", "name": "sf-fixture",
        "namespace": "chaos-mesh", "activity_state": "active",
    }

    def observe_chaos():
        observed.append("observe")
        return {
            "success": True, "observation_status": "observed",
            "active_experiments": [active], "inventory": [active],
            "inventory_complete": True, "count": 1,
        }

    def stop(**arguments):
        assert arguments == {
            "kind": "StressChaos", "name": "sf-fixture", "namespace": "chaos-mesh",
        }
        observed.append("action")
        return {"success": True}

    gate = ApprovalGate(timeout_seconds=0.01)
    original_request = gate.request_action

    def approve_fixture(**arguments):
        request = original_request(**arguments)
        observed.append("approval")
        if decision != "timeout":
            assert gate.callback(
                request.token, decision, approved_by="named-fixture-operator",
            )["ok"]
        return request

    monkeypatch.setattr(gate, "request_action", approve_fixture)
    monkeypatch.setattr(coordinator, "approval_gate", gate)
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "chaos_list_experiments", observe_chaos)
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "chaos_stop_experiment", stop)
    monkeypatch.setitem(
        coordinator.TOOL_REGISTRY, "slack_post_update", lambda **_kwargs: {"success": True},
    )

    verification = {
        "env_resolved": True, "verification_status": "passed", "failed_checks": [],
        "checks": [{"name": "recovery", "target": "paymentservice", "passed": True, "required": True}],
    }

    def verify(**_kwargs):
        observed.append("verify")
        return type("Result", (), {
            "env_resolved": True, "verification_status": "passed",
            "to_dict": lambda self: dict(verification),
        })()

    async def settle(**_kwargs):
        observed.append("settle")
        return {
            "settled": True, "timed_out": False, "observations": [
                {"env_resolved": True, "verification_status": "passed", "failed_checks": []},
            ],
        }

    monkeypatch.setattr(coordinator, "settle_environment", settle)
    monkeypatch.setattr("agents.verifier.verify_environment", verify)
    responses = iter([
        {"severity": "P1", "affected_services": ["paymentservice"], "title": "Fixture"},
        {"name": "chaos_list_experiments", "arguments": {}},
        {"root_cause": "Observed stress fixture", "confidence": 0.5},
        {
            "tool": "chaos_stop_experiment",
            "arguments": {"kind": "StressChaos", "name": "sf-fixture", "namespace": "chaos-mesh"},
            "agent_claimed_resolved": False,
        },
        {"slack_posted": False, "summary": "Fixture recovery observed"},
    ])
    monkeypatch.setattr(rig.tokenizer, "decode", lambda *_args, **_kwargs: json.dumps(next(responses)))
    result = await episode.capture_validation_episode(
        engine=rig.engine, arm=arm, scenario_id="single_fault/fixture",
        alert={"commonLabels": {"alertname": "Fixture", "service": "paymentservice"},
               "alerts": [{"labels": {"service": "paymentservice"}}]},
        run_id="fixture-run", incident_id=f"inc-paired-episode-{arm}",
        output_dir=tmp_path / f"episode-{arm}", execute_actions=True,
        kube_context=episode.CONTEXT,
    )
    await rig.engine.close()
    assert result["episode_status"] == "captured_for_review"
    assert result["arm"] == arm
    assert result["resolution"] is None
    assert result["empirical_claim_allowed"] is False
    if decision == "approved":
        assert observed.index("approval") < observed.index("action") < observed.index("settle") < observed.index("verify")
        assert result["incident"]["approval"]["approved_by"] == "named-fixture-operator"
    else:
        assert "action" not in observed
        assert result["incident"]["approval"]["decision"] == decision
        assert result["incident"]["resolved"] is False
        assert decision in result["incident"]["comms"]["final"]["status"]
    assert len(result["incident"]["remediation"]["policy_steps"]) == 1
    assert all(enabled is (arm == "sft") for enabled in rig.model.calls)
    rows = [json.loads(line) for line in rig.journal.read_text().splitlines()]
    assert all(row["evidence_context"]["incident_id"] == result["incident_id"] for row in rows)
    assert "fixture-run" not in json.dumps(rig.tokenizer.templates)
    coordinator.post_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonvalidation_refused_before_model_or_output(rig, tmp_path, monkeypatch):
    def lookup():
        pytest.fail("Split lookup must not run")
    monkeypatch.setattr(episode, "_validation_ids", lookup)
    with pytest.raises(ValueError, match="Validation-only"):
        await episode.capture_validation_episode(
            engine=rig.engine, arm="base", scenario_id="never-looked-up",
            alert={}, run_id="fixture", incident_id="inc-fixture",
            output_dir=tmp_path / "refused", split_name="test",
        )
    assert not (tmp_path / "refused").exists()
    assert rig.model.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "timeout"])
async def test_episode_preserves_failed_or_timed_out_attempt(
    outcome, rig, safe_runtime, monkeypatch, tmp_path
):
    monkeypatch.setattr(episode, "_validation_ids", lambda: ["single_fault/fixture"])
    monkeypatch.setenv("KUBECONFIG_CONTEXT", episode.CONTEXT)
    monkeypatch.setenv("ATLASOPS_RL_POLICY_EXECUTE_ACTIONS", "1")

    async def fail(_alert, **_kwargs):
        if outcome == "timeout":
            import asyncio

            await asyncio.sleep(1)
        raise RuntimeError("fixture-sensitive-message")

    monkeypatch.setattr(coordinator, "handle_incident", fail)
    output = tmp_path / "episode-failure"
    result = await episode.capture_validation_episode(
        engine=rig.engine, arm="base", scenario_id="single_fault/fixture",
        alert={}, run_id="fixture-run", incident_id="inc-episode-failure",
        output_dir=output, execute_actions=True, kube_context=episode.CONTEXT,
        episode_timeout_seconds=0.01,
    )
    assert result["episode_status"] == outcome
    assert result["resolution"] is None
    assert result["reward"] is None
    assert result["time_to_resolve_s"] is None
    assert "fixture-sensitive-message" not in (output / "episode_finished.json").read_text()
    assert (output / "episode_started.json").exists()
    assert (output / "episode_finished.json").exists()
