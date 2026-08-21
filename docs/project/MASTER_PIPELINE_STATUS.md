# AtlasOps Master Implementation Pipeline Status

This governance document records the repository's alignment with the canonical external project specification:

> **Canonical External Document:**
> *AtlasOps Intelligence | Master Implementation Pipeline v1.1 Free-First* (dated 16 August 2026).
> Pipeline v1.1 supersedes v1.0 for execution decisions. v1.0 remains the historical record.
> **Upstream Baseline:**
> `Harikishanth/AtlasOps` frozen at commit [`bf9bd197c9f4a05ae55ade254802a9eef1a74356`](https://github.com/Harikishanth/AtlasOps/commit/bf9bd197c9f4a05ae55ade254802a9eef1a74356).
> **Fork:**
> `virajchoudhary/AtlasOps`.

> [!IMPORTANT]
> **Pipeline v1.1 Free-First Execution Change:**
> The canonical Stage 3 environment is now a local Kind Kubernetes cluster on Windows/Docker Desktop/WSL2.
> GKE/GCP is no longer required for canonical project execution. GKE remains as OPTIONAL portability code.
> External service spend target: **$0**.

> [!IMPORTANT]
> **Repository Governance Rules:**
> 1. **Immutable Stage Sequence & Numbering**: Implementation reports and pull requests may not redefine, renumber, or silently reorder the Stages 0 through 15 defined in Pipeline v1.0.
> 2. **Implementation vs. Gate Closure**: The presence of downstream code, scripts, or documentation in the repository does *not* mean the corresponding stage gate has passed. Gates pass only upon independent verification with reproducible evidence.
> 3. **Evidence Precedence**: Executable code and independently verified evidence outrank upstream README claims, benchmark tables, or third-party assertions.
> 4. **Canonical Progression Order**:
>    $$\text{Environment (G3)} \rightarrow \text{Golden Incident (G4)} \rightarrow \text{Split Freeze (G5)} \rightarrow \text{Zero-Shot (G6)} \rightarrow \text{SFT (G7/G8)} \rightarrow \text{Corrected GRPO (G9)} \rightarrow \text{RS (G10/G11)} \rightarrow \text{Integration (G12)} \rightarrow \text{Final Evaluation (G13)}$$

---

## Canonical Stage & Gate Sequence (Pipeline v1.1 Free-First)

| Stage | Name | Gate | Target / Deliverable | Current Status |
|---|---|---|---|---|
| **Stage 0** | Freeze provenance and working scope | **G0** | Freeze upstream baseline SHA `bf9bd19`, preserve MIT license, establish repository boundaries. | **PASS** |
| **Stage 1** | Local reproducibility baseline | **G1** | Clean local Python environment, dependency lock, static syntax/name analysis, test harness baseline. | **PASS** |
| **Stage 2** | Stabilize upstream blockers | **G2** | Fix tier ordering, coordinator naming, tool ACL/RBAC, verifier contract (24 exact + 4 reviewed exceptions), offline benchmark reaches judge. | **PASS** |
| **Stage 3** | Provision controlled SRE environment | **G3** | Local Kind cluster (or optional GKE), Online Boutique (12 Deployments), Prometheus/Alertmanager, Jaeger, Argo CD, Chaos Mesh, non-destructive tool verification. **$0 external cost.** | **PASS** ([PR #17](https://github.com/virajchoudhary/AtlasOps/pull/17) merged; 100% free-local Kind cluster verified) |
| **Stage 4** | Prove one real end-to-end incident | **G4** | Single fault injection $\rightarrow$ alert $\rightarrow$ triage $\rightarrow$ diagnosis $\rightarrow$ gate $\rightarrow$ remediation $\rightarrow$ objective verification $\rightarrow$ comms. | **IN PROGRESS / BLOCKED** (EXP-STAGE4-SF002-001 = INVALID; EXP-STAGE4-SF002-002/003/004 = VALID FAIL; Gate G4 requires real tool remediation causing verified environment recovery) |
| **Stage 5** | Freeze scenario truth and benchmark splits | **G5** | Explicit scenario metadata and success predicates; training, validation, and final-test populations/variants; final-test isolation; frozen seeds, manifests, and content hashes. | **BLOCKED ON G4** |
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
| **[PR #3](https://github.com/virajchoudhary/AtlasOps/pull/3)** | `fix: harden runtime security configuration` | `fix/security-config-baseline` | Remove unsafe Argo CD defaults, fail-closed TLS config, remove fixed audit HMAC fallback, require private `ATLASOPS_AUDIT_SECRET` | **G1** |
| **[PR #4](https://github.com/virajchoudhary/AtlasOps/pull/4)** | `docs: archive GitHub history before fork detachment` | `docs/repository-detachment-record` | Detachment documentation, upstream attribution freeze | **G0** |
| **[PR #5](https://github.com/virajchoudhary/AtlasOps/pull/5)** | `fix: repair benchmark scenario tier ordering` | `fix/benchmark-tier-ordering` | Benchmark runner tier derivation before judge invocation | **G2** |
| **[PR #6](https://github.com/virajchoudhary/AtlasOps/pull/6)** | `fix: harden infrastructure provisioning contract` | `fix/infra-static-correctness` | Static check/apply guardrails, zonal topology, DNS names | **G2 / G3** |
| **[PR #7](https://github.com/virajchoudhary/AtlasOps/pull/7)** | `fix: wire core runtime observability contracts` | `fix/core-runtime-observability` | Dedicated coordinator runtime, private Service, RBAC, authenticated Alertmanager webhook route, `ATLASOPS_API_KEY` approval gate | **G2** |
| **[PR #8](https://github.com/virajchoudhary/AtlasOps/pull/8)** | `fix: formalize scenario catalogue contract` | `fix/scenario-catalog-contract` | 28 static scenarios, 10 dynamic default, centralized catalogue | **G2** |
| **[PR #9](https://github.com/virajchoudhary/AtlasOps/pull/9)** | `fix: formalize tool access and side-effect policy` | `fix/tool-policy-contract` | SRE tool ACL: 19 role-exposed tools, 3 unexposed tools | **G2** |
| **[PR #10](https://github.com/virajchoudhary/AtlasOps/pull/10)** | `feat: implement objective environment verifier and reward integration` | `feat/objective-environment-verifier` | Dedicated `agents/verifier.py` engine, separate `env_resolved` from agent claim | **G2** |
| **[PR #11](https://github.com/virajchoudhary/AtlasOps/pull/11)** | `fix: align objective verifier with frozen chaos manifests and add contract tests` | `fix/verifier-scenario-contract` | All 28 frozen scenarios covered (24 exact manifest targets + 4 reviewed exceptions), dynamic frontend guessing removed, namespace & tier validation | **G2** |
| **[PR #12](https://github.com/virajchoudhary/AtlasOps/pull/12)** | `docs(governance): reconcile stage truth against Pipeline v1.0 and record Gate G2 closure` | `docs/g2-g3-pipeline-reconciliation` | Pipeline v1.0 reconciliation, G2 closure audit, pre-G3 readiness audit | **G2 / G3** |
| **[PR #13](https://github.com/virajchoudhary/AtlasOps/pull/13)** | `fix: make environment verification authoritative before communications` | `fix/runtime-verification-truth` | Reorder coordinator execution (Remediation -> Verifier -> Comms), verification-aware Comms, trajectory persistence after verifier, fail-closed benchmark & reward truth | **Pre-G4 / G2** |
| **[PR #14](https://github.com/virajchoudhary/AtlasOps/pull/14)** | `feat: establish static G3 readiness for Jaeger and Argo CD with non-destructive acceptance plan` | `feat/g3-observability-readiness` | Static G3 readiness, low-resource Jaeger & Argo CD configs, non-destructive acceptance matrix | **G3** |
| **[PR #15](https://github.com/virajchoudhary/AtlasOps/pull/15)** | `docs: establish Stage 3 operator guide, preflight sequence, and local secret helper` | `docs/stage-3-operator-preflight-guide` | Stage 3 operator guide, preflight checklist, and runtime secret generator | **G3** |
| **[PR #16](https://github.com/virajchoudhary/AtlasOps/pull/16)** | `fix(infra): resolve fresh-cluster bootstrap ordering and Argo CD account activation contract` | `fix/infra-bootstrap-lifecycle` | Fresh-cluster bootstrap ordering, declarative Argo password verifier, single-pass bootstrap lifecycle | **G3** |
| **[PR #17](https://github.com/virajchoudhary/AtlasOps/pull/17)** | `feat(infra): establish free local Kind Stage 3 environment` | `feat/stage3-free-local-kind` | 100% free-local Kind cluster deployment (Pipeline v1.1), low-resource profiles, live 7-stage workload validation, and non-destructive tool acceptance | **G3** |
| **[PR #18](https://github.com/virajchoudhary/AtlasOps/pull/18)** | `feat(incident): prove real end-to-end golden incident and close Gate G4` | `feat/stage4-golden-incident` | Free local LLM inference harness and golden incident initial run (run `EXP-STAGE4-SF002-001` recorded and subsequently invalidated due to non-causal harness fault clearance) | **G4 (Audit Invalidation)** |

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
  - Objective Environment Verifier (`agents/verifier.py`): all 28 frozen scenarios covered, 24 standard manifests use exact manifest-target equality, 4 non-standard manifests use explicit reviewed exception contracts, tier and namespace agreement are validated, and dynamic frontend fallback is removed ([PR #10](https://github.com/virajchoudhary/AtlasOps/pull/10), [PR #11](https://github.com/virajchoudhary/AtlasOps/pull/11)).
- **Runtime Environment-Truth Contract [IMPLEMENTED / MOCKED-TESTED / LIVE VERIFIED] ([PR #13](https://github.com/virajchoudhary/AtlasOps/pull/13), [PR #17](https://github.com/virajchoudhary/AtlasOps/pull/17))**:
  - Coordinator execution reordered to canonical pipeline: $\text{Triage} \rightarrow \text{Diagnosis} \rightarrow \text{Remediation} \rightarrow \mathbf{Verifier} \rightarrow \mathbf{Comms}$.
  - Comms agent input context and closure messages receive objective verification evidence (`env_resolved`, `agent_claimed_resolved`, `verification`).
  - Trajectory JSON files are persisted to disk only after objective verification metadata is attached.
  - Fail-closed evaluation truth enforced: missing or inconclusive telemetry strictly results in `env_resolved = False`, with zero positive resolution reward.
- **Offline Benchmark Path to Judge Proven**:
  - `test_benchmark_single_scenario_reaches_judge_offline` in `tests/test_bench_runner.py` proves `run_scenario` orchestrates chaos injection, enriches alert with `scenario_id`, invokes multi-agent handling, passes full trajectory to `judge_trajectory(incident, tier="single_fault")`, computes centralized reward contract, and triggers cluster reset without real Kubernetes or cloud dependencies ([PR #12](https://github.com/virajchoudhary/AtlasOps/pull/12)).
- **Manifests Validated**:
  - Kubernetes RBAC, coordinator templates, Prometheus rules, and values files validated via static contract tests (`test_runtime_infra_contract.py`, `test_infra_contract.py`, `test_local_infra_contract.py`).
  - Infrastructure shell scripts pass static syntax validation (`bash -n`).
- **Regression Suite**: 463 tests passing green.

### Gate G3: Controlled SRE Environment — [PASS]
- **Cluster Topology**: Single-node local Kind cluster `atlasops-local` (`kindest/node:v1.31.0` on containerd `1.7.18`, Kind `v0.32.0`, context `kind-atlasops-local`) on Docker Desktop 29.7.2 ([PR #17](https://github.com/virajchoudhary/AtlasOps/pull/17)).
- **Online Boutique**: v0.10.0 pinned to immutable commit `98e60f5ee0b643cc00bceb71e6efb89617740432`. All 12 deployments healthy and available (`1/1 Running`). NodePort `30080` verified live (`HTTP 200 OK`, Title `Online Boutique`).
- **Prometheus & Alertmanager**: Chart `kube-prometheus-stack` v88.3.0 in `monitoring` namespace. Low-resource profile, custom `PrometheusRule: atlasops-online-boutique` active, query API returning HTTP 200 with 12 active scrape targets.
- **Jaeger**: Chart `jaegertracing/jaeger` v4.12.0 in `jaeger` namespace. In-memory all-in-one backend responding HTTP 200 to `/api/services`.
- **Argo CD**: Chart `argo/argo-cd` v10.3.2 in `argocd` namespace. Dedicated `atlasops` read-only user provisioned via bcrypt hash overlay; REST API responding HTTP 200.
- **Chaos Mesh**: Chart `chaos-mesh/chaos-mesh` v2.8.3 in `chaos-mesh` namespace. Controllers and CRDs active.
- **AtlasOps Coordinator**: Image `atlasops-coordinator:g3-local` built locally with non-root UID 10001, loaded directly into Kind node, deployed with ServiceAccount/RBAC, `/healthz` returning HTTP 200 `{"status": "ok"}`.
- **Real Non-Destructive Tool Acceptance**: Full suite of agent tool wrappers (`agents.tools.kubectl`, `prometheus`, `alertmanager`, `jaeger`, `argocd`) executed live and verified.
- **Zero Cloud Cost**: 0 cloud resources provisioned, **$0.00** billing incurred.

### Gate G4: Real End-to-End Golden Incident — [IN PROGRESS / BLOCKED]
- **Audit Invalidation Record**: Initial empirical run `EXP-STAGE4-SF002-001` was audited and found invalid because the test harness deleted the `StressChaos` CRD resource out-of-band prior to objective verification, and the remediation agent proposed an unrelated `argocd_rollback(checkoutservice)`.
- **Causal Baseline Failure Records (`EXP-STAGE4-SF002-002`, `EXP-STAGE4-SF002-003`, `EXP-STAGE4-SF002-004`)**:
  - Executed on 2026-08-17 against live Kind cluster `atlasops-local` with local Ollama `qwen2.5:1.5b`.
  - Proved pipeline orchestration: Alert $\rightarrow$ Triage $\rightarrow$ Diagnosis $\rightarrow$ Approval Gate $\rightarrow$ Remediation (with generic execution retry & namespace security allowlist) $\rightarrow$ Objective Verifier $\rightarrow$ Comms.
  - Truthfully recorded `gate_g4_pass: False` / `env_resolved: False` because the unfinetuned baseline model proposed invalid remediation arguments without emitting valid mutating tool execution in the loop.
  - While scientifically valuable as empirical development baselines, failed runs **do not close Gate G4**.
- **Gate G4 Pass Requirement**:
  - Gate G4 remains **BLOCKED** until AtlasOps multi-agent execution causes real mutating remediation resulting in verified objective environment recovery (`env_resolved == True`) under the strict 15-point causal predicate.
  - Stage 5 remains **BLOCKED ON G4**.

---

## Pre-G3 / Pre-G4 Architecture & Security Tracking

### 1. Argo CD Security & Credential Boundary [STATICALLY RESOLVED]
- Do **not** automatically extract or copy `argocd-initial-admin-secret` into coordinator application configuration.
- Use explicit operator-provisioned credentials (`ARGOCD_URL`, `ARGOCD_USER`, `ARGOCD_PASS`) backed by `atlasops-coordinator-secrets` SecretKeyRefs with least-privilege read permissions for the non-destructive G3 tool query contract (`argocd_list_apps`, `argocd_app_get`). Secret presence is validated fail-closed before deployment.
- Transport contract: In-cluster HTTP over ClusterIP with `--insecure` and `ARGOCD_VERIFY_TLS: "false"`. Classified explicitly as a development-cluster exception with credentials traversing only the private in-cluster network path.
- Mutating operations (`argocd_rollback`) remain gated behind the remediation approval gate.

### 2. Jaeger Backend Reachability vs. Online Boutique Trace Ingestion [STATICALLY RESOLVED]
- Distinguish:
  - **A. Jaeger Backend Installation & API Reachability**: Verified when the Jaeger query endpoint is deployed and responds HTTP 200 to `jaeger_services_list()` / `GET /api/services` (satisfies Stage 3 non-destructive tool contract).
  - **B. Microservice Trace Ingestion**: Requires OpenTelemetry Collector and application trace exporters in Online Boutique; until trace exporters are instrumented, trace query returns empty traces with `{"success": true, "count": 0}`.
- Chart values derived directly from pinned chart `4.12.0` (`helm show values jaegertracing/jaeger --version 4.12.0`).

---

> [!NOTE]
> This status document reflects the live repository state as of August 2026.
> **Pipeline v1.1 Free-First** (16 August 2026) is the current canonical execution specification.
> **Pipeline v1.0** (11 August 2026) remains the historical record of the original execution specification.
> For the complete academic specification and methodology, refer to the external Master Implementation Pipeline documents.
