# Future SFT Pilot Execution Record

**Template only. No execution approval exists or is pinned.**

Preparation approval does not authorize weight transfer, GPU allocation or
training. The gate's `EXECUTION_APPROVAL_SHA256` remains `None`; changing a JSON
flag or supplying an arbitrary `--execution-approval` path cannot open it.
A future human-approved record must be independently reviewed and its exact
raw hash pinned by a focused PR. All fields below are requirements, not values
chosen or approved by the agent.

## Required Record

`schema_version`: `atlasops-sft-execution-approval-v1`.
Record explicit `approved_by`, date/time, authority/scope, rationale, exact
`run_id` beginning `sft-pilot-`, absolute fresh `output_dir`, clean
`source_git_sha`, reviewed `plan_sha256`, `corpus_sha256`,
`corpus_manifest_sha256`, `execution_allowed: true`.
Bind the complete package map exactly to the pilot lock and the approved
Python version. Do not truncate it to direct dependencies.

`host` must contain hostname, approved built OCI image digest (full SHA-256),
Python version, literal-true entitlement/storage/budget checks,
GPU name and total-memory bytes, CUDA runtime version, and an independent
`runtime_manifest` path plus its raw SHA-256. That runtime record must bind the
same hostname, image, package map and lock digest. On-host driver/runtime,
CUDA/BF16 and bitsandbytes compatibility, memory headroom, disk requirement,
wall-clock cap and interruption recovery must be verified before approval.
The gate checks actual platform, package versions, hostname, Python, free
storage, GPU identity and BF16 capability; those checks do not substitute for
independent host/container attestation.
For the named-host plan, the permit must also bind `host.image_attestation`
and `host.image_attestation_sha256`: a separate reviewed record with exact
image digest/hostname, `verified: true`, named `verified_by`,
`persistent_storage_verified: true`, and `storage_quota_verified: true`.
The runtime record must include measured storage free/total/used bytes and
the recorded `/opt/sft-os-packages.txt` hash, corroborated by
`os_package_inventory_sha256` in the independent attestation. Operator-supplied image strings
alone cannot satisfy these additional checks.

`model_files_manifest` must be an absolute local path with its raw hash in
`model_files_manifest_sha256`. It must identify the exact Qwen repository and
revision and an absolute stable `snapshot_dir` in an isolated Hugging Face
cache layout:
`<cache>/models--Qwen--Qwen2.5-7B-Instruct/snapshots/<40-char-revision>`.
Inventory every snapshot file using its absolute path and SHA-256; include all
model shards and tokenizer/config/license files. Use regular files, no links,
and make the snapshot read-only to the training account. Transfer and bytes
must have prior explicit approval. The runtime loader uses that cache with
`local_files_only=True` and `trust_remote_code=False`; uncached weights cannot
be downloaded by the training command.

The gate's path-based inventory hash checks are not immutable file handles.
A hostile same-user file replacement between verification and loader reads
remains possible. Stable independently managed read-only model storage is a
host-side prerequisite, not an assumption about arbitrary writable paths.

## Exact Future Action

1. Obtain an explicitly approved NVIDIA host/entitlement, storage/cost ceiling,
   secure access and image-build authority. Build the frozen recipe and record
   its resulting OCI digest; do not infer a built image from the base digest.
2. Obtain separate weight-transfer authority, stage the pinned repository into
   stable storage and verify its complete file inventory.
3. Run non-training on-host compatibility checks; freeze runtime provenance,
   actual memory/storage headroom and the final run record.
4. Independently review and pin that approved record through a narrow PR.
   The root retains final acceptance; no self-issued execution permit.
5. Only after separate named-run authorization, run the prepared command once
   in a fresh external directory. Preserve every failure/partial checkpoint.
   Do not resume/retry in place or start G8/GRPO/final-Test work.

## Reload Acceptance

After a separately authorized successful run, use a separate clean process,
network disabled, with the same pinned base/tokenizer files and PEFT adapter.
The existing `bench.sft_eval._load_checkpoint_manifest` validates completed
inventory/source/data lineage; `LocalSFTInference._load` loads locally without
generating predictions. A reload-only proof may call that loader directly;
do not run the G8 empirical CLI, which performs Validation inference.
Record checkpoint inventory hash before/after loading, resolved revisions,
environment/host/source hash, loader outcome and failures. A fake-loader unit
test, a `completed` label, or successful hashing is not real reload evidence.
