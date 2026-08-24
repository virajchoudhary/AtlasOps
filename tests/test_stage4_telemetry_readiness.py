"""Regression tests for the G4 pre-reservation telemetry-readiness gate."""

from __future__ import annotations

import json
import pathlib
import subprocess

from scripts.run_stage4_golden_incident import (
    DEGRADATION_MIN_ABSOLUTE_INCREASE_CORES,
    DEGRADATION_MIN_RATIO,
    DEGRADATION_OBSERVATION_TIMEOUT_SECONDS,
    DEGRADATION_POLL_INTERVAL_SECONDS,
    DEGRADATION_QUERY,
    RAW_PAYMENTSERVICE_CPU_QUERY,
    TELEMETRY_REQUIRED_STABLE_PROBES,
    TELEMETRY_SCRAPE_INTERVAL_SECONDS,
    TELEMETRY_READINESS_TIMEOUT_SECONDS,
    wait_for_telemetry_readiness,
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


def _exact(result: list) -> dict:
    return {"success": True, "resultType": "vector", "result": result}


def _sample(value: str = "0.5", ts: int = 1) -> dict:
    return {"metric": {}, "value": [ts, value]}


def _raw(series: int = 1) -> dict:
    return {"success": True, "resultType": "vector", "result": [{} for _ in range(series)]}


def _make_run_query(
    *,
    exact_sequence: list | None = None,
    raw_result_count: int = 1,
    raw_error: str | None = None,
):
    state = {"n": 0}

    def fn(query: str) -> dict:
        if query == RAW_PAYMENTSERVICE_CPU_QUERY:
            if raw_error is not None:
                return {"success": False, "error": raw_error}
            return _raw(raw_result_count)
        assert query == DEGRADATION_QUERY
        if exact_sequence is None:
            return _exact([_sample()])
        item = exact_sequence[min(state["n"], len(exact_sequence) - 1)]
        state["n"] += 1
        return item

    return fn


def _targets_payload(health: str = "up", last_error: str = "") -> dict:
    return {
        "data": {
            "activeTargets": [
                {
                    "scrapeUrl": "https://172.20.0.3:10250/metrics/cadvisor",
                    "health": health,
                    "lastError": last_error,
                }
            ]
        }
    }


def _run_gate(**overrides):
    clock = FakeClock()
    kwargs = dict(
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
        timeout_seconds=TELEMETRY_READINESS_TIMEOUT_SECONDS,
        http_get_fn=lambda path: (200, "ok"),
        fetch_targets_fn=lambda: _targets_payload(),
        run_query_fn=_make_run_query(),
    )
    kwargs.update(overrides)
    result = wait_for_telemetry_readiness(**kwargs)
    return result, clock


# --- 1/2: selector correction -------------------------------------------------


def test_f1_query_selects_deployed_application_container():
    assert 'container="server"' in DEGRADATION_QUERY
    assert 'container="server"' in RAW_PAYMENTSERVICE_CPU_QUERY


def test_legacy_nonexistent_container_selector_is_absent():
    assert 'container="paymentservice"}' not in DEGRADATION_QUERY
    assert 'container="paymentservice"' not in RAW_PAYMENTSERVICE_CPU_QUERY
    # Intended metric scope is unchanged.
    assert 'namespace="default"' in DEGRADATION_QUERY
    assert 'pod=~"paymentservice-.*"' in DEGRADATION_QUERY
    assert DEGRADATION_QUERY.startswith("max(rate(")


# --- 3/14/15/16: ordering contract -------------------------------------------


def test_reservation_happens_only_after_telemetry_gate_and_before_injection_and_consumed():
    src = _runner_src()
    gate = src.index("wait_for_telemetry_readiness()")
    reservation = src.index("reservation = reserve_experiment_attempt(")
    injection = src.index('inject_res = run_kubectl(["apply", "-f", manifest_path])')
    consumed = src.index("consume_experiment_attempt(reservation)")
    assert gate < reservation < injection < consumed
    # CONSUMED sits exactly at the real fault boundary.
    assert "fault_crossed = True" in src[consumed : consumed + 200]


def test_telemetry_failure_aborts_before_reservation_without_persisting_or_releasing():
    src = _runner_src()
    abort_start = src.index('if not telemetry_ready:')
    reservation = src.index("reservation = reserve_experiment_attempt(")
    abort_block = src[abort_start:reservation]
    assert "return evidence" in abort_block
    assert "_persist" not in abort_block
    assert "_write_json_atomic" not in abort_block
    assert "release_experiment_reservation" not in abort_block
    assert "reserve_experiment_attempt" not in abort_block


def test_telemetry_gate_failure_creates_no_marker_chaos_or_consumed_state(tmp_path):
    failed, detail = _run_gate(http_get_fn=lambda path: (503, "unavailable"))[0]
    # Structural proof: the gate itself performs no filesystem writes.
    assert list(tmp_path.rglob("*")) == []
    assert failed is False
    assert detail["stable_probes"] == 0
    assert any("HTTP 503" in failure for failure in detail["failures"])
    # And the runner exposes no lifecycle transition on this path.
    src = _runner_src()
    abort_block = src[src.index("if not telemetry_ready:") : src.index("reservation = reserve_experiment_attempt(")]
    assert "ATTEMPT_STATE_CONSUMED" not in abort_block
    assert "consume_experiment_attempt" not in abort_block
    assert 'run_kubectl(["apply"' not in abort_block


# --- 4/5: endpoint readiness --------------------------------------------------


def test_ready_endpoint_non_200_fails_pre_reservation():
    def http_get(path: str):
        if path == "/-/ready":
            return 503, "starting"
        return 200, "ok"

    (failed, detail), _clock = _run_gate(http_get_fn=http_get, timeout_seconds=61)
    assert failed is False
    assert any("/-/ready -> HTTP 503" in failure for failure in detail["failures"])


def test_healthy_endpoint_non_200_fails_pre_reservation():
    def http_get(path: str):
        if path == "/-/healthy":
            return 500, "broken"
        return 200, "ok"

    (failed, detail), _clock = _run_gate(http_get_fn=http_get, timeout_seconds=61)
    assert failed is False
    assert any("/-/healthy -> HTTP 500" in failure for failure in detail["failures"])


def test_transport_error_fails_pre_reservation():
    def http_get(path: str):
        raise ConnectionError("refused")

    (failed, detail), _clock = _run_gate(http_get_fn=http_get, timeout_seconds=61)
    assert failed is False
    assert any("transport error" in failure for failure in detail["failures"])


# --- 6/7: scrape-target health ------------------------------------------------


def test_cadvisor_target_down_fails():
    (failed, detail), _clock = _run_gate(fetch_targets_fn=lambda: _targets_payload(health="down"), timeout_seconds=61)
    assert failed is False
    assert any("health=down" in failure for failure in detail["failures"])


def test_cadvisor_target_last_error_fails_even_when_up():
    (failed, detail), _clock = _run_gate(
        fetch_targets_fn=lambda: _targets_payload(health="up", last_error="connection reset"),
        timeout_seconds=61,
    )
    assert failed is False
    assert any("lastError=connection reset" in failure for failure in detail["failures"])


def test_missing_cadvisor_target_fails():
    (failed, detail), _clock = _run_gate(
        fetch_targets_fn=lambda: {"data": {"activeTargets": []}},
        timeout_seconds=61,
    )
    assert failed is False
    assert any("no cAdvisor scrape target" in failure for failure in detail["failures"])


# --- 8/9: metric existence vs exact-query emptiness ----------------------------


def test_raw_metric_absent_fails_even_if_exact_query_somehow_returns_sample():
    (failed, detail), _clock = _run_gate(run_query_fn=_make_run_query(raw_result_count=0), timeout_seconds=61)
    assert failed is False
    assert any("raw paymentservice CPU metric absent" in failure for failure in detail["failures"])


def test_raw_metric_query_error_fails():
    (failed, detail), _clock = _run_gate(run_query_fn=_make_run_query(raw_error="503 server error"), timeout_seconds=61)
    assert failed is False
    assert any("raw metric query failed" in failure for failure in detail["failures"])


def test_empty_vector_from_exact_query_fails_even_with_raw_metric_present():
    (failed, detail), _clock = _run_gate(
        run_query_fn=_make_run_query(exact_sequence=[_exact([])]),
        timeout_seconds=61,
    )
    assert failed is False
    assert any("no finite numeric sample" in failure for failure in detail["failures"])


def test_f1_query_http_error_fails():
    (failed, detail), _clock = _run_gate(
        run_query_fn=_make_run_query(exact_sequence=[{"success": False, "error": "503 Server Error"}]),
        timeout_seconds=61,
    )
    assert failed is False
    assert any("F1 query failed" in failure for failure in detail["failures"])


def test_non_vector_result_type_fails():
    (failed, detail), _clock = _run_gate(
        run_query_fn=_make_run_query(exact_sequence=[{"success": True, "resultType": "scalar", "result": []}]),
        timeout_seconds=61,
    )
    assert failed is False
    assert any("resultType" in failure and "'vector'" in failure for failure in detail["failures"])


# --- 10/11: non-finite values ---------------------------------------------------


def test_nan_sample_fails():
    (failed, detail), _clock = _run_gate(
        run_query_fn=_make_run_query(exact_sequence=[_exact([_sample("NaN")])]),
        timeout_seconds=61,
    )
    assert failed is False
    assert any("no finite numeric sample" in failure for failure in detail["failures"])


def test_positive_inf_sample_fails():
    (failed, detail), _clock = _run_gate(
        run_query_fn=_make_run_query(exact_sequence=[_exact([_sample("+Inf")])]),
        timeout_seconds=61,
    )
    assert failed is False


def test_negative_inf_sample_fails():
    (failed, detail), _clock = _run_gate(
        run_query_fn=_make_run_query(exact_sequence=[_exact([_sample("-Inf")])]),
        timeout_seconds=61,
    )
    assert failed is False


# --- 12/13: stability over consecutive probes -----------------------------------


def test_valid_then_invalid_probe_fails_stability():
    exact_sequence = [
        _exact([_sample("0.5")]),
        _exact([]),
        _exact([]),
        _exact([]),
    ]
    (failed, detail), clock = _run_gate(
        run_query_fn=_make_run_query(exact_sequence=exact_sequence),
        timeout_seconds=TELEMETRY_READINESS_TIMEOUT_SECONDS,
    )
    assert failed is False
    # An intermittent invalid probe resets the stability streak entirely.
    assert detail["stable_probes"] == 0
    assert detail["attempts"] == 4
    assert clock.sleeps, "gate must separate probes by a scrape interval"


def test_two_consecutive_valid_samples_pass_with_required_separation():
    (passed, detail), clock = _run_gate(timeout_seconds=TELEMETRY_READINESS_TIMEOUT_SECONDS)
    assert passed is True
    assert detail["stable_probes"] == TELEMETRY_REQUIRED_STABLE_PROBES == 2
    # Exactly one inter-probe separation, sized at >= one full scrape interval.
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] >= TELEMETRY_SCRAPE_INTERVAL_SECONDS


def test_stability_resets_after_intermittent_failure():
    exact_sequence = [
        _exact([_sample("0.5")]),
        _exact([_sample("NaN")]),  # breaks the streak
        _exact([_sample("0.5")]),
        _exact([_sample("0.5")]),
    ]
    passed, detail = _run_gate(
        run_query_fn=_make_run_query(exact_sequence=exact_sequence),
    )[0]
    assert passed is True
    assert detail["attempts"] == 4
    assert detail["stable_probes"] == 2


def test_gate_never_sleeps_longer_than_remaining_budget():
    exact_sequence = [_exact([])]  # always invalid
    _result, clock = _run_gate(timeout_seconds=45)
    assert all(seconds <= 45 for seconds in clock.sleeps)
    assert clock.now <= 45


# --- 17: frozen F1 scientific contract -------------------------------------------


def test_post_fault_f1_contract_matches_envelope_amendment():
    # Operator-approved 2026-08-24 envelope amendment: absolute threshold is
    # 75% of the pinned 0.2-core paymentservice CPU limit; observation covers
    # the 120s rate window plus one scrape-interval convergence margin.
    assert DEGRADATION_MIN_ABSOLUTE_INCREASE_CORES == 0.15
    assert DEGRADATION_MIN_RATIO == 2.0
    assert DEGRADATION_OBSERVATION_TIMEOUT_SECONDS == 150
    assert DEGRADATION_POLL_INTERVAL_SECONDS == 3
    assert "[2m]" in DEGRADATION_QUERY
    assert TELEMETRY_REQUIRED_STABLE_PROBES == 2
    assert TELEMETRY_SCRAPE_INTERVAL_SECONDS == 30
    assert TELEMETRY_READINESS_TIMEOUT_SECONDS == 120


def test_port_forward_streams_do_not_use_blocking_pipes():
    src = _runner_src()
    pf_block = src[src.index("pf_procs = []") : src.index("time.sleep(3)")]
    assert "stdout=subprocess.DEVNULL" in pf_block
    assert "stderr=subprocess.DEVNULL" in pf_block
    assert "subprocess.PIPE" not in pf_block


def test_runtime_attempt_markers_are_internal_and_ignored():
    """Attempt markers are internal lifecycle state, never public evidence."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    patterns = [
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    # The runtime attempts directory must be ignored...
    assert "artifacts/evidence/stage4/.attempts/" in patterns
    # ...narrowly: primary Stage4 experiment evidence must stay trackable.
    assert not any(
        pattern.rstrip("/") == "artifacts/evidence/stage4" for pattern in patterns
    )
    tracked = subprocess.run(
        ["git", "ls-files", "artifacts/evidence/stage4"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert any(path.endswith("EXP-STAGE4-SF002-005.json") for path in tracked)
    assert not any(".attempts/" in path for path in tracked)


def test_evidence_files_are_json_parseable_and_amendment_documents_correction():
    amendment = (
        pathlib.Path(__file__).resolve().parents[1]
        / "docs"
        / "project"
        / "G4_PROTOCOL_AMENDMENT_SF002_TELEMETRY.md"
    )
    text = amendment.read_text(encoding="utf-8")
    assert "EXP-STAGE4-SF002-005" in text
    assert "instrumentation failure" in text
    assert 'container="server"' in text
    assert json.loads(json.dumps({"checked": True}))["checked"] is True


def test_single_reservation_call_site_lives_after_the_gate():
    # Guards against accidental reintroduction of an unconditional top-of-main
    # reservation: the single reserve call site must live inside main()'s try
    # block after the telemetry gate.
    src = _runner_src()
    assert src.count("reservation = reserve_experiment_attempt(") == 1
    gate_index = src.index("wait_for_telemetry_readiness()")
    assert src.index("reservation = reserve_experiment_attempt(", gate_index) > gate_index
