# Stage 10: Build RS Data and Baselines (Gate G10)

**Status: PARTIAL (bounded synthetic benchmark implemented; historical data missing)**

AtlasOps contains a 12-runbook catalogue plus Random, Popularity, and BM25 baseline
recommenders. The available interaction data is generated from frozen benchmark scenario
metadata. It is synthetic and is not historical operator feedback.

## Data and Provenance

The original 28-row artifact and its metrics remain historical evidence. A corrected
noncanonical run under `artifacts/overnight_experiments/rs-20260924/` records:

- `data_origin: scenario_derived_synthetic_benchmark`;
- `historical_user_feedback: false`;
- source catalogue, manifest, generator, split, and canonical row hashes;
- Train, Validation, and Test scenario identities;
- seven excluded scenarios for which no defensible single runbook label exists;
- 21 retained interactions covering 9 of 12 runbooks.

`expected_root_cause` is used only to derive the offline benchmark label. Runtime-facing
features contain alert name, affected services, and observed symptom text. Custom output
paths write their manifest beside the requested dataset and do not mutate canonical Stage 10
evidence.

## Baseline Evaluation

The historical Stage 10 metrics in `artifacts/evidence/stage10/rs_baseline_eval.json` were
computed on the original 28-row scenario-derived dataset. They remain valid only for that
small synthetic benchmark and do not establish production generalization or historical
feedback quality.

The corrected overnight corpus is preserved separately so its provenance and exclusions
remain auditable. Tests validate runbook IDs, split boundaries, no runtime root-cause
leakage, custom-output isolation, and ranking metrics.

**Gate G10 Status: PARTIAL**
