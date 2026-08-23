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
| **Stage 4** | Prove one real end-to-end incident | **G4** | Single fault injection $\rightarrow$ alert $\rightarrow$ triage $\rightarrow$ diagnosis $\rightarrow$ gate $\rightarrow$ remediation $\rightarrow$ objective verification $\rightarrow$ comms. | **IN PROGRESS / BLOCKED** (EXP-STAGE4-SF002-001 = INVALID; -002/003/004 = VALID BASELINE FAIL; -005 = INVALID instrumentation per [G4 amendment](G4_PROTOCOL_AMENDMENT_SF002_TELEMETRY.md); -006 = pre-reservation ABORT; Gate G4 requires real tool remediation causing verified environment recovery) |
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
| **[PR #19](https://github.com/virajchoudhary/AtlasOps/pull/19)** | `fix(runtime): make Stage 4 golden incident causally valid` | `fix/stage4-causal-golden-incident` | Causal Stage 4 harness: anti-leakage model-visible alert contract, immutable evidence sidecars, attempt lifecycle, causal contract tests | **G4** |
| **[PR #20](https://github.com/virajchoudhary/AtlasOps/pull/20)** | `docs(evidence): record failed Stage 4 SF002 run 004` | `evidence/stage4-sf002-004` | Immutable evidence for `EXP-STAGE4-SF002-004` (valid baseline fail); experiment & evaluation registry established | **G4 Evidence** |
| **[PR #21](https://github.com/virajchoudhary/AtlasOps/pull/21)** | `fix(runtime): survive malformed native tool-call arguments` | `fix/g4-tool-call-interface` | Coordinator survives malformed native tool-call arguments fail-closed instead of crashing mid-loop | **G4 Runtime Robustness** |
| **[PR #22](https://github.com/virajchoudhary/AtlasOps/pull/22)** | `fix(training): encode SFT tool calls in native message structure` | `fix/sft-tool-call-data-contract` | Trajectory generator emits tool calls in native chat-message structure; frozen Qwen2.5 chat-template fixture + data-contract tests | **Pre-G7 Data Contract** |
| **[PR #23](https://github.com/virajchoudhary/AtlasOps/pull/23)** | `fix(sft): align tool template and assistant-only targets` | `fix/sft-tool-template-wiring` | Dedicated Qwen2.5 tool-SFT Jinja template, assistant-only loss-masking proof tests, rendering wiring | **Pre-G7** |
| **[PR #24](https://github.com/virajchoudhary/AtlasOps/pull/24)** | `fix(runtime): persist remediation retry turns` | `fix/coordinator-turn-observability` | Remediation retry turns persisted to trajectories for complete turn-level observability | **Pre-G4 Observability** |
| **[PR #25](https://github.com/virajchoudhary/AtlasOps/pull/25)** | `docs(evidence): record qwen2.5:3b-instruct G4 candidate qualification` | `evidence/model-qual-qwen25-3b` | Qualification evidence promoting `qwen2.5:3b-instruct` to G4 candidate model | **G4 Model Qualification Evidence** |
| **[PR #26](https://github.com/virajchoudhary/AtlasOps/pull/26)** | `fix(runtime): make Stage 4 model selection configurable` | `fix/stage4-model-selection` | `ATLASOPS_STAGE4_AGENT_MODEL` override with blank-safe default fallback and tests | **G4 Harness Configurability** |
| **[PR #27](https://github.com/virajchoudhary/AtlasOps/pull/27)** | `fix(g4): harden causal evidence contract` | `fix/g4-causal-evidence-hardening` | F1–F5 SF002 measurement contract: baseline/degradation-proof/verification phases, immutable JSON evidence schema | **G4 Measurement Contract** |
| **[PR #28](https://github.com/virajchoudhary/AtlasOps/pull/28)** | `fix(g4): correct paymentservice baseline schema` | `fix/g4-paymentservice-baseline-schema` | Paymentservice Deployment baseline-readiness schema correction in the pre-fault contract | **G4 Baseline Readiness** |
| **[PR #29](https://github.com/virajchoudhary/AtlasOps/pull/29)** | `fix(g4): gate attempts on telemetry readiness` | `fix/g4-telemetry-readiness` | Pre-reservation telemetry-readiness gate (7-point Prometheus/cAdvisor readiness, stability window); G4 protocol amendment adopted; `EXP-STAGE4-SF002-005` preserved as instrumentation-invalidated | **G4 Protocol Amendment** |
| **[PR #30](https://github.com/virajchoudhary/AtlasOps/pull/30)** | `fix(g4): move baseline readiness before reservation` | `fix/g4-baseline-prereservation` | Baseline-readiness gate evaluated BEFORE attempt reservation; reservation occurs only after both readiness gates pass; fault boundary remains sole CONSUMED point | **G4 Protocol Amendment** |

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
- **Instrumentation Invalidation Record (`EXP-STAGE4-SF002-005`)**:
  - Executed 2026-08-23 with the qualified candidate `qwen2.5:3b-instruct`; the run terminated at `measured_degradation` — Triage, Diagnosis, Remediation, Verification, and Comms were never reached, and the attempt state is permanently `CONSUMED`.
  - Audited as an **immutable instrumentation failure**: the frozen F1 selector `container="paymentservice"` cannot match the deployed paymentservice application container (`server`) and matched zero series even when the cluster transport was otherwise healthy. T0 was crossed, so the attempt is permanently spent and must never be rerun.
  - It is excluded from any inference about the model's remediation capability: it is not evidence of model success or model failure.
  - Full disposition, corrected selector (`container="server"`, all other query semantics unchanged), and replacement-attempt policy: [G4 Protocol Amendment: SF002 Telemetry Readiness](G4_PROTOCOL_AMENDMENT_SF002_TELEMETRY.md) (adopted 2026-08-23 via [PR #29](https://github.com/virajchoudhary/AtlasOps/pull/29)).
  - Evidence preserved unchanged under `artifacts/evidence/stage4/EXP-STAGE4-SF002-005.*` (JSON evidence, runlog, cleanup record).
- **Pre-Reservation Abort Record (`EXP-STAGE4-SF002-006`, 2026-08-23)**:
  - Aborted at the pre-fault readiness boundary BEFORE reservation: no attempt marker, no RESERVED state, no Chaos object, no T0 crossing, and no experiment ID consumed for a future replacement trial.
  - This abort motivated the amendment's baseline-readiness-before-reservation clause (adopted via [PR #30](https://github.com/virajchoudhary/AtlasOps/pull/30)): reservation now occurs only after BOTH the telemetry-readiness gate and the paymentservice baseline-readiness gate pass.
  - The workstation-local runlog for this abort is intentionally **untracked** (never committed); its formal disposition is **PENDING** team review. Do not commit it as part of routine work.
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

### 2. Jaeger Backend Reachability vs. Online Boutique Trace Ingestion [ROOT CAUSE IDENTIFIED; EXPORT PATH IMPLEMENTED; LIVE VERIFICATION PENDING]
- Distinguish:
  - **A. Jaeger Backend Installation & API Reachability**: Verified when the Jaeger query endpoint is deployed and responds HTTP 200 to `jaeger_services_list()` / `GET /api/services` (satisfies Stage 3 non-destructive tool contract).
  - **B. Microservice Trace Ingestion**: Requires application trace exporters in Online Boutique shipping spans to an OTLP receiver; without them, trace query returns empty traces with `{"success": true, "count": 0}`.
- **Root cause of historically empty traces (verified against the pinned upstream manifests at commit `98e60f5e`, boutique v0.10.0)**:
  1. The pinned release manifests set no `ENABLE_TRACING` on any service, so every service runs with tracing disabled; no spans are ever produced.
  2. No OTLP collector/receiver endpoint was referenced anywhere in the deployment path.
  3. At this boutique version only four deployed services honor the tracing contract (`ENABLE_TRACING=1` + `COLLECTOR_SERVICE_ADDR`, OTLP/gRPC): `frontend`, `checkoutservice`, `currencyservice`, `emailservice`. (`shippingservice`/`adservice` contain tracing stubs; `productcatalogservice`, `paymentservice`, `recommendationservice`, `cartservice` have none.)
  4. The local Jaeger values file used legacy chart-3.x keys (`allInOne`, `provisionDataStore`, component toggles) that chart `4.12.0` silently ignores; the rendered workload is the Jaeger v2 all-in-one binary whose default configuration already enables the OTLP receiver on ports 4317/4318.
- **Implemented export path (local Kind, Pipeline v1.1 Free-First)**: after the pinned boutique apply, `infra/local/setup_local.sh` patches exactly the four instrumented services with `ENABLE_TRACING=1` and `COLLECTOR_SERVICE_ADDR=jaeger.jaeger.svc.cluster.local:4317`; spans flow directly to the Jaeger v2 all-in-one OTLP receiver. No separate OpenTelemetry Collector is required.
- Chart values derived directly from pinned chart `4.12.0`; local values now use only keys the chart actually renders. **Live end-to-end trace verification is still pending** and must be executed per the morning live-verification runbook before claiming item B resolved.

---

> [!NOTE]
> This status document reflects the live repository state as of August 2026.
> **Pipeline v1.1 Free-First** (16 August 2026) is the current canonical execution specification.
> **Pipeline v1.0** (11 August 2026) remains the historical record of the original execution specification.
> For the complete academic specification and methodology, refer to the external Master Implementation Pipeline documents.
