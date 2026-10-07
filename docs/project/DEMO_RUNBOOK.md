# AtlasOps Presentation Runbook

## What This Demo Proves

The website demonstrates the intended incident-response sequence through a scripted,
interactive rehearsal, and separately displays preserved research evidence.
It does not run the coordinator, load a model, contact Kubernetes, issue operational
approvals, remediate an incident, or close a research gate.

Rehearsal outputs are **SYNTHETIC / NON-LIVE / NON-EMPIRICAL**. G4 remains
NOT_PASSED; scientific certification remains NOT_CERTIFIED.

## Start On This Windows Machine

From `C:\AtlasOps\.codex-worktrees\demo-readiness-20261007`:

```powershell
.\scripts\start-demo.ps1 -Check
.\scripts\start-demo.ps1
```

The launcher finds the checkout or shared repository virtual environment, checks
the built presentation, preserves existing listeners, and starts the server in
the background. Add `-LiveObservations` to reconnect the
three fixed local Kubernetes service forwards. It never starts Docker, changes
deployments, loads a model, injects faults, or approves actions. Existing server
options are preserved; select a different `-Port` to start with different options.
An occupied, unverified presentation port is rejected. Startup logs are kept in
a fresh `.codex-tmp/demo-start-*` directory; a startup timeout does not mean the
process stopped, so inspect that process before retrying.

The underlying Python commands remain available:

```powershell
& C:\AtlasOps\.venv\Scripts\python.exe -m demo.launcher --check
& C:\AtlasOps\.venv\Scripts\python.exe -m demo.launcher --host 127.0.0.1 --port 7864
```

Run `--check` before presenting. It checks the local JS/CSS build, evidence projection,
and matching rehearsal fixtures without starting services or contacting infrastructure.
It is not a live model, cluster, HTTP, or hosted-deployment verdict. Startup also runs
these checks and refuses an incomplete local build.

## Optional Live Observations

Start with `--live-observations` to show current health responses alongside the
clearly separate rehearsal. This permits fixed localhost GETs only:

- Boutique: `127.0.0.1:17880`
- Coordinator `/healthz`: `127.0.0.1:19099`
- Prometheus `/-/ready`: `127.0.0.1:19090`
- Cached model inventory `/api/tags`: `127.0.0.1:11434`

These depend on separately started services/loopback port forwards. Unreachable or
invalid responses remain unavailable, with a timestamp for the observation. A
responding coordinator or model inventory does not prove model inference, current
deployment provenance, incident resolution, or SFT/GRPO execution.
This option starts no Docker container, loads no model, calls no Kubernetes tool,
and executes no incident. The default launcher remains offline/read-only.

For the current existing Kind services, keep each forward running in a separate
terminal; reconnect a forward if its pod restarts:

```powershell
kubectl --context kind-atlasops-local port-forward --address 127.0.0.1 -n default service/frontend 17880:80
kubectl --context kind-atlasops-local port-forward --address 127.0.0.1 -n default service/atlasops-coordinator-svc 19099:9099
kubectl --context kind-atlasops-local port-forward --address 127.0.0.1 -n monitoring service/prometheus-kube-prometheus-prometheus 19090:9090
```

Then start the presentation from this checkout with:

```powershell
& C:\AtlasOps\.venv\Scripts\python.exe -m demo.launcher --host 127.0.0.1 --port 7864 --live-observations
```

The deployed coordinator reports `qwen2.5:1.5b` and uses an older `g3-local`
image. Its process health is not acceptance of the current source or trained
adapter. The separately started cached server's model inventory does not prove
generation. The live research freeze in `CONTROLLED_G9_ADMISSION_V1.md` remains
historical. The owner explicitly superseded it in this chat for one controlled
SF002 run only. The first governed launch failed startup transport qualification
before reservation or fault injection. That qualification is not a G4 incident result.

## Governed Capture Monitor

The opt-in monitor reads one selected local capture: runner phase markers,
recorded triage/approval/tool outcomes, verifier result, and cleanup sidecar.
It starts no run, approves no proposal, executes no tools, and loads no model.
Missing results remain unavailable.

```powershell
& C:\AtlasOps\.venv\Scripts\python.exe -m demo.launcher --host 127.0.0.1 --port 7864 --live-observations --incident-capture-root D:\Codex\AtlasOps-demo-20261007 --incident-id EXP-STAGE4-SF002-018
```

The preserved launch shows `STARTUP_FAILED / NOT_RESERVED` and
`bridge_transport_failure`, separate from the scripted rehearsal.
A second inference preparation completed its pinned transfer and checkpoint
admission, but the notebook session was subsequently replaced: its process handle
and preparation/source paths were absent on the fresh connection. No model load,
incident reservation, or fault injection followed that preparation. The current
remote session must not be treated as qualified by the previous session's output.

Local recovery on October 7 restarted the existing Kind control-plane container
without recreating it or deleting its volume. The node briefly reported Ready,
but application recovery and API requests remained unreliable; both attempted
presentation forwards exited on API timeouts. Use the rehearsal for a presentation
until fresh service and inference checks pass. Do not advertise a live-agent demo
on the strength of container startup or a prior notebook qualification.

Later on October 7, the existing cluster recovered enough for Boutique HTML,
coordinator process health and Prometheus readiness/query requests to respond.
The fixed forwards and presentation server were restored and verified through
the website. This is live service access, not qualified model inference: the
cached-model endpoint remains unavailable and the old private session is gone.
Notebook settings show No persistence; preserving files would not preserve a
live process or replace fresh runtime, checkpoint and transport qualification.

The fresh v3 notebook-local benign inference test returned READY on one T4.
Windows cold transport then failed with HTTP 524 during model load. Loading the
unchanged model locally before opening the tunnel produced a successful Windows
benign qualification, READY in 55.78 seconds with final artifact/runtime identity
checks. Both results and cleanup are preserved separately under `.codex-tmp`.
The temporary connections were subsequently stopped; this is recorded transport
qualification, not a currently running model or incident-resolution result.
The exact golden-v3 fault-injection launch was rejected by execution safety
before process creation. Attempt 018 remains unreserved and no fault was injected.

After explicit approval of the concrete SF002 injection, a golden-v3 launch
failed remote-main freshness before reservation. Its capture remains preserved.
A fresh clean execution checkout retained the byte-identical prior attempt ledger
and completed attempt 018 with a negative verdict. Diagnosis was invalid and the
direct-action completion used a tool-call envelope, which the strict policy parser
rejected. No P1 proposal was approved or mutating remediation executed. The
objective verifier reported unresolved before post-verdict harness cleanup.
Cleanup then verified zero Chaos independently. Attempt 018 is COMPLETED; the
two-attempt protocol budget is exhausted. G4 remains NOT_PASSED.

The current website selects that completed capture with
`--incident-capture golden-v3b` and shows the negative outcome, policy block and cleanup separately
from rehearsal. The local direct-action adapter has a focused offline-tested
output-contract fix, but no new empirical evaluation of that fix was performed.
Do not substitute those software tests for live recovery or rewrite this negative
result.

To restart the presentation with this recorded result:

```powershell
.\scripts\start-demo.ps1 -LiveObservations -IncidentCaptureRoot D:\Codex\AtlasOps-demo-20261007 -IncidentCapture golden-v3b
```

Existing server options remain unchanged if a presentation is already running.
The coordinator now distinguishes future policy-validation blocks from genuine
target mismatches in its communications summary. Offline composition tests cover
approved, rejected and timed-out exact P1 decisions with healthy and unhealthy
verifier observations. None of these mock outcomes changes the completed live
run or establishes that a new model completion will select a correct action.

The future diagnosis prompt now requests textual `root_cause` and top-level
evidence, matching the unchanged G4 runtime shape check. Its example and nearby
grounding/provider/rendering contracts passed 119 offline checks. The saved run
is unchanged. This prompt revision changes source/prompt provenance; a future
evaluation must review and identify the revised source and protocol explicitly.
Do not reuse old prompt hashes, reset the exhausted attempt ledger, or describe
format alignment as proof of correct live diagnosis.

A phase marker proves only that the runner logged that phase, not that it passed.
On Windows, active status requires a PID and creation-time match; a launch file
alone does not prove current activity. The API exposes selected fields and digests,
never raw logs, credential files, tunnel URLs, or arbitrary filesystem paths.
The capture root and experiment are supplied only at startup, not browser input.

Approval here is a recorded outcome. A live P1 decision belongs to the runner's
authenticated same-process channel after inspection of the exact proposed action.
This monitor exposes no approval capability or scientific-certification claim.

Open `http://127.0.0.1:7864/#/demo`. The frontend is already built in this checkout.
Only rebuild after source changes:

```powershell
npm ci --prefix frontend
npm run build --prefix frontend
```

No network connection, Docker, GPU, operational credentials, or model endpoint is
needed during the presentation. Dependencies must be installed before going offline.
The launcher is localhost-only and rejects public sharing.

## Five-Minute Click Path

1. **Overview:** introduce the four agent roles, role ACL, approval gate, and objective
   verifier. Say: "The architecture is implemented, but our empirical research results
   do not certify live incident resolution."
2. **Rehearsal:** choose Payment CPU saturation and Environment recovered. Start,
   then Play. It stops at P1 approval. Inspect Diagnosis to show the structured
   proposal. Say: "These are scripted outputs, not live model reasoning."
3. **Approve simulation:** Next stage shows the simulated verifier observation;
   Next stage shows Comms. Inspect any recorded stage and download the transcript.
   Say: "Tool success alone is insufficient. Verification controls the outcome."
4. **Reset:** start another rehearsal and choose Reject or Simulate timeout at the
   approval stage. Remediation and verification are skipped; Comms reports blocked.
5. **Reset:** choose Frontend packet loss and Fault still present. Approve at P1 and
   advance. The tool fixture succeeds, but verification leaves the incident unresolved.
6. **Evaluations / Incidents / Evidence:** show actual preserved SFT and negative
   Base-vs-SFT/G9 findings, final G4 chronology, and source hashes. These are distinct
   from the rehearsal, whose outcomes are not stored in research evidence.

The governed monitor can also show the actual completed attempt 018 as
RECORDED_NEGATIVE with policy block invalid_action. Show that recovery remained
false even though the post-verdict safety cleanup succeeded. This is a real
recorded failure, not a successful live remediation demonstration.

The Rehearsal tab resets when leaving the page or reloading. Within a run, prior
stages remain inspectable without repeating actions. Reset clears the rehearsal.

## Fallback

- If the server stops, run the startup command again. Use another port if 7864 is busy.
- If `/` returns 503, rebuild `frontend/` before presenting.
- If fixtures are unavailable, retry after restoring the matching checked-in manifests;
  the application must not invent substitute results.
- If autoplay timing is inconvenient, use Next stage. Playback never auto-approves P1.
- Existing evidence routes remain usable when the rehearsal fixture request fails.

## Opt-In Website Operator

The default server is still read-only. A separate startup option enables the
fixed governed runner controls without exposing credentials or accepting browser
commands, paths, model settings or cluster context:

```powershell
.\scripts\start-demo.ps1 -Port 7865 -LiveObservations -OperatorConfig C:\AtlasOps\.codex-worktrees\demo-readiness-20261007\.codex-tmp\website-operator.json -IncidentCaptureRoot D:\Codex\AtlasOps-demo-20261007 -IncidentCapture golden-v3b
```

This prepared local configuration intentionally references the exhausted
historical protocol and preserved attempt ledger. The Start button remains
blocked until source, protocol and ledger checks pass. Starting the website
does not launch an incident. Exact-action approvals remain interactive product
controls, never automatic approvals from a blanket chat instruction.

The operator config requires absolute checkout, capture root (outside that
checkout), secret directory, inference config, and preserved attempt-ledger root;
exact source/protocol and preserved-ledger inventory digests; fresh experiment
identity; and operator identity. The preserved ledger must be independent and
nonempty, and its poison marker is checked as well as the execution checkout.
Private settings stay server-side. The runner rechecks its own full admission
before reservation or injection. One server instance starts at most one run,
and an existing capture prevents relaunch after restart. No reset/stop endpoint
terminates a live run or substitutes harness cleanup for recovery.
An atomic, never-released website launch claim beside the preserved ledger limits
this evaluation to one website launch per protocol across execution checkouts.
A server restart reads the existing capture but cannot approve through a lost
process handle; pending requests remain fail-closed and expire normally.

The [revised evaluation plan](DEMO_REVISED_EVALUATION_PLAN.md) records the
remaining acceptance steps. It is prepared, not an active protocol or live
result. The website controls have software-test coverage; a reliable successful
model-backed demonstration is not yet established.

## Readiness Boundary

Local demo acceptance covers production build, browser interactions, responsive layout,
GET-only traffic, no execution imports, and preserved evidence. It is not a hosted-site
or deployment-safety verdict. The repository's historical Space Dockerfile starts the
operational `app.py`, not this React presentation, and is not changed by this work.
Hosted deployment requires a confirmed target and explicit authorization.
