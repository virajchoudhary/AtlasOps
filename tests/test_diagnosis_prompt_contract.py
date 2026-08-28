"""Model-visible Diagnosis contract checks for scenario-neutral discovery."""

from pathlib import Path


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
