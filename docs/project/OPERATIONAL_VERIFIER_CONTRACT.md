# Operational verifier contract

## Selection

`EnvironmentVerifier.verify()` uses the operational availability policy only when
`scenario_id` is empty and the alert name is
`AtlasOpsOnlineBoutiqueDeploymentUnavailable`. A non-empty scenario ID keeps its
existing scenario-resolution path. The frozen scenario specifications are unchanged.
With `incident_context.operational_mode=true`, an empty-scenario alert that does not
identify this registered policy fails closed instead of falling through to dynamic
scenario synthesis.

The operational policy checks one Online Boutique Deployment. It does not run the
cluster-wide Chaos Mesh clearance check.

## Alert target

The alert must name `AtlasOpsOnlineBoutiqueDeploymentUnavailable` in at least one
`commonLabels.alertname` or `alerts[].labels.alertname` field. Every supplied
`alertname` must match that value.

The target must resolve through `deployment`, `service`, or `app`. Every supplied
target label must name the same exact value from `ONLINE_BOUTIQUE_SERVICES`. A
`commonLabels` target can supply a value that an individual alert omits. If the
common target is absent, every individual alert must identify the same target.

The alert payload must contain at least one alert object with a labels object. The
namespace must match `ATLASOPS_WORKLOAD_NAMESPACE`, which defaults to `default`
when unset and must be a valid Kubernetes namespace. A common namespace can
supply a value that an individual alert omits. If the common namespace is absent,
every individual alert must provide the configured workload namespace. Missing,
empty, malformed, unknown, or conflicting labels, or an invalid namespace
configuration, return `verification_status="error"` with `env_resolved=false`.
The verifier does not infer a frozen scenario ID from alert labels.

When `incident_context.operational_mode` is `true`, the context must contain the
executed action's `prepared_action`; final verification may read it from
`remediation.final.executed_actions`. The action's tool and arguments must match
the prepared action. If `prepared_action.verification_target` is present, it is
the immutable Deployment identity for verification. Its non-empty `server`,
`context`, `api_version`, `kind`, `namespace`, `name`, `uid`, and
`resource_version` fields must identify an `apps/v1` Deployment whose namespace
and name match the alert labels. Its `server` and `context` must match the
prepared mutation target. A present but malformed `verification_target` is an
error and does not fall back to the mutation target. If this field is absent, the
legacy `prepared_action.target` must itself be an `apps/v1` Deployment. Actions
whose mutation target is Chaos Mesh or an Argo CD Application therefore require
the prepared `verification_target`. The current Deployment `metadata.uid` must
match the prepared UID. Current `resourceVersion` is recorded but not compared
with the prepared resourceVersion because a successful mutation changes it.
The observation runs through the existing Kubernetes getter; this verifier does
not perform an independent API-server identity lookup.

## Recovery evidence

The Kubernetes query names the exact target Deployment and must return
structured JSON for one `apps/v1` `Deployment` (or an `apps/v1`
`DeploymentList` containing exactly one matching object). The target object's
`metadata.name`, `metadata.namespace`, `metadata.uid`, and
`metadata.resourceVersion` must be valid. Its status must have caught up with the
object generation. The verifier requires
`status.observedGeneration >= metadata.generation`, `spec.replicas > 0`, and
both `status.readyReplicas` and `status.availableReplicas` at least equal to the
desired replica count.

Prometheus must return exactly one finite, non-negative integer series for each
of these metrics:

- `kube_deployment_spec_replicas`
- `kube_deployment_status_replicas_available`

Each query selects the exact validated namespace and Deployment. The verifier
requests both the replica value and `time() - timestamp(metric)` at the same
explicit Prometheus evaluation time. Before aggregation, each query filters source
series to `0 <= time() - timestamp(metric) <= 60`. It checks source age again at
receipt and records the evaluation time, derived source-sample timestamp,
observation time, source age, target labels, and queries in the check evidence.
The vector result timestamp alone does not prove that its source sample is fresh.
The Prometheus desired replica count must match the current Kubernetes spec, and
the available count must meet that desired count. Empty, malformed, stale, future,
nonfinite, or incorrectly labelled series do not pass.

The Alertmanager response must contain no alert with the target alert name,
configured namespace, and Deployment. The adapter explicitly includes active,
silenced, inhibited, and unprocessed alert states. An alert for another known
Online Boutique Deployment in the configured namespace does not block the
target. A target alert still present in the response blocks recovery, regardless
of its reported status; a silence is not recovery evidence. An alert with
missing, malformed, or unknown namespace/Deployment scope makes the check
inconclusive.

## Result status

The verifier returns `passed` only when the current Deployment, both fresh
Prometheus samples, the desired-versus-available predicate, and scoped
Alertmanager check all pass. A concrete unhealthy Deployment, metric predicate,
or still-present target alert returns `failed`. If no concrete predicate is false
but required telemetry is missing or malformed, the verifier returns
`inconclusive`. Every non-passing result has `env_resolved=false`.

The generic verifier also rejects empty, malformed, and nonfinite Prometheus
samples. Kubernetes workload checks require structured resource data and exact
names. Checks for legacy-deployment and Chaos Mesh absence require a parseable
resource list. These parser rules do not change the sparse readiness semantics of
the frozen scenario fixtures.

This code contract does not change frozen experiment records, establish empirical
G4 recovery, or authorize a live incident run.
