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
from scripts.package_submission import (
    _matching_tracked_files,
    build_submission_package,
    collect_submission_assets,
    compute_sha256,
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


class TestStage15SubmissionPackage:
    def test_submission_package_generator_creates_manifest_and_summary(self, tmp_path):
        manifest = build_submission_package(output_dir=tmp_path)
        assert manifest["project_name"] == "AtlasOps"
        assert manifest["status"] == "NOT_CERTIFIED"
        assert manifest["gate_statuses_declared"]["G4"] == "NOT_PASSED"
        assert manifest["gate_statuses_declared"]["G13"] == "REOPENED"
        assert manifest["gate_statuses_declared"]["G15"] == "PARTIAL"

        manifest_path = tmp_path / "SUBMISSION_MANIFEST.json"
        summary_path = tmp_path / "SUBMISSION_SUMMARY.md"

        assert manifest_path.exists()
        assert summary_path.exists()

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["status"] == "NOT_CERTIFIED"
        assert data["empirical_metrics"] is None
        summary = summary_path.read_text(encoding="utf-8")
        assert "NOT_CERTIFIED" in summary
        assert "100.0%" not in summary

    def test_technical_report_structure_and_completeness(self):
        report_path = Path("docs/AtlasOps_Technical_Report.md")
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")

        required_sections = [
            "# AtlasOps: Autonomous Multi-Agent Incident Response",
            "## Abstract",
            "## 1. Introduction & Background",
            "## 2. System Architecture & Multi-Agent Flow",
            "## 3. Academic Workstreams & Methodology",
            "## 4. Empirical Evaluation & Multi-Model Ablations",
            "## 5. Demonstration & Operator Console",
            "## 6. Conclusion & Attribution",
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
            "docs/slides.md",
            "docs/media/console-overview-20260926.png",
            "docs/media/gradio-demo-20260926.png",
            "agents/_http_retry.py",
            "agents/adversarial_designer.py",
            "pyproject.toml",
            "agents/judge.py",
            "agents/approval.py",
            "config/runtime.py",
            "config/scenario_catalog.py",
            "config/splits.py",
            "agents/verifier.py",
            "docs/project/G4_PROTOCOL_V34_APPROVAL_CHANNEL.md",
            "docs/project/DYNAMIC_ADVERSARIAL_PROPOSAL_CONTRACT.md",
            "docs/project/STAGE_5_SCENARIO_TRUTH_AND_SPLITS.md",
            "docs/project/UPSTREAM_ALIGNMENT_AUDIT_REPORT.md",
            "tests/stage4_approval_process.py",
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
        assert data["gate_statuses_declared"]["G4"] == "NOT_PASSED"
        assert data["gate_statuses_declared"]["G15"] == "PARTIAL"
        assert data["empirical_metrics"] is None

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
        crlf_blob_assets = frozen_manifest_paths | pre009_result_paths
        assert len(pre009_result_paths) == 11
        assert len(crlf_blob_assets) == 39

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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        tracked = tests_dir / "test_tracked_asset.py"
        tracked.write_text("# indexed version\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", "tests/test_tracked_asset.py"],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        tracked = tests_dir / "test_tracked_asset.py"
        tracked.write_text("# tracked\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", "tests/test_tracked_asset.py"],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        tracked = tests_dir / "test_tracked_asset.py"
        tracked.write_text("# tracked\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "--", "tests/test_tracked_asset.py"],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        original_lstat = Path.lstat

        def inaccessible_parent_lstat(path):
            if path == tests_dir:
                raise OSError("inspection denied")
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", inaccessible_parent_lstat)
        with pytest.raises(ValueError, match="cannot be inspected"):
            _matching_tracked_files(repo_root, ["tests/test_*.py"])

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

    def test_pipeline_master_status_records_all_gates(self):
        status_path = Path("docs/project/MASTER_PIPELINE_STATUS.md")
        assert status_path.exists()
        content = status_path.read_text(encoding="utf-8")

        # Verify all 15 Gates are recorded
        for g_idx in range(1, 16):
            gate_tag = f"**G{g_idx}**"
            assert gate_tag in content, f"Missing Gate G{g_idx} in MASTER_PIPELINE_STATUS.md"

    def test_presentation_keeps_empirical_claims_open(self):
        slides = Path("docs/slides.md").read_text(encoding="utf-8")
        assert slides.count("\n---\n") == 8
        assert "Reviewed code baseline: `8560a8c7c46a8f91d74c574ebdf9c456e2835b4b`" in slides
        assert "Current reviewed main:" not in slides
        assert "G4 remains NOT_PASSED" in slides
        assert "NOT_CERTIFIED" in slides
        assert "SFT + Online GRPO Trained" not in slides
        assert "One real GKE cluster. No simulations." not in slides
