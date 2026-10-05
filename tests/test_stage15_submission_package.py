"""Tests for Stage 15: Report, Package, and Submit (Gate G15).

Validates:
1. Generation and integrity of the final submission manifest (artifacts/SUBMISSION_MANIFEST.json).
2. Completeness of the final academic technical report (docs/AtlasOps_Technical_Report.md).
3. Cryptographic SHA-256 verification of canonical codebase assets.
4. Honest G0-G15 status inventory without inferred certification.
"""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.scenario_catalog import SCENARIO_CATALOG
from scripts.check_submission_links import check_current_markdown_links
from scripts.package_submission import (
    REQUIRED_REVIEW_ASSETS,
    _matching_tracked_files,
    build_submission_package,
    collect_submission_assets,
    compute_asset_inventory_sha256,
    compute_sha256,
    evaluate_package_readiness,
)


def _git_attributes(paths: set[str] | list[str]) -> dict[str, dict[str, str]]:
    ordered_paths = sorted(set(paths))
    output = subprocess.check_output(
        ["git", "check-attr", "-z", "--stdin", "text", "eol"],
        input=("\0".join(ordered_paths) + "\0").encode(),
    )
    fields = output.split(b"\0")
    attributes: dict[str, dict[str, str]] = {}
    for offset in range(0, len(fields) - 1, 3):
        path, name, value = (field.decode() for field in fields[offset : offset + 3])
        attributes.setdefault(path, {})[name] = value
    return attributes


def test_g7_prefreeze_review_documents_are_selected_when_tracked(monkeypatch):
    paths = [
        "docs/project/G7_D3_INDEPENDENT_PREFREEZE_REVIEW.md",
        "docs/project/G7_D3_PREFREEZE_CHECKPOINT.md",
    ]

    def indexed_files(args, **kwargs):
        assert args == ["git", "ls-files", "-z"]
        return SimpleNamespace(stdout=("\0".join(paths) + "\0").encode())

    monkeypatch.setattr("scripts.package_submission.subprocess.run", indexed_files)
    assets = collect_submission_assets()
    assert set(assets) == set(paths)
    for path in paths:
        raw = Path(path).read_bytes()
        assert assets[path] == {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }


def test_audit_implementation_is_selected_when_tracked(monkeypatch):
    path = "agents/audit.py"
    monkeypatch.setattr(
        "scripts.package_submission.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=(path + "\0").encode()),
    )
    raw = Path(path).read_bytes()
    assert collect_submission_assets() == {
        path: {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
    }


class TestStage15SubmissionPackage:
    def test_inventory_digest_is_stable_across_asset_mapping_order(self):
        assets = collect_submission_assets()
        reversed_assets = dict(reversed(list(assets.items())))

        digest = compute_asset_inventory_sha256(assets)
        assert len(digest) == 64
        assert compute_asset_inventory_sha256(reversed_assets) == digest

    def test_package_readiness_requires_assets_and_clean_local_links(self):
        complete_assets = {
            path: {"sha256": "0" * 64, "size_bytes": 1}
            for path in REQUIRED_REVIEW_ASSETS
        }

        assert evaluate_package_readiness(complete_assets, []) == {
            "package_ready": True,
            "package_readiness": "READY_FOR_REVIEW",
            "package_readiness_missing_assets": [],
            "package_readiness_link_errors": [],
        }
        missing_one = dict(complete_assets)
        missing_one.pop(REQUIRED_REVIEW_ASSETS[0])
        incomplete = evaluate_package_readiness(missing_one, [])
        assert incomplete["package_ready"] is False
        assert incomplete["package_readiness"] == "INCOMPLETE"
        assert incomplete["package_readiness_missing_assets"] == [
            REQUIRED_REVIEW_ASSETS[0]
        ]
        broken_links = evaluate_package_readiness(complete_assets, ["README.md:1 broken"])
        assert broken_links["package_ready"] is False
        assert broken_links["package_readiness_link_errors"] == ["README.md:1 broken"]

    def test_link_checker_resolves_relative_targets_and_ignores_code_and_external_urls(
        self, tmp_path
    ):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "target.md").write_text("# Target\n", encoding="utf-8")
        (docs / "source.md").write_text(
            "[target](target.md#section)\n"
            "[external](https://example.com/doc)\n"
            "`[inline code](missing-inline.md)`\n"
            "```md\n[code fence](missing-fence.md)\n```\n",
            encoding="utf-8",
        )

        assert check_current_markdown_links(tmp_path, ["docs/source.md"]) == []

    def test_link_checker_reports_missing_local_targets_and_repository_escapes(
        self, tmp_path
    ):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "source.md").write_text(
            "[missing](missing.md)\n[outside](../../outside.md)\n",
            encoding="utf-8",
        )

        errors = check_current_markdown_links(tmp_path, ["docs/source.md"])
        assert any("local link target does not exist: missing.md" in error for error in errors)
        assert any("local link escapes the repository: ../../outside.md" in error for error in errors)

    def test_link_checker_allows_documented_external_evidence_and_never_stats_heldout_outcomes(
        self, tmp_path, monkeypatch
    ):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "source.md").write_text(
            '[archive](../artifacts/evidence/stage9/full-archive.zip '
            '"External evidence SHA-256: ' + ("a" * 64) + '")\n'
            "[heldout](../artifacts/evidence/stage8/leaderboard-results.json)\n",
            encoding="utf-8",
        )
        original_exists = Path.exists

        def reject_heldout_stat(path):
            if "leaderboard-results.json" in str(path).lower():
                pytest.fail("protected held-out outcome path was statted")
            return original_exists(path)

        monkeypatch.setattr(Path, "exists", reject_heldout_stat)
        errors = check_current_markdown_links(tmp_path, ["docs/source.md"])
        assert len(errors) == 1
        assert "protected Test/Leaderboard outcome link is not checked" in errors[0]

    def test_submission_package_generator_creates_manifest_and_summary(self, tmp_path):
        manifest = build_submission_package(output_dir=tmp_path)
        assert manifest["project_name"] == "AtlasOps"
        assert manifest["pipeline_version"] == "v2.2"
        assert manifest["scope_revision"] == "GAI + RL (RS optional historical research)"
        assert manifest["gate_statuses_declared"]["G10"] == "OUT_OF_SCOPE"
        assert manifest["gate_statuses_declared"]["G11"] == "OUT_OF_SCOPE"
        assert manifest["gate_statuses_declared"]["G7"] == "PASS"
        assert len(manifest["academic_workstreams"]) == 2
        assert len(manifest["historical_optional_workstreams"]) == 1
        assert manifest["status"] == "NOT_CERTIFIED"
        assert manifest["status_scope"] == "scientific_pipeline_certification"
        assert manifest["gate_statuses_declared"]["G4"] == "NOT_PASSED"
        assert manifest["gate_statuses_declared"]["G9"] == "NOT_PASSED"
        assert manifest["gate_statuses_declared"]["G15"] == "PARTIAL"
        assert manifest["package_ready"] == (
            not manifest["package_readiness_missing_assets"]
            and not manifest["package_readiness_link_errors"]
        )
        assert manifest["asset_inventory_sha256"] == compute_asset_inventory_sha256(
            manifest["assets"]
        )

        manifest_path = tmp_path / "SUBMISSION_MANIFEST.json"
        summary_path = tmp_path / "SUBMISSION_SUMMARY.md"

        assert manifest_path.exists()
        assert summary_path.exists()

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["status"] == "NOT_CERTIFIED"
        assert data["empirical_metrics"] is None
        assert data["package_readiness"] in {"READY_FOR_REVIEW", "INCOMPLETE"}
        summary = summary_path.read_text(encoding="utf-8")
        assert "NOT_CERTIFIED" in summary
        assert "Package readiness" in summary
        assert "Scientific certification" in summary
        assert data["asset_inventory_sha256"] in summary
        assert "G3 PASS records historical local Kind acceptance" in summary
        assert "Private archives and model weights" in summary
        assert "100.0%" not in summary

    def test_technical_report_structure_and_completeness(self):
        report_path = Path("docs/AtlasOps_Technical_Report.md")
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")

        required_sections = [
            "# AtlasOps: Multi-Agent Incident Response with Generative AI and Policy Optimization",
            "## Abstract",
            "## 1. Problem and Motivation",
            "## 2. Architecture",
            "## 3. Safety and Governance",
            "## 7. Base-versus-SFT Validation Result",
            "## 9. Final Negative GRPO Result",
            "## 15. Conclusion and Deferred Research",
        ]

        for sec in required_sections:
            assert sec in content, f"Missing required section: {sec}"

    def test_submission_manifest_integrity_and_metrics(self, tmp_path):
        generated = build_submission_package(output_dir=tmp_path)
        data = json.loads(
            (tmp_path / "SUBMISSION_MANIFEST.json").read_text(encoding="utf-8")
        )
        assets = data["assets"]
        assert len(assets) >= 15
        assert assets == generated["assets"] == collect_submission_assets()
        assert {
            "ui_read_model.py",
            "static/index.html",
            "static/console.css",
            "static/console.js",
            "static/live-incident.js",
            "static/live-incident.test.js",
            "static/vendor/lucide.min.js",
            "static/vendor/LUCIDE-LICENSE",
            "AGENTS.md",
            "BLOG.md",
            "LICENSE",
            "docs/slides.md",
            "docs/EXPERIMENT_REGISTRY.md",
            "docs/EVIDENCE_INDEX.md",
            "docs/project/DEFERRED_RESEARCH_HANDOFF.md",
            "docs/project/REPRODUCTION.md",
            "docs/project/BASE_SFT_VALIDATION_RESULT_V1.md",
            "docs/project/CONTROLLED_G9_ADMISSION_V1.md",
            "docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md",
            "docs/project/G4_V38_RUN_OWNED_PREFLIGHT_GUARD_V1.md",
            "artifacts/evidence/mock_archive/README.md",
            "artifacts/evidence/stage7/free-t4-v17/RESULT.json",
            "artifacts/evidence/stage7/free-t4-v17/reload-v17.json",
            "artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/summary.json",
            "artifacts/evidence/stage8/base-sft-validation-v1/local-independent-recompute-v1.json",
            "artifacts/evidence/stage9/final-aligned-diagnostic-v1/diagnostic/diagnostic.json",
            "artifacts/evidence/stage9/final-aligned-diagnostic-v1/diagnostic/samples.jsonl",
            "artifacts/evidence/stage9/final-aligned-diagnostic-v1/LOCAL_VERIFICATION.json",
            "artifacts/evidence/stage9/final-aligned-diagnostic-v1/INDEPENDENT_REVIEW.json",
            "scripts/check_submission_links.py",
            "tests/test_current_project_truth.py",
            "docs/media/console-overview-20260926.png",
            "docs/media/gradio-demo-20260926.png",
            "agents/_http_retry.py",
            "agents/adversarial_designer.py",
            "pyproject.toml",
            "scripts/acceptance_stage3_local.py",
            "scripts/smoke_e2e_local.py",
            "scripts/smoke-e2e-local.sh",
            "scripts/smoke-e2e-local.ps1",
            "scripts/generate_training_plots.py",
            "training/merge_lora_for_hub.py",
            "recommender/_cli.py",
            "recommender/evaluate.py",
            "notebooks/README.md",
            "notebooks/kaggle_sft_training.ipynb",
            "notebooks/kaggle_grpo_training.ipynb",
            "DEPLOYMENT.md",
            "docs/MI300X_EVIDENCE.md",
            "docs/TRAINING_STORY.md",
            "assets/training/sft_loss.png",
            "assets/training/grpo_reward.png",
            "assets/training/benchmark_resolution.png",
            "assets/training/benchmark_per_tier.png",
            "agents/approval_http.py",
            "agents/judge.py",
            "agents/approval.py",
            "config/runtime.py",
            "config/scenario_catalog.py",
            "config/splits.py",
            "agents/verifier.py",
            "docs/project/G4_PROTOCOL_V34_APPROVAL_CHANNEL.md",
            "docs/project/G4_PROTOCOL_V35_CAUSAL_EVIDENCE.md",
            "docs/project/IMPLEMENTATION_STATUS.md",
            "docs/project/UPSTREAM_README_CURRENT_GAP_MATRIX.md",
            "docs/project/DYNAMIC_ADVERSARIAL_PROPOSAL_CONTRACT.md",
            "docs/project/G9_PROTOCOL_STANDALONE_P1_APPROVAL.md",
            "docs/project/G9_OBSERVATION_FIRST_PROTOCOL_V1_PROPOSAL.md",
            "docs/project/FINAL_PIPELINE_V22_STATUS.md",
            "docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_1.md",
            "docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_2.md",
            "docs/project/G13_COMMON_MEASUREMENT_CONTRACT_V0_3_PROPOSAL.md",
            "docs/project/G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_2.md",
            "docs/project/G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_3.md",
            "docs/project/G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_4.md",
            "docs/project/G7_G9_REMOTE_TRAINING_READINESS.md",
            "docs/project/G7_G13_REMOTE_EXECUTION_RUNBOOK.md",
            "docs/project/G7_G13_DECISION_REGISTER.md",
            "docs/project/G8_D12_PRE_RL_RESOLUTION_PROPOSAL_V1.md",
            "docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_3.md",
            "docs/project/GAI_RL_SCOPE_REVISION.md",
            "docs/project/STAGE_5_SCENARIO_TRUTH_AND_SPLITS.md",
            "docs/project/UPSTREAM_ALIGNMENT_AUDIT_REPORT.md",
            "tests/stage4_approval_process.py",
            "tests/g9_approval_process.py",
            "tests/test_stage4_approval_channel.py",
            "tests/test_stage5_scenario_splits_and_truth.py",
            "tests/test_verifier.py",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-010.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-010.cleanup.json",
            "artifacts/evidence/stage4/RECOVERY_INDEX_009_014.md",
            "artifacts/evidence/recovery/2026-09-05-workspace-recovery.json",
            "artifacts/evidence/.gitattributes",
            "artifacts/evidence/stage3/acceptance_report.json",
            "artifacts/evidence/stage10/rs_baseline_eval.json",
            "artifacts/evidence/recovery/SETUP-03_COMMANDS.md",
            "scripts/release_gate.py",
            "artifacts/evidence/stage7/sft_corpus_manifest.json",
            "artifacts/evidence/stage7/sft_training_config.json",
            "training/build_sft_dataset.py",
            "training/build_sft_candidate.py",
            "training/sft_candidate.py",
            "docs/project/G7_D3_CANDIDATE_REVIEW_V1.md",
            "docs/project/G7_D3_INDEPENDENT_PREFREEZE_REVIEW.md",
            "docs/project/G7_D3_PREFREEZE_CHECKPOINT.md",
            "docs/project/G7_SFT_PILOT_ACCEPTANCE_V1.md",
            "artifacts/evidence/stage7/candidates/train-candidate-v1/sft_corpus_train.jsonl",
            "artifacts/evidence/stage7/candidates/train-candidate-v1/sft_corpus_manifest.json",
            "artifacts/evidence/stage7/candidates/train-candidate-v1/quality_audit.md",
            "training/sft_rendering.py",
            "training/templates/qwen2_5_tool_sft.jinja",
            "training/grpo_reward.py",
            "tests/test_g9_direct_reward.py",
            "eval.py",
            "leaderboard.py",
        } <= assets.keys()

        frozen_manifest_paths = {
            scenario.manifest_relpath for scenario in SCENARIO_CATALOG.values()
        }
        assert len(frozen_manifest_paths) == 28
        packaged_scenario_manifests = {
            path
            for path in assets
            if path.startswith("bench/chaos_manifests/")
        }
        assert packaged_scenario_manifests == frozen_manifest_paths

        assert data["status"] == "NOT_CERTIFIED"
        assert data["pipeline_version"] == "v2.2"
        assert data["scope_revision"] == "GAI + RL (RS optional historical research)"
        assert data["gate_statuses_declared"]["G10"] == "OUT_OF_SCOPE"
        assert data["gate_statuses_declared"]["G11"] == "OUT_OF_SCOPE"
        assert data["gate_statuses_declared"]["G4"] == "NOT_PASSED"
        assert data["gate_statuses_declared"]["G7"] == "PASS"
        assert data["gate_statuses_declared"]["G9"] == "NOT_PASSED"
        assert data["gate_statuses_declared"]["G15"] == "PARTIAL"
        assert data["empirical_metrics"] is None
        assert data["status_scope"] == "scientific_pipeline_certification"
        assert data["asset_inventory_sha256"] == compute_asset_inventory_sha256(assets)
        assert data["package_ready"] == (
            not data["package_readiness_missing_assets"]
            and not data["package_readiness_link_errors"]
        )

        expected_stage4_assets = {
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-002.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-003.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-004.cleanup.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-004.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-005.cleanup.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-005.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-007.cleanup.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-007.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-008.cleanup.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-008.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-009.cleanup.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-009.interruption.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-009.leftover-chaos.yaml",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-010.cleanup.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-010.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-004.runlog.txt",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-005.runlog.txt",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-006.runlog.txt",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-007.runlog.txt",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-008.runlog.txt",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-011.cleanup.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-011.interruption.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-011.leftover-chaos.yaml",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-012.cleanup.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-012.interruption.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-013.cleanup.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-013.interruption.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-014.cleanup.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-014.interruption.json",
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-015.integrity-index-v1.json",
            "artifacts/evidence/stage4/RECOVERY_INDEX_009_014.md",
            "artifacts/evidence/stage4/golden_incident_sf002_manifest.json",
        }
        packaged_stage4_assets = {
            path for path in assets if path.startswith("artifacts/evidence/stage4/")
        }
        assert packaged_stage4_assets == expected_stage4_assets
        for attempt in range(9, 15):
            attempt_prefix = f"artifacts/evidence/stage4/EXP-STAGE4-SF002-{attempt:03d}."
            assert f"{attempt_prefix}cleanup.json" in packaged_stage4_assets
            result_kind = "json" if attempt == 10 else "interruption.json"
            assert f"{attempt_prefix}{result_kind}" in packaged_stage4_assets
        expected_stage4_runlogs = {
            f"artifacts/evidence/stage4/EXP-STAGE4-SF002-{attempt:03d}.runlog.txt"
            for attempt in range(4, 9)
        }
        assert {
            path for path in packaged_stage4_assets if path.endswith(".runlog.txt")
        } == expected_stage4_runlogs
        assert not any(
            ".attempts" in path.lower()
            or any(term in path.lower() for term in (".env", "secret", "token", "credential"))
            for path in packaged_stage4_assets
        )
        frozen_attempt_paths = {
            path
            for path in packaged_stage4_assets
            if any(
                f"EXP-STAGE4-SF002-{attempt:03d}." in path
                for attempt in range(9, 15)
            )
        }
        frozen_attributes = _git_attributes(frozen_attempt_paths)
        assert all(
            frozen_attributes[path]["text"] == "unset"
            for path in frozen_attempt_paths
        )

        for path_str, meta in assets.items():
            p = Path(path_str)
            assert p.exists(), f"Tracked asset {path_str} does not exist!"
            actual_sha = compute_sha256(p)
            assert actual_sha == meta["sha256"], f"Checksum mismatch for {path_str}!"
            assert p.stat().st_size == meta["size_bytes"]
            assert set(meta) == {"sha256", "size_bytes"}

        assert ".env" not in assets
        assert not any(
            Path(path).suffix.lower() in {".pt", ".safetensors"}
            for path in assets
        )
        assert "docs/RELEASE_READINESS.md" not in assets

    def test_frozen_asset_checkout_eol_preserves_git_blob_bytes(self):
        assets = collect_submission_assets()
        frozen_manifest_paths = {
            scenario.manifest_relpath for scenario in SCENARIO_CATALOG.values()
        }
        pre009_result_paths = {
            path
            for path in assets
            if path.startswith("artifacts/evidence/stage4/EXP-STAGE4-SF002-")
            and Path(path).name.split("SF002-", 1)[1][:3]
            in {"002", "003", "004", "005", "007", "008"}
            and path.endswith(".json")
        }
        pre009_result_paths.add(
            "artifacts/evidence/stage4/golden_incident_sf002_manifest.json"
        )
        crlf_blob_assets = frozen_manifest_paths | pre009_result_paths | {
            "artifacts/evidence/stage4/EXP-STAGE4-SF002-015.integrity-index-v1.json",
            "LICENSE",
        }
        assert len(pre009_result_paths) == 11
        assert len(crlf_blob_assets) == 41

        metadata_only_crlf_assets = {
            "artifacts/evidence/recovery/2026-09-05-workspace-recovery.json",
            "artifacts/evidence/stage3/acceptance_report.json",
            "artifacts/evidence/stage7/sft_corpus_manifest.json",
            "artifacts/evidence/stage7/sft_training_config.json",
            "artifacts/evidence/stage10/rs_baseline_eval.json",
            "training/templates/qwen2_5_tool_sft.jinja",
            *{
                path
                for path in assets
                if path.startswith("artifacts/evidence/stage4/")
                and path.endswith(".runlog.txt")
            },
        }
        assert metadata_only_crlf_assets <= assets.keys()

        crlf_attributes = _git_attributes(crlf_blob_assets | metadata_only_crlf_assets)
        for path, attributes in crlf_attributes.items():
            assert attributes == {"text": "set", "eol": "crlf"}

        for path in crlf_blob_assets:
            git_blob = subprocess.check_output(["git", "show", f"HEAD:{path}"])
            checkout_bytes = Path(path).read_bytes()
            assert checkout_bytes == git_blob.replace(b"\n", b"\r\n"), path

        recovery_commands = "artifacts/evidence/recovery/SETUP-03_COMMANDS.md"
        recovery_attributes = _git_attributes({recovery_commands})[recovery_commands]
        assert recovery_attributes == {"text": "set", "eol": "lf"}

    def test_all_assets_match_clean_linux_checkout_simulation(self):
        assets = collect_submission_assets()
        attributes = _git_attributes(set(assets))
        assert set(attributes) == set(assets)

        for path, metadata in assets.items():
            asset_path = Path(path)
            raw_bytes = asset_path.read_bytes()
            attr = attributes[path]
            if attr["eol"] == "crlf":
                assert attr["text"] == "set"
                linux_checkout_bytes = raw_bytes.replace(b"\r\n", b"\n").replace(
                    b"\n", b"\r\n"
                )
            elif attr["eol"] == "lf":
                assert attr["text"] == "set"
                linux_checkout_bytes = raw_bytes.replace(b"\r\n", b"\n")
            elif attr["text"] == "unset":
                linux_checkout_bytes = raw_bytes
            elif attr["text"] == "unspecified":
                worktree_blob = subprocess.check_output(
                    ["git", "hash-object", "--no-filters", "--", path],
                    text=True,
                ).strip()
                head_blob = subprocess.check_output(
                    ["git", "rev-parse", f"HEAD:{path}"],
                    text=True,
                ).strip()
                assert worktree_blob == head_blob, (
                    f"Unpinned asset differs from its Git blob: {path}"
                )
                linux_checkout_bytes = raw_bytes
            else:
                raise AssertionError(f"Unsupported text attribute for {path}: {attr}")

            simulated_sha256 = hashlib.sha256(linux_checkout_bytes).hexdigest()
            assert simulated_sha256 == metadata["sha256"], (
                f"Linux checkout hash mismatch for {path}"
            )

    def test_tracked_asset_selection_excludes_untracked_matches(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        tracked = tests_dir / "test_tracked_asset.py"
        tracked.write_text("# tracked\n", encoding="utf-8")
        untracked = tests_dir / "test_untracked_secret.py"
        untracked.write_text("# untracked fixture\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", "tests/test_tracked_asset.py"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )

        selected = _matching_tracked_files(repo_root, ["tests/test_*.py"])
        assert set(selected) == {"tests/test_tracked_asset.py"}

    def test_tracked_asset_selection_rejects_symlink_escapes(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        outside_target = tmp_path / "outside.py"
        outside_target.write_text("# outside target\n", encoding="utf-8")
        link = tests_dir / "test_escape.py"
        try:
            link.symlink_to(outside_target)
        except (OSError, NotImplementedError):
            pytest.skip("symbolic links are unavailable on this host")
        if not link.is_symlink():
            pytest.skip("Git worktree does not preserve symbolic links")
        subprocess.run(
            ["git", "add", "--", "tests/test_escape.py"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )

        with pytest.raises(ValueError, match="symbolic link"):
            _matching_tracked_files(repo_root, ["tests/test_*.py"])

    def test_tracked_parent_symlink_cannot_redirect_to_untracked_payload(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        tracked = tests_dir / "test_tracked_asset.py"
        tracked.write_text("# indexed version\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", "tests/test_tracked_asset.py"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )

        untracked_payload = repo_root / "payload"
        untracked_payload.mkdir()
        (untracked_payload / "test_tracked_asset.py").write_text(
            "# untracked payload\n", encoding="utf-8"
        )
        tracked.unlink()
        tests_dir.rmdir()
        try:
            tests_dir.symlink_to(untracked_payload, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("directory symbolic links are unavailable on this host")
        if not tests_dir.is_symlink():
            pytest.skip("Git worktree does not preserve directory symbolic links")

        with pytest.raises(ValueError, match="symbolic link"):
            _matching_tracked_files(repo_root, ["tests/test_*.py"])

    def test_tracked_parent_reparse_point_is_rejected(self, tmp_path, monkeypatch):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        tracked = tests_dir / "test_tracked_asset.py"
        tracked.write_text("# tracked\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", "tests/test_tracked_asset.py"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )

        original_lstat = Path.lstat

        def reparse_parent_lstat(path):
            if path == tests_dir:
                return SimpleNamespace(
                    st_mode=stat.S_IFDIR,
                    st_file_attributes=0x400,
                )
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", reparse_parent_lstat)
        with pytest.raises(ValueError, match="symbolic link or reparse point"):
            _matching_tracked_files(repo_root, ["tests/test_*.py"])

    def test_tracked_asset_selection_fails_closed_when_lstat_errors(
        self,
        tmp_path,
        monkeypatch,
    ):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        tracked = tests_dir / "test_tracked_asset.py"
        tracked.write_text("# tracked\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", "tests/test_tracked_asset.py"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )

        original_lstat = Path.lstat

        def inaccessible_parent_lstat(path):
            if path == tests_dir:
                raise OSError("inspection denied")
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", inaccessible_parent_lstat)
        with pytest.raises(ValueError, match="cannot be inspected"):
            _matching_tracked_files(repo_root, ["tests/test_*.py"])

    def test_stage13_raw_membership_parser_is_in_submission_inventory(self):
        assert "bench/episode_membership.py" in collect_submission_assets()

    def test_checked_in_submission_manifest_integrity_and_asset_keys(self):
        data = json.loads(
            Path("artifacts/SUBMISSION_MANIFEST.json").read_text(encoding="utf-8")
        )
        assets = data["assets"]
        assert data["asset_count"] == len(assets)

        for path_str, meta in assets.items():
            p = Path(path_str)
            assert p.exists(), f"Tracked asset {path_str} does not exist!"
            actual_sha = compute_sha256(p)
            assert actual_sha == meta["sha256"], f"Checksum mismatch for {path_str}!"
            assert p.stat().st_size == meta["size_bytes"]

        assert assets.keys() == collect_submission_assets().keys()
        assert data["asset_inventory_sha256"] == compute_asset_inventory_sha256(assets)
        assert data["package_ready"] == (
            not data["package_readiness_missing_assets"]
            and not data["package_readiness_link_errors"]
        )
        assert data["status"] == "NOT_CERTIFIED"
        assert data["status_scope"] == "scientific_pipeline_certification"

    def test_pipeline_master_status_records_all_gates(self):
        status_path = Path("docs/project/MASTER_PIPELINE_STATUS.md")
        assert status_path.exists()
        content = status_path.read_text(encoding="utf-8")

        for g_idx in range(16):
            gate_tag = f"**G{g_idx}**"
            assert gate_tag in content, f"Missing Gate G{g_idx} in MASTER_PIPELINE_STATUS.md"

    def test_g13_partial_decision_does_not_freeze_protocol(self):
        record = Path(
            "docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_2.md"
        ).read_text(encoding="utf-8")
        assert "PARTIAL PROJECT-LEAD APPROVAL / NOT FROZEN / NON-EXECUTABLE" in record
        assert "recommended A choices for items 1-4" in record
        assert "Item 5 pending" in record
        assert "Item 6 pending" in record
        assert "Item 7 deferred" in record
        assert "Empirical G6 and G8 outputs are diagnosis-only" in record
        assert "non-empirical markers exclude them from A1/A2 claims" in record
        assert "cannot yet retain every A1 eligible negative outcome" in record
        assert "G13 stays `REOPENED`" in record
        assert "`NOT_CERTIFIED`" in record

    def test_g7_g13_preparation_is_packaged_without_approving_execution(self):
        paths = {
            "docs/project/G13_COMMON_MEASUREMENT_CONTRACT_V0_3_PROPOSAL.md",
            "docs/project/G7_G9_REMOTE_TRAINING_READINESS.md",
            "docs/project/G7_G13_REMOTE_EXECUTION_RUNBOOK.md",
            "docs/project/G7_G13_DECISION_REGISTER.md",
        }
        assert paths <= collect_submission_assets().keys()
        contract = Path(
            "docs/project/G13_COMMON_MEASUREMENT_CONTRACT_V0_3_PROPOSAL.md"
        ).read_text(encoding="utf-8")
        readiness = Path(
            "docs/project/G7_G9_REMOTE_TRAINING_READINESS.md"
        ).read_text(encoding="utf-8")
        runbook = Path(
            "docs/project/G7_G13_REMOTE_EXECUTION_RUNBOOK.md"
        ).read_text(encoding="utf-8")
        decisions = Path(
            "docs/project/G7_G13_DECISION_REGISTER.md"
        ).read_text(encoding="utf-8")
        assert "PROPOSED / NOT APPROVED / NOT FROZEN / NON-EXECUTABLE" in contract
        assert "Items 1-4 retain their selected A directions" in contract
        assert "G13 `REOPENED`" in contract
        assert "TRAINING NOT AUTHORIZED" in readiness
        assert "historical" in readiness.lower()
        assert "FUTURE EXECUTION PLAN / NOT AUTHORIZED" in runbook
        assert "G4 is still `NOT_PASSED`" in runbook
        assert "D3 PREPARATION APPROVED, EXECUTION NOT APPROVED" in decisions
        assert "**APPROVED_FOR_PREPARATION.**" in decisions
        assert "Stage 15 remains `NOT_CERTIFIED`" in decisions

    def test_gai_rl_scope_amendment_keeps_three_arm_protocol_unfrozen(self):
        amendment = Path(
            "docs/project/G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_3.md"
        ).read_text(encoding="utf-8")
        scope = Path(
            "docs/project/GAI_RL_SCOPE_REVISION.md"
        ).read_text(encoding="utf-8")
        assert "NOT FROZEN" in amendment
        assert "V1: Zero-Shot Baseline" in amendment
        assert "V2: SFT Model" in amendment
        assert "V4: SFT + GRPO" in amendment
        assert "Neither record authorizes final-Test access" in amendment
        assert "G13_COMMON_MEASUREMENT_CONTRACT_V0_3_PROPOSAL.md" in scope
        assert "older, unapproved RS-inclusive planning snapshot" in scope

    def test_presentation_keeps_empirical_claims_open(self):
        slides = Path("docs/slides.md").read_text(encoding="utf-8")
        required_sections = (
            "## Incident Response Problem",
            "## Agent Workflow",
            "## SFT Artifact v17",
            "## Base-vs-SFT Validation Diagnostic",
            "## Controlled G9 Pilot Outcome",
            "## Final Aligned Action Diagnostic",
            "## G4 Live Incident Track",
            "## Historical Claim Boundary",
            "## Read-Only Local Demo",
            "## Conclusion and Deferred Research",
        )
        for section in required_sections:
            assert section in slides

        lower_slides = slides.lower()
        for value in ("0.16875", "0.15935", "-0.00940", "not_certified"):
            assert value in lower_slides
        assert "G4 remains NOT_PASSED" in slides
        assert "the g9 track is frozen" in lower_slides
        assert "no acceptable sft+grpo checkpoint" in lower_slides
        assert "zero of eight does not establish an exactly zero population probability" in lower_slides
        assert "Historical presentation evidence baseline" not in slides
        assert "SFT + Online GRPO Trained" not in slides
        assert "One real GKE cluster. No simulations." not in slides
