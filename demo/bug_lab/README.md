# AtlasOps bug lab

Use a fresh or empty directory for the first `--workdir` startup. To reopen a
workspace created by this version, pass both `--workdir` and `--resume`.
Startup refuses unowned existing contents and concurrent writers.
The lab accepts loopback Host names by default and rejects foreign Origins on
writes. `X-Bug-Lab: 1` is an intentional-write header, not authentication.
An explicitly authorized remote binding also needs its exact `--allowed-host`
name; wildcards are refused. This closure task does not authorize remote exposure.

Two web pages served by one local app:

- **Control room** (`/`): inject an intentional bug, then see what you injected,
  which agent is working on it right now, the patch it proposes, the approval
  gate, the verifier's verdict and an incident history.
- **Atlas Store** (`/shop/`): the page that gets broken. The control room also
  shows it in a live preview.

The lab keeps the AtlasOps pipeline and its rules. The verifier, not an agent,
decides whether an incident is resolved, and P1 fixes wait for a human to approve
the exact change.

```
Monitor → Triage → Diagnosis → Patch → Approval gate → Apply → Verifier → Comms
```

## Run it

From the repository root, with Python 3.11 or 3.12:

```bash
python -m pip install --require-hashes -r requirements/buglab-runtime.lock
python -m demo.bug_lab
```

Open http://127.0.0.1:8790/. The app binds to localhost only and edits a
temporary copy of the store, never the files in this folder.

See the [Windows and macOS runtime commands](../../docs/project/BUG_LAB_LOCAL_RUNTIME.md)
for isolated installation, rules-mode startup, shutdown, and smoke tests.

### Keep incident evidence across restarts

Choose a new directory for the first run:

```bash
python -m demo.bug_lab --agents rules --workdir ./bug-lab-state
```

Stop the foreground app with Ctrl+C. Reopen that same owned directory:

```bash
python -m demo.bug_lab --agents rules --workdir ./bug-lab-state --resume
```

Resume preserves the Store, completed JSONL, and incident sequence. A stopped
unfinished incident becomes `cancelled`; a process crash becomes `interrupted`
on recovery. Neither outcome is a resolution. The record retains its original
stage, approval, and verifier data in `interrupted_state`. Recovery never restores
an approval capability or replays a patch. If the preserved Store remains broken,
the monitor may open a new incident, which must pass a new approval gate.

Only local filesystems are supported. Do not remove the ownership marker, lock,
pending snapshot, or journal to force a directory to reopen. Unknown files,
invalid records, redirects, and unexplained partial appends fail startup without
adopting or deleting the data. Preserve the directory for diagnosis.

`GET /health` and `/health/ready` return 200 only after backend selection,
workspace recovery, and monitor startup. They return 503 before startup, after
shutdown, or after an evidence-write failure. `/health/live` reports that the
HTTP app is reachable. Readiness does not mean that the Store is fixed or that
the selected model remains reachable; outage fallback retains its separate logs.
Write APIs fail with 503 when the app is not ready. Read-only Store and evidence
access remain available for diagnosis.
Store and journal downloads use descriptor-checked reads. A redirected journal
returns 503 and makes the workspace unavailable; redirected Store files are not
served.

### Agents with a local model

With [Ollama](https://ollama.com) running and the model pulled
(`ollama pull qwen2.5:7b-instruct`), the default `--agents auto` setting uses it
for diagnosis and patches. Any OpenAI-compatible endpoint also works:

```bash
python -m demo.bug_lab --llm-base-url http://127.0.0.1:11434/v1 --llm-model qwen2.5:7b-instruct
python -m demo.bug_lab --agents rules      # never call a model
python -m demo.bug_lab --agents llm        # refuse to start without a model
```

`BUG_LAB_LLM_BASE_URL`, `BUG_LAB_LLM_MODEL` and `BUG_LAB_LLM_API_KEY` work in
place of the flags. The control room's header names the backend in use. With a
model, an incident gets up to two model attempts and then one rule-engine attempt.
A model patch that fails the safety check is replaced by the rule engine's patch
within the same attempt. Every patch and diagnosis is labelled with who wrote it.

After backend detection, startup prints the selected backend and any safe fallback
note with flushed output. `--agents llm` still fails startup when the endpoint
cannot serve the requested model. Losing a model during diagnosis or patching logs
the incident ID, stage and a safe failure reason when rules take over. Runtime
fallback does not rewrite the configured startup backend or claim a model-authored
repair. Raw exceptions, credentials and provider response bodies are not fallback
diagnostics.

The endpoint must list the exact requested identifier in a nonempty `/models`
catalog. Each completion must report the same identifier, a `stop` finish reason,
and nonempty text. Missing identity, refusals, truncation, malformed replies, and
contradictory diagnosis labels are not model fixes. Diagnosis and patch replies
must follow the requested format; one complete Markdown fence and numbered source
lines remain supported. Unusable model replies produce a logged warning and fall
back to the rule engine. Endpoint-reported identifiers do not prove which weights
the remote host loaded.

Replies that contain the configured API key are rejected before their text reaches
diagnosis, patching, or incident evidence. Public backend data and request errors
also omit credentials.

A CPU-only model is slow: one answer can take minutes, so raise `--llm-timeout` (default
120 seconds; `BUG_LAB_LLM_TIMEOUT` also works).

Two models have been tried, each on 4 CPU cores behind a local OpenAI-compatible server,
with bugs driven in real Chromium and P1 fixes approved in the UI:

- **Qwen2.5-0.5B-Instruct**, three bugs (stylesheet link, JavaScript crash, NaN prices).
  - Its diagnoses named the wrong file, and its patches changed nothing.
  - The safety check rejected each patch, and the rule engine fixed all three.
- **Qwen2.5-7B-Instruct**, all eight bugs: the Q4_K_M GGUF under llama.cpp, with
  `--llm-timeout 900`.
  - The model fixed the JavaScript crash itself.
  - For the other seven, the rule engine's patch was used:
    - it named the right file in 6 of 8 diagnoses;
    - its find text did not match the file 4 times, and its JSON edits left
      `products.json` invalid twice; the safety check rejected all six;
    - both of its slow-API patches passed the safety check but failed the verifier and were
      reverted.
  - Every bug ended verified. Model calls took 20–121 seconds, and incidents 2.5–5 minutes.

These are demo observations from one run each, not model-performance evidence.

## The bugs

Each bug is a small edit to exactly one store file. The agents never receive the
bug's id or description; they work only from what the checks observe.

| Bug | Type | File | What a visitor sees | Who notices |
| --- | --- | --- | --- | --- |
| JavaScript crash | Runtime error | `app.js` | No products; status says loading failed | Browser monitor |
| Broken stylesheet link | Missing asset | `index.html` | Unstyled page | Server and browser |
| Truncated product data | Data / API failure | `products.json` | Product API returns HTTP 500 | Server and browser |
| Prices show NaN | Data type error | `products.json` | Every price reads `$NaN` | Server and browser |
| Broken image paths | Asset path | `products.json` | Broken images | Server and browser |
| Add to cart does nothing | Functional regression | `app.js` | Clicking Add to cart throws; cart stays at 0 | Browser monitor |
| Layout overflows the screen | CSS layout | `styles.css` | Page scrolls sideways | Browser monitor |
| Slow product API | Performance | `config.json` | Products appear after 4 seconds | Server and browser |

The browser monitor (`static/monitor.js`) runs inside the store page. It reports
JavaScript errors, failed requests, prices, images, layout overflow and a
synthetic Add to cart click. Bugs only a browser can see need the store open
somewhere; the control room's preview counts.

## What each agent does

- **Monitor**: runs eight server checks every two seconds and reads browser
  reports for the current version of the store.
- **Triage agent**: sets P1, P2 or P3 from the failing signals. AtlasOps policy
  (`agents/approval.py`) makes P1 fixes wait for approval; you can make every fix wait.
- **Diagnosis agent**: reads files, requests URLs and names the faulty file and
  root cause.
- **Remediation agent**: proposes a find-and-replace patch. A safety check
  dry-runs it and refuses changes outside the store's five editable files, invalid
  JSON, edits that match more than one place, very large changes, and anything
  that removes the monitor script.
- **Approval gate**: binds an approval to the SHA-256 of the exact change. Approvals
  are single use and expire after five minutes. Rejection or expiry changes nothing,
  and monitoring pauses for that broken version instead of reopening it in a loop.
- **Verifier**: requires every server check and a fresh browser report for the
  patched version to pass. A failed fix is reverted. When no agent fix is verified,
  a **rollback policy** that is not AI restores the last known-good files. It goes
  through the same approval gate.
- **Comms agent**: writes the incident summary from a fixed template. It uses no
  model, so the summary cannot claim more than the record shows.

## What this is not

The store is a toy target served from a temporary folder. The lab does not run
against Kubernetes, the Online Boutique or any real service. Its results are not
G4 incident evidence, model-performance evidence or a gate result. The rule
engine's fixes are deterministic heuristics; label them that way when presenting.

## Tests

```bash
python -m pytest tests/test_bug_lab.py
```

The Python tests cover every bug's injection, detection and rule-based repair; the
approval, rejection, expiry and rollback paths; model answers through a stub
endpoint; the safety check; and the HTTP surface. Their browser reports are
test doubles. The in-page monitor itself only runs in a real browser.

Run the authenticated endpoint and strict response contracts without a real model:

```bash
python -m pytest tests/test_bug_lab.py tests/test_bug_lab_model_client.py
```

Verify owned-workspace recovery and an actual rule-only process crash:

```bash
python -m pytest tests/test_bug_lab_persistence.py tests/test_bug_lab_process_restart.py tests/test_bug_lab_workspace_journal.py
```

Run the real-browser application suite with the existing locked frontend dependencies:

```bash
npm ci --prefix frontend
npm run test:lab --prefix frontend
```

The test launcher owns a disposable rule-only Store and stops it after the suite.
The browser tests cover all eight bugs, exact-change approvals, rejection, expiry,
replay prevention, evidence download, telemetry age, and desktop/mobile rendering.
These tests do not contact Kaggle, a model endpoint, or infrastructure.

## Incident evidence

The Control Room's **Download incident evidence** link downloads completed
incidents as JSONL from `/lab/api/evidence`. The same records remain in
`incidents.jsonl` inside the printed working directory. Resetting the Store does
not erase those records.

The state API retains a completed result until reset or the next successful
injection. Those actions clear `incident`, not completed history or JSONL.
`incident_state` distinguishes `idle`, `pending`, `active` and `closed`;
`incident_sequence` counts incident openings in the owned workspace and continues
after explicit resume.
Pending browser-only detection has a null incident, so a previous result cannot be
mistaken for the new bug. Each new record includes its `workspace_id` and
`sequence`. Approval digests bind workspace identity as well as the exact change,
so a receipt from another workspace cannot authorize the same display ID.
Use the exact incident ID and approval digest for decisions.

Each record names its local-demonstration classification and selected backend.
Each attempted change retains its diagnosis, exact diff and digest, approval
outcome, and objective verifier checks. Verifier evidence includes the Store
version and browser report receipt time. Missing browser reports remain
unverified. A Store change during verification fails the check. Reports older
than 30 seconds are stale and do not open or verify incidents.

The target runtime acceptance must still validate the real endpoint's
authentication, exact served identifier, model replies and deadlines, Store
reloads and fresh telemetry, and persistent evidence collection on its target
host. Run both model-success and rule-fallback cases and report them separately.
This application branch changes no deployed runtime or host setup and certifies no
scientific gate. Historical model observations above retain their original
classification.
