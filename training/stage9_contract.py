"""Split-safe data and causal-pairing contracts for Stage 9 GRPO.

This module intentionally has no Torch/TRL imports so the scientific contract
can be validated on any workstation without a training environment.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


STAGE9_ROW_SCHEMA_VERSION = 1
REMEDIATION_POLICY_ROLE = "remediation"
_SCENARIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9/_-]*$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HIDDEN_PROVENANCE_FIELDS = (
    "scenario_id",
    "tier",
    "replay_id",
    "environment_identity",
    "split_source",
    "split_hash",
    "split_eligibility",
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reject_hidden_identity(node: Any, scenario_id: str, path: str) -> None:
    """Reject both the key and exact value used as the out-of-band pairing key."""
    if isinstance(node, dict):
        if "scenario_id" in node:
            raise ValueError(f"hidden_scenario_leak:{path}.scenario_id")
        for key, value in node.items():
            _reject_hidden_identity(value, scenario_id, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_hidden_identity(value, scenario_id, f"{path}[{index}]")
    elif isinstance(node, str) and node == scenario_id:
        raise ValueError(f"hidden_scenario_value_leak:{path}")


def build_remediation_policy_prompt(
    observation: dict[str, Any],
    *,
    scenario_id: str,
) -> str:
    """Build the model-visible remediation prompt from its upstream observations."""
    _reject_hidden_identity(observation.get("alert"), scenario_id, "observation.alert")
    _reject_hidden_identity(observation.get("triage"), scenario_id, "observation.triage")
    return canonical_json({
        "output_contract": {
            "format": "canonical_single_action_json",
            "keys": ["name", "arguments"],
            "actions": "exactly_one",
        },
        "role": REMEDIATION_POLICY_ROLE,
        "remediation_input": {
            "approval_mode": observation.get("approval_mode"),
            "diagnosis": observation.get("diagnosis"),
            "incident_id": observation.get("incident_id"),
            "triage": observation.get("triage"),
        },
    })


def build_remediation_training_row(
    *,
    observation: dict[str, Any],
    scenario_id: str,
    tier: str,
    replay_id: str,
    environment_identity: dict[str, Any],
    split_source: str,
    split_hash: str,
    split_eligibility: str,
    row_id: str | None = None,
) -> dict[str, Any]:
    """Create one immutable model-visible prompt plus hidden pairing metadata."""
    if not _SCENARIO_ID_RE.fullmatch(scenario_id or ""):
        raise ValueError("invalid_hidden_scenario_id")
    if not _SLUG_RE.fullmatch(tier or "") or not _SLUG_RE.fullmatch(replay_id or ""):
        raise ValueError("invalid_hidden_scenario_metadata")
    if split_eligibility not in {"training", "calibration"}:
        raise ValueError("invalid_split_eligibility")
    if not isinstance(split_source, str) or not split_source.strip():
        raise ValueError("invalid_split_source")
    if not _SHA256_RE.fullmatch(split_hash or ""):
        raise ValueError("invalid_split_hash")
    if not isinstance(environment_identity, dict):
        raise ValueError("invalid_environment_identity")

    hidden_provenance = {
        "environment_identity": environment_identity,
        "replay_id": replay_id,
        "scenario_id": scenario_id,
        "split_eligibility": split_eligibility,
        "split_hash": split_hash,
        "split_source": split_source,
        "tier": tier,
    }
    model_visible_prompt = build_remediation_policy_prompt(
        observation,
        scenario_id=scenario_id,
    )
    resolved_row_id = row_id or (
        "stage9-" + sha256_json({"prompt": model_visible_prompt, **hidden_provenance})[:24]
    )
    if not _SLUG_RE.fullmatch(resolved_row_id.replace("-", "").replace(".", "")):
        raise ValueError("invalid_row_id")
    return {
        "hidden_metadata": hidden_provenance,
        "model_visible_prompt": model_visible_prompt,
        "observation": observation,
        "prompt_sha256": sha256_text(model_visible_prompt),
        "provenance_hash": sha256_json(hidden_provenance),
        "role": REMEDIATION_POLICY_ROLE,
        "row_id": resolved_row_id,
        "schema_version": STAGE9_ROW_SCHEMA_VERSION,
        "stage9_group_id": sha256_json({
            "prompt_sha256": sha256_text(model_visible_prompt),
            "provenance_hash": sha256_json(hidden_provenance),
        }),
    }


def validate_remediation_training_row(
    raw_row: dict[str, Any],
    *,
    allowed_eligibility: set[str] | list[str] | tuple[str, ...] = ("training",),
) -> dict[str, Any]:
    """Validate one remediation-only row and reject mixed-role legacy prompts."""
    if not isinstance(raw_row, dict):
        raise ValueError("training_row_not_object")
    expected_role = raw_row.get("role")
    if expected_role != REMEDIATION_POLICY_ROLE:
        raise ValueError(f"mixed_or_unknown_role:{expected_role!r}")
    if raw_row.get("schema_version") != STAGE9_ROW_SCHEMA_VERSION:
        raise ValueError("unsupported_stage9_row_schema")

    observation = raw_row.get("observation")
    hidden = raw_row.get("hidden_metadata")
    if not isinstance(observation, dict) or not isinstance(hidden, dict):
        raise ValueError("invalid_stage9_row_structure")
    missing = [key for key in _HIDDEN_PROVENANCE_FIELDS if key not in hidden]
    if missing:
        raise ValueError(f"missing_hidden_metadata:{','.join(missing)}")

    rebuilt = build_remediation_training_row(
        observation=observation,
        scenario_id=hidden["scenario_id"],
        tier=hidden["tier"],
        replay_id=hidden["replay_id"],
        environment_identity=hidden["environment_identity"],
        split_source=hidden["split_source"],
        split_hash=hidden["split_hash"],
        split_eligibility=hidden["split_eligibility"],
        row_id=raw_row.get("row_id"),
    )
    if tuple(allowed_eligibility) and hidden["split_eligibility"] not in set(allowed_eligibility):
        raise ValueError(
            f"ineligible_split:{hidden['split_eligibility']}"
        )
    if raw_row.get("prompt_sha256") != rebuilt["prompt_sha256"]:
        raise ValueError("prompt_hash_mismatch")
    if raw_row.get("provenance_hash") != rebuilt["provenance_hash"]:
        raise ValueError("provenance_hash_mismatch")
    if raw_row.get("stage9_group_id") != rebuilt["stage9_group_id"]:
        raise ValueError("group_id_mismatch")
    if raw_row.get("model_visible_prompt") != rebuilt["model_visible_prompt"]:
        raise ValueError("model_visible_prompt_was_modified")
    return rebuilt


def load_remediation_training_rows(
    path: str | Path,
    *,
    allowed_eligibility: set[str] | list[str] | tuple[str, ...] = ("training",),
) -> list[dict[str, Any]]:
    """Load and fail closed on every invalid, mixed-role, or ineligible row."""
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                raw_row = json.loads(line)
                row = validate_remediation_training_row(
                    raw_row,
                    allowed_eligibility=allowed_eligibility,
                )
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                raise ValueError(f"invalid_stage9_training_row:{line_number}:{exc}") from exc
            rows.append(row)
    if not rows:
        raise ValueError("empty_stage9_training_dataset")
    return rows


def remediation_dataset_catalogue(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Hash only normalized contract fields; raw file formatting is irrelevant."""
    fingerprints = [
        {
            "prompt_sha256": row["prompt_sha256"],
            "provenance_hash": row["provenance_hash"],
            "role": row["role"],
            "row_id": row["row_id"],
            "stage9_group_id": row["stage9_group_id"],
        }
        for row in rows
    ]
    scenario_inventory = sorted({
        canonical_json({
            "environment_identity": row["hidden_metadata"]["environment_identity"],
            "replay_id": row["hidden_metadata"]["replay_id"],
            "scenario_id": row["hidden_metadata"]["scenario_id"],
            "tier": row["hidden_metadata"]["tier"],
        })
        for row in rows
    })
    return {
        "contract_version": STAGE9_ROW_SCHEMA_VERSION,
        "role": REMEDIATION_POLICY_ROLE,
        "rows": len(rows),
        "sha256": sha256_json(fingerprints),
        "unique_stage9_groups": len({row["stage9_group_id"] for row in rows}),
        "unique_hidden_scenarios": len(scenario_inventory),
        "hidden_scenario_inventory": [json.loads(item) for item in scenario_inventory],
    }
