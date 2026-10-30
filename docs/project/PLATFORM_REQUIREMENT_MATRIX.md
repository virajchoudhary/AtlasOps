# AtlasOps platform requirement matrix

This matrix maps the owner's full-platform brief to the existing implementation,
its software checks, and remaining runtime evidence. The original October 27
Run Plan is not a tracked source file. The
[Mac runbook](BUG_LAB_MAC_RUNBOOK_V1.md) records its implemented sections and
deviations. This matrix uses that record and the current platform brief.

## Source identity

The accepted software baseline is main
`ea797b002c8133d0ab67671c328101e16ebef7d1`, after core PR
[243](https://github.com/virajchoudhary/AtlasOps/pull/243) and Operations PR
[244](https://github.com/virajchoudhary/AtlasOps/pull/244), infrastructure PR
[245](https://github.com/virajchoudhary/AtlasOps/pull/245), and runtime-lock PR
[246](https://github.com/virajchoudhary/AtlasOps/pull/246).
All 18 checks passed on PR 246's reviewed head
`6800308ff5dc84452de2997abb8f9d08fed83a79`.
The full platform remains PARTIAL. Disposable integration PR
[247](https://github.com/virajchoudhary/AtlasOps/pull/247), at
`3216df4a32d4e4ae3ba3910a60fdd3c2051ae0d9`, is source-reviewed but unmerged.
Its [first hosted job](https://github.com/virajchoudhary/AtlasOps/actions/runs/40982696778)
failed. The failing step, successful recovery, and verified cleanup are not
established by the available status observation.

The final external delivery receipt names the exact merged commit and tree,
current CI runs, independent reviews, and archive SHA-256. The
[source-review package](SOURCE_REVIEW_PACKAGE.md) binds every row below to the
files at that commit through `source_inventory.json`, Git blob IDs, and raw-file
hashes. Rows marked unmerged refer to the cited PR source and are not members
of the current main archive. Neither this matrix nor its own presence establishes
a passing test.

## Application and integration

All paths in the table are repository-relative. Tests with command, API, or model
doubles are software evidence, not real-model or scientific acceptance.

| Requirement | Implementation | Software verification | Runtime evidence still required |
| --- | --- | --- | --- |
| Preserve the Bug Lab | `demo/bug_lab/`, `Dockerfile.buglab` | Existing eight-scenario API and `frontend/e2e/bug-lab.e2e.ts` coverage; preserved Mac V1/V2 evidence | Final-source physical-host smoke test; actual endpoint behavior when using a model |
| Monitor, triage, diagnosis, remediation, verifier, Comms | `agents/coordinator.py`, `agents/incident_dispatch.py` | Coordinator, operational mode, tool-policy, verifier, and Comms regressions | Governed real-model incident with independently observed recovery |
| Strict backend and response identity | Coordinator model client and existing Bug Lab `llm.py` | Authenticated endpoint doubles, returned-model checks, parsing, deadline, and outage tests | Accepted weights/serving identity and actual authenticated replies |
| Honest model-versus-rule attribution | `demo/bug_lab/agents.py`, `demo/bug_lab/llm.py` | Startup identity, fallback, malformed response, and credential-safe logging regressions | Real outage and fallback smoke test on the selected model host |
| Workload-scoped agent tools | `agents/tool_policy.py`, coordinator policy checks | `tests/test_operational_read_scope.py`, tool ACL and side-effect tests | Actual API/RBAC and backend reachability for each claimed tool |
| Exact prepared action and target | `agents/action_execution.py` | UID, resourceVersion, source, action-digest, and target-drift tests | Actual conflict and admission behavior on the selected cluster |
| Approval, rejection, expiry, no replay | `agents/approval.py`, `agents/operations_api.py` | Fail-closed approval and authenticated Operations handler tests | Configured operator identity and a real exact-action decision |
| Objective post-fix recovery | `agents/verifier.py` | Exact Deployment identity, fresh metric samples, active-alert, malformed, and negative-result tests | Fresh Kubernetes, Prometheus, and Alertmanager observations after an approved mutation |
| Verifier-authoritative Comms | Coordinator and `agents/tools/comms.py` | `tests/test_operational_comms.py` and coordinator evidence tests | Observed communication matching actual recovery status; external channels only if authorized |
| Durable incident history and export | Execution journal, audit, Operations API | Integrity, transition, unknown historical source, and original execution-source tests | Export/restart checks with the selected runtime-data volume |
| Operator UI | `frontend/src/pages/OperationsPage.tsx`, API parser | Unit tests and mounted authenticated-handler browser workflow, including same-key reconnect | Final-source physical-browser and operator acceptance against the private platform |
| Private integrated container | `Dockerfile`, `Dockerfile.coordinator`, `app.py` | Network-isolated container startup and locked runtime CI | AMD64 and ARM64 runtime behavior on selected hosts |

## Local infrastructure

| Requirement | Implementation | Software verification | Runtime evidence still required |
| --- | --- | --- | --- |
| Owned Kind lifecycle | `infra/local/kind-config.yaml`, setup/teardown scripts | Identity, endpoint, node image, mapping, namespace, and teardown doubles | Selected host engine health, capacity, ownership, startup, and cleanup |
| Single-owner Online Boutique | `infra/local/boutique/kustomization.yaml`, Argo Application and project | Pinned overlay, exact source revision, ownership, and reconcile tests | Actual Argo sync, ready workloads, and checkout traffic |
| Real availability signal | Prometheus values and rendered availability rule | Namespace/Deployment-scoped kube-state-metrics and rule contracts | Fresh desired/available series and firing/cleared target alert |
| Authenticated Alertmanager delivery | Private webhook route and namespace-scoped Secrets | Missing, invalid, and valid authorization tests; private route contracts | Actual alert delivery and authenticated admission |
| Grafana dashboard | `infra/local/grafana-dashboard.yaml` | Dashboard and real metric-name contracts | Correct panels backed by present source-labelled series |
| Jaeger and OpenTelemetry | Local Jaeger values and OTel Collector, `agents/telemetry.py` | Pinned-chart rendering, collector routing, metrics, and span contracts | Actual coordinator and seven source-supported Boutique services' traces |
| Safe GitOps remediation | Rollback-only admission policy and scoped coordinator RBAC | Pinned CRD/server-side admission CI and denial tests | Accepted observed history, exact approved rollback, and objective recovery |
| Safe Chaos Mesh use | Existing identity-bound tools and local controller values | Supported-kind, UID/resourceVersion, context, and cleanup tests | A separately authorized target/fault; installation is not a fault run |
| Cross-platform startup | Guarded shell lifecycle and multi-architecture images | Python/python3 lifecycle, shell checks, and existing native/container CI | Windows/WSL2, Mac, and Linux host-specific acceptance |
| Disposable integrated CI | Unmerged PR 247: `.github/workflows/platform-integration.yml`, `scripts/platform_ci.py` | Independent source acceptance; 242 combined API/command-double regressions passed with one platform-specific skip | First hosted job failed; diagnosis and a passing recovery/cleanup receipt remain required |

The hosted integrated check is `NON_EMPIRICAL_SOFTWARE_FIXTURE`. It uses real
disposable Kubernetes observations but deterministic model replies and synthetic
operator decisions. It cannot close G4, G6, G8, or G9.

## Packaging and historical preservation

| Requirement | Implementation | Acceptance evidence |
| --- | --- | --- |
| Exact source and complete membership | `scripts/package_source_review.py` | Final commit/tree, all tracked paths/modes/blob IDs, archive membership and CRC, canonical rebuild, and SHA-256 |
| Review asset completeness and extracted links | `scripts/package_submission.py`, `scripts/check_submission_links.py` | Required assets, selected hashes/sizes, and local links checked from extracted files |
| Upstream lineage and licenses | `LICENSE`, upstream baseline, bundled notices | Upstream ancestor and identical MIT license; original notices remain packaged |
| Preserve unsuccessful experiments | Existing `artifacts/` and `audit/` records | Unchanged historical Git blobs and classifications; no generated replacement result |
| Preserve scientific status | Master Pipeline, completion checklist, evidence index | Original statuses and negative/null/interrupted/exploratory evidence; no software-derived promotion |

## Remaining dependencies

The [completion checklist](COMPLETION_CHECKLIST.md) controls current gate status.
G4 and G9 remain `NOT_PASSED/FROZEN`; G7 retains its bounded artifact/reload PASS;
G10/G11 remain `OUT_OF_SCOPE`. G6, G8, and G12 lack qualifying incident evidence.
G13 needs comparable accepted arms and its measurement/access freeze.
G14/G15 retain their peer-host, scientific-review, and submission boundaries.

Docker/WSL startup, real model access, faults, remediation, training, Final-Test,
paid cloud, public exposure, credential rotation, billing changes, and submission
retain their explicit authorization boundaries.
The [local runbook](LOCAL_PLATFORM_RUNTIME.md) provides reproducible startup and
readiness commands. The [external closure actions](EXTERNAL_CLOSURE_ACTIONS_20261027.md)
record credential, billing, host, operator, and scientific dependencies.
