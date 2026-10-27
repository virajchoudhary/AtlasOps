# G14/G15 local offline progress record — 21 October 2026

**Scope:** clean-checkout software verification, non-empirical local demo and a
locally built source-review package. No peer host, deployment, credentials,
operator configuration or external submission was involved. Nothing was pushed.
G14 and G15 remain **PARTIAL**; scientific status remains `NOT_CERTIFIED`.
This record is not independent review and approves nothing.

## Source and host

- Source: fresh full-history clone, `origin/main` = `b23d6cb4319b77b4d8606934e401141bcb01d23f`, 801 tracked files, clean tracked tree.
- Host: Apple M4 (arm64), 16 GiB, macOS (Darwin 24.6.0); Python 3.12.11 venv from
  `pip install -e ".[dev]"` (`pip check`: all compatible); Node 25.9.0, npm `ci`.
  The Windows development lock is not installable on macOS, so this is CI's
  Linux-style install path, not the canonical Windows lock.
- Run the suite with the venv **activated**: `infra/setup_impl.sh` resolves `python3`
  from `PATH`, so calling `.venv/bin/python -m pytest` alone makes five
  `test_bootstrap_lifecycle.py` cases fail on a missing `bcrypt` in the system Python.

## Results

| Check | Result |
| --- | --- |
| `npm ci` + `npm run build` (frontend) | built; only Vite's >500 kB chunk-size advisory |
| `python -B -m demo.launcher --check` | `local_presentation_ready: true`, no errors |
| Browser: `http://127.0.0.1:7864/` (read-only launcher) | Overview shows READY_FOR_REVIEW / NOT_CERTIFIED / G4 NOT_PASSED / READ-ONLY; rehearsal labelled SYNTHETIC / NON-LIVE / NON-EMPIRICAL; alert stage ran; no console errors; server stopped afterwards |
| `ruff check . --select E9,F63,F7,F821,F401,F841` (CI rule set) | All checks passed |
| `compileall` (onboarding target list) | OK |
| `python -m build` | sdist + wheel built (from a checkout with no extra venvs inside it) |
| `pytest tests/` (full, venv activated) | 3402 passed, 43 skipped, **1 failed** in 9 min 24 s. The failure is an intermittent race in `tests/g9_approval_process.py` (13/20 isolated runs failed), fixed on the G9 handoff branch (0/20). Skips: 34 need the optional Torch/TRL/tokenizers stack (absent from the dev venv by design) and 9 are Windows-only or Linux-only platform checks. In the separate pinned venv (plus `pytest-asyncio` 1.4.0) those optional files pass, except `test_grpo_reload_installed.py` (Linux x86_64 seccomp only) and `test_sft_mask_proof.py`, whose 10 cases still skip without a cached real tokenizer, which was deliberately not downloaded |
| `npm run test` (frontend) | **63/63 after the fix below**; 62/63 before on Node 25 |
| CI pinned trainer job, reproduced in a separate venv | see the G9 branch record (installed-trainer 9/9; reload isolation Linux-only by design) |

### Defect fixed: frontend unit tests fail on Node 25

The onboarding guide requires "Node.js 22.12 or newer". On Node 25.9.0,
`PersistedIncidentHistory` failed `expect(window.localStorage.length).toBe(0)`
with `undefined`: Node 25 enables an experimental global `localStorage` that
shadows jsdom's Storage. Commit `68488d1` on this branch adds
`execArgv: ["--no-experimental-webstorage"]` to the Vitest config. Verified
63/63 on Node 25.9.0 and on Node 22.12.0 (the minimum). Application code and
the assertion itself are unchanged.

### Browser E2E (`npm run test:e2e`, Playwright 1.63.0, Chromium headless shell 153, macOS arm64)

Browsers were installed only under the local review directory (`PLAYWRIGHT_BROWSERS_PATH`),
with `CI=1` so no unknown server is reused.

| Run | Result |
| --- | --- |
| 1, before fix | 51 passed, 1 failed: `faq-glyph.spec.ts:71` reduced-motion (expected frame "0", received "1") |
| reduced-motion test repeated ×30, unfixed spec | 20 passed, 10 failed — every failure "0" → "1" |
| reduced-motion test repeated ×30, fixed spec | 30 passed |
| 2, after fix, concurrent with a full pytest run | 51 passed, 1 failed: `founder-touch.spec.ts:56` 90 s timeout (CPU contention) |
| `founder-touch.spec.ts` alone ×3 | 3 passed, ~44 s each — timeout not reproduced; not changed |
| 3, final, machine otherwise idle | **52 passed** |

### Defect fixed: reduced-motion E2E sampled the frame before the first paint

`FAQGlyphBackdrop` sets `data-animation-state="reduced-motion"` in `sync()` before the glyph
font has loaded; the first *static* draw then moves `data-frame` from "0" to "1". The test
could sample "0" and see "1" 500 ms later although nothing animates. Commit `d4c5bef` waits for
the first paint before sampling; the no-change assertion and the resume assertion are unchanged.
Classification: TEST_DEFECT (timing), not an application motion defect.

## Submission package (`python -B -m scripts.package_submission --output-dir <new>`)

Run from clean `main` `b23d6cb` into `package/submission-b23d6cb/` of the local review directory:
660 assets, `asset_inventory_sha256` `edfd7ec8c51297e09f96df98054515ecf8ebff3411cddc9d68973fedefc80b6a`,
`status=NOT_CERTIFIED`, `empirical_metrics=null`, `package_readiness=READY_FOR_REVIEW`; declared gates
G4/G9 NOT_PASSED, G6/G8/G12 IMPLEMENTED / EMPIRICAL EVIDENCE MISSING, G13 REOPENED, G10/G11 OUT_OF_SCOPE.
`python -B -m scripts.check_submission_links`: links resolved in 30 review documents.

**Finding:** the tracked `artifacts/SUBMISSION_MANIFEST.json` was generated on 5 Oct 2026
(464 assets, older scope wording) and does not describe `b23d6cb`. It was not edited; the fresh
manifest above is the one matching current source. Whether to refresh the tracked copy is a
maintainer decision.

## Source-review package (local, not the owner's receipt)

Built and verified with `python -m scripts.package_source_review --expected-source-sha b23d6cb4319b77b4d8606934e401141bcb01d23f`
(and `--verify`): `package_readiness=READY_FOR_REVIEW`,
`upstream_ancestry_and_mit_license=PASS`, `scientific_status=NOT_CERTIFIED`,
`execution_authorized=false`, 110 tracked frozen-evidence files.

| Field | Value |
| --- | --- |
| Archive | `atlasops-source-b23d6cb4319b.zip`, 28,867,838 bytes |
| Archive SHA-256 | `d6daef58eaa75b17fb8071877f957b5f32c9171cdad249035aea246a3f19ea7f` |
| Source tree | `216a56861eddd5f7c1739dd814865bce57533195` |

This archive comes from a macOS checkout with Python 3.12.11. Per
`SOURCE_REVIEW_PACKAGE.md`, a different checkout conversion or Python/zlib
version legitimately changes the archive hash, so it is **not** a substitute for
the owner's Windows package. The owner's ZIPs, receipts and checksums under
`C:/AtlasOps/.audit/peer-handoff-20261019/` were not available and were not
compared. It also predates the fix above; a package for any later reviewed
main must be regenerated. Third-party publication rights were not recertified.

## Still blocked (owner decisions)

Named peer-host owner, audience and independent reviewer; non-destructive host
acceptance; project-lead claim review against actual raw outcomes; submission
owner authorization and an actual external receipt. Report and slides were not
edited: no new accepted scientific result exists to add, and G4/G9 failure,
missing G6/G8/G12/G13 evidence, G10/G11 exclusion and the absence of any
submission remain the truthful claims.

## Peer-host preparation

[Peer-host acceptance checklist candidate](G14_PEER_HOST_ACCEPTANCE_CHECKLIST_CANDIDATE_20261021.md):
ten non-destructive checks; host, owner, audience and reviewer `UNSPECIFIED`. Not executed.

## Status against the PR checklist

None satisfied. A local package was built and hashed, but the owner's archive
and receipts were not shared, so the first box stays open.

## Record metadata

- Branch `handoff/g14-g15-peer-submission-20261020`; remote base `a4a3fa7`; source `main` `b23d6cb`.
- Gate status before and after: G14 **PARTIAL**, G15 **PARTIAL / NOT_CERTIFIED** (unchanged).
- Files: `frontend/vite.config.ts` (Vitest `execArgv`), `frontend/e2e/faq-glyph.spec.ts` (wait for first paint),
  this record, the peer-host checklist candidate, the brief (one "Local progress" line).
  Report, slides and application code unchanged.
- Final SHAs, full test counts and skip reasons: `~/Documents/AtlasOps-review/local-closure-20261021/README.md` (local only).
- **No remote push, deployment or submission occurred. No empirical result was fabricated.**
- Addendum (22 Oct 2026): this record was later pushed with the local closure work in
  `chore/local-closure-20261022`; the statements above describe the state when it was written.
