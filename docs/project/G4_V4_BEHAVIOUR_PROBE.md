# G4 v4 remediation behaviour probe

> [!IMPORTANT]
> **This is not Gate G4 evidence and must never be recorded as such.**
> Tool *results* are simulated in-process. There is no cluster, no Chaos Mesh, no
> Alertmanager, and the objective environment verifier never runs. Nothing here can
> close a gate. Gate G4 still requires a real end-to-end incident with
> `env_resolved == True` under the 15-point causal predicate.

## Why this probe exists

Runs `EXP-STAGE4-SF002-001` through `-008` all failed, and a live re-run costs roughly
12 minutes plus a full local Kind stack. That is an expensive way to answer one narrow
question, and a failed live run cannot distinguish a model problem from cluster timing,
alert delivery, or telemetry readiness.

The probe holds everything except the model fixed and asks only:

> Given the v4 tool contract, does the model discover the chaos experiment's exact
> resource name and stop it — or does it loop on `argocd_rollback` as run 008 did?

Reproduce with `scripts/probe_remediation_behaviour.py`.

## Setup

| Item | Value |
|---|---|
| Model | `qwen2.5:7b-instruct`, digest `845dbda0ea48…`, Q4_K_M, 7.6B params |
| Provider | Ollama 0.33.2, OpenAI-compatible endpoint, CPU inference |
| Protocol | v4 (`G4-OBSERVABILITY-V4-2026-08-30`) |
| Simulated fault | `StressChaos/sf-002-paymentservice-cpu`, verbatim from run 008 evidence |
| Argo CD | owns zero Applications, exactly as in the canonical environment |
| Diagnosis input | `category: "resource"` — **not** `fault_injection` |

The diagnosis category is deliberately unhelpful. The Diagnosis role has no chaos-aware
tool by design (see `tests/test_diagnosis_prompt_contract.py`), so it reports resource
saturation. Remediation has to reach the correct branch anyway. This is the harder case,
and it is the case run 008 actually failed.

## Result — active chaos

Three trials, consistent behaviour:

```
 1. chaos_list_experiments   {"namespace": "-A"}
 2. chaos_stop_experiment    {"kind": "StressChaos",
                              "name": "sf-002-paymentservice-cpu",
                              "namespace": "chaos-mesh"}          success=True
 3. promql_query             (verification — CPU back to baseline)
 …
argocd_rollback attempts: 0   (EXP-STAGE4-SF002-008 made 9)
reached goal state (experiment cleared): True
```

The model discovers the exact resource name and stops the right experiment on its second
tool call. The name is never guessed — it is read from the discovery result.

## Result — negative control

With no experiment present and a rollback that would genuinely succeed, the agent still
calls `chaos_list_experiments` first, gets `count: 0`, and moves on to another branch
(`kubectl_scale`). It is not chaos-fixated; the mandatory check is cheap and read-only,
and a negative answer routes it onward.

## What changed between run 008 and this probe

| | Run 008 | Probe |
|---|---|---|
| Chaos discovery available | no wrapper existed | `chaos_list_experiments`, remediation-only |
| First remediation call | `argocd_rollback(revision="latest")` | `chaos_list_experiments()` |
| `argocd_rollback` attempts | 9, all failing identically | 0 |
| Experiment name obtained | never | read from discovery output |
| Goal state reached | no | yes |

## Honest limits

- Simulated tool results. A live cluster adds latency, partial failures, and telemetry
  lag that this probe cannot reproduce.
- Three trials of one scenario (`sf-002`) with one model. Not a benchmark, not a rate.
- The probe exercises the Remediation role in isolation. The full chain — triage,
  diagnosis, approval gate, verifier, comms — is not run.
- It says nothing about whether Diagnosis can identify the root cause, which is the
  scientifically interesting question and is deliberately untouched by v4.

## Known limitation: chaos resource names carry the scenario ID

`bench/runner.py` now passes `scenario_id` out-of-band so it never enters the
model-visible prompt. But every frozen manifest names its resource after the
scenario — `sf-002-paymentservice-cpu`, `cs-001-currency-latency` — and
`chaos_stop_experiment` requires that exact string, so `chaos_list_experiments`
necessarily returns it to the Remediation role.

The leak is bounded:

- **Diagnosis never sees it.** Root-cause accuracy, the quantity actually being
  measured, is unaffected.
- Remediation already knows the affected service from triage; the additional
  information is the benchmark's own identifier.
- It matters most for a *trained* policy that could memorise
  scenario-id → action. No training has run, so nothing has learned it.

28 of 29 manifests are affected; `hist-knight-capital-2012` is the exception
because it injects a rogue Deployment rather than a chaos experiment. The surface
is pinned by `TestKnownScenarioIdentityLeak` in `tests/test_chaos_discovery.py`,
which fails if the manifests are renamed so the documentation is updated with it.

Fixing it means renaming resources across all 28 manifests to non-identifying
names, which changes manifest content hashes (`_scenario_fault_contract` in
`config/g4_protocol.py`) and the verifier's diagnostic prefix matching. That is a
Stage 5 scenario-truth decision, not a tooling change, so it is recorded rather
than taken here.

## Known design tension

Every one of the 28 frozen scenarios requires Chaos Mesh clearance
(`agents/verifier.py`, `require_chaos_cleared=True` for all specs). Stopping the
experiment is therefore a sufficient resolution for every scenario, which makes the
Remediation role's optimal policy close to a single action and limits what remediation
quality can be said to measure. Note also that "stop the fault injector" has no analogue
in a real incident, where no such control exists.

This predates v4 and is a property of the verifier's success model, not of the discovery
wrapper. It is recorded here because v4 makes that optimum reachable and therefore makes
the degeneracy observable. Resolving it means scenario-specific success predicates —
service recovered on its own merits, with chaos clearance handled by the harness rather
than credited to the agent — and that is a Stage 5 decision about scenario truth, not a
tooling change.
