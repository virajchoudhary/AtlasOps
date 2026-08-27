"""Split-safe data and causal-pairing contracts for Stage 9 GRPO.

This module intentionally has no Torch/TRL imports so the scientific contract
can be validated on any workstation without a training environment.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
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
_REJECTED_MODEL_VISIBLE_KEYS = frozenset({
    "scenario_id",
    "replay_id",
    "split_hash",
    "split_eligibility",
    "environment_identity",
    "stage9_group_id",
    "provenance_hash",
})

SUPPORTED_CHAOS_KINDS = frozenset({
    "podchaos",
    "stresschaos",
    "networkchaos",
    "dnschaos",
    "iochaos",
    "timechaos",
})
SUPPORTED_CHAOS_NAMESPACES = frozenset({
    "chaos-mesh",
})
_CANONICAL_CHAOS_KINDS = {
    "podchaos": "PodChaos",
    "stresschaos": "StressChaos",
    "networkchaos": "NetworkChaos",
    "dnschaos": "DNSChaos",
    "iochaos": "IOChaos",
    "timechaos": "TimeChaos",
}


@dataclass(frozen=True)
class ScenarioResource:
    api_version: str
    kind: str
    name: str
    namespace: str
    scenario_label: str
    manifest_sha256: str
    document_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "document_index": self.document_index,
            "kind": self.kind,
            "manifest_sha256": self.manifest_sha256,
            "name": self.name,
            "namespace": self.namespace,
            "scenario_label": self.scenario_label,
        }


@dataclass(frozen=True)
class ScenarioFaultPlan:
    scenario_id: str
    tier: str
    manifest_path: str
    manifest_sha256: str
    resources: tuple[ScenarioResource, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_path": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "resources": [r.to_dict() for r in self.resources],
            "scenario_id": self.scenario_id,
            "tier": self.tier,
        }


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


def build_scenario_fault_plan(
    scenario_id: str,
    manifests_dir: str | Path | None = None,
) -> ScenarioFaultPlan:
    """Build an exact deterministic scenario fault plan from the manifest itself.

    Parses all documents in the scenario's YAML manifest. Fails closed if any document
    contains unapproved resource kinds (e.g. ArgoCD Application, Deployment) or
    non-reversible setup semantics.
    """
    scenario_parts = scenario_id.split("/")
    if (
        len(scenario_parts) != 2
        or any(not part or part in {".", ".."} for part in scenario_parts)
        or not _SCENARIO_ID_RE.fullmatch(scenario_id)
    ):
        raise ValueError(f"invalid_scenario_id:{scenario_id}")

    tier, scenario_name = scenario_parts
    base_dir = Path(manifests_dir) if manifests_dir else Path("bench/chaos_manifests")
    manifest_file = (base_dir / f"{scenario_id}.yaml").resolve()
    base_resolved = base_dir.resolve()
    try:
        manifest_file.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError(f"manifest_path_escape:{scenario_id}") from exc

    if not manifest_file.is_file():
        raise ValueError(f"manifest_missing:{scenario_id}")

    import yaml

    content = manifest_file.read_text(encoding="utf-8")
    manifest_hash = sha256_text(content)

    resources: list[ScenarioResource] = []
    try:
        documents = list(yaml.safe_load_all(content))
    except yaml.YAMLError as exc:
        raise ValueError(f"manifest_yaml_error:{scenario_id}:{exc}") from exc

    if not documents:
        raise ValueError(f"empty_manifest:{scenario_id}")

    for doc_idx, doc in enumerate(documents):
        if doc is None:
            continue
        if not isinstance(doc, dict):
            raise ValueError(f"manifest_document_not_object:{scenario_id}:doc_{doc_idx}")

        api_version = str(doc.get("apiVersion", "")).strip()
        kind = str(doc.get("kind", "")).strip()
        kind_lower = kind.lower()
        metadata = doc.get("metadata")

        if not isinstance(metadata, dict):
            raise ValueError(f"manifest_metadata_missing:{scenario_id}:doc_{doc_idx}")

        name = str(metadata.get("name", "")).strip()
        namespace = str(metadata.get("namespace", "chaos-mesh")).strip().lower()
        labels = metadata.get("labels", {})
        if not isinstance(labels, dict):
            labels = {}
        scenario_label = str(labels.get("scenario", "")).strip()

        if api_version != "chaos-mesh.org/v1alpha1":
            raise ValueError(
                f"unsupported_scenario_document_for_stage9:{scenario_id}:"
                f"apiVersion={api_version!r} kind={kind!r}"
            )
        if kind_lower not in SUPPORTED_CHAOS_KINDS:
            raise ValueError(
                f"unsupported_scenario_document_for_stage9:{scenario_id}:kind={kind!r}"
            )
        if namespace not in SUPPORTED_CHAOS_NAMESPACES:
            raise ValueError(
                f"unauthorized_chaos_namespace_for_stage9:{scenario_id}:namespace={namespace!r}"
            )
        if not name:
            raise ValueError(f"manifest_resource_name_missing:{scenario_id}:doc_{doc_idx}")

        canonical_kind = _CANONICAL_CHAOS_KINDS.get(kind_lower, kind)
        resources.append(
            ScenarioResource(
                api_version=api_version,
                document_index=doc_idx,
                kind=canonical_kind,
                manifest_sha256=manifest_hash,
                name=name,
                namespace=namespace,
                scenario_label=scenario_label or scenario_name,
            )
        )

    if not resources:
        raise ValueError(f"manifest_has_no_supported_resources:{scenario_id}")

    return ScenarioFaultPlan(
        manifest_path=str(manifest_file),
        manifest_sha256=manifest_hash,
        resources=tuple(resources),
        scenario_id=scenario_id,
        tier=tier,
    )


def _validate_model_visible_payload(node: Any, scenario_id: str, path: str) -> None:
    """Validate the complete final model-visible payload recursively.

    Rejects hidden orchestration keys (e.g. scenario_id, replay_id, split_hash)
    and unambiguous hidden scenario identity values/aliases.
    """
    leaf_name = scenario_id.rsplit("/", 1)[-1] if "/" in scenario_id else scenario_id
    forbidden_values = {scenario_id}
    if leaf_name and len(leaf_name) > 2:
        forbidden_values.add(leaf_name)

    if isinstance(node, dict):
        for key in node:
            if key in _REJECTED_MODEL_VISIBLE_KEYS:
                raise ValueError(f"hidden_scenario_leak:{path}.{key}")
        for key, value in node.items():
            _validate_model_visible_payload(value, scenario_id, f"{path}.{key}")
    elif isinstance(node, (list, tuple, set)):
        for index, value in enumerate(node):
            _validate_model_visible_payload(value, scenario_id, f"{path}[{index}]")
    elif isinstance(node, str):
        for forbidden in forbidden_values:
            if forbidden in node:
                raise ValueError(f"hidden_scenario_value_leak:{path}")


def build_remediation_policy_prompt(
    observation: dict[str, Any],
    *,
    scenario_id: str,
) -> str:
    """Build the model-visible remediation prompt from its upstream observations."""
    if not isinstance(observation, dict):
        raise ValueError("invalid_observation_structure")

    remediation_input: dict[str, Any] = {
        "approval_mode": observation.get("approval_mode"),
        "diagnosis": observation.get("diagnosis"),
        "incident_id": observation.get("incident_id"),
        "triage": observation.get("triage"),
    }
    if "alert" in observation:
        _validate_model_visible_payload(observation.get("alert"), scenario_id, "observation.alert")
    if "recommender" in observation:
        remediation_input["recommender"] = observation.get("recommender")
    elif "recommendations" in observation:
        remediation_input["recommendations"] = observation.get("recommendations")

    payload = {
        "output_contract": {
            "format": "canonical_single_action_json",
            "keys": ["name", "arguments"],
            "actions": "exactly_one",
        },
        "role": REMEDIATION_POLICY_ROLE,
        "remediation_input": remediation_input,
    }
    _validate_model_visible_payload(payload, scenario_id, "payload")
    return canonical_json(payload)


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


def build_trl_training_record(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one validated Stage 9 row into the exact TRL dataset record.

    The TRL record exposes 'prompt' equal to the exact validated model_visible_prompt,
    paired with hidden metadata required by the online reward function.
    """
    validated = validate_remediation_training_row(row)
    prompt_str = validated["model_visible_prompt"]
    return {
        "hidden_metadata": validated["hidden_metadata"],
        "prompt": prompt_str,
        "prompt_sha256": validated["prompt_sha256"],
        "provenance_hash": validated["provenance_hash"],
        "role": validated["role"],
        "row_id": validated["row_id"],
        "stage9_group_id": validated["stage9_group_id"],
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
