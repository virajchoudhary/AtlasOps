# AtlasOps Evidence Index

Current review basis: freshly fetched main
`7ac0cfb5c5fbd77d82500a72fd06b802c2b826b7`, 5 October 2026.
The [master inventory](project/MASTER_PIPELINE_STATUS.md) controls gate
status. This index locates preserved results and records storage boundaries.
It does not authorize execution, publish private operational evidence, or
certify the full pipeline.

## Current Genuine Results

| Record | Classification | Canonical entry point | Result and boundary |
|---|---|---|---|
| Free-T4 v17 SFT | Completed bounded artifact | [RESULT.json](../artifacts/evidence/stage7/free-t4-v17/RESULT.json), [run manifest](../artifacts/evidence/stage7/free-t4-v17/sft_run_manifest.json) | 68 synthetic Train rows, one epoch, 9 optimizer steps. Real adapter, no incident improvement claim. |
| Independent v17 reload | Completed integrity/reload verification | [reload receipt](../artifacts/evidence/stage7/free-t4-v17/reload-v17.json), [local integrity](../artifacts/evidence/stage7/free-t4-v17/local-verification.json) | Fresh network-isolated process, 392 finite LoRA tensors, 27 checkpoint files. No inference during reload. |
| Matched Base/SFT Validation | Completed diagnostic-only result | [result](project/BASE_SFT_VALIDATION_RESULT_V1.md), [raw responses](../artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/raw_episodes.jsonl), [independent recomputation](../artifacts/evidence/stage8/base-sft-validation-v1/local-independent-recompute-v1.json) | F1 0.16875 / 0.15935, delta -0.00940, schema 6/6 each. No diagnostic improvement observed. Resolution, reward and TTR unavailable. |
| G4 attempt 015 | Terminal INCONCLUSIVE/unscored | [integrity index](../artifacts/evidence/stage4/EXP-STAGE4-SF002-015.integrity-index-v1.json) | Transport timeout, no retry or score. Full operational bundle remains external. |
| G4 attempt 016 | Pre-fault abort/non-result | [run-owned preflight record](project/G4_V38_RUN_OWNED_PREFLIGHT_GUARD_V1.md), [final chronology](project/CONTROLLED_G9_ADMISSION_V1.md) | No model-resolution score. Raw operational records remain external. |
| G4 attempt 017 | Completed negative | [audit/attempt archive anchors](project/CONTROLLED_G9_ADMISSION_V1.md) | Latest completed G4 negative, with prompt/evaluator and Comms caveats. No attempt 018. |
| Controlled G9 replacement v2 | Failed/frozen genuine pilot | [final result](project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md) | 2 optimizer steps, 4 malformed/blocked completions, rewards all -1, both advantage groups zero. No reward-driven learning or accepted checkpoint. Full pilot archive remains external. |
| Final aligned G9 diagnostic | Completed negative diagnostic | [diagnostic](../artifacts/evidence/stage9/final-aligned-diagnostic-v1/diagnostic/diagnostic.json), [samples](../artifacts/evidence/stage9/final-aligned-diagnostic-v1/diagnostic/samples.jsonl), [independent review](../artifacts/evidence/stage9/final-aligned-diagnostic-v1/INDEPENDENT_REVIEW.json) | 0/8 admissible actions, zero optimizer steps, all 392 tensor hashes unchanged. No population-zero or sole-cause conclusion. |

## Integrity Anchors And Storage

- Base/tokenizer: `Qwen/Qwen2.5-7B-Instruct@a09a35458c702b33eeacc393d103063234e8bc28`.
- v17 adapter SHA-256: `f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff`.
- v17 run manifest SHA-256: `7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440`.
- v17 full archive SHA-256: `59fea5f55ff5ecb4ca86d679bd5f8eaa856f1881b514020d92af9014cfb7c418`.
  Weights remain outside ordinary Git in the owner-controlled local/private
  evidence store identified by RESULT.json.
- Validation manifest SHA-256: `878b01e079d294ad35a6ab73365c01e1a3d7cf138a2afaae72b73ecbb59d49b4`.
- Pre-RL audit SHA-256: `1f494d8b71fbdb98843ada450d11e29eae3731552ba74e6289766557f03a27bf`.
- Attempt-017 archive SHA-256: `82d0b62ed33d0fb6ebc2924233a70fb1a1ae2e2385b8e374d06c931456eefb31`.
  Audit and operational archives stay in the prior execution chat's external
  `outputs` store. These are documented anchors, not bundled-byte claims.
  Owner-local location verified without republishing contents:
  `C:/Users/viraj/Documents/Codex/2026-10-03/continue-atlasops-from-latest-clean-main-2/outputs/`.
  The audit filename is `AtlasOps_Pre_RL_Audit_Report_20261005.md`;
  attempt 015 uses `g4-attempt-015-evidence/`, attempt 016 has
  `G4_PREFLIGHT_ABORT_V2.json`, and attempt 017 has `G4_ATTEMPT017_RESULT_V1.json`.
  Reviewers need a separately authorized evidence transfer; these paths
  are not portable package assets or an access grant.
- Controlled replacement archive SHA-256: `39834cc3911bf6e32deec509a1c139d905e17aee802563abcb2ce3802f23cd33`.
  Failed manifest SHA-256: `c8faa1afb8948c504a23fa45f57edf76fc4eb1f47a4074623aed4659ed1809cc`.
  Local preserved extraction: `C:/AtlasOps/controlled-g9-replacement-20261005-v2/`.
- Final diagnostic archive SHA-256: `6137ce1d88eada54152b0c58bb7dd02f9c8758ddacf73adfafffb5a8f598e6c8`.
  Raw samples SHA-256: `84e30346b8948cca44b164f69106fbfbba8bb189f31e892aa0ff7f6d4034439b`.
  Compact evidence is tracked. The full local archive remains outside Git.

The [submission manifest](../artifacts/SUBMISSION_MANIFEST.json) hashes
selected checkout bytes, including this index and compact current evidence.
Private archives and weights are references rather than bundled assets.
Preserved EOL/hash semantics differ by evidence class. Never normalize a
frozen file to make a checksum pass.

## Historical And Non-Empirical Records

- [Experiment registry](EXPERIMENT_REGISTRY.md) preserves dated run prose.
- [G4 recovery index 009-014](../artifacts/evidence/stage4/RECOVERY_INDEX_009_014.md)
  retains earlier negatives, interruptions and cleanup failures.
- [Mock archive boundary](../artifacts/evidence/mock_archive/README.md)
  classifies Stage 6/8/9 fixtures. Their metrics are not team measurements.
- Stage 13 predetermined five-arm profiles and inherited training charts
  remain historical/non-empirical, not a current final comparison.
- G10/G11 retain optional historical synthetic RS evidence, OUT_OF_SCOPE
  for required GAI + RL completion.
- Dated protocol, acceptance, readiness and upstream-gap snapshots retain
  their original source basis. Read [current status](project/MASTER_PIPELINE_STATUS.md)
  before treating an old pending/REOPENED statement as current.

Remaining scientific work is confined to the
[deferred research handoff](project/DEFERRED_RESEARCH_HANDOFF.md).
