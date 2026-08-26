import asyncio
import json

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agents import adversarial_designer
from agents.adversarial_designer import design_scenario
from bench import runner


def _payload(scenario_id: object) -> dict:
    return {
        "scenario_id": scenario_id,
        "title": "Unit scenario",
        "difficulty": "hard",
        "faults": [{
            "kind": "PodChaos",
            "action": "pod-kill",
            "target_service": "cartservice",
            "params": {},
        }],
    }


def _design_once(payload: dict) -> dict:
    with patch(
        "agents.adversarial_designer._call_judge",
        new_callable=AsyncMock,
        return_value=json.dumps(payload),
    ):
        return asyncio.run(design_scenario([]))


@pytest.mark.parametrize(
    "unsafe_id",
    ["../escape", "nested/name", "windows\\name", "   ", ".", ".."],
)
def test_rejects_unsafe_model_supplied_ids(tmp_path, monkeypatch, unsafe_id):
    monkeypatch.setattr(adversarial_designer, "ADVERSARIAL_DIR", tmp_path)

    with pytest.raises(ValueError, match="unsafe adversarial scenario_id"):
        _design_once(_payload(unsafe_id))

    assert list(tmp_path.iterdir()) == []


def test_missing_model_id_gets_safe_random_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(adversarial_designer, "ADVERSARIAL_DIR", tmp_path)

    result = _design_once({
        "title": "Unit scenario",
        "faults": [_payload("adv-fallback")["faults"][0]],
    })

    assert result["scenario_id"].startswith("adv-")
    assert (tmp_path / f"{result['scenario_id']}.yaml").is_file()


def test_confines_valid_id_and_records_safe_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(adversarial_designer, "ADVERSARIAL_DIR", tmp_path)

    result = _design_once(_payload("adv-safe_1.0"))

    manifest = tmp_path / "adv-safe_1.0.yaml"
    metadata = tmp_path / "adv-safe_1.0.json"
    assert result["scenario_id"] == "adv-safe_1.0"
    assert manifest.is_file()
    assert metadata.is_file()
    assert result["manifest_path"] == str(manifest)
    parsed = json.loads(metadata.read_text(encoding="utf-8"))
    assert parsed["scenario_id"] == "adv-safe_1.0"


def test_refuses_to_overwrite_existing_generated_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(adversarial_designer, "ADVERSARIAL_DIR", tmp_path)
    first = _design_once(_payload("adv-repeat"))
    original_manifest = (tmp_path / "adv-repeat.yaml").read_text(encoding="utf-8")
    original_metadata = (tmp_path / "adv-repeat.json").read_text(encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        _design_once(_payload("adv-repeat"))

    assert first["scenario_id"] == "adv-repeat"
    assert (tmp_path / "adv-repeat.yaml").read_text(encoding="utf-8") == original_manifest
    assert (tmp_path / "adv-repeat.json").read_text(encoding="utf-8") == original_metadata


def test_runner_derives_exploration_id_inside_contract_root(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "MANIFESTS_DIR", tmp_path / "manifests")
    adversarial_dir = runner.MANIFESTS_DIR / "adversarial"
    adversarial_dir.mkdir(parents=True)
    manifest = adversarial_dir / "adv-unit.yaml"
    manifest.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.yaml"
    outside.write_text("{}", encoding="utf-8")

    assert runner._exploration_scenario_id(manifest) == "adversarial/adv-unit"
    with pytest.raises(ValueError):
        runner._exploration_scenario_id(outside)


@pytest.mark.parametrize(
    "fault",
    [
        {
            "kind": "Namespace",
            "action": "create",
            "target_service": "frontend",
            "params": {},
        },
        {
            "kind": "NetworkChaos",
            "action": "partition",
            "target_service": "../../other",
            "params": {},
        },
        {
            "kind": "PodChaos",
            "action": "pod-kill",
            "target_service": "frontend",
            "params": {"selector": {"namespace": "kube-system"}},
        },
        {
            "kind": "StressChaos",
            "action": "cpu",
            "target_service": "frontend",
            "params": {"workers": 999, "load": 90},
        },
        {
            "kind": "PodChaos",
            "action": "pod-kill",
            "target_service": "frontend",
            "params": {"duration": "24h"},
        },
        {
            "kind": "NetworkChaos",
            "action": "delay",
            "target_service": "frontend",
            "params": {"latency": "16h"},
        },
        {
            "kind": "NetworkChaos",
            "action": "delay",
            "target_service": "frontend",
            "params": {"jitter": "99999h"},
        },
        {
            "kind": "StressChaos",
            "action": "memory",
            "target_service": "frontend",
            "params": {"size": "9999Gi"},
        },
    ],
)
def test_rejects_untrusted_fault_semantics(fault):
    with pytest.raises(ValueError):
        adversarial_designer._fault_document(fault, "adv-safe", 0)


def test_manifest_uses_kind_valid_specification():
    pod_document = adversarial_designer._fault_document(
        {
            "kind": "PodChaos",
            "action": "pod-kill",
            "target_service": "frontend",
            "params": {},
        },
        "adv-safe",
        0,
    )
    stress_document = adversarial_designer._fault_document(
        {
            "kind": "StressChaos",
            "action": "cpu",
            "target_service": "frontend",
            "params": {},
        },
        "adv-safe",
        0,
    )
    time_document = adversarial_designer._fault_document(
        {
            "kind": "TimeChaos",
            "action": "offset",
            "target_service": "frontend",
            "params": {},
        },
        "adv-safe",
        0,
    )

    assert pod_document["spec"]["action"] == "pod-kill"
    assert "action" not in stress_document["spec"]
    assert stress_document["spec"]["stressors"] == {
        "cpu": {"workers": 4, "load": 80}
    }
    assert "action" not in time_document["spec"]
    assert time_document["spec"]["timeOffset"] == "+300s"


def test_structured_manifest_contains_only_declared_faults(tmp_path, monkeypatch):
    monkeypatch.setattr(adversarial_designer, "ADVERSARIAL_DIR", tmp_path)
    payload = _payload("adv-yaml-boundary")
    payload["ignored_attack_field"] = {
        "kind": "Namespace",
        "metadata": {"name": "injected"},
    }
    payload["faults"].append({
        "kind": "NetworkChaos",
        "action": "delay",
        "target_service": "checkoutservice",
        "params": {"latency": "2000ms", "correlation": 70},
    })

    result = _design_once(payload)

    import yaml

    documents = list(
        yaml.safe_load_all((tmp_path / "adv-yaml-boundary.yaml").read_text(encoding="utf-8"))
    )
    assert len(documents) == 2
    assert [document["kind"] for document in documents] == ["PodChaos", "NetworkChaos"]
    assert all(document["apiVersion"] == "chaos-mesh.org/v1alpha1" for document in documents)
    assert all(
        document["metadata"]["namespace"] == "chaos-mesh"
        for document in documents
    )
    assert all(
        document["spec"]["selector"]["namespaces"] == ["default"]
        for document in documents
    )
    metadata_path = tmp_path / "adv-yaml-boundary.json"
    parsed_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata_path.is_file()
    assert parsed_metadata.get("ignored_attack_field") is None
    assert result["spec"]["faults"] == parsed_metadata["faults"]
    fault_keys = {"kind", "action", "target_service", "params"}
    assert all(set(fault) == fault_keys for fault in parsed_metadata["faults"])
