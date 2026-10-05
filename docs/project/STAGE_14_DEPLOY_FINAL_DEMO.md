# Stage 14: Local Read-Only Evidence Demo (Gate G14)

**G14 remains PARTIAL.** The local Gradio application is now a presentation-ready, read-only evidence browser. It does not prove a reproducible deployment, deployment safety, live service health, or empirical gate closure. The original G14 deliverable includes packaging and deployment; a local startup smoke alone does not satisfy that contract.

## Start

From the repository root on Windows:

```powershell
& .\.venv\Scripts\python.exe -m demo.launcher --host 127.0.0.1 --port 7860
```

If port `7860` is occupied, use another local port such as `7861`. The launcher rejects non-loopback hosts and public Gradio sharing. The demo never runs `kubectl`, injects a fault, starts the agent pipeline, mutates an approval, performs cleanup, loads a model, or runs inference.

## Five-Minute Walkthrough

1. **Overview:** introduce AtlasOps as a governed multi-agent SRE research system. Follow the incident path from alert through Triage, Diagnosis, the Safety/Approval Gate, Remediation, objective Environment Verification, and Comms. Point out that the status table is checked-in repository state, not a live health probe.
2. **Evaluations:** show the preserved v17 artifact and independent reload, then the matched Validation diagnostic: Base F1 `0.16875`, SFT F1 `0.15935`, paired delta `-0.00940`, and schema conformance `6/6` for each arm. State that this diagnostic observed no improvement and did not measure resolution, safety, reward, or time-to-resolve.
3. **Evaluations:** show the final controlled G9 negative: two optimizer steps, four malformed/blocked completions, reward `-1` for all four, zero reward-driven advantages in both groups, and no acceptable SFT+GRPO checkpoint. Then show the final aligned diagnostic: `0/8` admissible actions, zero optimizer steps, and unchanged hashes for all `392` LoRA tensors. `0/8` does not establish a zero population probability.
4. **Incidents:** show G4 remains frozen `NOT_PASSED` and the final attempt dispositions from `CONTROLLED_G9_ADMISSION_V1.md`: 015 terminal inconclusive/unscored, 016 pre-fault abort/non-result, and 017 completed negative. This checkout has the attempt 015 integrity index but not its referenced raw attempt record. Attempt 017's external raw operational evidence is intentionally not republished; the UI shows only its summary disposition.
5. **Evidence and Settings:** show current source paths and local SHA-256 values, then open the historical archive index. Stage 6/8/9 archived mock outputs and predetermined Stage 13 profiles are explicitly `HISTORICAL / NON-EMPIRICAL`; their metric contents are not loaded into the current evaluation. Confirm that the application has no live, model, approval, remediation, or cleanup controls.

The demo can be presented without Docker, Kind, a model endpoint, or a GPU. It is a local evidence presentation, not a live incident replay.

## Evidence Boundary

- The overview and gate table are repository snapshots; they are not live environment observations.
- The v17 record establishes a preserved adapter with independent reload verification. It does not establish incident improvement.
- The Base-vs-SFT result is a small, matched Validation-only diagnostic comparison. Resolution, action validity, safety, reward, and TTR remain unmeasured.
- The G9 pilot and aligned diagnostic are final negative evidence. No retry, training, model load, or inference is performed by this demo.
- Source paths and SHA-256 values are computed from files present in the checkout. Missing evidence and unmeasured values remain unavailable, not zero. The G4 disposition summary does not expose external raw incident records.
- The archive area shows pointers and classifications only; archived metric files are not opened or presented as current evaluations.
- The optional Runbooks tab is a static catalog. It does not fit or query the recommender and does not execute suggestions.
- Scenario manifest selection reports a read-only reference. It does not run `kubectl`, inject a fault, start an incident workflow, or clean a cluster.

## Verification

Run the focused local safety and read-model tests:

```powershell
& C:\AtlasOps\.venv\Scripts\python.exe -m pytest -q tests\test_stage14_demo_safety.py tests\test_ui_read_model.py
```

Start the launcher and confirm `http://127.0.0.1:7860/` returns HTTP 200. This verifies local startup only.

Fresh 5 October local verification passed 49 Stage 14/read-model/app tests.
Headless Edge loaded all eight desktop tabs, exercised snapshot refresh and
mobile overflow-menu navigation, and reported no page script errors. At
390px width, DOM bounds showed no page-width, heading or text overflow.
These are local browser/software checks, not deployment or model evidence.

Fresh captured views:
[desktop overview](../media/gradio-demo-20261005.png),
[current results](../media/gradio-results-20261005.png), and
[mobile results](../media/gradio-mobile-20261005.png).
Desktop viewport was 1440x1000 and mobile viewport 390x844.
Captures come from the final local UI; visual image inspection was unavailable
in the authoring session. Older `20260926` captures remain historical.

## G14 Acceptance

The current deliverable improves the local read-only presentation and exposes provenance for current findings. G14 stays `PARTIAL` because the approved gate asks for a reproducibly packaged and deployed demo; no such deployment evidence is established by these local checks. No public/live deployment or deployment-safety claim is made.
