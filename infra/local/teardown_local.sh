#!/usr/bin/env bash
# ==============================================================================
# AtlasOps Local Kind Environment Teardown
# Pipeline v1.1 Free-First
#
# Destroys the exact local Kind cluster and associated resources.
# Does NOT affect any cloud/GKE resources.
#
# Usage:
#   bash infra/local/teardown_local.sh --check
#   bash infra/local/teardown_local.sh --apply
# ==============================================================================
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
readonly KIND_CLUSTER_NAME="${ATLASOPS_KIND_CLUSTER_NAME:-atlasops-local}"
readonly KUBE_CONTEXT="kind-${KIND_CLUSTER_NAME}"
readonly DELETE_ACK_VALUE="DELETE_ATLASOPS_LOCAL"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  readonly PYTHON_BIN
elif command -v python3 >/dev/null 2>&1; then
  readonly PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  readonly PYTHON_BIN="python"
else
  readonly PYTHON_BIN="python3"
fi

fail() { echo "ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  bash infra/local/teardown_local.sh --check
  bash infra/local/teardown_local.sh --apply

Modes:
  --check  Show what would be deleted without deleting anything.
  --apply  Delete the selected Kind cluster after its ownership inspection.

Target:
  ATLASOPS_KIND_CLUSTER_NAME (default: atlasops-local)

Required apply-only acknowledgement:
  ATLASOPS_TEARDOWN_ACK=DELETE_ATLASOPS_LOCAL
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
      --*) fail "Unknown option: $arg" ;;
      *) fail "Unexpected argument: $arg" ;;
    esac
  done
  [[ -n "$MODE" ]] || { usage >&2; fail "An explicit --check or --apply mode is required."; }
}

main() {
  parse_arguments "$@"

  if [[ "$MODE" == "--apply" ]]; then
    [[ "${ATLASOPS_TEARDOWN_ACK:-}" == "$DELETE_ACK_VALUE" ]] \
      || fail "ATLASOPS_TEARDOWN_ACK must equal $DELETE_ACK_VALUE before deletion."
  fi
  command -v kind >/dev/null 2>&1 || fail "Required command 'kind' is not available."
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Required command '$PYTHON_BIN' is not available."

  local cluster_exists=false
  local inspection_output inspection_status=0
  inspection_output="$("$PYTHON_BIN" -B "${REPO_ROOT}/scripts/verify_platform_infrastructure.py" \
    inspect-kind-cluster --cluster-name "$KIND_CLUSTER_NAME" \
    --kube-context "$KUBE_CONTEXT" 2>&1)" || inspection_status=$?
  case "$inspection_status" in
    0) cluster_exists=true ;;
    3) cluster_exists=false ;;
    *) fail "Could not safely inspect Kind cluster '$KIND_CLUSTER_NAME': $inspection_output" ;;
  esac

  echo "=== AtlasOps Local Teardown ==="
  echo "Target cluster:  $KIND_CLUSTER_NAME"
  echo "Cluster exists:  $cluster_exists"
  echo "Mode:            $MODE"
  echo "================================"

  if [[ "$MODE" == "--check" ]]; then
    if [[ "$cluster_exists" == true ]]; then
      echo "CHECK: cluster '$KIND_CLUSTER_NAME' would be deleted."
    else
      echo "CHECK: cluster '$KIND_CLUSTER_NAME' does not exist. Nothing to delete."
    fi
    echo "CHECK COMPLETE: no resources were deleted."
    return
  fi

  if [[ "$cluster_exists" == false ]]; then
    echo "TEARDOWN: cluster '$KIND_CLUSTER_NAME' does not exist. Nothing to delete."
    return
  fi

  echo "TEARDOWN: deleting Kind cluster '$KIND_CLUSTER_NAME'..."
  kind delete cluster --name "$KIND_CLUSTER_NAME"
  echo "TEARDOWN: cluster '$KIND_CLUSTER_NAME' deleted."
  echo "TEARDOWN: local Docker images remain cached (use 'docker image prune' to reclaim)."
  echo "TEARDOWN COMPLETE."
}

main "$@"
