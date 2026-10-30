import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from demo.bug_lab.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "docker" / "buglab-entrypoint.sh"
WORKDIR = "workspace with spaces"


def _posix_shell():
    shell = shutil.which("sh")
    if shell:
        return shell
    if os.name == "nt":
        git = shutil.which("git")
        roots = [
            Path(git).parent.parent if git else None,
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Git",
        ]
        for root in filter(None, roots):
            for candidate in (root / "usr" / "bin" / "sh.exe", root / "bin" / "sh.exe"):
                if candidate.is_file():
                    return str(candidate)
    pytest.skip("POSIX sh is unavailable")


def _stub_module(root, capture):
    package = root / "demo" / "bug_lab"
    package.mkdir(parents=True)
    (root / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['BUG_LAB_CAPTURE']).write_text("
        "json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return capture


def _run_entrypoint(root, *arguments, real_cli=False, workdir=WORKDIR):
    capture = root / "entrypoint-args.json"
    if not real_cli:
        _stub_module(root, capture)

    env = dict(os.environ)
    env["BUG_LAB_WORKDIR"] = workdir
    env["BUG_LAB_HOST"] = "127.0.0.1"
    env["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), env.get("PATH", "")))
    if real_cli:
        env["PYTHONPATH"] = os.pathsep.join((str(ROOT), env.get("PYTHONPATH", "")))
    else:
        env["BUG_LAB_CAPTURE"] = str(capture)

    result = subprocess.run(
        [_posix_shell(), str(ENTRYPOINT), *arguments],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    return result, capture


def _workspace_arguments(*arguments, resume=False, workdir=WORKDIR):
    result = [
        "--host", "127.0.0.1",
        "--allow-remote",
        "--workdir", workdir,
    ]
    if resume:
        result.append("--resume")
    return [*result, *arguments]


def test_docker_defaults_to_flag_entrypoint_and_keeps_llm_mode():
    dockerfile = (ROOT / "Dockerfile.buglab").read_text(encoding="utf-8")

    assert "COPY docker/buglab-entrypoint.sh /usr/local/bin/buglab-entrypoint.sh" in dockerfile
    assert 'ENTRYPOINT ["/bin/sh", "/usr/local/bin/buglab-entrypoint.sh"]' in dockerfile
    assert 'CMD ["--agents", "llm"]' in dockerfile


def test_empty_workdir_uses_first_start_arguments_with_spaces(tmp_path):
    (tmp_path / WORKDIR).mkdir()

    result, capture = _run_entrypoint(tmp_path, "--agents", "llm")

    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text(encoding="utf-8")) == _workspace_arguments(
        "--agents", "llm",
    )


def test_existing_marker_selects_resume_before_user_flags(tmp_path):
    workspace = Workspace(tmp_path / WORKDIR)
    workspace.close()

    result, capture = _run_entrypoint(tmp_path, "--agents", "rules", "--step-delay", "0")

    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text(encoding="utf-8")) == _workspace_arguments(
        "--agents", "rules", "--step-delay", "0", resume=True,
    )


@pytest.mark.parametrize(
    "workdir_arguments",
    [
        ("--workdir", "selected workspace"),
        ("--workdir=selected workspace",),
    ],
)
def test_resume_selection_uses_effective_workdir_with_marker(tmp_path, workdir_arguments):
    selected_workdir = "selected workspace"
    selected = tmp_path / selected_workdir
    selected.mkdir()
    (selected / ".bug-lab-workspace.json").write_text("{}", encoding="utf-8")
    arguments = (*workdir_arguments, "--agents", "rules")

    result, capture = _run_entrypoint(
        tmp_path, *arguments, workdir="default workspace",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text(encoding="utf-8")) == _workspace_arguments(
        *arguments, resume=True, workdir=selected_workdir,
    )


@pytest.mark.parametrize(
    "workdir_arguments",
    [
        ("--workdir", "selected workspace"),
        ("--workdir=selected workspace",),
    ],
)
def test_resume_selection_ignores_marker_in_overridden_default_workdir(tmp_path, workdir_arguments):
    default = Workspace(tmp_path / WORKDIR)
    default.close()
    selected_workdir = "selected workspace"
    (tmp_path / selected_workdir).mkdir()
    arguments = (*workdir_arguments, "--agents", "rules")

    result, capture = _run_entrypoint(tmp_path, *arguments)

    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text(encoding="utf-8")) == _workspace_arguments(
        *arguments, workdir=selected_workdir,
    )


def test_corrupt_marker_is_routed_to_cli_resume_and_preserved(tmp_path):
    workspace = Workspace(tmp_path / WORKDIR)
    workspace.close()
    marker = tmp_path / WORKDIR / ".bug-lab-workspace.json"
    marker.write_text("{broken marker", encoding="utf-8")
    before = {
        path.relative_to(marker.parent).as_posix(): path.read_bytes()
        for path in marker.parent.rglob("*")
        if path.is_file()
    }

    result, _ = _run_entrypoint(tmp_path, "--agents", "rules", real_cli=True)

    assert result.returncode != 0
    assert "Bug Lab workspace JSON is malformed" in result.stderr
    assert {
        path.relative_to(marker.parent).as_posix(): path.read_bytes()
        for path in marker.parent.rglob("*")
        if path.is_file()
    } == before


def test_nonempty_workdir_without_marker_fails_without_mutation(tmp_path):
    workspace = tmp_path / WORKDIR
    workspace.mkdir()
    sentinel = workspace / "owner-data.txt"
    sentinel.write_text("preserve this data", encoding="utf-8")

    result, _ = _run_entrypoint(tmp_path, "--agents", "rules", real_cli=True)

    assert result.returncode != 0
    assert "Bug Lab workdir must be new or empty" in result.stderr
    assert [path.name for path in workspace.iterdir()] == ["owner-data.txt"]
    assert sentinel.read_text(encoding="utf-8") == "preserve this data"


def test_explicit_resume_without_marker_still_fails_closed(tmp_path):
    workspace = tmp_path / WORKDIR
    workspace.mkdir()
    sentinel = workspace / "owner-data.txt"
    sentinel.write_text("preserve this data", encoding="utf-8")

    result, _ = _run_entrypoint(
        tmp_path, "--resume", "--agents", "rules", real_cli=True,
    )

    assert result.returncode != 0
    assert "Bug Lab resume requires a valid workspace marker" in result.stderr
    assert [path.name for path in workspace.iterdir()] == ["owner-data.txt"]
    assert sentinel.read_text(encoding="utf-8") == "preserve this data"


def test_explicit_command_override_is_execed_unchanged_even_for_owned_workdir(tmp_path):
    workspace = Workspace(tmp_path / WORKDIR)
    workspace.close()

    result, _ = _run_entrypoint(
        tmp_path,
        "python",
        "-c",
        "import json, sys; print(json.dumps(sys.argv[1:]))",
        "argument with spaces",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["argument with spaces"]


def test_explicit_python_module_command_override_remains_usable(tmp_path):
    result, _ = _run_entrypoint(
        tmp_path, "python", "-m", "demo.bug_lab", "--help", real_cli=True,
    )

    assert result.returncode == 0, result.stderr
    assert "usage: python -m demo.bug_lab" in result.stdout
