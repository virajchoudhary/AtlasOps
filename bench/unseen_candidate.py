"""Controlled admission contract for genuinely unseen G5 final-test candidates.

This module deliberately does not create scenarios.  A future operator must run
admission outside model context, after SFT/GRPO/RS are frozen, and retain the
result in a private review location.  Publishing an admitted candidate--or even
showing it to a coding/model session--would destroy its holdout status.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from bench.scenario_contract import (
    CATALOG_PATH,
    EXPOSURE_LEDGER_PATH,
    _coarse_fault,
    _fault_records_from_documents,
    _normalise,
    _semantic_alert,
    catalog_entries,
    development_exposed_ids,
    sha256_object,
    write_json_atomically,
)


CANDIDATE_SCHEMA_VERSION = "atlasops.g5.unseen-candidate/v1"
ADMISSION_SCHEMA_VERSION = "atlasops.g5.candidate-admission/v1"
_REQUIRED_TOP_LEVEL = {
    "description",
    "exposure_attestation",
    "manifest_documents",
    "model_visible_alert",
    "scenario_id",
    "schema_version",
    "source_lineage",
    "success_predicates",
    "tier",
}
_OPTIONAL_TOP_LEVEL = {"variant_parent_id"}
_FORBIDDEN_ALERT_KEYS = {
    "answer",
    "expected",
    "fault",
    "root_cause",
    "rootcause",
    "scenario_family",
    "scenario_id",
    "split_role",
    "success_predicate",
    "verifier",
}


def _reject_forbidden_alert_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in _FORBIDDEN_ALERT_KEYS:
                raise ValueError(f"hidden-truth sentinel in model-visible alert: {path}.{key}")
            _reject_forbidden_alert_fields(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_alert_fields(item, f"{path}[{index}]")


def _require_nonempty_strings(mapping: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(key for key in keys if not str(mapping.get(key, "")).strip())
    if missing:
        raise ValueError(f"{label} missing non-empty fields: {missing}")


def _candidate_faults(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    documents = candidate.get("manifest_documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("manifest_documents must be a non-empty list")
    faults = _fault_records_from_documents([doc for doc in documents if doc])
    if not faults:
        raise ValueError("manifest_documents contain no recognised fault injection")
    return faults


def _historical_source(documents: list[dict[str, Any]]) -> str | None:
    for document in documents:
        value = ((document or {}).get("metadata", {}).get("labels", {}) or {}).get(
            "historical_incident"
        )
        if value:
            return str(value)
    return None


def _validate_predicates(scenario_id: str, predicates: dict[str, Any]) -> None:
    if predicates.get("scenario_id") != scenario_id:
        raise ValueError("success predicate scenario_id mismatch")
    if not isinstance(predicates.get("workloads"), list) or not predicates["workloads"]:
        raise ValueError("candidate must declare workload success predicates")
    if predicates.get("require_chaos_cleared") is not True:
        raise ValueError("candidate must require chaos clearance")


def admit_unseen_candidate(
    candidate: dict[str, Any],
    *,
    catalog: dict[str, Any],
    exposure_ledger: dict[str, Any],
) -> dict[str, Any]:
    """Validate one operator-provided candidate and return admission evidence."""
    if candidate.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        raise ValueError("unsupported unseen-candidate schema")
    unknown_keys = set(candidate) - _REQUIRED_TOP_LEVEL - _OPTIONAL_TOP_LEVEL
    missing_keys = _REQUIRED_TOP_LEVEL - set(candidate)
    if unknown_keys or missing_keys:
        raise ValueError(
            f"candidate fields invalid: unknown={sorted(unknown_keys)}, missing={sorted(missing_keys)}"
        )

    scenario_id = str(candidate["scenario_id"])
    match = re.fullmatch(r"(single_fault|cascade|multi_fault|named_replays)/([a-z0-9][a-z0-9-]*)", scenario_id)
    if not match:
        raise ValueError(f"invalid scenario_id format: {scenario_id}")
    if candidate["tier"] != match.group(1):
        raise ValueError("tier does not match scenario_id")
    if scenario_id in catalog_entries(catalog):
        raise ValueError("candidate collides with an existing catalogue scenario")

    _require_nonempty_strings(candidate, {"description"}, "candidate")
    lineage = candidate.get("source_lineage")
    attestation = candidate.get("exposure_attestation")
    if not isinstance(lineage, dict) or not isinstance(attestation, dict):
        raise ValueError("source_lineage and exposure_attestation must be objects")
    _require_nonempty_strings(
        lineage,
        {
            "commit_sha",
            "created_at",
            "generator_name",
            "generator_version",
            "path",
            "repository_url",
        },
        "source_lineage",
    )
    if not re.fullmatch(r"[0-9a-f]{40}", str(lineage.get("commit_sha", ""))):
        raise ValueError("source_lineage commit_sha is not a full commit SHA")
    _require_nonempty_strings(
        attestation,
        {"attested_by", "method", "status"},
        "exposure_attestation",
    )
    if attestation.get("status") != "UNEXPOSED_ATTESTED":
        raise ValueError("candidate exposure status must be UNEXPOSED_ATTESTED")

    alert = candidate.get("model_visible_alert")
    if not isinstance(alert, dict) or not alert.get("commonLabels") or not alert.get("alerts"):
        raise ValueError("model_visible_alert is incomplete")
    _reject_forbidden_alert_fields(alert)

    predicates = candidate.get("success_predicates")
    if not isinstance(predicates, dict):
        raise ValueError("success_predicates must be an object")
    _validate_predicates(scenario_id, predicates)

    documents = [doc for doc in candidate["manifest_documents"] if doc]
    faults = _candidate_faults(candidate)
    coarse_faults = [_coarse_fault(fault) for fault in faults]
    fault_signatures = [sha256_object(item) for item in coarse_faults]
    semantic_alert = _semantic_alert(alert)
    alert_semantic_hash = sha256_object(semantic_alert)
    source_incident = _historical_source(documents)

    exposed_ids = development_exposed_ids(catalog, exposure_ledger)
    if scenario_id in exposed_ids:
        raise ValueError("candidate is recorded as development-exposed")

    existing_entries = catalog_entries(catalog).values()
    conflicts = []
    for entry in existing_entries:
        if set(entry.get("fault_signatures", [])).intersection(fault_signatures):
            conflicts.append({"reason": "fault_signature", "scenario_id": entry["scenario_id"]})
        if entry.get("alert_semantic_hash") == alert_semantic_hash:
            conflicts.append({"reason": "alert_semantics", "scenario_id": entry["scenario_id"]})
        if source_incident and entry.get("source_incident_id") == source_incident:
            conflicts.append({"reason": "source_incident", "scenario_id": entry["scenario_id"]})
    if conflicts:
        raise ValueError(f"family leakage against existing scenarios: {conflicts}")

    computed = {
        "alert_semantic_hash": alert_semantic_hash,
        "causal_template_id": "|".join(
            sorted(f"{fault['kind']}:{fault['action']}" for fault in faults)
        ),
        "fault_signature": sha256_object(coarse_faults),
        "fault_signatures": fault_signatures,
        "faults": _normalise(faults),
        "manifest_semantic_hash": sha256_object(_normalise(faults)),
        "scenario_family_id": sha256_object(fault_signatures),
        "target_signature": sha256_object(
            sorted({target for fault in faults for target in fault["targets"]})
        ),
    }
    admission = {
        "admission_status": "ADMITTED_REVIEW_REQUIRED",
        "catalogue_sha256": catalog["catalog_sha256"],
        "computed": computed,
        "exposure_ledger_sha256": exposure_ledger["ledger_sha256"],
        "lineage": _normalise(lineage),
        "model_visibility_review": {
            "alert_hidden_truth_sentinels": "passed",
            "operator_only": True,
        },
        "scenario_id": scenario_id,
        "schema_version": ADMISSION_SCHEMA_VERSION,
        "success_predicates": _normalise(predicates),
        "tier": candidate["tier"],
        "variant_parent_id": candidate.get("variant_parent_id"),
    }
    admission["admission_sha256"] = sha256_object(admission)
    return admission


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    admit_parser = subparsers.add_parser("admit", help="admit one reviewed candidate")
    admit_parser.add_argument("--input", required=True)
    admit_parser.add_argument("--output", required=True)
    admit_parser.add_argument("--catalog", default=str(CATALOG_PATH))
    admit_parser.add_argument("--ledger", default=str(EXPOSURE_LEDGER_PATH))
    args = parser.parse_args(argv)

    try:
        candidate = load_json(Path(args.input))
        catalog = load_json(Path(args.catalog))
        ledger = load_json(Path(args.ledger))
        admitted = admit_unseen_candidate(
            candidate,
            catalog=catalog,
            exposure_ledger=ledger,
        )
        write_json_atomically(Path(args.output), admitted)
        print(
            f"admitted {admitted['scenario_id']} for private review; "
            "do not expose this output to models or developers who will tune on G5/G6."
        )
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if __package__ is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
