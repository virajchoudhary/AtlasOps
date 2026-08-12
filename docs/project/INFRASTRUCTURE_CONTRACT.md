# AtlasOps infrastructure contract

## Status and safety boundary

Stage 1C found that the inherited infrastructure scripts were **not safe to run**:
they could provision on ordinary invocation, misreported regional node topology,
used invalid identifiers, floated external dependencies, enabled unwired paid
components, exposed operator UIs, and hid failures.

Stage 1D-A repairs the static provisioning and teardown contract. It does not
prove that a real GKE deployment works or that AtlasOps observability is wired.

> **DO NOT RUN LIVE GKE UNTIL STAGE 1D-B IS COMPLETE.**

## Development GKE topology

The reviewed academic/development baseline is a GKE **Standard**, **zonal**
cluster. The default region remains `us-central1` for regional services, while
the default cluster zone is `us-central1-a`. `ATLASOPS_GKE_ZONE` may override
the zone only with a value in the selected region.

| Property | Contract |
|---|---|
| Availability claim | Development/research; not production HA |
| Machine type | `e2-standard-4` |
| Initial nodes | 1 total node in one zone |
| Cluster autoscaler | Enabled, minimum 1 and maximum 3 nodes |
| Workload Identity pool | `<PROJECT_ID>.svc.id.goog` |
| Ownership labels | `managed-by=atlasops`, `environment=development` |

The original regional command created one node in each of three zones while
describing the result as one node. The zonal contract makes the 1→3 cost intent
truthful. GKE documents `--min-nodes` and `--max-nodes` as per-zone limits; one
zone therefore makes them the total node limits for this baseline.

## Explicit safety modes

Provisioning and teardown reject invocations that omit a mode or use an unknown
mode.

```bash
bash infra/setup.sh <PROJECT_ID> [REGION] [CLUSTER_NAME] --check
bash infra/setup.sh <PROJECT_ID> [REGION] [CLUSTER_NAME] --apply

bash infra/teardown.sh <PROJECT_ID> [REGION] [CLUSTER_NAME] --check
bash infra/teardown.sh <PROJECT_ID> [REGION] [CLUSTER_NAME] --apply
```

`--check` performs local validation and read-only GCP inspection only. Setup
`--apply` additionally requires:

```text
ATLASOPS_COST_ACK=I_UNDERSTAND_GCP_COSTS
```

This is a cost acknowledgement, not a GCP Budget. Destructive teardown requires:

```text
ATLASOPS_TEARDOWN_ACK=DELETE_ATLASOPS_DEVELOPMENT_RESOURCES
```

Neither acknowledgement is a secret. The scripts never change global gcloud
configuration; project and location are explicit on applicable commands.

## Identity and control-plane access

Both setup modes require `ATLASOPS_GKE_NODE_SERVICE_ACCOUNT` to name a dedicated,
pre-existing service account in the selected project. The script verifies its
existence read-only and passes it to GKE. It does not create the account or grant
IAM roles. The operator must prepare and review least-privilege permissions
separately; static preflight cannot prove every effective permission.

`ATLASOPS_GKE_AUTHORIZED_NETWORKS` must contain one or more explicit IPv4 CIDRs.
`0.0.0.0/0` is rejected. Apply uses GKE master authorized networks and fails
when the configuration is absent or invalid. This baseline retains public nodes
and a public control-plane endpoint constrained by authorized networks; it does
not build private nodes, Cloud NAT, or a private endpoint. Current GKE behavior
also permits Google Compute Engine public IPs for a non-private cluster. That
remaining network-hardening limitation must be reviewed before any production
claim.

## Component classification

| Classification | Components | Stage 1D-A behavior |
|---|---|---|
| Core foundation | GKE, Online Boutique | Statically contracted; live apply unverified |
| Core but not yet wired | Prometheus/Alertmanager, Jaeger, Argo CD, Chaos Mesh, AtlasOps coordinator | Base Prometheus, Argo CD, and Chaos Mesh installs are pinned; Jaeger is blocked; coordinator and integrations are Stage 1D-B |
| Optional/deferred | Cloud SQL, Pub/Sub, Linkerd, Artifact Registry, Cloud Build | All default off; only Pub/Sub has a reviewed opt-in lifecycle |

The deferred Cloud SQL canonical identifier is reserved as `atlasops-cart-db`,
but Stage 1D-A creates and deletes no Cloud SQL instance. Linkerd has no setup
flag and no installer. Artifact Registry and Cloud Build flags fail closed when
set to `true` because no approved workflow consumes them.

Optional flags use strict `true`/`false` parsing and default to `false`:

- `ATLASOPS_ENABLE_CLOUD_SQL`
- `ATLASOPS_ENABLE_PUBSUB`
- `ATLASOPS_ENABLE_ARTIFACT_REGISTRY`
- `ATLASOPS_ENABLE_CLOUD_BUILD`

When Pub/Sub is enabled, setup checks explicit topic/subscription existence,
ownership labels, and relationships. Teardown targets only the same exact
identities after the same flag is enabled. This does not claim that AtlasOps
consumes Pub/Sub.

## Immutable supply-chain pins

| Dependency | Human version | Immutable/reviewed pin | Stage 1D-A status |
|---|---|---|---|
| Online Boutique | `v0.10.0` | commit `98e60f5ee0b643cc00bceb71e6efb89617740432` | Manifest URL uses commit |
| kube-prometheus-stack | chart `88.3.0` | explicit Helm `--version 88.3.0` | Base install only |
| Jaeger | chart `4.12.0`, app `2.20.0` | provenance constant `4.12.0` | **Blocked for Stage 1D-B; not installed** |
| Argo CD | chart `10.3.2`, app `v3.5.0` | explicit Helm `--version 10.3.2` | Base controller only |
| Chaos Mesh | chart/app `2.8.3` | explicit Helm `--version 2.8.3` | Base controller only |
| Linkerd | None | None | Deferred; no remote installer |

Pins were resolved from the projects' official GitHub tag/chart metadata and
official Helm repositories. The values-key structure used here was checked
against the pinned Prometheus, Argo CD, and Chaos Mesh chart sources. Jaeger is
deliberately not treated as deployable: the inherited values target an older
values and trace-export contract.

The GKE Kubernetes patch version remains managed by the `stable` release
channel rather than an immutable version constant. This is a disclosed platform
version limitation, not a floating manifest or chart; a live Stage 1D-B plan
must record the resolved control-plane and node versions as experiment evidence.

## Exposure and storage defaults

Grafana, Argo CD server, the future Jaeger query UI, and Chaos Mesh dashboard use
`ClusterIP`. Operator access is by `kubectl port-forward` after the intended
cluster context is independently confirmed. Project-managed observability/admin
UIs therefore expect zero public `LoadBalancer` Services by default.

The pinned Online Boutique manifest separately defines one public
`frontend-external` LoadBalancer. The pre-apply summary reports it separately.
The reviewed project-managed persistent-storage request is Prometheus `20Gi`.

## What Stage 1D-A proves

- Ordinary setup/teardown invocation cannot mutate without explicit mode.
- Apply and destructive teardown have separate acknowledgements.
- Topology, location, identity, authorized networks, labels, and autoscaling are
  explicit and incompatible existing clusters fail closed.
- Optional components default off and disabled APIs are not enabled.
- Active external manifests/charts have immutable or explicit version pins.
- Premature coordinator Alertmanager routing is absent.
- Static regression tests and shell parsing require no cloud or cluster contact.

## What Stage 1D-A does not prove

- Real GKE provisioning, quotas, IAM sufficiency, or teardown behavior
- Coordinator Deployment/Service or Alertmanager-to-coordinator routing
- Online Boutique Prometheus scrape configuration or alert rules
- Jaeger values compatibility, OTel ingestion, or trace-query behavior
- Argo CD Application ownership/wiring
- Observability integration, Chaos Mesh experiments, or full AtlasOps operation
- A GCP Budget, absence of all billable resources, or production hardening

Stage 1D-B must resolve these core runtime and observability contracts before a
live GKE run is permitted.

## Primary references

- [GKE cluster create flags](https://cloud.google.com/sdk/gcloud/reference/container/clusters/create)
- [GKE cluster autoscaler](https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-autoscaler)
- [GKE control-plane authorized networks](https://cloud.google.com/kubernetes-engine/docs/how-to/latest/network-isolation)
- [Google Cloud billing project inspection](https://cloud.google.com/sdk/gcloud/reference/billing/projects/describe)
- [Online Boutique release repository](https://github.com/GoogleCloudPlatform/microservices-demo/tree/v0.10.0)
- [Prometheus Community charts](https://github.com/prometheus-community/helm-charts)
- [Jaeger charts](https://github.com/jaegertracing/helm-charts)
- [Argo Helm charts](https://github.com/argoproj/argo-helm)
- [Chaos Mesh charts](https://charts.chaos-mesh.org/)
