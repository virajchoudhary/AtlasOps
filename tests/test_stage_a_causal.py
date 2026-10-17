import copy
import json
from types import SimpleNamespace

import pytest

from agents.stage_a import StageARun, evaluate_acceptance, stage_a_scope, target_identity


def pod_payload():
    return {"items": [{
        "metadata": {"name": "paymentservice-abc", "namespace": "default", "uid": "pod-1",
                     "labels": {"app": "paymentservice"}},
        "spec": {"containers": [{"name": "server", "resources": {"limits": {"cpu": "200m"}}}]},
        "status": {"phase": "Running", "containerStatuses": [{
            "name": "server", "ready": True, "state": {"running": {}},
            "containerID": "containerd://container-1",
        }]},
    }]}


def fault_payload():
    return {
        "kind": "StressChaos",
        "metadata": {"name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh",
                     "uid": "fault-1", "resourceVersion": "10",
                     "creationTimestamp": "1970-01-01T00:16:40Z"},
        "spec": {"duration": "10m", "mode": "one",
                 "selector": {"namespaces": ["default"], "labelSelectors": {"app": "paymentservice"}},
                 "stressors": {"cpu": {"workers": 4, "load": 90}}},
        "status": {
            "instances": {"default/paymentservice-abc/server": {
                "uid": "stress-instance-1", "startTime": "1970-01-01T00:16:40Z",
            }},
            "conditions": [{"type": "Paused", "status": "False"},
                           {"type": "AllRecovered", "status": "False"}],
            "experiment": {"containerRecords": [{
                "phase": "Injected", "id": "default/paymentservice-abc/server",
                "selectorKey": ".", "injectedCount": 1, "recoveredCount": 0,
            }]},
        },
    }


@pytest.fixture
def run(monkeypatch):
    clock = SimpleNamespace(epoch=1000.0, monotonic=500.0)
    monkeypatch.setattr("agents.stage_a.time.time", lambda: clock.epoch)
    monkeypatch.setattr("agents.stage_a.time.monotonic", lambda: clock.monotonic)
    run = StageARun(target_identity(pod_payload()), baseline={
        **observation(999, 0.03), "max_cores": 0.03,
    })
    run.begin_apply()
    run.bind_created_resource(fault_payload())
    run.bind_fault(fault_payload())
    return run, clock


def observation(stamp, counter):
    return {
        "fresh": True, "query_success": True,
        "target_identity": target_identity(pod_payload()),
        "series": [{"metric": {
                        "namespace": "default", "pod": "paymentservice-abc", "container": "server",
                        "id": "/kubepods/burstable/podpod-1/cri-containerd-container-1.scope",
                    },
                    "timestamp": stamp, "source_timestamp": stamp, "value": counter}],
    }


@pytest.mark.parametrize("mutation", [
    lambda value: value["items"].append(copy.deepcopy(value["items"][0])),
    lambda value: value["items"][0]["metadata"].pop("uid"),
    lambda value: value["items"][0]["status"]["containerStatuses"][0].update(ready=False),
    lambda value: value["items"][0]["status"]["containerStatuses"][0].pop("containerID"),
    lambda value: value["items"][0]["spec"]["containers"].append({"name": "sidecar"}),
    lambda value: value["items"][0]["metadata"].update(name='pod"}bad'),
])
def test_target_identity_rejects_unhealthy_ambiguous_or_incomplete_target(mutation):
    payload = pod_payload()
    mutation(payload)
    with pytest.raises(ValueError):
        target_identity(payload)


def test_target_identity_preserves_actual_pod_uid_and_runtime_container_id():
    identity = target_identity(pod_payload())
    assert identity["pod_uid"] == "pod-1"
    assert identity["container_id"] == "containerd://container-1"


@pytest.mark.parametrize("mutation", [
    lambda value: value["metadata"].update(uid="replacement"),
    lambda value: value["status"]["conditions"][1].update(status="True"),
    lambda value: value["status"]["experiment"]["containerRecords"][0].update(id="default/other/server"),
    lambda value: value["status"]["experiment"]["containerRecords"][0].update(id="default/paymentservice-abc/other"),
    lambda value: value["status"].pop("instances"),
    lambda value: value["spec"].update(stressors={"cpu": {"workers": 8, "load": 90}}),
    lambda value: value["metadata"].pop("creationTimestamp"),
])
def test_fault_identity_rejects_replacement_inactive_or_wrong_target(run, mutation):
    context, _ = run
    payload = fault_payload()
    mutation(payload)
    with pytest.raises(ValueError):
        context.bind_fault(payload)


@pytest.mark.parametrize("elapsed", [480.001, 600, 601])
def test_action_deadline_blocks_even_when_resource_still_exists(run, elapsed):
    context, clock = run
    clock.epoch += elapsed
    clock.monotonic += elapsed
    with pytest.raises(RuntimeError, match="deadline"):
        context.require_action_window()


def test_monotonic_overrun_cannot_be_hidden_by_wall_clock(run):
    context, clock = run
    clock.monotonic += 481
    with pytest.raises(RuntimeError, match="deadline"):
        context.require_action_window()


def test_recovery_deadline_rejects_expiry_or_late_verifier_success(run):
    context, clock = run
    clock.epoch += 540.001
    clock.monotonic += 540.001
    with pytest.raises(RuntimeError, match="deadline"):
        context.require_recovery_window()


def test_cpu_recovery_uses_only_two_post_action_counter_samples(run):
    context, clock = run
    context.action_completed_epoch = 1010
    clock.epoch = 1051
    assert context.recovery_check(observation(1020, 20)) is False
    assert context.recovery_check(observation(1050, 20.3)) is True
    assert context.cpu_recovery["measured_cores"] == pytest.approx(0.01)


@pytest.mark.parametrize("first,last", [
    ((1000, 20), (1050, 20.3)),
    ((1020, 20), (1020, 20)),
    ((1020, 20), (1040, 20.3)),
    ((1020, 20), (1050, 19)),
    ((1020, 20), (1050, 30)),
    ((1020, 20), (1150, 20.3)),
])
def test_cpu_recovery_rejects_pre_action_repeated_reset_high_or_future_samples(run, first, last):
    context, clock = run
    context.action_completed_epoch = 1010
    clock.epoch = 1051
    assert context.recovery_check(observation(*first)) is False
    assert context.recovery_check(observation(*last)) is False


def test_missing_or_stale_metrics_cannot_establish_recovery(run):
    context, clock = run
    context.action_completed_epoch = 1010
    clock.epoch = 1051
    context.recovery_check(observation(1020, 20))
    stale = observation(1050, 20.3)
    stale["fresh"] = False
    assert context.recovery_check(stale) is False


@pytest.mark.parametrize("key,value", [
    ("fresh", False), ("query_success", False), ("max_cores", float("inf")),
    ("max_cores", True),
])
def test_stage_a_degradation_rejects_invalid_or_stale_measurements(run, key, value):
    from scripts.run_stage4_golden_incident import sf002_degradation_decision

    context, _ = run
    baseline = {"max_cores": 0.03, "fresh": True, "query_success": True}
    degraded = {**observation(1020, 20), "max_cores": 0.2}
    with stage_a_scope(context):
        assert sf002_degradation_decision(baseline, degraded)["passed"] is True
        degraded[key] = value
        assert sf002_degradation_decision(baseline, degraded)["passed"] is False


def test_final_delete_guard_rejects_replaced_target_before_mutation(run, monkeypatch):
    from scripts import run_stage4_golden_incident as runner

    context, _ = run
    monkeypatch.setattr(runner, "_stage_a_target_unchanged", lambda: False)
    with stage_a_scope(context), pytest.raises(RuntimeError, match="target pod or container"):
        runner._stage_a_before_delete()


def test_scoped_verifier_requires_actual_cpu_recovery_and_target_identity(run, monkeypatch):
    import agents.verifier as verifier

    context, clock = run
    context.action_completed_epoch = 1010
    clock.epoch = 1021
    monkeypatch.setattr(verifier.environment_verifier, "verify", lambda **kwargs: SimpleNamespace(
        env_resolved=True, verification_status="passed", failed_checks=[], checks=[],
        is_false_resolution=False, is_false_negative=False,
    ))
    monkeypatch.setattr(verifier.environment_verifier, "_kubectl_get", lambda *args, **kwargs: {
        "success": True, "parsed": pod_payload(),
    })
    counter = {"value": 20.0, "stamp": 1020.0}

    def query(expression, time_unix=None):
        assert time_unix == clock.epoch
        value = counter["stamp"] if expression.startswith("timestamp(") else counter["value"]
        return {"success": True, "resultType": "vector", "result": [{
            "metric": observation(1020, 20)["series"][0]["metric"],
            "value": [clock.epoch, str(value)],
        }]}

    monkeypatch.setattr("agents.tools.prometheus.promql_query", query)
    with stage_a_scope(context):
        first = verifier.verify_environment("single_fault/sf-002", True)
        assert first.env_resolved is False
        assert "stage_a_post_action_cpu" in first.failed_checks
        clock.epoch = 1051
        counter.update(value=20.3, stamp=1050)
        second = verifier.verify_environment("single_fault/sf-002", True)
        assert second.env_resolved is True
        assert second.checks[-1].passed is True
        assert second.verification_timestamp == clock.epoch


def comms_record(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTMORTEM_DIR", str(tmp_path))
    path = tmp_path / "incident.md"
    path.write_text("paymentservice CPU stress was resolved and verified", encoding="utf-8")
    return {
        "incident_id": "inc-a",
        "diagnosis": {"final": {"root_cause": "paymentservice CPU stress"}},
        "comms": {
            "final": {"incident_id": "inc-a", "summary_for_dashboard": "Resolved and verified.",
                      "postmortem_path": str(path)},
            "trajectory": [{"tool": "postmortem_draft",
                            "output": {"success": True, "postmortem_path": str(path)}}],
        },
    }


def test_canonical_comms_summary_is_scored_without_legacy_key(tmp_path, monkeypatch):
    from scripts.run_stage4_golden_incident import _comms_evidence_satisfied

    assert _comms_evidence_satisfied(comms_record(tmp_path, monkeypatch), env_resolved=True)


def test_conflicting_or_unresolved_comms_summary_fails_scoring(tmp_path, monkeypatch):
    from scripts.run_stage4_golden_incident import _comms_evidence_satisfied

    record = comms_record(tmp_path, monkeypatch)
    record["comms"]["final"]["summary"] = "Unresolved"
    assert not _comms_evidence_satisfied(record, env_resolved=True)
    record["comms"]["final"].pop("summary")
    record["comms"]["final"]["summary_for_dashboard"] = "Unresolved"
    assert not _comms_evidence_satisfied(record, env_resolved=True)


def acceptance_records(run, tmp_path, monkeypatch):
    from scripts.run_stage4_golden_incident import evaluate_causal_g4_predicate
    from tests.test_stage4_causal_contract import TestStage4CausalPredicate

    maker = TestStage4CausalPredicate()
    maker.postmortem_dir = tmp_path
    monkeypatch.setenv("POSTMORTEM_DIR", str(tmp_path))
    incident = maker._make_valid_incident_result()
    incident["remediation"]["trajectory"][-1]["output"]["uid"] = "fault-1"
    inputs = {
        "baseline_healthy": True, "injection_success": True, "fault_observed": True,
        "harness_repaired_pre_verification": False, "degradation_proven": True,
        "primary_evidence_persisted": True,
    }
    criteria = evaluate_causal_g4_predicate(**inputs, incident_result=incident)["criteria"]
    context, clock = run
    context.action_started_epoch = 1009
    context.action_completed_epoch = 1010
    clock.epoch = 1051
    context.recovery_check(observation(1020, 20))
    context.recovery_check(observation(1050, 20.3))
    primary = {
        "experiment_id": "EXP-STAGE4-UNIT", "causal_recovery_pass": True,
        "causal_criteria": criteria, "incident_result": incident, "causal_inputs": inputs,
        "stage_a_causal": context.evidence(), "completed_at": 1052,
        "phases": {"verification": {"verification_report": {"verification_timestamp": 1051}}},
    }
    cleanup = {
        "experiment_id": "EXP-STAGE4-UNIT", "fault_uid": "fault-1", "max_attempts": 3,
        "verified_zero_chaos": True,
        "attempts": [{"started_at": 1053, "postflight_result": {
            "success": True, "stdout": json.dumps({"items": []}),
        }}],
    }
    return primary, cleanup


def test_acceptance_requires_separately_verified_cleanup(run, tmp_path, monkeypatch):
    primary, cleanup = acceptance_records(run, tmp_path, monkeypatch)
    assert evaluate_acceptance(primary, cleanup)["gate_g4_pass"] is True
    cleanup["verified_zero_chaos"] = False
    assert evaluate_acceptance(primary, cleanup)["gate_g4_pass"] is False


@pytest.mark.parametrize("mutation", [
    lambda p, c: p["stage_a_causal"].update(action_completed_epoch=1600),
    lambda p, c: p["phases"]["verification"]["verification_report"].update(verification_timestamp=1541),
    lambda p, c: p["stage_a_causal"].update(created_uid="replacement"),
    lambda p, c: c.update(fault_uid="replacement"),
    lambda p, c: c["attempts"][0].update(started_at=1050),
    lambda p, c: c["attempts"][0]["postflight_result"].update(stdout='{"items":[{}]}'),
    lambda p, c: p["stage_a_causal"]["recovery_observations"].clear(),
    lambda p, c: p["incident_result"]["approval"].update(status="rejected", decision="rejected"),
    lambda p, c: p["incident_result"]["remediation"]["trajectory"][-1]["output"].update(success=False),
    lambda p, c: p["stage_a_causal"]["baseline"]["series"][0].update(source_timestamp=900),
    lambda p, c: p["stage_a_causal"]["fault"]["target_identity"].update(container_id="replacement"),
])
def test_acceptance_rejects_expiry_wrong_uid_early_cleanup_or_missing_measurements(
    run, mutation, tmp_path, monkeypatch,
):
    primary, cleanup = acceptance_records(run, tmp_path, monkeypatch)
    mutation(primary, cleanup)
    assert evaluate_acceptance(primary, cleanup)["gate_g4_pass"] is False


def test_inventory_is_non_executable_and_does_not_change_historical_pins():
    from pathlib import Path

    from config.g4_protocol import APPROVED_G4_PROTOCOL_PROFILE
    from scripts.prepare_stage_a_requalification import inventory

    before = copy.deepcopy(APPROVED_G4_PROTOCOL_PROFILE)
    record = inventory(Path(__file__).resolve().parents[1])
    assert record["active"] is False
    assert record["launch_authorized"] is False
    assert record["model_runtime_verified"] is False
    assert record["sources"]["agents/stage_a.py"]["raw_sha256"]
    assert APPROVED_G4_PROTOCOL_PROFILE == before


def test_injection_cannot_use_baseline_that_became_stale(run):
    context, clock = run
    clock.epoch += 61
    assert not context.baseline_valid_at(clock.epoch)
    with pytest.raises(RuntimeError, match="baseline"):
        context.begin_apply()


@pytest.mark.parametrize("cgroup", [
    "/kubepods/burstable/podreplacement/cri-containerd-container-1.scope",
    "/kubepods/burstable/podpod-1/cri-containerd-replacement.scope",
    "/kubepods/burstable/podpod-1-extra/cri-containerd-container-1.scope",
    "",
])
def test_cpu_recovery_rejects_replacement_or_unbound_cgroup(run, cgroup):
    context, clock = run
    context.action_completed_epoch = 1010
    clock.epoch = 1051
    first = observation(1020, 20)
    last = observation(1050, 20.3)
    first["series"][0]["metric"]["id"] = cgroup
    last["series"][0]["metric"]["id"] = cgroup
    assert context.recovery_check(first) is False
    assert context.recovery_check(last) is False
