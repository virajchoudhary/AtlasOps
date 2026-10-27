# Right-sizing GRPO v2 — a pre-registration, pending authorization

**Status: PROPOSED, NOT RUN.** This protocol requires new training. The owner's
exploratory authorization of 27 October 2026 (PR #235) covers the v1 finish and
explicitly does **not** grant new training, repeated confirmation or expanded
spend. Nothing here executes until Viraj authorizes it. This is not a G9 protocol
and would not produce G9 evidence; G9 remains NOT_PASSED/FROZEN.

Everything below is fixed **before** any v2 run. Numbers from v1 are recomputed
from committed evidence by `analyze_gpu_v1`.

## Why v1 was weak

v1 met its criterion -- 125/128 against the warm start's 109/128, exact McNemar
p = 0.00086 -- and the result is still not worth much. Three reasons, each now
measured rather than asserted:

1. **The task was too easy.** The warm start already solved 109/128, leaving 19
   states of headroom.
2. **The run saturated.** Only 13 of 100 steps carried a gradient, the last at
   step 63. A bounded reward gives a solved group zero spread, zero advantage and
   zero update. 87 steps were a no-op.
3. **The selection was not evidence-driven.** `analyze_gpu_v1` now reports
   `dev_separation`: 10 candidates, 3 distinct scores, **8 tied at the 64/64
   ceiling**, `separates: false`. The tie-break chose `step-0030`; the scores
   chose nothing.

## Where the difficulty actually is

Measured across the 256 states the v1 warm start was scored on -- the dev,
confirmation and held-out rows, all committed:

| band | warm-start accuracy |
|---|---|
| inexact division, capacity 4 | **28/57 = 0.49** |
| inexact division, capacity 3 | 31/37 = 0.84 |
| inexact division, capacity 5 | 52/52 = 1.00 |
| exact division, any capacity | 0.82 – 1.00 |

Capacities 1, 2, 5 and 10 divide in the head; an exact division needs no rounding
at all. Difficulty is inexact division by a capacity that is not one of those.
`env.is_hard` is exactly that predicate.

## The v2 task

`env.all_states_v2` generates the hard band only, over capacities (3, 4, 6, 7, 8,
9) and quotients up to the tool policy's 20-replica bound -- a space v1 never
generated. `make_v2_sets` draws 512 Train, 128 development and 256 confirmation
states, stratified by capacity, seeded at 31415, and **excludes every state in the
v1 space**, so no v2 number can be inflated by anything the policy saw in v1.

Capacity 4 is the measured 0.49 band. Capacity 3 is the measured 0.84 band.
Capacities 6–9 are new: their rate is **predicted, not measured**, and the v2 run
is the test of that prediction. Each stratum is drawn and reported separately so a
per-stratum rate can be read off the result.

## Fixed in advance

**Task calibration.** The warm start must score between **0.40 and 0.65** on the
confirmation set. Outside that window the task is mis-sized -- too hard to learn or
too easy to be informative -- and the run is reported as a mis-sized task, with no
success claim either way. This check is made on the warm start alone, before any
GRPO step.

**Success criterion**, all three required:
1. the trained policy gains at least **15 percentage points** over its own warm
   start on the 256-state confirmation set;
2. exact McNemar **p < 0.01**, paired on the same states;
3. a **positive gain in at least 4 of the 6 capacity strata**, so a win confined to
   one divisor does not pass.

**Selection is void without separation.** A checkpoint may be selected only if
`separation(dev)["separates"]` is true -- exactly one candidate strictly ahead. If
the development scores tie at the top, the run reports **no selection** and no
confirmation is opened. v1 would have failed this check.

**Stop rule.** `--stop-on-saturation 10`: training ends once `reward_std` has been
zero for ten consecutive steps. The run records where it stopped, so a reader can
tell a run that finished from one that ran out of signal.

**Evaluation is fresh-reload only.** Every reported number comes from the `score`
subcommand in a separate process, loading the adapter from disk. No in-process
score appears in the v2 report. v1 reported in-process held-out numbers; v2 does
not.

**Replication.** Primary seed 2026; replication seeds 2027 and 2028 reported
beside it. The criterion is read off the primary seed; the replications are
reported whatever they show, including disagreement.

**Stopping for failure.** If the criterion is missed, that is the result. It is
written up with the same prominence as a pass, as the v1 null run was.

## What this still would not establish

Even passed in full, v2 remains one synthetic task on one model family, with no
independent review and no connection to incident response. It would not be G9
evidence, would not change any gate, and would not support a claim about live
performance.

## To execute

Needs Viraj's authorization for new training, plus GPU budget. The offline work --
state space, set construction, stop rule, separation diagnostic and their tests --
is committed and testable without a GPU.
