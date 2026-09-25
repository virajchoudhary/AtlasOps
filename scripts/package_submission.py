"""AtlasOps submission-readiness inventory generator (Gate G15).

Hashes the selected implementation, tests, evidence, and documentation needed to
review the current pipeline state. The inventory does not certify scientific gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("package_submission")

ARTIFACTS_DIR = Path("artifacts")
MASTER_STATUS_PATH = Path("docs/project/MASTER_PIPELINE_STATUS.md")
ALLOWED_GATE_STATES = {
    "PASS",
    "PARTIAL",
    "IMPLEMENTED / EMPIRICAL EVIDENCE MISSING",
    "REOPENED",
    "NOT_PASSED",
    "BLOCKED",
}


def declared_gate_statuses(path: Path = MASTER_STATUS_PATH) -> dict[str, str]:
    """Read the current governance inventory without certifying its claims."""
    statuses: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 7 or not cells[1].startswith("**Stage "):
            continue
        gate = re.fullmatch(r"\*\*G(\d+)\*\*", cells[3])
        state = re.match(r"\*\*([^*]+)\*\*", cells[5])
        if gate is None or state is None or state.group(1) not in ALLOWED_GATE_STATES:
            raise ValueError(f"Invalid gate status row: {line}")
        key = f"G{gate.group(1)}"
        if key in statuses:
            raise ValueError(f"Duplicate gate status: {key}")
        statuses[key] = state.group(1)
    if set(statuses) != {f"G{i}" for i in range(16)}:
        raise ValueError("Master status must declare exactly G0 through G15")
    return statuses


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def collect_submission_assets() -> dict[str, dict[str, Any]]:
    """Scan and catalog all core submission assets with hashes and sizes."""
    tracked_patterns = [
        ".gitattributes",
        "BENCHMARKS.md",
        "Makefile",
        "README.md",
        "app.py",
        "docs/AtlasOps_Technical_Report.md",
        "docs/BENCHMARKS.md",
        "docs/END_TO_END_FLOW.md",
        "docs/HF_SPACE_SETUP.md",
        "docs/project/MASTER_PIPELINE_STATUS.md",
        "docs/project/STAGE_*.md",
        "artifacts/models/hybrid_recommender.json",
        "artifacts/models/hybrid_recommender_synthetic_v2.json",
        "artifacts/evidence/stage10/rs_dataset_manifest.json",
        "artifacts/evidence/stage11/rs_hybrid_eval.json",
        "artifacts/evidence/stage11/rs_hybrid_eval_synthetic_v2.json",
        "artifacts/overnight_experiments/rs-20260924/interactions.jsonl",
        "artifacts/overnight_experiments/rs-20260924/interactions.manifest.json",
        "artifacts/evidence/stage13/ablation_benchmark_results.json",
        "agents/coordinator.py",
        "agents/grounding.py",
        "agents/policy_remediation.py",
        "agents/prompts/*.md",
        "agents/tool_policy.py",
        "agents/tools/argocd.py",
        "agents/tools/chaos.py",
        "agents/tools/prometheus.py",
        "bench/zero_shot_baseline.py",
        "bench/runner.py",
        "bench/sft_eval.py",
        "bench/grpo_eval.py",
        "dashboard.py",
        "demo/launcher.py",
        "static/index.html",
        "static/console.css",
        "static/console.js",
        "static/live-incident.js",
        "static/live-incident.test.js",
        "static/vendor/lucide.min.js",
        "static/vendor/LUCIDE-LICENSE",
        "config/g4_protocol.py",
        "recommender/baselines.py",
        "recommender/hybrid.py",
        "recommender/dataset.py",
        "recommender/train_hybrid.py",
        "scripts/run_stage4_golden_incident.py",
        "scripts/run_g12_integrated_episode.py",
        "scripts/package_submission.py",
        "training/sft.py",
        "training/generate_trajectories.py",
        "training/generate_trajectories_fast.py",
        "training/sft_provenance.py",
        "training/grpo.py",
        "training/grpo_environment.py",
        "training/grpo_provenance.py",
        "bench/ablation_suite.py",
        "tests/test_*.py",
    ]

    assets: dict[str, dict[str, Any]] = {}

    for pattern in tracked_patterns:
        matches = list(Path(".").glob(pattern))
        for p in matches:
            if p.is_file():
                rel = p.as_posix()
                assets[rel] = {
                    "sha256": compute_sha256(p),
                    "size_bytes": p.stat().st_size,
                    "last_modified": datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).isoformat(),
                }

    return assets


def build_submission_package(output_dir: Path | None = None) -> dict[str, Any]:
    """Assemble an integrity inventory without inventing gate or model metrics."""
    assets = collect_submission_assets()
    gates = declared_gate_statuses()
    log.info("Collected %d canonical submission assets.", len(assets))

    manifest = {
        "project_name": "AtlasOps",
        "project_repository": "virajchoudhary/AtlasOps",
        "upstream_baseline": "Harikishanth/AtlasOps @ bf9bd19",
        "pipeline_version": "v1.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "NOT_CERTIFIED",
        "gate_status_source": str(MASTER_STATUS_PATH.as_posix()),
        "gate_statuses_declared": gates,
        "academic_workstreams": [
            "Generative AI: Multi-Agent Incident Response & Trajectory Synthesis",
            "Recommender Systems: Hybrid Top-K Runbook Recommendation",
            "Reinforcement Learning: Online Group Relative Policy Optimization (GRPO)",
        ],
        "empirical_metrics": None,
        "metric_note": "No full-pipeline empirical metric is certified by this asset inventory.",
        "asset_count": len(assets),
        "assets": assets,
    }

    destination = output_dir or ARTIFACTS_DIR
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "SUBMISSION_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")
    log.info("Wrote %s", manifest_path)

    # Generate Markdown Summary
    summary_path = destination / "SUBMISSION_SUMMARY.md"
    summary_md = generate_submission_summary_md(manifest)
    summary_path.write_text(summary_md, encoding="utf-8", newline="\n")
    log.info("Wrote %s", summary_path)

    return manifest


def generate_submission_summary_md(manifest: dict[str, Any]) -> str:
    lines = [
        "# AtlasOps — Submission Readiness and Asset Inventory",
        "",
        f"- **Project Repository**: `{manifest['project_repository']}`",
        f"- **Upstream Baseline**: `{manifest['upstream_baseline']}`",
        f"- **Certification**: **{manifest['status']}**",
        f"- **Generated**: `{manifest['generated_at']}`",
        f"- **Gate inventory source**: `{manifest['gate_status_source']}`",
        "",
        "## Declared Gate Statuses",
        "",
        "| Gate | Status |",
        "| :--- | :--- |",
    ]
    for gate, status in manifest["gate_statuses_declared"].items():
        lines.append(f"| {gate} | {status} |")
    lines.extend([
        "",
        manifest["metric_note"],
        "Asset hashes establish file integrity, not scientific gate closure.",
        "",
        "## Canonical Submission Artifacts",
        "",
        "| Asset Path | SHA-256 Digest | Size (Bytes) |",
        "| :--- | :--- | :---: |",
    ])

    for path, meta in sorted(manifest["assets"].items()):
        short_hash = f"`{meta['sha256'][:16]}...`"
        lines.append(f"| `{path}` | {short_hash} | {meta['size_bytes']} |")

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="AtlasOps Submission Package Generator")
    parser.parse_args()
    build_submission_package()


if __name__ == "__main__":
    main()
