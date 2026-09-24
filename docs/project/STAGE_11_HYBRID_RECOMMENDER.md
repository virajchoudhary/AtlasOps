# Stage 11: Train Hybrid Recommender (Gate G11)

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

**Gate G11 Status: PASS within the recorded offline synthetic scope**
