# Base vs SFT Validation Result v1

**Completed real inference; no diagnostic improvement observed.**

The first matched Validation campaign ran on 3 October 2026 from clean
source `3808849125db0ddfb6bf64fe853dffe3fce608c1`. It generated one Base and
one SFT response for each of the six frozen Validation scenarios. All
12 responses returned and passed the diagnostic JSON schema.

| Metric | Base | SFT | SFT minus Base |
|---|---:|---:|---:|
| Scheduled / returned / scored responses | 6 / 6 / 6 | 6 / 6 / 6 | 0 |
| Diagnostic JSON conformance | 1.00000 | 1.00000 | 0.00000 |
| Mean token-set diagnostic F1 | 0.16875 | 0.15935 | -0.00940 |
| Inference / schema failures | 0 / 0 | 0 / 0 | 0 |

The F1 mean uses the existing scorer's four-decimal episode scores. The
independent checker displays arm means at four decimals and the paired
difference at four decimals; it also verifies the six-decimal native summary.
The small lexical diagnostic difference is descriptive only. It does not
prove general inferiority or superiority. Alert-only inputs provide limited
evidence and are not a live incident/tool workflow.

## Conditions And Provenance

- Model/tokenizer: `Qwen/Qwen2.5-7B-Instruct`, immutable revision
  `a09a35458c702b33eeacc393d103063234e8bc28`.
- SFT: `sft-pilot-free-t4-20261003-v17`; manifest SHA-256
  `7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440`;
  weight SHA-256
  `f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff`.
- Both arms used the same pinned base/tokenizer, encoded prompt, generation
  configuration and quantized model. Base disabled the PEFT adapter; SFT
  enabled it. Greedy decoding, seed 1337, maximum 512 new tokens, NF4 double
  quantization, FP16 compute, native SDPA, one visible `cuda:0` Tesla T4.
- Kaggle private notebook; Python 3.12.11 and the exact 72-package locked
  environment. Runtime CUDA 12.6, driver 580.178.04. Kaggle provisioned T4 x2,
  but only one device was visible to the experiment process. No paid compute.
- Started 12:49:57 UTC; completed 12:52:48 UTC. Run elapsed approximately
  170.652 seconds includes model inventory/loading and postflight checks;
  it is not incident TTR or a performance benchmark.
- All 12 base files and 27 checkpoint files were verified before and after
  inference. Source was clean; raw text and generated token IDs were persisted
  before any scoring.
- Training used 68 scenario-derived synthetic Train-only examples. Original
  corpus-origin declarations are retained. Opt-in revalidation of the original
  Colab corpus path was not performed, and its normalized origin is UNVERIFIED.
- Loader/generation warnings are preserved. They do not authorize changing
  decoding conditions or silently rerunning the campaign.

## Preserved Evidence

The [run manifest](../../artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/run_manifest.json),
[raw inference ledger](../../artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/raw_episodes.jsonl),
[scored episodes](../../artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/episodes.jsonl),
and [summary](../../artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/summary.json)
are byte-preserved. Run manifest hash:
`878b01e079d294ad35a6ab73365c01e1a3d7cf138a2afaae72b73ecbb59d49b4`.
Scored ledger hash:
`99a45c1cdb08c8c23f4b6d9c7c51153e00c6528bf976049cae6291bd16ad39c2`.

The [Kaggle recomputation](../../artifacts/evidence/stage8/base-sft-validation-v1/atlasops-validation-preparation/independent-recompute-v1.json)
and [local recomputation](../../artifacts/evidence/stage8/base-sft-validation-v1/local-independent-recompute-v1.json)
both report PASS and the same raw hashes. The original 103,804-byte ZIP is
preserved locally at SHA-256
`44ce75f87901c86da59739b9070c99d3198fbb8360d73f25b80264844d52ea8b`.
The browser download controls timed out; bounded notebook-output export
recovered the same ZIP with its hash verified. No inference rerun was needed.

## Limits

Resolution, safety, reward, action validity and time to resolve remain
unevaluated/null. G4 remains NOT_PASSED; this result does not close the G8
incident-resolution criterion or authorize GRPO. Final Test was not evaluated.
Earlier review inadvertently read Test split documentation; that access is
disclosed, with no Test catalog records or outcomes inspected. No training,
remediation, broad cleanup or UI redesign occurred in this campaign.
