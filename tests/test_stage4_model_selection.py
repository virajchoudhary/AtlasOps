"""Contract tests for the Stage 4 canonical model-selection path."""

from __future__ import annotations

import pathlib
import subprocess
import os
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run_probe(probe: str, *, model: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if model is None:
        env.pop("ATLASOPS_STAGE4_AGENT_MODEL", None)
    else:
        env["ATLASOPS_STAGE4_AGENT_MODEL"] = model
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_stage4_default_preserves_historical_model():
    expected = "qwen2.5:1.5b"
    completed = _run_probe(
        "import config.runtime as runtime\n"
        "import scripts.run_stage4_golden_incident as runner\n"
        "print(runtime.resolve_stage4_agent_model())\n"
        "print(runtime.DEFAULT_STAGE4_AGENT_MODEL)\n"
        "print(runner.SELECTED_STAGE4_AGENT_MODEL)\n"
        "print(runner.stage4_evidence_metadata()['model'])\n"
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [expected] * 4


def test_explicit_configuration_selects_qualified_3b_model():
    qualified_model = "qwen2.5:3b-instruct"
    completed = _run_probe(
        "import config.runtime as runtime\n"
        "import scripts.run_stage4_golden_incident as runner\n"
        "print(runtime.resolve_stage4_agent_model())\n"
        "print(runner.SELECTED_STAGE4_AGENT_MODEL)\n",
        model=qualified_model,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [qualified_model] * 2


def test_coordinator_request_model_matches_selected_model():
    qualified_model = "qwen2.5:3b-instruct"
    completed = _run_probe(
        "import os\n"
        "import pathlib\n"
        "import config.runtime as runtime\n"
        "import scripts.run_stage4_golden_incident as runner\n"
        "trajectories_dir = pathlib.Path(runner.REPO_ROOT) / 'scratch' / 'stage4-model-test-trajectories' / os.urandom(8).hex()\n"
        "trajectories_dir.mkdir(parents=True)\n"
        "os.environ['TRAJECTORIES_DIR'] = str(trajectories_dir)\n"
        "import agents.coordinator as coordinator\n"
        "isolated_dir = pathlib.Path(os.environ['TRAJECTORIES_DIR']).resolve()\n"
        "default_dir = (pathlib.Path(runner.REPO_ROOT) / 'artifacts' / 'trajectories').resolve()\n"
        "print(os.environ['AGENT_MODEL'])\n"
        "print(runner.SELECTED_STAGE4_AGENT_MODEL)\n"
        "print(coordinator.MODEL_NAME)\n"
        "print(pathlib.Path(coordinator.TRAJECTORIES_DIR).resolve() == isolated_dir)\n"
        "print(isolated_dir != default_dir)\n",
        model=qualified_model,
    )
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert lines[:3] == [qualified_model] * 3
    assert lines[3:] == ["True", "True"]


def test_blank_override_preserves_deterministic_default():
    completed = _run_probe(
        "import scripts.run_stage4_golden_incident as runner\n"
        "print(runner.SELECTED_STAGE4_AGENT_MODEL)\n",
        model=" \t\r\n ",
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "qwen2.5:1.5b"


def test_whitespace_around_explicit_override_is_normalized():
    qualified_model = "qwen2.5:3b-instruct"
    completed = _run_probe(
        "import config.runtime as runtime\n"
        "import scripts.run_stage4_golden_incident as runner\n"
        "print(runtime.resolve_stage4_agent_model())\n"
        "print(runner.SELECTED_STAGE4_AGENT_MODEL)\n",
        model=f"  {qualified_model}  ",
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [qualified_model] * 2


def test_evidence_metadata_matches_selected_execution_model():
    qualified_model = "qwen2.5:3b-instruct"
    completed = _run_probe(
        "import scripts.run_stage4_golden_incident as runner\n"
        "metadata = runner.stage4_evidence_metadata()\n"
        "print(metadata['model'])\n"
        "print(metadata['inference_provider'])\n",
        model=qualified_model,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [qualified_model, "ollama-local"]


def test_runner_wires_selected_model_into_coordinator_contract():
    source = (REPO_ROOT / "scripts" / "run_stage4_golden_incident.py").read_text(
        encoding="utf-8"
    )
    assert 'os.environ["AGENT_MODEL"] = SELECTED_STAGE4_AGENT_MODEL' in source
    assert '"model": SELECTED_STAGE4_AGENT_MODEL' in source


def test_runner_has_one_default_literal_and_no_independent_literals():
    source = (REPO_ROOT / "scripts" / "run_stage4_golden_incident.py").read_text(
        encoding="utf-8"
    )
    assert source.count('qwen2.5:1.5b') == 0
    assert source.count('qwen2.5:3b-instruct') == 0

    runtime_source = (
        REPO_ROOT / "config" / "runtime.py"
    ).read_text(encoding="utf-8")
    assert runtime_source.count('DEFAULT_STAGE4_AGENT_MODEL = "qwen2.5:1.5b"') == 1
    assert runtime_source.count('"qwen2.5:3b-instruct"') == 0


def test_direct_script_entrypoint_bootstraps_repository_import_path():
    script = REPO_ROOT / "scripts" / "run_stage4_golden_incident.py"
    probe = (
        "import importlib.util\n"
        "import pathlib\n"
        "import sys\n"
        f"script = pathlib.Path(r'{script}')\n"
        "spec = importlib.util.spec_from_file_location('stage4_runner_probe', script)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "module.__name__ = 'stage4_runner_probe'\n"
        "spec.loader.exec_module(module)\n"
        "assert str(module.REPO_ROOT) in sys.path\n"
        "print(module.SELECTED_STAGE4_AGENT_MODEL)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT.parent,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0
    assert "No module named 'config'" not in completed.stderr
    assert completed.stdout.strip()
