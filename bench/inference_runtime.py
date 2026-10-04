"""Validate the pinned v3.8 inference runtime without loading a model or GPU."""

from __future__ import annotations

from contextlib import closing
import hashlib
import http.client
import importlib
import importlib.metadata
import json
import platform
import re
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CONFIG = "config/g4_v38_inference_runtime_v1.json"
BASE_LOCK = "requirements/sft-pilot-linux-py312.lock"
OVERLAY_LOCK = "requirements/g4-v38-inference-runtime-v1.lock"
EXPECTED_ADDITIONS = {"click": "8.4.2", "uvicorn": "0.52.1"}
SERVER_STARTUP_TIMEOUT_SECONDS = 5.0
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s]+)$")
_HASH = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})")


class RuntimeQualificationError(RuntimeError):
    pass


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _locked_packages(path: Path) -> dict[str, dict]:
    packages: dict[str, dict] = {}
    current: dict | None = None

    def finish() -> None:
        if current is None:
            return
        if not current["hashes"]:
            raise RuntimeQualificationError(f"Lock entry has no SHA-256 hashes: {current['name']}")
        if current["name"] in packages:
            raise RuntimeQualificationError(f"Duplicate lock entry: {current['name']}")
        packages[current["name"]] = {
            "version": current["version"],
            "hashes": sorted(current["hashes"]),
        }

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.endswith("\\"):
            line = line[:-1].rstrip()
        match = _PIN.fullmatch(line)
        if match:
            finish()
            current = {
                "name": _name(match.group(1)),
                "version": match.group(2),
                "hashes": set(),
            }
        elif "--hash=" in line:
            if current is None:
                raise RuntimeQualificationError(f"Unbound hash in lock file: {path.name}")
            hashes = _HASH.findall(line)
            if not hashes:
                raise RuntimeQualificationError(f"Invalid lock hash in {path.name}")
            current["hashes"].update(value.lower() for value in hashes)
    finish()
    return packages


def _runtime_plan(repo_root: Path) -> tuple[dict, dict, dict]:
    root = repo_root.resolve()
    config = json.loads((root / RUNTIME_CONFIG).read_text(encoding="utf-8"))
    if config.get("version") != "g4-v38-inference-runtime-v1":
        raise RuntimeQualificationError("Unexpected inference runtime version")
    if config.get("base_lock_path") != BASE_LOCK or config.get("overlay_lock_path") != OVERLAY_LOCK:
        raise RuntimeQualificationError("Inference runtime lock paths differ from the v1 contract")
    if config.get("added_packages") != EXPECTED_ADDITIONS:
        raise RuntimeQualificationError("Inference runtime additions differ from the v1 contract")
    if config.get("python_version") != "3.12.11":
        raise RuntimeQualificationError("Inference runtime Python pin differs from the v1 contract")

    base_path, overlay_path = root / BASE_LOCK, root / OVERLAY_LOCK
    for path, expected in (
        (base_path, config.get("base_lock_sha256")),
        (overlay_path, config.get("overlay_lock_sha256")),
    ):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeQualificationError(f"Lock SHA-256 mismatch: {path.name}")

    base, overlay = _locked_packages(base_path), _locked_packages(overlay_path)
    if len(base) != 72:
        raise RuntimeQualificationError(f"Frozen SFT lock must contain 72 packages, found {len(base)}")
    for name, item in overlay.items():
        if name in base and item != base[name]:
            raise RuntimeQualificationError(f"Overlay changes frozen package identity: {name}")
    if set(overlay) - set(base) != set(EXPECTED_ADDITIONS):
        raise RuntimeQualificationError("Overlay package closure differs from the v1 contract")
    for name, version in EXPECTED_ADDITIONS.items():
        if overlay[name]["version"] != version:
            raise RuntimeQualificationError(f"Unexpected overlay version: {name}")

    expected = dict(base)
    expected.update({name: overlay[name] for name in EXPECTED_ADDITIONS})
    return config, expected, base


def _installed_versions() -> dict[str, str]:
    installed = {}
    for distribution in importlib.metadata.distributions():
        package_name = distribution.metadata.get("Name")
        if package_name:
            installed[_name(package_name)] = distribution.version
    return installed


def _server_startup_probe() -> dict:
    fastapi = importlib.import_module("fastapi")
    uvicorn = importlib.import_module("uvicorn")
    app = fastapi.FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=listener.getsockname()[1],
        log_level="critical", access_log=False,
    ))
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            server.run(sockets=[listener])
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve, name="inference-runtime-smoke", daemon=True)
    started = time.monotonic()
    deadline = started + SERVER_STARTUP_TIMEOUT_SECONDS
    thread.start()
    try:
        while not server.started:
            if errors:
                raise RuntimeQualificationError(f"Local server failed: {type(errors[0]).__name__}")
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeQualificationError("Local server did not start before its deadline")
            time.sleep(0.02)
        with closing(http.client.HTTPConnection(
            "127.0.0.1", server.config.port, timeout=max(0.1, deadline - time.monotonic())
        )) as client:
            client.request("GET", "/health")
            response = client.getresponse()
            body = json.loads(response.read(1024))
        if (
            response.status != 200
            or body != {"status": "ok"}
            or time.monotonic() > deadline
        ):
            raise RuntimeQualificationError("Local server health response was invalid")
    finally:
        server.should_exit = True
        thread.join(timeout=SERVER_STARTUP_TIMEOUT_SECONDS)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=1)
        listener.close()
        if thread.is_alive():
            raise RuntimeQualificationError("Local server did not stop cleanly")
    return {
        "status": "PASS",
        "host": "127.0.0.1",
        "health_path": "/health",
        "health_status_code": 200,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "thread_joined": True,
        "timeout_seconds": SERVER_STARTUP_TIMEOUT_SECONDS,
    }


def qualify_runtime(repo_root: Path = REPO_ROOT) -> dict:
    started = time.monotonic()
    record = {
        "schema_version": "atlasops-g4-v38-inference-runtime-qualification-v1",
        "qualification_only": True,
        "incident_attempt_reserved": False,
        "operational_tools_executed": False,
        "model_and_cuda_readiness": "NOT_CHECKED",
        "status": "NOT_QUALIFIED",
        "started_at_utc": datetime.now(UTC).isoformat(),
    }
    try:
        config, expected, base = _runtime_plan(Path(repo_root))
        record["lock_sources"] = {
            "base": {"path": BASE_LOCK, "sha256": config["base_lock_sha256"]},
            "overlay": {"path": OVERLAY_LOCK, "sha256": config["overlay_lock_sha256"]},
        }
        actual_python = platform.python_version()
        record["python"] = {"expected": config["python_version"], "actual": actual_python}
        if actual_python != config["python_version"]:
            raise RuntimeQualificationError("Python version does not match the locked runtime")

        installed = _installed_versions()
        missing = sorted(set(expected) - set(installed))
        mismatches = {
            name: {"expected": item["version"], "actual": installed[name]}
            for name, item in expected.items()
            if name in installed and installed[name] != item["version"]
        }
        record["packages"] = {
            "base_package_count": len(base),
            "expected_versions": {name: item["version"] for name, item in sorted(expected.items())},
            "installed_versions": {name: installed[name] for name in sorted(expected) if name in installed},
            "locked_artifact_sha256": {name: item["hashes"] for name, item in sorted(expected.items())},
            "missing": missing,
            "version_mismatches": mismatches,
            "unexpected_installed": {
                name: version for name, version in sorted(installed.items()) if name not in expected
            },
        }
        if missing or mismatches:
            raise RuntimeQualificationError("Installed packages differ from the hash-locked runtime")

        record["server_imports"] = {
            name: installed[name] for name in ("fastapi", "uvicorn")
        }
        record["server_startup"] = _server_startup_probe()
        record["status"] = "QUALIFIED"
    except Exception as exc:
        record["failure_category"] = type(exc).__name__
        record["failure_reason"] = str(exc)
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    record["finished_at_utc"] = datetime.now(UTC).isoformat()
    return record


def validate_runtime(repo_root: Path = REPO_ROOT) -> dict:
    record = qualify_runtime(repo_root)
    if record["status"] != "QUALIFIED":
        raise RuntimeQualificationError(record.get("failure_reason", "Runtime qualification failed"))
    return record
