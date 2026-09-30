# G7 Settling-Only Source Compatibility v1

**Non-live compatibility review; no empirical or execution approval.**

Main PR #128 changes only `agents.coordinator.settle_environment` in the
coordinator file to enforce a monotonic settling deadline. The frozen corpus,
manifest, Train membership, recipes, role ACLs, prompts, tool schemas and
project template remain unchanged. The earlier v1 technical audit is retained.

The pilot-only compatibility module admits this one exact pair; the frozen
candidate validator is unchanged and still rejects current-source drift:

- Original canonical-LF coordinator SHA-256:
  `84832e3549b718705f4491ce6f10a9b0ee4eaf44f5600d722ddf854f8cb6fc28`.
- Reviewed current coordinator SHA-256:
  `5d7be471592526fbf519fba571c2a1d2ccfd1d976f027736d00f2b3b39aadd0c`.
- Original construction commit:
  `e802dd3a5522c30d3919952954e86827fa96caa1`.
- Current source change: main `3c84b9031c2a9dff9ee569c2d0f6bfbe36a5e893`.

The ASTs outside `settle_environment` must be identical. Both exact file
digests are hard-coded; any other coordinator or construction-file hash still
fails closed. This is not an ignore-source flag, wildcard, mutable approval,
rehash of v1 evidence or semantic approval for arbitrary code drift.

Corpus recipes never execute settling or model tools. Their synthetic role
targets, static runtime tool policy and rendering are unchanged; deterministic
row hash replay remains required. The independent verifier must check the
compatibility pair and exact scope before merge. A later action-policy/schema,
prompt, recipe, template or different coordinator change still requires new
versioned evidence/review; no automatic approval follows.

Final tokenizer preflight and pilot plan are regenerated under new filenames
to bind the actual current validator and coordinator hashes. Prior token
reports and plan versions are preserved. D3 continues to authorize preparation
of the already approved corpus only; execution authority remains absent.
