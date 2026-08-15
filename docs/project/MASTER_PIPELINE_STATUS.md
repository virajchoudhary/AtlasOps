# AtlasOps Master Implementation Pipeline Status

This governance document records the repository's alignment with the canonical external project specification:

> **Canonical External Document:**
> *AtlasOps Intelligence | Master Implementation Pipeline v1.0* (dated 11 August 2026).
> **Upstream Baseline:**
> `Harikishanth/AtlasOps` frozen at commit [`bf9bd197c9f4a05ae55ade254802a9eef1a74356`](https://github.com/Harikishanth/AtlasOps/commit/bf9bd197c9f4a05ae55ade254802a9eef1a74356).
> **Fork:**
> `virajchoudhary/AtlasOps`.

> [!IMPORTANT]
> **Repository Governance Rules:**
> 1. **Immutable Stage Sequence & Numbering**: Implementation reports and pull requests may not redefine, renumber, or silently reorder the Stages 0 through 15 defined in Pipeline v1.0.
> 2. **Implementation vs. Gate Closure**: The presence of downstream code, scripts, or documentation in the repository does *not* mean the corresponding stage gate has passed. Gates pass only upon independent verification with reproducible evidence.
> 3. **Evidence Precedence**: Executable code and independently verified evidence outrank upstream README claims, benchmark tables, or third-party assertions.
> 4. **Canonical Progression Order**:
>    $$\text{Environment (G3)} \rightarrow \text{Golden Incident (G4)} \rightarrow \text{Split Freeze (G5)} \rightarrow \text{Zero-Shot (G6)} \rightarrow \text{SFT (G7/G8)} \rightarrow \text{Corrected GRPO (G9)} \rightarrow \text{RS (G10/G11)} \rightarrow \text{Integration (G12)} \rightarrow \text{Final Evaluation (G13)}$$

---

## Canonical Stage & Gate Sequence (Pipeline v1.0)

| Stage | Name | Gate | Target / Deliverable | Current Status |
|---|---|---|---|---|
| **Stage 0** | Freeze provenance and working scope | **G0** | Freeze upstream baseline SHA `bf9bd19`, preserve MIT license, establish repository boundaries. | **PASS** |
| **Stage 1** | Local reproducibility baseline | **G1** | Clean local Python environment, dependency lock, static syntax/name analysis, test harness baseline. | **PASS** |
| **Stage 2** | Stabilize upstream blockers | **G2** | Fix tier ordering, coordinator naming, tool ACL/RBAC, verifier contract 1:1 with 28 manifests, offline benchmark reaches judge. | **PASS** |
| **Stage 3** | Provision controlled SRE environment | **G3** | Standard GKE, Online Boutique (12 Deployments), Prometheus/Alertmanager, Jaeger, Argo CD, Chaos Mesh, non-destructive tool verification. | **PENDING** (Stage 3A local prep complete; cloud provisioning blocked on pre-G3 repairs) |
| **Stage 4** | Prove one real end-to-end incident | **G4** | Single fault injection $\rightarrow$ alert $\rightarrow$ triage $\rightarrow$ diagnosis $\rightarrow$ gate $\rightarrow$ remediation $\rightarrow$ objective verification $\rightarrow$ comms. | **BLOCKED (on G3)** |
| **Stage 5** | Freeze scenario truth and benchmark splits | **G5** | Explicit scenario metadata and success predicates; training, validation, and final-test populations/variants; final-test isolation; frozen seeds, manifests, and content hashes. | **BLOCKED (on G4)** |
| **Stage 6** | Reproduce GAI zero-shot baseline | **G6** | Execute zero-shot benchmark run across evaluation split; record genuine baseline metrics. | **BLOCKED (on G5)** |
| **Stage 7** | Generate SFT data and train | **G7** | Cleaned training-only trajectory corpus without test-set leakage; QLoRA SFT; frozen corpus manifest, config, checkpoint, and evidence. | **BLOCKED (on G6)** |
| **Stage 8** | Evaluate SFT before RL | **G8** | Benchmark SFT checkpoint; verify resolution rate and format compliance before starting RL. | **BLOCKED (on G7)** |
| **Stage 9** | Correct and train online GRPO | **G9** | Correct policy-environment-reward coupling, execute online GRPO with objective verifier reward. | **BLOCKED (on G8)** |
| **Stage 10** | Build RS data and baselines | **G10** | Compile historical incident & runbook dataset for Recommender Systems; evaluate baseline recommenders. | **BLOCKED (on G9)** |
| **Stage 11** | Train hybrid recommender | **G11** | Develop and train collaborative/content-based top-K runbook recommender. | **BLOCKED (on G10)** |
| **Stage 12** | Integrate GAI + RS + RL | **G12** | Full multi-agent pipeline with integrated Recommender System step between Diagnosis and Remediation. | **BLOCKED (on G11)** |
| **Stage 13** | Run final ablation and stress evaluation | **G13** | Full benchmark evaluation across predetermined comparison family: stabilized AtlasOps baseline, SFT, corrected GRPO, +RS, full GAI+RS+RL, unseen final-test and held-out adversarial evaluation. | **BLOCKED (on G12)** |
| **Stage 14** | Deploy final demo safely | **G14** | Package and deploy reproducible demo with safety guardrails and read-only operator UI. | **BLOCKED (on G13)** |
| **Stage 15** | Report, package and submit | **G15** | Compile final academic thesis/report, artifacts, and reproducible submission package. | **BLOCKED (on G14)** |

---

## Historical PR Provenance Record

| PR | Title | Branch | Primary Scope | Gate Alignment |
|---|---|---|---|---|
| **[PR #1](https://github.com/virajchoudhary/AtlasOps/pull/1)** | `chore: establish project governance and CI foundation` | `chore/project-bootstrap` | Operating rules, Git history preservation, GitHub Actions CI workflow | **G0** |
| **[PR #2](https://github.com/virajchoudhary/AtlasOps/pull/2)** | `chore: establish reproducible development baseline` | `chore/reproducible-dev-baseline` | Python 3.11/3.12 lock, pyproject dependencies, pytest baseline | **G1** |
| **[PR #3](https://github.com/virajchoudhary/AtlasOps/pull/3)** | `fix: harden runtime security configuration` | `fix/security-config-baseline` | Remove hardcoded secrets, add API key & webhook token verification | **G1** |
| **[PR #4](https://github.com/virajchoudhary/AtlasOps/pull/4)** | `docs: archive GitHub history before fork detachment` | `docs/repository-detachment-record` | Detachment documentation, upstream attribution freeze | **G0** |
| **[PR #5](https://github.com/virajchoudhary/AtlasOps/pull/5)** | `fix: repair benchmark scenario tier ordering` | `fix/benchmark-tier-ordering` | Benchmark runner tier derivation before judge invocation | **G2** |
| **[PR #6](https://github.com/virajchoudhary/AtlasOps/pull/6)** | `fix: harden infrastructure provisioning contract` | `fix/infra-static-correctness` | Static check/apply guardrails, zonal topology, DNS names | **G2 / G3** |
| **[PR #7](https://github.com/virajchoudhary/AtlasOps/pull/7)** | `fix: wire core runtime observability contracts` | `fix/core-runtime-observability` | Prometheus rule naming, Alertmanager webhook route, coordinator Service | **G2** |
| **[PR #8](https://github.com/virajchoudhary/AtlasOps/pull/8)** | `fix: formalize scenario catalogue contract` | `fix/scenario-catalog-contract` | 28 static scenarios, 10 dynamic default, centralized catalogue | **G2** |
| **[PR #9](https://github.com/virajchoudhary/AtlasOps/pull/9)** | `fix: formalize tool access and side-effect policy` | `fix/tool-policy-contract` | SRE tool ACL: 19 role-exposed tools, 3 unexposed tools | **G2** |
| **[PR #10](https://github.com/virajchoudhary/AtlasOps/pull/10)** | `feat: implement objective environment verifier and reward integration` | `feat/objective-environment-verifier` | Dedicated `agents/verifier.py` engine, separate `env_resolved` from agent claim | **G2** |
| **[PR #11](https://github.com/virajchoudhary/AtlasOps/pull/11)** | `fix: align objective verifier with frozen chaos manifests and add contract tests` | `fix/verifier-scenario-contract` | 1:1 manifest alignment, remove frontend fallback, namespace & tier validation | **G2** |
| **[PR #12](https://github.com/virajchoudhary/AtlasOps/pull/12)** | `docs(governance): reconcile stage truth against Pipeline v1.0 and record Gate G2 closure` | `docs/g2-g3-pipeline-reconciliation` | Pipeline v1.0 reconciliation, G2 closure audit, pre-G3 readiness audit | **G2 / G3** |

---

## Formal Gate Evidence Record

### Gate G0: Provenance and Scope — [PASS]
- Upstream baseline frozen at commit `bf9bd197c9f4a05ae55ade254802a9eef1a74356` ([PR #1](https://github.com/virajchoudhary/AtlasOps/pull/1), [PR #4](https://github.com/virajchoudhary/AtlasOps/pull/4)).
- Full Git history, MIT license, and original attribution preserved.
- Academic scope bounded to Generative AI, Reinforcement Learning, and Recommender Systems.

### Gate G1: Local Reproducibility Baseline — [PASS]
- Deterministic virtual environment using Python 3.11 / 3.12 ([PR #2](https://github.com/virajchoudhary/AtlasOps/pull/2)).
- `pyproject.toml` dependencies locked and verified via `pip check`.
- Static correctness, name binding, and syntax checks enforced via `ruff` (`E9,F63,F7,F821`). *(Note: provides syntax/correctness checks, not Python static type checking)*.
- GitHub Actions CI matrix executing on all pull requests.

### Gate G2: Stabilize Upstream Blockers — [PASS]
- **Defects Repaired**:
  - Benchmark runner tier ordering defect resolved before judge invocation ([PR #5](https://github.com/virajchoudhary/AtlasOps/pull/5)).
  - Coordinator service naming reconciled to `atlasops-coordinator-svc.default:9099` ([PR #6](https://github.com/virajchoudhary/AtlasOps/pull/6), [PR #7](https://github.com/virajchoudhary/AtlasOps/pull/7)).
  - Scenario catalogue contract formalized (28 static, 10 dynamic default) ([PR #8](https://github.com/virajchoudhary/AtlasOps/pull/8)).
  - SRE Tool inventory and deterministic role-based ACL frozen (19 exposed, 3 unexposed) ([PR #9](https://github.com/virajchoudhary/AtlasOps/pull/9)).
  - Objective Environment Verifier (`agents/verifier.py`) 1:1 aligned with all 28 frozen Chaos Mesh manifests, dynamic guessing removed, and selector namespace/tier validation enforced ([PR #10](https://github.com/virajchoudhary/AtlasOps/pull/10), [PR #11](https://github.com/virajchoudhary/AtlasOps/pull/11)).
- **Offline Benchmark Path to Judge Proven**:
  - `test_benchmark_single_scenario_reaches_judge_offline` in `tests/test_bench_runner.py` proves `run_scenario` orchestrates chaos injection, enriches alert with `scenario_id`, invokes multi-agent handling, passes full trajectory to `judge_trajectory(incident, tier="single_fault")`, computes centralized reward contract, and triggers cluster reset without real Kubernetes or cloud dependencies ([PR #12](https://github.com/virajchoudhary/AtlasOps/pull/12)).
- **Manifests Validated**:
  - Kubernetes RBAC, coordinator templates, Prometheus rules, and values files validated via static contract tests (`test_runtime_infra_contract.py`, `test_infra_contract.py`).
  - Infrastructure shell scripts pass static syntax validation (`bash -n`).
- **Regression Suite**: 402 tests passing green.

### Gate G3: Controlled SRE Environment — [PENDING]
- **Prerequisites Prepared (Stage 3A)**: Local CLI toolchain verified (Git Bash, `gcloud` 580.0.0, `helm` v4.2.4, `kubectl` v1.34.1, `docker` 29.4.0).
- **Exact Pinned Infrastructure Baseline**:
  - **Online Boutique**: v0.10.0 pinned to immutable commit `98e60f5ee0b643cc00bceb71e6efb89617740432`.
  - **Required Boutique Deployments (12)**: `currencyservice`, `loadgenerator`, `productcatalogservice`, `checkoutservice`, `shippingservice`, `cartservice`, `redis-cart`, `emailservice`, `paymentservice`, `frontend`, `recommendationservice`, `adservice`.
  - **Pinned Helm Chart Versions**:
    - `PROMETHEUS_CHART_VERSION="88.3.0"` (`prometheus-community/kube-prometheus-stack`)
    - `JAEGER_CHART_VERSION="4.12.0"` (`jaegertracing/jaeger`)
    - `ARGOCD_CHART_VERSION="10.3.2"` (`argo/argo-cd`)
    - `CHAOS_MESH_CHART_VERSION="2.8.3"` (`chaos-mesh/chaos-mesh`)
- **Zero-Cost Boundary**: 0 cloud resources created, $0.00 billing incurred.

---

## Known Architecture & Runtime Truth Gaps (Pre-G4 Tracking)

> [!WARNING]
> The following items do NOT block Gate G2 (which covers offline upstream stabilization), but are **MANDATORY PRE-G4 REPAIRS** that must be resolved before executing the Stage 4 golden live incident.

### 1. Pre-G4 Coordinator / Verifier Execution Ordering Gap [MUST FIX BEFORE G4]
- **Current Code Behavior**: [`agents/coordinator.py`](file:///c:/AtlasOps/agents/coordinator.py#L746-L836) executes:
  $$\text{Triage} \rightarrow \text{Diagnosis} \rightarrow \text{Remediation} \rightarrow \mathbf{Comms} \rightarrow \mathbf{Verifier}$$
- **Canonical Pipeline v1.0 Requirement**:
  $$\text{Triage} \rightarrow \text{Diagnosis} \rightarrow \text{Remediation} \rightarrow \mathbf{Verifier} \rightarrow \mathbf{Comms}$$
- **Implications**:
  1. *Comms runs before objective truth*: The communications agent drafts postmortems and Slack notifications before environment verifier results exist, preventing Comms from guaranteeing that it never claims resolution when the environment is unresolved.
  2. *Trajectory persistence race*: `full_record` is currently written to `TRAJECTORIES_DIR` before `verification` is appended, causing saved trajectory files on disk to omit verification metadata even if the in-memory return value includes it.
  3. *Repair Scope*: Reorder coordinator execution so `verify_environment` runs immediately after Remediation, pass verification results into Comms input context, and persist `full_record` after verification is attached.

### 2. Fail-Closed Benchmark and Reward Resolution Truth [MUST FIX BEFORE G4/G9]
- **Current Fallback Behavior**:
  - `bench/runner.py` currently falls back to `remediation.get("outcome") == "resolved"` when verification is missing.
  - `config/runtime.py` (`evaluate_reward_contract`) allows `env_resolved` to fall back to `episode["resolved"]`.
  - `agents/coordinator.py` falls back to `agent_claimed_resolved` when `verification_status == "inconclusive"`.
- **Follow-up Requirement**:
  - Benchmark and RL rewards must strictly fail closed: missing or inconclusive telemetry must result in `env_resolved = False` and positive resolution reward must not be awarded.
  - `agent_claimed_resolved` must remain strictly separated from ground-truth `env_resolved`.

### 3. Argo CD Security & Credential Boundary [PRE-G3 PLANNING]
- For future Stage 3 deployment, do **not** automatically extract or copy `argocd-initial-admin-secret` into coordinator application configuration.
- Use explicit operator-provisioned credentials (`ARGOCD_URL`, `ARGOCD_USER`, `ARGOCD_PASS`) with least-privilege read permissions for the non-destructive G3 tool query contract (`argocd_list_apps`, `argocd_app_get`).
- Mutating operations (`argocd_rollback`) remain gated behind the remediation approval gate.

### 4. Jaeger Backend Reachability vs. Online Boutique Trace Ingestion [PRE-G3 PLANNING]
- Distinguish:
  - **A. Jaeger Backend Installation & API Reachability**: Verified when the Jaeger query endpoint is deployed and responds HTTP 200 to `jaeger_search(service="...")` (satisfies Stage 3 non-destructive tool contract).
  - **B. Microservice Trace Ingestion**: Requires OpenTelemetry Collector and application trace exporters in Online Boutique; until trace exporters are instrumented, trace query returns empty traces with `{"success": true, "count": 0}`.
- For the pre-G3 infrastructure PR, derive chart values directly from pinned chart `4.12.0` (`helm show values jaegertracing/jaeger --version 4.12.0`) rather than guessing configuration keys.

---

> [!NOTE]
> This status document reflects the live repository state as of August 2026. For the complete academic specification and methodology, refer to the external Master Implementation Pipeline v1.0 document.
