# Named Remote SFT Host Plan v1

**Proposed resource/access plan only. No host, spend, weights or execution approved.**

Source review starts at main `cda3fa17b3cb59b8cc605b4f3206714e81566907`,
including #130/#131/#132. The original dirty checkout and worktrees remain
untouched. No remote entitlement, university scheduler allocation, cloud
instance or GPU availability was verified. No credentials were inspected.

## Frozen Pilot Identity

- Base and tokenizer: `Qwen/Qwen2.5-7B-Instruct`, immutable commit
  `a09a35458c702b33eeacc393d103063234e8bc28`.
- Preparation approval: exact `train-candidate-v1`, 68 Train-only rows,
  corpus SHA-256 `19606e4fec300f641c7c8b8a989497367a444a870d3491f225004c01df3ee5fd`.
- Plan: `config/sft_pilot_v4.json`, SHA-256
  `914f7a5af5c22a355b235018d997302688598d0bdbdeda8eb8fcba3552f9e83e`.
- Lock: 72 packages, Linux x86_64/Python 3.12.11, SHA-256
  `b649bfa91f1232b9b0fbf247927516a281c8769a6556330d561c7a9bd6992d9b`.
- Real offline preflight: all 68 rows pass at 8192; max 5738 tokens.
  The 2048 negative and old versions remain preserved.
- `--preflight-only` passes; execution digest is unset. D3 is preparation-only.

## University First

Preferred target: one university NVIDIA A100 80 GB, one visible GPU, Linux
x86_64, secure SSH or approved scheduler/container execution, persistent
private storage. Do not claim a university entitlement from a recommended GPU.
The lead must identify an actual hostname or scheduler partition/allocation,
administrator contact, account authorization, approved GPU-hours and storage
quota, permitted container build/runtime, and secure transfer policy.
No real university target is currently recorded.

If provided, inspect only under explicit access approval. Confirm actual GPU
name/VRAM, CUDA driver/BF16, exact package/image identity, Git, storage and
permission controls before weight transfer or launch. A100 80 GB is a planning
target, not measured fit or a guarantee of 8192-token QLoRA.

## Cloud Fallback

Use the separately cited [host comparison](G7_REMOTE_HOST_OPTIONS_V1.md)
for the concrete proposal: RunPod Secure Cloud US-KS-2, logical name
`atlasops-g7-sft-pilot`, one A100 SXM 80 GB, minimum 8 vCPU/32 GiB host RAM,
54 GB encrypted Volume Disk plus 20 GB container disk. The chosen
proposal is a named product, not a fabricated instance hostname. After
approved provisioning, record the actual instance/pod ID and runtime hostname;
the execution gate must bind those actual values.

An initial host/setup approval should explicitly **exclude weights and
training**. It permits only the named bounded resource, frozen image build,
SSH access and non-training compatibility/provenance checks. No spot/preemptible
host substitution, second GPU, public web UI, unrelated data upload or
unbounded storage retention.

## Storage and Image

Public model metadata records four shards totaling 15231271888 bytes
(about 14.19 GiB), not downloaded. Exact sizes and upstream SHA-256 are in
`artifacts/evidence/stage7/qwen_a09a354_weight_metadata_v1.json`.
At most one reviewed base snapshot should be staged; avoid a second automatic
Hugging Face blob-cache copy. The gate expects regular read-only snapshot
files with a complete inventory, not writable symlinks into shared caches.

The proposed 54 GB encrypted volume is about 50.3 GiB, plus 20 GB container
disk, about 68.9 GiB total capacity. Keep at least 20 GiB free across the
actual working filesystem and respect the existing 100 GiB total per-run cap;
stop if the verified required footprint cannot fit this proposal.
The 50 GiB target means provisioned durable capacity, not 50 GiB usable after
data; actual free space, filesystem overhead and quotas remain unverified.
Container disk is ephemeral and is not used for evidence retention.
Budget includes base weights, image/lock cache, tokenizer/corpus, adapter and
intermediate checkpoints, trainer/log/reload artifacts and temporary files.
Provider container disks and images can be billed separately; include them in
the explicit quote and resource ceiling rather than silently exceeding the cap.
Verify provider decimal-GB versus GiB units before confirming the purchase.
No checkpoint count/disk fit is measured yet.

The original frozen Dockerfile is preserved. The host-ready extension
`infra/training/sft-pilot/Dockerfile.host-v1` uses the same immutable Python
base and 72-package lock, with Git from dated signed Debian indexes.
Git is required for source/blob admission and was absent from the slim recipe.
The signed public snapshot endpoints returned HTTP 200 on 30 September 2026.
No large CUDA package wheels or image layers were downloaded.

The future approved image build must use the reviewed source checkout as
build context, record the built immutable image digest and
`/opt/sft-os-packages.txt`, and pass `pip check` and `git --version`.
A Dockerfile/base digest is not evidence that the image was built or that it
works on a GPU.

## Secure Access and File Layout

Only the project lead/on-call operator gets full SSH/SCP/SFTP access using a user-owned
public key registered through the provider's approved account settings.
Keep the private key in the user's local secret store; never paste or commit it.
The access proposal explicitly permits one public-IP mapped TCP 22, with
source-IP firewall restriction where supported. Basic proxy SSH cannot do
SCP/SFTP. Do not open Jupyter, dashboard, HTTP inference or public application ports.
Use SSH key authentication, restrict source IP where supported, and retain
ordinary host-key verification. No credentials go into the image, Git, logs
or reports.

The custom image's default remains inert. After resource/access approval set
the provider startup command to `/usr/local/bin/start-sft-host`; this starts
key-only SSH and keeps the Pod alive, without invoking training. The operator
supplies only their public key in `SSH_PUBLIC_KEY`. Host keys are generated
inside the container; verify the provider/SSH fingerprint through an approved
channel before first connection.

After access approval, use a clean committed source checkout under
`/workspace/AtlasOps`, external run artifacts under
`/workspace/sft-pilot/runs/<approved-run-id>`, and an isolated cache under
`/workspace/sft-pilot/model-cache`.
Weights, if separately authorized, go to
`model-cache/models--Qwen--Qwen2.5-7B-Instruct/snapshots/a09a35458c702b33eeacc393d103063234e8bc28`.
Keep the model directory and all files read-only to the training account.
Keep code/data/model identity separate from mutable output, with umask 077
and no shared/untrusted writers.

## Approval Sequence

1. Lead approves the named host/product/region or university allocation,
   explicit resource/access/storage/cost ceilings and frozen image build.
   This first decision authorizes neither weights nor SFT.
2. On that host, collect package/platform/Git/GPU/storage provenance without
   models. Verify image digest, full package map, NVIDIA/BF16/bitsandbytes and
   actual memory/disk capacity; preserve failures.
3. Lead separately approves the exact pinned weight transfer and storage.
   Compare downloaded bytes to the public metadata, tokenizer file inventory
   and shard index. Hash every file and enforce read-only snapshot storage.
4. Review the actual source/host/runtime/model inventories and bind a named
   run/output, budget/wall-clock cap, interruption cleanup/retention and
   independent reload plan. Record explicit lead execution approval; pin its
   raw hash through independently reviewed code/CI. No flags self-authorize.
5. Only after that approval, run one epoch with the frozen settings. No G8
   inference, GRPO, live incident, Chaos or final-Test step is included.

The read-only helper `scripts/collect_sft_remote_provenance.py` prepares the
host/runtime and model inventory records expected by the gate. It never
issues approvals, downloads weights, starts inference or invokes training.
Root may test it only with synthetic metadata/files until host access is approved.

After explicit host/access approval, these are collection commands, not
training commands. Create the private external evidence directory first and
choose unused absolute output paths:

```text
python scripts/collect_sft_remote_provenance.py runtime --image-digest sha256:<approved-built-image> --inspect-gpu --output /workspace/sft-pilot/runtime-<host-id>.json
```

After separate approved weight transfer, inventory the regular read-only cache
snapshot without loading it:

```text
python scripts/collect_sft_remote_provenance.py model-inventory --snapshot-dir /workspace/sft-pilot/model-cache/models--Qwen--Qwen2.5-7B-Instruct/snapshots/a09a35458c702b33eeacc393d103063234e8bc28 --output /workspace/sft-pilot/model-files-<transfer-id>.json
```

The runtime record labels the image digest as operator-supplied, not attested
by the collector. Corroborate it with provider/image-registry evidence before
pinning an execution permit. The gate now requires a separate hash-bound
image attestation with named verifier, matching hostname/digest and literal
persistent-storage/quota verification. It also requires recorded OS-package
inventory and measured disk free space. Host/runtime reports do not authorize execution.
The collector's read-only byte hashes are not protection against a hostile
writer during later loader reads; independently managed read-only storage
remains required.

## Next Decision

Approve the exact RunPod proposal above for **one-hour non-training setup**
and **seven days stopped-volume retention**, with a **$5 all-in cap**, or
provide a verified university allocation with equivalent controls.
Public listed-rate estimate is $4.12028 before tax; account-specific quote,
required funding minimum, capacity/encryption and image size are unverified.
If the checkout exceeds the cap or selected resources are unavailable,
stop for a revised decision rather than substituting a provider/region.
This approval may include building/publishing the no-weight host image to a
user-approved registry and key-only SSH exposure; it still excludes model
weights, inference and SFT. A later four-hour pilot/reload resource proposal
is $17.20 before tax with a proposed $25 ceiling and separate execution approval.

Exact actual hostname/ID is bound after provisioning,
not invented now. Until then, D1 remains unapproved and D2 weight-transfer
authority remains absent. G7 is PARTIAL and Stage 15 NOT_CERTIFIED.
