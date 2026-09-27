# AtlasOps Release Readiness

- Overall: **FAIL**
- Artifact checks: **PASS**
- Scientific/submission readiness: **NOT CERTIFIED**
- Critical failures: **2**
- Warnings: **2**

## Checks
### Artifact validation
- [PASS] `Required artifacts` (critical) - Required documentation and test files are present; benchmark output is optional.
- [PASS] `Chaos manifest count (single_fault)` (critical) - Expected 8, found 8.
- [PASS] `Chaos manifest count (cascade)` (critical) - Expected 5, found 5.
- [PASS] `Chaos manifest count (multi_fault)` (critical) - Expected 5, found 5.
- [PASS] `Chaos manifest count (named_replays)` (critical) - Expected 10, found 10.
- [PASS] `Difficulty tiers declared` (critical) - SPEED_MIDPOINTS declares all five required tier keys.
- [PASS] `Tier scenario pool structure` (critical) - SCENARIOS_BY_TIER declares all required scenario-pool keys.
- [WARN] `Tier scenario pool coverage` (advisory) - No explicit SCENARIOS_BY_TIER entries for: warmup, adversarial
- [PASS] `/config backend route` (critical) - FastAPI GET /config route is declared.
- [PASS] `Static UI loads console.js` (critical) - static/index.html loads /static/console.js.
- [PASS] `Console config endpoint mapping` (critical) - static/console.js maps the config endpoint to "/config".
- [WARN] `Benchmark comparison output (advisory)` (advisory) - No run-scoped comparison table exists under `bench/results/<run_id>/comparison_table.md`. This optional NON_EMPIRICAL runner output is advisory; its absence is not a release blocker.
### Scientific and submission readiness
- [FAIL] `G0-G15 declared gate inventory` (critical) - Open declared gates: G4=NOT_PASSED, G6=IMPLEMENTED / EMPIRICAL EVIDENCE MISSING, G7=PARTIAL, G8=IMPLEMENTED / EMPIRICAL EVIDENCE MISSING, G9=REOPENED, G10=PARTIAL, G12=IMPLEMENTED / EMPIRICAL EVIDENCE MISSING, G13=REOPENED, G14=PARTIAL, G15=PARTIAL. A declared status is not independent certification evidence.
- [FAIL] `Scientific certification` (critical) - Submission manifest reports NOT_CERTIFIED. Its asset hashes and declared gate statuses do not independently verify empirical closure.

## Blockers
- `G0-G15 declared gate inventory` - Open declared gates: G4=NOT_PASSED, G6=IMPLEMENTED / EMPIRICAL EVIDENCE MISSING, G7=PARTIAL, G8=IMPLEMENTED / EMPIRICAL EVIDENCE MISSING, G9=REOPENED, G10=PARTIAL, G12=IMPLEMENTED / EMPIRICAL EVIDENCE MISSING, G13=REOPENED, G14=PARTIAL, G15=PARTIAL. A declared status is not independent certification evidence.
- `Scientific certification` - Submission manifest reports NOT_CERTIFIED. Its asset hashes and declared gate statuses do not independently verify empirical closure.

