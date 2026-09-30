# Independent Pilot Preparation Review v1

**PASS for non-live preparation. Execution NOT AUTHORIZED.**

Review date: 30 September 2026. Reviewer: native curated Luna Max verifier,
behaviorally read-only. Root remained GPT-6.1 Sol. Fresh own worker runtime
metadata identified `azure-kmamc/gpt-6-luna` at `max`; no sandbox-enforced
read-only claim is made.

## Standards

PASS with no final actionable findings. AST parsing and scoped Ruff/diff
checks passed. Earlier future-authority findings were fixed rather than
treated as successful reviews.

## Spec

The verifier independently checked the current files and confirmed:

- Gate constant matches preparation plan SHA-256
  `3e768a5c8eaa1e773395df10c3a7a2ebf366574a71590a7216880e4421f8f916`.
- All six required artifact hashes match, including frozen corpus/manifest,
  preparation approval, environment lock, Dockerfile, tokenizer inventory,
  final preflight and unchanged Qwen template.
- Final preflight SHA-256 is
  `d0283fd2404540cf790c6f5f6ade26ad351d4fbc2a1e3d501c39a29c1b0f0c0d`;
  all 12 implementation hashes match current files.
- All 68 report rows match the candidate identities in order, 17 per role.
  No row mask, length, offset or fit discrepancy was found. Maximum 5738 tokens
  fits the 8192-token preparation plan with no applied truncation.
- Preserved final-implementation 2048 report is FAIL: 54 rows would truncate.
- All 72 lock package/version entries match the plan; 14 direct pins include
  the runner's imported schema-helper dependencies. Dockerfile uses an
  immutable Linux/amd64 base and hash-locked installs.
- Preparation approval cannot grant execution. The execution hash is unset.
  Future permit binds source/run/output, host, exact package versions,
  single visible GPU, and complete regular read-only model snapshot inventory.

## Execution Proof and Limitations

Root ran the actual `training.sft --preflight-only` command: preparation
admissible true, execution false; no output directory, trainer or model load.
Root performed real local tokenizer-only preflight with Transformers 4.57.6
and tokenizers 0.22.2, no Torch weights/models. Isolated tokenizer dependencies
were staged locally; no global package state was changed.

The independent verifier's CLI attempt could not import Jinja2 in its selected
interpreter. It created no output and did not install dependencies. Its final
PASS relies on independent artifact/hash/row/schema verification, not an
independent tokenizer rerun or GPU measurement.
Root's combined affected tests recorded 234 passed, 7 environment-gated skips
and one obsolete decision-label assertion failure. The assertion was updated
to the real D3 preparation approval; retain that failure as a development
record. Fresh relevant checks and PR CI must pass before merge.

No image was built and no remote GPU compatibility/memory measurement,
`pip check` in the full Linux environment, model-weight transfer, adapter reload,
inference or training occurred. The complete dependency lock was resolved from
metadata; multi-gigabyte Torch/CUDA wheel downloads remain gated.
The original frozen candidate, rejected historical fixture and prior evidence
were not overwritten. Earlier tokenizer reports remain preserved and are not
promoted into the final evidence.

Only external host/entitlement/build, weight-transfer and named execution
approvals remain. The exact next sequence is in
[the execution record](G7_PILOT_EXECUTION_RECORD_V1.md). G7 remains PARTIAL,
empirical gates unchanged, Stage 15 NOT_CERTIFIED.
