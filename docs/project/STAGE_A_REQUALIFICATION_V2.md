# Stage A corrected-source requalification v2

Status: **PREPARED SOFTWARE / NOT APPROVED / NOT EXECUTED**.
This protocol proposal does not activate a profile or grant execution authority.
PR 204 at `62bb1768a5964d4932631ce4d0fcb06c29c0cffd` is the preserved baseline.
The correction branch is `fix/stage-a-offline-blockers-20261016`.
The executable causal contract version is `g4-stage-a-causal-v1`.
Historical declarations, source pins, attempts, failures, and approvals remain unchanged.

## Corrected acceptance contract

The source and tests implement the following requirements.
Live qualification remains separate from offline software verification.

| Requirement | Software predicate | Live qualification |
| --- | --- | --- |
| Telemetry | Retain query, evaluation timestamps, source timestamps, labels, and raw results. Reject missing, non-finite, stale, future, or mismatched observations. Require healthy endpoints and two advancing cAdvisor probes at least 30 seconds apart. | BLOCKED until Docker, Kind, and Prometheus are available |
| Target | Exactly one healthy `default/paymentservice` pod; immutable pod UID, single `server` runtime container ID, 200m CPU limit. Recheck identity before injection, deletion, and recovery observations. | BLOCKED pending actual pod reads |
| Fault | Exclusive create, returned StressChaos UID, matching active injected record, exact SF002 spec, stress-instance UID and startTime. Retain complete observations. | BLOCKED pending newly authorized episode |
| Causality | Apply-start monotonic and wall clocks. Exactly one approved exact action, completed within 480 seconds. Recovery within 540 seconds and before conservative 600-second expiry. | BLOCKED pending newly authorized episode |
| CPU recovery | Two fresh raw counter samples strictly after action completion, at least 30 seconds apart; no counter reset. CPU rate must be below baseline + 0.15 cores. Deployment readiness and bounded settling must also pass. | BLOCKED pending actual measurements |
| Communications | Score `summary_for_dashboard`, reject conflicting legacy `summary`, require an outcome-consistent real postmortem. | BLOCKED pending actual model output |
| Cleanup | GET exact object, match run-created UID, DELETE with UID and current resourceVersion preconditions. Never delete an unbound replacement. Verify zero Chaos across the supported kinds independently. Preserve poison markers on uncertainty. | BLOCKED pending actual authorized cleanup |
| Acceptance | Recompute the 15 criteria from saved incident inputs, then independently recompute chronology, CPU recovery, and cleanup. Write a separate acceptance sidecar referencing raw primary and cleanup hashes. | BLOCKED until complete raw evidence exists |

The CPU threshold and deadlines are prospective choices requiring approval.
They cannot alter a historical result. A missing observation is not success.
Baseline timestamps must remain within 60 seconds at injection time, including in replay.
Every CPU sample must carry a cAdvisor cgroup ID matching the captured pod UID
and runtime container ID. Missing identity labels block acceptance.
The trigger is manual coordinator admission over independently measured degradation.
It is not an Alertmanager-originated `HighCpuUsage` alert.

Chaos Mesh v2.8.3 record shape is grounded in upstream
[common_types.go](https://github.com/chaos-mesh/chaos-mesh/blob/v2.8.3/api/v1alpha1/common_types.go),
[key.go](https://github.com/chaos-mesh/chaos-mesh/blob/v2.8.3/controllers/utils/controller/key.go),
and [stresschaos/impl.go](https://github.com/chaos-mesh/chaos-mesh/blob/v2.8.3/controllers/chaosimpl/stresschaos/impl.go).
Kubernetes provides pod UIDs and runtime container IDs, not a separate container UID.

## Source inventory and authorization

After the corrected source is committed, generate an exclusive inventory outside Git:

```powershell
python -B -m scripts.prepare_stage_a_requalification --output C:/AtlasOps/.audit/stage-a-fixes-20261016/source-inventory-final.json
```

The inventory records the commit, dirty state, raw and LF-normalized source hashes,
and declared model identity. It explicitly records runtime and launch authorization as false.
Never overwrite an earlier inventory. Regenerate under a new filename after a source change.
The existing admission checks still reject drift from frozen source hashes.
This inventory cannot replace the reviewed declaration or a new launch permit.

The owner must approve each outstanding item before an episode can start:

- Review the focused software PR. Merge only with separate approval.
- Approve restoration of the existing Docker/Kind environment after the workload inventory.
- Select and qualify the exact Base model revision, tokenizer, files, and effective generation settings.
- Rotate previously exposed provider credentials or authorize unrelated uncompromised local credentials.
  Rotation remains unverified. Do not retrieve, print, or use the affected credentials.
- Approve a new source-pinned declaration and a fresh, finite episode identity.
  Do not reuse an old approval, exhausted identifier, or old source fingerprint.
- Approve the unchanged SF002 manifest hash, one injection, exact action, and maximum cleanup budget.
- Retain authenticated interactive P1 approval for the exact proposed tool and arguments.
- Approve immutable private evidence storage, retention, redacted publication, and the proposed acceptance thresholds.

No launch command is provided because the corrected source has no newly approved active profile.
The next decision is software review and environment restoration, not incident execution.

## Host and environment recovery

Read-only observations on 2026-10-16 found 15.372 GiB usable host RAM,
about 3.069 GiB available, and 12 logical CPUs. Docker Desktop and its backend
had no running processes. `com.docker.service` was stopped with Manual startup.
Ubuntu and docker-desktop were stopped WSL2 distributions. `.wslconfig` was absent.
Docker's Linux-engine pipe was missing. The exact Kind API at `127.0.0.1:59000`
refused connections. The last backend log showed graceful shutdown on 2026-10-08,
not a demonstrated crash or corruption. Existing cluster-container state is unobservable
until the Docker engine returns.

The pinned Online Boutique release at
`98e60f5ee0b643cc00bceb71e6efb89617740432` declares 12 deployments.
Their summed requests are **1368 MiB and 1.57 CPU**.
Their summed container limits are **2542 MiB and 2.825 CPU**.
Paymentservice declares 64 MiB request, 128 MiB limit, and 200m CPU limit.
Its four stress workers run within the container CPU quota, not four unrestricted host cores.

| Local profile blocks | Memory requests | Memory limits |
| --- | ---: | ---: |
| Prometheus, Alertmanager, kube-state-metrics, operator | 416 MiB | 1024 MiB |
| Chaos daemon and one controller replica | 128 MiB | 512 MiB |
| Jaeger | 64 MiB | 256 MiB |
| Argo server, controller, repository server, Redis | 288 MiB | 1152 MiB |
| Coordinator | 256 MiB | 1024 MiB |
| Boutique plus those single-replica blocks | 2520 MiB | 6510 MiB |

These are configured declarations, not measured peaks or guaranteed total capacity.
Helm replica defaults, config reloaders, metrics-server, Kubernetes, containerd,
image decompression, and VM overhead are additional. Preserve the existing deployments;
do not remove services to fit a smaller VM without a separately reviewed profile.
Use **10 GiB VM RAM and 6 CPUs as an initial restoration budget**,
with at least **11.5 GiB host memory available before allocating it**.
This includes a host safety allowance; it is not an immutable admission threshold.
A smaller allocation needs measured stable health and memory-pressure evidence.
The old 16 GiB free-RAM planning estimate is not enforced here.
The pinned 7B model is separate from this cluster budget. Local model memory fit
is unqualified; do not load it into this budget or download weights.

The minimum restoration sequence requires explicit authorization:

1. Save unrelated work and have the owner close only identified disposable applications.
   Re-read host RAM. Do not kill processes, clear caches, or change pagefile settings.
2. Identify any unrelated WSL or Docker workloads. Do not run `wsl --shutdown`.
3. After approval, start the existing Docker Desktop normally. Do not reinstall, factory-reset,
   prune, or recreate its disks. Change WSL limits only with separate approval and backup.
4. Verify the Linux engine, then list all containers with names, state, and labels.
   Starting Docker can resume restart-policy containers; inspect those before starting Kind.
5. Identify the existing `atlasops-local-control-plane` container and its Kind labels.
   If stopped, request approval to start only that container. Never delete or recreate the cluster.
6. Verify the exact context endpoint, Ready nodes, all required deployments, metrics-server,
   zero Chaos, and two stable target/telemetry reads. Inventory actual usage and pressure.
7. If containers or storage are missing, stop. Request a separate recovery proposal.
   Do not invoke either setup script with `--apply`.

The following commands are GET-only checks for use after authorized restoration:

```powershell
docker --context desktop-linux info
docker --context desktop-linux ps -a --format "{{.Names}} {{.Status}} {{.Labels}}"
kubectl --context kind-atlasops-local --request-timeout=10s get nodes
kubectl --context kind-atlasops-local --request-timeout=10s get deployments,pods -A
kubectl --context kind-atlasops-local --request-timeout=10s get pods -n default -l app=paymentservice -o json
kubectl --context kind-atlasops-local --request-timeout=10s get podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos -A
kubectl --context kind-atlasops-local --request-timeout=10s top nodes
kubectl --context kind-atlasops-local --request-timeout=10s top pods -A
```

No restoration, resource mutation, inference, incident, cleanup, training, deployment,
merge, protocol activation, or scientific gate promotion occurred during this correction.
