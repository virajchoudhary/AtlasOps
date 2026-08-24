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
- post-fault observation timeout (30s) *(superseded 2026-08-24 — see F1
  envelope amendment below)*
- absolute threshold (+0.25 cores) *(superseded 2026-08-24 — see F1 envelope
  amendment below)*
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

Additionally (adopted after the 2026-08-23 EXP-STAGE4-SF002-006 pre-fault
abort): the paymentservice Deployment baseline readiness contract is also
evaluated BEFORE reservation, requiring two consecutive healthy fail-closed
baseline reads separated by at least five seconds within a bounded timeout.
Reservation now occurs only after BOTH gates pass; the fault boundary remains
the sole CONSUMED point. This ordering change preserves every health
requirement and all F1-F5 semantics.

Such a replacement trial is NOT a rerun of 005. 005 remains part of the
permanent experiment history.

---

# SF002 F1 Envelope Amendment (2026-08-24)

## A. Historical contract

The SF002 degradation contract introduced in a86fdf required, within a
30-second post-fault observation window polled every 3 seconds:

- absolute increase of the frozen F1 metric >= +0.25 cores
- relative increase >= 2.0x baseline
- frozen query max(rate(container_cpu_usage_seconds_total{namespace="default",pod=~"paymentservice-.*",container="server"}[2m]))
  over the unchanged [2m] rate window

## B. Why the historical contract is invalid

The pinned microservices-demo v0.10.6 paymentservice Deployment carries a hard
CPU limit on its application container (server):

- CPU limit: **200m = 0.2 cores** (Burstable QoS)

Consequences:

1. The container's maximum steady-state measured CPU rate is its CFS quota:
   **0.2 cores**. The historical absolute requirement of **+0.25 cores above
   baseline therefore exceeds the container's physical ceiling by more than
   5x the entire headroom and is unreachable at any exposure duration.
2. Even granting perfect full-quota execution, an exposure of only 30 seconds
   inside the 120-second rate window contributes at most
   .2 * 30 / 120 = 0.05 cores of observed increase at the observation
   boundary — the window is necessarily dominated by pre-fault history.

Therefore the historical contract could not certify a valid SF002 degradation
event under the pinned workload under any circumstances. This was confirmed by
EXP-STAGE4-SF002-007 (T0 crossed; fault created and observable; measured
relative ratio 34.76x satisfied; measured absolute increase +0.029 cores;
verdict INVALID at measured_degradation; agents never reached).

## C. Corrected prospective contract

Unchanged:

- scenario single_fault/sf-002, fault StressChaos sf-002-paymentservice-cpu
  (workers=4, load=90, duration=10m)
- target paymentservice / default, application container server
- exact F1 PromQL and [2m] rate window
- relative requirement >= 2.0x baseline
- polling cadence 3s

Changed (the only two amended parameters):

- **absolute increase >= +0.15 cores**
- **post-fault observation timeout = 150 seconds**

## D. Absolute threshold derivation

+0.15 cores = **75% of the pinned 0.2-core paymentservice CPU limit**. This
defines materially elevated application-container CPU while remaining strictly
inside the workload's physically reachable envelope. The derivation uses only
the pinned resource specification; it does not use any observed experimental
value from EXP-STAGE4-SF002-007 or any other attempt.

## E. Observation duration derivation

150 seconds accommodates:

- the full 120-second [2m] rate window being repopulated by post-fault
  scrapes, and
- one additional ~30-second Prometheus scrape interval of convergence margin.

With sustained stress, the measured [2m] rate converges toward the delivered
steady-state CPU as pre-fault samples age out of the window.

## F. Historical integrity

EXP-STAGE4-SF002-005, -006, and -007 retain their original evidence, runlogs,
lifecycle records, and official verdicts exactly as recorded under the
contracts in force at their execution time. No verdict is retroactively
rescored, no evidence is altered, and no attempt is reclassified as having run
under this amendment.

## G. Prospective scope

This amendment applies prospectively only, beginning with the first future
uniquely identified replacement trial (no earlier than
EXP-STAGE4-SF002-008). Such a trial is a new scientific attempt, not a rerun
of any prior experiment.
