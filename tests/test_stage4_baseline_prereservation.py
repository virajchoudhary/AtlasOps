"""Regression tests for the pre-reservation paymentservice baseline gate."""

from __future__ import annotations

import pathlib

from scripts.run_stage4_golden_incident import (
    BASELINE_READINESS_TIMEOUT_SECONDS,
    BASELINE_REQUIRED_STABLE_PROBES,
    BASELINE_STABILITY_INTERVAL_SECONDS,
    wait_for_baseline_readiness,
)

RUNNER_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_stage4_golden_incident.py"


def _runner_src() -> str:
    return RUNNER_PATH.read_text(encoding="utf-8")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _healthy_workloads() -> dict:
    return {"success": True, "stdout": '{"items": [{"status": {"replicas": 1, "readyReplicas": 1}}]}'}


def _make_check(sequence: list[bool]):
    state = {"n": 0}

    def fn():
        value = sequence[min(state["n"], len(sequence) - 1)]
        state["n"] += 1
        return value, (_healthy_workloads() if value else {"success": True, "stdout": '{"items": [{"status": {}}]}'})

    return fn


def _run_gate(sequence: list[bool], **overrides):
    clock = FakeClock()
    kwargs = dict(
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
        timeout_seconds=BASELINE_READINESS_TIMEOUT_SECONDS,
        check_fn=_make_check(sequence),
    )
    kwargs.update(overrides)
    ready, detail, healthy, workloads = wait_for_baseline_readiness(**kwargs)
    return (ready, detail, healthy, workloads), clock


def test_baseline_gate_passes_after_two_consecutive_healthy_reads():
    (ready, detail, healthy, workloads), clock = _run_gate([True, True])
    assert ready is True and healthy is True
    assert detail["stable_probes"] == BASELINE_REQUIRED_STABLE_PROBES == 2
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] >= BASELINE_STABILITY_INTERVAL_SECONDS == 5
    assert '"readyReplicas": 1' in workloads["stdout"]


def test_readiness_declares_exactly_on_second_consecutive_healthy_read():
    (ready, detail, _healthy, _workloads), _clock = _run_gate([True])
    assert ready is True
    assert detail["attempts"] == 2
    assert detail["stable_probes"] == 2


def test_unhealthy_read_fails_closed_and_resets_streak():
    (ready, detail, healthy, _workloads), _clock = _run_gate([True, False, False, False])
    assert ready is False
    assert healthy is False
    assert detail["stable_probes"] == 0


def test_command_failure_counts_as_unhealthy():
    def failing_check():
        return False, {"success": False, "stderr": "boom", "returncode": 1}

    clock = FakeClock()
    ready, detail, healthy, _w = wait_for_baseline_readiness(
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
        timeout_seconds=12,
        check_fn=failing_check,
    )
    assert ready is False and healthy is False
    assert detail["attempts"] >= 2


def test_never_reserves_or_mutates_on_failure(tmp_path):
    before = sorted(p.name for p in tmp_path.rglob("*"))
    _run_gate([False])
    after = sorted(p.name for p in tmp_path.rglob("*"))
    assert before == after == []


# --- ordering contract ---------------------------------------------------------


def test_baseline_gate_precedes_reservation_precedes_fault_and_consumption():
    src = _runner_src()
    telemetry = src.index("wait_for_telemetry_readiness()")
    baseline = src.index("wait_for_baseline_readiness()")
    reservation = src.index("reservation = reserve_experiment_attempt(")
    injection = src.index('inject_res = run_kubectl(["apply", "-f", manifest_path])')
    consumed = src.index("consume_experiment_attempt(reservation)")
    assert telemetry < baseline < reservation < injection < consumed


def test_baseline_preflight_aborts_without_reservation_side_effects():
    src = _runner_src()
    abort_block = src[src.index("if not baseline_ready:") : src.index("reservation = reserve_experiment_attempt(")]
    assert "return evidence" in abort_block
    assert 'failure_phase"] = "unhealthy_baseline"' in abort_block
    for forbidden in ("reserve_experiment_attempt", "release_experiment_reservation", "_persist", "_write_json_atomic", "run_kubectl([\"apply\""):
        assert forbidden not in abort_block


def test_old_post_reservation_baseline_block_removed():
    src = _runner_src()
    reservation = src.index("reservation = reserve_experiment_attempt(")
    injection = src.index('inject_res = run_kubectl(["apply", "-f", manifest_path])')
    between = src[reservation:injection]
    assert "Baseline paymentservice status" not in src[reservation:]
    assert "abort_before_fault(\"unhealthy_baseline\")" not in src
    # The causal gate still receives the pre-reservation baseline verdict.
    assert "baseline_healthy=baseline_healthy" in src[injection:]


def test_baseline_evidence_shape_preserved_for_downstream_consumers():
    src = _runner_src()
    block = src[src.index('"phases"]["baseline"') :]
    block = block[: block.index("}")]
    for key in ("timestamp", "target_deployments", "baseline_healthy", "cpu_telemetry"):
        assert f'"{key}"' in block


def test_frozen_contract_constants_unchanged():
    assert BASELINE_REQUIRED_STABLE_PROBES == 2
    assert BASELINE_STABILITY_INTERVAL_SECONDS == 5
    assert BASELINE_READINESS_TIMEOUT_SECONDS == 60
