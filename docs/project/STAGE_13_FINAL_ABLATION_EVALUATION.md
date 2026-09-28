# Stage 13: Final Ablation and Stress Evaluation (Gate G13)

**Status: REOPENED**

The ablation runner is now artifact driven. The required empirical artifact matrix does not
exist, so no final model comparison is currently supported.

## Required Matrix

Every run must cover each required variant:

1. Zero-Shot Baseline
2. SFT Model
3. SFT + Recommender
4. Online GRPO RL
5. Full Pipeline (GAI + RS + RL)

Each variant must provide empirical artifacts for Validation, Test, Leaderboard, and
Adversarial partitions. Every artifact must:

- identify the matching partition;
- be marked empirical and eligible for claims;
- contain measured resolution rate, contract reward, and format compliance; average
  TTR may be null only when no incident resolved, and its aggregation then has
  `n=0`, `mean=null`, and no interval;
- retain signed verifier-grounded reward values, including the G9 false-resolution
  penalty down to `-0.25`;
- supply raw prediction or trajectory evidence whose bytes match its SHA-256;
- identify its variant and partition, a distinct run ID, and a clean full source SHA.

Distinct repeated runs are aggregated with sample count, mean, and a two-sided
Student-t 95% confidence-interval half-width when at least two runs exist. The
small-sample critical values are tabulated to three decimals; larger samples
use a t-quantile expansion. This interval assumes independent runs. The input
manifest and every source artifact are preserved by path and SHA-256.

## Adversarial Membership Contract

A full-suite aggregation requires the input manifest to reference an actual JSON
membership record with `adversarial_membership` (a path, relative to the input
manifest unless absolute). The operator must separately supply the expected record
SHA-256 with `--expected-adversarial-membership-sha256`; a digest stored in the input
manifest is not an accepted trust anchor. The record is limited to 262,144 bytes and
must contain exactly the following fields, with no duplicate JSON keys:

```json
{
  "schema_version": 1,
  "split": "adversarial",
  "seed": 7,
  "protocol_sha256": "<64-character SHA-256>",
  "adversarial_ids": ["adv-example-001"]
}
```

The seed must be a non-negative integer. IDs must be unique, non-empty, safe IDs in
the adversarial generator's `adv-...` form, with at most 4,096 IDs. Each artifact in
the Adversarial partition must declare `split: "adversarial"`, the record's exact
ordered `adversarial_ids` list, matching `seed` and `protocol_sha256`, and
`adversarial_membership_sha256` equal to the externally supplied digest. Missing or
mismatched provenance fails before the output directory is created.
The public per-partition helper rejects direct adversarial aggregation; only
the full-suite path may pass a membership record after checking the external
digest. Its lower-level aggregator is internal and is not an approval API.

An accepted artifact aggregation is still not gate certification: the output sets
`empirical_claim_allowed` to `false` and `certification_status` to `NOT_CERTIFIED`.
No approved real adversarial membership record or seed is checked in.

**Remaining provenance limitation:** the current artifact schema verifies raw
prediction/trajectory bytes against their declared hashes, but does not standardize
per-row adversarial episode IDs or recompute membership from those bytes. The runner
therefore checks the artifact's declared membership against the anchored record; it
cannot prove that the hashed raw episodes actually contain exactly those IDs. The
operator-supplied digest is also a trust anchor, not a signed identity/approval
record. A future evaluator schema must bind raw episode IDs to the membership record
before scientific approval is possible. G13 remains **REOPENED**.

## Historical Predetermined Output

`artifacts/evidence/stage13/ablation_benchmark_results.json` contains the former constant
profiles, including the reported 100% resolution, 18-second TTR, and 0.918 reward. It is
preserved as historical output and is not an empirical finding. The current runner
rejects it as input because it lacks validated raw/source provenance and an anchored
adversarial membership record.

## Current Verification

Local tests prove that incomplete matrices, mock/non-empirical inputs, partition mismatches,
missing metrics, absent raw/source provenance, and absent or mismatched adversarial
membership provenance fail closed. Positive fixtures use synthetic test-only membership
and cannot establish scientific approval. Dry-run mode emits only a non-empirical execution
plan.

G13 can advance only after genuine prerequisite model/checkpoint and integrated-environment
artifacts exist.

**Gate G13 Status: REOPENED**
