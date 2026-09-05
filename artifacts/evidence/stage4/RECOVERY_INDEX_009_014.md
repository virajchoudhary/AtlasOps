# Recovery index: Stage 4 attempts 009-014

SETUP-03 preserved these pre-existing files from source HEAD
`5b4922660c1acc56dd4d2cb1d65f88b87cb3ed7f` on 2026-09-05.
This is a historical evidence index, not a new experiment or gate decision.

| Attempt | Recorded outcome | Cleanup record |
|---|---|---|
| 009 | Interrupted/inconclusive; host/inference connection failure | Successful cleanup after interruption |
| 010 | Latest completed negative result among 009-014; G4 failed | Successful cleanup after verdict persistence |
| 011 | Interrupted/inconclusive; model timeout | Successful cleanup after interruption |
| 012 | Interrupted/inconclusive; no authoritative persisted verifier verdict | Cleanup failed; unresolved |
| 013 | Interrupted/inconclusive; no authoritative persisted verifier verdict | Cleanup failed; unresolved |
| 014 | Interrupted/inconclusive; no authoritative persisted verifier verdict | Cleanup failed; unresolved |

None of attempts 009-014 passed G4. In 010, diagnosis/remediation targeted
adservice rather than the frozen paymentservice target, remediation calls failed,
and the objective verifier recorded `env_resolved=false` and
`agent_claimed_resolved=false`. Its approval `timeout` and
`8_approval_satisfied=true` are an unresolved inconsistency, not approval proof.

Successful cleanup after an interruption is not successful agent remediation.
Cleanup after verifier persistence cannot retroactively make G4 pass.
The 012-014 cleanup records themselves report failure (TLS handshake timeout);
this recovery task did not retry cleanup or establish current cluster state.
Missing/interrupted verifier results must not be converted into a resolution claim.
Environment verification remains authoritative; model output is a proposal.

The raw JSON/YAML files were not edited or normalized. Original and final paths,
byte sizes, and SHA-256 values are recorded in the
[workspace recovery provenance](../recovery/2026-09-05-workspace-recovery.json).
Scoped Git attributes preserve their exact bytes through staging and checkout.
Ignored attempt-lifecycle markers and external raw logs remain in their existing
locations and are not included in this commit.

Existing broader project PASS assertions were not reconciled by SETUP-03 and
must not override the negative/inconclusive evidence indexed here.
