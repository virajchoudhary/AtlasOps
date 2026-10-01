# Stage 10: Build RS Data and Baselines (Gate G10)

**Current scope: OUT_OF_SCOPE / retained optional historical research.**
The later [GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md) removes G10 as a
final prerequisite. The original bounded results and classification below
are historical, not a new empirical PASS.

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

## Optional Local Commands

CLI runs require a new output directory; existing directories are rejected.
Baseline evaluation requires an explicit existing corpus and never creates a
default dataset. For separately authorized synthetic research, the maintained
commands are:

```bash
python -m recommender.dataset --output-dir /scratch/rs-dataset-new
python -m recommender.evaluate --input /scratch/rs-dataset-new/interactions.jsonl --output-dir /scratch/rs-baselines-new
```

These outputs are optional research records, not historical user feedback or
empirical incident gains. Callable research APIs retain their explicit-output
compatibility; the CLI no longer defaults to preserved Stage 10 artifacts.

## Baseline Evaluation

The historical Stage 10 metrics in `artifacts/evidence/stage10/rs_baseline_eval.json` were
computed on the original 28-row scenario-derived dataset. They remain valid only for that
small synthetic benchmark and do not establish production generalization or historical
feedback quality.

The corrected overnight corpus is preserved separately so its provenance and exclusions
remain auditable. Tests validate runbook IDs, split boundaries, no runtime root-cause
leakage, custom-output isolation, and ranking metrics.

**Gate G10 Status: PARTIAL**
