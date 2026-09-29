# Stage 9: Correct and Train Online GRPO (Gate G9)

**Status: REOPENED**

The policy, environment, reward, provenance, and evaluator software contracts are
implemented. No real GRPO training run, completed adapter, or empirical evaluation is
currently preserved.

## Policy-Environment-Reward Contract

The required relationship is:

`state -> trained policy -> one structured action -> tool execution -> settling -> verifier -> reward -> next state`

- Train-only prompts carry their exact frozen `scenario_id` into the reward callback.
- The generated completion is parsed as exactly one action. The same tool and arguments
  are policy checked and executed; no second operational model is invoked.
- P1 requires explicit approval. The coordinator-backed `rl_policy` path requests
  approval for the exact parsed tool and arguments, then consumes a one-use
  incident/action-bound permit. Standalone training and evaluation now have a
  prospective [host-local operator channel](G9_PROTOCOL_STANDALONE_P1_APPROVAL.md)
  behind a separate `--enable-p1-approval` opt-in and explicit environment key.
  Its synthetic process-boundary tests are not live operator evidence. Without
  that opt-in, P1 mutation stays blocked. Evaluation treats untrusted
  public-state severity as P1 for mutation. P0 remains manual; unknown alert
  severity cannot enter the automatic P2 path. P2/P3 in training follow the
  configured policy only with authoritative alert severity.
- Rollback and Chaos stop require matching positive live history/resource observations
  immediately before the mutation.
- The environment executes at most one mutation before settling and verifier observation.
- A serialized training rollout requires a verified zero-Chaos preflight. Cleanup targets
  only its selected scenario manifest and must verify zero active Chaos resources before
  another rollout starts. Failed attempts remain in the raw rollout ledger.
  If cleanup fails after an action/verifier result exists, the failure row
  retains that result and its action/settling evidence while marking the
  attempt unscorable with null reward. The batch aborts; retained evidence
  cannot be scored as a resolved episode or retroactively prove cleanup.
- Verifier `env_resolved` controls resolution, reward, and curriculum state. A policy
  self-claim cannot establish success.
- A missing real alert or failed fault application yields an unscorable failed
  rollout with null reward and stops training; it is not a zero-valued policy
  outcome.
- The prospective direct-action reward uses only conclusive objective verifier
  checks: `0.75` for verified resolution plus `0.25` times required-check
  coverage, minus `0.25` for a false resolution claim. Multi-step evaluation
  averages recorded step totals. Unscorable observations have no numeric
  reward and cannot establish a completed empirical checkpoint. The older
  four-agent 70/30 contract/dense blend is not the direct-action scorer; absent
  role summaries or judge fields are never invented to make that blend run.
- A verifier check's `required` flag defaults to `true` only when omitted.
  Present values must be actual Booleans. `false` denotes an optional check;
  malformed null, string, or numeric flags make the environment observation
  unscorable and are rejected by the scorer before calculating coverage.
- Planned, running, completed, failed, and interrupted manifests bind model/tokenizer
  revisions, the full Train split hash plus selected prompt/scenario hashes, source
  state, seed, configuration, rollout ledger, trainer history, and checkpoint hashes.
- Non-live preflight requires full immutable model/tokenizer commit pins and a
  completed matching SFT parent before exclusively claiming a fresh,
  nonredirected output directory. A one-shot execution marker prevents
  direct retry or implicit resume; caught direct/CLI failures and interrupts
  retain terminal status. Optional Optuna trials carry the declared seed
  but remain separately gated live rollouts.
- Direct training and Optuna entrypoints compare their seed, requested
  hyperparameters or trial count, live context, approval profile, and model
  lineage with the persisted plan before beginning work. A mismatched
  preflight call does not terminalize an already-started run.
- Completion also requires matching loader-exposed base-model commit
  provenance. A tokenizer hash, if exposed, must match; otherwise its
  manifest basis explicitly says the full loader-argument pin was not
  independently returned. Neither record establishes served-model identity.
- A `completed` manifest requires a training summary with an actual positive
  integer `total_steps` and a `trainer_log_history` list before checkpoint
  inventory acceptance. This is structural evidence of optimizer progress,
  not a quality threshold, a matched run identity, or proof of a usable model.
  Failed and interrupted records remain separate negative evidence.
- Training requires a completed G7 SFT adapter. The trainer validates its full file
  inventory and clean source, checks the declared base model and tokenizer revisions,
  then loads the SFT adapter as trainable. The G9 manifest records the SFT parent
  checkpoint and manifest hashes, which are revalidated before G9 evaluation.

## Evaluator Contract

Empirical evaluation requires a completed real-environment GRPO checkpoint and validates
the entire checkpoint inventory and SFT parent before model loading. It preserves public state, raw policy
output, parsed and executed action, actual tool result, verifier result, reward decomposition,
next state, timestamps, and failures. Benchmark truth is rejected from policy input.
Empirical G9 development evaluation is Validation-only. Test and the overlapping
Leaderboard are rejected by the API and CLI before live context, split,
checkpoint, state, inference, or output access. A future final comparison
requires a separately reviewed protocol and explicit final-Test authorization;
this entrypoint has no bypass.
The empirical path requires the built-in tool/verifier adapter and a failed, reachable
pre-action verifier reading for the active fault. Injected environment adapters cannot
produce claim-eligible empirical summaries.
An unresolved bounded trajectory is a measured negative outcome; it does not require
a fabricated time-to-resolve value.

The deterministic compatibility evaluator remains available for tests and is always marked
`NON_EMPIRICAL`; it cannot update the shared comparison table.

## Historical Outputs

The Stage 9 files under `artifacts/evidence/mock_archive/stage9/` and the old comparison
tables were generated by deterministic mock logic. Their resolution, TTR, and reward values
are not trained-policy measurements.

## Current Verification

Local tests cover direct action execution, approval and policy blocking, the
prospective host-local callback, verifier authority,
settling order, resolved termination, Train/Val/Test isolation, checkpoint integrity, raw
trajectory preservation, and mock isolation. Real training requires an approved model,
the ML dependency stack, a reviewed operator protocol, and a controlled live
environment with serialized mutations. No standalone positive P1 decision has
been observed in a real experiment.

**Gate G9 Status: REOPENED**
