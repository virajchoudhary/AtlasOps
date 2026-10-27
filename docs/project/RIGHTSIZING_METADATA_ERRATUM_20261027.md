# Right-sizing metadata preservation erratum

Date: 27 October 2026. Review scope: PR #235, original head
`aea7e8eadf34a6f833896e6458293e8a5fff03ca`.

The shared experiment writer emitted `SMALL_MODEL_CPU_RL_EXPERIMENT` and
`rightsizing-grpo-cpu-v1` in GPU `RESULT.json` and `SCORE.json` records.
Their recorded CUDA/Tesla T4 runtime and 7B model provenance establish that the
hardware scope is exploratory GPU RL. Future CUDA records use
`EXPLORATORY_GPU_RL_EXPERIMENT` and `rightsizing-grpo-gpu-7b-v1`.
The original metadata labels, bytes, hashes, unsuccessful outcomes, and scientific
classifications of every saved run remain unchanged. This erratum explains a
metadata inconsistency; it does not relabel a failed run as successful.

The [GPU report](RIGHTSIZING_GRPO_GPU_7B_V1.md) records all failed SFT attempts,
the null phase-B run, both failed selection sweeps, and the successful B2/C1/C2
exploratory sequence. CPU evidence likewise remains preserved.
The development selection had eight of ten eligible checkpoints tied at 64/64.
Nine of twelve displayed entries were perfect only when the final adapter and
warm-start entries were also counted. The earliest-tie rule selected step-0030,
not demonstrated superiority over the other tied checkpoints.

The original C2 commit label is stale. Its source-tree digest and adapter hash
are distinct evidence and must be checked as documented in the preserved
`SOURCE_COMMIT_LABEL_IS_STALE.md` sidecar.
The analyzer now compares the checkpoint hash inventory, selected-adapter
record, and confirmation score inventory instead of asserting identity.
Missing or mismatched identity cannot establish the experiment's criterion.
This checks recorded provenance, not the externally stored weight bytes.

The preparation-only v2 proposal previously specified warm-start calibration
on confirmation. It now uses development for calibration and reserves the
unopened confirmation set for the final selected-policy/warm-start comparison.
No v2 experiment ran; this correction is neither a permit nor a new result.

All exploratory right-sizing evidence remains NOT_APPLICABLE to formal G9.
G9 remains NOT_PASSED/FROZEN. No gate or frozen execution receipt changes.
