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

    Unknown fields fail closed rather than being silently invented. The caller
    supplies operational booleans because those are policy state, not diagnosis
    output.
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
    root_cause = str(diagnosis.get("root_cause") or "")
    evidence_text = " ".join(str(item) for item in _value_list(diagnosis.get("evidence")))
    recommended_fix = str(diagnosis.get("recommended_fix") or "")
    combined = " ".join((root_cause, evidence_text, recommended_fix)).lower()
    fault_types = tuple(_infer_fault_types(combined))
    symptoms = tuple(dict.fromkeys(
        token for token in _TOKEN_RE.findall(f"{root_cause} {recommended_fix}".lower())
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


def tokenize_for_matching(*values: str | tuple[str, ...] | list[str]) -> list[str]:
    tokens: list[str] = []
    for value in values:
        if isinstance(value, str):
            tokens.extend(_TOKEN_RE.findall(value.lower()))
        else:
            for item in value:
                tokens.extend(_TOKEN_RE.findall(str(item).lower()))
    return [token for token in tokens if len(token) > 2]


def content_vector(runbook: Runbook) -> dict[str, float]:
    """Hash unigrams/bigrams into a fixed, sparse 256-dimensional vector."""
    raw_tokens = tokenize_for_matching(
        runbook.name,
        runbook.description,
        runbook.applicable_fault_types,
        runbook.tags,
        (runbook.tool_name,),
    )
    enriched = raw_tokens + [f"{left}_{right}" for left, right in zip(raw_tokens, raw_tokens[1:])]
    counts = Counter(enriched)
    vector: dict[str, float] = {}
    for term, count in counts.items():
        index, sign = _hash_token(term)
        contribution = float(count) * sign
        vector[str(index)] = vector.get(str(index), 0.0) + contribution
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm == 0.0:
        return {}
    return {key: value / norm for key, value in sorted(vector.items())}


def cosine_similarity(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    return sum(weight * right.get(key, 0.0) for key, weight in left.items())


def build_content_query(context: ContextFeatures) -> dict[str, float]:
    from collections import Counter

    tokens = tokenize_for_matching(context.fault_types, context.symptoms, context.diagnosis_text)
    counts = Counter(tokens)
    total = max(sum(counts.values()), 1)
    query = {token: count / total for token, count in sorted(counts.items())}
    query["service"] = 1.0
    return query


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


def _value_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, dict):
        return list(value.values())
    return [value] if value is not None else []


def _hash_token(token: str) -> tuple[int, int]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    number = int.from_bytes(digest, "big")
    return number % 256, 1 if number & 1 else -1
