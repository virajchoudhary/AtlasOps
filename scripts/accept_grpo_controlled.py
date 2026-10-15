"""Verify a locally exported controlled checkpoint without loading model weights."""

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def _reward_driven_groups(rows: list[dict[str, Any]]) -> dict[int, bool]:
    from training.grpo_controlled import HYPERPARAMETERS
    from training.grpo_reward import has_reward_driven_advantages

    group_count = HYPERPARAMETERS["max_steps"]
    samples_per_group = HYPERPARAMETERS["num_generations"]
    expected_groups = set(range(1, group_count + 1))
    rewards_by_group: dict[int, list[tuple[int, Any]]] = {}
    group_statuses: dict[int, str] = {}
    generations: dict[int, dict[str, Any]] = {}

    for row in rows:
        event = row["event"]
        if event == "transition_verified":
            group = row.get("group")
            sample = row.get("sample")
            if (
                type(group) is not int or group < 1
                or type(sample) is not int or sample < 0
            ):
                raise ValueError("Controlled transition has an invalid reward group")
            rewards_by_group.setdefault(group, []).append((sample, row.get("reward")))
        elif event == "group_finished":
            group = row.get("group")
            if type(group) is not int or group < 1 or group in group_statuses:
                raise ValueError("Controlled completion has an invalid reward group")
            group_statuses[group] = row.get("status")
        elif event == "generation_tokens":
            group = row.get("group")
            if type(group) is not int or group < 1 or group in generations:
                raise ValueError("GRPO token record has an invalid reward group")
            generations[group] = row

    if (
        set(rewards_by_group) != expected_groups
        or set(group_statuses) != expected_groups
        or set(generations) != expected_groups
    ):
        raise ValueError("Controlled reward and generation groups do not match")

    signals = {}
    for group in sorted(expected_groups):
        samples = sorted(rewards_by_group[group])
        if (
            group_statuses[group] != "completed"
            or len(samples) != samples_per_group
            or [sample for sample, _reward in samples] != list(range(samples_per_group))
        ):
            raise ValueError("Controlled reward group is incomplete or out of order")
        objective_rewards = [reward for _sample, reward in samples]
        generation = generations[group]
        if generation.get("objective_rewards") != objective_rewards:
            raise ValueError("GRPO tokens are not bound to their objective rewards")
        signal = has_reward_driven_advantages(
            objective_rewards, generation.get("advantages")
        )
        signals[group] = signal
    return signals


def _validate_ledger_chronology(rows: list[dict[str, Any]]) -> None:
    from training.grpo_controlled import HYPERPARAMETERS

    expected_events: list[tuple[str, int | None, int | None, int | None]] = [
        ("initial_adapter_tensors", None, None, None)
    ]
    for group in range(1, HYPERPARAMETERS["max_steps"] + 1):
        expected_events.append(("state_observed", group, None, None))
        for sample in range(HYPERPARAMETERS["num_generations"]):
            expected_events.extend(
                (
                    ("branch_reset", group, sample, None),
                    ("action_proposed", group, sample, None),
                    ("transition_verified", group, sample, None),
                )
            )
        expected_events.extend(
            (
                ("group_finished", group, None, None),
                ("generation_tokens", group, None, None),
                ("reward_gradient_observed", group, None, group - 1),
                ("gradient_observed", group, None, group - 1),
                ("optimizer_step_observed", group, None, group),
            )
        )
    expected_events.append(("training_finished", None, None, None))
    if len(rows) != len(expected_events):
        raise ValueError("Controlled export ledger chronology is incomplete")

    for sequence, (row, expected) in enumerate(
        zip(rows, expected_events, strict=True), start=1
    ):
        event, group, sample, step = expected
        if type(row.get("sequence")) is not int or row["sequence"] != sequence:
            raise ValueError(
                "Controlled export ledger sequence is missing, duplicated, or reordered"
            )
        if row.get("event") != event:
            raise ValueError(
                f"Controlled export ledger chronology expected {event}"
            )
        if group is not None and (
            type(row.get("group")) is not int or row["group"] != group
        ):
            raise ValueError("Controlled export ledger chronology has a group mismatch")
        if sample is not None and (
            type(row.get("sample")) is not int or row["sample"] != sample
        ):
            raise ValueError("Controlled export ledger chronology has a sample mismatch")
        if step is not None and (
            type(row.get("step")) is not int or row["step"] != step
        ):
            raise ValueError("Controlled export ledger chronology has a step mismatch")


def _has_matched_optimizer_update(
    rows: list[dict[str, Any]],
    finished: dict[str, Any],
    reward_signals: dict[int, bool],
) -> bool:
    from training.grpo_controlled import HYPERPARAMETERS

    group_count = HYPERPARAMETERS["max_steps"]
    expected_groups = set(range(1, group_count + 1))
    gradients = finished.get("gradients")
    reward_gradients = finished.get("reward_gradients")
    optimizer_steps = finished.get("optimizer_steps")
    if (
        not isinstance(gradients, list)
        or not isinstance(reward_gradients, list)
        or not isinstance(optimizer_steps, list)
    ):
        return False

    gradient_fields = ("step", "group", "finite", "norm")
    reward_gradient_fields = ("step", "group", "finite", "norm", "component")
    optimizer_fields = ("step", "group", "changed_trainable_tensors")
    raw_gradients = [
        {field: row.get(field) for field in gradient_fields}
        for row in rows if row.get("event") == "gradient_observed"
    ]
    raw_reward_gradients = [
        {field: row.get(field) for field in reward_gradient_fields}
        for row in rows if row.get("event") == "reward_gradient_observed"
    ]
    raw_optimizer_steps = [
        {field: row.get(field) for field in optimizer_fields}
        for row in rows if row.get("event") == "optimizer_step_observed"
    ]
    if (
        len(gradients) != group_count
        or len(reward_gradients) != group_count
        or len(optimizer_steps) != group_count
        or raw_gradients != gradients
        or raw_reward_gradients != reward_gradients
        or raw_optimizer_steps != optimizer_steps
    ):
        return False

    gradients_by_group = {}
    reward_gradients_by_group = {}
    optimizer_steps_by_group = {}
    for record in gradients:
        if not isinstance(record, dict):
            return False
        group = record.get("group")
        norm = record.get("norm")
        if (
            type(group) is not int or group not in expected_groups
            or type(record.get("step")) is not int
            or record.get("step") != group - 1
            or type(record.get("finite")) is not bool
            or not isinstance(norm, int | float)
            or isinstance(norm, bool) or not math.isfinite(float(norm))
            or group in gradients_by_group
        ):
            return False
        gradients_by_group[group] = record
    for record in reward_gradients:
        if not isinstance(record, dict):
            return False
        group = record.get("group")
        norm = record.get("norm")
        if (
            type(group) is not int or group not in expected_groups
            or type(record.get("step")) is not int
            or record.get("step") != group - 1
            or type(record.get("finite")) is not bool
            or record.get("component") != "reward_only_policy_loss"
            or not isinstance(norm, int | float)
            or isinstance(norm, bool) or not math.isfinite(float(norm))
            or norm < 0
            or group in reward_gradients_by_group
        ):
            return False
        reward_gradients_by_group[group] = record
    for record in optimizer_steps:
        if not isinstance(record, dict):
            return False
        group = record.get("group")
        changed = record.get("changed_trainable_tensors")
        if (
            type(group) is not int or group not in expected_groups
            or type(record.get("step")) is not int
            or record.get("step") != group
            or type(changed) is not int or changed < 0
            or group in optimizer_steps_by_group
        ):
            return False
        optimizer_steps_by_group[group] = record

    if (
        set(gradients_by_group) != expected_groups
        or set(reward_gradients_by_group) != expected_groups
        or set(optimizer_steps_by_group) != expected_groups
    ):
        return False
    return any(
        reward_signals[group]
        and reward_gradients_by_group[group]["finite"]
        and reward_gradients_by_group[group]["norm"] > 0
        and gradients_by_group[group]["finite"]
        and gradients_by_group[group]["norm"] > 0
        and optimizer_steps_by_group[group]["changed_trainable_tensors"] > 0
        for group in expected_groups
    )


def accept_export(run: Path, reload_report: Path, manifest_sha256: str, reload_sha256: str):
    from training.grpo_controlled import CLASSIFICATION, PROFILE
    from training.grpo_reload_isolation import validate_isolation_evidence
    from training.sft_provenance import checkpoint_inventory, has_redirecting_path_component

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
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("event"), str)
        or row.get("classification") != CLASSIFICATION
        for row in rows
    ):
        raise ValueError("Controlled export contains unclassified evidence")
    if any(row["event"] in {"training_failed", "verifier_failure"} for row in rows):
        raise ValueError("Controlled export contains a failed or unscorable run")
    finished = [row for row in rows if row["event"] == "training_finished"]
    if (
        len(finished) != 1
        or type(finished[0].get("global_step")) is not int
        or finished[0]["global_step"] != 2
        or type(finished[0].get("changed_tensors")) is not int
        or finished[0]["changed_tensors"] <= 0
    ):
        raise ValueError("Controlled export lacks genuine finite reward-driven training")
    reward_signals = _reward_driven_groups(rows)
    if (
        not any(reward_signals.values())
        or not _has_matched_optimizer_update(rows, finished[0], reward_signals)
    ):
        raise ValueError(
            "Controlled export lacks a separate reward-only policy gradient "
            "and same-group total gradient and parameter update"
        )
    _validate_ledger_chronology(rows)
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
