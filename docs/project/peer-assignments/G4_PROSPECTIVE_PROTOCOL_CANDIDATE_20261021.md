# G4 prospective one-episode protocol — CANDIDATE, not approved

**Status:** draft for project-lead review, prepared offline on 21 October 2026.
It reserves no run identity, authorizes nothing and does not reopen G4.
G4 remains **NOT_PASSED / FROZEN**. Every value below is read from source at
`b23d6cb4319b77b4d8606934e401141bcb01d23f` unless marked `UNSPECIFIED`;
`UNSPECIFIED` values must be supplied by the named role before any freeze.

## Identity and roles

| Field | Value |
| --- | --- |
| Frozen source commit | `UNSPECIFIED` — must be a reviewed final commit whose tree reproduces every pin (see the [local progress record](G4_LOCAL_PROGRESS_20261021.md) basis-label finding) |
| Project lead (reopens G4, freezes protocol) | `UNSPECIFIED` |
| Host owner (host, resources, restoration) | `UNSPECIFIED` |
| Operator (exact action, expiry, fault, cleanup) | `UNSPECIFIED` |
| Independent reviewer (recomputes verdict) | `UNSPECIFIED` |
| Host / kube context | proposal: `kind-atlasops-local` (`agents/stage_a.py` fault record); physical host `UNSPECIFIED` |
| Base model, tokenizer, adapter content digests | `UNSPECIFIED` (adapters disabled for all roles per proposal v3) |
| Sampling | seed 1337, greedy, temperature 0, top_p 1, 512 new tokens per generation (proposal v3) |

## Fault

`single_fault/sf-002`, manifest `bench/chaos_manifests/single_fault/sf-002.yaml`,
LF-normalized SHA-256 `23aac11f0faed1dd89b3e2428770483fd34b48a693681f4bea0badb757ff04b5`.
`StressChaos` `sf-002-paymentservice-cpu` in namespace `chaos-mesh`; selector
`namespaces: [default]`, `labelSelectors: {app: paymentservice}`; mode `one`;
stressors `cpu: {workers: 4, load: 90}`; duration `10m` (600 s).
`StageARun.bind_fault` rejects any other kind/name/namespace/spec, a missing
`uid`/`resourceVersion`, a UID differing from the created resource, a non-active
phase, or anything other than exactly one injected container record.

## Ordered episode and evidence

1. **Baseline:** fresh identity-bound CPU observation, every sample's `timestamp`
   and `source_timestamp` within −5…60 s of apply start (`baseline_valid_at`).
2. **Apply:** record apply-start epoch and monotonic clock; bind the create response
   to its exact UID (`bind_created_resource`), then bind the active fault (`bind_fault`).
3. **Degradation:** `DEGRADATION_QUERY` (`max(rate(container_cpu_usage_seconds_total{namespace="default",pod=~"paymentservice-.*",container="server"}[2m]))`)
   observed within 150 s at 3 s polling; 30 s scrape interval.
4. **Alert admission:** actual Alertmanager-originated delivery with fingerprint,
   receiver request, delivery time and resource UID retained. **Not yet implemented
   as proof:** the current runner invokes the alert directly (tracker). Required
   implementation and its review: `UNSPECIFIED` owner.
5. **Model response:** triage severity `p1`; diagnosis root cause grounded in observed
   evidence (`_diagnosis_is_evidence_backed`); every model response persisted.
6. **Approval:** authenticated `/approve` (`X-AtlasOps-Key`, `hmac.compare_digest`);
   token bound to the incident and the SHA-256 of the canonical action JSON; single-use
   permit; 300 s expiry (`STAGE4_APPROVAL_TIMEOUT_SECONDS`). **Known gap:** `approved_by`
   is a free-text label behind one shared key, not a per-operator authenticated
   identity; the tracker requires per-identity binding before a live run.
7. **Action:** only `chaos_stop_experiment` with arguments exactly
   `{"kind": "StressChaos", "name": "sf-002-paymentservice-cpu", "namespace": "chaos-mesh"}`;
   the active fault UID must be independently observed exactly once (`before_mutation`);
   one mutation budget; within 480 s of apply start and before fault expiry.
8. **Recovery (objective, pre-cleanup):** at least two fresh, identity-bound,
   strictly post-action counter samples ≥30 s apart, newest ≤60 s old, within 540 s
   of apply start and before expiry; measured rate < baseline max + 0.15 cores
   (`recovery_check`). Ready replicas, tool success or model prose do not count.
9. **Verdict:** all 15 criteria of `evaluate_causal_g4_predicate`, including
   no harness repair before verification and durable primary evidence.
10. **Cleanup:** run-owned only — experiment ID and fault UID match; 1–3 attempts;
    first attempt starts after the primary record completed; verified zero Chaos objects
    (`evaluate_acceptance` → `uid_bound_cleanup`). Cleanup never counts as recovery.
11. **Independent recomputation:** reviewer reruns `agents.stage_a.evaluate_acceptance`
    on the persisted primary and cleanup records; all four criteria
    (`causal_recovery`, `pre_expiry_chronology`, `post_action_cpu`, `uid_bound_cleanup`) must be true.

## Abort / NO-GO (outcome retained as NEGATIVE or INCONCLUSIVE; no retry)

Source drift from the frozen commit; baseline stale or unbound; create/fault identity
mismatch; degradation not observed in 150 s; no genuine Alertmanager admission;
approval missing, rejected, expired or identity-less; any action other than the exact
one; action after 480 s or after expiry; no qualifying recovery by 540 s or before
expiry; uncertain cleanup ownership or nonzero Chaos objects after cleanup.

## Owner-supplied before freeze

Frozen commit and re-pinned inventory; model/tokenizer/adapter digests bound to the served
identity; Alertmanager admission implementation and review; per-operator authenticated
identity; host capacity measurement; the four named roles above; one fresh attempt identity.
