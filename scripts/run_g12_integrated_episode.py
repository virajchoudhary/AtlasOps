"""Archive one governed live G12 episode without creating a second Chaos path.

This wrapper delegates all fault, approval, and cleanup control to the Stage 4
harness. A bundle is evidence for review, never an automatic gate certification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = re.compile(r"EXP-STAGE4-[A-Za-z0-9][A-Za-z0-9_-]{0,112}\Z")


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _source_sha(root: Path) -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    if status.stdout.strip():
        raise RuntimeError("G12 live capture requires a clean disposable source checkout")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("Unable to establish the exact G12 source commit")
    return commit


def _checkpoint_record(checkpoint: Path) -> dict[str, Any]:
    from bench.grpo_eval import validate_grpo_checkpoint

    return validate_grpo_checkpoint(checkpoint).to_record()


def _artifact_copy(source: Path, destination: Path, root: Path) -> dict[str, Any]:
    resolved = source.resolve()
    if source.is_symlink() or not resolved.is_relative_to(root.resolve()) or not source.is_file():
        raise ValueError(f"Refusing a non-repository evidence path: {source}")
    shutil.copyfile(source, destination)
    raw = destination.read_bytes()
    return {
        "path": destination.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def collect_bundle(
    *,
    root: Path,
    bundle: Path,
    experiment_id: str,
    source_sha: str,
    checkpoint: dict[str, Any],
    seed: int,
    process_exit_code: int,
    launch_error: str | None = None,
    checkpoint_postflight_error: str | None = None,
    checkpoint_postflight_verified: bool = False,
) -> dict[str, Any]:
    """Preserve raw attempt/trajectory bytes, including negative and partial runs."""
    if not EXPERIMENT_ID.fullmatch(experiment_id):
        raise ValueError("Experiment ID must be a safe EXP-STAGE4-* name")
    evidence_dir = root / "artifacts/evidence/stage4"
    assets: dict[str, dict[str, Any]] = {}
    for source in sorted(evidence_dir.glob(f"{experiment_id}.*")):
        if source.is_file():
            assets[source.name] = _artifact_copy(source, bundle / source.name, root)

    primary_path = evidence_dir / f"{experiment_id}.json"
    primary: dict[str, Any] | None = None
    incident: dict[str, Any] | None = None
    problems: list[str] = []
    if primary_path.is_file():
        try:
            loaded = json.loads(primary_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise TypeError("primary evidence is not an object")
            primary = loaded
        except (OSError, ValueError, TypeError) as exc:
            problems.append(f"primary_evidence_unreadable:{type(exc).__name__}")
    else:
        problems.append("primary_evidence_missing")
    if launch_error:
        problems.append(launch_error)
    if not checkpoint_postflight_verified:
        problems.append(checkpoint_postflight_error or "checkpoint_postflight_not_verified")

    phases = _object((primary or {}).get("phases"))
    execution = _object(phases.get("coordinator_execution"))
    incident_id = execution.get("incident_id")
    if isinstance(incident_id, str) and re.fullmatch(r"inc-[A-Za-z0-9_-]+", incident_id):
        trajectory = root / "artifacts/trajectories" / f"{incident_id}.json"
        if trajectory.is_file():
            assets[f"trajectory-{incident_id}.json"] = _artifact_copy(
                trajectory, bundle / f"trajectory-{incident_id}.json", root
            )
            try:
                loaded = json.loads(trajectory.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise TypeError("coordinator record is not an object")
                incident = loaded
            except (OSError, ValueError, TypeError) as exc:
                problems.append(f"coordinator_record_unreadable:{type(exc).__name__}")
        else:
            problems.append("coordinator_record_missing")
    else:
        problems.append("incident_identity_missing")

    if primary and primary.get("experiment_id") != experiment_id:
        problems.append("experiment_identity_mismatch")
    if _object((primary or {}).get("source_identity")).get("git_commit") != source_sha:
        problems.append("source_identity_mismatch")
    if not _object((primary or {}).get("preflight_evidence")).get("persisted_before_injection"):
        problems.append("governed_preflight_missing")
    required_fields = (
        "alert", "triage", "diagnosis", "recommender", "approval",
        "remediation", "settling", "verification", "comms",
    )
    for key in required_fields:
        if incident is None or incident.get(key) is None:
            problems.append(f"{key}_missing")
    policy = _object(_object((incident or {}).get("remediation")).get("final"))
    if policy.get("policy_backend") != "checkpoint":
        problems.append("checkpoint_policy_execution_unverified")
    recommendations = _object((incident or {}).get("recommender")).get("recommended_runbooks")
    if not isinstance(recommendations, list) or not recommendations:
        problems.append("recommender_output_missing")
    if incident and incident.get("incident_id") != incident_id:
        problems.append("incident_identity_mismatch")

    manifest = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "status": "CAPTURED_FOR_REVIEW" if not problems else "INCOMPLETE",
        "empirical_claim_allowed": False,
        "gate_certification": "NOT_CERTIFIED",
        "process_exit_code": process_exit_code,
        "launch_error": launch_error,
        "source_sha": source_sha,
        "checkpoint": checkpoint,
        "checkpoint_postflight_verified": checkpoint_postflight_verified,
        "policy_seed": seed,
        "incident_id": incident_id,
        "model_identity": _object((primary or {}).get("protocol_profile")).get("model"),
        "recorded_g4_verdict": (primary or {}).get("gate_g4_pass"),
        "recorded_env_resolved": _object((incident or {}).get("verification")).get("env_resolved"),
        "elapsed_seconds": (primary or {}).get("duration_seconds"),
        "time_to_resolve_s": None,
        "reward": None,
        "record_fields_present": {
            key: incident.get(key) is not None if incident else False
            for key in required_fields
        },
        "policy_step_count": len(_object((incident or {}).get("remediation")).get("policy_steps") or []),
        "problems": problems,
        "assets": assets,
    }
    target = bundle / "g12_capture_manifest.json"
    temporary = bundle / ".g12_capture_manifest.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, target)
    return manifest


def run_live_episode(
    *, checkpoint: Path, experiment_id: str, bundle: Path, seed: int
) -> dict[str, Any]:
    if not EXPERIMENT_ID.fullmatch(experiment_id):
        raise ValueError("Experiment ID must be a safe, unique EXP-STAGE4-* name")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("Policy seed must be a non-negative integer")
    root = ROOT.resolve()
    output = bundle.expanduser().resolve()
    if output == root or output.is_relative_to(root) or output.exists():
        raise ValueError("Bundle directory must be new and outside the experiment checkout")
    source_sha = _source_sha(root)
    provenance = _checkpoint_record(checkpoint.expanduser().resolve())
    evidence_dir = root / "artifacts/evidence/stage4"
    existing = next(evidence_dir.glob(f"{experiment_id}.*"), None)
    if existing is not None:
        raise FileExistsError(f"Stage 4 attempt artifact already exists: {existing}")

    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env["ATLASOPS_REMEDIATION_BACKEND"] = "rl_policy"
    env["ATLASOPS_RL_POLICY_CHECKPOINT"] = str(checkpoint.expanduser().resolve())
    env["ATLASOPS_POLICY_SEED"] = str(seed)
    env["STAGE4_EXPERIMENT_ID"] = experiment_id
    launch_error = None
    try:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.run_stage4_golden_incident"],
            cwd=root,
            env=env,
            check=False,
        )
        exit_code = result.returncode
    except KeyboardInterrupt:
        exit_code = 130
        launch_error = "harness_interrupted"
    except OSError as exc:
        exit_code = 127
        launch_error = f"harness_launch_failed:{type(exc).__name__}"
    try:
        if _checkpoint_record(checkpoint.expanduser().resolve()) != provenance:
            checkpoint_postflight_error = "checkpoint_changed_during_run"
        else:
            checkpoint_postflight_error = None
    except (OSError, ValueError, TypeError) as exc:
        checkpoint_postflight_error = f"checkpoint_postflight_unverifiable:{type(exc).__name__}"
    return collect_bundle(
        root=root,
        bundle=output,
        experiment_id=experiment_id,
        source_sha=source_sha,
        checkpoint=provenance,
        seed=seed,
        process_exit_code=exit_code,
        launch_error=launch_error,
        checkpoint_postflight_error=checkpoint_postflight_error,
        checkpoint_postflight_verified=checkpoint_postflight_error is None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Governed G12 integrated evidence capture")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--execute-live-chaos",
        action="store_true",
        help="Run the Stage 4 harness with real cluster actions after its preflight",
    )
    args = parser.parse_args()
    if not args.execute_live_chaos:
        parser.error("No run started: live Chaos requires --execute-live-chaos and separate authorization")
    manifest = run_live_episode(
        checkpoint=args.checkpoint,
        experiment_id=args.experiment_id,
        bundle=args.bundle_dir,
        seed=args.seed,
    )
    print(f"G12 evidence capture: {manifest['status']} (NOT_CERTIFIED)")
    if manifest["process_exit_code"] or manifest["status"] != "CAPTURED_FOR_REVIEW":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
