"""Contracts for the read-only presentation HTML and its evidence labels."""

from __future__ import annotations

import re
from copy import deepcopy

from demo import presentation


def test_public_views_render_page_sections_and_header():
    renderers = (
        presentation.overview,
        presentation.models,
        presentation.evaluations,
        presentation.agents,
        presentation.incidents,
        presentation.evidence,
        presentation.runbooks,
        presentation.settings,
    )
    for render in renderers:
        assert '<section class="ao-page' in render()

    header = presentation.header()
    assert 'class="ao-topbar"' in header
    assert "AtlasOps" in header
    assert "Read-only" in header


def test_model_lineage_shows_preserved_sft_artifact_not_grpo_success():
    rendered = presentation.models()

    assert "Qwen2.5-7B-Instruct" in rendered
    assert "QLoRA SFT v17" in rendered
    assert "No accepted GRPO checkpoint" in rendered
    assert "<strong>68</strong><small>Train-only rows</small>" in rendered
    assert "<strong>9</strong><small>Optimizer steps</small>" in rendered
    assert "<strong>392</strong><small>LoRA tensors verified</small>" in rendered
    assert "Artifact success is not diagnostic improvement" in rendered


def test_evaluations_show_exact_negative_validation_and_g9_results():
    rendered = presentation.evaluations()

    assert "0.16875" in rendered
    assert "0.15935" in rendered
    assert "-0.00940" in rendered
    assert rendered.count("Schema 6/6") == 2
    assert "No diagnostic improvement observed." in rendered
    assert "Resolution, safety, reward, and time-to-resolve were not measured" in rendered
    assert "<strong>4</strong><small>Genuine completions</small>" in rendered
    assert "<strong>4</strong><small>No admissible pilot actions</small>" in rendered
    assert "<strong>-1</strong><small>For every pilot action</small>" in rendered
    assert "<strong>0</strong><small>Across two groups</small>" in rendered
    assert "No accepted checkpoint" in rendered
    assert "<h3>0/8 admissible actions</h3>" in rendered
    assert "<strong>392 tensor hashes unchanged</strong>" in rendered


def test_unavailable_validation_metrics_are_not_replaced_with_performance_values(monkeypatch):
    unavailable = deepcopy(presentation.current_results())
    validation = unavailable["base_vs_sft"]
    validation.update(
        available=False,
        base_f1=None,
        sft_f1=None,
        paired_delta=None,
        base_schema_valid=None,
        base_schema_total=None,
        sft_schema_valid=None,
        sft_schema_total=None,
    )
    monkeypatch.setattr(presentation, "current_results", lambda: unavailable)

    rendered = presentation.evaluations()

    assert "Unavailable" in rendered
    assert "Diagnostic conclusion unavailable." in rendered
    assert "0.16875" not in rendered
    assert "0.15935" not in rendered
    assert "-0.00940" not in rendered


def test_missing_g9_sources_do_not_claim_a_negative_result(monkeypatch):
    results = deepcopy(presentation.current_results())
    for key in ("g9_pilot", "g9_aligned_diagnostic"):
        for field in results[key]:
            if field not in {"evidence", "status"}:
                results[key][field] = None
        results[key]["available"] = False
        results[key]["status"] = "UNAVAILABLE"
    monkeypatch.setattr(presentation, "current_results", lambda: results)

    rendered = presentation.evaluations()
    assert "Checkpoint status unavailable" in rendered
    assert "No accepted checkpoint" not in rendered
    assert "392 tensor hashes unchanged" not in rendered
    assert "GRPO status unavailable" in presentation.models()


def test_launcher_keeps_unscoped_responsive_styles_and_share_disabled(monkeypatch):
    import dashboard
    from demo.launcher import launch_demo

    calls = []

    class Demo:
        def launch(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(dashboard, "build_app", Demo)
    launch_demo(port=7861)
    assert len(calls) == 1
    assert calls[0]["server_name"] == "127.0.0.1"
    assert calls[0]["share"] is False
    assert calls[0]["head"] == f"<style>{presentation.CSS}</style>"
    assert "css" not in calls[0]


def test_dynamic_model_metadata_is_html_escaped(monkeypatch):
    results = deepcopy(presentation.current_results())
    injection = "</code><img src=x onerror=alert(1)>"
    results["sft_v17"]["model_revision"] = injection
    monkeypatch.setattr(presentation, "current_results", lambda: results)

    rendered = presentation.models()

    assert injection not in rendered
    assert "&lt;/code&gt;&lt;img src=x onerror=alert(1)&gt;" in rendered
    assert "<img src=x" not in rendered


def test_settings_and_stylesheet_keep_demo_boundaries_and_reduced_motion():
    settings = presentation.settings()

    for boundary in (
        "Loopback only",
        "Read-only",
        "No kubectl",
        "No model inference",
        "No fault injection",
        "No approval mutation",
        "No remediation",
        "No cleanup",
        "No public share",
    ):
        assert boundary in settings

    assert re.search(
        r"@media\s*\(\s*prefers-reduced-motion:\s*reduce\s*\)",
        presentation.CSS,
    )
