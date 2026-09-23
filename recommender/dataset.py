"""AtlasOps Incident-Runbook Interaction Dataset Builder (Gate G10).

Builds scenario-derived synthetic interactions, not historical user feedback,
with ground-truth relevance annotations, split partitioning, and manifest persistence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.scenario_catalog import SCENARIO_CATALOG, ScenarioMetadata
from config.splits import TEST_SPLIT, TRAIN_SPLIT, VAL_SPLIT
from recommender.runbook_catalog import RUNBOOK_CATALOG

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rs_dataset")

DATA_DIR = Path("data")
EVIDENCE_DIR = Path("artifacts/evidence/stage10")
RUNTIME_QUERY_FIELDS = ("alertname", "affected_services", "symptoms_text")

@dataclass
class IncidentInteraction:
    interaction_id: str
    incident_id: str
    scenario_id: str
    split: str
    tier: str
    alertname: str
    affected_services: list[str]
    symptoms_text: str
    relevant_runbook_id: str
    rating: float  # 1.0 = optimal resolution, 0.0 = irrelevant


def _cause_runbooks(text: str) -> set[str]:
    """Return runbooks supported by explicit cause terms in offline metadata."""
    cause = text.casefold()
    matches: set[str] = set()

    if any(term in cause for term in ("memory", "oom", "ram")):
        matches.add("RB-POD-OOM")
    if any(term in cause for term in ("cpu", "cfs quota", "throttl")):
        matches.add("RB-CPU-THROTTLE")
    if any(
        term in cause
        for term in ("dnschaos", "dns outage", "dns failure", "dns resolution", "dns query failure", "name resolution", "name-resolv")
    ):
        matches.add("RB-DNS-FAIL")
    if any(term in cause for term in ("corrupt", "checksum")):
        matches.add("RB-NET-CORRUPT")
    if any(term in cause for term in ("latency", "jitter", "packet delay", "network delay")):
        matches.add("RB-NET-DELAY")
    if any(
        term in cause
        for term in ("packet loss", "network partition", "partition", "packet drop", "bgp", "routing")
    ):
        matches.add("RB-NET-LOSS")
    if any(
        term in cause
        for term in ("filesystem error", "full condition", "disk full", "disk pressure", "write exhaustion", "no space")
    ):
        matches.add("RB-DISK-FILL")
    if any(term in cause for term in ("i/o delay", "io delay", "i/o latency", "io latency", "iowait")):
        matches.add("RB-IO-DELAY")
    if any(
        term in cause
        for term in ("pod kill", "pod failure", "container restart", "crashloop", "leader kill", "legacy code", "buggy code")
    ):
        matches.add("RB-POD-CRASH")
    if any(term in cause for term in ("replicas to 0", "replicas to zero", "scaled to 0", "scaling deployment to 0", "capacity removal")):
        matches.add("RB-SCALE-OUT")

    if any(term in cause for term in ("query storm", "cascading failure", "dependency chain")):
        matches.add("RB-CASCADE-HEAL")

    return matches


def _has_cascade_evidence(meta: ScenarioMetadata) -> bool:
    text = f"{meta.expected_root_cause} {meta.description}".casefold()
    return any(
        term in text
        for term in ("cascade", "query storm", "dependency chain", "chain reaction", "failover")
    )


def resolve_runbook_for_scenario(scenario_id: str, meta: ScenarioMetadata) -> str:
    """Derive an offline synthetic target from cause metadata, never the scenario ID.

    The expected root cause is used only to create the benchmark label. It is
    deliberately omitted from ``IncidentInteraction`` runtime-facing features.
    """
    if meta.tier == "cascade":
        return "RB-CASCADE-HEAL"
    if meta.tier == "multi_fault":
        if _has_cascade_evidence(meta):
            return "RB-CASCADE-HEAL"
        raise ValueError(
            f"No single relevant runbook is supported for multi-fault scenario {scenario_id!r}."
        )

    cause_matches = _cause_runbooks(meta.expected_root_cause)
    all_cause_text = f"{meta.expected_root_cause} {meta.description}".casefold()
    if "packet duplication" in all_cause_text:
        if _has_cascade_evidence(meta):
            return "RB-CASCADE-HEAL"
        raise ValueError(
            f"No catalog runbook covers packet duplication for scenario {scenario_id!r}."
        )
    if "RB-CASCADE-HEAL" in cause_matches:
        return "RB-CASCADE-HEAL"
    if len(cause_matches) == 1:
        return next(iter(cause_matches))
    if len(cause_matches) > 1:
        if _has_cascade_evidence(meta):
            return "RB-CASCADE-HEAL"
        raise ValueError(
            f"No single relevant runbook is supported for multiple cause classes in scenario {scenario_id!r}."
        )

    # Descriptions are a secondary offline source when the root-cause field is
    # broad. Chaos kind alone is not enough to distinguish (for example) CPU
    # from memory StressChaos or loss from latency NetworkChaos.
    description_matches = _cause_runbooks(meta.description)
    if "RB-CASCADE-HEAL" in description_matches:
        return "RB-CASCADE-HEAL"
    if len(description_matches) == 1:
        return next(iter(description_matches))
    if len(description_matches) > 1:
        if _has_cascade_evidence(meta):
            return "RB-CASCADE-HEAL"
        raise ValueError(
            f"No single relevant runbook is supported for multiple cause classes in scenario {scenario_id!r}."
        )

    raise ValueError(
        f"No catalog runbook can be derived from offline cause metadata for scenario {scenario_id!r}."
    )


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_incident_interactions(
    scenarios: dict[str, ScenarioMetadata] | None = None,
    output_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Build synthetic benchmark interactions, excluding unsupported labels."""
    catalog = SCENARIO_CATALOG if scenarios is None else scenarios
    out_file = output_path or (DATA_DIR / "rs_incident_interactions.jsonl")
    default_output = DATA_DIR / "rs_incident_interactions.jsonl"
    canonical_manifest = EVIDENCE_DIR / "rs_dataset_manifest.json"
    if out_file.resolve() == canonical_manifest.resolve():
        raise ValueError("The Stage 10 manifest cannot be used as a dataset output path.")
    manifest_path = (
        canonical_manifest
        if out_file.resolve() == default_output.resolve()
        else out_file.with_suffix(".manifest.json")
    )

    split_membership: dict[str, str] = {}
    for split_name, split_ids in (
        ("train", TRAIN_SPLIT),
        ("val", VAL_SPLIT),
        ("test", TEST_SPLIT),
    ):
        for scenario_id in split_ids:
            if scenario_id in split_membership:
                raise ValueError(f"Scenario {scenario_id!r} occurs in more than one frozen split.")
            split_membership[scenario_id] = split_name

    unknown_scenarios = sorted(set(catalog) - set(split_membership))
    if unknown_scenarios:
        raise ValueError(
            "Dataset generation only accepts scenarios in the frozen Train/Val/Test splits; "
            f"unknown IDs: {unknown_scenarios!r}."
        )

    interactions: list[IncidentInteraction] = []
    excluded_scenarios: dict[str, str] = {}
    for sid, meta in sorted(catalog.items()):
        if sid != meta.scenario_id:
            raise ValueError(
                f"Scenario key {sid!r} does not match metadata identity {meta.scenario_id!r}."
            )
        split_name = split_membership[sid]
        try:
            rb_id = resolve_runbook_for_scenario(sid, meta)
        except ValueError as exc:
            excluded_scenarios[sid] = str(exc)
            continue

        alert_observation = f"Alert {meta.expected_alert} firing" if meta.expected_alert else "Alert observed"
        services_text = ", ".join(meta.target_services)
        service_observation = f" on service(s) {services_text}" if services_text else ""
        symptoms = f"{alert_observation}{service_observation}."

        interaction = IncidentInteraction(
            interaction_id=f"int-{sid.replace('/', '-')}",
            incident_id=f"inc-{sid.replace('/', '-')}",
            scenario_id=sid,
            split=split_name,
            tier=meta.tier,
            alertname=meta.expected_alert,
            affected_services=list(meta.target_services),
            symptoms_text=symptoms,
            relevant_runbook_id=rb_id,
            rating=1.0,
        )
        interactions.append(interaction)

    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Write JSONL
    with out_file.open("w", encoding="utf-8") as f:
        for item in interactions:
            f.write(json.dumps(asdict(item)) + "\n")

    # Compute manifest statistics
    raw_bytes = out_file.read_bytes()
    canonical_sha256 = hashlib.sha256(raw_bytes.replace(b"\r\n", b"\n")).hexdigest()

    split_counts = {
        "train": sum(1 for i in interactions if i.split == "train"),
        "val": sum(1 for i in interactions if i.split == "val"),
        "test": sum(1 for i in interactions if i.split == "test"),
    }
    rb_counts = {}
    for i in interactions:
        rb_counts[i.relevant_runbook_id] = rb_counts.get(i.relevant_runbook_id, 0) + 1

    source_catalog = [
        {"scenario_id": sid, "metadata": asdict(meta)}
        for sid, meta in sorted(catalog.items())
    ]
    frozen_split_ids = {
        "train": sorted(TRAIN_SPLIT),
        "val": sorted(VAL_SPLIT),
        "test": sorted(TEST_SPLIT),
    }
    included_split_ids = {
        split_name: [item.scenario_id for item in interactions if item.split == split_name]
        for split_name in ("train", "val", "test")
    }

    manifest = {
        "dataset_name": "atlasops_rs_incident_interactions",
        "data_origin": "scenario_derived_synthetic_benchmark",
        "historical_user_feedback": False,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "runtime_query_fields": list(RUNTIME_QUERY_FIELDS),
        "runtime_feature_provenance": (
            "Synthetic fixture values from expected_alert and target_services; tier is "
            "scenario/split metadata only, and expected_root_cause is used only for the "
            "offline benchmark label."
        ),
        "offline_label_field": "relevant_runbook_id",
        "offline_label_policy": (
            "Generic cause-semantic mapping from scenario metadata; unsupported causes are excluded."
        ),
        "generator_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runbook_catalog_source_sha256": hashlib.sha256(
            Path(__file__).with_name("runbook_catalog.py").read_bytes()
        ).hexdigest(),
        "source_catalog_sha256": _canonical_sha256(source_catalog),
        "source_manifest_sha256_by_scenario": {
            sid: meta.manifest_sha256 for sid, meta in sorted(catalog.items())
        },
        "source_scenario_ids": sorted(catalog),
        "source_scenario_count": len(catalog),
        "frozen_split_sha256": _canonical_sha256(frozen_split_ids),
        "frozen_split_scenario_ids": frozen_split_ids,
        "source_split_scenario_ids": {
            split_name: sorted(sid for sid in catalog if split_membership[sid] == split_name)
            for split_name in ("train", "val", "test")
        },
        "included_split_scenario_ids": included_split_ids,
        "excluded_scenarios": excluded_scenarios,
        "file": str(out_file.as_posix()),
        "canonical_lf_sha256": canonical_sha256,
        "total_interactions": len(interactions),
        "excluded_scenario_count": len(excluded_scenarios),
        "split_distribution": split_counts,
        "runbook_distribution": rb_counts,
        "unique_runbooks_covered": len(rb_counts),
        "total_catalog_runbooks": len(RUNBOOK_CATALOG),
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    log.info("Built %d incident-runbook interactions in %s (SHA-256: %s)",
             len(interactions), out_file, canonical_sha256)
    return out_file, manifest


def load_interactions(path: Path | None = None) -> list[IncidentInteraction]:
    """Load interaction records from disk, generating if missing."""
    p = path or (DATA_DIR / "rs_incident_interactions.jsonl")
    if not p.exists():
        build_incident_interactions(output_path=p)
    
    records = []
    for line in p.read_text(encoding="utf-8").strip().splitlines():
        d = json.loads(line)
        records.append(IncidentInteraction(**d))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps Incident-Runbook Dataset Builder")
    parser.add_argument("--output", default="data/rs_incident_interactions.jsonl", help="Output path")
    args = parser.parse_args()
    build_incident_interactions(output_path=Path(args.output))


if __name__ == "__main__":
    main()
