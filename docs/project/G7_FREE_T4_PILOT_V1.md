# Free Colab T4 Pilot v1

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

Measured T4 compatibility does not prove model memory fit or successful
training. A completed adapter and successful reload establish only the
bounded synthetic SFT artifact, not empirical incident improvements.
