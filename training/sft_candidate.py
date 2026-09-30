"""Non-executing, fail-closed admission for the versioned SFT review candidate.

Technical checks are not D3 approval. No function here invokes a tool executor,
model loader, approval callback, or training dependency.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from config.splits import TRAIN_SPLIT
from training.sft_provenance import (
    MAX_VERIFIED_SFT_MANIFEST_BYTES,
    REPO_ROOT,
    TrainingCorpusSnapshot,
    _read_bounded_snapshot,
    canonical_bytes_sha256,
    canonical_json_sha256,
)
from training.sft_rendering import (
    SFT_EXAMPLE_FORMAT,
    SFT_ROLES,
    normalize_tool_arguments,
    prepare_example_for_training,
    render_messages,
    validate_tool_call_role_acl,
    validate_message_sequence,
)

SCHEMA_VERSION = "atlasops-sft-candidate-v1"
CORPUS_VERSION = "train-candidate-v1"
DATA_ORIGIN = "scenario_derived_synthetic_review_candidate"
OUTCOMES = frozenset(
    {"successful", "failed", "inconclusive", "unresolved", "blocked", "malformed"}
)
SOURCE_FILES = (
    "training/build_sft_candidate.py",
    "training/sft_candidate.py",
    "training/sft_rendering.py",
    "training/templates/qwen2_5_tool_sft.jinja",
    "agents/tool_policy.py",
    "agents/approval.py",
    "agents/coordinator.py",
    "config/scenario_catalog.py",
    "requirements/train-constraints.txt",
    "agents/prompts/triage.md",
    "agents/prompts/diagnosis.md",
    "agents/prompts/remediation.md",
    "agents/prompts/comms.md",
)
FORBIDDEN_MESSAGE_KEYS = frozenset(
    {
        "scenario_id", "case_id", "split", "tier", "expected_root_cause",
        "expected_remediation", "expected_alert", "judge", "judge_score",
        "reward", "reward_contract", "data_origin", "corpus_version",
    }
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"SFT candidate admission: {message}")


def _object_text(content: Any, *, label: str) -> dict[str, Any]:
    _require(isinstance(content, str) and bool(content.strip()), f"{label} must be JSON text")
    try:
        value = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"SFT candidate admission: {label} must be JSON object") from exc
    _require(isinstance(value, dict), f"{label} must be JSON object")
    _scan_message(value)
    return value


def _validate_schema(value: Any, schema: dict[str, Any]) -> None:
    """Validate the restricted JSON-schema vocabulary actually used at runtime."""
    types = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: type(v) is int,
        "number": lambda v: type(v) in (int, float),
        "boolean": lambda v: type(v) is bool,
    }
    kind = schema.get("type")
    _require(kind in types and types[kind](value), f"tool argument type must be {kind}")
    if "enum" in schema:
        _require(value in schema["enum"], "tool argument is outside runtime enum")
    if kind == "object":
        properties = schema.get("properties", {})
        _require(set(schema.get("required", ())) <= set(value), "required tool argument missing")
        if schema.get("additionalProperties") is False:
            _require(set(value) <= set(properties), "tool argument is not exposed by runtime schema")
        for name, item in value.items():
            if name in properties:
                _validate_schema(item, properties[name])
    if kind == "array" and "items" in schema:
        for item in value:
            _validate_schema(item, schema["items"])


def _scan_message(value: Any) -> None:
    if isinstance(value, dict):
        from agents.coordinator import _MODEL_FORBIDDEN_KEYS

        _require(
            not ({key.strip().casefold() for key in value}
                 & (FORBIDDEN_MESSAGE_KEYS | _MODEL_FORBIDDEN_KEYS)),
            "label/provenance key in model messages",
        )
        for item in value.values():
            _scan_message(item)
    elif isinstance(value, list):
        for item in value:
            _scan_message(item)
    elif type(value) is float:
        _require(math.isfinite(value), "non-finite number in messages")
    elif isinstance(value, str):
        # Membership metadata may be inspected; held-out labels/outcomes are never read.
        from config.splits import LEADERBOARD_SPLIT, TEST_SPLIT, VAL_SPLIT

        _require(
            not any(sid in value for sid in set(VAL_SPLIT + TEST_SPLIT + LEADERBOARD_SPLIT)),
            "held-out scenario identifier in model messages",
        )
        _require(not any(sid in value for sid in TRAIN_SPLIT), "scenario identifier in model messages")
        for key in FORBIDDEN_MESSAGE_KEYS:
            _require(f'"{key}"' not in value, "serialized label/provenance key in model messages")
        for token in (
            "<|im_start|>", "<|im_end|>", "<tool_call>", "</tool_call>",
            "<tool_response>", "</tool_response>", "<tools>", "</tools>",
        ):
            _require(token not in value, "template delimiter injected into model content")


def _validate_row(row: dict[str, Any]) -> tuple[dict[str, Any], list[str], str]:
    from agents.approval import approval_mode_for_severity
    from agents.coordinator import (
        _REMEDIATION_CONTROL_KEY,
        _check_remediation_action_preconditions,
        _check_tool_policy,
        _is_mutating_action,
        _tool_schema,
    )

    _require(row.get("schema_version") == SCHEMA_VERSION, "unknown schema version")
    _require(row.get("corpus_version") == CORPUS_VERSION, "unknown corpus version")
    _require(row.get("format") == SFT_EXAMPLE_FORMAT, "invalid wire format")
    _require(row.get("scenario_id") in TRAIN_SPLIT, "non-Train scenario")
    _require(row.get("role") in SFT_ROLES, "unsupported role")
    _require(row.get("outcome") in OUTCOMES, "invalid outcome")
    _require(row.get("simulation") is True, "synthetic provenance missing")
    _require(isinstance(row.get("case_id"), str) and bool(row["case_id"]), "case id missing")
    safety = row.get("safety")
    _require(isinstance(safety, dict), "safety context missing")
    severity = safety.get("severity")
    _require(severity in {"P0", "P1", "P2", "P3"}, "invalid severity")
    mode = approval_mode_for_severity(severity)
    _require(safety.get("approval_mode") == mode, "approval mode differs from runtime")
    approval = safety.get("approval")
    _require(isinstance(approval, dict), "approval evidence missing")
    decision = approval.get("status")
    _require(
        decision in {"approved", "rejected", "timeout", "missing", "malformed", "manual", "auto"},
        "invalid approval decision",
    )
    if severity == "P0":
        _require(decision == "manual", "P0 must remain manual-only")
    elif severity in {"P2", "P3"}:
        _require(decision == "auto", "auto severity has inconsistent approval")
    else:
        _require(decision not in {"auto", "manual"}, "P1 cannot be auto/manual")
    if decision == "approved":
        _require(
            isinstance(approval.get("approved_by"), str)
            and bool(approval["approved_by"].strip()),
            "approved decision lacks named operator",
        )
    else:
        _require(not approval.get("approved_by"), "non-approved decision has approver")

    messages = row.get("messages")
    _require(isinstance(messages, list) and len(messages) >= 3, "message sequence missing")
    _require(all(isinstance(m, dict) for m in messages), "message must be object")
    _require(messages[0].get("role") == "system", "canonical system placeholder must be first")
    _require(sum(m.get("role") == "system" for m in messages) == 1, "multiple system messages")
    _require(messages[1].get("role") == "user", "input context missing")
    _require(messages[-1].get("role") == "assistant", "conclusion missing")
    validate_message_sequence(messages)
    context = _object_text(messages[1].get("content"), label="input context")
    _require(context.get("simulation") is True, "input is not marked simulated")
    incident_id = context.get("incident_id")
    _require(isinstance(incident_id, str) and bool(incident_id), "incident id missing")
    if row["role"] == "remediation":
        _require(context.get("triage", {}).get("severity") == severity, "severity context mismatch")
        _require(context.get("approval_mode") == mode, "input approval mode mismatch")
        _require(context.get("approval") == approval, "input approval evidence mismatch")
        _require(approval.get("incident_id") == incident_id, "approval incident mismatch")
    blocked = severity == "P0" or (severity == "P1" and decision != "approved")
    state = copy.deepcopy(context)
    state[_REMEDIATION_CONTROL_KEY] = {"enforce_action_preconditions": True}
    tool_names: list[str] = []
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    verifier_after_mutation = False
    mutation_observed_resolved = False
    for index, message in enumerate(messages):
        _require(message.get("role") in {"system", "user", "assistant", "tool"}, "unknown message role")
        _require(isinstance(message.get("content"), str), "message content must be text")
        if message["role"] != "system":
            _scan_message(message["content"])
        if message["role"] == "user":
            update = _object_text(message["content"], label="user observation")
            _require(index == 1, "host verifier must be nested in paired tool observation")
            state.update(update)
        _require(
            "tool_calls" not in message or isinstance(message["tool_calls"], list),
            "tool_calls must be an array",
        )
        if message["role"] != "assistant":
            _require(not message.get("tool_calls"), "non-assistant carries tool calls")
        if mutation_observed_resolved and message["role"] == "assistant":
            _require(
                index == len(messages) - 1 and not message.get("tool_calls"),
                "runtime controller must stop after verified recovery",
            )
        for call in message.get("tool_calls", []):
            _require(message["role"] == "assistant", "tool call outside assistant")
            _require(call.get("type") == "function", "invalid tool call type")
            name = call["function"]["name"]
            args = normalize_tool_arguments(call["function"].get("arguments"))
            _scan_message(args)
            _validate_schema(args, _tool_schema(name)["function"]["parameters"])
            _require(_check_tool_policy(row["role"], name, args, state) is None, "runtime policy refused tool")
            _require(
                _check_remediation_action_preconditions(name, args, state) is None,
                "runtime evidence precondition refused action",
            )
            if row["role"] == "remediation" and blocked:
                _require(False, "host-blocked remediation cannot invoke the model/tools")
            if _is_mutating_action(name, args):
                _require(row["role"] == "remediation" and not blocked, "mutation lacks approval")
            calls[call["id"]] = (name, args)
            tool_names.append(name)
        if message["role"] == "tool":
            matched = calls.get(message.get("tool_call_id"))
            _require(
                matched is not None and message.get("tool_name") == matched[0],
                "tool observation name does not match call",
            )
            output = _object_text(message["content"], label="tool observation")
            _require(
                output.get("simulation") is True,
                "tool observation must be explicitly synthetic",
            )
            _scan_message(output)
            if _is_mutating_action(*matched):
                observation = output.get("verifier_observation")
                _require(isinstance(observation, dict), "mutation lacks paired host verifier")
                _require(
                    observation.get("tool_call_id") == message["tool_call_id"]
                    and observation.get("action_tool") == matched[0]
                    and observation.get("action_args") == matched[1]
                    and observation.get("observation_source") == "host_verifier_simulation"
                    and observation.get("simulation") is True
                    and type(observation.get("env_resolved")) is bool,
                    "paired verifier is not bound to mutation",
                )
                state["environment_observation"] = observation
                verifier_after_mutation = observation["env_resolved"]
                mutation_observed_resolved = observation["env_resolved"]
        if row["role"] == "remediation" and message["role"] == "tool":
            state.setdefault("observations", []).append(
                {
                    "tool": message["tool_name"],
                    "args": matched[1],
                    "output": _object_text(message["content"], label="tool observation"),
                }
            )
    if row["role"] == "remediation" and blocked:
        _require(row.get("execution_stage") == "host_gate_not_invoked", "blocked row misstates execution")
        _require(row["outcome"] == "blocked", "blocked remediation claims execution outcome")
    final = _object_text(messages[-1]["content"], label="conclusion")
    required_conclusions = {
        "triage": {"incident_id", "severity", "title", "blast_radius", "correlated_alerts", "next_agent", "handoff_notes"},
        "diagnosis": {"incident_id", "root_cause", "blast_radius_update", "next_agent", "recommended_actions"},
        "remediation": {"incident_id", "proposed_actions", "executed_actions", "outcome", "time_to_resolve_seconds", "next_agent", "handoff_notes"},
        "comms": {"incident_id", "slack_posted", "postmortem_path", "summary_for_dashboard", "lessons_learned"},
    }
    _require(required_conclusions[row["role"]] <= set(final), "canonical role conclusion missing fields")
    _require(final["incident_id"] == incident_id, "conclusion incident differs from input")
    limits = {"triage": 4, "diagnosis": 8, "remediation": 5, "comms": 3}
    _require(len(tool_names) <= limits[row["role"]], "runtime role tool budget exceeded")
    if row["role"] == "triage":
        _require(final["severity"] == severity, "triage and approval severity disagree")
        _require(final["next_agent"] == "diagnosis", "triage handoff missing")
    if row["role"] == "diagnosis":
        _require(final["next_agent"] == "remediation", "diagnosis handoff missing")
        _require(isinstance(final["root_cause"], dict), "structured root cause missing")
        _require(
            final["root_cause"].get("category") in {
                "deploy", "resource", "network", "dependency", "config", "external", "unknown"
            },
            "unknown root cause category",
        )
    if row["role"] == "comms":
        p1_blocked = severity == "P1" and decision != "approved"
        if p1_blocked:
            _require(
                not tool_names and row.get("execution_stage") == "host_gate_not_invoked"
                and final.get("slack_posted") is False,
                "P1 host-blocked communications cannot invoke model/tools",
            )
        else:
            _require(tool_names == ["slack_post_update", "postmortem_draft"], "canonical communications workflow missing")
    if row["role"] == "remediation":
        expected_outcome = {
            "successful": "resolved",
            "failed": "unresolved",
            "unresolved": "unresolved",
            "blocked": "escalated",
            "inconclusive": "escalated",
            "malformed": "escalated",
        }[row["outcome"]]
        _require(final.get("outcome") == expected_outcome, "runtime conclusion outcome mismatch")
        _require(final.get("next_agent") == "comms", "remediation handoff missing")
        executed = final.get("executed_actions")
        _require(isinstance(executed, list), "executed actions missing")
        for action in executed:
            _require(isinstance(action, dict), "executed action must be object")
            matches = [
                (call_id, args) for call_id, (name, args) in calls.items()
                if name == action.get("tool") and args == action.get("args")
            ]
            _require(bool(matches), "claimed action has no paired call")
            responses = [
                _object_text(m["content"], label="tool response")
                for m in messages if m["role"] == "tool"
                and m["tool_call_id"] in {call_id for call_id, _ in matches}
            ]
            if action.get("result") == "success":
                _require(any(r.get("success") is True for r in responses), "claimed tool success lacks response")
            _require(action.get("result") in {"success", "failed"}, "invalid executed-action result")
        _require(
            final.get("time_to_resolve_seconds") is None,
            "candidate cannot fabricate incident timing",
        )
    else:
        _require(final.get("outcome") == row["outcome"], "outcome label differs from conclusion")
    if row["role"] in {"remediation", "comms"} and row["outcome"] == "successful":
        _require(not blocked, "successful incident was approval-blocked")
        _require(
            state.get("environment_observation", {}).get("env_resolved") is True,
            "success lacks host verifier observation",
        )
        _require(
            state["environment_observation"].get("simulation") is True,
            "verifier observation must be explicitly synthetic",
        )
    if row["role"] == "remediation" and row["outcome"] == "successful":
        _require(verifier_after_mutation, "resolved remediation lacks action-bound post-mutation verifier")
        _require(
            any(
                action.get("result") == "success"
                and _is_mutating_action(action["tool"], action["args"])
                for action in final["executed_actions"]
            ),
            "resolved remediation lacks successful recorded mutation",
        )
        _require(
            any(_is_mutating_action(name, args) for name, args in calls.values()),
            "resolved remediation lacks mutation",
        )
        _require(
            mutation_observed_resolved
            and row.get("supervision_source") == "synthetic_controller_resolution",
            "runtime verified resolution must identify controller-generated supervision",
        )
    prepared = prepare_example_for_training(row)
    text, spans = render_messages(prepared["messages"], prepared["tools"], track_generation=True)
    assistant_turns = [m for m in messages if m["role"] == "assistant"]
    _require(bool(text) and len(spans) == len(assistant_turns), "assistant generation spans mismatch")
    fingerprint = canonical_json_sha256(
        [
            {"content": m["content"], "calls": [
                {"name": c["function"]["name"], "arguments": normalize_tool_arguments(c["function"]["arguments"])}
                for c in m.get("tool_calls") or []
            ]}
            for m in assistant_turns
        ]
    )
    return safety, tool_names, fingerprint


def inspect_candidate_rows(rows: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> dict[str, Any]:
    _require(bool(rows), "empty candidate")
    validate_tool_call_role_acl(rows)
    identities: set[tuple[str, str, str]] = set()
    fingerprints: set[tuple[str, str]] = set()
    roles: Counter = Counter()
    tiers: Counter = Counter()
    tools: Counter = Counter()
    role_tools: dict[str, Counter] = {role: Counter() for role in SFT_ROLES}
    outcomes: Counter = Counter()
    role_outcomes: dict[str, Counter] = {role: Counter() for role in SFT_ROLES}
    decisions: Counter = Counter()
    severities: Counter = Counter()
    scenario_ids: set[str] = set()
    case_roles: dict[tuple[str, str], set[str]] = {}
    case_safety: dict[tuple[str, str], str] = {}
    for row in rows:
        safety, names, fingerprint = _validate_row(row)
        identity = (row["scenario_id"], row["case_id"], row["role"])
        _require(identity not in identities, "duplicate scenario/case/role")
        identities.add(identity)
        case_key = (row["scenario_id"], row["case_id"])
        case_roles.setdefault(case_key, set()).add(row["role"])
        safety_hash = canonical_json_sha256(safety)
        _require(
            case_key not in case_safety or case_safety[case_key] == safety_hash,
            "roles disagree on incident safety/approval evidence",
        )
        case_safety[case_key] = safety_hash
        _require((row["role"], fingerprint) not in fingerprints, "exact duplicated assistant targets")
        fingerprints.add((row["role"], fingerprint))
        scenario_ids.add(row["scenario_id"])
        roles[row["role"]] += 1
        tiers[row["tier"]] += 1
        tools.update(names)
        role_tools[row["role"]].update(names)
        outcomes[row["outcome"]] += 1
        role_outcomes[row["role"]][row["outcome"]] += 1
        decisions[f'{safety["severity"]}:{safety["approval"]["status"]}'] += 1
        severities[safety["severity"]] += 1
    _require(scenario_ids == set(TRAIN_SPLIT), "candidate must cover exactly frozen Train")
    _require(set(roles) == set(SFT_ROLES), "candidate must cover all runtime roles")
    _require(
        all(covered == set(SFT_ROLES) for covered in case_roles.values()),
        "every incident case must cover all four roles",
    )
    _require(set(outcomes) == OUTCOMES, "outcome diversity incomplete")
    _require(
        {"P0:manual", "P1:approved", "P1:rejected", "P1:timeout", "P1:missing", "P1:malformed"}
        <= set(decisions),
        "approval diversity incomplete",
    )
    return {
        "total_examples": len(rows),
        "total_scenarios": len(scenario_ids),
        "scenario_ids": list(TRAIN_SPLIT),
        "role_distribution": dict(sorted(roles.items())),
        "tier_distribution": dict(sorted(tiers.items())),
        "tool_distribution": dict(sorted(tools.items())),
        "role_tool_distribution": {
            role: dict(sorted(values.items())) for role, values in role_tools.items()
        },
        "outcome_distribution": dict(sorted(outcomes.items())),
        "role_outcome_distribution": {
            role: dict(sorted(values.items())) for role, values in role_outcomes.items()
        },
        "approval_distribution": dict(sorted(decisions.items())),
        "severity_distribution": dict(sorted(severities.items())),
        "total_tool_calls": sum(tools.values()),
        "exact_duplicate_assistant_targets": 0,
        "split_membership_sha256": canonical_json_sha256(list(TRAIN_SPLIT)),
        "technical_admissibility": "PASS",
        "d3_approval": "PENDING",
    }


def candidate_manifest(rows: list[dict[str, Any]], raw: bytes, source_sha: str) -> dict[str, Any]:
    from training.build_sft_candidate import CONSTRUCTION_CONFIG, serialize_candidate_rows

    _require(len(source_sha) == 40 and all(c in "0123456789abcdef" for c in source_sha), "source SHA missing")
    _require(
        raw.replace(b"\r\n", b"\n") == serialize_candidate_rows(rows),
        "corpus bytes do not match canonical row serialization",
    )
    inventory = inspect_candidate_rows(rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "corpus_version": CORPUS_VERSION,
        "format": SFT_EXAMPLE_FORMAT,
        "data_origin": DATA_ORIGIN,
        "synthetic": True,
        "split": "train",
        "quarantined_populations": ["Validation", "adversarial", "leaderboard", "final-Test"],
        "held_out_outcomes_accessed": False,
        "assistant_only_loss": True,
        "canonical_hash_method": "sha256_utf8_crlf_to_lf",
        "source_git_sha": source_sha,
        "construction_method": "deterministic_hand_authored_train_contrastive_recipes",
        "construction_config": CONSTRUCTION_CONFIG,
        "construction_config_sha256": canonical_json_sha256(CONSTRUCTION_CONFIG),
        "source_file_sha256_canonical_lf": {
            name: canonical_bytes_sha256((REPO_ROOT / name).read_bytes()) for name in SOURCE_FILES
        },
        "corpus_sha256_canonical_lf": canonical_bytes_sha256(raw),
        "corpus_sha256_raw": hashlib.sha256(raw).hexdigest(),
        "rows_semantic_sha256": canonical_json_sha256(rows),
        **inventory,
    }


def read_candidate_manifest(corpus_path: Path) -> dict[str, Any]:
    raw, status = _read_bounded_snapshot(
        corpus_path.parent / "sft_corpus_manifest.json", MAX_VERIFIED_SFT_MANIFEST_BYTES
    )
    _require(raw is not None, f"manifest unavailable ({status})")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError("SFT candidate admission: invalid manifest") from exc
    _require(isinstance(value, dict), "manifest must be object")
    return value


def validate_source_revision(manifest: dict[str, Any]) -> None:
    """Require the named local Git commit to contain the exact construction code."""
    sha = manifest["source_git_sha"]
    try:
        resolved = subprocess.run(
            ["git", "rev-parse", f"{sha}^{{commit}}"], cwd=REPO_ROOT,
            check=True, capture_output=True,
        ).stdout.decode().strip()
        _require(resolved == sha, "source is not the named immutable commit")
        for name, digest in manifest["source_file_sha256_canonical_lf"].items():
            content = subprocess.run(
                ["git", "show", f"{sha}:{name}"], cwd=REPO_ROOT,
                check=True, capture_output=True,
            ).stdout
            _require(canonical_bytes_sha256(content) == digest, "source file differs from immutable commit")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("SFT candidate admission: immutable source revision unavailable") from exc


def validate_candidate_snapshot(snapshot: TrainingCorpusSnapshot, manifest: dict[str, Any]) -> dict[str, Any]:
    from training.build_sft_candidate import build_candidate_rows

    rows = list(snapshot.rows)
    expected = candidate_manifest(rows, snapshot.raw_bytes, manifest.get("source_git_sha", ""))
    _require(manifest == expected, "manifest/provenance/distribution hash mismatch")
    validate_source_revision(manifest)
    _require(
        canonical_json_sha256(build_candidate_rows()) == manifest["rows_semantic_sha256"],
        "candidate does not reproduce from pinned construction method",
    )
    return expected


def refuse_unapproved_candidate(snapshot: TrainingCorpusSnapshot) -> None:
    """Candidate v1 is review-only; no general approval/bypass switch exists."""
    if any(row.get("schema_version") == SCHEMA_VERSION or "corpus_version" in row for row in snapshot.rows):
        manifest = read_candidate_manifest(snapshot.source_path)
        validate_candidate_snapshot(snapshot, manifest)
        raise ValueError("SFT candidate technical checks passed; D3 approval PENDING, training refused")
    raise ValueError("SFT training refused: no versioned technically reviewed D3-approved corpus")
