# G9 observation-first software candidate v1

**NON_EMPIRICAL / NOT_CERTIFIED / DISCONNECTED. G9 REOPENED.**

This candidate implements the model-free trainer boundary described by the
[prospective protocol](G9_OBSERVATION_FIRST_PROTOCOL_V1_PROPOSAL.md). It does
not approve that protocol, connect live callbacks, remove the production
training block, or authorize weights, training, faults, P1 requests, Test
access, compute spend or deployment. The starting source is main
`5e9fca423b5cdcc57b7ecd74c2ad33902f510a2f`. Historical evidence and frozen
protocols are unchanged.

## Implemented boundary

`training.grpo_observation_first.ObservationFirstGRPOMixin` wraps pinned
TRL 0.19.1's `_generate_and_score_completions`. Its caller must inject
synchronous `begin`, `before_action`, `execute` and `finish` callbacks.
There are no default environment callbacks or connections to the production
`OnlineRewardFunction` or `run_training`.

Before generation, the hook requires the model's explicit boolean training
mode. Evaluation mode, missing mode and non-boolean mode values fail before
any lifecycle callback or evidence creation; evaluation must not accidentally
reuse the Train lifecycle. The hook verifies frozen Train membership and requires
exactly one complete repeated-sample group in the call. Multiple groups and
mixed scenarios are refused by this candidate. This is an implementation
limitation, not a newly approved project generation budget or repetition rule.
Only alert, incident identity, triage and public observations are admitted,
with the fixed action instruction. Existing recursive truth/authorization
rejection and a 16384-byte canonical UTF-8 limit apply; non-finite JSON fails.

The hook replaces catalogue text with that canonical observation and carries
scenario, digest, prompt hash, one-shot group binding and sample index through
TRL's reward columns. `max_prompt_length` must be `None`, so the candidate
does not silently truncate its observation. It owns the sole reward callback.
All completion strings are parsed before any action. Each execution needs a
fresh, matching snapshot with a timestamp no earlier than generation's
observation. The callback result must identify the exact completion, parsed
action, scenario and one executed action. The existing objective reward
function supplies numeric reward only for a scorable result.

The negative-evidence follow-up preserves a sample attempt before pre-action
admission. Exceptions, malformed state/timestamps and snapshot drift retain
the sample and failure phase without claiming execution. A correctly bound
environment block with no executed actions is distinct from an action-lineage
mismatch: it retains its bounded terminal category and, where supplied, the
allowlisted approval decision. Rejected and timed-out approvals do not become
the same cause. Operator identity, token, free-text reasons and arbitrary
result payloads are not exported. Blocked or invalid samples abort the group
with no numeric reward; earlier sample records remain visible, but a partial
group is not accepted as completed training evidence.

The `finish` callback runs after attempted setup, including setup failures,
generation failures and cancellation. Local defensive-copy records retain
failure/interruption and callback return/error states. A callback returning
does not prove cleanup or zero Chaos. Cleanup failure cannot leave a completed
group record. These in-memory records are not crash-durable recovery evidence.

## Verification scope

`tests/test_grpo_observation_first.py` exercises the injected boundary,
observation/action binding, state restrictions, failure and cleanup behavior.
`tests/test_grpo_observation_trl_routing.py` executes adapted pinned TRL
`_prepare_inputs` and `_calculate_rewards` methods with generation, tensors
and environment boundaries stubbed. It checks pre-generation observation,
reward-column order, buffered reuse without repeated environment execution,
fresh generation after an empty buffer and refusal of the evaluation routing
branch before observation. No Validation or Test evaluation is implemented
by this candidate.

The fixture cites upstream Git blob
`bc04493af12dc7b07bc5a9741fd34ba004922326` and retains TRL's Apache-2.0
license. These are source-routing and synthetic software checks, not a
real TRL model/optimizer/tokenizer run, verified gradient update, real
checkpoint, environmental recovery or empirical G9 PASS.

### Installed TRL contract

The separate `tests/test_grpo_observation_installed_trl.py` exercises the
actual installed TRL 0.19.1 trainer and factory with a tiny randomly
initialized CPU model and a locally constructed synthetic tokenizer.
Only the model-generation boundary supplies predetermined action tokens;
the parent TRL generation hook is not replaced with the source excerpt.
The test can verify construction, observation tokenization, reward-column
binding and lifecycle handling without a pretrained model or live incident.

The `Pinned TRL CPU contract` CI job installs the pinned trainer stack with
CPU Torch 2.7.1 and requires this test module to run; missing/incompatible
dependencies cannot become a successful skip there. The CI-specific
`ATLASOPS_REQUIRE_PINNED_CPU_STACK` check also verifies Torch, Datasets and
Tokenizers versions at runtime. The ordinary unit jobs
may skip the optional installed-stack module. Hugging Face model/dataset
networking is disabled. The test sets `model.train()` mode but never invokes
the trainer's `train()` loop, `backward()`, an
optimizer step or checkpoint saving. Local runs with another Torch version
are supplementary and do not attest the pinned CPU or NVIDIA runtime.

This is an installed-library integration check, not Qwen tokenizer/model
compatibility, trained-policy learning, full GRPO optimizer correctness,
GPU/BF16/4-bit feasibility or approval of live reset/repetition semantics.
No synthetic action/reward is promoted to an empirical training record.

## Remaining work before production integration

- Review and approve the prospective observation-first protocol, including
  generation-group reset/repetition and independent equivalent-state rules.
- Connect separately governed live lifecycle callbacks with observed
  preflight, actual approval, objective settling and scenario-scoped cleanup.
- Verify distributed/batch configuration, actual tokenizer/model conditioning,
  optimizer alignment, restart recovery and durable evidence under the pinned
  runtime; this single-group synchronous candidate does not establish them.
- Resolve D4-D7 measurement rules and D12 pre-RL acceptance, then obtain the
  separate D10 execution permit and approved model/checkpoint/host resources.

Strict snapshot drift rejection is a fail-closed candidate behavior, not an
approved physical reset mechanism. Matching JSON before each action does not
establish that independent live rollouts reproduced the same environment.
No scientific decision or existing scorer formula changes in this candidate.
