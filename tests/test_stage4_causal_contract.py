"""Regression tests for Stage 4 causal validity contracts and chaos remediation tools."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.tool_policy import (
    CLUSTER_MUTATING_TOOLS,
    ROLE_ALLOWED_TOOLS,
    SIDE_EFFECTING_TOOLS,
)
from agents.tools import TOOL_REGISTRY
from agents.tools.chaos import ALLOWED_CHAOS_KINDS, ALLOWED_CHAOS_NAMESPACES, chaos_stop_experiment
from scripts.run_stage4_golden_incident import evaluate_causal_g4_predicate


class TestChaosRemediationToolContract:
    """Security and contract tests for chaos_stop_experiment."""

    def test_chaos_tool_registered_in_registry(self) -> None:
        assert "chaos_stop_experiment" in TOOL_REGISTRY
        assert callable(TOOL_REGISTRY["chaos_stop_experiment"])

    def test_chaos_tool_is_remediation_role_only(self) -> None:
        assert "chaos_stop_experiment" in ROLE_ALLOWED_TOOLS["remediation"]
        assert "chaos_stop_experiment" not in ROLE_ALLOWED_TOOLS["triage"]
        assert "chaos_stop_experiment" not in ROLE_ALLOWED_TOOLS["diagnosis"]
        assert "chaos_stop_experiment" not in ROLE_ALLOWED_TOOLS["comms"]

    def test_chaos_tool_is_classified_cluster_mutating(self) -> None:
        assert "chaos_stop_experiment" in CLUSTER_MUTATING_TOOLS
        assert "chaos_stop_experiment" in SIDE_EFFECTING_TOOLS

    def test_chaos_tool_rejects_invalid_kinds(self) -> None:
        res = chaos_stop_experiment("Deployment", "some-deployment", namespace="default")
        assert res["success"] is False
        assert "Invalid chaos kind" in res["error"]

    def test_chaos_tool_rejects_arbitrary_shell_characters(self) -> None:
        res = chaos_stop_experiment("StressChaos", "foo; rm -rf /", namespace="chaos-mesh")
        assert res["success"] is False
        assert "Invalid chaos resource name" in res["error"]

    def test_chaos_tool_rejects_wildcard_names(self) -> None:
        res = chaos_stop_experiment("StressChaos", "*", namespace="chaos-mesh")
        assert res["success"] is False
        assert "Invalid chaos resource name" in res["error"]

    def test_chaos_tool_rejects_empty_or_invalid_namespaces(self) -> None:
        res = chaos_stop_experiment("StressChaos", "valid-name", namespace="bad namespace*")
        assert res["success"] is False
        assert "Unauthorized chaos namespace" in res["error"] or "Invalid namespace" in res["error"]

    def test_allowed_chaos_namespaces_has_no_generic_override(self) -> None:
        assert ALLOWED_CHAOS_NAMESPACES == frozenset({"chaos-mesh"})
        assert "default" not in ALLOWED_CHAOS_NAMESPACES
        import agents.tools.chaos as chaos_mod
        assert not hasattr(chaos_mod, "CHAOS_NAMESPACE_OVERRIDE")

    def test_chaos_tool_rejects_non_allowlisted_namespaces(self) -> None:
        for bad_ns in ["default", "kube-system", "monitoring", "argocd", "some-valid-namespace"]:
            res = chaos_stop_experiment("StressChaos", "sf-002-paymentservice-cpu", namespace=bad_ns)
            assert res["success"] is False
            assert "Unauthorized chaos namespace" in res["error"]

    def test_chaos_mesh_namespace_is_accepted_for_allowlisted_kind(self) -> None:
        with patch("agents.tools.chaos._run", return_value={"success": True, "stdout": "deleted", "returncode": 0}) as mock_run:
            res = chaos_stop_experiment("StressChaos", "any-valid-experiment", namespace="chaos-mesh")
        assert res["success"] is True
        assert res["namespace"] == "chaos-mesh"
        mock_run.assert_called_once()

    @patch("agents.tools.chaos._run")
    def test_chaos_tool_executes_exact_deletion_for_allowed_kind_and_namespace(self, mock_run: MagicMock) -> None:
        mock_run.return_value = {
            "success": True,
            "stdout": 'stresschaos.chaos-mesh.org "sf-002-paymentservice-cpu" deleted',
            "returncode": 0,
        }
        res = chaos_stop_experiment("StressChaos", "sf-002-paymentservice-cpu", namespace="chaos-mesh")
        assert res["success"] is True
        assert res["kind"] == "StressChaos"
        assert res["name"] == "sf-002-paymentservice-cpu"
        assert res["namespace"] == "chaos-mesh"
        mock_run.assert_called_once_with(
            ["kubectl", "delete", "stresschaos", "sf-002-paymentservice-cpu", "-n", "chaos-mesh", "--ignore-not-found=false"],
            timeout=30,
        )


class TestStage4CausalPredicate:
    """Contract tests for evaluate_causal_g4_predicate."""

    def _make_valid_incident_result(self) -> dict:
        return {
            "incident_id": "inc-valid-123",
            "triage": {
                "final": {
                    "severity": "P1",
                    "title": "CPU Stress on Paymentservice",
                    "blast_radius": {"services": ["paymentservice"]},
                }
            },
            "diagnosis": {
                "final": {
                    "root_cause": "Active StressChaos experiment sf-002-paymentservice-cpu",
                    "confidence": "High",
                    "recommended_actions": [
                        {"action": "stop_chaos", "kind": "StressChaos", "name": "sf-002-paymentservice-cpu"}
                    ],
                }
            },
            "approval": {"status": "approved", "token": "apr-123"},
            "remediation": {
                "trajectory": [
                    {
                        "role": "remediation",
                        "tool": "chaos_stop_experiment",
                        "args": {"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh"},
                        "output": {"success": True, "action": "stopped_chaos_experiment"},
                    }
                ],
                "final": {"outcome": "resolved"},
            },
            "verification": {"env_resolved": True, "checks": []},
            "env_resolved": True,
            "comms": {"final": {"slack_posted": True, "postmortem_path": "docs/postmortem.md"}},
        }

    def test_causal_predicate_passes_when_all_valid(self) -> None:
        inc = self._make_valid_incident_result()
        eval_res = evaluate_causal_g4_predicate(
            baseline_healthy=True,
            injection_success=True,
            fault_observed=True,
            incident_result=inc,
            harness_repaired_pre_verification=False,
        )
        assert eval_res["gate_g4_pass"] is True
        assert all(eval_res["criteria"].values())

    def test_causal_predicate_fails_if_harness_repaired_pre_verification(self) -> None:
        inc = self._make_valid_incident_result()
        eval_res = evaluate_causal_g4_predicate(
            baseline_healthy=True,
            injection_success=True,
            fault_observed=True,
            incident_result=inc,
            harness_repaired_pre_verification=True,
        )
        assert eval_res["gate_g4_pass"] is False
        assert eval_res["criteria"]["12_no_harness_repair_pre_verification"] is False

    def test_causal_predicate_fails_if_diagnosis_targets_wrong_service(self) -> None:
        inc = self._make_valid_incident_result()
        # Halucinated checkoutservice OOM
        inc["diagnosis"]["final"] = {
            "root_cause": "checkoutservice deployment bad revision",
            "confidence": "High",
            "recommended_actions": [{"action": "rollback", "target": "checkoutservice"}],
        }
        eval_res = evaluate_causal_g4_predicate(
            baseline_healthy=True,
            injection_success=True,
            fault_observed=True,
            incident_result=inc,
            harness_repaired_pre_verification=False,
        )
        assert eval_res["gate_g4_pass"] is False
        assert eval_res["criteria"]["7_diagnosis_truth_match"] is False

    def test_causal_predicate_fails_if_remediation_did_not_execute_mutating_tool(self) -> None:
        inc = self._make_valid_incident_result()
        # Only read-only promql query executed
        inc["remediation"]["trajectory"] = [
            {"role": "remediation", "tool": "promql_query", "args": {"query": "up"}, "output": {"success": True}}
        ]
        eval_res = evaluate_causal_g4_predicate(
            baseline_healthy=True,
            injection_success=True,
            fault_observed=True,
            incident_result=inc,
            harness_repaired_pre_verification=False,
        )
        assert eval_res["gate_g4_pass"] is False
        assert eval_res["criteria"]["9_remediation_mutating_tool_executed"] is False

    def test_causal_predicate_fails_if_remediation_tool_returned_error(self) -> None:
        inc = self._make_valid_incident_result()
        inc["remediation"]["trajectory"] = [
            {
                "role": "remediation",
                "tool": "chaos_stop_experiment",
                "args": {"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh"},
                "output": {"success": False, "error": "permission denied"},
            }
        ]
        eval_res = evaluate_causal_g4_predicate(
            baseline_healthy=True,
            injection_success=True,
            fault_observed=True,
            incident_result=inc,
            harness_repaired_pre_verification=False,
        )
        assert eval_res["gate_g4_pass"] is False
        assert eval_res["criteria"]["10_remediation_tool_success"] is False

    def test_causal_predicate_fails_if_remediation_target_mismatched(self) -> None:
        inc = self._make_valid_incident_result()
        # Rollback on unrelated service
        inc["remediation"]["trajectory"] = [
            {
                "role": "remediation",
                "tool": "argocd_rollback",
                "args": {"app": "checkoutservice", "revision": "1"},
                "output": {"success": True},
            }
        ]
        eval_res = evaluate_causal_g4_predicate(
            baseline_healthy=True,
            injection_success=True,
            fault_observed=True,
            incident_result=inc,
            harness_repaired_pre_verification=False,
        )
        assert eval_res["gate_g4_pass"] is False
        assert eval_res["criteria"]["11_remediation_target_match"] is False

    def test_causal_predicate_fails_if_env_not_resolved(self) -> None:
        inc = self._make_valid_incident_result()
        inc["env_resolved"] = False
        inc["verification"]["env_resolved"] = False
        eval_res = evaluate_causal_g4_predicate(
            baseline_healthy=True,
            injection_success=True,
            fault_observed=True,
            incident_result=inc,
            harness_repaired_pre_verification=False,
        )
        assert eval_res["gate_g4_pass"] is False
        assert eval_res["criteria"]["13_objective_env_resolved"] is False

    def test_causal_predicate_fails_if_comms_missing(self) -> None:
        inc = self._make_valid_incident_result()
        inc["comms"] = {}
        eval_res = evaluate_causal_g4_predicate(
            baseline_healthy=True,
            injection_success=True,
            fault_observed=True,
            incident_result=inc,
            harness_repaired_pre_verification=False,
        )
        assert eval_res["gate_g4_pass"] is False
        assert eval_res["criteria"]["14_comms_executed"] is False

    def test_causal_predicate_fails_if_mutating_tool_blocked_by_policy(self) -> None:
        inc = self._make_valid_incident_result()
        inc["remediation"]["trajectory"] = [
            {
                "role": "remediation",
                "tool": "chaos_stop_experiment",
                "args": {"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": "default"},
                "output": {"success": False, "error": "unauthorized"},
                "blocked_by_policy": True,
            }
        ]
        inc["env_resolved"] = False
        inc["verification"]["env_resolved"] = False
        eval_res = evaluate_causal_g4_predicate(
            baseline_healthy=True,
            injection_success=True,
            fault_observed=True,
            incident_result=inc,
            harness_repaired_pre_verification=False,
        )
        assert eval_res["gate_g4_pass"] is False
        assert eval_res["criteria"]["9_remediation_mutating_tool_executed"] is False


class TestCoordinatorRemediationContract:
    """Tests for coordinator tool policy checks and remediation execution retry."""

    def test_coordinator_policy_checks_chaos_namespace_allowlist(self) -> None:
        from agents.coordinator import _check_tool_policy

        # Valid namespace: chaos-mesh
        err = _check_tool_policy(
            "remediation",
            "chaos_stop_experiment",
            {"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh"},
            {},
        )
        assert err is None

        # Invalid namespaces: default, kube-system, monitoring, argocd, random
        for bad_ns in ["default", "kube-system", "monitoring", "argocd", "some-ns"]:
            err = _check_tool_policy(
                "remediation",
                "chaos_stop_experiment",
                {"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": bad_ns},
                {},
            )
            assert err is not None
            assert "unauthorized" in err

    def test_coordinator_policy_blocks_chaos_outside_remediation_role(self) -> None:
        from agents.coordinator import _check_tool_policy

        for role in ["triage", "diagnosis", "comms"]:
            err = _check_tool_policy(
                role,
                "chaos_stop_experiment",
                {"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh"},
                {},
            )
            assert err is not None
            assert "not allowed for role" in err or "blocked" in err

    def test_remediation_receives_one_tool_execution_retry_and_normalizes_outcome(self) -> None:
        import asyncio
        from agents.coordinator import call_agent

        # Simulate LLM returning plain text conclusion on turn 0 without calling tools,
        # and returning plain text conclusion again on retry turn 1 claiming resolved.
        mock_response_turn0 = MagicMock()
        mock_response_turn0.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": 'I recommend calling chaos_stop_experiment("StressChaos", "sf-002-paymentservice-cpu")'}}]
        }
        mock_response_turn0.raise_for_status = MagicMock()

        mock_response_turn1 = MagicMock()
        mock_response_turn1.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": '{"outcome": "resolved", "actions_taken": ["chaos_stop_experiment"]}'}}]
        }
        mock_response_turn1.raise_for_status = MagicMock()

        with patch("agents.coordinator.post_with_retry", side_effect=[mock_response_turn0, mock_response_turn1]) as mock_post:
            with patch("agents.coordinator.require_audit_log"):
                result = asyncio.run(call_agent("remediation", {"incident_id": "inc-test-retry"}))

        # Verify retry occurred
        assert mock_post.call_count == 2
        # Verify second call used tool_choice="required"
        assert mock_post.call_args_list[1][0][2]["tool_choice"] == "required"

        # Verify final JSON strictly sanitized: outcome must NOT be resolved, executed_actions must be empty
        final = result["final"]
        assert final["outcome"] == "unresolved"
        assert final["executed_actions"] == []
        assert final["actions_taken"] == []
        assert "proposed_actions" in final

    def test_remediation_executes_tool_on_retry_and_records_executed_actions(self) -> None:
        import asyncio
        from agents.coordinator import call_agent

        # Turn 0: LLM emits text proposal without tool_calls
        mock_response_turn0 = MagicMock()
        mock_response_turn0.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "I will stop the chaos experiment."}}]
        }
        mock_response_turn0.raise_for_status = MagicMock()

        # Turn 1 (Retry): LLM emits actual tool_calls
        mock_response_turn1 = MagicMock()
        mock_response_turn1.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_retry_1",
                        "type": "function",
                        "function": {
                            "name": "chaos_stop_experiment",
                            "arguments": json.dumps({"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh"}),
                        },
                    }],
                }
            }]
        }
        mock_response_turn1.raise_for_status = MagicMock()

        # Turn 2: LLM receives tool output and emits final conclusion
        mock_response_turn2 = MagicMock()
        mock_response_turn2.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": '{"outcome": "resolved", "summary": "chaos stopped"}'}}]
        }
        mock_response_turn2.raise_for_status = MagicMock()

        with patch("agents.coordinator.post_with_retry", side_effect=[mock_response_turn0, mock_response_turn1, mock_response_turn2]):
            with patch("agents.coordinator.require_audit_log"):
                with patch("agents.tools.chaos._run", return_value={"success": True, "stdout": "deleted", "returncode": 0}):
                    result = asyncio.run(call_agent("remediation", {"incident_id": "inc-test-success"}))

        final = result["final"]
        assert final["outcome"] == "resolved"
        assert len(final["executed_actions"]) == 1
        assert final["executed_actions"][0]["tool"] == "chaos_stop_experiment"
        assert final["executed_actions"][0]["success"] is True
        assert "proposed_actions" in final

    def test_prose_tool_name_is_not_extracted_as_tool_call(self) -> None:
        from agents.coordinator import _extract_tool_calls_from_content, _normalize_assistant_tool_calls

        prose = 'Please call chaos_stop_experiment(kind="StressChaos", name="x", namespace="chaos-mesh")'
        assert _extract_tool_calls_from_content(prose) == []
        assert _normalize_assistant_tool_calls({"role": "assistant", "content": prose}) == []

        conclusion = '{"outcome": "resolved", "actions_taken": [{"tool": "chaos_stop_experiment"}]}'
        assert _extract_tool_calls_from_content(conclusion) == []

    def test_legacy_function_call_is_normalized(self) -> None:
        from agents.coordinator import _normalize_assistant_tool_calls

        msg = {
            "role": "assistant",
            "content": "",
            "function_call": {
                "name": "get_test_value",
                "arguments": '{"name": "atlasops-probe"}',
            },
        }
        calls = _normalize_assistant_tool_calls(msg)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "get_test_value"

    def test_remediation_retry_tool_call_still_enforces_namespace_policy(self) -> None:
        import asyncio
        from agents.circuit_breaker import circuit_breaker
        from agents.coordinator import call_agent

        circuit_breaker.reset()
        mock_response_turn0 = MagicMock()
        mock_response_turn0.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "I will stop the chaos experiment."}}]
        }
        mock_response_turn0.raise_for_status = MagicMock()

        mock_response_turn1 = MagicMock()
        mock_response_turn1.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_retry_policy",
                        "type": "function",
                        "function": {
                            "name": "chaos_stop_experiment",
                            "arguments": json.dumps({"kind": "StressChaos", "name": "any-exp", "namespace": "default"}),
                        },
                    }],
                }
            }]
        }
        mock_response_turn1.raise_for_status = MagicMock()

        mock_response_turn2 = MagicMock()
        mock_response_turn2.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": '{"outcome": "resolved", "actions_taken": ["chaos_stop_experiment"]}'}}]
        }
        mock_response_turn2.raise_for_status = MagicMock()

        with patch("agents.coordinator.post_with_retry", side_effect=[mock_response_turn0, mock_response_turn1, mock_response_turn2]):
            with patch("agents.coordinator.require_audit_log"):
                with patch("agents.tools.chaos._run") as mock_run:
                    result = asyncio.run(call_agent("remediation", {"incident_id": "inc-test-policy"}))

        mock_run.assert_not_called()
        assert result["final"]["outcome"] == "unresolved"
        assert result["final"]["executed_actions"] == []
        assert any(step.get("blocked_by_policy") for step in result["trajectory"])

    def test_remediation_retry_tool_call_still_enforces_circuit_breaker(self) -> None:
        import asyncio
        from agents.circuit_breaker import CircuitBreakerTripped, circuit_breaker
        from agents.coordinator import call_agent

        circuit_breaker.reset()
        mock_response_turn0 = MagicMock()
        mock_response_turn0.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "I will stop the chaos experiment."}}]
        }
        mock_response_turn0.raise_for_status = MagicMock()

        mock_response_turn1 = MagicMock()
        mock_response_turn1.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_retry_cb",
                        "type": "function",
                        "function": {
                            "name": "chaos_stop_experiment",
                            "arguments": json.dumps({"kind": "StressChaos", "name": "any-exp", "namespace": "chaos-mesh"}),
                        },
                    }],
                }
            }]
        }
        mock_response_turn1.raise_for_status = MagicMock()

        mock_response_turn2 = MagicMock()
        mock_response_turn2.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": '{"outcome": "resolved", "actions_taken": ["chaos_stop_experiment"]}'}}]
        }
        mock_response_turn2.raise_for_status = MagicMock()

        with patch("agents.coordinator.post_with_retry", side_effect=[mock_response_turn0, mock_response_turn1, mock_response_turn2]):
            with patch("agents.coordinator.require_audit_log"):
                with patch(
                    "agents.coordinator.circuit_breaker.check_before_tool_call",
                    side_effect=CircuitBreakerTripped("Circuit breaker is OPEN — all tool calls blocked"),
                ):
                    with patch("agents.tools.chaos._run") as mock_run:
                        result = asyncio.run(call_agent("remediation", {"incident_id": "inc-test-cb"}))

        mock_run.assert_not_called()
        assert result["final"]["outcome"] == "unresolved"
        assert result["final"]["executed_actions"] == []
        assert any(step.get("blocked_by_circuit_breaker") for step in result["trajectory"])

    def test_coordinator_and_chaos_tool_share_namespace_allowlist(self) -> None:
        from agents.coordinator import _check_tool_policy

        assert ALLOWED_CHAOS_NAMESPACES == frozenset({"chaos-mesh"})
        err = _check_tool_policy(
            "remediation",
            "chaos_stop_experiment",
            {"kind": "NetworkChaos", "name": "any-exp", "namespace": "chaos-mesh"},
            {},
        )
        assert err is None
        err = _check_tool_policy(
            "remediation",
            "chaos_stop_experiment",
            {"kind": "NetworkChaos", "name": "any-exp", "namespace": "kube-system"},
            {},
        )
        assert err is not None
        assert "unauthorized" in err


class TestStage4EvaluationTruthLeakage:
    """Tripwires: evaluation-only ground truth must never reach the model."""

    def test_agent_prompts_do_not_contain_golden_scenario_identity(self) -> None:
        prompts_dir = Path(__file__).resolve().parents[1] / "agents" / "prompts"
        for role in ("diagnosis", "remediation", "triage"):
            prompt_text = (prompts_dir / f"{role}.md").read_text(encoding="utf-8")
            assert "sf-002" not in prompt_text, (
                f"{role}.md leaks golden-scenario identity 'sf-002' to the model"
            )
            assert "paymentservice-cpu" not in prompt_text, (
                f"{role}.md leaks the golden chaos experiment name to the model"
            )

    def test_runner_alert_payload_is_free_of_evaluation_metadata(self) -> None:
        runner_path = Path(__file__).resolve().parents[1] / "scripts" / "run_stage4_golden_incident.py"
        runner_src = runner_path.read_text(encoding="utf-8")
        start = runner_src.index("alert_payload = {")
        # Scan until the payload dict closes (balanced braces from start marker).
        depth = 0
        end = len(runner_src)
        for idx in range(start, len(runner_src)):
            if runner_src[idx] == "{":
                depth += 1
            elif runner_src[idx] == "}":
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    break
        payload_src = runner_src[start:end]
        assert '"scenario_id"' not in payload_src, (
            "Stage 4 harness alert embeds scenario_id — evaluation metadata leak"
        )
        assert "SCENARIO_ID" not in payload_src, (
            "Stage 4 harness alert embeds SCENARIO_ID — evaluation metadata leak"
        )
        assert "TARGET_CHAOS_NAME" not in payload_src, (
            "Stage 4 harness alert names the chaos experiment — root-cause giveaway"
        )

    def test_explicit_scenario_id_resolves_frozen_spec_not_dynamic_guess(self) -> None:
        from agents.verifier import SCENARIO_VERIFICATION_SPECS, EnvironmentVerifier

        verifier = EnvironmentVerifier(kubectl_getter=MagicMock(), alert_lister=MagicMock(), promql_querier=MagicMock())
        spec = verifier.resolve_spec("single_fault/sf-002")
        assert spec is SCENARIO_VERIFICATION_SPECS["single_fault/sf-002"]
        workload_names = [w.name for w in spec.workloads]
        assert workload_names == ["paymentservice"]
        # The frozen SF002 contract has no alert-clearance predicate; dynamic
        # synthesis must not be reached when the explicit scenario ID is passed.
        assert spec.alerts_must_clear == ()
        assert spec.require_chaos_cleared is True

    def test_handle_incident_signature_accepts_evaluation_only_scenario_channel(self) -> None:
        import inspect

        from agents.coordinator import handle_incident

        params = inspect.signature(handle_incident).parameters
        assert "scenario_id" in params
        assert params["scenario_id"].default is None

