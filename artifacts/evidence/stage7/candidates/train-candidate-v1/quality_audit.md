# Independent Frozen Corpus Quality Audit

**Verdict: PASS WITH LIMITATIONS for technical D3 review readiness.
D3 PENDING. Training NOT AUTHORIZED. G7 PARTIAL.**

Independent reviewer: `frozen_corpus_final_review`, curated
`azure-kmamc/gpt-6-luna`, Max reasoning. The root verified the child's own
fresh runtime `turn_context`; behavioral read-only review was requested, not
claimed as sandbox-enforced isolation. Reviewed on 30 September 2026.
Root integration/final acceptance remained GPT-6.1 Sol.

## Standards

No blocking standards issue was found in the reviewed paths.
Runtime ACLs, schemas, policy and evidence preconditions are reused by candidate
admission. The production D3 gate has no bypass flag, and the historical
fixture byte/assistant-target rejection remains in place.

## Spec and Identity

The independent reviewer inspected the actual frozen corpus and adjacent
manifest, ran read-only `validate_candidate_snapshot`, and verified deterministic
row replay, immutable source blobs, config/file hashes, counts and distributions.
It reviewed the change against main
`087a81132a1abb5ed2d69fdda501f85fd11a72db`.
Construction commit `e802dd3a5522c30d3919952954e86827fa96caa1` contains that
main revision as its second parent and the committed candidate source.

| Item | Verified value |
|---|---|
| Corpus version | `train-candidate-v1` |
| Row schema / wire format | `atlasops-sft-candidate-v1` / `openai-tool-messages-v1` |
| Corpus raw and canonical-LF SHA-256 | `19606e4fec300f641c7c8b8a989497367a444a870d3491f225004c01df3ee5fd` |
| Corpus bytes | 655065 |
| Manifest raw SHA-256 | `35c9fd63328ef1319f616f2a23a025be38ad99c594dcd8bb38b733e0ff44c67c` |
| Rows / Train scenarios / case groups | 68 / 16 / 17 |
| Roles | Triage 17, Diagnosis 17, Remediation 17, Comms 17 |
| Paired tool calls / observations | 147 / 147 |
| Exact duplicate assistant targets | 0 |
| Technical admission / lead approval | PASS / PENDING |

## Correctness, Safety and Isolation

- Each model tool call is legal for its runtime role and matches the actual
  exposed schema. Runtime policy limits and remediation evidence preconditions
  are checked, not substituted with a parallel tool registry.
- P0 remediation is manual-only. P1 mutation requires a simulated explicit
  named approval record. Rejected, timed-out, missing and malformed P1 decisions
  produce host-blocked Remediation/Comms examples with no model tool calls.
- Every call has an exactly paired observation. Mutation verification is nested
  in that response and bound to the call/action. Resolved examples explicitly
  identify synthetic controller-resolution supervision.
- Construction uses only frozen Train scenario metadata. No Validation,
  adversarial, leaderboard or final-Test outcomes are inspected or exposed.
  Train lookup guards and admission reject non-Train IDs; model-visible
  scenario/expected-label/reward/judge fields are excluded. Catalog source
  bytes are hashed for provenance, not used to extract held-out outcomes.
- Triage call reasoning uses prior observations; the independent local final
  review found no anticipatory future-output leakage. Malformed input examples
  quarantine incoming evidence rather than supervise illegal assistant calls.
- Canonical role prompts/tool schemas and the project-owned Qwen generation
  template are preserved. Jinja generation-span tests and assistant-only
  configuration are software evidence, not tokenizer/TRL runtime proof.
- The historical 64-row fixture and tracked Stage 7 evidence are unchanged.
  Its existing training rejection remains covered by the focused tests.

## Distributions

Role-stage outcomes, not incident-success rates:
blocked 10, failed 2, inconclusive 19, malformed 4, successful 16, unresolved 17.
Severity rows: P0 4, P1 28, P2 36.
Approval rows: P0 manual 4; P1 approved 12, rejected 4, timeout 4, missing 4,
malformed 4; P2 auto 36.
Synthetic paired-tool success flags: 144 true and 3 false.

| Tool | Calls |
|---|---:|
| alertmanager_list_alerts | 15 |
| argocd_app_history | 1 |
| argocd_rollback | 1 |
| chaos_list_experiments | 17 |
| chaos_stop_experiment | 9 |
| jaeger_search | 6 |
| kubectl_get | 27 |
| kubectl_logs | 2 |
| kubectl_top_pods | 8 |
| postmortem_draft | 13 |
| promql_query | 35 |
| slack_post_update | 13 |

The adjacent manifest records ordered Train IDs, tier counts, per-role outcomes
and tool counts, semantic/config/member hashes, and construction-file hashes.

## Duplication and Limitations

The frozen review used a diagnostic `SequenceMatcher` heuristic on joined
assistant text, normalizing incident IDs, available
`alert.commonLabels.service` values and numeric literals. Same-role pairs with
similarity >=0.90: Triage 0/136, Diagnosis 31/136, Remediation 33/136,
Comms 36/136. This method differs from the broader service-normalization
pre-freeze heuristic, which found 0/136, 19/136, 33/136 and 13/136 respectively.
Neither is a formal semantic novelty score. Diagnosis, Remediation and Comms
remain template-heavy; exact-duplicate rejection does not establish expert
quality or independent data coverage.

This is a small, hand-authored scenario-derived synthetic corpus, not genuine
historical expert trajectories or teacher-model executions. Case variants do
not add independent incidents. Tool success flags mostly describe synthetic
read-only responses and do not model real incident-frequency distributions.
No empirical performance, live safety certification or generalization follows.

Four approval-blocked P1 Comms rows state that their role did not create an
external update/postmortem. The runtime can separately send a coordinator-level
Slack fallback when a webhook is configured. The rows are **role-stage refusal
supervision**, not end-to-end communication traces; the lead must explicitly
accept that scope distinction before a pilot.

Tokenizer/TRL token-level masks and truncation on every row, approved NVIDIA
GPU/package compatibility, real adapter reload, training and all empirical
outcomes remain unverified. The independent reviewer did not run pytest or
load models under its read-only assignment. Root-run final affected software
tests were 297 passed, 7 existing environment-gated skips; the frozen-candidate
selection separately passed 27 tests. CI acceptance is recorded in the PR,
not invented in this audit.

## D3 Decision

The candidate is technically reproducible and ready for project-lead
review of a bounded synthetic feasibility pilot, subject to the limitations
above. The lead must accept/reject the exact corpus/manifest hashes, synthetic
controller/host-refusal supervision, repetition and limited scenario coverage,
and future pilot acceptance contract. This audit does not approve D3.

The runner still refuses pending input before output creation or ML imports.
After D3, resolve D1/D2 host/budget, immutable model/tokenizer revisions,
complete environment provenance and the all-row mask/truncation preflight,
then independently review a narrow hash-bound launch-gate amendment and obtain
separate run authorization. No training, model weights, paid compute, live
Kubernetes/fault/P1 authority, final-Test access or deployment occurred here.
