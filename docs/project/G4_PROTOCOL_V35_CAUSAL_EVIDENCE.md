# G4 prospective v3.5 causal evidence and Chaos activity

**Status: NON-LIVE SOFTWARE CANDIDATE / NOT AN APPROVED EXPERIMENT /
G4 NOT_PASSED.** This revision follows the prospective
[v3.4 host approval and pre-T0 controls](G4_PROTOCOL_V34_APPROVAL_CHANNEL.md).
It leaves the frozen v3.3 profile, v3.4 declaration, attempt accounting, and
all historical raw evidence unchanged. It does not reserve attempt 015,
request P1 approval, inject a fault, or establish live readiness.

Marker: `G4-RECOVERY-V3.5-2026-09-29`
(`g4-recovery-profile-v3.5`). Declared profile fingerprint:
`272ff5da77470598f071e9b896774faf5678ad0efe6dce3b7355441ada034ea7`.
The historical v3.4 profile fingerprint remains
`885349a5083509d43ef5366d6157367e33a986fb0022c4c71eb8c4fa02ea10a3`;
v3.3 and earlier fingerprints are unchanged.

## Prospective changes

- `chaos_list_experiments` preserves all supported observed resources in
  `inventory`, while `active_experiments` contains only positively active
  states. It checks the pinned Chaos Mesh 2.8.3 `containerRecords` and
  `Paused`/`AllRecovered` conditions, as well as a global phase when supplied.
  Finished or paused resources cannot authorize a stop. An unknown target,
  malformed read or unavailable state cannot authorize a stop. A positively
  active target may still be stopped if another resource is unclassified;
  that other resource is not called clean or active. The Stage 4 pre-reservation
  zero-Chaos check independently requires an empty raw cluster-wide list,
  not merely this filtered active list.
- The diagnosis grounding report still preserves and matches cited
  executions without rewriting model output. It separately counts exact-query
  PromQL citations backed by a successful, schema-valid nonempty finite sample.
  A failed query, valid empty result, malformed or contradictory result, or
  ambiguous repeated observation contributes no positive metric sample.
  Tool-specific error classes are checked before labeling a failure; raw
  error text is not copied into the summary. Citation provenance can be
  accurate while metric support is absent.
- For SF002, causal criterion 7 now requires at least one such cited
  nonempty PromQL sample in addition to its existing target/fault anchors,
  matching persisted grounding and no citation violations. A sample is
  **not** threshold attainment or proof of causality by itself. The separate
  pre-trigger degradation and objective post-remediation verifier criteria
  remain mandatory. Criterion 8 still requires the existing strict P1
  approval record, including mode, severity and operator identity.

## Fingerprint boundary

The active prospective profile pins LF-normalized SHA-256 of the triage,
diagnosis, remediation and Comms prompts, plus the Chaos observer,
grounding validator and Stage 4 runner source. It also declares the three
causal-policy terms above. Any changed file or runtime approval/model/tool
profile fails exact qualification until a separately reviewed prospective
declaration is made. This is source drift detection, not proof that a future
cluster, model or human operator behaves as intended.

Synthetic mocked tests exercise active/inactive/unknown Chaos states, malformed
observations, positive/empty/failed metric citations, and the G4 causal
criterion. They cannot establish a real recovery or cleanup. A future G4
attempt requires a clean approved `main` SHA, external secrets, capacity,
model residency and identity, operator channel, baseline/telemetry, zero
Chaos, attempt ledger and poison-latch verification, and a separately
authorized live execution. Until then G4 remains `NOT_PASSED`.
