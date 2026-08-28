#!/usr/bin/env bash
# =============================================================================
# AtlasOps OPTIONAL local metrics-server installer (Kind, zero-cost).
#
# STATUS: NOT INSTALLED BY DEFAULT. This is the opt-in adoption path for the
# `kubectl top` tool contract (G4-PLATFORM-HARDENING-2026-08-25).
#
# The canonical Kind context is fixed so an ambient kubectl context cannot be
# accidentally patched or used for wrapper verification. Apply mode mutates
# only kind-atlasops-local and must be run outside scientific observation
# windows.
#
# Usage:
#   bash infra/local/install_metrics_server.sh --check
#   bash infra/local/install_metrics_server.sh --apply
#   PYTHON_BIN=/path/to/python bash infra/local/install_metrics_server.sh --check
# =============================================================================
set -euo pipefail

readonly CANONICAL_CONTEXT="kind-atlasops-local"
readonly METRICS_SERVER_COMMIT="096960107da4a1b2e2ec83b2ac3424248cfc0ad5"
readonly METRICS_SERVER_VERSION="v0.7.2"
readonly EXPECTED_METRICS_SERVER_IMAGE="registry.k8s.io/metrics-server/metrics-server:v0.7.2"
readonly EXPECTED_METRICS_SERVER_ARGS="--cert-dir=/tmp --secure-port=10250 --kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname --kubelet-use-node-status-port --metric-resolution=15s --kubelet-insecure-tls"
readonly PINNED_SOURCE="https://github.com/kubernetes-sigs/metrics-server/manifests/overlays/release?ref=${METRICS_SERVER_COMMIT}"

fail() { echo "ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  bash infra/local/install_metrics_server.sh --check
  bash infra/local/install_metrics_server.sh --apply

Modes:
  --check  Report whether metrics-server is installed; mutate nothing.
  --apply  Install into the exact canonical Kind context.
EOF
}

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
      *) fail "Unexpected argument: $arg" ;;
    esac
  done
  [[ -n "$MODE" ]] || { usage >&2; fail "An explicit --check or --apply mode is required."; }
}

CONTEXT="${KUBECONFIG_CONTEXT:-$CANONICAL_CONTEXT}"
if [[ "$CONTEXT" != "$CANONICAL_CONTEXT" ]]; then
  fail "Refusing non-canonical context '$CONTEXT'; this installer may target only $CANONICAL_CONTEXT."
fi
export KUBECONFIG_CONTEXT="$CONTEXT"

metrics_server_state() {
  local stderr
  if stderr=$(kubectl --context "$CONTEXT" get deployment metrics-server -n kube-system 2>&1); then
    echo "installed"
  elif [[ "$stderr" == *'deployments.apps "metrics-server" not found'* ]]; then
    echo "missing"
  else
    echo "unknown"
  fi
}

metrics_server_provenance() {
  local image args service_account priority_class cpu_request memory_request port
  local container_names port_names port_protocols container_count
  local deployment_path='spec.template.spec.containers[?(@.name=="metrics-server")]'
  local base="deployment/metrics-server -n kube-system"

  container_names="$(kubectl --context "$CONTEXT" get $base -o jsonpath='{.spec.template.spec.containers[*].name}')"
  container_count="$(printf '%s\n' "$container_names" | wc -w)"

  image="$(kubectl --context "$CONTEXT" get $base -o jsonpath="{.${deployment_path}.image}")"
  args="$(kubectl --context "$CONTEXT" get $base -o jsonpath="{.${deployment_path}.args[*]}")"
  service_account="$(kubectl --context "$CONTEXT" get $base -o jsonpath='{.spec.template.spec.serviceAccountName}')"
  priority_class="$(kubectl --context "$CONTEXT" get $base -o jsonpath='{.spec.template.spec.priorityClassName}')"
  cpu_request="$(kubectl --context "$CONTEXT" get $base -o jsonpath="{.${deployment_path}.resources.requests.cpu}")"
  memory_request="$(kubectl --context "$CONTEXT" get $base -o jsonpath="{.${deployment_path}.resources.requests.memory}")"
  port_names="$(kubectl --context "$CONTEXT" get $base -o jsonpath="{.${deployment_path}.ports[*].name}")"
  port_protocols="$(kubectl --context "$CONTEXT" get $base -o jsonpath="{.${deployment_path}.ports[*].protocol}")"
  port="$(kubectl --context "$CONTEXT" get $base -o jsonpath="{.${deployment_path}.ports[0].containerPort}")"

  if [[ "$image" != "$EXPECTED_METRICS_SERVER_IMAGE" ]] \
    || [[ "$container_count" != "1" ]] \
    || [[ "$container_names" != "metrics-server" ]] \
    || [[ "$service_account" != "metrics-server" ]] \
    || [[ "$priority_class" != "system-cluster-critical" ]] \
    || [[ "$cpu_request" != "100m" ]] \
    || [[ "$memory_request" != "200Mi" ]] \
    || [[ "$(printf '%s\n' "$port_names" | wc -w)" != "1" ]] \
    || [[ "$(printf '%s\n' "$port_protocols" | wc -w)" != "1" ]] \
    || [[ "$port_names" != "https" ]] \
    || [[ "$port_protocols" != "TCP" ]] \
    || [[ "$port" != "10250" ]] \
    || [[ "$args" != "$EXPECTED_METRICS_SERVER_ARGS" ]]
  then
    fail "Existing metrics-server Deployment does not match the pinned provenance contract."
  fi

  printf 'namespace=%s\nname=%s\ncontainer=%s\nimage=%s\n' \
    kube-system metrics-server metrics-server "$image"
  printf 'service_account=%s\npriority_class=%s\ncpu_request=%s\nmemory_request=%s\nport=%s\nargs=%s\n' \
    "$service_account" "$priority_class" "$cpu_request" "$memory_request" "$port" "$args"
}

verify_metrics_api() {
  echo "Verifying Metrics API through direct kubectl and the AtlasOps wrapper..."
  REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
  for _ in $(seq 1 24); do
    if \
      kubectl --context "$CONTEXT" top nodes >/dev/null 2>&1 && \
      (cd "$REPO_ROOT" && "$PYTHON_BIN" -c 'from agents.tools.kubectl import kubectl_top_pods; result=kubectl_top_pods(); assert result.get("success"), result')
    then
      echo "Metrics API operational and AtlasOps kubectl_top_pods wrapper verified on $CONTEXT."
      return
    fi
    sleep 5
  done

  fail "metrics-server installed but its Metrics API did not become usable."
}

main() {
  parse_arguments "$@"
  command -v kubectl >/dev/null 2>&1 || fail "Required command 'kubectl' is not available."

  echo "=== AtlasOps Metrics Server Installer ==="
  echo "Context:         $CONTEXT"
  echo "Source commit:   $METRICS_SERVER_COMMIT"
  echo "Release version: $METRICS_SERVER_VERSION"
  echo "Mode:            $MODE"
  echo "========================================="

  state="$(metrics_server_state)"
  case "$state" in
    installed)
      echo "CHECK: metrics-server is already installed."
      metrics_server_provenance
      if [[ "$MODE" == "--check" ]]; then
        echo "CHECK COMPLETE: no resources were changed."
        return
      fi
      echo "APPLY: existing deployment provenance verified; verifying Metrics API..."
      PYTHON_BIN="${PYTHON_BIN:-python}"
      command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Required command '$PYTHON_BIN' is not available."
      verify_metrics_api
      return
      ;;
    missing)
      if [[ "$MODE" == "--check" ]]; then
        echo "CHECK: metrics-server would be installed from commit-pinned source."
        echo "CHECK COMPLETE: no resources were changed."
        return
      fi
      ;;
    *)
      fail "Unable to determine metrics-server state on '$CONTEXT'; refusing ambiguous check or install."
      ;;
  esac

  echo "APPLY: installing metrics-server..."
  PYTHON_BIN="${PYTHON_BIN:-python}"
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Required command '$PYTHON_BIN' is not available."
  # Commit-pinned upstream source resolves the release image tag at that exact
  # revision. Kind uses self-signed kubelet certificates, so the local TLS flag
  # is added with a standard JSON patch after installation.
  kubectl --context "$CONTEXT" apply -k "$PINNED_SOURCE"
  kubectl --context "$CONTEXT" patch deployment metrics-server -n kube-system \
    --type json \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'

  echo "APPLY: waiting for metrics-server availability..."
  kubectl --context "$CONTEXT" wait --for=condition=Available \
    deployment/metrics-server -n kube-system --timeout=180s

  metrics_server_provenance
  verify_metrics_api
}

main "$@"
