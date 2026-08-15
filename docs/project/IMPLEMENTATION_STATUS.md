# AtlasOps implementation status

This is a concise current classification, not a complete implementation audit.

| Area | Baseline status | Project treatment |
|---|---|---|
| Coordinator / four-agent flow | IMPLEMENTED / STATICALLY PACKAGED | Dedicated non-root 9099 container, private Service, safe probe, ConfigMap/Secret references, and bounded RBAC; live rollout/model/tool execution unverified |
| SRE tool policy | STATICALLY VALIDATED | 22 wrappers are registered, 19 are exposed through deterministic role ACLs, and 3 are intentionally unexposed. Cluster mutations, external communications, filesystem writes, and high-risk execution have separate side-effect classifications. Registration/exposure does not guarantee operational availability; Argo and other live integrations remain configuration-dependent and unverified. |
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
| Jaeger / tracing | BLOCKED / DEFERRED | No trace backend installed; `JAEGER_URL` fails closed when absent |
| Argo CD ownership | OPTIONAL / DEFERRED | Controller default-off; no Application objects or dual ownership; tools remain configuration-gated |
| Published benchmark/result claims | UNVERIFIED BY OUR TEAM | Reproduce |

## Development orchestration note

Native Codex fallback is temporarily authorized because official Sol Advisor 0.5.0 has
a verified Windows `PLUGIN_DATA` validation defect. This optional tooling issue does not
change AtlasOps implementation or runtime status.
