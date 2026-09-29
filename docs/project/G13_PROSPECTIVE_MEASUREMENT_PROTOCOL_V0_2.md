# G13 prospective measurement protocol v0.2: partial decision record

**Status: PARTIAL PROJECT-LEAD APPROVAL / NOT FROZEN / NON-EXECUTABLE.**
The project lead explicitly confirmed the recommended A choices for items 1-4
of [v0.1](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_1.md) in the 29 September
2026 Codex conversation. The question explicitly left items 5-7 and all
final-Test, live, training, paid-compute, and deployment actions pending; the
affirmative response did not approve any of those actions. This record is
conversation-backed, not a cryptographic signature or independent evaluation
reviewer sign-off.

Decision inputs: v0.1 SHA-256
`613bbdfe304225f0c330a959f2281aad9ecd39721d086a50ff59c2022f765ce0`;
reviewed clean `main` before this record
`878eb8d1709723e09ba48ef3ee00231af9a5c430`. Preserve v0.1 as the
original unapproved proposal. This v0.2 file is the canonical record for the
four selected directions, not a frozen measurement protocol or a new
empirical result.

## Problem Statement

The artifact-driven G13 runner checks source, hashes, ordered membership,
and G9 stream identity but still aggregates unverified declared summary
numbers. It cannot certify a five-arm comparison or turn synthetic
fixtures into measurements. The next non-live implementation needs clear
directions without inventing the remaining measurement and execution
decisions.

## Solution

Record the four project-lead selections below. They constrain future
non-live schema and scorer development. A later version must settle the
remaining implementation details, obtain independent evaluation review,
freeze its exact code/configuration and membership provenance, and receive
separate final-Test and operator authorization before any empirical use.

## User Story

As the evaluation owner, I need the selected measurement directions and
unresolved fields recorded together so that non-live validators can be
reviewed without treating a proposal or a declared summary as evidence.

## Implementation Decisions

| Decision | Selected direction | Still required before a frozen campaign |
|---|---|---|
| 1. Primary denominator and invalidation | **A:** verifier-confirmed recovery over eligible attempted episodes after an authorized, observed fault and alert. Genuine model failures remain negatives; predefined `INFRA_INVALID` attempts are preserved, separately reported, and excluded only after independent adjudication. | Freeze the post-start infrastructure-failure taxonomy, evidence for eligibility and exclusion, signed reattempt rule, and exact numerator/denominator fields. Do not replace the model denominator with an all-scheduled availability composite. |
| 2. Common reward | **A:** develop one versioned objective scorer for V1-V5 from the G9 direct-action terms: `0.75` verified resolution + `0.25` required-check coverage - `0.25` false-resolution claim, subject to actual raw field availability. Null/unscorable observations do not become invented zeros. | Specify per-episode versus step aggregation, all required raw inputs and missing-field behavior; implement/replay the common scorer and freeze its source/configuration hash before measurement. This selection does not declare today's G9 code a validated five-arm scorer or revive the inherited 70/30 blend. |
| 3. Format, diagnosis and TTR | **A:** primary episode-format and macro-F1 populations include every eligible episode output, with missing/invalid predictions recorded as misses; action validity uses attempted actions. TTR runs from alert delivery to first conclusive verifier success for resolved episodes; unresolved are censored, not assigned a time. | Freeze normalized diagnosis categories, no-output representation, action/Comms schemas, clock sources and precision, and censoring/reporting fields across all arms. A valid-response-only rate is not the primary estimate. |
| 4. Comparison and repetition | **A:** V1-V5 use matched checkpoint pairs, permissions, scenario order, tool/inference/time budgets and paired Test(6) rows. The planned single campaign is descriptive; repeated independent seeds and run-level statistics require preregistration. Leaderboard overlaps Train/Val and is diagnostic only. | Freeze model/checkpoint/serving IDs, budgets, seed schedule if repeated, aggregation unit, interval/reporting method, and a complete evaluator matrix before Test. This is not Test access or authorization to run a campaign. |

## Testing Decisions

- Non-live tests may use explicitly synthetic fixtures to reject missing,
  non-finite, conflicting, duplicated, out-of-order, or mock-labeled raw
  fields. Test at the existing G13 artifact/episode validation interface.
- A candidate common normalizer must preserve raw action, approval, tool,
  verifier, failure, timing and source fields and fail closed on missing
  evidence. It must not silently derive scientific metrics from supplied
  summary JSON or claim eligibility from a hash alone.
- After the remaining rules are frozen, independently recompute each
  numerator, denominator, censoring decision and reward component from
  trusted raw episodes/events, then compare them with declared summaries.
  No such complete recomputation or measured matrix exists today.

## Current Input Limitations

- Empirical G6 and G8 outputs are diagnosis-only rows: observed fault/alert
  eligibility, objective resolution, action/verifier evidence, reward and
  A3 TTR are absent or null. Mock rows can carry synthetic outcome values,
  but their explicit non-empirical markers exclude them from A1/A2 claims.
  Neither path can populate real incident outcomes by inference.
- G9 has action/verifier/reward events, but its episode start precedes
  preflight; its elapsed episode time is not alert-delivery-to-verifier
  TTR. The current G13 membership parser rejects failed or unscorable G9
  terminals, so it cannot yet retain every A1 eligible negative outcome.
- G12 capture retains action and verifier provenance but intentionally
  leaves reward/TTR unevaluated. The V3/V5 integrated empirical evaluator
  paths are absent. A loss-preserving non-live normalizer may expose
  unknown fields, not manufacture eligibility, times or scores.

## Out of Scope and Remaining Decisions

- **Item 5 pending:** adversarial source, admission, actual ordered
  membership, seed, protocol digest and independent reviewer. No placeholder
  ID, historical profile or self-declared digest is approved.
- **Item 6 pending:** final-Test access and exact invalid-run reattempt
  authorization. The A/B choice was not made by the four selections above.
- **Item 7 deferred:** host/model/compute, live G3/G4, P1 operator channel,
  fault injection, training, paid resources, public demo and deployment.
- Independent evaluation reviewer sign-off and a fully specified, hashed
  protocol remain pending. G13 stays `REOPENED`; Stage 15 stays
  `NOT_CERTIFIED`. No live or final-Test work follows from this record.
