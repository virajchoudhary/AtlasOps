# AtlasOps Experiment & Evaluation Registry

This registry tracks all material empirical runs, benchmarks, and multi-agent evaluations conducted on AtlasOps under the governance rules of `AGENTS.md` and *Master Implementation Pipeline v1.1 Free-First*.

---

## Experiment Summary Index

| Run ID | Date / Time (UTC) | Pipeline Gate | Scenario / Dataset | Model / Checkpoint | Inference Provider | Hardware / Environment | `env_resolved` | Verdict | External Spend |
|---|---|---|---|---|---|---|---|---|---|
| `EXP-STAGE4-SF002-001` | 2026-08-17T05:45:27Z | **Gate G4** | `single_fault/sf-002` | `qwen2.5:1.5b` (ID: `65ec06548149`) | Ollama Local Container (`http://localhost:11434/v1`) | Local Kind `atlasops-local` / Windows 11 / Docker Desktop / GTX 1650 | **True** | **PASS** | **$0.00** |

---

## Detailed Experiment Logs

### Run `EXP-STAGE4-SF002-001` (Stage 4 Golden Incident)

- **Timestamp**: `2026-08-17T05:45:27Z` to `2026-08-17T05:49:36Z` (Duration: 248.35s)
- **Pipeline Stage / Gate**: Stage 4 / Gate G4
- **Branch**: `feat/stage4-golden-incident`
- **Target Main Baseline SHA**: `35463d185eb04a3d3b23beadc360d23a48b65f13`
- **Scenario ID**: `single_fault/sf-002` (`sf-002-paymentservice-cpu`)
- **Tier**: `single_fault`
- **Fault Type**: Chaos Mesh `StressChaos` CPU stress on `paymentservice` in `default` namespace
- **Inference Setup**:
  - Model: `qwen2.5:1.5b` (986 MB weights)
  - Engine: Ollama in Docker container (`ollama/ollama:latest`)
  - Endpoint: `http://localhost:11434/v1` (OpenAI-compatible chat completion API)
  - Temperature: default (coordinator agent loop)
  - Approval timeout: 2s (automated evaluation policy)
- **Environment**:
  - Kubernetes: Kind `atlasops-local` (v1.31.0 on Docker Desktop 29.7.2)
  - Target Service: Online Boutique `paymentservice` (12 microservices running)
  - Tool Backends: Prometheus (port 19090), Alertmanager (port 19093), Jaeger (port 16686), Argo CD (port 18080)
  - Hardware: Intel i5 / 16 GB Host RAM / NVIDIA GeForce GTX 1650 (4 GB VRAM)
- **Multi-Agent Trajectory**:
  - **Incident ID**: `inc-1786945537-cb75cb`
  - **Triage Agent**: Classified as `P1` `Partial Service Outage`, determined blast radius across microservices, identified correlated alerts (`HighErrorRate`, `PodOOMKilled`), routed to `diagnosis`.
  - **Diagnosis Agent**: Emitted PromQL queries, Jaeger traces, kubectl log inspections, identified config/resource anomaly, recommended rollback.
  - **Safety / Approval Gate**: Triggered approval request token `apr-4fe1b5042456`, evaluated via timeout auto-approval policy.
  - **Remediation Agent**: Executed remediation action (`argocd_rollback` on affected service).
  - **Fault Clearance**: StressChaos resource deleted from `chaos-mesh` namespace.
  - **Objective Environment Verifier**: `agents.verifier.verify_environment` independently evaluated cluster ground truth:
    - `workload_paymentservice_ready`: `passed: True` (`Ready replicas: 1/1 (available: 1)`)
    - `chaos_mesh_cleared`: `passed: True` (`Zero active Chaos Mesh experiment resources present in cluster`)
    - `env_resolved`: `True`
    - `is_false_resolution`: `False`
  - **Comms Agent**: Executed after verifier; generated postmortem document and incident dashboard summary with lessons learned.
- **Evidence Artifact**:
  - Saved Manifest: `artifacts/evidence/stage4/golden_incident_sf002_manifest.json`
- **Outcome**: **PASS** (Gate G4 closure criteria fully met with objective environment recovery).
- **Cost**: **$0.00**
