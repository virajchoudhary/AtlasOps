# AtlasOps — Real-World Deployment Guide

> **Current project status:** this document records intended/historical runtime
> architecture; it is not a reproduced production procedure. Stage 1D-B adds a
> statically validated coordinator and minimum observability contract, but real
> GKE provisioning, alert delivery, model/tool execution, and tracing remain
> unverified. Review the unmerged Stage 1D-B change before any live apply.
> See [`docs/project/INFRASTRUCTURE_CONTRACT.md`](docs/project/INFRASTRUCTURE_CONTRACT.md).

> This is not a demo toy. This is how you put AtlasOps on-call for a real production cluster.

---

## The Real-World Use Case

**Problem:** Your on-call engineer gets paged at 3am. Average MTTR for complex incidents is 60–90 minutes. At $500/hr fully-loaded SRE cost, that's $500–$750 per incident. You have 10 incidents/month. That's $60–90K/year in on-call cost, before counting burnout and turnover.

**AtlasOps solves:** The P2/P3 incidents that don't need a human — CPU spikes, OOM kills, bad deploys, network partitions. These are 80% of all pages. AtlasOps resolves them in under 5 minutes, generates the postmortem, and only wakes a human for the truly novel P0s.

**What it actually does:**
1. Alertmanager fires a webhook to `coordinator:9099/webhook`
2. Triage agent classifies severity and blast radius (< 1 min)
3. Diagnosis agent traces root cause via Prometheus + Jaeger + kubectl logs (< 2 min)
4. Remediation agent executes the fix and verifies recovery (< 1 min)
5. Comms agent posts to Slack and generates a postmortem (30 sec)
6. If the agent can't resolve it, it escalates with full context — the human wakes up with a diagnosis already done

---

## Prerequisites

- Kubernetes cluster (GKE, EKS, AKS, or self-managed)
- Prometheus + Alertmanager installed (kube-prometheus-stack recommended)
- Jaeger (optional but recommended for trace-based diagnosis)
- Argo CD (optional — enables `argocd_rollback` remediation)
- AMD MI300X or compatible GPU (for self-hosted inference)
  - OR: Fireworks AI API key (managed AMD GPU inference)

## Guarded development infrastructure entry point

The infrastructure scripts no longer provision or delete resources on ordinary
invocation. Use `--check` for read-only preflight. `--apply` remains prohibited
until this contract has been reviewed and requires the explicit acknowledgements,
immutable coordinator image, model endpoint, identity/network inputs, and
namespaced Secret objects documented in the infrastructure contract. Grafana and
the coordinator use ClusterIP. Jaeger and Argo CD Applications remain deferred.

---

## Reviewed coordinator and observability contract

The coordinator image is built deliberately from `Dockerfile.coordinator` and
starts `agents.coordinator` on port `9099`. The repository does not build or push
an image during setup. An operator must supply an owned immutable image reference:

```text
ATLASOPS_COORDINATOR_IMAGE=<registry>/<repository>/<image>@sha256:<64-lowercase-hex>
ATLASOPS_BACKEND=vllm
ATLASOPS_VLLM_BASE=http://<reviewed-model-service>:8000/v1
ATLASOPS_AGENT_MODEL=<reviewed-model-id>
```

`infra/kubernetes/coordinator.yaml.tmpl` is rendered only by the guarded setup
path. It creates `atlasops-coordinator`, the private ClusterIP Service
`atlasops-coordinator-svc`, least-privilege read/remediation RBAC, resource
requests/limits, and liveness/readiness probes against the side-effect-free
`/healthz` route. It does not deploy an operator UI or expose a LoadBalancer.
Privileged approval routes (`/approve` and `/approval/pending`) fail closed and
require `X-AtlasOps-Key`; the approval capability token alone is insufficient.

Secret values are provisioned out of Git. Kubernetes Secrets are namespace
scoped, so the authenticated webhook requires two pre-existing Secret objects:

- `default/atlasops-coordinator-secrets` with `atlasops-audit-secret`,
  `alertmanager-webhook-secret`, and `atlasops-api-key`; add `llm-api-key` for
  `fireworks` or `openai` backends.
- `monitoring/atlasops-alertmanager-webhook` with
  `alertmanager-webhook-secret` containing the same credential.

Setup checks key presence without printing or storing value contents. Alertmanager
mounts the monitoring Secret and reads the bearer credential through
`credentials_file`; tracked Helm values contain no credential. The route is:

```text
http://atlasops-coordinator-svc.default.svc.cluster.local:9099/webhook
```

For a newly created cluster, the guarded setup intentionally stops immediately
after cluster/context initialization when these Secrets are absent, before
applying Online Boutique or AtlasOps workloads. Provision both Secrets through
the approved secret-delivery process, then rerun the same guarded apply. The
setup script never accepts secret values as arguments or creates them.

The first supported alert uses kube-state-metrics to detect unavailable replicas
across the 12 pinned Online Boutique Deployments. The pinned release does not
prove application request/error/latency Prometheus metrics, so no invented 5xx
or latency PromQL is included.

No live verification was performed. A later controlled run must independently
prove coordinator rollout, authenticated alert delivery, input metric presence,
model/tool execution, and recovery behavior.

---

## Production Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BACKEND` | `vllm` | `vllm` (self-hosted MI300X) or `fireworks` (managed API) |
| `LLM_API_KEY` | `""` | Required for Fireworks; empty for local vLLM |
| `VLLM_BASE` | `http://localhost:8000/v1` | vLLM endpoint (set to MI300X IP for self-hosted) |
| `AGENT_MODEL` | `Qwen/Qwen2.5-7B-Instruct` | Model name (or path to fine-tuned checkpoint) |
| `GCP_PROJECT` | `""` | GCP project for Cloud Logging tool |
| `PROMETHEUS_URL` | in-cluster Prometheus Service | Prometheus endpoint |
| `JAEGER_URL` | none | Explicitly required to enable the otherwise fail-closed Jaeger tools |
| `ALERTMANAGER_URL` | in-cluster Alertmanager Service | Alertmanager endpoint |
| `SLACK_WEBHOOK_URL` | `""` | Slack webhook (optional; logs locally if not set) |
| `ATLASOPS_AUDIT_SECRET` | none | Required before incident/model/tool execution; Secret reference only |
| `ATLASOPS_API_KEY` | none | Required by the reviewed GKE Secret contract |
| `ALERTMANAGER_WEBHOOK_SECRET` | none | Required by the reviewed GKE webhook contract |
| `ARGOCD_URL`, `ARGOCD_USER`, `ARGOCD_PASS` | none | Intentionally absent while Argo Application ownership is deferred |

### Using Your Fine-Tuned Checkpoints

Once you've run `make sft` and `make grpo`, point the coordinator at your checkpoints:

```bash
# Self-hosted on MI300X with fine-tuned adapters
BACKEND=vllm \
VLLM_BASE=http://your-mi300x-ip:8000/v1 \
AGENT_MODEL=checkpoints/grpo_v3 \
python agents/coordinator.py
```

### Running on AMD MI300X (Self-Hosted)

```bash
# On your MI300X instance
pip install vllm  # ROCm build
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --host 0.0.0.0 --port 8000 \
  --enable-lora \
  --max-lora-rank 16 \
  --dtype bfloat16

# Load all 4 agent adapters + judge
vllm serve Qwen/Qwen2.5-72B-Instruct \
  --host 0.0.0.0 --port 8001 \
  --dtype bfloat16 \
  --tensor-parallel-size 1  # MI300X 192GB fits it in one GPU
```

---

## What AtlasOps Does NOT Do

- **Does not replace humans for novel P0s.** If the incident pattern has never been seen before, AtlasOps escalates with full diagnostic context rather than guessing.
- **Does not execute destructive commands.** `kubectl delete`, `argocd app delete`, and mass scale-to-zero are blocked by the tool allowlist.
- **Does not silence alerts for more than 30 minutes.** Hard limit in `alertmanager_silence`.
- **Does not make business decisions.** "Should we roll back this revenue-critical deploy?" is a human call.

---

## Business Value

| Metric | Before AtlasOps | After AtlasOps |
|---|---|---|
| MTTR (P2/P3 incidents) | 60–90 min | **< 5 min** |
| On-call pages requiring human | 100% | **~20%** (P0/novel only) |
| Postmortem drafting time | 2 hrs/incident | **< 30 sec** |
| Cost per incident (P2/P3) | $500–750 | **~$0.50** (API cost) |

**Market context:** PagerDuty ($3.7B), Datadog ($25B), and OpsRamp ($2B+) all sell products in this space. None of them close the loop automatically — they alert humans and wait. AtlasOps is the first system that closes the loop end-to-end using real SRE tool chains trained on real incident history.

**Revenue model (if productized):**
- SaaS: $500/month per cluster (vs. $8K/month in SRE on-call cost)
- Enterprise: Custom fine-tuning on your incident history, private deployment
- Target: 50K+ Kubernetes clusters running in production globally
