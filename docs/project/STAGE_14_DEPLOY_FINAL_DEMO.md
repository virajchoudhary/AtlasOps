# Stage 14: Local Read-Only Evidence Demo (Gate G14)

**G14 remains PARTIAL.** The React/Vite presentation reads the existing Python evidence
projection through a separate localhost-only API. It does not prove deployment safety,
live service health, or empirical gate closure. The original G14 deliverable includes
packaging and deployment; a local startup smoke alone does not satisfy that contract.

## Start

From the repository root with Python 3.11+ and Node.js 22+:

```powershell
python -m pip install fastapi uvicorn
npm ci --prefix frontend
npm run build --prefix frontend
python -m demo.launcher --host 127.0.0.1 --port 7860
```

Build once before presentation, then only the Python launcher is needed. If port
`7860` is occupied, choose another local port. The launcher rejects non-loopback
hosts and public sharing. A missing build returns an actionable HTTP 503 rather than
silently falling back to Gradio. The demo never runs `kubectl`, injects a fault,
starts the agent pipeline, mutates an approval, performs cleanup, loads a model,
or runs inference.

For development, launch Python on `7862`, then run `npm run dev --prefix frontend`.
The Vite server binds to loopback and proxies `/api` and `/health` to that service.
No Docker, Kubernetes, GPU, model runtime, operational API key, or cloud account
is needed. No proprietary font files, logos, or illustrations are shipped.

## API Boundary

`demo/read_api.py` exposes only `GET /api/catalog`, the existing allowlisted
`GET /api/attempts/{name}` historical projection, `/health`, and built UI assets.
All write methods are rejected, including unknown execution paths. Host headers
must be loopback. There is no CORS permission for external origins and no
operational app mount, raw evidence download, recommender query, or approval route.
`ui_read_model.py` remains the evidence authority; the browser only formats its
values. Product counts are derived from the scenario catalog and registry syntax
without importing tool implementations. Twenty-four registered wrappers is not
twenty-four autonomous permissions: the existing role ACL exposes nineteen.

## Five-Minute Walkthrough

1. **Overview:** introduce AtlasOps as a governed multi-agent SRE research system. Follow the incident path from alert through Triage, Diagnosis, the Safety/Approval Gate, Remediation, objective Environment Verification, and Comms. Point out that the status table is checked-in repository state, not a live health probe.
2. **Evaluations:** show the preserved v17 artifact and independent reload, then the matched Validation diagnostic: Base F1 `0.16875`, SFT F1 `0.15935`, paired delta `-0.00940`, and schema conformance `6/6` for each arm. State that this diagnostic observed no improvement and did not measure resolution, safety, reward, or time-to-resolve.
3. **Evaluations:** show the final controlled G9 negative: two optimizer steps, four malformed/blocked completions, reward `-1` for all four, zero reward-driven advantages in both groups, and no acceptable SFT+GRPO checkpoint. Then show the final aligned diagnostic: `0/8` admissible actions, zero optimizer steps, and unchanged hashes for all `392` LoRA tensors. `0/8` does not establish a zero population probability.
4. **Incidents:** show G4 remains frozen `NOT_PASSED` and the final attempt dispositions from `CONTROLLED_G9_ADMISSION_V1.md`: 015 terminal inconclusive/unscored, 016 pre-fault abort/non-result, and 017 completed negative. This checkout has the attempt 015 integrity index but not its referenced raw attempt record. Attempt 017's external raw operational evidence is intentionally not republished; the UI shows only its summary disposition.
5. **Evidence and System:** show current source paths and local SHA-256 values, then
   open the historical archive index. Stage 6/8/9 archived mock outputs and
   predetermined Stage 13 profiles are explicitly `HISTORICAL / NON-EMPIRICAL`;
   their metric contents are not loaded into the current evaluation. Confirm that
   the application has no live, model, approval, remediation, or cleanup controls.
   `READY_FOR_REVIEW` describes the presentation package, not scientific certification.

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
python -m pytest -q tests/test_stage14_demo_safety.py tests/test_ui_read_model.py tests/test_demo_read_api.py
npm test --prefix frontend
npm run test:e2e --prefix frontend
```

Start the launcher and confirm `http://127.0.0.1:7860/` returns HTTP 200. This verifies local startup only.

Historical 5 October Gradio verification passed 49 Stage 14/read-model/app tests.
Headless Edge loaded all eight desktop tabs, exercised snapshot refresh and
mobile overflow-menu navigation, and reported no page script errors. At
390px width, DOM bounds showed no page-width, heading or text overflow.
These are local browser/software checks, not deployment or model evidence.

Historical captured views:
[desktop overview](../media/gradio-demo-20261005.png),
[current results](../media/gradio-results-20261005.png), and
[mobile results](../media/gradio-mobile-20261005.png).
Desktop viewport was 1440x1000 and mobile viewport 390x844.
These captures are historical Gradio UI evidence, not the rebuilt React frontend;
visual image inspection was unavailable in that authoring session.
Older `20260926` captures also remain historical.

## React Review Evidence

6 October 2026 local verification: production TypeScript/Vite build passed;
78 focused Python regressions, 6 frontend component tests and 9 Playwright browser
tests passed. Browser tests use the built frontend through the Python launcher,
not only Vite. The npm audit reported zero vulnerabilities, including development
dependencies. These are software checks, not empirical or deployment certification.

Three review passes were performed within the available capabilities:

1. Structure: fixed overview grid sizing, changed architecture grouping, introduced
   explicit lineage nodes and separated historical attempts.
2. Hierarchy: consolidated graphite tokens, strengthened product identity and metrics,
   added server-owned evidence tags, and captured desktop/tablet/mobile views.
3. Interaction/responsiveness: checked all routes, reduced motion, GET-only traffic,
   evidence filtering, mobile navigation and widths 1440/1280/1024/768/390.
   Independent Luna Max critique identified small chart labels, incorrect menu
   semantics, overlong gate detail and clipped caveats; these were corrected.

First-iteration captures:
[desktop Overview](../media/research-console-20261006/desktop-overview.jpg),
[Models](../media/research-console-20261006/desktop-models.jpg),
[Evaluations](../media/research-console-20261006/desktop-evaluations.jpg),
[Incidents](../media/research-console-20261006/desktop-incidents.jpg),
[Evidence](../media/research-console-20261006/desktop-evidence.jpg),
[tablet Overview](../media/research-console-20261006/tablet-overview.jpg),
[mobile Overview](../media/research-console-20261006/mobile-overview.jpg).

An external image-capable review of committed head
`b2c53f89640baf74bbaa33000b2eb72a202abf9e` replaced the unavailable root/Luna
pixel review and returned **CHANGES REQUIRED**. The second iteration reserves a
mobile scroll area above the fixed navigation, removes the redundant hero subtitle,
enlarges meaningful metadata, omits units for unavailable values, and tightens the
mobile architecture. Seven component tests and eleven browser tests passed,
including all-route mobile clearance and metadata regression checks.

Second-iteration captures are in `docs/media/research-console-20261006/external-review-2/`.
The capture record identifies the committed source and built asset hashes. Mobile
captures include both the initial viewport and scrolled architecture/RL views.
**Final external visual acceptance remains pending.** Keep PR #187 draft and
unmerged until the user accepts these regenerated screenshots.

## G14 Acceptance

The current deliverable improves the local read-only presentation and exposes provenance for current findings. G14 stays `PARTIAL` because the approved gate asks for a reproducibly packaged and deployed demo; no such deployment evidence is established by these local checks. No public/live deployment or deployment-safety claim is made.
