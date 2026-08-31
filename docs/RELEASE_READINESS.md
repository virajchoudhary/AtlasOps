# AtlasOps Release Readiness

- Overall: **PASS**
- Critical failures: **0**
- Warnings: **1**

## Checks
- [PASS] `Required artifacts` (critical) - All required docs/results/tests present.
- [PASS] `Chaos manifest count (single_fault)` (critical) - Expected 8, found 8.
- [PASS] `Chaos manifest count (cascade)` (critical) - Expected 5, found 5.
- [PASS] `Chaos manifest count (multi_fault)` (critical) - Expected 5, found 5.
- [PASS] `Chaos manifest count (named_replays)` (critical) - Expected 10, found 10.
- [PASS] `Difficulty tiers declared` (critical) - All five required tiers are declared in runtime config.
- [PASS] `Tier scenario pool coverage` (advisory) - Scenario pools include all required tiers or intentionally map tiers elsewhere.
- [PASS] `/config endpoint` (critical) - Configured correctly.
- [PASS] `Static UI dynamic config` (critical) - Configured correctly.
- [WARN] `Benchmark output sanity` (advisory) - Missing newer anti-gaming columns: avg_reward_contract, avg_penalty, unsafe_actions, false_resolution, hallucinated_evidence

