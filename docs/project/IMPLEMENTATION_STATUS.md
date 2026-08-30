# AtlasOps implementation status

This is a concise current classification, not a complete implementation audit. For canonical Stage 0-15 sequence, governance rules, and formal gate closure evidence, see [MASTER_PIPELINE_STATUS.md](MASTER_PIPELINE_STATUS.md).


| Area | Baseline status | Project treatment |
|---|---|---|
| Coordinator / four-agent flow | IMPLEMENTED / LIVE VERIFIED | Dedicated non-root 9099 container, private Service, safe probe, ConfigMap/Secret references, and bounded RBAC; live rollout, model inference, and mutating tool execution verified end to end by `EXP-STAGE4-SF002-010` (Gate G4 PASS) |
| SRE tool policy | STATICALLY VALIDATED | 24 wrappers are registered, 19 are exposed through deterministic role ACLs, and 5 are intentionally unexposed. Cluster mutations, external communications, filesystem writes, and high-risk execution have separate side-effect classifications. Registration/exposure does not guarantee operational availability; Argo and other live integrations remain configuration-dependent and unverified. Chaos deletion (`chaos_stop_experiment`) is remediation-only and namespace-allowlisted to `chaos-mesh`. |
| Safety/approval controls | IMPLEMENTED | Preserve + validate |
| Benchmark runner | REPAIRED | Stage 1B moved tier derivation before judge invocation, added mocked regression coverage, and enforced F821 in CI. Real GKE/Chaos benchmark execution remains UNVERIFIED; published benchmark results remain UNREPRODUCED. |
| SFT pipeline | IMPLEMENTED IN CODE | Reproduce later |
| GRPO | PARTIAL / REVIEW-SENSITIVE | Correct + validate |
| Environment verifier | IMPLEMENTED / CONTRACT VALIDATED / MOCKED/TESTED | Dedicated deterministic `agents/verifier.py` engine aligned with all 28 frozen Chaos Mesh manifests (`bench/chaos_manifests/`) and dynamic adversarial synthesis. Tested with exact workload target matching, tier agreement, selector namespace agreement, Chaos Mesh CRD clearance, legacy deployment removal, and Alertmanager alert clearance; distinguishes `agent_claimed_resolved` from `env_resolved` and penalizes false resolutions in benchmark reward evaluation. Live GKE cluster execution remains unverified until Stage 3. |
| Recommender Systems | ABSENT | Original extension |
| Infrastructure static provisioning | REPAIRED / STATICALLY VALIDATED | Explicit check/apply gates, zonal 1→3 topology, identity/network requirements, immutable pins, and static tests; see `INFRASTRUCTURE_CONTRACT.md` |
| Real GKE provisioning | UNVERIFIED | No setup/teardown apply was run; the canonical environment is local Kind, so GKE remains optional portability code |
| Gate G4 golden incident | **PASS** | `EXP-STAGE4-SF002-010`, all 15 causal criteria, live Kind cluster. Two conditions were previously unsatisfiable regardless of agent behaviour: an unobservable goal state, and a settling report the coordinator never returned. See `G4_LIVE_RUN_RECORD.md` |
| Prometheus / Alertmanager | LIVE VERIFIED | Live alert fired and delivered to the coordinator during Gate G4; CPU degradation measured through PromQL against the F1 envelope. Requires `prometheusOperator.tls.enabled: false` alongside disabled admission webhooks, or the operator never schedules |
| Application metrics | BLOCKED / DEFERRED | Pinned Boutique source does not prove request/error/latency Prometheus metrics |
| Jaeger / tracing | DEPLOYED / TRACE INGESTION UNVERIFIED | Jaeger 4.12.0 Helm configuration defined with ClusterIP and bounded development resources; `JAEGER_URL` wired into coordinator; live Query API reachability and Online Boutique trace ingestion pending Stage 3 live execution |
| Argo CD | LIVE DEPLOYED / 0 APPLICATIONS | Argo CD 10.3.2 controller enabled as canonical G3 component with ClusterIP; Secret-backed credential contract (`argocd-user`/`argocd-pass`); non-destructive `argocd_list_apps` contract with 0 Application ownership |
| Gate G3 Readiness | PASS / LIVE REPRODUCED | Full stack provisioned from a clean host on arm64 via Colima; `setup_local.sh --apply` exits 0. Deviations recorded in `LOCAL_ARM64_DEVIATIONS.md` |
| Published benchmark/result claims | UNVERIFIED BY OUR TEAM | Reproduce |

## Development orchestration note

Native Codex fallback is temporarily authorized because official Sol Advisor 0.5.0 has
a verified Windows `PLUGIN_DATA` validation defect. This optional tooling issue does not
change AtlasOps implementation or runtime status.
