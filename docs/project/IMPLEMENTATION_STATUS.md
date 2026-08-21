# AtlasOps implementation status

This is a concise current classification, not a complete implementation audit. For canonical Stage 0-15 sequence, governance rules, and formal gate closure evidence, see [MASTER_PIPELINE_STATUS.md](MASTER_PIPELINE_STATUS.md).


| Area | Baseline status | Project treatment |
|---|---|---|
| Coordinator / four-agent flow | IMPLEMENTED / STATICALLY PACKAGED | Dedicated non-root 9099 container, private Service, safe probe, ConfigMap/Secret references, and bounded RBAC; live rollout/model/tool execution unverified |
| SRE tool policy | STATICALLY VALIDATED | 23 wrappers are registered, 20 are exposed through deterministic role ACLs, and 3 are intentionally unexposed. Cluster mutations, external communications, filesystem writes, and high-risk execution have separate side-effect classifications. Registration/exposure does not guarantee operational availability; Argo and other live integrations remain configuration-dependent and unverified. Chaos deletion (`chaos_stop_experiment`) is remediation-only and namespace-allowlisted to `chaos-mesh`. |
| Safety/approval controls | IMPLEMENTED | Preserve + validate |
| Benchmark runner | REPAIRED | Stage 1B moved tier derivation before judge invocation, added mocked regression coverage, and enforced F821 in CI. Real GKE/Chaos benchmark execution remains UNVERIFIED; published benchmark results remain UNREPRODUCED. |
| SFT pipeline | IMPLEMENTED IN CODE | Reproduce later |
| GRPO | PARTIAL / REVIEW-SENSITIVE | Correct + validate |
| Environment verifier | IMPLEMENTED / CONTRACT VALIDATED / MOCKED/TESTED | Dedicated deterministic `agents/verifier.py` engine aligned with all 28 frozen Chaos Mesh manifests (`bench/chaos_manifests/`) and dynamic adversarial synthesis. Tested with exact workload target matching, tier agreement, selector namespace agreement, Chaos Mesh CRD clearance, legacy deployment removal, and Alertmanager alert clearance; distinguishes `agent_claimed_resolved` from `env_resolved` and penalizes false resolutions in benchmark reward evaluation. Live GKE cluster execution remains unverified until Stage 3. |
| Recommender Systems | ABSENT | Original extension |
| Infrastructure static provisioning | REPAIRED / STATICALLY VALIDATED | Explicit check/apply gates, zonal 1→3 topology, identity/network requirements, immutable pins, and static tests; see `INFRASTRUCTURE_CONTRACT.md` |
| Real GKE provisioning | UNVERIFIED | No setup/teardown apply was run; review Stage 1D-B before a controlled reproduction |
| Prometheus / Alertmanager | STATICALLY WIRED / LIVE UNVERIFIED | kube-state-metrics availability rule plus authenticated private coordinator route; no live alert proof |
| Application metrics | BLOCKED / DEFERRED | Pinned Boutique source does not prove request/error/latency Prometheus metrics |
| Jaeger / tracing | STATICALLY READY / LIVE UNVERIFIED | Jaeger 4.12.0 Helm configuration defined with ClusterIP and bounded development resources; `JAEGER_URL` wired into coordinator; live Query API reachability and Online Boutique trace ingestion pending Stage 3 live execution |
| Argo CD | STATICALLY READY / LIVE UNVERIFIED | Argo CD 10.3.2 controller enabled as canonical G3 component with ClusterIP; Secret-backed credential contract (`argocd-user`/`argocd-pass`); non-destructive `argocd_list_apps` contract with 0 Application ownership |
| Gate G3 Readiness | STATICALLY VALIDATED / LIVE PENDING | Static Helm template validation passed, acceptance matrix codified in `docs/project/G3_ACCEPTANCE_PLAN.md`; zero cloud resources provisioned |
| Published benchmark/result claims | UNVERIFIED BY OUR TEAM | Reproduce |

## Development orchestration note

Native Codex fallback is temporarily authorized because official Sol Advisor 0.5.0 has
a verified Windows `PLUGIN_DATA` validation defect. This optional tooling issue does not
change AtlasOps implementation or runtime status.
