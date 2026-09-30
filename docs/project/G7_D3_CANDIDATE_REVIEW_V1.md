# G7 / D3 Candidate Review Sheet

**D3: PENDING. G7: PARTIAL. Training: NOT AUTHORIZED.**

This is a new review candidate, not a replacement for the frozen historical
64-row schema fixture. The fixture remains preserved and rejected for training.
The required final comparison remains untouched base GAI, SFT, and SFT + GRPO.
RS is retained as optional historical work.

## Decision Requested

The project lead must accept or reject the exact `train-candidate-v1` corpus,
its manifest hash, independent quality review, limitations, and the
[future pilot acceptance contract](G7_SFT_PILOT_ACCEPTANCE_V1.md).
Technical admissibility is separate from scientific adequacy, D3 sign-off,
hardware entitlement, model selection, and authorization to execute.

The candidate uses hand-authored, deterministic contrastive recipes based only
on frozen Train metadata. Tool observations, approvals, and verifier results are
explicit simulations. They are not historical trajectories, model-generated
teacher executions, genuine operator approvals, or empirical incident recovery.
Malformed examples teach safe handling of malformed incoming evidence, not
malformed assistant function calls. Host-blocked P1/P0 records are synthetic
safe-refusal supervision and explicitly identify the model stage as not invoked.

## Review Artifacts

The immutable review bundle is
`artifacts/evidence/stage7/candidates/train-candidate-v1/`.
Its corpus and adjacent manifest anchor exact bytes, semantic row hashes, frozen
Train membership, recipe configuration, source Git commit and construction-file
hashes. The independent quality report records counts and distributions, findings,
duplication measures, and verification limitations. Each regeneration must use
a fresh destination; never overwrite this bundle or prior evidence.

The technical validator checks all four roles against runtime ACLs, schemas,
policy and evidence preconditions; P0 manual/P1 named approval semantics;
call/observation pairing; consistent incident context; canonical role outputs;
outcome diversity; label/provenance exclusion from model input; project Qwen
template generation regions; immutable source and manifest integrity.
Those checks cannot prove that every natural-language conclusion is an expert
diagnosis, that synthetic patterns generalize, or that a trained adapter improves.

## Limits Requiring Lead Judgment

- Sixteen Train scenarios are a small, fixed population. Contrastive cases do
  not create independent incidents or new empirical coverage.
- Hand-authored recipes can teach generator-specific shortcuts and repeated
  patterns. Exact-duplicate rejection is not a complete semantic novelty measure.
  A pre-freeze audit after Triage evidence-path revision found normalized
  assistant-text similarity at least 0.90 in 0/136 Triage pairs, 19/136 Diagnosis
  pairs, 33/136 Remediation pairs and 13/136 Comms pairs. These are heuristic
  pair counts, not a validated novelty score. Diagnosis and Remediation remain
  repetitive; the final frozen audit must recompute these values.
- Scenario metadata influences simulated observations. No expected diagnosis,
  reward, judge label, scenario ID, or held-out outcome is placed in model
  messages, but this does not establish real-world distributional validity.
- Simulated approvals demonstrate control flow only. They do not grant P1
  authority or validate a real operator channel.
- Local Jinja generation-span checks do not prove tokenizer-level masks or
  absence of truncation under the approved pinned remote training stack.
  The candidate now nests the action-bound verifier in the mutation's paired
  tool observation, matching runtime. Verified-resolution summaries are marked
  as synthetic controller supervision, not model-generated teacher behavior.
- A bounded pilot is a feasibility and data-contract experiment, not G7 closure,
  model quality certification, or approval to use Validation/final Test.

## After D3

First resolve D1/D2: an explicitly approved NVIDIA host and budget/entitlement,
immutable Qwen model/tokenizer pins, a complete hashed environment/image, stable
storage and retention, and the non-live tokenizer/mask/truncation preflight.
Then independently review a narrow D3 launch-gate amendment binding the approved
corpus/manifest hashes and recorded lead decision. Candidate v1 currently refuses
all training even after technical PASS; no general bypass flag exists.
Only a later, separately authorized bounded SFT run may load weights or train.
G4/G6 progression and any proposed exception remain explicit lead decisions.

Record a D3 decision with date, lead identity, reviewer, corpus/manifest/source
hashes, accepted limitations, permitted pilot budget/configuration, superseded
version and any conditions. A conversation or technical PASS is not a signed
empirical protocol, spend approval, or live remediation permit.
