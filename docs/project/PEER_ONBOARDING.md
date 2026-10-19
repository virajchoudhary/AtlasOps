# Continue AtlasOps safely

Start with [the handoff tracker](PEER_HANDOFF.md) and
[the authoritative completion matrix](COMPLETION_CHECKLIST.md).
The local software review needs no Docker, Kubernetes, GPU, model-provider key,
pretrained inference or experiment permit.

## Get the exact source

Use a new checkout rather than an existing dirty directory.
Read the final review package receipt for the accepted full commit and ZIP SHA-256.
Verify the ZIP hash before extraction. Keep its original files and evidence.

```powershell
git clone https://github.com/virajchoudhary/AtlasOps.git
Set-Location AtlasOps
git fetch origin main
git checkout --detach <full-reviewed-source-sha-from-receipt>
git rev-parse HEAD
git status --short
```

The angle-bracket value above is a required receipt value, not a command to run
literally. Do not select a branch by a remembered short SHA. Preserve upstream
history and MIT attribution. Never push to `Harikishanth/AtlasOps`.

## Install the local development environment

Use Python 3.12 on Windows and the tracked development lock. Python 3.11 is also
covered by CI, but the Windows lock is specifically for Python 3.12.
Use Node.js 22.12 or newer, as required by the current frontend.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple -r requirements/dev-win-py312.lock
.\.venv\Scripts\python.exe -m pip check
npm ci --prefix frontend
npm run build --prefix frontend
```

Dependency installation requires network access. The subsequent commands use the
checked-out source. Do not install training extras or download model weights.

## Verify software and present the non-empirical demo

```powershell
.\.venv\Scripts\python.exe -B -m demo.launcher --check
.\.venv\Scripts\python.exe -B -m pytest tests/test_current_project_truth.py tests/test_stage14_demo_safety.py tests/test_stage15_submission_package.py
.\.venv\Scripts\python.exe -B -m config.g4_demo_candidate
```

The launcher must report `local_presentation_ready=true`.
A G4 source mismatch is an honest research-readiness block, not a failed demo.
The [existing verification workflow](../../.agents/skills/verify-atlasops/SKILL.md)
defines the affected browser paths and their evidence requirements.

For the read-only presentation only:

```powershell
.\.venv\Scripts\python.exe -B -m demo.launcher --host 127.0.0.1 --port 7864
```

Open `http://127.0.0.1:7864/` on that host.
Do not supply operator configuration, live observations or an incident capture
directory. Rehearsal must remain labelled SYNTHETIC / NON-LIVE / NON-EMPIRICAL.
Stop only the server you started. Do not stop another listener to free its port.

## Run the broader software checks

Record actual results, not old test totals:

```powershell
.\.venv\Scripts\python.exe -m compileall -q agents bench config training scripts app.py dashboard.py eval.py inference.py leaderboard.py
.\.venv\Scripts\python.exe -m ruff check . --select E9,F63,F7,F821
.\.venv\Scripts\python.exe -B -m pytest tests/
npm run test --prefix frontend -- --run
.\.venv\Scripts\python.exe -m build
```

Full tests include mocked partition-isolation checks; those are not access to
empirical Final-Test outcomes. Optional installed-trainer tests use tiny random
fixtures only. Use the current CI workflow for its separately locked CPU/runtime
jobs; do not install their extras into the local lock merely to remove a skip.
Follow the existing Playwright workflow for full browser verification and preserve
its screenshots before another run replaces the results directory.
CI's UI container is software evidence, not experimental cluster acceptance.

## Troubleshoot without crossing a boundary

- Missing JS/CSS means the exact checkout needs its own frontend build.
  Do not borrow another checkout's `dist`.
- A wrong Python version or broken dependency check requires the matching venv
  and lock, not loosening dependency pins.
- A busy loopback port requires a different unused port or owner coordination,
  not termination of an unrelated process.
- Missing private credentials is expected for the read-only demo. Do not copy
  owner-local secrets, place keys in argv, or disable authentication.
- Docker/Kind, model/operator or GPU failures belong to the blocked experimental
  workstream. Retain the diagnostic and stop; do not run setup `--apply`.
- A failed test is evidence to investigate. Do not delete it, widen its timeout
  or manufacture a replacement scientific result.

## Choose a peer workstream

Use the DONE, BLOCKED, OWNER/APPROVAL and NEXT ACTION columns in
[the tracker](PEER_HANDOFF.md). Each peer PR must identify its exact source,
commands and results, unfinished dependencies, host/resources, reviewer checklist,
evidence artifacts, finite budget, stop rules and required approving roles.
Do not invent peer names or treat PR approval as a live execution permit.

G4/G9 reopening, inference/training, live fault/remediation, held-out access,
peer deployment and actual academic submission are separate decisions.
The safe demo can be reviewed from the exact built source without any of them.
The final exact-source package is pending until its external receipt identifies
the accepted commit, archive and SHA-256. Older packages remain reviewable only
for their own recorded source; they do not contain later engineering changes.
