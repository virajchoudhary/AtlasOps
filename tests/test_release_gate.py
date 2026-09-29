"""Regression tests for the artifact and scientific-readiness release checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import release_gate


GATES = tuple(f"G{index}" for index in range(16))
REQUIRED_SPEED_TIERS = (
    "warmup",
    "single_fault",
    "cascade",
    "multi_fault",
    "adversarial",
)
DECLARED_SPEED_TIERS = (
    "warmup",
    "single_fault",
    "cascade",
    "multi_fault",
    "named_replays",
    "adversarial",
)
REQUIRED_SCENARIO_TIERS = ("single_fault", "cascade", "multi_fault", "named_replays")


def _runtime_config(
    speed_tiers: tuple[str, ...] = DECLARED_SPEED_TIERS,
    scenario_tiers: tuple[str, ...] = REQUIRED_SCENARIO_TIERS,
    *,
    suffix: str = "",
) -> str:
    speed_entries = "\n".join(f'    "{tier}": 100.0,' for tier in speed_tiers)
    scenario_entries = "\n".join(f'    "{tier}": [],' for tier in scenario_tiers)
    return (
        f"SPEED_MIDPOINTS = {{\n{speed_entries}\n}}\n"
        f"SCENARIOS_BY_TIER = {{\n{scenario_entries}\n}}\n"
        f"{suffix}"
    )


def _master_status(statuses: dict[str, str], *, omit: str | None = None) -> str:
    lines = [
        "| Stage | Name | Gate | Target | Current Status | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, gate in enumerate(GATES):
        if gate == omit:
            continue
        status = statuses.get(gate, "PASS")
        lines.append(
            f"| **Stage {index}** | Stage {index} | **{gate}** | Target | **{status}** | Notes |"
        )
    return "\n".join(lines) + "\n"


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _workspace(
    root: Path,
    *,
    statuses: dict[str, str] | None = None,
    omit_gate: str | None = None,
    manifest_status: str = "NOT_CERTIFIED",
    empirical_metrics: object = None,
) -> None:
    statuses = {"G10": "OUT_OF_SCOPE", "G11": "OUT_OF_SCOPE", **(statuses or {})}
    for relative_path in (
        "docs/AMD_FINAL_DELIVERY_SCORECARD_AND_REWARD_SPEC.md",
        "docs/MI300X_EVIDENCE.md",
        "tests/test_app_endpoints.py",
        "tests/test_bench_runner.py",
        "tests/test_chaos_manifests.py",
    ):
        _write(root, relative_path, "fixture\n")

    for tier, count in {
        "single_fault": 8,
        "cascade": 5,
        "multi_fault": 5,
        "named_replays": 10,
    }.items():
        for index in range(count):
            _write(root, f"bench/chaos_manifests/{tier}/{index}.yaml", "fixture: true\n")

    _write(
        root,
        "config/runtime.py",
        _runtime_config(),
    )
    _write(root, "app.py", '@app.get("/config")\nasync def runtime_config():\n    return {}\n')
    _write(root, "static/index.html", '<script defer src="/static/console.js"></script>\n')
    _write(root, "static/console.js", 'const endpoints = { config: "/config" };\n')
    _write(
        root,
        "docs/project/MASTER_PIPELINE_STATUS.md",
        _master_status(statuses, omit=omit_gate),
    )

    manifest = {
        "status": manifest_status,
        "gate_statuses_declared": {
            gate: statuses.get(gate, "PASS") for gate in GATES if gate != omit_gate
        },
        "empirical_metrics": empirical_metrics,
    }
    _write(
        root,
        "artifacts/SUBMISSION_MANIFEST.json",
        json.dumps(manifest, indent=2) + "\n",
    )


def test_runtime_tiers_inspects_dictionary_keys_without_executing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _write(
        root,
        "config/runtime.py",
        _runtime_config(suffix='raise RuntimeError("runtime config must not execute")\n'),
    )
    monkeypatch.setattr(release_gate, "ROOT", root)

    checks = release_gate.check_runtime_tiers()
    by_name = {check.name: check for check in checks}

    assert by_name["Difficulty tiers declared"].status == "PASS"
    assert by_name["Tier scenario pool structure"].status == "PASS"
    assert by_name["Tier scenario pool coverage"].status == "WARN"
    assert not by_name["Tier scenario pool coverage"].critical
    assert "warmup, adversarial" in by_name["Tier scenario pool coverage"].details


@pytest.mark.parametrize(
    ("runtime_source", "check_name", "expected_detail"),
    [
        (
            '"warmup" "single_fault" "cascade" "multi_fault" "adversarial"\n',
            "Difficulty tiers declared",
            "SPEED_MIDPOINTS is not declared",
        ),
        (
            "SPEED_MIDPOINTS = {\n",
            "Difficulty tiers declared",
            "invalid Python syntax",
        ),
        (
            "SPEED_MIDPOINTS = []\n",
            "Difficulty tiers declared",
            "SPEED_MIDPOINTS must be assigned a dictionary literal",
        ),
        (
            _runtime_config(speed_tiers=REQUIRED_SPEED_TIERS[:-1]),
            "Difficulty tiers declared",
            "SPEED_MIDPOINTS is missing required keys: adversarial",
        ),
        (
            _runtime_config() + "SCENARIOS_BY_TIER = []\n",
            "Tier scenario pool structure",
            "SCENARIOS_BY_TIER must be assigned a dictionary literal",
        ),
        (
            _runtime_config(scenario_tiers=("single_fault",)),
            "Tier scenario pool structure",
            "SCENARIOS_BY_TIER is missing required keys: cascade, multi_fault, named_replays",
        ),
    ],
)
def test_runtime_tier_check_rejects_malformed_or_incomplete_declarations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_source: str,
    check_name: str,
    expected_detail: str,
) -> None:
    root = tmp_path / "workspace"
    _write(root, "config/runtime.py", runtime_source)
    monkeypatch.setattr(release_gate, "ROOT", root)

    checks = release_gate.check_runtime_tiers()
    failed_check = next(check for check in checks if check.name == check_name)

    assert failed_check.status == "FAIL"
    assert failed_check.critical
    assert expected_detail in failed_check.details


def test_absent_scenario_pool_mapping_remains_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _write(root, "config/runtime.py", _runtime_config().split("SCENARIOS_BY_TIER")[0])
    monkeypatch.setattr(release_gate, "ROOT", root)

    checks = release_gate.check_runtime_tiers()
    coverage = next(check for check in checks if check.name == "Tier scenario pool coverage")

    assert coverage.status == "PASS"
    assert not coverage.critical
    assert "may be provided elsewhere" in coverage.details


def _run_strict(
    root: Path,
    output_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, str]:
    monkeypatch.setattr(release_gate, "ROOT", root)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_gate.py",
            "--strict",
            "--output",
            str(output_path),
        ],
    )
    exit_code = release_gate.main()
    return exit_code, output_path.read_text(encoding="utf-8")


def test_not_certified_manifest_fails_strict_with_all_gate_statuses_passing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _workspace(root, statuses={"G10": "OUT_OF_SCOPE", "G11": "OUT_OF_SCOPE"})

    exit_code, report = _run_strict(root, tmp_path / "release.md", monkeypatch)

    assert exit_code == 1
    assert "- Overall: **FAIL**" in report
    assert "- Artifact checks: **PASS**" in report
    assert "Required documentation and test files are present; benchmark output is optional." in report
    assert "- Scientific/submission readiness: **NOT CERTIFIED**" in report
    assert "[PASS] `G0-G15 declared gate inventory`" in report
    assert "[FAIL] `Scientific certification`" in report
    assert "NOT_CERTIFIED" in report


def test_out_of_scope_gates_are_excluded_without_counting_as_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _workspace(root, statuses={"G10": "OUT_OF_SCOPE", "G11": "OUT_OF_SCOPE"})
    monkeypatch.setattr(release_gate, "ROOT", root)

    result = release_gate.check_gate_status_inventory()

    assert result.status == "PASS"
    assert "fourteen required" in result.details
    assert "not passed" in result.details


def test_scope_exclusion_does_not_hide_an_open_required_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _workspace(root, statuses={
        "G4": "NOT_PASSED",
        "G10": "OUT_OF_SCOPE",
        "G11": "OUT_OF_SCOPE",
    })
    monkeypatch.setattr(release_gate, "ROOT", root)

    result = release_gate.check_gate_status_inventory()

    assert result.status == "FAIL"
    assert "G4=NOT_PASSED" in result.details
    assert "G10/G11 are OUT_OF_SCOPE" in result.details


def test_out_of_scope_cannot_be_assigned_to_another_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _workspace(root, statuses={
        "G9": "OUT_OF_SCOPE",
        "G10": "OUT_OF_SCOPE",
        "G11": "OUT_OF_SCOPE",
    })
    monkeypatch.setattr(release_gate, "ROOT", root)

    result = release_gate.check_gate_status_inventory()

    assert result.status == "FAIL"
    assert "exactly G10/G11" in result.details


def test_strict_fails_for_open_declared_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _workspace(root, statuses={"G4": "NOT_PASSED"}, manifest_status="CERTIFIED")

    exit_code, report = _run_strict(root, tmp_path / "release.md", monkeypatch)

    assert exit_code == 1
    assert "[FAIL] `G0-G15 declared gate inventory`" in report
    assert "G4=NOT_PASSED" in report
    assert "self-declared" in report


@pytest.mark.parametrize(
    ("statuses", "omit_gate", "expected_detail"),
    [
        ({}, "G4", "exactly G0 through G15"),
        ({"G4": "UNKNOWN"}, None, "Invalid gate status row"),
    ],
)
def test_strict_fails_for_missing_or_invalid_gate_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    statuses: dict[str, str],
    omit_gate: str | None,
    expected_detail: str,
) -> None:
    root = tmp_path / "workspace"
    _workspace(root, statuses=statuses, omit_gate=omit_gate)

    exit_code, report = _run_strict(root, tmp_path / "release.md", monkeypatch)

    assert exit_code == 1
    assert "[FAIL] `G0-G15 declared gate inventory`" in report
    assert expected_detail in report


def test_fully_declared_pass_inventory_does_not_trust_certified_manifest_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _workspace(
        root,
        manifest_status="CERTIFIED",
        empirical_metrics={"reported_resolution_rate": 1.0},
    )

    exit_code, report = _run_strict(root, tmp_path / "release.md", monkeypatch)

    assert exit_code == 1
    assert "[PASS] `G0-G15 declared gate inventory`" in report
    assert "[FAIL] `Scientific certification`" in report
    assert "self-declared" in report
    assert "- Scientific/submission readiness: **NOT CERTIFIED**" in report


def test_ui_config_wiring_checks_route_entrypoint_and_endpoint_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _workspace(root)
    monkeypatch.setattr(release_gate, "ROOT", root)

    checks = release_gate.check_ui_runtime_config()

    assert {check.name: check.status for check in checks} == {
        "/config backend route": "PASS",
        "Static UI loads console.js": "PASS",
        "Console config endpoint mapping": "PASS",
    }


@pytest.mark.parametrize(
    ("relative_path", "contents", "failed_check"),
    [
        (
            "app.py",
            '@app.get("/health")\nasync def health():\n    return {}\n',
            "/config backend route",
        ),
        ("static/index.html", "<html></html>\n", "Static UI loads console.js"),
        (
            "static/console.js",
            'const endpoints = { health: "/health" };\n',
            "Console config endpoint mapping",
        ),
    ],
)
def test_ui_config_wiring_fails_when_a_real_link_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
    contents: str,
    failed_check: str,
) -> None:
    root = tmp_path / "workspace"
    _workspace(root)
    _write(root, relative_path, contents)
    monkeypatch.setattr(release_gate, "ROOT", root)

    checks = release_gate.check_ui_runtime_config()

    assert {check.name: check.status for check in checks}[failed_check] == "FAIL"


def test_benchmark_comparison_output_is_advisory_at_supported_run_scoped_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    _workspace(root)
    monkeypatch.setattr(release_gate, "ROOT", root)

    without_output = release_gate.check_benchmark_output_advisory()[0]

    assert without_output.status == "WARN"
    assert not without_output.critical
    assert "bench/results/<run_id>/comparison_table.md" in without_output.details

    _write(root, "bench/results/comparison_table.md", "# legacy table\n")
    legacy_output = release_gate.check_benchmark_output_advisory()[0]

    assert legacy_output.status == "WARN"
    assert not legacy_output.critical
    assert "historical/advisory artifact" in legacy_output.details

    _write(root, "bench/results/run-001/comparison_table.md", "# NON_EMPIRICAL fixture\n")
    with_output = release_gate.check_benchmark_output_advisory()[0]

    assert with_output.status == "WARN"
    assert not with_output.critical
    assert "run-scoped" in with_output.details
    assert "not evidence of empirical gate closure" in with_output.details
