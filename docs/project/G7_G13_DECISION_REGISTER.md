# G7-G13 remaining decision register

**Status: REVIEW DRAFT / NO NEW APPROVALS.** Source basis:
`56575293c0f6bfaebbbf98404d7cb1f5fd19d44e`.
The adopted v2.2 non-live scope and the [v0.2 A1-A4 partial
directions](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_2.md) are already
recorded; this register does not reopen them. It does not approve a
provider, spend, weight download, training, inference, final Test,
Kubernetes mutation, P1 request or deployment. Evidence and sign-off
must be attached to a later version before a pending row becomes approved.
The later [GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md) supersedes
only the old A4 five-arm count and RS requirement. The preserved
[five-arm v0.3 proposal](G13_COMMON_MEASUREMENT_CONTRACT_V0_3_PROPOSAL.md)
is historical, unapproved planning material; it is not the current
comparison or an experimental freeze.

| ID | Remaining project-lead decision | Required proposal and independent evidence before approval | Current state / blocked work |
|---|---|---|---|
| D1 | **Compute and cost:** host, entitlement, GPU/BF16/4-bit support, storage, duration/cost ceiling, security and retention | Measured compatibility and capacity on the selected environment; current quote or verified free/university entitlement; secure transfer and recovery design | **PENDING.** Personal laptop cannot host planned local 7B workload. No remote resource or paid budget is assumed. G7 training blocked. |
| D2 | **Model/tokenizer:** keep Qwen2.5-7B or approve a changed base, with immutable model and tokenizer revisions | License/remote-code review, exact commit/digests, tokenizer/template compatibility and revised three-arm pairing if model changes | **PENDING.** CLI revision arguments do not select or attest a model. No weight download or silent small-model substitution. |
| D3 | **Training data and G7 acceptance:** approve a new versioned Train-only trajectory corpus and independent reload criteria before any SFT run | Frozen corpus bytes/hash, role-tool ACL and P1 approval/negative-outcome audit, no Val/Test leakage, effective hyperparameters, package image and checkpoint retention | **PENDING.** The 64-row synthetic fixture has 32 remediation calls to tools absent from the runtime role ACL and no P1 approval context; it is usable for non-model schema checks only, not a training pilot. No completed adapter exists. |
| D4 | **A1 eligibility/invalidation:** exact authorized fault/observed alert start, post-start failure precedence, `INFRA_INVALID` evidence and signed reattempt rule | Proposed taxonomy with worked raw traces, independent adjudicator, retained negative/interrupted attempts and denominator replay | **A1 DIRECTION SELECTED, MECHANICS PROPOSED / NOT APPROVED.** No selective exclusion or final-Test rerun. |
| D5 | **A2 common scorer:** per-step/episode unit, required-check set/coverage, false-claim mapping, missing fields, any clipping, source/config hash and three-arm replay | Versioned implementation, independent worked examples, raw-field coverage in base/SFT/SFT+GRPO, discrepancy check against submitted summaries | **A2 DIRECTION SELECTED, SCORER PROPOSED / NOT APPROVED.** G9 terms are a candidate, not a frozen common scorer. |
| D6 | **A3 diagnosis/format/TTR:** label mapping, no-output and multi-fault handling, action/Comms schemas, alert-delivery clock, first conclusive verifier endpoint, precision and censoring | Raw event/schema examples for every arm, label isolation, clock correlation and independent test of invalid/unknown cases | **A3 DIRECTION SELECTED, MAPPINGS PROPOSED / NOT APPROVED.** G6/G8 diagnosis-only time is not incident TTR. |
| D7 | **A4 budgets/repetition:** exact base/SFT/SFT+GRPO checkpoint lineage, permissions, scenario order, tool/inference/time budgets, seed schedule, aggregation unit and interval method | Complete three-arm evaluator matrix, provenance attestations and pre-registered paired design | **A4 matched-design direction retained; old RS-inclusive arm count superseded. PARAMETERS PROPOSED / NOT APPROVED.** Six Test rows alone justify descriptive reporting only. |
| D8 | **Adversarial item 5:** generator or independent source, safety/novelty review, actual ordered IDs/count, seed and protocol digest | Independent admission and signed membership record, externally anchored SHA-256 before any arm exposure | **PENDING.** Generated proposals and historical profiles are not admitted membership. |
| D9 | **Final-Test item 6:** access owner, single-campaign schedule, invalidation/retry rule and audit log | Frozen protocol/scorer/checkpoints/evaluators, independent evaluation sign-off, source and model identity, access control | **PENDING.** No final-Test outcome access or adaptive reattempt. |
| D10 | **G9/G4 live item 7:** target cluster, actual operator/P1 channel, zero-Chaos preflight, fault/cleanup/recovery authority and cost | Fresh G3/G4 acceptance, verified parent checkpoint, model residency, independent safety review and separate operator authorization | **DEFERRED / NOT AUTHORIZED.** G4 `NOT_PASSED`; P1 reject/timeout/missing decision stays blocked. |
| D11 | **Deployment and final certification:** peer/cloud host, privacy/security, public access, presentation and submission sign-off | Completed evidence gates, verified runtime, independent review and explicit later release authorization | **DEFERRED / NOT AUTHORIZED.** Stage 15 remains `NOT_CERTIFIED`. |
| D12 | **G8 gate reconciliation:** whether the stated resolution-rate acceptance requires a new integrated evaluator before RL | Compare master G8 criterion with the diagnosis-only G8 raw contract; review a proposed evaluator or versioned criterion change without using mock outputs | **PENDING.** The current G8 evaluator leaves resolution null; no empirical G8 gate PASS follows from diagnosis scores. |

## Decision Record Requirements

For each future decision record the chosen option, rationale, scope, date,
project-lead identity, independent reviewer and evidence links, exact
protocol/source/configuration hashes, and superseded version. A general
conversation approval is not a signed empirical protocol, access grant,
spend authorization or live P1 decision. A failed or interrupted attempt
remains visible after any later decision.
The SFT runner's role-tool and known-fixture checks are technical admission
only; passing them for a different corpus does not authenticate a project-lead
D3 approval. Do not execute model training until that separate decision is
recorded.

The [proposed common contract](G13_COMMON_MEASUREMENT_CONTRACT_V0_3_PROPOSAL.md),
[training-readiness assessment](G7_G9_REMOTE_TRAINING_READINESS.md) and
[staged runbook](G7_G13_REMOTE_EXECUTION_RUNBOOK.md) are review inputs.
None can turn G4 `NOT_PASSED`, G13 `REOPENED`, or Stage 15 `NOT_CERTIFIED`
into a PASS without the missing independent raw and runtime evidence.
