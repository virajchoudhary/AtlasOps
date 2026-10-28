# Bug lab on the Mac — runbook and run record

Implements sections 6, 7, 9 and 10 of *AtlasOps Bug Lab: Run Plan* (27 October 2026)
on this MacBook. Section 8 (Amazon EKS) is optional and was not run: it needs AWS
spend, which is not authorized.

Status: **Phase 2 run. Phase 1 prepared but not run** — see [What is not verified](#what-is-not-verified).

| | |
|---|---|
| Branch | `feat/atlasops-mac-runtime` |
| Base | `4acf0c3` (the merge of PR #235 into `main`) |
| Image | `atlasops-buglab:local`, 51.7 MB, `linux/arm64` |
| Host | macOS 24.6.0 on Apple silicon, Docker 29.5.2 via Colima |
| Lab port | `8790`, published to `127.0.0.1` only |

A demo result here is not gate evidence and does not certify anything.

---

## 1. What changed in this branch

| File | Why |
|---|---|
| `Dockerfile.buglab` | New. Section 7.2 of the plan, with the corrections below. |
| `notebooks/buglab_kaggle_qwen_serving.ipynb` | New. Section 6 cells, ready to run on Kaggle. |
| `docs/project/BUG_LAB_MAC_RUNBOOK_V1.md` | This file. |
| `artifacts/evidence/buglab-mac-v1/` | New. Raw output of every run below, plus the drivers that produced it. |

No core application file was changed. Two findings for the core branch are in
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

## 3. The model on Kaggle (section 6)

`notebooks/buglab_kaggle_qwen_serving.ipynb` holds the four cells: installs, Ollama plus
model warm-up, the key-checking proxy on port 8000, and the ngrok tunnel opened last.
Cell order matters — opening the tunnel before the model answers is what produced
HTTP 524 in an earlier run of this repository
(`docs/project/G4_V38_TRANSPORT_DIAGNOSTICS_V1.md`).

Settings file, outside the repository, never committed:

```bash
mkdir -p ~/atlasops-local && chmod 700 ~/atlasops-local
cat > ~/atlasops-local/buglab.env <<'EOF'
BUG_LAB_LLM_BASE_URL=https://<your-id>.ngrok-free.app/v1
BUG_LAB_LLM_MODEL=qwen2.5:7b-instruct
BUG_LAB_LLM_API_KEY=<the same key as the Kaggle secret>
BUG_LAB_LLM_TIMEOUT=180
EOF
chmod 600 ~/atlasops-local/buglab.env
```

Before using a public tunnel, read Kaggle's terms. The run plan (§6.5) found no official
statement either way, and treats that as an open question rather than permission.

---

## 4. Test results (T1–T9)

Raw output is in `artifacts/evidence/buglab-mac-v1/`. All runs were in
`--agents rules` mode, because Phase 1 was not run.

| # | Test | Result | Evidence |
|---|---|---|---|
| T1 | Unit tests | **PASS** — 53 passed, 1 warning, 4.79 s | `T1_unit_tests.txt` |
| T2 | Startup header names the model | **NOT RUN** — needs the Kaggle model | `T2_container_startup.log` (rules mode) |
| T3 | State API | **PASS** — `GET /lab/api/state` returns 200 and valid JSON | `T5_rules_all_bugs.json` |
| T4 | Write guard | **PASS** — 403 without the header, 400 with it | `T4_binding_and_user.txt` |
| T5 | Rules baseline, all 8 bugs | **PASS** — 8/8 resolved and verified | `T5_rules_all_bugs.json` |
| T6 | Approval paths | **PASS** — both rejection and timeout block the patch | `T6a_reject_path.json`, `T6b_expire_path.json` |
| T7 | Model mode | **NOT RUN** — needs the Kaggle model | — |
| T8 | Model outage | **NOT RUN** — needs the Kaggle model | — |
| T9 | URL rotation | **NOT RUN** — needs the Kaggle model | — |

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

### The repository suite on this branch

`pytest tests/ -q` at this branch: **3565 passed, 6 failed, 43 skipped** in 9 m 20 s
(`REPO_SUITE_full_run.txt`).

Five of the six are an artefact of how that run invoked pytest, not real failures. The
`tests/test_bootstrap_lifecycle.py` cases need the virtualenv's `bin` on `PATH`, the way
CI runs them; with it they are **10 passed in 22.8 s**
(`REPO_SUITE_bootstrap_with_venv_on_path.txt`).

The remaining failure, `test_frontend_ui.py::test_root_endpoint_serves_html`, is
pre-existing and is finding 2 below. **No failure is caused by this branch** — the only
changes here are four new paths, and removing all of them leaves that failure unchanged
at `4acf0c3`.

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

For the owner of `feat/atlasops-local-core`. Neither was fixed here.

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

No defect was observed in the lab's own behaviour during these runs.

---

## 7. Risks as they actually applied (section 9)

| Risk from the plan | What happened here |
|---|---|
| Kaggle session ends | Not reached — Phase 1 not run. |
| ngrok URL changes every session | Not reached. Mitigation is in §3 and T9. |
| Public model URL abused | Proxy requires a Bearer key; tunnel opened last; URL never shared. |
| Kaggle terms uncertain | Unresolved. Flagged, not assumed. |
| Long prompts cut off | `OLLAMA_CONTEXT_LENGTH=8192` set in Cell 2. |
| Slow or failed model calls | `BUG_LAB_LLM_TIMEOUT=180`; model warmed before the tunnel. |
| Lab not on main | No longer applies — PR #235 merged as `4acf0c3`. |
| Lab has no login | Published to `127.0.0.1` only; verified by T4. |
| arm64 vs amd64 | Image built `linux/arm64`, matching the Mac. For x86 nodes use
  `docker buildx build --platform linux/amd64`. |
| Expecting too much from the model | Rule-engine fixes are reported as rule-engine fixes. |

---

## 8. The plan's checklist (section 10)

| Step | Work | State |
|---|---|---|
| 1 | Get the code: merge PR #235, or pin a branch worktree | **Done** — #235 merged as `4acf0c3`; this branch is cut from it. The plan's worktree workaround is no longer needed. |
| 2 | Phase 1: Kaggle notebook, secrets, Ollama, proxy, ngrok; smoke test | **Prepared, not run** — needs Kaggle and ngrok accounts. |
| 3 | Phase 2: add `Dockerfile.buglab`; run T1–T9 | **Done for T1, T3–T6.** T2, T7–T9 need Phase 1. |
| 4 | Phase 3: EKS | **Not started** — optional, costs money, not authorized. |

### Housekeeping from the plan's investigation

| Item | State |
|---|---|
| Stale GCP "LIVE" section in `CLAUDE.md` | **Already done** before this branch — the file now carries an UNVERIFIED notice. |
| Rotate the Grafana password; confirm Azure OpenAI and OpenRouter key rotation | **Owner only** — tracked in `docs/project/EXTERNAL_CLOSURE_ACTIONS_20261027.md`. |
| Check GCP billing for `cloudsre-v3-amd` | **Owner only** — no `gcloud` on this machine. |
| Quit Docker when not in use | `colima stop` when finished. |

---

## What is not verified

- **Phase 1 was not run.** The notebook is prepared but has not executed on Kaggle. It
  needs a Kaggle account with a verified phone, an ngrok authtoken and two notebook
  secrets, none of which are available to this session. Every statement about the model
  path is therefore a prediction from the source, not a measurement.
- **T2, T7, T8 and T9 require that model** and were not run.
- **Phase 3 (EKS) was not run** and incurs AWS charges.
- Nothing here is gate evidence. G4 and G9 remain NOT_PASSED and frozen; the project
  remains NOT_CERTIFIED.
