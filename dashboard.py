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


# ── Read-only product views ────────────────────────────────────────────────────
_UI_CSS = """
:root { --atlas-ink:#172322; --atlas-line:#dce3e0; --atlas-muted:#586664; }
body, .gradio-container { background:#f5f7f6 !important; color:var(--atlas-ink) !important; }
.gradio-container { max-width:1380px !important; padding:20px 28px 48px !important; }
.atlas-header { background:#182524; color:#edf5f1; padding:21px 25px; border-radius:6px; }
.atlas-header strong { font-size:19px; }
.atlas-header small { display:block; color:#b5c9c1; margin-top:3px; }
.atlas-status { display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
  background:#fff; border:1px solid var(--atlas-line); border-radius:6px; margin:17px 0 25px; }
.atlas-status div { padding:15px 18px; border-right:1px solid var(--atlas-line); }
.atlas-status div:last-child { border:0; }
.atlas-status span { display:block; color:var(--atlas-muted); font-size:12px; }
.atlas-status b { display:block; font-size:17px; margin-top:5px; }
.gradio-container .tab-nav { border-bottom:1px solid var(--atlas-line); gap:3px; }
.gradio-container .tab-nav button { border-radius:5px 5px 0 0 !important; font-size:13px; }
.gradio-container .tab-nav button.selected { border-bottom:2px solid #087c70 !important; }
.gradio-container .prose h2 { font-size:22px; margin-top:18px; }
.gradio-container .prose h3 { font-size:16px; }
.gradio-container button { border-radius:5px !important; }
@media(max-width:720px) {
  .gradio-container { padding:12px 13px 32px !important; }
  .atlas-status { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .atlas-status div:nth-child(2) { border-right:0; }
  .atlas-status div:nth-child(-n+2) { border-bottom:1px solid var(--atlas-line); }
}
"""


def build_overview_tab():
    with gr.Tab("Overview"):
        gr.Markdown("## Operations")
        gr.Markdown(
            "This local demo displays checked-in evidence, not a live incident feed. "
            "Open the FastAPI operator console for current process observations."
        )
        gr.Markdown("### Current constraints")
        gr.Markdown(
            "- **G4: NOT_PASSED.** Attempt 010 is a completed negative result.\n"
            "- **No current environment verdict.** Historical cluster acceptance is not live health.\n"
            "- **No verified model outcome aggregate.** Mock and predetermined outputs are not empirical results."
        )


def build_incidents_tab():
    with gr.Tab("Incidents"):
        gr.Markdown("## Preserved incident attempts")
        gr.Markdown(
            "Read-only historical G4 evidence. An interrupted or negative record is not a gate pass."
        )
        choices = _list_stage4_attempts()
        initial = "EXP-STAGE4-SF002-010.json" if "EXP-STAGE4-SF002-010.json" in choices else (
            choices[0] if choices else None
        )
        with gr.Row():
            incident_list = gr.Dropdown(choices=choices, value=initial, label="Recorded attempt")
            refresh_btn = gr.Button("Refresh evidence list")
        summary, source = _load_stage4_attempt(initial) if initial else (
            "No Stage 4 attempt evidence is available.", ""
        )
        summary_out = gr.Markdown(summary)
        provenance_out = gr.Markdown(source)
        incident_list.change(
            _load_stage4_attempt, inputs=[incident_list], outputs=[summary_out, provenance_out]
        )
        refresh_btn.click(lambda: gr.update(choices=_list_stage4_attempts()), outputs=[incident_list])
        gr.Markdown("### Inspect a scenario")
        gr.Markdown("Selection does not inject a fault, run agents, or change the cluster.")
        with gr.Row():
            scenario = gr.Dropdown(
                choices=list(SINGLE_FAULT), value=next(iter(SINGLE_FAULT)), label="Scenario manifest"
            )
            inspect = gr.Button("Inspect selection")
        selection_out = gr.Textbox(label="Read-only selection", lines=2, interactive=False)
        inspect.click(
            lambda s: _apply_chaos(SINGLE_FAULT[s]), inputs=[scenario], outputs=[selection_out]
        )


def build_agents_tab():
    with gr.Tab("Agents"):
        gr.Markdown("## Agent roles")
        gr.Markdown(
            "**Generative agents propose. Safety controls authorize. "
            "Environment verification decides success.**"
        )
        gr.Markdown(
            "| Role | Responsibility | Runtime state |\n|---|---|---|\n"
            "| Triage | Classify alerts and affected services | Not observed |\n"
            "| Diagnosis | Investigate root cause | Not observed |\n"
            "| Recommender | Rank advisory runbooks | Local query available |\n"
            "| Remediation policy | Enforce target, tool, and approval boundaries | Not observed |\n"
            "| Verifier | Check objective environment state | No current verdict |\n"
            "| Comms | Record incident updates | Not observed |"
        )


def build_models_tab():
    with gr.Tab("Models"):
        gr.Markdown("## Model readiness")
        gr.Markdown(
            "Model names and training code are not deployment evidence. No endpoint probe, "
            "usable SFT checkpoint, or completed GRPO evaluation is established by this demo."
        )
        gr.Markdown(
            "| Stage | Recorded state | Readiness |\n|---|---|---|\n"
            "| Base model | Repository configuration | Runtime not verified |\n"
            "| SFT | Corpus and config exist | Usable checkpoint unverified |\n"
            "| GRPO | Software contract implemented | Training and evaluation missing |"
        )


def build_evaluations_tab():
    with gr.Tab("Evaluations"):
        gr.Markdown("## Research status")
        status_out = gr.Markdown(_load_project_status())
        gr.Button("Refresh repository status").click(_load_project_status, outputs=[status_out])
        with gr.Accordion("Historical benchmark comparison", open=False):
            bench_out = gr.Markdown(_load_comparison_table())
            gr.Button("Refresh comparison").click(_load_comparison_table, outputs=[bench_out])
        with gr.Accordion("Predetermined ablation output", open=False):
            ablation_out = gr.Markdown(_load_ablation_matrix())
            gr.Button("Refresh matrix").click(_load_ablation_matrix, outputs=[ablation_out])


def build_runbooks_tab():
    with gr.Tab("Runbooks"):
        gr.Markdown("## Advisory runbook ranking")
        gr.Markdown(
            "Local recommender demonstration. Interactions are scenario-derived, not historical "
            "operator feedback; ranking scores are not recovery probabilities. No remediation runs."
        )
        with gr.Row():
            alert_in = gr.Dropdown(
                choices=["KubeMemoryOvercommit", "PodCrashLooping", "HighHTTP5xxRate",
                         "DatabaseConnectionExhaustion", "NetworkPartitionDetected",
                         "DiskVolumeUsageCritical"],
                value="KubeMemoryOvercommit", label="Alert",
            )
            service_in = gr.Dropdown(
                choices=["frontend", "checkoutservice", "paymentservice", "cartservice",
                         "emailservice", "productcatalogservice"],
                value="frontend", label="Service",
            )
            topk_slider = gr.Slider(minimum=1, maximum=5, value=3, step=1, label="Suggestions")
        symptoms_in = gr.Textbox(
            label="Symptoms", value="OOMKilled memory limit exceeded", lines=2
        )
        recommend_btn = gr.Button("Rank runbooks", variant="primary")
        recs_out = gr.Markdown("No query run in this session.")
        recommend_btn.click(
            _query_hybrid_recommender,
            inputs=[alert_in, service_in, symptoms_in, topk_slider],
            outputs=[recs_out],
        )


def build_evidence_tab():
    with gr.Tab("Evidence"):
        gr.Markdown("## Evidence classes")
        gr.Markdown(
            "| Source | Classification | Interpretation |\n|---|---|---|\n"
            "| Stage 4 attempts | Historical real attempts | Negative or interrupted; G4 NOT_PASSED |\n"
            "| Stage 6/8/9 archive | Mock outputs | Not empirical model performance |\n"
            "| Stage 10/11 | Scenario-derived | Bounded recommender evaluation |\n"
            "| Stage 13 matrix | Predetermined profile | Not measured ablation |"
        )
        gr.Markdown("### Named scenario manifests")
        gr.Markdown("Inspecting a manifest is not a replay or a new incident.")
        named = gr.Dropdown(
            choices=list(NAMED_REPLAYS), value=next(iter(NAMED_REPLAYS)), label="Named manifest"
        )
        output = gr.Textbox(label="Read-only selection", lines=2, interactive=False)
        gr.Button("Inspect manifest").click(
            lambda s: _apply_chaos(NAMED_REPLAYS[s]), inputs=[named], outputs=[output]
        )


def build_settings_tab():
    with gr.Tab("Settings"):
        gr.Markdown("## Read-only configuration")
        gr.Markdown(
            "| Item | State |\n|---|---|\n"
            "| Runtime environment | Not probed by this demo |\n"
            "| Model endpoint | Not probed by this demo |\n"
            "| Kubernetes context | Not exposed |\n"
            "| Evidence | Checked-in repository snapshot |\n"
            "| Approval and remediation | No controls in this demo |"
        )
        gr.Markdown("Real experiments use the governed Stage 4 harness and objective verification.")
        guidance = gr.Textbox(label="Cleanup guidance", lines=2, interactive=False)
        gr.Button("Show cleanup guidance").click(_reset_chaos, outputs=[guidance])


def build_app():
    with gr.Blocks(
        title="AtlasOps | Read-only demonstration",
        analytics_enabled=False,
    ) as demo:
        gr.HTML(
            '<div class="atlas-header"><strong>AtlasOps</strong>'
            "<small>Read-only demonstration / repository evidence snapshot</small></div>"
            '<div class="atlas-status">'
            "<div><span>Active incidents</span><b>Not connected</b></div>"
            "<div><span>Verified outcomes</span><b>Unavailable</b></div>"
            "<div><span>G4 governance</span><b>NOT_PASSED</b></div>"
            "<div><span>Environment</span><b>Not probed</b></div></div>"
        )
        build_overview_tab()
        build_incidents_tab()
        build_agents_tab()
        build_models_tab()
        build_evaluations_tab()
        build_runbooks_tab()
        build_evidence_tab()
        build_settings_tab()
    return demo


if __name__ == "__main__":
    demo = build_app()
    demo.launch(server_name="127.0.0.1", server_port=7860, share=False, css=_UI_CSS)
