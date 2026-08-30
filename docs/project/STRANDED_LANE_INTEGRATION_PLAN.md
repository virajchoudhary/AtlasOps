# Stranded lane integration plan

Three research lanes carry substantial completed work that is not on `main`. This
records what each contains, what actually blocks it, and the order in which they
can be integrated. It exists because "port the recommender / port the GRPO fix"
sounds like a merge decision and is not — two of the three depend on gates that
are still open, and merging them early would produce code that cannot be run,
validated, or honestly reported.

| Lane | Branch | Content | Status |
|---|---|---|---|
| D — Recommender (G10/G11) | `research/g10-g11-rs` | 12-module runbook recommender + 48 tests | **Ported to `main`** |
| B — Split freeze (G5/G6) | `prep/g5-g6-contract` | scenario contract, split planner, unseen-candidate tooling, G6 evidence | Blocked — see below |
| C — GRPO coupling (G9) | `research/g9-grpo-audit` | paired-rollout GRPO rewrite + `training/stage9_contract.py` | Blocked on Lane B |

All three branched from an older `main` and would delete newer G4 work if merged
directly. Lane D was ported file-by-file rather than merged; the others need the
same treatment.

---

## Dependency chain

```
G4 (one verified end-to-end incident)
  └─> G5 (freeze scenario truth and splits)        <- Lane B
        └─> G6 (zero-shot baseline on the frozen split)
              └─> G7/G8 (SFT + evaluate)
                    └─> G9 (corrected online GRPO)  <- Lane C
```

Lane C's entry point is `load_remediation_training_rows(path)`, which fails
closed on any row lacking a valid `split_eligibility`. Those rows come from the
G5 frozen split. Porting Lane C before Lane B yields a training module whose
only input contract cannot be satisfied — untestable and unreportable.

---

## Lane B is blocked by a benchmark-design problem, not by code

`bench/g5/split.proposed.json` on that branch reports
`status: "PROPOSED_BLOCKED_NO_FINAL_TEST"` with two blockers:

**1. `NO_UNEXPOSED_FINAL_TEST_CANDIDATES`** — all 28 frozen scenarios are already
exposed by SFT-generation defaults, so `splits.final_test` is empty and
`coverage.final_test_by_tier` is zero in every tier. There is currently **no
uncontaminated held-out set**, which means no honest final-evaluation number can
be produced no matter how well training goes.

**2. `FAMILY_RELATIONS_CROSS_ASSIGNED_SPLITS` (9 relations)** — related scenarios
share fault signatures, source incidents, or alert semantics across the proposed
train/validation roles. Even the train/validation boundary leaks.

Neither is fixed by merging anything. Both require **authoring new scenarios that
have never been exposed to trajectory generation**, then regenerating the split
with family-aware assignment.

This is the single most important constraint on the project's ability to report
an accuracy improvement, and it is independent of Gate G4.

---

## Recommended order

1. **Close G4.** Needs a running Docker daemon and the local Kind stack. The
   observability blocker that failed runs 001–008 is repaired and verified
   against the real model (`docs/project/G4_V4_BEHAVIOUR_PROBE.md`); what remains
   is an execution step, not a design problem.

2. **Author unexposed final-test scenarios.** Roughly 6 new scenarios (one or two
   per tier) that trajectory generation has never seen. This unblocks Lane B's
   first blocker and is the only work here that can start *today*, in parallel
   with everything else — it needs no cluster and no GPU.

3. **Port Lane B file-by-file** (`bench/scenario_contract.py`,
   `bench/alert_contract.py`, `bench/unseen_candidate.py`, `bench/g6_evidence.py`,
   plus `bench/g5/`), then regenerate the split with family-aware assignment and
   confirm both blockers clear.

4. **Reproduce G6 zero-shot** on the frozen split. This is the project's first
   honest headline number.

5. **Port Lane C** once G5 rows exist. Its correction is real: a dataset row owns
   one model-visible observation plus a hidden, split-safe environment identity,
   and TRL repeats that row across the group so every completion is scored
   against the same hidden scenario, with the policy's own completion parsed into
   the scored action (`extract_policy_action_record`, `policy_completion_valid`).
   That is the genuine repair for the uncoupled gradient that `main` currently
   handles by refusing to train (`UncoupledRewardError`).

---

## Porting method that worked for Lane D

Do not merge. The branches are stale and would revert current G4 work.

```bash
git checkout <branch> -- <specific paths>
.venv/bin/python -m pytest tests/ -q
```

Then reconcile against current contracts. For Lane D this surfaced one real
integration gap: the recommender's chaos runbooks all required a
`chaos_resource_name` prerequisite that nothing could satisfy, because the
discovery wrapper did not exist when the lane was written. Adding the
`discover_chaos_experiments` runbook closed it. Expect similar reconciliation for
Lanes B and C — the branches predate the v4 tool contract.
