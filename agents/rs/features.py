"""Deterministic diagnosis/context profile construction and lexical features."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from typing import Any, Mapping

from agents.rs.schemas import ContextFeatures, Runbook, SchemaError


def stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.blake2b(encoded, digest_size=32).hexdigest()

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STOPWORDS = frozenset({
    "a", "an", "and", "after", "at", "for", "from", "in", "is", "of", "on",
    "the", "this", "to", "was", "with",
})


def context_from_diagnosis(
    *,
    incident_key: str,
    triage: dict[str, Any],
    diagnosis: dict[str, Any],
    mutation_budget_remaining: int = 5,
    approval_granted: bool = False,
    deployment_recently_changed: bool | None = None,
    active_chaos_experiment: bool | None = None,
    revision_history_available: bool | None = None,
    mitigation_in_progress: bool | None = None,
) -> ContextFeatures:
    """Map existing coordinator dictionaries without importing runtime code.

    The canonical Diagnosis contract nests cause details under ``root_cause`` and
    proposals under ``recommended_actions``. Required canonical fields are checked
    explicitly; the adapter never invents missing evidence or recommendations.
    Operational facts whose observation is absent default to None (unknown).
    Approval state is fail-closed False authority policy unless explicitly granted.
    """
    if not isinstance(triage, dict) or not isinstance(diagnosis, dict):
        raise SchemaError("triage and diagnosis must be dictionaries")
    services = _string_list(triage.get("affected_services")) or [
        str(triage.get("service") or "unknown")
    ]
    if len(services) != 1:
        raise SchemaError("context builder currently requires exactly one primary affected service")
    alert_labels = triage.get("labels") if isinstance(triage.get("labels"), dict) else {}
    namespace = str(alert_labels.get("namespace") or triage.get("namespace") or "default")
    severity = str(triage.get("severity") or alert_labels.get("severity") or "unknown")
    root_cause_value = diagnosis.get("root_cause")
    if isinstance(root_cause_value, dict):
        category = root_cause_value.get("category")
        specific = root_cause_value.get("specific")
        evidence_items = root_cause_value.get("evidence")
        recommended_actions = diagnosis.get("recommended_actions")
        if not isinstance(category, str) or not category.strip():
            raise SchemaError("diagnosis.root_cause.category must be a non-empty string")
        if not isinstance(specific, str) or not specific.strip():
            raise SchemaError("diagnosis.root_cause.specific must be a non-empty string")
        if not isinstance(evidence_items, list):
            raise SchemaError("diagnosis.root_cause.evidence must be a list")
        if not isinstance(recommended_actions, list):
            raise SchemaError("diagnosis.recommended_actions must be a list")
        category_text = _text(category)
        specific_text = _text(specific)
        evidence_text = _text(evidence_items)
        actions_text = _text(recommended_actions)
    elif isinstance(root_cause_value, str) and root_cause_value.strip():
        # Coordinator's forced-conclusion compatibility form keeps root_cause
        # flat while retaining explicit evidence plus one recommendation field.
        evidence_items = diagnosis.get("evidence")
        legacy_action = diagnosis.get(
            "recommended_fix",
            diagnosis.get("next_action", diagnosis.get("recommended_actions")),
        )
        if not isinstance(evidence_items, list):
            raise SchemaError("legacy diagnosis.evidence must be a list")
        if not (
            (isinstance(legacy_action, str) and legacy_action.strip())
            or isinstance(legacy_action, list)
        ):
            raise SchemaError(
                "legacy diagnosis requires recommended_fix, next_action, or recommended_actions"
            )
        category_text = ""
        specific_text = root_cause_value
        evidence_text = _text(evidence_items)
        actions_text = _text(legacy_action)
    else:
        raise SchemaError("diagnosis.root_cause must be an object or non-empty string")
    combined = " ".join((category_text, specific_text, evidence_text, actions_text)).lower()
    fault_types = tuple(_infer_fault_types(combined))
    symptoms = tuple(dict.fromkeys(
        token for token in _TOKEN_RE.findall(f"{specific_text} {actions_text}".lower())
        if len(token) > 2 and token not in _STOPWORDS
    ))[:32]

    def _coerce_optional_bool(value: Any, name: str) -> bool | None:
        if value is None:
            return None
        if not isinstance(value, bool):
            raise SchemaError(f"{name} must be boolean or None")
        return value

    if not isinstance(approval_granted, bool):
        raise SchemaError("approval_granted must be boolean")

    return ContextFeatures(
        incident_key=incident_key,
        service=services[0],
        namespace=namespace,
        fault_types=fault_types,
        symptoms=symptoms,
        severity=severity,
        diagnosis_text=combined[:4000],
        deployment_recently_changed=_coerce_optional_bool(
            deployment_recently_changed, "deployment_recently_changed"
        ),
        active_chaos_experiment=_coerce_optional_bool(
            active_chaos_experiment, "active_chaos_experiment"
        ),
        mutation_budget_remaining=int(mutation_budget_remaining),
        approval_granted=approval_granted,
        revision_history_available=_coerce_optional_bool(
            revision_history_available, "revision_history_available"
        ),
        mitigation_in_progress=_coerce_optional_bool(
            mitigation_in_progress, "mitigation_in_progress"
        ),
    )


def canonical_recommendation_input(
    context: Any,
    template_values: Any = None,
) -> dict[str, Any]:
    """Extract a canonical deterministic dictionary of recommendation-affecting inputs.

    Binds all context, gate, budget, and template fields that affect candidate
    filtering, prerequisite evaluation, downstream blockers, or query vectors.
    """
    def _ctx_get(k: str, d: Any = None) -> Any:
        if isinstance(context, dict):
            return context.get(k, d)
        return getattr(context, k, d)

    def _str_val(val: Any) -> str:
        return str(val) if val is not None else ""

    def _str_list(val: Any) -> list[str]:
        if val is None:
            return []
        if isinstance(val, (list, tuple, set, frozenset)):
            return [str(v) for v in val]
        return [str(val)]

    def _opt_bool(val: Any) -> bool | None:
        if val is None:
            return None
        return bool(val)

    def _clean_numeric(val: Any) -> dict[str, float]:
        if not isinstance(val, dict):
            return {}
        return {str(k): float(v) for k, v in sorted(val.items(), key=lambda item: str(item[0]))}

    def _clean_templates(val: Any) -> dict[str, Any]:
        if not isinstance(val, (dict, Mapping)):
            return {}
        cleaned: dict[str, Any] = {}
        for k, v in sorted(val.items(), key=lambda item: str(item[0])):
            if isinstance(v, (int, float, bool, str)) or v is None:
                cleaned[str(k)] = v
            else:
                cleaned[str(k)] = str(v)
        return cleaned

    return {
        "incident_key": _str_val(_ctx_get("incident_key")),
        "service": _str_val(_ctx_get("service")),
        "namespace": _str_val(_ctx_get("namespace")),
        "severity": _str_val(_ctx_get("severity")),
        "fault_types": _str_list(_ctx_get("fault_types")),
        "symptoms": _str_list(_ctx_get("symptoms")),
        "diagnosis_text": _str_val(_ctx_get("diagnosis_text")),
        "deployment_recently_changed": _opt_bool(_ctx_get("deployment_recently_changed")),
        "active_chaos_experiment": _opt_bool(_ctx_get("active_chaos_experiment")),
        "revision_history_available": _opt_bool(_ctx_get("revision_history_available")),
        "mitigation_in_progress": _opt_bool(_ctx_get("mitigation_in_progress")),
        "mutation_budget_remaining": int(_ctx_get("mutation_budget_remaining", 0)),
        "approval_granted": bool(_ctx_get("approval_granted", False)),
        "workload_kind": _str_val(_ctx_get("workload_kind", "")),
        "recommendation_summary": _str_val(_ctx_get("recommendation_summary", "")),
        "numeric_features": _clean_numeric(_ctx_get("numeric_features")),
        "template_values": _clean_templates(template_values),
    }


def recommendation_input_hash(
    context: Any,
    template_values: Any = None,
) -> str:
    """Generate a canonical deterministic Blake2b fingerprint for recommendation inputs."""
    payload = canonical_recommendation_input(context, template_values)
    return stable_hash(payload)


def tokenize_for_matching(*values: Any) -> list[str]:
    tokens: list[str] = []
    for value in values:
        tokens.extend(_TOKEN_RE.findall(_text(value).lower()))
    return [token for token in tokens if len(token) > 2]


def content_vector(runbook: Runbook) -> dict[str, float]:
    """Hash query/candidate terms into one deterministic sparse vector space."""
    base_tokens = tokenize_for_matching(
        runbook.name,
        runbook.description,
        runbook.applicable_fault_types,
        runbook.tags,
    )
    structured_terms = [
        f"fault_type={value.lower()}"
        for value in runbook.applicable_fault_types
        if value.lower() != "all"
    ]
    structured_terms.extend(f"tag={value.lower()}" for value in runbook.tags)
    enriched = (
        base_tokens
        + [f"{left}_{right}" for left, right in zip(base_tokens, base_tokens[1:])]
        + structured_terms
    )
    return _hashed_vector(enriched)


def cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    return sum(weight * right.get(key, 0.0) for key, weight in left.items())


def build_content_query(context: ContextFeatures) -> dict[str, float]:
    """Build the query in exactly the same hashed feature space as runbooks.

    Service is intentionally omitted: Runbook currently has no per-service
    applicability relation, so adding it would create either zero overlap or an
    unconditional constant bonus.
    """
    base_tokens = tokenize_for_matching(
        context.fault_types,
        context.symptoms,
        context.diagnosis_text,
    )
    structured_terms = [f"fault_type={value.lower()}" for value in context.fault_types]
    return _hashed_vector(base_tokens + structured_terms)


def _hashed_vector(terms: list[str]) -> dict[str, float]:
    counts = Counter(term for term in terms if term)
    vector: dict[str, float] = {}
    for term, count in counts.items():
        index, sign = _hash_token(term)
        contribution = float(count) * sign
        key = str(index)
        vector[key] = vector.get(key, 0.0) + contribution
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm == 0.0:
        return {}
    return {key: value / norm for key, value in sorted(vector.items())}


def _text(value: Any) -> str:
    """Return stable text from JSON-like Diagnosis payloads."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _infer_fault_types(text: str) -> list[str]:
    patterns = (
        ("cpu_saturation", ("cpu",)),
        ("memory_pressure", ("memory", "oom")),
        ("disk_fault", ("disk", "enospc", "io")),
        ("network_partition", ("network", "packet", "partition", "timeout")),
        ("dns_failure", ("dns", "resolution")),
        ("clock_skew", ("clock", "skew", "jwt expiry")),
        ("pod_crash", ("crashloopbackoff", "pod kill", "restart", "not ready")),
        ("configuration_regression", ("rollback", "bad release", "recent deploy", "configmap")),
        ("traffic_surge", ("traffic", "thundering herd")),
        ("error_rate", ("error rate", "5xx", "500s")),
        ("latency", ("latency", "slow")),
    )
    found = [name for name, needles in patterns if any(needle in text for needle in needles)]
    return found or ["unknown"]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _hash_token(token: str) -> tuple[int, int]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    number = int.from_bytes(digest, "big")
    return number % 256, 1 if number & 1 else -1
