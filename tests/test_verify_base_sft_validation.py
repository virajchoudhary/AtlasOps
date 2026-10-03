import json

import pytest

from scripts.verify_base_sft_validation import (
    diagnostic_scores,
    parse_response,
    recompute,
)


def pair():
    prediction = {
        "severity": "P2",
        "affected_services": ["service"],
        "root_cause": "memory exhaustion",
        "confidence": 0.5,
    }
    return [
        {
            "scenario_id": "val-fixture",
            "arm": arm,
            "run_id": "fixture",
            "request_messages": [{"role": "user", "content": "alert"}],
            "prompt_token_ids": [1, 2],
            "generation_config": {"seed": 1337},
            "raw_model_response": json.dumps(prediction),
            "prediction": prediction,
            "status": "ok",
            "format_compliant": True,
            "diagnostic_precision": 1.0,
            "diagnostic_recall": 1.0,
            "diagnostic_f1": 1.0,
        }
        for arm in ("base", "sft")
    ]


def test_independent_token_set_scores():
    assert diagnostic_scores("memory memory exhaustion", "memory exhaustion")["f1"] == 1
    assert diagnostic_scores("memory issue", "memory exhaustion")["f1"] == 0.5


def test_valid_pair_recomputed():
    result = recompute(pair(), ["val-fixture"], {"val-fixture": "memory exhaustion"})
    assert result["base"]["avg_diagnostic_f1"] == 1
    assert result["sft"]["diagnostic_schema_conformance_rate"] == 1


@pytest.mark.parametrize("field", ["request_messages", "prompt_token_ids", "generation_config"])
def test_mismatched_conditions_rejected(field):
    rows = pair()
    rows[1][field] = None
    with pytest.raises(ValueError, match="Pair differs"):
        recompute(rows, ["val-fixture"], {"val-fixture": "memory exhaustion"})


def test_forged_score_rejected():
    rows = pair()
    rows[0]["diagnostic_f1"] = 0.5
    with pytest.raises(ValueError, match="score differs"):
        recompute(rows, ["val-fixture"], {"val-fixture": "memory exhaustion"})


def test_failure_keeps_null_scores_and_all_scheduled_denominator():
    rows = pair()
    row = rows[1]
    row.update(
        raw_model_response="malformed", prediction=None, status="error", format_compliant=False
    )
    for name in ("f1", "precision", "recall"):
        row[f"diagnostic_{name}"] = None
    result = recompute(rows, ["val-fixture"], {"val-fixture": "memory exhaustion"})
    assert result["sft"]["scheduled_count"] == 1
    assert result["sft"]["failed_scenarios"] == 1
    assert result["sft"]["avg_diagnostic_f1"] is None
    assert result["sft"]["diagnostic_schema_conformance_rate"] == 0
    assert result["paired"]["both_scored_count"] == 0
    assert result["paired"]["mean_sft_minus_base_diagnostic_f1"] is None


@pytest.mark.parametrize(
    "text",
    [
        '{"severity":"P2","severity":"P1"}',
        '{"severity":"P2","affected_services":["x"],"root_cause":"x","confidence":true}',
        '{"severity":"P2","affected_services":["x"],"root_cause":"x","confidence":0.5,"other":1e999}',
    ],
)
def test_strict_response_rejects_bad_json(text):
    with pytest.raises(ValueError):
        parse_response(text)


def test_missing_arm_rejected():
    with pytest.raises(ValueError, match="paired rows"):
        recompute(pair()[:1], ["val-fixture"], {"val-fixture": "memory exhaustion"})


def test_complete_runner_output_independently_verified(monkeypatch, tmp_path):
    import importlib.util
    import sys
    from pathlib import Path

    from bench import base_sft_validation
    from config import scenario_catalog, splits
    from scripts import verify_base_sft_validation

    spec = importlib.util.spec_from_file_location(
        "paired_test_fixture", Path(__file__).with_name("test_base_sft_validation.py")
    )
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    fixture = helpers._fixture_context(monkeypatch, tmp_path)
    fixture.manifest["checkpoint"]["files"] = [
        {
            "path": "adapter_model.safetensors",
            "sha256": verify_base_sft_validation.EXPECTED_ADAPTER_SHA256,
        }
    ]
    monkeypatch.setattr(
        verify_base_sft_validation, "EXPECTED_MANIFEST_SHA256", fixture.checkpoint_sha
    )
    helpers._install_fake_loaders(monkeypatch)
    base_sft_validation.run_base_sft_validation(
        checkpoint=fixture.checkpoint,
        checkpoint_manifest_sha256=fixture.checkpoint_sha,
        base_snapshot=fixture.base_snapshot,
        output_dir=fixture.output,
        run_id="checker-integration",
    )
    monkeypatch.setattr(splits, "get_split", fixture.evaluator.get_split)
    monkeypatch.setattr(scenario_catalog, "SCENARIO_CATALOG", fixture.evaluator.SCENARIO_CATALOG)
    output = tmp_path / "independent.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--run",
            str(fixture.output),
            "--output",
            str(output),
        ],
    )
    verify_base_sft_validation.main()
    report = json.loads(output.read_text())
    assert report["status"] == "PASS"
    assert report["metrics"]["paired"]["both_scored_count"] == 2

    manifest = json.loads((fixture.output / "run_manifest.json").read_text())
    rows = helpers._read_jsonl(fixture.output / "episodes.jsonl")
    rows[1]["adapter_state"] = "disabled"
    with pytest.raises(ValueError, match="Adapter state"):
        verify_base_sft_validation.validate_identity(manifest, rows)
    rows[1]["adapter_state"] = "enabled"
    manifest["input_identity"]["checkpoint_manifest_sha256_pin"] = "0" * 64
    with pytest.raises(ValueError, match="manifest pin"):
        verify_base_sft_validation.validate_identity(manifest, rows)
