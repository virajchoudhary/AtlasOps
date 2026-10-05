"""Verify a locally exported controlled checkpoint without loading model weights."""

import argparse
import hashlib
import json
from pathlib import Path


def accept_export(run: Path, reload_report: Path, manifest_sha256: str, reload_sha256: str):
    from training.grpo_controlled import CLASSIFICATION, PROFILE
    from training.sft_provenance import checkpoint_inventory, has_redirecting_path_component
    from training.grpo_reload_isolation import validate_isolation_evidence

    for path in (run, reload_report):
        if has_redirecting_path_component(path):
            raise ValueError("Controlled evidence must not contain redirects")
    manifest_path = run / "controlled_grpo_manifest.json"
    raw = manifest_path.read_bytes()
    reload_raw = reload_report.read_bytes()
    if (
        hashlib.sha256(raw).hexdigest() != manifest_sha256
        or hashlib.sha256(reload_raw).hexdigest() != reload_sha256
    ):
        raise ValueError("Exported evidence differs from preserved external hashes")
    manifest = json.loads(raw)
    reload = json.loads(reload_raw)
    evidence_files = manifest.get("evidence_files")
    if not isinstance(evidence_files, dict) or not evidence_files:
        raise ValueError("Controlled export has no frozen evidence inventory")
    actual_files = {}
    for path in sorted(run.rglob("*")):
        if has_redirecting_path_component(path):
            raise ValueError("Controlled evidence tree contains redirects")
        if path.is_file() and path != manifest_path:
            actual_files[path.relative_to(run).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    if evidence_files != actual_files:
        raise ValueError("Controlled exported evidence inventory mismatch")
    if (
        manifest.get("profile") != PROFILE
        or manifest.get("classification") != CLASSIFICATION
        or manifest.get("status") != "TRAINED_RELOAD_PENDING"
        or manifest.get("changed_tensors", 0) <= 0
        or reload.get("status") != "PASS"
        or reload.get("profile") != PROFILE
        or reload.get("classification") != CLASSIFICATION
        or reload.get("manifest_sha256") != manifest_sha256
        or not validate_isolation_evidence(
            reload.get("network_isolation"), source_sha=manifest["receipt"]["source_sha"]
        )
        or reload.get("inference_performed") is not False
        or reload.get("held_out_accessed") is not False
        or checkpoint_inventory(run / "adapter", manifest_path) != manifest["checkpoint"]
    ):
        raise ValueError("Controlled export does not establish trained/reloaded evidence")
    rows = [json.loads(line) for line in (run / "episodes.jsonl").read_text().splitlines()]
    if any(row.get("classification") != CLASSIFICATION for row in rows):
        raise ValueError("Controlled export contains unclassified evidence")
    finished = [row for row in rows if row["event"] == "training_finished"]
    if (
        len(finished) != 1 or finished[0]["global_step"] != 2
        or finished[0]["changed_tensors"] <= 0
        or len(finished[0]["gradients"]) != 2
        or not all(g["finite"] for g in finished[0]["gradients"])
        or not any(g["norm"] > 0 for g in finished[0]["gradients"])
        or not any(
            row["event"] == "generation_tokens"
            and any(value != 0 for value in row["advantages"])
            for row in rows
        )
        or any(row["event"] in {"training_failed", "verifier_failure"} for row in rows)
    ):
        raise ValueError("Controlled export lacks genuine finite reward-driven training")
    inventory = [
        {"path": path.relative_to(run).as_posix(), "bytes": path.stat().st_size,
         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(run.rglob("*")) if path.is_file()
    ]
    return {
        "status": "CONTROLLED_CHECKPOINT_TRAINED_RELOADED_AND_EXPORTED",
        "classification": CLASSIFICATION, "certification_status": "NOT_CERTIFIED",
        "manifest_sha256": manifest_sha256, "reload_sha256": reload_sha256,
        "local_files": inventory, "G4": "NOT_PASSED", "G8_live_criterion_met": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--reload-report", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--reload-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.run.resolve()):
        raise ValueError("Acceptance report requires a fresh external output")
    report = accept_export(
        args.run, args.reload_report, args.manifest_sha256, args.reload_sha256
    )
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)


if __name__ == "__main__":
    main()
