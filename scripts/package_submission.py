"""AtlasOps submission-readiness inventory generator (Gate G15).

Hashes the selected implementation, tests, evidence, and documentation needed to
review the current pipeline state. The inventory does not certify scientific gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
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
    "OUT_OF_SCOPE",
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


def _is_link_or_reparse_point(path: Path) -> bool:
    """Inspect a path component without following links or junctions."""
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ValueError(f"Tracked asset path component cannot be inspected: {path}") from error

    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if getattr(metadata, "st_file_attributes", 0) & reparse_attribute:
        return True

    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        try:
            return bool(is_junction())
        except (OSError, RuntimeError) as error:
            raise ValueError(
                f"Tracked asset path component cannot be inspected: {path}"
            ) from error
    return False


def _matching_tracked_files(repo_root: Path, patterns: list[str]) -> dict[str, Path]:
    """Select indexed files matching the package allowlist and reject escaping links."""
    repo_root = repo_root.resolve()
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    tracked_paths = {
        os.fsdecode(path) for path in listing.split(b"\0") if path
    }

    selected: dict[str, Path] = {}
    for pattern in patterns:
        for relative_path in sorted(tracked_paths):
            if "/" not in pattern and "/" in relative_path:
                continue
            if not PurePosixPath(relative_path).match(pattern):
                continue
            candidate = repo_root.joinpath(*PurePosixPath(relative_path).parts)
            component = repo_root
            for part in PurePosixPath(relative_path).parts:
                component = component / part
                if _is_link_or_reparse_point(component):
                    raise ValueError(
                        f"Tracked asset path traverses a symbolic link or reparse point: {relative_path}"
                    )
            try:
                resolved_path = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise ValueError(f"Tracked asset cannot be resolved: {relative_path}") from error
            try:
                resolved_path.relative_to(repo_root)
            except ValueError as error:
                raise ValueError(f"Tracked asset escapes repository root: {relative_path}") from error
            if not resolved_path.is_file():
                continue
            selected[relative_path] = candidate

    return selected


def collect_submission_assets() -> dict[str, dict[str, Any]]:
    """Catalog selected Git-index assets with raw-byte hashes and sizes."""
    tracked_patterns = [
        "AGENTS.md",
        ".gitattributes",
        "BENCHMARKS.md",
        "BLOG.md",
        "Makefile",
        "pyproject.toml",
        "README.md",
        "app.py",
        "ui_read_model.py",
        "docs/AtlasOps_Technical_Report.md",
        "docs/BENCHMARKS.md",
        "docs/END_TO_END_FLOW.md",
        "docs/EXPERIMENT_REGISTRY.md",
        "docs/HF_SPACE_SETUP.md",
        "docs/slides.md",
        "docs/media/*.png",
        "docs/project/G4_PROTOCOL_V34_APPROVAL_CHANNEL.md",
        "docs/project/IMPLEMENTATION_STATUS.md",
        "docs/project/DYNAMIC_ADVERSARIAL_PROPOSAL_CONTRACT.md",
        "docs/project/G9_PROTOCOL_STANDALONE_P1_APPROVAL.md",
        "docs/project/FINAL_PIPELINE_V22_STATUS.md",
        "docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_1.md",
        "docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_2.md",
        "docs/project/G13_COMMON_MEASUREMENT_CONTRACT_V0_3_PROPOSAL.md",
        "docs/project/G7_G9_REMOTE_TRAINING_READINESS.md",
        "docs/project/G7_G13_REMOTE_EXECUTION_RUNBOOK.md",
        "docs/project/G7_G13_DECISION_REGISTER.md",
        "docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_3.md",
        "docs/project/GAI_RL_SCOPE_REVISION.md",
        "docs/project/MASTER_PIPELINE_STATUS.md",
        "docs/project/UPSTREAM_ALIGNMENT_AUDIT_REPORT.md",
        "docs/project/UPSTREAM_README_CURRENT_GAP_MATRIX.md",
        "docs/project/STAGE_*.md",
        "artifacts/models/hybrid_recommender.json",
        "artifacts/models/hybrid_recommender_synthetic_v2.json",
        "artifacts/evidence/.gitattributes",
        "artifacts/evidence/recovery/2026-09-05-workspace-recovery.json",
        "artifacts/evidence/recovery/SETUP-03_COMMANDS.md",
        "artifacts/evidence/stage3/acceptance_report.json",
        "artifacts/evidence/stage4/*.json",
        "artifacts/evidence/stage4/*.yaml",
        "artifacts/evidence/stage4/*.md",
        "artifacts/evidence/stage4/EXP-STAGE4-SF002-00[4-8].runlog.txt",
        "artifacts/evidence/stage7/sft_corpus_manifest.json",
        "artifacts/evidence/stage7/sft_training_config.json",
        "artifacts/evidence/stage10/rs_baseline_eval.json",
        "artifacts/evidence/stage10/rs_dataset_manifest.json",
        "artifacts/evidence/stage11/rs_hybrid_eval.json",
        "artifacts/evidence/stage11/rs_hybrid_eval_synthetic_v2.json",
        "artifacts/overnight_experiments/rs-20260924/interactions.jsonl",
        "artifacts/overnight_experiments/rs-20260924/interactions.manifest.json",
        "artifacts/evidence/stage13/ablation_benchmark_results.json",
        "agents/coordinator.py",
        "agents/adversarial_designer.py",
        "agents/_http_retry.py",
        "agents/approval_http.py",
        "agents/approval.py",
        "agents/grounding.py",
        "agents/judge.py",
        "agents/policy_remediation.py",
        "agents/verifier.py",
        "agents/prompts/*.md",
        "agents/tool_policy.py",
        "agents/tools/argocd.py",
        "agents/tools/chaos.py",
        "agents/tools/prometheus.py",
        "bench/zero_shot_baseline.py",
        "bench/runner.py",
        "bench/chaos_manifests/cascade/*.yaml",
        "bench/chaos_manifests/multi_fault/*.yaml",
        "bench/chaos_manifests/named_replays/*.yaml",
        "bench/chaos_manifests/single_fault/*.yaml",
        "bench/sft_eval.py",
        "bench/grpo_eval.py",
        "dashboard.py",
        "demo/launcher.py",
        "eval.py",
        "leaderboard.py",
        "static/index.html",
        "static/console.css",
        "static/console.js",
        "static/live-incident.js",
        "static/live-incident.test.js",
        "static/vendor/lucide.min.js",
        "static/vendor/LUCIDE-LICENSE",
        "config/g4_protocol.py",
        "config/runtime.py",
        "config/scenario_catalog.py",
        "config/splits.py",
        "recommender/baselines.py",
        "recommender/hybrid.py",
        "recommender/dataset.py",
        "recommender/train_hybrid.py",
        "scripts/run_stage4_golden_incident.py",
        "scripts/run_g12_integrated_episode.py",
        "scripts/package_submission.py",
        "scripts/release_gate.py",
        "training/sft.py",
        "training/build_sft_dataset.py",
        "training/generate_trajectories.py",
        "training/generate_trajectories_fast.py",
        "training/sft_rendering.py",
        "training/templates/qwen2_5_tool_sft.jinja",
        "training/sft_provenance.py",
        "training/grpo.py",
        "training/grpo_environment.py",
        "training/grpo_provenance.py",
        "training/grpo_reward.py",
        "bench/ablation_suite.py",
        "bench/episode_membership.py",
        "tests/test_*.py",
        "tests/stage4_approval_process.py",
        "tests/g9_approval_process.py",
    ]

    repo_root = Path.cwd().resolve()
    assets: dict[str, dict[str, Any]] = {}
    for rel, path in _matching_tracked_files(repo_root, tracked_patterns).items():
        assets[rel] = {
            "sha256": compute_sha256(path),
            "size_bytes": path.stat().st_size,
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
        "pipeline_version": "v2.2",
        "scope_revision": "GAI + RL (RS optional historical research)",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "NOT_CERTIFIED",
        "gate_status_source": str(MASTER_STATUS_PATH.as_posix()),
        "gate_statuses_declared": gates,
        "academic_workstreams": [
            "Generative AI: Multi-Agent Incident Response & Trajectory Synthesis",
            "Reinforcement Learning: Online Group Relative Policy Optimization (GRPO)",
        ],
        "historical_optional_workstreams": [
            "Recommender Systems: bounded scenario-derived runbook ranking",
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
        f"- **Working Pipeline**: `{manifest['pipeline_version']}` with [{manifest['scope_revision']}](../docs/project/GAI_RL_SCOPE_REVISION.md); Section 25 and the measurement protocol are not frozen",
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
        "G3 PASS is historical local Kind acceptance with wrapper and tracing caveats; it does not establish current cluster health.",
        "G10/G11 OUT_OF_SCOPE retain historical scenario-derived RS evidence; the former bounded G11 PASS was not real incident improvement.",
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
