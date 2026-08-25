from __future__ import annotations

import copy
import math
from dataclasses import replace

import pytest

from agents.rs import (
    RUNBOOK_CATALOGUE,
    G5SplitBinding,
    bind_rows_to_g5,
    build_corpus_manifest,
    build_synthetic_fixture,
    deserialize_hybrid_model,
    mutating_action_exposure_at_k,
    serialize_hybrid_model,
    unsafe_recommendation_rate,
)
from agents.rs.features import build_content_query
from agents.rs.integration import RecommendationPacketBuilder
from agents.rs.persistence import interaction_corpus_fingerprint
from agents.rs.recommender import HybridRecommender, CollaborativeSVDBaseline, ContentBasedBaseline, PopularitySuccessBaseline, rank_candidates
from agents.rs.schemas import ContextFeatures, InteractionRow, SchemaError, validate_interactions
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


def test_catalogue_accepts_only_complete_current_remediation_acl():
    with pytest.raises(SchemaError):
        RecommendationPacketBuilder(
            RUNBOOK_CATALOGUE,
            HybridRecommender(ContentBasedBaseline(), CollaborativeSVDBaseline(), PopularitySuccessBaseline()),
            available_tools=frozenset(ROLE_ALLOWED_TOOLS["remediation"] - {"kubectl_scale"}),
        )
