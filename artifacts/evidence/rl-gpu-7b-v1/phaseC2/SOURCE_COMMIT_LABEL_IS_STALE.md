# The commit label in this phase's SCORE.json is wrong; the source is not

Both `SCORE.json` files in this phase record:

    "commit": "c20215b493dde961c874face195141998905a44d"

That is wrong. The driving kernel sets `ATLASOPS_SOURCE_COMMIT` by hand, and my
edit to update it for this phase silently did not apply (a `sed` alternation that
matched nothing). I noticed after the run had started. Kaggle offers no way to
cancel a running kernel, and pushing a corrected version risked starting a second
confirmation, which the owner authorization forbids, so the run was left alone.

The label is stale. **The source is provably the same one phases B2 and C1 ran**,
by a hash neither I nor the environment variable can influence:

| phase | recorded commit | `tree_sha256` |
|---|---|---|
| phaseB2 | `8c773142dcf3` | `53b6d558293143b2...` |
| phaseC1 | `8c773142dcf3` | `53b6d558293143b2...` |
| phaseC2 | `c20215b493dd` (stale) | `53b6d558293143b2...` |

`tree_sha256` is computed from the exported source bytes at run time. It is
identical across the three phases, so the confirmation ran on the same source as
the run it confirms. Read the commit field in this phase as `8c773142dcf3`.

No result was recomputed to make this come out well: the discrepancy was found
before the confirmation numbers were read, and the evidence is kept as the run
wrote it.
