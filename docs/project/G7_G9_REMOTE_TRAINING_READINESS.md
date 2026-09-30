# G7-G9 remote-training readiness assessment

**Status: NON-LIVE SOFTWARE REVIEW / TRAINING NOT AUTHORIZED / EMPIRICAL
EVIDENCE MISSING.** Source basis:
`56575293c0f6bfaebbbf98404d7cb1f5fd19d44e`. This assessment does not
select a compute provider, allocate a GPU, download weights, train a model,
evaluate final Test, or certify an experimental gate. The project lead reports
that the personal laptop cannot run the planned local 7B training workload.
External hardware is a future, separately approved decision.
The [versioned D3 candidate review](G7_D3_CANDIDATE_REVIEW_V1.md) and
[future G7 acceptance contract](G7_SFT_PILOT_ACCEPTANCE_V1.md) supplement
this historical readiness snapshot. The new candidate was synthetic and pending
lead approval at this snapshot. Training now fails closed for pending and
unversioned input; even the later preparation-approved candidate lacks a
separate named execution permit.
The later [GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md) removes RS from
the required comparison. The source SHA above is the original software-audit
snapshot, not a freeze of the earlier five-arm proposal.
The 2048-token, three-epoch figures below describe that earlier proposal.
The later `config/sft_pilot_v4.json` preparation plan instead pins 8192 tokens
and one epoch; the 2048-token all-row preflight is preserved as a negative
result, not a current pilot launch setting.

## Current Preparation Addendum (30 September 2026)

This non-live addendum updates current G7 preparation facts only; its reviewed
source basis is `1b13695f0e405883d7d66da20fe52d23435d8a7d`. The original source
basis and historical findings below remain unchanged. G7 D3 is now
`APPROVED_FOR_PREPARATION` for the exact `train-candidate-v1` candidate:
68 synthetic Train rows, corpus SHA-256
`19606e4fec300f641c7c8b8a989497367a444a870d3491f225004c01df3ee5fd`, and
manifest SHA-256
`35c9fd63328ef1319f616f2a23a025be38ad99c594dcd8bb38b733e0ff44c67c`.
No new candidate version or second G7 D3 decision is needed for this exact
preparation input. This approval is not training authorization.

The D2 preparation identity is resolved to
`Qwen/Qwen2.5-7B-Instruct` at immutable commit
`a09a35458c702b33eeacc393d103063234e8bc28` for both model and tokenizer.
`config/sft_pilot_v4.json` binds this identity, the exact corpus/manifest,
preparation settings, and required artifacts with `execution_allowed: false`.
The pin resolves preparation identity only; it does not authorize weight
transfer or loading, nor does it prove the identity of any local model weights.

The pilot-specific `training.sft_candidate_compatibility.validate_pilot_candidate`
admits only the reviewed old/new coordinator hash pair and confirms AST
identity outside `settle_environment`. The general
`validate_candidate_snapshot` remains unchanged and rejects source drift.
This narrow compatibility recomputes hashes for verification without rewriting
the frozen corpus or replacing its approved digests, and authorizes no other
source changes. The recorded v5 tokenizer/mask preflight is a prior non-live
result for all 68 rows at 8192 tokens with no truncation; it does not establish
hardware fit. Separately, a fresh `training.sft --preflight-only` admission
check for the exact v4 inputs returned `preparation_admissible: true` and
`execution_allowed: false`, with no output creation or ML-loader import. It
validates the prepared artifacts; it does not rerun tokenization or test a GPU.

No approved remote runtime or actual host-level GPU/BF16/4-bit compatibility
is established. No model-resident peak memory observation exists because
weight transfer/loading remains unauthorized; record it only during any
separately authorized load/run under a predeclared stop limit. No model weights
have been transferred or validated, and no completed adapter, training
record, or independent adapter reload exists. Public model metadata and the
staged tokenizer-file inventory are not a transferred local model-weight
inventory. A separate named, hash-bound execution approval and verified host,
budget, runtime, and model-weight inventory are still required before
training. G7 remains `PARTIAL`.

The separate 29 September 2026 read-only hardware feasibility discussion
recommended seeking a university BF16-capable allocation first, with
40-48 GB as an **SFT request estimate** and 80 GB as a **G9 feasibility
starting point**. It suggested 50-100 GB and 100-200 GB of persistent
storage as preliminary SFT and GRPO planning ranges. None is a measured
fit, available entitlement, quoted total cost or approved purchase. Free
notebooks have variable GPUs and transient sessions; they are not an
approved host for live G9's private cluster/operator channel. Rental is
a fallback only after current terms, security and budget are approved.

## Readiness Summary

| Stage | Software preparation present | Acceptance evidence still missing |
|---|---|---|
| G7 SFT | Exact 68-row D3-preparation-approved corpus, v4 hash-bound plan, settling-only source compatibility, all-row 8192-token preflight, QLoRA entrypoint and checkpoint inventory | Named execution approval; verified remote host/runtime, GPU capacity and storage; approved and inventoried weights; model-resident peak memory recorded during any authorized load/run; completed, independently loadable adapter; real run log |
| G8 evaluation | Checkpoint and corpus-origin validation, raw diagnosis/error capture and frozen split digest | Legitimate G7 adapter and independently reviewed inference. D12 remains `PENDING / NOT APPROVED`; the current diagnosis-only path has no environment-resolution measurement or integrated evaluator |
| G9 GRPO | Full commit-pin and SFT-parent preflight, fresh one-shot output claim, loader-identity basis, exact direct-action policy/environment/reward path, serialized live rollout controls and run ledger | Approved cluster/operator/model/compute protocol, actual SFT parent, completed real GRPO adapter and independently verified evaluation |

These are software capabilities, not training outcomes. G7 remains `PARTIAL`,
G8 `IMPLEMENTED / EMPIRICAL EVIDENCE MISSING`, and G9 `REOPENED`.

## Data and Input Provenance

- The tracked Stage 7 manifest records 64 demonstrations: 16 frozen Train
  scenarios times four roles. The current builder labels newly generated
  corpus sidecars `scenario_derived_synthetic` and `synthetic: true`; the
  older tracked manifest itself has no such origin fields. The canonical-LF
  corpus SHA-256 is
  `523cad3478e2018ebb830bab973bc02811045c6131dd0bf8f59328d756287e81`.
  `data/sft_corpus_train.jsonl` is ignored and absent in a clean checkout;
  `training.build_sft_dataset` reconstructs it in a chosen external output
  directory with adjacent manifest/config. A fresh 2026-09-30 audit found
  32 remediation tool calls to `k8s_delete_pod` and `environment_verify`,
  neither exposed to that role, and no P1 approval context in those rows.
  The exact canonical fixture and tested representation-only variants of
  its assistant targets are rejected for every SFT training role, even when
  an unrelated role changes. Selected-role target fingerprints are conservative:
  a reviewed corpus with different context but identical teacher targets
  can also be rejected pending a separate D3 admission mechanism. This is
  not authenticated approval for other data. This is the historical 64-row
  builder fixture, not the separate 68-row `train-candidate-v1` approved for
  preparation in the addendum. Keep it for schema/rendering checks only; do
  not train from it. The separate candidate's preparation approval still
  does not authorize training.
  All-role and remediation selections also contain disallowed tool calls;
  triage-only role-tool validity is not approval for a model-training pilot.
  This is a schema fixture, not a successful trajectory set or proof of
  downstream improvement.
- The builder excludes Validation and Test IDs and rejects redirected output
  into canonical evidence. Before training, independently verify exact bytes,
  rows, role distribution, Train membership, tool-call/observation pairing,
  and the adjacent manifest. Do not train on Validation, Test, Leaderboard
  overlap, or adversarial outcomes. Preserve the generated raw corpus and
  sidecars outside ordinary Git with access controls and hashes.
- At the original local-checkout snapshot, the ignored corpus matched the
  tracked canonical-LF digest, 64 rows and 16 Train scenarios, but its
  adjacent `data/sft_corpus_manifest.json` and
  `data/sft_training_config.json` are absent. Those bytes alone do not
  establish the origin classification in a new SFT run and are not the exact
  D3-approved candidate/manifest pair. Do not substitute this fixture for the
  approved preparation input.
- `training.sft` takes one bounded 16 MiB byte snapshot before its planned
  manifest and derives rows, count, origin and hash from that snapshot. An
  adjacent valid origin manifest can establish
  `scenario_derived_synthetic`; missing/conflicting provenance is
  `UNVERIFIED`, not silently historical or real data. G8 requires an explicit
  `--approved-corpus-path` to recheck that source; without it the origin
  remains `UNVERIFIED`. G9 does not re-read an approved corpus path when it
  validates the SFT parent, so training-time origin alone is not inherited
  as currently verified source provenance.

- At this snapshot, a non-live tokenizer-only preflight was proposed for
  every frozen `train-candidate-v1` row against a 2048-token ceiling; no real
  pinned-tokenizer run or file inventory was then recorded. The later v4
  preparation includes a pinned all-row 8192-token report and tokenizer-file
  inventory. Neither report authorizes weight transfer or a training launch.
- Prospective G4 v3.6 changes `agents/coordinator.py`, one of the frozen
  `train-candidate-v1` source files. The strict general
  `validate_candidate_snapshot` continues to reject this source drift. The
  pilot-specific validator admits only the exact reviewed old/new hash pair
  when the AST is unchanged outside `settle_environment`, so the already
  approved v1 may be used for its bounded preparation preflight without
  rewriting it or replacing its approved digests. Any other source or corpus
  change still requires separate review; do not rewrite v1 in place.

## Model, Training Stack and Reproducibility

| Item | Current contract | Remote-readiness gap |
|---|---|---|
| Model/tokenizer | D2 preparation identity is resolved to `Qwen/Qwen2.5-7B-Instruct` at immutable commit `a09a35458c702b33eeacc393d103063234e8bc28` for both. SFT and G9 require full 40-character pins. G9 matches the completed SFT parent, requires the model loader's exposed commit to match, and records a tokenizer loader match or the explicit pin-enforced/not-independently-returned basis. Neither path attests serving identity. | Exact pin approval for preparation is not authority to transfer or load weights. Public model metadata and staged tokenizer files are not a transferred local weight inventory; no verified serving identity or serving attestation exists. Obtain separate transfer and host approval, then inventory the actual files before any authorized load. A smaller/different model changes the comparison and needs a versioned decision. |
| QLoRA | SFT uses 4-bit NF4, double quantization, BF16 compute, rank 16/alpha 32/dropout 0.05, seven projection/MLP modules, paged 8-bit AdamW and assistant-only loss. The earlier proposal used 2048 tokens and three epochs; v4 preparation pins 8192 tokens, one epoch, batch 2, accumulation 4 and seed 2026. | Before weight transfer, verify the approved host's GPU, capacity, storage and package/software compatibility. Actual model-resident peak memory can only be observed after a separately authorized weight load; record it during that run under predeclared stop limits. Code refuses an installed TRL lacking `assistant_only_loss`. |
| Dependencies | `pyproject.toml` has lower bounds for Torch, Transformers, TRL, PEFT, Datasets, Accelerate, vLLM and bitsandbytes. `requirements/train-constraints.txt` pins a set validated for SFT contract tests on Windows, not a completed GPU run; the dev lock excludes training. G7, G8 and G9 loaders set `trust_remote_code=False`. | Freeze an approved Linux/container environment and exact package/driver/CUDA or ROCm versions and hashes. Do not assume the Windows dev lock or Apple Silicon is a drop-in BF16/4-bit/vLLM training environment. A model requiring custom Hub Python needs a separately reviewed protocol; these loaders do not authorize it. |
| Checkpoint | SFT writes planned/running/completed/failed/interrupted manifest states and hashes its saved adapter inventory. G8/G9 reject incomplete, dirty-source or tampered completed parents for empirical use. | No completed usable adapter or independent reload result is preserved. Keep adapter, tokenizer, trainer state, full manifest, logs and source/corpus provenance together; verify hashes after transfer. |
| SFT to G9 | G9 requires `--sft-checkpoint`, matching base/tokenizer revisions and a completed parent inventory; its manifest binds parent hashes and rechecks them immediately before PEFT adapter loading. | Do not substitute a base-model alias or mock adapter. Path-based PEFT reads still leave a post-check replacement window; approve stable storage and a controlled live environment before G9 training. |
| Failure/restart | SFT persists interrupted/failed status and calls `trainer.train()` without a resume argument. Revision and bounded corpus preflight precede exclusive creation of a new output path. G9 validates pins and parent before exclusively claiming a fresh nonredirected directory; a one-shot marker blocks direct retry, and caught failures/interrupts retain terminal status. | No proven in-place resume/replay-safe continuation exists. Preserve any claimed directory after a write/load interruption, failed manifest, or partial checkpoint; a new run needs a new output directory, reviewed recovery plan and distinct run ID. Hard termination and parent-path replacement remain limitations. Do not relabel a partial run completed. |

The G9 CLI declares eight generations, up to 200 steps, BF16 and paged
8-bit AdamW, but its default batch size one with accumulation four is not
divisible by eight generations under pinned TRL 0.19.1 and is rejected
before output or model loading. Optuna's fixed one-by-one trial batch is
also incompatible with its four/eight-generation options, so positive trial
requests are explicitly deferred before any live rollout. A compatible
generation budget requires separate review and approval; none is silently
substituted. Each eventual online reward would invoke a serialized
policy/environment rollout, with fault application, settling, verifier and
cleanup. These settings establish no GPU-memory figure, duration, cost,
completed episode count or resource availability.

## Current Gate Boundaries

1. **Software-preflight candidate:** clean source, hash checks for the exact
   D3-approved candidate and manifest, local contract tests, the v4
   preparation plan, and a dry inspection of CLI/configuration. The legacy
   64-row fixture is for schema/rendering checks only. No model loading is
   needed for the preparation preflight.
2. **Before G7 execution:** separately approve a host and storage budget,
   weight transfer and secure access, exact dependency image and runtime,
   host-level GPU/BF16/4-bit software compatibility, checkpoint retention and
   independent reload criteria. Record actual model-resident peak memory
   during any authorized weight load/run against predeclared stop limits. The
   immutable model and tokenizer pin is already resolved for preparation,
   not transfer.
   Free-tier or university access is a possibility to verify, not a presently
   available entitlement or spend authorization.
3. **Before G8 empirical inference:** independently validate the completed
   G7 adapter and source/corpus lineage, isolate raw output, prevent label
   leakage and agree on the Validation-only development plan. G8 diagnosis
   scores cannot establish incident recovery or G13 common reward. The master
   G8 acceptance text calls for resolution rate and format compliance, while
   today's diagnosis-only empirical evaluator explicitly leaves resolution
   null. D12 remains `PENDING / NOT APPROVED`; no integrated evaluator or G8
   resolution claim is established. Reconcile that acceptance target or add a
   separately reviewed integrated evaluator before claiming G8 meets it; do
   not fill the null from mock output.
4. **Before G9 live training:** separately approve the selected cluster,
   operator channel, fault/alert authority, P1 rule, serialized cleanup,
   parent checkpoint, model identity, budget and interruption recovery.
   P1 rejection/timeout/missing decision must remain blocked. Local tests
   and green CI are not live operator or cluster evidence.
5. **Before final comparison:** freeze the common raw metric protocol,
   scorer, three-arm evaluator matrix, adversarial admission/membership
   and Test access with independent review. Cross-arm checkpoint lineage
   and raw metric recomputation remain unverified; historical RS-bearing
   V3/V5 evaluators are optional, not required blockers. G13 remains
   `REOPENED`.

## Verification and Limitations

The repository has focused tests for corpus/split/template and provenance,
SFT lifecycle/checkpoint validation, G8 mode and raw-output isolation, and
G9 direct-action/approval/reward/ledger contracts. Those tests use synthetic
or mocked boundaries. The post-PR-98 `main` CI passed Python 3.11/3.12 and
frontend checks at the review basis, but it did not train or reload a 7B
adapter on an approved remote GPU, run a live incident, or certify the
current three-arm comparison. No provider quote or free-tier quota is treated as
capacity reserved for this project.

The companion [staged runbook](G7_G13_REMOTE_EXECUTION_RUNBOOK.md) describes
future gates without initiating them. The [decision
register](G7_G13_DECISION_REGISTER.md) lists approvals still required.
