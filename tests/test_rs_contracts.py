from __future__ import annotations

import copy
import math
import ast
from dataclasses import replace
from pathlib import Path

import pytest

from agents.rs import (
    RUNBOOK_CATALOGUE,
    G5SplitBinding,
    bind_rows_to_g5,
    build_corpus_manifest,
    build_synthetic_fixture,
    deserialize_hybrid_model,
    evaluate_prerequisites,
    mutating_action_exposure_at_k,
    serialize_hybrid_model,
    unsafe_recommendation_rate,
)
from agents.rs.features import build_content_query, recommendation_input_hash
from agents.rs.integration import RecommendationPacketBuilder
from agents.rs.persistence import interaction_corpus_fingerprint
from agents.rs.recommender import HybridRecommender, CollaborativeSVDBaseline, ContentBasedBaseline, PopularitySuccessBaseline, rank_candidates
from agents.rs.schemas import ContextFeatures, InteractionRow, SchemaError, validate_interactions
from agents.tool_policy import CLUSTER_MUTATING_TOOLS
from agents.tool_policy import ROLE_ALLOWED_TOOLS


FAKE_GIT_SHA = "0123456789abcdef0123456789abcdef01234567"
FIXTURE_G5_HASH = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"


def fitted_model():
    fixture = build_synthetic_fixture()
    model = HybridRecommender(
        content_model=ContentBasedBaseline(),
        collaborative_model=CollaborativeSVDBaseline(latent_dimensions=2),
        success_model=PopularitySuccessBaseline(),
    )
    model.fit([row for row in fixture["rows"] if row.eligible_for_fit])
    return model, fixture


def test_synthetic_fixture_is_marked_temporal_and_family_safe():
    fixture = build_synthetic_fixture()
    rows = fixture["rows"]
    assert fixture["marker"] == "deterministic-synthetic-fixture-v1"
    assert all(row.observation_type == "synthetic_label" for row in rows)
    assert all(row.source_run == fixture["marker"] for row in rows)
    assert max(row.recorded_at_unix for row in rows if row.split == "train") < min(
        row.recorded_at_unix for row in rows if row.split == "calibration"
    )
    assert max(row.recorded_at_unix for row in rows if row.split == "calibration") < min(
        row.recorded_at_unix for row in rows if row.split == "test"
    )
    by_family = {}
    for row in rows:
        by_family.setdefault(row.family_id, set()).add(row.split)
    assert all(len(splits) == 1 for splits in by_family.values())


def test_synthetic_content_preferences_are_learnable_and_deterministic():
    model, fixture = fitted_model()
    candidates = RUNBOOK_CATALOGUE
    cpu_scores = model.content_model.score(
        build_content_query(fixture["contexts"]["cpu"]), candidates
    )
    dns_scores = model.content_model.score(
        build_content_query(fixture["contexts"]["dns"]), candidates
    )
    expected_cpu = max(cpu_scores, key=cpu_scores.get)
    expected_dns = max(dns_scores, key=dns_scores.get)
    assert cpu_scores[expected_cpu] > cpu_scores["stop_dns_chaos"]
    assert dns_scores[expected_dns] > dns_scores["scale_up_cpu_saturation"]
    assert build_content_query(fixture["contexts"]["cpu"]) == build_content_query(fixture["contexts"]["cpu"])


def test_synthetic_contradictory_evidence_is_preserved_without_resolution_bias():
    fixture = build_synthetic_fixture()
    baseline = PopularitySuccessBaseline(min_actions_for_rate=1)
    baseline.fit([row for row in fixture["rows"] if row.split == "train"])
    scale_count, scale_average = baseline._stats["scale_up_cpu_saturation"]
    rollback_count, rollback_average = baseline._stats["rollout_undo_error_rate_regression"]
    assert (scale_count, rollback_count) == (4, 3)
    assert math.isclose(scale_average, (0.90 + 0.85 + 0.90 + 0.20) / 4.0)
    assert math.isclose(rollback_average, (0.70 + 0.10 + 0.85) / 3.0)
    assert abs(scale_average - rollback_average) < 0.25


def test_model_serialization_roundtrip_is_deterministic_and_fail_closed():
    model, fixture = fitted_model()
    envelope = serialize_hybrid_model(
        model,
        RUNBOOK_CATALOGUE,
        code_git_sha=FAKE_GIT_SHA,
        fitted_at_unix=1234.5,
    )
    restored = deserialize_hybrid_model(envelope, RUNBOOK_CATALOGUE)
    query = build_content_query(fixture["contexts"]["cpu"])
    assert model.score(query, RUNBOOK_CATALOGUE) == restored.score(query, RUNBOOK_CATALOGUE)
    tampered = copy.deepcopy(envelope)
    tampered["payload"]["seed"] += 1
    with pytest.raises(SchemaError, match="integrity"):
        deserialize_hybrid_model(tampered, RUNBOOK_CATALOGUE)
    drifted_catalogue = [replace(RUNBOOK_CATALOGUE[0], version=2), *RUNBOOK_CATALOGUE[1:]]
    with pytest.raises(SchemaError, match="catalogue hash"):
        deserialize_hybrid_model(envelope, drifted_catalogue)


def test_corpus_manifest_reports_pending_g5_and_exact_hashes():
    fixture = build_synthetic_fixture()
    rows = fixture["rows"]
    manifest = build_corpus_manifest(rows, synthetic=True)
    assert manifest.synthetic is True
    assert manifest.g5_binding_status == "pending_g5"
    assert manifest.g5_split_hash is None
    assert manifest.extraction_version == "not_extracted_until_g5_authorizes"
    assert manifest.row_count == len(rows)
    assert manifest.corpus_hash == interaction_corpus_fingerprint(rows)
    with pytest.raises(SchemaError):
        build_corpus_manifest(rows, synthetic=True, g5_split_hash="not-a-hash")


def test_g5_binding_adapter_fails_closed_and_binds_canonical_metadata():
    fixture = build_synthetic_fixture()
    rows = fixture["rows"]
    incidents = {row.incident_key: row.split for row in rows}
    families = {
        row.incident_key: row.family_id
        for row in rows
    }
    binding = G5SplitBinding(
        split_hash=FIXTURE_G5_HASH,
        incident_splits=incidents,
        family_by_incident=families,
        extraction_version="g5-test-adapter-v1",
    )
    bound = bind_rows_to_g5(rows, binding)
    assert all(row.family_id for row in bound)
    manifest = build_corpus_manifest(bound, g5_split_hash=binding.split_hash)
    assert manifest.g5_binding_status == "bound_to_g5"
    missing_binding = G5SplitBinding(
        split_hash=FIXTURE_G5_HASH,
        incident_splits={key: value for key, value in incidents.items() if key != rows[0].incident_key},
        family_by_incident=families,
        extraction_version="g5-test-adapter-v1",
    )
    with pytest.raises(SchemaError, match="missing from canonical G5"):
        bind_rows_to_g5(rows, missing_binding)


def test_packet_safety_metrics_and_exposure_bounds():
    builder = RecommendationPacketBuilder(RUNBOOK_CATALOGUE, HybridRecommender(
        ContentBasedBaseline(), CollaborativeSVDBaseline(), PopularitySuccessBaseline()
    ), k=5)
    builder.recommender.fit([])
    packet = builder.recommend_packet(build_synthetic_fixture()["contexts"]["dns"])
    exposure = mutating_action_exposure_at_k([packet], 5)
    assert 0.0 <= exposure <= 1.0
    assert unsafe_recommendation_rate([packet]) == 0.0


def test_adversarial_numeric_split_and_rank_inputs_fail_or_degrade_safely():
    with pytest.raises(SchemaError):
        ContextFeatures(
            incident_key="synthetic/nan",
            service="service",
            namespace="namespace",
            fault_types=("unknown",),
            symptoms=("signal",),
            severity="P3",
            diagnosis_text="synthetic",
            deployment_recently_changed=False,
            active_chaos_experiment=False,
            mutation_budget_remaining=1,
            numeric_features={"value": math.inf},
        )
    base = InteractionRow(
        incident_key="synthetic/family-one",
        action_id="scale_up_cpu_saturation",
        service="service",
        fault_types=("cpu_saturation",),
        outcome="success",
        relevance=1.0,
        selected=True,
        split="train",
        eligible_for_fit=True,
        family_id="shared-family",
    )
    cross_family = replace(base, incident_key="synthetic/family-two", split="test", eligible_for_fit=False)
    with pytest.raises(SchemaError, match="family crosses splits"):
        validate_interactions([base, cross_family])
    scores = {"b": 1.0, "a": 1.0}
    assert rank_candidates(scores, 99) == [("a", 1.0), ("b", 1.0)]


def test_builder_rejects_unregistered_tools_and_handles_reduced_availability():
    with pytest.raises(SchemaError, match="unregistered tools"):
        RecommendationPacketBuilder(
            RUNBOOK_CATALOGUE,
            HybridRecommender(ContentBasedBaseline(), CollaborativeSVDBaseline(), PopularitySuccessBaseline()),
            available_tools=frozenset({"unregistered_tool_surface"}),
        )
    # Reduced availability is accepted at init and excludes those tools at recommendation time
    reduced_builder = RecommendationPacketBuilder(
        RUNBOOK_CATALOGUE,
        HybridRecommender(ContentBasedBaseline(), CollaborativeSVDBaseline(), PopularitySuccessBaseline()),
        available_tools=frozenset(ROLE_ALLOWED_TOOLS["remediation"] - {"kubectl_scale"}),
    )
    assert "kubectl_scale" not in reduced_builder.available_tools


def test_evaluate_prerequisites_contract_and_deterministic_states():
    scale = next(item for item in RUNBOOK_CATALOGUE if item.action_id == "scale_up_cpu_saturation")
    chaos = next(item for item in RUNBOOK_CATALOGUE if item.action_id == "stop_stress_chaos")
    argo = next(item for item in RUNBOOK_CATALOGUE if item.action_id == "argocd_rollback_bad_manifest")
    silence = next(item for item in RUNBOOK_CATALOGUE if item.action_id == "silence_flapping_alert_during_mitigation")

    context = ContextFeatures(
        incident_key="synthetic/eval-prereqs",
        service="paymentservice",
        namespace="default",
        fault_types=("cpu_saturation",),
        symptoms=("cpu",),
        severity="P1",
        diagnosis_text="CPU saturation.",
        deployment_recently_changed=False,
        active_chaos_experiment=False,
        mutation_budget_remaining=3,
        revision_history_available=None,
        mitigation_in_progress=None,
    )

    # Scale has default target_replicas=4, service and namespace from context => all satisfied
    scale_states = evaluate_prerequisites(scale, context)
    assert scale_states == {"service": "satisfied", "namespace": "satisfied", "target_replicas": "satisfied"}

    # Chaos has missing chaos_resource_name (unknown) and active_chaos_experiment=False (unmet)
    chaos_states = evaluate_prerequisites(chaos, context)
    assert chaos_states["active_chaos_experiment"] == "unmet"
    assert chaos_states["chaos_resource_name"] == "unknown"

    # Argo has missing app/manifest (unknown) and revision_history_available=None (unknown)
    argo_states = evaluate_prerequisites(argo, context)
    assert argo_states["revision_history_available"] == "unknown"
    assert argo_states["argocd_app"] == "unknown"
    assert argo_states["bad_manifest"] == "unknown"

    # Silence has duration_minutes (default 30 => satisfied), alertname (unknown), mitigation_in_progress (unknown)
    silence_states = evaluate_prerequisites(silence, context)
    assert silence_states["duration_minutes"] == "satisfied"
    assert silence_states["alertname"] == "unknown"
    assert silence_states["mitigation_in_progress"] == "unknown"


def test_recommendation_input_hash_contract_and_epistemic_safety():
    ctx_none = ContextFeatures(
        incident_key="synthetic/contract-hash",
        service="paymentservice",
        namespace="default",
        fault_types=("cpu_saturation",),
        symptoms=("cpu",),
        severity="P1",
        diagnosis_text="CPU saturation.",
        deployment_recently_changed=None,
        active_chaos_experiment=None,
        mutation_budget_remaining=3,
        approval_granted=False,
    )
    ctx_false = replace(ctx_none, active_chaos_experiment=False)
    ctx_true = replace(ctx_none, active_chaos_experiment=True)

    h_none = recommendation_input_hash(ctx_none)
    h_false = recommendation_input_hash(ctx_false)
    h_true = recommendation_input_hash(ctx_true)

    # Distinct epistemic states must produce distinct fingerprints
    assert h_none != h_false
    assert h_none != h_true
    assert h_false != h_true

    # Hashes are deterministic and 64 hex characters
    assert len(h_none) == 64
    assert int(h_none, 16) > 0
    assert recommendation_input_hash(ctx_none) == h_none


def test_side_effect_classification_and_unseen_action_cold_start():
    for runbook in RUNBOOK_CATALOGUE:
        expected_mutation = (
            runbook.tool_name in CLUSTER_MUTATING_TOOLS
            or runbook.tool_name == "slack_post_update"
        )
        assert runbook.mutating is expected_mutation, runbook.action_id
        if runbook.stage != "remediation":
            assert not runbook.mutating
    model, _fixture = fitted_model()
    trained_actions = set(model.collaborative_model._action_index)
    unseen = next(item for item in RUNBOOK_CATALOGUE if item.action_id not in trained_actions)
    assert model.collaborative_model.score({}, [unseen]) == {unseen.action_id: 0.0}


def test_empty_ranking_and_malformed_relevance_are_handled_explicitly():
    assert rank_candidates({}, 3) == []
    with pytest.raises(SchemaError, match="relevance"):
        InteractionRow(
            incident_key="synthetic/bad-relevance",
            action_id="scale_up_cpu_saturation",
            service="service",
            fault_types=("cpu_saturation",),
            outcome="success",
            relevance=math.nan,
            selected=True,
            split="train",
            eligible_for_fit=True,
        )


def test_rs_package_ast_has_no_runtime_execution_or_network_surface():
    forbidden_modules = {"agents.tools", "subprocess", "socket", "requests", "httpx"}
    forbidden_calls = {"open", "eval", "exec", "system", "popen"}
    for path in (Path("agents/rs")).glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name not in forbidden_modules
                    and not any(alias.name.startswith(module + ".") for module in forbidden_modules)
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module not in forbidden_modules
                assert not any(module.startswith(item + ".") for item in forbidden_modules)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls


def test_every_remediation_tool_has_at_least_one_runbook():
    """A tool the agent may call but the recommender cannot rank is invisible to RS.

    The chaos stop runbooks all require `chaos_resource_name`, which stayed
    permanently unknown until a discovery wrapper existed to supply it — so
    every chaos remediation was ranked as blocked.
    """
    from agents.rs.catalogue import RUNBOOK_CATALOGUE
    from agents.tool_policy import ROLE_ALLOWED_TOOLS

    covered = {runbook.tool_name for runbook in RUNBOOK_CATALOGUE}
    assert ROLE_ALLOWED_TOOLS["remediation"] <= covered


def test_chaos_resource_name_has_a_discovery_runbook():
    """The prerequisite the stop runbooks depend on must be obtainable."""
    from agents.rs.catalogue import RUNBOOK_CATALOGUE

    stop_runbooks = [r for r in RUNBOOK_CATALOGUE if r.tool_name == "chaos_stop_experiment"]
    assert stop_runbooks
    assert all("chaos_resource_name" in r.prerequisites for r in stop_runbooks)

    discovery = [r for r in RUNBOOK_CATALOGUE if r.tool_name == "chaos_list_experiments"]
    assert len(discovery) == 1
    assert discovery[0].mutating is False
    assert discovery[0].stage == "diagnostic"
    # Discovery must itself be unblocked, or it cannot break the deadlock.
    assert discovery[0].prerequisites == ()
