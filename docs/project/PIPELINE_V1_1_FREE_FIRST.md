# AtlasOps Pipeline v1.1 Free-First — Execution Specification

> **Document version:** v1.1 Free-First
> **Date:** 16 August 2026
> **Supersedes:** Pipeline v1.0 (11 August 2026) for execution decisions.
> **Provenance:** Pipeline v1.0 remains the historical record.

---

## Execution Environment Change

Pipeline v1.1 Free-First changes the canonical execution environment
from mandatory GKE/GCP to a zero-cost local environment:

| Aspect | v1.0 (Historical) | v1.1 Free-First (Current) |
|---|---|---|
| **Kubernetes** | GKE Standard (zonal, e2-standard-4) | Kind local cluster on Docker Desktop/WSL2 |
| **Image delivery** | Artifact Registry `docker push` | `docker build` → `kind load docker-image` |
| **External cost** | GCP billing required | **$0** |
| **Cloud dependency** | `gcloud`, GCP project, billing account | None |
| **Inference** | Paid OpenAI-compatible endpoint | Local Ollama or equivalent free endpoint |
| **LoadBalancer** | GKE cloud LoadBalancer | ClusterIP + `kubectl port-forward` |
| **GKE** | **Required** | **Optional** (preserved as portability code) |

## Canonical Stage 3 Environment (v1.1)

```
Windows 11
  → Docker Desktop / WSL2 Linux containers
    → Kind local Kubernetes (single control-plane node)
      → Online Boutique v0.10.0
      → Prometheus + Alertmanager + kube-state-metrics
      → Jaeger (all-in-one, in-memory)
      → Argo CD (dedicated atlasops account)
      → Chaos Mesh (controllers + CRDs)
      → AtlasOps Coordinator
```

### Local Image Delivery
```
docker build -t atlasops-coordinator:g3-local -f Dockerfile.coordinator .
  → kind load docker-image atlasops-coordinator:g3-local --name atlasops-local
    → imagePullPolicy: Never
```

### Local Inference
```
Ollama (preferred when available)
  → OpenAI-compatible endpoint at http://localhost:11434/v1
  → Small Qwen-family instruct model (sized to host RAM/VRAM)
```

## Forbidden Operations (Zero-Cost Boundary)

The following operations are **forbidden** under Pipeline v1.1:

- `gcloud services enable`
- `gcloud billing ...`
- `gcloud projects create`
- GKE cluster creation
- Artifact Registry creation
- `docker push` to any paid registry
- Cloud SQL provisioning
- Pub/Sub provisioning
- Paid OpenAI/Fireworks/Anthropic API calls
- Paid GPU rental
- Paid Colab
- Paid GitHub Actions runners
- Any purchase or subscription

**External service spend target: $0.**

## Gate G3 Requirements (v1.1)

Gate G3 PASS requires all of:

1. Reproducible local Kubernetes cluster healthy
2. Online Boutique 12 required services healthy
3. Prometheus healthy
4. Alertmanager healthy
5. Jaeger backend healthy
6. Argo CD healthy
7. Chaos Mesh healthy
8. AtlasOps coordinator healthy
9. Required runtime Secrets wired
10. Non-destructive AtlasOps tool call reaches every required backend
11. Evidence recorded
12. Teardown instructions verified
13. **No paid service required**

**GKE IS NOT REQUIRED for G3 PASS.**

## Stage Sequence (Unchanged)

The stage sequence from Pipeline v1.0 remains immutable:

$$\text{G0} → \text{G1} → \text{G2} → \text{G3} → \text{G4} → \text{G5} → \text{G6} → \text{G7} → \text{G8} → \text{G9} → \text{G10} → \text{G11} → \text{G12} → \text{G13} → \text{G14} → \text{G15}$$

Only the execution environment has changed, not the stage definitions or gate criteria.

## Repository Structure

```
infra/
  local/                        ← NEW: Pipeline v1.1 local Kind path
    kind-config.yaml
    coordinator-local.yaml
    setup_local.sh
    teardown_local.sh
    values/
      prometheus-local.yaml
      jaeger-local.yaml
      argocd-local.yaml
      chaos-mesh-local.yaml
  setup_impl.sh                 ← PRESERVED: Pipeline v1.0 GKE path (optional)
  teardown_impl.sh              ← PRESERVED: Pipeline v1.0 GKE teardown (optional)
  kubernetes/
    coordinator.yaml.tmpl       ← PRESERVED: GKE coordinator template (optional)
  values/
    *.yaml                      ← PRESERVED: GKE Helm values (optional)
```
