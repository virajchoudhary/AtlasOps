"""AtlasOps Incident-Runbook Interaction Dataset Builder (Gate G10).

Builds historical incident-runbook interaction logs across the 28 benchmark scenarios,
with ground-truth relevance annotations, split partitioning, and manifest persistence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from config.scenario_catalog import SCENARIO_CATALOG, ScenarioMetadata
from config.splits import TEST_SPLIT, TRAIN_SPLIT, VAL_SPLIT, get_split
from recommender.runbook_catalog import RUNBOOK_CATALOG, Runbook

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rs_dataset")

DATA_DIR = Path("data")
EVIDENCE_DIR = Path("artifacts/evidence/stage10")

# Mapping of chaos kind / scenario pattern to canonical relevant runbook ID
SCENARIO_RUNBOOK_MAP: dict[str, str] = {
    "pod_memory_limit": "RB-POD-OOM",
    "pod_oom_frontend": "RB-POD-OOM",
    "pod_crash_loop": "RB-POD-CRASH",
    "pod_failure": "RB-POD-CRASH",
    "pod_kill_payment": "RB-POD-CRASH",
    "pod_cpu_burn": "RB-CPU-THROTTLE",
    "pod_cpu_hog": "RB-CPU-THROTTLE",
    "cart_cpu_hog": "RB-CPU-THROTTLE",
    "network_loss": "RB-NET-LOSS",
    "network_loss_checkout": "RB-NET-LOSS",
    "network_delay": "RB-NET-DELAY",
    "network_delay_cart": "RB-NET-DELAY",
    "network_corrupt": "RB-NET-CORRUPT",
    "network_duplicate": "RB-NET-DELAY",
    "dns_failure": "RB-DNS-FAIL",
    "dns_timeout_product": "RB-DNS-FAIL",
    "dns_resolve_error": "RB-DNS-FAIL",
    "disk_fill": "RB-DISK-FILL",
    "disk_io_delay": "RB-IO-DELAY",
    "http_500_cart": "RB-HTTP-5XX",
    "http_503_frontend": "RB-HTTP-5XX",
    "cart_checkout_cascade": "RB-CASCADE-HEAL",
    "frontend_payment_cascade": "RB-CASCADE-HEAL",
    "multi_fault_pod_network": "RB-CASCADE-HEAL",
    "named_replay_black_friday_cpu": "RB-SCALE-OUT",
    "named_replay_auth_dns_outage": "RB-DNS-FAIL",
    "named_replay_db_connection_leak": "RB-POD-CRASH",
    "named_replay_payment_partition": "RB-NET-LOSS",
}


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


def resolve_runbook_for_scenario(scenario_id: str, meta: ScenarioMetadata) -> str:
    """Resolve the ground truth relevant runbook for a given scenario."""
    slug = scenario_id.split("/", 1)[-1]
    if slug in SCENARIO_RUNBOOK_MAP:
        return SCENARIO_RUNBOOK_MAP[slug]

    # Fallback to category / chaos kind matching
    chaos_kind = meta.chaos_kinds[0].lower() if meta.chaos_kinds else ""
    if "memory" in chaos_kind or "oom" in chaos_kind:
        return "RB-POD-OOM"
    elif "cpu" in chaos_kind:
        return "RB-CPU-THROTTLE"
    elif "loss" in chaos_kind or "corrupt" in chaos_kind:
        return "RB-NET-LOSS"
    elif "delay" in chaos_kind or "duplicate" in chaos_kind:
        return "RB-NET-DELAY"
    elif "dns" in chaos_kind:
        return "RB-DNS-FAIL"
    elif "disk" in chaos_kind:
        return "RB-DISK-FILL"
    elif "io" in chaos_kind:
        return "RB-IO-DELAY"
    elif meta.tier == "cascade":
        return "RB-CASCADE-HEAL"
    return "RB-POD-CRASH"


def build_incident_interactions(
    scenarios: dict[str, ScenarioMetadata] | None = None,
    output_path: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Build historical incident-runbook interactions for all 28 scenarios across splits."""
    catalog = scenarios or SCENARIO_CATALOG
    out_file = output_path or (DATA_DIR / "rs_incident_interactions.jsonl")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    interactions: list[IncidentInteraction] = []
    
    # Track split memberships
    train_ids = set(TRAIN_SPLIT)
    val_ids = set(VAL_SPLIT)
    test_ids = set(TEST_SPLIT)

    for sid, meta in sorted(catalog.items()):
        split_name = "train" if sid in train_ids else ("val" if sid in val_ids else "test")
        rb_id = resolve_runbook_for_scenario(sid, meta)
        target_svc = meta.target_services[0] if meta.target_services else "frontend"

        symptoms = (
            f"Alert {meta.expected_alert} firing on service {target_svc}. "
            f"Workload exhibits {meta.expected_root_cause}. Tier: {meta.tier}."
        )

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

    manifest = {
        "dataset_name": "atlasops_rs_incident_interactions",
        "file": str(out_file.as_posix()),
        "canonical_lf_sha256": canonical_sha256,
        "total_interactions": len(interactions),
        "split_distribution": split_counts,
        "runbook_distribution": rb_counts,
        "unique_runbooks_covered": len(rb_counts),
        "total_catalog_runbooks": len(RUNBOOK_CATALOG),
    }

    manifest_path = EVIDENCE_DIR / "rs_dataset_manifest.json"
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
