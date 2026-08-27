# AtlasOps Remediation Recommender (RS)

This package is an offline-ready, split-safe recommendation layer for remediation
candidate ranking. It is not wired into coordinator behavior and cannot execute
tools. The intended boundary is:

```text
DiagnosisResult
  -> ContextFeatures
  -> Top-K RecommendationPacket
  -> safety / approval
  -> GRPO/remediation policy
  -> execution
```

## Status And Boundary

- This is G10/G11 preparation only. It does not claim a G10 or G11 PASS.
- No real final-test data, live training, Docker, Kind, Chaos, or Ollama was used.
- `agents.rs` does not import `agents.tools` or any HTTP/subprocess surface.
- A packet may rank mutating candidates before approval. Every side-effecting
  candidate carries `approval_required_before_execution=true`, current blockers,
  and `execution_eligible_after_downstream_gates=false` until downstream gates pass.
- Mutation-budget exhaustion remains visible for explanation but fails closed for
  execution eligibility.
- `mutating=true` includes external side effects such as Slack. Read-only evidence
  and verification actions are false.

## Catalogue And Ontology

`catalogue.py` contains 27 runbooks derived from the nine tools in the current
Remediation role ACL. Each runbook has version, service constraints, fault types,
typed template requirements, operational gates, risk, mutation classification,
safety constraints, verification references, deprecation state, and provenance.

A test compares every template with the coordinator's strict tool parameter schema.
Optional arguments may be omitted; no unknown argument is allowed. The catalogue
audit removed `created_by` from Alertmanager silence because the model-visible
schema does not expose it, and added the incident namespace to scale templates.

`ontology.py` derives typed parameter requirements from templates/gates,
evaluates operational prerequisites into deterministic states (`satisfied`,
`unmet`, `unknown`), and validates logical values/defaults. It never renders commands.
A successful parameter-contract validation is not approval or runtime-policy authorization.
Unmet and unknown operational prerequisites become explicit downstream execution blockers
in recommendation packets rather than silently dropping candidate actions from ranking.

## Features And Baselines

Context and candidate text use one deterministic Blake2b-hashed sparse feature
space. All text is case-normalized before regex tokenization to ensure terms like
`StressChaos`, `CPU`, `DNS`, and `NetworkChaos` are extracted deterministically.
Structured `fault_type=...` terms improve meaningful overlap without Python's
randomized hash. There is no constant service bonus because current runbooks have
wildcard service constraints.

The popularity/success baseline uses smoothed relevance and popularity. It fits only
observable outcomes: synthetic complete labels, selected outcomes, and human overrides.
Not-selected, policy-rejected, unsafe-filtered, and unknown-counterfactual rows cannot
contribute utility labels.

The collaborative baseline performs seeded truncated decomposition of the dense
incident/action matrix through power iteration on its Gram matrix, computing the
exact truncated SVD reconstruction $M \approx U \Sigma V^T$. Missing entries are
treated as zero, which is explicit cold-start pessimism rather than an imputed preference.
Latent scores are bounded to `[0,1]` before hybrid fusion. Unseen actions score zero;
unseen incidents receive the training population-mean projection without personalization.

Hybrid scoring is deterministic late fusion:

```text
score = content * 0.45 + collaborative * 0.20 + success * 0.25 - risk_penalty * 0.10
```

Weights are proposals, not calibrated values. Component scores, weighted contributions,
risk contribution, history count, and uncalibrated confidence state are persisted in each
packet for future ablation/explanation.

## Data, Splits, And Label Bias

`InteractionRow` records rank, observation type, policy/approval/executor/verifier facts,
counterfactual status, context hash, family ID, episode ID, schema version, source run,
and timestamp. Family and episode IDs may not cross splits.

There is deliberately no competing split system. A future canonical G5 artifact binds
incident/family assignments by immutable split hash through `bind_rows_to_g5`. Unknown
incidents fail closed. Until then manifests report `pending_g5`.

Synthetic fixtures use obviously fake names, temporal boundaries, distinct holdout families,
strong preferences, ties, sparse history, cold-start queries, and deterministic hashes.

## Persistence And Evaluation

Model serialization is JSON-compatible and deterministic. The envelope hashes its payload
and records catalogue/corpus/training-split hashes, weights, parameters, seed, fit time,
and git provenance. Loading rejects format/integrity/catalogue drift. Serialization itself
performs no file I/O.

Metrics include HitRate@K, MRR@K, NDCG@K, catalog coverage, mutation exposure at K, and
unsafe-packet contract rate. Scores are raw rankings, not calibrated probabilities.

## Future Integration Order

1. Land/review PR #32 first because it changes diagnosis prompt/tool-policy/tool schemas.
   Re-run RS catalogue/schema tests against the merged contract.
2. Bind RS interaction extraction only to Lane B/G5 canonical split metadata.
3. Agree the GRPO packet shape with Lane C; this branch already emits action/tool/risk/
   prerequisites/safety/verification/component metadata without execution authority.
4. Add a toggleable coordinator adapter after integration review; do not enable it in G4.
5. Calibrate only on G5-authorized train/calibration data. Final test remains untouched.

## Known Limitations

- Dense SVD is suitable for small catalogues/fixtures, not large corpora.
- Zero-filled missing interactions can understate novel preferences.
- Current service constraints are wildcard; service-specific learning awaits legal data.
- Content matching depends on diagnosis vocabulary and structured fault types.
- Historical success is observational and vulnerable to policy-selection bias despite
  counterfactual row separation.
- No calibrated confidence, final benchmark result, or production integration exists.
