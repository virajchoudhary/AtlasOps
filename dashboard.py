"""Read-only AtlasOps demonstration console with explicit evidence labels (G14)."""

import hashlib
import json
import logging
import os
import re
from pathlib import Path

import gradio as gr

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dashboard")

# ── Config ───────────────────────────────────────────────────────────────────
GRAFANA_URL     = os.getenv("GRAFANA_URL", "")
JAEGER_URL      = os.getenv("JAEGER_URL", "")
ARGOCD_URL      = os.getenv("ARGOCD_URL", "")
BOUTIQUE_URL    = os.getenv("BOUTIQUE_URL", "")
COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://localhost:9099")
PROJECT_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = PROJECT_ROOT / "bench/results"
EVIDENCE_DIR = PROJECT_ROOT / "artifacts/evidence"
CHAOS_DIR = PROJECT_ROOT / "bench/chaos_manifests"
STATUS_FILE = PROJECT_ROOT / "docs/project/MASTER_PIPELINE_STATUS.md"

NAMED_REPLAYS = {
    "Cloudflare 2019 — Regex CPU Storm":   "named_replays/hist-cloudflare-2019",
    "AWS S3 2017 — Accidental Scale-to-0": "named_replays/hist-aws-s3-2017",
    "GitHub 2018 — DB Failover Loop":      "named_replays/hist-github-2018",
    "Datadog 2023 — DNS Failure Cascade":  "named_replays/hist-datadog-2023",
    "Discord 2022 — Cache Thundering Herd":"named_replays/hist-discord-2022",
    "Fastly 2021 — Config Bug (VCL)":      "named_replays/hist-fastly-2021",
    "Facebook BGP 2021 — Route Withdraw":  "named_replays/hist-facebook-bgp-2021",
    "Slack 2022 — HTTP/2 Misconfig":       "named_replays/hist-slack-2022",
    "Azure DNS 2019 — Stale DNS":          "named_replays/hist-azure-dns-2019",
    "Knight Capital 2012 — Bad Deploy":    "named_replays/hist-knight-capital-2012",
}

SINGLE_FAULT = {
    "sf-001: cartservice pod-kill":          "single_fault/sf-001",
    "sf-002: paymentservice CPU hog":        "single_fault/sf-002",
    "sf-003: checkoutservice OOM":           "single_fault/sf-003",
    "sf-004: frontend 50% packet loss":      "single_fault/sf-004",
    "sf-005: Redis ↔ cartservice partition": "single_fault/sf-005",
    "sf-006: DNS failure on auth path":      "single_fault/sf-006",
    "sf-007: emailservice disk fill":        "single_fault/sf-007",
    "sf-008: paymentservice clock skew":     "single_fault/sf-008",
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _apply_chaos(scenario_path: str) -> str:
    allowed = set(NAMED_REPLAYS.values()) | set(SINGLE_FAULT.values())
    if scenario_path not in allowed:
        return "❌ Unknown scenario; no command executed."
    manifest = (CHAOS_DIR / f"{scenario_path}.yaml").resolve()
    if not manifest.is_relative_to(CHAOS_DIR.resolve()) or not manifest.is_file():
        return "❌ Scenario manifest unavailable; no command executed."
    return (
        f"[READ-ONLY DEMO] Selected {scenario_path}. No fault was injected, "
        "no incident workflow ran, and no cluster command was executed. "
        "Use the preserved G4 evidence tab for an actual recorded attempt."
    )


def _reset_chaos() -> str:
    return (
        "[READ-ONLY DEMO] No cluster cleanup was performed. "
        "Use the scoped Stage 4 harness cleanup and verification procedure for real experiments."
    )


def _load_comparison_table() -> str:
    p = RESULTS_DIR / "comparison_table.md"
    if p.exists():
        return (
            "**UNVERIFIED HISTORICAL COMPARISON — mock outputs may be present.**\n\n"
            + p.read_text(encoding="utf-8")
        )
    return "No verified empirical benchmark comparison is available."


def _load_ablation_matrix() -> str:
    p = RESULTS_DIR / "final_ablation_matrix.md"
    if p.exists():
        return (
            "**NON-EMPIRICAL HISTORICAL MATRIX — not a gate result.**\n\n"
            + p.read_text(encoding="utf-8")
        )
    ev_path = EVIDENCE_DIR / "stage13/ablation_benchmark_results.json"
    if ev_path.exists():
        data = json.loads(ev_path.read_text(encoding="utf-8"))
        return (
            "**NON-EMPIRICAL HISTORICAL RESULTS — predetermined profiles.**\n\n"
            f"```json\n{json.dumps(data, indent=2)}\n```"
        )
    return "No verified empirical ablation matrix is available."


def _query_hybrid_recommender(alertname: str, service: str, symptoms: str, top_k: int) -> str:
    try:
        from recommender.dataset import load_interactions
        from recommender.hybrid import HybridRecommender
        ckpt = PROJECT_ROOT / "artifacts/models/hybrid_recommender.json"
        if ckpt.exists():
            model = HybridRecommender.load_checkpoint(ckpt)
        else:
            model = HybridRecommender().fit(load_interactions())

        query = {
            "alertname": alertname or "KubeMemoryOvercommit",
            "affected_services": [service] if service else ["frontend"],
            "symptoms_text": symptoms or "memory limit exceeded OOMKilled",
            "tier": "single_fault",
        }
        recs = model.recommend_runbooks(query, k=int(top_k))

        md_lines = [
            f"### 🎯 Top {len(recs)} Recommended Runbooks for `{alertname}` on `{service}`",
            "",
        ]
        for idx, r in enumerate(recs, 1):
            md_lines.append(f"#### #{idx} — [{r.runbook_id}] {r.title} (Ranking Score: `{r.score:.3f}`)")
            md_lines.append(f"- **Category**: `{r.category}`")
            md_lines.append(f"- **Explanation**: {r.explanation}")
            md_lines.append(f"- **Suggested Tools**: `{'`, `'.join(r.suggested_tools)}`")
            md_lines.append("- **Recommended Actions**:")
            for act in r.actions:
                md_lines.append(f"  1. {act}")
            md_lines.append("")
        md_lines.append("_Ranker inputs are scenario-derived; scores are not calibrated recovery probabilities._")
        return "\n".join(md_lines)
    except Exception:
        log.exception("Recommender query failed")
        return "Recommender unavailable; inspect the local demo log for details."


def _load_project_status() -> str:
    """Display the checked-in governance snapshot, never inferred live health."""
    try:
        source = STATUS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Project status unavailable ({type(exc).__name__})."
    rows = []
    for line in source.splitlines():
        if not re.match(r"^\| \*\*Stage \d+\*\* \|", line):
            continue
        cells = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        status = cells[4].split(" (", 1)[0]
        rows.append(f"| {cells[2]} | {cells[1]} | {status} |")
    if len(rows) != 16:
        return "Project status unavailable: the checked-in G0–G15 table could not be read."
    return (
        "**Repository governance snapshot, not a live environment check.** "
        "Software tests and CI do not close empirical gates.\n\n"
        "| Gate | Stage | Recorded status |\n|---|---|---|\n"
        + "\n".join(rows)
        + "\n\nSource: `docs/project/MASTER_PIPELINE_STATUS.md`. "
        "G4 remains NOT_PASSED; no new real experiment is implied by this dashboard."
    )


_ATTEMPT_NAME = re.compile(r"EXP-STAGE4-SF002-\d{3}(?:\.interruption)?\.json\Z")


def _list_stage4_attempts() -> list[str]:
    stage_dir = EVIDENCE_DIR / "stage4"
    if not stage_dir.is_dir():
        return []
    return sorted(
        (path.name for path in stage_dir.iterdir() if path.is_file() and _ATTEMPT_NAME.fullmatch(path.name)),
        reverse=True,
    )


def _load_stage4_attempt(name: str) -> tuple[str, str]:
    if not name or not _ATTEMPT_NAME.fullmatch(name):
        return "Select a preserved Stage 4 attempt.", ""
    path = EVIDENCE_DIR / "stage4" / name
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("expected a JSON object")
    except (OSError, ValueError, TypeError, UnicodeDecodeError) as exc:
        return f"Evidence unavailable ({type(exc).__name__}).", ""

    phases = data.get("phases") or {}
    execution = phases.get("coordinator_execution") or {}
    verification = phases.get("verification") or {}
    approval = execution.get("approval") or {}
    triage = execution.get("triage") or {}
    diagnosis = execution.get("diagnosis") or {}
    comms = execution.get("comms") or {}
    actions = execution.get("executed_tool_actions") or []
    mutating = [action for action in actions if action.get("tool") in {"kubectl_rollout", "argocd_rollback", "kubectl_scale", "kubectl_restart"}]
    from config.scenario_catalog import SCENARIO_CATALOG

    scenario = SCENARIO_CATALOG.get(data.get("scenario_id"))
    frozen_targets = scenario.target_services if scenario else ()
    triage_targets = triage.get("affected_services") or []
    proposal = execution.get("model_proposed_action") or {}
    proposed_actions = proposal.get("proposed_actions") or []
    verdict = data.get("gate_g4_pass")
    if verdict is False:
        verdict_text = "NOT PASSED"
    elif verdict is True:
        verdict_text = "HISTORICAL RAW PASS (not current certification)"
    else:
        verdict_text = "INCONCLUSIVE / NOT CERTIFIED"
    lines = [
        f"### Preserved attempt {data.get('experiment_id', name)}",
        "**Historical evidence. Current G4 governance status: NOT_PASSED.**",
        f"- Recorded outcome: **{verdict_text}**; attempt state: `{data.get('attempt_state', 'unavailable')}`.",
        f"- Recorded at: `{data.get('completed_at') or data.get('interruption_timestamp') or 'unavailable'}`.",
        f"- Scenario: `{data.get('scenario_id', 'sf-002')}`; model: `{data.get('model', 'unavailable')}`.",
        f"- Frozen scenario target: `{', '.join(frozen_targets) or 'unavailable'}`; triage affected services: `{', '.join(triage_targets) or 'unavailable'}`; severity: `{triage.get('severity', 'unavailable')}`.",
        f"- Diagnosis: {str((diagnosis.get('root_cause') or {}).get('specific') or 'unavailable')[:300]}",
        "- Recommender output: unavailable in this historical G4 attempt; the G12 integration came later.",
        f"- P1 approval decision: `{approval.get('decision', 'unavailable')}`.",
        f"- Mutating tool attempts: `{len(mutating)}`; successful attempts: `{sum(bool(a.get('output', {}).get('success')) for a in mutating)}`.",
        f"- Environment verifier resolved: `{verification.get('env_resolved', data.get('env_resolved', 'unavailable'))}`.",
        f"- Comms summary: {str(comms.get('summary_for_dashboard') or 'unavailable')[:300]}",
    ]
    criteria = data.get("causal_criteria") or {}
    warnings = []
    if frozen_targets and triage_targets and not set(frozen_targets).intersection(triage_targets):
        warnings.append("- **Target drift:** triage named no frozen target service. This attempt cannot prove correct targeting.")
    if proposed_actions:
        first = proposed_actions[0]
        tool = first.get("tool", "unavailable") if isinstance(first, dict) else "unstructured"
        lines.append(
            f"- Historical model proposal: `{tool}`; "
            "proposal is not an executed action or a trained RL policy result."
        )
    if approval.get("decision") == "timeout" and criteria.get("8_approval_satisfied") is True:
        warnings.append("- **Evidence inconsistency:** historical causal criterion marks approval satisfied despite a timeout. This is not approval proof.")
    if approval.get("decision") == "timeout" and mutating:
        warnings.append("- **Safety finding:** mutating tool attempts are recorded after a P1 approval timeout. The attempt did not establish safe approval behavior.")
    lines[3:3] = warnings
    for action in mutating[:5]:
        args = action.get("args") or {}
        output = action.get("output") or {}
        target = args.get("resource") or args.get("app") or "unavailable"
        lines.append(
            f"  - Tool attempt: `{action.get('tool', 'unavailable')}` on `{str(target)[:80]}`; "
            f"tool success: `{bool(output.get('success'))}`."
        )
    if name.endswith(".interruption.json"):
        lines.append(f"- Interruption: `{data.get('classification', 'unavailable')}`; no completed verifier verdict is recorded.")
    provenance = (
        f"Source: `{path.relative_to(PROJECT_ROOT)}`  \n"
        f"SHA-256: `{hashlib.sha256(raw).hexdigest()}`  \n"
        "This summary shows selected fields; inspect the preserved source for the full record."
    )
    return "\n".join(lines), provenance


# ── Tab Builders ───────────────────────────────────────────────────────────────
def build_status_tab():
    with gr.Tab("🧭 Project Status"):
        gr.Markdown("## What has been proved so far")
        status_out = gr.Markdown(_load_project_status())
        gr.Button("Refresh repository status").click(_load_project_status, outputs=[status_out])


def build_live_ops_tab():
    with gr.Tab("⚡ Scenario Control"):
        gr.Markdown("## Scenario selection")
        gr.Markdown(
            "This console is read-only: selecting a scenario does not inject a fault or run "
            "the agent pipeline. The preserved G4 tab shows a recorded real attempt."
        )
        with gr.Row():
            scenario_dropdown = gr.Dropdown(
                choices=list(SINGLE_FAULT.keys()),
                value=next(iter(SINGLE_FAULT)),
                label="Select Failure Scenario",
            )
            trigger_btn = gr.Button("Inspect scenario (read-only)", variant="primary")
        status_box = gr.Textbox(label="Execution Status", lines=2)
        trigger_btn.click(lambda s: _apply_chaos(SINGLE_FAULT[s]), inputs=[scenario_dropdown], outputs=[status_box])


def build_recommender_tab():
    with gr.Tab("📚 Runbook Recommender (RS)"):
        gr.Markdown("## Interactive Hybrid Runbook Recommender (Gate G11 / Stage 12)")
        gr.Markdown("**Local recommender demonstration.** Ranking uses scenario-derived interactions, not historical user feedback or a calibrated recovery probability. No remediation is executed here.")
        with gr.Row():
            alert_in = gr.Dropdown(
                choices=["KubeMemoryOvercommit", "PodCrashLooping", "HighHTTP5xxRate", "DatabaseConnectionExhaustion", "NetworkPartitionDetected", "DiskVolumeUsageCritical"],
                value="KubeMemoryOvercommit",
                label="Alert Name",
            )
            service_in = gr.Dropdown(
                choices=["frontend", "checkoutservice", "paymentservice", "cartservice", "emailservice", "productcatalogservice"],
                value="frontend",
                label="Affected Microservice",
            )
            topk_slider = gr.Slider(minimum=1, maximum=5, value=3, step=1, label="Top-K Recommendations")
        symptoms_in = gr.Textbox(
            label="Observed Incident Symptoms & Root-Cause Notes",
            value="Container killed by OOM (exit code 137), memory limit 250Mi breached under flash-sale load.",
            lines=2,
        )
        recommend_btn = gr.Button("🔍 Query Hybrid Recommender", variant="primary")
        recs_out = gr.Markdown(_query_hybrid_recommender("KubeMemoryOvercommit", "frontend", "OOMKilled memory limit exceeded", 3))
        recommend_btn.click(
            _query_hybrid_recommender,
            inputs=[alert_in, service_in, symptoms_in, topk_slider],
            outputs=[recs_out],
        )


def build_incidents_tab():
    with gr.Tab("📋 Preserved G4 Evidence"):
        gr.Markdown("## Recorded golden-incident attempts")
        gr.Markdown("Read-only summaries of preserved Stage 4 records. An interrupted or negative record is not a gate pass.")
        choices = _list_stage4_attempts()
        initial = "EXP-STAGE4-SF002-010.json" if "EXP-STAGE4-SF002-010.json" in choices else (choices[0] if choices else None)
        with gr.Row():
            incident_list = gr.Dropdown(choices=choices, value=initial, label="Preserved evidence file")
            refresh_btn = gr.Button("Refresh evidence list")
        summary, source = _load_stage4_attempt(initial) if initial else ("No Stage 4 attempt evidence is available.", "")
        timeline_out = gr.Markdown(summary)
        payload_out = gr.Markdown(source)
        incident_list.change(_load_stage4_attempt, inputs=[incident_list], outputs=[timeline_out, payload_out])
        refresh_btn.click(lambda: gr.update(choices=_list_stage4_attempts()), outputs=[incident_list])


def build_ablation_tab():
    with gr.Tab("📈 Multi-Model Ablations (Stage 13)"):
        gr.Markdown("## Final Multi-Model Ablation & Stress Matrix (Gate G13)")
        gr.Markdown("Comparison of the predetermined 5-model family across all 4 evaluation splits.")
        ablation_out = gr.Markdown(_load_ablation_matrix())
        refresh_btn = gr.Button("🔄 Refresh Ablation Matrix")
        refresh_btn.click(_load_ablation_matrix, outputs=[ablation_out])


def build_bench_tab():
    with gr.Tab("📊 Benchmark Overview"):
        gr.Markdown("## AtlasOps — Benchmark Results & Tier Breakdown")
        bench_out = gr.Markdown(_load_comparison_table())
        refresh_btn = gr.Button("🔄 Refresh Benchmark Overview")
        refresh_btn.click(_load_comparison_table, outputs=[bench_out])


def build_replays_tab():
    with gr.Tab("🎬 Scenario Catalogue"):
        gr.Markdown("## Named scenario manifests")
        gr.Markdown("These buttons select a manifest in read-only mode. They do not replay a historical incident or produce an agent result.")
        reset_out = gr.Textbox(label="Action status", lines=3)
        with gr.Row():
            for name in list(NAMED_REPLAYS.keys())[:5]:
                btn = gr.Button(name, size="sm")
                path = NAMED_REPLAYS[name]
                btn.click(lambda p=path: _apply_chaos(p), outputs=[reset_out])
        with gr.Row():
            for name in list(NAMED_REPLAYS.keys())[5:]:
                btn = gr.Button(name, size="sm")
                path = NAMED_REPLAYS[name]
                btn.click(lambda p=path: _apply_chaos(p), outputs=[reset_out])
        reset_all = gr.Button("Show cleanup guidance")
        reset_all.click(_reset_chaos, outputs=[reset_out])


def build_about_tab():
    with gr.Tab("ℹ️ About & Architecture"):
        gr.Markdown("""
## AtlasOps — Autonomous Multi-Agent Incident Response on Kubernetes

### Architecture
`Incident Alert → Triage Agent → Diagnosis Agent → Hybrid Runbook Recommender → Approval Gate → Remediation Agent → Environment Verifier → Comms Agent`

### Academic Workstreams
1. **Generative AI**: Multi-agent reasoning, tool calling, fault diagnosis, and incident communication.
2. **Recommender Systems**: Hybrid collaborative/content-based top-$K$ runbook recommender ($S_{\\text{content}} + S_{\\text{collab}} + S_{\\text{prior}}$).
3. **Reinforcement Learning**: Online Group Relative Policy Optimization (GRPO) with normalized advantage estimation and objective verifier contract reward.

### Project Fork & Provenance
Forked from `Harikishanth/AtlasOps` (frozen baseline `bf9bd19`) into `virajchoudhary/AtlasOps` with full git history and attribution preserved.
""")


def build_app():
    with gr.Blocks(title="AtlasOps Ops Console & Demo Interface", analytics_enabled=False) as demo:
        gr.Markdown("# ⚡ AtlasOps — Autonomous Multi-Agent Incident Response Console")
        gr.Markdown("**Local demo and preserved evidence. No live health or empirical gate pass is implied.**")
        build_status_tab()
        build_live_ops_tab()
        build_recommender_tab()
        build_incidents_tab()
        build_ablation_tab()
        build_bench_tab()
        build_replays_tab()
        build_about_tab()
    return demo


if __name__ == "__main__":
    demo = build_app()
    demo.launch(server_name="127.0.0.1", server_port=7860, share=False)
