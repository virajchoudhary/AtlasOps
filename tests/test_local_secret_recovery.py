"""Exercise generated local secret recovery with command doubles only."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_runtime_secrets.py"
WRAPPER = r"""
exec 3>&2
python() {
  printf 'python' >&3
  printf ' [%s]' "$@" >&3
  printf '\n' >&3
  return "$MOCK_INSPECTION_STATUS"
}
python3() {
  printf 'python3' >&3
  printf ' [%s]' "$@" >&3
  printf '\n' >&3
  return "$MOCK_INSPECTION_STATUS"
}
kubectl() {
  printf 'kubectl' >&3
  printf ' [%s]' "$@" >&3
  printf '\n' >&3
  if [[ " $* " == *" apply -f - "* ]]; then
    cat >/dev/null
  fi
  if [[ "$*" == *" create namespace "* ]]; then
    printf 'apiVersion: v1\nkind: Namespace\n'
  elif [[ "$*" == *" create secret generic "* ]]; then
    printf 'apiVersion: v1\nkind: Secret\n'
  fi
}
command() {
  if [[ "$1" == "-v" && ( "$2" == "python" || "$2" == "python3" ) ]]; then
    [[ "$2" == "$MOCK_PYTHON_COMMAND" ]]
    return
  fi
  builtin command "$@"
}
helper="$1"
shift
source "$helper" "$@"
"""


def _bash() -> str:
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = (
        str(git_bash)
        if os.name == "nt" and git_bash.is_file()
        else shutil.which("bash")
    )
    if not bash or os.name == "nt" and "system32" in bash.casefold():
        pytest.skip("An owned Git Bash installation is required; do not start WSL")
    return bash


def _stage_source_tree(source_root: Path) -> tuple[Path, Path]:
    scripts = source_root / "scripts"
    scripts.mkdir(parents=True)
    generator = scripts / "generate_runtime_secrets.py"
    verifier = scripts / "verify_platform_infrastructure.py"
    shutil.copyfile(GENERATOR, generator)
    verifier.write_text("# fake verifier; the test double owns this call\n", encoding="utf-8")
    return generator, verifier


def _run_generator(
    source_root: Path,
    output_dir: str,
    *,
    argocd_pass_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    generator = source_root / "scripts" / "generate_runtime_secrets.py"
    command = [
        sys.executable,
        "-B",
        str(generator),
        "--output-dir",
        output_dir,
    ]
    if argocd_pass_file is not None:
        command.extend(["--argocd-pass-file", str(argocd_pass_file)])
    return subprocess.run(
        command,
        cwd=source_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def _generate_recovery_script(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "AtlasOps source with spaces"
    _generator, verifier = _stage_source_tree(source_root)
    output_dir = tmp_path / "secret material with spaces"
    result = _run_generator(source_root, str(output_dir))
    assert result.returncode == 0, result.stderr
    return output_dir / "apply_secrets.sh", verifier


def _run_recovery(
    helper: Path,
    arguments: list[str],
    *,
    cluster_name: str = "atlasops-local",
    inspection_status: int = 0,
    python_command: str = "python3",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _bash(),
            "--noprofile",
            "--norc",
            "-c",
            WRAPPER,
            "fixture",
            helper.as_posix(),
            *arguments,
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "ATLASOPS_KIND_CLUSTER_NAME": cluster_name,
            "MOCK_INSPECTION_STATUS": str(inspection_status),
            "MOCK_PYTHON_COMMAND": python_command,
            "PYTHON_BIN": "",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_kubectl_apply_double_drains_large_stdin(tmp_path: Path) -> None:
    helper = tmp_path / "large apply producer.sh"
    helper.write_text(
        "set -euo pipefail\n"
        "printf '%*s' 131072 '' | "
        "kubectl --context=kind-atlasops-local apply -f -\n",
        encoding="utf-8",
    )

    result = _run_recovery(helper, [])

    assert result.returncode == 0, result.stderr
    assert result.stderr.splitlines() == [
        "kubectl [--context=kind-atlasops-local] [apply] [-f] [-]"
    ]


def test_recovery_requires_an_explicit_context_before_any_command(tmp_path: Path) -> None:
    helper, _verifier = _generate_recovery_script(tmp_path)

    result = _run_recovery(helper, [])

    assert result.returncode != 0
    assert "Explicit --context" in result.stderr
    assert "kubectl [" not in result.stderr
    assert "python " not in result.stderr
    assert "python3 " not in result.stderr


def test_recovery_rejects_a_context_outside_the_selected_kind_cluster(
    tmp_path: Path,
) -> None:
    helper, _verifier = _generate_recovery_script(tmp_path)

    result = _run_recovery(helper, ["--context", "kind-atlasops-unrelated"])

    assert result.returncode != 0
    assert "kind-atlasops-local" in result.stderr
    assert "kubectl [" not in result.stderr
    assert "python " not in result.stderr
    assert "python3 " not in result.stderr


def test_recovery_stops_when_kind_ownership_cannot_be_verified(
    tmp_path: Path,
) -> None:
    helper, _verifier = _generate_recovery_script(tmp_path)

    result = _run_recovery(
        helper,
        ["--context", "kind-atlasops-local"],
        inspection_status=2,
    )

    assert result.returncode != 0
    assert "Could not safely verify selected Kind cluster" in result.stderr
    assert "python3 [" in result.stderr
    assert "[inspect-kind-cluster]" in result.stderr
    assert "kubectl [" not in result.stderr


@pytest.mark.parametrize(
    ("python_command", "expected_python"),
    [("python3", "python3"), ("python", "python")],
)
def test_owner_checked_recovery_uses_selected_python_and_local_secret_scopes(
    tmp_path: Path,
    python_command: str,
    expected_python: str,
) -> None:
    helper, verifier = _generate_recovery_script(tmp_path)
    cluster_name = "atlasops-fixture"

    result = _run_recovery(
        helper,
        ["--context", f"kind-{cluster_name}"],
        cluster_name=cluster_name,
        python_command=python_command,
    )

    assert result.returncode == 0, result.stderr
    assert "infra/local/setup_local.sh --apply" in helper.read_text(encoding="utf-8")
    commands = [
        line
        for line in result.stderr.splitlines()
        if line.startswith(("python ", "python3 ", "kubectl "))
    ]
    inspect = next(line for line in commands if "[inspect-kind-cluster]" in line)
    assert inspect.startswith(f"{expected_python} ")
    assert f"[{verifier}]" in inspect
    assert f"[{cluster_name}]" in inspect
    assert f"[kind-{cluster_name}]" in inspect
    kubectl_commands = [line for line in commands if line.startswith("kubectl ")]
    assert kubectl_commands
    assert commands.index(inspect) < commands.index(kubectl_commands[0])
    assert all(f"[--context=kind-{cluster_name}]" in line for line in kubectl_commands)

    coordinator = next(
        line for line in kubectl_commands if "[atlasops-coordinator-secrets]" in line
    )
    webhook = next(
        line
        for line in kubectl_commands
        if "[atlasops-alertmanager-webhook]" in line
    )
    grafana = next(
        line for line in kubectl_commands if "[atlasops-grafana-admin]" in line
    )
    assert "[--namespace=default]" in coordinator
    assert "[--namespace=monitoring]" in webhook
    assert "[--namespace=monitoring]" in grafana
    assert (
        "secret material with spaces/atlasops-api-key.secret]"
        in coordinator
    )
    assert "--from-file=llm-api-key=" not in coordinator


def test_recovery_restores_optional_model_key_without_exposing_its_value(
    tmp_path: Path,
) -> None:
    helper, verifier = _generate_recovery_script(tmp_path)
    model_key = tmp_path / "supplied model credential.secret"
    credential = "fixture-model-key-not-a-real-credential"
    model_key.write_text(credential, encoding="utf-8")
    generated = subprocess.run(
        [
            sys.executable,
            "-B",
            str(verifier.parent / "generate_runtime_secrets.py"),
            "--output-dir",
            str(helper.parent),
            "--llm-api-key-file",
            str(model_key),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr

    result = _run_recovery(helper, ["--context", "kind-atlasops-local"])

    assert result.returncode == 0, result.stderr
    coordinator = next(
        line
        for line in result.stderr.splitlines()
        if "[atlasops-coordinator-secrets]" in line
    )
    assert "--from-file=llm-api-key=" in coordinator
    assert "secret material with spaces/llm-api-key.secret]" in coordinator
    assert credential not in generated.stdout + generated.stderr + result.stdout + result.stderr


def test_recovery_rejects_an_empty_optional_model_key_before_any_mutation(
    tmp_path: Path,
) -> None:
    helper, _verifier = _generate_recovery_script(tmp_path)
    (helper.parent / "llm-api-key.secret").write_text("", encoding="utf-8")

    result = _run_recovery(helper, ["--context", "kind-atlasops-local"])

    assert result.returncode != 0
    assert "Missing or empty optional model key" in result.stderr
    assert "kubectl [" not in result.stderr


@pytest.mark.parametrize("invalid_target", ["empty", "relative", "repository-local"])
def test_secret_generator_rejects_unsafe_output_before_writes_or_password_checks(
    tmp_path: Path,
    invalid_target: str,
) -> None:
    source_root = tmp_path / "AtlasOps source with spaces"
    _stage_source_tree(source_root)
    if invalid_target == "empty":
        output_dir = ""
        audit_path = source_root / "atlasops-audit-secret.secret"
    elif invalid_target == "relative":
        output_dir = "relative secret material"
        audit_path = source_root / output_dir / "atlasops-audit-secret.secret"
    else:
        target_dir = source_root / "repo-local secret material"
        output_dir = str(target_dir)
        audit_path = target_dir / "atlasops-audit-secret.secret"

    result = _run_generator(
        source_root,
        output_dir,
        argocd_pass_file=tmp_path / "missing credential input",
    )

    assert result.returncode != 0
    assert "absolute output directory outside the checkout" in result.stderr
    assert "Specified Argo password file not found" not in result.stderr
    assert not audit_path.exists()


def test_secret_generator_rejects_symlink_resolved_repository_local_output(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "AtlasOps source with spaces"
    _stage_source_tree(source_root)
    repo_alias = tmp_path / "repository alias"
    try:
        repo_alias.symlink_to(source_root, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    output_dir = repo_alias / "linked secret material"

    result = _run_generator(
        source_root,
        str(output_dir),
        argocd_pass_file=tmp_path / "missing credential input",
    )

    assert result.returncode != 0
    assert "absolute output directory outside the checkout" in result.stderr
    assert "Specified Argo password file not found" not in result.stderr
    assert not (source_root / "linked secret material").exists()


@pytest.mark.parametrize("checkout_layout", ["parent", "neighbor"])
def test_secret_generator_rejects_another_checkout_before_writes(
    tmp_path: Path,
    checkout_layout: str,
) -> None:
    checkout = tmp_path / "existing checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("fixture Git marker", encoding="utf-8")
    source_root = (
        checkout / "nested AtlasOps checkout"
        if checkout_layout == "parent"
        else tmp_path / "AtlasOps source"
    )
    _stage_source_tree(source_root)
    output_dir = checkout / "secret material"

    result = _run_generator(
        source_root,
        str(output_dir),
        argocd_pass_file=tmp_path / "missing credential input",
    )

    assert result.returncode != 0
    assert "absolute output directory outside the checkout" in result.stderr
    assert "Specified Argo password file not found" not in result.stderr
    assert not output_dir.exists()


def test_secret_generator_accepts_an_absolute_external_output_directory(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "AtlasOps source with spaces"
    _stage_source_tree(source_root)
    output_dir = tmp_path / "external secret material"

    result = _run_generator(source_root, str(output_dir))

    assert result.returncode == 0, result.stderr
    assert (output_dir / "atlasops-audit-secret.secret").is_file()
    assert (output_dir / "apply_secrets.sh").is_file()
    assert not (source_root / "atlasops-audit-secret.secret").exists()
