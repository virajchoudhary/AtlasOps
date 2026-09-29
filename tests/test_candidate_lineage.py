from __future__ import annotations

import copy
import hashlib
import json

import pytest

from bench.candidate_lineage import validate_candidate_lineage

ARM_ORDER = [
    "Zero-Shot Baseline",
    "SFT Model",
    "SFT + GRPO",
]
SCENARIO_IDS = ["lineage-fixture-001", "lineage-fixture-002"]
SFT_V2_TREE_SHA256 = "2" * 64


def _split_sha256(scenario_ids: list[str]) -> str:
    payload = json.dumps(
        scenario_ids,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_schema() -> dict:
    return {
        "arms": list(ARM_ORDER),
        "scenario_ids": list(SCENARIO_IDS),
        "sft_v2_adapter_tree_sha256": SFT_V2_TREE_SHA256,
        "comparison_scorer": {
            "version": "synthetic-g13-scorer-v1",
            "sha256": "8" * 64,
        },
        "evaluation_contract": {
            "permissions": {"read": True, "mutate": False},
            "inference": {"temperature": 0.0, "seed": 37},
            "tool_budget": {"calls": 4},
            "action_budget": {"actions": 3},
            "observation_budget": {"bytes": 8192},
            "time_budget": {"seconds": 240},
        },
    }


def _run_descriptors() -> list[dict]:
    evaluation = copy.deepcopy(_candidate_schema()["evaluation_contract"])
    base_model = {"revision": "a" * 40, "sha256": "b" * 64}
    tokenizer = {"revision": "c" * 40, "sha256": "d" * 64}
    source = {
        "commit_sha": "e" * 40,
        "tree_sha256": "f" * 64,
        "dirty": False,
    }
    evaluator = {
        "commit_sha": "1" * 40,
        "tree_sha256": "3" * 64,
        "dirty": False,
    }
    common = {
        "base_model": base_model,
        "tokenizer": tokenizer,
        "source": source,
        "evaluator": evaluator,
        "comparison_scorer": copy.deepcopy(_candidate_schema()["comparison_scorer"]),
        "scenario_ids": list(SCENARIO_IDS),
        "split_sha256": _split_sha256(SCENARIO_IDS),
        "evaluation": evaluation,
        "serving_identity": {"declared_sha256": "4" * 64},
    }
    return [
        {
            **copy.deepcopy(common),
            "arm": ARM_ORDER[0],
            "training_budget": None,
            "adapter": None,
        },
        {
            **copy.deepcopy(common),
            "arm": ARM_ORDER[1],
            "training_budget": {"steps": 120},
            "adapter": {"tree_sha256": SFT_V2_TREE_SHA256},
        },
        {
            **copy.deepcopy(common),
            "arm": ARM_ORDER[2],
            "training_budget": {"steps": 85, "epochs": 2},
            "adapter": {
                "tree_sha256": "5" * 64,
                "parent_sft_tree_sha256": SFT_V2_TREE_SHA256,
            },
        },
    ]


def test_valid_candidate_is_non_empirical_and_keeps_training_budgets_separate():
    report = validate_candidate_lineage(_run_descriptors(), _candidate_schema())

    assert report["lineage_status"] == "CONSISTENT"
    assert report["errors"] == []
    assert report["non_empirical"] is True
    assert report["certification_status"] == "NOT_CERTIFIED"
    assert report["scenario_membership"]["scenario_ids"] == SCENARIO_IDS
    assert report["scenario_membership"]["split_sha256"] == _split_sha256(SCENARIO_IDS)
    assert report["scenario_membership"]["by_arm"][ARM_ORDER[1]] == {
        "scenario_ids": SCENARIO_IDS,
        "computed_split_sha256": _split_sha256(SCENARIO_IDS),
        "declared_split_sha256": _split_sha256(SCENARIO_IDS),
    }
    assert report["model_identity_by_arm"][ARM_ORDER[0]] == {
        "base_model": {"revision": "a" * 40, "sha256": "b" * 64},
        "tokenizer": {"revision": "c" * 40, "sha256": "d" * 64},
    }
    assert report["adapter_lineage_by_arm"][ARM_ORDER[1]] == {
        "tree_sha256": SFT_V2_TREE_SHA256,
        "matches_sft_v2": True,
    }
    assert report["adapter_lineage_by_arm"][ARM_ORDER[2]]["parent_matches_sft_v2"] is True
    assert report["comparison_scorer"]["contract"] == {
        "version": "synthetic-g13-scorer-v1",
        "sha256": "8" * 64,
    }
    assert report["comparison_scorer"]["declared_by_arm"][ARM_ORDER[2]] == {
        "version": "synthetic-g13-scorer-v1",
        "sha256": "8" * 64,
    }
    assert report["comparison_scorer"]["execution_status"] == "NOT_ESTABLISHED"
    assert report["training_budgets_by_arm"] == {
        ARM_ORDER[0]: None,
        ARM_ORDER[1]: {"steps": 120},
        ARM_ORDER[2]: {"steps": 85, "epochs": 2},
    }
    assert report["serving_identity"]["declared_by_arm"][ARM_ORDER[0]] == {
        "status": "RECORDED",
        "sha256": "4" * 64,
    }
    assert report["serving_identity"]["independently_observed"] == {
        "status": "NOT_PROVIDED",
        "sha256_by_arm": {},
    }


def test_rejects_missing_extra_duplicate_and_out_of_order_arms():
    missing = _run_descriptors()
    missing.pop()
    missing_report = validate_candidate_lineage(missing, _candidate_schema())
    assert any("missing arms" in error for error in missing_report["errors"])

    extra = _run_descriptors()
    extra.append({**copy.deepcopy(extra[0]), "arm": "unexpected arm"})
    extra_report = validate_candidate_lineage(extra, _candidate_schema())
    assert any("extra arms" in error for error in extra_report["errors"])

    duplicate = _run_descriptors()
    duplicate[1]["arm"] = ARM_ORDER[0]
    duplicate_report = validate_candidate_lineage(duplicate, _candidate_schema())
    assert any("duplicate arm labels" in error for error in duplicate_report["errors"])

    out_of_order = _run_descriptors()
    out_of_order[0], out_of_order[1] = out_of_order[1], out_of_order[0]
    order_report = validate_candidate_lineage(out_of_order, _candidate_schema())
    assert any("candidate_schema.arms order" in error for error in order_report["errors"])


def test_rejects_other_three_arm_categories_from_external_schema():
    schema = _candidate_schema()
    schema["arms"] = ["Zero-Shot Baseline", "SFT + RS", "SFT + GRPO"]
    runs = _run_descriptors()
    runs[1]["arm"] = "SFT + RS"

    report = validate_candidate_lineage(runs, schema)

    assert report["lineage_status"] == "INVALID"
    assert any("candidate_schema.arms must exactly match" in error for error in report["errors"])


def test_rejects_correct_arm_labels_in_wrong_schema_order():
    schema = _candidate_schema()
    schema["arms"] = [ARM_ORDER[1], ARM_ORDER[0], ARM_ORDER[2]]

    report = validate_candidate_lineage(_run_descriptors(), schema)

    assert report["lineage_status"] == "INVALID"
    assert any("candidate_schema.arms must exactly match" in error for error in report["errors"])


@pytest.mark.parametrize(
    "revision", ["main", "latest", "", "   ", True, float("nan"), float("inf")]
)
def test_rejects_mutable_blank_boolean_and_nonfinite_model_revisions(revision):
    runs = _run_descriptors()
    runs[0]["base_model"]["revision"] = revision

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert report["lineage_status"] == "INVALID"
    assert any("base_model.revision must be an immutable" in error for error in report["errors"])


@pytest.mark.parametrize("digest", ["", "not-a-digest", True, float("nan")])
def test_rejects_missing_or_invalid_base_and_tokenizer_digests(digest):
    runs = _run_descriptors()
    runs[1]["base_model"]["sha256"] = digest
    runs[2]["tokenizer"]["sha256"] = digest

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert report["lineage_status"] == "INVALID"
    assert any("base_model.sha256 must be a 64-character" in error for error in report["errors"])
    assert any("tokenizer.sha256 must be a 64-character" in error for error in report["errors"])


def test_rejects_different_base_or_tokenizer_identity_between_arms():
    runs = _run_descriptors()
    runs[2]["tokenizer"]["revision"] = "9" * 40

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert any("tokenizer revision/digest differs" in error for error in report["errors"])


def test_requires_sft_v2_hash_and_grpo_parent_to_match_it():
    runs = _run_descriptors()
    runs[1]["adapter"]["tree_sha256"] = "6" * 64
    runs[2]["adapter"]["parent_sft_tree_sha256"] = "6" * 64

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert any("SFT V2 tree hash" in error for error in report["errors"])
    assert any("parent_sft_tree_sha256 does not match" in error for error in report["errors"])


def test_rejects_missing_adapter_and_grpo_parent_hashes():
    runs = _run_descriptors()
    del runs[1]["adapter"]["tree_sha256"]
    del runs[2]["adapter"]["parent_sft_tree_sha256"]

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert any("adapter.tree_sha256 is required" in error for error in report["errors"])
    assert any("parent_sft_tree_sha256 is required" in error for error in report["errors"])


def test_requires_clean_source_and_evaluator_provenance():
    runs = _run_descriptors()
    runs[0]["source"]["dirty"] = True
    runs[1]["evaluator"]["tree_sha256"] = None
    runs[2]["evaluator"]["dirty"] = True

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert any("source.dirty must be explicitly false" in error for error in report["errors"])
    assert any(
        "evaluator.tree_sha256 must be a 64-character" in error for error in report["errors"]
    )
    assert any("evaluator.dirty must be explicitly false" in error for error in report["errors"])


def test_allows_distinct_clean_native_evaluator_provenance():
    runs = _run_descriptors()
    for index, run in enumerate(runs):
        run["evaluator"] = {
            "commit_sha": str(index + 1) * 40,
            "tree_sha256": str(index + 4) * 64,
            "dirty": False,
        }

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert report["lineage_status"] == "CONSISTENT"
    assert {
        provenance["commit_sha"] for provenance in report["evaluator_provenance_by_arm"].values()
    } == {"1" * 40, "2" * 40, "3" * 40}


@pytest.mark.parametrize(
    "field,wrong_value",
    [
        ("version", "synthetic-g13-scorer-v2"),
        ("sha256", "9" * 64),
    ],
)
def test_rejects_mismatched_caller_pinned_common_scorer(field, wrong_value):
    runs = _run_descriptors()
    runs[1]["comparison_scorer"][field] = wrong_value

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert report["lineage_status"] == "INVALID"
    assert any(
        f"comparison_scorer.{field} differs from the candidate schema" in error
        for error in report["errors"]
    )


def test_recomputes_split_digest_and_rejects_duplicate_or_reordered_membership():
    runs = _run_descriptors()
    runs[0]["scenario_ids"] = list(reversed(SCENARIO_IDS))
    runs[0]["split_sha256"] = _split_sha256(runs[0]["scenario_ids"])
    runs[1]["scenario_ids"] = [SCENARIO_IDS[0], SCENARIO_IDS[0]]
    runs[1]["split_sha256"] = _split_sha256(runs[1]["scenario_ids"])
    runs[2]["split_sha256"] = "0" * 64

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert any("candidate schema's ordered membership" in error for error in report["errors"])
    assert any("duplicate scenario IDs" in error for error in report["errors"])
    assert any("does not match its ordered scenario_ids" in error for error in report["errors"])


@pytest.mark.parametrize(
    "field",
    [
        "permissions",
        "inference",
        "tool_budget",
        "action_budget",
        "observation_budget",
        "time_budget",
    ],
)
def test_requires_exact_matched_evaluation_contract(field):
    runs = _run_descriptors()
    runs[1]["evaluation"][field] = {"different": 1}

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert any(
        f"evaluation.{field} differs from the candidate schema" in error
        for error in report["errors"]
    )


def test_rejects_boolean_and_nonfinite_evaluation_budgets():
    runs = _run_descriptors()
    runs[0]["evaluation"]["tool_budget"] = True
    runs[1]["evaluation"]["time_budget"] = {"seconds": float("inf")}

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert any("budget values must not be Boolean" in error for error in report["errors"])
    assert any("budget values must be finite" in error for error in report["errors"])


def test_rejects_run_self_declaration_as_independent_serving_attestation():
    runs = _run_descriptors()
    runs[1]["serving_identity_attestation"] = {
        "sha256": "4" * 64,
        "observer": "the run itself",
    }
    runs[2]["serving_identity"]["independently_observed_sha256"] = "4" * 64

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert (
        sum("not accepted as independent serving evidence" in error for error in report["errors"])
        == 1
    )
    assert any("contains unsupported fields" in error for error in report["errors"])
    assert report["serving_identity"]["independently_observed"]["status"] == "NOT_PROVIDED"
    assert report["certification_status"] == "NOT_CERTIFIED"


def test_training_budgets_are_recorded_without_requiring_equality():
    runs = _run_descriptors()
    runs[2]["training_budget"] = {"steps": 1, "epochs": 9}

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert report["lineage_status"] == "CONSISTENT"
    assert (
        report["training_budgets_by_arm"][ARM_ORDER[1]]
        != report["training_budgets_by_arm"][ARM_ORDER[2]]
    )


def test_baseline_training_budget_must_be_explicitly_null():
    runs = _run_descriptors()
    runs[0]["training_budget"] = {"steps": 1}

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert report["lineage_status"] == "INVALID"
    assert any("training_budget must be explicitly null" in error for error in report["errors"])

    runs = _run_descriptors()
    del runs[0]["training_budget"]
    missing_report = validate_candidate_lineage(runs, _candidate_schema())

    assert missing_report["lineage_status"] == "INVALID"
    assert any(
        "training_budget must be explicitly null" in error for error in missing_report["errors"]
    )


@pytest.mark.parametrize(
    "arm_index,budget,error_fragment",
    [
        (1, {"steps": -1}, "budget values must be non-negative"),
        (2, None, "must contain finite numeric budget values"),
        (1, {"steps": 0}, "at least one positive numeric budget value"),
        (2, {"steps": 0, "epochs": 0}, "at least one positive numeric budget value"),
        (1, {"steps": True}, "budget values must not be Boolean"),
        (2, {"steps": float("nan")}, "budget values must be finite"),
        (2, {"steps": float("inf")}, "budget values must be finite"),
        (1, {}, "budget mappings must not be empty"),
        (2, {"steps": None}, "must contain finite numeric budget values"),
        (1, {"steps": "120"}, "must contain finite numeric budget values"),
    ],
)
def test_trained_arm_budgets_require_positive_finite_nonnegative_numeric_measures(
    arm_index, budget, error_fragment
):
    runs = _run_descriptors()
    runs[arm_index]["training_budget"] = budget

    report = validate_candidate_lineage(runs, _candidate_schema())

    assert report["lineage_status"] == "INVALID"
    assert any(error_fragment in error for error in report["errors"])


def test_uses_caller_supplied_evaluation_seed_and_budgets_without_defaults():
    runs = _run_descriptors()
    schema = _candidate_schema()
    schema["evaluation_contract"]["inference"] = {
        "temperature": 0.65,
        "top_p": 0.91,
        "seed": 90210,
    }
    schema["evaluation_contract"]["tool_budget"] = {"calls": 47}
    schema["evaluation_contract"]["action_budget"] = {"actions": 19}
    for run in runs:
        run["evaluation"] = copy.deepcopy(schema["evaluation_contract"])

    report = validate_candidate_lineage(runs, schema)

    assert report["lineage_status"] == "CONSISTENT"
    assert report["certification_status"] == "NOT_CERTIFIED"
