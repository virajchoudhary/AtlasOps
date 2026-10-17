# Minimum project-closure execution proposal

Status: **PROPOSED / NOT APPROVED / NOT EXECUTED**.
This document requests decisions; it is not an execution permit.
G4/G9 freezes, consumed approvals, preserved ledgers and existing split/access
controls remain unchanged. G7 is not being repeated; G10/G11 are excluded.

The [closure checklist](COMPLETION_CHECKLIST.md) is the current work inventory.
The [Master Pipeline](MASTER_PIPELINE_STATUS.md) and
[deferred handoff](DEFERRED_RESEARCH_HANDOFF.md) define the remaining gates.
No new metric, checkpoint, incident result or cleanup result is claimed here.

## Environment and resources

Use a dedicated, owner-controlled experiment checkout of the final reviewed
source commit. The latest accepted software checkpoint is merged PR 211 at
main commit `c84b9f49c0938e2b95607c3fb72f4cec402b4d85`, tree
`6fd925be5df6db0d5ba80ac4aab9f29871e1d527`, after accepted pricing PR 209 and
FAQ layout PR 210.

The original unapproved execution candidate remains pinned to merged PR 207 at
`f13374f`. The software checkpoint does not repin research or authorize
execution. Any future execution requires a new reviewed execution-source freeze
with a full SHA and immutable source inventory, plus a separate execution
permit. This proposal remains unapproved.
Do not execute from a dirty development checkout or silently accept source
drift against an old protocol.

The original A-F plan is incomplete for G6. Stage B supplies G8 evidence only;
completing that pilot cannot close G6. A revised proposal must define a
separately reviewed G6 stage and execution permit, with its incident population,
finite budget, raw workflow evidence, and independent resolution acceptance.
This notice grants no execution authority and does not change the preserved plan.

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
