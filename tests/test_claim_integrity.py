"""Guard current README claims against registry and gate-status drift."""

from pathlib import Path

from agents.tool_policy import AGENT_EXPOSED_TOOLS
from agents.tools import REGISTERED_TOOLS
from scripts.package_submission import declared_gate_statuses


def test_readme_tool_counts_match_runtime_policy():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert f"**{len(REGISTERED_TOOLS)} SRE tool wrappers**" in readme
    assert f"**{len(AGENT_EXPOSED_TOOLS)}** to autonomous agents" in readme
    assert "chaos_list_experiments" in readme


def test_open_gates_are_not_presented_as_certified_results():
    statuses = declared_gate_statuses()
    assert any(status != "PASS" for status in statuses.values())

    readme = Path("README.md").read_text(encoding="utf-8")
    audit = Path("docs/project/UPSTREAM_ALIGNMENT_AUDIT_REPORT.md").read_text(
        encoding="utf-8"
    )
    assert "Current continuation status: NOT_CERTIFIED" in readme
    assert "short_description: Evidence-led multi-agent SRE research demo (not certified)" in readme
    assert "responding to real GKE incidents" not in readme
    assert "certified across all" not in readme
    assert "| **Full Pipeline (GAI + RS + RL)** |" not in readme
    assert "default benchmark requests" not in readme
    assert "requested by a default benchmark run" not in readme
    assert "Results auto-update" not in readme
    assert "requirements/dev-win-py312.lock" in readme
    assert "infra/local/setup_local.sh --check" in readme
    assert "Retrospective correction (2026-09-27): NOT_CERTIFIED" in audit
    assert "claim of complete scientific reproducibility" in audit
    assert "structured schema validation" not in audit
    assert "10 real historic production outages" not in audit


def test_readme_training_instructions_match_governed_entrypoints():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "For judges" not in readme
    assert "Models: **Qwen2.5-7B" not in readme
    assert "5k trajectories (real GKE rollouts" not in readme
    assert "historical upstream references" in readme.lower()
    assert "python training/sft.py --model Qwen/Qwen2.5-7B-Instruct --rocm" not in readme
    assert "python training/grpo.py --model checkpoints/sft_v3 --rocm" not in readme
    assert "G=8 parallel agent chain rollouts" not in readme
    assert "serial" in readme.lower()
    assert "model-revision" in readme
    assert "tokenizer-revision" in readme
    assert "Stage 7" in readme and "Stage 9" in readme


def test_current_implementation_summary_matches_gate_and_tool_inventory():
    summary = Path("docs/project/IMPLEMENTATION_STATUS.md").read_text(encoding="utf-8")
    statuses = declared_gate_statuses()

    assert f"{len(REGISTERED_TOOLS)} wrappers are registered" in summary
    assert f"{len(AGENT_EXPOSED_TOOLS)} are exposed" in summary
    assert "Recommender Systems | ABSENT" not in summary
    assert "GRPO | PARTIAL / REVIEW-SENSITIVE" not in summary
    assert f"G4 `{statuses['G4']}`" in summary
    assert f"G9 `{statuses['G9']}`" in summary
    assert "Current continuation status: NOT_CERTIFIED" in summary


def test_upstream_readme_comparison_covers_each_delivery_area_without_certifying_it():
    matrix = Path(
        "docs/project/UPSTREAM_README_CURRENT_GAP_MATRIX.md"
    ).read_text(encoding="utf-8")

    assert "bf9bd197c9f4a05ae55ade254802a9eef1a74356" in matrix
    assert "7ed2bacb187577d1903a824254363c67e4b767dd" in matrix
    assert "through PR #81" in matrix
    assert "GENERATED_UNAPPROVED" in matrix
    assert "opt-in standalone G9 host channel" in matrix
    for heading in (
        "Architecture",
        "Track 1",
        "Track 2",
        "Training Evidence",
        "20 Real SRE Tools",
        "38 Chaos Scenarios",
        "Production Guardrails",
        "Training Pipeline",
        "Benchmark Results",
        "Quick Start",
        "Project Structure",
        "Why AMD MI300X",
        "License",
    ):
        assert heading in matrix
    for gate in ("G4", "G6", "G7", "G8", "G9", "G10", "G12", "G13", "G14", "G15"):
        assert gate in matrix
    assert "NOT_CERTIFIED" in matrix
    assert "does not close" in matrix
