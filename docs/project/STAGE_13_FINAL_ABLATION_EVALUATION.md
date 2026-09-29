# Stage 13: Final Ablation and Stress Evaluation (Gate G13)

**Status: REOPENED**

The ablation runner is artifact driven. The later
[GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md) and prospective
[v0.3 amendment](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_3.md) replace
the former RS-inclusive five-arm requirement with three arms. Neither is a
frozen experimental protocol. The required empirical artifact matrix does
not exist, so no final model comparison is currently supported.

## Required Matrix

Every run must cover each required variant:

1. Zero-Shot Baseline
2. SFT Model
3. SFT + GRPO

The old V3 and V5 RS-bearing arms and five-arm predetermined artifact remain
historical, non-empirical research, not required variants. The new V4 arm
must use the same SFT parent as V2. Changing labels does not produce
comparable incident outcomes from the current diagnosis-only G6/G8 outputs.
The current runner validates artifact identity one arm at a time and does
not yet enforce this cross-arm lineage; its output is nonclaimable until a
reviewed frozen lineage schema and check exist.

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

## Raw Episode Membership Verification

The full-suite path derives ordered scenario membership from the same raw JSONL
bytes whose SHA-256 is checked against each artifact. Each artifact must also carry
a top-level `split_sha256` matching the SHA-256 of the expected ordered IDs for its
partition. Validation, Test, and Leaderboard use the frozen config split; Adversarial
uses the externally anchored membership list. The ordinary public aggregation helper
applies the same frozen-ID, split-hash, and raw-format checks for Validation, Test,
and Leaderboard; it rejects Adversarial requests.

Parsing is bounded to 64 MiB per raw output, 2 MiB per line, and 100,000 records.
Blank or malformed records, duplicate JSON object keys, non-finite JSON numbers,
and non-object records fail closed. Explicit raw `evaluation_mode` values such as
`mock`, `non_empirical`, `dry_run`, or `test`, `non_empirical: true`, and
`test_only_synthetic_fixture: true` are rejected recursively. Row-based formats
require each episode to declare `evaluation_mode: "empirical"`. No aggregate output
is written until the complete matrix and all raw membership checks pass.

The Zero-Shot Baseline and SFT Model formats require exactly one top-level
`scenario_id` episode row per expected member, in order, with the empirical
row marker. The SFT + GRPO format must be the G9 event stream:
one leading `run_started`, one `episode_started` and one successful
`episode_completed` per expected scenario, then one final `run_completed` summary
whose frozen split digest, counts, and claim-eligibility flag agree. Step and other
in-episode events must remain scoped to the active scenario; repeated step events
never increase episode membership. Interruptions, missing, duplicate,
nested, or out-of-order starts/terminals, mismatched terminal result IDs, and
cross-scenario events are rejected.

The G9 `run_started` evaluator source and checkpoint provenance and the
`run_completed` summary's run ID, model, evaluator source, and provenance must
match the separate artifact exactly. This binds its declared identity to the
hash-checked raw stream, but matching declarations do not attest served model
weights or independently validate its metrics.

## Loss-Preserving G9 Observations

`bench.episode_membership.normalize_g9_event_observations` is a separate,
non-claimable preparation interface for inspecting a bounded G9 JSONL stream.
Callers must supply the SHA-256 expected for the exact raw bytes. The result
retains that digest, the complete parsed event sequence, each episode's ordered
source events, and the source result object when present. Unknown fields and
missing fields remain as observed; the normalizer does not fill them in.
This digest comparison checks content integrity against the caller-supplied
value; it is not provenance attestation, signed approval, or source
authentication.

The interface records structural outcomes emitted by G9 (`completed`, `failed`,
`unscorable`, and `interrupted`). It also preserves an open episode as `partial`
when the stream ends before a terminal event. A final `run_interrupted` remains
an interruption, including a scenario interruption before `episode_started`;
it is never converted to a completion. A structurally complete event stream is
still only an observation: output is marked
`evaluation_mode: NON_EMPIRICAL_OBSERVATION`, `non_empirical: true`,
`empirical_claim_allowed: false`, and `certification_status: NOT_CERTIFIED`.
No A1 incident eligibility, A2 reward, A3 alert-to-verifier TTR, expected
membership denominator, or scientific metric is inferred or computed.

This interface does not replace or relax `derive_raw_scenario_ids` or the
ablation acceptance path. Those strict consumers still require complete,
ordered expected membership, matching artifact identity, claimable successful
episodes, and a final valid `run_completed`; non-claimable, failed, unscorable,
and interrupted streams remain ineligible for aggregation today. This
success-only restriction is an unresolved mismatch with the prospective A1
direction, under which genuine model failures remain eligible negatives. It
must be resolved under a reviewed invalidation protocol before empirical use.

Validation, Test, and Leaderboard membership is derived from the ordered frozen
configuration returned by `config.splits.get_split`, not from IDs declared in a
summary. The Leaderboard contains members from both frozen Train and Validation;
it is **not an independent held-out set**. Adversarial raw episode IDs must exactly
match the ordered list in the externally SHA-256-anchored membership record. The
artifact's declared list, seed, and protocol digest remain required but are not
accepted as a substitute for parsing the raw episodes.

The operator-supplied adversarial digest is a trust anchor, not a signed identity or
approval record, and no approved real adversarial membership record or seed is
checked in. `bench.sft_eval` now records the ordered frozen split digest in its
summary; this software compatibility does not supply a validated SFT checkpoint
or a claimable empirical G8 artifact. A comparable integrated evaluator for
all three required arms is still absent.

Metrics remain arithmetic aggregations of values declared in the input summaries;
they are not recomputed from raw episodes. Every accepted aggregate is explicitly
labeled `evaluation_mode: declared_artifact_aggregation`,
`non_empirical: true`, and `metrics_source: unverified_artifact_summaries`, while
retaining `empirical_claim_allowed: false` and `certification_status: NOT_CERTIFIED`.
Synthetic parser fixtures only exercise the expected row/event schema and do
not represent evaluation evidence. Objective metrics, their denominators,
and historical evidence are unchanged. G13 remains **REOPENED**.

## Historical Predetermined Output

`artifacts/evidence/stage13/ablation_benchmark_results.json` contains the former constant
profiles, including the reported 100% resolution, 18-second TTR, and 0.918 reward. It is
preserved as historical output and is not an empirical finding. The current runner
rejects it as input because it lacks validated raw/source provenance and an anchored
adversarial membership record.

## Current Verification

Local tests prove that incomplete matrices, mock/non-empirical inputs, partition mismatches,
missing metrics, absent raw/source provenance, absent or mismatched artifact split hashes,
mismatched ordered raw episode membership, explicit mock/non-empirical raw markers,
malformed or over-bound JSONL, invalid G9 lifecycle ordering, and absent or mismatched
adversarial membership provenance fail closed. Synthetic schema fixtures cover
the three prospective arms but cannot establish scientific approval or
reproduce missing evaluators. Dry-run
mode emits only a non-empirical execution plan.

G13 can advance only after genuine prerequisite model/checkpoint and integrated-environment
artifacts exist.

**Gate G13 Status: REOPENED**
