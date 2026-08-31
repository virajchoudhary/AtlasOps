# Stage 10: Build RS Data and Baselines (Gate G10)

This technical specification and governance document records the delivery of Stage 10 under the AtlasOps Master Implementation Pipeline v1.1, launching the **Recommender Systems (RS)** academic workstream.

---

## 1. SRE Runbook Catalog

We codified a structured catalog of 12 production-grade Kubernetes & Cloud-Native SRE Runbooks in `recommender/runbook_catalog.py`:

| Runbook ID | Title | Category | Primary Failure Patterns | Suggested Tool Sequence |
| :--- | :--- | :--- | :--- | :--- |
| `RB-POD-OOM` | Pod Out-Of-Memory Remediation | Resource Exhaustion | `memory`, `oom`, `exit 137` | `promql_query`, `k8s_delete_pod`, `k8s_scale_deployment` |
| `RB-POD-CRASH` | Pod CrashLoopBackOff Remediation | Workload Failure | `crash`, `panic`, `exception` | `kubectl_logs`, `argocd_rollback`, `k8s_delete_pod` |
| `RB-CPU-THROTTLE` | CPU Throttling Mitigation | Resource Exhaustion | `cpu`, `throttle`, `saturation` | `promql_query`, `k8s_scale_deployment` |
| `RB-NET-LOSS` | Network Packet Drop Remediation | Network Failure | `packet loss`, `drop`, `netloss` | `promql_query`, `k8s_delete_pod`, `environment_verify` |
| `RB-NET-DELAY` | Network Latency & Jitter Remediation | Network Failure | `latency`, `delay`, `jitter` | `promql_query`, `kubectl_describe`, `k8s_delete_pod` |
| `RB-NET-CORRUPT` | Network Checksum Corruption | Network Failure | `corrupt`, `checksum`, `bad packet` | `promql_query`, `kubectl_describe`, `k8s_delete_pod` |
| `RB-DISK-FILL` | Ephemeral Disk Pressure Remediation | Storage Failure | `disk`, `storage`, `full`, `fill` | `kubectl_describe`, `promql_query`, `k8s_delete_pod` |
| `RB-IO-DELAY` | Disk I/O Latency Mitigation | Storage Failure | `io`, `iowait`, `storage latency` | `promql_query`, `kubectl_describe`, `k8s_delete_pod` |
| `RB-DNS-FAIL` | CoreDNS Resolution Remediation | Service Discovery | `dns`, `resolve`, `coredns` | `promql_query`, `kubectl_describe`, `k8s_delete_pod` |
| `RB-HTTP-5XX` | HTTP 5xx Gateway Error Recovery | Application Outage | `500`, `502`, `503`, `http 5xx` | `promql_query`, `argocd_sync`, `argocd_rollback` |
| `RB-CASCADE-HEAL` | Cascading Dependency Deadlock | Cascade Failure | `cascade`, `dependency`, `downstream` | `promql_query`, `kubectl_describe`, `k8s_delete_pod` |
| `RB-SCALE-OUT` | Traffic Surge & Queue Autoscaling | Capacity Management | `surge`, `traffic`, `backlog` | `promql_query`, `k8s_scale_deployment` |

---

## 2. Historical Interaction Corpus & Manifest

- **Total Incident-Runbook Interactions**: 28 examples (`data/rs_incident_interactions.jsonl`).
- **Split Distribution**:
  - Training Partition: $|T_{\\text{train}}| = 16$ (57.1%)
  - Validation Partition: $|T_{\\text{val}}| = 6$ (21.4%)
  - Held-Out Test Partition: $|T_{\\text{test}}| = 6$ (21.4%)
- **Cryptographic Provenance**: Corpus LF SHA-256 (`2133eeb34ceac0371c93351fd621a48e2cc96a8a97db7ee0acb8c3dacbc681d4`) registered in `artifacts/evidence/stage10/rs_dataset_manifest.json`.

---

## 3. Evaluation Metric Suite

We implement standard Information Retrieval and Recommender Systems ranking metrics in `recommender/metrics.py`:
- $\\text{Hit}@K$: Fraction of incidents where at least one ground-truth relevant runbook is ranked in the top-$K$.
- $\\text{MRR}@K$: Mean Reciprocal Rank $\\frac{1}{\\text{rank}}$ of the first relevant runbook in top-$K$.
- $\\text{NDCG}@K$: Normalized Discounted Cumulative Gain $\\frac{\\text{DCG}@K}{\\text{IDCG}@K}$.
- $\\text{Precision}@K$ and $\\text{Recall}@K$.

---

## 4. Baseline Recommender Evaluation Matrix

Evaluated on held-out test split ($|T_{\\text{test}}| = 6$):

| Recommender Model | Test Hit@1 | Test Hit@3 | Test MRR@3 | Test NDCG@3 | Test Hit@5 |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Random Recommender** | `16.7%` | `33.3%` | `0.250` | `0.272` | `50.0%` |
| **Popularity Recommender** | `50.0%` | `83.3%` | `0.639` | `0.689` | `100.0%` |
| **BM25 Content Recommender** | **`66.7%`** | **`83.3%`** | **`0.750`** | **`0.772`** | **`100.0%`** |

### Scientific Significance:
- `BM25ContentRecommender` achieves **`66.7%`** Hit@1 and **`0.750`** MRR@3 on the held-out test partition, establishing a strong baseline for the Stage 11 Hybrid Recommender.

---

## 5. Gate G10 Acceptance Criteria

Gate G10 is verified by automated unit tests in `tests/test_stage10_rs_data_and_baselines.py`:
- `test_runbook_catalog_integrity`: **PASS** (12 runbooks verified).
- `test_build_and_load_interactions`: **PASS** (28 interactions across 3 splits).
- `test_metrics_mathematical_precision`: **PASS** (Hit@K, MRR@K, NDCG@K formulas analytically verified).
- `test_random_recommender`: **PASS**.
- `test_popularity_recommender`: **PASS**.
- `test_bm25_content_recommender`: **PASS**.
- `test_full_baseline_benchmark_execution`: **PASS** (Evidence saved to `artifacts/evidence/stage10/rs_baseline_eval.json`).

**Gate G10 Status**: **`PASS`**
