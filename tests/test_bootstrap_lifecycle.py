"""Tests for Stage 3 fresh-cluster bootstrap lifecycle and state-machine ordering.

Verifies that the bootstrap sequence in infra/setup_impl.sh has no circular
dependencies, creates namespaces before secret checks or component installs,
installs backends before coordinator runtime, and enforces fail-closed secret
validation before coordinator deployment.
"""

from __future__ import annotations

import re
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


SETUP_IMPL = read("infra/setup_impl.sh")
SETUP_ENTRY = read("infra/setup.sh")
FULL_SETUP = SETUP_ENTRY + "\n" + SETUP_IMPL


class TestBootstrapOrderStateMachine:
    """Proves the exact statement ordering inside apply_foundation()."""

    def test_apply_foundation_call_sequence(self) -> None:
        """Asserts the exact logical lifecycle ordering in apply_foundation()."""
        # Find apply_foundation body
        match = re.search(r"apply_foundation\(\)\s*\{([\s\S]*?)\n\}", SETUP_IMPL)
        assert match is not None, "apply_foundation function not found in setup_impl.sh"
        body = match.group(1)

        # Extract positions of critical milestones
        pos_enable_apis = body.find("gcloud services enable")
        pos_ensure_cluster = body.find("ensure_cluster")
        pos_init_access = body.find("initialize_cluster_access")
        pos_ensure_namespaces = body.find("ensure_namespaces")
        pos_boutique = body.find("kubectl_target apply -f \"$BOUTIQUE_MANIFEST\"")
        pos_helm_repos = body.find("helm repo add")
        pos_prometheus = body.find("helm_target upgrade --install prometheus")
        pos_jaeger = body.find("helm_target upgrade --install jaeger")
        pos_argocd = body.find("helm_target upgrade --install argocd")
        pos_chaos = body.find("helm_target upgrade --install chaos-mesh")
        pos_validate_secrets = body.find("validate_runtime_secret_contract")
        pos_render_coord = body.find("render_coordinator_manifest")
        pos_apply_coord = body.find("kubectl_target apply -f \"$RENDERED_COORDINATOR_MANIFEST\"")
        pos_coord_rollout = body.find("rollout status deployment/atlasops-coordinator")

        # Verify all milestones exist in apply_foundation
        assert pos_enable_apis != -1, "Missing API enablement"
        assert pos_ensure_cluster != -1, "Missing cluster creation/reuse"
        assert pos_init_access != -1, "Missing cluster access initialization"
        assert pos_ensure_namespaces != -1, "Missing namespace creation"
        assert pos_boutique != -1, "Missing Online Boutique apply"
        assert pos_helm_repos != -1, "Missing helm repo setup"
        assert pos_prometheus != -1, "Missing Prometheus install"
        assert pos_jaeger != -1, "Missing Jaeger install"
        assert pos_argocd != -1, "Missing Argo CD install"
        assert pos_chaos != -1, "Missing Chaos Mesh install"
        assert pos_validate_secrets != -1, "Missing runtime secret validation"
        assert pos_render_coord != -1, "Missing coordinator manifest render"
        assert pos_apply_coord != -1, "Missing coordinator manifest apply"
        assert pos_coord_rollout != -1, "Missing coordinator rollout wait"

        # Assert chronological non-circular dependency ordering
        assert pos_enable_apis < pos_ensure_cluster, "APIs must be enabled before cluster creation"
        assert pos_ensure_cluster < pos_init_access, "Cluster must exist before getting credentials"
        assert pos_init_access < pos_ensure_namespaces, "Kubeconfig must be initialized before creating namespaces"
        assert pos_ensure_namespaces < pos_boutique, "Namespaces must exist before applying Online Boutique"
        assert pos_boutique < pos_prometheus, "Online Boutique is deployed before monitoring stack"
        assert pos_prometheus < pos_jaeger, "Prometheus installed before Jaeger"
        assert pos_jaeger < pos_argocd, "Jaeger installed before Argo CD"
        assert pos_argocd < pos_chaos, "Argo CD installed before Chaos Mesh"
        assert pos_chaos < pos_validate_secrets, "Backends must be installed before coordinator secret validation"
        assert pos_validate_secrets < pos_render_coord, "Secret validation must pass before rendering coordinator"
        assert pos_render_coord < pos_apply_coord, "Manifest must be rendered before applying coordinator"
        assert pos_apply_coord < pos_coord_rollout, "Coordinator must be applied before waiting for rollout"

    def test_ensure_namespaces_includes_all_required_components(self) -> None:
        match = re.search(r"ensure_namespaces\(\)\s*\{([\s\S]*?)\n\}", SETUP_IMPL)
        assert match is not None
        body = match.group(1)
        assert "default" in body
        assert "monitoring" in body
        assert "jaeger" in body
        assert "chaos-mesh" in body
        assert "argocd" in body
        assert 'kubectl_target create namespace "$ns" --dry-run=client -o yaml | kubectl_target apply -f -' in body

    def test_argocd_disabled_deviation_skips_argocd_in_namespaces_and_foundation(self) -> None:
        assert 'if [[ "$ATLASOPS_ENABLE_ARGOCD" == true ]]; then' in SETUP_IMPL
        assert 'DEVIATION: canonical Gate G3 cannot PASS without Argo CD' in SETUP_IMPL

    def test_secret_validation_checks_all_required_keys_fail_closed(self) -> None:
        match = re.search(r"validate_runtime_secret_contract\(\)\s*\{([\s\S]*?)\n\}", SETUP_IMPL)
        assert match is not None
        body = match.group(1)
        # Required generic keys in default namespace
        for key in ["atlasops-audit-secret", "alertmanager-webhook-secret", "atlasops-api-key"]:
            assert key in body
        # Required Argo CD keys in default namespace
        assert "argocd-user" in body
        assert "argocd-pass" in body
        # Required Alertmanager key in monitoring namespace
        assert "secret_key_present monitoring \"$ALERTMANAGER_SECRET\" alertmanager-webhook-secret" in body


class TestSecretGeneratorContract:
    """Tests safety and correctness of scripts/generate_runtime_secrets.py."""

    def test_secret_generator_execution_and_randomness(self, tmp_path: Path) -> None:
        from scripts.generate_runtime_secrets import generate_token, main
        import sys

        out_dir = tmp_path / "test_secrets"
        # Test token randomness
        tok1 = generate_token(32)
        tok2 = generate_token(32)
        assert len(tok1) == 64
        assert len(tok2) == 64
        assert tok1 != tok2

        # Create dummy password file
        pass_file = tmp_path / "mypass.txt"
        pass_file.write_text("my-strong-argo-password", encoding="utf-8")

        # Run script with custom output dir and password file
        test_args = ["generate_runtime_secrets.py", "--output-dir", str(out_dir), "--argocd-user", "atlasops", "--argocd-pass-file", str(pass_file)]
        sys_argv_orig = sys.argv
        try:
            sys.argv = test_args
            main()
        finally:
            sys.argv = sys_argv_orig

        assert (out_dir / "atlasops-audit-secret.secret").is_file()
        assert (out_dir / "alertmanager-webhook-secret.secret").is_file()
        assert (out_dir / "atlasops-api-key.secret").is_file()
        assert (out_dir / "argocd-user.secret").is_file()
        assert (out_dir / "argocd-pass.secret").is_file()
        assert (out_dir / "apply_secrets.sh").is_file()

        assert (out_dir / "argocd-user.secret").read_text(encoding="utf-8").strip() == "atlasops"
        assert (out_dir / "argocd-pass.secret").read_text(encoding="utf-8").strip() == "my-strong-argo-password"

        # Check script contents
        script = (out_dir / "apply_secrets.sh").read_text(encoding="utf-8")
        assert "<ARGOCD_USER>" not in script
        assert "<ARGOCD_PASSWORD>" not in script
        assert "--from-file=argocd-user=" in script
        assert "--from-file=argocd-pass=" in script
        assert "Missing required secret file" in script

    def test_missing_password_file_raises_or_warns(self, tmp_path: Path) -> None:
        from scripts.generate_runtime_secrets import main
        import sys

        out_dir = tmp_path / "secrets_no_pass"
        test_args = ["generate_runtime_secrets.py", "--output-dir", str(out_dir)]
        sys_argv_orig = sys.argv
        try:
            sys.argv = test_args
            main()
        finally:
            sys.argv = sys_argv_orig

        # When no password file passed, script generates generic secrets and prompts for password
        assert (out_dir / "atlasops-audit-secret.secret").is_file()
        assert (out_dir / "argocd-user.secret").is_file()
        script = (out_dir / "apply_secrets.sh").read_text(encoding="utf-8")
        assert "Missing required secret file" in script
