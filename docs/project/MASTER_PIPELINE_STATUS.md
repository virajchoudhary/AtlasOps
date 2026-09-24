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
> 3. **Evidence Precedence**: Executable behavior and preserved evidence outrank prose status claims, including this document. Mock/deterministic outputs and predetermined metrics cannot close empirical gates; environment verification is authoritative for resolution. Preserve negative experiments.
> 4. **Canonical Progression Order**:
>    $$\text{Environment (G3)} \rightarrow \text{Golden Incident (G4)} \rightarrow \text{Split Freeze (G5)} \rightarrow \text{Zero-Shot (G6)} \rightarrow \text{SFT (G7/G8)} \rightarrow \text{Corrected GRPO (G9)} \rightarrow \text{RS (G10/G11)} \rightarrow \text{Integration (G12)} \rightarrow \text{Final Evaluation (G13)}$$

---

Reconciled on 2026-09-06 against main `fa2eed2bbb75eb81dedfecd6a80f3bc87915ee38`
(including evidence recovery PR #58). This is a source/evidence review, not a new experiment,
training run, service-health check, or re-execution of historical tests.

**State meanings:** PASS is limited to the evidence and scope stated below;
NOT_PASSED records an unmet gate; REOPENED withdraws an unsupported previous PASS;
IMPLEMENTED / EMPIRICAL EVIDENCE MISSING separates software from experimental proof;
PARTIAL preserves completed work while recording missing deliverables.
Downstream implementation can exist while upstream empirical gates remain open.
The research pipeline is not scientifically complete.

## Canonical Stage & Gate Sequence (Pipeline v1.1 Free-First)

| Stage | Name | Gate | Target / Deliverable | Current Status |
|---|---|---|---|---|
| **Stage 0** | Freeze provenance and working scope | **G0** | Freeze upstream baseline SHA `bf9bd19`, preserve MIT license, establish repository boundaries. | **PASS** |
| **Stage 1** | Local reproducibility baseline | **G1** | Clean local Python environment, dependency lock, static syntax/name analysis, test harness baseline. | **PASS** |
| **Stage 2** | Stabilize upstream blockers | **G2** | Fix tier ordering, coordinator naming, tool ACL/RBAC, verifier contract (24 exact + 4 reviewed exceptions), offline benchmark reaches judge. | **PASS** |
| **Stage 3** | Provision controlled SRE environment | **G3** | Local Kind cluster (or optional GKE), Online Boutique (12 Deployments), Prometheus/Alertmanager, Jaeger, Argo CD, Chaos Mesh, non-destructive tool verification. **$0 external cost.** | **PASS** (historical local Kind acceptance; wrapper limitations below) |
| **Stage 4** | Prove one real end-to-end incident | **G4** | Single fault injection $\rightarrow$ alert $\rightarrow$ triage $\rightarrow$ diagnosis $\rightarrow$ gate $\rightarrow$ remediation $\rightarrow$ objective verification $\rightarrow$ comms. | **NOT_PASSED** (010 completed negative; 009 and 011-014 interrupted/inconclusive) |
| **Stage 5** | Freeze scenario truth and benchmark splits | **G5** | Explicit scenario metadata and success predicates; training, validation, and final-test populations/variants; final-test isolation; frozen seeds, manifests, and content hashes. | **PASS** (scenario/split governance; not proof of every downstream consumer isolation) |
| **Stage 6** | Reproduce GAI zero-shot baseline | **G6** | Execute zero-shot benchmark run across evaluation split; record genuine baseline metrics. | **IMPLEMENTED / EMPIRICAL EVIDENCE MISSING** (historical outputs are MOCK) |
| **Stage 7** | Generate SFT data and train | **G7** | Cleaned training-only trajectory corpus without test-set leakage; QLoRA SFT; frozen corpus manifest, config, checkpoint, and evidence. | **PARTIAL** (corpus/configuration exists; successful trained checkpoint unverified) |
| **Stage 8** | Evaluate SFT before RL | **G8** | Benchmark SFT checkpoint; verify resolution rate and format compliance before starting RL. | **IMPLEMENTED / EMPIRICAL EVIDENCE MISSING** (deterministic mock evaluation; no demonstrated SFT improvement) |
| **Stage 9** | Correct and train online GRPO | **G9** | Correct policy-environment-reward coupling, execute online GRPO with objective verifier reward. | **REOPENED** (direct-action software contract implemented; real training/checkpoint/evaluation missing) |
| **Stage 10** | Build RS data and baselines | **G10** | Compile historical incident & runbook dataset for Recommender Systems; evaluate baseline recommenders. | **PARTIAL** (scenario-derived dataset and algorithmic evaluation exist; historical incident feedback is absent) |
| **Stage 11** | Train hybrid recommender | **G11** | Develop and train collaborative/content-based top-K runbook recommender. | **PASS** (bounded hybrid fitting/ranking evaluation on the same small dataset) |
| **Stage 12** | Integrate GAI + RS + RL | **G12** | Full multi-agent pipeline with integrated Recommender System step between Diagnosis and Remediation. | **IMPLEMENTED / EMPIRICAL EVIDENCE MISSING** (direct policy integration is locally tested; real checkpoint/environment execution missing) |
| **Stage 13** | Run final ablation and stress evaluation | **G13** | Full benchmark evaluation across predetermined comparison family: stabilized AtlasOps baseline, SFT, corrected GRPO, +RS, full GAI+RS+RL, unseen final-test and held-out adversarial evaluation. | **REOPENED** (hardcoded profiles are not actual ablation/stress evidence) |
| **Stage 14** | Deploy final demo safely | **G14** | Package and deploy reproducible demo with safety guardrails and read-only operator UI. | **PARTIAL** (UI/demo helpers exist; safe deployment not established by code alone) |
| **Stage 15** | Report, package and submit | **G15** | Compile final academic thesis/report, artifacts, and reproducible submission package. | **PARTIAL** (report/package implementation exists; scientific certification and submission unestablished) |

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
- The frozen upstream commit `bf9bd197c9f4a05ae55ade254802a9eef1a74356` remains an ancestor of reviewed main; `LICENSE` retains MIT licensing and original attribution. Architecture and academic scope remain unchanged.

### Gate G1: Local Reproducibility Baseline — [PASS]
- Retain the established baseline in `docs/project/LOCAL_BASELINE.md`, `requirements/dev-win-py312.lock`, and `pyproject.toml`. `.github/workflows/ci.yml` defines Python 3.11/3.12 correctness and unit-test checks.
- This retains the reproducibility milestone; it does not assert fresh environment validation or empirical model evidence.

### Gate G2: Stabilize Upstream Blockers — [PASS]
- `bench/runner.py` derives `tier` before `judge_trajectory`; the old ordering defect is resolved. Scenario/tool contracts and the offline runner-to-judge test exist in `config/scenario_catalog.py`, `agents/tool_policy.py`, and `tests/test_bench_runner.py`.
- `agents/coordinator.py` verifies the environment before Comms and persists verification fields. `config/runtime.py` requires explicit `env_resolved=True` for full resolution reward. This does not prove every training adapter forwards those fields (see G9).
- Static/local infrastructure work is retained. SAFETY-01 fixes P1 approval control flow: only `approved` permits remediation; `rejected`, `timeout`, and missing decisions fail closed with distinct persisted outcomes. Blocked runs use deterministic Comms reporting remediation not executed. Mock/unit dispatch-boundary tests establish this control-flow property, not empirical G4 evidence or safe deployment.

### Gate G3: Controlled SRE Environment — [PASS]
- `artifacts/evidence/stage3/acceptance_report.json` (2026-08-17) records a real `kind-atlasops-local` node, running workloads, coordinator and Online Boutique HTTP 200 responses, and reachable Prometheus, Alertmanager, Jaeger and Argo CD APIs.
- Its overall verdict is PASS, but `kubectl_describe` and `kubectl_logs` wrapper entries are false, `kubectl_get` returns zero items, and Jaeger returns no traces. Retain the local environment milestone without claiming every wrapper passed or trace ingestion was proved. Current cluster health and cloud billing were not checked here.

### Gate G4: Real End-to-End Golden Incident — [NOT_PASSED]
- Initial attempt 001 was invalidated for out-of-band harness fault clearance; subsequent negative results remain preserved. No newer authoritative PASS was found in the tracked Stage 4 evidence.
- `artifacts/evidence/stage4/EXP-STAGE4-SF002-010.json` is the latest completed negative result among 009-014: `gate_g4_pass=false`, objective `env_resolved=false`, and incorrect adservice targeting instead of paymentservice.
- Attempts 009 and 011-014 are interrupted/inconclusive, not successes. Cleanup for 012-014 failed with TLS timeouts; current cleanup state is unverified. Successful cleanup cannot retroactively establish agent resolution.
- `artifacts/evidence/stage4/RECOVERY_INDEX_009_014.md` indexes preserved outcomes. Attempt 010 records approval timeout alongside `8_approval_satisfied=true`; this is an inconsistency, not human approval evidence. Exhausting an attempt budget does not close G4.

### Gate G5: Scenario Truth and Benchmark Splits — [PASS]
- `config/scenario_catalog.py`, `config/splits.py`, and `tests/test_stage5_scenario_splits_and_truth.py` define/check 28 frozen manifests, hashes, verifier coverage and disjoint Train(16)/Val(6)/Test(6) populations.
- Retain scenario/split governance. This does not certify all later training/runtime consumers as leakage-free, nor substitute for G4.

### Gate G6: Reproduce GAI Zero-Shot Baseline — [IMPLEMENTED / EMPIRICAL EVIDENCE MISSING]
- `bench/zero_shot_baseline.py` now has a fail-closed real-inference path with raw-response and model provenance requirements. Recovered validation/test summaries in `artifacts/evidence/mock_archive/stage6/` explicitly record `mock_eval=true`.
- Their metrics are historical mock outputs, not an empirical baseline. A genuine evaluated baseline with run/model/environment provenance is still required.

### Gate G7: Generate SFT Data and Train — [PARTIAL]
- `training/build_sft_dataset.py` synthesizes training-scenario examples; rendering/masking and training code exist in `training/sft_rendering.py`, `training/templates/qwen2_5_tool_sft.jinja`, and `training/sft.py`.
- `artifacts/evidence/stage7/sft_corpus_manifest.json` records 64 examples / 16 training scenarios and corpus hash; `sft_training_config.json` records QLoRA settings. These are corpus/configuration evidence, not a completed training run or proof of successful live expert trajectories.
- `training/sft.py` now persists planned/running/completed-or-failed run provenance and checkpoint inventory when a real run is available. No successful SFT training record tied to a usable checkpoint was verified in the inspected repository/evidence and local checkpoint inventory. Training completion and usability remain unestablished.

### Gate G8: Evaluate SFT Before RL — [IMPLEMENTED / EMPIRICAL EVIDENCE MISSING]
- `bench/sft_eval.py` now requires a completed, provenance-checked local checkpoint for real inference; mock evaluation remains explicitly non-empirical.
- Recovered outputs in `artifacts/evidence/mock_archive/stage8/` are deterministic mock evidence. Previously reported SFT gains and format compliance do not establish checkpoint performance or unlock validated RL training.

### Gate G9: Correct and Train Online GRPO — [REOPENED]
- `training/grpo.py` now sends the policy completion to `DirectPolicyEnvironment` as the exact proposed action. That adapter checks approval and tool policy, executes at most one action, settles, and carries objective verifier fields into reward and curriculum updates.
- New trainer provenance records declared and loaded model revisions, frozen Train hash, source state, seed/configuration, rollout ledger, and a hashed completed adapter inventory. `bench/grpo_eval.py` validates this record and rejects missing or incomplete checkpoints; its mock path is explicitly non-empirical.
- No completed GRPO training run, usable local adapter, or safe real evaluation was obtained. Archived Stage 9 outputs in `artifacts/evidence/mock_archive/stage9/` remain mock evidence. G9 remains reopened pending real authorized trajectories, training, and held-out evaluation.

### Gate G10: Build RS Data and Baselines — [PARTIAL]
- Retain bounded algorithmic work: 12 runbooks, scenario-derived interaction generation, Random/Popularity/BM25 baselines and ranking metrics in `recommender/`. Evaluation fits on Train rows and scores separate splits.
- The original saved Stage 10 result remains historical: 28 scenario-derived rows covering 4 of 12 runbooks, with BM25 Test Hit@3=0.8333 and MRR@3=0.7500. It is not a new measurement or historical user feedback.
- The corrected generator excludes seven scenarios without a defensible single runbook label. The new local corpus has 21 rows (Train 12, Validation 5, Test 4) across 9 runbooks; its manifest records source/split hashes, exclusions, and `historical_user_feedback=false`. Custom dataset output now writes its own adjacent manifest, leaving the prior canonical Stage 10 manifest byte-preserved.
- The historical incident-to-runbook feedback required by G10 has not been obtained. Synthetic baseline measurements establish only the bounded offline algorithmic result.

### Gate G11: Train Hybrid Recommender — [PASS]
- `recommender/hybrid.py` fits content, service/alert co-occurrence and global priors without benchmark tier as a runtime feature. `recommender/train_hybrid.py` performs Train-only fitting, rejects wrong frozen-split assignments, and records corpus, source, and checkpoint hashes.
- Retain the original saved checkpoint and Stage 11 metrics as historical synthetic results. The newly saved `rs_hybrid_eval_synthetic_v2.json` records Test Hit@3=1.0000 and MRR@3=0.7083 on four scenario-derived Test rows; BM25 Hit@3=0.7500 and MRR@3=0.6250 on the same rows. The small sample and synthetic labels do not establish broad superiority or historical-feedback learning.

### Gate G12: Integrate GAI + RS + RL — [IMPLEMENTED / EMPIRICAL EVIDENCE MISSING]
- `agents/coordinator.py` inserts recommendations between Diagnosis and Remediation and supplies `recommended_runbooks`. Its default recommender checkpoint is the newly labeled synthetic v2 artifact; a missing checkpoint leaves recommendations unavailable rather than fitting the old mixed-split dataset.
- An explicit `rl_policy` backend loads a provenance-checked G9 checkpoint and executes its exact structured actions through approval, tool policy, settling, and verification. Local injected-policy tests prove control flow but are non-empirical. Complete end-to-end validation still depends on a real G9 checkpoint and live evidence.

### Gate G13: Run Final Ablation and Stress Evaluation — [REOPENED]
- `bench/ablation_suite.py` now consumes variant episode artifacts and rejects incomplete or unsupported real comparisons; it does not supply model-name constant metrics as empirical results.
- Preserve `artifacts/evidence/stage13/ablation_benchmark_results.json` as historical predetermined output. Previous 100% resolution, 18-second TTR and 0.918 reward claims are not empirical findings. Actual variant execution with valid checkpoints, independent verification, and held-out stress provenance is still required.

### Gate G14: Deploy Final Demo Safely — [PARTIAL]
- UI/demo implementations in `dashboard.py` and `demo/launcher.py` remain; default `DEMO_SAFE_MODE=1` makes the dashboard's kubectl/apply/reset helpers simulate their actions.
- These local helper guards do not certify every coordinator path, an actual safe deployment, or universal zero risk. SAFETY-01 fixes P1 timeout continuation with mock/unit coverage; this does not close an empirical gate. Demo displays of Stage 6/8/9/13 outputs must not be presented as empirical performance.

### Gate G15: Report, Package, and Submit — [PARTIAL]
- The technical report now separates implementation, mock/historical evidence, and missing empirical results. `scripts/package_submission.py` derives the declared G0-G15 inventory and emits `NOT_CERTIFIED` with asset hashes and no invented performance figures.
- Scientific certification and final submission readiness remain unestablished while empirical gates remain open. The prior unsupported report/package statements remain available in Git history; file hashes alone do not close a gate.

---

## Pre-G3 / Pre-G4 Architecture & Security Tracking

### 1. Argo CD Security & Credential Boundary [STATICALLY RESOLVED]
- Do **not** automatically extract or copy `argocd-initial-admin-secret` into coordinator application configuration.
- Use explicit operator-provisioned credentials (`ARGOCD_URL`, `ARGOCD_USER`, `ARGOCD_PASS`) backed by `atlasops-coordinator-secrets` SecretKeyRefs with least-privilege read permissions for the non-destructive G3 tool query contract (`argocd_list_apps`, `argocd_app_get`). Secret presence is validated fail-closed before deployment.
- Transport contract: In-cluster HTTP over ClusterIP with `--insecure` and `ARGOCD_VERIFY_TLS: "false"`. Classified explicitly as a development-cluster exception with credentials traversing only the private in-cluster network path.
- Mutating operations (`argocd_rollback`) enter the remediation approval path. SAFETY-01 requires explicit P1 approval and blocks timeout/rejection before remediation dispatch; validation is mock/unit control-flow evidence only.

### 2. Jaeger Backend Reachability vs. Online Boutique Trace Ingestion [STATICALLY RESOLVED]
- Distinguish:
  - **A. Jaeger Backend Installation & API Reachability**: Verified when the Jaeger query endpoint is deployed and responds HTTP 200 to `jaeger_services_list()` / `GET /api/services` (satisfies Stage 3 non-destructive tool contract).
  - **B. Microservice Trace Ingestion**: Requires OpenTelemetry Collector and application trace exporters in Online Boutique; until trace exporters are instrumented, trace query returns empty traces with `{"success": true, "count": 0}`.
- Chart values derived directly from pinned chart `4.12.0` (`helm show values jaegertracing/jaeger --version 4.12.0`).

---

> [!NOTE]
> This status document reflects source and preserved-evidence review as of 2026-09-06; it does not assert current live service health.
> **Pipeline v1.1 Free-First** (16 August 2026) is the current canonical execution specification.
> **Pipeline v1.0** (11 August 2026) remains the historical record of the original execution specification.
> For the complete academic specification and methodology, refer to the external Master Implementation Pipeline documents.
