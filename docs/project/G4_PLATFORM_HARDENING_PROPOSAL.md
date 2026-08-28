# G4 Platform Hardening Proposal — G4-PLATFORM-HARDENING-2026-08-25

Status: PROPOSED (prospective; not retroactive)
Provenance marker: `G4-PLATFORM-HARDENING-2026-08-25`
Motivating evidence: EXP-STAGE4-SF002-008 (VALID MODEL-CAPABILITY FAILURE, spent)
Pipeline compatibility: Pipeline v1.1 Free-First — no paid services, no
scenario semantics changes, verifier remains authoritative.

---

## 1. Evidence-grounding integrity (implemented)

### Old behavior

Agents' structured final outputs could cite tool observations that the agent
never executed. In EXP-STAGE4-SF002-008 the diagnosis agent cited an
`argocd_list_apps` observation that appears nowhere in its own trajectory,
attributing the incident to deployments and recommending rollback — steering
remediation into a doomed strategy.

### New behavior

A deterministic, general provenance validator (`agents/grounding.py`) checks
every structured `evidence[].tool` citation in an agent's final output against
the tools actually executed in that agent's own trajectory, and checks supplied
generic identifiers such as `query`, `resource`, and `app` against the
execution arguments. This proves citation provenance and identifier matching.
It does **not** prove that arbitrary natural-language `finding` text semantically
follows from the raw tool output; semantic entailment remains outside its
guarantee. Reports are recorded per role (`triage`/`diagnosis`/`remediation`) as
`grounding_validation` in the persisted trajectory record and in Stage 4
evidence (`phases.coordinator_execution.grounding_validation`).

An observation-id/call-id requirement was evaluated but not added: it would
alter the model-visible evidence schema and therefore require another protocol
change. The existing tool/argument check is sufficient for deterministic
provenance without unnecessarily changing task behavior.

### Behavior choice: preserve-and-score

- **Fail-closed rejected**: aborting a run over a model hallucination destroys
  exactly the measurement G4 exists to take.
- **Retry-with-validation-feedback rejected**: feeding violations back would
  change task difficulty mid-run and let the model erase its own failure
  signal, breaking comparability with prior attempts.
- **Preserve-and-score adopted**: raw output is untouched; violations are
  recorded separately so hallucination stays measurable; the authoritative
  environment verifier remains the sole success authority. Grounding results
  are NOT consumed by causal predicates.

### General justification

The mechanism is fully generic: any list field named `evidence` whose items
carry a `tool` key is validated against actual executions. It contains no
SF002/Argo CD/paymentservice/Chaos-specific rules and adds no hints about any
expected root cause.

### Why not derived to force success on 008

008 remains scored VALID MODEL-CAPABILITY FAILURE under its original frozen
contract. Grounding validation is diagnostic-only and prospective; it cannot
and does not alter 008's verdict, and it does not feed remediation hints. The
next model must still discover the fault on its own.

### Expected effect on future experiments

Hallucinated-evidence failures become deterministically measurable in evidence
instead of invisible. Success probability may rise if agents condition on their
own grounding feedback in future protocol versions — that is a legitimate,
declared platform-capability change, recorded via this amendment marker.

### Required tests/evidence

`tests/test_agents_grounding.py`: fabricated-citation regression (008-shaped),
raw-output immutability, genuine-citation pass, nested/multiple violations,
fail-safe malformed inputs, never-raises aggregation, JSON serializability.

## 2. Local Kubernetes tool-contract reconciliation (implemented: B + opt-in A)

### Old behavior

`kubectl_top_pods` / `kubectl_top_nodes` were exposed while the canonical Kind
environment lacks metrics-server; calls failed with raw stderr
("Metrics API not available") indistinguishable from transient failures.

### New behavior (B — deterministic degradation)

Both wrappers classify that stable stderr signature into a structured
`error_class="metrics_api_unavailable"` result, preserving the raw observation.
Agents can now distinguish "dependency missing" from "command failed".

### Opt-in adoption path (A — reproducible artifact provided, NOT installed by this change)

`infra/local/install_metrics_server.sh` installs a commit-pinned upstream
metrics-server into the exact canonical Kind context idempotently, waits for
availability, and verifies both direct `kubectl top` and the AtlasOps wrapper.
Apply mode is explicit; check mode mutates nothing. It is reversible with
`kubectl delete -k` against the same pinned source, free, and costs ~100m
CPU/~200Mi memory. It was NOT executed in this change: installing optional
infrastructure into the canonical scientific environment requires operator
authorization and should happen outside soak/science windows.

For an already-present Deployment, apply mode first verifies the canonical
namespace/name/container identity, exact v0.7.2 image, complete ordered argument
set including Kind TLS compatibility, service account, priority class, resource
requests, and container port. It prints that provenance before functional API
checks; any mismatch or inspection failure is fatal. The next hardened G4
protocol declares Metrics API **required-present** with this exact configuration,
so a missing Deployment cannot reserve an attempt. No installation occurs in
this Goal.

## 3. Argo CD deterministic error taxonomy (implemented)

### Old behavior

Every `_api` failure collapsed to `"argocd_request_error: request failed"` —
EXP-STAGE4-SF002-008's nine rollbacks all received this opaque string.

### New behavior

Deterministic classes: `timeout`, `connection_failed`, `authentication_failed`
(401), `authorization_failed` (403), `not_found` (404), `invalid_request`
(400), `conflict` (409), `unprocessable` (422), `http_<n>_error`, plus legacy
`request_failed` catch-all. Response bodies are intentionally not echoed
(secret-safety); only class + HTTP status are surfaced. Rollbacks are never
auto-converted into other actions — the model still chooses its next step.

## 4. Scientific-boundary review

| Change | Class | G4 difficulty effect |
|---|---|---|
| Grounding validator | Correctness/grounding defect fix (deterministic detection) | None within-run (diagnostic-only, preserve-and-score). Prospective: makes hallucination measurable. |
| kubectl top classification | Tool-contract correctness | None on workload health; improves agent failure attribution |
| Argo CD taxonomy | Observability/tool-contract correctness | Removes an information asymmetry; model must still choose actions |
| Diagnosis prompt/tool-contract changes | Scientific protocol change | Prospective: removes unavailable cloud tools and scenario-specific hints, adds generic CRD discovery and `unknown`; changes task difficulty. |
| Protocol profile/fingerprint + two-attempt cap | Scientific protocol/process control | Prospective only: legacy attempts remain immutable under their original contracts and do not consume the new profile budget. |

None of these weaken readiness gates, thresholds, windows, prompts (other than
the declared Diagnosis category/discovery changes), scenario semantics,
verifier predicates, or per-attempt lifecycle. No SF002-specific hint is
introduced anywhere.

## 5. Compatibility with Pipeline v1.1

All changes are local, free-tier, and read-mostly except the explicit opt-in
installer. Verifier remains authoritative; causal predicates are untouched;
evidence schemas gain additive provenance/marker fields. Future reservations
must exactly match a statically declared protocol profile and record its
human-readable components plus canonical SHA-256 fingerprint. The profile pins
the qualified 3B model/digest, exact Diagnosis prompt hash, model-visible role
tool contract hash (including schemas, ACLs/descriptions, and changed Argo CD /
kubectl response taxonomies), F1 contract, SF002 fault contract, and
required-present pinned Metrics API state. Reservation fails closed after two
claimed attempts for the same complete fingerprint; model, prompt/tool,
Metrics API drift, malformed accounting records, concurrent check-and-reserve
races, or stale reservation locks cannot consume an unaccounted budget slot.
Existing unmarked 005/006/007/008 attempts remain under
their original protocol versions and are not retroactively charged to this new
profile. Launching any next attempt remains outside this Goal and requires a
separate authorization decision.

Required tests: `tests/test_agents_grounding.py`,
`tests/test_argocd_error_taxonomy.py`,
`tests/test_kubectl_top_classification.py`, plus full suite green.

## 6. Model-selection decision support (read-only analysis)

For this prospective hardening profile only, the executable Stage 4 contract is
pinned to `qwen2.5:3b-instruct` with digest
`357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b`. The general
1.5B runtime default remains available elsewhere, but cannot reserve under this
profile. A different model or digest fails reservation and must be declared in
a separately reviewed protocol profile before it can consume any attempt budget.

Feasibility on the canonical host (15.37 GB RAM; WSL2 default ≈ half system
memory; Ollama CPU inference):

- The executable default remains `qwen2.5:1.5b`; `qwen2.5:3b-instruct` is the
  explicitly configured, previously qualified model. It uses ~2–3 GB resident,
  demonstrated coexistence with the full Kind stack during passing soaks, and
  had acceptable latency (model turns of seconds to ~45s).
- `qwen2.5:7b-instruct` Q4: ~5–6 GB resident — coexistence risk HIGH alongside
  Docker+Kind (~3–5 GB vmmem) with <6 GB typical free; multi-turn remediation
  latency would multiply, raising settling-window risk.
- Larger models: infeasible free-first on this host.

Recommendation: if and only if a separate Goal explicitly authorizes another
attempt, retain the qualified 3B under this hardening marker for comparability
with 008 and coexistence safety.

Pre-specified future-attempt policy (avoids success-chasing):

1. One attempt per experiment ID; verdicts immutable.
2. At most two attempts per platform-hardening version marker.
3. Any threshold/window/prompt/model change requires a new prospective
   amendment with independent justification AND increments the marker.
4. Metrics API is required-present under the next hardened profile using the
   pinned v0.7.2 upstream release configuration; it is not installed or
   activated by this amendment.
5. Escalation to a stronger untrained base model is permitted at most once,
   only after ≥2 consecutive valid model-capability failures with DISTINCT
   failure signatures across different hardening versions, and itself
   constitutes a new protocol version.
6. No parameter may be changed based on a single failed attempt's outcome.
