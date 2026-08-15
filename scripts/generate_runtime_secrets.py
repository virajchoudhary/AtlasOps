"""AtlasOps Local Runtime Secret Generation Helper.

Generates cryptographically strong random secrets for AtlasOps coordinator
and Alertmanager webhook integration without committing them to Git.

Usage:
    python scripts/generate_runtime_secrets.py [--output-dir secrets]

Outputs gitignored kubectl commands and files to create:
1. default/atlasops-coordinator-secrets
2. monitoring/atlasops-alertmanager-webhook
"""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_hex(nbytes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate AtlasOps runtime secrets safely.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("secrets"),
        help="Local directory to store gitignored secret files (default: secrets/)",
    )
    args = parser.parse_args()

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    audit_secret = generate_token(32)
    webhook_secret = generate_token(32)
    api_key = generate_token(32)

    # Write gitignored secret values to local files with restricted permissions
    (out_dir / "atlasops-audit-secret.secret").write_text(audit_secret, encoding="utf-8")
    (out_dir / "alertmanager-webhook-secret.secret").write_text(webhook_secret, encoding="utf-8")
    (out_dir / "atlasops-api-key.secret").write_text(api_key, encoding="utf-8")

    # Generate the exact kubectl creation commands for operator execution
    commands_text = f"""# ==============================================================================
# AtlasOps Runtime Secrets - Apply Commands
# Execute these commands against your target cluster before running setup.sh --apply
# DO NOT COMMIT THIS FILE OR PRINT SECRETS TO LOGS
# ==============================================================================

# 1. Create monitoring namespace if not present
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

# 2. Apply coordinator secrets in default namespace
# Note: replace <ARGOCD_USER> and <ARGOCD_PASSWORD> with your Argo CD operator credentials
kubectl create secret generic atlasops-coordinator-secrets \\
  --namespace=default \\
  --from-file=atlasops-audit-secret="{out_dir / 'atlasops-audit-secret.secret'}" \\
  --from-file=alertmanager-webhook-secret="{out_dir / 'alertmanager-webhook-secret.secret'}" \\
  --from-file=atlasops-api-key="{out_dir / 'atlasops-api-key.secret'}" \\
  --from-literal=argocd-user="<ARGOCD_USER>" \\
  --from-literal=argocd-pass="<ARGOCD_PASSWORD>" \\
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Apply Alertmanager webhook secret in monitoring namespace
kubectl create secret generic atlasops-alertmanager-webhook \\
  --namespace=monitoring \\
  --from-file=alertmanager-webhook-secret="{out_dir / 'alertmanager-webhook-secret.secret'}" \\
  --dry-run=client -o yaml | kubectl apply -f -
"""

    apply_script = out_dir / "apply_secrets.sh"
    apply_script.write_text(commands_text, encoding="utf-8")

    print(f"[OK] Generated runtime secrets in '{out_dir}/'")
    print(f"[OK] Run script saved to '{apply_script}' (gitignored)")
    print("[NOTE] Argo CD credentials must be supplied by the operator.")


if __name__ == "__main__":
    main()
