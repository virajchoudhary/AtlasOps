# Run the local AtlasOps platform

This guide describes the controlled local Kubernetes runtime of AtlasOps.
It does not authorize infrastructure startup, a model request, a fault,
remediation, training, or public deployment.

Keep the [Bug Lab](BUG_LAB_LOCAL_RUNTIME.md) for its existing local application
demonstration. Its Control Room and synthetic Store run through
`python -m demo.bug_lab` on a separate port. The Kubernetes runtime uses the
integrated application and its Operations view with real Online Boutique.
These are runtime modes of AtlasOps, not separate products.

## Select the source and host

Use a clean, complete checkout of an accepted main commit. Keep evidence and
runtime secret files outside the checkout. Record the source commit and tree.

```bash
git status --porcelain --untracked-files=all
git rev-parse HEAD
git rev-parse HEAD^{tree}
```

On Windows, use an owner-qualified WSL2 distribution with Docker Desktop
integration. On macOS or Linux, use the owner's selected Linux container engine.
Do not change another host's Docker, Colima, model, or tunnel settings.

The shell needs Bash, Git, Docker, Kind, kubectl, Helm, and Python 3.11 or 3.12.
Install the project's runtime dependencies in an isolated environment.
After selecting Python 3.11 or 3.12, create and activate the environment in
the selected Linux shell or WSL2 distribution.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e . "bcrypt==5.0.0"
```

The container build uses the checked-in runtime locks. The host environment
above runs the setup and readiness helpers; it is not a model-serving or
training environment. The explicit bcrypt dependency supports the local Argo
password helper and matches the tested development lock.

The host must fit all manifest resource requests, image storage, and runtime
data with headroom. Installed tools and a static check do not prove capacity.

The Kind configuration pins Kubernetes `v1.35.8` and its node-image digest.
Hosted CI uses Kind `0.33.0`, kubectl `1.35.8`, and Helm `3.17.3`.
The Docker image includes architecture-specific, SHA-256-checked kubectl for
AMD64 and ARM64. These source pins are not physical-host acceptance.

## Prepare the authorized inputs

Export the following inputs in the selected shell before invoking a script.

| Input | Required meaning |
| --- | --- |
| `ATLASOPS_KIND_CLUSTER_NAME` | Owner-selected Kind cluster name. The default is `atlasops-local`. |
| `ATLASOPS_SECRET_DIR` | Absolute owner-private directory containing the generated secret files. |
| `ATLASOPS_VLLM_BASE` | Reviewed OpenAI-compatible endpoint accessible from the selected cluster. |
| `ATLASOPS_AGENT_MODEL` | Exact identifier reported by the authorized serving endpoint. |
| `ATLASOPS_OPERATOR_ID` | Configured principal allowed to decide prepared actions. |
| `PYTHON_BIN` | Optional explicit Python executable. Direct commands default to the activated environment's python; lifecycle scripts choose python3, then python. |
| `ATLASOPS_KUBECONFIG` | Explicit kubeconfig for readiness verification on the selected target. |
| `ATLASOPS_EVIDENCE_DIR` | Owner-private evidence directory outside the checkout. |

After the owner approves secret preparation, generate fresh material in the
absolute owner-private directory selected above. The shell rejects an unset or
empty directory. The generator rejects relative paths and paths inside any Git
checkout before writing files.

```bash
"${PYTHON_BIN:-python}" -B -m scripts.generate_runtime_secrets \
  --output-dir "${ATLASOPS_SECRET_DIR:?Set an absolute owner-private directory outside the checkout}"
```

Do not use `--force` to replace active credentials. Do not retrieve or test
historically exposed values. Secret generation does not complete outstanding
provider-console rotation.

An authenticated model endpoint also needs an owner-approved
`llm-api-key.secret` in the secret directory. Provision its contents through the
authorized secret-delivery process. Do not put the value in arguments, Git,
screenshots, or incident exports.

## Inspect before startup

These commands do not create a cluster or apply a workload.

```bash
"${PYTHON_BIN:-python}" -B -m scripts.verify_platform_infrastructure validate-static
bash infra/local/setup_local.sh --check
"${PYTHON_BIN:-python}" -B -m scripts.verify_platform_infrastructure inspect-kind-cluster \
  --cluster-name "$ATLASOPS_KIND_CLUSTER_NAME" \
  --kube-context "kind-$ATLASOPS_KIND_CLUSTER_NAME"
```

The setup check requires a running Linux Docker engine and prepared local secret
files. It does not start the engine. Cluster inspection returns `3` when the
named cluster is absent. Any other nonzero result is an ownership, version,
mapping, namespace, or reachability failure. Do not delete or recreate a cluster
to work around a failed inspection.

Confirm source, API endpoint, node image, workload ownership, and capacity before
reuse. A stale kubeconfig does not establish that a cluster is absent.

## Start only the approved target

After approval for this exact local startup, use the checked-in entrypoint.

```bash
bash infra/local/setup_local.sh --apply
```

Setup builds the current source image, loads it into the selected Kind cluster,
and installs the private component configuration. It does not push an image or
create a cloud resource.

Argo CD owns the pinned Online Boutique overlay in `atlasops-boutique`.
The coordinator has read-only workload permissions. Its atomic Kubernetes
Application patch is constrained by the rollback-only admission policy.
Setup requires current type-check status before binding that policy.

Setup installs Chaos Mesh controllers and CRDs without injecting a fault.
Its success message does not establish a scientific gate.

## Open the application privately

Online Boutique uses the loopback-only Kind mapping at `127.0.0.1:8080`.
Keep all operator and observability access on loopback.

```bash
kubectl --context="kind-$ATLASOPS_KIND_CLUSTER_NAME" \
  --namespace=default port-forward --address=127.0.0.1 \
  service/atlasops-coordinator-svc 9099:9099
```

Service port `9099` targets the integrated application on Pod port `7860`.
Open the application at `http://127.0.0.1:9099/` and select Operations.
Supply the authorized operator key through the UI. The browser keeps the key in
memory, not in a URL or persistent storage. The standalone Bug Lab is not mounted
at this URL.

Use separate loopback forwards for observability and administration.

```bash
kubectl --context="kind-$ATLASOPS_KIND_CLUSTER_NAME" -n monitoring \
  port-forward --address=127.0.0.1 svc/prometheus-grafana 3000:80
kubectl --context="kind-$ATLASOPS_KIND_CLUSTER_NAME" -n monitoring \
  port-forward --address=127.0.0.1 svc/prometheus-kube-prometheus-prometheus 9090:9090
kubectl --context="kind-$ATLASOPS_KIND_CLUSTER_NAME" -n monitoring \
  port-forward --address=127.0.0.1 svc/prometheus-kube-prometheus-alertmanager 9093:9093
kubectl --context="kind-$ATLASOPS_KIND_CLUSTER_NAME" -n jaeger \
  port-forward --address=127.0.0.1 svc/jaeger 16686:16686
kubectl --context="kind-$ATLASOPS_KIND_CLUSTER_NAME" -n argocd \
  port-forward --address=127.0.0.1 svc/argocd-server 8443:80
```

Do not publish these interfaces or model administration endpoints.

## Collect readiness evidence

Use the explicit kubeconfig and matching context. The verifier discovers Services
and ready Pods on that target and creates its own loopback forwards. It checks
source identity before reading the operator key and closes its forwards before
writing evidence.

```bash
"${PYTHON_BIN:-python}" -B -m scripts.verify_platform_infrastructure verify-live \
  --source-sha "$(git rev-parse HEAD)" \
  --kubeconfig "$ATLASOPS_KUBECONFIG" \
  --kube-context "kind-$ATLASOPS_KIND_CLUSTER_NAME" \
  --operator-key-file "$ATLASOPS_SECRET_DIR/atlasops-api-key.secret" \
  --evidence-file "$ATLASOPS_EVIDENCE_DIR/readiness.json"
```

This check verifies software signals. It does not inject a fault, establish model
quality, or certify incident recovery. Seven Boutique services have
source-supported tracing patches. Do not describe the others as instrumented.
Deployment availability uses kube-state-metrics. Span-derived traffic and latency
metrics do not supply application metrics for uninstrumented services.

For a separately authorized incident, retain the prepared action, resource UID
and resourceVersion, approval digest, operator decision, mutation result,
objective verifier observations, signed execution journal, and audit export.
Rejection, timeout, expiry, stale evidence, and uncertain mutation must remain
unresolved. Comms cannot convert an unverified repair into success.
The [operational verifier contract](OPERATIONAL_VERIFIER_CONTRACT.md) defines the
recovery predicates.

Jaeger traces are bounded and in memory. Export required traces before replacing
its Pod. The runtime-data PVC retains the journal, audit, local Comms, and
postmortems across coordinator restarts. Cluster deletion removes that local data.

## Stop only the approved target

Export incident and cleanup evidence before an approved teardown.

```bash
bash infra/local/teardown_local.sh --check
```

After separate authorization to delete the selected cluster, set the explicit
acknowledgement and use the same cluster name.

```bash
ATLASOPS_TEARDOWN_ACK=DELETE_ATLASOPS_LOCAL \
  bash infra/local/teardown_local.sh --apply
```

Teardown inspects the target before deletion. It neither deletes another cluster
nor revokes model-provider credentials. Uncertain cleanup remains a failure,
not successful closure.

## Keep acceptance claims bounded

Hosted disposable integration uses authenticated deterministic model replies and
synthetic operator decisions. Its classification is
`NON_EMPIRICAL_SOFTWARE_FIXTURE`, even when its Kubernetes observations are real.
It does not establish a successful real-model incident or G4/G9 PASS.
The first hosted job for the unmerged
[integration PR](https://github.com/virajchoudhary/AtlasOps/pull/247) failed.
Its exact failure, recovery, and cleanup are not accepted.

Physical Windows/WSL2, Mac, and Linux acceptance still needs the selected host,
authorized model identity and deadlines, fresh telemetry, operator decisions,
recovery observations, and cleanup evidence. The exact-source review receipt
records which software and hosted checks passed.
Include the selected Bash and Python versions and generated recovery with and
without an optional model-key file in the host-specific smoke checks.

Preserve the [external actions](EXTERNAL_CLOSURE_ACTIONS_20261027.md), the
[completion checklist](COMPLETION_CHECKLIST.md), and all original scientific
statuses and unsuccessful results.
