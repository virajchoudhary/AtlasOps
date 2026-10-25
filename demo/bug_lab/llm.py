"""Optional OpenAI-compatible model client for the Diagnosis and Remediation agents.

Works with Ollama (``http://127.0.0.1:11434/v1``) or a vLLM server. When no
endpoint answers, the lab runs its rule engine instead and says so in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen2.5:7b-instruct"


@dataclass
class Backend:
    mode: str  # "llm" or "rules"
    label: str
    model: str | None = None
    base_url: str | None = None
    api_key: str = ""
    timeout: float = 120.0
    transport: httpx.AsyncBaseTransport | None = None
    note: str = ""

    def public(self) -> dict[str, Any]:
        return {"mode": self.mode, "label": self.label, "model": self.model,
                "base_url": self.base_url, "note": self.note}

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        return httpx.AsyncClient(base_url=self.base_url or "", headers=headers,
                                 timeout=self.timeout, transport=self.transport)

    async def chat(self, system: str, user: str, *, max_tokens: int = 800) -> str:
        if self.mode != "llm":
            raise RuntimeError("No model backend is configured")
        async with self._client() as client:
            response = await client.post("/chat/completions", json={
                "model": self.model,
                "temperature": 0,
                "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
            })
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"] or "")


RULES = Backend(mode="rules", label="Rule engine (no model)")


async def detect_backend(
    choice: str, base_url: str | None, model: str | None, api_key: str = "",
    transport: httpx.AsyncBaseTransport | None = None,
) -> Backend:
    """``choice`` is ``rules``, ``llm`` (must answer) or ``auto`` (model if reachable)."""
    if choice == "rules":
        return RULES
    url = (base_url or OLLAMA_BASE_URL).rstrip("/")
    wanted = model or DEFAULT_MODEL
    candidate = Backend(mode="llm", label=f"{wanted} via {url}", model=wanted,
                        base_url=url, api_key=api_key, transport=transport)
    try:
        async with candidate._client() as client:
            response = await client.get("/models", timeout=3.0)
            response.raise_for_status()
            names = {str(item.get("id")) for item in response.json().get("data", [])}
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        if choice == "llm":
            raise RuntimeError(f"Model endpoint {url} did not answer: {exc}") from exc
        return Backend(mode="rules", label=RULES.label,
                       note=f"No model endpoint answered at {url}; using the rule engine.")
    if names and wanted not in names:
        message = f"{url} does not serve {wanted} (available: {', '.join(sorted(names)[:5])})"
        if choice == "llm":
            raise RuntimeError(message)
        return Backend(mode="rules", label=RULES.label, note=f"{message}; using the rule engine.")
    return candidate
