# G7-G13 staged remote execution runbook

**Status: FUTURE EXECUTION PLAN / NOT AUTHORIZED.** This document is a
non-live handoff, not a launch instruction for the current machine or a
provider reservation. No command below was executed to train, load model
weights, use a cluster, access final Test, or deploy. Review basis:
`56575293c0f6bfaebbbf98404d7cb1f5fd19d44e`. The personal laptop is
not a viable host for the planned local 7B workload; remote hardware and
any cost require an explicit project-lead decision.
The later [GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md) supersedes
the earlier RS-inclusive arm count; the source SHA above identifies the
original software review, not an approved five-arm campaign.

Each phase has an entry gate and an exit artifact. A failed gate stops the
sequence. Keep the original attempt, logs, checkpoints and raw outputs
immutable; start a distinct attempt only under the approved recovery rule.
Do not use a green unit test or an LLM assessment as empirical proof.

## 0. Freeze the proposed execution inputs

**Entry:** project lead approves the exact remote host/entitlement, storage
ceiling and cost cap (including a zero-cost entitlement if applicable), model
and tokenizer immutable revisions, source SHA, security boundary and
transfer mechanism. An independent reviewer checks the chosen package image
and source. No compute is assumed available before this record exists.

**Preparation:** record GPU type, BF16 and 4-bit support, driver/runtime,
package versions and hashes, disk quota and checkpoint retention. Estimate
model/cache, optimizer, dataset, temporary checkpoints, rollout ledgers and
logs **before** any multi-GB transfer. Do not put tokens, kubeconfigs,
provider credentials or model keys in command arguments or tracked files.
G7 and G8 loaders set `trust_remote_code=False`; do not bypass that setting.
A model requiring custom Hub Python needs a separate reviewed authorization.

**Exit:** signed run intent with clean full Git SHA, immutable model/tokenizer
revisions, approved environment digest, seed/budgets, output root, security
controls and independent reviewer. If model size or architecture changes,
version the comparison plan first; do not quietly replace the planned 7B
base. This phase authorizes nothing by itself.

## 1. Reconstruct and verify the G7 Train corpus

**Entry:** approved isolated destination and clean source checkout. The
command is a **future template**, not a request to execute it in this task:

```text
python -m training.build_sft_dataset --output <approved-external-root>/g7-prep/sft_corpus_train.jsonl
```

**Checks:** hash the generated canonical-LF bytes and require
`523cad3478e2018ebb830bab973bc02811045c6131dd0bf8f59328d756287e81`;
inspect the adjacent manifest/config, 64 rows, 16 frozen Train scenarios,
four roles, schema and tool/observation pairing. Quarantine Validation and
Test. Do not copy the ignored generated corpus into ordinary Git or infer
real incident success from these synthetic demonstrations.
The ignored local `data/sft_corpus_train.jsonl` has matching bytes but no
adjacent provenance sidecars; do not substitute it for this complete
external-output preparation.
The current 64-row synthetic corpus passes those integrity checks but
must fail SFT training admission for every role. Its 16 P1 remediation
rows have no approval context and contain 32 calls to tools absent from
the remediation role's runtime
allowlist. Use it only for non-model schema/rendering checks. Preserve its
bytes and tracked evidence; even triage-only ACL validity does not permit
training on the known fixture. Obtain a new versioned, independently
reviewed Train-only corpus with role-allowed tools and
approval/negative-outcome labels before moving to phase 2.

**Exit:** byte-preserved corpus, manifest, config, hash and independent
Train-only check in an approved evidence store. Hash and split agreement
do not satisfy the training-quality gate for the current corpus. On mismatch,
stop and preserve the failed generation record; do not train.

## 2. Train G7 SFT, then prove adapter recovery

**Entry:** phases 0-1 passed; BF16/QLoRA compatibility measured on the
approved remote runtime; exact dependency image frozen; output directory
new and empty. A future command shape supported by `training.sft` is:

```text
python -m training.sft --model Qwen/Qwen2.5-7B-Instruct --model-revision <approved-immutable-commit> --tokenizer-revision <approved-immutable-commit> --data <verified-external-corpus> --output <new-external-sft-run-dir> --epochs 3 --lr 0.0002 --batch-size 2 --grad-accum 4 --max-seq-len 2048 --seed 2026
```

The values above are the tracked **proposed** configuration, not a
project-lead selection of model revision, host or budget. Record effective
settings, package/driver/runtime metadata, GPU memory, source state,
dataset/template hashes, trainer history, failures and full checkpoint
inventory. The canonical `sft_run_manifest.json` must remain inside the
adapter directory and end `completed` only after saved files are hashed.
The SFT CLI requires full 40-character model/tokenizer commit pins and
rejects local paths or mutable aliases before claiming output. It requires
the model loader's exposed commit to match. The pinned tokenizer loader
may not return a commit hash; in that case the manifest records the
full pin passed as its `revision` with the explicit
`PIN_ENFORCED_BY_LOADER_ARGUMENT/NOT_INDEPENDENTLY_RETURNED` basis.
Any exposed tokenizer hash must match. This is not independent weight or
serving attestation; the project lead must still approve exact commits,
license and any future weight transfer before loading.

**Recovery:** after the planned manifest has been written, an interruption
or caught training exception records `interrupted`/`failed` status. Preserve
that manifest, directory and any epoch checkpoints. Revision, path and
bounded corpus/manifest preflight now precede exclusive output-directory
creation, so invalid preflight input does not claim the run path. A failure
between directory creation and planned-manifest persistence can still leave
a claimed directory without a manifest; preserve and inspect it rather than
relabeling it a recorded training attempt. The CLI has no `--resume` option and calls
`trainer.train()` without a resume argument. Its merged output guard
requires a previously nonexistent path, rejects redirects, and claims the
final directory exclusively. Do not pre-create even an empty output
directory. This does not guard against a parent path being replaced between
check and creation. Do not hand an incomplete adapter to G8/G9 or overwrite
the old manifest. Diagnose the failure; a separately authorized retry uses
a **new** output directory/run ID and records its relationship to the
failed attempt. Implement and test a resume path in a separate review if
true continuation is required.

**Exit:** independent process/host reload of the adapter against the
pinned base/tokenizer, verified manifest and file hashes, readable trainer
state, and a complete failure/retention log. A falling loss curve alone
does not pass G7 or establish incident improvement.

## 3. Evaluate G8 on a development partition

**Entry:** legitimate completed G7 adapter and explicit approval to
recheck the exact Train corpus path. Use a unique output directory. Start
with Validation; do not access final-Test outcomes as part of this plan.
The supported future CLI shape is:

```text
python -m bench.sft_eval --empirical --split val --checkpoint <verified-sft-run-dir> --approved-corpus-path <exact-approved-train-corpus> --output <new-external-g8-val-dir>
```

**Checks:** the evaluator revalidates checkpoint inventory and source,
base/tokenizer revisions and optional corpus origin before loading. It
withholds Validation truth until after generation, preserves raw responses,
parse/inference failures, settings and ordered split digest. Compare
diagnosis outcomes with a matched untouched-base G6 run only under a
separately approved evaluation plan. Mock archive figures and synthetic
outputs are not empirical baselines.

**Exit:** independently checked raw diagnosis/evaluator artifacts and
failure counts. G8 is diagnosis-only: its resolution, safety, reward and
alert-to-verifier TTR remain unevaluated. Do not promote G8 to an incident
recovery or G13 common-score result. The master G8 row asks for resolution
rate and format compliance before RL; the current evaluator sets resolution
to null. Reconcile the acceptance text or implement a reviewed integrated
evaluation before claiming the stated gate criterion is met.

## 4. Controlled G9 training and recovery - separate live gate

**Entry is currently CLOSED.** A real GRPO run requires the completed
G7 parent, immutable model/tokenizer match, selected controlled cluster,
fresh target acceptance, approved fault and alert procedure, approved
operator/cleanup authority, zero-Chaos preflight, storage/compute budget
and a documented interruption response. G4 is still `NOT_PASSED`.
No live fault, P1 request, or model training is authorized by this runbook.

After those *separate* approvals, the operator must select a new output
directory. The G9 CLI rejects existing and redirected output paths and
claims a fresh directory exclusively only after validating full immutable
model/tokenizer commit pins, repository IDs, and the completed SFT parent.
The G9 tokenizer and base-model loaders refuse Hub remote Python. The SFT
parent inventory and persisted run binding are checked again immediately
before PEFT reads the adapter; path-based reads still leave a narrow
post-check replacement window that needs an approved stable storage
boundary for any empirical run.
Its one-shot execution marker prevents direct training from resuming that
run. Direct training must match the persisted seed, configuration, live
context and operator profile; a mismatch fails before work without
changing the existing run. Positive Optuna calls are deferred before
any model, output or live work. The CLI also requires
`--execute-live-chaos` and a named `--kube-context`. P1 needs the
separately opted-in same-process operator channel and a securely supplied
`ATLASOPS_API_KEY`; rejection, timeout and missing decision remain blocked.
Do not put the secret in arguments or print it. The checked-in default
batch size one and accumulation four cannot be divided into eight
generations under pinned TRL 0.19.1, so the CLI rejects that configuration
before output or model loading. Optuna's fixed one-by-one trial batches are
also incompatible with four or eight generations and are explicitly
deferred. Select no replacement settings here: a compatible generation
batch and any extra live trials require a reviewed, separately approved
protocol and resource budget. The 200-step default is **not** an approved
execution budget.

The exact completion must be the action policy-checked and executed. Save
per-step public state, approval, tool result, settling, conclusive verifier,
reward components, next state, cleanup and failures. Serialize mutations
and verify zero active Chaos before the next rollout. Stop on missing real
alert/fault, inconclusive environment, failed cleanup or lost operator
channel; keep reward null where observation is unscorable.

**Recovery:** G9 refuses any existing output directory and does not
implement a validated resume command. After its one-shot marker, caught
direct-entrypoint and CLI failures or interrupts persist a terminal status;
hard termination may still leave a partial directory without a final
record. Preserve that attempt and run a separately reviewed fresh attempt
in a new directory only after cluster safety and attribution are
re-established. Never reuse an interrupted ledger as a completed checkpoint.

**Exit:** a completed G9 adapter with independently checked checkpoint
inventory, SFT parent hashes, source/config/seed, loader identity basis,
raw rollout ledger,
cleanup and failure records. A trained adapter is still not a G9 empirical
evaluation or a scientific PASS.

## 5. G9 evaluation and integrated comparison - separate protocol gate

After a completed G9 adapter and fresh live authorization, the evaluator
requires a checkpoint, public state directory, unique output directory,
`--execute-actions` and named Kubernetes context. P1 remains a separate
operator opt-in. Its raw stream must retain failures and exact action to
verifier lineage. Use Validation for development; do not use final Test to
select settings or repair a checkpoint.

The current prospective comparison has three matched arms: untouched base,
SFT, and the same SFT parent plus corrected GRPO. It needs matched
checkpoints/budgets, independently frozen adversarial membership/seed, a
reviewed and hashed common raw scorer, adjudicated eligibility, and
explicitly authorized final-Test access. The current Stage 13 aggregator
checks three-arm artifact membership but uses declared summary metrics and
does not verify cross-arm lineage; it cannot certify that comparison.
The old RS-bearing V3/V5 paths remain historical optional research, not
required evaluators for this scope.

**Exit:** only a future, independently reviewed raw-row recomputation
could support a gate decision. Preserve negative and interrupted attempts.
Until then G13 stays `REOPENED` and the submission `NOT_CERTIFIED`.

## Stop and Handoff Record

At every phase boundary, record source SHA and dirty flag, model/tokenizer
revisions, input/output hashes, effective config, seed, environment,
start/end times, access and approval records, raw failures and the
independent review result. A missing prerequisite stops the sequence
without inventing a PASS. The [readiness assessment](G7_G9_REMOTE_TRAINING_READINESS.md)
and [decision register](G7_G13_DECISION_REGISTER.md) are the handoff
companions; none grants execution authority.
