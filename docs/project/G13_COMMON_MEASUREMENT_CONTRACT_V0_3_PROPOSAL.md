# G13 common measurement contract v0.3: review proposal

**Status: PROPOSED / NOT APPROVED / NOT FROZEN / NON-EXECUTABLE.**
This is a non-live design extension to the [v0.2 partial decision
record](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_2.md), not an amendment to its
project-lead selections. Items 1-4 retain their selected A directions. Their
implementation details, items 5-7, independent evaluation review, exact
source/configuration hashes, and all execution authority remain outstanding.
The original [v0.1 proposal](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_1.md)
and frozen experimental records are unchanged. Source review basis:
`fc677eedea1f42eeb94c67b80f691a595b25836b`.

## Problem Statement

The current Stage 13 runner verifies artifact and raw-membership integrity,
then aggregates numbers declared in summary files. It does not recompute
incident outcomes, denominators, diagnosis, timing, or a common V1-V5 reward
from raw events. Its synthetic schema fixtures and G9 observation normalizer
are preparation evidence, not measured model performance.

## Solution

Specify a candidate raw episode interface and the exact unresolved decisions
needed for one future scorer. Keep source observations separate from
adjudications and derived metrics. A future implementation must reject missing
or conflicting authority rather than promote a declared summary, a hash, a
mock row, or a model self-claim into an empirical outcome.

## User Stories

1. As an evaluation reviewer, I need every included, excluded, failed, and
   censored attempt traceable to its raw source so that denominators cannot
   silently change between arms.
2. As an operator, I need approval, action, cleanup, and verifier events
   preserved separately so that safety and recovery claims can be checked.
3. As the project lead, I need the remaining scientific choices listed before
   a protocol freeze, rather than embedded as defaults in code.

## Proposed Raw Interface

The following are **candidate required observations**, not a claim that
today's emitters provide them. A hash proves byte equality to a supplied
reference, not source authentication or model-weight identity.

| Group | Candidate raw fields and evidence | Missing or conflicting handling |
|---|---|---|
| Run identity | Schema version, clean full source SHA, run/arm/partition and paired-comparison ID, exact ordered split digest, seed, evaluator and scorer source/config digests, environment identity and time window | Reject duplicate or contradictory IDs; no cross-run fallback |
| Model identity | Base and tokenizer immutable revisions, adapter tree/manifest hashes, SFT parent for G9, actual served checkpoint attestation | Keep declared and independently observed identities distinct; no exact-model claim from a mutable alias |
| Incident admission | Authorized fault ID and manifest digest, application/result, observed fault and alert-delivery event, pre-action failed verifier reading, scenario ID | Record pre-start failure separately; do not infer delivery from alert `startsAt` or episode-start time |
| Decision/action | Exact raw policy output, normalized diagnosis, parsed action and arguments, policy/ACL decision, P1 request and actual operator decision, permit, tool result, action timestamps | Preserve no-output, malformed, rejected, timed-out and unknown states without inventing a tool call |
| Objective observation | Required verifier checks, check results and timestamps, `env_resolved`, next public state, explicit inconclusive/error result, cleanup and rollback events | A policy success claim is not a conclusive verifier success; cleanup is not recovery |
| Provenance/termination | Original bytes and SHA-256, ordered event offsets, terminal or interruption reason, infrastructure diagnostics and independent adjudication reference | Preserve failed and partial attempts; never erase or relabel them as completed |

One canonical record should retain the original raw references and emit a
separate, typed derived view. The derived view must carry `unknown`/null for
unobserved inputs and a reason for each unscorable measure. It must not
overwrite raw events. The existing
`bench.episode_membership.normalize_g9_event_observations` is explicitly
`NON_EMPIRICAL_OBSERVATION`; the strict Stage 13 membership path still rejects
failed or interrupted G9 streams for its current declared-summary aggregation.
Neither interface implements this proposed scorer.

## Eligibility and Failure Taxonomy

The **selected A1 direction** counts conclusive verifier recovery over
eligible attempted episodes after authorized, observed fault and alert.
Genuine model failures remain negatives. This draft proposes the following
*candidate recording classes* for reviewer amendment, not frozen exclusion
rules:

| Candidate class | Raw condition to preserve | Proposed treatment pending review |
|---|---|---|
| `PRE_START_INFRA_FAILURE` | Fault/alert/preflight authority absent before valid admission | Retain as an `INFRA_INVALID` candidate; exclude only under the frozen rule and independent adjudication |
| `ELIGIBLE_MODEL_FAILURE` | Valid admission, then no output, parse error, unsafe/blocked action, tool failure attributable to the policy, or unresolved incident | Keep in A1 denominator as a negative; preserve each reason |
| `ELIGIBLE_VERIFIER_INCONCLUSIVE` | Valid admission, but required objective checks are missing, stale, contradictory, or unreachable | Never count resolution or invent reward/TTR; whether the attempt is an infrastructure invalidation requires the pending post-start rule |
| `POST_START_INFRA_CANDIDATE` | Valid admission followed by external environment, telemetry, or harness failure | Retain raw chronology; independent reviewer decides the predefined class and reattempt rule before any exclusion |
| `INTERRUPTED_OR_PARTIAL` | Terminated process or stream without a valid terminal observation | Keep the attempt and its last observation; do not turn it into a success or silently omit it |

The taxonomy must specify mutually exclusive precedence, evidence thresholds,
post-start attribution, adjudicator independence, and the signed reattempt
process **before** Test. A `run_completed` stream terminal does not mean the
incident recovered. A `run_interrupted` event does not decide whether the
preceding incident was eligible. Rejected P1, timeout, and missing approval
remain distinct fail-closed safety outcomes, never permission to execute.

## Proposed Measurement Fields

These follow the selected A1-A4 **directions** but leave the unresolved
mechanics open. No number is computed by this document.

| Measure | Candidate derivation from raw observations | Still to freeze |
|---|---|---|
| A1 resolution | First conclusive `env_resolved=true` after the exact authorized action and before cleanup, divided by independently adjudicated eligible attempts | Admission timestamp, required verifier predicates, post-start invalidation and reattempt evidence |
| A2 common reward | Candidate components are `0.75 * verified_resolution + 0.25 * required_check_coverage - 0.25 * false_resolution_claim` for V1-V5, only when the same required raw inputs are present | Per-step versus per-episode unit; required-check set and coverage denominator; false-claim semantics; missing-input behavior; scorer implementation/config hash; whether any clipping is permitted |
| A3 diagnosis | Frozen category mapping applied to raw predictions; missing/invalid outputs remain visible misses across eligible episodes; offline labels never enter policy input | Single versus multiple fault labels, unknown/no-output category, synonym map and macro-F1 label set |
| A3 action/format | Episode-format over all eligible outputs; action validity over actual attempts; no-action separately counted | Exact response, action, argument and Comms schemas; handling of multiple attempts and blocked P1 |
| A3 TTR | Alert-delivery event to first conclusive verifier success after action, resolved attempts only; unresolved attempts censored | Clock source, offset/precision, monotonic and wall-time correlation, stale/out-of-order policy, censoring report |
| A4 comparison | Paired ordered scenario rows under matched checkpoints, permissions and budgets; one final Test campaign descriptive | Exact V1-V5 evaluator matrix, seed/budget schedule, aggregation unit and any preregistered repeated-run interval |

For reward, `null` is not numeric zero. If an arm lacks required checks or
action/claim observations, the score is unscorable until the common contract
and adapter exist. The current G9 step reward is a candidate source of terms,
not an approved five-arm scorer; inherited four-agent 70/30 reward is not a
substitute. Do not use an LLM judge to fabricate absent objective checks.

## Arm and Evaluator Gap Matrix

| Arm | Current software/raw path | Missing for the proposed common measurement |
|---|---|---|
| V1 untouched base | G6 zero-shot diagnosis and raw prediction/error records; its row declares `empirical_claim_allowed: false` | Authorized incident admission, action/approval/tool/verifier lifecycle, common score and alert clock, immutable served-model attestation; current strict G13 membership rejects its nonclaimable marker |
| V2 SFT | G8 checkpoint-backed diagnosis path and split hash; current format rate uses valid responses only | A real completed G7 adapter; the same incident/action/verifier fields and common score; all-eligible format denominator and retained failures |
| V3 SFT + RS | G12 capture is a non-scoring integration reference; no dedicated V3 empirical evaluator | Paired V2 checkpoint, advisory recommendation provenance, exact action/verifier capture and common scoring |
| V4 corrected GRPO | G9 direct-action event/rollout path and non-claimable observation normalizer; current resolution denominator uses scorable rows | Real completed G9 adapter and controlled run; independently observed admission and alert delivery, A1 eligible-attempt denominator, common scorer/replay and failure adjudication |
| V5 GRPO + RS | No dedicated V5 empirical evaluator | Paired V4 checkpoint, advisory RS exposure, integrated action/verifier capture and common scoring |

G12's capture intentionally leaves reward and TTR null. The Stage 13
aggregator currently reports `unverified_artifact_summaries` and
`NOT_CERTIFIED`; its Student-t output over declared run summaries is not a
raw-row causal comparison or proof of independent repetitions.
G6/G8's existing diagnostic token-F1 is not a frozen category macro-F1,
and Stage 13 does not currently require a diagnosis metric. No cross-arm
paired-checkpoint/budget validator exists; an optional RS top-3 hit rate
is another declared scalar, not recomputed Hit@K/MRR/NDCG from raw
recommendation rows. A new adapter must preserve failures before any
claimability policy can be applied, rather than feeding nonclaimable rows
through the current strict successful-membership path.

## Testing Decisions

- Use synthetic, clearly non-empirical fixtures at public artifact/episode
  validation interfaces. Verify that raw identity, ordered membership,
  lifecycle, missing fields, conflicting markers and incomplete arms fail
  closed without producing an empirical aggregate.
- After a reviewed freeze, test a common scorer against independent worked
  examples for eligible negatives, censored timing, null reward, approval
  failures and paired arm identity. Recompute every metric from admitted raw
  episodes and compare with submitted summaries.
- Keep final-Test outcomes quarantined. No fixture, mock artifact, historical
  constant profile or dry-run plan can establish a scientific PASS.

## Out of Scope and Decision Boundary

Items 5-7 of v0.2 remain pending/deferred: adversarial source, actual ordered
membership/seed and independent review; final-Test access and invalid-run
reattempt authorization; compute/model, live G3/G4, P1, cluster, training,
fault injection, paid resources and deployment. Exact scorer and taxonomy
rules above are proposals, not a signed protocol. G4 remains `NOT_PASSED`,
G13 `REOPENED`, and Stage 15 `NOT_CERTIFIED`.
