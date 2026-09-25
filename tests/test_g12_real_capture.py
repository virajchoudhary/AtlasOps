"""Non-live contracts for the governed Stage 12 evidence wrapper."""

from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from scripts import run_g12_integrated_episode as capture


def test_cli_requires_explicit_live_execution_before_any_work(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_g12_integrated_episode",
            "--checkpoint", str(tmp_path / "missing"),
            "--experiment-id", "EXP-STAGE4-SF002-999",
            "--bundle-dir", str(tmp_path / "bundle"),
            "--seed", "17",
        ],
    )
    monkeypatch.setattr(
        capture,
        "run_live_episode",
        lambda **_kwargs: pytest.fail("live harness invoked without opt-in"),
    )
    with pytest.raises(SystemExit) as exc:
        capture.main()
    assert exc.value.code == 2
    assert not (tmp_path / "bundle").exists()


def test_negative_integrated_attempt_is_archived_without_a_gate_claim(tmp_path):
    root = tmp_path / "repo"
    evidence_dir = root / "artifacts/evidence/stage4"
    trajectory_dir = root / "artifacts/trajectories"
    evidence_dir.mkdir(parents=True)
    trajectory_dir.mkdir(parents=True)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    experiment_id = "EXP-STAGE4-SF002-999"
    incident_id = "inc-test-999"
    source_sha = "a" * 40
    primary = {
        "experiment_id": experiment_id,
        "gate_g4_pass": False,
        "duration_seconds": 31.5,
        "source_identity": {"git_commit": source_sha},
        "preflight_evidence": {"persisted_before_injection": True},
        "protocol_profile": {"model": {"name": "fixture"}},
        "phases": {"coordinator_execution": {"incident_id": incident_id}},
    }
    incident = {
        "incident_id": incident_id,
        "alert": {"commonLabels": {"service": "paymentservice"}},
        "triage": {"final": {"severity": "P1"}},
        "diagnosis": {"final": {"root_cause": "observed pressure"}},
        "recommender": {"recommended_runbooks": [{"runbook_id": "RB-1"}]},
        "approval": {"decision": "approved"},
        "remediation": {
            "final": {"policy_backend": "checkpoint", "status": "unresolved"},
            "policy_steps": [{"parsed_action": {"tool": "chaos_stop_experiment"}}],
        },
        "settling": {"settled": False},
        "verification": {"env_resolved": False},
        "comms": {"final": {"summary": "Unresolved"}},
    }
    evidence_path = evidence_dir / f"{experiment_id}.json"
    evidence_path.write_text(json.dumps(primary), encoding="utf-8")
    (evidence_dir / f"{experiment_id}.cleanup.json").write_text(
        json.dumps({"verified_zero_chaos": True}), encoding="utf-8"
    )
    trajectory_path = trajectory_dir / f"{incident_id}.json"
    trajectory_path.write_text(json.dumps(incident), encoding="utf-8")

    manifest = capture.collect_bundle(
        root=root,
        bundle=bundle,
        experiment_id=experiment_id,
        source_sha=source_sha,
        checkpoint={"checkpoint_sha256": "b" * 64},
        seed=17,
        process_exit_code=1,
        checkpoint_postflight_verified=True,
    )

    assert manifest["status"] == "CAPTURED_FOR_REVIEW"
    assert manifest["empirical_claim_allowed"] is False
    assert manifest["gate_certification"] == "NOT_CERTIFIED"
    assert manifest["checkpoint_postflight_verified"] is True
    assert manifest["recorded_g4_verdict"] is False
    assert manifest["recorded_env_resolved"] is False
    assert manifest["time_to_resolve_s"] is None and manifest["reward"] is None
    assert manifest["policy_step_count"] == 1
    assert len(manifest["assets"]) == 3
    for asset in manifest["assets"].values():
        assert asset["sha256"] == hashlib.sha256((bundle / asset["path"]).read_bytes()).hexdigest()
    assert json.loads((bundle / "g12_capture_manifest.json").read_text()) == manifest


def test_missing_primary_is_preserved_as_incomplete(tmp_path):
    root = tmp_path / "repo"
    (root / "artifacts/evidence/stage4").mkdir(parents=True)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = capture.collect_bundle(
        root=root,
        bundle=bundle,
        experiment_id="EXP-STAGE4-SF002-999",
        source_sha="a" * 40,
        checkpoint={},
        seed=17,
        process_exit_code=1,
    )
    assert manifest["status"] == "INCOMPLETE"
    assert "primary_evidence_missing" in manifest["problems"]
    assert manifest["assets"] == {}


def test_live_wrapper_uses_governed_harness_and_records_failure(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "artifacts/evidence/stage4").mkdir(parents=True)
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(capture, "_source_sha", lambda _root: "a" * 40)
    monkeypatch.setattr(capture, "_checkpoint_record", lambda _checkpoint: {"verified": True})
    checkpoint = tmp_path / "checkpoint"
    bundle = tmp_path / "bundle"

    def fake_run(argv, *, cwd, env, check):
        assert argv == [sys.executable, "-m", "scripts.run_stage4_golden_incident"]
        assert cwd == root
        assert check is False
        assert env["ATLASOPS_REMEDIATION_BACKEND"] == "rl_policy"
        assert env["ATLASOPS_RL_POLICY_CHECKPOINT"] == str(checkpoint.resolve())
        assert env["STAGE4_EXPERIMENT_ID"] == "EXP-STAGE4-SF002-999"
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    manifest = capture.run_live_episode(
        checkpoint=checkpoint,
        experiment_id="EXP-STAGE4-SF002-999",
        bundle=bundle,
        seed=17,
    )
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["process_exit_code"] == 1
    assert (bundle / "g12_capture_manifest.json").is_file()


def test_live_wrapper_preserves_launch_failure(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "artifacts/evidence/stage4").mkdir(parents=True)
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(capture, "_source_sha", lambda _root: "a" * 40)
    monkeypatch.setattr(capture, "_checkpoint_record", lambda _checkpoint: {"verified": True})
    monkeypatch.setattr(
        capture.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable"))
    )
    bundle = tmp_path / "bundle"
    manifest = capture.run_live_episode(
        checkpoint=tmp_path / "checkpoint",
        experiment_id="EXP-STAGE4-SF002-999",
        bundle=bundle,
        seed=17,
    )
    assert manifest["status"] == "INCOMPLETE"
    assert manifest["process_exit_code"] == 127
    assert manifest["launch_error"] == "harness_launch_failed:OSError"
    assert (bundle / "g12_capture_manifest.json").is_file()


def test_live_wrapper_flags_checkpoint_change_after_run(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "artifacts/evidence/stage4").mkdir(parents=True)
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(capture, "_source_sha", lambda _root: "a" * 40)
    inventories = iter(({"tree_sha256": "a" * 64}, {"tree_sha256": "b" * 64}))
    monkeypatch.setattr(capture, "_checkpoint_record", lambda _checkpoint: next(inventories))
    monkeypatch.setattr(
        capture.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1)
    )
    manifest = capture.run_live_episode(
        checkpoint=tmp_path / "checkpoint",
        experiment_id="EXP-STAGE4-SF002-999",
        bundle=tmp_path / "bundle",
        seed=17,
    )
    assert manifest["checkpoint_postflight_verified"] is False
    assert "checkpoint_changed_during_run" in manifest["problems"]
    assert manifest["status"] == "INCOMPLETE"


def test_live_wrapper_rejects_unsafe_paths_before_execution(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(
        capture.subprocess, "run", lambda *args, **kwargs: pytest.fail("external command executed")
    )
    with pytest.raises(ValueError, match="Experiment ID"):
        capture.run_live_episode(
            checkpoint=tmp_path / "checkpoint",
            experiment_id="../../elsewhere",
            bundle=tmp_path / "bundle",
            seed=17,
        )
    with pytest.raises(ValueError, match="outside"):
        capture.run_live_episode(
            checkpoint=tmp_path / "checkpoint",
            experiment_id="EXP-STAGE4-SF002-999",
            bundle=root / "bundle",
            seed=17,
        )


def test_live_wrapper_rejects_existing_attempt_sidecar(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    evidence_dir = root / "artifacts/evidence/stage4"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "EXP-STAGE4-SF002-999.interruption.json").write_text(
        "{}", encoding="utf-8"
    )
    monkeypatch.setattr(capture, "ROOT", root)
    monkeypatch.setattr(capture, "_source_sha", lambda _root: "a" * 40)
    monkeypatch.setattr(capture, "_checkpoint_record", lambda _checkpoint: {"verified": True})
    monkeypatch.setattr(
        capture.subprocess, "run", lambda *args, **kwargs: pytest.fail("external command executed")
    )
    bundle = tmp_path / "bundle"
    with pytest.raises(FileExistsError, match="artifact already exists"):
        capture.run_live_episode(
            checkpoint=tmp_path / "checkpoint",
            experiment_id="EXP-STAGE4-SF002-999",
            bundle=bundle,
            seed=17,
        )
    assert not bundle.exists()
