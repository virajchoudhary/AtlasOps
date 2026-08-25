from __future__ import annotations

import math
from pathlib import Path

import pytest

from agents.rs import RUNBOOK_CATALOGUE
from agents.rs.catalogue import validate_catalogue as validate_catalogue_module
from agents.rs.features import context_from_diagnosis
from agents.rs.integration import RecommendationPacketBuilder, RecommendationToggle
from agents.rs.metrics import (
    coverage_at_k,
    hit_rate_at_k,
    mrr_at_k,
    ndcg_at_k,
)
from agents.rs.recommender import (
    CollaborativeSVDBaseline,
    ContentBasedBaseline,
    HybridRecommender,
    PopularitySuccessBaseline,
    build_content_query,
    rank_candidates,
)
from agents.rs.schemas import (
    ContextFeatures,
    InteractionRow,
    Runbook,
    SchemaError,
    validate_interactions,
    validate_split_boundaries,
)
from agents.tool_policy import CLUSTER_MUTATING_TOOLS, ROLE_ALLOWED_TOOLS


RS_DIR = Path("agents/rs")


def synthetic_context(key: str = "synthetic/inc-001", *, approval: bool = True) -> ContextFeatures:
    return ContextFeatures(
        incident_key=key,
        service="synthetic-service",
        namespace="synthetic-namespace",
        fault_types=("cpu_saturation", "error_rate"),
        symptoms=("cpu", "errors", "recent_release"),
        severity="P2",
        diagnosis_text="Sustained CPU saturation and 5xx errors began after a recent release.",
        deployment_recently_changed=True,
        active_chaos_experiment=False,
        mutation_budget_remaining=3,
        approval_granted=approval,
    )


def synthetic_rows() -> list[InteractionRow]:
    definitions = [
        ("train", "synthetic/train-1", "rollout_undo_error_rate_regression", 1.0, 100.0),
        ("train", "synthetic/train-1", "scale_up_cpu_saturation", 0.8, 101.0),
        ("train", "synthetic/train-1", "verify_signal_recovery", 0.6, 102.0),
        ("train", "synthetic/train-2", "rollout_undo_error_rate_regression", 0.9, 110.0),
        ("train", "synthetic/train-2", "query_current_service_signal", 0.7, 111.0),
        ("calibration", "synthetic/calibration-1", "scale_up_cpu_saturation", 0.9, 200.0),
        ("test", "synthetic/test-1", "rollout_undo_error_rate_regression", 1.0, 300.0),
        ("future_final_test", "synthetic/final-1", "scale_up_cpu_saturation", 1.0, 400.0),
    ]
    rows = [
        InteractionRow(
            incident_key=incident,
            action_id=action_id,
            service="synthetic-service",
            fault_types=("cpu_saturation",),
            outcome="success" if relevance >= 0.8 else "partial",
            relevance=relevance,
            selected=True,
            split=split,
            eligible_for_fit=split in {"train", "calibration"},
            recorded_at_unix=timestamp,
        )
        for split, incident, action_id, relevance, timestamp in definitions
    ]
    validate_split_boundaries(rows)
    return rows


def hybrid_model() -> HybridRecommender:
    return HybridRecommender(
        content_model=ContentBasedBaseline(),
        collaborative_model=CollaborativeSVDBaseline(latent_dimensions=2, iterations=20),
        success_model=PopularitySuccessBaseline(),
    )


def test_catalogue_contains_only_real_remediation_acl_tools():
    assert 20 <= len(RUNBOOK_CATALOGUE) <= 40
    validate_catalogue_module(RUNBOOK_CATALOGUE, ROLE_ALLOWED_TOOLS["remediation"])
    tools = {item.tool_name for item in RUNBOOK_CATALOGUE}
    assert tools <= ROLE_ALLOWED_TOOLS["remediation"]
    mutating = {item.tool_name for item in RUNBOOK_CATALOGUE if item.mutating}
    assert mutating == CLUSTER_MUTATING_TOOLS


def test_runbook_templates_declare_inputs_and_have_json_values():
    ids = [item.action_id for item in RUNBOOK_CATALOGUE]
    assert len(ids) == len(set(ids))
    for runbook in RUNBOOK_CATALOGUE:
        assert isinstance(runbook.parameter_template, dict)
        assert runbook.description.strip()
        if runbook.stage != "remediation":
            assert not runbook.mutating


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action_id": "Bad Id", "tool_name": "kubectl_get"},
        {"action_id": "valid_id", "tool_name": "not_a_real_tool"},
        {"risk": "catastrophic"},
    ],
)
def test_schema_rejects_invalid_runbooks(kwargs):
    base = {
        "action_id": "valid_id",
        "name": "Valid",
        "tool_name": "kubectl_get",
        "parameter_template": {"namespace": "{{namespace}}"},
        "applicable_fault_types": ("cpu_saturation",),
        "prerequisites": ("namespace",),
        "risk": "low",
        "mutating": False,
        "description": "Valid synthetic schema",
    }
    if kwargs.get("tool_name") == "not_a_real_tool":
        runbook = Runbook(**{**base, **kwargs})
        with pytest.raises(SchemaError):
            validate_catalogue_module([runbook], ROLE_ALLOWED_TOOLS["remediation"])
        return
    with pytest.raises(SchemaError):
        Runbook(**{**base, **kwargs})


def test_context_profile_maps_coordinator_shapes_without_runtime_import():
    triage = {
        "severity": "P1",
        "affected_services": ["paymentservice"],
        "labels": {"namespace": "default"},
    }
    diagnosis = {
        "root_cause": "CPU stress caused paymentservice saturation.",
        "evidence": ["Prometheus CPU above 90%", "active StressChaos"],
        "recommended_fix": "stop stress chaos experiment",
        "active_chaos_experiment": True,
    }
    context = context_from_diagnosis(
        incident_key="synthetic/profile",
        triage=triage,
        diagnosis=diagnosis,
        approval_granted=False,
        active_chaos_experiment=True,
    )
    assert context.service == "paymentservice"
    assert "cpu_saturation" in context.fault_types
    assert context.active_chaos_experiment is True
    assert context.approval_granted is False


def test_ranking_is_deterministic_and_top_k_respects_limit():
    scores = {"b_action": 0.7, "a_action": 0.9, "c_action": 0.7, "d_action": 0.4}
    assert rank_candidates(scores, 2) == [("a_action", 0.9), ("b_action", 0.7)]
    assert rank_candidates(scores, 10) == [
        ("a_action", 0.9), ("b_action", 0.7), ("c_action", 0.7), ("d_action", 0.4)
    ]


def test_metric_functions_match_hand_calculated_values():
    relevance = {"a": 0.0, "b": 0.5, "c": 1.0}
    ranking = ["a", "b", "c"]
    assert hit_rate_at_k(relevance, ranking, 2) == 1
    assert mrr_at_k(relevance, ranking, 2) == 0.5
    expected_ndcg = (0.5 / math.log2(3)) / (1.0 / math.log2(2) + 0.5 / math.log2(3))
    assert math.isclose(ndcg_at_k(relevance, ranking, 2), expected_ndcg)
    rankings = [["a"], ["a", "b"]]
    assert coverage_at_k(rankings, 4, 1) == 0.25


def test_leakage_guards_reject_final_and_cross_split_rows():
    legal = synthetic_rows()
    assert all(row.eligible_for_fit == (row.split in {"train", "calibration"}) for row in legal)
    with pytest.raises(SchemaError):
        InteractionRow(
            incident_key="synthetic/illegal-final",
            action_id="scale_up_cpu_saturation",
            service="synthetic-service",
            fault_types=("cpu_saturation",),
            outcome="success",
            relevance=1.0,
            selected=True,
            split="future_final_test",
            eligible_for_fit=True,
        )

    mixed = synthetic_rows()
    mixed.append(InteractionRow(
        incident_key="synthetic/test-1",
        action_id="verify_signal_recovery",
        service="synthetic-service",
        fault_types=("cpu_saturation",),
        outcome="success",
        relevance=0.5,
        selected=True,
        split="train",
        eligible_for_fit=True,
        recorded_at_unix=50.0,
    ))
    with pytest.raises(SchemaError):
        validate_interactions(mixed)
    boundary_overlap = synthetic_rows()
    boundary_overlap[0] = InteractionRow(**{
        **boundary_overlap[0].__dict__,
        "recorded_at_unix": 500.0,
    })
    with pytest.raises(SchemaError):
        validate_split_boundaries(boundary_overlap)


def test_models_fail_closed_on_ineligible_fit_rows():
    rows = [row for row in synthetic_rows() if row.split == "test"]
    models = [ContentBasedBaseline(), CollaborativeSVDBaseline(), PopularitySuccessBaseline()]
    for model in models:
        with pytest.raises(SchemaError):
            model.fit(rows)


def test_synthetic_baselines_learn_expected_preferences_and_hybrid_fits_legally():
    rows = [row for row in synthetic_rows() if row.eligible_for_fit]
    model = hybrid_model()
    model.fit(rows)
    query = build_content_query(synthetic_context())
    candidates = RUNBOOK_CATALOGUE
    content_scores = model.content_model.score(query, candidates)
    success_scores = model.success_model.score(query, candidates)
    collaborative_scores = model.collaborative_model.score(query, candidates)
    assert content_scores["rollout_undo_error_rate_regression"] > 0
    assert success_scores["rollout_undo_error_rate_regression"] > success_scores["silence_flapping_alert_during_mitigation"]
    assert all(math.isfinite(score) for score in collaborative_scores.values())
    hybrid_scores = model.score(query, candidates, risk_penalty_by_action={})
    ranked = rank_candidates(hybrid_scores, 5)
    assert len(ranked) == 5
    assert ranked == sorted(ranked, key=lambda item: (-item[1], item[0]))


def test_packet_filters_unapproved_or_budget_exhausted_mutations_and_missing_inputs():
    rows = [row for row in synthetic_rows() if row.eligible_for_fit]
    builder = RecommendationPacketBuilder(RUNBOOK_CATALOGUE, hybrid_model(), k=5)
    builder.recommender.fit(rows)
    unapproved = builder.recommend_packet(synthetic_context(approval=False))
    assert all(not candidate["mutating"] for candidate in unapproved["candidates"])
    exhausted_context = ContextFeatures(
        **{
            **synthetic_context().__dict__,
            "mutation_budget_remaining": 0,
        }
    )
    exhausted = builder.recommend_packet(exhausted_context)
    assert all(not candidate["mutating"] for candidate in exhausted["candidates"])
    approved = builder.recommend_packet(
        synthetic_context(),
        template_values={"chaos_resource_name": "synthetic-stress"},
    )
    assert approved["candidate_pool_size"] > 0
    assert len(approved["candidates"]) <= 5
    assert all(candidate["approval_required_before_execution"] == candidate["mutating"] for candidate in approved["candidates"])


def test_disabled_toggle_and_feedback_contract_are_explicit():
    rows = [row for row in synthetic_rows() if row.eligible_for_fit]
    builder = RecommendationPacketBuilder(RUNBOOK_CATALOGUE, hybrid_model(), k=3)
    builder.recommender.fit(rows)
    packet = builder.recommend_packet(synthetic_context(), toggle=RecommendationToggle(False, "ablation_baseline"))
    assert packet["candidates"] == []
    assert packet["toggle"]["reason"] == "ablation_baseline"
    enabled_packet = builder.recommend_packet(synthetic_context())
    top_action = enabled_packet["candidates"][0]["action_id"]
    row = builder.feedback_row(
        enabled_packet,
        top_action,
        selected=True,
        relevance=1.0,
        outcome="success",
        split="calibration",
        recorded_at_unix=999.0,
    )
    assert row.incident_key == "synthetic/inc-001"
    with pytest.raises(SchemaError):
        builder.feedback_row(
            enabled_packet,
            top_action,
            selected=True,
            relevance=1.0,
            outcome="not_selected",
            split="future_final_test",
        )


def test_packet_fails_closed_for_missing_action_and_prerequisite():
    rows = [row for row in synthetic_rows() if row.eligible_for_fit]
    reduced_tools = ROLE_ALLOWED_TOOLS["remediation"] - {"alertmanager_silence"}
    with pytest.raises(SchemaError):
        RecommendationPacketBuilder(
            RUNBOOK_CATALOGUE,
            hybrid_model(),
            available_tools=frozenset(reduced_tools),
        )
    builder = RecommendationPacketBuilder(RUNBOOK_CATALOGUE, hybrid_model(), k=5)
    builder.recommender.fit(rows)
    without_name = builder.recommend_packet(synthetic_context())
    with_name = builder.recommend_packet(
        synthetic_context(),
        template_values={"chaos_resource_name": "synthetic-stress"},
    )
    assert with_name["candidate_pool_size"] > without_name["candidate_pool_size"]


def test_rs_package_has_no_direct_tool_execution_path():
    source_files = list(RS_DIR.glob("*.py"))
    assert len(source_files) >= 6
    for path in source_files:
        text = path.read_text(encoding="utf-8")
        assert "from agents.tools" not in text
        assert "TOOL_REGISTRY" not in text
        assert ".execute(" not in text
    module_text = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    for tool in {item.tool_name for item in RUNBOOK_CATALOGUE}:
        assert f"{tool}(" not in module_text
