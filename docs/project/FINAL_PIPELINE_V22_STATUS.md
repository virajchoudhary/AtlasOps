# Pipeline v2.2 adoption and evidence status

**Current scope amendment:** The later project-lead
[GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md) removes RS as a final
requirement. This file retains the v2.2 adoption provenance and applies the
amendment to the current gate inventory below. The original review copies,
v0.2 partial G13 choices and historical RS evidence remain unchanged.

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
| G4 | NOT_PASSED | `artifacts/evidence/stage4/RECOVERY_INDEX_009_014.md` and `EXP-STAGE4-SF002-010.json`: 010 is a completed negative outcome; 009/011-014 are interrupted/inconclusive. Prospective [v3.4](G4_PROTOCOL_V34_APPROVAL_CHANNEL.md), [v3.5](G4_PROTOCOL_V35_CAUSAL_EVIDENCE.md), and [v3.6](G4_PROTOCOL_V36_SETTLING_DEADLINE.md) are software/protocol candidates, not a successful live incident. Attempt 015 remains unreserved. |
| G5 | PASS (governance) | `config/splits.py`, `config/scenario_catalog.py`, `tests/test_stage5_scenario_splits_and_truth.py`; Train 16 / Val 6 / Test 6 are frozen. Audit every downstream consumer; Leaderboard overlaps Train/Val. |
| G6 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | `bench/zero_shot_baseline.py`, `tests/test_stage6_zero_shot_baseline.py`; archived Stage 6 metrics are mock. Real base inference needs immutable serving identity and raw frozen-split episodes. |
| G7 | PARTIAL | The historical 64-row fixture is not training data. Frozen `train-candidate-v1` has 68 synthetic Train rows but pins the pre-v3.6 coordinator; current-source admission rejects it pending a separately reviewed new version and D3 decision. No completed, independently loadable adapter or training record exists. |
| G8 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | `bench/sft_eval.py`, `tests/test_stage8_sft_eval.py`; ordered split digest is recorded, but there is no verified G7 checkpoint result. Historical Stage 8 outputs are mock. |
| G9 | REOPENED | `training/grpo.py`, `training/grpo_environment.py`, `bench/grpo_eval.py`, `tests/test_stage9_grpo_pipeline.py`; direct-action software exists, not a completed trained adapter or held-out real evaluation. |
| G10 | OUT_OF_SCOPE | Historical `artifacts/evidence/stage10/rs_dataset_manifest.json` and `recommender/dataset.py`; former PARTIAL, with no historical operator feedback. Retain both 28-row and corrected 21-row synthetic cohorts. |
| G11 | OUT_OF_SCOPE | Historical bounded-offline PASS in `artifacts/evidence/stage11/rs_hybrid_eval_synthetic_v2.json`; four synthetic Test rows support small-data ranking only, not incident resolution improvement. |
| G12 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | `agents/coordinator.py`, `scripts/run_g12_integrated_episode.py`; direct policy-action/status capture is locally tested. RS is optional, not a GAI + RL prerequisite. A real checkpoint/environment episode is missing. |
| G13 | REOPENED | `bench/ablation_suite.py`, `bench/episode_membership.py`; the prospective base/SFT/SFT+GRPO three-arm matrix has no independently recomputed raw incident outcomes, common eligible population, approved adversarial membership/seed, or frozen protocol. Historical five-arm output remains non-empirical. See [v0.3](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_3.md) and the preserved [partial v0.2 record](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_2.md). |
| G14 | PARTIAL | `app.py`, `dashboard.py`, `demo/launcher.py`, `tests/test_stage14_demo_safety.py`; read-only software is not a fresh peer-host safe deployment or operator sign-off. Deployment is deferred. |
| G15 | PARTIAL / NOT_CERTIFIED | `scripts/package_submission.py`, `artifacts/SUBMISSION_MANIFEST.json`, `artifacts/SUBMISSION_SUMMARY.md`; hashes establish inventory integrity, not scientific certification. Regenerate after each accepted source/evidence change. |

No new empirical PASS, overall completion percentage, or live readiness verdict
is inferred from this matrix.

## Decisions and work order

1. **D1 recorded and later scope-amended:** v2.2 Sections 1-24 and 26-27
   were adopted for non-live work; RS-required portions are superseded by
   the GAI + RL direction. Retain the v1.1 Kind instructions as a historical
   setup reference and G10/G11 as optional archived work.
2. **D2 partial, D3 pending:** the project lead selected the recommended
   A directions for measurement items 1-4 in the former five-arm proposal;
   the arm count and RS requirement are superseded. The exact failure taxonomy,
   common scorer implementation/hash, category/clock mapping, budgets and
   independent evaluation sign-off are not frozen. The single
   [v0.2 partial decision record](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_2.md)
   preserves the choices and remaining fields. Adversarial source, actual
   membership and seed, Test access and live execution remain unapproved.
3. **Safe software lane:** reconcile the GAI + RL runtime, README, package
   and gate labels; design common raw episode normalizers and fail-closed
   validators for base, SFT and SFT+GRPO. Preserve mock/source labels and
   historical V3/V5 material. A measured Stage 13 claim cannot use declared
   summary values alone.
4. **Prerequisite empirical lane (separate authorization):** fresh G3 host,
   model and operator preflight; causally valid G4; immutable-serving G6;
   real G7/G8 checkpoint and comparison; serialized direct-action G9;
   real GAI + RL G12 integration; then approved G13 Test and adversarial campaigns.
   Do not reserve 015, request P1, inject faults, train, or access quarantined
   outcomes from this adoption.
5. **Deployment lane (deferred):** G14 peer-host safe-mode/operator and
   security acceptance only after the project is finished and approved;
   G15 final certification only after raw evidence, recomputation and
   independent sign-off. Public/live deployment is not authorized here.
