from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from scripts.check_submission_links import CURRENT_MARKDOWN_PATHS
from scripts.package_submission import (
    REQUIRED_REVIEW_ASSETS,
    collect_submission_assets,
    evaluate_package_readiness,
)


ROOT = Path(__file__).resolve().parents[1]
PLATFORM_DOCUMENTS = (
    "DEPLOYMENT.md",
    "docs/project/INFRASTRUCTURE_CONTRACT.md",
    "docs/project/LOCAL_PLATFORM_RUNTIME.md",
    "docs/project/PLATFORM_REQUIREMENT_MATRIX.md",
    "docs/project/OPERATIONAL_VERIFIER_CONTRACT.md",
    "docs/project/SOURCE_REVIEW_PACKAGE.md",
)


@pytest.mark.parametrize("path", PLATFORM_DOCUMENTS)
def test_platform_review_document_is_selected_required_and_link_checked(monkeypatch, path):
    def git_files(*_args, **_kwargs):
        return SimpleNamespace(stdout=(path + "\0").encode())

    monkeypatch.setattr("scripts.package_submission.subprocess.run", git_files)
    raw = (ROOT / path).read_bytes()

    assert collect_submission_assets() == {
        path: {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
    }
    assert path in REQUIRED_REVIEW_ASSETS
    assert path in CURRENT_MARKDOWN_PATHS

    other_assets = {
        required: {"sha256": "0" * 64, "size_bytes": 1}
        for required in REQUIRED_REVIEW_ASSETS
        if required != path
    }
    readiness = evaluate_package_readiness(other_assets, [])
    assert readiness["package_ready"] is False
    assert readiness["package_readiness_missing_assets"] == [path]


def test_local_runbook_separates_the_bug_lab_from_the_kubernetes_application():
    runbook = (ROOT / "docs/project/LOCAL_PLATFORM_RUNTIME.md").read_text()

    assert "`python -m demo.bug_lab` on a separate port" in runbook
    assert "The standalone Bug Lab is not mounted" in runbook
    assert "NON_EMPIRICAL_SOFTWARE_FIXTURE" in runbook
    assert "--address=127.0.0.1" in runbook
    assert "ATLASOPS_TEARDOWN_ACK=DELETE_ATLASOPS_LOCAL" in runbook
    assert "after separate authorization" in runbook.lower()


def test_stage_three_cloud_guide_is_historical_not_current_gate_authority():
    guide = (ROOT / "docs/project/STAGE_3_OPERATOR_GUIDE.md").read_text()
    introduction = guide.split("## 2.", 1)[0]

    assert "HISTORICAL / OPTIONAL GKE PORTABILITY" in introduction
    assert "[local platform runbook](LOCAL_PLATFORM_RUNTIME.md)" in introduction
    assert "READY FOR OPERATOR ACTIVATION" not in introduction
    assert "does not establish current G3" in introduction
    assert "100% validated offline" not in introduction


def test_stage_three_cloud_guide_uses_the_selected_private_secret_directory():
    guide = (ROOT / "docs/project/STAGE_3_OPERATOR_GUIDE.md").read_text()

    assert "`$ATLASOPS_SECRET_DIR/argocd-pass.secret`" in guide
    assert "`secrets/argocd-pass.secret`" not in guide


@pytest.mark.parametrize("secret_dir", [None, "", "/owner-private/atlasops demo"])
@pytest.mark.parametrize("python_bin", [None, "selected_python"])
@pytest.mark.parametrize(
    ("document", "section_start"),
    [
        ("docs/project/LOCAL_PLATFORM_RUNTIME.md", "## Prepare the authorized inputs"),
        ("docs/project/STAGE_3_OPERATOR_GUIDE.md", "2. Generate local runtime secret material:"),
    ],
)
def test_runbook_secret_preparation_requires_an_explicit_directory(
    secret_dir, python_bin, document, section_start
):
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    bash = (
        str(git_bash)
        if os.name == "nt" and git_bash.is_file()
        else shutil.which("bash")
    )
    if not bash or os.name == "nt" and "system32" in bash.casefold():
        pytest.skip("Git Bash is required on Windows; do not start WSL")
    runbook = (ROOT / document).read_text()
    section = runbook.split(section_start, 1)[1]
    command = section.split("```bash\n", 1)[1].split("```", 1)[0]
    env = dict(os.environ)
    env.pop("ATLASOPS_SECRET_DIR", None)
    env.pop("PYTHON_BIN", None)
    if secret_dir is not None:
        env["ATLASOPS_SECRET_DIR"] = secret_dir
    if python_bin is not None:
        env["PYTHON_BIN"] = python_bin

    result = subprocess.run(
        [
            bash,
            "--noprofile",
            "--norc",
            "-c",
            "python() { printf 'GENERATOR_INVOKED [%s]\\n' \"$@\"; }\n"
            "selected_python() { printf 'SELECTED_INTERPRETER\\n'; python \"$@\"; }\n"
            + command,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    if secret_dir:
        assert result.returncode == 0, result.stderr
        assert f"GENERATOR_INVOKED [{secret_dir}]" in result.stdout
        assert ("SELECTED_INTERPRETER" in result.stdout) == bool(python_bin)
    else:
        assert result.returncode != 0
        assert "ATLASOPS_SECRET_DIR" in result.stderr
        assert "GENERATOR_INVOKED" not in result.stdout
