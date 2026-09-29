"""Scientific-validity contracts for the Stage 6 zero-shot evaluator."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
from dataclasses import replace

import httpx
import pytest

import bench.zero_shot_baseline as zero_shot
from config.scenario_catalog import SCENARIO_CATALOG
from config.splits import VAL_SPLIT


@pytest.mark.parametrize(
    "confidence", [True, False, math.nan, math.inf, -math.inf, -0.1, 1.1, 10**500]
)
def test_invalid_confidence_is_not_scored_as_numeric_inference(tmp_path, confidence):
    async def inference(_messages, _model_name, _generation_config):
        return json.dumps({
            "severity": "P1",
            "root_cause": "service saturation under load",
            "affected_services": ["paymentservice"],
            "confidence": confidence,
        })

    output = tmp_path / "run"
    summary = asyncio.run(zero_shot.evaluate_zero_shot_split(
        "val",
        mode="empirical",
        model_revision="synthetic-test-revision",
        output_dir=output,
        inference_fn=inference,
    ))
    rows = [
        json.loads(line)
        for line in (output / "results_per_episode.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert summary["failed_scenarios"] == len(VAL_SPLIT)
    assert all(row["status"] == "error" and row["prediction"] is None for row in rows)
    assert all(row["error"].startswith("ValueError: invalid_response") for row in rows)
    assert all(row["empirical_claim_allowed"] is False for row in rows)
    assert all(row["raw_model_response"] for row in rows)
    assert all(row["empirical_inference_executed"] is True for row in rows)
    assert all(row["diagnostic_metrics"] is None for row in rows)
    assert all(row["outcome"] == "invalid_prediction" and row["total_turns"] == 1 for row in rows)
    assert summary["empirical_inference_executed"] is True
    assert summary["diagnostic_scored_count"] == 0
    assert summary["avg_diagnostic_f1"] is None
    assert all(
        tier["scored_count"] == 0 and tier["avg_diagnostic_f1"] is None
        for tier in summary["per_tier"].values()
    )


def test_diagnostic_average_excludes_invalid_returned_predictions(tmp_path):
    calls = 0

    async def inference(_messages, _model_name, _generation_config):
        nonlocal calls
        calls += 1
        return json.dumps({
            "severity": "P1",
            "root_cause": "service saturation under load",
            "affected_services": ["paymentservice"],
            "confidence": 0.6 if calls == 1 else math.nan,
        })

    output = tmp_path / "mixed"
    summary = asyncio.run(zero_shot.evaluate_zero_shot_split(
        "val",
        mode="empirical",
        model_revision="synthetic-test-revision",
        output_dir=output,
        inference_fn=inference,
    ))
    rows = [
        json.loads(line)
        for line in (output / "results_per_episode.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    scored = [row for row in rows if row["status"] == "ok"]
    assert calls == len(VAL_SPLIT)
    assert len(scored) == 1
    assert summary["failed_scenarios"] == len(VAL_SPLIT) - 1
    assert summary["diagnostic_scored_count"] == 1
    assert summary["avg_diagnostic_f1"] == round(scored[0]["diagnostic_metrics"]["f1"], 4)
    assert sum(tier["scored_count"] for tier in summary["per_tier"].values()) == 1
    assert all(
        tier["avg_diagnostic_f1"] is None
        for tier in summary["per_tier"].values()
        if tier["scored_count"] == 0
    )
    assert summary["empirical_inference_executed"] is True
    assert summary["empirical_claim_allowed"] is False


@pytest.mark.parametrize("confidence", [0, 0.4, 1])
def test_finite_numeric_confidence_remains_valid(confidence):
    prediction = zero_shot._parse_prediction(json.dumps({
        "severity": "P1",
        "root_cause": "service saturation under load",
        "affected_services": ["paymentservice"],
        "confidence": confidence,
    }))
    assert prediction["confidence"] == confidence


def _prediction(root_cause: str = "service saturation under load") -> str:
    return json.dumps(
        {
            "severity": "P1",
            "affected_services": ["unknown"],
            "root_cause": root_cause,
            "confidence": 0.4,
        }
    )


def _set_clean_source_provenance(monkeypatch):
    monkeypatch.setattr(
        zero_shot,
        "_source_provenance",
        lambda: {"git_sha": "c" * 40, "git_dirty": False},
    )


@pytest.mark.parametrize("severity", ["P0", "P1", "P2", "P3"])
def test_prediction_accepts_each_defined_severity(severity):
    payload = json.loads(_prediction())
    payload["severity"] = severity

    prediction = zero_shot._parse_prediction(json.dumps(payload))

    assert prediction["severity"] == severity


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "omit"),
    [
        ("severity", None, False),
        ("severity", "P4", False),
        ("severity", "p1", False),
        ("severity", 1, False),
        ("severity", None, True),
        ("affected_services", None, True),
        ("affected_services", "paymentservice", False),
        ("affected_services", [1], False),
        ("affected_services", [""], False),
    ],
)
async def test_invalid_required_prediction_fields_are_not_scored(
    tmp_path,
    field,
    value,
    omit,
):
    payload = json.loads(_prediction())
    if omit:
        payload.pop(field)
    else:
        payload[field] = value
    raw_prediction = json.dumps(payload)

    async def inference(_messages, _model_name, _generation_config):
        return raw_prediction

    output_dir = tmp_path / "invalid-prediction"
    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        mode="empirical",
        model_revision="synthetic-test-revision",
        output_dir=output_dir,
        inference_fn=inference,
    )
    rows = [
        json.loads(line)
        for line in (output_dir / "results_per_episode.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert summary["failed_scenarios"] == len(VAL_SPLIT)
    assert summary["diagnostic_scored_count"] == 0
    assert summary["avg_diagnostic_f1"] is None
    assert all(row["status"] == "error" for row in rows)
    assert all(row["outcome"] == "invalid_prediction" for row in rows)
    assert all(row["prediction"] is None for row in rows)
    assert all(row["diagnostic_metrics"] is None for row in rows)
    assert all(row["raw_model_response"] == raw_prediction for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("split_name", ["test", " TEST "])
async def test_empirical_test_split_is_refused_before_resolution_or_output(
    tmp_path,
    monkeypatch,
    split_name,
):
    calls = []
    output_dir = tmp_path / "unauthorized-test"

    def resolve_split(_split_name):
        calls.append("split")
        return VAL_SPLIT

    async def infer(_messages, _model_name, _generation_config):
        calls.append("inference")
        return _prediction()

    monkeypatch.setattr(zero_shot, "get_split", resolve_split)

    with pytest.raises(ValueError, match="test split"):
        await zero_shot.evaluate_zero_shot_split(
            split_name,
            mode="empirical",
            model_revision="synthetic-test-revision",
            output_dir=output_dir,
            inference_fn=infer,
        )

    assert calls == []
    assert not output_dir.exists()


@pytest.mark.asyncio
async def test_configured_empirical_requires_clean_source_before_model_observation(
    tmp_path,
    monkeypatch,
):
    calls = []
    output_dir = tmp_path / "dirty-source"
    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")

    def dirty_source():
        calls.append("source")
        return {"git_sha": "a" * 40, "git_dirty": True}

    async def observe_model(_model_name):
        calls.append("identity")
        return {"name": "qwen2.5:7b-instruct", "digest": "a" * 64}

    async def infer(_messages, _model_name, _generation_config):
        calls.append("inference")
        return zero_shot.InferenceResult(_prediction(), "qwen2.5:7b-instruct")

    monkeypatch.setattr(zero_shot, "_source_provenance", dirty_source)
    monkeypatch.setattr(zero_shot, "observe_local_model_identity", observe_model)
    monkeypatch.setattr(zero_shot, "openai_compatible_inference", infer)

    with pytest.raises(RuntimeError, match="clean Git source"):
        await zero_shot.evaluate_zero_shot_split(
            "val",
            model_revision="a" * 64,
            mode="empirical",
            output_dir=output_dir,
        )

    assert calls == ["source"]
    assert not output_dir.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("postflight", "status", "unchanged"),
    [
        ({"git_sha": "a" * 40, "git_dirty": False}, "unchanged", True),
        ({"git_sha": "b" * 40, "git_dirty": False}, "changed", False),
        ({"git_sha": "a" * 40, "git_dirty": True}, "changed", False),
    ],
)
async def test_configured_empirical_records_source_preflight_and_postflight(
    tmp_path,
    monkeypatch,
    postflight,
    status,
    unchanged,
):
    model_name = "qwen2.5:7b-instruct"
    digest = "a" * 64
    preflight = {"git_sha": "a" * 40, "git_dirty": False}
    source_values = iter([preflight, postflight])
    events = []
    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")

    def source_provenance():
        events.append("source")
        return next(source_values)

    async def observe_model(_model_name):
        events.append("identity")
        return {"name": model_name, "digest": digest}

    async def infer(_messages, _model_name, _generation_config):
        events.append("inference")
        return zero_shot.InferenceResult(_prediction(), model_name)

    monkeypatch.setattr(zero_shot, "_source_provenance", source_provenance)
    monkeypatch.setattr(zero_shot, "observe_local_model_identity", observe_model)
    monkeypatch.setattr(zero_shot, "openai_compatible_inference", infer)

    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        model_name=model_name,
        model_revision=digest,
        mode="empirical",
        output_dir=tmp_path / "source-provenance",
    )

    assert events[0] == "source"
    assert events[-1] == "source"
    assert events.index("inference") > events.index("identity")
    assert summary["source"] == preflight
    assert summary["source_preflight"] == preflight
    assert summary["source_postflight"] == postflight
    assert summary["source_provenance_status"] == status
    assert summary["source_unchanged"] is unchanged
    assert summary["empirical_claim_allowed"] is False


@pytest.mark.asyncio
async def test_split_and_dataset_hashes_are_frozen_before_inference(
    tmp_path,
):
    scenario_id = VAL_SPLIT[0]
    original_metadata = SCENARIO_CATALOG[scenario_id]
    changed_metadata = replace(original_metadata, manifest_sha256="f" * 64)
    changed = False

    async def inference(_messages, _model_name, _generation_config):
        nonlocal changed
        if not changed:
            SCENARIO_CATALOG[scenario_id] = changed_metadata
            changed = True
        return _prediction()

    try:
        summary = await zero_shot.evaluate_zero_shot_split(
            "val",
            mode="empirical",
            model_revision="synthetic-test-revision",
            output_dir=tmp_path / "hashes",
            inference_fn=inference,
        )
    finally:
        SCENARIO_CATALOG[scenario_id] = original_metadata

    assert summary["split_sha256"] == (
        "9f1bad373e66d7818019092c213f70edcc7e09dfbc538346ff6d0693ea78c6e4"
    )
    assert summary["dataset_sha256"] == (
        "1322805985556d197bddee8d5a036a01dcc773e00972ba37c0d9708443b1fce6"
    )


def _mock_tags_endpoint(
    monkeypatch,
    payload,
    *,
    status_code: int = 200,
    failure=None,
    client_options=None,
):
    requests = []

    class FakeResponse:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            return payload

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            if client_options is not None:
                client_options.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            requests.append(url)
            if failure is not None:
                raise failure
            return FakeResponse()

    monkeypatch.setattr(zero_shot.httpx, "AsyncClient", FakeAsyncClient)
    return requests


def _mock_completion_endpoint(
    monkeypatch,
    payload,
    *,
    status_code: int = 200,
    client_options=None,
    failure=None,
    during_request=None,
):
    response = httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request(
            "POST",
            "http://127.0.0.1:11434/v1/chat/completions",
        ),
    )
    calls = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            if client_options is not None:
                client_options.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    async def fake_post(_client, url, json, *, context, max_response_bytes=None):
        calls.append(
            {
                "url": url,
                "payload": json,
                "context": context,
                "max_response_bytes": max_response_bytes,
            }
        )
        if failure is not None:
            raise failure
        if during_request is not None:
            during_request()
        return response

    monkeypatch.setattr(zero_shot.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(zero_shot, "post_with_retry", fake_post)
    return response, calls


@pytest.mark.asyncio
async def test_evaluation_mode_must_be_explicit(tmp_path):
    with pytest.raises(ValueError, match="Evaluation mode is required"):
        await zero_shot.evaluate_zero_shot_split("val", output_dir=tmp_path)


@pytest.mark.asyncio
async def test_empirical_mode_requires_exact_model_revision(tmp_path):
    async def inference(messages, model_name, generation_config):
        return _prediction()

    with pytest.raises(ValueError, match="model_revision"):
        await zero_shot.evaluate_zero_shot_split(
            "val",
            mode="empirical",
            output_dir=tmp_path,
            inference_fn=inference,
        )


@pytest.mark.asyncio
async def test_empirical_default_backend_requires_configured_endpoint(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("VLLM_BASE", raising=False)
    with pytest.raises(RuntimeError, match="VLLM_BASE"):
        await zero_shot.evaluate_zero_shot_split(
            "val",
            model_revision="revision-1",
            mode="empirical",
            output_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_configured_backend_rejects_unverified_model_before_output(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")
    _set_clean_source_provenance(monkeypatch)

    async def wrong_identity(_model_name):
        return {"name": "qwen2.5:7b-instruct", "digest": "b" * 64}

    monkeypatch.setattr(zero_shot, "observe_local_model_identity", wrong_identity)
    with pytest.raises(RuntimeError, match="model revision"):
        await zero_shot.evaluate_zero_shot_split(
            "val",
            model_name="qwen2.5:7b-instruct",
            model_revision="a" * 64,
            mode="empirical",
            output_dir=tmp_path / "run",
        )
    assert not (tmp_path / "run").exists()


@pytest.mark.asyncio
async def test_configured_backend_requires_sha256_model_revision_before_observation(
    tmp_path,
    monkeypatch,
):
    observer_called = False

    async def identity_observer(_model_name):
        nonlocal observer_called
        observer_called = True
        return {"name": "qwen2.5:7b-instruct", "digest": "a" * 64}

    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(zero_shot, "observe_local_model_identity", identity_observer)

    with pytest.raises(ValueError, match="64-hex SHA-256"):
        await zero_shot.evaluate_zero_shot_split(
            "val",
            model_name="qwen2.5:7b-instruct",
            model_revision="commit-identifier",
            mode="empirical",
            output_dir=tmp_path / "run",
        )

    assert observer_called is False
    assert not (tmp_path / "run").exists()


@pytest.mark.asyncio
async def test_observe_local_model_identity_reads_exact_ollama_tag(monkeypatch):
    model_name = "qwen2.5:7b-instruct"
    digest = "a" * 64
    requests = _mock_tags_endpoint(
        monkeypatch,
        {
            "models": [
                {"name": "other-model:latest", "digest": "b" * 64},
                {"name": model_name, "digest": f"sha256:{digest}"},
            ]
        },
    )
    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")

    identity = await zero_shot.observe_local_model_identity(model_name)

    assert requests == ["http://127.0.0.1:11434/api/tags"]
    assert identity == {
        "provider": "ollama-local",
        "name": model_name,
        "digest": digest,
    }


@pytest.mark.asyncio
async def test_ambient_http_proxy_is_disabled_for_local_identity_and_inference(
    monkeypatch,
):
    model_name = "qwen2.5:7b-instruct"
    digest = "a" * 64
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8123")
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:8123")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8123")
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:8123")
    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")

    tag_client_options = []
    _mock_tags_endpoint(
        monkeypatch,
        {"models": [{"name": model_name, "digest": digest}]},
        client_options=tag_client_options,
    )
    await zero_shot.observe_local_model_identity(model_name)
    assert tag_client_options[0]["trust_env"] is False

    inference_client_options = []
    _mock_completion_endpoint(
        monkeypatch,
        {
            "model": model_name,
            "choices": [{"message": {"content": _prediction()}}],
        },
        client_options=inference_client_options,
    )
    inference_result = await zero_shot.openai_compatible_inference(
        [],
        model_name,
        {
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 512,
            "seed": 1,
            "timeout_seconds": 2,
        },
    )

    assert inference_result.model_name == model_name
    assert inference_client_options[0]["trust_env"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("models", "error"),
    [
        ([], "not installed"),
        ([{"name": "qwen2.5:7b-instruct-v2", "digest": "a" * 64}], "not installed"),
        (
            [
                {"name": "qwen2.5:7b-instruct", "digest": "a" * 64},
                {"name": "qwen2.5:7b-instruct", "digest": "b" * 64},
            ],
            "ambiguous",
        ),
        ([{"name": "qwen2.5:7b-instruct", "digest": "not-a-digest"}], "64-hex"),
    ],
)
async def test_observe_local_model_identity_fails_closed_for_bad_tags(
    tmp_path,
    monkeypatch,
    models,
    error,
):
    _mock_tags_endpoint(monkeypatch, {"models": models})
    monkeypatch.setenv("VLLM_BASE", "http://localhost:11434/v1")

    with pytest.raises(RuntimeError, match=error):
        await zero_shot.observe_local_model_identity("qwen2.5:7b-instruct")


@pytest.mark.asyncio
async def test_local_identity_rejects_remote_endpoint_without_leaking_url_credentials(
    monkeypatch,
):
    requests = _mock_tags_endpoint(monkeypatch, {"models": []})
    monkeypatch.setenv(
        "VLLM_BASE",
        "https://user:private-password@example.invalid/v1?token=private-token",
    )

    with pytest.raises(RuntimeError) as exc_info:
        await zero_shot.observe_local_model_identity("qwen2.5:7b-instruct")

    assert requests == []
    assert "private-password" not in str(exc_info.value)
    assert "private-token" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_local_identity_rejects_non_loopback_endpoint_without_connecting(
    monkeypatch,
):
    requests = _mock_tags_endpoint(monkeypatch, {"models": []})
    monkeypatch.setenv("VLLM_BASE", "https://inference.example.invalid/v1")

    with pytest.raises(RuntimeError, match="loopback"):
        await zero_shot.observe_local_model_identity("qwen2.5:7b-instruct")

    assert requests == []


@pytest.mark.asyncio
async def test_local_identity_transport_failure_is_sanitized(monkeypatch):
    requests = _mock_tags_endpoint(
        monkeypatch,
        {"models": []},
        failure=httpx.ConnectError("credential must not be retained"),
    )
    monkeypatch.setenv("VLLM_BASE", "http://localhost:11434/v1")

    with pytest.raises(RuntimeError) as exc_info:
        await zero_shot.observe_local_model_identity("qwen2.5:7b-instruct")

    assert requests == ["http://localhost:11434/api/tags"]
    assert "credential" not in str(exc_info.value)
    assert "ConnectError" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unverifiable_preflight_fails_before_creating_run_output(
    tmp_path,
    monkeypatch,
):
    _mock_tags_endpoint(
        monkeypatch,
        {"models": []},
        failure=httpx.ConnectError("endpoint detail must not be retained"),
    )
    monkeypatch.setenv("VLLM_BASE", "http://localhost:11434/v1")
    _set_clean_source_provenance(monkeypatch)

    with pytest.raises(RuntimeError, match="ConnectError"):
        await zero_shot.evaluate_zero_shot_split(
            "val",
            model_name="qwen2.5:7b-instruct",
            model_revision="a" * 64,
            mode="empirical",
            output_dir=tmp_path / "run",
        )

    assert not (tmp_path / "run").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("served_model", "should_succeed"),
    [
        ("qwen2.5:7b-instruct", True),
        ("qwen2.5:7b-instruct-v2", False),
        (None, False),
    ],
)
async def test_openai_compatible_response_model_must_match_request(
    monkeypatch,
    served_model,
    should_succeed,
):
    response_body = {"choices": [{"message": {"content": _prediction()}}]}
    if served_model is not None:
        response_body["model"] = served_model
    response, calls = _mock_completion_endpoint(monkeypatch, response_body)
    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")
    generation_config = {
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 512,
        "seed": 1,
        "timeout_seconds": 2,
    }

    if should_succeed:
        result = await zero_shot.openai_compatible_inference(
            [],
            "qwen2.5:7b-instruct",
            generation_config,
        )
        assert result.content == _prediction()
        assert result.model_name == "qwen2.5:7b-instruct"
    else:
        with pytest.raises(
            zero_shot.InferenceResponseError,
            match="model",
        ) as exc_info:
            await zero_shot.openai_compatible_inference(
                [],
                "qwen2.5:7b-instruct",
                generation_config,
            )
        evidence = exc_info.value.response_error
        assert evidence["kind"] == (
            "missing_model_name" if served_model is None else "response_model_name_mismatch"
        )
        assert evidence["status_code"] == 200
        assert evidence["body_sha256"] == hashlib.sha256(response.content).hexdigest()
        assert evidence["body_byte_length"] == len(response.content)
        assert not hasattr(exc_info.value, "raw_response_body")

    assert calls[0]["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert calls[0]["payload"]["model"] == "qwen2.5:7b-instruct"
    assert calls[0]["context"] == "g6-zero-shot"
    assert calls[0]["max_response_bytes"] == zero_shot.MAX_G6_INFERENCE_RESPONSE_BYTES


@pytest.mark.asyncio
async def test_large_encoded_error_body_is_not_persisted(monkeypatch):
    api_key = "api-key-must-not-be-retained"
    encoded_key = base64.b64encode(api_key.encode()).decode()
    oversized_value = "oversized-response-" * 20_000
    model_name = "qwen2.5:7b-instruct"
    monkeypatch.setenv("LLM_API_KEY", api_key)
    response, _calls = _mock_completion_endpoint(
        monkeypatch,
        {
            "model": "other-model",
            "error": {
                "message": api_key,
                "encoded": encoded_key,
                "blob": oversized_value,
            },
        },
    )
    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")

    async def failing_inference(messages, requested_model, generation_config):
        return await zero_shot.openai_compatible_inference(
            messages,
            requested_model,
            generation_config,
        )

    episode = await zero_shot._evaluate_empirical_episode(
        VAL_SPLIT[0],
        model_name,
        {
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 512,
            "seed": 1,
            "timeout_seconds": 2,
        },
        failing_inference,
        "openai-compatible",
        {
            "provider": "ollama-local",
            "name": model_name,
            "digest": "a" * 64,
        },
    )
    serialized = json.dumps(episode, sort_keys=True)

    assert episode["status"] == "error"
    assert episode["inference_response_error"] == {
        "kind": "response_model_name_mismatch",
        "status_code": 200,
        "body_format": "json",
        "body_sha256": hashlib.sha256(response.content).hexdigest(),
        "body_byte_length": len(response.content),
    }
    assert episode["response_model_name"] is None
    assert episode["response_model_name_verified"] is False
    assert api_key not in serialized
    assert encoded_key not in serialized
    assert oversized_value not in serialized
    assert "raw_inference_error_response" not in episode
    assert len(serialized) < 10_000


@pytest.mark.asyncio
async def test_g6_oversized_transport_response_persists_only_bounded_fingerprint(
    monkeypatch,
):
    model_name = "qwen2.5:7b-instruct"
    limit = zero_shot.MAX_G6_INFERENCE_RESPONSE_BYTES
    prefix = b"x" * limit
    oversized = zero_shot.ResponseBodyTooLargeError(
        status_code=200,
        limit_bytes=limit,
        body_prefix_sha256=hashlib.sha256(prefix).hexdigest(),
    )
    _response, calls = _mock_completion_endpoint(
        monkeypatch,
        {},
        failure=oversized,
    )
    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")

    async def failing_inference(messages, requested_model, generation_config):
        return await zero_shot.openai_compatible_inference(
            messages,
            requested_model,
            generation_config,
        )

    episode = await zero_shot._evaluate_empirical_episode(
        VAL_SPLIT[0],
        model_name,
        {
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 512,
            "seed": 1,
            "timeout_seconds": 2,
        },
        failing_inference,
        "openai-compatible",
        {
            "provider": "ollama-local",
            "name": model_name,
            "digest": "a" * 64,
        },
    )
    serialized = json.dumps(episode, sort_keys=True)

    assert episode["status"] == "error"
    assert episode["inference_response_error"] == {
        "kind": "response_body_too_large",
        "status_code": 200,
        "body_format": "other",
        "body_sha256": hashlib.sha256(prefix).hexdigest(),
        "body_byte_length": limit,
        "body_truncated": True,
        "body_limit_bytes": limit,
    }
    assert episode["raw_model_response"] == ""
    assert episode["inference_response_error"]["body_sha256"] in serialized
    assert "x" * 1024 not in serialized
    assert calls[0]["max_response_bytes"] == limit
    assert len(serialized) < 10_000


@pytest.mark.asyncio
async def test_transient_alias_with_matching_observations_stays_nonclaimable(
    tmp_path,
    monkeypatch,
):
    model_name = "qwen2.5:7b-instruct"
    digest = "a" * 64
    transient_digest = "b" * 64
    base_url = "http://127.0.0.1:11434/v1"
    tag_payload = {"models": [{"name": model_name, "digest": digest}]}
    transient_served_digests = []

    def simulate_transient_alias_swap_and_revert():
        if not transient_served_digests:
            tag_payload["models"][0]["digest"] = transient_digest
            transient_served_digests.append(tag_payload["models"][0]["digest"])
            tag_payload["models"][0]["digest"] = digest

    monkeypatch.setenv("VLLM_BASE", base_url)
    _set_clean_source_provenance(monkeypatch)
    completion_response, completion_calls = _mock_completion_endpoint(
        monkeypatch,
        {
            "model": model_name,
            "choices": [{"message": {"content": _prediction()}}],
        },
        during_request=simulate_transient_alias_swap_and_revert,
    )
    tag_requests = _mock_tags_endpoint(monkeypatch, tag_payload)

    output_dir = tmp_path / "run"
    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        model_name=model_name,
        model_revision=f"sha256:{digest}",
        mode="empirical",
        output_dir=output_dir,
    )

    assert transient_served_digests == [transient_digest]
    assert tag_payload["models"][0]["digest"] == digest
    assert tag_requests == [f"{base_url.removesuffix('/v1')}/api/tags"] * 2
    assert len(completion_calls) == len(VAL_SPLIT)
    assert all(
        call["url"] == f"{base_url}/chat/completions"
        and call["payload"]["model"] == model_name
        and call["context"] == "g6-zero-shot"
        for call in completion_calls
    )
    assert set(completion_response.json()) == {"model", "choices"}

    assert summary["empirical_inference_executed"] is True
    assert summary["empirical_claim_allowed"] is False
    assert summary["non_empirical"] is False
    attestation = summary["model_identity_attestation"]
    assert attestation["status"] == "alias_observed_not_immutable"
    assert attestation["recheck_status"] == "observations_match"
    assert attestation["observations_match"] is True
    assert attestation["immutable_serving_attestation"] is False
    assert attestation["claimable"] is False
    assert attestation["observed_before"]["digest"] == digest
    assert attestation["observed_after"]["digest"] == digest
    assert attestation["response_model_names"] == [model_name] * len(VAL_SPLIT)

    rows = [
        json.loads(line)
        for line in (output_dir / "results_per_episode.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert all(row["empirical_claim_allowed"] is False for row in rows)
    assert all(
        row["model_identity_attestation_status"] == "alias_observed_not_immutable" for row in rows
    )
    assert all(row["model_identity_recheck_status"] == "observations_match" for row in rows)
    assert all(row["response_model_name"] == model_name for row in rows)
    assert all(row["response_model_name_verified"] is True for row in rows)
    assert all(row["observed_model_identity"]["digest"] == digest for row in rows)

    persisted_summary = json.loads(
        (output_dir / "results_summary.json").read_text(encoding="utf-8")
    )
    assert persisted_summary["empirical_claim_allowed"] is False
    assert (
        persisted_summary["model_identity_attestation"]["status"] == "alias_observed_not_immutable"
    )
    assert persisted_summary["model_identity_attestation"]["immutable_serving_attestation"] is False


@pytest.mark.asyncio
async def test_episode_finalize_replace_failure_preserves_prior_raw_rows(
    tmp_path,
    monkeypatch,
):
    model_name = "qwen2.5:7b-instruct"
    digest = "a" * 64
    before_replace = []

    async def identity_observer(requested_name):
        return {"name": requested_name, "digest": digest}

    async def fake_inference(_messages, _model_name, _generation_config):
        return zero_shot.InferenceResult(_prediction(), model_name)

    def fail_replace(_temporary_path, destination):
        before_replace.append(destination.read_bytes())
        raise OSError("simulated atomic replace failure")

    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")
    _set_clean_source_provenance(monkeypatch)
    monkeypatch.setattr(zero_shot, "observe_local_model_identity", identity_observer)
    monkeypatch.setattr(zero_shot, "openai_compatible_inference", fake_inference)
    monkeypatch.setattr(zero_shot.os, "replace", fail_replace)

    output_dir = tmp_path / "run"
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        await zero_shot.evaluate_zero_shot_split(
            "val",
            model_name=model_name,
            model_revision=digest,
            mode="empirical",
            output_dir=output_dir,
        )

    episode_file = output_dir / "results_per_episode.jsonl"
    assert len(before_replace) == 1
    assert episode_file.read_bytes() == before_replace[0]
    rows = [json.loads(line) for line in before_replace[0].decode().splitlines()]
    assert len(rows) == len(VAL_SPLIT)
    assert all(row["empirical_claim_allowed"] is False for row in rows)
    assert all(row["model_identity_attestation_status"] == "pending" for row in rows)
    assert all(row["raw_model_response"] == _prediction() for row in rows)
    assert list(output_dir.glob(".results_per_episode.jsonl.*.tmp")) == []
    assert not (output_dir / "results_summary.json").exists()


@pytest.mark.asyncio
async def test_configured_empirical_identity_drift_makes_run_nonclaimable(
    tmp_path,
    monkeypatch,
):
    model_name = "qwen2.5:7b-instruct"
    requested_digest = "a" * 64
    served_digest = "b" * 64
    identities = iter(
        [
            {"name": model_name, "digest": requested_digest},
            {"name": model_name, "digest": served_digest},
        ]
    )

    async def identity_observer(_requested_name):
        return next(identities)

    async def fake_inference(_messages, _model_name, _generation_config):
        return zero_shot.InferenceResult(_prediction(), model_name)

    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")
    _set_clean_source_provenance(monkeypatch)
    monkeypatch.setattr(zero_shot, "observe_local_model_identity", identity_observer)
    monkeypatch.setattr(zero_shot, "openai_compatible_inference", fake_inference)

    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        model_name=model_name,
        model_revision=requested_digest,
        mode="empirical",
        output_dir=tmp_path / "run",
    )

    assert summary["empirical_inference_executed"] is True
    assert summary["empirical_claim_allowed"] is False
    assert summary["model_identity_attestation"]["status"] == "alias_observed_not_immutable"
    assert summary["model_identity_attestation"]["recheck_status"] == "identity_changed"
    assert summary["model_identity_attestation"]["observations_match"] is False
    assert summary["model_identity_attestation"]["claimable"] is False
    assert summary["model_identity_attestation"]["observed_after"]["digest"] == served_digest
    episode_file = tmp_path / "run" / "results_per_episode.jsonl"
    rows = [json.loads(line) for line in episode_file.read_text(encoding="utf-8").splitlines()]
    assert all(row["empirical_claim_allowed"] is False for row in rows)
    assert all(
        row["model_identity_attestation_status"] == "alias_observed_not_immutable" for row in rows
    )
    assert all(row["model_identity_recheck_status"] == "identity_changed" for row in rows)
    assert (
        summary["raw_predictions_sha256"] == hashlib.sha256(episode_file.read_bytes()).hexdigest()
    )


@pytest.mark.asyncio
async def test_unverifiable_final_identity_recheck_makes_run_nonclaimable(
    tmp_path,
    monkeypatch,
):
    model_name = "qwen2.5:7b-instruct"
    digest = "a" * 64
    checks = 0

    async def identity_observer(_requested_name):
        nonlocal checks
        checks += 1
        if checks == 1:
            return {"name": model_name, "digest": digest}
        raise RuntimeError("untrusted detail must not be retained")

    async def fake_inference(_messages, _model_name, _generation_config):
        return zero_shot.InferenceResult(_prediction(), model_name)

    monkeypatch.setenv("VLLM_BASE", "http://127.0.0.1:11434/v1")
    _set_clean_source_provenance(monkeypatch)
    monkeypatch.setattr(zero_shot, "observe_local_model_identity", identity_observer)
    monkeypatch.setattr(zero_shot, "openai_compatible_inference", fake_inference)

    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        model_name=model_name,
        model_revision=digest,
        mode="empirical",
        output_dir=tmp_path / "run",
    )

    assert checks == 2
    assert summary["empirical_inference_executed"] is True
    assert summary["empirical_claim_allowed"] is False
    assert summary["model_identity_attestation"]["status"] == "alias_observed_not_immutable"
    assert summary["model_identity_attestation"]["recheck_status"] == "unverifiable"
    assert summary["model_identity_attestation"]["claimable"] is False
    assert "must not be retained" not in summary["model_identity_attestation"]["error"]
    episode_file = tmp_path / "run" / "results_per_episode.jsonl"
    rows = [json.loads(line) for line in episode_file.read_text(encoding="utf-8").splitlines()]
    assert all(row["empirical_claim_allowed"] is False for row in rows)
    assert all(
        row["model_identity_attestation_status"] == "alias_observed_not_immutable" for row in rows
    )
    assert all(row["model_identity_recheck_status"] == "unverifiable" for row in rows)


@pytest.mark.asyncio
async def test_empirical_mode_never_exposes_expected_root_cause(tmp_path):
    calls = []

    async def inference(messages, model_name, generation_config):
        calls.append(
            {
                "messages": messages,
                "model_name": model_name,
                "generation_config": generation_config,
            }
        )
        return _prediction()

    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        model_name="example/base-model",
        model_revision="0123456789abcdef",
        mode="empirical",
        output_dir=tmp_path,
        inference_fn=inference,
        inference_backend="openai-compatible",
    )

    assert len(calls) == len(VAL_SPLIT)
    for scenario_id, call in zip(VAL_SPLIT, calls, strict=True):
        serialized = json.dumps(call["messages"])
        assert SCENARIO_CATALOG[scenario_id].expected_root_cause not in serialized
        assert "expected_root_cause" not in serialized
        assert "chaos_kinds" not in serialized
        assert "manifest_relpath" not in serialized

    assert summary["evaluation_mode"] == "empirical"
    assert summary["empirical_inference_executed"] is True
    assert summary["empirical_claim_allowed"] is False
    assert summary["non_empirical"] is True
    assert summary["environment_resolution_evaluated"] is False
    assert summary["resolution_rate"] is None
    assert summary["avg_reward_contract"] is None
    assert summary["avg_time_to_resolve_s"] is None
    assert summary["model_revision"] == "0123456789abcdef"
    assert summary["failed_scenarios"] == 0
    assert summary["source"]["git_sha"]
    assert summary["raw_predictions_sha256"]


@pytest.mark.asyncio
async def test_empirical_failure_is_preserved_without_mock_fallback(
    tmp_path,
    monkeypatch,
):
    calls = 0
    secret_marker = "synthetic-injected-secret-marker"
    monkeypatch.setenv("ATLASOPS_MOCK_EVAL", "1")

    async def failing_inference(messages, model_name, generation_config):
        nonlocal calls
        calls += 1
        raise RuntimeError(secret_marker)

    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        model_revision="revision-1",
        mode="empirical",
        output_dir=tmp_path,
        inference_fn=failing_inference,
        inference_backend="injected-test-double",
    )

    assert calls == len(VAL_SPLIT)
    assert summary["failed_scenarios"] == len(VAL_SPLIT)
    assert summary["empirical_inference_executed"] is False
    assert summary["diagnostic_scored_count"] == 0
    assert summary["avg_diagnostic_f1"] is None
    rows = [
        json.loads(line)
        for line in (tmp_path / "results_per_episode.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert all(row["status"] == "error" for row in rows)
    assert all(row["empirical_inference_executed"] is False for row in rows)
    assert all(row["diagnostic_metrics"] is None for row in rows)
    assert all(row["evaluation_mode"] == "empirical" for row in rows)
    assert all("mock" not in row for row in rows)
    assert all(row["error"] == "RuntimeError: inference_failure; details redacted" for row in rows)
    assert secret_marker not in json.dumps(rows)


@pytest.mark.asyncio
async def test_custom_output_does_not_write_canonical_evidence(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical-evidence"
    output = tmp_path / "run-output"
    monkeypatch.setattr(zero_shot, "EVIDENCE_DIR", canonical)

    summary = await zero_shot.evaluate_zero_shot_split(
        "val",
        mode="mock",
        output_dir=output,
    )

    assert summary["mock_eval"] is True
    assert summary["non_empirical"] is True
    assert summary["empirical_inference_executed"] is False
    assert not canonical.exists()
    assert (output / "zero_shot_val_summary.json").exists()


@pytest.mark.asyncio
async def test_implicit_mock_output_is_isolated_from_canonical_evidence(tmp_path, monkeypatch):
    canonical = tmp_path / "canonical-evidence"
    monkeypatch.setattr(zero_shot, "EVIDENCE_DIR", canonical)
    monkeypatch.setattr(zero_shot, "RESULTS_DIR", tmp_path / "results")

    summary = await zero_shot.evaluate_zero_shot_split("val", mode="mock")

    assert summary["non_empirical"] is True
    assert not canonical.exists()
    assert "non_empirical" in summary["raw_predictions_path"]
    with pytest.raises(ValueError, match="explicit unique output_dir"):
        await zero_shot.evaluate_zero_shot_split(
            "val", mode="empirical", model_revision="revision"
        )
