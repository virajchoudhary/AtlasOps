---
name: verify-atlasops
description: Verify the AtlasOps local React evidence console and synthetic rehearsal through its existing browser tests. Use for presentation changes, not live incident qualification or research certification.
---

# Verify AtlasOps

Read [the feature map](features/README.md), then choose the affected paths.
Use the checkout under review, not another checkout's installed Python package or built frontend.
Keep all results labelled local software or synthetic evidence.

## Launch

From the repository root, use Python 3.11 or 3.12 with the project dependencies.
On this Windows host, the existing `C:\AtlasOps\.venv\Scripts\python.exe` is available.
Put its `Scripts` directory first on this shell's `PATH` so Playwright launches that Python.

If frontend dependencies are absent, install with `npm ci --prefix frontend` only when allowed.
An existing local dependency tree may be copied when its lockfile matches this checkout.
Never borrow a built `frontend/dist`; build the source being verified.

```powershell
npm run build --prefix frontend
python -B -m demo.launcher --check
```

The check must report `local_presentation_ready: true`.
For a manual browser drive, start a foreground instance on an unused loopback port:

```powershell
python -B -m demo.launcher --host 127.0.0.1 --port 7864
```

Do not pass `--operator-config`, `--live-observations`, or an incident capture directory.
Do not run infrastructure wrappers or inference/training commands.

## Doctor

For a server this run owns, GET `/health` and `/api/rehearsal`.
Require health success and fixtures labelled `SYNTHETIC / NON-LIVE / NON-EMPIRICAL`.
Verify the launch command and checkout before reusing any server.
Stop on missing assets or fixtures; do not invent replacement evidence.

## Drive

The existing `frontend/e2e/console.spec.ts` covers navigation, evidence, rehearsal,
blocked approval, failed verification, and responsive layouts.
Run the affected tests from `frontend`:

```powershell
$env:CI = "1"
npm run test:e2e -- --grep "rehearsal|approval never|failed verification|non-GET"
```

`CI=1` prevents Playwright from silently reusing another server on port 7863.
If that port is occupied, stop and resolve ownership; never stop an unrelated process.
The config starts and tears down its own server. Windows defaults to installed Edge.
On other hosts, use an available Playwright browser or report the missing dependency.
The complete presentation suite is `npm run test:e2e`; use it when changes warrant broader coverage.

## Evidence

Capture the exact checkout SHA and diff, commands, exit codes, and test report.
Retain logs and screenshots under a new directory outside frozen `artifacts/evidence`.
The existing `manual pause stops playback and desktop/mobile rehearsal views are captured`
test creates screenshots under `frontend/test-results`.
Copy proof artifacts outside that directory before another run can replace them.
Check synthetic labels, approval blocking, and GET-only requests alongside visible outcomes.
Mocked operator-status tests prove rendering contracts, not real operator execution.

## Cleanup

Stop only the foreground instance or managed test server this run started.
Restore this shell's previous `CI` and `PATH` values after verification.
Do not delete evidence, existing dependency trees, research records, or another run's output.
Confirm retained proof files still exist after the owned server stops.

## Helpers

Reuse `demo.launcher --check` and `frontend/playwright.config.ts`.
No additional launcher or parallel verification engine is required.
