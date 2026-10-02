"""Read-only console responsiveness and bounded feed retention."""

import asyncio
import inspect
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "path,target",
    [
        ("/ui/catalog", "catalog"),
        ("/ui/attempts/EXP-STAGE4-SF002-010.json", "attempt_detail"),
        ("/audit/log", "audit_log.tail"),
        ("/audit/verify", "audit_log.verify_integrity"),
    ],
)
def test_slow_console_reads_do_not_block_health(monkeypatch, path, target):
    import app as module

    entered = threading.Event()
    release = threading.Event()

    def slow_read(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=2)
        return [] if target == "audit_log.tail" else {}

    owner = module
    attribute = target
    if "." in target:
        attribute = target.split(".")[1]
        owner = module.audit_log
    monkeypatch.setattr(owner, attribute, slow_read)
    timer = threading.Timer(0.5, release.set)
    timer.start()

    async def requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=module.app),
            base_url="http://test",
        ) as client:
            read = asyncio.create_task(client.get(path))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                health = await client.get("/health")
                assert health.status_code == 200
                assert not release.is_set(), "console read blocked unrelated health request"
            finally:
                release.set()
                await read

    try:
        asyncio.run(requests())
    finally:
        release.set()
        timer.cancel()
        timer.join()


def test_blocking_console_handlers_use_the_fastapi_worker_pool():
    import app as module

    for name in (
        "root", "comparison_table_markdown", "query_recommender_endpoint",
        "get_ablation_matrix_endpoint", "ui_catalog", "ui_attempt",
        "audit_log_entries", "audit_verify", "slack_feed",
    ):
        assert not inspect.iscoroutinefunction(getattr(module, name)), name


def test_slack_feed_streams_and_keeps_last_valid_records(tmp_path, monkeypatch):
    import app as module

    path = tmp_path / "data/slack_posts.jsonl"
    path.parent.mkdir()
    with path.open("w", encoding="utf-8", newline="") as file:
        for index in range(100):
            file.write(json.dumps({"index": index, "text": "caf\u00e9"}) + "\r\n")
            file.write("malformed\n")
        file.write('{"index": 100, "text": "final"}')
    monkeypatch.chdir(tmp_path)

    def refuse_full_read(*args, **kwargs):
        raise AssertionError("feed must not read the entire log into memory")

    monkeypatch.setattr(type(path), "read_text", refuse_full_read)
    response = TestClient(module.app).get("/slack/feed")
    assert response.status_code == 200
    assert [post["index"] for post in response.json()["posts"]] == list(range(71, 101))


@pytest.mark.parametrize("content", ["", "\r\n \n", "invalid\n", "null\n42\n[]"])
def test_slack_feed_preserves_empty_and_non_object_json(tmp_path, monkeypatch, content):
    import app as module

    path = tmp_path / "data/slack_posts.jsonl"
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    response = TestClient(module.app).get("/slack/feed")
    expected = [None, 42, []] if content.startswith("null") else []
    assert response.json() == {"posts": expected}
