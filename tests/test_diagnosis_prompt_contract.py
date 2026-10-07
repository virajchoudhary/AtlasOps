"""Model-visible Diagnosis contract checks for scenario-neutral discovery."""

import json
from pathlib import Path
import re

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
