# Stage 9: Correct and Train Online GRPO (Gate G9)

**Status: REOPENED**

The policy, environment, reward, provenance, and evaluator software contracts are
implemented. No real GRPO training run, completed adapter, or empirical evaluation is
currently preserved.

**Training admission is blocked for a confirmed observation-order defect.**

The [disconnected software candidate](G9_OBSERVATION_FIRST_SOFTWARE_CANDIDATE_V1.md)
tests the observation-before-generation hook with injected model-free
boundaries. It is not wired into production training and does not remove
this admission block or approve the prospective protocol.
The preserved trainer generates completions from static catalogue alerts
before its reward callback applies the fault and captures the real alert.
Those completions are not conditioned on the observation used for execution.
The [observation-first repair proposal](G9_OBSERVATION_FIRST_PROTOCOL_V1_PROPOSAL.md)
is prospective and unapproved. The CLI and direct training entrypoint refuse
work before model/checkpoint/output/cluster access; unit tests of the legacy
body are software evidence only, not an executable training path.

SFT-parent admission verifies both the checkpoint byte inventory and the
consumed adapter configuration against recorded LoRA/base-model settings.
Hash-matching but semantically inconsistent configs fail before any model
loader; missing settings are not inferred. This is software admission, not
evidence that an adapter has been independently loaded or trained.
The saved GRPO adapter is checked against that same parent LoRA contract
before evaluation loads it. The parent manifest snapshot must still match
its validated digest; a hash-matching but inconsistent GRPO config remains
rejected. These checks do not prove tensor compatibility or eliminate a
replacement window after the final path-based validation.

## Policy-Environment-Reward Contract

The required relationship is:

`state -> trained policy -> one structured action -> tool execution -> settling -> verifier -> reward -> next state`

- Train-only prompts carry their exact frozen `scenario_id` into the reward callback.
- The generated completion is parsed as exactly one action. The same tool and arguments
  are policy checked and executed; no second operational model is invoked.
  Duplicate object keys at any depth, non-finite JSON numbers and excessive
  nesting are rejected before policy, tool or verifier callbacks. Valid nested
  JSON values remain supported; these checks do not grant action authorization.
- `agent_claimed_resolved` is a required JSON Boolean in that completion.
  Missing or malformed claims block the action before dispatch or reward;
  they are not silently interpreted as `false`.
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
- Persisted training rollout rows retain `lifecycle_observations` for the
  zero-Chaos preflight, Chaos apply return, alert query, and scenario cleanup
  return or exception. Each called stage has a separate UTC host-observed
  timestamp; uncalled stages remain explicit. A false or raised preflight
  records a null-reward failure without persisting the unused policy completion
  or exception message. These
  API/query outcomes are not independent fault authorization, observed fault,
  delivered alert time, or objective recovery. No raw alert payload is added
  to this trail, and the ledger still requires an explicit rollout log path.
- Verifier `env_resolved` controls resolution, reward, and curriculum state. A policy
  self-claim cannot establish success.
- A missing, synthetic, mismatched, or ambiguous observed alert cannot be
  paired with the selected Train scenario for policy execution or reward.
  The failed rollout retains a sanitized alert-query classification and
  null reward; scenario-scoped cleanup still runs. A matching alert is a
  necessary identity check, not independent proof of fault causation.
  Failed fault application is likewise unscorable, not a zero-valued policy
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
  direct retry or implicit resume. On supported hosts, caught direct/CLI
  failures and interrupts retain terminal status. Positive Optuna trials
  are deferred before model, output or live work.
- Direct training compares its seed, requested hyperparameters, live
  context, approval profile, and model lineage with the persisted plan
  before beginning work. A mismatched preflight call does not terminalize
  an already-started run. Direct Optuna refuses positive trials rather
  than selecting unapproved settings.
- Completion also requires matching loader-exposed base-model commit
  provenance. A tokenizer hash, if exposed, must match; otherwise its
  manifest basis explicitly says the full loader-argument pin was not
  independently returned. Neither record establishes served-model identity.
- Safely persisted failed and interrupted manifests leave `checkpoint` null.
  On POSIX hosts with supported stable directory handles, they inventory only
  extant, regular, single-link rollout-ledger and training-summary files by
  relative path, byte size and SHA-256, with a 64 MiB total and a deadline
  checked between bounded reads. Missing, redirected, changed, oversized or
  timed-out files remain explicitly unverified. The manifest write uses the
  same pinned directory, whose device and inode are bound at the first planned
  or running status and checked again before a failed/interrupted transition.
  A replacement directory or failure to write through the handle aborts
  persistence instead of falling back to a mutable pathname. On Windows the
  portable runtime cannot pin that directory identity, so the failed or
  interrupted transition is not persisted: the prior running manifest and
  raw files remain, with no partial-file hash claim. Raw content stays out of
  the manifest. The live training CLI rejects hosts without stable
  directory-handle support before creating run output or applying a fault;
  direct training applies the same guard. Positive Optuna requests are
  refused before reaching that capability boundary or any live work.
  None of this makes a failed run resumable or claimable.
- A `completed` manifest requires a training summary with an actual positive
  integer `total_steps` and a `trainer_log_history` list before checkpoint
  inventory acceptance. This is structural evidence of optimizer progress,
  not a quality threshold, a matched run identity, or proof of a usable model.
  Failed and interrupted records remain separate negative evidence.
- Training requires a completed G7 SFT adapter. The trainer validates its full file
  inventory and clean source, checks the declared base model and tokenizer revisions,
  then loads the SFT adapter as trainable. The G9 manifest records the SFT parent
  checkpoint and manifest hashes, which are revalidated before G9 evaluation.
- The single-writer Train prompt source is map-style so repeated generation-group
  indices use one scenario until verifier feedback updates the curriculum. The
  next consumed group can then sample from the updated Train-only distribution;
  local tests cover that cache boundary, not an actual TRL training step.
  The pinned TRL 0.19.1 generation batch must be divisible by
  `num_generations`. The checked-in `batch_size=1`, `grad_accum=4`,
  `num_generations=8` default fails that preflight before output or model load.
  Optuna's fixed one-by-one trial batches with four or eight generations are
  explicitly deferred pending an approved compatible budget. No parameters
  are silently replaced, and neither path establishes G9 training readiness.

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
