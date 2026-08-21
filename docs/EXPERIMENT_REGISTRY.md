# AtlasOps Experiment & Evaluation Registry

This registry tracks all material empirical runs, benchmarks, and multi-agent evaluations conducted on AtlasOps under the governance rules of `AGENTS.md` and *Master Implementation Pipeline v1.1 Free-First*.

---

## Experiment Summary Index

| Run ID | Date / Time (UTC) | Pipeline Gate | Scenario / Dataset | Model / Checkpoint | Inference Provider | Hardware / Environment | `env_resolved` | Verdict | External Spend |
|---|---|---|---|---|---|---|---|---|---|
| `EXP-STAGE4-SF002-001` | 2026-08-17T05:45:27Z | **Gate G4** | `single_fault/sf-002` | `qwen2.5:1.5b` (ID: `65ec06548149`) | Ollama Local Container (`http://localhost:11434/v1`) | Local Kind `atlasops-local` / Windows 11 / Docker Desktop / GTX 1650 | **True** (Harness Forced) | **INVALID** | **$0.00** |
| `EXP-STAGE4-SF002-002` | 2026-08-17T15:23:07Z | **Gate G4** | `single_fault/sf-002` | `qwen2.5:1.5b` (ID: `65ec06548149`) | Ollama Local Container (`http://localhost:11434/v1`) | Local Kind `atlasops-local` / Windows 11 / Docker Desktop / GTX 1650 | **False** (Unfinetuned Baseline) | **FAIL (Valid Baseline)** | **$0.00** |
| `EXP-STAGE4-SF002-003` | 2026-08-17T16:50:28Z | **Gate G4** | `single_fault/sf-002` | `qwen2.5:1.5b` (ID: `65ec06548149`) | Ollama Local Container (`http://localhost:11434/v1`) | Local Kind `atlasops-local` / Windows 11 / Docker Desktop / GTX 1650 | **False** (Unfinetuned Baseline) | **FAIL (Valid Baseline)** | **$0.00** |

---

## Detailed Experiment Logs

### Run `EXP-STAGE4-SF002-003` (Stage 4 Causal Golden Incident Rerun with Retry & Allowlist) — [FAIL (Valid Baseline)]

> [!NOTE]
> **Development Diagnostic & Baseline Status**:
> This run validated the complete causal runtime contract:
> 1. Namespace allowlist `ALLOWED_CHAOS_NAMESPACES = frozenset({'chaos-mesh'})` was enforced.
> 2. Generic remediation execution retry was dispatched when the model returned without executing a mutating tool.
> 3. Strict output sanitization normalized the outcome to `unresolved` and `executed_actions` to `[]` when the unfinetuned model proposed an invalid invocation (`PodChaos` on `default` namespace) which was blocked by coordinator policy checks.
> 4. Objective environment verifier evaluated the cluster state directly (`env_resolved: False` because `StressChaos` remained active).
> 5. All 15 causal predicates evaluated cleanly with no harness-level interference.
>
> **Important Split & Governance Notes**:
> 1. `EXP-STAGE4-SF002-003` is a development diagnostic / failed Gate G4 attempt; it does not close Gate G4.
> 2. Scenario `single_fault/sf-002` is development-exposed and **MUST NOT** later be assigned to the unseen final-test population during Stage 5 split freeze.

- **Timestamp**: `2026-08-17T16:50:28Z` to `2026-08-17T17:00:17Z` (Duration: 589.11s)
- **Pipeline Stage / Gate**: Stage 4 / Gate G4
- **Branch**: `fix/stage4-causal-golden-incident`
- **Target Main Baseline SHA**: `b5e969a73ec7797f7abbb66bc3a649a96f23e5da`
- **Scenario ID**: `single_fault/sf-002` (`sf-002-paymentservice-cpu`)
- **Tier**: `single_fault`
- **Fault Type**: Chaos Mesh `StressChaos` CPU stress on `paymentservice` in `default` namespace (4 workers, 90% load, 10m duration)
- **Trigger Type**: Manual coordinator trigger over a real independently observed cluster fault
- **Inference Setup**:
  - Model: `qwen2.5:1.5b` (986 MB weights)
  - Engine: Ollama (`ollama/ollama:latest` / v0.32.14)
  - Endpoint: `http://localhost:11434/v1` (OpenAI-compatible chat completion API)
- **Environment**:
  - Kubernetes: Kind `atlasops-local` (v1.31.0 on Docker Desktop 29.7.2)
  - Target Service: Online Boutique `paymentservice` (12 microservices running)
  - Tool Backends: Prometheus (port 19090), Alertmanager (port 19093), Jaeger (port 16686), Argo CD (port 18080)
  - Hardware: Intel i5 / 16 GB Host RAM / NVIDIA GeForce GTX 1650 (4 GB VRAM)
- **Multi-Agent Execution**:
  - **Incident ID**: `inc-1786985440-86e668`
  - **Triage Agent**: Severity `P1`, Title `High CPU Usage on Paymentservice`, blast radius `paymentservice` in `default` namespace.
  - **Diagnosis Agent**: Identified paymentservice CPU anomaly, noted `sf-002-paymentservice-cpu` in `chaos-mesh` namespace (`kubectl_describe` evidence).
  - **Approval Gate**: Passed.
  - **Remediation Agent**: Model proposed `chaos_stop_experiment(kind='PodChaos', name='paymentservice-1', namespace='default')`; rejected by coordinator policy check (namespace `default` not in `chaos-mesh` allowlist; target resource not found). Coordinator normalized outcome to `unresolved` and `executed_actions` to `[]`.
  - **Objective Environment Verifier**:
    - `workload_paymentservice_ready`: `passed: True`
    - `alerts_cleared`: `passed: True`
    - `chaos_mesh_cleared`: `passed: False` (`Active scenario chaos experiments remain: StressChaos/sf-002-paymentservice-cpu`)
    - `env_resolved`: `False`
    - `is_false_resolution`: `False`
  - **Comms Agent**: Executed post-verification; generated postmortem document and incident dashboard summary.
- **15-Point Causal Predicate Evaluation**:
  - `[PASS] 1_baseline_healthy`
  - `[PASS] 2_injection_success`
  - `[PASS] 3_fault_observed_pre_trigger`
  - `[PASS] 4_trigger_delivered`
  - `[PASS] 5_triage_valid`
  - `[PASS] 6_diagnosis_valid`
  - `[PASS] 7_diagnosis_truth_match`
  - `[PASS] 8_approval_satisfied`
  - `[FAIL] 9_remediation_mutating_tool_executed`
  - `[FAIL] 10_remediation_tool_success`
  - `[FAIL] 11_remediation_target_match`
  - `[PASS] 12_no_harness_repair_pre_verification`
  - `[FAIL] 13_objective_env_resolved`
  - `[PASS] 14_comms_executed`
  - `[PASS] 15_evidence_persisted`
- **Evidence Artifact**:
  - Saved Manifest: `artifacts/evidence/stage4/golden_incident_sf002_manifest.json`
- **Outcome**: **FAIL (Causally Valid Baseline / Failed G4 Attempt)**
- **Cost**: **$0.00**

---

### Run `EXP-STAGE4-SF002-002` (Stage 4 Causal Golden Incident Rerun) — [FAIL (Valid Baseline)]

> [!NOTE]
> **Development Diagnostic & Baseline Status**:
> This run executed with strict causal validity: no harness pre-verification deletions, no forced resolution logic (`or True`), and rigorous separation between model proposal and real tool execution. The unfinetuned `qwen2.5:1.5b` model correctly identified the paymentservice CPU fault and recommended `chaos_stop_experiment` in text output, but failed to emit function-calling invocations within the coordinator tool loop. Because the fault was not cleared by AtlasOps tool actions, the objective environment verifier truthfully recorded `env_resolved: False`.
> 
> **Important Split & Governance Notes**:
> 1. `EXP-STAGE4-SF002-002` is a development diagnostic and failed Gate G4 attempt; it does not close Gate G4.
> 2. It is NOT the canonical Stage 6 zero-shot benchmark because Stage 5 splits have not yet been frozen.
> 3. Scenario `single_fault/sf-002` has now been development-exposed and **MUST NOT** later be assigned to the unseen final-test population during Stage 5 split freeze.

- **Timestamp**: `2026-08-17T15:23:07Z` to `2026-08-17T15:26:46Z` (Duration: 218.30s)
- **Pipeline Stage / Gate**: Stage 4 / Gate G4
- **Branch**: `fix/stage4-causal-golden-incident`
- **Target Main Baseline SHA**: `b5e969a73ec7797f7abbb66bc3a649a96f23e5da`
- **Scenario ID**: `single_fault/sf-002` (`sf-002-paymentservice-cpu`)
- **Tier**: `single_fault`
- **Fault Type**: Chaos Mesh `StressChaos` CPU stress on `paymentservice` in `default` namespace (4 workers, 90% load, 10m duration)
- **Trigger Type**: Manual coordinator trigger over a real independently observed cluster fault
- **Inference Setup**:
  - Model: `qwen2.5:1.5b` (986 MB weights)
  - Engine: Ollama (`ollama/ollama:latest`)
  - Endpoint: `http://localhost:11434/v1` (OpenAI-compatible chat completion API)
- **Environment**:
  - Kubernetes: Kind `atlasops-local` (v1.31.0 on Docker Desktop 29.7.2)
  - Target Service: Online Boutique `paymentservice` (12 microservices running)
  - Tool Backends: Prometheus (port 19090), Alertmanager (port 19093), Jaeger (port 16686), Argo CD (port 18080)
  - Hardware: Intel i5 / 16 GB Host RAM / NVIDIA GeForce GTX 1650 (4 GB VRAM)
- **Multi-Agent Execution**:
  - **Incident ID**: `inc-1786980196-4c46b5`
  - **Triage Agent**: Severity `P1`, Title `High CPU Usage on Paymentservice`, blast radius `paymentservice` in `default`.
  - **Diagnosis Agent**: Identified paymentservice CPU anomaly, suggested `chaos_stop_experiment('stresschaos', 'chaos-mesh')`.
  - **Approval Gate**: Passed.
  - **Remediation Agent**: Proposed action `chaos_stop_experiment(kind='StressChaos', name='sf-002-paymentservice-cpu', namespace='chaos-mesh')` in final text output, but did not execute via active tool call step.
  - **Objective Environment Verifier**:
    - `workload_paymentservice_ready`: `passed: True`
    - `chaos_mesh_cleared`: `passed: False` (`Active Chaos Mesh experiment sf-002-paymentservice-cpu still present`)
    - `env_resolved`: `False`
    - `is_false_resolution`: `False`
  - **Comms Agent**: Executed post-verification; generated postmortem document and incident dashboard summary.
- **15-Point Causal Predicate Evaluation**:
  - `[PASS] 1_baseline_healthy`
  - `[PASS] 2_injection_success`
  - `[PASS] 3_fault_observed_pre_trigger`
  - `[PASS] 4_trigger_delivered`
  - `[PASS] 5_triage_valid`
  - `[PASS] 6_diagnosis_valid`
  - `[PASS] 7_diagnosis_truth_match`
  - `[PASS] 8_approval_satisfied`
  - `[FAIL] 9_remediation_mutating_tool_executed`
  - `[FAIL] 10_remediation_tool_success`
  - `[FAIL] 11_remediation_target_match`
  - `[PASS] 12_no_harness_repair_pre_verification`
  - `[FAIL] 13_objective_env_resolved`
  - `[PASS] 14_comms_executed`
  - `[PASS] 15_evidence_persisted`
- **Evidence Artifact**:
  - Saved Manifest: `artifacts/evidence/stage4/golden_incident_sf002_manifest.json`
- **Outcome**: **FAIL (Causally Valid Baseline)**
- **Cost**: **$0.00**

---

### Run `EXP-STAGE4-SF002-001` (Stage 4 Golden Incident) — [INVALID]

> [!WARNING]
> **Audit Reclassification (INVALID)**:
> Golden-run harness removed the StressChaos resource before objective verification; agent diagnosis/remediation did not match paymentservice CPU scenario truth; `agent_claimed_resolved` was forced true by runner logic (`or True`). The run verifies infrastructure/verifier behavior but is not valid evidence that AtlasOps caused recovery.

- **Timestamp**: `2026-08-17T05:45:27Z` to `2026-08-17T05:49:36Z` (Duration: 248.35s)
- **Pipeline Stage / Gate**: Stage 4 / Gate G4
- **Branch**: `feat/stage4-golden-incident`
- **Target Main Baseline SHA**: `35463d185eb04a3d3b23beadc360d23a48b65f13`
- **Scenario ID**: `single_fault/sf-002` (`sf-002-paymentservice-cpu`)
- **Tier**: `single_fault`
- **Fault Type**: Chaos Mesh `StressChaos` CPU stress on `paymentservice` in `default` namespace
- **Outcome**: **INVALID** (Reclassified due to out-of-band harness fault removal and non-causal remediation).
- **Cost**: **$0.00**
