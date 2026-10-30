from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from tests.test_bug_lab_runtime_lock import MODEL_PACKAGES, _parse_requirements

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements" / "platform-runtime.lock"


def test_every_platform_runtime_dependency_has_a_version_and_distribution_hash() -> None:
    entries = _parse_requirements(LOCK.read_text(encoding="utf-8"))
    assert entries
    locked: dict[str, str] = {}
    for requirement, hashes in entries:
        pins = list(requirement.specifier)
        assert len(pins) == 1 and pins[0].operator == "=="
        assert hashes, f"{requirement.name} has no distribution hashes"
        locked[canonicalize_name(requirement.name)] = pins[0].version

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extra_inputs = (
        (ROOT / "requirements" / "platform-runtime.in").read_text(encoding="utf-8").splitlines()
    )
    inputs = project["project"]["dependencies"] + [
        item for item in extra_inputs if item and not item.startswith("#")
    ]
    for text in inputs:
        requirement = Requirement(text)
        name = canonicalize_name(requirement.name)
        assert name in locked, f"{name} is missing from the runtime lock"
        assert requirement.specifier.contains(locked[name], prereleases=True)

    assert {"httptools", "uvloop", "watchfiles", "websockets"} <= locked.keys()
    assert not (
        locked.keys() & (MODEL_PACKAGES | {"bcrypt", "gradio", "pytest", "pytest-asyncio", "ruff"})
    )


def test_integrated_image_installs_the_hash_locked_runtime_and_local_project() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert (
        "COPY requirements/platform-runtime.lock ./requirements/platform-runtime.lock" in dockerfile
    )
    assert "pip install --no-cache-dir --require-hashes --only-binary=:all:" in dockerfile
    assert "-r requirements/platform-runtime.lock" in dockerfile
    assert "pip install --no-cache-dir --no-deps --no-build-isolation ." in dockerfile
    assert "pip check" in dockerfile
    assert 'pip install --no-cache-dir . "uvicorn[standard]"' not in dockerfile
