from __future__ import annotations

import math
from pathlib import Path

import pytest

from agents.rs import RUNBOOK_CATALOGUE
from agents.rs.catalogue import validate_catalogue as validate_catalogue_module
from agents.rs.features import (
    build_content_query,
    content_vector,
    context_from_diagnosis,
    cosine_similarity,
)
from agents.rs.integration import RecommendationPacketBuilder, RecommendationToggle
from agents.rs.metrics import (
    coverage_at_k,
    hit_rate_at_k,
    mrr_at_k,
    ndcg_at_k,
)
from agents.rs.ontology import (
    derive_parameter_requirements,
    service_matches_constraints,
    validate_parameter_contract,
)
from agents.rs.recommender import (
    CollaborativeSVDBaseline,
    ContentBasedBaseline,
    HybridRecommender,
    PopularitySuccessBaseline,
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
    side_effecting_tools = CLUSTER_MUTATING_TOOLS | {"slack_post_update"}
    mutating = {item.tool_name for item in RUNBOOK_CATALOGUE if item.mutating}
    assert mutating == side_effecting_tools


def test_catalogue_templates_match_strict_coordinator_tool_schemas():
    from agents.coordinator import _TOOL_PARAMETER_SCHEMAS

    json_type_for_literal = {
        dict: "object",
        list: "array",
        bool: "boolean",
        int: "integer",
        float: "number",
        str: "string",
    }
    for runbook in RUNBOOK_CATALOGUE:
        schema = _TOOL_PARAMETER_SCHEMAS[runbook.tool_name]
        properties = schema["properties"]
        assert set(runbook.parameter_template) <= set(properties), runbook.action_id
        for key, value in runbook.parameter_template.items():
            expected_type = properties[key]["type"]
            if isinstance(value, str) and value.startswith("{{"):
                actual_type = "integer" if "|int" in value else "string"
            else:
                actual_type = next(
                    json_type for python_type, json_type in json_type_for_literal.items()
                    if isinstance(value, python_type)
                )
            assert actual_type == expected_type, (runbook.action_id, key)
        assert set(schema.get("required", ())) <= set(runbook.parameter_template)


def test_parameter_contracts_are_typed_defaults_and_reject_unknown_values():
    scale = next(item for item in RUNBOOK_CATALOGUE if item.action_id == "scale_up_cpu_saturation")
    requirements = derive_parameter_requirements(scale)
    by_name = {item.name: item for item in requirements}
    assert by_name["target_replicas"].parameter_type == "integer"
    assert by_name["target_replicas"].default == 4
    assert by_name["target_replicas"].required is False
    assert by_name["namespace"].required is True
    normalized = validate_parameter_contract(
        scale,
        {"service": "synthetic-service", "namespace": "synthetic-ns", "target_replicas": 5},
    )
    assert normalized["target_replicas"] == 5
    with pytest.raises(SchemaError, match="unknown parameters"):
        validate_parameter_contract(scale, {"arbitrary_shell": "forbidden-value"})
    with pytest.raises(SchemaError, match="must be integer"):
        validate_parameter_contract(scale, {"target_replicas": True})
    assert service_matches_constraints("paymentservice", ("*",))
    assert not service_matches_constraints("paymentservice", ())
    argo = next(item for item in RUNBOOK_CATALOGUE if item.action_id == "argocd_rollback_bad_manifest")
    argo_requirements = {
        item.name: item for item in derive_parameter_requirements(argo)
    }
    typed_revision_name = next(
        item.removesuffix(":int") for item in argo.prerequisites if item.endswith(":int")
    )
    assert argo_requirements[typed_revision_name].parameter_type == "integer"
    assert argo_requirements[typed_revision_name].source == "gate"


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
        "incident_id": "synthetic/profile",
        "root_cause": {
            "category": "resource",
            "specific": "CPU stress caused paymentservice saturation.",
            "evidence": [
                {"tool": "promql_query", "finding": "CPU above 90%"},
                {
                    "tool": "kubectl_get",
                    "resource": "stresschaos",
                    "finding": "active StressChaos",
                },
            ],
        },
        "recommended_actions": [
            {
                "action": "stop_chaos",
                "kind": "StressChaos",
                "name": "paymentservice-cpu",
                "namespace": "chaos-mesh",
            }
        ],
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
    assert "stresschaos" in context.diagnosis_text
    assert "paymentservice-cpu" in context.diagnosis_text
    assert context.active_chaos_experiment is True
    assert context.approval_granted is False


@pytest.mark.parametrize(
    ("removed_key", "message"),
    [
        ("category", "diagnosis.root_cause.category"),
        ("specific", "diagnosis.root_cause.specific"),
        ("evidence", "diagnosis.root_cause.evidence"),
    ],
)
def test_context_adapter_fails_closed_on_missing_canonical_diagnosis_fields(
    removed_key,
    message,
):
    diagnosis = {
        "root_cause": {
            "category": "resource",
            "specific": "CPU stress caused saturation.",
            "evidence": [{"tool": "promql_query", "finding": "CPU above 90%"}],
        },
        "recommended_actions": [{"action": "scale_up", "target": "paymentservice"}],
    }
    del diagnosis["root_cause"][removed_key]
    with pytest.raises(SchemaError, match=message):
        context_from_diagnosis(
            incident_key="synthetic/malformed",
            triage={"affected_services": ["paymentservice"]},
            diagnosis=diagnosis,
        )


def test_context_adapter_supports_flat_coordinator_compatibility_contract():
    context = context_from_diagnosis(
        incident_key="synthetic/flat-profile",
        triage={
            "severity": "P1",
            "affected_services": ["paymentservice"],
            "labels": {"namespace": "default"},
        },
        diagnosis={
            "root_cause": "CPU stress caused paymentservice saturation.",
            "confidence": 0.82,
            "evidence": [
                {"tool": "promql_query", "finding": "CPU above 90%"},
            ],
            "recommended_fix": "scale paymentservice after confirming capacity headroom",
        },
        mutation_budget_remaining=2,
    )
    assert context.service == "paymentservice"
    assert "cpu_saturation" in context.fault_types
    assert "promql_query" in context.diagnosis_text
    assert context.approval_granted is False


def test_context_adapter_rejects_dict_stringification_and_incomplete_legacy():
    with pytest.raises(SchemaError, match="category"):
        context_from_diagnosis(
            incident_key="synthetic/incomplete-nested",
            triage={"affected_services": ["paymentservice"]},
            diagnosis={"root_cause": {"specific": "CPU stress"}, "recommended_actions": []},
        )
    with pytest.raises(SchemaError, match="legacy diagnosis"):
        context_from_diagnosis(
            incident_key="synthetic/incomplete-flat",
            triage={"affected_services": ["paymentservice"]},
            diagnosis={"root_cause": "CPU stress", "evidence": []},
        )


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


def test_svd_implementation_recovers_hand_verifiable_rank_one_matrix():
    model = CollaborativeSVDBaseline(latent_dimensions=1, iterations=120)
    matrix_values = [
        ("synthetic/svd-1", "scale_up_cpu_saturation", 0.2),
        ("synthetic/svd-1", "stop_dns_chaos", 0.4),
        ("synthetic/svd-2", "scale_up_cpu_saturation", 0.4),
        ("synthetic/svd-2", "stop_dns_chaos", 0.8),
    ]
    rows = []
    for rank, (incident, action_id, relevance) in enumerate(matrix_values, start=1):
        rows.append(InteractionRow(
            incident_key=incident,
            action_id=action_id,
            service="synthetic-service",
            fault_types=("cpu_saturation",),
            outcome="success" if relevance >= 0.5 else "partial",
            relevance=relevance,
            selected=True,
            split="train",
            eligible_for_fit=True,
            observation_type="selected_success" if relevance >= 0.5 else "selected_partial",
            rank=rank,
            family_id="synthetic-svd-family",
        ))
    model.fit(rows)
    assert math.isclose(model.singular_values[0], 1.0, abs_tol=1e-9)
    assert model.reconstruction_error() < 1e-8
    factor = [model._action_factors[0][idx] for idx in range(2)]
    denominator = math.sqrt(5.0)
    expected = [1.0 / denominator, 2.0 / denominator]
    if factor[0] < 0:
        expected = [-value for value in expected]
    assert math.isclose(factor[0], expected[0], abs_tol=1e-8)
    assert math.isclose(factor[1], expected[1], abs_tol=1e-8)
    candidates = [
        next(item for item in RUNBOOK_CATALOGUE if item.action_id == "scale_up_cpu_saturation"),
        next(item for item in RUNBOOK_CATALOGUE if item.action_id == "stop_dns_chaos"),
    ]
    first_scores = model.score({}, candidates)
    assert first_scores == model.score({}, candidates)


def test_empty_legal_history_has_deterministic_conservative_cold_start():
    builder = RecommendationPacketBuilder(RUNBOOK_CATALOGUE, hybrid_model(), k=5)
    with pytest.raises(SchemaError, match="recommend before calling fit"):
        builder.recommend_packet(synthetic_context())
    builder.recommender.fit([])
    packet = builder.recommend_packet(synthetic_context(approval=False))
    assert packet["confidence_state"] == "uncalibrated"
    assert all(
        candidate["confidence_state"] == "insufficient_history"
        for candidate in packet["candidates"]
    )
    assert all(candidate["score"] == candidate["score"] for candidate in packet["candidates"])
    repeat = builder.recommend_packet(synthetic_context(approval=False))
    assert packet == repeat
    mutating_positions = [
        index for index, candidate in enumerate(packet["candidates"])
        if candidate["mutating"]
    ]
    low_risk_positions = [
        index for index, candidate in enumerate(packet["candidates"])
        if candidate["risk"] == "low"
    ]
    if mutating_positions:
        assert max(mutating_positions) > min(low_risk_positions)


def test_unobserved_recommendations_do_not_create_negative_success_labels():
    action_id = "rollout_undo_error_rate_regression"
    observed = InteractionRow(
        incident_key="synthetic/observed",
        action_id=action_id,
        service="synthetic-service",
        fault_types=("error_rate",),
        outcome="success",
        relevance=1.0,
        selected=True,
        split="train",
        eligible_for_fit=True,
        observation_type="selected_success",
    )
    unobserved = InteractionRow(
        incident_key="synthetic/unobserved",
        action_id=action_id,
        service="synthetic-service",
        fault_types=("error_rate",),
        outcome="not_selected",
        relevance=0.0,
        selected=False,
        split="calibration",
        eligible_for_fit=True,
        observation_type="not_selected",
    )
    baseline = PopularitySuccessBaseline()
    baseline.fit([observed])
    observed_only_score = baseline.score({}, RUNBOOK_CATALOGUE)[action_id]
    baseline.fit([observed, unobserved])
    assert baseline.score({}, RUNBOOK_CATALOGUE)[action_id] == observed_only_score


def test_baselines_emit_distinct_deterministic_signals():
    rows = [row for row in synthetic_rows() if row.eligible_for_fit]
    model = hybrid_model()
    model.fit(rows)
    query = build_content_query(synthetic_context())
    candidates = RUNBOOK_CATALOGUE
    component_sets = [
        model.content_model.score(query, candidates),
        model.collaborative_model.score(query, candidates),
        model.success_model.score(query, candidates),
    ]
    for scores in component_sets:
        assert len(set(scores.values())) > 1
    assert component_sets[0] != component_sets[1]
    assert component_sets[0] != component_sets[2]
    assert component_sets[1] != component_sets[2]

    repeat_content = model.content_model.score(query, candidates)
    repeat_collaborative = model.collaborative_model.score(query, candidates)
    repeat_success = model.success_model.score(query, candidates)
    assert repeat_content == component_sets[0]
    assert repeat_collaborative == component_sets[1]
    assert repeat_success == component_sets[2]


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


def test_content_feature_space_hashes_query_and_candidates_identically():
    cpu_context = ContextFeatures(
        incident_key="synthetic/content-cpu",
        service="synthetic-service",
        namespace="synthetic-namespace",
        fault_types=("cpu_saturation",),
        symptoms=("cpu", "saturation"),
        severity="P2",
        diagnosis_text="Sustained CPU saturation on the affected workload.",
        deployment_recently_changed=False,
        active_chaos_experiment=False,
        mutation_budget_remaining=3,
    )
    unrelated_context = ContextFeatures(
        **{
            **cpu_context.__dict__,
            "fault_types": ("dns_failure",),
            "symptoms": ("dns", "resolution"),
            "diagnosis_text": "DNS resolution failures are causing timeouts.",
        }
    )
    cpu_runbook = next(
        item for item in RUNBOOK_CATALOGUE
        if item.action_id == "scale_up_cpu_saturation"
    )
    dns_runbook = next(
        item for item in RUNBOOK_CATALOGUE
        if item.action_id == "stop_dns_chaos"
    )
    cpu_query = build_content_query(cpu_context)
    unrelated_query = build_content_query(unrelated_context)
    cpu_vector = content_vector(cpu_runbook)
    unrelated_vector = content_vector(dns_runbook)

    assert cosine_similarity(cpu_query, cpu_vector) > 0.0
    assert cosine_similarity(unrelated_query, unrelated_vector) > 0.0
    assert len(cpu_query) > 0 and len(cpu_vector) > 0
    assert build_content_query(cpu_context) == cpu_query
    assert content_vector(cpu_runbook) == cpu_vector
    scores = ContentBasedBaseline().score(cpu_query, [cpu_runbook, dns_runbook])
    assert scores["scale_up_cpu_saturation"] > scores[
        "stop_dns_chaos"
    ]
    changed_scores = ContentBasedBaseline().score(
        unrelated_query,
        [cpu_runbook, dns_runbook],
    )
    assert changed_scores != scores
    assert len(set(scores.values())) > 1
    assert all("service" not in vector for vector in (cpu_query, unrelated_query))


def test_preapproval_packet_ranks_mutations_and_preserves_execution_boundary():
    rows = [row for row in synthetic_rows() if row.eligible_for_fit]
    builder = RecommendationPacketBuilder(RUNBOOK_CATALOGUE, hybrid_model(), k=5)
    builder.recommender.fit(rows)
    unapproved = builder.recommend_packet(synthetic_context(approval=False))
    mutating = [candidate for candidate in unapproved["candidates"] if candidate["mutating"]]
    assert mutating
    assert unapproved["next_stage"] == "safety_approval_and_grpo_policy"
    for candidate in mutating:
        assert candidate["approval_required_before_execution"] is True
        assert candidate["downstream_execution_blockers"] == ["approval_pending"]
        assert candidate["execution_eligible_after_downstream_gates"] is False

    exhausted_context = ContextFeatures(
        **{
            **synthetic_context().__dict__,
            "mutation_budget_remaining": 0,
        }
    )
    exhausted = builder.recommend_packet(exhausted_context)
    exhausted_mutating = [
        candidate for candidate in exhausted["candidates"] if candidate["mutating"]
    ]
    assert exhausted_mutating
    for candidate in exhausted_mutating:
        assert set(candidate["downstream_execution_blockers"]) == {
            "approval_pending",
            "mutation_budget_exhausted",
        }
        assert candidate["execution_eligible_after_downstream_gates"] is False

    approved = builder.recommend_packet(
        synthetic_context(),
        template_values={"chaos_resource_name": "synthetic-stress"},
    )
    assert approved["candidate_pool_size"] > 0
    assert len(approved["candidates"]) <= 5
    for candidate in approved["candidates"]:
        assert candidate["approval_required_before_execution"] is candidate["mutating"]
        expected_blockers = ["approval_pending"] if candidate["mutating"] else []
        assert candidate["downstream_execution_blockers"] == expected_blockers
        assert (
            candidate["execution_eligible_after_downstream_gates"]
            is not candidate["mutating"]
        )


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
    assert row.observation_type == "selected_success"
    assert row.context_hash
    rejected_action = enabled_packet["candidates"][-1]["action_id"]
    rejected = builder.feedback_row(
        enabled_packet,
        rejected_action,
        selected=False,
        relevance=0.0,
        outcome="policy_rejected",
        split="train",
        counterfactual_status="unknown_counterfactual",
    )
    assert rejected.observation_type == "policy_rejected"
    with pytest.raises(SchemaError):
        builder.feedback_row(
            enabled_packet,
            rejected_action,
            selected=False,
            relevance=1.0,
            outcome="not_selected",
            split="future_final_test",
        )
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
