# AtlasOps Release Readiness

- Overall: **PASS**
- Critical failures: **0**
- Warnings: **2**

## Checks
- [PASS] `Required artifacts` (critical) - All required docs/tests present.
- [WARN] `Benchmark results present` (advisory) - No benchmark output. bench/results/ is gitignored and requires a run; this team publishes no resolution rate until Gate G5 freezes an evaluation split, so absence here is expected.
- [PASS] `Chaos manifest count (single_fault)` (critical) - Expected 8, found 8.
- [PASS] `Chaos manifest count (cascade)` (critical) - Expected 5, found 5.
- [PASS] `Chaos manifest count (multi_fault)` (critical) - Expected 5, found 5.
- [PASS] `Chaos manifest count (named_replays)` (critical) - Expected 10, found 10.
- [PASS] `Difficulty tiers declared` (critical) - All five required tiers are declared in runtime config.
- [PASS] `Tier scenario pool coverage` (advisory) - Scenario pools include all required tiers or intentionally map tiers elsewhere.
- [PASS] `/config endpoint` (critical) - Configured correctly.
- [PASS] `Static UI dynamic config` (critical) - Configured correctly.
- [WARN] `Benchmark output sanity` (advisory) - No comparison_table.md to inspect (no benchmark run in this checkout).

