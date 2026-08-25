"""Static safety contracts for the optional metrics-server adoption script."""

from pathlib import Path


_INSTALLER = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "local"
    / "install_metrics_server.sh"
)
_SCRIPT = _INSTALLER.read_text(encoding="utf-8")


def test_installer_requires_explicit_mode():
    assert "An explicit --check or --apply mode is required." in _SCRIPT
    assert 'MODE=""' in _SCRIPT


def test_installer_pins_context_and_exports_it_for_wrapper_verification():
    assert 'CANONICAL_CONTEXT="kind-atlasops-local"' in _SCRIPT
    assert 'CONTEXT="${KUBECONFIG_CONTEXT:-$CANONICAL_CONTEXT}"' in _SCRIPT
    assert '[[ "$CONTEXT" != "$CANONICAL_CONTEXT" ]]' in _SCRIPT
    assert 'export KUBECONFIG_CONTEXT="$CONTEXT"' in _SCRIPT
    assert "kubectl_top_pods()" in _SCRIPT


def test_installer_uses_commit_pinned_upstream_source():
    assert "096960107da4a1b2e2ec83b2ac3424248cfc0ad5" in _SCRIPT
    assert "manifests/overlays/release?ref=" in _SCRIPT
    assert "manifests/components/release?ref=" not in _SCRIPT
    assert "releases/download/" not in _SCRIPT


def test_installer_check_does_not_treat_cluster_failure_as_missing_dependency():
    assert "metrics_server_state()" in _SCRIPT
    assert "Unable to determine metrics-server state" in _SCRIPT
    assert 'deployments.apps "metrics-server" not found' in _SCRIPT
    assert "metrics_server_installed() {" not in _SCRIPT


def test_apply_verifies_existing_deployment_before_reporting_success():
    assert "verify_metrics_api()" in _SCRIPT
    assert (
        _SCRIPT.index("deployment already present; verifying Metrics API")
        < _SCRIPT.index("APPLY: waiting for metrics-server availability")
    )
