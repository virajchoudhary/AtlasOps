# Pipeline v2.2 adoption and evidence status

**Working specification for non-live work:** The project lead adopted Sections
1-24 and 26-27 of the 29 September 2026 AtlasOps Master Implementation,
Validation & Deployment Pipeline v2.2. Section 25 is a prospective Stage 13
proposal, **NOT APPROVED**. Adoption changes governance priorities and evidence
standards, not historical protocols or experimental results. The original
upstream README and v1.0/v1.1 documents remain dated references.

## Review basis

- Reviewed clean remote `main`:
  `39bb97ca23b9e9497360860c53b5d8cba36fd707`. This is the source
  snapshot used for the matrix below, not a frozen SHA for a future experiment.
  New code or evidence requires a new review.
- Frozen upstream baseline:
  `Harikishanth/AtlasOps@bf9bd197c9f4a05ae55ade254802a9eef1a74356`.
  The fork retains Git ancestry, MIT license and attribution. The original
  README's GKE/MI300X and benchmark statements are historical claims, not
  reproduced results for this continuation.
- Review-copy PDF SHA-256:
  `20266f27f058894a61149e7f79aa33bdd4b1ed847c561673d933cf66c7e591d0`.
  DOCX SHA-256:
  `ceb40cdcf592b5ac90573637775f4d962eb8db309133c236ba3b9ec2e2d1ffc7`.
  The adoption instruction is separate from the review copies. No PDF or
  DOCX text by itself authorizes execution.
- Evidence precedence: objective environment observations and raw episode
  records, then exact source and test logs, then summaries. See
  [the master inventory](MASTER_PIPELINE_STATUS.md),
  [implementation status](IMPLEMENTATION_STATUS.md), and
  [upstream README gap matrix](UPSTREAM_README_CURRENT_GAP_MATRIX.md).
  This review did not repeat live cluster, model, training or Test execution.

## Current status and gaps

The status column is the declared gate inventory at the reviewed source SHA.
The cited paths are review leads, not substitutes for independent acceptance
evidence. A historical PASS is scoped to its recorded conditions.

| Gate | Status | Source/evidence boundary and next unmet work |
|---|---|---|
| G0 | PASS | `LICENSE`, upstream ancestry, `docs/project/UPSTREAM_BASELINE.md`; freeze a new clean execution SHA only when an actual run is approved. |
| G1 | PASS | `requirements/dev-win-py312.lock`, `.github/workflows/ci.yml`, `tests/test_app_endpoints.py`; refresh security and environment checks at the eventual execution SHA. |
| G2 | PASS | `agents/verifier.py`, `agents/tool_policy.py`, `agents/approval.py`, `tests/test_approval_fail_closed.py`; local contracts do not prove live backend reachability. |
| G3 | PASS (historical, caveated) | `artifacts/evidence/stage3/acceptance_report.json` records a local Kind milestone, but `kubectl_describe`/`kubectl_logs` wrapper failures and absent Jaeger traces remain; fresh target acceptance is required. |
| G4 | NOT_PASSED | `artifacts/evidence/stage4/RECOVERY_INDEX_009_014.md` and `EXP-STAGE4-SF002-010.json`: 010 is a completed negative outcome; 009/011-014 are interrupted/inconclusive. The prospective `G4_PROTOCOL_V34_APPROVAL_CHANNEL.md` is software, not a successful live incident. Attempt 015 remains unreserved. |
| G5 | PASS (governance) | `config/splits.py`, `config/scenario_catalog.py`, `tests/test_stage5_scenario_splits_and_truth.py`; Train 16 / Val 6 / Test 6 are frozen. Audit every downstream consumer; Leaderboard overlaps Train/Val. |
| G6 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | `bench/zero_shot_baseline.py`, `tests/test_stage6_zero_shot_baseline.py`; archived Stage 6 metrics are mock. Real base inference needs immutable serving identity and raw frozen-split episodes. |
| G7 | PARTIAL | `artifacts/evidence/stage7/sft_corpus_manifest.json` records 64 synthetic Train-only examples, not a completed run. `training/sft.py` still needs an independently loadable real adapter and training record. |
| G8 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | `bench/sft_eval.py`, `tests/test_stage8_sft_eval.py`; ordered split digest is recorded, but there is no verified G7 checkpoint result. Historical Stage 8 outputs are mock. |
| G9 | REOPENED | `training/grpo.py`, `training/grpo_environment.py`, `bench/grpo_eval.py`, `tests/test_stage9_grpo_pipeline.py`; direct-action software exists, not a completed trained adapter or held-out real evaluation. |
| G10 | PARTIAL | `artifacts/evidence/stage10/rs_dataset_manifest.json` and `recommender/dataset.py`; scenario-derived interactions do not establish historical operator feedback. Retain the 28-row historical and separate 21-row corrected synthetic cohorts. |
| G11 | PASS (bounded offline) | `recommender/hybrid.py`, `artifacts/evidence/stage11/rs_hybrid_eval_synthetic_v2.json`; four synthetic Test rows support only small-data ranking, not incident resolution improvement. |
| G12 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | `agents/coordinator.py`, `scripts/run_g12_integrated_episode.py`; recommendation and exact policy-action/status capture have local tests. A real checkpoint/environment episode and incremental influence are missing. |
| G13 | REOPENED | `bench/ablation_suite.py`, `bench/episode_membership.py`, `tests/test_stage13_ablation_suite.py`; raw ordered membership and hashes are checked, but declared summary metrics are not independently recomputed from raw episodes. The five-variant/four-partition measured matrix, two integrated evaluators, and approved adversarial membership/seed are missing. See [prospective v0.1](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_1.md). |
| G14 | PARTIAL | `app.py`, `dashboard.py`, `demo/launcher.py`, `tests/test_stage14_demo_safety.py`; read-only software is not a fresh peer-host safe deployment or operator sign-off. Deployment is deferred. |
| G15 | PARTIAL / NOT_CERTIFIED | `scripts/package_submission.py`, `artifacts/SUBMISSION_MANIFEST.json`, `artifacts/SUBMISSION_SUMMARY.md`; hashes establish inventory integrity, not scientific certification. Regenerate after each accepted source/evidence change. |

No new empirical PASS, overall completion percentage, or live readiness verdict
is inferred from this matrix.

## Decisions and work order

1. **D1 recorded:** v2.2 Sections 1-24 and 26-27 adopted for non-live work.
   Retain the v1.1 local Kind instructions as a historical setup reference.
2. **D2/D3 pending:** review the exact metric, invalid-run, reward, repeated-run,
   and adversarial source/seed/membership options in
   [the prospective decision sheet](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_1.md).
   No numeric option or actual adversarial population is approved by this file.
3. **Safe software lane:** finish file-level README/evidence audit, test the
   package and gate labels, design common raw episode normalizers, add
   fail-closed validators, and implement the missing V3/V5 evaluator contracts
   behind non-live fixtures. Preserve mock/source labels. A measured Stage 13
   claim cannot use declared summary values alone.
4. **Prerequisite empirical lane (separate authorization):** fresh G3 host,
   model and operator preflight; causally valid G4; immutable-serving G6;
   real G7/G8 checkpoint and comparison; serialized direct-action G9;
   real G12 integration; then approved G13 Test and adversarial campaigns.
   Do not reserve 015, request P1, inject faults, train, or access quarantined
   outcomes from this adoption.
5. **Deployment lane (deferred):** G14 peer-host safe-mode/operator and
   security acceptance only after the project is finished and approved;
   G15 final certification only after raw evidence, recomputation and
   independent sign-off. Public/live deployment is not authorized here.
