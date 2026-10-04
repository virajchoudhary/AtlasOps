"""Qualification never reserves or executes an incident, including failures."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from config.g4_protocol import APPROVED_G4_V38_MODEL
from scripts import qualify_integrated_inference as qualification
from scripts import run_stage4_golden_incident as runner


def engine(*, response="READY", failure=None):
    records = []
    complete = AsyncMock(return_value={
        "choices": [{"message": {"content": response}}],
    }, side_effect=failure)
    return SimpleNamespace(
        fixture_backend=False, load_timeout_seconds=600, rpc_timeout_seconds=600,
        _journal=True, _write=records.append, records=records,
        provider=lambda arm: complete,
        verify_final=AsyncMock(return_value={
            "checkpoint_manifest_sha256": APPROVED_G4_V38_MODEL["checkpoint_manifest_sha256"],
            "runtime": {
                "platform": {
                    "system": "Linux", "machine": "x86_64", "python_version": "3.12.11",
                    "python_implementation": "CPython",
                },
                "package_versions": {
                    "torch": "2.7.1", "transformers": "4.57.6",
                    "peft": "0.17.1", "bitsandbytes": "0.46.1",
                },
                "cuda_runtime_version": "12.6", "cuda_available": True,
                "cuda_device_count": 1, "cuda_current_device": 0,
                "cuda_device_name": "Tesla T4",
            },
        }),
        close=AsyncMock(), complete=complete,
    )


@pytest.mark.asyncio
async def test_benign_qualification_keeps_raw_response_and_no_incident():
    candidate = engine()
    record = await qualification.qualify_engine(candidate)
    assert record["status"] == "QUALIFIED"
    assert record["qualification_only"] is True
    assert record["incident_attempt_reserved"] is False
    assert record["operational_tools_executed"] is False
    assert record["response"]["choices"][0]["message"]["content"] == "READY"
    role, request = candidate.complete.call_args.args
    assert role == "triage"
    assert request["messages"] == qualification.BENIGN_MESSAGES
    assert "tools" not in request
    assert record == candidate.records[-1]


@pytest.mark.asyncio
async def test_qualification_failure_is_preserved_and_main_closes(monkeypatch):
    candidate = engine(failure=TimeoutError())
    monkeypatch.setattr(qualification, "engine_from_environment", lambda: candidate)
    run = AsyncMock()
    monkeypatch.setattr(runner, "_main_with_qualified_inference", run)
    with pytest.raises(TimeoutError):
        await runner.main()
    run.assert_not_called()
    candidate.close.assert_awaited_once()
    assert candidate.records[-1]["status"] == "NOT_QUALIFIED"
    assert candidate.records[-1]["failure_category"] == "TimeoutError"
    assert runner._QUALIFIED_MODEL_IDENTITY is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["fixture_backend", "load_timeout_seconds", "rpc_timeout_seconds"])
async def test_qualification_rejects_unreviewed_backend_or_deadline(changed):
    candidate = engine()
    setattr(candidate, changed, True if changed == "fixture_backend" else 601)
    with pytest.raises(ValueError):
        await qualification.qualify_engine(candidate)
    candidate.complete.assert_not_called()


def test_missing_qualification_fails_before_reservation(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_QUALIFIED_MODEL_IDENTITY", None)
    monkeypatch.setattr(runner, "_current_main_sha", lambda **kwargs: "a" * 40)
    with pytest.raises(RuntimeError, match="qualified pinned Base"):
        runner.reserve_experiment_attempt(
            "EXP-STAGE4-SF002-016", selected_model=APPROVED_G4_V38_MODEL["name"],
            main_sha="a" * 40, attempt_root=str(tmp_path),
        )
    assert list(tmp_path.iterdir()) == []


def test_historical_model_cannot_use_qualified_base_identity(monkeypatch):
    monkeypatch.setattr(runner, "_QUALIFIED_MODEL_IDENTITY", deepcopy(APPROVED_G4_V38_MODEL))
    with pytest.raises(RuntimeError, match="qualified pinned Base"):
        runner._observe_protocol_profile("qwen2.5:7b-instruct")


@pytest.mark.asyncio
async def test_actual_decoding_drift_cannot_qualify(monkeypatch):
    candidate = engine()
    monkeypatch.setattr(
        qualification, "EFFECTIVE_GENERATION_CONFIG",
        {**qualification.EFFECTIVE_GENERATION_CONFIG, "max_new_tokens": 513},
    )
    with pytest.raises(ValueError, match="Effective decoding"):
        await qualification.qualify_engine(candidate)
    candidate.complete.assert_not_called()


def test_inherited_external_judge_is_disabled(monkeypatch):
    from agents.coordinator import _live_judge_requested

    monkeypatch.setenv("ATLASOPS_LIVE_JUDGE", "1")
    monkeypatch.setenv("ATLASOPS_USE_HF_INFERENCE", "1")
    with patch.dict("os.environ"):
        runner._configure_stage4_runtime()
        assert _live_judge_requested() is False


def test_raw_inference_reference_preserves_qualification_and_digest(monkeypatch, tmp_path):
    journal = tmp_path / "raw.jsonl"
    journal.write_bytes(b'{"record":"attempt_finished","status":"failed"}\n')
    qualification_record = {"loaded_identity": {"runtime": "synthetic"}, "status": "QUALIFIED"}
    monkeypatch.setattr(runner, "EXPERIMENT_ID", "EXP-STAGE4-SF002-016")
    monkeypatch.setattr(runner, "_INFERENCE_QUALIFICATION", qualification_record)
    monkeypatch.setattr(runner, "_experiment_evidence_dir", lambda _: str(tmp_path))
    runner._persist_inference_reference(SimpleNamespace(journal_path=journal))
    reference = json.loads((tmp_path / "EXP-STAGE4-SF002-016.inference-reference.json").read_bytes())
    assert reference["qualification"] == qualification_record
    assert reference["journal"]["raw_sha256"] == hashlib.sha256(journal.read_bytes()).hexdigest()
    assert reference["qualification_establishes_incident_resolution"] is False
    with pytest.raises(FileExistsError):
        runner._persist_inference_reference(SimpleNamespace(journal_path=journal))
