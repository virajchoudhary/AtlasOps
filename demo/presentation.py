"""Presentation-only HTML over the unchanged, read-only evidence projection."""

from __future__ import annotations

import html
import json
from pathlib import Path

from config.scenario_catalog import SCENARIO_CATALOG
from ui_read_model import catalog, current_results, final_g4_attempts, gates, historical_archive

ROOT = Path(__file__).resolve().parent
CSS = (ROOT / "presentation.css").read_text(encoding="utf-8")
ICONS = json.loads((ROOT / "icons.json").read_text(encoding="utf-8"))


def esc(value: object) -> str:
    return html.escape("Unavailable" if value is None else str(value), quote=True)


def icon(name: str) -> str:
    return f'<span class="ao-icon" aria-hidden="true">{ICONS[name]}</span>'


def chip(label: object, tone: str = "neutral") -> str:
    return f'<span class="ao-chip {tone}">{esc(label)}</span>'


def number(value: object, digits: int | None = None) -> str:
    if isinstance(value, float) and digits is not None:
        return f"{value:.{digits}f}"
    return esc(value)


def page(title: str, subtitle: str, body: str, label: str = "RESEARCH SNAPSHOT") -> str:
    return (
        '<section class="ao-page">'
        f'<header class="ao-page-heading"><span class="ao-eyebrow">{esc(label)}</span>'
        f'<h2>{esc(title)}</h2><p>{esc(subtitle)}</p></header>{body}</section>'
    )


def notice(title: str, text: str, tone: str = "amber") -> str:
    return (
        f'<aside class="ao-notice {tone}">{icon("shield-check")}'
        f'<div><strong>{esc(title)}</strong><p>{esc(text)}</p></div></aside>'
    )


def fact(label: str, value: object, note: str = "", tone: str = "") -> str:
    return (
        f'<div class="ao-fact {tone}"><span>{esc(label)}</span>'
        f'<strong>{esc(value)}</strong><small>{esc(note)}</small></div>'
    )


def provenance(items: list[dict]) -> str:
    return '<div class="ao-evidence-grid">' + "".join(evidence_record(i) for i in items) + "</div>"


def evidence_record(item: dict) -> str:
    digest = item.get("sha256")
    availability = "Present in checkout" if item.get("available") else "Unavailable in checkout"
    return (
        '<details class="ao-evidence-record">'
        f'<summary>{icon("file-check")}<span class="ao-record-name">'
        f'<strong>{esc(item["title"])}</strong><small>{esc(item["classification"])}</small>'
        f'</span><span class="ao-record-state">{chip(availability, "green" if digest else "amber")}'
        f'<code>{esc(digest[:12] + "..." if digest else None)}</code></span>'
        f'{icon("chevron-down")}</summary><div class="ao-record-detail">'
        f'<span class="ao-eyebrow">SOURCE PATH</span><code>{esc(item["path"])}</code>'
        f'<span class="ao-eyebrow">SHA-256 / CHECKOUT BYTES</span><code>{esc(digest)}</code>'
        '<p>Repository evidence only. No model or operational action is executed.</p>'
        '</div></details>'
    )


def header() -> str:
    return (
        '<header class="ao-topbar"><div class="ao-brand">'
        f'{icon("workflow")}<strong>AtlasOps</strong><span>Research console</span></div>'
        '<div class="ao-top-status"><span class="ao-local-dot"></span>'
        'Local snapshot<span class="ao-divider"></span>'
        f'{icon("lock-keyhole")}Read-only</div></header>'
    )


def workflow() -> str:
    stages = [
        ("bell-ring", "Alert", "Incident signal", "input"),
        ("scan-line", "Triage", "Scope & severity", "agent"),
        ("search", "Diagnosis", "Grounded cause", "agent"),
        ("shield-check", "Approval", "Human safety gate", "gate"),
        ("wrench", "Remediation", "Policy-bound action", "agent"),
        ("check-check", "Verifier", "Objective state", "gate"),
        ("messages-square", "Comms", "Verified outcome", "agent"),
    ]
    nodes = "".join(
        f'<div class="ao-flow-node {kind}">{icon(name)}<strong>{title}</strong>'
        f'<small>{subtitle}</small><span class="ao-node-kind">'
        f'{"AGENT" if kind == "agent" else "CONTROL" if kind == "gate" else "INPUT"}'
        '</span></div>'
        for name, title, subtitle, kind in stages
    )
    return (
        '<section class="ao-workflow"><div class="ao-section-head">'
        '<div><span class="ao-eyebrow">GOVERNED BY DESIGN</span>'
        '<h3>One incident. Four agents. Explicit accountability.</h3></div>'
        f'{chip("Architecture / not a live run")}</div>'
        f'<div class="ao-flow">{nodes}</div>'
        '<div class="ao-control-strip">'
        f'<span>{icon("key-round")}Role ACLs</span>'
        f'<span>{icon("shield-check")}Explicit P1 approval</span>'
        f'<span>{icon("check-check")}Environment-authoritative verdict</span>'
        f'<span>{icon("fingerprint")}Evidence provenance</span></div></section>'
    )


def overview() -> str:
    results = current_results()
    sft, validation, pilot = (
        results["sft_v17"], results["base_vs_sft"], results["g9_pilot"]
    )
    delta = number(validation["paired_delta"], 5)
    artifact_state = "Reload verified" if sft["available"] else "Unavailable"
    g4_state = next(row["status"] for row in gates() if row["gate"] == "G4")
    g9_state = "Final negative" if pilot["available"] else "Unavailable"
    return (
        '<section class="ao-page ao-overview"><div class="ao-hero">'
        '<span class="ao-eyebrow">GENERATIVE AI + REINFORCEMENT LEARNING</span>'
        '<h1>AtlasOps</h1><h2>Governed Multi-Agent SRE Intelligence</h2>'
        '<p>From incident signal to verified outcome. Four specialized agents, '
        'human approval, and an evidence trail that keeps claims accountable.</p>'
        '<div class="ao-chip-row">'
        f'{chip("READ-ONLY DEMO", "green")}{chip("GAI + RL")}'
        f'{chip("24 TOOL WRAPPERS")}{chip(f"{len(SCENARIO_CATALOG)} FROZEN SCENARIOS")}'
        f'{chip("NOT CERTIFIED", "amber")}</div></div>'
        '<div class="ao-result-row">'
        f'{fact("SFT v17 / artifact", artifact_state, "Artifact creation, not performance", "green")}'
        f'{fact("Base vs SFT / diagnostic F1", delta, "Paired delta / no improvement", "rose")}'
        f'{fact("G4 / incident-resolution gate", g4_state, "Final chronology: 015 > 016 > 017", "amber")}'
        f'{fact("Controlled G9 / GRPO", g9_state, "No accepted checkpoint", "rose")}</div>'
        f'{workflow()}'
        '<div class="ao-overview-bottom"><section><span class="ao-eyebrow">WHAT EXISTS</span>'
        '<h3>Implementation with traceable evidence</h3><p>Multi-agent orchestration, '
        'role-scoped tools, fail-closed approval, objective verification, and a '
        'preserved QLoRA SFT artifact.</p></section><section>'
        '<span class="ao-eyebrow">WHAT THE EVIDENCE DOES NOT ESTABLISH</span>'
        '<h3>Incident gains and an accepted RL policy</h3><p>The matched diagnostic '
        'shows no improvement. G4 and G9 remain NOT_PASSED. '
        'Final empirical evaluation and deployment acceptance remain deferred.</p></section></div>'
        '<footer class="ao-footnote">Checked-in research evidence, not live telemetry. '
        'Software delivery and scientific gate closure are separate.</footer></section>'
    )


def models() -> str:
    results = current_results()
    result = results["sft_v17"]
    pilot = results["g9_pilot"]
    state = "Reload verified" if result["available"] else "Unavailable"
    model = result["model"]
    lineage = [
        ("01", "Base Qwen", model, "BASE MODEL", "neutral"),
        ("02", "QLoRA SFT v17", state, "PRESERVED ARTIFACT", "green"),
        ("03", "Controlled GRPO attempt", "Train-only bounded simulator research",
         "EXPERIMENTALLY UNSUCCESSFUL" if pilot["available"] else "UNAVAILABLE", "amber"),
        ("04", "No accepted GRPO checkpoint" if pilot["available"] else "GRPO status unavailable",
         "No SFT + GRPO arm for final evaluation" if pilot["available"] else "Canonical report unavailable",
         pilot["status"], "rose"),
    ]
    lineage_html = "".join(
        f'<li class="{tone}"><span class="ao-lineage-number">{step}</span><div>'
        f'<h3>{title}</h3><p>{esc(description)}</p>{chip(label, tone)}</div></li>'
        for step, title, description, label, tone in lineage
    )
    body = (
        '<div class="ao-model-layout"><section class="ao-lineage-section">'
        '<span class="ao-eyebrow">MODEL LINEAGE</span>'
        f'<ol class="ao-lineage">{lineage_html}</ol></section>'
        '<section class="ao-artifact"><div class="ao-section-head"><div>'
        '<span class="ao-eyebrow">ARTIFACT ACCEPTANCE</span><h3>SFT v17</h3></div>'
        f'{chip(result["status"], "green" if result["available"] else "amber")}</div>'
        f'<p>{"A real QLoRA adapter with independent fresh-process reload evidence." if result["available"] else "Canonical artifact or reload evidence unavailable."}</p>'
        '<div class="ao-artifact-facts">'
        f'{fact("Synthetic corpus", result["corpus_rows"], "Train-only rows")}'
        f'{fact("Training", result["training_steps"], "Optimizer steps")}'
        f'{fact("Independent reload", result["reload_tensor_count"], "LoRA tensors verified")}'
        '</div><dl class="ao-metadata">'
        f'<dt>Base model</dt><dd>{esc(model)}</dd>'
        f'<dt>Revision</dt><dd><code>{esc(result["model_revision"])}</code></dd>'
        f'<dt>Run</dt><dd><code>{esc(result["run_id"])}</code></dd>'
        f'<dt>Adapter SHA-256</dt><dd><code>{esc(result["adapter_sha256"])}</code></dd>'
        '</dl></section></div>'
        + notice("Artifact success is not diagnostic improvement",
                 "Artifact acceptance and measured performance are separate. The paired Validation "
                 "result does not establish improved diagnosis or incident resolution.")
        + provenance(result["evidence"])
    )
    return page("Models & lineage", "A preserved SFT artifact. No accepted GRPO checkpoint.", body)


def comparison_chart(validation: dict) -> str:
    rows = []
    for name, key, tone in [("Base", "base", "base"), ("SFT v17", "sft", "sft")]:
        value = validation[f"{key}_f1"]
        # Shared, fixed 0..1 F1 scale; missing values never become a zero bar.
        width = max(0, min(100, value * 100)) if value is not None else 0
        schema = f'{number(validation[f"{key}_schema_valid"])}/{number(validation[f"{key}_schema_total"])}'
        rows.append(
            f'<div class="ao-chart-row {tone}"><div class="ao-chart-label">'
            f'<strong>{name}</strong><span>Schema {schema}</span></div>'
            f'<div class="ao-bar-track"><div class="ao-bar" style="--bar:{width}%"></div>'
            f'</div><strong class="ao-chart-value">{number(value, 5)}</strong></div>'
        )
    return (
        '<figure class="ao-comparison" aria-label="Diagnostic F1 comparison on a fixed zero to one scale">'
        '<div class="ao-section-head"><div><span class="ao-eyebrow">MATCHED VALIDATION</span>'
        '<h3>Diagnostic F1</h3></div>' + chip(
            f'{number(validation["base_schema_total"])} / '
            f'{number(validation["sft_schema_total"])} matched rows / Base & SFT'
        )
        + '</div><div class="ao-chart">' + "".join(rows)
        + '<div class="ao-chart-axis"><span>0</span><span>0.25</span><span>0.50</span>'
        '<span>0.75</span><span>1.00</span></div></div>'
        '<figcaption>Shared 0-1 F1 scale. Schema validity is not diagnostic correctness.</figcaption></figure>'
    )


def archive() -> str:
    return (
        '<details class="ao-archive"><summary>'
        f'{icon("archive")}Historical / Non-Empirical archive{icon("chevron-down")}'
        '</summary><div class="ao-archive-body">'
        '<p>Preserved source pointers. Not current model-performance evidence; '
        'archived metric rows are not loaded.</p>'
        + "".join(
            '<article><strong>' + esc(item["title"]) + '</strong>'
            + chip(item["classification"], "amber")
            + '<code>' + esc(item["path"]) + '</code><p>' + esc(item["details"]) + '</p></article>'
            for item in historical_archive()
        ) + '</div></details>'
    )


def evaluations() -> str:
    results = current_results()
    validation = results["base_vs_sft"]
    pilot = results["g9_pilot"]
    aligned = results["g9_aligned_diagnostic"]
    delta = number(validation["paired_delta"], 5)
    conclusion = (
        "No diagnostic improvement observed."
        if validation["paired_delta"] is not None and validation["paired_delta"] <= 0
        else "Diagnostic conclusion unavailable."
    )
    body = (
        '<div class="ao-evaluation-layout">' + comparison_chart(validation)
        + '<section class="ao-delta"><span class="ao-eyebrow">PAIRED DELTA / SFT - BASE</span>'
        f'<strong>{delta}</strong><h3>{conclusion}</h3>'
        '<p>Validation-only diagnostic. Resolution, safety, reward, and '
        'time-to-resolve were not measured and remain unavailable.</p></section></div>'
        '<section class="ao-g9"><div class="ao-section-head"><div>'
        '<span class="ao-eyebrow">CONTROLLED G9 / TRAIN-ONLY</span>'
        '<h3>A real attempt. A negative result.</h3></div>'
        f'{chip(pilot["status"], "rose")}</div><div class="ao-g9-facts">'
        f'{fact("Policy completions", pilot["completions"], "Genuine completions")}'
        f'{fact("Malformed / blocked", pilot["malformed_or_blocked"], "No admissible pilot actions" if pilot["available"] else "Canonical report unavailable", "rose")}'
        f'{fact("Objective reward", pilot["reward_each"], "For every pilot action", "rose")}'
        f'{fact("Reward-driven advantages", 0 if pilot["available"] else None, "Across two groups")}'
        '</div><div class="ao-g9-disposition">'
        f'<span>{number(pilot["optimizer_steps"])} optimizer steps</span>'
        f'<strong>{"No accepted checkpoint" if pilot["available"] else "Checkpoint status unavailable"}</strong>'
        f'<span>{"Further training retries frozen" if pilot["available"] else "Canonical report unavailable"}</span>'
        '</div></section><section class="ao-aligned"><div>'
        '<span class="ao-eyebrow">FINAL ALIGNED DIAGNOSTIC</span>'
        f'<h3>{number(aligned["admissible_count"])}/{number(aligned["sample_count"])} '
        'admissible actions</h3><p>Observed sample, not proof of a zero population probability.</p>'
        '</div><div>'
        f'<strong>{number(aligned["tensor_hash_count"])} tensor hashes '
        f'{"unchanged" if aligned["tensor_hashes_unchanged"] is True else "unavailable"}</strong>'
        f'<p>{number(aligned["optimizer_steps"])} optimizer steps. '
        f'{"No reward-driven update or accepted checkpoint resulted." if aligned["available"] else "Canonical diagnostic unavailable."}</p></div></section>'
        + provenance(validation["evidence"] + pilot["evidence"] + aligned["evidence"])
        + archive()
    )
    return page("Evaluations", "Empirical findings, including the results that did not improve.", body)


def agents() -> str:
    roles = [
        ("scan-line", "Triage", "Scope the incident", "Classify severity and affected services.",
         "Alert context and observational tools", "Structured incident handoff",
         "No remediation authority"),
        ("search", "Diagnosis", "Ground the cause", "Connect observations to a proposed root cause.",
         "Metrics, logs, traces and Kubernetes reads", "Grounded diagnosis and action proposal",
         "A proposal is not an executed action"),
        ("wrench", "Remediation", "Act within policy", "Submit role-allowed actions after required approval.",
         "Deployment, scaling and rollback tools", "Recorded tool attempts and outcomes",
         "P1 timeout or rejection fails closed"),
        ("messages-square", "Comms", "Communicate the verdict", "Report the objectively verified outcome.",
         "Incident updates and postmortem tools", "Status communication and evidence record",
         "Agent claims cannot override the verifier"),
    ]
    role_cards = "".join(
        f'<article class="ao-role">{icon(name)}<span class="ao-eyebrow">AGENT {index:02}</span>'
        f'<h3>{title}</h3><h4>{purpose}</h4><p>{description}</p>'
        '<dl><dt>Tool categories</dt>'
        f'<dd>{tools}</dd><dt>Output / handoff</dt><dd>{output}</dd></dl>'
        f'<div class="ao-role-boundary">{icon("lock-keyhole")}{boundary}</div></article>'
        for index, (name, title, purpose, description, tools, output, boundary)
        in enumerate(roles, 1)
    )
    body = (
        '<div class="ao-role-grid">' + role_cards + '</div>'
        '<div class="ao-governance"><section>'
        f'{icon("key-round")}<h3>Role ACL</h3><p>Least-privilege tool exposure by role. '
        'Read access is separate from action authority.</p></section><section>'
        f'{icon("shield-check")}<h3>Approval gate</h3><p>Explicit human P1 approval. '
        'Timeout and rejection remain distinct blocked outcomes.</p></section><section>'
        f'{icon("check-check")}<h3>Objective verifier</h3><p>Environment state determines '
        'resolution, independently of what the model claims.</p></section></div>'
        + notice("Architecture is not a live execution",
                 "No agent, tool wrapper, approval channel, or verifier is executed by this demo.",
                 "neutral")
    )
    return page("Agents & governance", "Specialized responsibilities. Explicit limits. Verifiable handoffs.", body)


def incidents() -> str:
    timeline = "".join(
        '<li><span class="ao-attempt-number">' + row["attempt"] + '</span>'
        '<div class="ao-attempt"><div class="ao-section-head"><h3>Attempt '
        + row["attempt"] + '</h3>' + chip(row["state"], "rose" if row["attempt"] == "017" else "amber")
        + '</div><p>' + esc(row["source_note"])
        + '</p><div class="ao-attempt-measures"><span>Resolution: unavailable</span>'
        '<span>Reward: unavailable</span><span>TTR: unavailable</span></div>'
        + evidence_record(row["source"]) + '</div></li>'
        for row in final_g4_attempts()
    )
    body = (
        notice("G4: NOT_PASSED / frozen",
               "015 is inconclusive, 016 is a pre-fault non-result, and 017 is completed negative. "
               "No incident success or new retry is implied.")
        + '<ol class="ao-timeline">' + timeline + '</ol>'
    )
    return page("Incident chronology", "The final live-systems dispositions, with their evidence limits.", body, "G4 / FINAL DISPOSITIONS")


def evidence() -> str:
    records = catalog()["evidence"]
    body = (
        '<div class="ao-section-head"><h3>Current evidence</h3>'
        + chip(f"{len(records)} indexed records") + '</div>'
        + '<p class="ao-section-note">Availability refers to this checkout. '
        'Hashes identify source bytes, not scientific certification.</p>'
        + provenance(records) + archive()
    )
    return page("Evidence & provenance", "Current sources, explicit classifications, and verifiable hashes.", body)


def runbooks() -> str:
    records = catalog()["runbooks"]
    body = notice(
        "Historical / optional / OUT_OF_SCOPE",
        "Runbooks are a static reference catalog. Recommender research is not required "
        "for current GAI + RL completion and does not establish historical operator feedback.",
        "neutral",
    ) + '<div class="ao-runbook-grid">' + "".join(
        f'<details class="ao-runbook"><summary>{icon("book-open")}<span>'
        f'<small>{esc(record["id"])}</small><strong>{esc(record["title"])}</strong>'
        f'</span>{icon("chevron-down")}</summary><div><p>{esc(record["description"])}</p>'
        f'{chip(record["category"])}<p>Suggested tools: '
        f'{esc(", ".join(record["tools"]))}</p></div></details>'
        for record in records
    ) + '</div>'
    return page("Runbook reference", "Static catalog only. No ranking, inference, or execution.", body)


def settings() -> str:
    boundaries = [
        ("network", "Loopback only", "127.0.0.1 / localhost"),
        ("lock-keyhole", "Read-only", "Checked-in evidence"),
        ("terminal", "No kubectl", "No cluster commands"),
        ("cpu", "No model inference", "No loading or generation"),
        ("shield-check", "No fault injection", "No Chaos execution"),
        ("key-round", "No approval mutation", "No approval controls"),
        ("wrench", "No remediation", "No action controls"),
        ("archive", "No cleanup", "Evidence preserved"),
        ("globe", "No public share", "Gradio sharing disabled"),
    ]
    body = (
        '<div class="ao-safety-banner">' + icon("shield-check")
        + '<div><span class="ao-eyebrow">LOCAL EVIDENCE MODE</span>'
        '<h3>Operational execution is unavailable.</h3>'
        '<p>The launcher enforces loopback binding and rejects public sharing.</p></div>'
        + chip("READ-ONLY", "green") + '</div><div class="ao-boundary-grid">'
        + "".join(
            f'<div>{icon(name)}<span><strong>{title}</strong><small>{subtitle}</small></span>'
            f'{chip("Enforced" if title in {"Loopback only", "No public share"} else "Read-only" if title == "Read-only" else "Unavailable", "green")}</div>'
            for name, title, subtitle in boundaries
        ) + '</div>'
        + notice("G14 remains PARTIAL",
                 "Local UI verification is not peer-host deployment acceptance or scientific certification.",
                 "neutral")
    )
    return page("Safety boundary", "A presentation surface, never an operations control plane.", body)
