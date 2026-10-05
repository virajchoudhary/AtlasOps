# Deferred Research Handoff

Created 5 October 2026 against freshly fetched remote main
`7ac0cfb5c5fbd77d82500a72fd06b802c2b826b7`. This is the source/evidence
basis, not the later finalization commit SHA. Current authorities are the
[master inventory](MASTER_PIPELINE_STATUS.md), [evidence index](../EVIDENCE_INDEX.md)
and [final controlled result](CONTROLLED_G9_FINAL_NEGATIVE_V1.md).
**This document grants no execution, access, spend or deployment authority.**

## Frozen State

- G4 NOT_PASSED: 015 terminal INCONCLUSIVE/unscored, 016 pre-fault
  abort/non-result, 017 completed negative. No attempt 018.
  [015 integrity record](../../artifacts/evidence/stage4/EXP-STAGE4-SF002-015.integrity-index-v1.json)
  and [016/017 chronology plus external audit/archive hashes](CONTROLLED_G9_ADMISSION_V1.md)
  locate canonical evidence. Attempt 017 has prompt/evaluator and Comms
  mismatches and is not pure model-performance evidence.
  Owner-local G4/audit store:
  `C:/Users/viraj/Documents/Codex/2026-10-03/continue-atlasops-from-latest-clean-main-2/outputs/`.
  The evidence index names the precise records; no contents are republished
  and no credential or access authority is granted.
- G7's genuine [v17 artifact](../../artifacts/evidence/stage7/free-t4-v17/RESULT.json)
  and independent reload satisfy bounded G7 artifact acceptance: PASS.
  The preapproved free-T4 profile replaces OCI with a hash-locked venv;
  tolerance applies only before a future separately approved replicate.
  No incident improvement or numerical repeatability is claimed.
- [Base/SFT Validation](BASE_SFT_VALIDATION_RESULT_V1.md): F1
  0.16875 / 0.15935, paired delta -0.00940, schema 6/6 per arm.
  No diagnostic improvement observed. G8 live resolution remains unmet.
- G9 NOT_PASSED/frozen: genuine replacement pilot, 2 optimizer steps,
  4 malformed/blocked completions, rewards all -1, both advantage groups
  zero. Finite KL-driven gradients do not establish reward-driven learning.
  Failed manifest has checkpoint null; no acceptable SFT+GRPO model exists.
- Final aligned diagnostic: 0/8 admissible actions, zero optimizer steps,
  392 unchanged tensor hashes. Neither population probability exactly zero
  nor sole causation by interface mismatch follows from these observations.

## Smallest Future Scientific Work

1. Obtain a new project-lead decision on whether and how to resume either
   frozen experimental track. Review all negative/security/methodological
   records before proposing a versioned Train-only interface/data experiment.
   No old attempt ID, consumed launch approval, parser or output may be reused.
2. For live work, independently establish target/model/operator readiness
   and resolve the documented evaluator/schema discrepancies under a reviewed
   protocol. A new explicitly authorized causal incident must establish
   actual approval, permitted action, objective recovery and cleanup.
3. For RL, preregister the smallest controlled test capable of showing
   admissible actions and nonzero objective reward-driven advantages.
   Only new explicit training authority can permit it. A successful future
   run needs complete raw outcomes, checkpoint inventory and fresh reload.
4. Only after an accepted policy and incident evaluator exist, freeze the
   matched Base/SFT/SFT+GRPO metric, failure-denominator, budget, repetition
   and adversarial rules. Final-Test/Leaderboard access needs its own
   explicit authorization. Do not start a missing third arm or invent metrics.

G6/G8 incident-level empirical closure depends on governed inference and
objective incident evidence. G12 additionally needs an accepted policy and
integrated environment episode. G13 needs those artifacts and a frozen
measurement protocol. G14 peer-host/operator deployment acceptance and
G15 full scientific certification/external submission remain separate.
G10/G11 are historical optional RS work and OUT_OF_SCOPE.

## Entry Points And Preservation

- SFT: `training/sft.py`, `training/sft_free_t4_gate.py`,
  [G7 original acceptance](G7_SFT_PILOT_ACCEPTANCE_V1.md),
  `tests/test_sft_v17_evidence.py`.
- Validation: `scripts/verify_base_sft_validation.py`,
  `tests/test_base_sft_validation_evidence.py`.
- G4: `scripts/run_stage4_golden_incident.py`,
  [run-owned preflight](G4_V38_RUN_OWNED_PREFLIGHT_GUARD_V1.md),
  `tests/test_stage4_run_owned_source_guard.py`.
- G9: `training/grpo_controlled.py`,
  `tests/test_grpo_controlled.py`; final aligned source is archived evidence,
  not an enabled production replacement.
- G12/G13: `tests/test_g12_real_capture.py`,
  `tests/test_candidate_replay.py`,
  [unfrozen three-arm measurement direction](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_3.md).

Never overwrite raw evidence, failed/partial archives, frozen corpora,
approvals, inventories, source hashes or negative summaries. Preserve every
failure, timeout, block and inconclusive outcome. Historical/mock metrics,
cleanup, CI and model-free optimizer fixtures cannot close scientific gates.
The finished local presentation and submission review package remain useful
without asserting full pipeline certification.
