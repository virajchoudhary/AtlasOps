# Peer assignment B — G6 Base and G8 matched SFT incident evaluations

**Starting software baseline:** `b23d6cb4319b77b4d8606934e401141bcb01d23f` (20 October 2026). This is NOT an experiment source freeze, approval or scientific certification.

**Agent entrypoint:** Read this assignment fully, then [peer onboarding](../PEER_ONBOARDING.md), [peer handoff tracker](../PEER_HANDOFF.md), [completion matrix](../COMPLETION_CHECKLIST.md), [master pipeline](../MASTER_PIPELINE_STATUS.md), and [proposed execution protocol](../PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md). Verify current `origin/main` and your draft PR head before editing. Use your own clean worktree, preserve original evidence and all negative outcomes. Make maximum progress on permitted offline work, test it, and add evidence to this PR. Never promote an empirical gate from tests or mock fixtures.

## Current status
G6 and G8 are **IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**, not passed. The real six-scenario Validation diagnostic yielded Base F1 **0.16875** and SFT-v17 F1 **0.15935** (delta **−0.00940**). Those are diagnosis metrics, not incident recovery. G7's genuine v17 artifact and offline reload already satisfy its bounded artifact scope; do not repeat or relabel G7. Valid causal G4 evidence and a separately approved evaluation population are prerequisites.

## Do now
- Read the distinct A6 and paired B stages in [execution proposal v3](../PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md), the [frozen splits](../MASTER_PIPELINE_STATUS.md), and [negative diagnostic context](../COMPLETION_CHECKLIST.md).
- Inspect existing scenario catalog, evaluator, capture, replay and validation contracts. Implement only missing offline correctness protections: raw event provenance, eligible population, negative/blocked/null rows, action and verifier truth, duration clocks, reset equivalence evidence, per-arm lineage and lossless export. Test with synthetic fixtures explicitly labeled non-empirical.
- Prepare two **separately approvable** protocols: A6 Base-only six Validation scenarios (six fresh episodes); B twelve distinct Base/v17 arms across the same six scenarios. Counterbalance predeclared order: odd numbered pairs Base→v17, even numbered pairs v17→Base, with verified reset/telemetry state. Never reuse G6 episodes as G8 arms.
- Freeze seeds, membership, same role prompts, tokenizer, budgets, failure denominator, objective recovery/TTR predicates, clocks, retry exclusion and peer-review criteria. Predeclare the original G8 criterion's acceptance and improvement adjudication; do not decide it from a better-looking subset.

## Authority and stop conditions
No inference, workload startup, fault injection, P1 action, testing on held-out Final-Test or deployment without separate run-specific approvals. G4 accepted result, host/model/operator qualification and source/predicate freeze are entry requirements. Retain every failure, inconclusive result and expired episode. Do not reattempt or enlarge a budget automatically.

## Definition of done
- **Offline handoff:** two separately reviewed frozen protocol candidates and independently tested raw-measurement tooling.
- **G6 empirical evidence:** original Base-only admitted incident outcomes and independently recomputed evaluation, with accepted coverage of G6's actual criterion.
- **G8 empirical evidence:** raw matched arm episodes, format, action/safety, recovery and TTR measures with honest negatives; independent adjudication of the originally required improvement criterion. Diagnosis-only F1 cannot substitute.

**Local progress:** [21 Oct 2026 offline record](G6_G8_LOCAL_PROGRESS_20261021.md) — Validation population, manifest hashes and counterbalanced A6/B arm order resolved from source; blocked on G4; no box below is satisfied.

**PR completion checklist**
- [ ] G4 causal dependency independently accepted
- [ ] A6 and B protocols independently reviewed and approved
- [ ] Synthetic offline evaluators preserve negative/null outcomes
- [ ] Authorized, separately bounded G6 and G8 campaigns completed
- [ ] Original criteria independently recomputed, with full raw artifacts retained
