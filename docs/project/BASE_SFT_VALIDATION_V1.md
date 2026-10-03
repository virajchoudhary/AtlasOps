# Base vs SFT Validation v1

This comparison is diagnosis-only. It does not close G4 or G8's incident
resolution criterion, unlock GRPO, or evaluate final Test.

## Matched Conditions

Use Qwen/Qwen2.5-7B-Instruct revision
`a09a35458c702b33eeacc393d103063234e8bc28` and the preserved
`sft-pilot-free-t4-20261003-v17` adapter. Its run manifest SHA-256 is
`7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440`;
adapter weight SHA-256 is
`f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff`.
Training used 68 scenario-derived synthetic Train-only examples. That origin
remains disclosed and is not genuine historical incident feedback.

Both arms use the same pinned base/tokenizer files, encoded prompts,
Transformers/PEFT runtime, NF4 double quantization, FP16 compute, native SDPA,
single GPU, and generation configuration: seed 1337, greedy decoding,
temperature 0, top-p 1, maximum 512 new tokens. Base disables the adapter;
SFT enables it. Neither arm mutates a cluster or invokes remediation tools.
The immutable ordered Validation split is the only evaluated population.

Kaggle is a substitute execution host after Colab refused GPU allocation
because of usage limits. Record the actual host, driver, package versions,
model residency, source SHA, and all file hashes. The Kaggle T4 x2 allocation
does not authorize distributed inference: both arms use only `cuda:0`.
Prepare pinned weights and the existing hash-locked isolated environment
separately; loaders remain local-only during inference. Do not reuse the
Ollama alias as proof of pinned Hugging Face model identity.

## Evidence And Metrics

Claim a new output directory exclusively. Preserve raw requests, encoded
prompts, generated token IDs, returned text, failures and interrupted rows
before scoring. Run/model/adapter identity must bind each row. Rehash model
and checkpoint inventories before and after execution. No failed attempt
is overwritten or silently replaced.

Schema conformance uses all scheduled responses as its denominator.
Diagnostic precision, recall, and F1 use the existing token-set scorer only
for schema-valid responses; failures are null, never manufactured zeros.
Report valid-response counts and paired SFT-minus-Base diagnostic differences
only where both responses are scorable. The six-scenario result is descriptive;
do not infer statistically established superiority.

`scripts/verify_base_sft_validation.py` independently parses the raw text,
checks prompt/configuration pairing and Validation membership, recomputes
token-set diagnostics, and rejects inconsistent recorded scores. Its PASS
alone is not evidence that models executed: inspect runtime records and raw
outputs independently as well.

Resolution, reward, safety, action validity, and time to resolve remain
unevaluated/null. No performance result exists until real inference and
independent verification complete. No final-Test evaluation, GRPO, cleanup,
or unrelated UI change belongs to this experiment.
