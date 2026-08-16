"""AtlasOps Local Runtime Secret Generation Helper.

Generates cryptographically strong random secrets for AtlasOps coordinator
and Alertmanager webhook integration without committing them to Git.

Usage:
    python scripts/generate_runtime_secrets.py [--output-dir secrets] [--argocd-user atlasops] [--argocd-pass-file PATH]

Outputs gitignored secret files and a fail-closed apply script:
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
    parser.add_argument(
        "--argocd-user",
        type=str,
        default="atlasops",
        help="Argo CD dedicated least-privilege username (default: atlasops)",
    )
    parser.add_argument(
        "--argocd-pass-file",
        type=Path,
        default=None,
        help="Optional path to existing file containing the Argo CD password",
    )
    args = parser.parse_args()

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    audit_secret = generate_token(32)
    webhook_secret = generate_token(32)
    api_key = generate_token(32)

    # Write gitignored generic secret values to local files
    (out_dir / "atlasops-audit-secret.secret").write_text(audit_secret, encoding="utf-8")
    (out_dir / "alertmanager-webhook-secret.secret").write_text(webhook_secret, encoding="utf-8")
    (out_dir / "atlasops-api-key.secret").write_text(api_key, encoding="utf-8")
    (out_dir / "argocd-user.secret").write_text(args.argocd_user.strip(), encoding="utf-8")

    # If operator provided a password file, copy it; otherwise check if it already exists
    pass_file = out_dir / "argocd-pass.secret"
    if args.argocd_pass_file:
        if not args.argocd_pass_file.is_file():
            raise FileNotFoundError(f"Specified Argo password file not found: {args.argocd_pass_file}")
        pass_content = args.argocd_pass_file.read_text(encoding="utf-8").strip()
        pass_file.write_text(pass_content, encoding="utf-8")

    # Generate fail-closed kubectl apply script using file references only
    commands_text = f"""#!/usr/bin/env bash
# ==============================================================================
# AtlasOps Runtime Secrets - Apply Script
# Applies required secrets to target cluster using local gitignored secret files.
# DO NOT COMMIT SECRET FILES OR PRINT SECRET VALUES TO LOGS
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"

# Validate required secret files exist before running kubectl mutations
for req in atlasops-audit-secret.secret alertmanager-webhook-secret.secret atlasops-api-key.secret argocd-user.secret argocd-pass.secret; do
  if [[ ! -f "$SCRIPT_DIR/$req" ]]; then
    echo "ERROR: Missing required secret file: $SCRIPT_DIR/$req" >&2
    echo "Please write the required credential material to $SCRIPT_DIR/$req before applying." >&2
    exit 1
  fi
  # Verify file is not empty or containing raw placeholders
  if [[ ! -s "$SCRIPT_DIR/$req" ]]; then
    echo "ERROR: Secret file is empty: $SCRIPT_DIR/$req" >&2
    exit 1
  fi
done

# 1. Create namespaces if not present
kubectl create namespace default --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

# 2. Apply coordinator secrets in default namespace via file references
kubectl create secret generic atlasops-coordinator-secrets \\
  --namespace=default \\
  --from-file=atlasops-audit-secret="$SCRIPT_DIR/atlasops-audit-secret.secret" \\
  --from-file=alertmanager-webhook-secret="$SCRIPT_DIR/alertmanager-webhook-secret.secret" \\
  --from-file=atlasops-api-key="$SCRIPT_DIR/atlasops-api-key.secret" \\
  --from-file=argocd-user="$SCRIPT_DIR/argocd-user.secret" \\
  --from-file=argocd-pass="$SCRIPT_DIR/argocd-pass.secret" \\
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Apply Alertmanager webhook secret in monitoring namespace
kubectl create secret generic atlasops-alertmanager-webhook \\
  --namespace=monitoring \\
  --from-file=alertmanager-webhook-secret="$SCRIPT_DIR/alertmanager-webhook-secret.secret" \\
  --dry-run=client -o yaml | kubectl apply -f -

echo "SECRETS: Successfully applied coordinator and Alertmanager secrets from '$SCRIPT_DIR'."
"""

    apply_script = out_dir / "apply_secrets.sh"
    apply_script.write_text(commands_text, encoding="utf-8")

    print(f"[OK] Generated runtime secrets in '{out_dir}/'")
    print(f"[OK] Run script saved to '{apply_script}' (gitignored)")
    if not pass_file.is_file():
        print(f"[ACTION REQUIRED] Create '{pass_file}' with your Argo CD operator password before running apply_secrets.sh")


if __name__ == "__main__":
    main()
