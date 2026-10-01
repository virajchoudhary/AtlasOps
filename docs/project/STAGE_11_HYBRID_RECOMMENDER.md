# Stage 11: Train Hybrid Recommender (Gate G11)

**Current scope: OUT_OF_SCOPE / retained optional historical research.**
The later [GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md) removes G11 as a
final prerequisite. The former bounded synthetic offline PASS below is
historical, not a current gate status or real incident improvement.

**Status: PASS within the bounded scenario-derived benchmark**

The hybrid ranker combines lexical BM25, alert/service co-occurrence, and a global runbook
prior:

`score = 0.50 * content + 0.35 * co_occurrence + 0.15 * prior`

Benchmark tier and expected root cause are not runtime features. The returned score is a
ranking value, not a calibrated recovery probability. Recommendations are advisory and do
not authorize or determine remediation.

## Corrected Synthetic Run

The corrected model was fit on 12 Train rows and evaluated on five Validation and four Test
rows. The run is preserved in:

- `artifacts/models/hybrid_recommender_synthetic_v2.json`;
- `artifacts/evidence/stage11/rs_hybrid_eval_synthetic_v2.json`;
- `artifacts/overnight_experiments/rs-20260924/`.

On the four-row synthetic Test partition:

| Model | Hit@3 | MRR@3 | NDCG@3 |
| :--- | :---: | :---: | :---: |
| BM25 | 0.7500 | 0.6250 | 0.6577 |
| Hybrid | 1.0000 | 0.7083 | 0.7827 |

These values are reproducible measurements on a small scenario-derived dataset. They do not
prove statistical superiority, historical-feedback learning, guaranteed retrieval, or
production performance.

## Provenance and Verification

The evidence records the exact interaction-row hash, included frozen split IDs, generator
and catalogue hashes, checkpoint path, and checkpoint SHA-256. Training rejects rows assigned
to the wrong frozen split. Local tests cover scoring, serialization, runtime feature
isolation, split enforcement, and evidence binding.

The original `hybrid_recommender.json` and `rs_hybrid_eval.json` remain as historical
scenario-derived artifacts.

## Optional Local Command

The CLI requires an explicit existing corpus and a new output directory.
It cannot implicitly generate inputs or overwrite an existing directory:

```bash
python -m recommender.train_hybrid --input /scratch/rs-dataset-new/interactions.jsonl --output-dir /scratch/rs-hybrid-new
```

The new directory receives `hybrid_recommender.json` and `hybrid_eval.json`.
This is optional synthetic ranking research, not a required GAI + RL step,
live remediation authorization, or an empirical gate result.

**Gate G11 Status: PASS within the recorded offline synthetic scope**
