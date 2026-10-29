from __future__ import annotations

from typing import Any

from agents.incident_history import _SECRET_KEY, _SECRET_TEXT

_TOKEN_COUNT_FIELDS = frozenset({
    "max_new_tokens", "min_new_tokens", "max_tokens",
    "completion_tokens", "prompt_tokens", "total_tokens",
})


def evidence_field_is_secret(key: str, value: Any) -> bool:
    return bool(_SECRET_KEY.search(key)) and not (
        key in _TOKEN_COUNT_FIELDS and type(value) is int
    )


def redact_persisted_evidence(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: redact_persisted_evidence(item)
            for key, item in value.items()
            if not isinstance(key, str) or not evidence_field_is_secret(key, item)
        }
    if isinstance(value, (list, tuple)):
        return [redact_persisted_evidence(item) for item in value]
    if isinstance(value, str):
        for pattern in _SECRET_TEXT:
            value = pattern.sub("[REDACTED]", value)
    return value
