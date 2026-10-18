# Minimum project-closure execution proposal

Status: **PROPOSED / NOT APPROVED / NOT EXECUTED**.
This document requests decisions; it is not an execution permit.
G4/G9 freezes, consumed approvals, preserved ledgers and existing split/access
controls remain unchanged. G7 is not being repeated; G10/G11 are excluded.

The [closure checklist](COMPLETION_CHECKLIST.md) is the current work inventory.
The [Master Pipeline](MASTER_PIPELINE_STATUS.md) and
[deferred handoff](DEFERRED_RESEARCH_HANDOFF.md) define the remaining gates.
No new metric, checkpoint, incident result or cleanup result is claimed here.

Preparation revision: **v2, 18 October 2026**, reviewed against fetched main
`d004140ee6dd1cc7cecb74813393ee958e01ef55`.
This extends the current proposal, not any frozen experiment.
Every stage remains **PROPOSED / NOT APPROVED / NOT EXECUTED**.
The source SHA is an audit basis, not a new execution-source freeze.

## Environment and resources

Use a dedicated, owner-controlled experiment checkout of the final reviewed
source commit. The recorded software checkpoint is merged PR 214 at
main commit `539c4ae76a1955efb778b006f6414e69f04a3325`, tree
`e94f6699c59e4cd9c202742cc847ecaada50eded`, after accepted corrections in
PRs 209-213. Its verified source package does not authorize research execution.

The original unapproved execution candidate remains pinned to merged PR 207 at
`f13374f`. The software checkpoint does not repin research or authorize
execution. Any future execution requires a new reviewed execution-source freeze
with a full SHA and immutable source inventory, plus a separate execution
permit. This proposal remains unapproved.
Do not execute from a dirty development checkout or silently accept source
drift against an old protocol.

The original A-F plan omitted G6. Revision v2 adds A6 as a separately
identified zero-shot incident pilot. Stage B still supplies G8 evidence only.
Neither pilot automatically satisfies the full evaluation-split gate.
Their protocols, populations, and permits must be independently accepted.

| Resource | Proposed use | Admission requirement |
| --- | --- | --- |
| Existing local Docker/Kind | Canonical `kind-atlasops-local`, isolated Online Boutique and observability | Fresh target/context identity, healthy workloads, independent cleanup baseline, usable fault-detection metrics and no unrelated workload exposure |
| Local CPU/RAM | Offline evaluator and control-plane work | Planning estimate: 4 available CPU cores and 16 GiB free RAM; measure actual host capacity before approval |
| Owner-provided GPU | Pinned Qwen 7B Base, preserved v17 and a future controlled G9 run | Prefer at least 24 GiB VRAM; a free 16 GiB T4 requires separately demonstrated peak-memory fit and a frozen low-memory configuration |
| Disk | Immutable raw episodes, local checkpoint and reloading evidence | Planning allowance: 60 GiB free; inventory existing cache first. No multi-GB download/copy or checkpoint staging under this proposal |
| Credentials | Local model/operator/audit access only when required | Rotate the previously exposed provider keys first or use unrelated approved local credentials. Keep values outside argv, logs and Git |

No paid GPU, cloud cluster, new infrastructure project or production deployment
is proposed. These resource figures are estimates, not observed readiness.
Model/provider access and GPU use require an explicit owner decision.

Retain the documented Base/tokenizer revision
`Qwen/Qwen2.5-7B-Instruct@a09a35458c702b33eeacc393d103063234e8bc28`
and the existing v17 provenance. Do not copy an old approval into a new run.
Any revised corpus, prompt, action parser, environment or reward definition
must have new hashes and independent protocol review before execution.

## Staged execution and authorization

Each stage below requires its own exact approval. Approval of this proposal does
not authorize every later stage. A negative result consumes the declared run
budget and stops that stage; it does not start an automatic retry.

| Stage | Bounded proposed work | Evidence needed | Separate authorization |
| --- | --- | --- | --- |
| A: G4 causal incident | One newly identified SF002 paymentservice episode after all readiness checks | Baseline health; fresh metrics; actual fault; alert; model diagnosis/action; authenticated exact-action decision; executed action; independent recovery and cleanup | Exact context, fault manifest, source/model hashes, one fresh attempt identity, execution/cleanup budget and allowed action set |
| A6: G6 zero-shot incident pilot | Six Base-only Validation episodes in the frozen order, after accepted G4 and readiness | Genuine per-episode alert, agent workflow, approval, action, verifier, failure, clocks and cleanup; independent baseline recomputation | Separate G6 permit, six fresh episode identities, approved Validation membership and finite limits; no SFT, RL, Test or Leaderboard |
| B: G8 matched incident pilot | Base and v17 on the same approved incident population, seeds, budgets and objective predicates | Raw per-arm episodes, format/action/safety outcomes and independently computed resolution/TTR with failures retained | Frozen population and finite episode count. A pilot does not automatically satisfy the full G8 criterion |
| C: G9 new Train-only protocol | Corrected observation-before-action and canonical SFT-compatible policy contract, followed by one declared controlled optimizer pilot | Raw generations/tokens, parse decisions, objective rewards, group advantages, reward-only versus KL diagnostics, parameter changes, checkpoint inventory and independent reload | Explicit reopening of the frozen controlled track, new data/config/source hashes, GPU/memory budget, optimizer/generation caps and stop rule |
| D: G12 integrated policy | One governed environment episode using an independently accepted G9 checkpoint | Same causal sequence as G4 plus exact policy artifact provenance | New integrated-run permit; no inherited permission from stage C |
| E: G13 final measurement | Matched Base/SFT/SFT+GRPO and declared ablations/stress cases | Frozen variant/split membership, failure denominators, trial budgets, raw outcomes, uncertainty and independent recomputation | Accepted third arm plus separate Final-Test/Leaderboard access approval and a complete measurement freeze |
| F: G14/G15 acceptance | Owner-selected peer host and final academic submission | Image/source hashes, peer startup/security checks, verified package and actual receipt of submission | Target/audience/deployment approval and external submission approval |

Stage A uses the existing SF002 StressChaos target: one `paymentservice` pod in
`default`, four CPU workers at 90% load, ten-minute manifest duration.
The exact manifest hash and action policy must be frozen in the new protocol.
The evaluator must distinguish agent-caused recovery from automatic fault expiry
or harness cleanup. No old attempt identifier or attempt budget is reopened.
If the fault does not occur, observations become stale, approval is absent,
transport fails, or cleanup is uncertain, stop and preserve that outcome.

Stage C is a new methodological proposal, not a rerun of the negative four
completions or eight-sample diagnostic. The smallest preparatory question is
whether the reviewed observation/action contract permits valid actions with
varying objective rewards. Synthetic CPU optimizer fixtures already establish
software mechanics only. They cannot supply a trained third arm or G9 PASS.
The proposed pilot ceiling is two groups of four completions, at most 192 new
tokens per completion and two optimizer steps, using seed 2026. Freeze all other
sampling, optimizer, loss and memory settings in the approval bundle. The new
contract and inputs must be distinct from the preserved negative experiments.
No automatic warm-up retry, parser repair, extra sampling or budget extension
is permitted. These limits require approval before pretrained inference or
training begins and are preparation evidence, not enough by themselves for a
full G9 scientific conclusion.

Stages B and E cannot proceed with unspecified trials or a missing GRPO arm.
Retain the diagnostic Base/SFT result unchanged; do not classify another
alert-only comparison as incident-resolution evidence.

## Revision v2 incident preparation

The following limits are proposed ceilings, not execution authority.
No new run identity is reserved during preparation.

| Contract | A: G4 | A6: G6 | B: G8 |
| --- | --- | --- | --- |
| Population | One new `single_fault/sf-002` episode | The six `VAL_SPLIT` scenarios in `config/scenario_catalog.py`, in declared order | The same six scenarios, paired Base then v17 for each scenario |
| Model | Pinned Base, adapters disabled for all roles | Pinned Base only, zero-shot, no fitting | Same Base/tokenizer and unchanged v17; adapter enabled only in SFT |
| Sampling | Seed 1337; greedy; temperature 0; top_p 1; 512 new tokens per generation | Same proposed sampling and role contract | Same sampling, role prompts, tools, predicates and limits per arm |
| Episode limit | One; 3,600 seconds after benign qualification | Six; 3,600 seconds each, 21,600 seconds total | Twelve; 3,600 seconds each, 43,200 seconds total |
| Mutation limit | One approved remediation action; separate run-owned cleanup | At most one approved remediation action per episode; cleanup before advancing | Same limit per arm; independent reset and fresh fault/observation binding |
| Retry | None after reservation | None; negative/inconclusive episodes retain their identity | None; no replacing an unsuccessful arm or selecting the better repeat |
| Acceptance claim | Causal SF002 incident only | Descriptive Validation incident baseline; full G6 acceptance needs its approved evaluation-population rule | Descriptive matched pilot; original G8 improvement/incident criterion requires independent adjudication |

A6 and B use distinct episodes and manifests. No A6 episode is reused as
B's Base arm. A future shared-baseline design needs explicit prospective
approval of reuse and its statistical dependence; it cannot double-count
one episode as two independent observations.
Each generation must bind its immutable Base/tokenizer/adapter content
digests to the actual loaded and served identity and raw request/response.
An alias, configured revision or caller declaration alone is nonclaimable.
Independent A6 acceptance must verify this per-generation serving evidence
under the [G6 identity contract](STAGE_6_ZERO_SHOT_BASELINE.md).
The single-action cap may be insufficient for some cascade/multi-fault cases.
Such failures stay negative. Changing this cap requires a new protocol and
permit before execution, not a post-result exception.

All three incident stages require actual Alertmanager-originated admission,
not a harness-constructed alert. Preserve alert fingerprint, receiver request,
delivery time, resource UID and metric sample timestamps. Prove the fault is
active before remediation and objective recovery precedes fault expiry and
harness cleanup. Ready replicas, tool success and model prose are insufficient.
Cleanup uses only the run's exact UID/resourceVersion-bound resources, with
an independently approved finite cleanup deadline and a zero-fault check.
The 600-second model-load/request limits do not extend the episode deadline.
Benign qualification itself requires separate approved model access and limits.

The current model-free website candidate inspection reports SOURCE_MISMATCH
on seven files against its historical pins. The profile is inactive,
runtime qualification is false, and the historical profile remains unchanged.
Do not repin that profile here. A fresh reviewed execution-source inventory,
causal admission implementation review and operator permit remain prerequisites.

## Proposed Train-only G9 v2 contract

Identifier: `G9-TRAIN-OBSERVATION-ACTION-V2-PROPOSED`.
This is a methodological design, not an enabled profile.
The normal dependency is accepted G4, G6 and G8 before G9.
An earlier controlled-only path would require a new explicit project-lead
exception and separate acceptance limits. The consumed v1 exception grants none.

| Component | Proposed v2 rule | Required proof before any launch |
| --- | --- | --- |
| Parent and inputs | Unchanged pinned Base/tokenizer and v17 inventories; only `single_fault/sf-002` Train membership; any new instructional corpus is separately versioned and labelled synthetic | Train-only provenance, exact corpus/prompt/template hashes and absence of validation/test truth in policy input |
| Observation | Bounded canonical JSON captured before generation: incident ID, alert/severity, target, measured ready capacity/demand, tool schemas and timestamps | Generation-input bytes/digest match the admitted observation; omit expected action, scoring predicates, reward labels and caller approval |
| Action | Exactly one JSON object with `tool`, `arguments`, and boolean `agent_claimed_resolved`; strict duplicates/nonfinite/extra-field rejection; schema and tool policy before execution | Independently demonstrate actual v17 tokenizer/chat-template and output-envelope compatibility; no assumption that its synthetic SFT corpus taught this direct-action format |
| Admissible tools | Simulator-only `kubectl_get` and `kubectl_scale`, target `default/paymentservice`, replica bounds from the reviewed tool schema | No host/Kubernetes calls; disallowed tools/targets/types are retained blocked outcomes; P1 exact-action permits remain separate |
| Reset and drift | Each group's four completions use the same immutable initial state; each simulated transition starts from an exact independent memory clone | Snapshot/branch-reset digests and serial execution order; no claim of equivalent physical cluster resets; next group uses a predeclared state, never a reward-selected branch |
| Objective reward | Freeze one version of `score_direct_action_step` and its full raw decomposition; invalid/blocked actions remain -1 under the reviewed rule; missing verifier evidence is unscorable, not zero | Recompute from observed transitions and checks; include admissible unresolved and resolved transitions; no caller claim or handcrafted reward enters optimization |
| Learning evidence | At least one group with admissible actions, different objective rewards and nonzero centered advantages; finite strictly positive same-group reward-only gradient norm and an update attributable to that reward component | Raw tokens/log-probabilities, masks, rewards, advantages, KL contribution, reward-only gradient and before/after tensor hashes; independently verify the update is reward-influenced, not KL-only or mere parameter change |
| Budgets | Two groups of four completions; at most 192 new tokens each; seed 2026; at most two optimizer steps; no extra warm-up or retry | New four-generation trainer configuration and installed-TRL lifecycle tests, exact sampling/optimizer/loss/runtime hashes, measured peak-memory fit and approved wall-clock/VRAM ceilings |
| Negative stop | Stop on malformed bindings, absent reward-only diagnostic, verifier failure, no admissible actions or no reward variation in the first group | Preserve raw failures and null accepted checkpoint; no second group merely to rescue a failed first group; no automatic parser/corpus repair |
| Artifact acceptance | Completed source/model/config/run manifest, full checkpoint inventory and fresh-process offline reload | Independent recomputation of the learning evidence and reload; a tiny pilot establishes only its bounded learning signal, not live G9 effectiveness or a G13-ready arm |

The installed controlled profile still declares two generations, not four,
and `REWARD_GRADIENT_DIAGNOSTIC_AVAILABLE = False`.
Its admission remains fail closed. Revision v2 does not flip this flag,
change `HYPERPARAMETERS`, reuse a receipt, or edit the negative v1 evidence.
The v2 sampling, optimizer, beta, prompt limit and wall-clock/VRAM fields must
be explicitly frozen after compatible-input and tiny model-free trainer
verification, before pretrained inference or training authorization.
No new SFT training or parser broadening is implied by the corpus review.

The [observation-first proposal](G9_OBSERVATION_FIRST_PROTOCOL_V1_PROPOSAL.md),
its [software candidate](G9_OBSERVATION_FIRST_SOFTWARE_CANDIDATE_V1.md),
journal and async bridge are reusable software context.
They are historical/disconnected candidates, not a new permit.
Retain every preserved negative and the limitations on sole-cause attribution.

## Proposed G13 measurement freeze

Use exactly Base, Base+v17 and v17+accepted GRPO.
Before any campaign, independently pin the common Base/tokenizer, V2
adapter and V4-to-V2 parent lineage, actual serving identity, source tree,
ordered population, scenario hashes, tool/approval policy, budgets and seeds.
The following directions are proposed, not approved formulas:

- Primary recovery denominator is all eligible started episodes, including
  malformed/no-output, policy/approval blocks and model timeouts.
  Only independently adjudicated infrastructure-invalid episodes may be
  excluded, with immutable reasons/raw references and counts shown separately.
- Before-start readiness refusals are not incident trials. After-start
  infrastructure exclusions need a preregistered taxonomy and independent
  adjudication blind to arm reward. Uncertain attribution stays eligible and
  inconclusive. Reattempts require separate authority and preserve originals.
- Recovery needs authoritative `env_resolved=true`, conclusive verifier
  evidence and causal recovery before expiry/cleanup. Missing fields are null.
  Report cleanup failures separately and do not delete their episode rows.
- Format and diagnosis use all eligible attempts, with a frozen no-output
  rule and diagnosis taxonomy. Reward is independently recomputed using one
  hashed common scorer; specify per-step aggregation before any launch.
- TTR starts at admitted Alertmanager delivery and ends at first conclusive
  objective recovery before the deadline. Unresolved episodes are censored
  at their finite deadline. Report resolved count, censored count and the
  time origin; a resolved-only mean is not a population recovery-time claim.
- Freeze the clock source, timestamp precision/offset, wall-to-monotonic
  correlation, maximum accepted skew and stale/out-of-order rejection policy.
  Missing or incomparable timestamps make TTR null/unscorable, not
  deadline-censored. Censoring requires a valid comparable time origin.
- The first campaign is descriptive. Predeclare seed/order pairing and the
  independent replicate/interval method before any repeated-seed claim.
  No superiority inference follows from a single campaign or selected runs.
- Final-Test access, independently sourced adversarial membership and its
  seed, and invalid-run reattempt rules remain owner decisions. Leaderboard
  overlaps Train/Validation and is not an independent held-out population.

The current [raw replay v0.4 candidate](G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_4.md)
retains terminals and checks metadata lineage, but remains nonclaimable.
Its dated source hashes are not a current-source freeze.
The legacy aggregator consumes unverified artifact summaries; the newer
candidate deliberately refuses Final-Test scope.
Neither is an approved final campaign executor. A reviewed final-access path,
exact metric mapping, null rules and independent raw recomputation must precede
G13 execution. Do not remove the current access guard for preparation.

## Immutable evidence plan

1. Create a fresh run UUID and exclusive output root outside the execution
   checkout. Preserve all previous archives, ledgers, poison markers and approvals.
2. Before launch, freeze code SHA, source inventory, model/tokenizer/checkpoint
   hashes, dataset/split membership, configuration, seeds, environment versions,
   host/context identity, allowed action policy and the signed/dated approval.
3. Persist append-only raw input observations, generations/tokens, tool requests
   and responses, exact-action approvals, timestamps, objective verifier checks,
   reward/advantage/optimizer diagnostics, failures, cancellation and cleanup.
4. Keep sensitive raw operational evidence in the approved private store.
   Publish only an allowlisted, redacted projection with SHA-256 references.
   Never log provider keys, approval credentials or kubeconfig contents.
5. Seal a content-hashed run manifest and archive. Checkpoints receive a complete
   size/hash inventory and fresh-process offline reload; failed runs keep null
   checkpoints and retain failed artifacts.
6. An independent verifier recomputes acceptance from the preserved raw bytes.
   Only then may the Master Pipeline gate change. CI, a marker, model prose,
   tool success and cleanup alone are never a recovery verdict.

## Required owner decisions

The next executable action is approval of a fully pinned stage-A protocol or a
fully pinned stage-C controlled protocol, not a blanket "finish everything" grant.
The owner must select the host/model access, explicitly reopen the relevant
frozen track, approve the finite budget and exact risks, and retain interactive
exact-action approval for live remediation.

Until then, scientific closure is blocked, not failed software work in need of
more retries. The preserved negative results remain the project's genuine
scientific conclusions. Local software and a verified review package remain
useful and must be reported separately from accepted research completion.
