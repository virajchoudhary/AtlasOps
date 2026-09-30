# G9 observation-first learning protocol v1 proposal

**PROPOSED / NOT APPROVED / NON-EXECUTABLE. G9 REOPENED.**

This prospective repair preserves the historical GRPO source, frozen G4
protocols and all experiment records. It does not authorize model loading,
training, fault injection, operator/P1 requests, Test access, compute spend or
deployment. Review basis: `f6d6dbef281f0a1673f41b957112c50e58649464`.

## Confirmed ordering defect

`training/grpo.py` builds Train prompts from catalogue alert names before
`GRPOTrainer` generates completions. Its reward callback then performs
zero-Chaos preflight, applies the selected fault and reads the actual alert.
That alert is used to execute and verify a completion generated without it.

Pinned TRL 0.19.1 calls `_generate_and_score_completions` before
`_calculate_rewards`; dataset columns reach the reward callback, but cannot
retroactively change the model's conditioning input. Correct prompt/scenario
association and exact action execution do not repair this temporal mismatch.
Static catalogue input must not be described as live-observation-conditioned
learning, even if a later environment verifier reports success.

## Proposed replacement contract

1. Admit the exact approved Train scenario, source/model/tokenizer/checkpoint
   identities and resource/operator protocol before any work.
2. Establish zero active Chaos, apply the authorized fault and capture a
   scenario-matched observed alert plus the permitted public evidence.
3. Sanitize one bounded, immutable policy-state snapshot before generation.
   Exclude scoring truth, expected diagnosis/remediation, verifier predicates,
   reward labels and caller-supplied approval. Bind its canonical digest to
   the generation request, exact scenario identity and observation timestamps.
4. Generate the policy completion from that snapshot. The completion delivered
   to the safety gate must be the same completion used for policy optimization;
   no second model may replace the proposed action.
5. Validate and, only with the separate required authorization, execute the
   exact action. Preserve any fresh host evidence/preconditions and operator
   decision separately from the earlier policy snapshot.
6. Settle and record objective verification, failure, next state and cleanup.
   Return numeric reward only where the approved reward contract permits it.
7. Prove scenario-scoped cleanup and zero active Chaos before the next
   mutation. Preserve negative, blocked, interrupted and unscorable records.

The generation group requires an explicit reviewed reset/repetition rule:
one shared initial observation cannot justify claiming each repeated fault
reproduced the same state. The future implementation must bind every completion
to its actual conditioning snapshot and preserve state drift between serialized
executions. Neither repeating a static prompt nor silently generating a new
completion in the reward callback satisfies this requirement.

## Required non-live implementation proof

Use a model-free, injected lifecycle test at the trainer/environment boundary.
Capture the actual input delivered to generation and compare its digest and
public fields with the admitted observation. Verify ordering, group membership,
exact completion/action lineage, fresh preconditions, failure retention,
cancellation and cleanup. A static catalogue-only input must be rejected as a
claim of observation-conditioned execution. Missing or changed bindings must
fail before mutation or reward.

The integration must be checked against the pinned TRL generation/group reuse
behavior, not only a fake reward callback. No model weights or cluster are
needed for this software proof. Real behavior still needs separate later
hardware/model/cluster approval and independent empirical verification.

## Admission and open decisions

The existing training CLI and direct `run_training` entrypoint are blocked
before model, checkpoint, output or cluster work. Unit tests may inspect the
preserved legacy body with an explicit test-only stub; that is not a production
override or execution authority. There is no environment variable or CLI
switch that can approve this ordering.

The replacement needs a reviewed trainer hook and generation-group lifecycle,
public-state schema and size bounds, drift policy, interruption recovery and
an execution permit. D4-D7 measurement decisions, D10 live authorization and
D12 pre-RL acceptance remain pending. This proposal changes no scorer formula
and selects no missing scientific rule. A completed software replacement and
reviewed protocol are required before removing the admission block; removal
alone would not authorize training or close G9.
