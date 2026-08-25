import copy

import pytest

from bench import scenario_contract as contract
from bench import unseen_candidate as candidate_contract


def _candidate():
    return {
        "description": "Private synthetic admission fixture",
        "exposure_attestation": {
            "attested_by": "operator",
            "method": "private generator outside model context",
            "status": "UNEXPOSED_ATTESTED",
        },
        "manifest_documents": [
            {
                "apiVersion": "chaos-mesh.org/v1alpha1",
                "kind": "PodChaos",
                "metadata": {
                    "labels": {"scenario": "holdout-x", "tier": "single_fault"},
                    "name": "holdout-x-private",
                    "namespace": "chaos-mesh",
                },
                "spec": {
                    "action": "pod-kill",
                    "duration": "5m",
                    "mode": "one",
                    "selector": {
                        "labelSelectors": {"app": "unpublished-service"},
                        "namespaces": ["default"],
                    },
                },
            }
        ],
        "model_visible_alert": {
            "alerts": [
                {"labels": {"alertname": "PodNotReady", "service": "unpublished-service"}}
            ],
            "commonLabels": {
                "alertname": "PodNotReady",
                "namespace": "default",
                "severity": "warning",
            },
        },
        "scenario_id": "single_fault/holdout-x",
        "schema_version": candidate_contract.CANDIDATE_SCHEMA_VERSION,
        "source_lineage": {
            "commit_sha": "0123456789abcdef0123456789abcdef01234567",
            "created_at": "2030-01-01T00:00:00Z",
            "generator_name": "private-holdout-generator",
            "generator_version": "1.0.0",
            "path": "private/holdout-x.yaml",
            "repository_url": "https://example.invalid/private.git",
        },
        "success_predicates": {
            "require_chaos_cleared": True,
            "scenario_id": "single_fault/holdout-x",
            "workloads": [
                {"kind": "deployment", "name": "unpublished-service", "namespace": "default"}
            ],
        },
        "tier": "single_fault",
        "variant_parent_id": None,
    }


def test_admission_recomputes_lineage_and_family_evidence():
    catalog = contract.build_catalog()
    ledger = contract.load_exposure_ledger()
    admitted = candidate_contract.admit_unseen_candidate(
        _candidate(),
        catalog=catalog,
        exposure_ledger=ledger,
    )

    assert admitted["admission_status"] == "ADMITTED_REVIEW_REQUIRED"
    assert admitted["model_visibility_review"]["operator_only"] is True
    assert admitted["computed"]["fault_signatures"]
    assert admitted["admission_sha256"]


def test_admission_rejects_hidden_answer_sentinel_in_alert():
    candidate = _candidate()
    candidate["model_visible_alert"]["root_cause"] = "unpublished service killed"
    with pytest.raises(ValueError, match="hidden-truth sentinel"):
        candidate_contract.admit_unseen_candidate(
            candidate,
            catalog=contract.build_catalog(),
            exposure_ledger=contract.load_exposure_ledger(),
        )


def test_admission_rejects_hidden_identity_prose_in_alert():
    candidate = _candidate()
    candidate["model_visible_alert"]["commonAnnotations"] = {
        "summary": "Investigate holdout-x-private"
    }
    with pytest.raises(ValueError, match="hidden identity prose"):
        candidate_contract.admit_unseen_candidate(
            candidate,
            catalog=contract.build_catalog(),
            exposure_ledger=contract.load_exposure_ledger(),
        )


def test_admission_rejects_manifest_labels_disagreeing_with_identity():
    candidate = _candidate()
    candidate["manifest_documents"][0]["metadata"]["labels"]["scenario"] = "other"
    with pytest.raises(ValueError, match="labels disagree"):
        candidate_contract.admit_unseen_candidate(
            candidate,
            catalog=contract.build_catalog(),
            exposure_ledger=contract.load_exposure_ledger(),
        )


def test_admission_rejects_development_exposed_candidate(monkeypatch):
    monkeypatch.setattr(
        candidate_contract,
        "development_exposed_ids",
        lambda _catalog, _ledger: {"single_fault/holdout-x"},
    )
    with pytest.raises(ValueError, match="development-exposed"):
        candidate_contract.admit_unseen_candidate(
            _candidate(),
            catalog=contract.build_catalog(),
            exposure_ledger=contract.load_exposure_ledger(),
        )


def test_admission_rejects_paraphrased_existing_alert():
    catalog = contract.build_catalog()
    candidate = _candidate()
    candidate["model_visible_alert"] = copy.deepcopy(
        catalog["entries"][0]["model_visible_alert"]
    )
    del candidate["model_visible_alert"]["commonLabels"]["namespace"]
    with pytest.raises(ValueError, match="alert paraphrase leakage"):
        candidate_contract.admit_unseen_candidate(
            candidate,
            catalog=catalog,
            exposure_ledger=contract.load_exposure_ledger(),
        )


def test_alert_similarity_detects_rewrites_but_not_distinct_incidents():
    catalog = contract.build_catalog()
    alert = copy.deepcopy(catalog["entries"][0]["model_visible_alert"])
    assert contract.alert_similarity(alert, alert) == 1.0
    assert contract.alert_similarity(
        alert,
        catalog["entries"][1]["model_visible_alert"],
    ) < 0.85


def test_admission_rejects_existing_family_signature():
    candidate = _candidate()
    catalog = contract.build_catalog()
    admitted = candidate_contract.admit_unseen_candidate(
        candidate,
        catalog=catalog,
        exposure_ledger=contract.load_exposure_ledger(),
    )
    related_catalog = copy.deepcopy(catalog)
    related_catalog["entries"][0]["fault_signatures"] = admitted["computed"]["fault_signatures"]
    with pytest.raises(ValueError, match="family leakage"):
        candidate_contract.admit_unseen_candidate(
            candidate,
            catalog=related_catalog,
            exposure_ledger=contract.load_exposure_ledger(),
        )
