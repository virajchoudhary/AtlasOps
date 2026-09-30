# G7 SFT Pilot Acceptance v1

**Status: future acceptance contract; not an approval or launch instruction.**
Qwen2.5-7B-Instruct QLoRA remains the planned baseline. This checklist
defines evidence for one bounded pilot; it does not authorize remote compute,
spending, model-weight access or download, training, evaluation, or final-Test
access. Completing software checks or this document does not promote a gate.

## Scope and Gate Boundary

- Use only a new, versioned, Train-only candidate whose exact bytes and
  provenance have D3 approval. The historical schema/rendering fixture remains
  rejected for model training; a passing technical admission check is not D3
  approval.
- Keep Validation, leaderboard, adversarial and final-Test outcomes out of
  corpus construction, training, tokenizer proof and tuning. The SFT trainer
  receives only the approved Train dataset; G8 inference and GRPO are separate
  activities.
- A successful pilot can establish a reproducible G7 adapter, not incident
  improvement or closure of G4, G6, G8, G9, G13 or Stage 15. Preserve their
  current statuses and evidence. The required eventual comparison is untouched
  base, SFT, and SFT+GRPO; Recommender Systems remain optional.
- G7 remains `PARTIAL` until a future authorized run and the independent
  evidence below pass.

## Approval and Next Step

Record each decision against this checklist version and exact evidence. No
decision implies another:

- **D1, compute and cost:** name the remote Linux/NVIDIA host and entitlement;
  approve the GPU, storage and retention ceiling, wall-clock/resource limits,
  security boundary, transfer method and cost cap (including an explicitly
  verified zero-cost entitlement if applicable).
- **D2, model and tokenizer:** approve the repository IDs, licenses and full
  immutable 40-character commit revisions for both base model and tokenizer.
  Keep `trust_remote_code=False`. The `a09a35458c702b33eeacc393d103063234e8bc28`
  value recorded in the training constraints is tokenizer provenance for
  tokenizer-only contract work; it is not D2 approval, base-model pinning or
  model-weight attestation. Model-weight transfer needs its own explicit
  authorization.
- **D3, data and G7 acceptance:** approve only the named corpus version and
  exact canonical-LF and raw corpus SHA-256 values, adjacent manifest file
  SHA-256, schema/version, construction-config and source SHA, and the
  project-owned template hash. Bind the decision to the runtime prompt/tool
  schema hashes, Train-only and safety/role review, exact effective pilot
  configuration, environment image, retention policy and independent reload
  evidence. A later candidate version or changed hash requires a new D3 review.

After D3, the next step is **non-live host, immutable revision and container
image selection plus a launch-gate review**. This is planning and compatibility
review only; it is not weight download or training. Its outputs support D1/D2
decisions and the exact run authorization. Do not transfer base weights or
train until D1, D2, D3 and separately scoped host/spend, weight-transfer and
named-run launch authorizations are recorded. G4 remains `NOT_PASSED` and G6
empirical evidence remains missing; assess their prerequisites separately for
downstream evaluation and comparison.

**Current runner prerequisite:** at the code state inspected for this
checklist, [`refuse_unapproved_candidate`](../../training/sft_candidate.py)
validates the review candidate and then always refuses it as D3-pending; the
runner has no path to consume an approved D3 record. Before training can be
eligible, a separately reviewed implementation must verify approval bound to
the exact candidate version, corpus, manifest and template hashes, and fail
closed on missing, stale or mismatched approval. Do not add a general bypass.
Recheck this contract at launch; this document does not enable training.

## Pre-Run Acceptance

1. **Source and corpus identity.** Use a committed, clean source revision; record
   its full Git SHA and verify the candidate's source/config, corpus,
   manifest, template, role-prompt and runtime-schema hashes against the
   version-specific D3 record. Snapshot and hash the exact external corpus
   bytes that will be passed to `training.sft`; do not reconstruct, edit,
   normalize or substitute it after approval. Verify every row belongs to the
   frozen Train split, every tool call is permitted by the actual runtime role
   ACL, call/observation pairing is exact, and the approval/outcome semantics
   remain fail-closed. Any mismatch stops the run and requires a new reviewed
   candidate; do not weaken admission.
2. **Tokenizer-only mask and truncation proof.** After D2 and tokenizer-file
   staging are authorized, run a network-disabled, tokenizer-only proof on
   every approved candidate row with the exact pinned tokenizer, project
   template, role prompts/schemas and approved maximum length. Save a
   per-row record of input length, supervised-token count and truncation
   disposition. Require aligned token/mask lengths, non-empty assistant
   targets, context-only system/user/tool definitions and tool observations,
   and target coverage of assistant calls and conclusions. Reject rows where
   truncation removes required approval context, splits a call from its
   observation, removes the final conclusion, or leaves no supervised tokens.
   Do not alter rows to pass without issuing a new candidate version and D3
   review. The existing [`test_sft_mask_proof.py`](../../tests/test_sft_mask_proof.py)
   covers a synthetic example; it does not prove all approved rows or
   truncation behavior.
3. **Remote runtime freeze.** On the approved NVIDIA host, record GPU model and
   memory, driver, CUDA runtime and PyTorch CUDA build, Python/OS, BF16
   capability, and the tested `bitsandbytes` version and 4-bit compatibility.
   Freeze the OCI/container image by digest and the complete resolved
   transitive package set with lock/SBOM and package or wheel hashes. The
   current [`train-constraints.txt`](../../requirements/train-constraints.txt)
   is a subset validated for Windows/CPython 3.12 contract tests; it omits an
   explicit `bitsandbytes` pin and is not a complete remote GPU lock. Resolve
   and review that gap before launch. Do not infer NVIDIA or BF16 readiness
   from local tests or package declarations.
4. **Model-file inventory.** Only after the separate D2 transfer authorization,
   record an inventory of every downloaded base/tokenizer file by relative
   path, size and SHA-256, plus the exact source revision and transfer
   verification. The runner's resolved-revision fields and the tokenizer's
   `PIN_ENFORCED_BY_LOADER_ARGUMENT/NOT_INDEPENDENTLY_RETURNED` basis, when
   applicable, are not file hashes or independent weight attestation.

## Bounded Pilot

Freeze the **effective** settings in the D3 record and run manifest before
loading base weights. The implementation proposal is the current
[`training.sft`](../../training/sft.py) default: one epoch, learning rate
`2e-4`, batch size `2`, gradient accumulation `4`, maximum sequence length
`2048`, and the recorded training seed; QLoRA uses 4-bit NF4 with double
quantization and BF16 compute, LoRA rank `16` / alpha `32` / dropout `0.05`,
the current seven projection/MLP target modules, paged 8-bit AdamW and
assistant-only loss. These are proposed values, not approved parameters. The
older three-epoch configuration is not authorization; defaults are not
authorization either.

Run only the named, bounded SFT attempt on the approved remote host, using a
new external output path. Confirm the effective trainer has the approved
Train-only dataset and no evaluation dataset or Validation/Test inputs; the
current entrypoint builds `SFTTrainer` with `train_dataset` only. Do not start
G8 inference, GRPO, Chaos or final-Test evaluation as part of this run, and do
not run the planned 7B workload on the personal laptop.

## Completion Evidence and Recovery

Accept G7 only when an independent reviewer can verify all of the following:

- The final `sft_run_manifest.json` is `completed`, records a clean code SHA,
  exact base/tokenizer IDs and resolved revisions, Train split and corpus
  provenance, template hash, effective settings, seed, runtime and trainer
  state/history.
- The complete adapter output inventory lists every relative path, byte size
  and SHA-256, including adapter config/weights, tokenizer files, trainer
  state and any intermediate checkpoints; the inventory tree hash matches the
  manifest. Preserve stdout/stderr, structured logs, failures, start/end
  times, resource observations and hashes in the approved evidence store.
  Supplement the runner's selected package/runtime metadata with the full
  image digest and dependency lock/SBOM.
- A separate clean process on the approved host performs a real local-only
  base/tokenizer plus PEFT adapter load using the pinned revisions, with
  network access disabled and file hashes rechecked. In current G8 code,
  `_load_checkpoint_manifest()` verifies manifest and checkpoint bytes while
  `LocalSFTInference._load()` opens the local-only base, tokenizer and adapter
  and revalidates the checkpoint. The G8 `--empirical` CLI is not a
  reload-only validator: it runs Validation inference and requires separate
  G8 authorization. Existing loader-contract tests use fake model loaders and
  are not real adapter-reload evidence. If a separately authorized fresh
  process cannot verify a real load, G7 acceptance remains pending.
- The reproducibility bundle is sufficient to reconstruct inputs without
  mutable aliases: clean source SHA, approved corpus/sidecars/template hashes,
  immutable model/tokenizer revisions and authorized file inventory, exact
  container and complete dependency digests, GPU/driver/CUDA details,
  effective configuration and seed, output inventory, and logs. State the
  expected numerical repeatability tolerance before any separately authorized
  replicate; do not claim bitwise reproducibility unless measured.

On any exception, interruption, partial checkpoint or missing manifest, retain
the entire claimed output path, run manifest, logs and partial files unchanged.
The current runner has no resume contract: do not retry in place, repair a
failed output into a completed result or overwrite evidence. A later attempt
requires a distinct run ID and new output path, an explicit recovery decision
and the approvals applicable to that run. Software validation, a completed
adapter or a reload alone does not establish G7 empirical improvement or
promote any downstream gate.

See the [remote training readiness assessment](G7_G9_REMOTE_TRAINING_READINESS.md),
[staged execution runbook](G7_G13_REMOTE_EXECUTION_RUNBOOK.md), and
[decision register](G7_G13_DECISION_REGISTER.md) for the companion gates.
