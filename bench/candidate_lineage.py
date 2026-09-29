"""Validate a prospective three-arm candidate's lineage without certifying results.

The caller supplies arm labels, ordered scenario membership, the SFT V2 tree hash,
and the evaluation contract. This module validates metadata only: it does not
load weights, perform inference, or establish which weights a serving process used.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from bench.episode_membership import ordered_scenario_ids_sha256

_REQUIRED_EVALUATION_FIELDS = (
    "permissions",
    "inference",
    "tool_budget",
    "action_budget",
    "observation_budget",
    "time_budget",
)
_REQUIRED_ARM_ORDER = (
    "Zero-Shot Baseline",
    "SFT Model",
    "SFT + GRPO",
)
_REVISION_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64}|sha256:[0-9a-f]{64})$", re.I)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.I)
_SELF_ATTESTATION_FIELDS = {
    "independent_serving_attestation",
    "independently_observed_serving_identity",
    "observed_serving_sha256",
    "serving_identity_attestation",
    "independently_observed_model_sha256",
    "observed_model_sha256",
}
_SERVING_IDENTITY_FIELDS = {"declared_sha256"}


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _same_json_value(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, Mapping):
        return left.keys() == right.keys() and all(
            _same_json_value(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_value(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _json_value_errors(value: Any, path: str) -> list[str]:
    if value is None or type(value) in (str, bool, int):
        return []
    if type(value) is float:
        return [] if math.isfinite(value) else [f"{path} must not contain non-finite numbers"]
    if isinstance(value, Mapping):
        errors = []
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(f"{path} object keys must be strings")
                continue
            errors.extend(_json_value_errors(item, f"{path}.{key}"))
        return errors
    if isinstance(value, list):
        errors = []
        for index, item in enumerate(value):
            errors.extend(_json_value_errors(item, f"{path}[{index}]"))
        return errors
    return [f"{path} must contain only JSON-compatible values"]


def _budget_value_errors(
    value: Any,
    path: str,
    *,
    require_positive: bool = False,
) -> list[str]:
    errors: list[str] = []
    numeric_leaves = 0
    positive_leaves = 0

    def visit(item: Any, item_path: str) -> None:
        nonlocal numeric_leaves, positive_leaves
        if type(item) is bool:
            errors.append(f"{item_path} budget values must not be Boolean")
        elif type(item) in (int, float):
            numeric_leaves += 1
            if type(item) is float and not math.isfinite(item):
                errors.append(f"{item_path} budget values must be finite")
            elif item < 0:
                errors.append(f"{item_path} budget values must be non-negative")
            elif item > 0:
                positive_leaves += 1
        elif isinstance(item, Mapping):
            if not item:
                errors.append(f"{item_path} budget mappings must not be empty")
            for key, nested in item.items():
                if not isinstance(key, str):
                    errors.append(f"{item_path} budget object keys must be strings")
                else:
                    visit(nested, f"{item_path}.{key}")
        elif isinstance(item, list):
            if not item:
                errors.append(f"{item_path} budget lists must not be empty")
            for index, nested in enumerate(item):
                visit(nested, f"{item_path}[{index}]")
        else:
            errors.append(f"{item_path} must contain finite numeric budget values")

    visit(value, path)
    if numeric_leaves == 0 and not errors:
        errors.append(f"{path} must contain at least one numeric budget value")
    if require_positive and positive_leaves == 0:
        errors.append(f"{path} must contain at least one positive numeric budget value")
    return errors


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _looks_like_serving_attestation_field(field: Any) -> bool:
    if not isinstance(field, str):
        return False
    normalized = field.casefold()
    if normalized in _SELF_ATTESTATION_FIELDS:
        return True
    if normalized == "serving_identity":
        return False
    has_subject = any(token in normalized for token in ("serv", "served", "model"))
    has_claim = any(
        token in normalized
        for token in ("attest", "independent", "observed", "identity", "sha", "digest", "hash")
    )
    return has_subject and has_claim


def _normalized_sha256(value: str) -> str:
    return value.lower()


def _comparison_scorer_errors(
    value: Any,
    path: str,
) -> tuple[list[str], dict[str, str] | None]:
    if not isinstance(value, Mapping):
        return [f"{path} must record version and sha256"], None

    errors: list[str] = []
    version = value.get("version")
    if not isinstance(version, str) or not version.strip():
        errors.append(f"{path}.version must be a non-blank string")

    sha256 = value.get("sha256")
    if not _valid_sha256(sha256):
        errors.append(f"{path}.sha256 must be a 64-character SHA-256 digest")

    extra_fields = set(value) - {"version", "sha256"}
    if extra_fields:
        errors.append(f"{path} contains unsupported fields: {sorted(extra_fields, key=repr)}")

    if errors:
        return errors, None
    assert isinstance(version, str)
    assert isinstance(sha256, str)
    return [], {"version": version.strip(), "sha256": _normalized_sha256(sha256)}


def _model_pin_errors(value: Any, path: str) -> tuple[list[str], dict[str, str] | None]:
    if not isinstance(value, Mapping):
        return [f"{path} must be an object with revision and sha256"], None

    errors: list[str] = []
    revision = value.get("revision")
    if not isinstance(revision, str) or _REVISION_RE.fullmatch(revision.strip()) is None:
        errors.append(
            f"{path}.revision must be an immutable full 40/64-character commit "
            "or sha256 digest; aliases and blank values are invalid"
        )

    digest = value.get("sha256")
    if not _valid_sha256(digest):
        errors.append(f"{path}.sha256 must be a 64-character SHA-256 digest")

    if errors:
        return errors, None
    assert isinstance(revision, str)
    assert isinstance(digest, str)
    return [], {"revision": revision.strip().lower(), "sha256": _normalized_sha256(digest)}


def _provenance_errors(
    value: Any,
    path: str,
) -> tuple[list[str], dict[str, Any] | None]:
    if not isinstance(value, Mapping):
        return [f"{path} must record commit_sha, tree_sha256, and dirty=false"], None

    errors: list[str] = []
    commit_sha = value.get("commit_sha")
    if (
        not isinstance(commit_sha, str)
        or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit_sha, re.I) is None
    ):
        errors.append(f"{path}.commit_sha must be a full 40/64-character Git commit SHA")

    tree_sha256 = value.get("tree_sha256")
    if not _valid_sha256(tree_sha256):
        errors.append(f"{path}.tree_sha256 must be a 64-character SHA-256 digest")

    if value.get("dirty") is not False:
        errors.append(f"{path}.dirty must be explicitly false")

    if errors:
        return errors, None
    assert isinstance(commit_sha, str)
    assert isinstance(tree_sha256, str)
    return [], {
        "commit_sha": commit_sha.lower(),
        "tree_sha256": _normalized_sha256(tree_sha256),
        "dirty": False,
    }


def _scenario_ids_errors(value: Any, path: str) -> tuple[list[str], list[str] | None]:
    if not isinstance(value, list) or not value:
        return [f"{path} must be a non-empty ordered list of scenario IDs"], None
    errors: list[str] = []
    if any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{path} must contain non-blank string scenario IDs")
    if all(isinstance(item, str) for item in value) and len(set(value)) != len(value):
        errors.append(f"{path} must not contain duplicate scenario IDs")
    if errors:
        return errors, None
    return [], list(value)


def _valid_run_descriptors(value: Any) -> list[Any]:
    if not _is_sequence(value):
        return []
    return list(value)


def validate_candidate_lineage(
    run_descriptors: Sequence[Mapping[str, Any]],
    candidate_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate three arm descriptors against externally supplied candidate settings.

    ``candidate_schema`` must provide the exact GAI+RL arm order, ``scenario_ids``
    (the expected ordered membership), ``sft_v2_adapter_tree_sha256``, a
    ``comparison_scorer`` version/SHA-256 contract, and ``evaluation_contract``.
    Each descriptor records its arm, base model and tokenizer revision/digest,
    clean source and native evaluator provenance, the common comparison scorer,
    scenario IDs and split digest, evaluation settings, a per-arm training budget,
    and an adapter entry. The baseline adapter is explicitly ``None``; the SFT
    and GRPO entries carry ``tree_sha256`` and GRPO also carries
    ``parent_sft_tree_sha256``.

    The return value describes metadata consistency only. It always remains
    ``non_empirical`` and ``NOT_CERTIFIED``. A run-declared serving digest is not
    independent evidence of the weights that were actually served.
    """
    errors: list[str] = []
    if not isinstance(candidate_schema, Mapping):
        candidate_schema = {}
        errors.append("candidate_schema must be an object")

    expected_arms = list(_REQUIRED_ARM_ORDER)
    raw_arms = candidate_schema.get("arms")
    if not _is_sequence(raw_arms) or list(raw_arms) != expected_arms:
        observed_arms = list(raw_arms) if _is_sequence(raw_arms) else raw_arms
        errors.append(
            "candidate_schema.arms must exactly match the required ordered labels "
            f"{expected_arms}; observed {observed_arms!r}"
        )

    expected_ids_errors, expected_scenario_ids = _scenario_ids_errors(
        candidate_schema.get("scenario_ids"),
        "candidate_schema.scenario_ids",
    )
    errors.extend(expected_ids_errors)
    expected_split_sha256 = (
        ordered_scenario_ids_sha256(expected_scenario_ids)
        if expected_scenario_ids is not None
        else None
    )

    sft_v2_tree_sha256 = candidate_schema.get("sft_v2_adapter_tree_sha256")
    if not _valid_sha256(sft_v2_tree_sha256):
        errors.append(
            "candidate_schema.sft_v2_adapter_tree_sha256 must be a 64-character SHA-256 digest"
        )
        sft_v2_tree_sha256 = None
    else:
        sft_v2_tree_sha256 = _normalized_sha256(sft_v2_tree_sha256)

    scorer_errors, comparison_scorer_contract = _comparison_scorer_errors(
        candidate_schema.get("comparison_scorer"),
        "candidate_schema.comparison_scorer",
    )
    errors.extend(scorer_errors)

    expected_evaluation = candidate_schema.get("evaluation_contract")
    if not isinstance(expected_evaluation, Mapping):
        errors.append("candidate_schema.evaluation_contract must be an object")
        expected_evaluation = {}
    for field in _REQUIRED_EVALUATION_FIELDS:
        if field not in expected_evaluation:
            errors.append(f"candidate_schema.evaluation_contract.{field} is required")
    errors.extend(
        _json_value_errors(
            expected_evaluation,
            "candidate_schema.evaluation_contract",
        )
    )
    for field in (
        "tool_budget",
        "action_budget",
        "observation_budget",
        "time_budget",
    ):
        if field in expected_evaluation:
            errors.extend(
                _budget_value_errors(
                    expected_evaluation[field],
                    f"candidate_schema.evaluation_contract.{field}",
                )
            )

    runs = _valid_run_descriptors(run_descriptors)
    if not _is_sequence(run_descriptors):
        errors.append("run_descriptors must be an ordered sequence of run objects")

    observed_arms: list[str] = []
    valid_runs: list[tuple[int, Mapping[str, Any], str]] = []
    for index, run in enumerate(runs):
        label = f"run_descriptors[{index}]"
        if not isinstance(run, Mapping):
            errors.append(f"{label} must be an object")
            continue
        arm = run.get("arm")
        if not isinstance(arm, str) or not arm.strip():
            errors.append(f"{label}.arm must be a non-blank string")
            continue
        observed_arms.append(arm)
        valid_runs.append((index, run, arm))

    if observed_arms:
        seen_arms: set[str] = set()
        duplicate_arms: set[str] = set()
        for arm in observed_arms:
            if arm in seen_arms:
                duplicate_arms.add(arm)
            seen_arms.add(arm)
        duplicates = sorted(duplicate_arms)
        if duplicates:
            errors.append(f"run_descriptors contain duplicate arm labels: {duplicates}")
        missing_arms = [arm for arm in expected_arms if arm not in observed_arms]
        extra_arms = [arm for arm in observed_arms if arm not in expected_arms]
        if missing_arms:
            errors.append(f"run_descriptors are missing arms: {missing_arms}")
        if extra_arms:
            errors.append(f"run_descriptors contain extra arms: {extra_arms}")
        if observed_arms != expected_arms:
            errors.append(
                "run_descriptors must appear in candidate_schema.arms order; "
                f"expected {expected_arms}, observed {observed_arms}"
            )
    elif expected_arms:
        errors.append("run_descriptors must contain exactly the three required arms")
    if len(runs) != 3:
        errors.append(f"run_descriptors must contain exactly three runs; observed {len(runs)}")

    pins_by_arm: dict[str, dict[str, dict[str, str]]] = {}
    source_by_arm: dict[str, dict[str, Any]] = {}
    evaluator_by_arm: dict[str, dict[str, Any]] = {}
    comparison_scorer_by_arm: dict[str, dict[str, str]] = {}
    training_budgets_by_arm: dict[str, Any] = {}
    declared_serving_by_arm: dict[str, dict[str, Any]] = {}
    scenario_membership_by_arm: dict[str, dict[str, Any]] = {}
    adapter_lineage_by_arm: dict[str, dict[str, Any]] = {}
    baseline_arm, sft_arm, grpo_arm = (
        expected_arms if len(expected_arms) == 3 else (None, None, None)
    )

    for index, run, arm in valid_runs:
        label = f"run_descriptors[{index}] ({arm})"

        for field in run:
            if _looks_like_serving_attestation_field(field):
                errors.append(
                    f"{label}.{field} is not accepted as independent serving evidence; "
                    "this validator has no trusted observation input"
                )

        arm_pins: dict[str, dict[str, str]] = {}
        for field in ("base_model", "tokenizer"):
            field_errors, pin = _model_pin_errors(run.get(field), f"{label}.{field}")
            errors.extend(field_errors)
            if pin is not None:
                arm_pins[field] = pin
        if len(arm_pins) == 2:
            pins_by_arm[arm] = arm_pins

        source_errors, source = _provenance_errors(run.get("source"), f"{label}.source")
        errors.extend(source_errors)
        if source is not None:
            source_by_arm[arm] = source

        evaluator_errors, evaluator = _provenance_errors(
            run.get("evaluator"),
            f"{label}.evaluator",
        )
        errors.extend(evaluator_errors)
        if evaluator is not None:
            evaluator_by_arm[arm] = evaluator

        scorer_errors, scorer = _comparison_scorer_errors(
            run.get("comparison_scorer"),
            f"{label}.comparison_scorer",
        )
        errors.extend(scorer_errors)
        if scorer is not None:
            comparison_scorer_by_arm[arm] = scorer
            if comparison_scorer_contract is not None:
                for field in ("version", "sha256"):
                    if scorer[field] != comparison_scorer_contract[field]:
                        errors.append(
                            f"{label}.comparison_scorer.{field} differs from the candidate schema"
                        )

        scenario_errors, scenario_ids = _scenario_ids_errors(
            run.get("scenario_ids"),
            f"{label}.scenario_ids",
        )
        errors.extend(scenario_errors)
        if scenario_ids is not None and expected_scenario_ids is not None:
            if scenario_ids != expected_scenario_ids:
                errors.append(
                    f"{label}.scenario_ids do not match the candidate schema's ordered membership"
                )
        if scenario_ids is not None:
            run_split_sha256 = ordered_scenario_ids_sha256(scenario_ids)
            recorded_split_sha256 = run.get("split_sha256")
            scenario_record = {
                "scenario_ids": scenario_ids,
                "computed_split_sha256": run_split_sha256,
            }
            if not _valid_sha256(recorded_split_sha256):
                errors.append(f"{label}.split_sha256 must be a 64-character SHA-256 digest")
            elif _normalized_sha256(recorded_split_sha256) != run_split_sha256:
                errors.append(f"{label}.split_sha256 does not match its ordered scenario_ids")
            elif expected_split_sha256 is not None and run_split_sha256 != expected_split_sha256:
                errors.append(f"{label}.scenario_ids digest differs from the candidate schema")
            if _valid_sha256(recorded_split_sha256):
                scenario_record["declared_split_sha256"] = _normalized_sha256(recorded_split_sha256)
            scenario_membership_by_arm[arm] = scenario_record

        evaluation = run.get("evaluation")
        if not isinstance(evaluation, Mapping):
            errors.append(f"{label}.evaluation must be an object")
        else:
            errors.extend(_json_value_errors(evaluation, f"{label}.evaluation"))
            actual_fields = set(evaluation)
            expected_fields = set(expected_evaluation)
            if actual_fields != expected_fields:
                missing = sorted(expected_fields - actual_fields, key=repr)
                extra = sorted(actual_fields - expected_fields, key=repr)
                if missing:
                    errors.append(f"{label}.evaluation is missing fields: {missing}")
                if extra:
                    errors.append(f"{label}.evaluation contains unconfigured fields: {extra}")
            for field in _REQUIRED_EVALUATION_FIELDS:
                if field not in evaluation or field not in expected_evaluation:
                    continue
                if not _same_json_value(evaluation[field], expected_evaluation[field]):
                    errors.append(f"{label}.evaluation.{field} differs from the candidate schema")
            for field in expected_fields - set(_REQUIRED_EVALUATION_FIELDS):
                if field in evaluation and not _same_json_value(
                    evaluation[field],
                    expected_evaluation[field],
                ):
                    errors.append(f"{label}.evaluation.{field} differs from the candidate schema")
            for field in (
                "tool_budget",
                "action_budget",
                "observation_budget",
                "time_budget",
            ):
                if field in evaluation:
                    errors.extend(
                        _budget_value_errors(
                            evaluation[field],
                            f"{label}.evaluation.{field}",
                        )
                    )

        if arm == baseline_arm:
            if "training_budget" not in run or run["training_budget"] is not None:
                errors.append(f"{label}.training_budget must be explicitly null for the baseline")
                training_budgets_by_arm[arm] = {"status": "INVALID"}
            else:
                training_budgets_by_arm[arm] = None
        elif "training_budget" not in run:
            errors.append(f"{label}.training_budget must be explicitly recorded")
            training_budgets_by_arm[arm] = {"status": "INVALID"}
        else:
            budget_errors = _budget_value_errors(
                run["training_budget"],
                f"{label}.training_budget",
                require_positive=arm in (sft_arm, grpo_arm),
            )
            errors.extend(budget_errors)
            training_budgets_by_arm[arm] = (
                {"status": "INVALID"} if budget_errors else run["training_budget"]
            )

        serving_identity = run.get("serving_identity")
        if serving_identity is None:
            declared_serving_by_arm[arm] = {
                "status": "NOT_RECORDED",
                "sha256": None,
            }
        elif not isinstance(serving_identity, Mapping):
            errors.append(f"{label}.serving_identity must be an object when supplied")
            declared_serving_by_arm[arm] = {"status": "INVALID", "sha256": None}
        else:
            extra_fields = set(serving_identity) - _SERVING_IDENTITY_FIELDS
            if extra_fields:
                errors.append(
                    f"{label}.serving_identity contains unsupported fields: "
                    f"{sorted(extra_fields, key=repr)}; only a declared_sha256 is recorded"
                )
            declared_digest = serving_identity.get("declared_sha256")
            if declared_digest is None:
                declared_serving_by_arm[arm] = {
                    "status": "NOT_RECORDED",
                    "sha256": None,
                }
            elif not _valid_sha256(declared_digest):
                errors.append(
                    f"{label}.serving_identity.declared_sha256 must be a "
                    "64-character SHA-256 digest"
                )
                declared_serving_by_arm[arm] = {"status": "INVALID", "sha256": None}
            else:
                declared_serving_by_arm[arm] = {
                    "status": "RECORDED",
                    "sha256": _normalized_sha256(declared_digest),
                }

        adapter = run.get("adapter")
        if arm == baseline_arm:
            if "adapter" not in run or adapter is not None:
                errors.append(f"{label}.adapter must be explicitly null for the zero-shot baseline")
            else:
                adapter_lineage_by_arm[arm] = {
                    "status": "BASE_MODEL_ONLY",
                    "tree_sha256": None,
                }
        elif arm == sft_arm:
            if not isinstance(adapter, Mapping):
                errors.append(f"{label}.adapter must record the SFT adapter tree hash")
            else:
                tree_sha256 = adapter.get("tree_sha256")
                if not _valid_sha256(tree_sha256):
                    errors.append(
                        f"{label}.adapter.tree_sha256 is required and must be a "
                        "64-character SHA-256 digest"
                    )
                else:
                    normalized_tree_sha256 = _normalized_sha256(tree_sha256)
                    matches_v2 = (
                        sft_v2_tree_sha256 is not None
                        and normalized_tree_sha256 == sft_v2_tree_sha256
                    )
                    adapter_lineage_by_arm[arm] = {
                        "tree_sha256": normalized_tree_sha256,
                        "matches_sft_v2": matches_v2,
                    }
                    if sft_v2_tree_sha256 is not None and not matches_v2:
                        errors.append(
                            f"{label}.adapter.tree_sha256 does not match the "
                            "candidate schema SFT V2 tree hash"
                        )
        elif arm == grpo_arm:
            if not isinstance(adapter, Mapping):
                errors.append(
                    f"{label}.adapter must record the GRPO adapter tree and parent hashes"
                )
            else:
                tree_sha256 = adapter.get("tree_sha256")
                parent_sha256 = adapter.get("parent_sft_tree_sha256")
                if not _valid_sha256(tree_sha256):
                    errors.append(
                        f"{label}.adapter.tree_sha256 is required and must be a "
                        "64-character SHA-256 digest"
                    )
                else:
                    normalized_tree_sha256 = _normalized_sha256(tree_sha256)
                if not _valid_sha256(parent_sha256):
                    errors.append(
                        f"{label}.adapter.parent_sft_tree_sha256 is required and must be a "
                        "64-character SHA-256 digest"
                    )
                else:
                    normalized_parent_sha256 = _normalized_sha256(parent_sha256)
                if _valid_sha256(tree_sha256) and _valid_sha256(parent_sha256):
                    parent_matches_v2 = (
                        sft_v2_tree_sha256 is not None
                        and normalized_parent_sha256 == sft_v2_tree_sha256
                    )
                    adapter_lineage_by_arm[arm] = {
                        "tree_sha256": normalized_tree_sha256,
                        "parent_sft_tree_sha256": normalized_parent_sha256,
                        "parent_matches_sft_v2": parent_matches_v2,
                    }
                    if sft_v2_tree_sha256 is not None and not parent_matches_v2:
                        errors.append(
                            f"{label}.adapter.parent_sft_tree_sha256 does not match "
                            "the candidate schema SFT V2 tree hash"
                        )

    if pins_by_arm:
        first_arm = next(iter(pins_by_arm))
        first_pins = pins_by_arm[first_arm]
        for arm, pins in pins_by_arm.items():
            if arm == first_arm:
                continue
            for field in ("base_model", "tokenizer"):
                if (
                    field in pins
                    and field in first_pins
                    and not _same_json_value(
                        pins[field],
                        first_pins[field],
                    )
                ):
                    errors.append(f"{arm}.{field} revision/digest differs from {first_arm}")

    report_scenario_ids = expected_scenario_ids or []
    return {
        "lineage_status": "CONSISTENT" if not errors else "INVALID",
        "errors": errors,
        "non_empirical": True,
        "certification_status": "NOT_CERTIFIED",
        "scenario_membership": {
            "scenario_ids": report_scenario_ids,
            "split_sha256": expected_split_sha256,
            "by_arm": scenario_membership_by_arm,
        },
        "model_identity_by_arm": pins_by_arm,
        "adapter_lineage_by_arm": adapter_lineage_by_arm,
        "source_provenance_by_arm": source_by_arm,
        "evaluator_provenance_by_arm": evaluator_by_arm,
        "comparison_scorer": {
            "contract": comparison_scorer_contract,
            "declared_by_arm": comparison_scorer_by_arm,
            "execution_status": "NOT_ESTABLISHED",
        },
        "training_budgets_by_arm": training_budgets_by_arm,
        "serving_identity": {
            "declared_by_arm": declared_serving_by_arm,
            "independently_observed": {
                "status": "NOT_PROVIDED",
                "sha256_by_arm": {},
            },
            "interpretation": (
                "A run-declared digest establishes only the declared bytes, not the "
                "weights served during inference."
            ),
        },
    }
