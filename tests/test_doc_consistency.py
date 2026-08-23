"""Offline tests for the documentation consistency guardrail.

These tests are fast and hermetic:
- They never touch a cluster, network, or the filesystem outside tmp_path
  (except reading the real repo for the end-to-end green check).
- Runtime truth is introspected from the live modules, so if a tool or frozen
  scenario is added later, ``test_runtime_truth_matches_governance_numbers``
  fails until the governance docs and this pin are updated together.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

from scripts.check_doc_consistency import (
    CURRENT_TRUTH_FILES,
    REPO_ROOT,
    check_manifest_inventory,
    check_numeric_claims,
    check_stale_patterns,
    main,
    runtime_truth,
)

# Governance-pinned numbers. If one of these legitimately changes (e.g. tool
# #24 is registered), update the docs AND this pin in the same PR; the
# guardrail script itself derives expectations from runtime, not from here.
EXPECTED_REGISTERED = 23
EXPECTED_EXPOSED = 20
EXPECTED_FROZEN = 28
EXPECTED_DYNAMIC = 10


class TestRuntimeTruthIntrospection:
    """The guardrail's notion of reality must match the pinned truth."""

    def test_registered_tool_count(self) -> None:
        assert runtime_truth().registered == EXPECTED_REGISTERED

    def test_agent_exposed_count(self) -> None:
        assert runtime_truth().exposed == EXPECTED_EXPOSED

    def test_unexposed_tools_are_the_documented_three(self) -> None:
        assert set(runtime_truth().unexposed) == {
            "argocd_app_get",
            "kubectl_exec",
            "kubectl_top_nodes",
        }

    def test_frozen_scenario_count(self) -> None:
        assert runtime_truth().frozen_scenarios == EXPECTED_FROZEN

    def test_dynamic_adversarial_default(self) -> None:
        assert runtime_truth().dynamic_count == EXPECTED_DYNAMIC


class TestNumericClaimScanning:
    def test_current_repo_claims_match_truth(self) -> None:
        problems = check_numeric_claims(runtime_truth(), CURRENT_TRUTH_FILES)
        assert problems == []

    def test_detects_wrong_total_in_tmp_doc(self, tmp_path: Path) -> None:
        fake = tmp_path / "README.md"
        fake.write_text("AtlasOps registers **24 registered** wrappers.\n", encoding="utf-8")
        problems = check_numeric_claims(runtime_truth(), [str(fake)])
        assert any("claim 24" in p for p in problems)

    def test_per_module_breakdown_rows_are_not_flagged(self, tmp_path: Path) -> None:
        fake = tmp_path / "README.md"
        fake.write_text("kubectl (8 registered / 6 exposed)\n", encoding="utf-8")
        problems = check_numeric_claims(runtime_truth(), [str(fake)])
        assert problems == []


class TestStalePatternRejection:
    def test_repo_has_no_stale_claims(self) -> None:
        assert check_stale_patterns() == []

    def test_stale_totals_are_rejected(self, tmp_path: Path) -> None:
        fake = tmp_path / "DOC.md"
        fake.write_text(
            "22 registered wrappers; 19 agent-exposed\n", encoding="utf-8"
        )
        problems = check_stale_patterns([str(fake)])
        assert len(problems) >= 2

    def test_historical_pr9_lines_are_exempt(self) -> None:
        problems = check_stale_patterns(["docs/project/MASTER_PIPELINE_STATUS.md"])
        assert problems == []


class TestManifestInventory:
    def test_frozen_catalogue_maps_to_manifests_on_disk(self) -> None:
        problems = check_manifest_inventory(runtime_truth())
        assert problems == []

    def test_missing_manifest_is_detected(self, tmp_path: Path) -> None:
        base = tmp_path / "chaos_manifests"
        (base / "single_fault").mkdir(parents=True)
        problems = check_manifest_inventory(runtime_truth(), manifests_dir=base)
        assert any("single_fault" in p and "missing" in p for p in problems)


class TestMainEntryPoint:
    def test_main_exits_zero_and_reports_pass(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main()
        assert code == 0
        assert "DOC CONSISTENCY: PASS" in buf.getvalue()
