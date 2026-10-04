from __future__ import annotations

import hashlib
import json
import sys

import pytest

from bench import inference_runtime as runtime
from scripts import serve_g4_v38_inference_v1 as server_wrapper


def _write_lock(path, packages):
    lines = []
    for name, (version, digest) in packages.items():
        lines.extend((
            f"{name}=={version} \\",
            f"    --hash=sha256:{digest}",
            "    # via fixture",
        ))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _fixture_plan(tmp_path):
    important = {
        "torch": "2.7.1",
        "transformers": "4.57.6",
        "peft": "0.17.1",
        "bitsandbytes": "0.46.1",
        "tokenizers": "0.22.1",
        "fastapi": "0.141.1",
        "h11": "0.16.0",
    }
    base = {
        name: (version, f"{index:064x}")
        for index, (name, version) in enumerate(important.items(), start=1)
    }
    base.update({
        f"fixture-{index:02}": ("1.0.0", f"{index + 100:064x}")
        for index in range(1, 66)
    })
    assert len(base) == 72
    overlay = {
        "h11": base["h11"],
        "click": ("8.4.2", f"{201:064x}"),
        "uvicorn": ("0.52.1", f"{202:064x}"),
    }
    base_path = tmp_path / runtime.BASE_LOCK
    overlay_path = tmp_path / runtime.OVERLAY_LOCK
    _write_lock(base_path, base)
    _write_lock(overlay_path, overlay)
    config = {
        "version": "g4-v38-inference-runtime-v1",
        "base_lock_path": runtime.BASE_LOCK,
        "base_lock_sha256": hashlib.sha256(base_path.read_bytes()).hexdigest(),
        "overlay_lock_path": runtime.OVERLAY_LOCK,
        "overlay_lock_sha256": hashlib.sha256(overlay_path.read_bytes()).hexdigest(),
        "python_version": "3.12.11",
        "added_packages": runtime.EXPECTED_ADDITIONS,
    }
    config_path = tmp_path / runtime.RUNTIME_CONFIG
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    installed = {name: value[0] for name, value in base.items()}
    installed.update(runtime.EXPECTED_ADDITIONS)
    installed["pip"] = "25.0"
    return config_path, overlay_path, installed


def test_qualification_checks_locked_packages_and_reports_extras(tmp_path, monkeypatch):
    config_path, _, installed = _fixture_plan(tmp_path)
    monkeypatch.setattr(runtime.platform, "python_version", lambda: "3.12.11")
    monkeypatch.setattr(runtime, "_installed_versions", lambda: installed)
    monkeypatch.setattr(runtime, "_server_startup_probe", lambda: {"status": "PASS"})

    record = runtime.qualify_runtime(tmp_path)

    assert record["status"] == "QUALIFIED"
    assert record["incident_attempt_reserved"] is False
    assert record["model_and_cuda_readiness"] == "NOT_CHECKED"
    assert record["packages"]["base_package_count"] == 72
    assert record["packages"]["unexpected_installed"] == {"pip": "25.0"}
    assert record["packages"]["locked_artifact_sha256"]["torch"]
    assert config_path.is_file()


def test_repository_plan_matches_versioned_lock_bindings():
    config, expected, base = runtime._runtime_plan(runtime.REPO_ROOT)

    assert config["version"] == "g4-v38-inference-runtime-v1"
    assert len(base) == 72
    assert expected["h11"] == base["h11"]
    assert {name: expected[name]["version"] for name in runtime.EXPECTED_ADDITIONS} == (
        runtime.EXPECTED_ADDITIONS
    )


def test_qualification_rejects_frozen_package_drift(tmp_path, monkeypatch):
    _fixture_plan(tmp_path)
    monkeypatch.setattr(runtime.platform, "python_version", lambda: "3.12.11")
    _, expected, _ = runtime._runtime_plan(tmp_path)
    actual = {name: value["version"] for name, value in expected.items()}
    actual["torch"] = "2.7.2"
    monkeypatch.setattr(runtime, "_installed_versions", lambda: actual)

    record = runtime.qualify_runtime(tmp_path)

    assert record["status"] == "NOT_QUALIFIED"
    assert record["packages"]["version_mismatches"]["torch"] == {
        "expected": "2.7.1", "actual": "2.7.2",
    }


def test_qualification_rejects_python_and_missing_package(tmp_path, monkeypatch):
    _fixture_plan(tmp_path)
    _, expected, _ = runtime._runtime_plan(tmp_path)
    installed = {name: value["version"] for name, value in expected.items()}
    installed.pop("torch")
    monkeypatch.setattr(runtime, "_installed_versions", lambda: installed)
    monkeypatch.setattr(runtime.platform, "python_version", lambda: "3.12.10")

    record = runtime.qualify_runtime(tmp_path)

    assert record["status"] == "NOT_QUALIFIED"
    assert record["failure_reason"] == "Python version does not match the locked runtime"

    monkeypatch.setattr(runtime.platform, "python_version", lambda: "3.12.11")
    record = runtime.qualify_runtime(tmp_path)
    assert record["packages"]["missing"] == ["torch"]


def test_qualification_rejects_overlay_base_hash_drift(tmp_path):
    _, overlay_path, _ = _fixture_plan(tmp_path)
    text = overlay_path.read_text(encoding="utf-8").replace("0.16.0", "0.16.1")
    overlay_path.write_text(text, encoding="utf-8")
    config = json.loads((tmp_path / runtime.RUNTIME_CONFIG).read_text(encoding="utf-8"))
    config["overlay_lock_sha256"] = hashlib.sha256(overlay_path.read_bytes()).hexdigest()
    (tmp_path / runtime.RUNTIME_CONFIG).write_text(json.dumps(config), encoding="utf-8")

    record = runtime.qualify_runtime(tmp_path)

    assert record["status"] == "NOT_QUALIFIED"
    assert "Overlay changes frozen package identity: h11" in record["failure_reason"]


def test_fastapi_server_starts_answers_health_and_joins():
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")

    result = runtime._server_startup_probe()

    assert result["status"] == "PASS"
    assert result["health_status_code"] == 200
    assert result["thread_joined"] is True


def test_server_wrapper_validates_before_import_and_preserves_argv(monkeypatch):
    argv = ["serve", "--checkpoint", "/pinned/adapter", "--base-snapshot", "/pinned/base"]
    monkeypatch.setattr(sys, "argv", argv)
    calls = []
    monkeypatch.setattr(server_wrapper, "validate_runtime", lambda: calls.append("validate"))
    monkeypatch.setattr(
        server_wrapper,
        "_existing_server_main",
        lambda: calls.append("load-server") or (lambda: calls.append(list(sys.argv))),
    )

    server_wrapper.main()

    assert calls == ["validate", "load-server", argv]
    assert sys.argv == argv


def test_server_wrapper_does_not_load_server_if_validation_fails(monkeypatch):
    loaded = []
    monkeypatch.setattr(
        server_wrapper, "validate_runtime",
        lambda: (_ for _ in ()).throw(runtime.RuntimeQualificationError("not qualified")),
    )
    monkeypatch.setattr(server_wrapper, "_existing_server_main", lambda: loaded.append(True))

    with pytest.raises(runtime.RuntimeQualificationError, match="not qualified"):
        server_wrapper.main()
    assert loaded == []


def test_existing_server_fails_closed_before_engine_on_operational_credentials(monkeypatch, tmp_path):
    from scripts import serve_integrated_inference

    engine_called = []
    monkeypatch.setattr(
        serve_integrated_inference, "PairedCompletionEngine",
        lambda **kwargs: engine_called.append(kwargs),
    )
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "kubeconfig"))
    monkeypatch.setattr(sys, "argv", [
        "serve", "--checkpoint", str(tmp_path / "adapter"),
        "--base-snapshot", str(tmp_path / "base"),
        "--base-inventory-sha256", "0" * 64,
        "--inference-key-file", str(tmp_path / "inference-key"),
    ])

    with pytest.raises(RuntimeError, match="must not carry operational credentials"):
        serve_integrated_inference.main()
    assert engine_called == []
