# Stage 14: Local Read-Only Demo (Gate G14)

**G14 is PARTIAL.** The local Gradio console is a demonstration and evidence browser. It does not establish live service health, deployment safety, or an empirical gate pass.

## Start

From the repository root on Windows:

```powershell
& .\.venv\Scripts\python.exe -m demo.launcher --host 127.0.0.1 --port 7860
```

The launcher binds to localhost by default. The Gradio console is read-only: its scenario controls identify checked-in Chaos manifests without running `kubectl`, injecting a fault, starting the agent pipeline, or cleaning a cluster. Live G4 execution belongs to the governed Stage 4 harness after its own preflight and authorization. Do not describe scenario selection as a simulated incident result.

## Five-minute evidence-led walkthrough

1. **Project Status:** show the checked-in G0–G15 governance snapshot. Explain that G4 is `NOT_PASSED` and CI proves software behavior, not model performance.
2. **About & Architecture:** follow Alert → Triage → Diagnosis → Recommender → approval → one action → environment verifier → Comms. The verifier determines resolution.
3. **Scenario Control:** select `sf-002` to show the available manifest. The console reports that no command or incident workflow ran.
4. **Preserved G4 Evidence:** open `EXP-STAGE4-SF002-010.json`. Show the P1 approval timeout inconsistency, incorrect target, failed mutating tool attempts, objective unresolved verdict, Comms summary, and source SHA-256. Then select attempt 014 to show an interrupted record with no completed verifier verdict.
5. **Runbook Recommender:** issue the prefilled query. Explain that its ranking is based on scenario-derived interactions and is advisory; no remediation is executed by this tab.
6. **Ablations and Benchmarks:** show the explicit non-empirical historical labels and the absence of certified measured results. Close on the status tab's remaining gates and blockers.

This walkthrough works without Docker, Kind, a model endpoint, or a GPU. It is a fallback presentation using preserved historical evidence and local software. It must be described as such.

## Visible evidence contract

- The status table is read from `docs/project/MASTER_PIPELINE_STATUS.md`, so it is a repository snapshot, not live health.
- Stage 4 attempt summaries are read from `artifacts/evidence/stage4/`. They display selected fields and a SHA-256 of the source file; the full source remains unchanged.
- Historical comparison and ablation files are labelled unverified or non-empirical. They do not close G13.
- The recommender ranks runbooks from scenario-derived data. A ranking score is not a recovery probability.
- Missing evidence is shown as unavailable. The UI does not invent resolution, reward, TTR, checkpoint, or model metrics.

## Acceptance boundary

`tests/test_stage14_demo_safety.py` checks construction, read-only behavior, the G0–G15 snapshot, negative and interrupted G4 evidence, recommender output, and historical result labels. A local startup smoke test checks the browser server separately. Neither check proves G14 deployment or any empirical stage.
