# Peer assignment D — G12 integrated policy and G13 measurement

**Starting software baseline:** `b23d6cb4319b77b4d8606934e401141bcb01d23f` (20 October 2026). This is NOT an experiment source freeze, approval or scientific certification.

**Agent entrypoint:** Read this assignment fully, then [peer onboarding](../PEER_ONBOARDING.md), [peer handoff tracker](../PEER_HANDOFF.md), [completion matrix](../COMPLETION_CHECKLIST.md), [master pipeline](../MASTER_PIPELINE_STATUS.md), and [proposed execution protocol](../PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md). Verify current `origin/main` and your draft PR head before editing. Use your own clean worktree, preserve original evidence and all negative outcomes. Make maximum progress on permitted offline work, test it, and add evidence to this PR. Never promote an empirical gate from tests or mock fixtures.

## Current status and dependencies
G12 is **IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**. G13 is **REOPENED**. No independently accepted G9 SFT+GRPO checkpoint/third arm exists, so no final three-arm empirical comparison can be claimed. G10/G11 recommender research is **OUT_OF_SCOPE**: do not make it a required dependency.

## Read first
- [G13 safeguards and reproduction](../G13_OFFLINE_SAFEGUARDS_HANDOFF.md)
- [G9 learned-policy dependencies](../G9_OFFLINE_SAFEGUARDS_HANDOFF.md)
- [Measurement protocol proposal](../PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md)
- [Scientific status matrix](../COMPLETION_CHECKLIST.md)

## Do now
- Reproduce current G13 model-free replay, raw-evidence, lifecycle, lineage and normalization tests without promoting synthetic outputs.
- Independently validate event provenance, eligible attempt denominator, preservation of absent/invalid outputs, comparison coverage, null policy and negative episodes, and separate display of infrastructure exclusions.
- Develop an independently reviewable **prospective** three-arm contract (Base, same Base plus SFT-v17, accepted SFT+GRPO): per-arm serving identity, split and scenario membership, source/scorer hashes, matched seeds and budgets, balanced arm order, reset/observation coverage, and failure/invalidation/reattempt policy.
- Freeze clock origins, wall/monotonic correlation and skew tolerance. TTR begins at admitted Alertmanager delivery and ends at first pre-cleanup objective recovery; unresolved episodes are censored only through actually observed comparable pre-cleanup coverage. Missing/incomparable clocks are null, never invented deadline times.
- Ensure infrastructure exclusions are independently adjudicated from an outcome-blinded projection; independently recompute metrics from raw event lines, not declared summaries.

## Separate authorized empirical steps
After G9 checkpoint acceptance, prepare a separately approved G12 policy-bound controlled incident with verified action approval, recovery and cleanup. For G13, the frozen measurement protocol, permitted arms, Final-Test/adversarial membership, reviewer, trial budget and reattempt decision all require distinct approval. Do not access held-out data in offline preparation.

## Definition of done
- **Offline handoff:** rigorous, testable G13 methodology and capture/lineage contracts.
- **G12:** raw integrated environment episode with exactly accepted learned policy and independently verified causal safety/recovery.
- **G13:** prospectively frozen three-arm comparison, valid raw outcomes including negatives and nulls, separately authorized held-out access and independent recomputation. No surrogate third arm.

**Local progress:** [21 Oct 2026 offline record](G12_G13_LOCAL_PROGRESS_20261021.md) — model-free G13 safeguards reproduced (274 passed); clock-attestation path awaits the project lead's contract freeze; no box below is satisfied.

**PR completion checklist**
- [ ] Model-free G13 safeguard tests and raw replay independently accepted
- [ ] Exact measurement and censoring protocol frozen and reviewed
- [ ] Accepted G9 policy and distinct integrated-run permit verified
- [ ] Authorized G12 raw episode accepted
- [ ] Authorized G13 three-arm measurement independently accepted
