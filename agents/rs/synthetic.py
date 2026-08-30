"""Obviously synthetic fixtures for offline RS behavior tests.

Names are deliberately fake and unrelated to AtlasOps benchmark scenario IDs.
"""

from __future__ import annotations

from typing import Any

from agents.rs.features import ContextFeatures
from agents.rs.persistence import stable_hash
from agents.rs.schemas import InteractionRow, validate_split_boundaries

FIXTURE_SOURCE = "deterministic-synthetic-fixture-v1"


def synthetic_context(
    incident_key: str,
    *,
    service: str,
    fault_types: tuple[str, ...],
    symptoms: tuple[str, ...],
    diagnosis_text: str,
    mutation_budget_remaining: int = 3,
    approval_granted: bool = False,
) -> ContextFeatures:
    return ContextFeatures(
        incident_key=incident_key,
        service=service,
        namespace="synthetic-namespace",
        fault_types=fault_types,
        symptoms=symptoms,
        severity="P2",
        diagnosis_text=diagnosis_text,
        deployment_recently_changed=False,
        active_chaos_experiment=False,
        mutation_budget_remaining=mutation_budget_remaining,
        approval_granted=approval_granted,
        numeric_features={
            "context_seed": int(stable_hash(incident_key)[:8], 16) % 1_000_000 / 1_000_000.0
        },
    )


def build_synthetic_fixture() -> dict[str, Any]:
    contexts = {
        "cpu": synthetic_context(
            "synthetic-fixture-cpu-query",
            service="alpha-service",
            fault_types=("cpu_saturation",),
            symptoms=("cpu", "saturation"),
            diagnosis_text="Synthetic CPU saturation with sustained high utilization.",
        ),
        "dns": synthetic_context(
            "synthetic-fixture-dns-query",
            service="beta-service",
            fault_types=("dns_failure",),
            symptoms=("dns", "resolution"),
            diagnosis_text="Synthetic DNS resolution failures inside an isolated namespace.",
        ),
        "cold_start": synthetic_context(
            "synthetic-fixture-novel-query",
            service="novel-service",
            fault_types=("unknown_novel_fault",),
            symptoms=("novel", "signal"),
            diagnosis_text="A deliberately novel synthetic fault with no training history.",
        ),
    }
    definitions = [
        ("train", "family-alpha", "synthetic-fixture-cpu-001", "scale_up_cpu_saturation", 0.90, 10.0),
        ("train", "family-alpha", "synthetic-fixture-cpu-001", "rollout_undo_error_rate_regression", 0.70, 11.0),
        ("train", "family-alpha", "synthetic-fixture-cpu-001", "stop_dns_chaos", 0.05, 12.0),
        ("train", "family-alpha", "synthetic-fixture-cpu-002", "scale_up_cpu_saturation", 0.85, 20.0),
        ("train", "family-alpha", "synthetic-fixture-cpu-002", "verify_signal_recovery", 0.60, 21.0),
        ("train", "family-beta", "synthetic-fixture-dns-001", "stop_dns_chaos", 0.95, 30.0),
        ("train", "family-beta", "synthetic-fixture-dns-001", "query_current_service_signal", 0.75, 31.0),
        ("train", "family-beta", "synthetic-fixture-dns-002", "stop_dns_chaos", 0.88, 40.0),
        ("train", "family-tie", "synthetic-fixture-tie-001", "verify_readiness_after_action", 0.50, 50.0),
        ("train", "family-tie", "synthetic-fixture-tie-001", "describe_after_action", 0.50, 51.0),
        ("train", "family-contradiction", "synthetic-fixture-cpu-contradiction-a", "scale_up_cpu_saturation", 0.90, 60.0),
        ("train", "family-contradiction", "synthetic-fixture-cpu-contradiction-a", "rollout_undo_error_rate_regression", 0.10, 61.0),
        ("train", "family-contradiction", "synthetic-fixture-cpu-contradiction-b", "scale_up_cpu_saturation", 0.20, 70.0),
        ("train", "family-contradiction", "synthetic-fixture-cpu-contradiction-b", "rollout_undo_error_rate_regression", 0.85, 71.0),
        ("calibration", "family-sparse", "synthetic-fixture-sparse-001", "silence_noisy_duplicate_alert", 0.80, 100.0),
        ("test", "family-alpha-holdout", "synthetic-fixture-cpu-holdout", "scale_up_cpu_saturation", 1.00, 200.0),
        ("test", "family-beta-holdout", "synthetic-fixture-dns-holdout", "stop_dns_chaos", 1.00, 210.0),
    ]
    rows = [
        InteractionRow(
            incident_key=incident,
            action_id=action_id,
            service="alpha-service" if "cpu" in incident else "beta-service",
            fault_types=("cpu_saturation",) if "cpu" in incident else ("dns_failure",),
            outcome="success" if relevance >= 0.8 else "partial",
            relevance=relevance,
            selected=True,
            split=split,
            eligible_for_fit=split in {"train", "calibration"},
            source_run=FIXTURE_SOURCE,
            recorded_at_unix=timestamp,
            observation_type="synthetic_label",
            rank=rank,
            policy_choice="synthetic_policy",
            approval_result="not_applicable_synthetic",
            executor_outcome="not_executed_synthetic",
            verifier_outcome="synthetic_objective_target",
            counterfactual_status="complete_offline_label",
            context_hash=stable_hash({"service": incident, "fault": action_id}),
            family_id=family,
            episode_id=f"{incident}:synthetic-episode",
        )
        for rank, (split, family, incident, action_id, relevance, timestamp) in enumerate(definitions, start=1)
    ]
    validate_split_boundaries(rows)
    return {
        "marker": FIXTURE_SOURCE,
        "contexts": contexts,
        "rows": rows,
        "expected_preferences": {
            "cpu": "scale_up_cpu_saturation",
            "dns": "stop_dns_chaos",
        },
        "temporal_order": ["train", "calibration", "test"],
    }
