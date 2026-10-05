# Stage 12: Integrate GAI + RL (Gate G12)

**Status: IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**

The required path follows the later
[GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md):

`Alert -> Triage -> Diagnosis -> observed evidence -> Approval/Safety -> RL policy -> one action -> tool result -> settling -> verifier -> next state or Comms`

## Runtime Contract

- Original incident identity and alert anchors remain present throughout the flow.
- Benchmark truth is stripped from model and policy input.
- Policy generation and the approval provider receive independent copies of
  the sanitized state. Generation settings are also copied per call. Mutating
  a callback input cannot downgrade the authoritative severity, replace the
  incident identity, alter later generation settings or rewrite captured
  pre-action state. This protects the object boundary; it does not authenticate
  an injected callback or establish live P1 approval.
- The historical recommender is an explicit optional advisory path, not a
  prerequisite. Disabled or missing recommendations do not become remediation
  truth, an approval decision, or an invalid GAI + RL capture.
  `ATLASOPS_RECOMMENDER_ENABLED=1` explicitly enables it; the default is off.
  The incident record retains `disabled`, `unavailable`, or `executed` status
  and a compatibility `recommended_runbooks` list.
- `ATLASOPS_REMEDIATION_BACKEND=rl_policy` selects the policy path and requires
  `ATLASOPS_RL_POLICY_CHECKPOINT` for checkpoint-backed use. Both injected
  and checkpoint-backed live policy paths require
  `ATLASOPS_RL_POLICY_EXECUTE_ACTIONS=1` and an explicit
  `KUBECONFIG_CONTEXT` before incident work or tool dispatch; there is no
  operational-model fallback. An injected policy remains non-empirical model
  evidence even when its controlled environment action was explicitly authorized.
- Each policy completion becomes the exact action sent through ACL, evidence preconditions,
  approval, one tool execution, settling, and objective verification.
  Capture reuses that runtime parser: malformed claims, duplicate keys,
  non-finite numbers and excessive nesting remain invalid actions. Correctly
  blocked negative records preserve raw text for review; an allegedly executed
  action with invalid raw JSON makes the capture incomplete, not certified.
- Policy steps retain the environment's projected `pre_action_observation` for
  guarded rollback or Chaos-stop support reads, including the reader name,
  success and observed-status fields, and target history or active resources.
  Unguarded steps and blocks before a support read record null. A guarded
  reader that returns an object but fails its target or status precondition
  remains blocked, retains the projected negative read, and makes G12 capture
  `INCOMPLETE` for independent review. This is not the failed objective
  pre-action verifier needed for G13 episode admission.
- An unresolved verifier result is included in the next policy state.
- A resolved verifier result terminates further mutations.
- Comms receives the verified resolution state.
- Injected policy tests are labeled `NON_EMPIRICAL`.

The default agent remediation backend remains available for the existing runtime. An
opted-in recommender remains advisory and its failure is recorded rather than
treated as approval or ground truth.

## Current Verification

Local integration tests cover identity preservation, hidden benchmark fields, optional
advisory recommendations, exact action execution, approval and ACL ordering, one mutation per
verification step, next-state feedback, resolved termination, Comms state, and provenance.

A real end-to-end run still requires a valid G9 checkpoint, a healthy controlled environment,
and legitimate approval for any P1 mutation.

## Governed Real Evidence Capture

`scripts/run_g12_integrated_episode.py` is a thin wrapper around the Stage 4
golden-incident harness. It does not implement another fault injector or cleanup
path. It requires a clean disposable checkout, a provenance-validated completed
GRPO checkpoint, a new `EXP-STAGE4-*` ID, an external new bundle directory, and
the explicit `--execute-live-chaos` flag plus
`--kube-context kind-atlasops-local`. The wrapper validates these before
creating a bundle and passes the live-policy opt-in to the coordinator. The
Stage 4 harness must still pass its
own source, model, telemetry, baseline, zero-Chaos, approval, and postflight
controls. Do not run it unattended or use this flag as a substitute for
experiment-specific authorization.

After the harness returns, the wrapper copies the available raw Stage 4 attempt,
pre-fault failure record, and coordinator trajectory, including negative or
interrupted evidence when present,
and records their SHA-256 hashes, exact source SHA, checkpoint provenance, seed,
model identity, policy origin, and missing fields. It revalidates the checkpoint
inventory after the run and marks a changed or unavailable checkpoint incomplete.
The requested policy seed and recorded
`remediation.final.generation_seed` must be nonnegative exact integers
and match. A malformed request or missing, malformed, or mismatched
observation makes capture `INCOMPLETE` rather than presenting the
request as execution provenance. Malformed requested seeds are null in
the manifest rather than serialized as nonstandard JSON numbers.
It leaves reward and TTR
unevaluated and always sets `empirical_claim_allowed=false` and
`gate_certification=NOT_CERTIFIED`; a capture requires independent review before
any G12 empirical claim. An incomplete attempt remains visible rather than
becoming a mock success.

`CAPTURED_FOR_REVIEW` requires every policy step to contain its ordered index,
direct environment status (`ok`, `blocked`, or `unscorable`), timezone-aware
start and completion timestamps, the separate nullable support-read
`pre_action_observation`, and pre-action and next state
objects with matching action, tool-result, verifier and resolution feedback.
For an executed guarded rollback or Chaos-stop action, capture checks the
projected reader, successful return, and matching target; Chaos-stop also
requires the observed-status value returned by its precondition read.
Rollback's current history reader may omit that status, but an explicit
non-observed value blocks the runtime action and invalidates capture.
This structural check does not independently authenticate that tool response
or turn it into fault authorization, alert delivery, or an objective
pre-action failed verifier. The external raw bundle must be access-controlled;
projected nested history/resource entries are retained for review.
Both states retain the incident ID, alert, and incident anchors, and their alert
and anchors must match the source incident. The final executed-action list must
match the ordered policy-step actions and results. A deadline
observation may omit resolution and failed-check fields, but an ordinary
conclusive observation must retain them.
Adjacent steps must form a state chain with non-overlapping chronology; no
step may start from an already resolved state or follow a recorded resolution,
terminal block, or unscorable/error settlement. The primary Stage 4 anchor copy
must also match the trajectory and alert-derived anchors. The final remediation
status must match the last step. The recorded environment status must agree with
the terminal block or explicit settlement failure; any non-`ok` status stops the
policy sequence. Verifier status, resolution, required checks and failed-check
names must agree; any verifier
nested in settlement is checked against the step verifier. Executed steps retain
exactly one tool action with its result and structured settlement observations
or an explicit timeout/error/unscorable record. A blocked step requires a recognized
terminal-block category and reason, no executed action, and null verification
and settlement. A blocked step that retains an incompatible guarded support
read remains visible in the raw bundle but is not `CAPTURED_FOR_REVIEW`.
Failed or unresolved results without such a contradiction do not prevent capture.

The Stage 4 cleanup sidecar is required and checked for schema, experiment
identity, and consistency of the raw postflight Chaos item count with the
recorded count and zero-Chaos verdict. A recorded cleanup failure and
poisoned-environment state are valid
negative evidence, not a reason to discard the bundle or a cleanup PASS. Missing
or malformed cleanup evidence, step data, or state relationships keeps the
bundle `INCOMPLETE`; copied raw artifacts and their hashes remain available
either way. An unscorable step may preserve a last passed verifier while
remaining unresolved when its settlement failure is explicit. Review capture is
not evidence of remediation success, gate closure, or empirical certification.

No real G12 episode was executed in this implementation pass. The local Kind
API and Docker Linux engine were unavailable, and no completed GRPO checkpoint
was supplied. The existing control-flow tests and new capture tests are
non-live software evidence only.

## Finalization Boundary (5 October 2026)

The non-experimental integration, contract tests, capture tooling and
documentation are complete. The genuine v17 parent exists, but the final
controlled G9 result produced no acceptable SFT+GRPO checkpoint. A real
integrated policy/environment episode therefore remains deferred empirical
work. The historical host-unavailability observations above are dated facts,
not a current cluster-health check or an instruction to start services.
See [the deferred handoff](DEFERRED_RESEARCH_HANDOFF.md).

**Gate G12 Status: IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**
