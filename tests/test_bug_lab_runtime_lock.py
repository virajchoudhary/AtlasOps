from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "requirements" / "buglab-runtime.in"
LOCK = ROOT / "requirements" / "buglab-runtime.lock"
DIRECT_PINS = {
    "fastapi": "0.141.1",
    "httpx": "0.28.1",
    "uvicorn": "0.52.1",
}
MODEL_PACKAGES = {
    "accelerate",
    "bitsandbytes",
    "datasets",
    "peft",
    "torch",
    "transformers",
    "trl",
    "vllm",
}
HASH = re.compile(r"--hash=([a-z0-9]+):([0-9a-f]+)$")


def _parse_requirements(text: str) -> list[tuple[Requirement, set[str]]]:
    entries: list[tuple[Requirement, set[str]]] = []
    parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        continues = stripped.endswith("\\")
        parts.append(stripped.removesuffix("\\").strip())
        if continues:
            continue

        requirement_text = " ".join(part for part in parts if not part.startswith("--hash="))
        hash_options = [part for part in parts if part.startswith("--hash=")]
        hashes: set[str] = set()
        for option in hash_options:
            match = HASH.fullmatch(option)
            assert match is not None and match.group(1) == "sha256"
            assert len(match.group(2)) == 64
            hashes.add(match.group(2))
        entries.append((Requirement(requirement_text), hashes))
        parts.clear()

    assert not parts, "requirements file ended inside a continuation"
    return entries


def _active_entries(
    entries: list[tuple[Requirement, set[str]]],
) -> list[tuple[Requirement, set[str]]]:
    environment = {**default_environment(), "extra": ""}
    return [
        entry
        for entry in entries
        if entry[0].marker is None or entry[0].marker.evaluate(environment)
    ]


def _make_probe_wheel(path: Path) -> None:
    package = "buglab_lock_probe"
    version = "1.0"
    dist_info = f"{package}-{version}.dist-info"
    files = {
        f"{package}/__init__.py": b"",
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: buglab-lock-probe\n"
            "Version: 1.0\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: bug-lab-runtime-lock-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode(),
    }
    record_rows = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest())
        record_rows.append(
            f"{name},sha256={digest.rstrip(b'=').decode('ascii')},{len(content)}"
        )
    record_rows.append(f"{dist_info}/RECORD,,")

    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr(f"{dist_info}/RECORD", "\n".join(record_rows) + "\n")


def test_input_and_lock_keep_the_minimal_runtime_versions_and_hashes() -> None:
    direct_entries = _parse_requirements(INPUT.read_text(encoding="utf-8"))
    direct = {
        canonicalize_name(requirement.name): str(requirement.specifier)
        for requirement, hashes in direct_entries
    }
    assert direct == {name: f"=={version}" for name, version in DIRECT_PINS.items()}
    assert all(not hashes for _, hashes in direct_entries)

    lock_entries = _parse_requirements(LOCK.read_text(encoding="utf-8"))
    assert lock_entries
    for requirement, hashes in lock_entries:
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1 and specifiers[0].operator == "=="
        assert hashes, f"{requirement.name} is missing distribution hashes"

    active = _active_entries(lock_entries)
    locked_versions = {
        canonicalize_name(requirement.name): str(requirement.specifier).removeprefix("==")
        for requirement, _ in active
    }
    assert {name: locked_versions.get(name) for name in DIRECT_PINS} == DIRECT_PINS
    assert not (set(locked_versions) & MODEL_PACKAGES)


@pytest.mark.skipif(
    os.environ.get("ATLASOPS_REQUIRE_BUGLAB_RUNTIME_LOCK") != "1",
    reason="Installed-lock validation requires the isolated hash-locked runtime job",
)
def test_installed_runtime_dependencies_are_present_in_the_hashed_lock() -> None:
    lock_entries = _active_entries(_parse_requirements(LOCK.read_text(encoding="utf-8")))
    locked = {
        canonicalize_name(requirement.name): requirement
        for requirement, _ in lock_entries
    }
    environment = {**default_environment(), "extra": ""}

    for name, requirement in locked.items():
        installed_version = importlib.metadata.version(name)
        assert requirement.specifier.contains(installed_version, prereleases=True)
        distribution = importlib.metadata.distribution(name)
        for dependency_text in distribution.requires or ():
            dependency = Requirement(dependency_text)
            if dependency.marker is not None and not dependency.marker.evaluate(environment):
                continue
            dependency_name = canonicalize_name(dependency.name)
            assert dependency_name in locked, (
                f"{name} requires {dependency_name}, which is absent from the runtime lock"
            )
            dependency_version = importlib.metadata.version(dependency_name)
            assert dependency.specifier.contains(dependency_version, prereleases=True)


def test_pip_require_hashes_rejects_an_unhashed_local_wheel(tmp_path: Path) -> None:
    wheel = tmp_path / "buglab_lock_probe-1.0-py3-none-any.whl"
    _make_probe_wheel(wheel)
    requirements = tmp_path / "unhashed.txt"
    requirements.write_text(wheel.as_uri() + "\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["PIP_CONFIG_FILE"] = os.devnull

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--dry-run",
            "--no-deps",
            "--no-index",
            "--no-cache-dir",
            "--require-hashes",
            "-r",
            str(requirements),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode != 0
    assert "hash" in output and "--require-hashes" in output
