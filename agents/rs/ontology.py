"""Strict runbook parameter contracts without rendering or execution."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping

from agents.rs.schemas import Runbook, SchemaError, iter_placeholder_names_and_kinds

PARAMETER_TYPES = frozenset({"string", "integer", "number", "boolean", "array", "object"})
_GATE_TYPES = {
    "active_chaos_experiment": "boolean",
    "deployment_recently_changed": "boolean",
    "revision_history_available": "boolean",
    "mitigation_in_progress": "boolean",
}
_DEFAULT_RE = re.compile(r"(?:\|int:|\|str:)([^}]+)\}\}")


@dataclass(frozen=True)
class ParameterRequirement:
    name: str
    parameter_type: str
    required: bool
    default: Any = None
    source: str = "template"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise SchemaError(f"invalid parameter requirement name: {self.name!r}")
        if self.parameter_type not in PARAMETER_TYPES:
            raise SchemaError(f"invalid parameter type for {self.name}: {self.parameter_type}")
        if self.source not in {"template", "gate"}:
            raise SchemaError(f"invalid parameter source for {self.name}: {self.source}")
        if not isinstance(self.required, bool):
            raise SchemaError(f"required must be boolean for {self.name}")
        if self.default is not None and not _matches_type(self.default, self.parameter_type):
            raise SchemaError(f"default does not match {self.parameter_type} for {self.name}")
        if not isinstance(self.description, str):
            raise SchemaError(f"description must be text for {self.name}")


def derive_parameter_requirements(runbook: Runbook) -> tuple[ParameterRequirement, ...]:
    """Expose a typed contract for every template input and operational gate."""
    requirements: dict[str, ParameterRequirement] = {}
    for name, kind in iter_placeholder_names_and_kinds(runbook.parameter_template):
        if name in requirements:
            continue
        default: Any = None
        required = True
        parameter_type = "string"
        if kind == "int":
            parameter_type = "integer"
        rendered = _find_rendered_placeholder(runbook.parameter_template, name)
        if rendered is not None:
            default_match = _DEFAULT_RE.search(rendered)
            if default_match:
                required = False
                default = _parse_default(default_match.group(1), parameter_type)
        requirements[name] = ParameterRequirement(
            name=name,
            parameter_type=parameter_type,
            required=required,
            default=default,
            source="template",
            description="Typed logical input for downstream policy rendering",
        )
    for name in runbook.prerequisites:
        base_name = name.removesuffix(":int")
        if base_name in requirements:
            if name.endswith(":int"):
                # A gate can type a numeric logical input even when the downstream
                # tool schema receives its JSON-string rendering.
                requirements[base_name] = replace(
                    requirements[base_name],
                    parameter_type="integer",
                    source="gate",
                )
            continue
        parameter_type = _GATE_TYPES.get(base_name, "string")
        if name.endswith(":int"):
            parameter_type = "integer"
        requirements[base_name] = ParameterRequirement(
            name=base_name,
            parameter_type=parameter_type,
            required=True,
            source="gate",
            description="Operational prerequisite evaluated before downstream rendering",
        )
    return tuple(requirements[name] for name in sorted(requirements))


def evaluate_prerequisites(
    runbook: Runbook,
    context: Any,
    template_values: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Evaluate operational gates and inputs for a runbook against context/values.

    Returns a mapping of base prerequisite name -> "satisfied" | "unmet" | "unknown".
    Boolean operational gates evaluate to:
      - "satisfied" if explicitly True
      - "unmet" if explicitly False
      - "unknown" if None / absent / not boolean
    Template inputs evaluate to:
      - "satisfied" if supplied (or having a declared default)
      - "unknown" if missing / None / empty
    """
    values = template_values or {}
    requirements = {req.name: req for req in derive_parameter_requirements(runbook)}
    states: dict[str, str] = {}
    for prereq in runbook.prerequisites:
        base_name = prereq.removesuffix(":int")
        req = requirements.get(base_name)
        param_type = req.parameter_type if req else _GATE_TYPES.get(base_name, "string")
        has_default = req is not None and req.default is not None

        # 1. Check explicit template_values or context
        val: Any = None
        if base_name in values:
            val = values[base_name]
        elif hasattr(context, base_name):
            val = getattr(context, base_name)
        elif prereq in values:
            val = values[prereq]
        elif base_name == "workload_kind":
            val = getattr(context, "workload_kind", "deployment")
        elif base_name == "recommendation_summary":
            val = getattr(context, "recommendation_summary", "Top-K recommendation pending approval")

        # 2. Evaluate based on type and gate vs template
        if param_type == "boolean" or base_name in _GATE_TYPES:
            if val is True:
                states[base_name] = "satisfied"
            elif val is False:
                states[base_name] = "unmet"
            else:
                states[base_name] = "unknown"
        else:
            if val is not None and val != "":
                if _matches_type(val, param_type):
                    states[base_name] = "satisfied"
                else:
                    states[base_name] = "unmet"
            elif has_default:
                states[base_name] = "satisfied"
            else:
                states[base_name] = "unknown"
    return states


def validate_parameter_contract(
    runbook: Runbook,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate logical inputs and apply declared defaults; never render commands.

    A successful return does not imply that approval, ACL, verifier, or runtime
    policy checks have passed.
    """
    requirements = derive_parameter_requirements(runbook)
    allowed = {item.name for item in requirements}
    unknown = sorted(set(values).difference(allowed))
    if unknown:
        raise SchemaError(f"{runbook.action_id} rejects unknown parameters: {unknown}")
    normalized: dict[str, Any] = {}
    errors: list[str] = []
    for requirement in requirements:
        if requirement.name in values:
            value = values[requirement.name]
            if not _matches_type(value, requirement.parameter_type):
                errors.append(f"{requirement.name} must be {requirement.parameter_type}")
            else:
                normalized[requirement.name] = value
        elif requirement.default is not None:
            normalized[requirement.name] = requirement.default
        elif requirement.required:
            errors.append(f"missing {requirement.name}")
    if errors:
        raise SchemaError(
            f"invalid parameter contract for {runbook.action_id}: {'; '.join(errors)}"
        )
    return normalized


def service_matches_constraints(service: str, constraints: tuple[str, ...]) -> bool:
    """Match explicit names or trailing wildcards; an empty constraint denies."""
    if not constraints:
        return False
    return any(
        pattern == "*"
        or (pattern.endswith("*") and service.startswith(pattern[:-1]))
        or pattern == service
        for pattern in constraints
    )


def _find_rendered_placeholder(value: Any, name: str) -> str | None:
    if isinstance(value, str):
        return value if ("{{" + name) in value else None
    if isinstance(value, dict):
        for child in value.values():
            found = _find_rendered_placeholder(child, name)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _find_rendered_placeholder(child, name)
            if found is not None:
                return found
    return None


def _parse_default(raw: str, parameter_type: str) -> Any:
    if parameter_type == "integer":
        try:
            return int(raw)
        except ValueError as exc:
            raise SchemaError(f"invalid integer placeholder default: {raw!r}") from exc
    return raw


def _matches_type(value: Any, parameter_type: str) -> bool:
    if parameter_type == "boolean":
        return isinstance(value, bool)
    if parameter_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if parameter_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if parameter_type == "string":
        return isinstance(value, str)
    if parameter_type == "array":
        return isinstance(value, list)
    return isinstance(value, dict)
