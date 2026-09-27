"""Guard current README claims against registry and gate-status drift."""

from pathlib import Path

from agents.tool_policy import AGENT_EXPOSED_TOOLS
from agents.tools import REGISTERED_TOOLS
from scripts.package_submission import declared_gate_statuses


def test_readme_tool_counts_match_runtime_policy():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert f"**{len(REGISTERED_TOOLS)} SRE tool wrappers**" in readme
    assert f"**{len(AGENT_EXPOSED_TOOLS)}** to autonomous agents" in readme
    assert "chaos_list_experiments" in readme


def test_open_gates_are_not_presented_as_certified_results():
    statuses = declared_gate_statuses()
    assert any(status != "PASS" for status in statuses.values())

    readme = Path("README.md").read_text(encoding="utf-8")
    audit = Path("docs/project/UPSTREAM_ALIGNMENT_AUDIT_REPORT.md").read_text(
        encoding="utf-8"
    )
    assert "Current continuation status: NOT_CERTIFIED" in readme
    assert "short_description: Evidence-led multi-agent SRE research demo (not certified)" in readme
    assert "responding to real GKE incidents" not in readme
    assert "certified across all" not in readme
    assert "| **Full Pipeline (GAI + RS + RL)** |" not in readme
    assert "default benchmark requests" not in readme
    assert "requested by a default benchmark run" not in readme
    assert "Results auto-update" not in readme
    assert "requirements/dev-win-py312.lock" in readme
    assert "infra/local/setup_local.sh --check" in readme
    assert "Retrospective correction (2026-09-27): NOT_CERTIFIED" in audit
    assert "claim of complete scientific reproducibility" in audit
    assert "structured schema validation" not in audit
    assert "10 real historic production outages" not in audit
