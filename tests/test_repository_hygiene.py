"""Local cleanup contracts; no models, infrastructure, or external services."""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def test_pinned_trainer_ci_does_not_install_the_demo_development_stack():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    trainer = workflow.split("  trainer-contract:", 1)[1].split("  frontend:", 1)[0]
    assert 'python -m pip install -e . pytest \\' in trainer
    assert '.[dev]' not in trainer
    assert 'trl==0.19.1 transformers==4.57.6 peft==0.17.1' in trainer
    assert 'ATLASOPS_REQUIRE_PINNED_CPU_STACK: "1"' in trainer
    assert 'ATLASOPS_REQUIRE_TRL_INTEGRATION: "1"' in trainer
    assert 'python -m pytest tests/test_grpo_observation_installed_trl.py -q' in trainer
    quality = workflow.split("  quality:", 1)[1]
    assert 'python -m pip install -e ".[dev]" build' in quality


@pytest.mark.parametrize("quiet", [False, True])
@pytest.mark.parametrize("returncode", [0, 1, 5])
def test_shared_smoke_runner_preserves_selection_interpreter_and_failure(
    monkeypatch, quiet, returncode
):
    from types import SimpleNamespace

    from scripts import smoke_e2e_local

    observed = []

    def run(command, **kwargs):
        observed.append((command, kwargs))
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(smoke_e2e_local.subprocess, "run", run)
    assert smoke_e2e_local.main(["--quiet"] if quiet else []) == returncode
    assert observed == [(
        [
            sys.executable, "-m", "pytest",
            "tests/test_app_endpoints.py", "tests/test_coordinator.py",
            "tests/test_tools.py", "tests/test_bench_runner.py",
            "-q" if quiet else "-v",
        ],
        {"cwd": ROOT, "check": False},
    )]


def test_smoke_wrappers_delegate_without_duplicate_test_lists():
    shell = (ROOT / "scripts/smoke-e2e-local.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "scripts/smoke-e2e-local.ps1").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert 'exec python "${SCRIPT_DIR}/smoke_e2e_local.py" --quiet' in shell
    assert 'Join-Path $PSScriptRoot "smoke_e2e_local.py"' in powershell
    assert "exit $LASTEXITCODE" in powershell
    assert "\tpython scripts/smoke_e2e_local.py --quiet" in makefile
    assert "tests/test_" not in shell and "tests/test_" not in powershell


def test_powershell_smoke_wrapper_forwards_quiet_and_nonzero_exit(tmp_path):
    shell = shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    if os.name == "nt":
        python = tmp_path / "python.cmd"
        python.write_text("@echo off\necho %*\nexit /b 5\n", encoding="utf-8")
    else:
        python = tmp_path / "python"
        python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 5\n', encoding="utf-8")
        python.chmod(0o755)
    result = subprocess.run(
        [shell, "-NoProfile", "-File", str(ROOT / "scripts/smoke-e2e-local.ps1"), "-Quiet"],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 5
    assert "smoke_e2e_local.py" in result.stdout
    assert "--quiet" in result.stdout


def test_bash_smoke_wrapper_forwards_quiet_and_nonzero_exit(tmp_path):
    shell = shutil.which("bash")
    if os.name == "nt" or not shell:
        pytest.skip("POSIX Bash wrapper test")
    python = tmp_path / "python"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 5\n', encoding="utf-8")
    python.chmod(0o755)
    result = subprocess.run(
        [shell, str(ROOT / "scripts/smoke-e2e-local.sh"), "quiet"],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 5
    assert "smoke_e2e_local.py" in result.stdout
    assert "--quiet" in result.stdout


@pytest.mark.parametrize(
    "script",
    [
        "eval.py",
        "leaderboard.py",
        "bench/quick_eval.py",
        "inference.py",
        "scripts/generate_training_plots.py",
        "training/merge_lora_for_hub.py",
    ],
)
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


def test_retired_model_export_import_has_no_optional_model_imports_or_side_effects(tmp_path):
    path = ROOT / "training/merge_lora_for_hub.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))
    code = (
        "import runpy; "
        f"runpy.run_path({str(path)!r}, run_name='retired_export_import'); "
        "print('IMPORT_OK')"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout.strip() == "IMPORT_OK"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "notebook", ["kaggle_sft_training.ipynb", "kaggle_grpo_training.ipynb"]
)
def test_retired_notebook_fails_without_imports_shell_commands_or_writes(notebook, tmp_path):
    path = ROOT / "notebooks" / notebook
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["nbformat"] == 4
    assert "accelerator" not in document["metadata"]
    code_cells = [cell for cell in document["cells"] if cell["cell_type"] == "code"]
    assert len(code_cells) == 1
    cell = code_cells[0]
    assert cell["execution_count"] is None
    assert cell["outputs"] == []
    source = "".join(cell["source"])
    tree = ast.parse(source)
    assert len(tree.body) == 1
    statement = tree.body[0]
    assert isinstance(statement, ast.Raise)
    assert isinstance(statement.exc, ast.Call)
    assert isinstance(statement.exc.func, ast.Name)
    assert statement.exc.func.id == "SystemExit"
    assert len(statement.exc.args) == 1 and not statement.exc.keywords
    assert isinstance(statement.exc.args[0], ast.Constant)
    assert isinstance(statement.exc.args[0].value, str)
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", source],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    assert "disabled" in result.stderr
    assert list(tmp_path.iterdir()) == []
    for cell in document["cells"]:
        if cell["cell_type"] == "markdown":
            for target in re.findall(r"\]\(([^)]+)\)", "".join(cell["source"])):
                assert (path.parent / target).is_file()


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


def test_ui_container_uses_project_runtime_dependencies_and_pinned_kubectl():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    coordinator = (ROOT / "Dockerfile.coordinator").read_text(encoding="utf-8")
    assert 'COPY . .\nRUN pip install --no-cache-dir . "uvicorn[standard]"' in dockerfile
    assert "aiofiles" not in dockerfile
    assert "gnupg" not in dockerfile
    assert "curl git" not in dockerfile
    assert ".[dev]" not in dockerfile and ".[train]" not in dockerfile
    assert "stable.txt" not in dockerfile
    for argument in ("KUBECTL_VERSION", "KUBECTL_SHA256"):
        value = re.search(rf"^ARG {argument}=(.+)$", coordinator, re.MULTILINE)
        assert value is not None
        assert f"ARG {argument}={value.group(1)}" in dockerfile
    assert "sha256sum -c -" in dockerfile
    assert 'CMD ["python", "app.py"]' in dockerfile
    assert "EXPOSE 7860" in dockerfile


def test_ci_builds_ui_image_and_checks_it_without_external_network():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["ui-container"]
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "docker build --tag atlasops-ui-ci ." in commands
    assert "--network none --name atlasops-ui-ci" in commands
    assert "--env ATLASOPS_AUTO_HF_INFERENCE=0" in commands
    assert 'base = "http://127.0.0.1:7860"' in commands
    assert 'base + "/webhook", data=b"{}"' in commands
    assert "assert exc.code == 503" in commands
    cleanup = job["steps"][-1]
    assert cleanup["if"] == "always()"
    assert "docker stop atlasops-ui-ci" in cleanup["run"]
    assert "docker container rm atlasops-ui-ci" in cleanup["run"]


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
    assert "G4 remains NOT_PASSED" in guide
    assert "python -m demo.launcher --host 127.0.0.1 --port 7860" in guide
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
    assert {Requirement(value).name for value in extras["demo"]} == {"fastapi", "uvicorn"}
    assert "gradio" in {Requirement(value).name for value in extras["dev"]}
    assert "matplotlib" not in {Requirement(value).name for value in extras["dev"]}


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
        "matplotlib", "contourpy", "cycler", "fonttools", "kiwisolver", "pyparsing",
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
