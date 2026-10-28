# Findings reported to the owner of `feat/atlasops-local-core`

Posted 2026-10-28 14:06 UTC as a comment on PR #237:
https://github.com/virajchoudhary/AtlasOps/pull/237#issuecomment-6471170749

The posted version is this text from the horizontal rule down, with two edits for
its destination: "PR #237" written as "this PR", and each evidence filename given
its `artifacts/evidence/buglab-mac-v1/` prefix.

---

Hi Viraj — while running the Mac runtime acceptance for the bug lab
(`feat/atlasops-mac-runtime`, PR #237) I hit five things in the core lab code. I have
not touched any of them, per the brief: no rewriting of the core application, model
client or agent pipeline. Four are still open after I merged `origin/main` carrying
your core PR; one you have partly addressed already. Numbering matches §6 of
`docs/project/BUG_LAB_MAC_RUNBOOK_V1.md` on my branch.

All of this was measured against `qwen2.5:7b-instruct` served locally on an M4 Mac
(not the Kaggle T4 in the plan — reasons in §3.1 of the runbook). None of it is
gate evidence.

## Still open

**3. The startup banner never names the backend.**
`demo/bug_lab/__main__.py:67-68` prints the control room URL, the store URL and the
working copy, and nothing else. A container that fell back to the rule engine looks
identical on stdout to one driving a 7B. The information already exists —
`/lab/api/state` returns `backend.label`, and the control room header shows it — it
just is not printed. The run plan's T2 expects the backend in the startup header, so
T2 is recorded as PARTIAL on my branch for this reason alone. One line after the
existing banner closes it. My current protection is passing `--agents llm` rather
than `auto`, because `llm` refuses to start without a model.
Evidence: `artifacts/evidence/buglab-mac-v1/T2_llm_mode_startup.txt`.

**4. The fallback from the model to the rule engine is silent.**
In T8 I killed the model endpoint mid-incident. The incident resolved correctly
through the rule engine — which is the right behaviour — but `docker logs buglab`
recorded nothing at all about the model being unreachable. `demo/bug_lab/engine.py`
has only two log sites (`log.exception` at :256 and :337), and neither covers this
path: `diagnose_llm`/`remediate_llm` (`demo/bug_lab/agents.py:521,537`) return `None`
on an unparseable answer, and a transport failure resolves to the rule engine the same
way. So three distinct outcomes — model not used, model unreachable, model answered
but unparseable — are indistinguishable in the logs. An operator sees a healthy
incident and never learns the model went away.
Your merge added a `note` field to the backend for endpoint errors, which looks like
the right home for this, but it is empty on a healthy start and I saw nothing in it
during a mid-incident outage.
Evidence: `POSTMERGE_T8_model_outage.json`, `T8_model_outage.txt`.

**5. `/lab/api/state` keeps serving the last closed incident.**
After an incident closes, `incident` keeps holding it (`engine.py:630-648`) with no
field distinguishing "this just finished" from "this is live". `active` alone is not
enough, because there is a window where a new incident is opening and `active` has
not flipped yet. Any poller has to track incident ids itself. This cost me three bad
test runs before I understood it — including one where I reported `slow-api` resolving
in 2.0 s, which was a stale read of the previous incident, not a result. A monotonic
counter, or clearing `incident` on reset, would make the endpoint safe to poll
naively.
Evidence: the caveats in `T7_llm_all_bugs.txt` and `T8_model_outage.txt`.

**1. `demo/bug_lab/workspace.py:11` pulls `training.sft_provenance` into the lab's
dependency graph.**
`from training.sft_provenance import has_redirecting_path_component` is the only
reason a minimal lab container has to carry `training/` and `config/`. The lab is
otherwise self-contained in `demo/` plus stdlib. Moving the predicate into
`workspace.py`, or into a small stdlib-only shared module, would make the lab
deployable on its own. Low priority — the image works as it is.

## Partly yours already

**2. `tests/test_frontend_ui.py::test_root_endpoint_serves_html` fails in a fresh
checkout.**
`GET /` returns 503 "React frontend build missing" because `frontend/dist/` is
gitignored (`.gitignore:69`) and no build has run. It passes in a checkout where
someone has run `npm run build`, so the same commit passes or fails depending on
untracked build output. I confirmed it is pre-existing: it fails identically at
`4acf0c3` with every file from my branch removed. It is the single failure in both of
my suite runs (3570 passed / 1 failed pre-merge, 3644 passed / 1 failed post-merge).
Either the test should skip when `frontend/dist` is absent, or the suite should build
the frontend first.
Evidence: `REPO_SUITE_after_llm_work.txt`, `POSTMERGE_REPO_SUITE.txt`.

## One thing that is not a finding, but you should know

Across eight bugs with the model in the loop, every incident resolved and verified,
but the model authored only **2 of 8** final patches pre-merge and **1 of 8**
post-merge; the rule engine carried the rest. Post-merge the model made 4 patch
attempts and 3 failed verification. `slow-api` is the clearest case: the fault is a
delay in `config.json`, the model patched `app.js` twice and failed both times, and
the rule engine fixed `config.json` on attempt 3.

The 2/8 → 1/8 move is **not** evidence your merge changed anything — one run each
way with a non-deterministic model cannot be separated from variance. I am flagging
it only so "8/8 resolved" is not read as "the model fixed eight bugs".
Evidence: `T7_llm_all_bugs.json`, `POSTMERGE_T7_llm_all_bugs.json`.

Happy to re-run any of this if you want a measurement after a change.
