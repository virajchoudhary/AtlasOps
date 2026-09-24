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

**Gate G12 Status: IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**
