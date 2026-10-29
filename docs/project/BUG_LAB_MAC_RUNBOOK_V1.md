# Bug lab on the Mac — runbook and run record

Implements sections 6, 7, 9 and 10 of *AtlasOps Bug Lab: Run Plan* (27 October 2026)
on this MacBook. Section 8 (Amazon EKS) is optional and was not run: it needs AWS
spend, which is not authorized.

Status: **T1–T9 all run, then rerun after the core PR merged.** The model was served on this Mac rather than on a Kaggle T4
(§3.1) — a deviation from the plan, chosen because it removes the public tunnel and the
open question about Kaggle's terms along with it. The Kaggle notebook is prepared but
not run. See [What is not verified](#what-is-not-verified).

| | |
|---|---|
| Branch | `feat/atlasops-mac-runtime` |
| Base | `4acf0c3`, then `origin/main` merged in after core PR #236 landed |
| Image | `atlasops-buglab:local`, 51.7 MB, `linux/arm64` |
| Host | macOS 24.6.0, Apple M4, 16 GB; Docker 29.5.2 via Colima (8 GB to the VM) |
| Lab port | `8790`, published to `127.0.0.1` only |
| Model | `qwen2.5:7b-instruct` via Ollama on this Mac, behind a keyed proxy on `127.0.0.1:8000` |

A demo result here is not gate evidence and does not certify anything.

Software follow-up on 28 October 2026 addresses the five findings in section 6.
The tables and raw Mac records below remain the observations of the original run,
not results from a new Mac run. See [Software follow-up](#61-software-follow-up).

A second Mac run against `9ab930a` on 29 October 2026 confirms all five findings
closed and is reported separately in
[BUG_LAB_MAC_ACCEPTANCE_V2.md](BUG_LAB_MAC_ACCEPTANCE_V2.md), with records in
`artifacts/evidence/buglab-mac-v2/`. It is not gate evidence either.

---

## 1. What changed in this branch

| File | Why |
|---|---|
| `Dockerfile.buglab` | New. Section 7.2 of the plan, with the corrections below. |
| `notebooks/buglab_kaggle_qwen_serving.ipynb` | New. Section 6 cells, ready to run on Kaggle. Prepared, not run. |
| `scripts/local_llm_proxy.py` | New. The same keyed proxy as the notebook, for the local model path. |
| `docs/project/BUG_LAB_MAC_RUNBOOK_V1.md` | This file. |
| `artifacts/evidence/buglab-mac-v1/` | New. Raw output of every run below, plus the drivers that produced it. |

No core application file was changed. Five findings for the core branch are in
[Findings for the core branch](#findings-for-the-core-branch).

`docs/EVIDENCE_INDEX.md` was deliberately **not** updated. That index lists evidence
for the project's scientific claims; a demo run is not one, and listing it there would
blur a line the project is careful about.

### Corrections to the plan's Dockerfile

The plan's draft (§7.2) was written against `aea7e8e`. Two things did not hold at
`4acf0c3`:

1. **`COPY agents/__init__.py` fails** — there is no such file. `agents/` is a PEP 420
   namespace package, so copying `agents/approval.py` alone is enough.
2. **`demo/` plus `agents/approval.py` is not the full dependency set.**
   `demo/bug_lab/workspace.py` imports `training.sft_provenance`, which imports
   `config.splits`, which imports `config.scenario_catalog`. That import arrived in
   `03606c3`, after the plan was written. Without them the container exits at startup
   with `ModuleNotFoundError: No module named 'training'`. The image now copies
   `training/sft_provenance.py` and `config/`. Both are standard library only, so the
   pinned dependency set is unchanged.

The three dependencies are pinned to the versions in `requirements/*.lock`:
`fastapi==0.141.1`, `uvicorn==0.52.1`, `httpx==0.28.1`.

`.dockerignore` was deliberately left alone. It is shared with the root `Dockerfile`
and `Dockerfile.coordinator`, and it already excludes credential material
(`**/*.secret`, `**/*.key`, `**/*.pem`, `**/kubeconfig`, `**/.env*`). Narrowing it for
this image's build speed risked breaking the other two.

---

## 2. Build and run

```bash
cd ~/Documents/atlasops-buglab          # worktree on feat/atlasops-mac-runtime
colima start                            # Docker daemon; Docker Desktop also works
docker build -f Dockerfile.buglab -t atlasops-buglab:local .
```

Rule-engine baseline, no model needed:

```bash
docker run -d --name buglab -p 127.0.0.1:8790:8790 atlasops-buglab:local \
  python -m demo.bug_lab --host 0.0.0.0 --allow-remote --workdir /work --agents rules
```

With the Kaggle model (see section 3 for the env file):

```bash
docker run -d --name buglab -p 127.0.0.1:8790:8790 \
  --env-file ~/atlasops-local/buglab.env atlasops-buglab:local
```

Then open:

- Control room — <http://127.0.0.1:8790/>
- Store — <http://127.0.0.1:8790/shop/>

Stop with `docker rm -f buglab`.

**Why `--allow-remote` is safe here.** The container must listen on `0.0.0.0` for a
published port to reach it. `-p 127.0.0.1:8790:8790` is what limits access: the port is
bound on the loopback interface of the Mac, so nothing off the machine can reach it.
The lab has no login, so it must never be published on `0.0.0.0`.

**Keep a browser tab open on the store.** Three of the eight bugs
(`js-reference-error`, `cart-handler`, `layout-overflow`) are noticed only by the in-page
monitor, which reports through `POST /lab/api/telemetry`. With no browser on `/shop/`,
injecting one of those changes the store but starts no incident. Confirmed here:
injecting `layout-overflow` with no browser open left `active: false` and an empty
incident, while the same bug resolved normally once a tab was open. This is by design
and is not a fault, but it is easy to mistake for a hang.

---

## 3. The model (section 6)

Section 6 of the plan serves Qwen2.5-7B from a Kaggle T4 and reaches it through an
ngrok tunnel. **This branch runs the same model on this Mac instead.** Both paths are
prepared; only the local one has been run.

### 3.1 Why the deviation

The plan's §6.5 looked for an official Kaggle position on tunnels and found none, and
treats that as an open question rather than permission. Serving the model locally
removes the question entirely, and removes the public endpoint with it. The
model — `qwen2.5:7b-instruct` — is the one the plan names, and was already cached on
this machine.

What is lost: the T4. The model now runs on an Apple M4 with 16 GB, of which Colima
holds 8 GB. **Timings in section 4 are M4 timings and say nothing about T4
performance.** A GPU run would need the notebook path below.

### 3.2 The local path (what was run)

Ollama is bound to loopback only. The lab's `BUG_LAB_LLM_API_KEY` would be decorative
in front of Ollama, which has no authentication of its own, so the same key-checking
proxy the notebook uses runs here too — `scripts/local_llm_proxy.py`, in the lab image
so its dependencies stay pinned. The container reaches it through
`host.docker.internal`, which under Colima maps to the Mac's loopback: nothing listens
on the LAN at any point. Verified, not assumed — see `T63_local_model_smoke.txt`.

```bash
# 1. Serve the model. The Homebrew ollama 0.30.7 on this machine is broken (its
#    llama-server binary is missing from the Cellar); the app bundle's 0.33.2 works.
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_CONTEXT_LENGTH=8192 OLLAMA_KEEP_ALIVE=-1 \
  /Applications/Ollama.app/Contents/Resources/ollama serve &
/Applications/Ollama.app/Contents/Resources/ollama pull qwen2.5:7b-instruct

# 2. Settings, outside the repository, never committed.
mkdir -p ~/atlasops-local && chmod 700 ~/atlasops-local
umask 077
cat > ~/atlasops-local/buglab.env <<EOF
BUG_LAB_LLM_BASE_URL=http://host.docker.internal:8000/v1
BUG_LAB_LLM_MODEL=qwen2.5:7b-instruct
BUG_LAB_LLM_API_KEY=$(openssl rand -hex 32)
BUG_LAB_LLM_TIMEOUT=180
EOF
chmod 600 ~/atlasops-local/buglab.env
printf 'PROXY_KEY=%s\nOLLAMA_BASE_URL=http://host.docker.internal:11434\n' \
  "$(grep '^BUG_LAB_LLM_API_KEY=' ~/atlasops-local/buglab.env | cut -d= -f2-)" \
  > ~/atlasops-local/proxy.env
chmod 600 ~/atlasops-local/proxy.env

# 3. The keyed proxy, published to loopback only.
docker run -d --name buglab-llm-proxy -p 127.0.0.1:8000:8000 \
  --env-file ~/atlasops-local/proxy.env \
  -v "$PWD/scripts/local_llm_proxy.py:/app/proxy.py:ro" \
  atlasops-buglab:local \
  python -m uvicorn proxy:app --host 0.0.0.0 --port 8000

# 4. The lab, in explicit model mode.
docker run -d --name buglab -p 127.0.0.1:8790:8790 \
  --env-file ~/atlasops-local/buglab.env atlasops-buglab:local \
  python -m demo.bug_lab --host 0.0.0.0 --allow-remote --workdir /work --agents llm
```

Smoke test (plan §6.3), all three outcomes recorded in `T63_local_model_smoke.txt`:

```bash
KEY="$(grep '^BUG_LAB_LLM_API_KEY=' ~/atlasops-local/buglab.env | cut -d= -f2-)"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/v1/models          # 401
curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:8000/v1/models            # 200
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.1.105:8000/v1/models      # 000
```

### 3.3 The Kaggle path (prepared, not run)

`notebooks/buglab_kaggle_qwen_serving.ipynb` holds the four cells: installs, Ollama
plus model warm-up, the same key-checking proxy, and the ngrok tunnel opened last.
Cell order matters — opening the tunnel before the model answers is what produced
HTTP 524 in an earlier run of this repository
(`docs/project/G4_V38_TRANSPORT_DIAGNOSTICS_V1.md`).

To switch to it, set `BUG_LAB_LLM_BASE_URL` to the notebook's printed URL, set the
notebook secret `BUG_LAB_LLM_API_KEY` to the same key, and recreate the lab container.
Nothing else changes: the proxy interface is identical either way. Before using a
public tunnel, read Kaggle's terms — see §6.5 of the plan.

---

## 4. Test results (T1–T9)

Raw output is in `artifacts/evidence/buglab-mac-v1/`. T1 and T3–T6 were run in
`--agents rules` mode; T2 and T7–T9 in `--agents llm` against the locally served
`qwen2.5:7b-instruct` described in §3.

| # | Test | Result | Evidence |
|---|---|---|---|
| T1 | Unit tests | **PASS** — 53 passed, 1 warning, 4.79 s | `T1_unit_tests.txt` |
| T2 | Startup header names the model | **PARTIAL** — backend detected and reported by the API and the UI, but the startup header never names it | `T2_llm_mode_startup.txt` |
| T3 | State API | **PASS** — `GET /lab/api/state` returns 200 and valid JSON | `T5_rules_all_bugs.json` |
| T4 | Write guard | **PASS** — 403 without the header, 400 with it | `T4_binding_and_user.txt` |
| T5 | Rules baseline, all 8 bugs | **PASS** — 8/8 resolved and verified | `T5_rules_all_bugs.json` |
| T6 | Approval paths | **PASS** — both rejection and timeout block the patch | `T6a_reject_path.json`, `T6b_expire_path.json` |
| T7 | Model mode, all 8 bugs | **PASS** — 8/8 resolved and verified, but the model authored only 2 of the 8 final patches | `T7_llm_all_bugs.txt/.json` |
| T8 | Model outage | **PASS** — incident still resolved and verified after the endpoint was killed mid-incident | `T8_model_outage.txt/.json` |
| T9 | URL rotation | **PASS** — needs the container recreated; no reload path | `T9_endpoint_rotation.txt/.json` |
| §6.3 | Local model smoke test | **PASS** — key enforced, nothing on the LAN | `T63_local_model_smoke.txt` |

The plan's T1 expected 41 tests. There are 53 at `4acf0c3`, because commits
`03606c3` and `e29d8b3` added more after the plan was written. All pass.

### T5 — every bug, rule engine only

| bug | severity | approval needed | diagnosis matched the broken file | detect s | resolve s |
|---|---|---|---|---|---|
| `js-reference-error` | P1 | yes | yes | 2.0 | 76.5 |
| `broken-stylesheet` | P2 | no | yes | 0.5 | 4.0 |
| `truncated-data` | P1 | yes | yes | 0.3 | 4.7 |
| `string-prices` | P1 | yes | yes | 0.0 | 5.6 |
| `broken-image-paths` | P3 | no | yes | 0.6 | 4.0 |
| `cart-handler` | P1 | yes | yes | 0.7 | 4.5 |
| `layout-overflow` | P3 | no | yes | 1.3 | 4.5 |
| `slow-api` | P3 | no | yes | 1.3 | 4.5 |

Every incident ended `resolved` with outcome *Resolved and verified*. Every P1
waited for approval and none applied a patch before one was given. Every patch
was authored by `Rule engine (no model)` — there is no model contribution in this
table, and none is claimed.

`js-reference-error` took 76.5 s to resolve against about 4.5 s for the rest. It
is one of the browser-detected bugs, so its timing includes waiting for the page
monitor rather than agent work.

### T7 — every bug with the model, and what the model actually contributed

Full table in `T7_llm_all_bugs.txt`, raw records in `T7_llm_all_bugs.json`.

All eight bugs were resolved and verified, and the diagnosis matched the injected
fault in all eight. That is the headline, and on its own it is misleading, so here is
the breakdown that matters:

| | count |
|---|---|
| Bugs resolved and verified | 8 / 8 |
| Final patch authored by the model | **2 / 8** |
| Patches the model attempted | 8 |
| Model attempts that failed verification | **6** |
| Bugs where the model never attempted | 3 |

`--agents llm` does not mean model-only. The model is consulted, its patch is put
through the same verification as any other, and the rule engine takes over when
verification fails. On this bug set the model's patches verified twice out of eight
attempts, and on three bugs (`broken-stylesheet`, `cart-handler`, `layout-overflow`)
the rule engine authored attempt 1 without the model being asked.

`slow-api` is the clearest case. The fault is a four-second delay in `config.json`.
The model patched `app.js` twice, both attempts failed verification, and the rule
engine corrected `config.json` on attempt 3 — 101.6 s in total.

Against the rule-engine baseline in T5, the model mode is slower (36–102 s per
incident versus roughly 4.5 s) and resolves the same eight bugs. **A 7B on an M4 is
not evidence about a 7B on a T4, and neither is evidence about anything beyond this
eight-bug demo set.**

One correction to this run: the first pass recorded `slow-api` in 2.0 s, which was a
stale read of `INC-0007` — layout-overflow's incident. That was a defect in
`driver_t7.py`, not in the lab. `slow-api` was rerun alone with an idle precondition
and a fresh-incident guard, and the rerun (`INC-0009`) is what the table reports. The
driver still carries the defect and is kept as-run.

### The repository suite on this branch

`pytest -q` with the virtualenv's `bin` on `PATH`, after all the work below:
**3570 passed, 1 failed, 43 skipped** in 9 m 47 s (`REPO_SUITE_after_llm_work.txt`).

An earlier run of the same suite reported six failures
(`REPO_SUITE_full_run.txt`). Five were `tests/test_bootstrap_lifecycle.py`, which needs
the virtualenv's `bin` on `PATH` the way CI runs it; that was an artefact of how that
run invoked pytest, and those five pass here. Only one real failure remains.

The remaining failure, `test_frontend_ui.py::test_root_endpoint_serves_html`, is
pre-existing and is finding 2 below. **No failure is caused by this branch** — the only
changes here are new paths that nothing else imports, and removing all of them leaves
that failure unchanged at `4acf0c3`.

### T6 — both approval paths leave the store broken

The approval timeout is 300 s (`LabOptions.approval_timeout`, `demo/bug_lab/engine.py:62`).
Both runs used `truncated-data`, a P1 that the server detects on its own, so neither
depended on a browser being open.

| path | incident | decision recorded | final status | outcome | store API at end |
|---|---|---|---|---|---|
| rejected | INC-0009 | `rejected` | `escalated` | Blocked because you rejected the change | 500 |
| expired | INC-0010 | `timeout` | `escalated` | Blocked because the approval timed out | 500 |

In the rejection run the store API returned 500 both before and after the decision —
the patch was never applied. In the expiry run the approval counted down
(`expires_in` 210 → 0 over 210 s of observation) and flipped to `escalated` at the
moment it reached zero, with no patch applied and the store still failing. The full
countdown is in `T6b_expire_console.txt`.

Neither path invented a resolution, which is the property the test exists to check.

### T4 — the lab is not reachable off this machine

```
docker port          8790/tcp -> 127.0.0.1:8790
host listener        TCP 127.0.0.1:8790 (LISTEN)
container user       uid=10001(lab) gid=999(lab)
http://127.0.0.1     200
http://192.168.1.105 000   (connection refused — the intent)
```

---

## 6. Findings for the core branch

For the owner of `feat/atlasops-local-core`. None of them was fixed here.

1. **`demo/bug_lab/workspace.py` pulls `training.sft_provenance` into the lab's
   dependency graph.** The lab is otherwise self-contained in `demo/` plus one stdlib
   helper. This makes a minimal container image carry `training/` and `config/` for a
   single path-safety predicate. Moving `has_redirecting_path_component` into
   `demo/bug_lab/workspace.py`, or into a small shared stdlib-only module, would keep
   the lab deployable on its own. Not urgent; the image works as it is.

2. **`tests/test_frontend_ui.py::test_root_endpoint_serves_html` fails in a fresh
   checkout.** `GET /` returns 503 *"React frontend build missing"* because
   `frontend/dist/` is gitignored (`.gitignore:69`) and no build has been run. It
   passes in a checkout where someone has run `npm run build`, which makes the suite
   non-hermetic: the same commit passes or fails depending on untracked build output.
   Confirmed pre-existing — it fails identically with every file from this branch
   removed, at `4acf0c3`. Either the test should skip when `frontend/dist` is absent,
   or the suite should build the frontend first. **Not caused by this branch and not
   fixed here.**

3. **The startup banner never names the backend.** `demo/bug_lab/__main__.py` prints
   the control room URL, the store URL and the working copy, and nothing else. A
   container that fell back to the rule engine looks identical on stdout to one using
   a 7B. The information exists — `/lab/api/state` returns `backend.label` and the
   control room header shows it — it just is not printed. The plan's T2 expects it in
   the startup header. One line after the existing banner would close it. Using
   `--agents llm` rather than `auto` is the current protection, because it refuses to
   start without a model.

4. **The model's fallback to the rule engine is silent.** During T8 the endpoint was
   killed mid-incident; the incident resolved correctly via the rule engine, and
   `docker logs buglab` recorded nothing at all about the model being unreachable. An
   operator would see a healthy incident and never know the model had gone. The
   fallback behaviour itself is good; the silence is the problem.

5. **`/lab/api/state` keeps serving the last closed incident.** After an incident
   closes, `incident` continues to hold it with no field distinguishing "this just
   finished" from "this is live", and `active` alone is not enough while a new
   incident is still opening. Any poller must track incident ids itself to avoid
   reading stale data. This cost three bad test runs here before being understood —
   see the caveats in `T7_llm_all_bugs.txt` and `T8_model_outage.txt`. A monotonic
   counter, or clearing `incident` on reset, would make the API safe to poll naively.

Findings 3–5 are observations about the lab's behaviour, not crashes. The lab resolved
every bug it was given, in both modes, and the approval gate held in every case.

### 6.1 Software follow-up

All five findings have application fixes and regression coverage. This is software
verification, not a new Mac acceptance run or a scientific result.

| Finding | Correction | Regression |
|---|---|---|
| 1. Training/config imports | The workspace path-safety predicate is local and standard-library only. It retains fail-closed symlink, junction and reparse-point checks. | `tests/test_bug_lab_standalone.py` imports the minimal application with training/config unavailable and checks path rejection. |
| 2. Fresh-checkout frontend | The root-route tests use an owned temporary frontend fixture, and separately require 503 when the build is absent. No skipped missing-build test or production fallback is added. | `tests/test_frontend_ui.py` covers present and absent builds independently of ignored `frontend/dist`. |
| 3. T2 backend banner | Lifespan startup reports the actual selected backend after detection, including a safe startup-fallback note. Explicit model mode still refuses an unavailable backend. | `tests/test_bug_lab_startup_logging.py` covers rules, model, auto fallback and failed startup using doubles. |
| 4. Silent outage | Runtime fallback warnings include incident ID, stage and a fixed safe failure reason. No raw model exception, credential or provider body is logged. Incident events retain model/rule attribution. | `tests/test_bug_lab_state_logging.py` covers diagnosis/patch outages and unexpected credential-bearing exceptions. |
| 5. Stale incident | Reset and successful new injection clear only the current incident slot. Completed history and JSONL remain. State exposes `incident_state` and `incident_sequence`. | Python lifecycle tests and the real-browser reset/history/new-incident regression. |

The state API uses `incident_state` values `idle`, `pending`, `active` and `closed`.
`pending` means an injection awaits detection and `incident` is null. `active`
includes approval waits. `closed` is the last result before the next reset or
injection. `incident_sequence` increments when an incident opens and does not reset
during the process lifetime. Use the returned incident ID and exact approval digest
for decisions. This initial follow-up kept the sequence in memory. The
owned-workspace follow-up below makes it durable across explicit resume.

No Dockerfile or Mac runtime setting changes are required. The existing image
recipe still copies training/config, but the application no longer imports them.
Do not remove those copies from the working deployment as part of this follow-up.

The Mac owner must smoke-test the accepted software with the existing configuration:

1. Rebuild the existing image from accepted main when separately authorized.
2. Confirm T2 logs the actual rules/model backend after selection. For `auto`
   fallback, require a rule label and a safe reason. For unavailable `llm`, require
   startup failure instead of a rule-only success.
3. Repeat the authorized T8 outage and confirm diagnosis or patch fallback is
   visible in container logs and incident evidence. Verify exact approvals and
   objective checks still govern the rule repair.
4. Reset a completed incident. Require `incident: null`, `incident_state: idle`,
   unchanged completed JSONL and visible history. Inject again and require a
   pending or newly active incident, never the prior result.
5. Confirm fresh Store telemetry, all eight scenario repairs, rejected/expired
   approvals, rollback and evidence download on the actual Mac browser.

This software task does not perform those Mac actions, change credentials, restart
services, run models, or update the historical T2/T7/T8/T9 classifications.

### 6.2 Owned-workspace software follow-up

The later application follow-up adds explicit `--resume` for a directory created
by the new owned-workspace format. It preserves `site/`, completed
`incidents.jsonl`, and a durable incident sequence. Older unmarked workdirs are
not adopted or migrated automatically.

Shutdown records an unfinished incident as `cancelled`. Crash recovery records it
as `interrupted`, preserving the prior stage, approval, and verifier data without
replaying an approval or repair. Both outcomes remain unverified. Digests now bind
the workspace identity as well as the exact patch.

The new `/health` readiness probe returns 503 before startup, after shutdown, or
after an evidence-write failure. A deliberately broken Store can still have a
ready application; readiness does not establish resolution or model health.

See the [application resume and readiness instructions](../../demo/bug_lab/README.md#keep-incident-evidence-across-restarts).
These are source-level changes, not a new physical-Mac acceptance run. The
historical run commands, images, results, failures, and classifications above
remain unchanged. The working Mac Docker recipe and runtime configuration are
not changed by this follow-up.

The owner must separately smoke-test foreground shutdown, volume retention,
explicit resume, rejected old approval receipts, and evidence download on the
Mac. Preserve an older workdir before any owner-approved rebuild or migration.
No model experiment, fault execution, infrastructure startup, or gate promotion
is authorized by these instructions.

---

## 7. Risks as they actually applied (section 9)

The model runs on this Mac rather than on Kaggle (§3.1), so several of the plan's
risks do not arise at all. They are kept in the table because they return the moment
anyone switches to the notebook path.

| Risk from the plan | What happened here |
|---|---|
| Kaggle session ends | Does not arise locally. Returns on the notebook path. |
| ngrok URL changes every session | Does not arise locally. Rotation itself is tested by T9. |
| Public model URL abused | No public URL exists. Ollama binds `127.0.0.1`, the proxy publishes to `127.0.0.1`, and both refuse the LAN address. Verified in `T63_local_model_smoke.txt`. |
| Kaggle terms uncertain | Avoided rather than resolved. Still open for the notebook path. |
| Long prompts cut off | `OLLAMA_CONTEXT_LENGTH=8192` set on the local server, as in Cell 2. |
| Slow or failed model calls | `BUG_LAB_LLM_TIMEOUT=180`. Outage behaviour measured by T8. |
| Lab not on main | No longer applies — PR #235 merged as `4acf0c3`. |
| Lab has no login | Published to `127.0.0.1` only; verified by T4. |
| arm64 vs amd64 | Image built `linux/arm64`, matching the Mac. For x86 nodes use `docker buildx build --platform linux/amd64`. |
| Expecting too much from the model | Each patch records its author, so model and rule-engine fixes are never conflated. §4 reports both. |

Two risks the plan does not list, found here:

- **An unauthenticated model endpoint is reachable from inside the Colima VM.**
  `host.docker.internal` resolves to `192.168.5.2` from any container, and that reaches
  the Mac's loopback. A service bound to `127.0.0.1` on the Mac is therefore *not*
  private to the Mac — every container, including the `kind` cluster running alongside
  this lab, can reach it. This is why the keyed proxy is kept locally and why Ollama's
  own port is never given to the lab directly.
- **The model identity is not in the startup log.** `docker logs buglab` prints three
  URL lines and nothing about the backend, so a container that silently fell back would
  look identical. `--agents llm` refuses to start without a model, which is what makes
  the mode trustworthy here; `--agents auto` would not be. Reported as a core finding.

---

## 8. The plan's checklist (section 10)

| Step | Work | State |
|---|---|---|
| 1 | Get the code: merge PR #235, or pin a branch worktree | **Done** — #235 merged as `4acf0c3`; this branch is cut from it. The plan's worktree workaround is no longer needed. |
| 2 | Phase 1: serve Qwen2.5-7B, keyed proxy, smoke test | **Done, by a different route** — the model is served on this Mac (§3.2) instead of on a Kaggle T4 through ngrok. Smoke test passed. The Kaggle notebook is prepared but not run. |
| 3 | Phase 2: add `Dockerfile.buglab`; run T1–T9 | **Done.** T1, T3–T6 in rule-engine mode; T2 and T7–T9 against the model. T2 is partial — see §6, finding 3. |
| 4 | Phase 3: EKS | **Not started** — optional, costs money, not authorized. |

### Housekeeping from the plan's investigation

| Item | State |
|---|---|
| Stale GCP "LIVE" section in `CLAUDE.md` | **Already done** before this branch — the file now carries an UNVERIFIED notice. |
| Rotate the Grafana password; confirm Azure OpenAI and OpenRouter key rotation | **Owner only** — tracked in `docs/project/EXTERNAL_CLOSURE_ACTIONS_20261027.md`. |
| Check GCP billing for `cloudsre-v3-amd` | **Owner only** — no `gcloud` on this machine. |
| Quit Docker when not in use | `colima stop` when finished. Also stop the model: `pkill -f "ollama serve"`, and `docker rm -f buglab buglab-llm-proxy`. |
| Homebrew `ollama` 0.30.7 on this machine is broken | Its `llama-server` binary is missing from the Cellar, so `ollama serve` accepts requests and then fails every generation. The `/Applications/Ollama.app` bundle (0.33.2) works and is what §3.2 uses. A `brew reinstall ollama` would probably fix the CLI, and was not attempted. |

---

## 9. Combined acceptance after the core PR merged

Core PR #236 (`feat/atlasops-local-core`) merged into `main` as `20e3c96`, bringing four
lab commits — including *contain malformed model endpoint errors* and *harden model
boundaries and verified incident evidence* — and two new test files. `origin/main` was
merged into this branch, the image was rebuilt from the merged tree, and T7–T9 were
rerun against it. Full record in `POSTMERGE_ACCEPTANCE.txt`.

| Test | Pre-merge | Post-merge |
|---|---|---|
| T7, eight bugs | 8/8 resolved and verified | 8/8 resolved and verified |
| T7, final patches by the model | 2 / 8 | **1 / 8** |
| T7, model attempts (failed) | 8 (6) | 4 (3) |
| T8, outage mid-incident | resolved and verified | resolved and verified |
| T9, rotation | needs container recreated | needs container recreated |
| Suite | 3570 passed, 1 failed | **3644 passed, 1 failed**, 43 skipped |

The model's share of accepted patches moved from 2/8 to 1/8 across the two runs. The
model is non-deterministic and this is one run each way, so **that difference is not
evidence that the merge changed anything.** What both runs agree on is that the model
contributes a small minority of accepted patches on this eight-bug set, and that the
rule engine carries the rest.

The one suite failure is the same pre-existing `test_root_endpoint_serves_html`.

Findings 1, 3 and 4 are all still open after the merge: `workspace.py` still imports
`training.sft_provenance`, the startup banner still prints only three URL lines, and
losing the endpoint mid-incident still writes nothing to the log. The core did add a
`note` field on the backend object for endpoint errors, which is the right place for
this, but it is empty on a healthy start.

---

## What is not verified

- **The Kaggle path was not run.** The notebook is prepared but has not executed on
  Kaggle. It needs a Kaggle account with a verified phone, an ngrok authtoken and two
  notebook secrets, none of which are available to this session. Every statement about
  the *tunnel* path is therefore read from the source, not measured. The model itself
  was exercised locally instead (§3.1), which is a deviation from the plan, not a
  completion of it.
- **No T4 GPU timings exist.** All model timings here are an Apple M4 serving a 4-bit
  7B. They say nothing about T4 latency, throughput or concurrency.
- **The model's patch quality was measured on eight demo bugs, once each.** Two of
  eight final patches came from the model. That is a single small sample on a fixed,
  synthetic bug set, and it is not a benchmark of the model, the prompts or the agent
  pipeline.
- **Phase 3 (EKS) was not run** and incurs AWS charges.
- Nothing here is gate evidence. G4 and G9 remain NOT_PASSED and frozen; the project
  remains NOT_CERTIFIED.
