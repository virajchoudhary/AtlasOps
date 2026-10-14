"""Model-visible Diagnosis contract checks for scenario-neutral discovery."""

import json
import re
from pathlib import Path

from scripts.run_stage4_golden_incident import evaluate_causal_g4_predicate

_PROMPT = Path(__file__).resolve().parents[1] / "agents" / "prompts" / "diagnosis.md"


def test_unknown_category_is_part_of_the_output_contract():
    prompt = _PROMPT.read_text(encoding="utf-8")
    assert (
        'category": "deploy|resource|network|dependency|config|external|unknown'
        in prompt
    )
    assert 'return `category: "unknown"`' in prompt


def test_prompt_does_not_name_a_fault_mechanism_or_namespace():
    prompt = _PROMPT.read_text(encoding="utf-8").casefold()
    assert "stresschaos" not in prompt
    assert "podchaos" not in prompt
    assert "chaos-mesh" not in prompt
    assert "stop_chaos" not in prompt


def test_generic_discovery_is_available_without_scenario_hints():
    prompt = _PROMPT.read_text(encoding="utf-8")
    assert "`kubectl_get`" in prompt
    assert "installed resource types" in prompt
    assert "relevant custom resources" in prompt


def test_prompt_example_matches_runtime_diagnosis_shape_without_claiming_pass():
    prompt = _PROMPT.read_text(encoding="utf-8")
    example = re.search(r"```json\s*(.*?)\s*```", prompt, re.DOTALL)
    assert example is not None
    diagnosis = json.loads(example.group(1))
    result = evaluate_causal_g4_predicate(
        baseline_healthy=False, injection_success=False, fault_observed=False,
        incident_result={"diagnosis": {"final": diagnosis, "trajectory": []}},
        harness_repaired_pre_verification=False,
    )
    assert result["criteria"]["6_diagnosis_valid"] is True
    assert result["criteria"]["7_diagnosis_truth_match"] is False
    assert result["gate_g4_pass"] is False
    assert isinstance(diagnosis["evidence"], list)
    assert isinstance(diagnosis["recommended_fix"], list)
    assert type(diagnosis["confidence"]) in (int, float)
    assert 0 <= diagnosis["confidence"] <= 1
    assert "no_series" in prompt


def test_synthetic_candidate_builder_emits_renderable_canonical_diagnosis_target():
    from agents.coordinator import _remediation_plan_summary, _try_parse_json
    from training import build_sft_candidate
    from training.sft import validate_diagnosis_target_contracts
    from training.sft_rendering import (
        prepare_example_for_training,
        render_messages,
    )

    prompt = _PROMPT.read_text(encoding="utf-8")
    examples = [
        row
        for row in build_sft_candidate.build_candidate_rows()
        if row["role"] == "diagnosis"
    ]
    originals = [json.dumps(example, sort_keys=True) for example in examples]

    validate_diagnosis_target_contracts(examples)
    prepared = prepare_example_for_training(examples[0])
    rendered, generation_spans = render_messages(
        prepared["messages"],
        prepared["tools"],
        track_generation=True,
    )

    assert prepared["messages"][0]["content"] == prompt
    assert [json.dumps(example, sort_keys=True) for example in examples] == originals
    assert prepared["provenance"]["role"] == "diagnosis"
    target = json.loads(examples[0]["messages"][-1]["content"])
    parsed = _try_parse_json(examples[0]["messages"][-1]["content"])
    summary = _remediation_plan_summary({"severity": "P1"}, parsed)
    assert parsed == target
    assert isinstance(target["root_cause"], str)
    assert target["root_cause"] in summary
    assert str(target["recommended_fix"]) in summary
    assert "apply safe rollback/scale" not in summary
    assert any(examples[0]["messages"][-1]["content"] in span for span in generation_spans)
    assert "<tool_response>" not in "\n".join(generation_spans)
    assert target["root_cause"] in rendered
