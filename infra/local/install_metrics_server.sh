#!/usr/bin/env bash
# =============================================================================
# AtlasOps OPTIONAL local metrics-server installer (Kind, zero-cost).
#
# STATUS: NOT INSTALLED BY DEFAULT. This script exists as the reproducible
# adoption path for the `kubectl top` tool contract (G4-PLATFORM-HARDENING-
# 2026-08-25). Operators may run it to make `kubectl top pods/nodes` — and the
# AtlasOps `kubectl_top_pods` tool — functional in the canonical local cluster.
#
# Properties:
#   - Idempotent: skips if the metrics-server is already present.
#   - Reproducible: pinned upstream manifest version.
#   - Free: upstream Kubernetes SIG project manifests only.
#   - Reversible: `kubectl delete -f <manifest-url>` removes it cleanly;
#     the AtlasOps tool degrades deterministically without it.
#
# Resource cost on the host/cluster is small but non-zero (~100m CPU /
# ~200Mi memory for the metrics-server pod). Run only outside scientific
# observation windows so the soak gates measure an undisturbed baseline.
#
# Usage:
#   bash infra/local/install_metrics_server.sh
# =============================================================================
set -euo pipefail

CONTEXT="${KUBECONFIG_CONTEXT:-kind-atlasops-local}"
PINNED_MANIFEST="https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml"

kubectl --context "$CONTEXT" get deployment metrics-server -n kube-system >/dev/null 2>&1 && {
  echo "metrics-server already installed; nothing to do."
  exit 0
}

echo "Installing metrics-server into context '$CONTEXT'..."
# Kind control plane uses a self-signed serving cert; pass -kubelet-insecure-tls
# via an args patch after install (standard practice for local clusters).
kubectl --context "$CONTEXT" apply -f "$PINNED_MANIFEST"
kubectl --context "$CONTEXT" patch deployment metrics-server -n kube-system \
  --type json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'

echo "Waiting for metrics-server availability..."
kubectl --context "$CONTEXT" wait --for=condition=Available \
  deployment/metrics-server -n kube-system --timeout=180s

echo "Verifying Metrics API..."
for i in $(seq 1 24); do
  if kubectl --context "$CONTEXT" top nodes >/dev/null 2>&1; then
    REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
    PYTHON_BIN="${PYTHON_BIN:-python}"
    if (cd "$REPO_ROOT" && "$PYTHON_BIN" -c 'from agents.tools.kubectl import kubectl_top_pods; r=kubectl_top_pods(); assert r.get("success"), r' ); then
      echo "Metrics API operational and AtlasOps kubectl_top_pods wrapper verified."
      exit 0
    fi
  fi
  sleep 5
done

echo "WARNING: metrics-server installed but Metrics API not answering yet." >&2
exit 1
