# G4 Protocol Amendment: SF002 Telemetry Readiness

Status: Adopted
Date: 2026-08-23
Scope: Stage 4 Gate G4 instrumentation contract (`scripts/run_stage4_golden_incident.py`)
Supersedes: nothing; amends the SF002 F1 measurement contract introduced in
`fa86fdf` ("fix(g4): harden causal evidence contract").

## A. EXP-STAGE4-SF002-005 disposition

EXP-STAGE4-SF002-005 is an immutable instrumentation failure.

- T0 was crossed and the attempt is permanently spent (RESERVED -> CONSUMED ->
  COMPLETED). It must never be rerun.
- The run terminated at `measured_degradation`: Triage, Diagnosis, Remediation,
  Verification, and Comms were never reached, so the qualified model
  (`qwen2.5:3b-instruct`) was never invoked.
- 005 is therefore excluded from any inference about the remediation capability
  of that model. It is not evidence of success or failure of the model.
- Its evidence remains preserved unchanged:
  - `artifacts/evidence/stage4/EXP-STAGE4-SF002-005.json`
  - `artifacts/evidence/stage4/EXP-STAGE4-SF002-005.runlog.txt`
  - `artifacts/evidence/stage4/EXP-STAGE4-SF002-005.cleanup.json`
  - `artifacts/evidence/stage4/.attempts/EXP-STAGE4-SF002-005.attempt.json`

## B. Query defect

The original F1 selector:

```
container="paymentservice"
```

cannot match the pinned deployed workload: the paymentservice Deployment of
microservices-demo v0.10.6 names its application container `server`
(verified from the live Deployment spec, pod spec, and cAdvisor series labels).
The frozen query therefore matches zero series even when Prometheus and all
scrape targets are perfectly healthy.

This is a measurement-contract defect in the harness, not an observed model
result. The defect was latent until 005 because no prior experiment executed
the F1 path (002-004 predate its introduction).

## C. Corrected selector

Only the container selector is corrected:

```
container="paymentservice"  ->  container="server"
```

The intended metric is unchanged: paymentservice application-container CPU.

Explicitly unchanged:

- namespace selector (`namespace="default"`)
- pod selector (`pod=~"paymentservice-.*"`)
- rate window (`[2m]`)
- degradation polling cadence (3s)
- post-fault observation timeout (30s)
- absolute threshold (+0.25 cores)
- relative threshold (2x)

The explicit pinned container name is preferred over a generalized selector
because the deployed workload version is known, cAdvisor labels confirm it,
it preserves exact reproducibility, and it avoids accidentally aggregating
pause/sidecar containers.

## D. Future attempt policy

A subsequent uniquely identified experiment (a new `STAGE4_EXPERIMENT_ID`) may
be used as the replacement model-capability trial only after telemetry
readiness is proven BEFORE reservation, per the implemented pre-reservation
telemetry-readiness gate:

1. Prometheus endpoint transport works.
2. `/-/healthy` returns HTTP 200.
3. `/-/ready` returns HTTP 200.
4. Required kubelet/cAdvisor scrape target is UP with empty `lastError`.
5. Raw metric exists for
   `container_cpu_usage_seconds_total{namespace="default",pod=~"paymentservice-.*",container="server"}`.
6. The exact corrected F1 query returns a vector with at least one finite,
   non-NaN numeric sample compatible with the existing parser.
7. Stability: two consecutive fully-valid cycles separated by at least one
   configured Prometheus scrape interval (30s), within a bounded readiness
   timeout (120s).

Telemetry-readiness failure causes a preflight abort before
`reserve_experiment_attempt()`: no attempt marker, no RESERVED state, no Chaos
object, no T0, no CONSUMED, and no experiment ID consumed.

Such a replacement trial is NOT a rerun of 005. 005 remains part of the
permanent experiment history.
