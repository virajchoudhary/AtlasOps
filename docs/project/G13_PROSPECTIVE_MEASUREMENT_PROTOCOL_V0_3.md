# G13 prospective measurement protocol v0.3: GAI + RL scope amendment

**Status: PROJECT-LEAD SCOPE DIRECTION / PARTIAL / NOT FROZEN /
NON-EXECUTABLE.** This amends only the comparison family in the
[v0.2 partial decision record](G13_PROSPECTIVE_MEASUREMENT_PROTOCOL_V0_2.md)
after the later [GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md).
Neither record authorizes final-Test access, a live campaign, training,
paid compute, or deployment. Preserve v0.1/v0.2 and the historical five-arm
predetermined artifact without changing their bytes or scientific labels.

## Prospective comparison

| Arm | Required model path | Current evidence limitation |
|---|---|---|
| V1: Zero-Shot Baseline | Pinned base GAI model, no SFT or RL adapter | G6 real inference and immutable served-model identity remain unverified. Diagnosis-only outputs do not supply incident recovery. |
| V2: SFT Model | Same base plus independently loadable, hashed Train-only QLoRA SFT adapter | G7 has no verified completed adapter; G8 real checkpoint evaluation and incident outcomes are absent. |
| V4: SFT + GRPO | The V2 adapter as parent of a completed direct-action online GRPO adapter | G9 has local control-flow tests but no completed trained checkpoint, safe real rollouts, or held-out incident outcomes. |

V3 and V5 in the old five-arm proposal include RS. They remain historical
optional research, not missing required arms. The V4 label in new manifests
means **SFT + GRPO**; it does not reclassify old `"Online GRPO RL"` artifacts.
All three arms require matched eligible scenarios, order, permissions,
inference/tool/time budgets, objective verifier and independent source/model
provenance. Record absent results and invalid attempts rather than filling
them with mock outputs or declared summaries.

## Measurement rules still to freeze

The v0.2 selections for eligible-attempt recovery (A1), a common
verifier-grounded reward direction (A2), and all-eligible format/diagnosis
populations with censored unresolved TTR (A3) remain directions. A4's
matched-budget, paired Test and descriptive single-campaign principles apply
to the three arms, but its old five-arm count and RS requirement are
superseded. The later scope decision does not settle the open scientific
details:

- Predefine the exact post-start infrastructure-invalid taxonomy, evidence
  and independent adjudication, numerator/denominator fields, and reattempt
  rule. A genuine model failure stays in the denominator as a negative.
- Freeze the common scorer's raw inputs, per-step or per-episode aggregation,
  missing-field behavior, version and source/configuration digest. An
  unscorable field stays null, never an invented zero.
- Freeze diagnosis categories, no-output handling, action/Comms schemas,
  alert-delivery and conclusive-verifier clocks, precision, censoring fields,
  and independent-run interval method.
- Pin exact base and adapter weights, tokenizer, served-model identity,
  evaluator code, dataset/split membership, permissions, budgets, seed
  schedule and environment. A model alias alone is not immutable identity.
- Obtain an actual independently reviewed adversarial source, ordered
  membership, seed and protocol digest. The frozen Leaderboard overlaps
  Train/Val and is diagnostic only. Final-Test access and invalid-run
  reattempt authorization remain separate project-lead decisions.

Before a claimable comparison, all three arms need comparable controlled
incident episodes that retain alert, action, approval, tool, verifier,
failure, timing and source fields. G6/G8 diagnosis-only rows and current
G12 captures cannot be transformed into recovery, reward or TTR outcomes by
schema conversion. G9 failed/unscorable terminals must be preserved and
adjudicated under the frozen eligibility rules rather than discarded as
success-only membership. Independently recompute each metric component from
authenticated raw episodes and compare it with every declared summary.
The current G13 runner does not verify cross-arm base-model identity or
V4-to-V2 adapter lineage. Its accepted matrix is nonclaimable even if
individual artifact declarations match; freeze a common lineage schema and
reject mismatches before any empirical acceptance.

## Current software acceptance boundary

A three-arm schema validator may reject missing, extra, duplicate or
historical RS-bearing variants, bad split membership and raw hashes,
non-finite fields, mock markers, and inconsistent G9 lifecycle or model
provenance. Synthetic fixtures prove only fail-closed software behavior.
Today's aggregator still uses unverified artifact summary metrics, and
the required common integrated evaluator, cross-arm lineage check, and
scientific freeze are absent.
Its output remains `NOT_CERTIFIED` and non-empirical; G13 remains `REOPENED`.
