# Runbook — Jaeger Live Trace Verification (Morning Procedure)

Purpose: prove that the A3 trace-ingestion fix actually produces spans in Jaeger
on a live local Kind cluster. This is **live verification only** — run it on an
authorized workstation with Docker Desktop running. Nothing here touches GKE or
any production system.

Authorizing context: `docs/project/MASTER_PIPELINE_STATUS.md` §"Jaeger Backend
Reachability vs. Online Boutique Trace Ingestion" — item B stays
"LIVE VERIFICATION PENDING" until every check below passes.

## Background (what changed and why)

- Pinned Online Boutique v0.10.0 manifests ship with tracing disabled; no
  service ever exported spans, so `jaeger_search` returned
  `{"success": true, "count": 0}`.
- Chart `jaegertracing/jaeger` 4.12.0 renders the Jaeger v2 all-in-one binary
  (in-memory storage) whose default configuration already accepts OTLP on
  ports 4317 (gRPC) / 4318 (HTTP). Service name is `jaeger` in namespace
  `jaeger`.
- `infra/local/setup_local.sh` now patches exactly the four boutique services
  whose images honor the tracing contract (`frontend`, `checkoutservice`,
  `currencyservice`, `emailservice`) with:
  - `ENABLE_TRACING=1`
  - `COLLECTOR_SERVICE_ADDR=jaeger.jaeger.svc.cluster.local:4317`
- No separate OpenTelemetry Collector is required.

## Preconditions

1. Docker Desktop running; `docker`, `kind`, `kubectl`, `helm` on PATH.
2. Local secrets present (`secrets/` — see
   `scripts/generate_runtime_secrets.py`). Network access for pulling the
   pinned boutique manifest, helm charts, and images.
3. Work from the repo root of this worktree.

## Step 1 — Deploy the environment

```bash
bash infra/local/setup_local.sh --apply
```

Expect near the end:

```
TRACING: enabling trace export on deployment/frontend...
TRACING: enabling trace export on deployment/checkoutservice...
TRACING: enabling trace export on deployment/currencyservice...
TRACING: enabling trace export on deployment/emailservice...
TRACING: boutique spans now export OTLP/gRPC to jaeger.jaeger.svc.cluster.local:4317.
```

If reusing an existing cluster: the boutique apply resets env vars, but the
patch step runs afterwards every time, so re-running `--apply` converges.

## Step 2 — Verify the patch landed

```bash
kubectl --context=kind-atlasops-local get deploy frontend checkoutservice \
  currencyservice emailservice -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .spec.template.spec.containers[0].env[*]}{.name}={.value} {"\n"}{end}{end}' \
  | grep -E "ENABLE_TRACING|COLLECTOR_SERVICE_ADDR"
```

Expect four pairs: `ENABLE_TRACING=1` and
`COLLECTOR_SERVICE_ADDR=jaeger.jaeger.svc.cluster.local:4317`.

Also confirm all four deployments are Available after the patch rollout:

```bash
kubectl --context=kind-atlasops-local get deploy \
  frontend checkoutservice currencyservice emailservice
```

## Step 3 — Generate traffic

Boutique's own loadgenerator is usually sufficient. To force fresh traces,
browse or curl the frontend while port-forwarded:

```bash
kubectl --context=kind-atlasops-local port-forward -n default svc/frontend 8080:80 &
# then: open http://localhost:8080 and add an item to the cart / place an order
```

A full checkout exercises frontend -> cart -> product catalog -> currency ->
shipping -> payment -> email paths (traced segments from the four instrumented
services).

## Step 4 — Verify traces arrive in Jaeger

Port-forward the query API:

```bash
kubectl --context=kind-atlasops-local port-forward -n jaeger svc/jaeger 16686:16686 &
```

Wait ~30 seconds (span batching), then:

```bash
curl -s http://localhost:16686/api/services | python -m json.tool
```

PASS criteria: `"data"` contains at least `frontend`, and ideally
`checkoutservice`, `currencyservice`, `emailservice`. If you previously saw
only `"data": []`, that contrast IS the fix working.

Then confirm real traces with spans:

```bash
curl -s "http://localhost:16686/api/traces?service=frontend&limit=5&lookback=1h" | python -m json.tool | head -50
```

PASS criteria: non-empty `data`, each trace has multiple spans, and spans from
other boutique services appear in the same trace (trace propagation through
gRPC metadata).

Optional UI check: open http://localhost:16686 (same forward), pick service
`frontend`, Find Traces.

## Step 5 — Exercise the AtlasOps tool path (agent-equivalent)

```bash
JAEGER_URL=http://localhost:16686 python -c "
from agents.tools.jaeger import jaeger_search, jaeger_get_trace
r = jaeger_search('frontend', lookback='1h')
assert r['success'] and r['count'] > 0, r
print('jaeger_search count:', r['count'])
tid = r['traces'][0]['trace_id']
t = jaeger_get_trace(tid)
assert t['success'], t
print('jaeger_get_trace OK:', tid)
"
```

PASS criteria: `count > 0` and one full trace fetched by ID. This is the exact
call pattern the Diagnosis agent uses.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `services` list empty after >2 min of traffic | Check pod env patch landed (Step 2). Check traced pods restarted AFTER jaeger was up: `kubectl --context=kind-atlasops-local get pods -n default` (AGE should postdate jaeger). |
| Only `frontend` appears | Expected at minimum; other three emit spans mainly on their gRPC handlers/clients during checkout flows. Drive a full order (Step 3). |
| Jaeger pod crashloops | `kubectl --context=kind-atlasops-local logs -n jaeger deploy/jaeger --tail=100`. Do NOT add values-file keys beyond `jaeger.resources` — chart 4.x ignores legacy 3.x keys silently. |
| Patch step fails with merge error | Container name must be `server`; verify with `kubectl --context=kind-atlasops-local get deploy frontend -o jsonpath='{.spec.template.spec.containers[0].name}'`. |
| `curl` connection refused on 16686 | Port-forward died; re-run it. Confirm service exists: `kubectl --context=kind-atlasops-local get svc -n jaeger jaeger`. |

## Cleanup

```bash
bash infra/local/teardown_local.sh
```

(Jaeger is in-memory; all traces are intentionally lost on teardown.)

## After verification passes

1. Record the evidence (commands + outputs + timestamps) under
   `artifacts/evidence/stage3/` per AGENTS.md experiment-evidence rules.
2. Update MASTER_PIPELINE_STATUS.md item B heading from
   `[ROOT CAUSE IDENTIFIED; EXPORT PATH IMPLEMENTED; LIVE VERIFICATION PENDING]`
   to `[LIVE VERIFIED ...]` with date + evidence pointer.
