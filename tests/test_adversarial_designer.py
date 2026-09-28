"""Tests for the adversarial scenario designer."""

import json
import asyncio
import hashlib
import math
import os
import subprocess
import sys
import tomllib
import httpx
import pytest
import yaml
from pathlib import Path
from unittest.mock import AsyncMock, patch


def _valid_spec():
    return {
        "scenario_id": "adv-safe-proposal",
        "title": "Two bounded faults",
        "difficulty": "hard",
        "root_cause_chain": ["paymentservice CPU", "checkout timeout"],
        "red_herrings": ["cartservice warning is unrelated"],
        "weakness_targeted": "causal diagnosis",
        "faults": [
            {"kind": "PodChaos", "action": "pod-kill",
             "target_service": "cartservice", "params": {}},
            {"kind": "StressChaos", "action": "cpu",
             "target_service": "paymentservice", "params": {"workers": 2, "load": 80}},
        ],
    }


def test_yaml_is_declared_as_a_runtime_dependency():
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert any(
        dependency.lower().startswith("pyyaml")
        for dependency in project["project"]["dependencies"]
    )


class TestWeaknessExtraction:
    def _episode(self, scenario_id="sf-001", resolved=False, tier="single_fault",
                 reasoning=0.3, correctness=0.4, efficiency=0.5):
        return {
            "scenario_id": scenario_id,
            "resolved": resolved,
            "tier": tier,
            "judge": {"reasoning": reasoning, "correctness": correctness, "efficiency": efficiency},
        }

    def test_extracts_dns_weakness(self):
        from agents.adversarial_designer import _extract_weaknesses
        history = [self._episode(scenario_id="sf-006-dns"), self._episode(scenario_id="sf-006-dns-2")]
        weaknesses = _extract_weaknesses(history)
        assert any("dns" in w.lower() for w in weaknesses)

    def test_extracts_poor_evidence_weakness(self):
        from agents.adversarial_designer import _extract_weaknesses
        history = [self._episode(reasoning=0.1, correctness=0.2) for _ in range(5)]
        weaknesses = _extract_weaknesses(history)
        assert any("evidence" in w.lower() for w in weaknesses)

    def test_resolved_episodes_not_counted(self):
        from agents.adversarial_designer import _extract_weaknesses
        history = [self._episode(resolved=True) for _ in range(10)]
        weaknesses = _extract_weaknesses(history)
        assert len(weaknesses) == 0

    def test_returns_top_3_max(self):
        from agents.adversarial_designer import _extract_weaknesses
        history = [
            self._episode(scenario_id="dns-1"),
            self._episode(scenario_id="network-1"),
            self._episode(scenario_id="cascade-1", tier="cascade"),
            self._episode(reasoning=0.1, correctness=0.1),
        ]
        weaknesses = _extract_weaknesses(history)
        assert len(weaknesses) <= 3


class TestFaultToYaml:
    def test_podchaos_yaml(self):
        from agents.adversarial_designer import _fault_to_yaml
        fault = {"kind": "PodChaos", "action": "pod-kill", "target_service": "frontend", "params": {}}
        yaml = _fault_to_yaml(fault, "adv-test-001", 0)
        assert "PodChaos" in yaml
        assert "pod-kill" in yaml
        assert "frontend" in yaml
        assert "adv-test-001" in yaml

    def test_networkchaos_delay_yaml(self):
        from agents.adversarial_designer import _fault_to_yaml
        fault = {"kind": "NetworkChaos", "action": "delay",
                 "target_service": "checkoutservice", "params": {"latency": "2000ms"}}
        yaml = _fault_to_yaml(fault, "adv-delay", 0)
        assert "NetworkChaos" in yaml
        assert "2000ms" in yaml

    def test_stresschaos_cpu_yaml(self):
        from agents.adversarial_designer import _fault_to_yaml
        fault = {"kind": "StressChaos", "action": "cpu",
                 "target_service": "paymentservice", "params": {"workers": 4, "load": 90}}
        yaml = _fault_to_yaml(fault, "adv-stress", 0)
        assert "StressChaos" in yaml
        assert "workers: 4" in yaml

    def test_dnschaos_yaml(self):
        from agents.adversarial_designer import _fault_to_yaml
        fault = {"kind": "DNSChaos", "action": "error",
                 "target_service": "frontend", "params": {}}
        yaml = _fault_to_yaml(fault, "adv-dns", 0)
        assert "DNSChaos" in yaml
        assert "frontend.default.svc.cluster.local." in yaml
        assert "*.default.svc.cluster.local." not in yaml

    def test_yaml_has_no_deprecated_scheduler(self):
        from agents.adversarial_designer import _fault_to_yaml
        fault = {"kind": "PodChaos", "action": "pod-kill",
                 "target_service": "frontend", "params": {}}
        yaml = _fault_to_yaml(fault, "adv-test", 0)
        assert "scheduler" not in yaml


class TestDesignScenario:
    def test_import_does_not_create_a_repository_relative_output(self, tmp_path):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        subprocess.run(
            [sys.executable, "-c", "import agents.adversarial_designer"],
            cwd=tmp_path, env=env, check=True,
        )
        assert not (tmp_path / "bench").exists()

    def test_invalid_judge_response_fails_without_fabrication(self, tmp_path):
        from agents.adversarial_designer import design_scenario

        with patch("agents.adversarial_designer._call_judge",
                   new_callable=AsyncMock, return_value="not valid json at all"):
            with pytest.raises(ValueError, match="judge response"):
                asyncio.run(design_scenario([], output_dir=tmp_path))

        assert list(tmp_path.iterdir()) == []

    def test_writes_validated_yaml_and_non_empirical_metadata(self, tmp_path):
        from agents.adversarial_designer import design_scenario

        mock_spec = json.dumps({
            "scenario_id": "adv-unit-test-001",
            "title": "Unit test scenario",
            "difficulty": "hard",
            "root_cause_chain": ["currencyservice latency", "checkoutservice timeout"],
            "red_herrings": ["cartservice looks slow but is healthy"],
            "weakness_targeted": "cascade root cause tracing",
            "faults": [
                {"kind": "PodChaos", "action": "pod-kill",
                 "target_service": "cartservice", "params": {}},
                {"kind": "StressChaos", "action": "cpu",
                 "target_service": "paymentservice",
                 "params": {"workers": 2, "load": 80}},
            ],
        })
        with patch("agents.adversarial_designer._call_judge",
                   new_callable=AsyncMock, return_value=mock_spec):
            result = asyncio.run(design_scenario([], output_dir=tmp_path))

        assert result["manifest_path"] == str(tmp_path / "adv-unit-test-001.yaml")
        assert (tmp_path / "adv-unit-test-001.yaml").exists()
        assert (tmp_path / "adv-unit-test-001.json").exists()
        meta = json.loads((tmp_path / "adv-unit-test-001.json").read_text())
        assert meta["scenario_id"] == "adv-unit-test-001"
        assert meta["evidence_classification"] == "GENERATED_UNAPPROVED"
        assert meta["empirical_claim_allowed"] is False
        assert meta["execution_authorized"] is False
        assert meta["model_identity_status"] == "unverified_alias"
        assert meta["manifest_sha256"] == hashlib.sha256(
            (tmp_path / "adv-unit-test-001.yaml").read_bytes()
        ).hexdigest()
        manifests = list(yaml.safe_load_all(
            (tmp_path / "adv-unit-test-001.yaml").read_text(encoding="utf-8")
        ))
        assert [item["kind"] for item in manifests] == ["PodChaos", "StressChaos"]
        assert all(item["metadata"]["namespace"] == "chaos-mesh" for item in manifests)
        assert all(item["spec"]["selector"]["namespaces"] == ["default"] for item in manifests)
        assert "action" not in manifests[1]["spec"]

    @pytest.mark.parametrize(
        "scenario_id",
        ["../../escape", "adv-bad\nkind: PodChaos", "single_fault/sf-002"],
    )
    def test_invalid_scenario_id_cannot_write_outside_output(self, tmp_path, scenario_id):
        from agents.adversarial_designer import design_scenario

        spec = {
            "scenario_id": scenario_id,
            "title": "Invalid ID",
            "difficulty": "hard",
            "root_cause_chain": ["a", "b"],
            "red_herrings": ["decoy"],
            "weakness_targeted": "diagnosis",
            "faults": [
                {"kind": "PodChaos", "action": "pod-kill",
                 "target_service": "cartservice", "params": {}},
                {"kind": "StressChaos", "action": "cpu",
                 "target_service": "paymentservice", "params": {}},
            ],
        }
        with patch("agents.adversarial_designer._call_judge",
                   new_callable=AsyncMock, return_value=json.dumps(spec)):
            with pytest.raises(ValueError, match="scenario"):
                asyncio.run(design_scenario([], output_dir=tmp_path))
        assert list(tmp_path.iterdir()) == []

    def test_untrusted_fault_fields_cannot_inject_yaml(self, tmp_path):
        from agents.adversarial_designer import design_scenario

        spec = {
            "scenario_id": "adv-injection",
            "title": "Invalid action",
            "difficulty": "hard",
            "root_cause_chain": ["a", "b"],
            "red_herrings": ["decoy"],
            "weakness_targeted": "diagnosis",
            "faults": [
                {"kind": "PodChaos", "action": "pod-kill\nmetadata: {name: escaped}",
                 "target_service": "cartservice", "params": {}},
                {"kind": "StressChaos", "action": "cpu",
                 "target_service": "paymentservice", "params": {}},
            ],
        }
        with patch("agents.adversarial_designer._call_judge",
                   new_callable=AsyncMock, return_value=json.dumps(spec)):
            with pytest.raises(ValueError, match="action"):
                asyncio.run(design_scenario([], output_dir=tmp_path))
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        ("fault", "message"),
        [
            ({"kind": "NetworkChaos", "action": "delay", "target_service": "frontend",
              "params": {"latency": "100ms\nkind: PodChaos"}}, "latency"),
            ({"kind": "NetworkChaos", "action": "partition", "target_service": "frontend",
              "params": {}}, "peer service"),
            ({"kind": "StressChaos", "action": "cpu", "target_service": "paymentservice",
              "params": {"workers": True}}, "workers"),
            ({"kind": "TimeChaos", "action": "offset", "target_service": "frontend",
              "params": {"offset": "+999s"}}, "time offset"),
            ({"kind": "PodChaos", "action": "pod-kill", "target_service": "frontend",
              "params": {"duration": "90m"}}, "duration"),
            ({"kind": "PodChaos", "action": "pod-kill", "target_service": "kube-system",
              "params": {}}, "target service"),
        ],
    )
    def test_unsafe_fault_params_are_rejected(self, fault, message):
        from agents.adversarial_designer import _fault_to_yaml

        with pytest.raises(ValueError, match=message):
            _fault_to_yaml(fault, "adv-safe", 0)

    def test_every_advertised_primitive_has_a_bounded_yaml_shape(self):
        from agents.adversarial_designer import ALLOWED_ACTIONS, _fault_to_yaml

        for kind, actions in ALLOWED_ACTIONS.items():
            for action in actions:
                params = {"peer_service": "cartservice"} if action == "partition" else {}
                fault = {
                    "kind": kind, "action": action,
                    "target_service": "frontend", "params": params,
                }
                document = yaml.safe_load(_fault_to_yaml(fault, "adv-safe", 0))
                assert document["kind"] == kind
                assert document["metadata"]["namespace"] == "chaos-mesh"
                assert document["spec"]["selector"] == {
                    "namespaces": ["default"],
                    "labelSelectors": {"app": "frontend"},
                }
                assert document["spec"]["mode"] == "one"
                assert document["spec"]["duration"] == "10m"
                if kind in {"StressChaos", "TimeChaos"}:
                    assert "action" not in document["spec"]

    def test_explicit_external_output_and_history_are_required_before_judge(
        self, tmp_path,
    ):
        from agents.adversarial_designer import REPO_ROOT, design_scenario

        with patch("agents.adversarial_designer._call_judge", new_callable=AsyncMock) as judge:
            for directory in (Path("relative"), REPO_ROOT / "bench"):
                with pytest.raises(ValueError, match="output"):
                    asyncio.run(design_scenario([], output_dir=directory))
            with pytest.raises(ValueError, match="history"):
                asyncio.run(design_scenario(
                    [{"scenario_id": "secret\nin-prompt", "resolved": False}],
                    output_dir=tmp_path,
                ))
            judge.assert_not_awaited()
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize("score", [True, "0.3", math.nan, math.inf, 10**500, -1, 2])
    def test_invalid_history_score_is_rejected_before_judge(self, tmp_path, score):
        from agents.adversarial_designer import design_scenario

        history = [{
            "scenario_id": "single_fault/sf-002",
            "resolved": False,
            "tier": "single_fault",
            "judge": {"reasoning": score},
        }]
        with patch("agents.adversarial_designer._call_judge", new_callable=AsyncMock) as judge:
            with pytest.raises(ValueError, match="failure history judge"):
                asyncio.run(design_scenario(history, output_dir=tmp_path))
            judge.assert_not_awaited()
        assert list(tmp_path.iterdir()) == []

    def test_existing_proposal_is_not_overwritten(self, tmp_path):
        from agents.adversarial_designer import design_scenario

        raw = json.dumps(_valid_spec())
        with patch("agents.adversarial_designer._call_judge",
                   new_callable=AsyncMock, return_value=raw):
            asyncio.run(design_scenario([], output_dir=tmp_path))
            first_yaml = (tmp_path / "adv-safe-proposal.yaml").read_bytes()
            first_meta = (tmp_path / "adv-safe-proposal.json").read_bytes()
            with pytest.raises(FileExistsError, match="already exists"):
                asyncio.run(design_scenario([], output_dir=tmp_path))
        assert (tmp_path / "adv-safe-proposal.yaml").read_bytes() == first_yaml
        assert (tmp_path / "adv-safe-proposal.json").read_bytes() == first_meta

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("difficulty", []),
            ("fault_kind", []),
            ("fault_action", {}),
            ("root_cause_chain", "not a list"),
        ],
    )
    def test_malformed_judge_types_fail_without_output(self, tmp_path, field, value):
        from agents.adversarial_designer import design_scenario

        spec = _valid_spec()
        if field == "fault_kind":
            spec["faults"][0]["kind"] = value
        elif field == "fault_action":
            spec["faults"][0]["action"] = value
        else:
            spec[field] = value
        with patch("agents.adversarial_designer._call_judge",
                   new_callable=AsyncMock, return_value=json.dumps(spec)):
            with pytest.raises(ValueError, match="adversarial"):
                asyncio.run(design_scenario([], output_dir=tmp_path))
        assert list(tmp_path.iterdir()) == []

    def test_redirected_output_directory_is_rejected_before_judge(self, tmp_path):
        from agents.adversarial_designer import design_scenario

        target = tmp_path / "target"
        target.mkdir()
        redirect = tmp_path / "redirect"
        try:
            redirect.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("Directory symlinks are not available on this host")
        with patch("agents.adversarial_designer._call_judge", new_callable=AsyncMock) as judge:
            with pytest.raises(ValueError, match="link or junction"):
                asyncio.run(design_scenario([], output_dir=redirect))
            judge.assert_not_awaited()
        assert list(target.iterdir()) == []

    @pytest.mark.parametrize("count", [0, 11, True])
    def test_batch_count_rejected_before_judge(self, tmp_path, count):
        from agents.adversarial_designer import design_batch

        with patch("agents.adversarial_designer._call_judge", new_callable=AsyncMock) as judge:
            with pytest.raises(ValueError, match="batch count"):
                asyncio.run(design_batch([], count, output_dir=tmp_path))
            judge.assert_not_awaited()
        assert list(tmp_path.iterdir()) == []

    def test_judge_http_is_bounded_and_ignores_ambient_proxy(self, monkeypatch):
        from agents.adversarial_designer import _call_judge

        actual_client = httpx.AsyncClient
        observed = {}

        def transport(request):
            observed["authorization"] = request.headers.get("authorization")
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(_valid_spec())}}]}
            )

        def client_factory(*args, **kwargs):
            observed["trust_env"] = kwargs.get("trust_env")
            return actual_client(*args, transport=httpx.MockTransport(transport), **kwargs)

        monkeypatch.setenv("LLM_API_KEY", "synthetic-only")
        monkeypatch.setenv("HTTP_PROXY", "http://invalid.proxy.test:1")
        monkeypatch.setattr(httpx, "AsyncClient", client_factory)
        assert json.loads(asyncio.run(_call_judge("synthetic"))) == _valid_spec()
        assert observed == {
            "authorization": "Bearer synthetic-only",
            "trust_env": False,
        }

        def oversized(_request):
            return httpx.Response(200, content=b"x" * (256 * 1024 + 1))

        def oversized_client(*args, **kwargs):
            return actual_client(*args, transport=httpx.MockTransport(oversized), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", oversized_client)
        with pytest.raises(ValueError, match="exceeds 256 KiB"):
            asyncio.run(_call_judge("synthetic"))
