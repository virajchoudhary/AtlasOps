"""Generate a release-readiness gate report for AtlasOps.

The goal is to provide a single command that validates core shipping evidence
before hackathon submission and emits a human-readable markdown report.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import sys

if __package__:
    from .package_submission import declared_gate_statuses
else:
    from package_submission import declared_gate_statuses


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "RELEASE_READINESS.md"


class _ScriptSourceCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            source = dict(attrs).get("src")
            if source:
                self.sources.add(source)


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | FAIL | WARN
    details: str
    critical: bool = True
    category: str = "artifact"


def _exists(path: Path) -> bool:
    return path.exists()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_artifact_presence() -> list[CheckResult]:
    required = [
        ROOT / "docs" / "AMD_FINAL_DELIVERY_SCORECARD_AND_REWARD_SPEC.md",
        ROOT / "docs" / "MI300X_EVIDENCE.md",
        ROOT / "tests" / "test_app_endpoints.py",
        ROOT / "tests" / "test_bench_runner.py",
        ROOT / "tests" / "test_chaos_manifests.py",
    ]
    missing = [str(p.relative_to(ROOT)) for p in required if not _exists(p)]
    if missing:
        return [CheckResult("Required artifacts", "FAIL", f"Missing: {', '.join(missing)}", True)]
    return [
        CheckResult(
            "Required artifacts",
            "PASS",
            "Required documentation and test files are present; benchmark output is optional.",
            True,
        )
    ]


def check_chaos_manifest_inventory() -> list[CheckResult]:
    expected = {
        "single_fault": 8,
        "cascade": 5,
        "multi_fault": 5,
        "named_replays": 10,
    }
    results: list[CheckResult] = []
    for tier, count in expected.items():
        actual = len(list((ROOT / "bench" / "chaos_manifests" / tier).glob("*.yaml")))
        if actual != count:
            results.append(
                CheckResult(
                    f"Chaos manifest count ({tier})",
                    "FAIL",
                    f"Expected {count}, found {actual}.",
                    True,
                )
            )
        else:
            results.append(
                CheckResult(
                    f"Chaos manifest count ({tier})",
                    "PASS",
                    f"Expected {count}, found {actual}.",
                    True,
                )
            )
    return results


def _top_level_assignment(module: ast.Module, name: str) -> tuple[bool, ast.expr | None]:
    assigned = False
    value: ast.expr | None = None
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name for target in statement.targets
            ):
                assigned = True
                value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and statement.target.id == name:
                assigned = True
                value = statement.value
    return assigned, value


def _literal_dict_keys(value: ast.expr | None, name: str) -> tuple[set[str] | None, str | None]:
    if not isinstance(value, ast.Dict):
        return None, f"{name} must be assigned a dictionary literal."

    keys: set[str] = set()
    for key in value.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None, f"{name} must use literal string keys."
        keys.add(key.value)
    return keys, None


def check_runtime_tiers() -> list[CheckResult]:
    runtime_path = ROOT / "config" / "runtime.py"
    runtime_text = _read_text(runtime_path)
    try:
        runtime_module = ast.parse(runtime_text, filename=str(runtime_path))
    except SyntaxError as exc:
        return [
            CheckResult(
                "Difficulty tiers declared",
                "FAIL",
                f"config/runtime.py has invalid Python syntax at line {exc.lineno}: {exc.msg}.",
                True,
            ),
            CheckResult(
                "Tier scenario pool coverage",
                "WARN",
                "Cannot inspect scenario-pool keys because config/runtime.py did not parse.",
                False,
            ),
        ]

    speed_assigned, speed_value = _top_level_assignment(runtime_module, "SPEED_MIDPOINTS")
    if not speed_assigned:
        speed_keys, speed_error = None, "SPEED_MIDPOINTS is not declared."
    else:
        speed_keys, speed_error = _literal_dict_keys(speed_value, "SPEED_MIDPOINTS")

    required_speed_tiers = (
        "warmup",
        "single_fault",
        "cascade",
        "multi_fault",
        "adversarial",
    )
    if speed_error:
        speed_result = CheckResult("Difficulty tiers declared", "FAIL", speed_error, True)
    else:
        missing_speed_tiers = [tier for tier in required_speed_tiers if tier not in speed_keys]
        if missing_speed_tiers:
            speed_result = CheckResult(
                "Difficulty tiers declared",
                "FAIL",
                "SPEED_MIDPOINTS is missing required keys: " + ", ".join(missing_speed_tiers),
                True,
            )
        else:
            speed_result = CheckResult(
                "Difficulty tiers declared",
                "PASS",
                "SPEED_MIDPOINTS declares all five required tier keys.",
                True,
            )

    results = [speed_result]
    pool_assigned, pool_value = _top_level_assignment(runtime_module, "SCENARIOS_BY_TIER")
    if not pool_assigned:
        results.append(
            CheckResult(
                "Tier scenario pool coverage",
                "PASS",
                "No explicit SCENARIOS_BY_TIER mapping is declared; pools may be provided elsewhere.",
                False,
            )
        )
        return results

    pool_keys, pool_error = _literal_dict_keys(pool_value, "SCENARIOS_BY_TIER")
    if pool_error:
        results.append(CheckResult("Tier scenario pool structure", "FAIL", pool_error, True))
        return results

    required_pool_tiers = ("single_fault", "cascade", "multi_fault", "named_replays")
    missing_pool_tiers = [tier for tier in required_pool_tiers if tier not in pool_keys]
    if missing_pool_tiers:
        results.append(
            CheckResult(
                "Tier scenario pool structure",
                "FAIL",
                "SCENARIOS_BY_TIER is missing required keys: " + ", ".join(missing_pool_tiers),
                True,
            )
        )
        return results

    results.append(
        CheckResult(
            "Tier scenario pool structure",
            "PASS",
            "SCENARIOS_BY_TIER declares all required scenario-pool keys.",
            True,
        )
    )

    # Warmup and adversarial pools remain advisory because the runtime may source them elsewhere.
    advisory_missing = [tier for tier in ("warmup", "adversarial") if tier not in pool_keys]
    if advisory_missing:
        results.append(
            CheckResult(
                "Tier scenario pool coverage",
                "WARN",
                "No explicit SCENARIOS_BY_TIER entries for: " + ", ".join(advisory_missing),
                False,
            )
        )
    else:
        results.append(
            CheckResult(
                "Tier scenario pool coverage",
                "PASS",
                "SCENARIOS_BY_TIER explicitly includes warmup and adversarial pools.",
                False,
            )
        )
    return results


def check_ui_runtime_config() -> list[CheckResult]:
    app_text = _read_text(ROOT / "app.py")
    static_text = _read_text(ROOT / "static" / "index.html")
    console_text = _read_text(ROOT / "static" / "console.js")
    try:
        app_tree = ast.parse(app_text)
    except SyntaxError:
        app_tree = None

    has_config_route = False
    if app_tree is not None:
        for node in ast.walk(app_tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "app"
                    and decorator.func.attr == "get"
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                    and decorator.args[0].value == "/config"
                ):
                    has_config_route = True
                    break
            if has_config_route:
                break

    script_collector = _ScriptSourceCollector()
    script_collector.feed(static_text)
    endpoint_map = re.search(
        r"\bconst\s+endpoints\s*=\s*\{(?P<body>[^}]*)\}", console_text, re.DOTALL
    )
    config_mapping_present = (
        endpoint_map is not None
        and re.search(r"\bconfig\s*:\s*['\"]/config['\"]", endpoint_map.group("body")) is not None
    )
    checks = [
        (
            "/config backend route",
            has_config_route,
            "FastAPI GET /config route is declared."
            if has_config_route
            else "FastAPI GET /config route is missing or app.py is invalid.",
        ),
        (
            "Static UI loads console.js",
            "/static/console.js" in script_collector.sources,
            "static/index.html loads /static/console.js."
            if "/static/console.js" in script_collector.sources
            else "static/index.html does not load /static/console.js.",
        ),
        (
            "Console config endpoint mapping",
            config_mapping_present,
            'static/console.js maps the config endpoint to "/config".'
            if config_mapping_present
            else 'static/console.js does not map the config endpoint to "/config".',
        ),
    ]
    out: list[CheckResult] = []
    for name, ok, details in checks:
        out.append(
            CheckResult(
                name,
                "PASS" if ok else "FAIL",
                details,
                True,
            )
        )
    return out


def check_benchmark_output_advisory() -> list[CheckResult]:
    results_dir = ROOT / "bench" / "results"
    run_tables = sorted(results_dir.glob("*/comparison_table.md"))
    legacy_table = results_dir / "comparison_table.md"

    if run_tables:
        details = (
            f"Found {len(run_tables)} run-scoped comparison table(s) under "
            "`bench/results/<run_id>/`. The legacy runner marks its outputs NON_EMPIRICAL; "
            "presence and columns are advisory, not evidence of empirical gate closure."
        )
    elif legacy_table.exists():
        details = (
            "Only the legacy root-level comparison table is present. Treat it as a "
            "historical/advisory artifact; current runner outputs are run-scoped and "
            "NON_EMPIRICAL."
        )
    else:
        details = (
            "No run-scoped comparison table exists under "
            "`bench/results/<run_id>/comparison_table.md`. This optional NON_EMPIRICAL "
            "runner output is advisory; its absence is not a release blocker."
        )

    return [
        CheckResult(
            "Benchmark comparison output (advisory)",
            "WARN",
            details,
            False,
        )
    ]


def check_gate_status_inventory() -> CheckResult:
    status_path = ROOT / "docs" / "project" / "MASTER_PIPELINE_STATUS.md"
    try:
        statuses = declared_gate_statuses(status_path)
    except (OSError, ValueError) as exc:
        return CheckResult(
            "G0-G15 declared gate inventory",
            "FAIL",
            f"Cannot validate the Master Pipeline status inventory: {exc}",
            True,
            "readiness",
        )

    open_gates = [f"{gate}={status}" for gate, status in statuses.items() if status != "PASS"]
    if open_gates:
        return CheckResult(
            "G0-G15 declared gate inventory",
            "FAIL",
            "Open declared gates: "
            + ", ".join(open_gates)
            + ". A declared status is not independent certification evidence.",
            True,
            "readiness",
        )

    return CheckResult(
        "G0-G15 declared gate inventory",
        "PASS",
        "All sixteen declared gate statuses are PASS. This status inventory is not "
        "independent scientific certification evidence.",
        True,
        "readiness",
    )


def check_scientific_certification() -> CheckResult:
    manifest_path = ROOT / "artifacts" / "SUBMISSION_MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return CheckResult(
            "Scientific certification",
            "FAIL",
            f"Cannot validate the submission manifest: {exc}",
            True,
            "readiness",
        )

    if not isinstance(manifest, dict):
        details = "Submission manifest must be a JSON object."
    elif manifest.get("status") == "NOT_CERTIFIED":
        details = (
            "Submission manifest reports NOT_CERTIFIED. Its asset hashes and declared "
            "gate statuses do not independently verify empirical closure."
        )
    elif manifest.get("status") == "CERTIFIED":
        details = (
            "Submission manifest declares CERTIFIED, but this gate does not independently "
            "validate empirical evidence. A self-declared status or metrics payload is not proof."
        )
    else:
        details = (
            "Submission manifest has no supported certification status; certification "
            "cannot be inferred from artifact presence or gate declarations."
        )

    return CheckResult(
        "Scientific certification",
        "FAIL",
        details,
        True,
        "readiness",
    )


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = []
    results.extend(check_artifact_presence())
    results.extend(check_chaos_manifest_inventory())
    results.extend(check_runtime_tiers())
    results.extend(check_ui_runtime_config())
    results.extend(check_benchmark_output_advisory())
    results.append(check_gate_status_inventory())
    results.append(check_scientific_certification())
    return results


def render_report(results: list[CheckResult]) -> str:
    critical_failures = [r for r in results if r.critical and r.status == "FAIL"]
    warnings = [r for r in results if r.status == "WARN"]
    artifact_failures = [
        r for r in results if r.category == "artifact" and r.critical and r.status == "FAIL"
    ]
    overall = "PASS" if not critical_failures else "FAIL"
    lines = [
        "# AtlasOps Release Readiness",
        "",
        f"- Overall: **{overall}**",
        f"- Artifact checks: **{'FAIL' if artifact_failures else 'PASS'}**",
        "- Scientific/submission readiness: **NOT CERTIFIED**",
        f"- Critical failures: **{len(critical_failures)}**",
        f"- Warnings: **{len(warnings)}**",
        "",
        "## Checks",
    ]
    for category, heading in (
        ("artifact", "Artifact validation"),
        ("readiness", "Scientific and submission readiness"),
    ):
        category_results = [r for r in results if r.category == category]
        if not category_results:
            continue
        lines.append(f"### {heading}")
        for r in category_results:
            icon = "PASS" if r.status == "PASS" else ("FAIL" if r.status == "FAIL" else "WARN")
            gate = "critical" if r.critical else "advisory"
            lines.append(f"- [{icon}] `{r.name}` ({gate}) - {r.details}")
    lines.append("")
    if critical_failures:
        lines.append("## Blockers")
        for r in critical_failures:
            lines.append(f"- `{r.name}` - {r.details}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AtlasOps release readiness gate.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Markdown report output path.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero on critical failures.")
    args = parser.parse_args()

    results = run_checks()
    report = render_report(results)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote release readiness report: {output_path}")
    critical_failures = [r for r in results if r.critical and r.status == "FAIL"]
    if args.strict and critical_failures:
        print("Release gate failed (critical checks).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
