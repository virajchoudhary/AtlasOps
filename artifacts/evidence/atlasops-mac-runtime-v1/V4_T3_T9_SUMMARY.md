# Bug lab T3–T9 re-run on the merged image

The earlier Mac acceptance report closed T2–T9 as **PARTIAL**: T2 was
verified on the merged image and T3–T9 were not re-run. This closes that gap.

| Field | Value |
|---|---|
| Image | `atlasops-buglab:v4-resume` (merged main `fafe1eb` + the entrypoint fix) |
| Date | 30 October 2026 |
| Host | Apple M4, 10 cores, 16 GB, macOS 24.6.0, arm64 |
| Rules lab | `127.0.0.1:8792`, `--agents rules` |
| Model lab | `127.0.0.1:8791`, `--agents llm`, `qwen2.5:7b-instruct` |
| Isolated lab | `127.0.0.1:8793` + its own proxy on `8001`/`8002`, for T8 and T9 |
| Live demo lab | `127.0.0.1:8790` left running and untouched throughout |

## Results

| Test | What it checks | Result |
|---|---|---|
| T3 | State and health API | **PASS** — `/lab/api/state` 200 and valid JSON in both modes; all three health routes 200 |
| T4 | Write guard | **PASS** — 403 on all five write routes without `X-Bug-Lab`; foreign `Origin` also 403; container runs as uid 10001; ports loopback-only |
| T5 | Rules baseline, all 8 bugs | **PASS** — 8/8 resolved and verified, 6.1–16.2s |
| T6 | Approval outcomes | **PASS** — approve, reject and expire all behave correctly; a replayed approval is refused |
| T7 | Model mode, all 8 bugs | **PASS on resolution, and that is the misleading number** — 8/8 resolved and verified, but the model authored only **1 of 8** final patches |
| T8 | Model outage mid-incident | **PASS** — cut at `diagnosis`; incident still `resolved`, store 200 at end; fallback logged with incident, stage and a safe reason |
| T9 | Endpoint rotation | **PASS** — a running container cannot learn a new URL; recreating it is the documented step |

## T5 — rule engine, every bug

| bug | status | outcome | approval | elapsed |
|---|---|---|---|---:|
| `js-reference-error` | resolved | Resolved and verified | approved | 6.3s |
| `broken-stylesheet` | resolved | Resolved and verified | not required | 6.1s |
| `truncated-data` | resolved | Resolved and verified | approved | 8.2s |
| `string-prices` | resolved | Resolved and verified | approved | 6.2s |
| `broken-image-paths` | resolved | Resolved and verified | not required | 6.1s |
| `cart-handler` | resolved | Resolved and verified | approved | 8.2s |
| `layout-overflow` | resolved | Resolved and verified | not required | 8.1s |
| `slow-api` | resolved | Resolved and verified | not required | 16.2s |

## T6 — the three approval outcomes

| path | incident | decision | final status | store before → after | outcome |
|---|---|---|---|---|---|
| approve | INC-0011 | `approved` | resolved | 500 → 200 | Resolved and verified |
| reject | INC-0012 | `rejected` | escalated | 500 → 500 | Blocked because you rejected the change |
| expire | INC-0013 | `timeout` | escalated | None → None | Blocked because the approval timed out |

Two details worth keeping. Replaying the approved digest returned **HTTP 409**
(`No approval is pending for this incident`), so the approval really is
single-use. On the reject path the store API stayed at **500** either side of
the decision — the patch was never applied, rather than applied and reverted.

The expire path's store status reads `None` on both sides: the driver does not
sample it for that path. Recorded as it stands rather than filled in.

## T7 — model mode, and what the model actually contributed

| bug | status | patch author | diagnosis matched | elapsed |
|---|---|---|---|---:|
| `js-reference-error` | resolved | qwen2.5:7b-instruct (model) | True | 19.3s |
| `broken-stylesheet` | resolved | Rule engine (no model) | True | 21.3s |
| `truncated-data` | resolved | Rule engine (no model) | True | 126.1s |
| `string-prices` | resolved | Rule engine (no model) | True | 75.8s |
| `broken-image-paths` | resolved | Rule engine (no model) | True | 52.8s |
| `cart-handler` | resolved | Rule engine (no model) | True | 52.6s |
| `layout-overflow` | resolved | Rule engine (no model) | True | 50.7s |
| `slow-api` | resolved | Rule engine (no model) | True | 107.2s |

**8/8 resolved and verified is true and, on its own, misleading.** The patch
authors are 7 Rule engine (no model), 1 qwen2.5:7b-instruct (model).
The lab's own log gives the reason rather than leaving it to inference:

```
INC-0002 propose: Model fallback: no safe usable patch; using the rule engine.
INC-0003 propose: Model fallback: no safe usable patch; using the rule engine.
INC-0004 propose: Model fallback: no safe usable patch; using the rule engine.
INC-0005 propose: Model fallback: no safe usable patch; using the rule engine.
INC-0006 propose: Model fallback: no safe usable patch; using the rule engine.
INC-0007 propose: Model fallback: no safe usable patch; using the rule engine.
INC-0008 diagnosis: Model fallback: model attempts were not verified; using the rule engine.
```

So the model produced a patch that failed the lab's safety check in six of
eight incidents, and at the diagnosis stage once. The v1 run recorded 2 of 8
model-authored patches; this run is 1 of 8, so the trend is slightly worse,
not better. Resolution is carried by the rule engine.

Timings are not clean numbers: GRPO training was running on this machine's
CPU throughout T7, with host load average between 11 and 15. They are real
measurements of a contended machine and say nothing about the model's speed
on an idle one.

## T8 — the model taken away mid-incident

```
INC-0001 diagnosis: Model fallback: connection failed; using the rule engine.
```

| field | value |
|---|---|
| `bug` | truncated-data |
| `stage_when_cut` | diagnosis |
| `final_status` | resolved |
| `final_stage` | closed |
| `final_outcome` | Resolved and verified |
| `resolved_by` | Rule engine (no model) |
| `store_status_at_end` | 200 |
| `endpoint_after_cut` | HTTPError — **probed the wrong port, see below** |
| `endpoint_after_restore` | HTTP 401 — **probed the wrong port, see below** |

The fallback line carries the incident ID, the stage and a fixed safe reason
(`connection failed`) with no raw exception or credential, which is what the
logging change in the merged branch was for.

**Two fields in that table do not measure what they claim.** `driver_t8.py`
hardcodes `http://127.0.0.1:8000/v1/models` for its "confirm the endpoint really
is gone" check (lines 109 and 159). The proxy stopped here was the dedicated one
on `8001`, so both probes hit the *live* proxy on `8000`, which was up the whole
time — `HTTPError` is its 401, not evidence of anything being down. Neither field
should be read as verifying the cut.

What does verify it is first-hand and better: the lab's own log line above.
`Model fallback: connection failed; using the rule engine.` was written by the
container under test at the moment its model call failed, so the model was
genuinely unreachable from that container. The incident then resolved with the
store at 200, which is the behaviour T8 exists to check.

This is a defect in the shared driver, not in the product, and it is listed
among the findings for the owner of that code.

## T9 — rotating the model endpoint

| step | observed |
|---|---|
| `old_endpoint_before` | HTTP 401 (401 = proxy up, key enforced) |
| `old_endpoint_after_move` | URLError (nothing listening) |
| `new_endpoint_after_move` | HTTP 401 (401 = proxy up, key enforced) |
| `live_lab_8790_still_up` | 200 |
| backend before the move | `http://host.docker.internal:8001/v1` |
| backend still reported after the move | `http://host.docker.internal:8001/v1` |
| backend after recreating the container | `http://host.docker.internal:8002/v1` |

The running container kept reporting the dead endpoint after the proxy moved,
because `demo/bug_lab/llm.py` probes `GET /models` once during `create_app`.
That is the documented design, not a fault; recreating the container is the
documented step, and it then reported the new URL.

## How T8 and T9 were run without touching the live lab

The stock drivers could not be used as they stand:

- `driver_t8.py` does `docker stop buglab-llm-proxy` and reads
  `docker logs buglab` — it would have cut the model out from under the live
  demo lab and captured the wrong container's log.
- `driver_t9.py` does `docker rm -f buglab` and rewrites
  `~/atlasops-local/buglab.env` — it would have destroyed the live lab
  container and edited the operator's config file.

T8 was run with the container and proxy names taken from the environment,
against a dedicated proxy on `8001`. T9 was rewritten to move that dedicated
proxy to `8002` and recreate only the dedicated lab. The live lab answered
HTTP 200 at the end of T9, and `~/atlasops-local/buglab.env` has the same
MD5 before and after (`0218cf54def682b9b906544631f5b741`).

## One correction to the earlier report

The live demo lab on `8790` runs `atlasops-buglab:v3-fafe1eb`, **not** the v4
image. The restart-persistence fix was verified on a separate v4 container,
so the container currently serving the demo would still fail `docker restart`.
An earlier message in this work said it was on v4; that was wrong.

## What this is not

Not gate evidence. These are bug lab runtime tests on one Mac; G4 remains
NOT_PASSED and G9 remains frozen. `docs/EVIDENCE_INDEX.md` is untouched and
no gate status changed. The earlier `buglab-mac-v1` and `buglab-mac-v2`
evidence directories were not modified.
