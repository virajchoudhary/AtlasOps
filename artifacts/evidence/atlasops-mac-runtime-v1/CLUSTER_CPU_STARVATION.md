# Finding: the documented local stack does not fit a 4-CPU Colima VM

Observed 29 October 2026 on the Mac, before any change was made. This is the
state the Kind cluster had been left in, not a state this run created.

## What was found

The cluster was in a self-sustaining cascading failure. Every component was
installed and every UI was reachable, and none of it worked.

| Measurement | Value |
|---|---|
| Colima VM | 4 CPU / 8 GiB (host: 10 cores / 16 GB) |
| Node CPU requests / limits | 3265m (81%) / 5625m (140%) |
| Node memory requests / limits | 3074Mi (38%) / 6082Mi (76%) |
| Load average (1/5/15) at first look | 38.57 / 23.33 / 10.43 |
| Load average 12 minutes later | 105.75 / 68.41 / 32.62 |
| `vmstat` run queue | 124, then 131 |
| `vmstat` idle / iowait | 0% / 0% |
| CPU split | 73% user, 27% system |
| Node container CPU (docker stats) | 428.75% of 400% available |
| Node memory | 3.8 GiB of 7.7 GiB used, 4.8 GiB available |

Memory was never the constraint. CPU was oversubscribed by roughly 30x at the
run-queue level.

## The failure loop

Pods crashloop, each restart spawns `runc` (measured at 30.5% of one CPU), the
run queue grows, liveness probes stop completing inside their timeout, the
kubelet kills more containers, and those restarts spawn more `runc`.

Components killed by failed liveness probes, from `kubectl get events`:

```
default     Killing  pod/adservice-744d57df97-wwrbw        Container server failed liveness probe, will be restarted
default     Killing  pod/paymentservice-54555d986f-xlcn4   Container server failed liveness probe, will be restarted
argocd      Killing  pod/argocd-repo-server-855d5fb89c     Container repo-server failed liveness probe, will be restarted
monitoring  Killing  pod/prometheus-...-prometheus-0       Container prometheus failed liveness probe, will be restarted
```

cartservice specifically: `Liveness probe failed: timeout: health rpc did not
complete within 5s`, 353 times over 25 hours.

Restart counts reached: paymentservice 462, cartservice 443, loadgenerator 298,
adservice 273, currencyservice 198, argocd-repo-server 39.

## Consequences that matter for acceptance

1. **Prometheus never became ready.** `/-/healthy` returned 200 while `/-/ready`
   and every `/api/v1/*` query returned `503 Service Unavailable`. A reachable
   Prometheus UI was not a working Prometheus: no query, no target list, no
   alert list could be retrieved. This is exactly the case the brief warns
   about — installed and reachable is not integrated.
2. **The Metrics API was down.** `kubectl top node` returned "Metrics API not
   available"; metrics-server was crashlooping, its own `/livez` and `/readyz`
   timing out. `kubectl_top_pods` is one of the tools the Triage agent is
   allowed to call, so that tool was broken.
3. **The Kubernetes API itself became unreachable** during the session:
   `net/http: TLS handshake timeout` on every call, so the cluster could not be
   administered at all until the node was restarted.
4. **cartservice was genuinely undersized**, separate from the CPU problem:
   OOMKilled with exit code 137, 438 restarts, working set 115 MiB against a
   128 MiB limit.

## What was changed, and what it did not fix

Runtime-only changes to the live cluster (no repository manifest was edited;
shared Kubernetes infrastructure code belongs to the Windows agent):

| Change | Reason |
|---|---|
| `docker restart atlasops-local-control-plane` | Only way to clear a run queue of 130 with the API unreachable. Touched the Kind node container only; the bug lab and proxy containers kept serving throughout and were verified at HTTP 200 before and after. |
| `loadgenerator` → 0 replicas | Init container crashlooping 298 times, and its purpose is to generate load this node could not afford. |
| `chaos-controller-manager` 3 → 1 replica | Shipped HA replica count; one is enough for a demo. |
| `chaos-dashboard` → 0 replicas | UI not driven by the agents. |
| `cartservice` memory 128Mi → 320Mi limit, 64Mi → 192Mi request | Observed working set 115 MiB. Memory was not scarce. |

Result: load fell from 105.75 to 10.62 over about 15 minutes and all Online
Boutique services reached Running, **but it did not hold**. Load returned to
33.95 with a run queue of 128 on the next sample. Shedding load was not enough:
the stack does not fit in 4 CPUs.

## Conclusion

The sizing in the local setup documentation is not sufficient for the full
documented stack (Online Boutique + kube-prometheus-stack + Jaeger + Argo CD +
Chaos Mesh + coordinator) on a 4-CPU Colima VM. The VM was resized to 8 CPU /
12 GiB with the user's explicit approval, leaving the Mac 2 cores and 4 GB.

Recorded separately: whether the resize is sufficient, and what the stack then
actually verifies.
