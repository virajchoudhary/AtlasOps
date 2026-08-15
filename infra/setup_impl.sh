#!/usr/bin/env bash
# Implementation for infra/setup.sh. Do not invoke this file directly.
set -euo pipefail

readonly DEFAULT_REGION="us-central1"
readonly DEFAULT_CLUSTER="atlasops"
readonly MACHINE_TYPE="e2-standard-4"
readonly INITIAL_NODES="1"
readonly MIN_NODES="1"
readonly MAX_NODES="3"
readonly RESOURCE_LABELS="managed-by=atlasops,environment=development"
readonly COST_ACK_VALUE="I_UNDERSTAND_GCP_COSTS"
readonly COORDINATOR_SECRET="atlasops-coordinator-secrets"
readonly ALERTMANAGER_SECRET="atlasops-alertmanager-webhook"
readonly COORDINATOR_MANIFEST_TEMPLATE="infra/kubernetes/coordinator.yaml.tmpl"
readonly ATLASOPS_PROMETHEUS_RULES="infra/kubernetes/atlasops-prometheus-rules.yaml"
readonly COORDINATOR_ROLLOUT_TIMEOUT="5m"

# v0.10.0 is retained for human release provenance. The manifest URL uses the
# immutable commit so a moved tag cannot change an apply.
readonly BOUTIQUE_RELEASE="v0.10.0"
readonly BOUTIQUE_COMMIT="98e60f5ee0b643cc00bceb71e6efb89617740432"
readonly BOUTIQUE_MANIFEST="https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/${BOUTIQUE_COMMIT}/release/kubernetes-manifests.yaml"
readonly BOUTIQUE_ROLLOUT_TIMEOUT="10m"
# Exact Deployment names reviewed from release/kubernetes-manifests.yaml at
# BOUTIQUE_COMMIT. Keep this list tied to that immutable manifest revision.
readonly BOUTIQUE_DEPLOYMENTS=(
  "currencyservice"
  "loadgenerator"
  "productcatalogservice"
  "checkoutservice"
  "shippingservice"
  "cartservice"
  "redis-cart"
  "emailservice"
  "paymentservice"
  "frontend"
  "recommendationservice"
  "adservice"
)

readonly PROMETHEUS_CHART_VERSION="88.3.0"
readonly JAEGER_CHART_VERSION="4.12.0"
readonly ARGOCD_CHART_VERSION="10.3.2"
readonly CHAOS_MESH_CHART_VERSION="2.8.3"

readonly PUBSUB_TOPICS=("AtlasOps-checkout-events" "AtlasOps-alerts")
readonly PUBSUB_SUBSCRIPTIONS=("AtlasOps-checkout-sub" "AtlasOps-alerts-sub")

ATLASOPS_ENABLE_CLOUD_SQL="${ATLASOPS_ENABLE_CLOUD_SQL:-false}"
ATLASOPS_ENABLE_PUBSUB="${ATLASOPS_ENABLE_PUBSUB:-false}"
ATLASOPS_ENABLE_ARTIFACT_REGISTRY="${ATLASOPS_ENABLE_ARTIFACT_REGISTRY:-false}"
ATLASOPS_ENABLE_CLOUD_BUILD="${ATLASOPS_ENABLE_CLOUD_BUILD:-false}"
ATLASOPS_ENABLE_ARGOCD="${ATLASOPS_ENABLE_ARGOCD:-true}"
ATLASOPS_BACKEND="${ATLASOPS_BACKEND:-vllm}"

usage() {
  cat <<'EOF'
Usage:
  bash infra/setup.sh <PROJECT_ID> [REGION] [CLUSTER_NAME] --check
  bash infra/setup.sh <PROJECT_ID> [REGION] [CLUSTER_NAME] --apply

Modes:
  --check  Run local and read-only GCP preflight. Never mutates resources.
  --apply  Provision only after every gate and cost acknowledgement passes.

Required for check/apply:
  ATLASOPS_GKE_NODE_SERVICE_ACCOUNT=<dedicated account in PROJECT_ID>
  ATLASOPS_GKE_AUTHORIZED_NETWORKS=<comma-separated IPv4 CIDRs>
  ATLASOPS_COORDINATOR_IMAGE=<registry/image>@sha256:<64 lowercase hex>
  ATLASOPS_VLLM_BASE=<explicit http(s) OpenAI-compatible endpoint>
  ATLASOPS_AGENT_MODEL=<explicit model identifier>

Optional:
  ATLASOPS_GKE_ZONE=<zone in REGION>       default: <REGION>-a
  ATLASOPS_ENABLE_CLOUD_SQL=false          deferred; true fails closed
  ATLASOPS_ENABLE_PUBSUB=false             reviewed opt-in lifecycle
  ATLASOPS_ENABLE_ARTIFACT_REGISTRY=false  deferred; true fails closed
  ATLASOPS_ENABLE_CLOUD_BUILD=false        deferred; true fails closed
  ATLASOPS_ENABLE_ARGOCD=true              canonical G3 controller (ClusterIP; no Application ownership)
  ATLASOPS_BACKEND=vllm                    vllm, fireworks, or openai

Apply-only acknowledgement:
  ATLASOPS_COST_ACK=I_UNDERSTAND_GCP_COSTS

The acknowledgement is not a billing budget. Linkerd is unconditionally
deferred and no remote installer is executed. Before apply reaches runtime
wiring, create Secret atlasops-coordinator-secrets in namespace default with
keys atlasops-audit-secret, alertmanager-webhook-secret, and atlasops-api-key.
For fireworks/openai also add llm-api-key. Create Secret
atlasops-alertmanager-webhook in namespace monitoring with key
alertmanager-webhook-secret containing the same webhook credential. Secret
values are never accepted on the command line or printed by this script.
EOF
}

fail() { echo "ERROR: $*" >&2; exit 1; }

parse_bool() {
  local name="$1" value
  value="${!name:-false}"
  case "$value" in
    true|false) printf -v "$name" '%s' "$value" ;;
    *) fail "$name must be exactly 'true' or 'false' (received '$value')." ;;
  esac
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command '$1' is not available."
}

validate_ipv4_cidrs() {
  local value="$1" cidr ip prefix octet
  local -a cidrs octets
  [[ -n "$value" ]] || fail "ATLASOPS_GKE_AUTHORIZED_NETWORKS is required."
  [[ "$value" != *[[:space:]]* ]] || fail "Authorized CIDRs must not contain whitespace."
  IFS=',' read -r -a cidrs <<< "$value"
  ((${#cidrs[@]} > 0)) || fail "At least one authorized CIDR is required."
  for cidr in "${cidrs[@]}"; do
    [[ "$cidr" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/([0-9]|[12][0-9]|3[0-2])$ ]] || fail "Invalid IPv4 CIDR: '$cidr'."
    ip="${cidr%/*}"; prefix="${cidr#*/}"
    IFS='.' read -r -a octets <<< "$ip"
    for octet in "${octets[@]}"; do
      ((10#$octet <= 255)) || fail "Invalid IPv4 CIDR: '$cidr'."
    done
    ((10#$prefix <= 32)) || fail "Invalid IPv4 CIDR: '$cidr'."
    [[ "$cidr" != "0.0.0.0/0" ]] || fail "The unrestricted CIDR 0.0.0.0/0 is forbidden."
  done
}

parse_arguments() {
  MODE=""; POSITIONAL=()
  local arg
  for arg in "$@"; do
    case "$arg" in
      --check|--apply)
        [[ -z "$MODE" ]] || fail "Specify exactly one mode."
        MODE="$arg"
        ;;
      -h|--help) usage; exit 0 ;;
      --*) fail "Unknown option: $arg" ;;
      *) POSITIONAL+=("$arg") ;;
    esac
  done
  [[ -n "$MODE" ]] || { usage >&2; fail "An explicit --check or --apply mode is required."; }
  ((${#POSITIONAL[@]} >= 1 && ${#POSITIONAL[@]} <= 3)) || { usage >&2; fail "Expected PROJECT_ID and at most REGION and CLUSTER_NAME."; }
  PROJECT="${POSITIONAL[0]}"
  REGION="${POSITIONAL[1]:-$DEFAULT_REGION}"
  CLUSTER="${POSITIONAL[2]:-$DEFAULT_CLUSTER}"
  ZONE="${ATLASOPS_GKE_ZONE:-${REGION}-a}"
}

validate_static_inputs() {
  ((BASH_VERSINFO[0] >= 4)) || fail "Bash 4 or newer is required."
  require_command gcloud; require_command kubectl; require_command helm
  require_command grep; require_command mktemp; require_command sed; require_command sort; require_command tr
  [[ "$PROJECT" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]] || fail "Invalid GCP project ID: '$PROJECT'."
  [[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] || fail "Invalid GCP region: '$REGION'."
  [[ "$ZONE" =~ ^${REGION}-[a-z]$ ]] || fail "Zone '$ZONE' is not in region '$REGION'."
  [[ "$CLUSTER" =~ ^[a-z]([a-z0-9-]{0,38}[a-z0-9])?$ ]] || fail "Invalid GKE cluster name: '$CLUSTER'."

  NODE_SERVICE_ACCOUNT="${ATLASOPS_GKE_NODE_SERVICE_ACCOUNT:-}"
  AUTHORIZED_NETWORKS="${ATLASOPS_GKE_AUTHORIZED_NETWORKS:-}"
  COORDINATOR_IMAGE="${ATLASOPS_COORDINATOR_IMAGE:-}"
  VLLM_BASE="${ATLASOPS_VLLM_BASE:-}"
  AGENT_MODEL="${ATLASOPS_AGENT_MODEL:-}"
  [[ "$NODE_SERVICE_ACCOUNT" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]@${PROJECT}\.iam\.gserviceaccount\.com$ ]] || \
    fail "ATLASOPS_GKE_NODE_SERVICE_ACCOUNT must be a dedicated account in '$PROJECT'."
  validate_ipv4_cidrs "$AUTHORIZED_NETWORKS"
  [[ "$COORDINATOR_IMAGE" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(:[0-9]+)?(/[a-z0-9]+([._-][a-z0-9]+)*)+@sha256:[a-f0-9]{64}$ ]] || \
    fail "ATLASOPS_COORDINATOR_IMAGE must be an explicit lowercase registry image pinned by sha256 digest."
  [[ "$VLLM_BASE" =~ ^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?(/[^[:space:]\|\&]*)?$ ]] || \
    fail "ATLASOPS_VLLM_BASE must be an explicit http(s) endpoint without whitespace or shell metacharacters."
  [[ "$AGENT_MODEL" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$ ]] || \
    fail "ATLASOPS_AGENT_MODEL contains unsupported characters."
  case "$ATLASOPS_BACKEND" in
    vllm|fireworks|openai) ;;
    *) fail "ATLASOPS_BACKEND must be vllm, fireworks, or openai." ;;
  esac
  [[ -f "$COORDINATOR_MANIFEST_TEMPLATE" ]] || fail "Coordinator manifest template is missing."
  [[ -f "$ATLASOPS_PROMETHEUS_RULES" ]] || fail "AtlasOps Prometheus rules are missing."

  parse_bool ATLASOPS_ENABLE_CLOUD_SQL
  parse_bool ATLASOPS_ENABLE_PUBSUB
  parse_bool ATLASOPS_ENABLE_ARTIFACT_REGISTRY
  parse_bool ATLASOPS_ENABLE_CLOUD_BUILD
  parse_bool ATLASOPS_ENABLE_ARGOCD
  [[ "$ATLASOPS_ENABLE_CLOUD_SQL" == false ]] || fail "Cloud SQL is DEFERRED in Stage 1D-A."
  [[ "$ATLASOPS_ENABLE_ARTIFACT_REGISTRY" == false ]] || fail "Artifact Registry is DEFERRED in Stage 1D-A."
  [[ "$ATLASOPS_ENABLE_CLOUD_BUILD" == false ]] || fail "Cloud Build is DEFERRED in Stage 1D-A."
  if [[ "$MODE" == "--apply" ]]; then
    [[ "${ATLASOPS_COST_ACK:-}" == "$COST_ACK_VALUE" ]] || \
      fail "--apply requires ATLASOPS_COST_ACK=$COST_ACK_VALUE."
  fi
}

validate_location_and_quota() {
  gcloud compute regions describe "$REGION" --project="$PROJECT" --format='value(name)' >/dev/null || return 1
  gcloud compute zones describe "$ZONE" --project="$PROJECT" --format='value(name,region)' >/dev/null || return 1
  gcloud compute project-info describe --project="$PROJECT" --format='value(name)' >/dev/null || return 1
}

read_only_preflight() {
  local account project_id billing_enabled
  account="$(gcloud auth list --filter='status:ACTIVE' --limit=1 --format='value(account)')"
  [[ -n "$account" ]] || fail "No active gcloud account is available."
  echo "PREFLIGHT: active account found (identity value not printed)."
  project_id="$(gcloud projects describe "$PROJECT" --project="$PROJECT" --format='value(projectId)')" || fail "Project is inaccessible."
  [[ "$project_id" == "$PROJECT" ]] || fail "Project verification returned '$project_id'."
  billing_enabled="$(gcloud billing projects describe "$PROJECT" --project="$PROJECT" --format='value(billingEnabled)')" || fail "Billing status is unverifiable."
  [[ "${billing_enabled,,}" == true ]] || fail "Billing is not enabled for '$PROJECT'."
  gcloud iam service-accounts describe "$NODE_SERVICE_ACCOUNT" --project="$PROJECT" --format='value(email)' >/dev/null || fail "Node service account is absent or inaccessible."
  ENABLED_SERVICES="$(gcloud services list --enabled --project="$PROJECT" --format='value(config.name)')" || fail "Enabled APIs are uninspectable."
  if validate_location_and_quota; then
    echo "PREFLIGHT: project, billing, location, node identity, and quota metadata are inspectable."
  elif [[ "$MODE" == --check ]]; then
    fail "Location/quota metadata is uninspectable; no mutations occurred."
  else
    echo "PREFLIGHT: location/quota metadata is not yet inspectable; selected APIs will be enabled, then this check repeats before cluster mutation."
  fi
  if grep -Fxq container.googleapis.com <<< "$ENABLED_SERVICES"; then
    inspect_existing_cluster
  elif [[ "$MODE" == --check ]]; then
    fail "GKE API is disabled, so existing-cluster compatibility cannot be verified; no mutations occurred."
  else
    CLUSTER_STATE="UNVERIFIED (GKE API disabled; apply will enable and recheck)"
    echo "PREFLIGHT: $CLUSTER_STATE."
  fi
  echo "PREFLIGHT LIMIT: these checks do not prove every IAM permission or sufficient quota."
}

print_summary() {
  cat <<EOF

=== AtlasOps Stage 1D-A selected contract ===
Mode:                              $MODE
Project:                           $PROJECT
Region:                            $REGION
Zone:                              $ZONE
Cluster:                           $CLUSTER
GKE mode:                          Standard (zonal development/research)
Machine type:                      $MACHINE_TYPE
Initial node count:                $INITIAL_NODES
Autoscaling:                       enabled ($MIN_NODES-$MAX_NODES, one zone)
Node service account:              $NODE_SERVICE_ACCOUNT
Control-plane authorized networks: explicit; 0.0.0.0/0 rejected
Cloud SQL:                         SKIPPED / DEFERRED
Pub/Sub:                           $([[ "$ATLASOPS_ENABLE_PUBSUB" == true ]] && echo ENABLED || echo SKIPPED)
Coordinator image:                 operator-supplied immutable digest
Coordinator Service:               atlasops-coordinator-svc.default:9099 (ClusterIP)
Coordinator secrets:               pre-existing namespaced Secret contracts
Prometheus/Alertmanager:            coordinator route statically configured
Application metrics/traces:         DEFERRED (not present in pinned Boutique manifest)
Argo CD:                            $([[ "$ATLASOPS_ENABLE_ARGOCD" == true ]] && echo "ENABLED (ClusterIP; chart $ARGOCD_CHART_VERSION; required credentials enforced)" || echo "SKIPPED / DEFERRED (DEVIATION: canonical Gate G3 cannot PASS without Argo CD)")
Linkerd:                           SKIPPED / DEFERRED
Artifact Registry:                 SKIPPED / DEFERRED
Cloud Build:                       SKIPPED / DEFERRED
Persistent-storage requests:       Prometheus 20Gi
Project-managed public admin UIs:  0 LoadBalancer Services
Online Boutique exposure:          pinned manifest has 1 frontend LoadBalancer
Jaeger:                            in-cluster Query backend (ClusterIP; chart $JAEGER_CHART_VERSION)
Existing target cluster:           $CLUSTER_STATE
===============================================
EOF
}

normalize_cidrs() { tr ',' '\n' <<< "$1" | sort | tr '\n' ',' | sed 's/,$//'; }

validate_existing_cluster() {
  local row location autopilot workload_pool man_enabled
  local pools pool_count pool_name machine service_account autoscaling min_nodes max_nodes
  local actual_cidrs desired_cidrs labels
  row="$(gcloud container clusters describe "$CLUSTER" --zone="$ZONE" --project="$PROJECT" \
    --format='csv[no-heading](location,autopilot.enabled,workloadIdentityConfig.workloadPool,masterAuthorizedNetworksConfig.enabled)')"
  IFS=',' read -r location autopilot workload_pool man_enabled <<< "$row"
  [[ "$location" == "$ZONE" ]] || fail "Existing cluster location is '$location'."
  [[ -z "$autopilot" || "${autopilot,,}" == false ]] || fail "Existing cluster is Autopilot."
  [[ "$workload_pool" == "${PROJECT}.svc.id.goog" ]] || fail "Existing Workload Identity pool is incompatible."
  [[ "${man_enabled,,}" == true ]] || fail "Existing cluster lacks master authorized networks."
  labels="$(gcloud container clusters describe "$CLUSTER" --zone="$ZONE" --project="$PROJECT" --format='value(resourceLabels)')"
  owned_labels "$labels" || fail "Existing cluster is not labeled as AtlasOps development-owned."
  pools="$(gcloud container clusters describe "$CLUSTER" --zone="$ZONE" --project="$PROJECT" --flatten='nodePools[]' \
    --format='csv[no-heading](nodePools.name,nodePools.config.machineType,nodePools.config.serviceAccount,nodePools.autoscaling.enabled,nodePools.autoscaling.minNodeCount,nodePools.autoscaling.maxNodeCount)')"
  pool_count="$(grep -cve '^$' <<< "$pools")"
  [[ "$pool_count" == 1 ]] || fail "Existing cluster has $pool_count node pools; exactly one is required."
  IFS=',' read -r pool_name machine service_account autoscaling min_nodes max_nodes <<< "$pools"
  [[ -n "$pool_name" && "$machine" == "$MACHINE_TYPE" ]] || fail "Existing machine topology is incompatible."
  [[ "$service_account" == "$NODE_SERVICE_ACCOUNT" ]] || fail "Existing node identity is incompatible."
  [[ "${autoscaling,,}" == true && "$min_nodes" == "$MIN_NODES" && "$max_nodes" == "$MAX_NODES" ]] || fail "Existing autoscaling is incompatible."
  actual_cidrs="$(gcloud container clusters describe "$CLUSTER" --zone="$ZONE" --project="$PROJECT" \
    --flatten='masterAuthorizedNetworksConfig.cidrBlocks[]' --format='value(masterAuthorizedNetworksConfig.cidrBlocks.cidrBlock)' | sort | tr '\n' ',' | sed 's/,$//')"
  desired_cidrs="$(normalize_cidrs "$AUTHORIZED_NETWORKS")"
  [[ "$actual_cidrs" == "$desired_cidrs" ]] || fail "Existing authorized-network CIDRs are incompatible."
  echo "CLUSTER: existing cluster matches the reviewed static contract."
}

inspect_existing_cluster() {
  local rows name location found=false
  rows="$(gcloud container clusters list --project="$PROJECT" --filter="name=$CLUSTER" --format='csv[no-heading](name,location)')" || fail "Clusters are uninspectable."
  while IFS=',' read -r name location; do
    [[ -n "$name" ]] || continue
    [[ "$name" == "$CLUSTER" ]] || continue
    [[ "$location" == "$ZONE" ]] || fail "Cluster '$CLUSTER' exists in '$location', not '$ZONE'."
    found=true
  done <<< "$rows"
  if [[ "$found" == true ]]; then
    validate_existing_cluster
    CLUSTER_STATE="PRESENT / COMPATIBLE"
  else
    CLUSTER_STATE="ABSENT"
  fi
}

ensure_cluster() {
  inspect_existing_cluster
  if [[ "$CLUSTER_STATE" == "PRESENT / COMPATIBLE" ]]; then
    echo "CLUSTER: compatible cluster reused without mutation."
    return
  fi
  gcloud container clusters create "$CLUSTER" \
    --zone="$ZONE" --project="$PROJECT" --machine-type="$MACHINE_TYPE" \
    --num-nodes="$INITIAL_NODES" --enable-autoscaling --min-nodes="$MIN_NODES" --max-nodes="$MAX_NODES" \
    --release-channel=stable --workload-pool="${PROJECT}.svc.id.goog" --service-account="$NODE_SERVICE_ACCOUNT" \
    --enable-master-authorized-networks --master-authorized-networks="$AUTHORIZED_NETWORKS" --labels="$RESOURCE_LABELS"
  echo "CLUSTER: created Standard zonal cluster (1 initial node, autoscaling 1-3)."
}

owned_labels() { [[ "$1" == *"managed-by=atlasops"* && "$1" == *"environment=development"* ]]; }

ensure_topic() {
  local topic="$1" names labels
  names="$(gcloud pubsub topics list --project="$PROJECT" --filter="name:projects/$PROJECT/topics/$topic" --format='value(name)')" || fail "Topics are uninspectable."
  if grep -Fxq "projects/$PROJECT/topics/$topic" <<< "$names"; then
    labels="$(gcloud pubsub topics describe "$topic" --project="$PROJECT" --format='value(labels)')"
    owned_labels "$labels" || fail "Existing topic '$topic' is not AtlasOps-owned."
    echo "PUBSUB: topic '$topic' already exists."
  else
    gcloud pubsub topics create "$topic" --project="$PROJECT" --labels="$RESOURCE_LABELS"
    echo "PUBSUB: topic '$topic' created."
  fi
}

ensure_subscription() {
  local subscription="$1" topic="$2" names labels actual_topic
  names="$(gcloud pubsub subscriptions list --project="$PROJECT" --filter="name:projects/$PROJECT/subscriptions/$subscription" --format='value(name)')" || fail "Subscriptions are uninspectable."
  if grep -Fxq "projects/$PROJECT/subscriptions/$subscription" <<< "$names"; then
    labels="$(gcloud pubsub subscriptions describe "$subscription" --project="$PROJECT" --format='value(labels)')"
    actual_topic="$(gcloud pubsub subscriptions describe "$subscription" --project="$PROJECT" --format='value(topic)')"
    owned_labels "$labels" || fail "Existing subscription '$subscription' is not AtlasOps-owned."
    [[ "$actual_topic" == "projects/$PROJECT/topics/$topic" ]] || fail "Subscription '$subscription' has the wrong topic."
    echo "PUBSUB: subscription '$subscription' already exists with the expected topic."
  else
    gcloud pubsub subscriptions create "$subscription" --topic="$topic" --project="$PROJECT" --ack-deadline=60 --labels="$RESOURCE_LABELS"
    echo "PUBSUB: subscription '$subscription' created."
  fi
}

provision_pubsub() {
  ensure_topic "${PUBSUB_TOPICS[0]}"; ensure_topic "${PUBSUB_TOPICS[1]}"
  ensure_subscription "${PUBSUB_SUBSCRIPTIONS[0]}" "${PUBSUB_TOPICS[0]}"
  ensure_subscription "${PUBSUB_SUBSCRIPTIONS[1]}" "${PUBSUB_TOPICS[1]}"
  echo "PUBSUB: provisioned opt-in resources; application consumption remains unwired."
}

RUNTIME_KUBECONFIG=""
RENDERED_COORDINATOR_MANIFEST=""

cleanup_runtime_files() {
  [[ -z "$RENDERED_COORDINATOR_MANIFEST" || ! -f "$RENDERED_COORDINATOR_MANIFEST" ]] || \
    rm -f -- "$RENDERED_COORDINATOR_MANIFEST"
  [[ -z "$RUNTIME_KUBECONFIG" || ! -f "$RUNTIME_KUBECONFIG" ]] || rm -f -- "$RUNTIME_KUBECONFIG"
}

initialize_cluster_access() {
  RUNTIME_KUBECONFIG="$(mktemp)"
  chmod 600 "$RUNTIME_KUBECONFIG"
  export KUBECONFIG="$RUNTIME_KUBECONFIG"
  KUBE_CONTEXT="gke_${PROJECT}_${ZONE}_${CLUSTER}"
  trap cleanup_runtime_files EXIT
  gcloud container clusters get-credentials "$CLUSTER" --zone="$ZONE" --project="$PROJECT"
  kubectl --context="$KUBE_CONTEXT" cluster-info >/dev/null
  echo "KUBERNETES: isolated temporary kubeconfig initialized for the exact target context."
}

kubectl_target() { kubectl --context="$KUBE_CONTEXT" "$@"; }

helm_target() { helm --kube-context "$KUBE_CONTEXT" "$@"; }

secret_key_present() {
  local namespace="$1" secret="$2" key="$3"
  kubectl_target get secret "$secret" --namespace="$namespace" \
    -o "go-template={{if index .data \"$key\"}}present{{end}}" | grep -Fxq present
}

validate_runtime_secret_contract() {
  local key
  for key in atlasops-audit-secret alertmanager-webhook-secret atlasops-api-key; do
    secret_key_present default "$COORDINATOR_SECRET" "$key" || \
      fail "Secret '$COORDINATOR_SECRET' in namespace default is missing required key '$key'."
  done
  if [[ "$ATLASOPS_BACKEND" != vllm ]]; then
    secret_key_present default "$COORDINATOR_SECRET" llm-api-key || \
      fail "Secret '$COORDINATOR_SECRET' requires llm-api-key for backend '$ATLASOPS_BACKEND'."
  fi
  if [[ "$ATLASOPS_ENABLE_ARGOCD" == true ]]; then
    for key in argocd-user argocd-pass; do
      secret_key_present default "$COORDINATOR_SECRET" "$key" || \
        fail "Secret '$COORDINATOR_SECRET' in namespace default is missing required Argo CD credential key '$key'."
    done
  fi
  secret_key_present monitoring "$ALERTMANAGER_SECRET" alertmanager-webhook-secret || \
    fail "Secret '$ALERTMANAGER_SECRET' in namespace monitoring is missing alertmanager-webhook-secret."
  echo "SECRETS: required key presence validated without printing or storing value contents."
}

render_coordinator_manifest() {
  RENDERED_COORDINATOR_MANIFEST="$(mktemp)"
  sed \
    -e "s|__ATLASOPS_COORDINATOR_IMAGE__|$COORDINATOR_IMAGE|g" \
    -e "s|__ATLASOPS_BACKEND__|$ATLASOPS_BACKEND|g" \
    -e "s|__ATLASOPS_VLLM_BASE__|$VLLM_BASE|g" \
    -e "s|__ATLASOPS_AGENT_MODEL__|$AGENT_MODEL|g" \
    -e "s|__ATLASOPS_GCP_PROJECT__|$PROJECT|g" \
    "$COORDINATOR_MANIFEST_TEMPLATE" > "$RENDERED_COORDINATOR_MANIFEST"
  ! grep -q '__ATLASOPS_' "$RENDERED_COORDINATOR_MANIFEST" || \
    fail "Coordinator manifest rendering left unresolved placeholders."
}

apply_foundation() {
  local -a services=(compute.googleapis.com container.googleapis.com monitoring.googleapis.com logging.googleapis.com)
  if [[ "$ATLASOPS_ENABLE_PUBSUB" == true ]]; then
    services+=(pubsub.googleapis.com)
  fi
  gcloud services enable "${services[@]}" --project="$PROJECT"
  echo "APIS: selected APIs enabled; deferred-component APIs remain disabled."
  validate_location_and_quota || fail "Location/quota metadata remained unverifiable before cluster mutation."
  ensure_cluster
  initialize_cluster_access
  validate_runtime_secret_contract
  kubectl_target apply -f "$BOUTIQUE_MANIFEST"
  local deployment
  for deployment in "${BOUTIQUE_DEPLOYMENTS[@]}"; do
    echo "ONLINE BOUTIQUE: waiting for deployment/$deployment to become Available."
    kubectl_target rollout status "deployment/$deployment" --namespace=default --timeout="$BOUTIQUE_ROLLOUT_TIMEOUT"
  done
  echo "ONLINE BOUTIQUE: all ${#BOUTIQUE_DEPLOYMENTS[@]} required Deployments are Available."
  echo "ONLINE BOUTIQUE: $BOUTIQUE_RELEASE at immutable commit $BOUTIQUE_COMMIT applied and ready."

  render_coordinator_manifest
  kubectl_target apply -f "$RENDERED_COORDINATOR_MANIFEST"
  kubectl_target rollout status deployment/atlasops-coordinator --namespace=default --timeout="$COORDINATOR_ROLLOUT_TIMEOUT"
  echo "COORDINATOR: private Service and authenticated runtime deployed from immutable image digest."

  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
  helm repo add jaegertracing https://jaegertracing.github.io/helm-charts --force-update
  if [[ "$ATLASOPS_ENABLE_ARGOCD" == true ]]; then
    helm repo add argo https://argoproj.github.io/argo-helm --force-update
  fi
  helm repo add chaos-mesh https://charts.chaos-mesh.org --force-update
  helm repo update
  helm_target upgrade --install prometheus prometheus-community/kube-prometheus-stack --version "$PROMETHEUS_CHART_VERSION" \
    --namespace monitoring --values infra/values/kube-prometheus-stack.yaml --wait --timeout=10m
  kubectl_target apply -f "$ATLASOPS_PROMETHEUS_RULES"
  echo "PROMETHEUS: kube-state-metrics availability alert and authenticated coordinator route configured."
  echo "PROMETHEUS: application error-rate and latency signals remain DEFERRED / UNPROVEN."

  kubectl_target create namespace jaeger --dry-run=client -o yaml | kubectl_target apply -f -
  helm_target upgrade --install jaeger jaegertracing/jaeger --version "$JAEGER_CHART_VERSION" \
    --namespace jaeger --values infra/values/jaeger.yaml --wait --timeout=10m
  echo "JAEGER: in-cluster Query backend and collector installed at pinned chart $JAEGER_CHART_VERSION."
  echo "JAEGER: Online Boutique trace ingestion remains LIVE UNVERIFIED (instrumentation follow-up required)."

  if [[ "$ATLASOPS_ENABLE_ARGOCD" == true ]]; then
    kubectl_target create namespace argocd --dry-run=client -o yaml | kubectl_target apply -f -
    helm_target upgrade --install argocd argo/argo-cd --version "$ARGOCD_CHART_VERSION" \
      --namespace argocd --values infra/values/argocd.yaml --wait --timeout=10m
    echo "ARGO CD: canonical base controller installed; no Application or workload ownership configured."
  else
    echo "ARGO CD: SKIPPED / DEFERRED (operator deviation; canonical Gate G3 cannot PASS with Argo CD disabled)."
  fi
  kubectl_target create namespace chaos-mesh --dry-run=client -o yaml | kubectl_target apply -f -
  helm_target upgrade --install chaos-mesh chaos-mesh/chaos-mesh --version "$CHAOS_MESH_CHART_VERSION" \
    --namespace chaos-mesh --values infra/values/chaos-mesh.yaml --set chaosDaemon.runtime=containerd --wait --timeout=10m
  echo "CHAOS MESH: base controller installed; no experiment executed."
  [[ "$ATLASOPS_ENABLE_PUBSUB" == false ]] && echo "PUBSUB: SKIPPED." || provision_pubsub
  echo "CLOUD SQL: SKIPPED / DEFERRED."
  echo "LINKERD: SKIPPED / DEFERRED; no remote installer executed."
  echo "ARTIFACT REGISTRY: SKIPPED / DEFERRED."
  echo "CLOUD BUILD: SKIPPED / DEFERRED."
}

main() {
  parse_arguments "$@"; validate_static_inputs; read_only_preflight; print_summary
  if [[ "$MODE" == --check ]]; then
    echo "CHECK COMPLETE: preflight passed; no resources were mutated."
    return
  fi
  echo "APPLY: explicit mode and cost acknowledgement accepted."
  apply_foundation
  cat <<'EOF'

=== CORE RUNTIME WIRED — LIVE VALIDATION STILL REQUIRED ===
The coordinator, kube-state-metrics availability alert, and authenticated
Alertmanager route are configured. This output does not prove alert delivery,
model/tool execution, application metrics, tracing, or full AtlasOps readiness.

Safe operator access:
  kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80
  kubectl port-forward -n jaeger svc/jaeger 16686:16686
  # when ATLASOPS_ENABLE_ARGOCD=true:
  kubectl port-forward -n argocd svc/argocd-server 8080:443

Jaeger query API is reachable in-cluster (http://jaeger.jaeger.svc.cluster.local:16686);
application trace ingestion remains unproven until workload instrumentation is added.
Argo CD provides the API backend without claiming intrusive Application ownership.
Online Boutique separately creates a public frontend LoadBalancer; AtlasOps admin/runtime
Services remain ClusterIP.
EOF
}

main "$@"
