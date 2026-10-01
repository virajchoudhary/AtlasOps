"""Local cleanup contracts; no models, infrastructure, or external services."""

import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", ["eval.py", "leaderboard.py", "bench/quick_eval.py", "inference.py"])
def test_retired_live_cli_fails_before_writing_or_dispatching(script, tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / script), "--quick"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "disabled" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_container_build_context_excludes_local_secrets_and_worktrees():
    patterns = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {
        ".git", ".codex", ".agents", ".codex-worktrees", ".codex-tmp",
        ".venv", ".env", "*.env", "**/*.secret", "**/*.secrets",
        "**/*.key", "**/*.token", "secrets", ".secrets", "scratch",
        "data", "checkpoints", "artifacts/trajectories",
        "artifacts/overnight_test_outputs", "artifacts/overnight_diffs",
        "artifacts/local-preflight", "artifacts/evidence/stage4/.attempts",
    } <= patterns
    assert not {
        "agents", "config", "training", "bench", "static", "docs",
        "recommender", "demo", "requirements", "artifacts/evidence",
    } & patterns


@pytest.mark.parametrize(
    "filename",
    [
        ".env.local",
        ".env.production",
        "config/.env.local",
        "credentials.json",
        "config/application_default_credentials.json",
        "gcp-service-account.json",
        "config/service_account_key.json",
        "gcp-credentials-dev.json",
        "config/gcloud-credentials.json",
    ],
)
def test_credential_filename_globs_are_excluded_from_container_context(filename):
    patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    # These positive, slash-separated globs share Docker's filename semantics.
    assert any(PurePosixPath("/" + filename).match(pattern) for pattern in patterns)


def test_git_ignores_generated_clutter_but_not_research_evidence():
    generated = [
        ".codex-worktrees/fixture/app.py",
        ".codex-tmp/fixture/log.txt",
        ".tmp-pytest-fixture/output.json",
        ".pytest-tmp-fixture/output.json",
        ".g12_capture_integrity_fixture/output.json",
        "artifacts/overnight_test_outputs/fixture/output.json",
    ]
    preserved = [
        "artifacts/overnight_diffs/fixture.patch",
        "artifacts/local-preflight/fixture.json",
        "artifacts/evidence/stage4/EXP-STAGE4-SF002-010.json",
        "artifacts/evidence/stage7/candidates/train-candidate-v1/sft_corpus_train.jsonl",
        "training/grpo_observation_first.py",
        "OVERNIGHT_STATE.md",
    ]
    result = subprocess.run(
        ["git", "-c", "core.excludesFile=NUL", "check-ignore", "-z", "--no-index", "--stdin"],
        input=("\0".join(generated + preserved) + "\0").encode(),
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    assert {path.decode() for path in result.stdout.split(b"\0") if path} == set(generated)


def test_source_distribution_excludes_local_records_and_credential_filenames():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = set(project["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"])
    assert {
        "/OVERNIGHT_STATE.md",
        "/artifacts/local-preflight",
        "/artifacts/overnight_diffs",
    } <= patterns
    local_filenames = [
        "scripts/run_stage4_golden_incident.py.bak-0918",
        ".env.local",
        "config/.env.production",
        "credentials.json",
        "config/application_default_credentials.json",
        "config/gcp-service-account.json",
        "config/service_account_key.json",
        "config/gcp-credentials-dev.json",
        "config/gcloud-credentials.json",
        "config/operator.secret",
        "config/operator.secrets",
        "config/operator.key",
        "config/operator.token",
        "config/operator.pem",
        "config/operator.kubeconfig",
        "config/kubeconfig",
    ]
    preserved_source = [
        "agents/coordinator.py",
        "training/templates/qwen2_5_tool_sft.jinja",
        "artifacts/evidence/stage4/EXP-STAGE4-SF002-010.json",
        "artifacts/evidence/stage7/candidates/train-candidate-v1/sft_corpus_train.jsonl",
        "docs/project/MASTER_PIPELINE_STATUS.md",
        "LICENSE",
    ]
    assert all(
        any(PurePosixPath("/" + path).match(pattern) for pattern in patterns)
        for path in local_filenames
    )
    assert not any(
        PurePosixPath("/" + path).match(pattern)
        for path in preserved_source
        for pattern in patterns
    )


def test_old_training_shortcuts_do_not_launch_python():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("sft", "grpo"):
        recipe = makefile.split(f"\n{target}:\n", 1)[1].split("\n\n", 1)[0]
        assert "Retired:" in recipe
        assert "\t@exit 2" in recipe
        assert "\tpython" not in recipe


def test_review_guide_does_not_recommend_ungoverned_chaos():
    guide = (ROOT / "JUDGES_START_HERE.md").read_text(encoding="utf-8")
    assert "NOT_CERTIFIED" in guide
    assert "G4 is `NOT_PASSED`" in guide
    assert "python dashboard.py" in guide
    assert "kubectl apply" not in guide
    assert "kubectl delete" not in guide
    assert "Everything below hits a live GKE cluster" not in guide


def test_runtime_dependency_declarations_have_no_retired_clients():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = {Requirement(value).name for value in project["dependencies"]}
    assert not {"openai", "anthropic", "google-cloud-pubsub", "rich", "typer"} & dependencies
    assert {"httpx", "kubernetes", "google-cloud-logging"} <= dependencies
    assert "gradio" not in dependencies
    extras = project["optional-dependencies"]
    assert "gradio" in {Requirement(value).name for value in extras["demo"]}
    assert "gradio" in {Requirement(value).name for value in extras["dev"]}


def test_dev_lock_drops_orphans_but_retains_transitive_demo_dependencies():
    lock = (ROOT / "requirements/dev-win-py312.lock").read_text(encoding="utf-8")
    packages = {
        Requirement(line).name
        for line in lock.splitlines()
        if line and not line.startswith(("#", " ", "-"))
    }
    assert not {
        "openai", "anthropic", "docstring-parser", "google-cloud-pubsub",
        "opentelemetry-sdk", "opentelemetry-semantic-conventions",
        "torch", "transformers", "trl", "peft", "bitsandbytes", "vllm",
    } & packages
    assert {"rich", "typer", "gradio", "opentelemetry-api"} <= packages


def test_approval_test_handoff_is_invisible_until_json_is_complete(tmp_path, monkeypatch):
    import json

    from tests import stage4_approval_process as helper

    target = tmp_path / "result.json"
    original_dump = json.dump

    def observe_write(payload, stream):
        assert not target.exists()
        original_dump(payload, stream)
        stream.flush()
        assert not target.exists()

    monkeypatch.setattr(helper.json, "dump", observe_write)
    helper.publish_json(target, {"decision": "timeout"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"decision": "timeout"}
    assert not target.with_suffix(".json.tmp").exists()
