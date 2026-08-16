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

readonly KIND_CLUSTER_NAME="atlasops-local"
readonly DELETE_ACK_VALUE="DELETE_ATLASOPS_LOCAL"

fail() { echo "ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage:
  bash infra/local/teardown_local.sh --check
  bash infra/local/teardown_local.sh --apply

Modes:
  --check  Show what would be deleted without deleting anything.
  --apply  Delete the exact Kind cluster 'atlasops-local'.

Apply-only acknowledgement (optional safety):
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

  command -v kind >/dev/null 2>&1 || fail "Required command 'kind' is not available."

  local cluster_exists=false
  if kind get clusters 2>/dev/null | grep -Fxq "$KIND_CLUSTER_NAME"; then
    cluster_exists=true
  fi

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

  # --apply mode
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
