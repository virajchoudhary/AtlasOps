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

## Shared Smoke-Test Entrypoint

Base: PR 163 merge `cdf2f004c0f2cb8afb1b48b2a9cb6d153318adfe`.
The same four-file smoke selection was duplicated in Bash, PowerShell, and
Make. It now lives in `scripts/smoke_e2e_local.py`. Existing shell commands
delegate to it and retain quiet/verbose behavior; the PowerShell wrapper now
explicitly propagates the test exit code. The runner uses its current Python
interpreter and resolves repository paths independently of the caller's working
directory. This is software smoke coverage, not a live incident claim.

Verification: the shared real smoke run passed 96 tests. Wrapper/inventory
selection passed 52 tests with one POSIX-only Bash test skipped on Windows;
Linux CI supplies that boundary. The initial PowerShell mock used `-Command`,
which mapped the nested nonzero result to 1; the corrected test invokes the
wrapper with `-File` and a fake native Python command and verifies exit 5.
The failed harness report remains preserved. No runtime or dependency contract
changed. Broader source audits identify the legacy LoRA exporter as a separate
candidate; no model export or historical reward-helper deletion was performed.

## Legacy Model Export Retirement

Base: PR 164 merge `73348f3fd4b58d9c9573a07be3dd9a109e705a9e`.
The legacy LoRA exporter had no current code caller or required Stage 7/9 plan.
It imported the model stack eagerly, loaded mutable model/tokenizer identifiers
with remote code trusted, wrote an existing output directory, and could publish
to a Hub repository with public visibility by default. That incomplete parallel
workflow is replaced with a disabled compatibility entrypoint; the original
remains in Git history. The historical Space guide no longer recommends its
invocation. Future export is not implemented by this cleanup and requires its
own checkpoint-provenance and publication contract.

Tests verify import needs no ML package and writes nothing, and the legacy CLI
fails before any work. Active trainers, serving adapters, candidate checkpoints,
protocols and model artifacts are preserved. No model was loaded or exported,
and no Hub publication was attempted. The retired source is hash-inventoried.

Verification: the first selection had 72 passes, one POSIX-only skip, and one
Linux-checkout hash failure for retained CRLF in the edited exporter. Normalized
that file to its existing LF attribute and regenerated the manifest; the rerun
passed 73 tests with one POSIX-only skip. Both reports remain preserved.
Ruff and whitespace passed; protected training/runtime/evidence paths have no diff.

## Shared G13 JSON Validation Helpers

Base: PR 165 merge `7ae2e3b3225bfb836e2f82b4420f3f9987f95cc7`.
AST comparison established exact duplicate bodies for replay's type-strict JSON
equality and duplicate-field hook. Replay already imports the lineage and
membership modules that own those implementations. The replay-local helper names
now alias those implementations, removing redundant bodies without new imports,
modules, metric formulas, membership rules or validation semantics.
Regression cases cover strict Boolean/integer/float distinctions, nested values,
length/key differences, helper identity and duplicate-field refusal. Existing
replay/lineage/lossless-observation tests remain the primary behavior checks.
Frozen measurement proposals, evidence, source hashes in historical records,
training and runtime reward code are unchanged.

Verification: 285 replay, lineage, lossless-observation, Stage 13 and inventory
tests passed. Replay's runtime scorer manifest already includes the owning
lineage and membership modules; their source hashes remain part of provenance.
Correctness/unused-import lint and whitespace checks passed. No empirical
episode, model operation, scenario membership or metric definition was changed.

## Optional RS CLI Output Isolation

Base: PR 166 merge `99ed800ddb43eb337ac85fa7793628a086d8f6b5`.
The optional dataset/baseline/hybrid commands defaulted to historical artifact
paths. They now require explicit fresh output directories. Evaluation and
hybrid training require an existing input file and cannot auto-generate a
default corpus; explicit empty baseline inputs no longer trigger a truthiness
fallback. Callable research APIs retain their existing compatibility behavior.
The CLI-only directory admission is shared rather than copied across commands.

Tests use synthetic scratch records to check missing arguments/input, preserved
existing directories, no implicit generation, and exact fresh output files.
Historical RS artifacts, ranking formulas, splits and the required GAI + RL
pipeline remain unchanged. This is output hygiene, not new incident evidence.

Verification: 59 CLI, dataset, baseline, hybrid-provenance and inventory tests
passed. Existing directories, files and symlink destinations are refused;
missing inputs are not created and no output directory is created for them.
Fresh-directory synthetic subprocess runs create only their declared output
files. Historical artifacts and protected training/runtime paths have no diff.

## Completion Audit and Unused Test Bindings

Base: PR 167 merge `4f3ca2bb8efe38d5ed1685a07ebb0114813e7f92`.
The tracked-file inventory contains 460 files (7562852 bytes); all 191 Python
files parse. All 369 curated submission hashes and sizes match. Three exact
byte-duplicate groups remain: historical/current Stage 4 records, saved/research
RS checkpoints, and historical postmortems. Their roles and provenance differ;
equal bytes alone do not justify deletion.

A repository-wide correctness/unused-import check found 28 unused test bindings.
Removed 24 in eleven accessible test files. AST comparison after removing
import nodes confirms all non-import test logic is unchanged; no assertion,
fixture, test case, or runtime source was removed. Four bindings in
`tests/test_tools.py` remain: the command hook blocks that file's reads after
classifying its content as forced deletion. The hook and file are preserved,
and no alternate read or edit was attempted to bypass that boundary.

Independent requirement and API audits distinguish scoped maintenance acceptance
from the unconditional goal. The public legacy reward helper remains tested and
used in older isolated worktrees; absence of current production calls does not
authorize a compatibility break. The 75 registered worktrees and unrelated
records remain preserved. Existing elevated archive manifests record pre/post
verification, but no independent traversal of every protected archived byte is
claimed. Empirical gates are not authorized by source cleanup.

Verification: 330 affected tests passed in 130.22 seconds. Correctness and
unused-import checks pass on all eleven changed test files; whitespace checks
pass. Full-repository unused-import cleanliness remains unproven because of
the four preserved bindings in the hook-blocked file.

## Hook-Unblocked Import Completion: 2026-10-02

Resume base: PR 168 merge `979888ffc2a7b5a493cd6c6ecca45b630068748b`,
verified against the current GitHub main revision. There were no pending tracked
edits; unrelated overnight records, preflight logs, diffs and the script backup
remain preserved. The repaired hook permits the actual tool-test source read.

Removed the final four unused bindings from `tests/test_tools.py`: `json`,
`pytest`, `importlib` and `pathlib.Path`. AST comparison with the resume base
confirms that these are the only removed import nodes and every non-import node
is identical. All 31 test cases and their adversarial command strings remain.
No runtime source, assertion, fixture, dependency or safety rule changed.

Current verification: the 31-case tool baseline passed before editing. After the
patch, 164 tool, approval, policy, repository-hygiene and release-contract tests
passed; one POSIX-only Bash wrapper test skipped on Windows. All 191 tracked
Python files parse and pass repository-wide correctness/unused-import checks
(`E9,F63,F7,F821,F401`). The 460-file tracked inventory and upstream baseline
ancestry are unchanged. The derived submission inventory is refreshed after
this record; its 369 asset keys and all gate declarations are retained.

The final Stage 15 selection passed 18/18. Direct hash/size verification matches
369/369 assets, and whitespace checks pass. The read-only strict release check
passes artifact validation and returns its expected failure for open empirical
gates and `NOT_CERTIFIED`. No new full-suite or built-image result is claimed.

This closes the identified import-cleanup discrepancy, not the empirical
project gates. `NOT_CERTIFIED`, `G4 NOT_PASSED`, prospective training approval
boundaries, historical evidence and compatibility APIs remain unchanged.
Existing worktrees and archived records are retained; this continuation does
not claim independent traversal of every archived byte. No infrastructure,
Chaos, model, training, deployment or historical-evidence regeneration ran.

## Workspace and Runtime Efficiency Follow-Up: 2026-10-02

Base: `55f3626dde53da96770f17d463a81abc5c05ca95` (PR 169 merge).
The user authorized a further local-housekeeping and GitHub optimization pass.
Native curated Luna Max audits covered local clutter, runtime and Git/CI hygiene;
root verified their actual rollout model/effort and owns integration.

Nine read-only console handlers now use FastAPI's existing synchronous worker
pool, so file reads, optional ranker construction and full audit verification
do not run directly on the event loop. The audit log's lazy initializer and
same-instance appends are synchronized. Readers take a locked file snapshot;
parsing and full-chain HMAC verification run after releasing the lock. Public
formats, secret checks, tail slicing and integrity predicates are retained.
This is single-process, same-instance consistency, not a multi-process writer
contract. Pool saturation and filesystem contention remain possible.

The communications feed streams the JSONL file and retains only its last 30
valid records. It still scans the file and skips malformed JSON as before;
it does not truncate history, cache stale evidence or modify communications.
Regression tests reproduced blocked health requests and full-file feed retention
before the fix, then passed with the new handlers. Independent review caught
the initial audit initialization/append race; that failure and the regression
results remain preserved, not relabeled as passes.

Matched synthetic local measurement, three timing samples per variant:

| Measurement | Before | After |
| --- | --- | --- |
| Feed request median, 100000 valid records / 15.1 MB fixture | 325.594 ms | 236.442 ms |
| Feed peak Python-traced allocation | 67520045 bytes | 100341 bytes |
| Health response during a deliberately blocked 250 ms catalog read | 264.864 ms | 4.801 ms |

Feed outputs were identical. These are software-only local measurements, not
incident-resolution timings, production throughput or empirical gate closure.
Loopback startup/read-route probes passed and temporary servers stopped.
Corrected actual-server working-set measurements were about 78 MB before and
after; no meaningful idle-memory or startup improvement is claimed. The earlier
Windows launcher-PID memory observation is retained as a diagnostic, not used
as server-memory evidence.

The pinned-TRL CI lane now installs the base project and pytest rather than the
whole demo/development extra. Required pinned CPU/trainer packages and fail-hard
integration flags remain unchanged; the full-suite quality lane still installs
the complete development extra. Local static contract checks cannot establish
the new CI lane's success or timing; observe that job separately.

Eight reviewed obsolete environment/cache/build directories were deleted from
`.codex-tmp`: 9714 files, 202199683 bytes. Exact no-link workspace guards,
tracked-file checks, process metadata, protected-file hashes and worktree
identities were checked. All 86 recorded protected files and all 75 registered
worktree identities were independently verified unchanged. Historical XML/JSON
failures, archive receipts, local notes/patches, main `.venv`, model artifacts,
scratch and all existing worktrees remain. The reduced test environment is
retained until final validation; its eventual retirement is recorded separately.
No generic deletion of ignored directories or Git history is authorized.

Detailed local receipts, regression XML and performance JSON are preserved in
`.codex-tmp`, outside ordinary Git and build context. New validations use unique
external temporary fixture directories with pytest cache disabled. Rebuildable
environments and package outputs should be retired after their owning task
finishes; compact evidence is retained separately. An inactive or merged
worktree is not automatically disposable when its ownership or dirty state
is unresolved.

Thirteen unused merged cleanup branch heads (PRs 157-169) were retired both
locally and on origin after verifying merged ancestry, exact remote head SHAs,
no open PR references and no registered worktree use. Remote deletion was
atomic and expected-SHA guarded; local deletion used Git's merged-branch check.
PR 156's checked-out worktree branch and all other worktrees/branches remain.
No commit history was rewritten. Git connectivity verification passed.
Three old Git temporary object files remain excluded from deletion because
their open-handle/recovery role was not established; no aggressive GC or
immediate prune was run.

Integrated console, audit, coordinator safety and hygiene verification:
120 passed, one POSIX-only skip. The earlier concurrency failures remain
separate negative regression records. Broader package and CI results must be
observed after the final manifest refresh.

The broader local run completed with 2580 passes, 13 skips, one deselected
Helm-mutating test and one Stage 15 checksum failure. The failure read the old
manifest while `scripts/package_submission.py` was being extended to include
the newly synchronized audit implementation. This full-run failure is preserved;
it is not reported as a clean full-suite PASS. After the final 371-asset refresh,
the complete Stage 15, console and hygiene selection passed 66 tests with one
POSIX-only skip, and direct verification found zero asset hash/size mismatches.
Source distribution and wheel builds passed; inspection found required audit
source and new tests, with no known local-clutter entries. CI must independently
validate the final committed tree and reduced pinned-trainer installation.
