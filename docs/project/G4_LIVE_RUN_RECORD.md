# Gate G4 live run record — runs 009 onward

Runs `EXP-STAGE4-SF002-001` through `-008` all failed. This records what changed
and what the repaired runtime actually does on a live cluster.

Every run here executes against a real Kind cluster: real Chaos Mesh injection,
real Prometheus telemetry, real `kubectl` mutation, and the objective
environment verifier. Nothing is simulated. The model-behaviour probe in
`G4_V4_BEHAVIOUR_PROBE.md` is a separate, explicitly simulated artifact and is
not evidence for this gate.

## Environment

| | |
|---|---|
| Cluster | Kind `atlasops-local`, single node, arm64 |
| Container runtime | Colima (headless; Docker Desktop's VM will not start without GUI input) |
| Stack | Online Boutique v0.10.0 @ `98e60f5`, kube-prometheus-stack 88.3.0, Jaeger 4.12.0, Argo CD 10.3.2, Chaos Mesh 2.8.3, metrics-server v0.7.2 |
| Model | `qwen2.5:3b-instruct`, digest `357c53fb659c…` |
| Protocols | `G4-OBSERVABILITY-V4-3B` (runs 009-010), `G4-BOUNDED-COMPLETION-V5-3B` (011-013), `G4-RESERVED-REMEDIATION-V6-3B` (014) |
| Deviations | `docs/project/LOCAL_ARM64_DEVIATIONS.md` (D1 applied) |

The 3B model is deliberate. Runs 005–008 used this same model binary — its
digest is byte-identical to the one protocol v2 declared — so the repaired tool
contract is the **only** difference from the run that failed. A larger model
would have confounded the comparison.

---

## Run 009 — 14 of 15 criteria pass

Remediation trajectory, verbatim from the evidence record:

```
1. chaos_list_experiments()                                            success
2. chaos_stop_experiment(kind="StressChaos",
                         name="sf-002-paymentservice-cpu",
                         namespace="chaos-mesh")                       success
3. kubectl_get(resource="deployments", namespace="default")            success
```

| | Run 008 | Run 009 |
|---|---|---|
| `argocd_rollback` attempts | 9, all failing identically | **0** |
| Experiment name obtained | never | read from discovery output |
| 10 — remediation tool success | **FAIL** | **PASS** |
| 11 — remediation target match | **FAIL** | **PASS** |
| 13 — objective env resolved | FAIL | FAIL (see below) |
| Diagnosis category | `deploy`, contradicted by its own evidence | `unknown`, honest |
| Duration | 721s | 267s |

The diagnosis result is worth dwelling on. Diagnosis has no chaos-aware tool by
design, so it cannot name a fault injection. In run 008 it invented a deployment
cause its own `argocd_list_apps` output had just refuted. In run 009 it reported
`unknown` — an accurate statement of what it could observe — and remediation
still reached the correct action from its own decision tree. The scenario
neutrality contract held, and the repair did not paper over it.

### Why criterion 13 still failed — a second unreachable condition

The objective verifier **passed**:

```
env_resolved          : True
verification_status   : passed
failed_checks         : []
  [PASS] workload_paymentservice_ready   Ready replicas: 1/1 (available: 1)
  [PASS] chaos_mesh_cleared              Zero active Chaos Mesh experiment resources
```

The gate disagreed with its own verifier. Criterion 13 is

```python
c13 = env_resolved is True and settling_completed is True
settling_completed = incident_result.get("settling", {}).get("settled", False)
```

`handle_incident` computed the bounded-convergence report and passed it to the
Comms agent, but never added it to the record it returns. The runner therefore
read `{}` on every run and `settling_completed` was always `False`. **Criterion
13 could not be satisfied by any cluster state**, which is why runs 001–008
could never have passed even had their remediation been perfect.

This is the same defect class as the phantom `triage_seed` in GRPO: a value
produced in one place and read in another that never receives it. Fixed by
persisting `settling` in the incident record; pinned from both ends by
`TestIncidentRecordCarriesSettlingReport`.

---

## Run 010 — Gate G4 PASS

`EXP-STAGE4-SF002-010` repeated run 009 with the settling report persisted.
**All 15 causal criteria pass; `gate_g4_pass: true`.**

```
1_baseline_healthy                    PASS
2_injection_success                   PASS
3_fault_observed_pre_trigger          PASS
4_trigger_delivered                   PASS
5_triage_valid                        PASS
6_diagnosis_valid                     PASS
7_diagnosis_truth_match               PASS
8_approval_satisfied                  PASS
9_remediation_mutating_tool_executed  PASS
10_remediation_tool_success           PASS
11_remediation_target_match           PASS
12_no_harness_repair_pre_verification PASS
13_objective_env_resolved             PASS
14_comms_executed                     PASS
15_evidence_persisted                 PASS
```

Remediation trajectory:

```
1. chaos_list_experiments()                                   success
2. chaos_stop_experiment("StressChaos",
                         "sf-002-paymentservice-cpu")         success
3. promql_query(...)                                          success
4. kubectl_scale(paymentservice, replicas=1)                  success
5. kubectl_rollout(status, deployment/paymentservice)         success
```

Objective verifier:

```
agent_claimed_resolved : True
env_resolved           : True
verification_status    : passed
  [PASS] workload_paymentservice_ready   Ready replicas: 1/1 (available: 1)
  [PASS] chaos_mesh_cleared              Zero active Chaos Mesh experiment resources
```

The agent discovered the experiment, stopped it by exact name, verified with
PromQL, and confirmed the workload — with no harness repair before verification
(criterion 12) and no `argocd_rollback` attempts.

### What the run cost, and what it exposed

Duration was 670s against run 009's 267s. The difference is a single defect: the
**triage** agent generated 11,000+ tokens in one turn without emitting a stop
token, hit the 300s request timeout, and retried for another 300s.

Agent calls carried no `max_tokens` — only the judge did. Ten turns across four
roles makes the worst case multi-hour for an incident that completes in four
minutes when the model terminates normally. Protocol **v5**
(`G4-BOUNDED-COMPLETION-V5`) declares a 1024-token ceiling on every agent
completion. Runs 009 and 010 were executed under v4-3b, before that ceiling
existed, and are budgeted separately from any v5 run.

### Caveats on this result

- **One run, one scenario, one model.** `sf-002` on `qwen2.5:3b-instruct`. This
  closes a gate that requires a single verified incident; it is not a resolution
  rate and implies nothing about the other 27 scenarios.
- **The success predicate remains chaos clearance**, which makes remediation
  close to a single-action problem and has no analogue in a real incident. That
  limitation is unchanged by this run and is recorded in
  `G4_V4_BEHAVIOUR_PROBE.md`.
- **Run 010's evidence file does not contain the settling report** its own
  criterion 13 was computed from; that record was in memory only. Fixed
  afterwards so later runs are auditable from the artifact alone.
- **arm64 deviations were active** (`LOCAL_ARM64_DEVIATIONS.md`). They affect
  cartservice and currencyservice, neither of which participates in `sf-002`.

---

## Run 011 — INVALID, and correctly so

The first run under protocol v5 (`G4-BOUNDED-COMPLETION-V5-3B`) never reached
the agent chain. The degradation gate rejected it:

```
outcome                 : INVALID
failure_phase           : measured_degradation
post_to_baseline_ratio  : 1.99   (min 2.0)
absolute_increase_cores : 0.0023 (min 0.15)
baseline_max_cores      : 0.0023
post_fault_max_cores    : 0.0046
```

The `StressChaos` resource was created and observable, but the stressor never
took hold — run 010 saw 0.0008 → 0.1597 cores on the same scenario, a ratio of
190 against this run's 1.99. The node was at 4% CPU with Chaos Mesh healthy, so
this is intermittent injection failure, not resource starvation.

**This is the protocol working.** An attempt where the fault never established
says nothing about the agents, and the harness classified it `INVALID` rather
than counting it as a remediation failure — the same protection that
retrospectively invalidated run 001. A gate that scored this as FAIL would be
measuring Chaos Mesh, not AtlasOps.

Practical consequence: fault establishment on a single-node arm64 Kind cluster
is not perfectly reliable, so a run that fails at `measured_degradation` should
be retried rather than analysed. Attempt budgets are consumed either way, which
is the correct trade: the alternative is a harness that silently retries until it
gets the result it wants.

---

## Run 012 — FAIL, and the fix that caused it

The second v5 run reached the agent chain and failed 9/15: criteria 6, 7, 9, 10, 11 and 13 all missed. Every remediation tool call, including read-only chaos
discovery, returned the same thing:

```
{"blocked_by_circuit_breaker": true,
 "error": "Tool call limit (50) exceeded for inc-1788079789-1813d6"}
```

**Triage and diagnosis had spent the entire per-incident budget before
remediation ran.** Remediation then guessed a chaos experiment name
(`PodChaos/high-cpu-impact` — no such resource) because the tool that would have
told it the real one was refused, and escalated.

### This was caused by the v5 fix

Bounding completions at 1024 tokens stops a single runaway generation. It also
makes the model produce *more, shorter* turns. Combined with Ollama's 4096-token
context — which silently discards the earliest turns once exceeded — the
investigating agents lose track of what they have already done and repeat
themselves until the 50-call budget is gone.

So v5 traded one failure mode for another: run 010 burned 600s on one unbounded
generation; run 012 burned 50 tool calls on bounded churn. Both starve the same
thing.

### The repair: reserve budget for the role that can act

`max_tool_calls_per_incident` was a single pool shared across all four roles,
consumed in execution order, with remediation last. Investigation could therefore
spend all of it. The breaker now reserves
`reserved_remediation_tool_calls` (12 of 50): triage, diagnosis and comms stop at
38 with a message telling them to conclude from the evidence they have, and
remediation always arrives with budget to act.

The reservation is a floor, not an exemption — remediation is still bounded by
the overall limit, and callers that pass no role keep the original behaviour.

### What run 012 says about the gate

Run 010's PASS stands: it is a real, fully verified incident under a declared
protocol. But **one pass is not a reliable pass.** Across runs 009-012 the same
scenario and model produced 14/15, 15/15, INVALID, and 9/15. That spread is the
honest headline, and it is why no resolution rate should be quoted from this
work. A gate defined as "one verified incident" is satisfied; a claim about how
often the system resolves incidents is not supported by anything here.

---

## Run 013 — refused by the attempt budget

The first attempt to test the reserved-budget repair never started:

```
RuntimeError: protocol attempt limit reached for profile 2cee3442005b…: 2/2
```

Runs 011 and 012 had consumed both attempts allotted to the `v5-3b` fingerprint,
and the harness refused a third. **This is the control working.** Re-running the
same declared protocol until it produces a better number is exactly the failure
mode the attempt budget exists to prevent, and it applies to the person holding
the repair as much as to anyone else.

It also pointed at the right answer. The reserved-budget change alters what an
agent can do mid-incident, so it is not the same protocol — running it under
`v5-3b` would have mixed two runtimes under one identity. The fix was to declare
it.

### v6, and a gap it closed

`G4-RESERVED-REMEDIATION-V6` reserves 12 of the 50 per-incident tool calls for
the Remediation role. Declaring it surfaced an omission: the circuit-breaker
limits were never part of the protocol profile at all, even though run 012 shows
they can decide a run's outcome on their own. v6 adds a `safety_envelope` to the
declaration, and a test asserts the declared numbers match the live breaker — a
declaration nothing enforces is decoration.

---

## Run 014 — Gate G4 PASS reproduced under v6

`EXP-STAGE4-SF002-014`, protocol `G4-RESERVED-REMEDIATION-V6-3B`.
**All 15 criteria pass; `gate_g4_pass: true`.**

```
chaos_list_experiments()                                  success
chaos_stop_experiment("StressChaos",
                      "sf-002-paymentservice-cpu")        success
promql_query(...)                                         success

env_resolved     : True     verification_status : passed
settling settled : True     settling duration   : 0.11s
duration         : 281s
```

This matters more than run 010 did, for three reasons.

**It is a second, independent pass** under a *different and stricter* protocol —
bounded completions, a reserved remediation budget, and a declared safety
envelope that run 010 had none of. Two passes across two protocols is a
materially different claim from one pass under one.

**Every repair is visible in the numbers.** Duration fell from 670s to 281s
because no turn ran away; settling converged in 0.11s rather than timing out at
30s; and remediation reached the goal in three calls with budget to spare, where
run 012 had every call refused.

**The failures in between were load-bearing.** Run 012's starvation was caused by
the v5 fix and would not have been visible without it. Had run 010's pass been
treated as the end of the work, the reserved-budget defect would have shipped
undetected.

### Result set as it stands

| Run | Protocol | Outcome | What it established |
|---|---|---|---|
| 009 | v4-3b | 14/15 | exposed the unreadable settling report |
| 010 | v4-3b | **15/15 PASS** | both unreachable conditions removed |
| 011 | v5-3b | INVALID | fault never established; harness refused to score it |
| 012 | v5-3b | 9/15 FAIL | bounded completions starved remediation of budget |
| 013 | v5-3b | refused | attempt budget spent; forced a new declaration |
| 014 | v6-3b | **15/15 PASS** | reproduced under a stricter protocol |

Two passes in four valid attempts, across two protocols. That is enough to close
a gate defined as one verified incident, and **not** enough to state a resolution
rate — which is why none is published.

---

## Runtime characteristics worth knowing

**Effective context is 4096 tokens, and it silently truncates.** Ollama's default
`num_ctx` is 4096. During a run the server logs
`slot context shift … n_discard = 2045`: once the agent's message history exceeds
the window, the earliest turns are discarded without any signal to the
coordinator. A long incident therefore reasons over a sliding window, not the
full trajectory, and evidence cited from an early turn may no longer be visible
when the conclusion is produced.

This interacts directly with tool-result size. The model-facing budget is 8000
characters, roughly 2000 tokens — half the context window — so a single
unprojected list result could crowd out the entire prior conversation. It is one
more reason the projection in `_model_facing_tool_output` drops whole items to
fit a declared budget rather than hard-slicing.

Raising `num_ctx` is an Ollama server setting rather than repository
configuration, so it is recorded here rather than changed. Any run comparison
should note that both sides used the same window.

**Completion ceiling, observed.** Under v5 the longest generation seen was 916
tokens against the declared 1024 ceiling. Under v4-3b, run 010's triage turn
reached 11,691 tokens before the request timed out.

---

## Reproducing

```bash
colima start --cpu 6 --memory 9 --disk 60 --runtime docker
python scripts/generate_runtime_secrets.py
PYTHON_BIN="$PWD/.venv/bin/python" bash infra/local/setup_local.sh --apply
PYTHON_BIN="$PWD/.venv/bin/python" bash infra/local/install_metrics_server.sh --apply
ollama pull qwen2.5:3b-instruct

STAGE4_EXPERIMENT_ID=EXP-STAGE4-SF002-0NN \
ATLASOPS_STAGE4_AGENT_MODEL=qwen2.5:3b-instruct \
  python -u scripts/run_stage4_golden_incident.py
```

Each attempt consumes a budget slot keyed to the protocol fingerprint, so 3B and
7B runs are accounted separately and neither can borrow the other's budget.

---

## Reading the evidence directory

`artifacts/evidence/stage4/` contains runs from three eras, and the differences
between them are provenance, not inconsistency. Anyone auditing the directory
should know which is which before citing a number.

| Runs | `protocol_marker` | Status |
|---|---|---|
| 002, 003, 004, 008 | key **absent** | Pre-mechanism. Real runs, honestly recorded, but not attributable to a declared protocol. |
| 005, 007 | key **absent** | INVALID — fault establishment failed before the agent ran. |
| 009–014 | present | Protocol-attributed. Citable as gate evidence. |

Three consequences follow.

**The absent marker is not a governance violation.** Protocol markers and
per-fingerprint attempt budgets were introduced at run 009. Runs 002–008 predate
the mechanism, so the key is missing rather than blank. They remain in the
directory because deleting failed runs is how a pass rate becomes a lie — but
they cannot be cited as evidence *for* or *against* a named protocol, because no
protocol was declared when they ran.

**Fault-establishment flakiness predates v5.** Runs 005 and 007 are INVALID for
the same reason as run 011: the SF-002 degradation proof did not reach its
threshold, so the experiment aborted before the agent was invoked. Three INVALID
runs across three separate protocols means this is a property of the injection
harness on this host, not evidence about any agent change. Run 011 in particular
must not be read as a v5 regression.

**Run 013 has no evidence file at all.** It was refused by the attempt-budget
check before execution — the v5-3b fingerprint had already consumed 2 of 2
attempts. A refusal is not a run and deliberately produces no artifact; the gap
in the numbering is the mechanism working.

The only two runs that may be cited as Gate G4 passes are **010** (v4-3b) and
**014** (v6-3b), each 15/15 with a declared protocol fingerprint.
