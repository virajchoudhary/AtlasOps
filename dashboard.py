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
:root {
  --atlas-bg: #070b12;
  --atlas-bg-soft: #0c121d;
  --atlas-panel: rgba(15, 23, 36, 0.82);
  --atlas-panel-strong: rgba(19, 29, 44, 0.96);
  --atlas-line: rgba(148, 163, 184, 0.16);
  --atlas-line-strong: rgba(148, 163, 184, 0.28);
  --atlas-text: #f6f8fb;
  --atlas-muted: #94a3b8;
  --atlas-faint: #64748b;
  --atlas-green: #35d7ad;
  --atlas-green-soft: rgba(53, 215, 173, 0.13);
  --atlas-cyan: #67e8f9;
  --atlas-blue: #60a5fa;
  --atlas-red: #fb7185;
  --atlas-red-soft: rgba(251, 113, 133, 0.12);
  --atlas-amber: #fbbf24;
  --atlas-purple: #c084fc;
  --atlas-radius: 22px;
  --atlas-shadow: 0 18px 60px rgba(0, 0, 0, 0.34);
}

html { scroll-behavior: smooth; }
body,
.gradio-container {
  color: var(--atlas-text) !important;
  background:
    radial-gradient(circle at 12% 8%, rgba(53, 215, 173, 0.10), transparent 26rem),
    radial-gradient(circle at 88% 2%, rgba(96, 165, 250, 0.11), transparent 28rem),
    radial-gradient(circle at 52% 70%, rgba(192, 132, 252, 0.055), transparent 34rem),
    var(--atlas-bg) !important;
}

body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  opacity: .18;
  background-image:
    linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px);
  background-size: 42px 42px;
  mask-image: linear-gradient(to bottom, black, transparent 80%);
}

.gradio-container {
  max-width: 1460px !important;
  padding: 24px 30px 64px !important;
}

.gradio-container .contain,
.gradio-container .wrap {
  overflow: visible !important;
}

.atlas-hero {
  position: relative;
  overflow: hidden;
  min-height: 290px;
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(320px, .7fr);
  gap: 26px;
  align-items: end;
  padding: 34px;
  border: 1px solid var(--atlas-line);
  border-radius: 30px;
  background:
    linear-gradient(145deg, rgba(18, 28, 42, .96), rgba(7, 12, 21, .88)),
    var(--atlas-panel-strong);
  box-shadow: var(--atlas-shadow);
  isolation: isolate;
  animation: atlasRise .7s cubic-bezier(.2,.8,.2,1) both;
}

.atlas-hero::after {
  content: "";
  position: absolute;
  width: 520px;
  height: 520px;
  right: -160px;
  top: -280px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(53,215,173,.20), rgba(96,165,250,.06) 42%, transparent 68%);
  filter: blur(4px);
  z-index: -1;
  animation: atlasFloat 8s ease-in-out infinite alternate;
}

.atlas-eyebrow,
.atlas-kicker {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #9ff5dd;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .14em;
  text-transform: uppercase;
}

.atlas-live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--atlas-green);
  box-shadow: 0 0 0 0 rgba(53,215,173,.48);
  animation: atlasPulse 2.2s infinite;
}

.atlas-hero h1 {
  margin: 12px 0 12px;
  max-width: 820px;
  font-size: clamp(42px, 5vw, 72px);
  line-height: .98;
  letter-spacing: -.05em;
  font-weight: 800;
  color: #fff;
}

.atlas-gradient-text {
  background: linear-gradient(90deg, #f8fafc 6%, #9ff5dd 52%, #93c5fd 100%);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.atlas-hero p {
  max-width: 760px;
  margin: 0;
  color: #b7c2d3;
  font-size: 16px;
  line-height: 1.75;
}

.atlas-hero-side {
  display: grid;
  gap: 10px;
  align-self: stretch;
  align-content: end;
}

.atlas-mini {
  position: relative;
  overflow: hidden;
  padding: 15px 16px;
  border: 1px solid var(--atlas-line);
  border-radius: 16px;
  background: rgba(255,255,255,.035);
  backdrop-filter: blur(16px);
}

.atlas-mini::before {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(110deg, transparent 35%, rgba(255,255,255,.06) 50%, transparent 65%);
  transform: translateX(-120%);
  animation: atlasShimmer 7s ease-in-out infinite;
}

.atlas-mini span,
.atlas-metric span {
  display: block;
  color: var(--atlas-muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
}

.atlas-mini b {
  display: block;
  margin-top: 6px;
  color: #fff;
  font-size: 17px;
}

.atlas-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin: 18px 0 26px;
}

.atlas-metric {
  min-width: 0;
  position: relative;
  overflow: hidden;
  padding: 19px 18px 17px;
  border: 1px solid var(--atlas-line);
  border-radius: 20px;
  background: linear-gradient(145deg, rgba(18,28,42,.84), rgba(10,16,27,.88));
  box-shadow: 0 12px 35px rgba(0,0,0,.18);
  transition: transform .25s ease, border-color .25s ease, box-shadow .25s ease;
  animation: atlasRise .65s cubic-bezier(.2,.8,.2,1) both;
}

.atlas-metric:nth-child(2) { animation-delay: .06s; }
.atlas-metric:nth-child(3) { animation-delay: .12s; }
.atlas-metric:nth-child(4) { animation-delay: .18s; }

.atlas-metric:hover,
.atlas-agent:hover,
.atlas-result-card:hover {
  transform: translateY(-4px);
  border-color: rgba(103,232,249,.34);
  box-shadow: 0 20px 50px rgba(0,0,0,.28);
}

.atlas-metric b {
  display: block;
  margin-top: 9px;
  color: #fff;
  font-size: 24px;
  line-height: 1.15;
  overflow-wrap: anywhere;
}

.atlas-metric small {
  display: block;
  margin-top: 8px;
  color: var(--atlas-muted);
  font-size: 12px;
  line-height: 1.5;
}

.atlas-metric.good::after,
.atlas-metric.bad::after,
.atlas-metric.info::after,
.atlas-metric.warn::after {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
}
.atlas-metric.good::after { background: var(--atlas-green); }
.atlas-metric.bad::after { background: var(--atlas-red); }
.atlas-metric.info::after { background: var(--atlas-blue); }
.atlas-metric.warn::after { background: var(--atlas-amber); }

.atlas-section {
  margin: 22px 0 13px;
  padding: 0 2px;
}
.atlas-section h2 {
  margin: 6px 0 6px !important;
  font-size: clamp(24px, 2.3vw, 34px) !important;
  letter-spacing: -.035em;
  color: #f8fafc;
}
.atlas-section p {
  margin: 0;
  max-width: 850px;
  color: var(--atlas-muted);
  line-height: 1.7;
}

.atlas-panel {
  border: 1px solid var(--atlas-line) !important;
  border-radius: var(--atlas-radius) !important;
  background: linear-gradient(145deg, rgba(16,25,39,.86), rgba(10,16,27,.88)) !important;
  box-shadow: 0 16px 44px rgba(0,0,0,.20) !important;
  padding: 6px 10px !important;
}

.atlas-panel .prose,
.atlas-panel.prose {
  color: #d7deea !important;
}

.atlas-flow {
  position: relative;
  display: grid;
  grid-template-columns: repeat(7, minmax(110px,1fr));
  gap: 10px;
  align-items: stretch;
  margin: 10px 0 26px;
}

.atlas-flow::before {
  content: "";
  position: absolute;
  left: 6%;
  right: 6%;
  top: 50%;
  height: 2px;
  z-index: 0;
  background: linear-gradient(90deg, transparent, var(--atlas-green), var(--atlas-cyan), var(--atlas-blue), transparent);
  opacity: .45;
  background-size: 200% 100%;
  animation: atlasFlow 5s linear infinite;
}

.atlas-flow-node {
  position: relative;
  z-index: 1;
  min-height: 100px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 14px 12px;
  border: 1px solid var(--atlas-line);
  border-radius: 16px;
  text-align: center;
  background: rgba(10,16,27,.94);
  box-shadow: 0 10px 30px rgba(0,0,0,.18);
  transition: transform .22s ease, border-color .22s ease;
}

.atlas-flow-node:hover {
  transform: scale(1.035);
  border-color: rgba(53,215,173,.42);
}
.atlas-flow-node strong { font-size: 13px; color: #fff; }
.atlas-flow-node span { margin-top: 5px; font-size: 11px; color: var(--atlas-muted); }
.atlas-flow-node.guard { border-color: rgba(251,191,36,.30); }
.atlas-flow-node.verify { border-color: rgba(53,215,173,.30); }

.atlas-agent-grid,
.atlas-result-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0,1fr));
  gap: 14px;
  margin: 12px 0 24px;
}

.atlas-agent,
.atlas-result-card {
  position: relative;
  min-height: 178px;
  padding: 20px;
  border: 1px solid var(--atlas-line);
  border-radius: 20px;
  background: linear-gradient(145deg, rgba(18,28,42,.86), rgba(9,15,25,.92));
  transition: transform .25s ease, border-color .25s ease, box-shadow .25s ease;
}

.atlas-agent .icon {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  margin-bottom: 16px;
  border: 1px solid var(--atlas-line-strong);
  border-radius: 13px;
  background: rgba(255,255,255,.04);
  font-size: 20px;
}
.atlas-agent h3,
.atlas-result-card h3 { margin: 0 0 8px; color: #fff; font-size: 17px; }
.atlas-agent p,
.atlas-result-card p { margin: 0; color: var(--atlas-muted); font-size: 13px; line-height: 1.65; }

.atlas-pill-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.atlas-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  border: 1px solid var(--atlas-line);
  border-radius: 999px;
  background: rgba(255,255,255,.025);
  color: #b9c4d3;
  font-size: 11px;
  font-weight: 700;
}
.atlas-pill.green { color: #9ff5dd; border-color: rgba(53,215,173,.25); background: var(--atlas-green-soft); }
.atlas-pill.red { color: #fecdd3; border-color: rgba(251,113,133,.25); background: var(--atlas-red-soft); }
.atlas-pill.blue { color: #bfdbfe; border-color: rgba(96,165,250,.24); background: rgba(96,165,250,.08); }

.atlas-chart {
  display: grid;
  grid-template-columns: 1fr;
  gap: 13px;
  padding: 19px;
  border: 1px solid var(--atlas-line);
  border-radius: 20px;
  background: rgba(9,15,25,.72);
}
.atlas-bar-row {
  display: grid;
  grid-template-columns: 95px 1fr 76px;
  gap: 12px;
  align-items: center;
}
.atlas-bar-row label { color: #d7deea; font-size: 12px; font-weight: 700; }
.atlas-bar-track {
  height: 10px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148,163,184,.10);
}
.atlas-bar-fill {
  height: 100%;
  border-radius: inherit;
  transform-origin: left;
  animation: atlasBar 1.1s cubic-bezier(.2,.8,.2,1) both;
}
.atlas-bar-fill.base { background: linear-gradient(90deg, var(--atlas-blue), var(--atlas-cyan)); }
.atlas-bar-fill.sft { background: linear-gradient(90deg, var(--atlas-purple), #f0abfc); animation-delay: .12s; }
.atlas-bar-row b { text-align: right; font-size: 12px; color: #f8fafc; }

.atlas-callout {
  position: relative;
  margin: 12px 0 18px;
  padding: 17px 18px 17px 20px;
  border: 1px solid var(--atlas-line);
  border-radius: 18px;
  background: rgba(255,255,255,.025);
  color: #cbd5e1;
  line-height: 1.65;
}
.atlas-callout::before {
  content: "";
  position: absolute;
  left: 0;
  top: 15px;
  bottom: 15px;
  width: 3px;
  border-radius: 3px;
  background: linear-gradient(var(--atlas-green), var(--atlas-cyan));
}

.atlas-safety {
  display: grid;
  grid-template-columns: repeat(3,minmax(0,1fr));
  gap: 12px;
  margin: 12px 0 20px;
}
.atlas-safety > div {
  padding: 16px;
  border: 1px solid var(--atlas-line);
  border-radius: 16px;
  background: rgba(255,255,255,.025);
}
.atlas-safety b { display: block; color: #fff; margin-bottom: 5px; }
.atlas-safety span { color: var(--atlas-muted); font-size: 12px; line-height: 1.55; }

.gradio-container .tab-nav {
  position: sticky;
  top: 10px;
  z-index: 30;
  gap: 5px !important;
  margin: 20px 0 18px !important;
  padding: 6px !important;
  border: 1px solid var(--atlas-line) !important;
  border-radius: 16px !important;
  background: rgba(8,13,22,.82) !important;
  backdrop-filter: blur(20px);
  box-shadow: 0 14px 38px rgba(0,0,0,.22);
}
.gradio-container .tab-nav button {
  min-height: 38px !important;
  padding: 7px 13px !important;
  border: 0 !important;
  border-radius: 11px !important;
  color: var(--atlas-muted) !important;
  font-size: 12px !important;
  font-weight: 750 !important;
  transition: background .2s ease, color .2s ease, transform .2s ease !important;
}
.gradio-container .tab-nav button:hover {
  color: #fff !important;
  background: rgba(255,255,255,.045) !important;
  transform: translateY(-1px);
}
.gradio-container .tab-nav button.selected {
  color: #06110e !important;
  background: linear-gradient(135deg, #9ff5dd, #67e8f9) !important;
  box-shadow: 0 8px 22px rgba(53,215,173,.18) !important;
}

.gradio-container .prose { color: #d3dbe7 !important; }
.gradio-container .prose h1,
.gradio-container .prose h2,
.gradio-container .prose h3 { color: #f8fafc !important; letter-spacing: -.02em; }
.gradio-container .prose a { color: var(--atlas-cyan) !important; }
.gradio-container .prose code {
  color: #b7f7e5 !important;
  background: rgba(53,215,173,.075) !important;
  border: 1px solid rgba(53,215,173,.13);
  border-radius: 6px;
  padding: 2px 5px;
}
.gradio-container table {
  overflow: hidden;
  border: 1px solid var(--atlas-line) !important;
  border-radius: 14px !important;
  background: rgba(8,13,22,.48) !important;
}
.gradio-container table th {
  color: #e2e8f0 !important;
  background: rgba(255,255,255,.035) !important;
}
.gradio-container table td { color: #b9c4d3 !important; border-color: var(--atlas-line) !important; }

.gradio-container button:not(.tab-nav button) {
  border: 1px solid var(--atlas-line-strong) !important;
  border-radius: 12px !important;
  background: linear-gradient(145deg, rgba(28,40,58,.96), rgba(14,23,36,.96)) !important;
  color: #e8edf5 !important;
  font-weight: 700 !important;
  transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease !important;
}
.gradio-container button:not(.tab-nav button):hover {
  transform: translateY(-2px);
  border-color: rgba(103,232,249,.38) !important;
  box-shadow: 0 12px 26px rgba(0,0,0,.24) !important;
}

.gradio-container input,
.gradio-container textarea,
.gradio-container select {
  border-color: var(--atlas-line-strong) !important;
  border-radius: 12px !important;
  background: rgba(6,10,18,.70) !important;
  color: #edf2f7 !important;
}

.gradio-container .accordion {
  border-color: var(--atlas-line) !important;
  border-radius: 16px !important;
  background: rgba(255,255,255,.02) !important;
}

@keyframes atlasRise {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes atlasPulse {
  0% { box-shadow: 0 0 0 0 rgba(53,215,173,.42); }
  70% { box-shadow: 0 0 0 10px rgba(53,215,173,0); }
  100% { box-shadow: 0 0 0 0 rgba(53,215,173,0); }
}
@keyframes atlasFloat {
  from { transform: translate3d(0,0,0) scale(1); }
  to { transform: translate3d(-35px,28px,0) scale(1.08); }
}
@keyframes atlasShimmer {
  0%, 72% { transform: translateX(-120%); }
  90%, 100% { transform: translateX(120%); }
}
@keyframes atlasFlow {
  from { background-position: 0 0; }
  to { background-position: 200% 0; }
}
@keyframes atlasBar {
  from { transform: scaleX(0); opacity: .35; }
  to { transform: scaleX(1); opacity: 1; }
}

@media (max-width: 1050px) {
  .atlas-hero { grid-template-columns: 1fr; min-height: unset; }
  .atlas-hero-side { grid-template-columns: repeat(3,minmax(0,1fr)); }
  .atlas-metric-grid, .atlas-agent-grid, .atlas-result-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .atlas-flow { grid-template-columns: repeat(4,minmax(120px,1fr)); }
  .atlas-flow::before { display: none; }
}
@media (max-width: 720px) {
  .gradio-container { padding: 12px 12px 38px !important; }
  .atlas-hero { padding: 24px 20px; border-radius: 23px; }
  .atlas-hero h1 { font-size: 42px; }
  .atlas-hero-side, .atlas-metric-grid, .atlas-agent-grid, .atlas-result-grid,
  .atlas-safety { grid-template-columns: 1fr; }
  .atlas-flow { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .gradio-container .tab-nav { position: static; overflow-x: auto; flex-wrap: nowrap; }
  .atlas-bar-row { grid-template-columns: 75px 1fr 62px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .001ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: .001ms !important;
  }
}
"""


def _safe_html(value) -> str:
    return html.escape(_format_value(value))


def _section_html(kicker: str, title: str, description: str) -> str:
    return (
        '<div class="atlas-section">'
        f'<span class="atlas-kicker">{html.escape(kicker)}</span>'
        f'<h2>{html.escape(title)}</h2>'
        f'<p>{html.escape(description)}</p>'
        '</div>'
    )


def _header_html() -> str:
    result = current_results()
    validation = result["base_vs_sft"]
    sft = result["sft_v17"]
    pilot = result["g9_pilot"]
    aligned = result["g9_aligned_diagnostic"]
    return (
        '<div class="atlas-hero">'
        '<div>'
        '<div class="atlas-eyebrow"><span class="atlas-live-dot"></span>'
        'READ-ONLY RESEARCH SNAPSHOT</div>'
        '<h1>Incident intelligence, <span class="atlas-gradient-text">governed by evidence.</span></h1>'
        '<p>AtlasOps is a multi-agent SRE research system built around explicit safety policy, '
        'human approval and objective environment verification. This interface is intentionally '
        'read only and foregrounds the results that actually exist.</p>'
        '<div class="atlas-pill-row">'
        '<span class="atlas-pill green">LOCAL EVIDENCE DEMO</span>'
        '<span class="atlas-pill blue">24 TOOL WRAPPERS</span>'
        '<span class="atlas-pill blue">28 FROZEN SCENARIOS</span>'
        '<span class="atlas-pill red">NOT CERTIFIED</span>'
        '</div>'
        '</div>'
        '<div class="atlas-hero-side">'
        '<div class="atlas-mini"><span>SFT artifact</span>'
        f'<b>{html.escape(str(sft["status"]))}</b></div>'
        '<div class="atlas-mini"><span>Base vs SFT delta</span>'
        f'<b>{html.escape(_format_value(validation["paired_delta"], 5))}</b></div>'
        '<div class="atlas-mini"><span>Final aligned G9</span>'
        f'<b>{_safe_html(aligned["admissible_count"])}/{_safe_html(aligned["sample_count"])} admissible</b></div>'
        '</div>'
        '</div>'
        '<div class="atlas-metric-grid">'
        '<div class="atlas-metric bad"><span>G4 governance</span><b>NOT_PASSED</b>'
        '<small>015 inconclusive · 016 pre-fault abort · 017 completed negative</small></div>'
        '<div class="atlas-metric good"><span>SFT v17</span>'
        f'<b>{_safe_html(sft["training_steps"])} steps</b>'
        f'<small>{_safe_html(sft["reload_tensor_count"])} LoRA tensors independently checked</small></div>'
        '<div class="atlas-metric info"><span>Validation</span>'
        f'<b>{html.escape(_format_value(validation["base_f1"], 5))} → '
        f'{html.escape(_format_value(validation["sft_f1"], 5))}</b>'
        '<small>Base to SFT diagnostic F1 · schema 6/6 in both arms</small></div>'
        '<div class="atlas-metric warn"><span>Controlled G9</span>'
        f'<b>{_safe_html(pilot["optimizer_steps"])} steps</b>'
        f'<small>{_safe_html(pilot["malformed_or_blocked"])}/{_safe_html(pilot["completions"])} '
        'completions malformed or blocked · final negative</small></div>'
        '</div>'
    )


def _flow_html() -> str:
    nodes = [
        ("01", "Alert", "Observed incident"),
        ("02", "Triage", "Scope + severity"),
        ("03", "Diagnosis", "Evidence-grounded cause"),
        ("04", "Approval", "Policy + human gate"),
        ("05", "Remediation", "One governed action"),
        ("06", "Verifier", "Objective outcome"),
        ("07", "Comms", "Recorded conclusion"),
    ]
    rendered = []
    for index, title, note in nodes:
        class_name = "atlas-flow-node"
        if title == "Approval":
            class_name += " guard"
        if title == "Verifier":
            class_name += " verify"
        rendered.append(
            f'<div class="{class_name}"><span>{index}</span><strong>{title}</strong>'
            f'<span>{note}</span></div>'
        )
    return '<div class="atlas-flow">' + "".join(rendered) + '</div>'


def _comparison_html() -> str:
    result = current_results()["base_vs_sft"]
    base = float(result["base_f1"])
    sft = float(result["sft_f1"])
    scale = max(base, sft, 0.25)
    base_width = max(4.0, min(100.0, base / scale * 100.0))
    sft_width = max(4.0, min(100.0, sft / scale * 100.0))
    delta = _format_value(result["paired_delta"], 5)
    return (
        '<div class="atlas-chart">'
        '<div class="atlas-bar-row"><label>Base</label><div class="atlas-bar-track">'
        f'<div class="atlas-bar-fill base" style="width:{base_width:.1f}%"></div></div>'
        f'<b>{base:.5f}</b></div>'
        '<div class="atlas-bar-row"><label>SFT v17</label><div class="atlas-bar-track">'
        f'<div class="atlas-bar-fill sft" style="width:{sft_width:.1f}%"></div></div>'
        f'<b>{sft:.5f}</b></div>'
        '<div class="atlas-callout">'
        f'Paired delta (SFT − Base): <strong>{html.escape(delta)}</strong>. '
        'The measured diagnostic did not improve after SFT. This is a six-scenario '
        'Validation diagnostic, not an incident-resolution benchmark.'
        '</div>'
        '</div>'
    )


def _agents_html() -> str:
    cards = [
        ("◎", "Triage", "Classifies severity and affected services before any remediation path begins.",
         "Read / classify"),
        ("⌁", "Diagnosis", "Connects observed telemetry and incident evidence into a grounded root-cause proposal.",
         "Evidence first"),
        ("↗", "Remediation", "Submits policy-allowed actions only after the required safety and approval checks.",
         "Guarded action"),
        ("✦", "Comms", "Records incident status and outcome after verification rather than inventing success.",
         "Verified summary"),
    ]
    parts = []
    for icon, title, copy, badge in cards:
        parts.append(
            '<div class="atlas-agent">'
            f'<div class="icon">{html.escape(icon)}</div>'
            f'<h3>{html.escape(title)} Agent</h3><p>{html.escape(copy)}</p>'
            f'<div class="atlas-pill-row"><span class="atlas-pill green">{html.escape(badge)}</span></div>'
            '</div>'
        )
    return '<div class="atlas-agent-grid">' + "".join(parts) + '</div>'


def _safety_html() -> str:
    return (
        '<div class="atlas-safety">'
        '<div><b>Role ACL</b><span>Agents see only the tool subset their role is allowed to use.</span></div>'
        '<div><b>P1 approval</b><span>Rejection, timeout or a missing decision blocks mutation.</span></div>'
        '<div><b>Objective verifier</b><span>Environment evidence, not model confidence, controls resolution truth.</span></div>'
        '</div>'
    )


def _results_cards_html() -> str:
    result = current_results()
    sft = result["sft_v17"]
    validation = result["base_vs_sft"]
    pilot = result["g9_pilot"]
    aligned = result["g9_aligned_diagnostic"]
    return (
        '<div class="atlas-result-grid">'
        '<div class="atlas-result-card"><span class="atlas-kicker">MODEL ARTIFACT</span>'
        '<h3>SFT v17</h3>'
        f'<p>{_safe_html(sft["training_steps"])} training steps across {_safe_html(sft["corpus_rows"])} '
        f'synthetic Train-only rows. {_safe_html(sft["reload_tensor_count"])} LoRA tensors checked on reload.</p>'
        '<div class="atlas-pill-row"><span class="atlas-pill green">RELOADABLE</span></div></div>'
        '<div class="atlas-result-card"><span class="atlas-kicker">VALIDATION</span>'
        '<h3>Base vs SFT</h3>'
        f'<p>F1 {_safe_html(validation["base_f1"])} vs {_safe_html(validation["sft_f1"])}. '
        f'Paired delta {_safe_html(validation["paired_delta"])}. No diagnostic improvement observed.</p>'
        '<div class="atlas-pill-row"><span class="atlas-pill blue">6 / 6 SCHEMA BOTH</span></div></div>'
        '<div class="atlas-result-card"><span class="atlas-kicker">CONTROLLED RL</span>'
        '<h3>G9 pilot</h3>'
        f'<p>{_safe_html(pilot["completions"])} genuine completions, '
        f'{_safe_html(pilot["malformed_or_blocked"])} blocked, reward {_safe_html(pilot["reward_each"])} each. '
        'No reward-driven checkpoint.</p>'
        '<div class="atlas-pill-row"><span class="atlas-pill red">FINAL NEGATIVE</span></div></div>'
        '<div class="atlas-result-card"><span class="atlas-kicker">ALIGNED DIAGNOSTIC</span>'
        '<h3>0 / 8 admissible</h3>'
        f'<p>{_safe_html(aligned["tensor_hash_count"])} LoRA tensor hashes remained unchanged with '
        f'{_safe_html(aligned["optimizer_steps"])} optimizer steps.</p>'
        '<div class="atlas-pill-row"><span class="atlas-pill red">NO ACCEPTED GRPO ARM</span></div></div>'
        '</div>'
    )


def build_overview_tab():
    with gr.Tab("Overview"):
        gr.HTML(_section_html(
            "PROJECT SNAPSHOT",
            "The system at a glance",
            "A presentation-first view of the architecture, measured results and current research boundary.",
        ))
        gr.HTML(_results_cards_html())
        gr.HTML(_section_html(
            "PIPELINE",
            "Governed incident flow",
            "Every remediation path passes through safety controls and ends in objective verification.",
        ))
        gr.HTML(_flow_html())
        gr.HTML(_section_html(
            "MEASURED SIGNAL",
            "Base vs SFT Validation",
            "The real matched diagnostic result is foregrounded instead of historical or mock benchmark numbers.",
        ))
        gr.HTML(_comparison_html())
        with gr.Accordion("Gate inventory and detailed qualifiers", open=False):
            status_out = gr.Markdown(_load_project_status())
            gr.Button("Refresh repository snapshot").click(_load_project_status, outputs=[status_out])


def build_incidents_tab():
    with gr.Tab("Incidents"):
        gr.HTML(_section_html(
            "FROZEN LIVE TRACK",
            "G4 incident chronology",
            "Final dispositions are preserved as evidence. This UI never retries, mutates or infers success.",
        ))
        gr.HTML(
            '<div class="atlas-callout"><strong>Current governance:</strong> G4 is frozen NOT_PASSED. '
            'Attempt 015 is inconclusive/unscored, 016 is a pre-fault non-result, and 017 is a completed negative.</div>'
        )
        current_out = gr.Markdown(_load_g4_final_chronology(), elem_classes=["atlas-panel"])
        gr.Button("Refresh G4 references").click(_load_g4_final_chronology, outputs=[current_out])
        with gr.Accordion("Earlier preserved G4 records", open=False):
            gr.Markdown(
                "These records are historical context only. They do not replace the 015/016/017 chronology."
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
            summary_out = gr.Markdown(summary, elem_classes=["atlas-panel"])
            provenance_out = gr.Markdown(source)
            incident_list.change(
                _load_stage4_attempt, inputs=[incident_list], outputs=[summary_out, provenance_out]
            )
            refresh_btn.click(
                lambda: gr.update(choices=_list_stage4_attempts()), outputs=[incident_list]
            )
        with gr.Accordion("Scenario manifest references", open=False):
            gr.Markdown(
                "Selection only checks for a known local manifest. It does not inject a fault, run agents, call kubectl, or change a cluster."
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
        gr.HTML(_section_html(
            "AGENT SYSTEM",
            "Four roles, one governed workflow",
            "The model can propose; policy, approval and environment evidence decide what counts.",
        ))
        gr.HTML(_agents_html())
        gr.HTML(_section_html(
            "GUARDRAILS",
            "Safety is a runtime boundary",
            "The read-only demo exposes the architecture without exposing mutation controls.",
        ))
        gr.HTML(_safety_html())
        gr.HTML(_flow_html())


def build_models_tab():
    with gr.Tab("Models"):
        result = current_results()["sft_v17"]
        gr.HTML(_section_html(
            "MODEL LINEAGE",
            "The artifact that actually exists",
            "A genuine QLoRA SFT adapter is preserved and reloadable; no incident-improvement claim is attached to it.",
        ))
        gr.HTML(
            '<div class="atlas-result-grid">'
            '<div class="atlas-result-card"><span class="atlas-kicker">BASE</span>'
            f'<h3>{html.escape(str(result["model"]))}</h3>'
            f'<p>Immutable revision {_safe_html(result["model_revision"])}. This demo performs no model loading or inference.</p>'
            '<div class="atlas-pill-row"><span class="atlas-pill blue">PINNED</span></div></div>'
            '<div class="atlas-result-card"><span class="atlas-kicker">SFT CHECKPOINT</span>'
            f'<h3>{_safe_html(result["status"])}</h3>'
            f'<p>Run {_safe_html(result["run_id"])} · {_safe_html(result["training_steps"])} optimizer steps · '
            f'{_safe_html(result["corpus_rows"])} synthetic Train-only rows.</p>'
            '<div class="atlas-pill-row"><span class="atlas-pill green">GENUINE ARTIFACT</span></div></div>'
            '<div class="atlas-result-card"><span class="atlas-kicker">RELOAD</span>'
            f'<h3>{_safe_html(result["reload_status"])}</h3>'
            f'<p>{_safe_html(result["reload_tensor_count"])} LoRA tensors independently checked in a fresh-process reload.</p>'
            '<div class="atlas-pill-row"><span class="atlas-pill green">VERIFIED</span></div></div>'
            '<div class="atlas-result-card"><span class="atlas-kicker">GRPO ARM</span>'
            '<h3>Not accepted</h3>'
            '<p>The controlled research track ended negatively. No SFT+GRPO checkpoint is presented as a model artifact.</p>'
            '<div class="atlas-pill-row"><span class="atlas-pill red">ABSENT BY EVIDENCE</span></div></div>'
            '</div>'
        )
        with gr.Accordion("Artifact provenance details", open=False):
            gr.Markdown(
                f"- Status: **{result['status']}**.\n"
                f"- Run: {_format_value(result['run_id'])}.\n"
                f"- Base model: {_format_value(result['model'])}@{_format_value(result['model_revision'])}.\n"
                f"- Corpus: {_format_value(result['corpus_rows'])} {_format_value(result['corpus_split'])}-split rows; "
                f"synthetic: {_format_value(result['synthetic_corpus'])}.\n"
                f"- Completed training steps: {_format_value(result['training_steps'])}.\n"
                f"- Adapter SHA-256: {_format_value(result['adapter_sha256'])}.\n"
                f"- Fresh-process independent reload: {_format_value(result['reload_status'])}; "
                f"{_format_value(result['reload_tensor_count'])} LoRA tensors checked."
            )


def build_evaluations_tab():
    with gr.Tab("Evaluations"):
        gr.HTML(_section_html(
            "EMPIRICAL RESULTS",
            "What was actually measured",
            "Current evidence is separated from inherited upstream claims and historical mock profiles.",
        ))
        gr.HTML(_results_cards_html())
        gr.HTML(_comparison_html())
        with gr.Accordion("Detailed evidence-backed result summary", open=True):
            result_out = gr.Markdown(_load_current_results(), elem_classes=["atlas-panel"])
            gr.Button("Refresh result summaries").click(_load_current_results, outputs=[result_out])
        with gr.Accordion("Current gate statuses", open=False):
            status_out = gr.Markdown(_load_project_status())
            gr.Button("Refresh gate snapshot").click(_load_project_status, outputs=[status_out])
        with gr.Accordion(
            "Historical archive · non-empirical and excluded from current results", open=False
        ):
            gr.Markdown(_load_archive_index())


def build_runbooks_tab():
    with gr.Tab("Runbooks"):
        gr.HTML(_section_html(
            "REFERENCE CATALOG",
            "Runbooks without hidden automation",
            "Optional recommender research is out of the required GAI+RL scope. This view is a static read-only catalog.",
        ))
        gr.HTML(
            '<div class="atlas-callout">No ranking, fitting, model inference or remediation is performed here. '
            'Suggested tools are reference material only.</div>'
        )
        gr.Markdown(_load_static_runbooks(), elem_classes=["atlas-panel"])


def build_evidence_tab():
    with gr.Tab("Evidence"):
        gr.HTML(_section_html(
            "PROVENANCE",
            "Trace every claim back to evidence",
            "Current evidence stays foregrounded; unavailable or external raw records remain explicitly unavailable.",
        ))
        gr.HTML(
            '<div class="atlas-safety">'
            '<div><b>Immutable references</b><span>Tracked evidence is surfaced by path and SHA-256.</span></div>'
            '<div><b>Negative results preserved</b><span>Failed, blocked and inconclusive outcomes are not rewritten.</span></div>'
            '<div><b>Historical separation</b><span>Mock and upstream artifacts are kept outside current measurements.</span></div>'
            '</div>'
        )
        gr.Markdown(_load_current_evidence(), elem_classes=["atlas-panel"])
        with gr.Accordion("Historical archive", open=False):
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
        gr.HTML(_section_html(
            "DEMO BOUNDARY",
            "Designed to be safe by construction",
            "This interface is a local evidence browser, not an operations console.",
        ))
        gr.HTML(
            '<div class="atlas-safety">'
            '<div><b>Loopback only</b><span>The launcher rejects non-loopback hosts and public Gradio sharing.</span></div>'
            '<div><b>No execution surface</b><span>No kubectl, fault injection, approval mutation, remediation or cleanup controls.</span></div>'
            '<div><b>No model runtime</b><span>No checkpoint loading, model inference or external provider is required.</span></div>'
            '</div>'
        )
        gr.Markdown(
            "| Surface | State |\n|---|---|\n"
            "| Binding | Loopback only |\n"
            "| Evidence | Checked-in repository snapshot |\n"
            "| Runtime health / Kubernetes | Not queried |\n"
            "| Model loading or inference | Not performed |\n"
            "| Fault injection or kubectl | Not available |\n"
            "| Approval, remediation, or cleanup | No controls or actions |",
            elem_classes=["atlas-panel"],
        )
        gr.HTML(
            '<div class="atlas-callout"><strong>G14 remains PARTIAL.</strong> '
            'A polished, locally verifiable read-only UI is not evidence of public deployment or operator acceptance.</div>'
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
