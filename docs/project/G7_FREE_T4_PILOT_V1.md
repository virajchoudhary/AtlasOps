# Free Colab T4 Pilot v1

> **Historical execution/profile record; gate-status wording superseded on
> 5 October 2026.** The outcome and approved profile below remain unchanged.
> The [current Stage 7 acceptance review](STAGE_7_SFT_DATA_AND_TRAINING.md)
> reconciles G7 to PASS for the bounded Train-only artifact/reload target.
> The older PARTIAL sentence describes the status at recording time.
> No incident improvement, replicate or further training is authorized.

This is a separate execution profile. The frozen `sft_pilot_v4.json` and
its execution refusal remain unchanged. On 2026-10-03 the project lead
approved the free-T4 preparation and workflow in this chat:
"yes u have approval for all".

The profile preserves Qwen2.5-7B-Instruct revision
`a09a35458c702b33eeacc393d103063234e8bc28`, the exact 68-row
`train-candidate-v1`, assistant-only loss, one epoch, seed 2026, learning
rate 0.0002, 8192-token limit without dropping or truncating rows, NF4 double
quantization, rank-16 LoRA, and the existing paged 8-bit optimizer.

Declared changes are FP16 compute instead of BF16 and per-device batch 1
with accumulation 8 instead of batch 2 with accumulation 4. Effective batch
remains 8; numerical equivalence is not claimed. Python 3.12.11 and the
complete existing 72-package hash lock run in an isolated Colab venv.
There is no fabricated OCI digest or persistent Colab storage attestation.

Only a verified free Tesla T4 is admitted. Spending is capped at zero.
Before launch, bind the exact clean source SHA, fresh run/output, approved
profile, runtime and complete read-only model inventory in an external
execution record. An operator supplies its independently reviewed raw
SHA-256 separately; that digest is an integrity pin, not a cryptographic
signature or proof of human identity. No JSON flag self-authorizes a run.

Keep all failures and partial artifacts. Export evidence and adapter to the
user's local evidence store before releasing the ephemeral Colab runtime.
The independent reload command must run in a separate network-isolated
process and revalidate model and adapter inventories without evaluating or
generating predictions. GRPO, P1 remediation, and final-Test are excluded.

At planning time, T4 compatibility alone did not prove memory fit or a
successful run. The v17 record below establishes both for that bounded run;
adapter reload establishes only the synthetic SFT artifact, not empirical
incident improvements.

## Recorded v17 outcome

The separate `free-t4-v1` profile completed as
`sft-pilot-free-t4-20261003-v17` on clean source
`af629ada4629e76c194e66a357935981f42e2c90`, using the pinned Qwen2.5-7B-Instruct
revision and the 68-row synthetic `train-candidate-v1` corpus. Its one-epoch
run completed 9 optimizer steps; the separate named execution approval capped
spend at $0. The complete provenance and raw evidence are in the canonical
[v17 result record](../../artifacts/evidence/stage7/free-t4-v17/RESULT.json).

The preserved adapter weight inventory is SHA-256
`f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff`, with run
manifest SHA-256 `7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440`.
A fresh `unshare --net` process independently reloaded it and checked 392
finite LoRA tensors without inference. The 377,494,628-byte evidence bundle
was verified locally and in private Drive at SHA-256
`59fea5f55ff5ecb4ca86d679bd5f8eaa856f1881b514020d92af9014cfb7c418`.

This closes only the bounded synthetic artifact/reload milestone. G7 remains
PARTIAL; no incident improvement is claimed, and G8 evaluation, GRPO, and
final-Test work were not run. The original v4 preparation plan and its
`execution_allowed=false` refusal remain unchanged.
