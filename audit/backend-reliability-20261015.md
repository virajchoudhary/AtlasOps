# Backend Reliability Lane

Date: 2026-10-15
Branch: `feat/completion-backend-20261015`
Baseline: `da740ab18813918bcac8360a9f052cdc007afa0d`

## Defect and Fix

After a coordinator webhook completed, its `finally` block cleared
`active_processing`. A repeated Alertmanager payload for the same correlated
incident then passed the correlator's `not active_processing` check and
dispatched the full incident workflow again. The baseline regression test
reproduced this: after processing was cleared, the same alert fingerprint
returned `should_dispatch=True`.

The correlator now claims fingerprints as part of its locked ingest decision.
Replays of an already-claimed alert do not dispatch again, including after the
first workflow finishes. New, distinct fingerprints remain eligible for a
follow-up dispatch once processing is idle. Alerts without an Alertmanager
fingerprint use a stable hash of their canonical JSON payload.

## Verification

Regression before the fix:

`C:\AtlasOps\.venv\Scripts\python.exe -m pytest tests/test_correlator.py::test_replayed_alert_is_not_redispatched_after_processing_completes -q --basetemp=C:\AtlasOps\.codex-worktrees\completion-backend-20261015\audit\pytest-backend-red-20261015-2`

Result: `1 failed in 6.53s`; expected `False`, observed `True` for
`replay_dispatch`.

After the fix:

`C:\AtlasOps\.venv\Scripts\python.exe -m pytest tests/test_correlator.py::test_replayed_alert_is_not_redispatched_after_processing_completes -q --basetemp=C:\AtlasOps\.codex-worktrees\completion-backend-20261015\audit\pytest-backend-green-20261015`

Result: `1 passed in 0.51s`.

`C:\AtlasOps\.venv\Scripts\python.exe -m pytest tests/test_correlator.py -q --basetemp=C:\AtlasOps\.codex-worktrees\completion-backend-20261015\audit\pytest-correlator-green-20261015`

Result: `6 passed in 0.48s`.

`C:\AtlasOps\.venv\Scripts\python.exe -m ruff check agents/correlator.py tests/test_correlator.py`: passed.

`C:\AtlasOps\.venv\Scripts\python.exe -m py_compile agents/correlator.py tests/test_correlator.py`: passed.

`git diff --check`: passed.

All pytest runs set `ATLASOPS_AUTO_HF_INFERENCE=0`; no cluster or model calls
were made.

## Scope and Limits

Changed source and test files: `agents/correlator.py`,
`tests/test_correlator.py`. The coordinator still uses the same correlator
interface. No training code, historical evidence, G4 pins, or governance
documents were changed.

An existing coordinator webhook test was attempted but did not produce output
within the bounded wait and was stopped. The regression proof therefore covers
the correlator decision directly, not a live ASGI request.

The deduplication state is in-memory and per process, and claims expire with
the existing five-minute correlation window. Failed or cancelled executions
remain claimed during that window to avoid replaying a possibly partial
mutation. Distributed idempotency, persistent incident state, and an explicit
operator retry path remain separate follow-up work.

No G4 preflight or incident attempt was run. This change is software-control
flow evidence only and does not alter empirical gate status.
