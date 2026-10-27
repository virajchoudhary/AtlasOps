# Phase C1, attempt 2 — two candidates scored, then out of memory, 27 October 2026

The `Namespace` fault from attempt 1 was fixed and scoring worked: `step-0010` and
`step-0020` completed and their rows are kept here. The third candidate died in
`prepare_model_for_kbit_training`:

    torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.03 GiB.
    GPU 0 has a total capacity of 14.56 GiB of which 314.81 MiB is free.

The loop loaded a fresh 7B per candidate in one process. A 7B does not leave the GPU
when its Python reference goes away: `torch.cuda.empty_cache()` after each candidate
is not enough, which the phase B diagnosis had already established and which I should
have carried over to this sweep.

Attempt 3 scores each candidate in its own process, driving the repository's `score`
subcommand rather than a hand-built `Namespace`. Process exit returns the memory, and
argparse supplies every argument the handler reads, so both of the faults that stopped
attempts 1 and 2 are removed by the same change.

These two scores are **not** carried into attempt 3's selection. A selection assembled
from runs under different memory conditions is not the registered procedure; attempt 3
rescores every candidate from scratch. No part of the confirmation set was opened.
