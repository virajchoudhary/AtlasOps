# Continue G9 software acceptance

This handoff covers offline safeguards, not a trained policy.
G9 remains frozen `NOT_PASSED` and scientific status remains `NOT_CERTIFIED`.
The [completion matrix](COMPLETION_CHECKLIST.md) and
[frozen negative result](CONTROLLED_G9_FINAL_NEGATIVE_V1.md) remain authoritative.

## Completed safeguards

The controlled scorer preserves objective rewards and verifier decomposition.
It supplies environmental verifier rewards to GRPO and retains an admissibility
flag for each sample. Export acceptance independently recomputes transitions,
verifier checks, reward components and normalized advantages from raw completions
and the shared pre-state.

Each group needs two admissible samples with varied verified-resolution or
required-check-coverage components. Claim-only variation, invalid-action
penalties and KL variation cannot establish that environmental signal.
A failed first group cannot be rescued by a later group.

Export acceptance requires the fixed tokenizer identity and loads the tokenizer
saved in `adapter/` with `local_files_only=True` and
`trust_remote_code=False`. It re-encodes the exact observation-first state JSON
using the pinned TRL text-tokenization settings and decodes the recorded
completion IDs using TRL 0.19.1's `batch_decode(..., skip_special_tokens=True)`
call. The decoded strings must equal the raw completions used for reward and
environment verification. This verifies digest and token/text consistency; it
does not authenticate an export against an actor able to rewrite every hash.

The beta-zero diagnostic records environmental-policy gradients separately
from the optimization gradient. Gradient and update records must carry matching
environmental-advantage, environmental-signal and loss-input hashes.
The probe attempts to preserve RNG, existing gradients, metrics and checkpoint
settings. Its pinned-runtime behavior requires separate numerical verification.

The previously published PR #226 candidate is head
`cfc5d8339273abba4078be11437e3f1616cd0784` over
`c90d95602762c5d9c79bec7ce76d805446422c39`. Its exact-head CI run
`39260177808` passed, including the pinned TRL CPU contract. These results
validate offline safeguards and tiny random fixtures only; G9 remains frozen
`NOT_PASSED` and scientific status remains `NOT_CERTIFIED`.

The shuffle-permutation repair in the current worktree is a separate pending
change and is not covered by that candidate or CI run. It requires its own
reviewable commit and exact-head checks; no future commit hash or CI result is
assumed here.

## Reproduce model-free checks

Use the reviewed checkout and a Python 3.11 or 3.12 development environment.
On this Windows host, the canonical interpreter is
`C:\AtlasOps\.venv\Scripts\python.exe`. A fresh peer checkout must install its
own matching development lock before running these commands.

```powershell
$python = 'C:/AtlasOps/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Select the reviewed development interpreter.' }
& $python -B -m pytest -q tests/test_grpo_reward_mechanics.py tests/test_grpo_controlled.py
& $python -B -m pytest -q -rs tests/test_grpo_observation_installed_trl.py
& $python -B -m ruff check training/grpo_reward.py training/grpo_controlled.py scripts/accept_grpo_controlled.py --select E9,F63,F7,F821,F401,F841
```

These tests use synthetic fixtures and do not call the project pilot.
An unavailable Torch or pinned TRL stack produces skips, not numerical PASS.
The existing CI workflow has a separate pinned CPU job for tiny random fixtures.
Do not install its training extras into the Windows development lock to remove
a skip. No pretrained weights, GPU or live infrastructure are needed for the
model-free tests.

## Evidence and remaining acceptance

The worker reported baseline acceptance of penalty-only and claim-only groups,
followed by rejecting full-export regressions. Its focused result was
43 passed, 1 skipped and 1 deselected; those counts are worker-reported.
Its installed-TRL result was 1 passed and 5 skipped.
The root independently reran the unchanged P1 approval/replay regression under
the approved local profile: 1 passed. The earlier observation-window stall
remains recorded; its cause is not established by that later pass.

The independent lineage repro was accepted before this repair even though group
1 sample 0's raw completion described `replicas=4` while its recorded
completion IDs remained `[3, 2]`; the result remained synthetic and
`NOT_CERTIFIED`. After the repair, that saved fixture fails closed because it
has no pinned tokenizer identity or saved tokenizer. This is not a
mismatch-specific decode pass. The missing-tokenizer regression was run red
before and green after the repair. In this Windows venv, the acceptance suite
reported 5 passed and 20 skipped; all 19 tokenizer-backed cases skipped because
`tokenizers` is absent, and one numeric probe skipped because `torch` is absent.
The installed-TRL suite reported 1 passed and 5 skipped for missing Torch and
the pinned runtime packages. The controlled suite excluding its P1 permit/replay
test reported 23 passed; the P1 test stalled in this run and was interrupted,
while the parent had independently reported that unchanged regression passing.
The tiny-tokenizer positive, prompt-lineage and coherently rehashed mismatch
fixtures therefore still require the pinned CPU CI run.

The later root run used isolated pinned tokenizer libraries without installing
training extras into the development lock. It passed 24 cases, with only the
Torch numerical probe skipped. Fresh independent acceptance returned
PASS+NOTES for actual saved-tokenizer loading, the valid export, prompt mismatch,
coherently rehashed text/token mismatch, first-group rejection and evidence-hash
binding mutations. That reviewer also independently ran four normalization and
chronology cases; the Torch probe remained skipped. This accepts offline
lineage behavior only. Exact-head run `39260177808` later passed the pinned CPU
contract for the published `cfc5d833` candidate using tiny random fixtures.
Those results are not real-model empirical evidence. The current shuffle repair
is not covered by that run and still needs its own exact-head checks.

Before software acceptance, inspect the independent review, exact-head CI and
raw test evidence. Verify the pinned runtime's concrete policy/reference
log-probability inputs, environmental advantages, separate gradient and
same-step parameter update. Digest agreement alone is not authenticity.
Verify RNG, gradient, metric and checkpoint-setting noninterference with the
real pinned loss path. Keep skips and unavailable evidence explicit.

`REWARD_GRADIENT_DIAGNOSTIC_AVAILABLE` remains false.
The existing profile still uses `num_generations=2`; it is not the unapproved
four-generation prospective plan. No profile, frozen pin, consumed permission
or historical evidence was changed to reconcile that discrepancy.

## Peer prerequisites and stop rules

The project lead must approve a versioned G9 reopening and its exact scientific
protocol. Training needs separate host/resource and execution authorization.
Normally accept G4, the G6 incident baseline and matched G8 evidence first.
Any earlier simulator-only exception must be new and explicit.

The prospective two-group, four-generation, two-step ceiling is preparation
only. It must not be copied into the frozen executable profile without review.
Select measured RAM, GPU, storage and runtime resources under a fresh approved
plan. Do not assume the current host or an old free-T4 receipt qualifies.

Stop before optimization if the first group lacks admissible environmental
variation or a finite attributable gradient. Stop on source drift, malformed
actions, missing lineage, nonfinite gradients, unchanged parameters, expired
authority or uncertain output ownership. Do not extend budgets or replace a
negative run without a new decision.

The maintainer owns software integration. The project lead owns reopening,
scientific criteria and access. The selected host owner supplies resources.
Independent review owns acceptance of the eventual raw evidence.
This patch authorizes no pretrained inference, project training, infrastructure
startup, live fault, remediation, Final-Test access, deployment or submission.
