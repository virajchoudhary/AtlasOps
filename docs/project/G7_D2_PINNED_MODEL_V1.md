# D2 Immutable Model Preparation v1

**Model/tokenizer identity resolved for preparation. Weight transfer and
execution NOT AUTHORIZED.**

The required baseline remains `Qwen/Qwen2.5-7B-Instruct`. Public Hugging Face
metadata, checked 30 September 2026, resolves the immutable commit to
`a09a35458c702b33eeacc393d103063234e8bc28`. Use this same explicit 40-character
revision for model and tokenizer; do not use mutable `main`, a different
tokenizer repository, or a smaller model as an implicit replacement.

Primary sources:

- [Immutable model metadata](https://huggingface.co/api/models/Qwen/Qwen2.5-7B-Instruct/revision/a09a35458c702b33eeacc393d103063234e8bc28)
- [Immutable repository tree](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/tree/a09a35458c702b33eeacc393d103063234e8bc28)
- [Pinned model config](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/a09a35458c702b33eeacc393d103063234e8bc28/config.json)
- [Pinned Apache-2.0 license](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/a09a35458c702b33eeacc393d103063234e8bc28/LICENSE)

Metadata reports Apache-2.0 and an ungated repository. The local pinned config
identifies `qwen2`, `Qwen2ForCausalLM`, context length 32768, and no `auto_map`.
The loaders retain `trust_remote_code=False`. This verifies public metadata
and tokenizer files, not actual model-weight identity, capacity or performance.

## Small Tokenizer Staging

Only `tokenizer.json`, `tokenizer_config.json`, `vocab.json`, `merges.txt`,
`config.json`, `generation_config.json`, and `LICENSE` were staged for the
approved non-live CPU preflight: 11499871 bytes total. Every path/size/SHA-256
is preserved in
`artifacts/evidence/stage7/tokenizer_files_a09a354_v1.json`.
No `.safetensors`, `.bin`, checkpoint, or model-weight file was requested.

The bounded allowlist script `scripts/stage_sft_tokenizer.py` writes a fresh
directory only, verifies the API revision, and caps each file at 16 MiB and
the total at 32 MiB. The offline preflight rejects extra/non-allowlisted files,
bad hashes, redirects, mismatched config/revision, and model fallback.

## Remaining D2 Authority

The project lead's D3 decision authorizes preparation only. Before any later
model-weight transfer, obtain explicit authority for the exact repository,
revision, host, storage and inventory. Record every model shard/config by
relative path, size and SHA-256 after the approved transfer and bind the
inventory to the named-run execution record. Remote loaders use only locally
staged files, never silently download uncached weights. Metadata and requested
revision arguments are not independent weight attestations.
