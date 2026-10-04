# Controlled G9 Admission v1

**Approved prospective research direction: 5 October 2026.**
Project lead: Viraj Choudhary, explicit `/goal` authorization in the
controlled-G9 chat. Admission is conditional on this amendment's merge,
required CI, independent Luna Max verification, and a separately verified
zero-cost training host. It is not an empirical PASS or a signature.

## Two Tracks

The live systems track is frozen. G4 remains **NOT_PASSED**; no attempt
018 or further golden-incident optimization is authorized. Preserve:

- 015: terminal INCONCLUSIVE / unscored.
- 016: pre-fault abort / non-result.
- 017: completed negative G4 outcome.
- Original G8 live incident-resolution criterion: unmet.

The controlled ML/RL track may admit one bounded genuine GRPO pilot from
the verified v17 SFT parent without satisfying or renaming live G4/G8.
This is a prospective exception to their progression prerequisites for
**controlled research only**, not a replacement G8 criterion, live-G9
authorization, G9 PASS, G12 readiness or final certification. D12 Option A
remains the live criterion; the old proposal and all historical refusals
remain preserved. D4-D9 final measurement decisions are not approved here.

The completed Pre-RL Execution Audit (external, immutable review packet)
records the diagnosis prompt/evaluator mismatch: the prompt requested a
`root_cause` object while G4 required a string. It also records Comms
`target_mismatch` versus `primary_target_preserved`. Attempt 017 is not
pure model-performance evidence or a Base/SFT comparison. All security
incidents, methodological caveats, raw files and hashes remain unchanged.
No audit or raw operational evidence is republished by this amendment.

Audit: `AtlasOps_Pre_RL_Audit_Report_20261005.md`, in the prior chat's
`outputs` directory. Audit raw SHA-256:
`1f494d8b71fbdb98843ada450d11e29eae3731552ba74e6289766557f03a27bf`.
Attempt-017 archive SHA-256:
`82d0b62ed33d0fb6ebc2924233a70fb1a1ae2e2385b8e374d06c931456eefb31`.

## Bounded Protocol

`training/grpo_controlled.py` defines `controlled-g9-capacity-v1`.
It is a closed, deterministic, stateful capacity simulator, not Kind,
Chaos Mesh, a physical digital twin or a live incident evaluator.
Only frozen Train identity `single_fault/sf-002` is admitted. The simulator
is a declared training analogue; it does **not** reproduce the original
SF002 Chaos-clearance recovery predicate. No held-out generalization or
incident improvement can be inferred from it.

The actual causal sequence must be:

`observed state -> v17-initialized trained policy -> one raw structured action
-> policy/approval checks -> simulator transition -> objective checks
-> reward -> next observed state`.

Two policy completions branch from exactly cloned memory states in one
generation group. They are independent action samples, not separate live
replicates. The fixed last branch supplies the next group's state, chosen
before seeing reward; recovered episodes reset to a new declared fault.
No reward-maximizing branch selection or silent state drift is allowed.
Every group observes its actual current state before generating tokens.

Actions use the existing strict action parser and runtime argument schemas,
role ACL, replica bounds and severity approval policy. Only local simulated
`kubectl_scale` and `kubectl_get` are implemented. No builtin operational
tool is dispatched; all other tools are refused. P2 is the pilot severity.
P0 remains manual; P1/unknown mutations fail closed unless the existing
gate supplies an exact-action, incident-bound, single-use permit. There is
no simulated approver or auto-approved P1 in the pilot.

Objective required checks: capacity covers fixed demand, and ready capacity
is positive. Only observed simulator state determines these checks.
Reward is `0.75 * verified_recovery + 0.25 * required_check_coverage`
for admitted actions. Invalid format, unsafe policy, unavailable tool,
wrong target or missing approval receives **-1**, a prospective controlled
training penalty, not a historical G4 score. The unchanged objective scorer
is called with claim input disabled. `agent_claimed_resolved` is retained
raw but **never changes recovery or reward**, including penalties.
Verifier failure/nonconclusive evidence yields **null**, aborts the group,
and prohibits a completed-training claim. No guessed zero replaces null.

One pilot: seed 2026; 2 optimizer steps; 2 generations; device batch 2;
accumulation 1; learning rate 1e-6; beta 0.04; 192 completion tokens;
sampling temperature 1.0, top_p 1.0, top_k 50; untruncated observed prompt;
one process; no Optuna, validation selection,
resumption or automatic retry. A group with all equal rewards may produce
zero advantages; optimizer invocation alone is not proof of learning.
Finite gradient evidence and changed adapter tensors are required.

## Parent, Runtime and Evidence

Base/tokenizer: `Qwen/Qwen2.5-7B-Instruct` at
`a09a35458c702b33eeacc393d103063234e8bc28`.
v17 manifest SHA-256:
`7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440`.
v17 adapter SHA-256:
`f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff`.
Revalidate the complete parent inventory before and after execution.
Load that adapter trainably; never rewrite it or start SFT again.
Pinned TRL's PEFT beta reference disables the adapter: KL is against the
pinned Base, not a frozen SFT copy. v17 is the trainable initialization,
not an asserted KL reference. This distinction is part of the prospective
pilot, not an unchanged three-arm evaluation design.

Pinned TRL 0.19.1 and the existing hash-locked NVIDIA runtime are required;
one verified free Tesla T4, FP16/NF4, single-process execution only.
Budget: $0, at most one hour, fresh output outside ordinary Git.
Estimate at least 15.24 GB base snapshot plus 0.38 GB parent bundle,
adapter/optimizer/evidence headroom and more than 20 GiB free host storage
before any transfer. Existing weights may be reused only after verification.
The historical SFT execution permit does not authorize a new host session.
No operational credentials or raw G4 material may be sent to a training host.

Before training, preserve an externally hash-pinned execution receipt with
merged clean source, protocol/config hashes, exact parent, base inventory,
observed runtime/GPU, named host and entitlement, start/end ceiling,
resource authority and local evidence-export destination. It is an integrity
record, not a human signature. No JSON boolean self-authorizes execution.

Preserve raw states, raw generated completions/token IDs, exact actions,
policy/approval outcomes, transitions/checks, rewards/nulls, failures,
source/model/runtime/config/seed, trainer and optimizer/gradient logs,
pre/post tensor hashes and saved adapter inventory. Exclusive durable
episode journaling starts before action dispatch; interrupted runs are
non-results and are never silently resumed.

Acceptance: a finite, changed, saved SFT+GRPO adapter plus fresh-process
network-isolated reload with parent/output inventories rechecked. Label
all episodes **CONTROLLED_SYNTHETIC_TRAINING / NOT_CERTIFIED**. This proves
only a bounded simulator-trained artifact, not live recovery or superiority.
Train only; no Test/Leaderboard loading, evaluation or publication.
Validation is neither needed nor used by this pilot; its prior frozen
development protocol and checkpoint remain unchanged.

The guarded CLI is `python -m training.grpo_controlled --receipt <external.json>
--receipt-sha256 <reviewed-digest>`; `--preflight-only` creates no training
output. The direct runner repeats admission checks. The reload CLI is
`python -m scripts.reload_grpo_controlled --run <run-dir>
--manifest-sha256 <preserved-digest> --output <external-reload.json>`,
launched separately with network isolation. Training returns
`TRAINED_RELOAD_PENDING`, never a completed-artifact claim by itself.

## Operational Boundary

Fresh-process reload refuses a shared network namespace or any interface
other than loopback. The caller uses `unshare --net`; offline loader flags
alone do not satisfy it. The training process enforces the receipt deadline
with Linux SIGALRM; a timeout remains failed/null evidence.
Require nonzero finite objective-reward advantages as well as changed
tensors; KL-only updates on equal rewards do not count as GRPO learning.

After supervised download of the full run plus reload report to the named
local evidence store, run `python -m scripts.accept_grpo_controlled` with
the separately preserved manifest/reload hashes. It rehashes the checkpoint
and raw training ledger and writes an exclusive external acceptance report.
No ephemeral-host training or reload status satisfies completion until this
local export check passes. Export is supervised, not an automatic Drive mount.
The resource receipt is not proof of provider entitlement: root must verify
current free quota/account and named-host use authority independently before
launch. Current resources and transfer authority cannot be inferred from v17.

The historical live `training/grpo.py` observation-order refusal and all
live opt-in/context guards remain intact. Local deterministic contract
tests and CI are software evidence, never the genuine pilot.
If fresh free-host/resource/transfer authority is absent, stop at that
precise non-G4 external blocker. Do not solve it by renting compute,
changing models, transferring secrets, retrying G4 or weakening a guard.
