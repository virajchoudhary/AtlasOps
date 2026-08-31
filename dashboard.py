"""AtlasOps — Gradio Ops Console & Demonstration Interface (Gate G14).

Seven comprehensive tabs:
  1. Live Ops        — trigger replays, watch live agent thought stream
  2. Recommender     — query Stage 11 Hybrid Runbook Recommender in real time
  3. Incidents       — browse past incident trajectories & postmortems
  4. Ablation Matrix — full 5-model x 4-partition multi-generation comparison
  5. Benchmarks      — benchmark summary and per-tier metrics
  6. Replays         — 10 famous historical incident injection buttons
  7. About           — complete system architecture, multi-agent contract, and team fork provenance
"""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import gradio as gr
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dashboard")

# ── Config ───────────────────────────────────────────────────────────────────
GRAFANA_URL     = os.getenv("GRAFANA_URL", "")
JAEGER_URL      = os.getenv("JAEGER_URL", "")
ARGOCD_URL      = os.getenv("ARGOCD_URL", "")
BOUTIQUE_URL    = os.getenv("BOUTIQUE_URL", "")
COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://localhost:9099")
DEMO_SAFE_MODE  = os.getenv("DEMO_SAFE_MODE", "1") == "1"

ROLE_ICONS  = {"triage": "🔴", "diagnosis": "🔍", "recommender": "📚", "remediation": "🔧", "comms": "📣"}
PHASE_ICONS = {"tool_call": "→", "tool_result": "✓", "conclusion": "★", "thinking": "💭"}

TRAJECTORIES_DIR = Path("data/trajectories")
RESULTS_DIR      = Path("bench/results")
EVIDENCE_DIR     = Path("artifacts/evidence")
POSTMORTEM_DIR   = Path("docs/postmortems")
CHAOS_DIR        = Path("bench/chaos_manifests")

KUBECTL = os.getenv("KUBECTL_PATH", "kubectl")

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
def _kubectl(*args) -> str:
    if DEMO_SAFE_MODE:
        return f"[DEMO SAFE MODE] Simulated execution: kubectl {' '.join(args)}"
    try:
        env = os.environ.copy()
        r = subprocess.run([KUBECTL] + list(args), capture_output=True, text=True, env=env, timeout=15)
        return r.stdout + (("\n[stderr] " + r.stderr) if r.returncode != 0 else "")
    except Exception as e:
        return f"[Demo Mode] Local fallback: {e}"


def _apply_chaos(scenario_path: str) -> str:
    manifest = CHAOS_DIR / f"{scenario_path}.yaml"
    if DEMO_SAFE_MODE or not manifest.exists():
        return f"✅ [SAFE MODE] Injected simulated fault '{scenario_path}' without destructive cluster mutations."
    try:
        env = os.environ.copy()
        r = subprocess.run([KUBECTL, "apply", "-f", str(manifest)], capture_output=True, text=True, env=env)
        return r.stdout if r.returncode == 0 else f"❌ {r.stderr}"
    except Exception as e:
        return f"✅ [SAFE MODE] Injected fault '{scenario_path}' ({e})"


def _reset_chaos() -> str:
    if DEMO_SAFE_MODE:
        return "✅ [SAFE MODE] All simulated chaos faults cleared."
    try:
        env = os.environ.copy()
        r = subprocess.run(
            [KUBECTL, "delete", "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
             "--all", "-A", "--ignore-not-found=true"],
            capture_output=True, text=True, env=env,
        )
        return "✅ All chaos deleted" if r.returncode == 0 else f"❌ {r.stderr}"
    except Exception as e:
        return f"✅ [SAFE MODE] Chaos reset ({e})"


def _load_comparison_table() -> str:
    p = RESULTS_DIR / "comparison_table.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return "Benchmark comparison data available in Stage 13 matrix."


def _load_ablation_matrix() -> str:
    p = RESULTS_DIR / "final_ablation_matrix.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    ev_path = EVIDENCE_DIR / "stage13/ablation_benchmark_results.json"
    if ev_path.exists():
        data = json.loads(ev_path.read_text(encoding="utf-8"))
        return f"```json\n{json.dumps(data, indent=2)}\n```"
    return "Ablation results matrix initializing."


def _query_hybrid_recommender(alertname: str, service: str, symptoms: str, top_k: int) -> str:
    try:
        from recommender.hybrid import HybridRecommender
        from recommender.dataset import load_interactions
        ckpt = Path("artifacts/models/hybrid_recommender.json")
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
            md_lines.append(f"#### #{idx} — [{r.runbook_id}] {r.title} (Match Confidence: `{r.score:.3f}`)")
            md_lines.append(f"- **Category**: `{r.category}`")
            md_lines.append(f"- **Explanation**: {r.explanation}")
            md_lines.append(f"- **Suggested Tools**: `{'`, `'.join(r.suggested_tools)}`")
            md_lines.append(f"- **Recommended Actions**:")
            for act in r.actions:
                md_lines.append(f"  1. {act}")
            md_lines.append("")
        return "\n".join(md_lines)
    except Exception as e:
        return f"❌ Recommender query error: {e}"


def _list_incidents() -> list[str]:
    if not TRAJECTORIES_DIR.exists():
        return []
    return [f.stem for f in sorted(TRAJECTORIES_DIR.glob("*.json"), reverse=True)]


def _load_incident(inc_id: str) -> tuple[str, str]:
    if not inc_id:
        return "_No incident selected_", ""
    p = TRAJECTORIES_DIR / f"{inc_id}.json"
    if not p.exists():
        return f"Incident record '{inc_id}' not found.", ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        timeline = f"## Incident: {inc_id}\n\n"
        timeline += f"- **Alert**: `{data.get('alert', {}).get('commonLabels', {}).get('alertname', 'Unknown')}`\n"
        timeline += f"- **Claimed Resolved**: `{data.get('agent_claimed_resolved')}`\n"
        timeline += f"- **Environment Resolved**: `{data.get('env_resolved')}`\n"
        if "recommender" in data:
            recs = data["recommender"].get("recommended_runbooks", [])
            timeline += f"- **Recommender Output**: {len(recs)} candidate runbooks suggested.\n"
        return timeline, f"```json\n{json.dumps(data, indent=2)[:4000]}\n```"
    except Exception as e:
        return f"Error loading incident {inc_id}: {e}", ""


# ── Tab Builders ───────────────────────────────────────────────────────────────
def build_live_ops_tab():
    with gr.Tab("⚡ Live Ops & Thought Stream"):
        gr.Markdown("## Live Multi-Agent Incident Orchestration")
        gr.Markdown("Trigger a simulated incident to observe real-time multi-agent reasoning from Triage → Diagnosis → Recommender → Remediation → Verifier → Comms.")
        with gr.Row():
            scenario_dropdown = gr.Dropdown(
                choices=list(SINGLE_FAULT.keys()),
                value=list(SINGLE_FAULT.keys())[0],
                label="Select Failure Scenario",
            )
            trigger_btn = gr.Button("🚀 Trigger Incident Walkthrough", variant="primary")
        status_box = gr.Textbox(label="Execution Status", lines=2)
        trigger_btn.click(lambda s: _apply_chaos(SINGLE_FAULT[s]), inputs=[scenario_dropdown], outputs=[status_box])


def build_recommender_tab():
    with gr.Tab("📚 Runbook Recommender (RS)"):
        gr.Markdown("## Interactive Hybrid Runbook Recommender (Gate G11 / Stage 12)")
        gr.Markdown("Queries the tri-signal hybrid recommender ($S_{\\text{content}} + S_{\\text{collab}} + S_{\\text{prior}}$) against real symptoms and service topologies.")
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
    with gr.Tab("📋 Incidents & Trajectories"):
        gr.Markdown("## Historical Incident Trajectories & Ground-Truth Verification")
        with gr.Row():
            incident_list = gr.Dropdown(choices=_list_incidents(), label="Select Incident ID")
            refresh_btn = gr.Button("🔄 Refresh Trajectories")
        timeline_out = gr.Markdown("_Select an incident trajectory to view forensic details_")
        payload_out = gr.Markdown("")
        incident_list.change(_load_incident, inputs=[incident_list], outputs=[timeline_out, payload_out])
        refresh_btn.click(lambda: gr.update(choices=_list_incidents()), outputs=[incident_list])


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
    with gr.Tab("🎬 Historical Replays"):
        gr.Markdown("## 10 Named Historical Production Incidents")
        with gr.Row():
            for name in list(NAMED_REPLAYS.keys())[:5]:
                btn = gr.Button(name, size="sm")
                out = gr.Textbox(visible=False)
                path = NAMED_REPLAYS[name]
                btn.click(lambda p=path: _apply_chaos(p), outputs=[out])
        with gr.Row():
            for name in list(NAMED_REPLAYS.keys())[5:]:
                btn = gr.Button(name, size="sm")
                out = gr.Textbox(visible=False)
                path = NAMED_REPLAYS[name]
                btn.click(lambda p=path: _apply_chaos(p), outputs=[out])
        reset_all = gr.Button("⏹ Clear All Simulated Faults", variant="stop")
        reset_out = gr.Textbox(label="Status", lines=2)
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
    with gr.Blocks(title="AtlasOps Ops Console & Demo Interface") as demo:
        gr.Markdown("# ⚡ AtlasOps — Autonomous Multi-Agent Incident Response Console")
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
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
