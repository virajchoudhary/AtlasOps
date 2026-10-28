import asyncio
import builtins
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from demo.bug_lab import llm, server
from demo.bug_lab.engine import LabOptions
from demo.bug_lab.server import LabConfig

MODEL = "qwen2.5:7b-instruct"
API_KEY = "startup-test-secret"
URL_SECRET = "url-query-secret"
URL_CREDENTIAL = "uri-password-test-secret"
OTHER_URL_SECRET = "unrelated-query-secret"
OTHER_URL_CREDENTIAL = "unrelated-uri-password"
BASE_URL = f"http://owner:{URL_CREDENTIAL}@model.test/v1?token={URL_SECRET}"


@pytest.mark.parametrize(
    ("agents", "expected_mode"),
    [
        pytest.param("rules", "rules", id="rules"),
        pytest.param("auto", "rules", id="auto-fallback"),
        pytest.param("llm", "llm", id="llm"),
    ],
)
def test_startup_logs_detected_backend_after_lifespan_selection(
    tmp_path, monkeypatch, capsys, agents, expected_mode,
):
    events = []
    requests = []
    detect_backend = llm.detect_backend

    def transport_for(choice):
        def endpoint(request):
            requests.append(request)
            if choice == "auto":
                raise httpx.ConnectError(
                    f"endpoint failure includes {API_KEY} and {URL_SECRET}",
                    request=request,
                )
            return httpx.Response(200, json={"data": [{"id": MODEL}]})

        return httpx.MockTransport(endpoint)

    async def detect_with_mock_transport(choice, base_url, model, api_key, *, timeout):
        events.append(("detection-started", choice))
        selected = await detect_backend(
            choice,
            base_url,
            model,
            api_key,
            transport=transport_for(choice),
            timeout=timeout,
        )
        events.append(("detection-completed", selected.mode))
        return selected

    original_print = builtins.print

    def record_backend_print(*args, **kwargs):
        if args and str(args[0]).startswith("  Backend: "):
            events.append(("backend-logged", str(args[0])))
        original_print(*args, **kwargs)

    monkeypatch.setattr(server, "detect_backend", detect_with_mock_transport)
    monkeypatch.setattr(builtins, "print", record_backend_print)
    app = server.create_app(LabConfig(
        workdir=tmp_path / agents,
        agents=agents,
        llm_base_url=BASE_URL,
        llm_model=MODEL,
        llm_api_key=API_KEY,
        llm_timeout=1.0,
        options=LabOptions(step_delay=0, monitor_interval=1),
    ))

    assert events == []
    assert capsys.readouterr().out == ""
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/lab/api/state").status_code == 200
        selected_backend = app.state.lab.backend

    public = selected_backend.public()
    expected_line = f"  Backend: {public['label']}"
    if public["note"]:
        expected_line += f" ({public['note']})"

    output = capsys.readouterr().out
    assert expected_line in output
    assert API_KEY not in output
    assert URL_SECRET not in output
    assert selected_backend.mode == expected_mode
    assert events.index(("detection-completed", expected_mode)) < events.index(
        ("backend-logged", expected_line)
    )
    assert len(requests) == {"rules": 0, "auto": 1, "llm": 1}[agents]


def test_model_failure_reason_returns_only_fixed_categories():
    request = httpx.Request("GET", f"https://model.test/v1?token={URL_SECRET}")
    response = httpx.Response(503, request=request, text=API_KEY)
    errors = [
        (httpx.ReadTimeout(API_KEY, request=request), "request timed out"),
        (httpx.HTTPStatusError(
            API_KEY, request=request, response=response,
        ), "HTTP status error"),
        (httpx.ConnectError(
            f"{API_KEY} at {request.url}", request=request,
        ), "connection failed"),
        (ValueError(f"provider response included {API_KEY}"), "invalid response"),
        (RuntimeError(f"request to {request.url} failed with {API_KEY}"), "unknown error"),
        (llm.ModelError("invalid response", "fixed diagnostic"), "invalid response"),
    ]

    reasons = [llm.model_failure_reason(error) for error, _ in errors]

    assert reasons == [expected for _, expected in errors]
    assert API_KEY not in " ".join(reasons)
    assert URL_SECRET not in " ".join(reasons)


def test_backend_chat_replaces_unexpected_transport_error_text():
    def fail_with_secret(request):
        raise RuntimeError(
            f"provider at {request.url} echoed {API_KEY} and {URL_SECRET}"
        )

    backend = llm.Backend(
        mode="llm",
        label="test model",
        model=MODEL,
        base_url="http://model.test/v1",
        api_key=API_KEY,
        timeout=1.0,
        transport=httpx.MockTransport(fail_with_secret),
    )

    with pytest.raises(llm.ModelError) as caught:
        asyncio.run(backend.chat("system", "user"))

    assert str(caught.value) == "Model request failed."
    assert llm.model_failure_reason(caught.value) == "unknown error"
    assert API_KEY not in str(caught.value)
    assert URL_SECRET not in str(caught.value)


def test_backend_chat_preserves_fixed_response_validation_diagnostic():
    def invalid_model_response(_request):
        return httpx.Response(200, json={
            "model": "different-model",
            "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
        })

    backend = llm.Backend(
        mode="llm",
        label="test model",
        model=MODEL,
        base_url="http://model.test/v1",
        transport=httpx.MockTransport(invalid_model_response),
    )

    with pytest.raises(llm.ModelError) as caught:
        asyncio.run(backend.chat("system", "user"))

    assert str(caught.value) == (
        "Model completion response identity did not match the requested model."
    )
    assert llm.model_failure_reason(caught.value) == "invalid response"


def test_httpx_info_logs_redact_model_url_but_not_concurrent_other_request(caplog):
    model_url = (
        f"http://owner:{URL_CREDENTIAL}@model.test/private/{API_KEY}/v1"
        f"?token={URL_SECRET}"
    )
    other_url = (
        f"http://other:{OTHER_URL_CREDENTIAL}@other.test/v1"
        f"?token={OTHER_URL_SECRET}"
    )
    model_request_started = asyncio.Event()
    other_request_finished = asyncio.Event()

    class WaitingModelTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            model_request_started.set()
            await other_request_finished.wait()
            return httpx.Response(
                200, json={"data": [{"id": MODEL}]}, request=request,
            )

    async def run_requests():
        async def check_model():
            return await llm.detect_backend(
                "llm",
                model_url,
                MODEL,
                api_key=API_KEY,
                transport=WaitingModelTransport(),
            )

        async def check_unrelated_url():
            await model_request_started.wait()
            async with httpx.AsyncClient(
                base_url=other_url,
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, request=request)
                ),
            ) as client:
                await client.get("/health")
            other_request_finished.set()

        selected, _ = await asyncio.gather(check_model(), check_unrelated_url())
        return selected

    with caplog.at_level(logging.INFO, logger="httpx"):
        selected = asyncio.run(run_requests())

    assert selected.mode == "llm"
    messages = [record.getMessage() for record in caplog.records]
    model_message = next(message for message in messages if "model.test" in message)
    other_message = next(message for message in messages if "other.test" in message)
    assert "http://model.test/private/[redacted]/v1" in model_message
    assert API_KEY not in model_message
    assert URL_CREDENTIAL not in model_message
    assert URL_SECRET not in model_message
    assert f"other:{OTHER_URL_CREDENTIAL}@other.test" in other_message
    assert OTHER_URL_SECRET in other_message


def test_httpx_info_logs_redact_userinfo_without_bearer_key(caplog):
    def model_catalog(_request):
        return httpx.Response(200, json={"data": [{"id": MODEL}]})

    with caplog.at_level(logging.INFO, logger="httpx"):
        selected = asyncio.run(llm.detect_backend(
            "llm",
            BASE_URL,
            MODEL,
            transport=httpx.MockTransport(model_catalog),
        ))

    assert selected.mode == "llm"
    message = next(
        record.getMessage() for record in caplog.records if "model.test" in record.getMessage()
    )
    assert "http://model.test/v1" in message
    assert URL_CREDENTIAL not in message
    assert URL_SECRET not in message
