"""Async HTTP POST with retry for HF Inference Router 429/5xx and transient errors."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import httpx

log = logging.getLogger("atlasops.http_retry")


class ResponseBodyTooLargeError(httpx.HTTPError):
    """Raised when a bounded streaming POST observes a body over its limit."""

    def __init__(
        self,
        *,
        status_code: int,
        limit_bytes: int,
        body_prefix_sha256: str,
    ) -> None:
        super().__init__("HTTP response body exceeded configured byte limit")
        self.status_code = status_code
        self.limit_bytes = limit_bytes
        self.body_bytes_read = limit_bytes
        self.body_prefix_sha256 = body_prefix_sha256


async def _read_limited_response(
    response: httpx.Response,
    max_response_bytes: int,
) -> httpx.Response:
    body = bytearray()
    digest = hashlib.sha256()
    chunk_size = min(64 * 1024, max_response_bytes + 1)

    async for chunk in response.aiter_raw(chunk_size=chunk_size):
        remaining = max_response_bytes - len(body)
        if len(chunk) > remaining:
            digest.update(chunk[:remaining])
            raise ResponseBodyTooLargeError(
                status_code=response.status_code,
                limit_bytes=max_response_bytes,
                body_prefix_sha256=digest.hexdigest(),
            )
        body.extend(chunk)
        digest.update(chunk)

    headers = {
        name: value
        for name, value in response.headers.items()
        if name.lower() != "transfer-encoding"
    }
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=bytes(body),
        request=response.request,
    )


def _retry_delay(
    response: httpx.Response,
    *,
    attempt: int,
    max_attempts: int,
    base_backoff: float,
    context: str,
) -> float | None:
    if response.status_code == 429:
        ra = response.headers.get("Retry-After")
        try:
            wait = float(ra) if ra is not None else base_backoff * (attempt + 1)
        except (TypeError, ValueError):
            wait = base_backoff * (attempt + 1)
        wait = min(max(wait, 0.5), 60.0)
        log.warning(
            "HF 429 (%s); retry %d/%d after %.1fs",
            context,
            attempt + 1,
            max_attempts,
            wait,
        )
        return wait
    if 500 <= response.status_code < 600 and attempt < max_attempts - 1:
        wait = base_backoff * (attempt + 1)
        log.warning(
            "HF %d (%s); retry %d/%d after %.1fs",
            response.status_code,
            context,
            attempt + 1,
            max_attempts,
            wait,
        )
        return wait
    return None


async def post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    json: dict[str, Any],
    *,
    context: str = "",
    max_attempts: int = 5,
    base_backoff: float = 1.5,
    max_response_bytes: int | None = None,
) -> httpx.Response:
    """POST with retry on 429, 5xx, and transient connection errors.

    Returns the first response not scheduled for retry or raises the last exception.
    The optional response limit streams raw bytes and raises with a prefix fingerprint
    instead of buffering an oversized body; the default keeps legacy behavior.
    """
    if max_response_bytes is not None and max_response_bytes < 0:
        raise ValueError("max_response_bytes must be non-negative")

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            if max_response_bytes is None:
                r = await client.post(url, json=json)
                retry_delay = _retry_delay(
                    r,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    base_backoff=base_backoff,
                    context=context,
                )
                if retry_delay is not None:
                    await asyncio.sleep(retry_delay)
                    continue
            else:
                retry_delay = None
                async with client.stream("POST", url, json=json) as streamed_response:
                    retry_delay = _retry_delay(
                        streamed_response,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        base_backoff=base_backoff,
                        context=context,
                    )
                    if retry_delay is None:
                        return await _read_limited_response(
                            streamed_response,
                            max_response_bytes,
                        )
                if retry_delay is not None:
                    await asyncio.sleep(retry_delay)
                    continue
            return r
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                wait = base_backoff * (attempt + 1)
                log.warning(
                    "HF transient %s (%s); retry %d/%d after %.1fs",
                    type(e).__name__, context, attempt + 1, max_attempts, wait,
                )
                await asyncio.sleep(wait)
                continue
            raise
    if last_exc:
        raise last_exc
    raise httpx.HTTPError(f"post_with_retry exhausted {max_attempts} attempts ({context})")
