# GAI + RL scope revision to Pipeline v2.2

**Status: PROJECT-LEAD SCOPE DIRECTION / NON-LIVE / NOT AN EXPERIMENTAL FREEZE.**
The project lead has removed Recommender Systems (RS) from the final AtlasOps
requirement. This prospective revision supersedes the RS-required portions of
the adopted v2.2 working specification for future work. It does not rewrite
the v2.2 review copies, the partial G13 v0.2 decision record, frozen protocols,
or historical experiments. No new empirical gate passes by changing scope.
The separately preserved
[G13 common measurement v0.3 draft](G13_COMMON_MEASUREMENT_CONTRACT_V0_3_PROPOSAL.md)
is an older, unapproved RS-inclusive planning snapshot. It remains in the
submission inventory for provenance, not as the current three-arm protocol.

## Required system and learning path

Preserve the upstream multi-agent incident flow, objective environment
verification, safety/approval gate, and incident communications:

`Alert -> Triage -> Diagnosis -> Safety/Approval -> Remediation -> Verifier -> Comms`

The required model progression is:

`Base GAI model -> Train-only trajectories -> QLoRA SFT -> SFT evaluation -> direct-action online GRPO -> objective-verifier evaluation`

The existing runbook recommender remains available as a clearly labeled
optional, advisory research artifact. It must not be loaded by default,
contribute ground truth or approval, or become a prerequisite for the GAI + RL
completion path. Its code, original 28-row result, corrected 21-row cohort,
and bounded Stage 11 ranking results retain their historical classifications.

## Stage and gate interpretation

Stage numbers G0-G15 remain stable for traceability. G10 and G11 are
`OUT_OF_SCOPE` in the current gate inventory, not newly passed or failed
gates. Their former `PARTIAL` and bounded-offline `PASS` labels describe
historical RS work only; they no longer block G12, G13, or G15. The historical
labels remain in dated records, not as current gate statuses.

G12 now requires a GAI + RL direct-action integration without an RS dependency.
The optional recommender can be evaluated separately, but its presence cannot
be used to certify the required path. G12 remains
`IMPLEMENTED / EMPIRICAL EVIDENCE MISSING` until a valid trained policy and
real controlled-environment episode are independently verified.

G13's prospective required comparison is three matched arms: base GAI
(zero-shot), the same base plus SFT, and that SFT checkpoint plus corrected
online GRPO. RS-inclusive historical profiles and the old five-arm synthetic
fixtures remain labeled historical/non-empirical; they are not accepted as a
three-arm result. Every arm must use compatible frozen scenario membership,
permissions, budgets and objective verification. Validation is diagnostic,
Test access remains separately authorized, the frozen Leaderboard overlaps
Train/Val and is not an independent held-out set, and adversarial membership
and seed remain pending. G13 is `REOPENED`.

The G13 v0.2 A1-A3 measurement directions (eligible attempted denominator
with adjudicated infrastructure exclusions, a versioned common objective
reward, and all-eligible format/diagnosis populations with censored unresolved
TTR) remain useful directions, not executable formulas. Its A4 five-arm
selection is superseded **only as to the arm count and RS requirement** by
this later project scope direction. Exact raw-field mapping, invalidation,
reattempt, checkpoint/serving identity, budget, repeated-seed, interval and
adversarial rules still require a prospective reviewed and hashed protocol.
Do not infer missing incident outcomes from diagnosis-only or mock rows or
certify metrics from declared summaries.

G4 is `NOT_PASSED`; G6/G8/G12 still lack empirical evidence; G7 is `PARTIAL`;
G9/G13 are `REOPENED`. The G15 gate is `PARTIAL` and the overall submission
package remains `NOT_CERTIFIED`. The scope revision is not
authorization to reserve attempt 015, request live P1 approval, inject faults,
train, read final-Test outcomes, spend on compute, or deploy.

## Implementation order and acceptance

1. Make the required runtime path independent of RS; preserve an explicitly
   opted-in advisory path and fail-closed approval and verifier behavior.
2. Adapt G13 validators and raw-evidence tooling to exactly the three required
   arms without rewriting historical artifacts or manufacturing scores.
3. Complete G4/G6/G7-G9/G12 non-live readiness and remote GPU/private-cluster
   runbooks, with synthetic tests labeled as software evidence only.
4. Reconcile the README, stage contracts, UI, report and Stage 15 package with
   this revision. Verify tests and inventory hashes after each accepted change.
5. Separately authorize and preregister empirical execution, then independently
   recompute and review its raw evidence before any scientific gate claim.

Historical RS work may be cited as an optional extension, never as a required
academic workstream or a substitute for the GAI + RL evidence.
