"""Deterministic G5 scenario catalogue and split-freeze contract.

The catalogue is derived from immutable inputs that already exist in the
repository: frozen Chaos Mesh manifests, the objective verifier specifications,
and the model-visible alert templates.  A *proposed* split is inert data.  Only
``split.frozen.json`` is consumed by training/evaluation, and freezing is
intentionally a separate, fail-closed operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFESTS_DIR = REPO_ROOT / "bench" / "chaos_manifests"
CONTRACT_DIR = REPO_ROOT / "bench" / "g5"
CATALOG_PATH = CONTRACT_DIR / "scenario_catalog.json"
SPLIT_PROPOSED_PATH = CONTRACT_DIR / "split.proposed.json"
SPLIT_FROZEN_PATH = CONTRACT_DIR / "split.frozen.json"
EXPOSURE_LEDGER_PATH = CONTRACT_DIR / "exposure_ledger.json"

CATALOG_SCHEMA_VERSION = "atlasops.g5.scenario-catalog/v4"
SPLIT_SCHEMA_VERSION = "atlasops.g5.split-plan/v2"
EXPOSURE_SCHEMA_VERSION = "atlasops.g5.exposure-ledger/v3"
SPLIT_ALGORITHM_VERSION = "stratified-family-aware-v3"
SPLIT_GENERATOR_VERSION = "bench.scenario_contract/v2"
FROZEN_TIERS = ("single_fault", "cascade", "multi_fault", "named_replays")

_BARE_ID_PREFIXES = {"sf": "single_fault", "cs": "cascade", "mf": "multi_fault"}
_NAMED_REPLAYS = (
    "hist-aws-s3-2017", "hist-azure-dns-2019", "hist-cloudflare-2019",
    "hist-datadog-2023", "hist-discord-2022", "hist-facebook-bgp-2021",
    "hist-fastly-2021", "hist-github-2018", "hist-knight-capital-2012",
    "hist-slack-2022",
)
_MODEL_VISIBLE_HISTORICAL_PATHS = {
    "app.py",
    "bench/quick_eval.py",
    "bench/runner.py",
    "dashboard.py",
    "inference.py",
    "static/index.html",
    "training/generate_trajectories_fast.py",
    "training/grpo.py",
}
_DERIVED_CONTRACT_ARTIFACT_PATHS = {
    "bench/g5/exposure_ledger.json",
    "bench/g5/scenario_catalog.json",
    "bench/g5/split.proposed.json",
    "bench/g5/split.frozen.json",
}
ROLE_IDS_KEY = {
    "train": "train",
    "sft": "train",
    "grpo": "train",
    "rs_tuning": "train",
    "validation": "validation",
    "final_test": "final_test",
}
DEVELOPMENT_CONSUMERS = {
    "evaluation_subset",
    "grpo_curriculum",
    "leaderboard_subset",
}
_STAGE4_SPECIAL_SCENARIO = "single_fault/sf-002"
_JSON_SORT_KWARGS = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": False,
}


def canonical_json(value: Any) -> str:
    """Return stable JSON used for hashes and byte-for-byte artifacts."""
    return json.dumps(value, **_JSON_SORT_KWARGS)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_object(value: Any) -> str:
    return sha256_text(canonical_json(value))


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def portable_sha256_bytes(value: bytes) -> str:
    """Hash logical UTF-8 text independently of checkout line endings and BOM."""
    if value.startswith(b"\xef\xbb\xbf"):
        value = value[3:]
    value = value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return sha256_bytes(value)


def portable_sha256_file(path: Path) -> str:
    return portable_sha256_bytes(path.read_bytes())


def repository_head(repo_root: Path = REPO_ROOT) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot determine repository HEAD")
    value = completed.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeError("repository HEAD is not a full commit SHA")
    return value


def write_json_atomically(path: Path, value: Any) -> None:
    """Write deterministic JSON through a same-directory temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(canonical_json(value) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _normalise(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True))


def _extract_scenario_ids(content: str) -> list[str]:
    alternatives = [
        r"single_fault/sf-\d{3}",
        r"cascade/cs-\d{3}",
        r"multi_fault/mf-\d{3}",
        "|".join(re.escape(f"named_replays/{item}") for item in _NAMED_REPLAYS),
        r"\b(sf|cs|mf)-(\d{3})\b",
        "|".join(re.escape(item) for item in _NAMED_REPLAYS),
        r"\bSF-?002\b",
        r"\bsf-001\.\.008\b",
        r"\bcs-001\.\.005\b",
        r"\bmf-001\.\.005\b",
        r"named_replays/",
    ]
    pattern = re.compile("|".join(alternatives), re.IGNORECASE)
    found: set[str] = set()
    for match in pattern.finditer(content):
        token = match.group(0).lower()
        bare_match = re.fullmatch(r"(sf|cs|mf)-(\d{3})", token)
        if bare_match:
            found.add(f"{_BARE_ID_PREFIXES[bare_match.group(1)]}/{token}")
        elif token == "sf002":
            found.add("single_fault/sf-002")
        elif token == "sf-001..008":
            found.update(f"single_fault/sf-{index:03d}" for index in range(1, 9))
        elif token == "cs-001..005":
            found.update(f"cascade/cs-{index:03d}" for index in range(1, 6))
        elif token == "mf-001..005":
            found.update(f"multi_fault/mf-{index:03d}" for index in range(1, 6))
        elif token == "named_replays/":
            found.update(f"named_replays/{item}" for item in _NAMED_REPLAYS)
        elif token in _NAMED_REPLAYS:
            found.add(f"named_replays/{token}")
        else:
            found.add(token)
    return sorted(found)


def _classifications_for_path(path: str) -> list[str]:
    posix_path = path.replace("\\", "/")
    if posix_path.startswith("artifacts/evidence/stage4/") or posix_path.startswith(
        "docs/postmortems/"
    ):
        return [
            "MODEL_VISIBLE_HISTORICAL_EXPOSURE",
            "HIDDEN_ORACLE_DEVELOPER_EXPOSURE",
        ]
    if posix_path in _MODEL_VISIBLE_HISTORICAL_PATHS:
        return [
            "MODEL_VISIBLE_HISTORICAL_EXPOSURE",
            "DEVELOPER_TUNING_EXPOSURE",
        ]
    if (
        posix_path.startswith("bench/chaos_manifests/")
        or posix_path == "agents/verifier.py"
        or posix_path == "scripts/run_stage4_golden_incident.py"
    ):
        return ["HIDDEN_ORACLE_DEVELOPER_EXPOSURE"]
    return ["DEVELOPER_TUNING_EXPOSURE"]


def build_exposure_ledger(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Scan tracked files and classify every canonical-scenario reference."""
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    paths = sorted(
        item.decode("utf-8").replace("\\", "/")
        for item in completed.stdout.split(b"\0")
        if item
    )
    surfaces: list[dict[str, Any]] = []
    for relative in paths:
        if relative.replace("\\", "/") == EXPOSURE_LEDGER_PATH.as_posix():
            continue
        if relative.replace("\\", "/") in _DERIVED_CONTRACT_ARTIFACT_PATHS:
            continue
        absolute = repo_root / relative
        try:
            content = absolute.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scenario_ids = _extract_scenario_ids(content)
        if not scenario_ids:
            continue
        surfaces.append(
            {
                "path": relative,
                "scenario_ids": scenario_ids,
                "sha256": portable_sha256_file(absolute),
                "surface_classifications": _classifications_for_path(relative),
            }
        )

    surfaces.extend(
        [
            {
                "note": (
                    "handle_incident(scenario_id=...) is an execution-only channel; "
                    "the benchmark and training callers pass it outside the model alert."
                ),
                "path": "agents/coordinator.py",
                "scenario_ids": [],
                "surface_classifications": ["EXECUTION_ONLY_HIDDEN_CHANNEL"],
            },
            {
                "note": "No recommender-system interaction generation or fitting path exists.",
                "path": "<repository>",
                "scenario_ids": [],
                "surface_classifications": ["RS_NOT_IMPLEMENTED"],
            },
        ]
    )
    surfaces.sort(key=lambda item: (str(item["path"]), canonical_json(item)))

    by_classification: dict[str, set[str]] = {name: set() for name in (
        "MODEL_VISIBLE_HISTORICAL_EXPOSURE",
        "DEVELOPER_TUNING_EXPOSURE",
        "HIDDEN_ORACLE_DEVELOPER_EXPOSURE",
        "EXECUTION_ONLY_HIDDEN_CHANNEL",
        "SAFE_UNUSED_SCENARIO",
        "RS_NOT_IMPLEMENTED",
    )}
    development_exposed: set[str] = set()
    all_ids = {f"single_fault/sf-{index:03d}" for index in range(1, 9)}
    all_ids.update(f"cascade/cs-{index:03d}" for index in range(1, 6))
    all_ids.update(f"multi_fault/mf-{index:03d}" for index in range(1, 6))
    all_ids.update(f"named_replays/{item}" for item in _NAMED_REPLAYS)
    for surface in surfaces:
        ids = set(surface["scenario_ids"])
        canonical_ids = ids.intersection(all_ids)
        classifications = surface["surface_classifications"]
        for classification in classifications:
            by_classification.setdefault(classification, set()).update(canonical_ids)
        if {"MODEL_VISIBLE_HISTORICAL_EXPOSURE", "DEVELOPER_TUNING_EXPOSURE", "HIDDEN_ORACLE_DEVELOPER_EXPOSURE"}.intersection(classifications):
            development_exposed.update(canonical_ids)

    safe_unused = sorted(all_ids - development_exposed)
    by_classification["DEVELOPER_TUNING_EXPOSURE"] = sorted(by_classification["DEVELOPER_TUNING_EXPOSURE"])
    by_classification["EXECUTION_ONLY_HIDDEN_CHANNEL"] = []
    by_classification["HIDDEN_ORACLE_DEVELOPER_EXPOSURE"] = sorted(by_classification["HIDDEN_ORACLE_DEVELOPER_EXPOSURE"])
    by_classification["MODEL_VISIBLE_HISTORICAL_EXPOSURE"] = sorted(by_classification["MODEL_VISIBLE_HISTORICAL_EXPOSURE"])
    by_classification["RS_NOT_IMPLEMENTED"] = []
    by_classification["SAFE_UNUSED_SCENARIO"] = safe_unused

    payload = {
        "classification_definitions": {
            "MODEL_VISIBLE_HISTORICAL_EXPOSURE": (
                "Scenario identity or fault identity reached an agent-visible alert/input "
                "in a historical implementation or recorded transcript."
            ),
            "DEVELOPER_TUNING_EXPOSURE": (
                "Developers, tests, demos, documentation, subset selection, or tuning code "
                "observed scenario identity or results."
            ),
            "HIDDEN_ORACLE_DEVELOPER_EXPOSURE": (
                "Manifests, verifier mappings, evidence, or catalogue metadata reveal intended "
                "truth to developers but are not placed in model prompts."
            ),
            "EXECUTION_ONLY_HIDDEN_CHANNEL": (
                "Identity is accepted as orchestration metadata without serialization into agent input."
            ),
            "SAFE_UNUSED_SCENARIO": "Canonical scenarios with no observed development exposure.",
            "RS_NOT_IMPLEMENTED": "No RS interaction generation/fitting path exists.",
        },
        "exclusions": [
            {
                "paths": sorted(_DERIVED_CONTRACT_ARTIFACT_PATHS),
                "reason": (
                    "Derived contract artifacts are excluded to prevent self-referential "
                    "ledger drift; every exposure they restate is already captured from "
                    "independent manifests, runtime code, tests, docs, evidence, and subsets."
                ),
            }
        ],
        "schema_version": EXPOSURE_SCHEMA_VERSION,
        "summary": {
            "by_classification": {key: sorted(value) for key, value in by_classification.items()},
            "canonical_scenario_count": len(all_ids),
            "development_exposed_scenario_ids": sorted(development_exposed),
            "eligible_final_test_candidates": safe_unused,
        },
        "surfaces": surfaces,
    }
    payload["ledger_sha256"] = sha256_object(payload)
    return payload


def _chaos_targets(spec: dict[str, Any]) -> list[str]:
    selector = spec.get("selector") or {}
    labels = selector.get("labelSelectors") or {}
    namespaces = selector.get("namespaces") or []
    targets = [str(value) for value in labels.values() if str(value).strip()]
    if not targets and namespaces:
        targets = ["+".join(str(item) for item in namespaces)]

    target_selector = spec.get("target", {}).get("selector") or {}
    target_namespaces = target_selector.get("namespaces") or []
    if target_namespaces and namespaces:
        source = "+".join(str(item) for item in namespaces)
        destination = "+".join(str(item) for item in target_namespaces)
        targets.append(f"{source}->{destination}")
    return sorted(dict.fromkeys(targets))


def _application_target(doc: dict[str, Any]) -> tuple[list[str], str, dict[str, Any]]:
    import yaml

    targets: list[str] = []
    replicas: int | None = None
    kustomize = doc.get("spec", {}).get("source", {}).get("kustomize", {}) or {}
    for patch in kustomize.get("patches", []):
        target = patch.get("target", {}) or {}
        name = str(target.get("name", "")).strip()
        if name:
            targets.append(name)
        try:
            patch_body = yaml.safe_load(patch.get("patch", "")) or []
        except yaml.YAMLError:
            patch_body = []
        for operation in patch_body:
            if operation.get("op") == "replace" and operation.get("path") == "/spec/replicas":
                replicas = int(operation.get("value", 0))
    action = "argo_scale_to_zero" if replicas == 0 else "argo_application_patch"
    return sorted(set(targets)), action, {"replicas": replicas}


def _fault_records_from_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for document in documents:
        if not document:
            continue
        kind = str(document.get("kind", ""))
        spec = document.get("spec", {}) or {}
        if kind.endswith("Chaos"):
            records.append(
                {
                    "action": str(spec.get("action", "unspecified")),
                    "duration": str(spec.get("duration", "")),
                    "kind": kind,
                    "parameters": _normalise(
                        {
                            key: value
                            for key, value in spec.items()
                            if key
                            in {
                                "corrupt",
                                "delay",
                                "duplicate",
                                "loss",
                                "partition",
                                "patterns",
                                "stressors",
                            }
                        }
                    ),
                    "targets": _chaos_targets(spec),
                }
            )
        elif kind == "Application":
            targets, action, parameters = _application_target(document)
            records.append(
                {
                    "action": action,
                    "duration": "",
                    "kind": kind,
                    "parameters": parameters,
                    "targets": targets,
                }
            )
        elif kind == "Deployment":
            name = str(document.get("metadata", {}).get("name", "")).strip()
            replicas = int(spec.get("replicas", 0))
            records.append(
                {
                    "action": "deploy_legacy_replicas" if replicas else "deploy_legacy",
                    "duration": "",
                    "kind": kind,
                    "parameters": {"replicas": replicas},
                    "targets": [name] if name else [],
                }
            )
    return sorted(records, key=lambda item: canonical_json(item))


def _fault_records(path: Path) -> list[dict[str, Any]]:
    import yaml

    return _fault_records_from_documents(
        [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]
    )


def _services_from_alert(alert: dict[str, Any]) -> list[str]:
    services: set[str] = set()

    def visit(labels: Any) -> None:
        if not isinstance(labels, dict):
            return
        for key in ("service", "deployment", "pod"):
            value = str(labels.get(key, "")).strip()
            if value:
                services.add(value.removesuffix("-xxx"))

    visit(alert.get("commonLabels"))
    for item in alert.get("alerts", []) or []:
        if isinstance(item, dict):
            visit(item.get("labels"))
    return sorted(item for item in services if item)


def _alert_template(scenario_id: str) -> tuple[dict[str, Any], str]:
    # Imported lazily so catalogue inspection does not require agent runtime setup.
    from inference import ALERTS

    source_id = scenario_id.split("/", 1)[1]
    if source_id not in ALERTS:
        raise KeyError(f"frozen scenario has no model-visible alert template: {scenario_id}")
    return ALERTS[source_id], source_id


def _semantic_alert(alert: dict[str, Any]) -> dict[str, Any]:
    """Remove volatile timing/status and prose annotations before comparison."""
    value = _normalise(alert)
    value.pop("commonAnnotations", None)
    value.get("commonLabels", {}).pop("startsAt", None)
    value.get("commonLabels", {}).pop("status", None)
    for item in value.get("alerts", []) or []:
        item.pop("startsAt", None)
        item.pop("status", None)
        item.pop("annotations", None)
    return value


def _alert_tokens(value: Any) -> set[str]:
    words: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                visit(key)
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif item is not None:
            words.update(re.findall(r"[a-z0-9]+", str(item).lower()))

    visit(value)
    return words


def alert_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Return conservative Jaccard similarity of serialized alert tokens."""
    left_tokens = _alert_tokens(_semantic_alert(left))
    right_tokens = _alert_tokens(_semantic_alert(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / len(
        left_tokens.union(right_tokens)
    )


def near_duplicate_alerts(
    alert: dict[str, Any],
    catalog: dict[str, Any],
    *,
    threshold: float = 0.85,
) -> list[dict[str, Any]]:
    conflicts = []
    for entry in catalog_entries(catalog).values():
        similarity = alert_similarity(alert, entry["model_visible_alert"])
        if similarity >= threshold:
            conflicts.append(
                {
                    "reason": "alert_near_duplicate",
                    "scenario_id": entry["scenario_id"],
                    "similarity": round(similarity, 6),
                }
            )
    return sorted(conflicts, key=lambda item: (-item["similarity"], item["scenario_id"]))


def _coarse_fault(record: dict[str, Any]) -> dict[str, Any]:
    parameters = record.get("parameters", {}) or {}
    if record["kind"] == "StressChaos":
        coarse_parameters = {
            "stressor_types": sorted((parameters.get("stressors") or {}).keys())
        }
    elif record["kind"] == "NetworkChaos":
        coarse_parameters = {
            "effect_types": sorted(
                key
                for key in ("corrupt", "delay", "duplicate", "loss")
                if key in parameters
            )
        }
    elif record["kind"] == "DNSChaos":
        coarse_parameters = {"patterns": sorted(parameters.get("patterns", []))}
    else:
        coarse_parameters = {}
    return {
        "action": record.get("action"),
        "kind": record.get("kind"),
        "parameters": coarse_parameters,
        "targets": sorted(record.get("targets") or []),
    }


def _historical_source_id(path: Path) -> str | None:
    import yaml

    for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        value = ((document or {}).get("metadata", {}).get("labels", {}) or {}).get(
            "historical_incident"
        )
        if value:
            return str(value)
    return None


def scenario_relationships(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Return conservative near-duplicate relations among catalogue entries."""
    groups: dict[tuple[str, str], set[str]] = {}
    targets: dict[str, set[str]] = {}
    for entry in catalog_entries(catalog).values():
        scenario_id = entry["scenario_id"]
        for target in entry.get("injected_fault_services") or []:
            if str(target) and "->" not in str(target) and "+" not in str(target):
                targets.setdefault(str(target), set()).add(scenario_id)
        for fault_signature in entry.get("fault_signatures", []):
            groups.setdefault(("fault_signature", fault_signature), set()).add(scenario_id)
        if entry.get("source_incident_id"):
            groups.setdefault(
                ("source_incident", str(entry["source_incident_id"])), {scenario_id}
            ).add(scenario_id)
        if entry.get("alert_semantic_hash"):
            groups.setdefault(
                ("alert_semantics", str(entry["alert_semantic_hash"])), {scenario_id}
            ).add(scenario_id)
    for target, scenario_ids in targets.items():
        if len(scenario_ids) > 1:
            groups.setdefault(("single_target", target), set()).update(scenario_ids)
    return [
        {"key": key, "reason": reason, "scenario_ids": sorted(members)}
        for (reason, key), members in sorted(groups.items())
        if len(members) > 1
    ]


def cross_split_family_conflicts(split: dict[str, Any], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    splits = split["splits"]
    membership = {
        scenario_id: role
        for role in ("train", "validation", "final_test")
        for scenario_id in splits[role]
    }
    conflicts: list[dict[str, Any]] = []
    for relation in scenario_relationships(catalog):
        roles = sorted({membership[item] for item in relation["scenario_ids"]})
        if len(roles) > 1:
            conflicts.append({**relation, "roles": roles})
    return sorted(conflicts, key=canonical_json)


def validate_family_boundaries(split: dict[str, Any], catalog: dict[str, Any]) -> None:
    conflicts = cross_split_family_conflicts(split, catalog)
    if conflicts:
        raise ValueError(
            "family leakage across split boundaries: "
            + canonical_json(conflicts)
        )


def _verifier_predicates(scenario_id: str) -> dict[str, Any]:
    from agents.verifier import SCENARIO_VERIFICATION_SPECS

    try:
        specification = SCENARIO_VERIFICATION_SPECS[scenario_id]
    except KeyError as exc:
        raise KeyError(f"frozen scenario has no objective verifier predicate: {scenario_id}") from exc
    return _normalise(asdict(specification))


def build_catalog(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    from config.runtime import FROZEN_SCENARIOS

    manifests_dir = repo_root / "bench" / "chaos_manifests"
    filesystem_ids = {
        path.relative_to(manifests_dir).with_suffix("").as_posix()
        for tier in FROZEN_TIERS
        for path in (manifests_dir / tier).glob("*.yaml")
    }
    catalogue_ids = set(FROZEN_SCENARIOS)
    if filesystem_ids != catalogue_ids:
        raise ValueError(
            "frozen catalogue/filesystem mismatch: "
            f"missing={sorted(catalogue_ids - filesystem_ids)}, "
            f"extra={sorted(filesystem_ids - catalogue_ids)}"
        )

    entries: list[dict[str, Any]] = []
    for scenario_id in sorted(FROZEN_SCENARIOS):
        manifest_path = manifests_dir / f"{scenario_id}.yaml"
        alert, alert_source_id = _alert_template(scenario_id)
        faults = _fault_records(manifest_path)
        if not faults:
            raise ValueError(f"scenario has no derivable fault injection: {scenario_id}")
        coarse_faults = [_coarse_fault(fault) for fault in faults]
        fault_signatures = [sha256_object(fault) for fault in coarse_faults]
        semantic_alert = _semantic_alert(alert)
        targets = {
            target
            for fault in faults
            for target in fault["targets"]
            if target and "->" not in target and "+" not in target and "/" not in target
        }
        alert_services = _services_from_alert(alert)
        non_target_services = sorted(set(alert_services) - targets)
        red_herring_status = "NO_CANDIDATES" if not non_target_services else "NOT_REVIEWED"
        entries.append(
            {
                "alert_source_id": alert_source_id,
                "alert_semantic_hash": sha256_object(semantic_alert),
                "causal_template_id": "|".join(
                    sorted(f"{fault['kind']}:{fault['action']}" for fault in faults)
                ),
                "faults": faults,
                "fault_signature": sha256_object(coarse_faults),
                "fault_signatures": fault_signatures,
                "manifest_path": f"bench/chaos_manifests/{scenario_id}.yaml",
                "manifest_sha256": portable_sha256_file(manifest_path),
                "manifest_semantic_hash": sha256_object(faults),
                "model_visible_alert": _normalise(alert),
                "model_visible_alert_sha256": sha256_object(alert),
                "scenario_family_id": sha256_object(fault_signatures),
                "alert_observed_services": alert_services,
                "injected_fault_services": sorted(targets),
                "non_target_alert_services": non_target_services,
                "reviewed_red_herring_services": [],
                "red_herring_review_status": red_herring_status,
                "scenario_id": scenario_id,
                "source_incident_id": _historical_source_id(manifest_path),
                "target_signature": sha256_object(
                    sorted(
                        {
                            target
                            for fault in faults
                            for target in fault["targets"]
                        }
                    )
                ),
                "variant_parent_id": None,
                "success_predicates": _verifier_predicates(scenario_id),
                "tier": scenario_id.split("/", 1)[0],
            }
        )

    relative_sources = [
        "agents/verifier.py",
        "bench/scenario_contract.py",
        "bench/runner.py",
        "bench/unseen_candidate.py",
        "bench/g6_evidence.py",
        "bench/g5/exposure_ledger.json",
        "config/runtime.py",
        "inference.py",
    ]
    source_files = {
        relative: portable_sha256_file(repo_root / relative)
        for relative in sorted(relative_sources)
        if (repo_root / relative).exists()
    }
    payload = {
        "entries": entries,
        "schema_version": CATALOG_SCHEMA_VERSION,
        "scenario_count": len(entries),
        "source_files": source_files,
    }
    payload["catalog_sha256"] = sha256_object({key: value for key, value in payload.items()})
    return payload


def catalog_entries(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = {entry["scenario_id"]: entry for entry in catalog.get("entries", [])}
    if len(entries) != len(catalog.get("entries", [])):
        raise ValueError("catalog contains duplicate scenario_id entries")
    return entries


def validate_exposure_ledger(
    ledger: dict[str, Any],
    catalog: dict[str, Any],
    *,
    require_reproducible: bool = False,
) -> None:
    if ledger.get("schema_version") != EXPOSURE_SCHEMA_VERSION:
        raise ValueError("unsupported exposure ledger schema")
    expected_hash = ledger.get("ledger_sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("exposure ledger hash is missing or malformed")
    unsigned = {key: value for key, value in ledger.items() if key != "ledger_sha256"}
    if sha256_object(unsigned) != expected_hash:
        raise ValueError("exposure ledger hash mismatch")
    summary = ledger.get("summary", {})
    catalog_ids = set(catalog_entries(catalog))
    development = set(summary.get("development_exposed_scenario_ids", []))
    eligible = set(summary.get("eligible_final_test_candidates", []))
    unknown = development.union(eligible) - catalog_ids
    if unknown:
        raise ValueError(f"exposure ledger refers to unknown scenarios: {sorted(unknown)}")
    if development | eligible != catalog_ids:
        raise ValueError("exposure ledger does not account for every catalogue scenario")
    if development.intersection(eligible):
        raise ValueError("scenario is both development-exposed and final-test-eligible")
    if require_reproducible:
        rebuilt = build_exposure_ledger()
        if canonical_json(rebuilt) != canonical_json(ledger):
            raise ValueError("exposure ledger drift detected; regenerate from current sources")


def load_exposure_ledger(repo_root: Path = REPO_ROOT, *, verify: bool = True) -> dict[str, Any]:
    path = repo_root / "bench" / "g5" / "exposure_ledger.json"
    if not path.exists():
        raise RuntimeError(
            "EXPOSURE_LEDGER_NOT_FOUND: bench/g5/exposure_ledger.json is required"
        )
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read exposure ledger: {exc}") from exc
    if verify:
        catalog = build_catalog(repo_root)
        validate_exposure_ledger(ledger, catalog, require_reproducible=True)
    return ledger


def development_exposed_ids(catalog: dict[str, Any], ledger: dict[str, Any] | None = None) -> set[str]:
    selected = ledger or load_exposure_ledger()
    summary = selected["summary"]
    exposed = set(summary["development_exposed_scenario_ids"])
    unknown = exposed - set(catalog_entries(catalog))
    if unknown:
        raise ValueError(f"exposure ledger refers to unknown scenarios: {sorted(unknown)}")
    return exposed


def _stratified_quotas(count: int, train_fraction: float, validation_fraction: float) -> tuple[int, int]:
    exact_train = count * train_fraction
    exact_validation = count * validation_fraction
    base_train = int(exact_train)
    base_validation = int(exact_validation)
    remaining = count - base_train - base_validation
    remainders = sorted(
        (
            (exact_train - base_train, 0),
            (exact_validation - base_validation, 1),
        ),
        reverse=True,
    )
    additions = {0: 0, 1: 0}
    for index in range(min(remaining, 2)):
        additions[remainders[index][1]] += 1
    if remaining > 2:
        additions[0] += remaining - 2
    return base_train + additions[0], base_validation + additions[1]


def build_proposed_split(
    catalog: dict[str, Any],
    *,
    seed: str = "atlasops-g5-proposal-v1",
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    exposure_ledger: dict[str, Any] | None = None,
    repo_sha: str | None = None,
) -> dict[str, Any]:
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("split fractions must be between zero and one")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train and validation fractions must leave room for final test")

    entries = catalog_entries(catalog)
    by_tier: dict[str, list[str]] = {tier: [] for tier in FROZEN_TIERS}
    for scenario_id in entries:
        by_tier[scenario_id.split("/", 1)[0]].append(scenario_id)

    assignments: dict[str, str] = {}
    for tier in FROZEN_TIERS:
        ranked = sorted(
            by_tier[tier],
            key=lambda scenario_id: hashlib.sha256(
                f"{seed}:{scenario_id}".encode()
            ).hexdigest(),
        )
        train_count, validation_count = _stratified_quotas(
            len(ranked), train_fraction, validation_fraction
        )
        assignments.update({scenario_id: "train" for scenario_id in ranked[:train_count]})
        assignments.update(
            {scenario_id: "validation" for scenario_id in ranked[train_count : train_count + validation_count]}
        )

    ledger = exposure_ledger or load_exposure_ledger()
    exposed = development_exposed_ids(catalog, ledger)
    # No current ID is eligible for final test because both SFT defaults expose all.
    final_test: list[str] = []
    blockers = [
        {
            "code": "NO_UNEXPOSED_FINAL_TEST_CANDIDATES",
            "detail": (
                "All 28 frozen scenarios are exposed by current SFT-generation defaults. "
                "Add new isolated scenarios and regenerate the candidate before freeze."
            ),
        }
    ]
    splits = {
        "train": sorted(scenario_id for scenario_id, role in assignments.items() if role == "train"),
        "validation": sorted(
            scenario_id for scenario_id, role in assignments.items() if role == "validation"
        ),
        "final_test": final_test,
        "ineligible_final_test_development_exposed": sorted(exposed),
    }
    family_conflicts = cross_split_family_conflicts({"splits": splits}, catalog)
    if family_conflicts:
        blockers.append(
            {
                "code": "FAMILY_RELATIONS_CROSS_ASSIGNED_SPLITS",
                "count": len(family_conflicts),
                "detail": (
                    "Related scenarios share fault signatures, source incidents, or alert "
                    "semantics across proposed roles; regenerate only after a reviewed "
                    "family-aware assignment exists."
                ),
            }
        )
    return {
        "activation": {"active": False, "authorized_at": None, "frozen": False},
        "blockers": blockers,
        "catalog_sha256": catalog["catalog_sha256"],
        "exposure_ledger_sha256": ledger["ledger_sha256"],
        "family_relation_count": len(scenario_relationships(catalog)),
        "contract_provenance": {
            "algorithm_version": SPLIT_ALGORITHM_VERSION,
            "generator_version": SPLIT_GENERATOR_VERSION,
            "repo_sha": repo_sha or repository_head(),
        },
        "coverage": {
            "final_test_by_tier": {
                tier: sum(1 for item in final_test if item.startswith(f"{tier}/"))
                for tier in FROZEN_TIERS
            },
            "final_test_causal_templates": sorted(
                {
                    catalog_entries(catalog)[item]["causal_template_id"]
                    for item in final_test
                }
            ),
        },
        "gate_prerequisites": {
            "G4": "OPEN",
            "explicit_freeze_authorization": False,
            "no_seed_retry_policy": "One predeclared seed per reviewed candidate generation.",
        },
        "ready_for_freeze": False,
        "schema_version": SPLIT_SCHEMA_VERSION,
        "seed": seed,
        "split_fractions": {
            "train": train_fraction,
            "validation": validation_fraction,
            "final_test": round(max(0.0, 1.0 - train_fraction - validation_fraction), 12),
        },
        "splits": splits,
        "status": "PROPOSED_BLOCKED_NO_FINAL_TEST",
        "usage_policy": {
            "consumed_by_runtime": False,
            "required_role_gate": "Only bench/g5/split.frozen.json may select runtime scenarios.",
        },
    }


def validate_split(
    split: dict[str, Any],
    catalog: dict[str, Any],
    *,
    require_ready: bool = False,
    exposure_ledger: dict[str, Any] | None = None,
) -> None:
    if split.get("schema_version") != SPLIT_SCHEMA_VERSION:
        raise ValueError("unsupported split schema")
    if split.get("catalog_sha256") != catalog.get("catalog_sha256"):
        raise ValueError("split catalog digest mismatch")
    provenance = split.get("contract_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("split contract provenance is missing")
    if not re.fullmatch(r"[0-9a-f]{40}", str(provenance.get("repo_sha", ""))):
        raise ValueError("split repository SHA is missing or malformed")
    if provenance.get("algorithm_version") != SPLIT_ALGORITHM_VERSION or provenance.get(
        "generator_version"
    ) != SPLIT_GENERATOR_VERSION:
        raise ValueError("split generator/algorithm version mismatch")

    status = split.get("status")
    if status not in {"PROPOSED_READY", "FROZEN"} and not (
        isinstance(status, str) and status.startswith("PROPOSED_BLOCKED")
    ):
        raise ValueError(f"unsupported split status: {status}")

    activation = split.get("activation")
    if not isinstance(activation, dict):
        raise ValueError("split activation is missing or not a dict")

    if status == "FROZEN":
        if activation.get("active") is not True or activation.get("frozen") is not True:
            raise ValueError("frozen split activation must have active=True and frozen=True")
        auth_keys = {"authorized_at", "authorized_by", "authorization_ref"}
        if set(activation.keys()) != auth_keys | {"active", "frozen"}:
            raise ValueError("frozen split activation contains invalid or missing keys")
        for k in auth_keys:
            val = activation.get(k)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"frozen split authorization field '{k}' must be a non-empty string")
    else:
        expected_activation = {"active": False, "authorized_at": None, "frozen": False}
        if activation != expected_activation:
            raise ValueError("inactive/proposed split activation state is inconsistent")

    splits = split.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "validation", "final_test", "ineligible_final_test_development_exposed"}:
        raise ValueError("split roles are incomplete")
    role_lists = {
        role: list(values)
        for role, values in splits.items()
        if role != "ineligible_final_test_development_exposed"
    }
    seen: set[str] = set()
    for role, values in role_lists.items():
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate scenario in {role}")
        overlap = seen.intersection(values)
        if overlap:
            raise ValueError(f"scenario appears in multiple roles: {sorted(overlap)}")
        seen.update(values)
    expected_ids = set(catalog_entries(catalog))
    assigned = set(role_lists["train"]) | set(role_lists["validation"]) | set(role_lists["final_test"])
    ineligible = set(splits["ineligible_final_test_development_exposed"])
    if assigned != expected_ids:
        raise ValueError(
            "split assignment mismatch: "
            f"missing={sorted(expected_ids - assigned)}, extra={sorted(assigned - expected_ids)}"
        )

    expected_ledger_hash = split.get("exposure_ledger_sha256")
    if not isinstance(expected_ledger_hash, str):
        raise ValueError("split exposure ledger hash is missing")
    selected_ledger = exposure_ledger or load_exposure_ledger()
    validate_exposure_ledger(selected_ledger, catalog)
    if selected_ledger["ledger_sha256"] != expected_ledger_hash:
        raise ValueError("split exposure ledger digest mismatch")
    exposed = development_exposed_ids(catalog, selected_ledger)
    leaked = set(role_lists["final_test"]).intersection(exposed)
    if leaked:
        raise ValueError(f"final-test leakage: development-exposed scenarios present: {sorted(leaked)}")
    declared_ineligible = ineligible == exposed
    if not declared_ineligible:
        raise ValueError("ineligible-final-test ledger does not match the exposure ledger")

    gates = split.get("gate_prerequisites")
    if not isinstance(gates, dict):
        raise ValueError("split gate prerequisites are missing")
    if status == "FROZEN":
        if gates.get("G4") != "PASSED" or gates.get("explicit_freeze_authorization") is not True:
            raise ValueError("frozen split lacks G4 or explicit authorization prerequisites")
    elif gates.get("G4") != "OPEN" or gates.get("explicit_freeze_authorization") is not False:
        raise ValueError("inactive split gate prerequisites are inconsistent")

    if require_ready:
        if status != "PROPOSED_READY":
            raise ValueError(f"split is not PROPOSED_READY: {status}")

    if status in {"PROPOSED_READY", "FROZEN"}:
        if not role_lists["final_test"]:
            raise ValueError("freezable/frozen split must have final-test scenarios")
        if split.get("blockers"):
            raise ValueError("freezable/frozen split still has blockers")
        if split.get("ready_for_freeze") is not True:
            raise ValueError("ready_for_freeze is not true")
        coverage = split.get("coverage", {}).get("final_test_by_tier", {})
        if any(int(coverage.get(tier, 0)) < 1 for tier in FROZEN_TIERS):
            raise ValueError("ready/frozen split must cover every frozen tier in final test")
        unresolved_red_herrings = sorted(
            entry["scenario_id"]
            for entry in catalog_entries(catalog).values()
            if entry.get("red_herring_review_status") == "NOT_REVIEWED"
        )
        if unresolved_red_herrings:
            raise ValueError(
                "red-herring classification requires operator review for: "
                + ", ".join(unresolved_red_herrings)
            )
        validate_family_boundaries(split, catalog)
    else:
        if split.get("ready_for_freeze") is not False:
            raise ValueError("blocked split cannot be ready_for_freeze")
        if not split.get("blockers"):
            raise ValueError("blocked split must declare blockers")


def freeze_split(
    candidate_path: Path,
    frozen_path: Path,
    *,
    authorization: dict[str, Any],
    catalog_path: Path = CATALOG_PATH,
    expected_repo_sha: str | None = None,
    exposure_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    split = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidate_root = candidate_path.resolve().parent.parent.parent
    if exposure_ledger is not None:
        ledger = exposure_ledger
    elif (catalog_path.parent / "exposure_ledger.json").exists():
        ledger = json.loads((catalog_path.parent / "exposure_ledger.json").read_text(encoding="utf-8"))
    else:
        ledger = load_exposure_ledger(candidate_root, verify=False)
    validate_split(split, catalog, require_ready=True, exposure_ledger=ledger)
    if frozen_path.exists():
        raise FileExistsError(f"refusing to replace an active frozen split: {frozen_path}")

    required_keys = {"authorized_at", "authorized_by", "authorization_ref"}
    if not required_keys.issubset(authorization) or any(
        not isinstance(authorization.get(key), str) or not str(authorization[key]).strip()
        for key in required_keys
    ):
        raise ValueError("freeze authorization record is incomplete")
    if authorization.get("g4_passed") is not True:
        raise ValueError("G4 must be passed before split freeze")
    source_repo_sha = str(split["contract_provenance"]["repo_sha"])
    observed_repo_sha = repository_head(candidate_root)
    if expected_repo_sha is not None and expected_repo_sha != observed_repo_sha:
        raise RuntimeError(
            "expected repository SHA differs from the observed clean HEAD; "
            "refusing to attest a different revision"
        )
    dirty_sources = _unexpected_dirty_proposal_sources(candidate_root)
    if dirty_sources:
        raise RuntimeError(
            "split worktree is not clean for non-derived sources: "
            + ", ".join(dirty_sources)
        )
    drifted_sources = _non_derived_source_drift_since(
        candidate_root,
        source_repo_sha,
        observed_repo_sha,
    )
    if drifted_sources:
        raise RuntimeError(
            "split drift detected since the recorded proposal source: "
            + ", ".join(drifted_sources)
        )

    frozen = dict(split)
    frozen["activation"] = {
        "active": True,
        "authorized_at": str(authorization["authorized_at"]),
        "authorized_by": str(authorization["authorized_by"]),
        "authorization_ref": str(authorization["authorization_ref"]),
        "frozen": True,
    }
    frozen["status"] = "FROZEN"
    frozen["gate_prerequisites"] = {
        **split["gate_prerequisites"],
        "G4": "PASSED",
        "explicit_freeze_authorization": True,
    }

    # Validate prospective frozen object in memory BEFORE persisting to disk
    validate_split(frozen, catalog, exposure_ledger=ledger)

    if frozen_path.exists():
        raise FileExistsError(f"refusing to replace an active frozen split: {frozen_path}")

    write_json_atomically(frozen_path, frozen)
    return frozen


def load_active_split(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    frozen_path = repo_root / "bench" / "g5" / "split.frozen.json"
    if not frozen_path.exists():
        raise RuntimeError(
            "G5_SPLIT_NOT_ACTIVE: no bench/g5/split.frozen.json; refusing to select scenarios"
        )
    split = json.loads(frozen_path.read_text(encoding="utf-8"))
    catalog = json.loads((repo_root / "bench" / "g5" / "scenario_catalog.json").read_text(encoding="utf-8"))
    validate_split(
        split,
        catalog,
        exposure_ledger=load_exposure_ledger(repo_root, verify=False),
    )
    if split.get("status") != "FROZEN" or split.get("activation", {}).get("active") is not True:
        raise RuntimeError("G5 split is present but not active")
    return split


def allowed_scenario_ids(role: str, repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    if role not in ROLE_IDS_KEY:
        raise ValueError(f"unknown scenario consumer role: {role}")
    split = load_active_split(repo_root)
    return tuple(sorted(split["splits"][ROLE_IDS_KEY[role]]))


def development_scenario_ids(consumer: str) -> tuple[str, ...]:
    """Return the pre-G5 development subset through one named-policy boundary."""
    if consumer not in DEVELOPMENT_CONSUMERS:
        raise ValueError(f"unknown development consumer: {consumer}")
    import config.runtime as runtime

    if consumer == "leaderboard_subset":
        return tuple(sorted({scenario for scenario, _tier in runtime.LEADERBOARD_SCENARIOS}))
    mapping = {
        "evaluation_subset": runtime.EVAL_SCENARIOS_BY_TIER,
        "grpo_curriculum": runtime.SCENARIOS_BY_TIER,
    }
    return tuple(sorted({
        scenario
        for scenarios in mapping[consumer].values()
        for scenario in scenarios
    }))


def canonical_split_status(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    frozen_path = repo_root / "bench" / "g5" / "split.frozen.json"
    if not frozen_path.exists():
        return {"active": False, "status": "NOT_ACTIVE"}
    split = load_active_split(repo_root)
    return {
        "active": True,
        "catalog_sha256": split["catalog_sha256"],
        "exposure_ledger_sha256": split["exposure_ledger_sha256"],
        "status": split["status"],
    }


def assert_consumer_may_use_scenario(
    consumer: str,
    scenario_id: str,
    repo_root: Path = REPO_ROOT,
) -> None:
    """Fail closed before a demo/development consumer reaches a declared holdout."""
    if consumer == "stage4_special":
        if scenario_id != _STAGE4_SPECIAL_SCENARIO:
            raise ValueError("Stage4 special consumer is restricted to the golden scenario")
        return
    if consumer in ROLE_IDS_KEY:
        allowed = set(allowed_scenario_ids(ROLE_IDS_KEY[consumer], repo_root))
        if scenario_id not in allowed:
            raise ValueError(f"scenario is outside active {consumer} population: {scenario_id}")
        return
    if consumer == "demo_development":
        catalog_path = repo_root / "bench" / "g5" / "scenario_catalog.json"
        if catalog_path.exists():
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            valid_catalog_ids = set(catalog_entries(catalog))
        else:
            from config.runtime import FROZEN_SCENARIOS

            valid_catalog_ids = set(FROZEN_SCENARIOS)

        if scenario_id not in valid_catalog_ids:
            raise ValueError(f"scenario is outside the frozen scenario catalogue: {scenario_id}")
        try:
            split = load_active_split(repo_root)
        except RuntimeError as exc:
            if "G5_SPLIT_NOT_ACTIVE" not in str(exc):
                raise
            return
        if scenario_id in set(split["splits"]["final_test"]):
            raise ValueError("demo/development consumer cannot use final-test scenario")
        return
    if consumer in DEVELOPMENT_CONSUMERS:
        if scenario_id not in development_scenario_ids(consumer):
            raise ValueError(f"scenario is outside {consumer}: {scenario_id}")
        assert_consumer_may_use_scenario("demo_development", scenario_id, repo_root)
        return
    raise ValueError(f"unknown canonical scenario consumer: {consumer}")


def _write_catalog(args: argparse.Namespace) -> int:
    write_json_atomically(Path(args.output), build_catalog())
    print(f"wrote catalog: {args.output}")
    return 0


def _non_derived_source_drift_since(
    repo_root: Path,
    source_sha: str,
    head_sha: str,
) -> list[str]:
    """Return non-derived paths changed after the proposal's source revision."""
    completed_ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", source_sha, head_sha],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed_ancestry.returncode != 0:
        return ["<proposal source is not an ancestor of the observed HEAD>"]

    exclusions = [
        f":(exclude){path}"
        for path in sorted(_DERIVED_CONTRACT_ARTIFACT_PATHS)
    ]
    completed_diff = subprocess.run(
        ["git", "diff", "--name-only", source_sha, head_sha, "--", ".", *exclusions],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed_diff.returncode != 0:
        return ["<git diff failed>"]

    unexpected = []
    for line in completed_diff.stdout.splitlines():
        path = line.replace("\\", "/").strip('"')
        if path and path not in _DERIVED_CONTRACT_ARTIFACT_PATHS:
            unexpected.append(path)
    return sorted(set(unexpected))


def _unexpected_dirty_proposal_sources(repo_root: Path = REPO_ROOT) -> list[str]:
    """Return non-artifact paths that make a default repo_sha untrustworthy."""
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return ["<git status failed>"]

    unexpected = []
    for line in completed.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.replace("\\", "/").strip('"')
        if path not in _DERIVED_CONTRACT_ARTIFACT_PATHS:
            unexpected.append(path)
    return sorted(set(unexpected))


def _write_plan(args: argparse.Namespace) -> int:
    dirty_sources = _unexpected_dirty_proposal_sources()
    if dirty_sources and not args.repo_sha:
        raise ValueError(
            "proposal repo_sha would be ambiguous because non-derived sources are "
            f"dirty: {dirty_sources}"
        )
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    plan = build_proposed_split(
        catalog,
        seed=args.seed,
        train_fraction=args.train_fraction,
        validation_fraction=args.validation_fraction,
        repo_sha=args.repo_sha or None,
    )
    write_json_atomically(Path(args.output), plan)
    print(f"wrote inert split proposal ({plan['status']}): {args.output}")
    return 0


def _write_exposure(args: argparse.Namespace) -> int:
    write_json_atomically(Path(args.output), build_exposure_ledger())
    print(f"wrote exposure ledger: {args.output}")
    return 0


def _freeze_command(args: argparse.Namespace) -> int:
    if not args.g4_passed:
        raise ValueError("freeze requires --g4-passed as an explicit operator attestation")
    frozen = freeze_split(
        Path(args.candidate),
        Path(args.frozen_output),
        authorization={
            "authorized_at": args.authorized_at,
            "authorized_by": args.authorized_by,
            "authorization_ref": args.authorization_ref,
            "g4_passed": True,
        },
        catalog_path=Path(args.catalog),
        expected_repo_sha=args.repo_sha or None,
    )
    print(f"froze active split: {args.frozen_output} ({len(frozen['splits']['final_test'])} final-test scenarios)")
    return 0


def _verify_command(args: argparse.Namespace) -> int:
    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    rebuilt = build_catalog()
    if canonical_json(rebuilt) != canonical_json(catalog):
        raise ValueError("catalog does not reproduce byte-for-byte from current sources")
    load_exposure_ledger(verify=True)
    target = json.loads(Path(args.path).read_text(encoding="utf-8"))
    validate_split(target, catalog, require_ready=target.get("status") == "PROPOSED_READY")
    print(f"verified: {args.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    catalog_parser = subparsers.add_parser("catalog", help="build deterministic catalog")
    catalog_parser.add_argument("--output", default=str(CATALOG_PATH))
    catalog_parser.set_defaults(handler=_write_catalog)

    plan_parser = subparsers.add_parser("plan", help="build an inactive split proposal")
    plan_parser.add_argument("--catalog", default=str(CATALOG_PATH))
    plan_parser.add_argument("--output", default=str(SPLIT_PROPOSED_PATH))
    plan_parser.add_argument("--seed", default="atlasops-g5-proposal-v1")
    plan_parser.add_argument("--train-fraction", type=float, default=0.60)
    plan_parser.add_argument("--validation-fraction", type=float, default=0.20)
    plan_parser.add_argument("--repo-sha", default="", help="exact clean source revision")
    plan_parser.set_defaults(handler=_write_plan)

    exposure_parser = subparsers.add_parser("exposure", help="build deterministic exposure ledger")
    exposure_parser.add_argument("--output", default=str(EXPOSURE_LEDGER_PATH))
    exposure_parser.set_defaults(handler=_write_exposure)

    freeze_parser = subparsers.add_parser("freeze", help="atomically activate a ready candidate")
    freeze_parser.add_argument("--candidate", default=str(SPLIT_PROPOSED_PATH))
    freeze_parser.add_argument("--frozen-output", default=str(SPLIT_FROZEN_PATH))
    freeze_parser.add_argument("--catalog", default=str(CATALOG_PATH))
    freeze_parser.add_argument("--authorized-at", required=True)
    freeze_parser.add_argument("--authorized-by", required=True)
    freeze_parser.add_argument("--authorization-ref", required=True)
    freeze_parser.add_argument("--g4-passed", action="store_true")
    freeze_parser.add_argument("--repo-sha", default="", help="expected clean HEAD")
    freeze_parser.set_defaults(handler=_freeze_command)

    verify_parser = subparsers.add_parser("verify", help="reproduce and verify catalog/split")
    verify_parser.add_argument("--catalog", default=str(CATALOG_PATH))
    verify_parser.add_argument("--path", required=True)
    verify_parser.set_defaults(handler=_verify_command)

    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
