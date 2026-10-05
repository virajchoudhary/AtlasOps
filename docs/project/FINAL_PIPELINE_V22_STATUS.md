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

- Current finalization basis, freshly fetched on 5 October 2026:
  `7ac0cfb5c5fbd77d82500a72fd06b802c2b826b7`. The table below now reflects
  preserved final evidence. Earlier source SHAs remain provenance for their
  dated adoption and experiment records, not a claim of current live health.

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

The later [final controlled-G9 result](CONTROLLED_G9_FINAL_NEGATIVE_V1.md)
supersedes the historical G9 REOPENED status: G9 is now NOT_PASSED and frozen
as experimentally unsuccessful. Exactly eight aligned inference-only samples
produced no admissible canonical action, so the final training pilot was not
launched. Reporting excludes an absent SFT+GRPO model and uses only preserved
actual Base/v17 results and negatives; no new held-out or live evaluation.

The current status column uses the 5 October final evidence state. G7/G8 implementation
rows were last reconciled to reviewed-source basis
`1b13695f0e405883d7d66da20fe52d23435d8a7d`; the G7 row now also records the
later v17 execution evidence, whose training source SHA is given in its result
record. G8 also records the independently verified matched Validation campaign
from clean source `3808849125db0ddfb6bf64fe853dffe3fce608c1`. The cited paths remain review leads, not substitutes for independent
acceptance evidence. A historical PASS is scoped to its recorded conditions.
The G7 D3 preparation decision is separate from the G13 measurement D3 below.

| Gate | Status | Source/evidence boundary and next unmet work |
|---|---|---|
| G0 | PASS | `LICENSE`, upstream ancestry, `docs/project/UPSTREAM_BASELINE.md`; freeze a new clean execution SHA only when an actual run is approved. |
| G1 | PASS | `requirements/dev-win-py312.lock`, `.github/workflows/ci.yml`, `tests/test_app_endpoints.py`; refresh security and environment checks at the eventual execution SHA. |
| G2 | PASS | `agents/verifier.py`, `agents/tool_policy.py`, `agents/approval.py`, `tests/test_approval_fail_closed.py`; local contracts do not prove live backend reachability. |
| G3 | PASS (historical, caveated) | `artifacts/evidence/stage3/acceptance_report.json` records a local Kind milestone, but `kubectl_describe`/`kubectl_logs` wrapper failures and absent Jaeger traces remain; fresh target acceptance is required. |
| G4 | NOT_PASSED | Frozen: 015 terminal INCONCLUSIVE/unscored; 016 pre-fault abort/non-result; 017 completed negative, with methodological caveats. No 018. [Current chronology and external archive anchors](CONTROLLED_G9_ADMISSION_V1.md); the older 009-014 recovery index remains historical and unchanged. |
| G5 | PASS (governance) | `config/splits.py`, `config/scenario_catalog.py`, `tests/test_stage5_scenario_splits_and_truth.py`; Train 16 / Val 6 / Test 6 are frozen. Audit every downstream consumer; Leaderboard overlaps Train/Val. |
| G6 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | Archived Stage 6 metrics are mock. The separate matched Validation campaign records real pinned-base diagnostic inference and raw provenance; it does not establish incident resolution or close G6. |
| G7 | PASS (bounded artifact/reload) | v17, 68 synthetic Train rows, 1 epoch / 9 steps, named $0-cap approval, verified inventory and independent offline reload meet the bounded target. Preapproved free-T4 profile replaces OCI with a hash-locked isolated venv. Tolerance applies only before a future replicate; see [acceptance review](STAGE_7_SFT_DATA_AND_TRAINING.md). No incident improvement inferred. |
| G8 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | The complete [real Validation comparison](BASE_SFT_VALIDATION_RESULT_V1.md) records Base F1 0.16875, SFT 0.15935, delta -0.00940, schema 6/6 each: no diagnostic improvement observed. Resolution remains null; D12 Option A retains the original live criterion. Remaining incident evaluation is deferred. Historical Stage 8 outputs remain mock. |
| G9 | NOT_PASSED / FROZEN | Final controlled negative: the replacement pilot had zero reward-driven advantages; the final aligned diagnostic had 0/8 canonical admissible actions. No acceptable SFT+GRPO checkpoint and no further training retries. Historical direct-action software remains non-empirical. |
| G10 | OUT_OF_SCOPE | Historical `artifacts/evidence/stage10/rs_dataset_manifest.json` and `recommender/dataset.py`; former PARTIAL, with no historical operator feedback. Retain both 28-row and corrected 21-row synthetic cohorts. |
| G11 | OUT_OF_SCOPE | Historical bounded-offline PASS in `artifacts/evidence/stage11/rs_hybrid_eval_synthetic_v2.json`; four synthetic Test rows support small-data ranking only, not incident resolution improvement. |
| G12 | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | Direct-action integration and capture software complete, RS optional. Accepted checkpoint and live integrated episode deferred; final G9 produced no accepted policy. |
| G13 | REOPENED | Unsupported historical PASS withdrawn. Software/review deliverables complete; empirical three-arm matrix deferred without a valid third arm and prospectively frozen measurement/adversarial/access protocol. Historical profiles remain non-empirical. |
| G14 | PARTIAL | Local read-only evidence demo complete. Fresh peer-host, operator and deployment-security acceptance remains absent; no public/live deployment claim. |
| G15 | PARTIAL / NOT_CERTIFIED | Current report, slides, reviewer guide, evidence index and hash-verified package complete. Scientific certification and actual external submission remain unestablished. |

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
   This G13 measurement D3 is distinct from the G7 D3 preparation approval;
   neither changes the other's scope.
3. **Safe software lane:** reconcile the GAI + RL runtime, README, package
   and gate labels; design common raw episode normalizers and fail-closed
   validators for base, SFT and SFT+GRPO. Preserve mock/source labels and
   historical V3/V5 material. A measured Stage 13 claim cannot use declared
   summary values alone.
4. **Deferred empirical lane (separate future authorization):** fresh G3 host,
   model and operator preflight; causally valid G4; immutable-serving G6;
   the G7 synthetic checkpoint and real paired Validation diagnostic comparison
   now exist; G8 incident-resolution evidence remains outstanding; an accepted direct-action policy;
   real GAI + RL G12 integration; then approved G13 Test and adversarial campaigns.
   G4/G9 are frozen, no attempt 018 exists, and no retry is authorized.
   The [deferred handoff](DEFERRED_RESEARCH_HANDOFF.md) replaces immediate
   experiment-next-step language. Do not request P1, inject faults, train,
   or access quarantined outcomes from this finalization.
5. **Deployment lane (deferred):** G14 peer-host safe-mode/operator and
   security acceptance only after the project is finished and approved;
   G15 final certification only after raw evidence, recomputation and
   independent sign-off. Public/live deployment is not authorized here.
