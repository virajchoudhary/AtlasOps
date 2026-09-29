# G7-G9 remote-training readiness assessment

**Status: NON-LIVE SOFTWARE REVIEW / TRAINING NOT AUTHORIZED / EMPIRICAL
EVIDENCE MISSING.** Source basis:
`56575293c0f6bfaebbbf98404d7cb1f5fd19d44e`. This assessment does not
select a compute provider, allocate a GPU, download weights, train a model,
evaluate final Test, or certify an experimental gate. The project lead reports
that the personal laptop cannot run the planned local 7B training workload.
External hardware is a future, separately approved decision.
The later [GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md) removes RS from
the required comparison. The source SHA above is the original software-audit
snapshot, not a freeze of the earlier five-arm proposal.

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
| G7 SFT | Train-only corpus builder, QLoRA entrypoint, assistant-only template/loss checks, status and checkpoint inventory | Approved compatible remote runtime and immutable model/tokenizer revisions; completed, independently loadable adapter; real run log |
| G8 evaluation | Checkpoint and corpus-origin validation, raw diagnosis/error capture and frozen split digest | Legitimate G7 adapter and independently reviewed inference; no environment-resolution measurement in this diagnosis-only path |
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
  The known canonical fixture must be rejected for every SFT training role.
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
- The ignored corpus currently present in the local checkout matches the
  tracked canonical-LF digest, 64 rows and 16 Train scenarios, but its
  adjacent `data/sft_corpus_manifest.json` and
  `data/sft_training_config.json` are absent. Those bytes alone do not
  establish the origin classification in a new SFT run. Generate and verify
  a fresh external corpus **with** its adjacent sidecars before any training.
- `training.sft` takes one bounded 16 MiB byte snapshot before its planned
  manifest and derives rows, count, origin and hash from that snapshot. An
  adjacent valid origin manifest can establish
  `scenario_derived_synthetic`; missing/conflicting provenance is
  `UNVERIFIED`, not silently historical or real data. G8 requires an explicit
  `--approved-corpus-path` to recheck that source; without it the origin
  remains `UNVERIFIED`. G9 does not re-read an approved corpus path when it
  validates the SFT parent, so training-time origin alone is not inherited
  as currently verified source provenance.

## Model, Training Stack and Reproducibility

| Item | Current contract | Remote-readiness gap |
|---|---|---|
| Model/tokenizer | Planned `Qwen/Qwen2.5-7B-Instruct`; SFT and G9 require full 40-character commit pins. G9 matches the completed SFT parent, requires the model loader's exposed commit to match, and records a tokenizer loader match or the explicit pin-enforced/not-independently-returned basis. Neither path attests serving identity. | No project-lead-approved model/tokenizer commits, actual weight identity, or serving attestation. Resolve and approve exact commits before a future authorized load, which may download uncached weights. A smaller/different model changes the comparison and needs a versioned decision. |
| QLoRA | SFT uses 4-bit NF4, double quantization, BF16 compute, rank 16/alpha 32/dropout 0.05, seven projection/MLP modules, paged 8-bit AdamW, assistant-only loss, 2048-token maximum. Tracked config proposes three epochs, batch 2, accumulation 4 and seed 2026. | Verify GPU BF16, bitsandbytes/PEFT/TRL compatibility, peak memory, storage and actual effective configuration on the approved host before weight transfer. Code refuses an installed TRL lacking `assistant_only_loss`. |
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

1. **Software-preflight candidate:** clean source, deterministic Train-only
   corpus generation/hash checks, local contract tests, a pinned dependency
   plan and a dry inspection of CLI/configuration. No model loading is needed
   to inspect these contracts.
2. **Before G7 execution:** select and approve a host and storage budget,
   model/tokenizer revisions, secure access/transfer, exact dependency image,
   BF16/4-bit compatibility, checkpoint retention and independent reload
   criteria. Free-tier or university access is a possibility to verify, not
   a presently available entitlement or spend authorization.
3. **Before G8 empirical inference:** independently validate the completed
   G7 adapter and source/corpus lineage, isolate raw output, prevent label
   leakage and agree on the Validation-only development plan. G8 diagnosis
   scores cannot establish incident recovery or G13 common reward. The master
   G8 acceptance text calls for resolution rate and format compliance, while
   today's empirical evaluator explicitly leaves resolution null. Reconcile
   that acceptance target or add a separately reviewed integrated evaluator
   before claiming G8 meets it; do not fill the null from mock output.
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
