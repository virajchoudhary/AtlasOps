# AtlasOps Stage 3 / Gate G3 Operator Guide & Preflight Package

**Document Version:** 1.0  
**Specification Reference:** Master Implementation Pipeline v1.0 (Stage 3 / Gate G3)  
**Status:** READY FOR OPERATOR ACTIVATION (Cloud Mutation Blocked Pending Operator Authorization)

---

## 1. Overview & Purpose

This document provides the exact contract, input parameters, secret architecture, and end-to-end execution sequence required to execute **Stage 3 (Controlled SRE Environment)** on Google Kubernetes Engine (GKE) and formally validate **Gate G3**.

> [!IMPORTANT]
> **Zero-Cost & No-Cloud Safety Rule**:
> No cloud resources will be provisioned, no GCP APIs enabled, and no gcloud logins performed until the operator explicitly runs the Stage 3 sequence. All previous stages (G0, G1, G2, Runtime Truth, G3 Static Readiness) are 100% validated offline with $0.00 cloud billing.

---

## 2. Operator Input Contract

Before executing `infra/setup.sh --apply`, the operator must supply the following environment variables.

### 2.1 GCP Infrastructure Parameters

| Variable | Requirement / Format | Origin | Notes |
| :--- | :--- | :--- | :--- |
| `PROJECT_ID` / `ATLASOPS_GCP_PROJECT` | `^[a-z][a-z0-9-]{4,28}[a-z0-9]$` | Cloud state | Existing GCP project with billing enabled |
| `REGION` / `ATLASOPS_GKE_REGION` | e.g. `us-central1` | Operator choice | GCP compute region |
| `ZONE` / `ATLASOPS_GKE_ZONE` | e.g. `us-central1-a` | Operator choice | Zonal development cluster constraint |
| `CLUSTER_NAME` / `ATLASOPS_GKE_CLUSTER` | `^[a-z]([a-z0-9-]{0,38}[a-z0-9])?$` | Operator choice | Default: `atlasops-cluster` |
| `ATLASOPS_GKE_NODE_SERVICE_ACCOUNT` | `<name>@<project>.iam.gserviceaccount.com` | Cloud state | Dedicated, least-privilege node service account |
| `ATLASOPS_GKE_AUTHORIZED_NETWORKS` | Comma-separated IPv4 CIDRs (e.g. `203.0.113.10/32`) | Operator network | `0.0.0.0/0` strictly rejected by preflight |

### 2.2 Coordinator Runtime Parameters

| Variable | Requirement / Format | Origin | Notes |
| :--- | :--- | :--- | :--- |
| `ATLASOPS_COORDINATOR_IMAGE` | Pinned digest `registry/image@sha256:<64-hex>` | Registry build | Built from `Dockerfile.coordinator` |
| `ATLASOPS_BACKEND` | `vllm`, `fireworks`, or `openai` | Operator choice | Default: `vllm` |
| `ATLASOPS_VLLM_BASE` | HTTP(S) URL without metacharacters | Local/Remote | Endpoint for agent inference |
| `ATLASOPS_AGENT_MODEL` | Valid model identifier | Operator choice | Model name (e.g. `Qwen/Qwen2.5-7B-Instruct`) |

### 2.3 Required Secret Contracts

All secrets are provisioned out-of-band in the cluster before runtime deployment:

#### `default/atlasops-coordinator-secrets`
- `atlasops-audit-secret`: HMAC key for signing audit log entries (generate with `scripts/generate_runtime_secrets.py`).
- `alertmanager-webhook-secret`: Bearer token for authenticating Alertmanager webhook posts.
- `atlasops-api-key`: Header secret (`X-AtlasOps-Key`) for operator approval endpoints.
- `argocd-user`: Dedicated read-only Argo CD username (e.g. `atlasops-readonly`).
- `argocd-pass`: Password for the Argo CD read-only user.
- `llm-api-key` (*Optional*): Required only if `ATLASOPS_BACKEND` is `fireworks` or `openai`.

#### `monitoring/atlasops-alertmanager-webhook`
- `alertmanager-webhook-secret`: Must match the value in `default/atlasops-coordinator-secrets`.

> [!CAUTION]
> **Secret Hygiene Rules**:
> 1. Never paste real secret values into documentation, Git commits, or pull requests.
> 2. Never echo or print secret values in shell history or CI logs.
> 3. Use `scripts/generate_runtime_secrets.py` to generate cryptographically strong local secret files (`secrets/` is `.gitignored`).

---

## 3. End-to-End Stage 3 Execution Sequence (Gates 3A–3K)

```mermaid
flowchart TD
    G3A["Gate 3A: Local CLI Tools Verified"] --> G3B["Gate 3B: gcloud auth login"]
    G3B --> G3C["Gate 3C: Select Existing GCP Project"]
    G3C --> G3D["Gate 3D: Verify Billing, IAM, Quotas & CIDRs"]
    G3D --> G3E["Gate 3E: Build & Publish Immutable Coordinator Image"]
    G3E --> G3F["Gate 3F: Run infra/setup.sh --check (Preflight)"]
    G3F --> G3G["Gate 3G: Explicit Cost Acknowledgement"]
    G3G --> G3H["Gate 3H: Run infra/setup.sh --apply"]
    G3H --> G3I["Gate 3I: Run G3 Non-Destructive Acceptance Plan"]
    G3I --> G3J["Gate 3J: Record Evidence in MASTER_PIPELINE_STATUS.md"]
    G3J --> G3K["Gate 3K: Run infra/teardown.sh --apply"]
```

### Gate 3A: Local Toolchain Verification
Verify local CLI tool versions:
```bash
git --version
bash --version
gcloud version
kubectl version --client
helm version
docker version
```

### Gate 3B: Operator GCP Authentication
Authenticate the operator account against Google Cloud:
```bash
gcloud auth login
```

### Gate 3C: Select Existing Project
Set and confirm the target project ID:
```bash
export PROJECT_ID="<your-project-id>"
gcloud config set project "$PROJECT_ID"
```

### Gate 3D: Confirm Billing, Node SA, and Quotas
Verify that billing is enabled and required identities exist:
```bash
gcloud billing projects describe "$PROJECT_ID"
gcloud iam service-accounts describe "<node-sa>@$PROJECT_ID.iam.gserviceaccount.com"
```

### Gate 3E: Build and Publish Coordinator Image
Build and push the coordinator image to a private container registry, then capture its exact sha256 digest:
```bash
# Build container locally
docker build -f Dockerfile.coordinator -t "gcr.io/$PROJECT_ID/atlasops-coordinator:g3-rc" .

# Authenticate docker and push
gcloud auth configure-docker
docker push "gcr.io/$PROJECT_ID/atlasops-coordinator:g3-rc"

# Extract immutable SHA256 digest
export ATLASOPS_COORDINATOR_IMAGE=$(docker inspect --format='{{index .RepoDigests 0}}' "gcr.io/$PROJECT_ID/atlasops-coordinator:g3-rc")
echo "Pinned Image Digest: $ATLASOPS_COORDINATOR_IMAGE"
```

### Gate 3F: Read-Only Preflight (`--check`)
Run read-only preflight validation to verify compatibility without cloud mutations:
```bash
bash infra/setup.sh "$PROJECT_ID" us-central1 atlasops-cluster --check
```

### Gate 3G: Explicit Cost Authorization
Set the required cost acknowledgement environment variable:
```bash
export ATLASOPS_COST_ACK="ACKNOWLEDGE_STAGE_1D_BILLING"
```

### Gate 3H: Apply Infrastructure Foundation (`--apply`)
Create cluster, deploy Online Boutique, coordinator, Prometheus/Alertmanager, Jaeger, Argo CD, and Chaos Mesh:
```bash
# 1. Provision cluster and core foundation
bash infra/setup.sh "$PROJECT_ID" us-central1 atlasops-cluster --apply

# 2. Provision runtime secrets before coordinator pod start
python scripts/generate_runtime_secrets.py
bash secrets/apply_secrets.sh
```

### Gate 3I: Non-Destructive G3 Backend Acceptance
Follow the formal procedures in [`docs/project/G3_ACCEPTANCE_PLAN.md`](G3_ACCEPTANCE_PLAN.md):
1. **Kubernetes & Boutique**: 12 deployments Available in `default` namespace.
2. **Prometheus**: Vector queries verify `availableReplicas == replicas` for all 12 services.
3. **Alertmanager**: Firing alert dispatched to coordinator `/webhook` responds HTTP 200 `{"ok": true, ...}`.
4. **Jaeger**: `jaeger_services_list()` responds HTTP 200 with query backend active.
5. **Argo CD**: `argocd_list_apps()` authenticates via in-cluster ClusterIP and returns `[]`.
6. **Chaos Mesh**: All 28 frozen manifests in `bench/chaos_manifests/` pass `kubectl apply --dry-run=server`.

### Gate 3J: Record Gate G3 PASS Evidence
Update `docs/project/MASTER_PIPELINE_STATUS.md` with:
- GKE Cluster name, zone, and node topology.
- Immutable coordinator image digest.
- Timestamps and raw response outputs from each acceptance check.
- Mark Gate G3 as **PASS**.

### Gate 3K: Teardown & Cost Guardrails
When testing is complete, destroy all cloud resources to prevent ongoing charges:
```bash
ATLASOPS_TEARDOWN_ACK=DELETE_ATLASOPS_DEVELOPMENT_RESOURCES \
bash infra/teardown.sh "$PROJECT_ID" us-central1 atlasops-cluster --apply
```
- Verify 0 remaining GKE clusters: `gcloud container clusters list`
- Confirm $0 ongoing compute billing.
