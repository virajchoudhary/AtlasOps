"""Exercise local teardown with command doubles, never an actual cluster."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infra" / "local" / "teardown_local.sh"
WRAPPER = r"""
exec 3>&2
kind() {
  printf 'kind' >&3
  printf ' [%s]' "$@" >&3
  printf '\n' >&3
  if [[ "$1" == "get" ]]; then printf '%s\n' "$MOCK_CLUSTER"; fi
}
inspection() {
  printf 'inspection' >&3
  printf ' [%s]' "$@" >&3
  printf '\n' >&3
  return "$MOCK_INSPECTION_STATUS"
}
python() { inspection python "$@"; }
python3() { inspection python3 "$@"; }
command() {
  if [[ "$1" == "-v" && ( "$2" == "python" || "$2" == "python3" ) ]]; then
    [[ "$2" == "$MOCK_PYTHON_COMMAND" ]]
    return
  fi
  builtin command "$@"
}
source "$1" "${@:2}"
"""


def run_teardown(
    *, cluster: str, acknowledgement: str, inspection_status: int = 0,
    python_command: str = "python", mode: str = "--apply",
):
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
    if not bash or os.name == "nt" and "system32" in bash.casefold():
        pytest.skip("An owned Git Bash installation is required; do not start WSL")
    return subprocess.run(
        [bash, "--noprofile", "--norc", "-c", WRAPPER, "fixture", SCRIPT.as_posix(), mode],
        cwd=ROOT,
        env={
            **os.environ,
            "ATLASOPS_KIND_CLUSTER_NAME": cluster,
            "ATLASOPS_TEARDOWN_ACK": acknowledgement,
            "MOCK_CLUSTER": cluster,
            "MOCK_INSPECTION_STATUS": str(inspection_status),
            "MOCK_PYTHON_COMMAND": python_command,
            "PYTHON_BIN": "",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_teardown_targets_the_same_selected_cluster_as_setup():
    result = run_teardown(
        cluster="atlasops-fixture", acknowledgement="DELETE_ATLASOPS_LOCAL",
    )
    assert result.returncode == 0, result.stderr
    assert "kind [delete] [cluster] [--name] [atlasops-fixture]" in result.stderr
    assert "[--cluster-name] [atlasops-fixture]" in result.stderr
    assert "[--kube-context] [kind-atlasops-fixture]" in result.stderr
    assert result.stderr.index("inspection") < result.stderr.index("kind [delete]")


@pytest.mark.parametrize("acknowledgement", ["", "wrong"])
def test_teardown_refuses_deletion_without_its_explicit_acknowledgement(acknowledgement):
    result = run_teardown(cluster="atlasops-local", acknowledgement=acknowledgement)
    assert result.returncode != 0
    assert "ATLASOPS_TEARDOWN_ACK" in result.stderr
    assert "kind [delete]" not in result.stderr


def test_teardown_rejects_unverified_cluster_identity_before_deletion():
    result = run_teardown(
        cluster="atlasops-local", acknowledgement="DELETE_ATLASOPS_LOCAL",
        inspection_status=2,
    )
    assert result.returncode != 0
    assert "Could not safely inspect Kind cluster" in result.stderr
    assert "kind [delete]" not in result.stderr


def test_teardown_is_idempotent_when_the_selected_cluster_is_absent():
    result = run_teardown(
        cluster="atlasops-fixture", acknowledgement="DELETE_ATLASOPS_LOCAL",
        inspection_status=3,
    )
    assert result.returncode == 0
    assert "does not exist" in result.stdout
    assert "kind [delete]" not in result.stderr


@pytest.mark.parametrize("mode", ["--check", "--apply"])
def test_teardown_supports_python3_only_hosts(mode):
    result = run_teardown(
        cluster="atlasops-fixture", acknowledgement="DELETE_ATLASOPS_LOCAL",
        python_command="python3", mode=mode,
    )
    assert result.returncode == 0, result.stderr
    assert "inspection [python3] [-B]" in result.stderr
    if mode == "--check":
        assert "kind [delete]" not in result.stderr


def test_teardown_uses_python_when_python3_is_unavailable():
    result = run_teardown(
        cluster="atlasops-fixture", acknowledgement="DELETE_ATLASOPS_LOCAL",
    )
    assert result.returncode == 0, result.stderr
    assert "inspection [python] [-B]" in result.stderr


def test_teardown_without_either_python_fails_before_inspection_or_deletion():
    result = run_teardown(
        cluster="atlasops-fixture", acknowledgement="DELETE_ATLASOPS_LOCAL",
        python_command="none",
    )
    assert result.returncode != 0
    assert "inspection" not in result.stderr
    assert "kind [delete]" not in result.stderr


def test_secret_preparation_points_to_the_local_not_cloud_bootstrap(
    tmp_path, monkeypatch, capsys,
):
    from scripts.generate_runtime_secrets import main

    monkeypatch.setattr(
        sys, "argv", ["generate_runtime_secrets.py", "--output-dir", str(tmp_path)],
    )
    main()
    assert "infra/local/setup_local.sh --apply" in capsys.readouterr().out
    recovery = (tmp_path / "apply_secrets.sh").read_text(encoding="utf-8")
    assert "infra/local/setup_local.sh --apply" in recovery
