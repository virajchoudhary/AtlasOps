"""Regression tests for Stage 4 causal validity contracts and chaos remediation tools."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.tool_policy import (
    CLUSTER_MUTATING_TOOLS,
    ROLE_ALLOWED_TOOLS,
    SIDE_EFFECTING_TOOLS,
)
from agents.tools import TOOL_REGISTRY
from agents.tools.chaos import ALLOWED_CHAOS_KINDS, chaos_stop_experiment
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
        assert "Invalid namespace" in res["error"]

    @patch("agents.tools.chaos._run")
    def test_chaos_tool_executes_exact_deletion_for_allowed_kind(self, mock_run: MagicMock) -> None:
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
