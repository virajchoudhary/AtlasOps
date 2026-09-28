"""Importing the coordinator must not create trajectory evidence directories."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("configured_path", [False, True])
def test_coordinator_import_does_not_create_trajectory_directory(
    tmp_path, configured_path,
):
    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repo_root)
    trajectory_dir = (
        tmp_path / "configured-trajectories"
        if configured_path else tmp_path / "data" / "trajectories"
    )
    if configured_path:
        environment["TRAJECTORIES_DIR"] = str(trajectory_dir)
    else:
        environment.pop("TRAJECTORIES_DIR", None)

    process = subprocess.run(
        [sys.executable, "-B", "-c", "import agents.coordinator"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert process.returncode == 0
    assert not trajectory_dir.exists()
