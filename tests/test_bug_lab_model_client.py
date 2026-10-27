"""OpenAI-compatible model-client behavior at its HTTP boundary."""

import asyncio
import json

import httpx
import pytest

from demo.bug_lab import agents
from demo.bug_lab.agents import Diagnosis
from demo.bug_lab.llm import Backend, RULES, detect_backend
from demo.bug_lab.workspace import Workspace

MODEL = "qwen2.5:7b-instruct"
BASE_URL = "http://model.test/v1"
API_KEY = "test-api-key"


def _completion(content="A usable answer.", *, model=MODEL, finish_reason="stop", refusal=None):
    message = {"role": "assistant", "content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return {
        "model": model,
        "choices": [{"message": message, "finish_reason": finish_reason}],
    }


def _backend(transport, *, api_key=API_KEY, timeout=11.0):
    return asyncio.run(detect_backend(
        "llm", BASE_URL, MODEL, api_key=api_key, timeout=timeout, transport=transport,
    ))


def _backend_for_answer(answer):
    def endpoint(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        return httpx.Response(200, json=_completion(answer))

    return _backend(httpx.MockTransport(endpoint))


def test_requested_model_is_catalogued_and_bearer_auth_covers_both_requests():
    requests = []

    def endpoint(request):
        requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        return httpx.Response(200, json=_completion("  diagnosis text  "))

    backend = _backend(httpx.MockTransport(endpoint))

    assert asyncio.run(backend.chat("system prompt", "user prompt")) == "diagnosis text"
    assert [request.headers.get("Authorization") for request in requests] == [
        f"Bearer {API_KEY}", f"Bearer {API_KEY}",
    ]
    assert json.loads(requests[1].content)["model"] == MODEL
    assert requests[0].extensions["timeout"]["read"] == 3.0
    assert requests[1].extensions["timeout"]["read"] == 11.0
    assert API_KEY not in json.dumps(backend.public())
    assert API_KEY not in repr(backend)


@pytest.mark.parametrize(
    "catalog",
    [
        pytest.param([], id="non-object"),
        pytest.param({}, id="missing-data"),
        pytest.param({"data": []}, id="empty"),
        pytest.param({"data": {}}, id="data-not-list"),
        pytest.param({"data": [None]}, id="entry-not-object"),
        pytest.param({"data": [{"id": 7}]}, id="id-not-text"),
        pytest.param({"data": [{"id": "  "}]}, id="blank-id"),
        pytest.param({"data": [{"id": MODEL}, {"id": 7}]}, id="partially-malformed"),
        pytest.param({"data": [{"id": "other-model"}]}, id="requested-model-absent"),
    ],
)
def test_auto_mode_rejects_catalogs_that_do_not_identify_the_requested_model(catalog):
    backend = asyncio.run(detect_backend(
        "auto", BASE_URL, MODEL, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=catalog),
        ),
    ))

    assert backend.mode == "rules"
    assert backend is not RULES
    assert backend.note


def test_explicit_model_mode_rejects_catalog_without_the_requested_model():
    with pytest.raises(RuntimeError, match="model catalog"):
        asyncio.run(detect_backend(
            "llm", BASE_URL, MODEL, transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": []}),
            ),
        ))


def test_auto_mode_rejects_non_json_model_catalog():
    backend = asyncio.run(detect_backend(
        "auto", BASE_URL, MODEL, transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"not json", headers={"content-type": "application/json"},
            ),
        ),
    ))

    assert backend.mode == "rules"
    assert "rule engine" in backend.note


@pytest.mark.parametrize(
    "completion",
    [
        pytest.param([], id="non-object"),
        pytest.param({"choices": []}, id="missing-model-and-choices"),
        pytest.param({"model": "different-model", "choices": []}, id="wrong-model"),
        pytest.param({"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]},
                     id="missing-model-identity"),
        pytest.param({"model": MODEL, "choices": []}, id="empty-choices"),
        pytest.param({"model": MODEL, "choices": [None]}, id="choice-not-object"),
        pytest.param({"model": MODEL, "choices": [{"message": None, "finish_reason": "stop"}]},
                     id="missing-message"),
        pytest.param({"model": MODEL, "choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
                     id="empty-content"),
        pytest.param({"model": MODEL, "choices": [{"message": {"content": "  "}, "finish_reason": "stop"}]},
                     id="whitespace-content"),
        pytest.param({"model": MODEL, "choices": [{"message": {"content": 7}, "finish_reason": "stop"}]},
                     id="non-text-content"),
        pytest.param({"model": MODEL, "choices": [{"message": {"content": ["answer"]},
                                                   "finish_reason": "stop"}]},
                     id="multimodal-content"),
        pytest.param(_completion("partial answer", finish_reason="length"), id="truncated"),
        pytest.param(_completion("blocked", refusal="I cannot help with that request."),
                     id="refusal"),
        pytest.param(_completion("answer", refusal={"reason": "unknown"}), id="malformed-refusal"),
    ],
)
def test_completion_payload_must_be_a_complete_answer_from_the_requested_model(completion):
    def endpoint(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        return httpx.Response(200, json=completion)

    backend = _backend(httpx.MockTransport(endpoint))

    with pytest.raises(RuntimeError):
        asyncio.run(backend.chat("system prompt", "user prompt"))


def test_malformed_completion_json_is_rejected_without_echoing_response_data():
    def endpoint(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        return httpx.Response(
            200, content=API_KEY.encode(),
            headers={"content-type": "application/json"},
        )

    backend = _backend(httpx.MockTransport(endpoint))

    with pytest.raises(RuntimeError) as error:
        asyncio.run(backend.chat("system prompt", "user prompt"))

    assert API_KEY not in str(error.value)


def test_timeout_and_auth_failures_are_redacted_and_auto_mode_uses_rules():
    def timeout(request):
        raise httpx.ReadTimeout(f"request failed with {API_KEY}", request=request)

    backend = asyncio.run(detect_backend(
        "auto", BASE_URL, MODEL, api_key=API_KEY,
        transport=httpx.MockTransport(timeout),
    ))

    assert backend.mode == "rules"
    assert API_KEY not in backend.note
    assert "timed out" in backend.note

    def unauthorized(request):
        return httpx.Response(401, text=API_KEY)

    with pytest.raises(RuntimeError) as error:
        asyncio.run(detect_backend(
            "llm", BASE_URL, MODEL, api_key=API_KEY,
            transport=httpx.MockTransport(unauthorized),
        ))

    assert API_KEY not in str(error.value)
    assert "401" in str(error.value)


def test_completion_timeout_uses_configured_timeout_and_redacts_credentials():
    requests = []

    def endpoint(request):
        requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        raise httpx.ReadTimeout(f"request failed with {API_KEY}", request=request)

    backend = _backend(httpx.MockTransport(endpoint), timeout=19.0)

    with pytest.raises(RuntimeError) as error:
        asyncio.run(backend.chat("system prompt", "user prompt"))

    assert requests[-1].extensions["timeout"]["read"] == 19.0
    assert "timed out" in str(error.value)
    assert API_KEY not in str(error.value)


def test_rules_choice_never_contacts_model_endpoint():
    def unreachable(_request):
        raise AssertionError("rules mode must not make model requests")

    backend = asyncio.run(detect_backend(
        "rules", BASE_URL, MODEL, api_key=API_KEY,
        transport=httpx.MockTransport(unreachable),
    ))

    assert backend is RULES


def test_auto_mode_uses_rules_for_an_invalid_endpoint_without_echoing_credentials():
    secret = "synthetic-invalid-port-secret"
    backend = asyncio.run(detect_backend(
        "auto", f"http://model.test:{secret}/v1", MODEL, api_key=secret,
    ))
    assert backend.mode == "rules"
    assert "Invalid model endpoint" in backend.note
    assert secret not in json.dumps(backend.public())


def test_explicit_model_mode_reports_invalid_endpoint_without_echoing_credentials():
    secret = "synthetic-invalid-port-secret"
    with pytest.raises(RuntimeError) as error:
        asyncio.run(detect_backend(
            "llm", f"http://model.test:{secret}/v1", MODEL, api_key=secret,
        ))
    assert "Invalid model endpoint" in str(error.value)
    assert secret not in str(error.value)


def test_direct_backend_invalid_endpoint_error_does_not_echo_credentials():
    secret = "synthetic-invalid-port-secret"
    backend = Backend(
        mode="llm", label="Model", model=MODEL, api_key=secret,
        base_url=f"http://model.test:{secret}/v1",
    )
    with pytest.raises(RuntimeError) as error:
        asyncio.run(backend.chat("system", "user"))
    assert secret not in str(error.value)


def test_public_backend_and_repr_do_not_expose_endpoint_credentials():
    secret_url = f"http://owner:{API_KEY}@model.test/v1?api_key={API_KEY}"
    backend = Backend(
        mode="llm", label=f"{MODEL} via {secret_url}", model=MODEL,
        base_url=secret_url, api_key=API_KEY,
    )

    assert backend.public()["base_url"] == "http://model.test/v1"
    assert API_KEY not in json.dumps(backend.public())
    assert API_KEY not in repr(backend)


@pytest.mark.parametrize(
    "timeout",
    [
        pytest.param(0.0, id="zero"),
        pytest.param(-1.0, id="negative"),
        pytest.param(float("inf"), id="infinite"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(True, id="boolean"),
    ],
)
def test_invalid_model_timeout_is_rejected_before_contacting_the_endpoint(timeout):
    def unreachable(_request):
        raise AssertionError("invalid timeout must be rejected before network I/O")

    with pytest.raises(ValueError, match="positive finite"):
        asyncio.run(detect_backend(
            "llm", BASE_URL, MODEL, timeout=timeout,
            transport=httpx.MockTransport(unreachable),
        ))


def test_whole_completion_deadline_bounds_async_transport_time():
    class SlowCompletionTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.requests = []

        async def handle_async_request(self, request):
            self.requests.append(request)
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json={"data": [{"id": MODEL}]})
            await asyncio.sleep(0.1)
            return httpx.Response(200, json=_completion())

    transport = SlowCompletionTransport()
    backend = _backend(transport, timeout=0.02)

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(backend.chat("system prompt", "user prompt"))

    assert transport.requests[0].extensions["timeout"]["read"] == 3.0
    assert transport.requests[1].extensions["timeout"]["read"] == 0.02


def test_diagnosis_accepts_a_single_valid_label_pair_in_a_whole_fence(tmp_path):
    answer = (
        "```text\n"
        "FILE: index.html\n"
        "ROOT_CAUSE: The stylesheet link points to a missing file.\n"
        "```"
    )
    workspace = Workspace(tmp_path / "workspace")

    diagnosis = asyncio.run(agents.diagnose_llm(_backend_for_answer(answer), [], workspace))

    assert diagnosis is not None
    assert diagnosis.file == "index.html"
    assert diagnosis.root_cause == "The stylesheet link points to a missing file."


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("FILE: index.html\nFILE: app.js\nROOT_CAUSE: Conflicting files.", id="duplicate-file"),
        pytest.param("FILE: index.html\nROOT_CAUSE: Cause one.\nROOT_CAUSE: Cause two.",
                     id="duplicate-cause"),
        pytest.param("Commentary\nFILE: index.html\nROOT_CAUSE: Cause.", id="leading-chatter"),
        pytest.param("FILE: index.html\nROOT_CAUSE: Cause.\nExtra line.", id="trailing-chatter"),
        pytest.param("FILE: ../app.js\nROOT_CAUSE: Path escapes the store.", id="invalid-file"),
        pytest.param("```text\nFILE: index.html\nROOT_CAUSE: Cause.\n```\nMore text",
                     id="partial-fence"),
    ],
)
def test_diagnosis_rejects_ambiguous_or_nonconforming_answers(tmp_path, answer):
    workspace = Workspace(tmp_path / "workspace")

    diagnosis = asyncio.run(agents.diagnose_llm(_backend_for_answer(answer), [], workspace))

    assert diagnosis is None


def test_remediation_accepts_complete_numbered_source_lines_inside_a_whole_fence(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    original = next(
        line for line in workspace.read("index.html").splitlines()
        if 'rel="stylesheet"' in line
    )
    replacement = original.replace("styles.css", "style.css")
    answer = (
        "```text\n"
        "FIND:\n"
        f"  1| {original}\n"
        "REPLACE:\n"
        f"  1| {replacement}\n"
        "END\n"
        "EXPLANATION: Point to the existing stylesheet.\n"
        "```"
    )

    patch = asyncio.run(agents.remediate_llm(
        _backend_for_answer(answer), Diagnosis("index.html", "Bad link.", "test"),
        [], workspace,
    ))

    assert patch is not None
    assert patch.edits == [{"find": original, "replace": replacement}]
    assert patch.explanation == "Point to the existing stylesheet."


@pytest.mark.parametrize(
    "suffix",
    [
        pytest.param("", id="missing-explanation"),
        pytest.param("EXPLANATION: A fix.\nUnrequested text.", id="trailing-chatter"),
        pytest.param("EXPLANATION: A fix.\nEXPLANATION: Another fix.", id="duplicate-explanation"),
    ],
)
def test_remediation_rejects_partial_or_ambiguous_answers(tmp_path, suffix):
    workspace = Workspace(tmp_path / "workspace")
    original = next(
        line for line in workspace.read("index.html").splitlines()
        if 'rel="stylesheet"' in line
    )
    answer = f"FIND:\n{original}\nREPLACE:\n{original}\nEND\n{suffix}"

    patch = asyncio.run(agents.remediate_llm(
        _backend_for_answer(answer), Diagnosis("index.html", "Bad link.", "test"),
        [], workspace,
    ))

    assert patch is None


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("Commentary\nFIND:\nold\nREPLACE:\nnew\nEND\nEXPLANATION: Fix.",
                     id="leading-chatter"),
        pytest.param("FIND:\nold\nREPLACE:\nnew\nEXPLANATION: Fix.", id="missing-end"),
        pytest.param("FIND:\nold\nnew\nEND\nEXPLANATION: Fix.", id="missing-replace"),
        pytest.param("FIND:\nold\nREPLACE:\nnew\nEND and chatter\nEXPLANATION: Fix.",
                     id="end-is-not-a-line"),
        pytest.param("```text\nFIND:\nold\nREPLACE:\nnew\nEND\nEXPLANATION: Fix.",
                     id="unclosed-fence"),
    ],
)
def test_remediation_rejects_incomplete_patch_blocks(tmp_path, answer):
    workspace = Workspace(tmp_path / "workspace")

    patch = asyncio.run(agents.remediate_llm(
        _backend_for_answer(answer), Diagnosis("index.html", "Bad link.", "test"),
        [], workspace,
    ))

    assert patch is None
