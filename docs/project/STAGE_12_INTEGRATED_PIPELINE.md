# Stage 12: Integrate GAI + RS + RL (Gate G12)

**Status: IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**

The coordinator supports the intended integrated sequence:

`Alert -> Triage -> Diagnosis -> observed evidence -> Recommender Top-K -> Approval/Safety -> RL policy -> one action -> tool result -> settling -> verifier -> next state or Comms`

## Runtime Contract

- Original incident identity and alert anchors remain present throughout the flow.
- Benchmark truth is stripped from model and policy input.
- The recommender loads a reviewed checkpoint, uses observed query fields, and remains
  advisory. Missing recommendations do not become remediation truth.
- `ATLASOPS_REMEDIATION_BACKEND=rl_policy` selects the policy path and requires
  `ATLASOPS_RL_POLICY_CHECKPOINT`; there is no operational-model fallback.
- Each policy completion becomes the exact action sent through ACL, evidence preconditions,
  approval, one tool execution, settling, and objective verification.
- An unresolved verifier result is included in the next policy state.
- A resolved verifier result terminates further mutations.
- Comms receives the verified resolution state.
- Injected policy tests are labeled `NON_EMPIRICAL`.

The default agent remediation backend remains available for the existing runtime. The
recommender is an advisory enhancement and its failure is recorded rather than treated as
approval or ground truth.

## Current Verification

Local integration tests cover identity preservation, hidden benchmark fields, advisory
recommendations, exact action execution, approval and ACL ordering, one mutation per
verification step, next-state feedback, resolved termination, Comms state, and provenance.

A real end-to-end run still requires a valid G9 checkpoint, a healthy controlled environment,
and legitimate approval for any P1 mutation.

## Governed Real Evidence Capture

`scripts/run_g12_integrated_episode.py` is a thin wrapper around the Stage 4
golden-incident harness. It does not implement another fault injector or cleanup
path. It requires a clean disposable checkout, a provenance-validated completed
GRPO checkpoint, a new `EXP-STAGE4-*` ID, an external new bundle directory, and
the explicit `--execute-live-chaos` flag. The Stage 4 harness must still pass its
own source, model, telemetry, baseline, zero-Chaos, approval, and postflight
controls. Do not run it unattended or use this flag as a substitute for
experiment-specific authorization.

After the harness returns, the wrapper copies the raw Stage 4 attempt and
coordinator trajectory, including negative or interrupted evidence when present,
and records their SHA-256 hashes, exact source SHA, checkpoint provenance, seed,
model identity, policy origin, and missing fields. It revalidates the checkpoint
inventory after the run and marks a changed or unavailable checkpoint incomplete.
It leaves reward and TTR
unevaluated and always sets `empirical_claim_allowed=false` and
`gate_certification=NOT_CERTIFIED`; a capture requires independent review before
any G12 empirical claim. An incomplete attempt remains visible rather than
becoming a mock success.

No real G12 episode was executed in this implementation pass. The local Kind
API and Docker Linux engine were unavailable, and no completed GRPO checkpoint
was supplied. The existing control-flow tests and new capture tests are
non-live software evidence only.

**Gate G12 Status: IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**
