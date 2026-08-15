"""Tests for coordinator routing and tool narration."""

import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestToolNarration:
    def test_narrate_kubectl_get(self):
        from agents.coordinator import _narrate_tool_call
        result = _narrate_tool_call("triage", "kubectl_get", {"resource": "pods"})
        assert "pods" in result.lower() or "cluster" in result.lower()

    def test_narrate_promql(self):
        from agents.coordinator import _narrate_tool_call
        result = _narrate_tool_call("diagnosis", "promql_query", {"query": "rate(http_requests[1m])"})
        assert "prometheus" in result.lower() or "querying" in result.lower()

    def test_narrate_argocd_rollback(self):
        from agents.coordinator import _narrate_tool_call
        result = _narrate_tool_call("remediation", "argocd_rollback",
                                    {"app": "frontend", "revision": "2"})
        assert "rollback" in result.lower() or "frontend" in result.lower()

    def test_narrate_unknown_tool(self):
        from agents.coordinator import _narrate_tool_call
        result = _narrate_tool_call("triage", "unknown_tool_xyz", {})
        assert "unknown_tool_xyz" in result

    def test_narrate_tool_result_success(self):
        from agents.coordinator import _narrate_tool_result
        result = _narrate_tool_result("kubectl_get", {"success": True, "items": []})
        assert isinstance(result, str) and len(result) > 0

    def test_narrate_tool_result_failure(self):
        from agents.coordinator import _narrate_tool_result
        result = _narrate_tool_result("kubectl_get", {"success": False, "error": "timeout"})
        assert "⚠️" in result or "error" in result.lower()

    def test_conclusion_summaries(self):
        from agents.coordinator import _summarise_conclusion
        for role in ("triage", "diagnosis", "remediation", "comms"):
            result = _summarise_conclusion(role, "some json output")
            assert isinstance(result, str) and len(result) > 10


class TestJsonParsing:
    def test_parse_clean_json(self):
        from agents.coordinator import _try_parse_json
        content = '{"severity": "P1", "next_agent": "diagnosis"}'
        result = _try_parse_json(content)
        assert result["severity"] == "P1"

    def test_parse_json_with_preamble(self):
        from agents.coordinator import _try_parse_json
        content = 'Here is my analysis:\n\n{"severity": "P1"}\n\nDone.'
        result = _try_parse_json(content)
        assert result["severity"] == "P1"

    def test_parse_empty_returns_empty_dict(self):
        from agents.coordinator import _try_parse_json
        result = _try_parse_json("")
        assert result == {}

    def test_parse_invalid_json_returns_raw(self):
        from agents.coordinator import _try_parse_json
        result = _try_parse_json("not json at all")
        assert "raw" in result


class TestBackendConfig:
    @staticmethod
    def _clear_hf_space_leakage(monkeypatch):
        """Other tests (or local env) may leave Space-style vars; coordinator reads env at import."""
        for k in (
            "HF_TOKEN",
            "HUGGING_FACE_HUB_TOKEN",
            "SPACE_AUTHOR_NAME",
            "SPACE_REPO_NAME",
            "SPACE_ID",
            "SYSTEM",
            "VLLM_BASE",
            "JUDGE_URL",
            "ATLASOPS_USE_HF_INFERENCE",
        ):
            monkeypatch.delenv(k, raising=False)

    def test_vllm_defaults(self, monkeypatch):
        self._clear_hf_space_leakage(monkeypatch)
        monkeypatch.setenv("BACKEND", "vllm")
        import importlib
        import agents.coordinator as coord
        importlib.reload(coord)
        assert "localhost" in coord.VLLM_BASE or "8000" in coord.VLLM_BASE

    def test_fireworks_defaults(self, monkeypatch):
        self._clear_hf_space_leakage(monkeypatch)
        monkeypatch.setenv("BACKEND", "fireworks")
        import importlib
        import agents.coordinator as coord
        importlib.reload(coord)
        assert "fireworks" in coord.VLLM_BASE

    def test_api_key_empty_by_default(self, monkeypatch):
        self._clear_hf_space_leakage(monkeypatch)
        monkeypatch.setenv("BACKEND", "vllm")
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        import importlib
        import agents.coordinator as coord
        importlib.reload(coord)
        assert coord.API_KEY == ""


def test_call_agent_requires_audit_secret_before_model_or_tool(monkeypatch):
    monkeypatch.delenv("ATLASOPS_AUDIT_SECRET", raising=False)

    import agents.coordinator as coord
    from agents.audit import AuditConfigurationError

    model_request = AsyncMock()
    tool_call = MagicMock()
    monkeypatch.setattr(coord, "post_with_retry", model_request)
    monkeypatch.setitem(coord.TOOL_REGISTRY, "kubectl_get", tool_call)

    with pytest.raises(
        AuditConfigurationError,
        match="audit_configuration_error: ATLASOPS_AUDIT_SECRET is required for audit integrity",
    ):
        asyncio.run(
            coord.call_agent(
                "triage",
                {"incident_id": "inc-test", "alert": {}},
                max_turns=1,
            )
        )

    model_request.assert_not_awaited()
    tool_call.assert_not_called()


class TestApprovalFlow:
    @pytest.fixture(autouse=True)
    def _configure_safe_test_runtime(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ATLASOPS_LIVE_JUDGE", "0")
        monkeypatch.setenv("ATLASOPS_USE_HF_INFERENCE", "0")
        monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-placeholder-audit-secret")
        monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))

    def test_manual_mode_skips_remediation_agent(self, monkeypatch):
        import agents.coordinator as coord

        triage = {"role": "triage", "trajectory": [], "final": {"severity": "P0"}}
        diagnosis = {"role": "diagnosis", "trajectory": [], "final": {"root_cause": "cpu spike"}}
        comms = {"role": "comms", "trajectory": [], "final": {"status": "posted"}}
        mock_call = AsyncMock(side_effect=[triage, diagnosis, comms])
        monkeypatch.setattr(coord, "call_agent", mock_call)

        result = asyncio.run(coord.handle_incident({"commonLabels": {"alertname": "TestAlert"}}))
        assert result["remediation"]["final"]["mode"] == "manual"
        roles = [c.args[0] for c in mock_call.call_args_list]
        assert roles == ["triage", "diagnosis", "comms"]

    def test_approve_mode_rejected_skips_remediation_execution(self, monkeypatch):
        import agents.coordinator as coord

        triage = {"role": "triage", "trajectory": [], "final": {"severity": "P1"}}
        diagnosis = {"role": "diagnosis", "trajectory": [], "final": {"root_cause": "bad deploy"}}
        comms = {"role": "comms", "trajectory": [], "final": {"status": "posted"}}
        mock_call = AsyncMock(side_effect=[triage, diagnosis, comms])
        monkeypatch.setattr(coord, "call_agent", mock_call)

        async def fake_wait(_incident_id):
            return {"status": "rejected", "approved_by": "oncall", "reason": "needs more evidence"}

        monkeypatch.setattr(coord.approval_gate, "wait_for_decision", fake_wait)

        result = asyncio.run(coord.handle_incident({"commonLabels": {"alertname": "TestAlert"}}))
        assert result["remediation"]["final"]["status"] == "approval_rejected"
        roles = [c.args[0] for c in mock_call.call_args_list]
        assert roles == ["triage", "diagnosis", "comms"]


class TestCoordinatorExecutionAndVerificationTruth:
    @pytest.fixture(autouse=True)
    def _configure_safe_test_runtime(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ATLASOPS_LIVE_JUDGE", "0")
        monkeypatch.setenv("ATLASOPS_USE_HF_INFERENCE", "0")
        monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "test-placeholder-audit-secret")
        monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))

        import agents.coordinator as coord
        async def fake_wait(_incident_id):
            return {"status": "approved", "approved_by": "auto-test"}
        monkeypatch.setattr(coord.approval_gate, "wait_for_decision", fake_wait)

    def test_canonical_execution_ordering_and_verifier_passed_to_comms(self, monkeypatch, tmp_path):
        import agents.coordinator as coord
        from agents.verifier import EnvironmentVerificationResult

        traj_dir = tmp_path / "trajectories"
        monkeypatch.setattr(coord, "TRAJECTORIES_DIR", traj_dir)

        call_order = []

        triage = {"role": "triage", "trajectory": [{"action": "ack"}], "final": {"severity": "P1", "title": "CartService Crash"}}
        diagnosis = {"role": "diagnosis", "trajectory": [{"action": "logs"}], "final": {"root_cause": "OOMKill", "target": "cartservice"}}
        remediation = {"role": "remediation", "trajectory": [{"action": "restart"}], "final": {"outcome": "resolved", "status": "resolved"}}
        comms = {"role": "comms", "trajectory": [{"action": "slack"}], "final": {"summary": "Resolved cartservice crash."}}

        async def fake_call_agent(role, input_data):
            call_order.append((role, input_data))
            if role == "triage":
                return triage
            elif role == "diagnosis":
                return diagnosis
            elif role == "remediation":
                return remediation
            elif role == "comms":
                return comms
            return {"role": role, "trajectory": [], "final": {}}

        def fake_verify_environment(scenario_id, agent_claimed_resolved, alert, incident_context):
            call_order.append(("verify_environment", {
                "scenario_id": scenario_id,
                "agent_claimed_resolved": agent_claimed_resolved,
                "incident_context_has_remediation": "remediation" in incident_context,
            }))
            return EnvironmentVerificationResult(
                scenario_id=scenario_id,
                agent_claimed_resolved=agent_claimed_resolved,
                env_resolved=True,
                verification_status="passed",
                evidence=["Environment fully healthy."],
            )

        monkeypatch.setattr(coord, "call_agent", fake_call_agent)
        import agents.verifier as verifier_mod
        monkeypatch.setattr(verifier_mod, "verify_environment", fake_verify_environment)

        alert = {
            "scenario_id": "single_fault/sf-001",
            "commonLabels": {"alertname": "OnlineBoutiqueCartServiceDown", "service": "cartservice"},
            "alerts": [{"labels": {"alertname": "OnlineBoutiqueCartServiceDown"}}],
        }

        result = asyncio.run(coord.handle_incident(alert))

        # 1. Verify execution order: triage -> diagnosis -> remediation -> verify_environment -> comms
        roles_in_order = [item[0] for item in call_order]
        assert roles_in_order == ["triage", "diagnosis", "remediation", "verify_environment", "comms"]

        # 2. Verify Comms input received objective verification results
        comms_input = next(item[1] for item in call_order if item[0] == "comms")
        assert comms_input["env_resolved"] is True
        assert comms_input["agent_claimed_resolved"] is True
        assert comms_input["verification"]["verification_status"] == "passed"
        assert comms_input["verification"]["env_resolved"] is True

        # 3. Verify returned record has verification truth
        assert result["env_resolved"] is True
        assert result["agent_claimed_resolved"] is True
        assert result["verification"]["verification_status"] == "passed"

        # 4. Verify persisted trajectory on disk matches in-memory record and contains verification
        incident_id = result["incident_id"]
        persisted_file = traj_dir / f"{incident_id}.json"
        assert persisted_file.exists()
        persisted_data = json.loads(persisted_file.read_text(encoding="utf-8"))
        assert persisted_data["incident_id"] == incident_id
        assert persisted_data["env_resolved"] is True
        assert persisted_data["agent_claimed_resolved"] is True
        assert persisted_data["verification"]["verification_status"] == "passed"
        assert persisted_data["comms"] == comms

    def test_agent_claims_resolved_but_verifier_fails(self, monkeypatch, tmp_path):
        import agents.coordinator as coord
        from agents.verifier import EnvironmentVerificationResult

        traj_dir = tmp_path / "trajectories"
        monkeypatch.setattr(coord, "TRAJECTORIES_DIR", traj_dir)

        triage = {"role": "triage", "trajectory": [], "final": {"severity": "P1"}}
        diagnosis = {"role": "diagnosis", "trajectory": [], "final": {"root_cause": "Crash"}}
        remediation = {"role": "remediation", "trajectory": [], "final": {"outcome": "resolved", "status": "resolved"}}
        comms = {"role": "comms", "trajectory": [], "final": {"summary": "Attempted remediation."}}

        comms_received_input = {}

        async def fake_call_agent(role, input_data):
            if role == "triage":
                return triage
            elif role == "diagnosis":
                return diagnosis
            elif role == "remediation":
                return remediation
            elif role == "comms":
                nonlocal comms_received_input
                comms_received_input = input_data
                return comms
            return {"role": role, "trajectory": [], "final": {}}

        def fake_verify_environment(scenario_id, agent_claimed_resolved, alert, incident_context):
            return EnvironmentVerificationResult(
                scenario_id=scenario_id,
                agent_claimed_resolved=agent_claimed_resolved,
                env_resolved=False,
                verification_status="failed",
                evidence=["Pod still in CrashLoopBackOff."],
            )

        monkeypatch.setattr(coord, "call_agent", fake_call_agent)
        import agents.verifier as verifier_mod
        monkeypatch.setattr(verifier_mod, "verify_environment", fake_verify_environment)

        alert = {"scenario_id": "single_fault/sf-001", "commonLabels": {"alertname": "TestAlert"}}
        result = asyncio.run(coord.handle_incident(alert))

        # Comms sees env_resolved=False even though agent claimed resolved
        assert comms_received_input["agent_claimed_resolved"] is True
        assert comms_received_input["env_resolved"] is False
        assert comms_received_input["verification"]["verification_status"] == "failed"

        # Coordinator return and persisted file
        assert result["env_resolved"] is False
        assert result["agent_claimed_resolved"] is True

        incident_id = result["incident_id"]
        persisted_data = json.loads((traj_dir / f"{incident_id}.json").read_text(encoding="utf-8"))
        assert persisted_data["env_resolved"] is False
        assert persisted_data["agent_claimed_resolved"] is True
        assert persisted_data["verification"]["verification_status"] == "failed"
