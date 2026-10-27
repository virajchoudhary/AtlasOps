"""Optional OpenAI-compatible model client for the Diagnosis and Remediation agents.

Works with Ollama (``http://127.0.0.1:11434/v1``) or a vLLM server. When no
endpoint answers, the lab runs its rule engine instead and says so in the UI.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen2.5:7b-instruct"


def _redact_secret(value: str | None, secret: str) -> str | None:
    if value is None or not secret:
        return value
    return value.replace(secret, "[redacted]")


def _display_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "[invalid endpoint]"
    if not host:
        return "[invalid endpoint]"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _failure_reason(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "request timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.ConnectError):
        return "connection failed"
    return "request failed"


def _validate_timeout(timeout: float) -> None:
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("Model timeout must be a positive finite number.")


def _json_object(response: httpx.Response, purpose: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        raise ValueError(f"{purpose} response is not valid JSON.") from None
    if not isinstance(payload, dict):
        raise ValueError(f"{purpose} response must be a JSON object.")
    return payload


def _model_names(response: httpx.Response) -> set[str]:
    payload = _json_object(response, "Model catalog")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("Model catalog response must contain a non-empty data list.")
    names = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Model catalog entries must contain non-empty string ids.")
        name = item.get("id")
        if not isinstance(name, str) or not name or name != name.strip():
            raise ValueError("Model catalog entries must contain non-empty string ids.")
        names.add(name)
    return names


def _completion_text(response: httpx.Response, requested_model: str) -> str:
    payload = _json_object(response, "Model completion")
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Model completion response is missing its model identity.")
    if model != requested_model:
        raise ValueError("Model completion response identity did not match the requested model.")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("Model completion response must contain a choice.")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("Model completion did not finish normally.")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("Model completion choice is missing its message.")
    refusal = message.get("refusal")
    if refusal is not None:
        if not isinstance(refusal, str):
            raise ValueError("Model completion refusal field is malformed.")
        if refusal.strip():
            raise ValueError("Model refused the request.")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Model completion content must be non-empty text.")
    return content.strip()


@dataclass
class Backend:
    mode: str  # "llm" or "rules"
    label: str = field(repr=False)
    model: str | None = field(default=None, repr=False)
    base_url: str | None = field(default=None, repr=False)
    api_key: str = field(default="", repr=False)
    timeout: float = 120.0
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)
    note: str = field(default="", repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "label": _redact_secret(self.label, self.api_key),
            "model": _redact_secret(self.model, self.api_key),
            "base_url": _redact_secret(_display_url(self.base_url), self.api_key),
            "note": _redact_secret(self.note, self.api_key),
        }

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        return httpx.AsyncClient(base_url=self.base_url or "", headers=headers,
                                 timeout=self.timeout, transport=self.transport)

    async def chat(self, system: str, user: str, *, max_tokens: int = 800) -> str:
        if self.mode != "llm":
            raise RuntimeError("No model backend is configured")
        if not isinstance(self.model, str) or not self.model.strip():
            raise RuntimeError("No requested model is configured")
        _validate_timeout(self.timeout)
        try:
            async with asyncio.timeout(self.timeout):
                async with self._client() as client:
                    response = await client.post("/chat/completions", json={
                        "model": self.model,
                        "temperature": 0,
                        "max_tokens": max_tokens,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": user}],
                    })
                    response.raise_for_status()
                    text = _completion_text(response, self.model)
                    if self.api_key and (self.api_key in text or self.api_key in self.model):
                        raise ValueError("Model response contained configured credentials; rejected.")
                    return text
        except TimeoutError:
            raise RuntimeError("Model completion request timed out.") from None
        except httpx.InvalidURL:
            raise RuntimeError("Invalid model endpoint URL.") from None
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Model completion request failed: {_failure_reason(exc)}."
            ) from None
        except ValueError as exc:
            raise RuntimeError(_redact_secret(str(exc), self.api_key)) from None


RULES = Backend(mode="rules", label="Rule engine (no model)")


async def detect_backend(
    choice: str, base_url: str | None, model: str | None, api_key: str = "",
    transport: httpx.AsyncBaseTransport | None = None, timeout: float = 120.0,
) -> Backend:
    """``choice`` is ``rules``, ``llm`` (must answer) or ``auto`` (model if reachable)."""
    if choice == "rules":
        return RULES
    _validate_timeout(timeout)
    url = (base_url or OLLAMA_BASE_URL).rstrip("/")
    wanted = model or DEFAULT_MODEL
    safe_url = _redact_secret(_display_url(url), api_key)
    safe_model = _redact_secret(wanted, api_key)
    candidate = Backend(mode="llm", label=f"{safe_model} via {safe_url}", model=wanted,
                        base_url=url, api_key=api_key, timeout=timeout, transport=transport)
    try:
        async with candidate._client() as client:
            response = await client.get("/models", timeout=3.0)
            response.raise_for_status()
            names = _model_names(response)
    except httpx.InvalidURL:
        if choice == "llm":
            raise RuntimeError("Invalid model endpoint URL.") from None
        return Backend(mode="rules", label=RULES.label,
                       note="Invalid model endpoint URL; using the rule engine.")
    except httpx.HTTPError as exc:
        reason = _failure_reason(exc)
        if choice == "llm":
            raise RuntimeError(
                f"Model endpoint {safe_url} did not answer ({reason})."
            ) from None
        return Backend(mode="rules", label=RULES.label,
                       note=f"No model endpoint answered at {safe_url} ({reason}); "
                            "using the rule engine.")
    except (ValueError, TypeError, AttributeError) as exc:
        reason = _redact_secret(str(exc), api_key) or "invalid response"
        if choice == "llm":
            raise RuntimeError(
                f"Model endpoint {safe_url} returned an invalid model catalog: {reason}"
            ) from None
        return Backend(mode="rules", label=RULES.label,
                       note=f"Model endpoint {safe_url} returned an invalid model catalog "
                            f"({reason}); using the rule engine.")
    if wanted not in names:
        message = (f"{safe_url} does not serve requested model {wanted} "
                   f"(available: {', '.join(sorted(names)[:5])})")
        message = _redact_secret(message, api_key) or message
        if choice == "llm":
            raise RuntimeError(message)
        return Backend(mode="rules", label=RULES.label, note=f"{message}; using the rule engine.")
    return candidate
