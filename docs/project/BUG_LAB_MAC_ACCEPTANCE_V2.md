# Bug lab — Mac acceptance rerun against main at 9ab930a

Second Mac acceptance run, requested after PR #238 (`fix(lab): close Mac acceptance
software findings`) merged the fixes for the five findings raised by the first run.
This document records what was observed. It is not gate evidence, it certifies
nothing, and no gate row changes.

| | |
|---|---|
| Commit under test | `9ab930a4b5bc91b260ade4fc4f8d9b9a3da5b977` (main, merge of PR #238) |
| Image | `atlasops-buglab:v2`, `sha256:831e9a43028993ee22e7ce1c31839df1aa7bddb529050dccf6615a90412385da` |
| Host | Apple M4 MacBook Pro, 16 GB, arm64, macOS 24.6.0, Colima 4 CPU / 8 GiB |
| Model | `qwen2.5:7b-instruct` on local Ollama, reached through the keyed proxy on `127.0.0.1:8000` |
| Browser | Chrome on `http://127.0.0.1:8790/shop/` throughout, so the in-page monitor was live |
| Evidence | `artifacts/evidence/buglab-mac-v2/` |

The model runs on this Mac, not on the Kaggle T4 in the original plan. Every timing
below is an M4 serving a 4-bit 7B and says nothing about GPU performance.

After the run, PR #239's Ruff check found an unused `first` assignment in
`driver_reset_state.py`. The CI repair removes only that assignment and retains
the probe call. All raw run records are unchanged. The original driver is
preserved at Git commit `f99f7f546d49e9695b4d6fefe4cc7c23bb796182`; the repair
does not represent a new Mac run.

---

## What was asked, and what happened

| Asked | Result |
|---|---|
| T2: correct model identification at startup, refusal when the model is unavailable | **Pass** |
| T8: model outage and rule-engine fallback properly logged | **Pass** |
| Reset and incident tracking without stale results | **Pass** |
| All 8 bugs resolve with real browser verification | **Pass**, 8/8 |
| Approvals, rejection, expiry, rollback | **Approve, reject and expiry pass. Rollback not exercised** — see below |
| Incident history and JSONL evidence preserved | **Pass** |

---

## T2 — backend identification and refusal

`artifacts/evidence/buglab-mac-v2/T2_startup_identification.txt`

Three cases, all from a container started on the image above.

**A. `--agents llm` with the endpoint reachable.** Startup now names the backend:

```
AtlasOps bug lab
  Control room: http://0.0.0.0:8790/
  Store:        http://0.0.0.0:8790/shop/
  Working copy: /work
  Backend: qwen2.5:7b-instruct via http://host.docker.internal:8000/v1
```

**B. `--agents llm` with nothing at the endpoint.** The container refuses to start:
`demo.bug_lab.llm.ModelError: Model endpoint did not answer (connection failed).`,
exit code 3. It does not fall back to the rule engine, which is the required
behaviour for explicit model mode.

**C. `--agents auto` with nothing at the endpoint.** Starts on the rule engine and
says why, both on stdout and in the state API:

```
  Backend: Rule engine (no model) (No model endpoint answered (connection failed); using the rule engine.)
```
```json
{"mode": "rules", "label": "Rule engine (no model)", "model": null, "base_url": null,
 "note": "No model endpoint answered (connection failed); using the rule engine."}
```

Finding 3 is closed. T2 was PARTIAL in the first run for exactly this reason and is
now a pass.

One cosmetic observation, not a defect: the banner is printed before backend
detection runs, so in case B the control room and store URLs are advertised and then
the process exits. The URLs also echo `--host` verbatim, so they read
`http://0.0.0.0:8790/` rather than a reachable address.

---

## T8 — model outage mid-incident

`artifacts/evidence/buglab-mac-v2/T8_model_outage.{json,txt}`, driver `driver_t8.py`

The proxy was stopped while INC-0001 was at the diagnosis stage, confirmed gone by a
direct request to the endpoint. The incident continued on the rule engine, resolved
and verified, and the store returned 200 at the end. The container log now records
the fallback:

```
INC-0001 propose: Model fallback: connection failed; using the rule engine.
```

The same line appears as a warning event on the incident itself. It carries the
incident id, the stage and a fixed safe reason, with no raw exception, no credential
and no provider response body. Finding 4 is closed.

The fallback logging was exercised far beyond this single test: the T6 and T7 runs
produced lines such as `INC-0011 diagnosis: Model fallback: model attempts were not
verified; using the rule engine.` and `INC-0009 propose: Model fallback: no safe
usable patch; using the rule engine.`, distinguishing an unreachable endpoint from a
model answer that failed verification.

**A correction to my own first attempt.** The first T8 run of this session reported
the container log as empty. That was wrong and the fault was in the driver, not the
lab: `docker logs` writes the container's stderr to the caller's stderr, and the
driver captured stdout only. The driver now merges both streams and the run above is
from a fresh container after that fix.

---

## T7 — all eight bugs with the model and a real browser

`artifacts/evidence/buglab-mac-v2/T7_llm_all_bugs.{json,txt}`, driver `driver_t7.py`

| bug | incident | elapsed | final patch author | diagnosis matched | browser checks |
|---|---|---|---|---|---|
| js-reference-error | INC-0004 | 14.3 s | **qwen2.5:7b-instruct (model)** | yes | 9/9 |
| broken-stylesheet | INC-0005 | 12.1 s | Rule engine (no model) | yes | 9/9 |
| truncated-data | INC-0006 | 28.3 s | Rule engine (no model) | yes | 9/9 |
| string-prices | INC-0007 | 28.2 s | Rule engine (no model) | yes | 9/9 |
| broken-image-paths | INC-0008 | 38.4 s | Rule engine (no model) | yes | 9/9 |
| cart-handler | INC-0009 | 16.2 s | Rule engine (no model) | yes | 9/9 |
| layout-overflow | INC-0010 | 18.2 s | Rule engine (no model) | yes | 9/9 |
| slow-api | INC-0011 | 82.7 s | Rule engine (no model) | yes | 9/9 |

All eight resolved and verified. Eight distinct incident ids, INC-0004 to INC-0011,
with no repeats — the first run could not say that, because stale reads duplicated an
id. Every run finished with `browser_current: true` and `browser_stale: false`, so the
verification came from the page's own monitor in an open Chrome tab rather than from
server checks alone. `incident_state` read `closed` at the end of all eight.

**The model authored 1 of 8 final patches.** It made 5 patch attempts, 4 of which
failed verification; the rule engine produced the other 7 repairs. This matches the
first run's pattern (2/8, then 1/8 after the core merge) and is the number to quote
alongside "8/8 resolved", which on its own would suggest the model fixed eight bugs.

`control-room-live-INC-0012.jpg` catches the failure path mid-run: the verifier
reporting `Not resolved: checks still fail` and `Reverted the failed change to app.js`
before the next attempt.

---

## Reset, incident tracking, history and evidence

`artifacts/evidence/buglab-mac-v2/RESET_AND_STATE.json`, driver `driver_reset_state.py`

Finding 5 was that `/lab/api/state` kept serving the last closed incident with
nothing marking it stale, so a poller that did not track incident ids read the
previous result as the new one. The transitions now observed:

| step | `incident_state` | `incident` | `incident_sequence` | history |
|---|---|---|---|---|
| after reset | `idle` | null | 11 | 11 |
| immediately after inject | `pending` | null | 11 | 11 |
| after the incident finished | `closed` | INC-0012 `resolved` | 12 | 12 |
| after reset | `idle` | null | 12 | 12 |
| while the next incident opens | `active` | INC-0013 `open` | 13 | 12 |
| after that incident finished | `closed` | INC-0013 `resolved` | 13 | 13 |
| after the final reset | `idle` | null | 13 | 13 |

A completed result is still served, which is wanted, but it is now labelled
`closed` rather than being indistinguishable from a live one, and both reset and a
successful injection clear it. **Zero stale reads** were seen in the window that
broke three runs of the first acceptance. Finding 5 is closed.

History and evidence survive every reset: after three resets the history held 13
entries and `/lab/api/evidence` returned 13 JSONL records, INC-0001 to INC-0013,
with no gaps.

A first run of this driver is kept as `RESET_AND_STATE_run1_driver_defect.json`. It
never sent an approval, so both P1 incidents sat at `awaiting_approval` until the
next reset cancelled them and the `closed` state was never reached. Its summary line
`closed_incident_marked: "active"` is that defect, not a lab fault.

---

## T6 — approval outcomes

`artifacts/evidence/buglab-mac-v2/T6_approval_paths_run1.json`, `T6_approve_path.json`,
drivers `driver_t6.py` and `driver_t6_approve.py`

| path | incident | result | store at end |
|---|---|---|---|
| approve | INC-0018 | `resolved` — "Resolved and verified", approval decision `approved` | 500 → **200**, repaired |
| reject | INC-0015 | `escalated` — "Blocked because you rejected the change" | 500, still broken |
| expire | INC-0016 | `escalated` — "Blocked because the approval timed out", decision `timeout` | 500, still broken |

The approval is single-use and bound to one exact change. On INC-0018, replaying the
same digest returned `409 No approval is pending for this incident`, and a fabricated
digest of 64 zeroes was refused the same way, so a change cannot be approved by
guessing.

**Each attempt has its own gate.** On INC-0017 the approved patch failed verification
and was reverted; the next attempt proposed a different patch, which opened a new gate
with a new digest. Approving once does not pre-authorise whatever the lab tries next.
That run is kept as `T6_approve_path_run1_single_approval.json`: its
`final_status: awaiting_approval` is the second gate waiting, not a lost approval, and
the driver was corrected to approve every distinct digest.

The expiry path was watched for its whole window: `expires_in` counted 299 → 19 in
20-second samples with the incident at `awaiting_approval`, then flipped to
`escalated` at t=300. Nothing is applied when a gate expires, and nothing is applied
when it is rejected; in both cases the store is left broken, which is the safe
outcome.

**Rollback was not exercised.** The rollback policy (`engine.py:560`) runs only when
every attempt fails verification, and in every run here the rule engine produced a
repair that passed, so the path was never reached. Forcing it would mean changing
core code, which this task does not do. It is covered by unit tests
(`tests/test_bug_lab.py:398`, `:583`, `:884`), which pass, but it has not been seen
on this Mac.

---

## Full repository suite

`artifacts/evidence/buglab-mac-v2/REPO_SUITE.txt`

```
3665 passed, 43 skipped in 632.35s (0:10:32)
```

**No failures.** The first acceptance ran 3570 passed / 1 failed and then 3644
passed / 1 failed after the core merge; that single failure was
`tests/test_frontend_ui.py::test_root_endpoint_serves_html`, finding 2, and it is
now gone. The 25 regression tests added by PR #238
(`test_bug_lab_standalone.py`, `test_bug_lab_startup_logging.py`,
`test_bug_lab_state_logging.py`, and the reworked `test_frontend_ui.py`) pass.

---

## The five findings from the first acceptance

| # | Finding | State |
|---|---|---|
| 1 | `workspace.py` imported `training.sft_provenance` | **Closed.** The predicate is local and stdlib-only; `test_bug_lab_standalone.py` imports the lab with `training`/`config` unavailable. |
| 2 | `test_root_endpoint_serves_html` failed in a fresh checkout | **Closed.** Suite is green here with no frontend build. |
| 3 | Startup banner never named the backend | **Closed.** Verified in all three startup modes. |
| 4 | Model-to-rules fallback was silent | **Closed.** Logged with incident id, stage and a safe reason; three distinct causes distinguished. |
| 5 | `/lab/api/state` kept serving the last closed incident | **Closed.** `incident_state` / `incident_sequence`, and the slot is cleared on reset and injection. |

---

## What is not verified

- The model ran on this Mac, not the Kaggle T4 in the original plan. No timing here
  speaks to GPU performance. The notebook path remains prepared and unrun.
- Rollback has not been seen on this Mac, only in unit tests.
- Phase 3 (EKS) is not started. It costs money and is not authorized.
- **None of this is gate evidence.** No gate row changes; G4 and G9 remain
  NOT_PASSED/FROZEN per `docs/project/COMPLETION_CHECKLIST.md:80,85`, and
  `docs/EVIDENCE_INDEX.md` is untouched. A local demo result is not a certification.
- Four defects in this session's own test drivers were found and corrected, each with
  the bad run kept: stdout-only log capture in T8; a missing approval in the reset
  driver; a premature sample plus an interfering reset in the T6 approve case; and a
  single approval where the incident opened a second gate. The last one would have
  read as a lab failure if reported raw — the lab was correct and the driver was
  wrong. They are recorded because a test that was wrong once may be wrong again.
