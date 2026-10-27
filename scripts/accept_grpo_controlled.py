"""Verify a locally exported controlled checkpoint without loading model weights."""

import argparse
import hashlib
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _expected_verification(state: Mapping[str, Any]) -> dict[str, Any]:
    observations = state["observations"]
    replicas = observations["ready_replicas"]
    demand = observations["demand_units"]
    checks = [
        {
            "name": "capacity_covers_demand",
            "required": True,
            "passed": replicas >= demand,
            "observed": {"capacity": replicas, "demand": demand},
        },
        {
            "name": "ready_capacity_positive",
            "required": True,
            "passed": replicas > 0,
            "observed": replicas,
        },
    ]
    resolved = all(check["passed"] for check in checks)
    return {
        "verification_status": "passed" if resolved else "failed",
        "env_resolved": resolved,
        "checks": checks,
    }


def _action_disposition(
    completion: str, state: Mapping[str, Any], approval_decision: Any
) -> tuple[str | None, dict[str, Any] | None]:
    from agents.approval import approval_mode_for_severity
    from agents.coordinator import _TOOL_PARAMETER_SCHEMAS, _check_tool_policy
    from training.grpo_environment import parse_policy_action
    from training.sft_candidate import _validate_schema

    try:
        action = parse_policy_action(completion)
        raw = json.loads(completion)
        if not isinstance(raw, dict) or set(raw) != {
            "tool",
            "arguments",
            "agent_claimed_resolved",
        }:
            return "invalid_action", action
    except (TypeError, ValueError, json.JSONDecodeError):
        return "invalid_action", None

    tool = action["tool"]
    arguments = action["arguments"]
    try:
        _validate_schema(arguments, _TOOL_PARAMETER_SCHEMAS.get(tool, {}))
        if _check_tool_policy("remediation", tool, arguments, dict(state)):
            return "policy_block", action
    except (TypeError, ValueError):
        return "invalid_action", action

    if tool not in {"kubectl_scale", "kubectl_get"}:
        return "tool_unavailable", action
    if tool == "kubectl_scale":
        mode = approval_mode_for_severity(state["triage"]["severity"])
        if mode != "auto" and approval_decision != "approved":
            return "approval_required", action
    if (
        arguments.get("namespace", "default") != "default"
        or (
            tool == "kubectl_scale"
            and arguments["deployment"] != "paymentservice"
        )
    ):
        return "target_mismatch", action
    return None, action


def _next_state(
    state: Mapping[str, Any],
    action: Mapping[str, Any] | None,
    disposition: str | None,
) -> dict[str, Any]:
    expected = json.loads(json.dumps(state))
    replicas = state["observations"]["ready_replicas"]
    if disposition is None and action is not None and action["tool"] == "kubectl_scale":
        replicas = action["arguments"]["replicas"]
    expected["observations"]["ready_replicas"] = replicas
    expected["alert"]["status"] = (
        "firing"
        if replicas < state["observations"]["demand_units"]
        else "resolved"
    )
    return expected


def _environmental_signal_groups(
    rows: list[dict[str, Any]],
    tokenizer: Any,
) -> dict[int, dict[str, str]]:
    from training.grpo_controlled import HYPERPARAMETERS, SCENARIO, canonical_bytes
    from training.grpo_observation_first import _public_snapshot
    from training.grpo_reward import (
        ENVIRONMENTAL_REWARD_COMPONENTS,
        has_environmental_reward_signal,
        normalize_group_advantages,
        score_direct_action_step,
    )

    group_count = HYPERPARAMETERS["max_steps"]
    samples_per_group = HYPERPARAMETERS["num_generations"]
    expected_groups = set(range(1, group_count + 1))
    states: dict[int, dict[str, Any]] = {}
    state_rows: dict[int, dict[str, Any]] = {}
    branches: dict[int, dict[int, dict[str, Any]]] = {}
    proposals: dict[int, dict[int, dict[str, Any]]] = {}
    transitions: dict[int, dict[int, dict[str, Any]]] = {}
    group_statuses: dict[int, str] = {}
    generations: dict[int, dict[str, Any]] = {}

    for row in rows:
        event = row["event"]
        if event == "state_observed":
            group = row.get("group")
            state = row.get("state")
            if type(group) is not int or group < 1 or not isinstance(state, dict):
                raise ValueError("Controlled observation has an invalid reward group")
            if group in states:
                raise ValueError("Controlled observation group is duplicated")
            states[group] = state
            state_rows[group] = row
        elif event == "branch_reset":
            group, sample = row.get("group"), row.get("sample")
            if type(group) is not int or type(sample) is not int or sample < 0:
                raise ValueError("Controlled branch has an invalid reward sample")
            if sample in branches.setdefault(group, {}):
                raise ValueError("Controlled branch sample is duplicated")
            branches[group][sample] = row
        elif event == "action_proposed":
            group, sample = row.get("group"), row.get("sample")
            if type(group) is not int or type(sample) is not int or sample < 0:
                raise ValueError("Controlled proposal has an invalid reward sample")
            if sample in proposals.setdefault(group, {}):
                raise ValueError("Controlled proposal sample is duplicated")
            proposals[group][sample] = row
        elif event == "transition_verified":
            group, sample = row.get("group"), row.get("sample")
            if type(group) is not int or group < 1 or type(sample) is not int or sample < 0:
                raise ValueError("Controlled transition has an invalid reward sample")
            if sample in transitions.setdefault(group, {}):
                raise ValueError("Controlled transition sample is duplicated")
            transitions[group][sample] = row
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
        set(states) != expected_groups
        or set(branches) != expected_groups
        or set(proposals) != expected_groups
        or set(transitions) != expected_groups
        or set(group_statuses) != expected_groups
        or set(generations) != expected_groups
    ):
        raise ValueError("Controlled environmental and generation groups do not match")

    signals = {}
    for group in sorted(expected_groups):
        sample_indices = list(range(samples_per_group))
        if (
            group_statuses[group] != "completed"
            or set(branches[group]) != set(sample_indices)
            or set(proposals[group]) != set(sample_indices)
            or set(transitions[group]) != set(sample_indices)
        ):
            raise ValueError("Controlled environmental group is incomplete or out of order")

        state = states[group]
        prompt_bytes, expected_digest = _public_snapshot(state)
        expected_prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest()
        generation = generations[group]
        group_token = generation.get("group_token")
        if (
            not isinstance(group_token, str)
            or re.fullmatch(r"[0-9a-f]{32}", group_token) is None
            or generation.get("scenario_id") != SCENARIO
        ):
            raise ValueError("GRPO generation has an invalid group token or scenario")
        if (
            state_rows[group].get("observation_digest") != expected_digest
            or state_rows[group].get("state_prompt_sha256") != expected_prompt_sha256
        ):
            raise ValueError("Controlled observed state hash is inconsistent")
        if (
            generation.get("observation_digest") != expected_digest
            or generation.get("state_prompt_sha256") != expected_prompt_sha256
        ):
            raise ValueError("GRPO generation is not bound to its observed prompt")

        prompt_ids = generation.get("prompt_ids")
        completion_ids = generation.get("completion_ids")
        if (
            not isinstance(prompt_ids, list)
            or len(prompt_ids) != samples_per_group
            or any(
                not isinstance(sample_ids, list)
                or not sample_ids
                or any(type(token) is not int or token < 0 for token in sample_ids)
                for sample_ids in prompt_ids
            )
            or any(sample_ids != prompt_ids[0] for sample_ids in prompt_ids[1:])
            or not isinstance(completion_ids, list)
            or len(completion_ids) != samples_per_group
            or any(
                not isinstance(sample_ids, list)
                or not sample_ids
                or any(type(token) is not int or token < 0 for token in sample_ids)
                for sample_ids in completion_ids
            )
        ):
            raise ValueError("GRPO token record is incomplete or not same-prompt")

        prompt_text = prompt_bytes.decode("utf-8")
        try:
            encoded_prompts = tokenizer(
                text=[prompt_text] * samples_per_group,
                padding=True,
                padding_side="left",
                add_special_tokens=False,
            )["input_ids"]
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(
                "GRPO observed prompt cannot be encoded by its saved tokenizer"
            ) from exc
        if encoded_prompts != prompt_ids:
            raise ValueError(
                "GRPO prompt token IDs do not encode the observation-first state"
            )

        objective_rewards = []
        environmental_components = []
        environmental_rewards = []
        environmental_admissible = []
        completion_hashes = []
        completions = []
        for sample in sample_indices:
            branch = branches[group][sample]
            proposal = proposals[group][sample]
            transition = transitions[group][sample]
            if (
                branch.get("state") != state
                or proposal.get("state") != state
                or any(
                    row.get("g9_group_token") != group_token
                    or row.get("observation_digest") != expected_digest
                    or row.get("state_prompt_sha256") != expected_prompt_sha256
                    for row in (branch, proposal, transition)
                )
            ):
                raise ValueError("Controlled sample state or prompt lineage changed")

            completion = proposal.get("raw_completion")
            if (
                not isinstance(completion, str)
                or transition.get("policy_completion") != completion
            ):
                raise ValueError("Controlled transition differs from its raw completion")
            completions.append(completion)
            disposition, action = _action_disposition(
                completion, state, transition.get("approval_decision")
            )
            if transition.get("policy_action") != action:
                raise ValueError("Controlled transition action differs from its completion")
            expected_status = "ok" if disposition is None else "blocked"
            if (
                transition.get("terminal_block") != disposition
                or transition.get("status") != expected_status
                or transition.get("scorable") is not True
            ):
                raise ValueError("Controlled transition disposition is contradictory")

            expected_next_state = _next_state(state, action, disposition)
            if transition.get("next_state") != expected_next_state:
                raise ValueError("Controlled transition changed an unbound state")
            expected_verification = _expected_verification(expected_next_state)
            if transition.get("verification") != expected_verification:
                raise ValueError("Controlled verifier output differs from its environment state")

            decomposition = score_direct_action_step(
                expected_verification, agent_claimed_resolved=False
            )
            if transition.get("objective_reward_decomposition") != decomposition:
                raise ValueError("Controlled verifier reward decomposition is contradictory")
            components = {
                name: decomposition[name]
                for name in ENVIRONMENTAL_REWARD_COMPONENTS
            }
            environmental_reward = round(sum(components.values()), 6)
            admissible = disposition is None
            expected_reward = -1.0 if not admissible else decomposition["total"]
            if (
                transition.get("environmental_components") != components
                or transition.get("environmental_reward") != environmental_reward
                or transition.get("environmental_admissible") is not admissible
                or transition.get("reward") != expected_reward
            ):
                raise ValueError("Controlled reward contradicts its verifier components")

            executed = transition.get("executed_actions")
            if admissible:
                if (
                    not isinstance(executed, list)
                    or len(executed) != 1
                    or executed[0].get("tool") != action["tool"]
                    or executed[0].get("arguments") != action["arguments"]
                    or executed[0].get("result", {}).get("success") is not True
                ):
                    raise ValueError("Admissible controlled action lacks execution evidence")
            elif executed != []:
                raise ValueError("Blocked controlled action has execution evidence")

            objective_rewards.append(expected_reward)
            environmental_components.append(components)
            environmental_rewards.append(environmental_reward)
            environmental_admissible.append(admissible)
            completion_hashes.append(
                hashlib.sha256(completion.encode("utf-8")).hexdigest()
            )

        try:
            # Match pinned TRL 0.19.1's decode call before reward scoring.
            decoded_completions = tokenizer.batch_decode(
                completion_ids, skip_special_tokens=True
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "GRPO completion token IDs cannot be decoded by their saved tokenizer"
            ) from exc
        if (
            not isinstance(decoded_completions, list)
            or len(decoded_completions) != samples_per_group
            or decoded_completions != completions
        ):
            raise ValueError(
                "GRPO completion text differs from the training completion tokens"
            )

        advantages = generation.get("advantages")
        environmental_advantages = generation.get("environmental_advantages")
        if (
            generation.get("objective_rewards") != objective_rewards
            or generation.get("objective_advantages")
            != normalize_group_advantages(objective_rewards)
            or generation.get("environmental_components") != environmental_components
            or generation.get("environmental_rewards") != environmental_rewards
            or generation.get("environmental_admissible") != environmental_admissible
            or not isinstance(advantages, list)
            or environmental_advantages != advantages
            or len(advantages) != samples_per_group
        ):
            raise ValueError(
                "GRPO environmental advantages are not bound to controlled rewards"
            )

        normalized_components = hashlib.sha256(
            canonical_bytes(
                {
                    "prompt_ids": prompt_ids,
                    "completion_ids": completion_ids,
                    "advantages": environmental_advantages,
                }
            )
        ).hexdigest()
        environmental_advantages_sha256 = hashlib.sha256(
            canonical_bytes([float(value) for value in environmental_advantages])
        ).hexdigest()
        optimization_input_sha256 = generation.get("optimization_input_sha256")
        if (
            generation.get("completion_sha256s") != completion_hashes
            or generation.get("environmental_signal_sha256") != normalized_components
            or generation.get("environmental_advantages_sha256")
            != environmental_advantages_sha256
            or not isinstance(optimization_input_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", optimization_input_sha256) is None
        ):
            raise ValueError("GRPO environmental signal hash or token lineage is invalid")

        try:
            environmental_signal = has_environmental_reward_signal(
                environmental_components,
                environmental_admissible,
                environmental_advantages,
            )
        except ValueError as exc:
            raise ValueError(
                "Controlled GRPO advantages do not match environmental verifier rewards"
            ) from exc
        if not environmental_signal:
            raise ValueError(
                "Controlled reward group lacks admissible varied environmental verifier signal"
            )
        signals[group] = {
            "environmental_signal_sha256": normalized_components,
            "environmental_advantages_sha256": environmental_advantages_sha256,
            "optimization_input_sha256": optimization_input_sha256,
        }
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
    environmental_signals: dict[int, dict[str, str]],
) -> bool:
    from training.grpo_controlled import HYPERPARAMETERS
    from training.grpo_reward import ENVIRONMENTAL_GRADIENT_COMPONENT

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

    signal_hash_fields = (
        "environmental_advantages_sha256",
        "environmental_signal_sha256",
        "optimization_input_sha256",
    )
    gradient_fields = ("step", "group", "finite", "norm", *signal_hash_fields)
    reward_gradient_fields = (
        "step",
        "group",
        "finite",
        "norm",
        "component",
        *signal_hash_fields,
    )
    optimizer_fields = (
        "step",
        "group",
        "changed_trainable_tensors",
        *signal_hash_fields,
    )
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

    def hashes_match(record: dict[str, Any], group: int) -> bool:
        expected = environmental_signals.get(group)
        return bool(expected) and all(
            isinstance(record.get(name), str)
            and re.fullmatch(r"[0-9a-f]{64}", record[name]) is not None
            and record[name] == expected.get(name)
            for name in signal_hash_fields
        )

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
            or not hashes_match(record, group)
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
            or record.get("component") != ENVIRONMENTAL_GRADIENT_COMPONENT
            or not isinstance(norm, int | float)
            or isinstance(norm, bool) or not math.isfinite(float(norm))
            or norm < 0
            or not hashes_match(record, group)
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
            or not hashes_match(record, group)
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
    return all(
        group in environmental_signals
        and reward_gradients_by_group[group]["finite"]
        and reward_gradients_by_group[group]["norm"] > 0
        and gradients_by_group[group]["finite"]
        and gradients_by_group[group]["norm"] > 0
        and optimizer_steps_by_group[group]["changed_trainable_tensors"] > 0
        for group in expected_groups
    )


def _require_local_evidence_store(receipt: Any, run: Path, *paths: Path) -> None:
    """Accept only inside the admitted local store, never on the training output itself."""
    from training.sft_provenance import has_redirecting_path_component

    destination = receipt.get("local_evidence_destination") if isinstance(receipt, dict) else None
    training_output = receipt.get("output_path") if isinstance(receipt, dict) else None
    if (
        not isinstance(destination, str) or not destination
        or not isinstance(training_output, str) or not training_output
    ):
        raise ValueError("Controlled export lacks its admitted local evidence destination")
    store = Path(destination)
    if not store.is_absolute() or has_redirecting_path_component(store) or not store.is_dir():
        raise ValueError("Admitted local evidence store is not present on this host")
    store = store.resolve()
    if any(not path.resolve().is_relative_to(store) for path in (run, *paths)):
        raise ValueError("Controlled acceptance must run inside the admitted local evidence store")
    if run.resolve() == Path(training_output).resolve():
        raise ValueError("Controlled acceptance requires a supervised local copy of the run")


def accept_export(
    run: Path, reload_report: Path, manifest_sha256: str, reload_sha256: str,
    output: Path | None = None,
):
    from training.grpo_controlled import CLASSIFICATION, MODEL, PROFILE, REVISION
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
    _require_local_evidence_store(
        manifest.get("receipt"), run, reload_report, *([output] if output else [])
    )
    if manifest.get("tokenizer_identity") != {"id": MODEL, "revision": REVISION}:
        raise ValueError("Controlled export lacks its pinned tokenizer identity")
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
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise ValueError(
            "Controlled export validation requires Transformers tokenizer support"
        ) from exc
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            run / "adapter",
            local_files_only=True,
            trust_remote_code=False,
        )
    except (ImportError, OSError, ValueError) as exc:
        raise ValueError(
            "Controlled export has no usable locally saved tokenizer"
        ) from exc
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
    environmental_signals = _environmental_signal_groups(rows, tokenizer)
    if not _has_matched_optimizer_update(rows, finished[0], environmental_signals):
        raise ValueError(
            "Controlled export lacks a separate environmental-verifier policy gradient "
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
        args.run, args.reload_report, args.manifest_sha256, args.reload_sha256,
        output=args.output,
    )
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)


if __name__ == "__main__":
    main()
