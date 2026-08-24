"""Mathematical contract tests for the amended G4 SF002 F1 envelope contract.

Operator-approved amendment (2026-08-24): absolute degradation threshold is
+0.15 cores (75% of the pinned 0.2-core paymentservice CPU limit) and the
post-fault observation window is 150 seconds so the frozen [2m] rate window
can converge onto post-fault samples.
"""

from __future__ import annotations

import pathlib
import re

from scripts.run_stage4_golden_incident import (
    DEGRADATION_MIN_ABSOLUTE_INCREASE_CORES,
    DEGRADATION_MIN_RATIO,
    DEGRADATION_OBSERVATION_TIMEOUT_SECONDS,
    DEGRADATION_POLL_INTERVAL_SECONDS,
    DEGRADATION_QUERY,
)

RUNNER_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_stage4_golden_incident.py"
CHAOS_MANIFEST = (
    pathlib.Path(__file__).resolve().parents[1]
    / "bench"
    / "chaos_manifests"
    / "single_fault"
    / "sf-002.yaml"
)
AMENDMENT_DOC = (
    pathlib.Path(__file__).resolve().parents[1]
    / "docs"
    / "project"
    / "G4_PROTOCOL_AMENDMENT_SF002_TELEMETRY.md"
)

# Audited pinned workload envelope (live paymentservice Deployment `server`
# container resources, verified 2026-08-23 via kubectl; Burstable QoS).
PINNED_PAYMENTSERVICE_CPU_LIMIT_CORES = 0.2
RATE_WINDOW_SECONDS = 120


def _runner_src() -> str:
    return RUNNER_PATH.read_text(encoding="utf-8")


def test_absolute_threshold_is_envelope_derived_value():
    assert DEGRADATION_MIN_ABSOLUTE_INCREASE_CORES == 0.15


def test_threshold_is_physically_reachable_under_pinned_cpu_limit():
    # The pinned container's hard CFS ceiling is 0.2 cores; the predeclared
    # requirement must sit strictly below it to be reachable at all.
    assert PINNED_PAYMENTSERVICE_CPU_LIMIT_CORES == 0.2
    assert DEGRADATION_MIN_ABSOLUTE_INCREASE_CORES < PINNED_PAYMENTSERVICE_CPU_LIMIT_CORES


def test_previous_threshold_was_physically_impossible():
    # The superseded +0.25-core requirement exceeded the 0.2-core hard ceiling,
    # so no amount of stress could ever satisfy it under the pinned workload.
    previous_threshold = 0.25
    assert previous_threshold > PINNED_PAYMENTSERVICE_CPU_LIMIT_CORES


def test_full_quota_contribution_math():
    # Idealized constant extra CPU L sustained for t seconds inside a 120s
    # rate window contributes L * t / 120 cores of observed rate increase.
    def contribution(cores: float, exposed_s: float) -> float:
        return cores * exposed_s / RATE_WINDOW_SECONDS

    # Old contract: even perfect full-quota execution for the entire old 30s
    # observation contributed only 0.05 cores — far below the old 0.25 bar.
    assert contribution(PINNED_PAYMENTSERVICE_CPU_LIMIT_CORES, 30) < 0.25

    # Amended contract: sustained full-quota stress reaches the 0.15 bar once
    # the [2m] window is populated by post-fault samples (150s >= 120s + one
    # ~30s scrape interval).
    assert contribution(PINNED_PAYMENTSERVICE_CPU_LIMIT_CORES, 150) >= 0.15

    # And the required steady-state delivered CPU for +0.15 at full window
    # convergence stays below the pinned ceiling.
    assert 0.15 <= PINNED_PAYMENTSERVICE_CPU_LIMIT_CORES


def test_relative_threshold_unchanged():
    assert DEGRADATION_MIN_RATIO == 2.0


def test_exact_frozen_promql_unchanged():
    assert DEGRADATION_QUERY == (
        'max(rate(container_cpu_usage_seconds_total{namespace="default",'
        'pod=~"paymentservice-.*",container="server"}[2m]))'
    )
    assert "[2m]" in DEGRADATION_QUERY


def test_polling_cadence_and_observation_timeout_amended_values():
    assert DEGRADATION_POLL_INTERVAL_SECONDS == 3
    assert DEGRADATION_OBSERVATION_TIMEOUT_SECONDS == 150


def test_stresschaos_specification_unchanged():
    text = CHAOS_MANIFEST.read_text(encoding="utf-8")
    assert "kind: StressChaos" in text
    assert "name: sf-002-paymentservice-cpu" in text
    assert re.search(r"workers:\s*4\b", text)
    assert re.search(r"load:\s*90\b", text)
    assert re.search(r'duration:\s*"10m"', text)


def test_precondition_ordering_preserved():
    src = _runner_src()
    telemetry = src.index("wait_for_telemetry_readiness()")
    baseline = src.index("wait_for_baseline_readiness()")
    reservation = src.index("reservation = reserve_experiment_attempt(")
    injection = src.index('inject_res = run_kubectl(["apply", "-f", manifest_path])')
    consumed = src.index("consume_experiment_attempt(reservation)")
    assert telemetry < baseline < reservation < injection < consumed


def test_degradation_loop_uses_amended_timeout_constant():
    src = _runner_src()
    assert src.count("DEGRADATION_OBSERVATION_TIMEOUT_SECONDS") >= 2
    assert "DEGRADATION_OBSERVATION_TIMEOUT_SECONDS = 150" in src
    assert "DEGRADATION_OBSERVATION_TIMEOUT_SECONDS = 30" not in src


def test_amendment_documents_derivation_and_historical_integrity():
    text = AMENDMENT_DOC.read_text(encoding="utf-8")
    assert "+0.15 cores" in text or "0.15 cores" in text
    assert "75%" in text
    assert "150 seconds" in text or "150s" in text
    # Historical integrity: prior verdicts are never retroactively rescored.
    assert "EXP-STAGE4-SF002-007" in text
    assert "retroactive" in text.lower() or "no retroactively" in text.lower()
