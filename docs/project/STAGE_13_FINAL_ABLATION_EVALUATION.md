# Stage 13: Final Ablation and Stress Evaluation (Gate G13)

**Status: REOPENED**

The ablation runner is now artifact driven. The required empirical artifact matrix does not
exist, so no final model comparison is currently supported.

## Required Matrix

Every run must cover each required variant:

1. Zero-Shot Baseline
2. SFT Model
3. SFT + Recommender
4. Online GRPO RL
5. Full Pipeline (GAI + RS + RL)

Each variant must provide empirical artifacts for Validation, Test, Leaderboard, and
Adversarial partitions. Every artifact must:

- identify the matching partition;
- be marked empirical and eligible for claims;
- contain measured resolution rate, contract reward, and format compliance; average
  TTR may be null only when no incident resolved, and its aggregation then has
  `n=0`, `mean=null`, and no interval;
- retain signed verifier-grounded reward values, including the G9 false-resolution
  penalty down to `-0.25`;
- supply raw prediction or trajectory evidence whose bytes match its SHA-256;
- identify its variant and partition, a distinct run ID, and a clean full source SHA.

Distinct repeated runs are aggregated with sample count, mean, and a two-sided
Student-t 95% confidence-interval half-width when at least two runs exist. The
small-sample critical values are tabulated to three decimals; larger samples
use a t-quantile expansion. This interval assumes independent runs. The input
manifest and every source artifact are preserved by path and SHA-256.

## Historical Predetermined Output

`artifacts/evidence/stage13/ablation_benchmark_results.json` contains the former constant
profiles, including the reported 100% resolution, 18-second TTR, and 0.918 reward. It is
preserved as historical output and is not an empirical finding. The current runner will
reject it as an input to a claim-eligible aggregation.

## Current Verification

Local tests prove that incomplete matrices, mock/non-empirical inputs, partition mismatches,
missing metrics, and absent raw/source provenance fail closed. Dry-run mode emits only a
non-empirical execution plan.

G13 can advance only after genuine prerequisite model/checkpoint and integrated-environment
artifacts exist.

**Gate G13 Status: REOPENED**
