"""Read-only AtlasOps demonstration console with explicit evidence labels (G14)."""

import hashlib
import html
import json
import re
from pathlib import Path

import gradio as gr

from ui_read_model import catalog as read_model_catalog
from ui_read_model import current_results, final_g4_attempts
from ui_read_model import gates as read_model_gates

PROJECT_ROOT = Path(__file__).resolve().parent
EVIDENCE_DIR = PROJECT_ROOT / "artifacts/evidence"
CHAOS_DIR = PROJECT_ROOT / "bench/chaos_manifests"

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
    return (
        "**HISTORICAL ARCHIVE / NON-EMPIRICAL.** Legacy comparison tables are preserved "
        "for traceability and are not loaded or displayed as current results. See "
        "`artifacts/evidence/mock_archive/`."
    )


def _load_ablation_matrix() -> str:
    return (
        "**HISTORICAL ARCHIVE / NON-EMPIRICAL.** The Stage 13 predetermined profile is "
        "not a measured ablation, current evaluation, or gate result. Its source remains "
        "`artifacts/evidence/stage13/ablation_benchmark_results.json`; this demo does not "
        "load its metric rows."
    )


def _load_project_status() -> str:
    """Display the checked-in governance snapshot, never inferred live health."""
    try:
        rows = read_model_gates()
    except (OSError, ValueError) as exc:
        return f"Project status unavailable ({type(exc).__name__})."
    table_rows = [
        f"| {row['gate']} | {row['stage']} | {row['status']} | "
        f"{row['status_note'] or 'None'} |"
        for row in rows
    ]
    return (
        "**Repository governance snapshot, not a live environment check.** "
        "The detailed G4/G9 results below provide the final evidence chronology; "
        "software tests and CI do not close empirical gates.\n\n"
        "| Gate | Stage | Status code | Snapshot qualifier |\n|---|---|---|---|\n"
        + "\n".join(table_rows)
        + "\n\nSource: `docs/project/MASTER_PIPELINE_STATUS.md`."
    )


def _format_value(value, digits: int | None = None) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, float) and digits is not None:
        return f"{value:.{digits}f}"
    return str(value)


def _load_results_overview() -> str:
    results = current_results()
    sft = results["sft_v17"]
    validation = results["base_vs_sft"]
    pilot = results["g9_pilot"]
    aligned = results["g9_aligned_diagnostic"]
    base_schema = (
        f"{_format_value(validation['base_schema_valid'])}/"
        f"{_format_value(validation['base_schema_total'])}"
    )
    sft_schema = (
        f"{_format_value(validation['sft_schema_valid'])}/"
        f"{_format_value(validation['sft_schema_total'])}"
    )
    aligned_count = (
        f"{_format_value(aligned['admissible_count'])}/"
        f"{_format_value(aligned['sample_count'])}"
    )
    return (
        "| Evidence | Current finding | Scope |\n|---|---|---|\n"
        f"| SFT v17 | {sft['status']} | {sft['reload_tensor_count'] or 'Unavailable'} "
        "adapter tensors checked; no incident-improvement claim |\n"
        f"| Base vs SFT | {_format_value(validation['base_f1'], 5)} vs "
        f"{_format_value(validation['sft_f1'], 5)}; delta "
        f"{_format_value(validation['paired_delta'], 5)} | Validation diagnostic; "
        f"schema {base_schema} / {sft_schema} |\n"
        f"| Controlled G9 | {pilot['status']} | "
        f"{_format_value(pilot['optimizer_steps'])} steps; "
        f"{_format_value(pilot['malformed_or_blocked'])}/"
        f"{_format_value(pilot['completions'])} blocked |\n"
        f"| Final aligned diagnostic | {aligned_count} admissible | "
        f"{_format_value(aligned['optimizer_steps'])} optimizer steps; "
        f"{_format_value(aligned['tensor_hash_count'])} LoRA hashes "
        f"{'unchanged' if aligned['tensor_hashes_unchanged'] else 'unavailable'} |"
    )


def _load_current_results() -> str:
    results = current_results()
    sft = results["sft_v17"]
    validation = results["base_vs_sft"]
    pilot = results["g9_pilot"]
    aligned = results["g9_aligned_diagnostic"]
    g9_pilot_archive = _format_value(pilot["archive_sha256"])
    schema_rows = (
        "| Base | "
        f"{_format_value(validation['base_f1'], 5)} | "
        f"{_format_value(validation['base_schema_valid'])}/"
        f"{_format_value(validation['base_schema_total'])} |\n"
        "| SFT v17 | "
        f"{_format_value(validation['sft_f1'], 5)} | "
        f"{_format_value(validation['sft_schema_valid'])}/"
        f"{_format_value(validation['sft_schema_total'])} |"
    )
    return (
        "## Current evidence\n\n"
        "### SFT v17 artifact\n"
        f"- Status: **{sft['status']}**; run `{_format_value(sft['run_id'])}`.\n"
        f"- Base model: `{_format_value(sft['model'])}@"
        f"{_format_value(sft['model_revision'])}`.\n"
        f"- Training evidence: {_format_value(sft['training_steps'])} optimizer steps; "
        f"{_format_value(sft['corpus_rows'])} synthetic Train-only rows.\n"
        f"- Adapter weight SHA-256: `{_format_value(sft['adapter_sha256'])}`; "
        f"independent reload checked {_format_value(sft['reload_tensor_count'])} LoRA tensors.\n"
        "- This establishes a preserved, reloadable artifact, not incident improvement.\n\n"
        "### Base vs SFT Validation diagnostic\n"
        f"Run: `{_format_value(validation['run_id'])}`; "
        f"{validation['scope']}. Six matched rows per arm are summarized below.\n\n"
        "| Arm | Diagnostic F1 | Schema-conformant JSON |\n|---|---:|---:|\n"
        f"{schema_rows}\n\n"
        f"Paired delta (SFT - Base): **{_format_value(validation['paired_delta'], 5)}**. "
        "No diagnostic improvement was observed. Resolution, safety, reward, and "
        "time-to-resolve were not measured and remain unavailable.\n\n"
        "### Controlled G9 final pilot\n"
        f"- Status: **{pilot['status']}**; "
        f"{_format_value(pilot['optimizer_steps'])} optimizer steps and "
        f"{_format_value(pilot['completions'])} policy completions.\n"
        f"- {_format_value(pilot['malformed_or_blocked'])}/"
        f"{_format_value(pilot['completions'])} completions were malformed/blocked; "
        f"reward was `{_format_value(pilot['reward_each'])}` for each.\n"
        f"- Zero reward-driven advantages in "
        f"{_format_value(pilot['zero_advantage_groups'])} groups; no acceptable "
        "SFT+GRPO checkpoint exists. No further training is authorized.\n"
        f"- Preserved negative archive SHA-256: `{g9_pilot_archive}`.\n\n"
        "### Final aligned diagnostic\n"
        f"- **{_format_value(aligned['admissible_count'])}/"
        f"{_format_value(aligned['sample_count'])}** admissible actions; "
        f"{_format_value(aligned['optimizer_steps'])} optimizer steps.\n"
        f"- {_format_value(aligned['tensor_hash_count'])} LoRA tensor hashes were "
        f"{'unchanged' if aligned['tensor_hashes_unchanged'] else 'unavailable'}.\n"
        "- Zero of eight does not establish that the population probability is zero. "
        "No reward-driven update or accepted checkpoint resulted.\n"
        "- Base/parent inventory, resolution, reward, and time-to-resolve values not "
        "reported here are unavailable."
    )


def _load_g4_final_chronology() -> str:
    rows = final_g4_attempts()
    rendered = []
    for row in rows:
        source = row["source"]
        if row["raw_record_available"]:
            source_state = f"`{source['path']}` SHA-256 `{source['sha256']}`"
        elif row["attempt"] == "015" and source["available"]:
            source_state = (
                f"Integrity index only: `{source['path']}` SHA-256 `{source['sha256']}`; "
                "referenced raw attempt is external."
            )
        elif row["attempt"] == "017":
            source_state = (
                f"Status summary: `{source['path']}` SHA-256 `{source['sha256']}`; "
                "external raw operational evidence is intentionally not republished."
            )
        elif source["available"]:
            source_state = (
                f"Status summary: `{source['path']}` SHA-256 `{source['sha256']}`; "
                "attempt-level raw evidence is unavailable here."
            )
        else:
            source_state = "Attempt-level source unavailable in this checkout."
        rendered.append(
            f"| {row['attempt']} | **{row['state']}** | {source_state} | "
            "Resolution / reward / TTR: unavailable |"
        )
    return (
        "**G4 remains NOT_PASSED and frozen.** This chronology reports the known "
        "final dispositions; the demo does not infer resolution, reward, or TTR.\n\n"
        "| Attempt | Disposition | Local provenance | Unavailable measures |\n"
        "|---|---|---|---|\n"
        + "\n".join(rendered)
        + "\n\nThe current source snapshot contains the attempt 015 integrity index, "
        "but not the complete 015/016/017 raw bundle."
    )


def _load_current_evidence() -> str:
    evidence = read_model_catalog()["evidence"]
    rows = []
    for item in evidence:
        digest = f"`{item['sha256']}`" if item["sha256"] else "Unavailable"
        availability = "Present" if item["available"] else "Not present in this checkout"
        rows.append(
            f"| {item['title']} | {item['classification']} | `{item['path']}` | "
            f"{availability}; SHA-256 {digest} |"
        )
    return (
        "**Evidence files are read only.** Hashes below are computed from the current "
        "checkout; missing source files remain unavailable.\n\n"
        "| Record | Classification | Source path | Local availability / SHA-256 |\n"
        "|---|---|---|---|\n"
        + "\n".join(rows)
    )


def _load_archive_index() -> str:
    archive = read_model_catalog()["historical_archive"]
    rows = [
        f"| {item['title']} | {item['classification']} | `{item['path']}` | "
        f"{item['details']} |"
        for item in archive
    ]
    return (
        "**HISTORICAL ARCHIVE / NON-EMPIRICAL where marked.** These source pointers "
        "are not current evaluation results and their metric rows are not loaded here.\n\n"
        "| Archive | Classification | Preserved location | Boundary |\n"
        "|---|---|---|---|\n"
        + "\n".join(rows)
    )


def _load_static_runbooks() -> str:
    from recommender.runbook_catalog import get_all_runbooks

    rows = [
        f"- **{item.runbook_id}**: {item.title} ({item.category}). {item.description}"
        for item in get_all_runbooks()
    ]
    return (
        "**Static catalog only.** This view does not rank, fit, or query a recommender "
        "and does not execute suggested actions.\n\n"
        + "\n".join(rows)
    )


_ATTEMPT_NAME = re.compile(
    r"EXP-STAGE4-SF002-(?:00[2-9]|01[0-4])(?:\.interruption)?\.json\Z"
)


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
:root { --atlas-ink:#182421; --atlas-line:#d9e1dd; --atlas-muted:#56645f;
  --atlas-paper:#f4f7f5; --atlas-green:#087c68; --atlas-red:#a83f36;
  --atlas-gold:#855b16; }
body, .gradio-container { background:var(--atlas-paper) !important; color:var(--atlas-ink) !important; }
.gradio-container { max-width:1380px !important; padding:18px 26px 44px !important; }
.atlas-header { background:#172522; color:#f1f6f3; padding:19px 23px; border-radius:6px; }
.atlas-header strong { font-size:20px; }
.atlas-header small { display:block; color:#c2d1ca; margin-top:4px; }
.atlas-status { display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
  background:#fff; border:1px solid var(--atlas-line); border-radius:6px; margin:15px 0 24px; }
.atlas-status div { min-width:0; padding:13px 16px; border-right:1px solid var(--atlas-line); }
.atlas-status div:last-child { border:0; }
.atlas-status span { display:block; color:var(--atlas-muted); font-size:12px; }
.atlas-status b { display:block; overflow-wrap:anywhere; font-size:16px; margin-top:5px; }
.atlas-status .negative b { color:var(--atlas-red); }
.atlas-status .result b { color:var(--atlas-green); }
.atlas-flow { border-left:3px solid var(--atlas-green); padding:11px 15px;
  background:#fff; margin:8px 0 18px; line-height:1.8; }
.gradio-container .tab-nav { border-bottom:1px solid var(--atlas-line); gap:3px; }
.gradio-container .tab-nav button { border-radius:5px 5px 0 0 !important; font-size:13px; }
.gradio-container .tab-nav button.selected { border-bottom:2px solid var(--atlas-green) !important; }
.gradio-container .prose h2 { font-size:22px; margin-top:17px; }
.gradio-container .prose h3 { font-size:16px; }
.gradio-container button { border-radius:5px !important; }
@media(max-width:720px) {
  .gradio-container { padding:11px 13px 30px !important; }
  .atlas-status { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .atlas-status div:nth-child(2) { border-right:0; }
  .atlas-status div:nth-child(-n+2) { border-bottom:1px solid var(--atlas-line); }
}
"""


def _header_html() -> str:
    result = current_results()
    delta = html.escape(_format_value(result["base_vs_sft"]["paired_delta"], 5))
    sft_state = html.escape(result["sft_v17"]["status"])
    pilot_state = html.escape(result["g9_pilot"]["status"])
    return (
        '<div class="atlas-header"><strong>AtlasOps</strong>'
        "<small>Governed multi-agent SRE research / local read-only evidence snapshot</small></div>"
        '<div class="atlas-status">'
        '<div class="negative"><span>G4 governance</span><b>NOT_PASSED</b></div>'
        f'<div class="result"><span>SFT v17 artifact</span><b>{sft_state}</b></div>'
        f'<div class="negative"><span>Validation delta</span><b>{delta}</b></div>'
        f'<div class="negative"><span>Controlled G9</span><b>{pilot_state}</b></div>'
        "</div>"
    )


def build_overview_tab():
    with gr.Tab("Overview"):
        gr.Markdown("## Project snapshot")
        gr.Markdown(
            "AtlasOps is a governed multi-agent SRE research system. This console presents "
            "checked-in implementation and evidence; it is not an operations console or a live health view."
        )
        gr.Markdown("### Incident workflow")
        gr.HTML(
            '<div class="atlas-flow">Incident / Alert -&gt; Triage Agent -&gt; Diagnosis Agent '
            "-&gt; Safety / Approval Gate -&gt; Remediation Agent -&gt; Objective Environment "
            "Verifier -&gt; Comms Agent</div>"
        )
        gr.Markdown("### Current findings")
        gr.Markdown(_load_results_overview())
        gr.Markdown("### Gate summary")
        status_out = gr.Markdown(_load_project_status())
        gr.Button("Refresh repository snapshot").click(_load_project_status, outputs=[status_out])


def build_incidents_tab():
    with gr.Tab("Incidents"):
        gr.Markdown("## Final G4 chronology")
        current_out = gr.Markdown(_load_g4_final_chronology())
        gr.Button("Refresh G4 references").click(
            _load_g4_final_chronology, outputs=[current_out]
        )
        with gr.Accordion("Earlier preserved G4 records", open=False):
            gr.Markdown(
                "These older records are historical context only. They do not replace the "
                "015/016/017 chronology or change G4's frozen NOT_PASSED status."
            )
            choices = _list_stage4_attempts()
            initial = (
                "EXP-STAGE4-SF002-010.json"
                if "EXP-STAGE4-SF002-010.json" in choices
                else choices[0] if choices else None
            )
            with gr.Row():
                incident_list = gr.Dropdown(choices=choices, value=initial, label="Earlier record")
                refresh_btn = gr.Button("Refresh earlier records")
            summary, source = _load_stage4_attempt(initial) if initial else (
                "No earlier Stage 4 record is available.", ""
            )
            summary_out = gr.Markdown(summary)
            provenance_out = gr.Markdown(source)
            incident_list.change(
                _load_stage4_attempt, inputs=[incident_list], outputs=[summary_out, provenance_out]
            )
            refresh_btn.click(
                lambda: gr.update(choices=_list_stage4_attempts()), outputs=[incident_list]
            )
        with gr.Accordion("Scenario manifest references", open=False):
            gr.Markdown(
                "Selection only checks for a known local manifest. It does not inject a fault, "
                "run agents, call kubectl, or change a cluster."
            )
            with gr.Row():
                scenario = gr.Dropdown(
                    choices=list(SINGLE_FAULT),
                    value=next(iter(SINGLE_FAULT)),
                    label="Manifest reference",
                )
                inspect = gr.Button("Inspect reference")
            selection_out = gr.Textbox(
                label="Read-only reference result", lines=2, interactive=False
            )
            inspect.click(
                lambda selected: _apply_chaos(SINGLE_FAULT[selected]),
                inputs=[scenario],
                outputs=[selection_out],
            )


def build_agents_tab():
    with gr.Tab("Agents"):
        gr.Markdown("## Architecture and controls")
        gr.Markdown(
            "**Generative agents propose. Role and tool policy constrain actions. "
            "Explicit approval gates P1 remediation. Objective environment verification "
            "decides whether resolution occurred.**"
        )
        gr.Markdown(
            "Incident / Alert -> Triage Agent -> Diagnosis Agent -> Safety / Approval Gate "
            "-> Remediation Agent -> Environment Verifier -> Comms Agent"
        )
        gr.Markdown(
            "| Component | Responsibility | Demo/runtime state |\n|---|---|---|\n"
            "| Triage Agent | Classify the alert and affected services | Not executed by this demo |\n"
            "| Diagnosis Agent | Ground a proposed cause in observations | Not executed by this demo |\n"
            "| Remediation Agent | Submit only policy-allowed actions after approval | No action controls here |\n"
            "| Comms Agent | Record incident updates and outcome | No incident run here |\n"
            "| Safety / Approval Gate | Role ACL, tool policy, explicit P1 approval; fail closed | No approval mutation here |\n"
            "| Environment Verifier | Check objective state before a success claim | No live environment probe here |"
        )


def build_models_tab():
    with gr.Tab("Models"):
        result = current_results()["sft_v17"]
        gr.Markdown("## Preserved SFT artifact")
        gr.Markdown(
            f"- Status: **{result['status']}**.\n"
            f"- Run: `{_format_value(result['run_id'])}`.\n"
            f"- Base model: `{_format_value(result['model'])}@"
            f"{_format_value(result['model_revision'])}`.\n"
            f"- Corpus: {_format_value(result['corpus_rows'])} "
            f"{_format_value(result['corpus_split'])}-split rows; synthetic: "
            f"`{_format_value(result['synthetic_corpus'])}`.\n"
            f"- Completed training steps: {_format_value(result['training_steps'])}.\n"
            f"- Adapter SHA-256: `{_format_value(result['adapter_sha256'])}`.\n"
            f"- Fresh-process independent reload: `{_format_value(result['reload_status'])}`; "
            f"{_format_value(result['reload_tensor_count'])} LoRA tensors checked.\n\n"
            "The artifact is genuine and reloadable. These records do not show incident "
            "improvement. This demo performs no model loading or inference."
        )


def build_evaluations_tab():
    with gr.Tab("Evaluations"):
        gr.Markdown("## Current results")
        result_out = gr.Markdown(_load_current_results())
        gr.Button("Refresh result summaries").click(
            _load_current_results, outputs=[result_out]
        )
        gr.Markdown("### Current gate statuses")
        status_out = gr.Markdown(_load_project_status())
        gr.Button("Refresh gate snapshot").click(_load_project_status, outputs=[status_out])
        with gr.Accordion(
            "Historical archive: NON-EMPIRICAL and excluded from current results", open=False
        ):
            gr.Markdown(_load_archive_index())


def build_runbooks_tab():
    with gr.Tab("Runbooks"):
        gr.Markdown("## Advisory catalog")
        gr.Markdown(
            "Optional historical recommender work is scenario-derived and out of scope for "
            "required GAI+RL completion. This static catalog does not rank, fit, infer, or "
            "execute runbooks."
        )
        gr.Markdown(_load_static_runbooks())


def build_evidence_tab():
    with gr.Tab("Evidence"):
        gr.Markdown("## Current evidence provenance")
        gr.Markdown(_load_current_evidence())
        gr.Markdown("### Historical archive")
        gr.Markdown(_load_archive_index())
        with gr.Accordion("Named scenario references", open=False):
            gr.Markdown(
                "Inspecting a local manifest reference is not a replay or a new incident."
            )
            named = gr.Dropdown(
                choices=list(NAMED_REPLAYS),
                value=next(iter(NAMED_REPLAYS)),
                label="Scenario reference",
            )
            output = gr.Textbox(
                label="Read-only reference result", lines=2, interactive=False
            )
            gr.Button("Inspect reference").click(
                lambda selected: _apply_chaos(NAMED_REPLAYS[selected]),
                inputs=[named],
                outputs=[output],
            )


def build_settings_tab():
    with gr.Tab("Settings"):
        gr.Markdown("## Read-only demo boundary")
        gr.Markdown(
            "| Surface | State |\n|---|---|\n"
            "| Binding | Loopback only |\n"
            "| Evidence | Checked-in repository snapshot |\n"
            "| Runtime health / Kubernetes | Not queried |\n"
            "| Model loading or inference | Not performed |\n"
            "| Fault injection or kubectl | Not available |\n"
            "| Approval, remediation, or cleanup | No controls or actions |"
        )
        gr.Markdown(
            "G14 remains PARTIAL: a locally verifiable read-only UI is not evidence of a "
            "reproducible deployment or deployment acceptance."
        )


def build_app():
    with gr.Blocks(
        title="AtlasOps | Read-only evidence demo",
        analytics_enabled=False,
    ) as demo:
        gr.HTML(_header_html())
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
