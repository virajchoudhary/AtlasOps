"""Deterministic diagnosis/context profile construction and lexical features."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any

from agents.rs.schemas import ContextFeatures, Runbook, SchemaError

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
    deployment_recently_changed: bool = False,
    active_chaos_experiment: bool = False,
) -> ContextFeatures:
    """Map existing coordinator dictionaries without importing runtime code.

    The canonical Diagnosis contract nests cause details under ``root_cause`` and
    proposals under ``recommended_actions``. Required canonical fields are checked
    explicitly; the adapter never invents missing evidence or recommendations.
    Operational booleans remain caller-supplied policy state, not Diagnosis output.
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
    root_cause = diagnosis.get("root_cause")
    if not isinstance(root_cause, dict):
        raise SchemaError("diagnosis.root_cause must be an object")
    category = root_cause.get("category")
    specific = root_cause.get("specific")
    evidence_items = root_cause.get("evidence")
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
    combined = " ".join((category_text, specific_text, evidence_text, actions_text)).lower()
    fault_types = tuple(_infer_fault_types(combined))
    symptoms = tuple(dict.fromkeys(
        token for token in _TOKEN_RE.findall(f"{specific_text} {actions_text}".lower())
        if len(token) > 2 and token not in _STOPWORDS
    ))[:32]
    return ContextFeatures(
        incident_key=incident_key,
        service=services[0],
        namespace=namespace,
        fault_types=fault_types,
        symptoms=symptoms,
        severity=severity,
        diagnosis_text=combined[:4000],
        deployment_recently_changed=bool(deployment_recently_changed),
        active_chaos_experiment=bool(active_chaos_experiment),
        mutation_budget_remaining=int(mutation_budget_remaining),
        approval_granted=bool(approval_granted),
    )


def tokenize_for_matching(*values: Any) -> list[str]:
    tokens: list[str] = []
    for value in values:
        tokens.extend(_TOKEN_RE.findall(_text(value)))
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
        f"fault_type={value}"
        for value in runbook.applicable_fault_types
        if value != "all"
    ]
    structured_terms.extend(f"tag={value}" for value in runbook.tags)
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
    structured_terms = [f"fault_type={value}" for value in context.fault_types]
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
