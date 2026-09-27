"""Tests for agents/_http_retry.py async retry logic."""

import asyncio
import hashlib
from unittest.mock import AsyncMock, patch

import httpx
import pytest


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes, *, chunk_size: int = 256):
        self.body = body
        self.chunk_size = chunk_size
        self.bytes_yielded = 0
        self.closed = False

    async def __aiter__(self):
        for offset in range(0, len(self.body), self.chunk_size):
            chunk = self.body[offset : offset + self.chunk_size]
            self.bytes_yielded += len(chunk)
            yield chunk

    async def aclose(self):
        self.closed = True


def test_immediate_success():
    from agents._http_retry import post_with_retry

    call_count = 0

    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(mock_handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as c:
            r = await post_with_retry(c, "http://test/v1", {}, context="test")
            assert r.status_code == 200

    asyncio.run(run())
    assert call_count == 1


def test_retry_on_429():
    from agents._http_retry import post_with_retry

    call_count = 0

    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return httpx.Response(429, headers={"Retry-After": "0.1"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(mock_handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as c:
            r = await post_with_retry(c, "http://test/v1", {}, context="test429", base_backoff=0.1)
            assert r.status_code == 200

    asyncio.run(run())
    assert call_count == 3


def test_retry_on_503():
    from agents._http_retry import post_with_retry

    call_count = 0

    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(mock_handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as c:
            r = await post_with_retry(c, "http://test/v1", {}, context="test503", base_backoff=0.1)
            assert r.status_code == 200

    asyncio.run(run())
    assert call_count == 2


def test_non_retryable_error_returns_immediately():
    from agents._http_retry import post_with_retry

    call_count = 0

    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(400, json={"error": "bad request"})

    transport = httpx.MockTransport(mock_handler)

    async def run():
        async with httpx.AsyncClient(transport=transport) as c:
            r = await post_with_retry(c, "http://test/v1", {}, context="test400", base_backoff=0.1)
            assert r.status_code == 400

    asyncio.run(run())
    assert call_count == 1


def test_bounded_post_stops_reading_after_limit():
    from agents._http_retry import ResponseBodyTooLargeError, post_with_retry

    limit = 1024
    stream = CountingStream(b"x" * 1_000_000)

    async def mock_handler(request):
        return httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            stream=stream,
            request=request,
        )

    transport = httpx.MockTransport(mock_handler)

    async def run():
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            with pytest.raises(ResponseBodyTooLargeError) as exc_info:
                await post_with_retry(
                    client,
                    "http://test/v1",
                    {},
                    context="bounded",
                    max_response_bytes=limit,
                )
        return exc_info.value

    error = asyncio.run(run())

    assert stream.bytes_yielded < len(stream.body)
    assert stream.bytes_yielded <= limit + stream.chunk_size
    assert stream.closed is True
    assert error.body_bytes_read == limit
    assert error.limit_bytes == limit
    assert error.body_prefix_sha256 == hashlib.sha256(stream.body[:limit]).hexdigest()


@pytest.mark.parametrize("retry_status", [429, 503])
def test_bounded_post_preserves_retry_status_semantics(monkeypatch, retry_status):
    from agents._http_retry import post_with_retry

    retry_stream = CountingStream(b"discarded retry body")
    call_count = 0

    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            headers = {"Retry-After": "0.1"} if retry_status == 429 else {}
            return httpx.Response(
                retry_status,
                headers=headers,
                stream=retry_stream,
                request=request,
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=CountingStream(b'{"ok":true}'),
            request=request,
        )

    transport = httpx.MockTransport(mock_handler)

    async def run():
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            return await post_with_retry(
                client,
                "http://test/v1",
                {},
                context="bounded-retry",
                base_backoff=0.1,
                max_attempts=2,
                max_response_bytes=1024,
            )

    async def sleep_after_close(_wait):
        assert retry_stream.closed is True

    sleep = AsyncMock(side_effect=sleep_after_close)
    with patch("agents._http_retry.asyncio.sleep", sleep):
        response = asyncio.run(run())

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert call_count == 2
    assert retry_stream.bytes_yielded == 0
    assert retry_stream.closed is True
    sleep.assert_awaited_once()
