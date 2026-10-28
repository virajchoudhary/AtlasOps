"""Contracts for keeping the Bug Lab workspace independently importable and safe."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from demo.bug_lab.workspace import EDITABLE_FILES, Workspace


def _make_directory_redirect(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", os.fspath(link), os.fspath(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        link.symlink_to(target, target_is_directory=True)
    assert link.resolve(strict=True) == target.resolve(strict=True)


def test_workspace_import_does_not_require_training_or_config(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    workdir = tmp_path / "standalone-lab"
    script = """
import importlib.abc
import sys
from pathlib import Path

class DenyTrainingAndConfig(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0] in {"training", "config"}:
            raise AssertionError(f"forbidden import: {fullname}")
        return None

sys.meta_path.insert(0, DenyTrainingAndConfig())
from demo.bug_lab.workspace import Workspace
from demo.bug_lab.server import LabConfig, create_app

assert Workspace.__name__ == "Workspace"
application = create_app(LabConfig(workdir=Path(sys.argv[1]), agents="rules"))
assert application.title == "AtlasOps Bug Lab"
assert not any(
    name.partition(".")[0] in {"training", "config"}
    for name in sys.modules
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, os.fspath(workdir)],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("redirected_component", ["workdir", "ancestor"])
def test_workspace_rejects_actual_directory_redirect(
    tmp_path, redirected_component
):
    target = tmp_path / "redirect-target"
    link = tmp_path / "redirect"
    _make_directory_redirect(link, target)
    root = link if redirected_component == "workdir" else link / "workdir"

    with pytest.raises(ValueError, match="must not traverse"):
        Workspace(root)

    assert list(target.iterdir()) == []


def test_workspace_reset_rejects_redirected_site(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for name in EDITABLE_FILES:
        (source / name).write_text("{}", encoding="utf-8")
    workspace = Workspace(tmp_path / "workdir", source=source)

    outside = tmp_path / "outside"
    sentinel = outside / "sentinel.txt"
    outside.mkdir()
    sentinel.write_text("untouched", encoding="utf-8")
    for name in EDITABLE_FILES:
        (workspace.site / name).unlink()
    workspace.site.rmdir()
    _make_directory_redirect(workspace.site, outside)

    with pytest.raises(ValueError, match="reset must stay inside"):
        workspace.reset()

    assert sentinel.read_text(encoding="utf-8") == "untouched"


def test_workspace_fails_closed_when_path_inspection_raises(tmp_path, monkeypatch):
    root = tmp_path / "uninspectable"
    original_lstat = Path.lstat

    def denied_lstat(path, *args, **kwargs):
        if path == root:
            raise PermissionError("test path inspection denial")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", denied_lstat)
    with pytest.raises(ValueError, match="must not traverse"):
        Workspace(root)
    assert not root.exists()
