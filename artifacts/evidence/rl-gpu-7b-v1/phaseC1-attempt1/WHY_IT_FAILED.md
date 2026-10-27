# Phase C1, attempt 1 — scored nothing, 27 October 2026

Every one of the eleven candidates raised the same error inside `score_set`:

    AttributeError: 'Namespace' object has no attribute 'gradient_checkpointing'

`8c77314` gave `load_base` a `gradient_checkpointing` argument and updated the four
call sites to pass `args.gradient_checkpointing`. The CLI is unaffected: all four
subparsers define `--gradient-checkpointing`. The driving kernel builds its own
`argparse.Namespace` by hand, and that was not updated, so it lacked the attribute.

Two faults, not one. The second is the worse of them: the kernel caught each
failure, continued, and still wrote `development_bar_met: false` and logged
`PHASE C1 COMPLETE` with `dev_scores: {}`. A sweep that scored nothing reported
success. Attempt 2 stops at the first failure and asserts that a selection exists
before it claims to be complete.

No part of the confirmation set was opened; `confirmation_set_opened_here` is false.
This attempt selected nothing, so it constrains nothing in attempt 2.
