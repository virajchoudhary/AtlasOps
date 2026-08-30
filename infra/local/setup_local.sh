#!/usr/bin/env bash
# ==============================================================================
# AtlasOps Local Kind Environment Setup
# Pipeline v1.1 Free-First — zero external-service cost
#
# Creates a reproducible local Kubernetes environment using Kind with:
#   - Online Boutique (v0.10.0 at frozen commit)
#   - Prometheus + Alertmanager + kube-state-metrics
#   - Jaeger (all-in-one, in-memory)
#   - Argo CD (dedicated atlasops account)
#   - Chaos Mesh (controllers + CRDs only)
#   - AtlasOps Coordinator
#
# Requirements: docker, kind, kubectl, helm, python3/python
# Zero gcloud dependency. Zero billing requirement.
#
# Usage:
#   bash infra/local/setup_local.sh --check
#   bash infra/local/setup_local.sh --apply
# ==============================================================================
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

readonly KIND_CLUSTER_NAME="atlasops-local"
readonly KUBE_CONTEXT="kind-${KIND_CLUSTER_NAME}"
readonly KIND_CONFIG="${SCRIPT_DIR}/kind-config.yaml"

readonly COORDINATOR_IMAGE="atlasops-coordinator:g3-local"
readonly COORDINATOR_MANIFEST="${SCRIPT_DIR}/coordinator-local.yaml"
readonly COORDINATOR_ROLLOUT_TIMEOUT="5m"

readonly BOUTIQUE_RELEASE="v0.10.0"
readonly BOUTIQUE_COMMIT="98e60f5ee0b643cc00bceb71e6efb89617740432"
readonly BOUTIQUE_MANIFEST="https://raw.githubusercontent.com/GoogleCloudPlatform/microservices-demo/${BOUTIQUE_COMMIT}/release/kubernetes-manifests.yaml"
readonly BOUTIQUE_ROLLOUT_TIMEOUT="10m"
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

readonly ATLASOPS_SECRET_DIR="${ATLASOPS_SECRET_DIR:-${REPO_ROOT}/secrets}"
readonly ATLASOPS_PROMETHEUS_RULES="${REPO_ROOT}/infra/kubernetes/atlasops-prometheus-rules.yaml"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  readonly PYTHON_BIN
elif command -v python3 >/dev/null 2>&1; then
  readonly PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  readonly PYTHON_BIN="python"
else
  readonly PYTHON_BIN="python3"
fi

# ==============================================================================
# Utility functions
# ==============================================================================
fail() { echo "ERROR: $*" >&2; exit 1; }

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command '$1' is not available."
}

kubectl_local() { kubectl --context="$KUBE_CONTEXT" "$@"; }

helm_local() { helm --kube-context "$KUBE_CONTEXT" "$@"; }

usage() {
  cat <<'EOF'
Usage:
  bash infra/local/setup_local.sh --check
  bash infra/local/setup_local.sh --apply

Modes:
  --check  Validate local prerequisites without creating resources.
  --apply  Create Kind cluster and deploy all required services.

Environment:
  ATLASOPS_SECRET_DIR=secrets  directory containing local secret material
                               (default: secrets/ in repo root)

Requirements:
  docker, kind, kubectl, helm, python3/python
  Docker daemon must be running.

Zero gcloud dependency. Zero external-service cost.
EOF
}

# ==============================================================================
# Argument parsing
# ==============================================================================
MODE=""
parse_arguments() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --check|--apply)
        [[ -z "$MODE" ]] || fail "Specify exactly one mode."
        MODE="$arg"
        ;;
      -h|--help) usage; exit 0 ;;
      --*) fail "Unknown option: $arg" ;;
      *) fail "Unexpected argument: $arg" ;;
    esac
  done
  [[ -n "$MODE" ]] || { usage >&2; fail "An explicit --check or --apply mode is required."; }
}

# ==============================================================================
# Preflight validation
# ==============================================================================
validate_prerequisites() {
  require_command docker
  require_command kind
  require_command kubectl
  require_command helm
  require_command "$PYTHON_BIN"

  # Verify a container daemon is reachable. Docker Desktop is one option; a
  # headless daemon such as Colima works identically and needs no GUI.
  docker info >/dev/null 2>&1 || fail "No reachable Docker daemon. Start Docker Desktop, or run: colima start --cpu 6 --memory 9 --disk 60"

  # Verify PYTHON_BIN can actually do the Argo CD credential derivation. Without
  # this, provisioning dies ~10 minutes in on 'No module named bcrypt', after
  # the cluster and half the stack already exist.
  "$PYTHON_BIN" -c "import bcrypt" >/dev/null 2>&1 || fail "\
'$PYTHON_BIN' cannot import bcrypt, which Argo CD credential derivation requires.
Point PYTHON_BIN at an interpreter that has it, for example:
  PYTHON_BIN=\"\$PWD/.venv/bin/python\" bash infra/local/setup_local.sh --apply"

  # Verify required files exist
  [[ -f "$KIND_CONFIG" ]] || fail "Kind config missing: $KIND_CONFIG"
  [[ -f "$COORDINATOR_MANIFEST" ]] || fail "Coordinator manifest missing: $COORDINATOR_MANIFEST"
  [[ -f "$ATLASOPS_PROMETHEUS_RULES" ]] || fail "Prometheus rules missing: $ATLASOPS_PROMETHEUS_RULES"

  echo "PREFLIGHT: all required commands and files verified."
  echo "PREFLIGHT: Docker daemon is healthy."
  echo "PREFLIGHT: zero gcloud dependency confirmed."
}

validate_local_secrets() {
  local secret_dir="$ATLASOPS_SECRET_DIR"
  [[ -d "$secret_dir" ]] || fail "Local secret directory '$secret_dir' not found. Run: $PYTHON_BIN scripts/generate_runtime_secrets.py"

  local -a req_files=("atlasops-audit-secret.secret" "alertmanager-webhook-secret.secret" "atlasops-api-key.secret" "argocd-user.secret" "argocd-pass.secret")
  local f full_path content
  for f in "${req_files[@]}"; do
    full_path="$secret_dir/$f"
    [[ -f "$full_path" ]] || fail "Missing required secret file: '$full_path'."
    [[ -s "$full_path" ]] || fail "Empty secret file: '$full_path'."
    content="$(<"$full_path")"
    if [[ "$content" =~ \<ARGOCD_ || "$content" =~ \<YOUR_ || "$content" =~ \<REPLACE_ ]]; then
      fail "Secret file '$full_path' contains unresolved placeholder text."
    fi
  done

  local argo_user
  argo_user="$(tr -d '[:space:]' < "$secret_dir/argocd-user.secret")"
  [[ "$argo_user" == "atlasops" ]] || fail "argocd-user.secret must be 'atlasops' (found: '$argo_user')."

  echo "LOCAL SECRETS: verified required local secret files in '$secret_dir'."
}

# ==============================================================================
# Kind cluster management
# ==============================================================================
ensure_kind_cluster() {
  if kind get clusters 2>/dev/null | grep -Fxq "$KIND_CLUSTER_NAME"; then
    echo "KIND: cluster '$KIND_CLUSTER_NAME' already exists. Reusing."
    # Verify kubectl can reach the cluster
    kubectl_local cluster-info >/dev/null 2>&1 || fail "Kind cluster exists but is unreachable."
  else
    echo "KIND: creating cluster '$KIND_CLUSTER_NAME' from $KIND_CONFIG..."
    kind create cluster --name "$KIND_CLUSTER_NAME" --config "$KIND_CONFIG" --wait 120s
    echo "KIND: cluster '$KIND_CLUSTER_NAME' created successfully."
  fi
  echo "KIND: context is '$KUBE_CONTEXT'."
  kubectl_local cluster-info
}

# ==============================================================================
# Namespace management
# ==============================================================================
ensure_namespaces() {
  local ns
  local -a namespaces=("default" "monitoring" "jaeger" "argocd" "chaos-mesh")
  for ns in "${namespaces[@]}"; do
    kubectl_local create namespace "$ns" --dry-run=client -o yaml | kubectl_local apply -f -
  done
  echo "NAMESPACES: required namespaces verified."
}

# ==============================================================================
# Secret management
# ==============================================================================
apply_runtime_secrets() {
  local secret_dir="$ATLASOPS_SECRET_DIR"

  kubectl_local create secret generic atlasops-coordinator-secrets \
    --namespace=default \
    --from-file="atlasops-audit-secret=$secret_dir/atlasops-audit-secret.secret" \
    --from-file="alertmanager-webhook-secret=$secret_dir/alertmanager-webhook-secret.secret" \
    --from-file="atlasops-api-key=$secret_dir/atlasops-api-key.secret" \
    --from-file="argocd-user=$secret_dir/argocd-user.secret" \
    --from-file="argocd-pass=$secret_dir/argocd-pass.secret" \
    --dry-run=client -o yaml | kubectl_local apply -f -

  # Optional LLM API key
  if [[ -f "$secret_dir/llm-api-key.secret" ]] && [[ -s "$secret_dir/llm-api-key.secret" ]]; then
    kubectl_local create secret generic atlasops-coordinator-secrets \
      --namespace=default \
      --from-file="atlasops-audit-secret=$secret_dir/atlasops-audit-secret.secret" \
      --from-file="alertmanager-webhook-secret=$secret_dir/alertmanager-webhook-secret.secret" \
      --from-file="atlasops-api-key=$secret_dir/atlasops-api-key.secret" \
      --from-file="argocd-user=$secret_dir/argocd-user.secret" \
      --from-file="argocd-pass=$secret_dir/argocd-pass.secret" \
      --from-file="llm-api-key=$secret_dir/llm-api-key.secret" \
      --dry-run=client -o yaml | kubectl_local apply -f -
  fi

  kubectl_local create secret generic atlasops-alertmanager-webhook \
    --namespace=monitoring \
    --from-file="alertmanager-webhook-secret=$secret_dir/alertmanager-webhook-secret.secret" \
    --dry-run=client -o yaml | kubectl_local apply -f -

  echo "SECRETS: applied runtime secrets from local material to context '$KUBE_CONTEXT'."
}

# ==============================================================================
# Online Boutique deployment
# ==============================================================================
deploy_online_boutique() {
  echo "ONLINE BOUTIQUE: deploying $BOUTIQUE_RELEASE at commit $BOUTIQUE_COMMIT..."
  kubectl_local apply -f "$BOUTIQUE_MANIFEST"

  apply_arm64_boutique_deviation

  local deployment
  for deployment in "${BOUTIQUE_DEPLOYMENTS[@]}"; do
    echo "ONLINE BOUTIQUE: waiting for deployment/$deployment..."
    kubectl_local rollout status "deployment/$deployment" --namespace=default --timeout="$BOUTIQUE_ROLLOUT_TIMEOUT"
  done
  echo "ONLINE BOUTIQUE: all ${#BOUTIQUE_DEPLOYMENTS[@]} deployments are Available."
}

# The pinned Boutique manifest is tuned for amd64 GKE. On arm64 cartservice (.NET)
# is OOMKilled at its 128Mi ceiling, then restarted by a 1s gRPC probe it cannot
# answer under contention. Applied after the manifest, because `kubectl apply`
# reverts it on every run. Documented in docs/project/LOCAL_ARM64_DEVIATIONS.md.
apply_arm64_boutique_deviation() {
  local node_arch
  node_arch="$(kubectl_local get nodes -o jsonpath='{.items[0].status.nodeInfo.architecture}' 2>/dev/null || echo unknown)"
  if [[ "$node_arch" != "arm64" ]]; then
    echo "ONLINE BOUTIQUE: node architecture '$node_arch' — no deviation needed."
    return 0
  fi

  # cartservice (.NET) and currencyservice (Node.js) exceed the manifest's 128Mi
  # ceiling on arm64. The other 128Mi services (Go, Python, and paymentservice,
  # also Node) stay within it, so only these two are raised.
  echo "ONLINE BOUTIQUE: DEVIATION (arm64) — raising cartservice and currencyservice limits."
  kubectl_local set resources deployment/cartservice --namespace=default \
    --limits=cpu=600m,memory=384Mi --requests=cpu=200m,memory=128Mi >/dev/null
  kubectl_local set resources deployment/currencyservice --namespace=default \
    --limits=cpu=400m,memory=384Mi --requests=cpu=100m,memory=128Mi >/dev/null

  # The pinned probes allow one second, which .NET and Node startup cannot meet
  # on a contended arm64 node; the container then exits 0 on the liveness
  # SIGTERM, which reads as a clean exit rather than a probe failure.
  local svc
  for svc in cartservice currencyservice; do
    kubectl_local patch deployment "$svc" --namespace=default --type=json -p='[
      {"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/timeoutSeconds","value":5},
      {"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/initialDelaySeconds","value":30},
      {"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/timeoutSeconds","value":5},
      {"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/initialDelaySeconds","value":45},
      {"op":"replace","path":"/spec/template/spec/containers/0/livenessProbe/failureThreshold","value":6}
    ]' >/dev/null
  done
  echo "ONLINE BOUTIQUE: DEVIATION applied. Benchmark runs on arm64 must record it."
}

# ==============================================================================
# Helm chart deployments
# ==============================================================================
deploy_prometheus() {
  echo "PROMETHEUS: adding Helm repo and deploying..."
  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
  helm repo update prometheus-community

  helm_local upgrade --install prometheus prometheus-community/kube-prometheus-stack \
    --version "$PROMETHEUS_CHART_VERSION" \
    --namespace monitoring \
    --values "${SCRIPT_DIR}/values/prometheus-local.yaml" \
    --wait --timeout=10m

  kubectl_local apply -f "$ATLASOPS_PROMETHEUS_RULES"
  echo "PROMETHEUS: kube-prometheus-stack deployed with local low-resource profile."
}

deploy_jaeger() {
  echo "JAEGER: adding Helm repo and deploying..."
  helm repo add jaegertracing https://jaegertracing.github.io/helm-charts --force-update
  helm repo update jaegertracing

  helm_local upgrade --install jaeger jaegertracing/jaeger \
    --version "$JAEGER_CHART_VERSION" \
    --namespace jaeger \
    --values "${SCRIPT_DIR}/values/jaeger-local.yaml" \
    --wait --timeout=10m

  echo "JAEGER: all-in-one deployed with in-memory storage."
}

deploy_argocd() {
  echo "ARGO CD: adding Helm repo and deploying..."
  helm repo add argo https://argoproj.github.io/argo-helm --force-update
  helm repo update argo

  # Generate bcrypt overlay for atlasops account password
  local argo_overlay
  argo_overlay="$(mktemp)"
  chmod 600 "$argo_overlay"

  "$PYTHON_BIN" -c '
import sys, os, json
from scripts.bcrypt_util import hash_bcrypt, format_iso_timestamp
pass_path = os.path.join(sys.argv[1], "argocd-pass.secret")
with open(pass_path, "r", encoding="utf-8") as f:
    pwd = f.read().strip()
hashed = hash_bcrypt(pwd)
mtime = format_iso_timestamp()
doc = {"configs": {"secret": {"extra": {"accounts.atlasops.password": hashed, "accounts.atlasops.passwordMtime": mtime}}}}
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(doc, f)
' "$ATLASOPS_SECRET_DIR" "$argo_overlay"

  helm_local upgrade --install argocd argo/argo-cd \
    --version "$ARGOCD_CHART_VERSION" \
    --namespace argocd \
    --values "${SCRIPT_DIR}/values/argocd-local.yaml" \
    --values "$argo_overlay" \
    --wait --timeout=10m

  rm -f -- "$argo_overlay"
  echo "ARGO CD: deployed with dedicated atlasops account and bcrypt password verifier."
}

deploy_chaos_mesh() {
  echo "CHAOS MESH: adding Helm repo and deploying..."
  helm repo add chaos-mesh https://charts.chaos-mesh.org --force-update
  helm repo update chaos-mesh

  helm_local upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
    --version "$CHAOS_MESH_CHART_VERSION" \
    --namespace chaos-mesh \
    --values "${SCRIPT_DIR}/values/chaos-mesh-local.yaml" \
    --wait --timeout=10m

  echo "CHAOS MESH: controllers and CRDs deployed. No experiments injected."
}

# ==============================================================================
# Coordinator deployment
# ==============================================================================
load_coordinator_image() {
  if docker image inspect "$COORDINATOR_IMAGE" >/dev/null 2>&1; then
    echo "COORDINATOR IMAGE: '$COORDINATOR_IMAGE' found locally."
  else
    echo "COORDINATOR IMAGE: building '$COORDINATOR_IMAGE' from Dockerfile.coordinator..."
    docker build -t "$COORDINATOR_IMAGE" -f "${REPO_ROOT}/Dockerfile.coordinator" "$REPO_ROOT"
  fi

  echo "COORDINATOR IMAGE: loading into Kind cluster '$KIND_CLUSTER_NAME'..."
  kind load docker-image "$COORDINATOR_IMAGE" --name "$KIND_CLUSTER_NAME"
  echo "COORDINATOR IMAGE: loaded into Kind. No registry push required."
}

deploy_coordinator() {
  echo "COORDINATOR: applying local manifest..."
  kubectl_local apply -f "$COORDINATOR_MANIFEST"
  echo "COORDINATOR: waiting for rollout..."
  kubectl_local rollout status deployment/atlasops-coordinator --namespace=default --timeout="$COORDINATOR_ROLLOUT_TIMEOUT"
  echo "COORDINATOR: deployed and available."
}

# ==============================================================================
# Main execution
# ==============================================================================
main() {
  parse_arguments "$@"
  validate_prerequisites

  if [[ "$MODE" == "--check" ]]; then
    echo ""
    echo "=== AtlasOps Local Preflight Summary ==="
    echo "Kind cluster name:     $KIND_CLUSTER_NAME"
    echo "Kubectl context:       $KUBE_CONTEXT"
    echo "Coordinator image:     $COORDINATOR_IMAGE"
    echo "Online Boutique:       $BOUTIQUE_RELEASE (commit $BOUTIQUE_COMMIT)"
    echo "Prometheus chart:      $PROMETHEUS_CHART_VERSION"
    echo "Jaeger chart:          $JAEGER_CHART_VERSION"
    echo "Argo CD chart:         $ARGOCD_CHART_VERSION"
    echo "Chaos Mesh chart:      $CHAOS_MESH_CHART_VERSION"
    echo "Secret directory:      $ATLASOPS_SECRET_DIR"
    echo "GCP dependency:        NONE"
    echo "External cost:         \$0"
    echo "========================================="
    echo ""
    if [[ "$MODE" == "--check" ]]; then
      validate_local_secrets
    fi
    echo "CHECK COMPLETE: all local prerequisites validated. No resources created."
    return
  fi

  # --apply mode
  echo "APPLY: creating local Kind environment with zero external-service cost."

  validate_local_secrets
  ensure_kind_cluster
  ensure_namespaces
  apply_runtime_secrets

  deploy_online_boutique
  deploy_prometheus
  deploy_jaeger
  deploy_argocd
  deploy_chaos_mesh

  load_coordinator_image
  deploy_coordinator

  cat <<EOF

=== ATLASOPS LOCAL ENVIRONMENT READY ===
Cluster:             $KIND_CLUSTER_NAME
Context:             $KUBE_CONTEXT
GCP dependency:      NONE
External cost:       \$0

Safe local access:
  kubectl --context=$KUBE_CONTEXT port-forward -n default svc/frontend 8080:80
  kubectl --context=$KUBE_CONTEXT port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
  kubectl --context=$KUBE_CONTEXT port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093
  kubectl --context=$KUBE_CONTEXT port-forward -n jaeger svc/jaeger-query 16686:16686
  kubectl --context=$KUBE_CONTEXT port-forward -n argocd svc/argocd-server 8443:80
  kubectl --context=$KUBE_CONTEXT port-forward -n default svc/atlasops-coordinator-svc 9099:9099
=========================================
EOF
}

main "$@"
