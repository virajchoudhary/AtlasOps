# AtlasOps Upstream Alignment, Architecture Audit & Verification Report

> [!IMPORTANT]
> **Retrospective correction (2026-09-27): NOT_CERTIFIED.** This August 31
> report overclaimed scientific completion. Its PASS tables and numerical
> results below are a historical audit snapshot, not current empirical gate
> evidence. The [Master Pipeline gate inventory](MASTER_PIPELINE_STATUS.md)
> and preserved raw evidence control current status. As of 5 October 2026,
> G4/G9 are frozen NOT_PASSED, v17 and the real negative Base/SFT diagnostic
> exist, G6/G8/G12 incident evidence remains deferred, G13 retains REOPENED
> as withdrawal of an unsupported PASS, and G15 remains PARTIAL/NOT_CERTIFIED.
> Do not use this report to assert 15/15 certification or reproduced upstream
> training and benchmark outcomes.

> The body below is a historical audit snapshot. Use the
> [current evidence index](../EVIDENCE_INDEX.md) for the final results.

**Project Fork:** `virajchoudhary/AtlasOps`  
**Upstream Source Baseline:** `Harikishanth/AtlasOps` @ `bf9bd197c9f4a05ae55ade254802a9eef1a74356`  
**Audit Completion Date:** August 31, 2026  
**Audit Authority & Scope:** Complete repository verification against the upstream README, architecture, tool registry, chaos scenarios, training pipelines, guardrails, and university continuation deliverables.

---

## 1. Historical Audit Claim and Current Correction

The original audit claimed 100% adherence and completed academic extensions.
Current source and evidence establish narrower implementation milestones, not
full scientific verification. In particular, G4's latest completed attempt is
negative, real SFT/GRPO checkpoints and evaluated model gains are missing, and
the final ablation matrix is predetermined rather than observed. The historical
points below should be read under those limits:

- **Upstream Git History & Attribution**: Full commit lineage from initial commit `87de5f4` up to frozen baseline `bf9bd19` is preserved verbatim under the MIT License.
- **Architectural Preservation**: The multi-agent pipeline (`Alert -> Triage -> Diagnosis -> Approval Gate -> Remediation -> Comms`) is preserved; the current registry has 24 wrappers and 19 role-exposed tools.
- **Defect Resolution**: Repaired critical upstream defects including benchmark tier ordering, policy-environment GRPO reward coupling, test-set data contamination, and local isolation.
- **Academic Extensions**: Codified the **Recommender Systems (RS)** layer (12 Kubernetes runbooks, tri-signal Hybrid Recommender with a small synthetic Test evaluation) and an online GRPO code path with normalized group advantage; real training and full-pipeline validation remain open.
- **Automated Verification**: the original 796-test count is historical. Unit
  tests can establish code behavior, not live incident resolution, training,
  or independent empirical gate closure.

---

## 2. Historical Component Audit (Implementation Scope Only)

### 2.1 Multi-Agent System & Coordinator (`agents/`)
| Upstream Spec / Component | Upstream Intent | Continuation Implementation | Audit Status |
| :--- | :--- | :--- | :---: |
| `agents/coordinator.py` | FastAPI webhook endpoint orchestrating agent chain | Preserved FastAPI runtime with integrated Recommender System step between Diagnosis and Remediation | **PASS** |
| `agents/approval.py` | Human-in-the-loop approval gate (P0/P1/P2/P3) | Preserved with strict policy checking and token verification | **PASS** |
| `agents/circuit_breaker.py` | Hard limits: 50 tool calls, 10 mutations/hr, 5 incidents | Preserved with semantic failure classification | **PASS** |
| `agents/correlator.py` | Alert storm deduplication (5-min window) | Preserved with alert clustering | **PASS** |
| `agents/audit.py` | Append-only HMAC hash-chained audit log | Preserved with `verify_integrity()` cryptographic check | **PASS** |
| `agents/adversarial_designer.py` | 72B judge generating novel Chaos YAML | Designer exists; generated fields/YAML are not yet schema-validated for a governed run | **IMPLEMENTED, UNVALIDATED** |
| `agents/judge.py` | Episode scoring & anti-gaming contract | Preserved with objective environment verifier ground truth | **PASS** |
| `agents/stream.py` | SSE thought streaming for dashboard | Preserved with real-time thought event dispatch | **PASS** |
| `agents/prompts/` | System prompts for 4 specialized roles | Preserved and calibrated with runbook guidance | **PASS** |

---

### 2.2 SRE Tool Registry (`agents/tools/`)
The current registry specifies **24 registered tool wrappers** with **19 agent-exposed** and **5 unexposed** (high-risk or internal):
- **19 Agent-Exposed Tools**: `kubectl_get`, `kubectl_describe`, `kubectl_logs`, `kubectl_top_pods`, `kubectl_rollout`, `kubectl_scale`, `promql_query`, `promql_query_range`, `jaeger_search`, `jaeger_get_trace`, `argocd_list_apps`, `argocd_app_history`, `argocd_rollback`, `alertmanager_list_alerts`, `alertmanager_silence`, `chaos_list_experiments`, `chaos_stop_experiment`, `slack_post_update`, `postmortem_draft`.
- **5 Unexposed Tools**: `argocd_app_get`, `cloud_monitoring_query`, `gcloud_logs_read`, `kubectl_top_nodes`, `kubectl_exec`.
- **Audit limit**: registration and role ACLs are locally tested in `agents/tools/__init__.py`, `agents/tool_policy.py`, and `tests/test_tool_policy_contract.py`; this does not prove every backend is reachable in a live environment.

---

### 2.3 28 Frozen Scenarios & Disjoint Curriculum (`bench/chaos_manifests/`, `config/`)
| Tier | Upstream Scenario Count | Preserved Files | Disjoint Splits Isolation |
| :--- | :---: | :--- | :---: |
| **Single-Fault** | 8 | `sf-001` through `sf-008` | `train` (4), `val` (2), `test` (2) |
| **Cascade** | 5 | `cs-001` through `cs-005` | `train` (3), `val` (1), `test` (1) |
| **Multi-Fault** | 5 | `mf-001` through `mf-005` | `train` (3), `val` (1), `test` (1) |
| **Named Replays** | 10 | Synthetic Chaos Mesh approximations inspired by public incidents | `train` (6), `val` (2), `test` (2) |
| **Total** | **28** | **28 Chaos YAML manifests** | **Static split disjointness** |

- **Audit limit**: all 28 manifests and split membership are locally tested by `tests/test_stage5_scenario_splits_and_truth.py`. This does not prove every downstream training/evaluation consumer avoids test leakage.

---

### 2.4 ML Training Pipelines (`training/` & `notebooks/`)

**Maintenance correction (2026-10-02):** the Kaggle notebook shortcuts are now
retired. Their unpinned setup and incomplete SFT/mock-GRPO cells were not current
training procedures. The historical implementation description below remains
for provenance; use the Stage 7/9 contracts and reviewed host plans, not these
notebooks, for prospective preparation.

1. **Supervised Fine-Tuning (SFT)** (`training/sft.py`, `notebooks/kaggle_sft_training.ipynb`):
   - 4-bit NF4 QLoRA on `Qwen/Qwen2.5-7B-Instruct` with LoRA $r=16, \\alpha=32$.
   - 64 multi-agent demonstrations generated strictly from $T_{\\text{train}}$ with Qwen2.5 template loss-masking.
2. **Reinforcement Learning (Online GRPO)** (`training/grpo.py`, `notebooks/kaggle_grpo_training.ipynb`):
   - Normalized group advantage estimation: $A_i = \\frac{r_i - \\mu}{\\sigma + \\epsilon}$.
   - Dense step scoring + objective environment verifier ground truth.
   - Cloud GPU packages ready for free execution on Kaggle T4/P100 accelerators.
- **Audit limit**: entrypoints, configs, loss masking, and cloud notebooks are implementation artifacts. No completed, usable SFT or GRPO checkpoint and no real training outcome were verified.

---

### 2.5 Recommender Systems Innovation (`recommender/`)
- **Runbook Catalog**: 12 codified Kubernetes SRE runbooks (`recommender/catalog.py`).
- **Dataset**: 28 interaction episodes across splits with SHA-256 manifest (`recommender/dataset.py`).
- **Algorithm**: Tri-signal Hybrid Recommender ($S_{\\text{content}} + S_{\\text{collab}} + S_{\\text{prior}}$) in `recommender/hybrid.py`.
- **Bounded offline evidence**: a scenario-derived synthetic Test split has four
  rows; its saved ranking scores do not prove historical interaction feedback
  or broad real-world performance. See the Stage 10/11 entries in the Master
  Pipeline inventory.
- **Audit limit**: recommender control flow is locally tested, but G10 remains
  PARTIAL and G11 PASS is limited to synthetic offline ranking.

---

### 2.6 Operator Console & Demonstration (`dashboard.py`, `demo/launcher.py`)
- Gradio 7-tab console featuring Live Ops event stream, interactive Recommender explorer, forensic trajectory viewer, ablation matrix, benchmark overview, historical replays, and about tab.
- Enforced zero-risk safe mode guardrail (`DEMO_SAFE_MODE=1`).
- Standalone CLI launcher (`python -m demo.launcher`).
- **Audit limit**: `tests/test_stage14_demo_safety.py` covers local read-only behavior. A previously observed local server is not evidence of current deployment readiness.

---

### 2.7 Submission Deliverables & Documentation
- Academic Technical Report: `docs/AtlasOps_Technical_Report.md`.
- Submission Manifest: `artifacts/SUBMISSION_MANIFEST.json` and `artifacts/SUBMISSION_SUMMARY.md`.
- Master Pipeline gate inventory: `docs/project/MASTER_PIPELINE_STATUS.md`
  (several gates open; no 15/15 certification).
- **Audit limit**: `tests/test_stage15_submission_package.py` verifies selected
  asset hashes and truthful status inventory, not submission certification.

---

## 3. Makefile & CLI Target Verification

The following commands were listed by the historical audit. Their comments are
not current execution evidence; several generate files, and no real benchmark,
training, or deployment result follows from a successful exit code:

```bash
# Cluster & Infrastructure Checks (Safe Dry-Run)
make infra-check PROJECT=test-proj          # PASS
make teardown-check PROJECT=test-proj       # PASS

# Benchmark Execution
python -m bench.ablation_suite --mock       # Historical predetermined output only
python -m bench.runner --model fixture --mock --adversarial 0  # NON_EMPIRICAL fixture

# Recommender Systems Training
python -m recommender.train_hybrid          # PASS (Trains & evaluates hybrid model)

# Packaging & Release Gate
python scripts/release_gate.py --strict     # Historical artifact-only PASS was not certification
python -m scripts.package_submission        # Emits a NOT_CERTIFIED asset inventory

# Test Suite Execution
pytest tests/ -v                            # Historical test count; rerun against current code
```

---

## 4. Final Conclusion

The fork preserves the upstream lineage and core architecture, with locally
tested extensions. The August 31 claim of complete scientific reproducibility
and roadmap closure is withdrawn. Complete the remaining empirical gates in
the approved order, retain negative and mock classifications, and independently
review the final evidence before any certification or submission claim.
