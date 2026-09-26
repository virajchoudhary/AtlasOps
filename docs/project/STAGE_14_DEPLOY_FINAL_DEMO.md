# Stage 14: Local Read-Only Demo (Gate G14)

**G14 is PARTIAL.** The local Gradio console is a demonstration and evidence browser. It does not establish live service health, deployment safety, or an empirical gate pass.

## Start

From the repository root on Windows:

```powershell
& .\.venv\Scripts\python.exe -m demo.launcher --host 127.0.0.1 --port 7860
```

The launcher binds to localhost by default. The Gradio console is read-only: its scenario controls identify checked-in Chaos manifests without running `kubectl`, injecting a fault, starting the agent pipeline, or cleaning a cluster. Live G4 execution belongs to the governed Stage 4 harness after its own preflight and authorization. Do not describe scenario selection as a simulated incident result.

## Five-minute evidence-led walkthrough

1. **Overview:** show `G4 NOT_PASSED`, the checked-in G0-G15 governance snapshot, and the explicit absence of current cluster or model verdicts. CI proves software behavior, not model performance.
2. **Incidents and Agents:** inspect the preserved negative G4 attempt 010. Follow alert, Triage, Diagnosis, advisory recommendation, approval timeout, attempted action, objective unresolved verdict, and Comms. A recorded phase is not proof of success.
3. **Evidence:** inspect attempt 010's source hash, then compare interrupted attempt 014, which has no completed verifier verdict. Select a named manifest to show its read-only description; no command or incident workflow runs.
4. **Runbooks:** issue the prefilled query. Its ranking uses scenario-derived interactions, is advisory, and executes no remediation.
5. **Evaluations:** show the mock Stage 6/8/9 and predetermined Stage 13 labels. No certified measured comparison exists.
6. **Settings:** confirm the demo is a repository evidence snapshot without operator approval, injection, or cleanup controls.

This walkthrough works without Docker, Kind, a model endpoint, or a GPU. It is a fallback presentation using preserved historical evidence and local software. It must be described as such.

## Visible evidence contract

- The status table is read from `docs/project/MASTER_PIPELINE_STATUS.md`, so it is a repository snapshot, not live health.
- Stage 4 attempt summaries are read from `artifacts/evidence/stage4/`. They display selected fields and a SHA-256 of the source file; the full source remains unchanged.
- Historical comparison and ablation files are labelled unverified or non-empirical. They do not close G13.
- The recommender ranks runbooks from scenario-derived data. A ranking score is not a recovery probability.
- Missing evidence is shown as unavailable. The UI does not invent resolution, reward, TTR, checkpoint, or model metrics.

## Acceptance boundary

`tests/test_stage14_demo_safety.py` checks construction, read-only behavior, the G0–G15 snapshot, negative and interrupted G4 evidence, recommender output, and historical result labels. A local startup smoke test checks the browser server separately. Neither check proves G14 deployment or any empirical stage.
