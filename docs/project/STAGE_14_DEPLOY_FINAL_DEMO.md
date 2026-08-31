# Stage 14: Deploy Final Demo Safely (Gate G14)

This technical specification and governance document records the packaging, architecture, and verification of the **AtlasOps Safe Operator Demonstration Console and CLI Launcher**.

---

## 1. Operator Demonstration Console Overview

The AtlasOps demonstration suite is structured into an interactive 7-tab Gradio interface accessible locally (`python -m demo.launcher`) and on cloud accelerators:

1. **⚡ Live Ops & Thought Stream**:
   - Real-time event streaming and visual orchestration of multi-agent incident triage, root-cause diagnosis, runbook recommendation, mutating remediation, and environment verification.
2. **📚 Runbook Recommender (RS)**:
   - Interactive live query explorer for the Stage 11 Hybrid Recommender ($S_{\\text{content}} + S_{\\text{collab}} + S_{\\text{prior}}$).
   - Dynamically inspects top-$K$ recommendations, match confidence explanations, suggested tool sequences, and step-by-step remediation procedures.
3. **📋 Incidents & Trajectories**:
   - Forensic trajectory inspector rendering historical multi-agent execution records (`data/trajectories/*.json`).
4. **📈 Multi-Model Ablations**:
   - Live rendering of the comprehensive 5-model x 4-partition benchmark matrix produced in Stage 13.
5. **📊 Benchmark Overview**:
   - Per-tier breakdown across Single-Fault, Named Replays, and Cascading Leaderboard scenarios.
6. **🎬 Historical Replays**:
   - 10 named historic production incident replicas (Cloudflare 2019, AWS S3 2017, GitHub 2018, Datadog 2023, Knight Capital 2012, etc.).
7. **ℹ️ About & Architecture**:
   - Full academic workstream reference, system contract, and upstream attribution (`Harikishanth/AtlasOps` $\\rightarrow$ `virajchoudhary/AtlasOps`).

---

## 2. Zero-Risk Safe Mode Guardrails

To ensure that the demonstration console can be executed safely by evaluators without requiring real cluster access or risking accidental infrastructure mutations:
- **Default Safe Mode (`DEMO_SAFE_MODE=1`)**:
  - Intercepts all destructive `kubectl` and `Chaos Mesh` mutating calls.
  - Returns clear simulated outcomes with full diagnostic telemetry.
- **Explicit Live Flag (`--live-cluster`)**:
  - Live mutating commands are only executed when explicitly authorized with `--live-cluster` in controlled sandbox clusters.

---

## 3. Quickstart Launcher CLI

```bash
# Launch in safe demonstration mode (default)
python -m demo.launcher --port 7860

# Launch with public sharing link
python -m demo.launcher --port 7860 --share
```

---

## 4. Gate G14 Acceptance Criteria

Gate G14 is verified by automated unit tests in `tests/test_stage14_demo_safety.py`:
- `test_build_app_constructs_all_seven_tabs`: **PASS** (Gradio interface successfully initialized).
- `test_demo_safe_mode_prevents_cluster_mutations`: **PASS** (Safe-mode guardrail validated).
- `test_dashboard_recommender_query_interactive`: **PASS** (Interactive RS querying verified).
- `test_load_ablation_matrix_and_comparison_table`: **PASS** (Benchmark and ablation matrix loaders verified).
- `test_demo_launcher_cli_configuration`: **PASS** (CLI argument parser verified).

**Gate G14 Status**: **`PASS`**
