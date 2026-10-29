# AtlasOps Mac runtime acceptance v1

**Verdict: PARTIAL.** The monitoring-to-coordinator pipeline is demonstrated
end to end on a real cluster fault for the first time, and the agents reached
remediation and mutated the live cluster. No incident completed objective
verification, three documented components are absent or empty, and four Online
Boutique services do not stay up. Nothing here is gate evidence, and G4 and G9
remain NOT_PASSED and frozen.

| Field | Value |
|---|---|
| Date | 29 October 2026 |
| Branch | `verify/atlasops-mac-runtime-20261029` |
| Base | `fafe1eb` (origin/main, merge of PR #240) |
| Host | Apple M4, 10 cores, 16 GB, macOS 24.6.0, arm64 |
| VM | Colima, resized 4 CPU / 8 GiB → **8 CPU / 12 GiB** (user-approved) |
| Cluster | Kind `atlasops-local`, Kubernetes v1.37.0, single node |
| Model | `qwen2.5:7b-instruct`, Ollama on the Mac host |
| Spend | $0. No cloud, no EKS, no paid compute. |

## Summary

| Area | Status | Basis |
|---|---|---|
| T1 unit tests on merged source | **PASS** | 200 passed, 1 skipped, 0 failed |
| Bug lab container restart persistence | **PASS after a fix here** | was exit 1 on both v2 and merged main |
| Prometheus serving real metrics | **PASS** | 10/10 targets up at best sample; real boutique series |
| Alert rule fires on a real fault | **PASS** | 5 alerts pending → firing, from kube-state-metrics data |
| Alertmanager → coordinator webhook | **PASS** | authenticated webhook delivered, 200 OK |
| Coordinator receives and opens incidents | **PASS** | 4 incidents, HMAC-chained audit |
| Triage agent acts on the live cluster | **PASS** | 4 real tools, `policy_check: allowed` on all records |
| Remediation agent mutates the live cluster | **PASS, with a flag** | two `kubectl_scale` calls succeeded; `approved_by` empty |
| Objective verification / rollback on K8s | **NOT REACHED** | no verifier or rollback record, no incident closed |
| Grafana dashboards | **BLOCKED** | Grafana is not installed in this cluster |
| Jaeger traces | **FAIL** | 0 services reporting |
| Cluster stability | **PARTIAL** | 4 of 11 boutique services crashloop |
| Kaggle / ngrok model route | **BLOCKED** | no credential, terms not established |
| Bug lab T2–T9 on merged image | **PARTIAL** | T2 verified; T3–T9 not re-run on the new image |

## What was proven, with evidence

### The monitoring-to-coordinator pipeline works

This had not been demonstrated before on this machine. The chain ran on a real
fault, not an injected one: several Online Boutique deployments were genuinely
unavailable, and every step below is from a recorded artifact.

1. **Real metric.** `kube_deployment_status_replicas_available{namespace="default"}`
   returned 13 series; adservice, checkoutservice, emailservice, paymentservice
   and recommendationservice each read `0`.
2. **Real rule.** `AtlasOpsOnlineBoutiqueDeploymentUnavailable` (`for: 2m`,
   labels `atlasops_route=coordinator`, `severity=warning`) went
   `inactive → pending` with 5 alerts, then fired.
   Evidence: `prometheus_rules.json`.
3. **Real routing.** Alertmanager's route matches `atlasops_route="coordinator"`
   and delivers to a webhook receiver with a `credentials_file`. Before the
   coordinator was started, `AlertmanagerFailedToSendAlerts` was active, which is
   itself evidence that delivery was being attempted.
4. **Real delivery.** `received alertmanager webhook: 1 firing alerts`,
   `POST /webhook HTTP/1.1 200 OK`.
5. **Real incidents.** Four opened:
   `inc-1793255445-22b4fb`, `inc-1793255446-4b8d4f`,
   `inc-1793255446-2040f4`, `inc-1793255446-708bc8`.
6. **Real model calls.** `POST http://host.docker.internal:11434/v1/chat/completions
   "HTTP/1.1 200 OK"`, repeatedly. The coordinator reaches the Mac's Ollama from
   inside the cluster; `GET /models` lists `qwen2.5:7b-instruct`.
7. **Real tool use against the live cluster.** 50 audit records, 4 incidents:

   | Role | Tool | Calls |
   |---|---|---:|
   | coordinator | `incident_start` | 4 |
   | triage | `alertmanager_list_alerts` | 8 (4 call + 4 result) |
   | triage | `kubectl_get` | 8 |
   | triage | `kubectl_top_pods` | 8 |
   | triage | `promql_query` | 8 |
   | remediation | `remediation_retry` | 4 |
   | remediation | `kubectl_scale` | 6 (3 call + 3 result) |

   `policy_check` is `allowed` on all 50 records, and the HMAC chain verifies:
   every record's `prev_hash` equals the previous record's `entry_hash`.
   Evidence: `coordinator_audit_log.jsonl`.

Those four triage tools are exactly the four the triage prompt permits, and the agent
stopped at its 4-call budget. The tool ACL is being enforced and recorded, not
merely configured.

### The container could not restart; now it can

`docker restart` exited 1 on **both** the old image and merged main:
`ValueError: Bug Lab workdir must be new or empty`. PR #240 made the workspace
durable but `Dockerfile.buglab` never passed `--resume`, and adding it to the
fixed CMD would have broken first starts instead. `docker/buglab-entrypoint.sh`
now chooses the flag from the marker file at start time. Verified: after
`docker restart`, HTTP 200, `resuming the existing working copy`, history and
`incidents.jsonl` intact, sequence advanced rather than reset.
Evidence: `RESTART_PERSISTENCE.md`.

## What failed or was not reached

### The chain reached remediation and mutated the cluster, then stalled

An earlier draft of this report said the chain stopped at triage. That was
wrong, and the correction matters. The final audit log has **50 records**:

| Role | Records |
|---|---:|
| coordinator | 8 |
| triage | 32 |
| remediation | 10 |

Diagnosis appears nowhere in the audit log, because the audit records tool calls
and the diagnosis agent made none.

Four remediation records are retries with the summary
`turn 0: no executable tool call; retry remediation_no_tool_call_retry` — the
7B model failed to emit a usable tool call and the coordinator retried. Then
**two mutations executed successfully on the live cluster**:

```
kubectl_scale -> deployment.apps/checkoutservice scaled   returncode 0
kubectl_scale -> deployment.apps/paymentservice scaled    returncode 0
```

checkoutservice went from 1 replica to 3 and paymentservice to 3. Both were
reverted to 1 here, because three replicas of a service that crashloops on a
1-second probe timeout adds load to an already strained node and does not fix
the fault. The coordinator was then scaled back to 0 so it would not keep
mutating the cluster unattended.

Still not reached: objective verification and rollback. No verifier record, no
rollback record, no incident closed, no postmortem and no trajectory file.

### Flag: two cluster mutations with no recorded approver

Both `kubectl_scale` records carry `approved_by: ""` with
`policy_check: allowed`. Whether that is correct depends on the severity triage
assigned, and **this could not be determined from the available records**: the
audit log stores tool calls, not the triage verdict, and no trajectory file was
written because no incident closed.

Under `agents/approval.py`, P2 and P3 map to `auto`, so an unapproved mutation
is by design if triage rated these P2 or P3; P1 maps to `approve` and would
require a named approver. The alert's own label was `severity: warning`.

This is raised as a question for the owner, not asserted as a defect. To settle
it, the triage severity for `inc-1793255445-22b4fb` and `inc-1793255446-4b8d4f`
has to be read from a completed trajectory, which this run never produced.

**The fault self-resolved first.** At 06:38 Alertmanager delivered
`0 firing alerts`: the deployments recovered on their own while the agents were
still gathering evidence. On this hardware the agent pipeline is slower than the
incident it is responding to. That is a real result about the 7B model on an
8-core CPU, not a code defect, and it means no remediation claim can be made.

### Three documented components are absent or empty

- **Grafana is not installed.** The `monitoring` namespace has the Prometheus
  operator, Prometheus, Alertmanager and kube-state-metrics, and no Grafana
  deployment or service. The brief asks for populated Grafana panels; there is
  no Grafana to populate. Recorded as BLOCKED rather than worked around.
- **Jaeger reports zero services.** `GET /api/services` returns
  `{"data":[],"total":0}`. Jaeger runs, nothing sends it spans, so there are no
  traces to verify.
- **node-exporter** is absent from the target list, so node-level panels would
  have no source either.

### The cluster is not stable

Four of eleven Online Boutique services crashloop: adservice (291 restarts),
paymentservice (482), checkoutservice (38), recommendationservice (26). All fail
liveness or readiness probes with a **1-second timeout**
(`health rpc did not complete within 1s`). Scrape health fluctuated between
10/10 and 7/10 across samples, and some Prometheus queries timed out entirely.

The VM resize fixed the catastrophic case (run queue 124–131, load 105) but did
not make the stack steady: load returned to 40 on 8 CPUs.
Evidence: `CLUSTER_CPU_STARVATION.md`.

### Kaggle / ngrok

BLOCKED on two independent grounds: no ngrok authtoken exists on this machine,
and Kaggle's terms could not be shown to permit tunnels. The previously working
local Mac model was retained. Nothing was attempted and no Kaggle result is
claimed. Evidence: `KAGGLE_NGROK_BLOCKER.md`.

## Runtime changes made (live cluster only)

No shared Kubernetes manifest in the repository was edited; that code belongs to
the Windows agent. These are runtime changes to this Mac's cluster:

| Change | Reason |
|---|---|
| Colima 4 CPU / 8 GiB → 8 CPU / 12 GiB | the stack does not fit in 4 CPUs |
| `docker restart` of the Kind node | the API server was unreachable; only way to clear the run queue |
| `cartservice` memory 128Mi → 320Mi limit | OOMKilled 438 times at a 115 MiB working set |
| `loadgenerator` → 0 replicas | init container crashlooping 298 times |
| `chaos-controller-manager` 3 → 1 replica | HA replica count the demo does not need |
| `chaos-dashboard` → 0 replicas | UI the agents do not drive |
| `kube-state-metrics` pod deleted once | recovered cleanly with CPU available |
| `atlasops-coordinator` 0 → 1 replica | it was scaled to zero, so webhooks had no listener |

The bug lab on `127.0.0.1:8790` and the model proxy on `127.0.0.1:8000` stayed
loopback-only throughout.

## One finding for the owner of the shared application code

`tests/test_bug_lab_runtime_lock.py::test_pip_require_hashes_rejects_an_unhashed_local_wheel`
shells out to `python -m pip` without checking that pip is importable. On an
interpreter without pip it fails confusingly, and its first assertion passes for
an unrelated reason, so it cannot distinguish "pip refused an unhashed wheel"
from "pip is missing". Suggest a skip guard. Not a product defect; the behaviour
it checks is correct here. Details in `T1_UNIT_TESTS.txt`.

## What this is not

Not a G4 result. G4 remains NOT_PASSED: no incident completed diagnosis,
remediation, approval or objective verification on Kubernetes. G9 remains
frozen. Nothing here is gate evidence, `docs/EVIDENCE_INDEX.md` is untouched,
and no gate status was changed. A demo observation is not a scientific result.
