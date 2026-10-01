# AtlasOps Cleanup Audit: 2026-10-01

Base: GitHub and local main `149efaf318513ffc4108a7c971abee9b56e6bdbf`.
Work: `chore/codebase-cleanup-20261001` in an isolated checkout.
This is a local maintenance audit, not empirical gate closure or certification.

## Inventory and Decisions

The base contains 455 tracked files, about 7.6 MB of checkout bytes. The original
`C:\AtlasOps` checkout is 313 commits behind this main and has unrelated G4 edits.
Registered worktrees, untracked preflight records, patches, backup files, and
negative test outputs are preserved; their age or filename is not deletion proof.

| Candidate | Decision | Verification |
|---|---|---|
| `eval.py`, `leaderboard.py`, `bench/quick_eval.py`, `inference.py` | Replace redundant live orchestration with disabled compatibility entrypoints. They bypassed governed incident admission and used claimed outcomes. | Import and subprocess retirement tests; canonical evaluator tests remain. |
| `make sft` / `make grpo` | Retire incomplete shortcuts; retain current trainers and readiness contracts. | Recipe tests; no live/model operation. |
| Unused and duplicate imports | Remove unused bindings; retain `_bounded_speed_score` compatibility export. | Runtime unused-code lint; affected tests. |
| Build context | Exclude secrets, worktrees, caches, local data/checkpoints and internal attempt ledger. Retain tracked research evidence. | `.dockerignore` contract; source/wheel archive inspection. No Docker build claimed. |
| Local test clutter | Add narrow Git ignores, without deleting preserved failures. | Git check-ignore tests include explicit retained paths. |
| Judge guide and benchmark instructions | Remove active ungoverned execution instructions; keep attribution and historical numeric tables. | Guide tests and current-claim tests. |
| `docs/RELEASE_READINESS.md` | Remove stale generated snapshot from tracked source; preserve in Git history and regenerate locally on demand. | Release checker tests; snapshot remains excluded from submission inventory. |
| Submission inventory | Include maintenance/build boundaries and refresh derived hashes. | Stage 15 generator and full hash verification. Status remains `NOT_CERTIFIED`. |
| Unused direct dependencies | Remove `openai`, `anthropic`, `google-cloud-pubsub`, `rich`, `typer` declarations. Regenerated dev lock drops nine orphaned packages; Gradio still requires Rich/Typer transitively. | Same retained pins; environment validation below. |

## Retained, Not Junk

- Core incident pipeline, explicit approval, objective verification, tools and UI.
- Stage 6/8/9 evaluators, SFT serializer, prospective GRPO/G13 candidate modules.
- Frozen protocols, scenario splits, negative/interrupted G4 attempts, synthetic
  research data, historical results, provenance, MIT license and upstream history.
- Both Dockerfiles: source-based UI and core coordinator have different contracts.
- Optional recommender code: supplied operating instructions and tracked current
  scope differ on RS requirements; cleanup does not choose a new academic scope.

Exact duplicates found at the base are retained: the Stage 4 current/historical
manifest pair, the saved/research-checkpoint pair, and two historical postmortems.
Their roles and provenance differ despite equal bytes.

## Unverified or Deferred

- Initial lock regeneration was blocked by pip-tools/pip incompatibility and a
  broken extra index. A task-only pip 25.3 and exact `PIP_CONFIG_FILE=os.devnull`
  fixed the tooling boundary without modifying the installed environment.
  On Windows, uppercase `NUL` does not match pip's lowercase `nul` check.
- The wheel is a core package, not a standalone UI/demo distribution. Required
  prompt/template/scenario package data were inspected in the built wheel.
- No live Kubernetes, Chaos, model download, training, deployed image, empirical
  benchmark, final-Test access, publication or independent scientific validation
  is established by this cleanup. Existing gates remain open.
- Existing worktree/cache retirement needs ownership and recoverability checks;
  broad recursive deletion is not part of this maintenance change. The original
  root `__pycache__` and `.ruff_cache` were verified untracked, without reparse
  points, and removed using non-forcing PowerShell deletion: 143 generated
  files, 261117 bytes. Test records and worktrees were not removed.

## Verification Record

- Baseline API/frontend/Stage 15: 38 passed before edits.
- Focused API/compatibility/recommender/demo pass: 97 passed, one new ignore
  test failed on Windows text-mode CRLF; corrected to NUL-delimited Git I/O.
- Final retirement/hygiene/Stage 15 inventory: 44 passed.
- Full local suite: 2506 passed, 12 skipped, one deselected, one failure in
  420.66 seconds. The deselected Helm-template test can change local repository
  configuration; no Helm configuration change was authorized.
- Full-suite failure: unchanged `training/sft_provenance.py` atomic `os.replace`
  returned `WinError 5` in the model-revision plan-rejection case. The exact
  11-case group passed on isolated rerun. The full-run failure is retained,
  not relabeled as a clean full-suite PASS; Windows replacement risk remains.
- Repository correctness lint and runtime unused-import lint passed; four
  Node live-incident projection tests passed. Final local source and wheel builds
  passed; archive inspection found required package data and no local secret paths.
- Read-only release check: zero critical artifact failures, expected readiness
  FAIL for open empirical gates and `NOT_CERTIFIED`.
- Both bounded audit workers used `azure-kmamc/gpt-6-luna` at `max`, confirmed
  from their own rollout turn context. They were behaviorally read-only,
  not sandbox-enforced read-only. Root owns final acceptance.
- Independent review caught missing `.env.local` and common GCP credential JSON
  exclusions. Added narrow globs and synthetic filename regressions; no
  credential files were read. Arbitrarily named secrets are not detectable by
  an ignore list; builds still require operator-controlled source context.
- No commit, push, merge, deployment, or empirical closure was performed.

## Dependency Continuation

- Regenerated the Windows lock with task-only pip-tools 7.6.0 / pip 25.3 and
  no persistent pip configuration changes. The exact `os.devnull` value disables
  machine-index configuration; the uppercase Windows device alias does not.
- Lock comparison: 112 -> 107 packages, no additions and no retained pin changes.
  Removed Anthropic, docstring-parser, Pub/Sub, OpenTelemetry SDK and semantic
  conventions; retained OpenTelemetry API for Cloud Logging and Rich/Typer for
  Gradio. Frozen training locks and pilot configuration remain unchanged.
- Fresh task-only environment installed all 107 pins, with zero version
  mismatches and no removed clients or training/model packages installed.
  `pip check`, source/wheel build, and 18 hygiene/dependency tests passed.
- Full local reduced-environment suite: 2516 passed, 12 skipped, one deselected,
  two failures in 413.69 seconds. The prior full-run Windows replacement failure
  remains preserved above.
- New failures identified: the newly inventoried `requirements/dev.in` had
  unpinned Windows line endings, and the existing synthetic Stage 4 subprocess
  published an empty `result.json` before its write completed. Pinned/normalized
  the input to LF and changed only the synthetic test helper to publish complete
  JSON via exclusive temporary write and rename. Runtime approval is unchanged.
- Targeted correction run: 52 passed; one Stage 15 checksum failure read a stale
  inventory while the documented resolver command was being refreshed. After
  all edits and regeneration completed, the entire Stage 15 selection passed
  18/18. No full-suite clean PASS is claimed.
- Built wheel metadata omits the four retired direct requirements; required
  prompts/templates remain. Resolver documentation now matches the generated
  lock command, including bounded retries and timeout.

## Stable-Tree and Import Audit

- Final stable-tree full suite after the above fixes: 2519 passed, 12 skipped,
  one deselected, zero failures in 377.36 seconds. The report is preserved at
  `C:\AtlasOps\.codex-tmp\cleanup-final-stable-suite-20261001.xml`.
  Earlier failures remain negative records; this is software-only validation.
- Read-only import graph: all 188 tracked Python files parsed successfully.
  Isolated modules were the retired evaluation stubs, `inference.py`, and the
  documented LoRA export CLI. Export functionality is retained.
- The last synthetic-alert dispatcher in `inference.py` was retired after this
  full pass; its unused local-model loader was removed. The README's active
  Optimum-AMD/BetterTransformer claim is now explicitly historical.
- No source/notebook uses the OpenAI SDK. HTTPX handles the OpenAI-compatible
  wire protocol; removing the SDK does not remove the provider backend.
  Final lock: 112 -> 103 packages, no added or changed retained pins.
  The task-only environment removes OpenAI plus distro/jiter/sniffio;
  the original installed environment remains unchanged.
- A no-build-isolation editable refresh failed because the build-only
  `editables` package was absent. The normal isolated editable build succeeded;
  no build-only dependency was added to the runtime lock.
- Of 41 inspected in-workspace registered worktrees, 14 were dirty and three
  heads were not ancestors of the cleanup base. All were preserved. Thirty-three
  other registered worktrees were left untouched outside this audit lane.
- Final inference/SDK affected suite: 364 passed, zero failures in 122.21 seconds.
  This covers coordinator/API, G6/G8 HTTPX inference contracts, retired CLIs,
  current claims, and Stage 15. The report is preserved at
  `C:\AtlasOps\.codex-tmp\cleanup-final-sdk-affected-20261001.xml`.
  The stable full-suite result above predates these final isolated changes.
- Final task environment: 103/103 locked pins match, removed SDK clients and
  training packages absent, `pip check` passes. Final source/wheel build and
  correctness lint pass; final wheel metadata omits the unused OpenAI SDK.
- No further local deletion is justified from the inspected evidence. Dirty
  worktrees need owner decisions, and clean/merged status alone does not establish
  inactivity or permission to remove an existing task checkout. GitHub commit,
  push, PR, CI, and merge remain pending explicit publication authorization.

## Headless Dependency Extension

- Follow-up base: merged cleanup `700584216d4df6b6986056950e761d8748f83463`.
  Local branch: `chore/lean-coordinator-dependencies-20261001`.
- Gradio has no coordinator, tool, or benchmark runtime imports. Move it from
  base dependencies to `demo`, retaining it in `dev` so the documented
  development setup and unit suite remain complete.
- Regenerated canonical Windows development lock is byte-identical; no
  dependency versions changed. Demo/container/hygiene selection: 42 passed.
  Source/wheel build and correctness lint passed.
- A fresh base-wheel-only environment has 60 installed distributions, no
  Gradio or training stack, and passes `pip check`. From an empty temporary
  working directory, the installed wheel's coordinator served loopback
  `/healthz` with HTTP 200 and `{"status":"ok"}`. The test process was stopped.
- This runtime probe used no credentials, incident dispatch, models, or cluster.
  It proves headless package startup only, not incident resolution or deployment.
- Initial constraints attempts using the editable dev lock and extra-bearing
  requirements were rejected by pip. The successful probe used a temporary
  name/version-only constraints projection; the tracked development lock was
  not rewritten or weakened.

## Runtime and Packaging Follow-Up: 2026-10-02

- PRs 156-159 are merged through `231a74585369c138c78393f9a984daadd24a9871`.
  The current follow-up remains local on `chore/runtime-cleanup-polish-20261002`.
- Removed remaining equivalent import, UTC, and subprocess syntax diagnostics
  while retaining deferred imports, compatibility exports, failure reporting,
  and fail-closed approval. An independent Luna Max read-only review accepted
  both standards and scope for the six-file runtime-polish batch.
- Runtime-polish recovery: 93 affected tests passed. The earlier fixture-location
  failure and missing-scratch-parent errors remain preserved as separate XML
  reports under `C:\AtlasOps-backups`; neither was converted into a passing run.
- Stable-tree local suite: 2524 passed, 13 skipped, one deselected, zero failures
  in 406.13 seconds. The accidentally deselected read-only infrastructure-values
  test separately passed. The intended legacy Helm test did run; Helm repository
  configuration modification time remained unchanged. No live acceptance script
  or real infrastructure operation was executed.
- Source and wheel builds succeeded. Archive inspection then found preserved
  untracked preflight logs, overnight patches, coordination notes, and a backup
  in the source distribution. Added source-distribution-only exclusions for
  these records and known credential filename patterns, leaving files and Git
  visibility unchanged. The full-suite result above predates this packaging fix.
- Added the maintained Stage 3 acceptance script to the curated submission
  inventory. This hashes its source; it does not claim current cluster health.
  Historical evidence, frozen configuration, upstream ancestry, and license
  remain unchanged. The packaging, inventory, demo, and runtime-contract selection
  passed 61 tests. The rebuilt source archive excluded all 65 local-only entries
  identified above while retaining the negative incident evidence and SFT template.
  The final inventory contains 351 selected assets.
- Read-only release checks pass artifact validation. Scientific readiness still
  fails for the open empirical gates, and the inventory remains `NOT_CERTIFIED`.
  No generic cleanup approval authorizes training, Chaos, or empirical closure.

## Historical Plotting Retirement

Follow-up base: PR 160 merge `1498b072fa517f1cf8bfabdd3afb74fe0179e4ab`.
The source audit found an unused generator with hardcoded SFT, GRPO, and incident
scores, import-time filesystem writes, and overwrite targets in tracked assets.
It is replaced with the same disabled-compatibility pattern as retired incident
runners. The original implementation remains in Git history; all four existing
chart PNGs are preserved without regeneration.

The standalone training and MI300X narratives now label their historical claims
directly. Current README and deployment wording no longer recommends historical
Space setup as a complete current procedure or presents target incident timings
as measured continuation outcomes. Historical numbers and procedures remain.
The generator, narratives, deployment guide, and charts are included in the
curated integrity inventory without promoting them into empirical evidence.

Matplotlib has no remaining tracked source/notebook import after retirement.
The development lock is regenerated to remove only the unused plotting stack;
training locks and canonical runtime behavior are outside this change.

Verification: 54 retirement/claim/inventory/demo tests passed after the final
changes. The task environment matches all 97 pins and passes `pip check`; the
lock removes six plotting packages from 103 without additions or retained-pin
changes. Source and wheel builds passed. Chart files and frozen evidence have
no diff. The wider runtime audit established no high-confidence unused internal
function in its scope; an apparent duplicated reward-history scan was retained
because the before/after append slices differ and consolidation would change
the reward contract.

## Notebook Shortcut Retirement

Base: PR 161 merge `01af01993a1b02554e8a1628b1721edb7891ab18`.
The two cloud notebooks were historical, unexecuted shortcuts with no saved
outputs. SFT omitted required revision/admission inputs; the GRPO notebook ran
mock evaluators rather than training. Their duplicate unpinned installation,
clone, benchmark, and checkpoint-listing cells are removed. Each notebook now
has one immediate disabled guard and links to maintained Stage 7/9 contracts.
The old cells remain in Git history. No model, training, or empirical artifact
is deleted or regenerated. Notebook JSON is included in the integrity inventory
with explicit LF checkout handling; local contract tests execute only the
AST-validated disabled guard in an isolated temporary working directory.

Verification: the first 77-case selection had 76 passes and one Linux-checkout
hash failure for CRLF notebook text. Normalized only the three edited notebook
files to their declared LF form and regenerated the manifest; the rerun passed
77/77. Both reports are preserved outside the checkout. Ruff, whitespace,
source/wheel builds, and all 362 inventory hashes/sizes passed. No training or
model download was performed.

## UI Container Dependency Consolidation

Base: PR 162 merge `8cb716aead1aaa71026abcf60993774437c3d7ab`.
The source UI Dockerfile maintained a second Python requirement list, including
unused aiofiles. It now installs the base project
from `pyproject.toml`, preserving the source UI entrypoint on port 7860 and
excluding demo/training extras. Uvicorn's standard extras are explicitly retained
for HTTP acceleration; functional probes do not establish equivalent throughput.
Removed unused GnuPG and Git from the UI image; the launched source UI does not
use Git, and its build context has no repository metadata.
Kubectl now shares the coordinator image's reviewed
version/checksum rather than resolving mutable `stable.txt`.

The Docker daemon is unreachable at its Windows named pipe. No image build,
daemon startup, container execution, provisioning or deployment is claimed.
Source contract checks and a base-dependency UI runtime probe are separate
software evidence, not proof of a built/deployed image.

The base-only task environment, without Gradio, aiofiles or Uvicorn optional
extras, served HTML/static assets/configuration/catalog over loopback and refused
the unconfigured webhook with HTTP 503. The probe process was terminated.
CI now builds the actual UI image and runs the same startup checks in a
network-isolated container; its result must be observed before an image claim.

Final affected API/HF/runtime/container/inventory selection: 84 passed.
Default changed-file Ruff and whitespace checks passed. The source-only probe
does not substitute for the pending actual image build/startup CI result.
