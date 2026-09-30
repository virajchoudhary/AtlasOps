# G4 prospective v3.6 settling deadline

**Status: NON-LIVE SOFTWARE CANDIDATE / NOT AN APPROVED EXPERIMENT /
G4 NOT_PASSED.** This revision follows the prospective
[v3.5 causal-evidence candidate](G4_PROTOCOL_V35_CAUSAL_EVIDENCE.md).
It does not amend frozen v3.3, the v3.4/v3.5 declarations, or historical
attempt evidence. Attempt 015 remains unreserved. No operator approval,
fault injection, model inference, cluster mutation, or live readiness is
authorized by this document or the software tests.

Marker: `G4-RECOVERY-V3.6-2026-09-30`
(`g4-recovery-profile-v3.6`). Prospective profile fingerprint:
`eedcc9e09d30700c7d53d206398bf32ff8d7dee10ce4c90a96d6c60686a9b97a`.
The v3.5 fingerprint remains
`01467df37336b2cd3fb9c93148c782a1539c358f09d6f7e68c29ab7ca6fe7cd9`.

## Deadline and evidence

- The 30-second settling deadline is monotonic. Each synchronous verifier
  observation is accepted only if it returns at or before that deadline.
  A positive verifier result returned later is recorded but cannot settle.
  Polling does not start another observation after the deadline.
- Persist actual elapsed and total duration without rounding an overrun
  into the allowed window. A settled report must explicitly have
  `timed_out: false`; every observation must have a finite, nonnegative,
  ordered elapsed time no greater than the reported duration or timeout.
  The reported timeout and poll interval must equal the pinned 30/2
  profile values; a report cannot extend its own budget. Total duration
  must be finite and no greater than that timeout.
- The separate authoritative final verifier, approval gate, causal
  criteria, and objective environment resolution remain required. A
  passing settling observation alone cannot establish G4 recovery.
  The verifier call remains synchronous, so this deadline governs
  acceptance rather than forcibly interrupting a blocked verifier.

The active prospective software profile pins the LF-normalized coordinator
source at
`5d7be471592526fbf519fba571c2a1d2ccfd1d976f027736d00f2b3b39aadd0c`
and Stage 4 runner source at
`24b4e51c0173fbe0c85c58f871a56f0c1ff52dde8ea3b1e85809ef6f4a226f09`
and declares the deadline terms and constants. A different source or
runtime profile fails exact qualification. This fingerprint is a static
contract, not live evidence of model, operator, telemetry, cleanup, or
cluster readiness.

## Acceptance boundary

Mocked-clock regressions cover in-budget success, a successful verifier
that returns just after the deadline, expired polling, and forged timing
reports. Protocol tests retain exact v3.3-v3.5 fingerprints. These tests
cannot close the prior G4 negative and interrupted outcomes. A future live
attempt still requires a separately approved protocol and execution
decision, fresh pre-T0 capacity/model/operator and zero-Chaos checks,
an unused attempt ledger, and independent evidence review.

The separate G7 `train-candidate-v1` manifest pins the pre-v3.6 coordinator
source. Its frozen bytes and historical quality audit are preserved, but
current-source admission now rejects it. A new versioned candidate and
independent D3 review are required before any future training; the v1
manifest must not be silently rehashed.
